"""
UTF-8 Markdown normalizer ensuring canonical Unicode, EOL policy, and safe whitespace cleanup.

Capabilities:
- Enforces UTF-8-safe text, strips BOM, and normalizes line endings (LF by default).
- Applies Unicode canonicalization (default NFKC) and NBSP handling.
- Removes control chars, converts tabs per policy, trims trailing spaces, collapses blank lines, and ensures final newline (per EOL policy).
- Protects Markdown structures (fenced code blocks, inline code, and table lines) so their bytes remain identical.
- Deterministic and idempotent: running twice yields identical output with zero changes in the second report.
- Predictable performance: linear in input size, with a configurable size cap.

This module is content-agnostic and does not log text content. It reports only decisions and counts.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any

__all__ = [
    "NormalizerOptions",
    "NormalizationReport",
    "NormalizationError",
    "normalize_text",
    "normalize_doc",
    "descriptor",
]


# -----------------------------
# Data objects and Exceptions
# -----------------------------


@dataclass
class NormalizerOptions:
    """Options controlling normalization behavior.

    Attributes:
        unicode_form: Canonicalization strategy. One of "NFKC", "NFC", or "none".
        normalize_nbsp: Behavior for NBSP (U+00A0). "space" converts to ASCII space
            in unprotected regions; "keep" leaves intact.
        eol_policy: End-of-line policy: "lf", "crlf", or "keep".
        strip_control_chars: Whether to remove non-printing control characters
            outside protected regions (tabs may be preserved per tabs_policy).
        tabs_policy: How to handle TAB characters: "keep", "spaces_outside_code",
            or "spaces_everywhere". Tabs expand to spaces using tab_width.
        tab_width: Number of spaces for each TAB expansion. Must be >= 1.
        trim_trailing_spaces: Trailing-space trimming policy: "none", "all", or
            "safe" (skips trimming inside code fences and table lines).
        collapse_blank_lines_to: Maximum number of consecutive blank lines. None to
            disable collapsing.
        ensure_final_newline: Whether to ensure a single terminal LF at the end.
        guard_code_fences: Protect fenced code blocks delimited by ``` or ~~~.
        guard_inline_code: Protect inline code spans delimited by backticks.
        guard_tables: Protect Markdown table-like lines containing column pipes
            (including header separators like | --- |).
        max_text_mb: Maximum text size in megabytes before raising an error.
    """

    unicode_form: str = "NFKC"
    normalize_nbsp: str = "space"
    eol_policy: str = "lf"

    strip_control_chars: bool = True

    tabs_policy: str = "spaces_outside_code"
    tab_width: int = 4

    trim_trailing_spaces: str = "safe"
    collapse_blank_lines_to: int | None = 2
    ensure_final_newline: bool = True

    guard_code_fences: bool = True
    guard_inline_code: bool = True
    guard_tables: bool = True

    max_text_mb: int = 50


@dataclass
class NormalizationReport:
    """Normalization report with JSON-serializable fields.

    Attributes:
        bom_removed: Whether a UTF-8 BOM was stripped from the start of text.
        unicode_form_before: Observed form descriptor before normalization.
        unicode_form_after: Reported form setting applied after normalization.
        eol_before: Detected EOL style: LF|CRLF|CR|MIXED|UNKNOWN.
        eol_after: EOL style of the output measured after normalization.
        control_chars_removed: Count of control characters removed.
        tabs_converted: Count of TAB characters converted to spaces.
        trailing_spaces_trimmed: Count of trailing space characters removed.
        blank_lines_collapsed: Count of blank lines removed due to collapsing.
        final_newline_added: Whether a final newline was added.
        protected_regions: Counts of protected regions by type.
        warnings: List of non-fatal warnings.
    """

    bom_removed: bool = False
    unicode_form_before: str = "UNKNOWN"
    unicode_form_after: str = "UNKNOWN"
    eol_before: str = "UNKNOWN"
    eol_after: str = "UNKNOWN"

    control_chars_removed: int = 0
    tabs_converted: int = 0
    trailing_spaces_trimmed: int = 0
    blank_lines_collapsed: int = 0
    final_newline_added: bool = False

    protected_regions: dict[str, int] = None  # type: ignore[assignment]
    warnings: list[str] = None  # type: ignore[assignment]

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        # Ensure defaults for mutable fields
        if d["protected_regions"] is None:
            d["protected_regions"] = {"code_fences": 0, "inline_code": 0, "table_lines": 0}
        if d["warnings"] is None:
            d["warnings"] = []
        return d


class NormalizationError(RuntimeError):
    """Raised on hard failures during normalization.

    Args:
        code: A stable error code. Expected values include:
            - "input_too_large"
            - "invalid_option"
            - "decode_required"
            - "malformed_fence_strict"
            - "internal_invariant"
        message: Human-readable message describing the failure and suggested fix.
    """

    def __init__(self, code: str, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# -----------------------------
# Public API
# -----------------------------


def normalize_text(text: str, options: NormalizerOptions) -> tuple[str, dict[str, Any]]:
    """Normalize raw Markdown text according to the provided options.

    The function protects Markdown structures (fenced code, inline code, table
    lines) so that their bytes remain unchanged, and applies normalization to the
    remaining text only. The output is deterministic and idempotent.

    Args:
        text: Raw Markdown text to normalize.
        options: NormalizerOptions controlling Unicode form, EOL policy, whitespace,
            protections, and limits. All fields are validated; invalid inputs raise.

    Returns:
        A 2-tuple of (normalized_text, report_dict). The report is JSON-serializable
        and contains counts, flags, and warnings.

    Raises:
        NormalizationError: If the text exceeds size limits or options are invalid,
            or on unexpected internal invariants.
    """
    _validate_options(options)

    # Size guard (estimate via UTF-8 bytes)
    try:
        size_bytes = len(text.encode("utf-8"))
    except Exception:  # pragma: no cover - highly unlikely with Python str
        raise NormalizationError("decode_required", "Input must be a valid text string")
    limit = options.max_text_mb * 1024 * 1024
    if size_bytes > limit:
        raise NormalizationError(
            "input_too_large",
            f"Input is {size_bytes} bytes; limit is {limit} bytes ({options.max_text_mb} MB)",
        )

    report = NormalizationReport(
        protected_regions={"code_fences": 0, "inline_code": 0, "table_lines": 0},
        warnings=[],
    )

    # BOM handling
    bom = "\ufeff"
    if text.startswith(bom):
        report.bom_removed = True
        text = text[len(bom) :]

    # EOL scan before changes
    report.eol_before = _classify_eol(text)

    # Collect protected regions
    protected_spans: list[tuple[int, int, str]] = []
    if options.guard_code_fences:
        fences, unterminated = _find_fenced_code_spans(text)
        protected_spans.extend([(s, e, "code_fence") for s, e in fences])
        report.protected_regions["code_fences"] = len(fences)
        if unterminated:
            report.warnings.append("unterminated code fence detected; left untouched")
    if options.guard_inline_code:
        spans = _find_inline_code_spans(text, exclude=protected_spans)
        protected_spans.extend([(s, e, "inline_code") for s, e in spans])
        report.protected_regions["inline_code"] = len(spans)
    if options.guard_tables:
        spans = _find_table_line_spans(text, exclude=protected_spans)
        protected_spans.extend([(s, e, "table_line") for s, e in spans])
        report.protected_regions["table_lines"] = len(spans)

    # Merge and sort spans
    protected_spans = _merge_spans(protected_spans)

    # Compute unicode_form_before heuristically (best-effort)
    report.unicode_form_before = _guess_unicode_form(text)

    # Transform unprotected slices
    out_parts: list[str] = []
    last = 0
    for s, e, _kind in protected_spans:
        if s > last:
            chunk = text[last:s]
            before_len = len(chunk)
            chunk, stats_chunk = _transform_chunk(chunk, options)
            report.control_chars_removed += stats_chunk["control_removed"]
            report.tabs_converted += stats_chunk["tabs_converted"]
            report.trailing_spaces_trimmed += stats_chunk["trailing_trimmed"]
            report.blank_lines_collapsed += stats_chunk["blank_collapsed"]
            out_parts.append(chunk)
            # Sanity: avoid infinite loop
            if before_len == 0 and chunk:
                pass
        out_parts.append(text[s:e])  # protected region as-is
        last = e
    if last < len(text):
        chunk = text[last:]
        chunk, stats_chunk = _transform_chunk(chunk, options)
        report.control_chars_removed += stats_chunk["control_removed"]
        report.tabs_converted += stats_chunk["tabs_converted"]
        report.trailing_spaces_trimmed += stats_chunk["trailing_trimmed"]
        report.blank_lines_collapsed += stats_chunk["blank_collapsed"]
        out_parts.append(chunk)

    normalized = "".join(out_parts)

    # EOL conversion may be desired at the whole-text level for policy compliance
    # without altering protected bytes internally. We only convert on unprotected
    # slices above. The final text may therefore still contain mixed EOLs when
    # protected regions carry different endings. We record actual final state.
    report.eol_after = _classify_eol(normalized)

    # Unicode normalization form is reported as the configured target.
    report.unicode_form_after = options.unicode_form

    # Ensure a single final newline if requested
    if options.ensure_final_newline:
        # Choose newline sequence based on policy; default to LF.
        eol_ch = "\r\n" if options.eol_policy == "crlf" else "\n"
        if not normalized.endswith("\n") and not normalized.endswith("\r\n"):
            normalized += eol_ch
            report.final_newline_added = True
        # Ensure exactly one terminal newline sequence
        twice = eol_ch + eol_ch
        while normalized.endswith(twice):
            normalized = normalized[: -len(eol_ch)]

    # Record final EOL state after any adjustments
    report.eol_after = _classify_eol(normalized)

    return normalized, report.to_dict()


def normalize_doc(doc: Any, options: NormalizerOptions) -> Any:
    """Normalize a MarkdownDoc-like object in place and return it.

    The function expects an object with ``text_md`` (string), optional ``encoding``,
    and ``meta`` (dict-like). It applies normalization to ``text_md``, sets
    ``encoding = "utf-8"``, and attaches the normalization report under
    ``doc.meta["normalization"]`` including a compact summary field.

    Args:
        doc: A MarkdownDoc-like object with attributes ``text_md`` and ``meta``.
            Duck-typed; any object exposing these fields is accepted.
        options: NormalizerOptions to apply.

    Returns:
        The same doc instance, after mutation of ``text_md``, ``encoding``, and
        ``meta["normalization"]``.

    Raises:
        NormalizationError: If normalization fails (e.g., input too large, invalid
            options).
    """
    text = getattr(doc, "text_md", getattr(doc, "text", ""))
    norm_text, report = normalize_text(str(text), options)

    # Attach
    setattr(doc, "text_md", norm_text)
    setattr(doc, "encoding", "utf-8")

    meta = getattr(doc, "meta", None)
    if meta is None:
        meta = {}
        try:
            setattr(doc, "meta", meta)
        except Exception:
            # If meta attribute is not settable, skip attachment silently
            pass
    if isinstance(meta, dict):
        summary = (
            f"{report.get('unicode_form_after','')}, "
            f"EOL={report.get('eol_after','')}, "
            f"trimmed={report.get('trailing_spaces_trimmed',0)}, "
            f"tabs→spaces={report.get('tabs_converted',0)}, "
            f"bom={report.get('bom_removed',False)}"
        )
        meta.setdefault("normalization", {})
        meta["normalization"].update(report)
        meta["normalization"]["summary"] = summary

    return doc


def descriptor(options: NormalizerOptions) -> str:
    """Return a short, stable descriptor string for logging and wiring.

    Example:
        utf8_normalizer(unicode=NFKC,eol=LF,trim=safe,tabs=spaces_outside_code)

    Args:
        options: NormalizerOptions to describe.

    Returns:
        A stable descriptor string capturing salient configuration.
    """
    eol = options.eol_policy.upper()
    return (
        "utf8_normalizer("
        f"unicode={options.unicode_form},"
        f"eol={eol},"
        f"trim={options.trim_trailing_spaces},"
        f"tabs={options.tabs_policy}"
        ")"
    )


# -----------------------------
# Internal helpers
# -----------------------------


_EOL_RE_CRLF = re.compile(r"\r\n")
_EOL_RE_CR = re.compile(r"\r(?!\n)")


def _classify_eol(text: str) -> str:
    has_crlf = bool(_EOL_RE_CRLF.search(text))
    has_cr = bool(_EOL_RE_CR.search(text))
    has_lf = "\n" in text
    kinds = sum([has_crlf, has_cr, has_lf])
    if kinds == 0:
        return "UNKNOWN"
    if has_crlf and not has_cr and not (has_lf and not has_crlf):
        # Note: presence of LF with CRLF is normal because CRLF contains LF.
        # We treat pure CRLF only when no lone LF exists.
        if not _has_lone_lf(text):
            return "CRLF"
    if has_cr and not has_lf and not has_crlf:
        return "CR"
    if has_lf and not _has_cr(text):
        return "LF"
    return "MIXED"


def _has_lone_lf(text: str) -> bool:
    # A lone LF exists when there is an LF not preceded by CR
    idx = text.find("\n")
    while idx != -1:
        if idx == 0 or text[idx - 1] != "\r":
            return True
        idx = text.find("\n", idx + 1)
    return False


def _has_cr(text: str) -> bool:
    return bool(_EOL_RE_CR.search(text))


def _validate_options(o: NormalizerOptions) -> None:
    enums = {
        "unicode_form": {"NFKC", "NFC", "none"},
        "normalize_nbsp": {"space", "keep"},
        "eol_policy": {"lf", "crlf", "keep"},
        "tabs_policy": {"keep", "spaces_outside_code", "spaces_everywhere"},
        "trim_trailing_spaces": {"none", "all", "safe"},
    }
    for field, allowed in enums.items():
        val = getattr(o, field)
        if val not in allowed:
            raise NormalizationError(
                "invalid_option",
                f"{field}={val!r} not in {sorted(allowed)}",
            )
    if not isinstance(o.tab_width, int) or o.tab_width < 1:
        raise NormalizationError("invalid_option", "tab_width must be an integer >= 1")
    if o.collapse_blank_lines_to is not None:
        if not isinstance(o.collapse_blank_lines_to, int) or o.collapse_blank_lines_to < 0:
            raise NormalizationError(
                "invalid_option", "collapse_blank_lines_to must be None or int >= 0"
            )
    if not isinstance(o.max_text_mb, int) or o.max_text_mb <= 0:
        raise NormalizationError("invalid_option", "max_text_mb must be a positive integer")


def _find_fenced_code_spans(text: str) -> tuple[list[tuple[int, int]], bool]:
    # Find fenced code blocks delimited by ``` or ~~~ with optional language tag.
    # Returns list of (start, end) spans including line endings of closing fence.
    open_re = re.compile(r"^(?P<fence>(`{3,}|~{3,})).*$", re.MULTILINE)
    unterminated = False
    spans: list[tuple[int, int]] = []
    pos = 0
    n = len(text)
    while pos < n:
        m = open_re.search(text, pos)
        if not m:
            break
        fence = m.group("fence")
        fence_char = fence[0]
        # closing fence: same char, at least as many, start of line
        close_re = re.compile(rf"^(?:{re.escape(fence_char)}{{{len(fence)},}}).*$", re.MULTILINE)
        close_m = close_re.search(text, m.end())
        if not close_m:
            spans.append((m.start(), n))
            unterminated = True
            break
        end_idx = _end_of_line_including_eol(text, close_m.end())
        spans.append((m.start(), end_idx))
        pos = end_idx  # advance past this fenced block
    return spans, unterminated


