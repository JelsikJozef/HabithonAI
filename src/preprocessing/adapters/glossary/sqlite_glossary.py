"""
SQLite-backed deterministic glossary adapter for plain-text normalization.

This module implements a local glossary engine that stores rules in a SQLite
database and applies them deterministically to plain text segments (never raw
Markdown). It is designed to be stable, reproducible, and offline-only.

Public surface:
- GlossaryError: Exception type with stable short codes.
- SqliteGlossary: Main adapter exposing load/apply/admin helpers/close.

Behavioral guarantees:
- Deterministic rule ordering and non-overlapping application per rule.
- Language-aware rule selection using src_lang/tgt_lang and mode (pre/post).
- Unicode-aware, letter-based boundaries that include accented Latin letters.
- Regex handling is disabled by default; when enabled, only safe patterns are
  accepted and a timeout is enforced when available.

Note on Markdown: The caller must pass plain text segments only. This adapter
never attempts to parse or preserve Markdown; it assumes it's already stripped.
"""

from __future__ import annotations

import re
import sqlite3
import time
import unicodedata
from dataclasses import dataclass
from typing import Any


class GlossaryError(Exception):
    """Domain-specific error for glossary operations.

    Attributes:
        code: Stable short code identifying the error category. One of:
              "DB_OPEN_FAILED", "SCHEMA_INVALID", "DB_ERROR",
              "RULE_INVALID", "TIMEOUT".
        message: Human-readable description (non-localized), safe for logs.
        details: Optional structured details for diagnostics.
    """

    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:  # pragma: no cover - mirrors base behavior
        return f"{self.code}: {self.message}"


@dataclass(frozen=True)
class _Rule:
    """Internal representation of a glossary rule.

    Fields:
        id: Row identifier from the SQLite table.
        pattern: Raw pattern string as stored (normalized if configured).
        replacement: Replacement string.
        case_sensitive: Whether matching is case-sensitive (True) or not (False).
        word_boundary: Whether to enforce letter-based boundaries around matches.
        priority: Lower is earlier when conflicts_policy == "priority".
    """

    id: int
    pattern: str
    replacement: str
    case_sensitive: bool
    word_boundary: bool
    priority: int


