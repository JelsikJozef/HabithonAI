"""Conversion-only application orchestrator for preprocessing.

Capability note
----------------
This module performs conversion-only preprocessing: it discovers input files under a
source directory, selects a Markdown parser adapter via the central registry, converts
files to Markdown, normalizes the output to UTF-8 with LF line endings, and writes the
results (and optional sidecar metadata) to a target directory while preserving the
relative directory structure. It does not perform translate, anonymization, LLM
operations, vector storage, or network calls.

Determinism and path mapping
----------------------------
- Given identical inputs and configuration, this orchestrator produces identical
  filenames, asset directory names, and a deterministic RunResult structure.
- Path mapping preserves directory structure relative to ``src_dir``:
  ``src_dir/reports/q1.docx → out_dir/reports/q1.md``. Per-file assets (when used)
  live under ``out_dir/reports/{assets_subdir}/``.
- Text output is normalized to UTF-8 and LF (``\n``) unless ``normalize_eol="keep"``.
  Control characters are removed except TAB (``\t``) and LF.

Integration points
------------------
- Parser selection uses the registry from ``adapters/parsers/registry.py``.
- The orchestrator attempts to obtain the parser registry, encoding normalizer, and
  Markdown serializer from ``app.factories`` if available. If not provided, it falls
  back to built-in, offline defaults that keep everything local and deterministic.
- Adapters are treated as opaque; this module does not re-implement parsing logic.

Public API
----------
- plan_folder(src_dir, out_dir, config) -> Plan
- convert_file(src_path, out_dir, config) -> FileResult
- convert_folder(src_dir, out_dir, config) -> RunResult

Keep this module thin and auditable. Avoid shared mutable state; parallelize at file
level only, and ensure deterministic aggregation of results.
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import (
    Any,
    Literal,
)

# Local domain types
try:
    from ..domain.models import RawDocument
except Exception:  # pragma: no cover - defensive in case of partial install
    RawDocument = Any  # type: ignore

# Registry (adapters/parsers/registry.py)
try:
    from ..adapters.parsers.registry import (
        ParserNotFoundError,  # type: ignore[attr-defined]
        ParserRegistry,  # type: ignore
    )
except Exception:  # pragma: no cover - graceful degradation
    ParserRegistry = None  # type: ignore

    class ParserNotFoundError(Exception):  # type: ignore
        pass


# ---------------------------
# Data contracts and results
# ---------------------------


@dataclass(frozen=True)
class FileError:
    """Structured error payload for failed file conversions.

    Attributes:
        code: Short, machine-readable error code. One of:
            - "unsupported_extension": No adapter could be resolved for the file.
            - "adapter_error": Adapter or registry raised an error.
            - "io_error": Read error for source, or unreadable/corrupted file.
            - "write_error": Failed to write Markdown or metadata.
        message: Human-readable, actionable message with a short hint.
    """

    code: str
    message: str


@dataclass(frozen=True)
class FileResult:
    """Outcome of converting a single file to Markdown.

    Description:
        Summarizes how an individual file was handled by the conversion orchestrator,
        including the selected adapter, resulting output paths, timing, and errors.
        Results are suitable for CLI reporting and inclusion in a run-level JSON report.

    Attributes:
        src_path: Absolute path to the input file.
        out_md_path: Absolute path to the Markdown file that was written (or would be
            written in dry-run). Uses UTF-8 and LF newlines when written.
        assets_dir: Absolute path to the sibling directory for binary assets used by
            the Markdown (e.g., images), or None when not applicable.
        adapter_key: Adapter identifier selected by the registry (e.g., "DocxToMd").
        status: One of {"ok", "skip", "fail"}.
        duration_ms: Processing duration in milliseconds (monotonic wall-clock).
        size_bytes_src: Optional size of the source file in bytes when available.
        warnings: List of warnings produced by the adapter and/or orchestrator.
        meta_written: One of {"none", "sidecar", "inline"} describing if/how metadata
            was persisted.
        error: Structured error payload present only when status == "fail".
    """

    src_path: str
    out_md_path: str
    assets_dir: str | None
    adapter_key: str | None
    status: Literal["ok", "skip", "fail"]
    duration_ms: int
    size_bytes_src: int | None = None
    warnings: list[str] = field(default_factory=list)
    meta_written: Literal["none", "sidecar", "inline"] = "none"
    error: FileError | None = None


@dataclass(frozen=True)
class RunResult:
    """Aggregate result of a conversion run over a folder.

    Attributes:
        started_at: ISO-8601 UTC timestamp when the run started.
        ended_at: ISO-8601 UTC timestamp when the run ended.
        scanned: Total number of filesystem entries inspected.
        matched: Number of files that matched the selection filters.
        converted_ok: Number of files successfully converted and written.
        skipped_existing: Number of files skipped due to existing targets when
            overwrite=False.
        failed: Number of files that failed.
        files: Deterministically ordered list of per-file results.
        config_echo: Echo of effective configuration (subset) for audit.
        terminated_early: True when on_error="fail" caused early termination.
    """

    started_at: str
    ended_at: str
    scanned: int
    matched: int
    converted_ok: int
    skipped_existing: int
    failed: int
    files: list[FileResult]
    config_echo: Mapping[str, Any]
    terminated_early: bool = False


@dataclass(frozen=True)
class PlanCandidate:
    """A single planned file conversion without side effects.

    Attributes:
        src_path: Absolute path of the source file.
        out_md_path: Absolute path where Markdown would be written.
        adapter_key: Adapter key that would be selected, when known; None otherwise.
        reason_if_skipped: Explanation when the file would be skipped.
    """

    src_path: str
    out_md_path: str
    adapter_key: str | None
    reason_if_skipped: str | None


@dataclass(frozen=True)
class PlanSummary:
    """Summary of the dry-run plan.

    Attributes:
        matched: Number of files matching selection filters.
        would_convert: Number of files planned for conversion.
        would_skip_existing: Number of files that would be skipped due to existing
            targets when overwrite=False.
    """

    matched: int
    would_convert: int
    would_skip_existing: int


@dataclass(frozen=True)
class Plan:
    """Deterministic dry-run plan with candidates and summary counts.

    Attributes:
        candidates: Deterministically ordered candidates describing intended actions.
        summary: Aggregate counts for matched, conversions, and would-skip.
    """

    candidates: list[PlanCandidate]
    summary: PlanSummary


# ---------------------------
# Configuration (pure data)
# ---------------------------


@dataclass(frozen=True)
class ConvertOnlyConfig:
    """Configuration for conversion-only folder/file processing.

    Scanning & selection
    --------------------
    - recurse (bool): Traverse subfolders when True.
    - include_ext (list[str]): Allowed extensions (case-insensitive). Default:
      [".docx", ".xlsx", ".pdf", ".jpg", ".jpeg", ".msg"]. Dots optional.
    - exclude_glob (list[str]): Globs relative to src_dir to skip (e.g., "**/tmp/**").
    - max_files (int | None): Cap number of files processed after filtering.

    Writing
    -------
    - overwrite (bool): Overwrite existing .md; else mark as SKIP.
    - assets_subdir (str): Subdirectory under each file's output folder for assets.
      Default "assets".
    - write_meta (Literal["none","sidecar","inline"]): How to persist metadata.
      Default "sidecar". Inline convention: a single HTML comment on the first line
      of the Markdown file with a stable JSON object, e.g., "<!-- meta: { ... } -->".
    - normalize_eol (Literal["lf","keep"]): Normalize newlines to LF when "lf".

    Execution & reliability
    -----------------------
    - workers (int): File-level parallelism. Outputs and results remain deterministic,
      collected in sorted order by src_path. Use 1 to disable parallelism.
    - on_error (Literal["skip","fail"]): Continue after errors ("skip") or stop
      early and return partial results with terminated_early=True ("fail").
    - dry_run (bool): Compute and return a Plan; write nothing when True.
    - strict (bool): Promote adapter warnings into errors when possible.

    Diagnostics
    -----------
    - log_level (Literal["ERROR","WARNING","INFO","DEBUG"]): Runtime logging level.
    - report_path (str | None): When provided, write a JSON report mirroring RunResult.
    """

    # Scanning & selection
    recurse: bool = True
    include_ext: list[str] = field(
        default_factory=lambda: [".docx", ".xlsx", ".pdf", ".jpg", ".jpeg", ".msg"]
    )
    exclude_glob: list[str] = field(default_factory=list)
    max_files: int | None = None

    # Writing
    overwrite: bool = False
    assets_subdir: str = "assets"
    write_meta: Literal["none", "sidecar", "inline"] = "sidecar"
    normalize_eol: Literal["lf", "keep"] = "lf"

    # Execution & reliability
    workers: int = 1
    on_error: Literal["skip", "fail"] = "skip"
    dry_run: bool = False
    strict: bool = False

    # Diagnostics
    log_level: Literal["ERROR", "WARNING", "INFO", "DEBUG"] = "INFO"
    report_path: str | None = None


# ---------------------------
# Internal helpers/ports
# ---------------------------


def _coerce_config(cfg: Any) -> ConvertOnlyConfig:
    """Return a ConvertOnlyConfig from either a config object or a dict-like.

    Accepts a ConvertOnlyConfig instance or a mapping produced by the CLI
    (EffectiveConfig.as_app_config). Missing keys fall back to defaults.
    """
    if isinstance(cfg, ConvertOnlyConfig):
        return cfg
    if isinstance(cfg, Mapping):
        scan = cfg.get("scan", {}) if isinstance(cfg.get("scan"), Mapping) else {}
        write = cfg.get("write", {}) if isinstance(cfg.get("write"), Mapping) else {}
        runtime = cfg.get("runtime", {}) if isinstance(cfg.get("runtime"), Mapping) else {}
        ui = cfg.get("ui", {}) if isinstance(cfg.get("ui"), Mapping) else {}
        return ConvertOnlyConfig(
            recurse=bool(scan.get("recurse", True)),
            include_ext=list(
                scan.get("include_ext", [".docx", ".xlsx", ".pdf", ".jpg", ".jpeg", ".msg"])
            ),
            exclude_glob=list(scan.get("exclude_glob", [])),
            max_files=scan.get("max_files", None),
            overwrite=bool(write.get("overwrite", False)),
            assets_subdir=str(write.get("assets_subdir", "assets")),
            write_meta=str(write.get("write_meta", "sidecar")),
            normalize_eol=str(write.get("normalize_eol", "lf")),
            workers=int(runtime.get("workers", 1)),
            on_error=str(runtime.get("on_error", "skip")),
            dry_run=bool(runtime.get("dry_run", False)),
            strict=bool(runtime.get("strict", False)),
            log_level=str(ui.get("log_level", "INFO")),
            report_path=str(cfg.get("report")) if cfg.get("report") else None,
        )
    # Fallback to defaults when unknown type
    return ConvertOnlyConfig()


class _EncodingNormalizer:
    """Default offline normalizer enforcing UTF-8 and newline policy.

    This is a minimal internal implementation used when factories don't provide a
    concrete EncodingNormalizerPort. It removes C0 control characters except TAB and
    LF, and normalizes CRLF/CR to LF when configured.
    """

    _CTRL_RE = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")

    def normalize(self, text: str, *, eol: Literal["lf", "keep"] = "lf") -> str:
        s = str(text)
        if eol == "lf":
            s = s.replace("\r\n", "\n").replace("\r", "\n")
        # Remove control characters except TAB (\t) and LF (\n)
        s = self._CTRL_RE.sub("", s)
        return s


class _MarkdownSerializer:
    """Default offline serializer that writes Markdown and optional sidecar JSON.

    The serializer writes UTF-8 text with LF newlines. It does not perform any
    Markdown formatting; it only persists the given content and metadata.
    """

    def write(
        self,
        *,
        md_text: str,
        md_path: Path,
        meta: Mapping[str, Any] | None = None,
        inline_mode: Literal["none", "sidecar", "inline"] = "sidecar",
    ) -> Literal["none", "sidecar", "inline"]:
        md_path.parent.mkdir(parents=True, exist_ok=True)
        # Inline metadata (HTML comment on the first line)
        text_to_write = md_text
        if inline_mode == "inline" and meta:
            header = f"<!-- meta: {json.dumps(meta, sort_keys=True, ensure_ascii=False)} -->\n"
            text_to_write = header + md_text
        with md_path.open("w", encoding="utf-8", newline="\n") as f:
            f.write(text_to_write)
        if inline_mode == "sidecar" and meta is not None:
            sidecar = md_path.with_suffix(md_path.suffix + ".meta.json")
            with sidecar.open("w", encoding="utf-8", newline="\n") as sf:
                json.dump(meta, sf, ensure_ascii=False, sort_keys=True)
            return "sidecar"
        return "inline" if inline_mode == "inline" else "none"


# Obtain dependencies via factories when available; otherwise, fall back to defaults


def _get_registry() -> Any:
    """Return a ParserRegistry instance using factories or a local default."""
    # Try factories first
    try:  # pragma: no cover - import-level resilience
        from . import factories as _fact

        if hasattr(_fact, "get_parser_registry"):
            return _fact.get_parser_registry()
    except Exception:
        pass
    # Fallback: instantiate a default registry with known adapters if module is present
    if ParserRegistry is None:
        raise RuntimeError("ParserRegistry is not available; cannot proceed")
    static_map: dict[str, Any] = {}
    # Import known adapters lazily; ignore if unavailable
    try:
        from ..adapters.parsers.docx_to_md import DocxToMd as _Docx

        static_map["docx"] = _Docx()
    except Exception:
        pass
    try:
        from ..adapters.parsers.xlsx_to_md import XlsxToMd as _Xlsx  # type: ignore

        static_map["xlsx"] = _Xlsx()  # type: ignore
    except Exception:
        pass
    try:
        from ..adapters.parsers.pdf_to_md import PdfToMd as _Pdf

        static_map["pdf"] = _Pdf()
    except Exception:
        pass
    try:
        from ..adapters.parsers.jpg_to_md import JpgToMd as _Jpg  # type: ignore

        static_map["jpg"] = _Jpg()  # type: ignore
        static_map["jpeg"] = _Jpg()  # type: ignore
    except Exception:
        pass
    try:
        from ..adapters.parsers.msg_to_md import MsgToMd as _Msg  # type: ignore

        static_map["msg"] = _Msg()  # type: ignore
    except Exception:
        pass
    return ParserRegistry(static_map)


def _get_encoding_normalizer() -> _EncodingNormalizer:
    """Return an encoding normalizer from factories or the built-in default."""
    try:  # pragma: no cover
        from . import factories as _fact

        if hasattr(_fact, "get_encoding_normalizer"):
            return _fact.get_encoding_normalizer()  # type: ignore[return-value]
    except Exception:
        pass
    return _EncodingNormalizer()


def _get_md_serializer() -> _MarkdownSerializer:
    """Return a Markdown serializer from factories or the built-in default."""
    try:  # pragma: no cover
        from . import factories as _fact

        if hasattr(_fact, "get_markdown_serializer"):
            return _fact.get_markdown_serializer()  # type: ignore[return-value]
    except Exception:
        pass
    return _MarkdownSerializer()


# ---------------------------
# Public API
# ---------------------------


def plan_folder(
    src_dir: Path | str, out_dir: Path | str, config: ConvertOnlyConfig | Mapping[str, Any]
) -> Plan:
    """Compute a deterministic, side-effect-free plan for converting a folder.

    Description:
        Traverses the source folder (recursively when configured), applies extension
        selection and exclude globs, and maps each candidate to a target Markdown path
        under ``out_dir`` while preserving the relative structure. The parser adapter
        is resolved via the registry when possible to include the chosen adapter key in
        the plan. Existing target files are marked with ``reason_if_skipped`` when
        ``overwrite=False``. The returned plan is suitable for ``--dry-run``.

    Args:
        src_dir (Path | str): Absolute or relative path to the source directory to
            scan. Must exist and be a directory.
        out_dir (Path | str): Absolute or relative path to the output directory where
            Markdown files would be written.
        config (ConvertOnlyConfig): Pure-data config controlling traversal, filters,
            writing policy, and diagnostics. See ConvertOnlyConfig for all fields.

    Returns:
        Plan: A deterministic plan containing the list of candidates (ordered by
        ``src_path``) and a summary with counts for matched files, conversions, and
        would-skip due to existing targets.

    Raises:
        FileNotFoundError: When ``src_dir`` does not exist.
        NotADirectoryError: When ``src_dir`` is not a directory.
        ValueError: When configuration values are invalid (e.g., negative max_files).

    Notes:
        - All paths in the plan are absolute for clarity and auditability.
        - Unsupported extensions are included in ``candidates`` with a descriptive
          ``reason_if_skipped="unsupported_extension"``.
    """
    src_root = Path(src_dir).resolve()
    out_root = Path(out_dir).resolve()
    config = _coerce_config(config)
    if not src_root.exists():
        raise FileNotFoundError(f"Source directory not found: {src_root}")
    if not src_root.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {src_root}")
    if config.max_files is not None and config.max_files < 0:
        raise ValueError("max_files must be >= 0 or None")

    # Prepare filters
    include = _normalize_ext_list(config.include_ext)
    exclude_globs = list(config.exclude_glob)

    registry = _get_registry()

    scanned = 0
    matched = 0
    would_skip_existing = 0
    candidates: list[PlanCandidate] = []

    files_iter = _iter_files(src_root, recurse=config.recurse)
    for abs_path in files_iter:
        scanned += 1
        rel = abs_path.relative_to(src_root)
        # Exclude checks (relative paths only)
        if _is_excluded(rel, exclude_globs):
            continue
        if not _is_included(abs_path, include):
            continue
        matched += 1
        out_md = _map_out_path(abs_path, src_root, out_root)
        adapter_key: str | None = None
        reason: str | None = None
        # Try resolving adapter to include its key in the plan
        try:
            parser, decision = registry.choose(str(abs_path))  # type: ignore[attr-defined]
            adapter_key = getattr(
                parser, "name", getattr(parser, "__class__", type("_", (), {})()).__name__
            )
        except Exception:
            reason = "unsupported_extension"
        # Existing target handling
        if not config.overwrite and out_md.exists():
            would_skip_existing += 1
            reason = reason or "exists_and_overwrite_false"
        candidates.append(
            PlanCandidate(
                src_path=str(abs_path),
                out_md_path=str(out_md),
                adapter_key=adapter_key,
                reason_if_skipped=reason,
            )
        )
        if config.max_files is not None and len(candidates) >= config.max_files:
            break

    # Deterministic ordering by src_path
    candidates.sort(key=lambda c: c.src_path)

    would_convert = sum(1 for c in candidates if c.reason_if_skipped is None)
    summary = PlanSummary(
        matched=matched, would_convert=would_convert, would_skip_existing=would_skip_existing
    )
    return Plan(candidates=candidates, summary=summary)


def convert_file(
    src_path: Path | str, out_dir: Path | str, config: ConvertOnlyConfig | Mapping[str, Any]
) -> FileResult:
    """Convert a single file to Markdown end-to-end.

    Description:
        Resolves the appropriate Markdown parser via the registry, converts the input
        file into a structure-preserving Markdown document, normalizes encoding and
        newlines, then writes the output (and optional metadata) to the target folder
        preserving relative structure from ``src_dir`` is not known at this level; the
        caller is responsible for choosing ``out_dir`` consistent with its plan. The
        result includes timing, warnings, and error information.

    Args:
        src_path (Path | str): Absolute or relative filesystem path to the source
            file. Must be readable by the current process.
        out_dir (Path | str): Target output directory under which the Markdown file
            will be written, preserving the relative directory structure only when the
            caller uses paths consistent with a prior plan. For direct calls, the
            Markdown file is placed next to ``out_dir`` using the basename of
            ``src_path``.
        config (ConvertOnlyConfig): Configuration controlling writing policy, metadata
            persistence, normalization, and error handling. Only a subset is used at
            single-file level (e.g., overwrite, write_meta, normalize_eol, strict).

    Returns:
        FileResult: Per-file outcome with resolved adapter, output paths, status,
        warnings, and duration metrics.

    Raises:
        FileNotFoundError: When ``src_path`` does not exist.
        IsADirectoryError: When ``src_path`` is a directory.

    Notes:
        - Unsupported extensions are reported with ``error.code="unsupported_extension"``.
        - IO failures in reading or writing are reported with ``io_error`` or
          ``write_error`` respectively.
        - When ``config.strict`` is True, adapter warnings are promoted to errors.
    """
    config = _coerce_config(config)
    start_ns = _now_ns()
    src = Path(src_path).resolve()
    if not src.exists():
        raise FileNotFoundError(f"Source file not found: {src}")
    if src.is_dir():
        raise IsADirectoryError(f"Source path is a directory: {src}")

    out_root = Path(out_dir).resolve()
    # For single-file, place under out_root preserving only basename mapping
    out_md = out_root / (src.stem + ".md")

    size_bytes = None
    try:
        size_bytes = src.stat().st_size
    except Exception:
        pass

    assets_dir = str(out_md.parent / config.assets_subdir)

    # Resolve parser via registry
    registry = _get_registry()
    adapter_key: str | None = None
    try:
        parser, decision = registry.choose(str(src))  # type: ignore[attr-defined]
        adapter_key = getattr(
            parser, "name", getattr(parser, "__class__", type("_", (), {})()).__name__
        )
    except ParserNotFoundError as e:
        return FileResult(
            src_path=str(src),
            out_md_path=str(out_md),
            assets_dir=assets_dir,
            adapter_key=None,
            status="fail",
            duration_ms=_elapsed_ms(start_ns),
            size_bytes_src=size_bytes,
            warnings=[],
            meta_written="none",
            error=FileError(code="unsupported_extension", message=str(e)),
        )
    except Exception as e:
        return FileResult(
            src_path=str(src),
            out_md_path=str(out_md),
            assets_dir=assets_dir,
            adapter_key=None,
            status="fail",
            duration_ms=_elapsed_ms(start_ns),
            size_bytes_src=size_bytes,
            warnings=[],
            meta_written="none",
            error=FileError(code="adapter_error", message=f"Registry failure: {e}"),
        )

    # Skip if exists and overwrite=False
    if not config.dry_run and out_md.exists() and not config.overwrite:
        try:
            existing_size = out_md.stat().st_size
        except Exception:
            existing_size = None
        if existing_size is not None and existing_size > 0:
            return FileResult(
                src_path=str(src),
                out_md_path=str(out_md),
                assets_dir=assets_dir,
                adapter_key=adapter_key,
                status="skip",
                duration_ms=_elapsed_ms(start_ns),
                size_bytes_src=size_bytes,
                warnings=["Target exists; overwrite disabled"],
                meta_written="none",
                error=None,
            )
        # Else: file exists but is empty (size 0) or size unknown -> proceed to write
        if existing_size == 0:
            logging.getLogger(__name__).info(
                "Existing target is empty; will overwrite despite overwrite=False: %s", out_md
            )

    # Build RawDocument for adapters that expect it
    raw_doc: Any
    try:
        from datetime import datetime as _dt

        raw_doc = RawDocument(
            path=src,
            size=size_bytes or 0,
            mtime=_dt.fromtimestamp(src.stat().st_mtime),
            ext=src.suffix,
        )
    except Exception:
        # Fallback when RawDocument is not available at runtime
        raw_doc = {"path": str(src), "ext": src.suffix}

    warnings_list: list[str] = []
    meta: dict[str, Any] = {}
    try:
        # Adapter is opaque; expected to expose parse(raw) -> MarkdownDoc-like object
        parsed = parser.parse(raw_doc)
        # Extract text and metadata in a tolerant way
        text_md = getattr(parsed, "text_md", getattr(parsed, "text", ""))
        meta = dict(getattr(parsed, "meta", getattr(parsed, "metadata", {})) or {})
        warnings_from_adapter = meta.get("conversion_warnings") or meta.get("warnings") or []
        if isinstance(warnings_from_adapter, list):
            warnings_list.extend([str(w) for w in warnings_from_adapter])
    except FileNotFoundError as e:
        return FileResult(
            src_path=str(src),
            out_md_path=str(out_md),
            assets_dir=assets_dir,
            adapter_key=adapter_key,
            status="fail",
            duration_ms=_elapsed_ms(start_ns),
            size_bytes_src=size_bytes,
            warnings=warnings_list,
            meta_written="none",
            error=FileError(code="io_error", message=str(e)),
        )
    except PermissionError as e:
        return FileResult(
            src_path=str(src),
            out_md_path=str(out_md),
            assets_dir=assets_dir,
            adapter_key=adapter_key,
            status="fail",
            duration_ms=_elapsed_ms(start_ns),
            size_bytes_src=size_bytes,
            warnings=warnings_list,
            meta_written="none",
            error=FileError(code="io_error", message=str(e)),
        )
    except Exception as e:
        return FileResult(
            src_path=str(src),
            out_md_path=str(out_md),
            assets_dir=assets_dir,
            adapter_key=adapter_key,
            status="fail",
            duration_ms=_elapsed_ms(start_ns),
            size_bytes_src=size_bytes,
            warnings=warnings_list,
            meta_written="none",
            error=FileError(code="adapter_error", message=str(e)),
        )

    # Strict mode promotion
    if config.strict and warnings_list:
        return FileResult(
            src_path=str(src),
            out_md_path=str(out_md),
            assets_dir=assets_dir,
            adapter_key=adapter_key,
            status="fail",
            duration_ms=_elapsed_ms(start_ns),
            size_bytes_src=size_bytes,
            warnings=warnings_list,
            meta_written="none",
            error=FileError(
                code="adapter_error", message="Strict mode: warnings promoted to error"
            ),
        )

    # Normalize encoding/newlines
    normalizer = _get_encoding_normalizer()
    text_md_norm = normalizer.normalize(str(text_md), eol=config.normalize_eol)

    # Persist
    meta_written: Literal["none", "sidecar", "inline"] = "none"
    if not config.dry_run:
        try:
            serializer = _get_md_serializer()
            meta_written = serializer.write(
                md_text=text_md_norm,
                md_path=out_md,
                meta=meta,
                inline_mode=config.write_meta,
            )
        except Exception as e:
            return FileResult(
                src_path=str(src),
                out_md_path=str(out_md),
                assets_dir=assets_dir,
                adapter_key=adapter_key,
                status="fail",
                duration_ms=_elapsed_ms(start_ns),
                size_bytes_src=size_bytes,
                warnings=warnings_list,
                meta_written="none",
                error=FileError(code="write_error", message=f"Failed to write output: {e}"),
            )

    return FileResult(
        src_path=str(src),
        out_md_path=str(out_md),
        assets_dir=assets_dir,
        adapter_key=adapter_key,
        status="ok",
        duration_ms=_elapsed_ms(start_ns),
        size_bytes_src=size_bytes,
        warnings=warnings_list,
        meta_written=meta_written,
        error=None,
    )


def convert_folder(
    src_dir: Path | str, out_dir: Path | str, config: ConvertOnlyConfig | Mapping[str, Any]
) -> RunResult:
    """Convert all supported files in a folder to Markdown.

    Description:
        Orchestrates end-to-end discovery and conversion across a source directory.
        Applies selection filters and recursive traversal when configured, chooses the
        appropriate adapter per file via the registry, converts, normalizes, and writes
        outputs under ``out_dir`` preserving relative structure. Produces a rich
        RunResult with per-file outcomes, counts, timing, and optional JSON report.

    Args:
        src_dir (Path | str): Source directory to scan. Must exist and be a directory.
        out_dir (Path | str): Output directory where Markdown files will be written.
        config (ConvertOnlyConfig): Pure-data configuration object controlling traversal,
            filters, concurrency, writing, error handling, and diagnostics.

    Returns:
        RunResult: Aggregate outcome including per-file FileResult entries, summary
        counts, timing, and echoed configuration for audit.

    Raises:
        FileNotFoundError: When ``src_dir`` does not exist.
        NotADirectoryError: When ``src_dir`` is not a directory.
        ValueError: When configuration values are invalid (e.g., negative max_files or
            non-positive workers).

    Notes:
        - Concurrency is file-level using threads. Results are aggregated deterministically
          and sorted by source path. No shared mutable state is used.
        - Only writes under ``out_dir``; source files are never modified.
        - When ``on_error='fail'``, processing stops after the first failure and
          ``terminated_early=True`` is set in the returned RunResult.
    """
    config = _coerce_config(config)
    src_root = Path(src_dir).resolve()
    out_root = Path(out_dir).resolve()
    if not src_root.exists():
        raise FileNotFoundError(f"Source directory not found: {src_root}")
    if not src_root.is_dir():
        raise NotADirectoryError(f"Source path is not a directory: {src_root}")
    if config.max_files is not None and config.max_files < 0:
        raise ValueError("max_files must be >= 0 or None")
    if config.workers <= 0:
        raise ValueError("workers must be >= 1")

    # Logging level
    logging.getLogger(__name__).setLevel(
        getattr(logging, str(config.log_level).upper(), logging.INFO)
    )

    # Build list of candidates deterministically
    include = _normalize_ext_list(config.include_ext)
    exclude_globs = list(config.exclude_glob)
    scanned = 0
    matched_files: list[Path] = []
    for abs_path in _iter_files(src_root, recurse=config.recurse):
        scanned += 1
        rel = abs_path.relative_to(src_root)
        if _is_excluded(rel, exclude_globs):
            continue
        if not _is_included(abs_path, include):
            continue
        matched_files.append(abs_path)
        if config.max_files is not None and len(matched_files) >= config.max_files:
            break

    matched_files.sort(key=lambda p: str(p))

    # Convert sequentially or with threads; collect deterministic results
    started_at = _now_iso()
    results: list[FileResult] = []
    lock = threading.Lock()
    terminated_early = False

    def _task(p: Path) -> FileResult:
        # Map per-file out path preserving relative structure
        out_md = _map_out_path(p, src_root, out_root)
        # Delegate to convert_file but force the chosen out path
        # We re-implement the final write here to ensure preserved structure
        # by calling convert_file on a per-file out directory equal to desired parent.
        single_out_dir = out_md.parent
        res = convert_file(p, single_out_dir, config)
        return res

    if config.workers == 1 or config.dry_run:
        # In dry_run, we don't write; still run sequential plan -> results
        for p in matched_files:
            res = _task(p)
            results.append(res)
            if config.on_error == "fail" and res.status == "fail":
                terminated_early = True
                break
    else:
        with ThreadPoolExecutor(max_workers=config.workers, thread_name_prefix="conv") as ex:
            future_map = {ex.submit(_task, p): p for p in matched_files}
            for fut in as_completed(future_map):
                p = future_map[fut]
                try:
                    res = fut.result()
                except Exception as e:  # pragma: no cover - defensive
                    res = FileResult(
                        src_path=str(p),
                        out_md_path=str(_map_out_path(p, src_root, out_root)),
                        assets_dir=str(
                            (_map_out_path(p, src_root, out_root)).parent / config.assets_subdir
                        ),
                        adapter_key=None,
                        status="fail",
                        duration_ms=0,
                        size_bytes_src=None,
                        warnings=[],
                        meta_written="none",
                        error=FileError(code="adapter_error", message=str(e)),
                    )
                with lock:
                    results.append(res)
                    if config.on_error == "fail" and res.status == "fail":
                        terminated_early = True
                        break
        # If early termination requested, ignore remaining futures by not reading them

    # Deterministic ordering
    results.sort(key=lambda r: r.src_path)

    converted_ok = sum(1 for r in results if r.status == "ok")
    skipped_existing = sum(1 for r in results if r.status == "skip")
    failed = sum(1 for r in results if r.status == "fail")

    ended_at = _now_iso()
    run = RunResult(
        started_at=started_at,
        ended_at=ended_at,
        scanned=scanned,
        matched=len(matched_files),
        converted_ok=converted_ok,
        skipped_existing=skipped_existing,
        failed=failed,
        files=results,
        config_echo={
            "recurse": config.recurse,
            "include_ext": sorted(_normalize_ext_list(config.include_ext)),
            "exclude_glob": list(config.exclude_glob),
            "max_files": config.max_files,
            "overwrite": config.overwrite,
            "assets_subdir": config.assets_subdir,
            "write_meta": config.write_meta,
            "normalize_eol": config.normalize_eol,
            "workers": config.workers,
            "on_error": config.on_error,
            "dry_run": config.dry_run,
            "strict": config.strict,
            "log_level": config.log_level,
            "report_path": config.report_path,
        },
        terminated_early=terminated_early,
    )

    # Optional report output
    if config.report_path:
        try:
            _write_report(Path(config.report_path), run)
        except Exception:  # pragma: no cover - report failures should not crash the run
            logging.getLogger(__name__).exception("Failed to write run report")

    return run


# ---------------------------
# Utilities
# ---------------------------


def _now_iso() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _now_ns() -> int:
    return os.times_ns() if hasattr(os, "times_ns") else int(datetime.now().timestamp() * 1e9)


def _elapsed_ms(start_ns: int) -> int:
    now_ns = _now_ns()
    return int(max(0, (now_ns - start_ns) / 1_000_000))


def _normalize_ext_list(exts: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for e in exts:
        s = str(e).strip().lower()
        if not s:
            continue
        if s.startswith("."):
            s = s[1:]
        out.add(s)
    return out


def _iter_files(root: Path, *, recurse: bool) -> Iterable[Path]:
    if recurse:
        for dirpath, _dirnames, filenames in os.walk(root):
            d = Path(dirpath)
            for name in filenames:
                yield (d / name).resolve()
    else:
        for p in root.iterdir():
            if p.is_file():
                yield p.resolve()


def _is_excluded(rel_path: Path, patterns: Sequence[str]) -> bool:
    # Match patterns against POSIX-style relative paths
    rel = rel_path.as_posix()
    return any(fnmatch(rel, pat) for pat in patterns)


def _is_included(abs_path: Path, include_exts: set[str]) -> bool:
    ext = abs_path.suffix.lower().lstrip(".")
    return ext in include_exts


def _map_out_path(abs_path: Path, src_root: Path, out_root: Path) -> Path:
    rel = abs_path.relative_to(src_root)
    out_rel = rel.with_suffix(".md")
    return (out_root / out_rel).resolve()


def _write_report(path: Path, run: RunResult) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = asdict(run)
    with path.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, sort_keys=True)