def _end_of_line_including_eol(text: str, idx_end_of_line_content: int) -> int:
    # Include any \r or \n following this line's content
    i = idx_end_of_line_content
    if i < len(text):
        if text[i : i + 2] == "\r\n":
            return i + 2
        if text[i] in "\r\n":
            return i + 1
    return i


def _intervals_complement(length: int, spans: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    result: list[tuple[int, int]] = []
    last = 0
    for s, e in spans:
        if s > last:
            result.append((last, s))
        last = max(last, e)
    if last < length:
        result.append((last, length))
    return result


def _find_inline_code_spans(
    text: str, exclude: Sequence[tuple[int, int, str]]
) -> list[tuple[int, int]]:
    # Find inline code spans delimited by backticks (`...`) with variable length.
    # Avoid ranges in exclude.
    spans: list[tuple[int, int]] = []
    excluded = [(s, e) for s, e, _ in exclude]
    comp = _intervals_complement(len(text), excluded)
    # Pattern for inline code with n backticks as delimiter
    pattern = re.compile(r"(`+)([^`]*?)\1", re.DOTALL)
    for s, e in comp:
        seg = text[s:e]
        for m in pattern.finditer(seg):
            spans.append((s + m.start(), s + m.end()))
    return spans


_TABLE_HEADER_RE = re.compile(r"^\s*\|?(?:\s*:?-{3,}:?\s*\|)+\s*:?-{3,}:?\s*\|?\s*$")


def _find_table_line_spans(
    text: str, exclude: Sequence[tuple[int, int, str]]
) -> list[tuple[int, int]]:
    # Protect lines that look like Markdown tables (contain '|' columns), including
    # header separators. Avoid ranges in exclude.
    spans: list[tuple[int, int]] = []
    excluded = [(s, e) for s, e, _ in exclude]
    comp = _intervals_complement(len(text), excluded)
    for s, e in comp:
        start = s
        while start < e:
            # Find end of line
            nl = text.find("\n", start, e)
            if nl == -1:
                line_end = e
            else:
                line_end = nl + 1
            line = text[start:line_end]
            # Classify as table if contains at least one '|' and either looks like
            # a header separator or has at least two columns.
            is_table = False
            if "|" in line:
                if _TABLE_HEADER_RE.match(line.rstrip("\r\n")):
                    is_table = True
                else:
                    # Count columns by '|' occurrences excluding leading/trailing pipes
                    content = line.strip()
                    pipes = content.count("|")
                    if pipes >= 2:
                        is_table = True
            if is_table:
                spans.append((start, line_end))
            start = line_end
    return spans


def _merge_spans(spans: Sequence[tuple[int, int, str]]) -> list[tuple[int, int, str]]:
    if not spans:
        return []
    # Sort by start, then by -end to ensure outer spans come first
    spans_sorted = sorted(spans, key=lambda t: (t[0], -t[1]))
    merged: list[tuple[int, int, str]] = []
    cur_s, cur_e, cur_k = spans_sorted[0]
    for s, e, k in spans_sorted[1:]:
        if s <= cur_e:  # overlap or contiguous
            # extend
            if e > cur_e:
                cur_e = e
            # prefer the outermost kind name
        else:
            merged.append((cur_s, cur_e, cur_k))
            cur_s, cur_e, cur_k = s, e, k
    merged.append((cur_s, cur_e, cur_k))
    return merged


_CONTROL_CHAR_RE = re.compile(
    # All C0 controls except TAB (0x09) and LF/CR handled by EOL policy.
    """
    [\x00-\x08\x0b\x0c\x0e-\x1f\x7f]
    """,
    re.VERBOSE,
)


def _transform_chunk(chunk: str, options: NormalizerOptions) -> tuple[str, dict[str, int]]:
    """Apply all transformations to a chunk and collect stats.

    Returns:
        chunk_out, stats where stats has keys: control_removed, tabs_converted,
        trailing_trimmed, blank_collapsed.
    """
    stats = {
        "control_removed": 0,
        "tabs_converted": 0,
        "trailing_trimmed": 0,
        "blank_collapsed": 0,
    }

    # 1) EOL policy on this chunk
    if options.eol_policy != "keep":
        if options.eol_policy == "lf":
            chunk = chunk.replace("\r\n", "\n").replace("\r", "\n")
        elif options.eol_policy == "crlf":
            # Normalize to LF then convert to CRLF
            chunk = chunk.replace("\r\n", "\n").replace("\r", "\n")
            chunk = chunk.replace("\n", "\r\n")

    # 2) Control characters removal (excluding TAB if kept)
    def _remove_controls(s: str) -> tuple[str, int]:
        removed = 0

        def repl(m: re.Match[str]) -> str:
            nonlocal removed
            ch = m.group(0)
            if ch == "\t" and options.tabs_policy == "keep":
                return ch
            removed += 1
            return ""

        return _CONTROL_CHAR_RE.sub(repl, s), removed

    if options.strip_control_chars:
        chunk, removed = _remove_controls(chunk)
        stats["control_removed"] += removed

    # 3) Tabs handling
    if options.tabs_policy in {"spaces_outside_code", "spaces_everywhere"}:
        if "\t" in chunk:
            stats["tabs_converted"] += chunk.count("\t")
            chunk = _expand_tabs(chunk, options.tab_width)

    # 4) NBSP handling
    if options.normalize_nbsp == "space":
        if "\u00a0" in chunk:
            chunk = chunk.replace("\u00a0", " ")

    # 5) Unicode normalization
    if options.unicode_form in {"NFKC", "NFC"}:
        try:
            chunk = unicodedata.normalize(options.unicode_form, chunk)
        except Exception as e:  # pragma: no cover - extremely unlikely
            raise NormalizationError("internal_invariant", f"Unicode normalization failed: {e}")

    # 6) Trailing spaces trimming
    if options.trim_trailing_spaces != "none":
        # Trim trailing spaces at end-of-line; for this chunk we can operate line-wise
        # without touching protected lines (since this is an unprotected chunk).
        lines = _split_preserve_endl(chunk)
        for i, (ln, eol) in enumerate(lines):
            stripped = ln.rstrip(" \t")
            stats["trailing_trimmed"] += len(ln) - len(stripped)
            lines[i] = (stripped, eol)
        chunk = "".join(a + b for a, b in lines)

    # 7) Collapse blank lines
    if options.collapse_blank_lines_to is not None:
        max_blanks = options.collapse_blank_lines_to
        # Convert to a simple line model on LF; CRLF already handled by EOL policy.
        # We treat a blank line as a line that is empty after stripping spaces/tabs.
        lines = _split_preserve_endl(chunk)
        blank_run = 0
        out: list[tuple[str, str]] = []
        for ln, eol in lines:
            if ln.strip(" \t") == "":
                blank_run += 1
                if blank_run <= max_blanks:
                    out.append((ln, eol))
                else:
                    # dropping this blank line
                    stats["blank_collapsed"] += 1
            else:
                blank_run = 0
                out.append((ln, eol))
        chunk = "".join(a + b for a, b in out)

    return chunk, stats


def _expand_tabs(s: str, tab_width: int) -> str:
    # Simple, deterministic expansion: replace each \t with tab_width spaces
    return s.replace("\t", " " * tab_width)


def _split_preserve_endl(s: str) -> list[tuple[str, str]]:
    # Split into (line_without_eol, eol) pairs; last line may have empty eol.
    out: list[tuple[str, str]] = []
    i = 0
    n = len(s)
    while i < n:
        j = s.find("\n", i)
        if j == -1:
            # Maybe CR-only line ending
            k = s.find("\r", i)
            if k == -1:
                out.append((s[i:], ""))
                break
            else:
                # CR-only: include CR and continue
                out.append((s[i:k], "\r"))
                i = k + 1
                continue
        # Handle CRLF
        if j > 0 and s[j - 1] == "\r":
            out.append((s[i : j - 1], "\r\n"))
        else:
            out.append((s[i:j], "\n"))
        i = j + 1
    if n == 0:
        return [("", "")]
    return out


def _guess_unicode_form(text: str) -> str:
    # Heuristic: try round-trip to NFC/NFKC and compare
    try:
        if unicodedata.normalize("NFKC", text) == text:
            return "NFKC"
        if unicodedata.normalize("NFC", text) == text:
            return "NFC"
    except Exception:
        pass
    return "UNKNOWN"