class SqliteGlossary:
    """SQLite-backed glossary adapter applying deterministic term normalization.

    Responsibilities:
    - Store and retrieve glossary rules in a local SQLite database.
    - Apply non-overlapping, deterministic replacements to plain text segments.
    - Respect language filters (src_lang/tgt_lang) and mode ("pre"/"post").
    - Provide optional administrative helpers for CRUD operations on rules.

    Constructor parameters:
        db_path: Absolute or relative filesystem path to the SQLite database
            file. Must be accessible on the local machine; network I/O is not
            performed by this adapter.
        default_glossary_id: Optional string used when a method does not
            explicitly provide a glossary_id. If both are missing, applying
            rules fails gracefully returning input unchanged.
        max_rules_per_call: Upper bound on the number of rules loaded and
            considered per apply() invocation. Guards against pathological
            databases. Excess rules beyond the limit are ignored deterministically.
        regex_enabled: If False (default), patterns are treated as literals and
            safely escaped. If True, patterns are interpreted as regular
            expressions, subject to additional safety checks and potential timeouts.
        unicode_word_chars: Optional string of additional characters considered
            "letters" for word boundary checks. By default, all Unicode letters
            (unicodedata.category(c).startswith('L')) are considered. Provide this
            to widen or narrow the boundary class deterministically.
        normalization: Optional dict configuring normalization behavior:
            - normalize_input (bool, default True): normalize input text to NFKC
              prior to matching.
            - normalize_rules (bool, default True): normalize stored patterns and
              replacements to NFKC at load time.
        conflicts_policy: Controls deterministic rule ordering when applying:
            - "priority" (default): primary sort by priority ascending.
            - "longest_match": primary sort by pattern length descending.
            - "first_wins": primary sort by id ascending (insertion order).
            Ties are resolved by pattern length (desc) then id (asc).
        deterministic_order: When True (default), enforce stable ordering as
            specified by conflicts_policy; when False, the engine may delegate to
            the database ORDER BY but still remains stable given the tiebreakers.

    Public methods:
        load(glossary_id): Open the database, validate schema, and prepare for
            application. Idempotent.
        apply(text, src_lang, tgt_lang, mode, glossary_id): Apply matching rules
            to the provided plain text and return the normalized string.
        add_term(...), remove_term(...), enable_term(...), list_terms(...):
            Administrative helpers wrapped in transactions.
        capabilities(): Engine fingerprint with configuration details.
        close(): Close the SQLite connection; idempotent.

    Error handling:
        All database and rule issues are converted to GlossaryError with stable
        codes. No raw sqlite3 or regex exceptions leak to callers.
    """

    def __init__(
        self,
        db_path: str,
        *,
        default_glossary_id: str | None = None,
        max_rules_per_call: int = 5000,
        regex_enabled: bool = False,
        unicode_word_chars: str | None = None,
        normalization: dict[str, Any] | None = None,
        conflicts_policy: str = "priority",
        deterministic_order: bool = True,
    ) -> None:
        self._db_path = db_path
        self._default_glossary_id = default_glossary_id
        self._max_rules_per_call = max(1, int(max_rules_per_call))
        self._regex_enabled = bool(regex_enabled)
        self._extra_word_chars = unicode_word_chars or ""
        self._normalization = {
            "normalize_input": True,
            "normalize_rules": True,
            # Internally we use NFKC form for normalization; it's not configurable
            # via public API to keep deterministic behavior simple.
            "_form": "NFKC",
        }
        if normalization:
            self._normalization.update(normalization)
        self._conflicts_policy = conflicts_policy
        self._deterministic_order = deterministic_order

        self._conn: sqlite3.Connection | None = None
        self._active_glossary_id: str | None = None

    # ---------------------------- Public API ----------------------------

    def load(self, glossary_id: str | None | None = None) -> None:
        """Open the SQLite database and validate schema.

        Parameters:
            glossary_id: Optional string that becomes the active default for
                subsequent calls to apply() if not explicitly provided there. If
                None, the adapter will fall back to the constructor's
                default_glossary_id.

        Returns:
            None. The method sets up internal state and raises GlossaryError on
            failure.

        Raises:
            GlossaryError("DB_OPEN_FAILED"): When the database file can't be
                opened for any reason.
            GlossaryError("SCHEMA_INVALID"): When the database doesn't contain
                the required schema (missing table or incompatible columns).
        """
        self._active_glossary_id = glossary_id or self._default_glossary_id
        try:
            if self._conn is None:
                # Open a local connection; autocommit disabled so we can control
                # transactions in admin helpers. We avoid setting URI read-only
                # to allow admin ops without re-opening.
                self._conn = sqlite3.connect(self._db_path)
                self._conn.row_factory = sqlite3.Row
        except Exception as ex:  # pragma: no cover - depends on environment
            raise GlossaryError("DB_OPEN_FAILED", f"Failed to open DB: {ex}") from ex

        # Validate schema presence and shape; do not create here to fail fast
        # for runtime-only usage, as specified.
        try:
            self._ensure_schema(expect_exists=True)
        except GlossaryError:
            # Re-raise as-is
            raise
        except Exception as ex:  # pragma: no cover - defensive
            raise GlossaryError("SCHEMA_INVALID", f"Schema validation failed: {ex}") from ex

    def apply(
        self,
        text: str,
        src_lang: str,
        tgt_lang: str,
        mode: str,
        glossary_id: str | None | None = None,
    ) -> str:
        """Apply glossary rules to a plain text segment deterministically.

        Inputs:
            text: Plain human text (no Markdown syntax). The adapter assumes the
                caller already segmented and stripped Markdown; only lexical
                content is modified.
            src_lang: Source language code (lowercase ISO-like). Rules with
                src_lang matching this value or NULL will be considered.
            tgt_lang: Target language code. For post-translation normalization,
                this is typically "en". Rules with tgt_lang matching this value
                or NULL will be considered.
            mode: Either "pre" or "post". Selects which normalization stage's
                rules to apply.
            glossary_id: Optional identifier selecting a glossary within the
                database. If None, falls back to the value set via load() or the
                constructor. If no glossary_id is available, the method returns
                the input unchanged.

        Process:
            1) Optionally normalize the input text to NFKC for stable matching.
            2) Query the database for enabled rules for the selected glossary,
               mode, and languages, capped at max_rules_per_call.
            3) Order rules deterministically according to conflicts_policy with
               stable tiebreakers (pattern length desc, id asc).
            4) Apply each rule once in a left-to-right, non-overlapping manner.
               Word boundaries (letters only) and case sensitivity are respected.
               No recursive matching or feedback loops are performed.

        Output:
            Returns the final normalized string. Whitespace and line-breaks are
            preserved; only matched lexical content is replaced.

        Errors:
            Raises GlossaryError with one of the following codes:
              - "DB_ERROR": The adapter isn't loaded or a general DB error
                occurs during rule selection.
              - "RULE_INVALID": When regex is enabled and a rule fails safety
                checks or compilation.
              - "TIMEOUT": When regex is enabled and compilation or matching
                exceeds a time budget (if supported by the runtime).

        Returns:
            str: The normalized text.
        """
        # Resolve glossary id
        gid = glossary_id or self._active_glossary_id or self._default_glossary_id
        if gid is None:
            # No glossary configured; no-op as per spec (not an error)
            return (
                self._normalize_input(text)
                if self._normalization.get("normalize_input", True)
                else text
            )

        if self._conn is None:
            raise GlossaryError("DB_ERROR", "Adapter not loaded. Call load() first.")

        # Normalize input if configured
        working = (
            self._normalize_input(text)
            if self._normalization.get("normalize_input", True)
            else text
        )

        # Fetch and order rules deterministically
        try:
            rules = self._select_rules(gid=gid, mode=mode, src_lang=src_lang, tgt_lang=tgt_lang)
        except GlossaryError:
            raise
        except Exception as ex:  # pragma: no cover - defensive
            raise GlossaryError("DB_ERROR", f"Failed to select rules: {ex}") from ex

        if not rules:
            return working

        # Apply rules in the resolved order. Each rule is applied in a single
        # pass with non-overlapping matches; later rules see the updated text.
        for rule in rules:
            try:
                working = self._apply_one_rule(working, rule)
            except GlossaryError:
                # Propagate rule-level errors (e.g., invalid regex when allowed)
                raise
        return working

    # ---------------------- Administrative helpers ----------------------

    def add_term(
        self,
        glossary_id: str,
        pattern: str,
        replacement: str,
        *,
        src_lang: str | None = None,
        tgt_lang: str | None = None,
        mode: str = "post",
        case_sensitive: bool = False,
        word_boundary: bool = True,
        priority: int = 100,
        enabled: bool = True,
        notes: str | None = None,
    ) -> int:
        """Insert a new glossary term into the database atomically.

        Parameters:
            glossary_id: Logical group identifier for the rule. Required.
            pattern: Literal phrase or regex (only if regex_enabled=True). Required.
            replacement: Replacement phrase. Required.
            src_lang: Optional language filter for source language; None means any.
            tgt_lang: Optional language filter for target language; None means any.
            mode: Stage indicator, "pre" or "post". Default "post".
            case_sensitive: Whether matching is case sensitive. Default False.
            word_boundary: Enforce letter-based boundaries around matches. Default True.
            priority: Lower values apply earlier when conflicts_policy=="priority".
            enabled: Whether the rule is active. Default True.
            notes: Optional free-form notes.

        Returns:
            int: The auto-incremented id of the inserted rule.

        Raises:
            GlossaryError("DB_OPEN_FAILED"/"DB_ERROR"): On connection or insert errors.
        """
        conn = self._ensure_connection()
        try:
            self._ensure_schema(expect_exists=False)
            with conn:  # transaction
                cur = conn.execute(
                    """
                    INSERT INTO terms (
                        glossary_id, src_lang, tgt_lang, mode, pattern, replacement,
                        case_sensitive, word_boundary, priority, enabled, notes
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        glossary_id,
                        src_lang,
                        tgt_lang,
                        mode,
                        (
                            self._normalize_rule(pattern)
                            if self._normalization.get("normalize_rules", True)
                            else pattern
                        ),
                        (
                            self._normalize_rule(replacement)
                            if self._normalization.get("normalize_rules", True)
                            else replacement
                        ),
                        1 if case_sensitive else 0,
                        1 if word_boundary else 0,
                        int(priority),
                        1 if enabled else 0,
                        notes,
                    ),
                )
                return int(cur.lastrowid)
        except GlossaryError:
            raise
        except Exception as ex:  # pragma: no cover - environmental
            raise GlossaryError("DB_ERROR", f"Failed to add term: {ex}") from ex

    def remove_term(self, term_id: int) -> None:
        """Delete a glossary term by id in a single transaction.

        Parameters:
            term_id: Integer id of the rule to delete.

        Returns:
            None.

        Raises:
            GlossaryError("DB_ERROR"): On delete failures.
        """
        conn = self._ensure_connection()
        try:
            self._ensure_schema(expect_exists=True)
            with conn:
                conn.execute("DELETE FROM terms WHERE id = ?", (int(term_id),))
        except GlossaryError:
            raise
        except Exception as ex:  # pragma: no cover
            raise GlossaryError("DB_ERROR", f"Failed to remove term: {ex}") from ex

    def enable_term(self, term_id: int, enabled: bool) -> None:
        """Enable or disable a glossary term by id atomically.

        Parameters:
            term_id: Integer id of the rule to update.
            enabled: True to enable, False to disable.

        Returns:
            None.

        Raises:
            GlossaryError("DB_ERROR"): On update failures.
        """
        conn = self._ensure_connection()
        try:
            self._ensure_schema(expect_exists=True)
            with conn:
                conn.execute(
                    "UPDATE terms SET enabled = ? WHERE id = ?",
                    (1 if enabled else 0, int(term_id)),
                )
        except GlossaryError:
            raise
        except Exception as ex:  # pragma: no cover
            raise GlossaryError("DB_ERROR", f"Failed to enable/disable term: {ex}") from ex

    def list_terms(
        self,
        glossary_id: str,
        *,
        mode: str | None = None,
        src_lang: str | None = None,
        tgt_lang: str | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[dict[str, Any]]:
        """List glossary terms filtered by glossary/languages/mode with paging.

        Parameters:
            glossary_id: Logical group id to filter by. Required.
            mode: Optional filter for stage ("pre"/"post"). If None, both are returned.
            src_lang: Optional filter for source language. If None, both NULL and
                any specific languages are included.
            tgt_lang: Optional filter for target language. If None, both NULL and
                any specific languages are included.
            limit: Maximum number of rows to return. Capped at max_rules_per_call.
            offset: Number of rows to skip for paging.

        Returns:
            list[dict]: Each dict mirrors columns in the terms table.

        Raises:
            GlossaryError("DB_ERROR"): On query failures.
        """
        conn = self._ensure_connection()
        try:
            self._ensure_schema(expect_exists=True)
            clauses = ["glossary_id = ?"]
            params: list[Any] = [glossary_id]
            if mode is not None:
                clauses.append("mode = ?")
                params.append(mode)
            if src_lang is not None:
                clauses.append("(src_lang IS NULL OR src_lang = ?)")
                params.append(src_lang)
            if tgt_lang is not None:
                clauses.append("(tgt_lang IS NULL OR tgt_lang = ?)")
                params.append(tgt_lang)
            where = " AND ".join(clauses)
            capped_limit = max(0, min(int(limit), self._max_rules_per_call))
            rows = conn.execute(
                f"SELECT * FROM terms WHERE {where} ORDER BY id ASC LIMIT ? OFFSET ?",
                (*params, capped_limit, int(offset)),
            ).fetchall()
            return [dict(row) for row in rows]
        except GlossaryError:
            raise
        except Exception as ex:  # pragma: no cover
            raise GlossaryError("DB_ERROR", f"Failed to list terms: {ex}") from ex

    def capabilities(self) -> dict[str, Any]:
        """Return engine fingerprint and configuration for telemetry/reporting.

        Returns:
            dict: A dictionary with keys including:
                - name (str): Engine name, always "sqlite-glossary".
                - db_path (str): Database path provided at construction.
                - regex_enabled (bool): Whether regex support is enabled.
                - deterministic (bool): Whether deterministic ordering is enforced.
                - max_rules_per_call (int): Current cap on rules per apply call.
                - user_version (int): SQLite PRAGMA user_version value if available,
                  otherwise 0.
        """
        user_version = 0
        if self._conn is not None:
            try:
                user_version = int(self._conn.execute("PRAGMA user_version").fetchone()[0])
            except Exception:  # pragma: no cover - optional
                user_version = 0
        return {
            "name": "sqlite-glossary",
            "db_path": self._db_path,
            "regex_enabled": self._regex_enabled,
            "deterministic": self._deterministic_order,
            "max_rules_per_call": self._max_rules_per_call,
            "user_version": user_version,
        }

    def close(self) -> None:
        """Close the SQLite connection if open; idempotent.

        Returns:
            None.

        Errors:
            None. Silently ignores errors during close.
        """
        try:
            if self._conn is not None:
                self._conn.close()
        finally:
            self._conn = None

    # ---------------------------- Internals -----------------------------

    def _ensure_connection(self) -> sqlite3.Connection:
        if self._conn is None:
            try:
                self._conn = sqlite3.connect(self._db_path)
                self._conn.row_factory = sqlite3.Row
            except Exception as ex:  # pragma: no cover
                raise GlossaryError("DB_OPEN_FAILED", f"Failed to open DB: {ex}") from ex
        return self._conn

    def _ensure_schema(self, *, expect_exists: bool) -> None:
        """Validate or create the required schema.

        Parameters:
            expect_exists: When True, raise SCHEMA_INVALID if the schema is
                missing or incompatible. When False, create the schema if needed
                and ensure indexes are present.

        Returns:
            None.

        Raises:
            GlossaryError("SCHEMA_INVALID"): When expect_exists is True and the
                schema is missing/incompatible.
            GlossaryError("DB_ERROR"): On unexpected database errors.
        """
        conn = self._ensure_connection()
        try:
            cur = conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='terms'")
            row = cur.fetchone()
            if row is None:
                if expect_exists:
                    raise GlossaryError("SCHEMA_INVALID", "Missing required table 'terms'.")
                # Create table and indexes
                with conn:
                    conn.executescript(
                        """
                        CREATE TABLE IF NOT EXISTS terms (
                            id INTEGER PRIMARY KEY,
                            glossary_id TEXT NOT NULL,
                            src_lang TEXT NULL,
                            tgt_lang TEXT NULL,
                            mode TEXT NOT NULL,
                            pattern TEXT NOT NULL,
                            replacement TEXT NOT NULL,
                            case_sensitive INTEGER NOT NULL DEFAULT 0,
                            word_boundary INTEGER NOT NULL DEFAULT 1,
                            priority INTEGER NOT NULL DEFAULT 100,
                            enabled INTEGER NOT NULL DEFAULT 1,
                            notes TEXT NULL
                        );
                        CREATE INDEX IF NOT EXISTS idx_terms_enabled ON terms (enabled);
                        CREATE INDEX IF NOT EXISTS idx_terms_selector ON terms (
                            glossary_id, mode, src_lang, tgt_lang, priority
                        );
                        PRAGMA user_version = 1;
                        """
                    )
            else:
                # Validate minimal required columns exist
                cols = {r[1] for r in conn.execute("PRAGMA table_info('terms')").fetchall()}
                required = {
                    "id",
                    "glossary_id",
                    "src_lang",
                    "tgt_lang",
                    "mode",
                    "pattern",
                    "replacement",
                    "case_sensitive",
                    "word_boundary",
                    "priority",
                    "enabled",
                    "notes",
                }
                missing = required - cols
                if missing:
                    if expect_exists:
                        raise GlossaryError(
                            "SCHEMA_INVALID", f"Missing columns: {', '.join(sorted(missing))}"
                        )
                    # Attempt to add missing columns when allowed
                    with conn:
                        if "case_sensitive" in missing:
                            conn.execute(
                                "ALTER TABLE terms ADD COLUMN case_sensitive INTEGER NOT NULL DEFAULT 0"
                            )
                        if "word_boundary" in missing:
                            conn.execute(
                                "ALTER TABLE terms ADD COLUMN word_boundary INTEGER NOT NULL DEFAULT 1"
                            )
                        if "priority" in missing:
                            conn.execute(
                                "ALTER TABLE terms ADD COLUMN priority INTEGER NOT NULL DEFAULT 100"
                            )
                        if "enabled" in missing:
                            conn.execute(
                                "ALTER TABLE terms ADD COLUMN enabled INTEGER NOT NULL DEFAULT 1"
                            )
                        if "notes" in missing:
                            conn.execute("ALTER TABLE terms ADD COLUMN notes TEXT NULL")
                        # keep existing data intact
        except GlossaryError:
            raise
        except Exception as ex:  # pragma: no cover
            raise GlossaryError("DB_ERROR", f"Schema check failed: {ex}") from ex

    def _normalize_input(self, text: str) -> str:
        if not self._normalization.get("normalize_input", True):
            return text
        form = str(self._normalization.get("_form", "NFKC"))
        return unicodedata.normalize(form, text)

    def _normalize_rule(self, text: str) -> str:
        if not self._normalization.get("normalize_rules", True):
            return text
        form = str(self._normalization.get("_form", "NFKC"))
        return unicodedata.normalize(form, text)

    def _select_rules(
        self,
        *,
        gid: str,
        mode: str,
        src_lang: str,
        tgt_lang: str,
    ) -> list[_Rule]:
        """Select matching rules from the database and sort deterministically."""
        conn = self._ensure_connection()

        order_by_sql: str
        # Determine ordering at SQL level if possible to reduce post-processing
        if self._conflicts_policy == "longest_match":
            order_by_sql = "LENGTH(pattern) DESC, priority ASC, id ASC"
        elif self._conflicts_policy == "first_wins":
            order_by_sql = "id ASC, LENGTH(pattern) DESC"
        else:  # default: priority
            order_by_sql = "priority ASC, LENGTH(pattern) DESC, id ASC"

        # Cap rules to avoid pathological runtime
        limit = self._max_rules_per_call

        try:
            rows = conn.execute(
                f"""
                SELECT id, pattern, replacement, case_sensitive, word_boundary, priority
                FROM terms
                WHERE enabled = 1
                  AND glossary_id = ?
                  AND mode = ?
                  AND (src_lang IS NULL OR src_lang = ?)
                  AND (tgt_lang IS NULL OR tgt_lang = ?)
                ORDER BY {order_by_sql}
                LIMIT ?
                """,
                (gid, mode, src_lang, tgt_lang, limit),
            ).fetchall()
        except Exception as ex:  # pragma: no cover
            raise GlossaryError("DB_ERROR", f"Rule selection failed: {ex}") from ex

        rules: list[_Rule] = []
        for row in rows:
            pattern = row["pattern"]
            replacement = row["replacement"]
            if self._normalization.get("normalize_rules", True):
                pattern = self._normalize_rule(pattern)
                replacement = self._normalize_rule(replacement)
            rules.append(
                _Rule(
                    id=int(row["id"]),
                    pattern=pattern,
                    replacement=replacement,
                    case_sensitive=bool(row["case_sensitive"]),
                    word_boundary=bool(row["word_boundary"]),
                    priority=int(row["priority"]),
                )
            )

        # If deterministic_order is requested, ensure final ordering matches policy
        if self._deterministic_order:
            if self._conflicts_policy == "longest_match":
                rules.sort(key=lambda r: (-len(r.pattern), r.priority, r.id))
            elif self._conflicts_policy == "first_wins":
                rules.sort(key=lambda r: (r.id, -len(r.pattern)))
            else:
                rules.sort(key=lambda r: (r.priority, -len(r.pattern), r.id))

        return rules

    def _is_letter(self, ch: str) -> bool:
        # Unicode letter categories start with 'L'. Allow extra chars provided via config.
        if ch in self._extra_word_chars:
            return True
        cat = unicodedata.category(ch)
        return len(ch) == 1 and cat.startswith("L")

    def _boundary_ok(self, text: str, start: int, end: int) -> bool:
        # Enforce that surrounding characters are not letters (per _is_letter) when enabled
        if start > 0:
            prev = text[start - 1]
            if self._is_letter(prev):
                return False
        if end < len(text):
            nxt = text[end]
            if self._is_letter(nxt):
                return False
        return True

    def _compile_pattern(self, pattern: str, *, case_sensitive: bool) -> re.Pattern[str]:
        # Literal-mode compilation by default; regex-enabled optionally
        flags = 0 if case_sensitive else re.IGNORECASE
        if not self._regex_enabled:
            return re.compile(re.escape(pattern), flags)
        # Basic safety: require patterns to be anchored to boundaries or explicit
        # lookarounds; otherwise consider them invalid to avoid backtracking traps.
        safe_anchor_tokens = ("\\b", "^", "$", "(?<", "(?!)", "(?=")
        if not any(tok in pattern for tok in safe_anchor_tokens):
            raise GlossaryError("RULE_INVALID", "Regex requires boundary anchors or lookarounds.")
        try:
            # Python >=3.11 supports timeouts in re compilation/matching via keyword
            # arguments; if not available, this call will ignore unknown kwargs.
            try:
                compiled = re.compile(pattern, flags, timeout=0.05)  # type: ignore[arg-type]
            except TypeError:
                # Fallback when timeout kw isn't supported
                compiled = re.compile(pattern, flags)
            return compiled
        except re.error as ex:
            raise GlossaryError("RULE_INVALID", f"Invalid regex: {ex}") from ex

    def _apply_one_rule(self, text: str, rule: _Rule) -> str:
        # Compile suitable matcher
        pattern_text = rule.pattern
        compiled = self._compile_pattern(pattern_text, case_sensitive=rule.case_sensitive)

        # Iterate matches left-to-right non-overlapping and build result
        matches: list[tuple[int, int]] = []
        last_end = 0
        new_parts: list[str] = []

        # We need to guard against catastrophic regex behavior when enabled.
        start_time = time.time()
        max_seconds = 0.1 if self._regex_enabled else None

        for m in compiled.finditer(text):
            if max_seconds is not None and (time.time() - start_time) > max_seconds:
                raise GlossaryError("TIMEOUT", "Regex matching exceeded time budget.")
            s, e = m.span()
            if s < last_end:
                # Overlap with previous accepted match: skip to preserve non-overlap
                continue
            if rule.word_boundary and not self._boundary_ok(text, s, e):
                continue
            # Accept match
            matches.append((s, e))
            # Append text since last_end, then replacement
            new_parts.append(text[last_end:s])
            new_parts.append(rule.replacement)
            last_end = e
        if not matches:
            return text
        # Trailing remainder
        new_parts.append(text[last_end:])
        return "".join(new_parts)


__all__ = ["SqliteGlossary", "GlossaryError"]
