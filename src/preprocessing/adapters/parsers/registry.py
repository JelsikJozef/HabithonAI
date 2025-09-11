from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, MutableMapping, Optional, Sequence, Tuple
import mimetypes

try:  # Prefer but do not require python-magic; stay offline-only
    import magic  # type: ignore
except Exception:  # pragma: no cover
    magic = None  # type: ignore

from ...domain.errors import ParserNotFoundError
from ...domain.models import RawDocument


def _normalize_ext(ext: str | None) -> str:
    if not ext:
        return ""
    s = ext.strip().lower()
    if s.startswith("."):
        s = s[1:]
    return s


def _parser_name(obj: Any) -> str:
    """Best-effort human-readable adapter identifier for logs/audit."""
    if obj is None:
        return "<none>"
    # Prefer explicit .name attribute when provided
    name = getattr(obj, "name", None)
    if isinstance(name, str) and name:
        return name
    # Fall back to class name
    try:
        return obj.__class__.__name__
    except Exception:
        return str(type(obj))


@dataclass(frozen=True)
class Decision:
    """Decision trace for observability and audit.

    Attributes
    ----------
    path : str | None
        File path used for the decision (when available).
    ext : str
        Normalized extension (lowercase, without leading dot). May be empty.
    mime : str | None
        MIME type used or detected during the decision.
    candidates : Tuple[str, ...]
        Candidate parser names considered (after filtering/priority).
    selected : str | None
        Chosen parser name or None when no parser found.
    reason : str
        Short explanation of why the parser was selected ("by_ext", "by_mime", "fallback").
    """

    path: str | None
    ext: str
    mime: str | None
    candidates: Tuple[str, ...]
    selected: str | None
    reason: str


class ParserRegistry:
    """Central router for converting files to Markdown.

    Responsibilities
    ----------------
    - Maintain mapping "extension → parser adapters" with priority.
    - Optionally use MIME sniffing when extension is missing/unreliable.
    - Enforce deterministic choice: same input → same parser.
    - Expose a minimal port for the pipeline (get(ext), supported()).
    - Provide richer helpers (choose(...)) for callers that have full file info.

    Contracts and assumptions
    -------------------------
    - A "parser" is any object exposing parse(raw: RawDocument) -> ParsedDocument.
      The registry does not call vendor SDKs; it only returns the adapter instance.
    - Keys are lowercase extensions without leading dot (e.g., "pdf", "docx").
    - The registry does not perform heavy I/O; no file reading/parsing here.

    Configuration knobs
    -------------------
    static_map : Mapping[str, object | Sequence[object]]
        Built-in mapping from extension to one or more parser adapters in priority order.
    dynamic_map : Mapping[str, object | Sequence[object]] | None
        Optional map merged on top of static_map to add/override parsers without code changes.
    preferences : Mapping[str, Sequence[str]] | None
        Optional per-extension preference of parser names (class name or .name attribute).
        When provided, candidates are re-ordered to respect the given priority.
    disabled : Iterable[str] | None
        Optional set/list of parser names to disable globally (by name/class).
    allow_plain_fallback : bool
        If True and no known parser is available, allow returning a plain-text/heuristic parser
        when present in the map under keys like "txt" or "plain".
    sniff_mime : bool
        If True, use MIME sniffing (built-in mimetypes and optional libmagic) when the extension
        is unknown or mismatched.
    cache_decisions : bool
        If True, keep a lightweight in-run cache of decisions keyed by absolute path string.
    """

    # Minimal MIME→ext map for common office formats; complements mimetypes
    _MIME_TO_EXT: Mapping[str, str] = {
        "application/pdf": "pdf",
        "application/msword": "doc",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": "docx",
        "application/vnd.ms-excel": "xls",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": "xlsx",
        "text/plain": "txt",
        "image/jpeg": "jpg",
        "image/png": "png",
        "message/rfc822": "msg",
    }

    def __init__(
        self,
        static_map: Mapping[str, Any] | None = None,
        *,
        dynamic_map: Mapping[str, Any] | None = None,
        preferences: Mapping[str, Sequence[str]] | None = None,
        disabled: Iterable[str] | None = None,
        allow_plain_fallback: bool = False,
        sniff_mime: bool = True,
        cache_decisions: bool = True,
    ) -> None:
        self._map: Dict[str, List[Any]] = {}
        self._prefs: Dict[str, Tuple[str, ...]] = {k.lower(): tuple(v) for k, v in (preferences or {}).items()}
        self._disabled: Tuple[str, ...] = tuple((disabled or []))
        self._allow_plain = bool(allow_plain_fallback)
        self._sniff_mime = bool(sniff_mime)
        self._cache_enabled = bool(cache_decisions)
        self._cache: MutableMapping[str, Decision] = {}
        self._last: Optional[Decision] = None

        def _ingest(src: Mapping[str, Any] | None) -> None:
            if not src:
                return
            for k, v in src.items():
                ext = _normalize_ext(k)
                if isinstance(v, (list, tuple)):
                    items = list(v)
                else:
                    items = [v]
                # Normalize to list and append/override existing entries
                # Latest wins (dynamic overrides static by replacement at front)
                if ext not in self._map:
                    self._map[ext] = []
                # Prepend to give higher priority to dynamic overrides
                self._map[ext] = list(items) + [p for p in self._map[ext] if p not in items]

        _ingest(static_map)
        _ingest(dynamic_map)

    # ----- Port methods (minimal surface) -----
    def get(self, ext: str):
        """Return the highest-priority enabled parser for an extension.

        Parameters
        ----------
        ext : str
            File extension. Case-insensitive; leading dot is allowed.

        Returns
        -------
        object
            Parser adapter instance exposing parse(raw: RawDocument) -> ParsedDocument.

        Raises
        ------
        ParserNotFoundError
            If no parser is registered (or only disabled) for the given extension.
        """
        norm = _normalize_ext(ext)
        if not norm:
            raise ParserNotFoundError("No parser for empty/unknown extension")
        candidates = self._candidates_for(norm)
        if not candidates:
            raise self._unknown_ext_error(norm)
        return candidates[0]

    def supported(self) -> set[str]:
        """Return the set of supported extensions (lowercase, without dot)."""
        return set(self._map.keys())

    # ----- Rich helpers (optional for callers) -----
    def choose(
        self,
        file: RawDocument | Path | str,
        *,
        mime: str | None = None,
        content_sniff: bool = False,
    ) -> Tuple[object, Decision]:
        """Select the best parser given a file reference and optional MIME.

        Parameters
        ----------
        file : RawDocument | Path | str
            Input reference used to resolve extension and memoize decisions.
        mime : str | None, optional
            Caller-provided MIME when available (e.g., from HTTP upload headers).
        content_sniff : bool, optional
            If True and libmagic is available, attempt content-based MIME langid when
            extension/MIME are inconclusive. Defaults to False.

        Returns
        -------
        (parser, decision) : tuple[object, Decision]
            The chosen parser instance and a Decision record describing the reasoning.

        Raises
        ------
        ParserNotFoundError
            When no suitable parser (including optional fallback) can be selected.
        """
        # Cache by absolute path string when possible
        path_str: Optional[str] = None
        ext: str = ""
        if isinstance(file, RawDocument):
            path_str = str(file.path.resolve()) if file.path else None
            ext = _normalize_ext(file.ext)
        elif isinstance(file, (Path, str)):
            p = Path(str(file))
            path_str = str(p.resolve())
            ext = _normalize_ext(p.suffix)
        else:  # pragma: no cover - defensive
            raise TypeError("file must be RawDocument | Path | str")

        if self._cache_enabled and path_str and path_str in self._cache:
            dec = self._cache[path_str]
            # Return the parser referenced by decision if still valid
            if dec.selected:
                parser = self._first_enabled_for_name(ext or dec.ext, dec.selected)
                if parser is not None:
                    self._last = dec
                    return parser, dec

        # 1) Extension-first
        candidates = self._candidates_for(ext)
        if candidates:
            parser = candidates[0]
            dec = Decision(path_str, ext, mime, tuple(_parser_name(p) for p in candidates), _parser_name(parser), "by_ext")
            self._remember(path_str, dec)
            return parser, dec

        # 2) MIME sniffing (when allowed)
        detected_mime = mime or self._guess_mime_from_path(path_str)
        detected_ext = self._ext_from_mime(detected_mime)
        if self._sniff_mime and detected_ext:
            candidates = self._candidates_for(detected_ext)
            if candidates:
                parser = candidates[0]
                dec = Decision(path_str, detected_ext, detected_mime, tuple(_parser_name(p) for p in candidates), _parser_name(parser), "by_mime")
                self._remember(path_str, dec)
                return parser, dec

        # 3) Optional libmagic content sniff (disabled by default)
        if content_sniff and magic is not None and path_str:
            try:
                m = magic.Magic(mime=True)  # type: ignore[call-arg]
                probed = m.from_file(path_str)  # type: ignore[attr-defined]
                probed_ext = self._ext_from_mime(probed)
                if probed_ext:
                    candidates = self._candidates_for(probed_ext)
                    if candidates:
                        parser = candidates[0]
                        dec = Decision(path_str, probed_ext, probed, tuple(_parser_name(p) for p in candidates), _parser_name(parser), "by_magic")
                        self._remember(path_str, dec)
                        return parser, dec
            except Exception:  # pragma: no cover - best-effort sniff
                pass

        # 4) Fallback: explicit failure or plain-text if allowed
        if self._allow_plain:
            # Try a generic text/heuristic parser if registered under common keys
            for k in ("txt", "text", "plain"):
                candidates = self._candidates_for(k)
                if candidates:
                    parser = candidates[0]
                    dec = Decision(path_str, k, detected_mime, tuple(_parser_name(p) for p in candidates), _parser_name(parser), "fallback")
                    self._remember(path_str, dec)
                    return parser, dec

        # Give a helpful message with hints
        raise self._unknown_ext_error(ext, detected_mime)

    # ----- Internals -----
    def _candidates_for(self, ext: str) -> List[object]:
        ext = _normalize_ext(ext)
        items = list(self._map.get(ext, []))
        if not items:
            return []
        # Filter disabled by name
        items = [p for p in items if _parser_name(p) not in self._disabled]
        # Reorder by preferences if configured
        prefs = self._prefs.get(ext)
        if prefs:
            pref_order = list(prefs)
            items.sort(key=lambda p: (pref_order.index(_parser_name(p)) if _parser_name(p) in pref_order else len(pref_order)))
        return items

    def _first_enabled_for_name(self, ext: str, name: str) -> object | None:
        for p in self._candidates_for(ext):
            if _parser_name(p) == name:
                return p
        return None

    def _ext_from_mime(self, mime: str | None) -> str:
        if not mime:
            return ""
        mime = mime.split(";", 1)[0].strip().lower()
        # 1) internal map
        if mime in self._MIME_TO_EXT:
            return self._MIME_TO_EXT[mime]
        # 2) mimetypes reverse lookup (best-effort)
        try:
            # Guess extension like ".pdf" and strip the dot
            ext = mimetypes.guess_extension(mime) or ""
            return _normalize_ext(ext)
        except Exception:  # pragma: no cover
            return ""

    def _guess_mime_from_path(self, path_str: Optional[str]) -> str | None:
        if not path_str:
            return None
        try:
            m, _ = mimetypes.guess_type(path_str)
            return m
        except Exception:  # pragma: no cover
            return None

    def _remember(self, path_str: Optional[str], decision: Decision) -> None:
        self._last = decision
        if self._cache_enabled and path_str:
            self._cache[path_str] = decision

    def _unknown_ext_error(self, ext: str, mime: str | None = None) -> ParserNotFoundError:
        ext_msg = ext or "<unknown>"
        mime_msg = mime or "<unknown>"
        supported = ", ".join(sorted(self.supported())) or "<none>"
        hint = (
            "Enable plain fallback or install a parser adapter. "
            "If this is a PDF, check that your preferred adapter is registered."
        )
        return ParserNotFoundError(
            f"No parser for extension: {ext_msg} (MIME: {mime_msg}). Supported: [{supported}]. {hint}"
        )

    # Read-only observability accessors
    @property
    def last_decision(self) -> Optional[Decision]:
        """Return the most recent Decision (if any)."""
        return self._last

