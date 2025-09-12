"""Factories for wiring preprocessing adapters based on Settings.

This module centralizes construction of concrete implementations for domain ports
used by the preprocessing application. It translates Settings into deterministic,
offline-first adapter instances, performs preflight checks for local dependencies
(e.g., OCR binaries), and exposes small, explicit factory functions for each
adapter dependency. The factories are thin, auditable, and avoid network calls
by default. Identical Settings objects produce identical wiring.

Lifecycle
- Preflight: call preflight(settings) early to validate required local
  dependencies and filesystem permissions. It returns a list of warnings and
  raises FactoryError (or SettingsError) on hard failures.
- Wiring: call the appropriate make_* factory to obtain configured adapters.
- Teardown: call teardown() at process shutdown or test cleanup to release any
  long-lived resources and reset per-process caches.

Convert-only scope (implemented)
- Parsers registry (PDF/DOCX/XLSX/JPG/JPEG/MSG → Markdown)
- Encoding normalizer (UTF-8 and newline policy)
- Markdown serializer (writes .md under out_dir, assets policy)
- OCR engine handle (local; optional)

Forward-compatible factories are defined but may raise NotConfigured unless
explicitly enabled. These are placeholders for future pipeline stages and are
implemented with an offline-first policy (no egress by default).
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
import threading
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# Settings and domain errors
from .settings import Settings, SettingsError

try:
    from ..domain.errors import PreprocessingError
except Exception:  # pragma: no cover - fallback in isolation

    class PreprocessingError(Exception):
        """Base exception for preprocessing errors (fallback)."""


# Domain ports for typing only
try:
    from ..domain.ports import OcrPort, ParserRegistryPort, SerializerPort
except Exception:  # pragma: no cover - typing fallback
    from typing import Protocol

    class ParserRegistryPort(Protocol):  # type: ignore
        def get(self, ext: str) -> Any:
            ...

        def supported(self) -> set[str]:
            ...

    class OcrPort(Protocol):  # type: ignore
        def run(self, input_path: Path, *, languages: tuple[str, ...]) -> str:
            ...

    class SerializerPort(Protocol):  # type: ignore
        def append(self, record: dict) -> None:
            ...

        def close(self) -> None:
            ...


# Concrete parser registry and adapters
from ..adapters.parsers.docx_to_md import DocxToMd
from ..adapters.parsers.jpg_to_md import JpgToMd
from ..adapters.parsers.pdf_to_md import PdfToMd
from ..adapters.parsers.registry import ParserRegistry
from ..adapters.parsers.xlsx_to_md import XlsxToMd

try:
    from ..adapters.parsers.msg_to_md import MsgToMd  # optional
except Exception:  # pragma: no cover - optional adapter
    MsgToMd = None  # type: ignore

_logger = logging.getLogger(__name__)


# ----- Errors -----
class FactoryError(PreprocessingError):
    """Factory construction error.

    Raised when adapter wiring fails due to missing local dependencies (e.g.,
    binaries/models), invalid or conflicting Settings, or filesystem issues.
    """


class NotConfigured(FactoryError):
    """Raised by forward-compatible factories when a backend is not configured."""


# ----- Lightweight adapters (encoding normalizer, markdown serializer) -----
@dataclass(frozen=True)
class _EncodingNormalizer:
    """Encoding and newline policy normalizer (UTF-8 + EOL enforcement).

    Description:
        Stateless helper that enforces UTF-8 text invariant and normalizes line
        endings according to the configured policy. Intended for use by the
        pipeline's normalization stage; construction is deterministic and has no
        side effects.

    Args:
        utf8_enforce (bool): If True, bytes are decoded with strict UTF-8;
            otherwise, best-effort decoding is used (errors="replace").
        eol_policy (str): One of {"lf", "keep"}. "lf" converts CRLF/CR → LF;
            "keep" preserves original newlines.

    Notes:
        - This adapter contains no I/O and is thread-safe.
        - It can be easily mocked in tests by replacing the normalize_text method.
    """

    utf8_enforce: bool
    eol_policy: str

    def normalize_text(self, data: str | bytes) -> str:
        """Return UTF-8, EOL-normalized text from str/bytes input.

        Args:
            data: Input text or bytes.

        Returns:
            str: UTF-8 text. When eol_policy="lf", all CRLF/CR are converted to
            LF ("\n").

        Raises:
            UnicodeDecodeError: When data is bytes and utf8_enforce=True but the
                payload is not valid UTF-8.
        """
        if isinstance(data, bytes):
            text = data.decode("utf-8", errors="strict" if self.utf8_enforce else "replace")
        else:
            text = data
        if self.eol_policy == "lf":
            # Normalize CRLF and CR to LF
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text


class _MarkdownFileSerializer(SerializerPort):
    """Markdown serializer writing .md files under an output directory.

    Description:
        A lightweight writer that accepts dictionary records produced by the
        pipeline and, when those records contain a Markdown payload and source
        path, writes a .md file under out_dir preserving the relative structure.
        It also manages an assets subdirectory and can optionally emit sidecar
        metadata depending on the configured policy.

    Args:
        out_dir (Path): Base output directory. Files are written under this root
            mirroring the original relative path. The constructor does not create
            directories; creation happens on first append() call.
        assets_subdir (str): Subdirectory for extracted assets relative to the
            Markdown file output directory.
        write_meta (str): Metadata policy: one of {"none","sidecar","inline"}.
        overwrite (bool): Overwrite existing .md files when True; otherwise, skip.

    Notes:
        - This adapter implements SerializerPort (append, close) to integrate with
          existing services. It is intentionally minimal; business logic for record
          shaping lives elsewhere.
        - No network calls are performed. File writes occur only when append() is
          invoked by callers.
    """

    def __init__(
        self,
        out_dir: Path,
        *,
        assets_subdir: str,
        write_meta: str,
        overwrite: bool,
    ) -> None:
        self._out_dir = Path(out_dir)
        self._assets_subdir = str(assets_subdir)
        self._write_meta = str(write_meta)
        self._overwrite = bool(overwrite)
        self._closed = False

    def append(self, record: Mapping[str, Any]) -> None:  # type: ignore[override]
        if self._closed:
            raise FactoryError("serializer is closed")
        # Expect a record with fields compatible with ParsedDocument.to_record()
        text = record.get("text")
        source = record.get("source", {})
        if not isinstance(text, str):
            raise FactoryError("record['text'] must be a string for Markdown serialization")
        src_path = source.get("path") if isinstance(source, Mapping) else None
        if not src_path:
            raise FactoryError("record['source']['path'] is required for Markdown serialization")
        # Compute output path: mirror relative folder structure; set .md extension
        src = Path(str(src_path))
        rel = Path(src.name) if src.is_absolute() else src
        # If the source is nested (e.g., a/b/c.pdf), preserve directories under out_dir
        if src.is_absolute():
            # Try to preserve the leaf and its parent folder name for stability
            rel = Path(*src.parts[-2:]) if len(src.parts) >= 2 else Path(src.name)
        out_dir = self._out_dir / rel.parent
        out_path = out_dir / (rel.stem + ".md")
        out_dir.mkdir(parents=True, exist_ok=True)
        if out_path.exists() and not self._overwrite:
            # Skip silently; callers may inspect filesystem for existence
            return
        # Write Markdown content; normalize to LF as the project default
        content = text.replace("\r\n", "\n").replace("\r", "\n")
        out_path.write_text(content, encoding="utf-8")
        # Optionally emit minimal sidecar metadata
        if self._write_meta == "sidecar":
            sidecar = out_path.with_suffix(".meta.json")
            try:
                import json

                json.dump(
                    record, sidecar.open("w", encoding="utf-8"), ensure_ascii=False, sort_keys=True
                )
            except Exception as e:  # pragma: no cover - best-effort
                raise FactoryError(f"failed to write sidecar metadata: {sidecar}: {e}") from e
        # Inline mode is out-of-scope for this lightweight serializer; documented only.

    def close(self) -> None:  # type: ignore[override]
        self._closed = True


# ----- OCR engine handles (local only) -----
class _NoOpOcr(OcrPort):
    """No-op OCR handle used when OCR is disabled.

    The `run` method returns an empty string deterministically. Parsers should
    treat it as "no OCR available" and degrade gracefully (e.g., emit warnings).
    """

    def run(self, input_path: Path, *, languages: tuple[str, ...]) -> str:  # type: ignore[override]
        return ""


class _TesseractOcr(OcrPort):
    """Thin Tesseract OCR handle (deferred execution, no network calls).

    Description:
        Encapsulates a pointer to a local Tesseract binary and configured language
        packs. This handle does not perform OCR itself; it exists to be injected
        into parser adapters that need OCR, and to surface deterministic adapter
        identity for logging and audit.

    Args:
        binary (str): Name or absolute path of the Tesseract executable.
        languages (tuple[str, ...]): OCR language codes in priority order.

    Notes:
        - This handle does not spawn processes at construction time.
        - Actual OCR execution is out of scope for this factories module.
    """

    def __init__(self, binary: str, languages: tuple[str, ...]) -> None:
        self._binary = str(binary)
        self._langs = tuple(languages)

    @property
    def binary(self) -> str:
        return self._binary

    @property
    def languages(self) -> tuple[str, ...]:
        return self._langs

    def run(self, input_path: Path, *, languages: tuple[str, ...] | None = None) -> str:  # type: ignore[override]
        raise NotImplementedError(
            "_TesseractOcr.run is not implemented here; a concrete OCR adapter should be used at call sites."
        )


# ----- Module-level caches -----
_OCR_CACHE: dict[tuple[str, tuple[str, ...]], OcrPort] = {}
_OCR_LOCK = threading.Lock()


def _check_binary_available(name: str) -> bool:
    return shutil.which(name) is not None


# ----- Public factories -----


def make_parsers_registry(settings: Settings) -> ParserRegistryPort:
    """Build the Markdown parsers registry with deterministic, offline adapters.

    Description:
        Constructs and returns a ParserRegistry that maps file extensions to
        concrete, configured adapter instances for converting to Markdown.
        Registered adapters include DOCX, XLSX, PDF, and JPG/JPEG; if available,
        the MSG adapter is registered as well. Shared settings (OCR toggles,
        assets policies, and table/header/footer policies) are injected into
        adapter constructors. The resulting registry performs no I/O; it only
        stores adapter instances and selection preferences.

    Args:
        settings (Settings): Immutable configuration object. Relevant fields:
            - DOCX: docx_export_images, docx_assets_subdir
            - XLSX: xlsx_header_rows, xlsx_render_mode, xlsx_max_rows,
              xlsx_max_cols, xlsx_merged_cells_policy
            - PDF: pdf_page_range, pdf_remove_headers_footers, pdf_page_divider,
              pdf_export_images, pdf_assets_subdir, pdf_table_detection
            - OCR/JPG: ocr_enabled, ocr_langs, ocr_max_working_dpi,
              ocr_confidence_threshold, ocr_fail_on_low_confidence,
              save_processed_assets
            - MSG: msg_prefer_body, msg_export_assets, msg_assets_subdir,
              msg_quoted_reply_mode

    Returns:
        ParserRegistryPort: A registry that resolves an adapter for a given
        extension. Supported keys are lowercase extensions without leading dot
        (e.g., {"docx","xlsx","pdf","jpg","jpeg"[,"msg"]}). The registry
        is deterministic: identical Settings → identical adapter configuration.

    Raises:
        FactoryError: If Settings contain invalid combinations or required
            components cannot be constructed.
        SettingsError: If input Settings are invalid (enum/range conflicts).

    Notes:
        - Deterministic adapter IDs can be derived via adapter.describe() where
          available, e.g., "PdfToMd(ocr=on;tables=auto)".
        - The registry itself performs no file I/O and makes no network calls.
        - For OCR, this function only forwards toggles to relevant adapters; use
          make_ocr_engine() to construct an OCR handle if needed.
    """
    # DOCX
    docx = DocxToMd(
        export_images=bool(settings.docx_export_images),
        assets_subdir=str(settings.docx_assets_subdir),
    )

    # XLSX
    xlsx = XlsxToMd(
        header_rows=int(settings.xlsx_header_rows),
        render_mode=str(settings.xlsx_render_mode),
        max_rows=settings.xlsx_max_rows,
        max_cols=settings.xlsx_max_cols,
        merged_cells_policy=str(settings.xlsx_merged_cells_policy),
        # Defaults documented by adapter for other, more advanced knobs
    )

    # PDF
    pdf = PdfToMd(
        page_range=settings.pdf_page_range,
        remove_headers_footers=bool(settings.pdf_remove_headers_footers),
        page_divider=str(settings.pdf_page_divider),
        export_images=bool(settings.pdf_export_images),
        assets_subdir=str(settings.pdf_assets_subdir),
        table_detection=str(settings.pdf_table_detection),
        ocr_enabled=bool(settings.ocr_enabled),
        ocr_langs=tuple(settings.ocr_langs),
        ocr_fail_on_low_confidence=bool(settings.ocr_fail_on_low_confidence),
        ocr_confidence_threshold=float(settings.ocr_confidence_threshold),
        strict_mode=bool(settings.strict),
    )

    # JPG/JPEG (OCR-oriented)
    jpg = JpgToMd(
        ocr_engine="tesseract",
        ocr_langs=tuple(settings.ocr_langs),
        max_working_dpi=int(settings.ocr_max_working_dpi),
        confidence_threshold=float(settings.ocr_confidence_threshold),
        fail_on_low_confidence=bool(settings.ocr_fail_on_low_confidence),
        save_processed_assets=bool(settings.save_processed_assets),
        assets_subdir=str(settings.assets_subdir),
    )

    static_map: dict[str, Any] = {
        "docx": docx,
        "xlsx": xlsx,
        "pdf": pdf,
        "jpg": jpg,
        "jpeg": jpg,
    }
    if MsgToMd is not None:
        msg = MsgToMd(
            prefer_body=tuple(settings.msg_prefer_body) if settings.msg_prefer_body else None,
            export_assets=bool(settings.msg_export_assets),
            assets_subdir=str(settings.msg_assets_subdir),
            quoted_reply_mode=str(settings.msg_quoted_reply_mode),
            strict_mode=bool(settings.strict),
        )
        static_map["msg"] = msg

    reg = ParserRegistry(static_map)

    # One-time wiring summary for auditability
    try:
        _logger.info(
            "parsers wired: %s",
            {k: getattr(v, "name", v.__class__.__name__) for k, v in static_map.items()},
        )
    except Exception:  # pragma: no cover - logging must not break wiring
        pass

    return reg


def make_encoding_normalizer(settings: Settings) -> _EncodingNormalizer:
    """Return an encoding normalizer enforcing UTF-8 and EOL policy.

    Description:
        Builds a lightweight helper that enforces the project's UTF-8 invariant
        and applies newline normalization per Settings.normalize_eol.

    Args:
        settings (Settings): Immutable configuration. Fields used:
            - utf8_enforce (bool): Enforce strict UTF-8 decoding for bytes.
            - normalize_eol (str): "lf" or "keep".

    Returns:
        _EncodingNormalizer: Stateless adapter with normalize_text(data) -> str.
        The object is thread-safe and holds no external resources; no teardown is
        required.

    Raises:
        SettingsError: If normalize_eol is not one of {"lf","keep"}.

    Notes:
        - Deterministic: identical Settings yield equal behavior.
        - Offline: no network calls; no filesystem writes.
    """
    if settings.normalize_eol not in {"lf", "keep"}:
        raise SettingsError(
            f"normalize_eol must be 'lf' or 'keep' (got: {settings.normalize_eol!r})"
        )
    return _EncodingNormalizer(
        utf8_enforce=bool(settings.utf8_enforce), eol_policy=str(settings.normalize_eol)
    )


def make_markdown_serializer(settings: Settings) -> SerializerPort:
    """Return a Markdown serializer configured for out_dir and assets policy.

    Description:
        Returns a SerializerPort-compatible writer that persists Markdown records
        to .md files under Settings.out_dir. The writer preserves a stable
        relative path layout and can optionally write sidecar metadata files
        depending on Settings.write_meta.

    Args:
        settings (Settings): Configuration object. Fields used:
            - out_dir (Path): Base directory to write Markdown files.
            - assets_subdir (str): Subdirectory used for binary assets placement.
            - write_meta (str): "none", "sidecar", or "inline" (inline is
              documented but not performed by this lightweight adapter).
            - overwrite (bool): Overwrite existing .md files when True; otherwise,
              append() skips instead of raising.

    Returns:
        SerializerPort: An object exposing append(record) and close(). The writer
        performs no I/O until append() is invoked.

    Raises:
        FactoryError: If Settings are inconsistent or out_dir is invalid.
        SettingsError: If write_meta is not one of {"none","sidecar","inline"}.

    Notes:
        - Offline: writing .md files performs local filesystem I/O only.
        - Deterministic: output file paths are derived consistently from input
          source paths and Settings.
    """
    if settings.write_meta not in {"none", "sidecar", "inline"}:
        raise SettingsError(
            f"write_meta must be 'none'|'sidecar'|'inline' (got: {settings.write_meta!r})"
        )
    out_dir = settings.out_dir
    if not isinstance(out_dir, Path):  # defensive
        out_dir = Path(str(out_dir))
    return _MarkdownFileSerializer(
        out_dir,
        assets_subdir=str(settings.assets_subdir),
        write_meta=str(settings.write_meta),
        overwrite=bool(settings.overwrite),
    )


def make_ocr_engine(settings: Settings) -> OcrPort | None:
    """Return a local OCR engine handle when enabled; otherwise None.

    Description:
        Constructs a handle pointing to a local OCR engine (Tesseract) using
        languages from Settings. Preflight checks ensure that the binary is
        available; missing dependencies raise FactoryError with remediation
        guidance. When Settings.ocr_enabled is False, returns None and parsers
        should degrade gracefully.

    Args:
        settings (Settings): Configuration object. Fields used:
            - ocr_enabled (bool): Master OCR toggle.
            - ocr_langs (tuple[str, ...]): OCR languages in priority order.

    Returns:
        OcrPort | None: A per-process cached OCR handle when enabled; None when
        disabled. The handle is thread-safe for injection and holds no external
        resources at construction time.

    Raises:
        FactoryError: When OCR is enabled but required binaries/models are missing.
        SettingsError: When languages are invalid (empty list) if OCR is enabled.

    Notes:
        - Thread-safe singleton: identical configuration yields the same object
          per process. The cache is cleared by teardown().
        - Offline: no network calls; the handle defers execution to call sites.
    """
    if not settings.ocr_enabled:
        return None
    langs = tuple(settings.ocr_langs or ())
    if not langs:
        raise SettingsError("ocr_langs must be a non-empty tuple when OCR is enabled")

    if not _check_binary_available("tesseract"):
        raise FactoryError(
            "Tesseract binary not found. Install 'tesseract-ocr' and verify it is on PATH. "
            "On Debian/Ubuntu: apt-get install tesseract-ocr tesseract-ocr-eng."
        )

    key = ("tesseract", langs)
    with _OCR_LOCK:
        if key in _OCR_CACHE:
            return _OCR_CACHE[key]
        handle = _TesseractOcr("tesseract", langs)
        _OCR_CACHE[key] = handle
        return handle


# ----- Forward-compatible factories (placeholders; offline by default) -----


def make_translator(settings: Settings) -> Any:
    """Return a translator adapter according to Settings (offline by default).

    Description:
        Forward-compatible factory for a translate backend. By default, this
        module refuses to construct cloud-backed translators and returns a local
        adapter only when explicitly configured. In the convert-only flow, this
        typically raises NotConfigured.

    Args:
        settings (Settings): Configuration with forward-compatible fields.

    Returns:
        Any: TranslatorPort-compatible adapter when configured.

    Raises:
        NotConfigured: When translate is not configured for offline/local use.
        FactoryError: If a cloud backend is requested while offline policy is
            active, or when the requested local model is unavailable.
    """
    raise NotConfigured("translator backend not configured (convert-only mode)")


def make_anonymizer(settings: Settings) -> Any:
    """Return an anonymization adapter according to Settings.

    In convert-only mode this factory raises NotConfigured. Future versions may
    wire a TokenVault-backed anonymizer.
    """
    raise NotConfigured("anonymization backend not configured (convert-only mode)")


def make_enricher(settings: Settings) -> Any:
    """Return an LLM-based enrichment adapter (offline by default).

    In convert-only mode this factory raises NotConfigured. Future versions may
    wire a local-only LLM that refuses raw PII and processes anonymized content
    only.
    """
    raise NotConfigured("LLM enricher not configured (convert-only mode)")


def make_vector_builder(settings: Settings) -> Any:
    """Return a vector builder (chunker + embeddings) according to Settings.

    In convert-only mode this factory raises NotConfigured.
    """
    raise NotConfigured("vector builder not configured (convert-only mode)")


def make_vector_db(settings: Settings) -> Any:
    """Return a vector database client according to Settings.

    In convert-only mode this factory raises NotConfigured.
    """
    raise NotConfigured("vector DB not configured (convert-only mode)")


# ----- Preflight and teardown -----


def preflight(settings: Settings) -> list[str]:
    """Run dependency checks and return warnings; raise on hard failures.

    Description:
        Validates the local environment according to Settings and returns a list
        of non-fatal warnings. Predictable hard failures raise FactoryError with
        actionable remediation guidance. The function performs no network calls.

    Args:
        settings (Settings): Configuration to validate. Checks performed:
            - out_dir parent exists and is writable (no directory creation).
            - OCR: when enabled, tesseract binary availability and non-empty
              language list; language packs cannot be verified portably, so a
              guidance warning is returned for non-English languages.
            - PDF table engines: when settings.pdf_table_detection explicitly
              requests "camelot" or "tabula", ensure the module is importable.

    Returns:
        list[str]: Deterministic list of human-readable warnings. Empty when no
        issues are detected. Order is stable.

    Raises:
        FactoryError: On missing required binaries (tesseract), unavailable
            requested table engine, or unwritable out_dir parent.
        SettingsError: On invalid Settings combinations (e.g., empty ocr_langs
            while OCR is enabled).

    Notes:
        - This function is idempotent and has no side effects (does not create
          directories or write files).
    """
    warnings: list[str] = []

    # Check out_dir writability without creating it
    out_dir = settings.out_dir
    parent = out_dir if out_dir.exists() and out_dir.is_dir() else out_dir.parent
    if not parent.exists():
        # Parent must exist to infer write permission deterministically
        raise FactoryError(
            f"Output directory parent does not exist: {parent}. Create it or adjust OUT_DIR."
        )
    if not os.access(str(parent), os.W_OK):
        raise FactoryError(
            f"No write permission for output directory: {parent}. Grant permissions or choose a writable path."
        )

    # OCR checks
    if settings.ocr_enabled:
        if not settings.ocr_langs:
            raise SettingsError("ocr_langs must be non-empty when OCR is enabled")
        if not _check_binary_available("tesseract"):
            raise FactoryError(
                "Tesseract not found. Install 'tesseract-ocr' and verify PATH. "
                "On Debian/Ubuntu: apt-get install tesseract-ocr tesseract-ocr-eng."
            )
        non_eng = [l for l in settings.ocr_langs if l.lower() != "eng"]
        if non_eng:
            warnings.append(
                "Verify Tesseract language packs are installed for: " + ", ".join(non_eng)
            )
    else:
        warnings.append("OCR is disabled; image-only content will not be recognized.")

    # PDF table engine checks
    engine = (settings.pdf_table_detection or "auto").lower()
    if engine in {"camelot", "tabula"}:
        mod = "camelot" if engine == "camelot" else "tabula"
        try:
            importlib.import_module(mod)
        except Exception as e:
            raise FactoryError(
                f"Requested PDF table engine '{engine}' is unavailable. Install '{mod}' locally or set PDF_TABLE_DETECTION to 'auto' or 'none'."
            ) from e

    return warnings


def teardown() -> None:
    """Release long-lived resources created by factories in this module.

    Description:
        Clears per-process caches and closes adapter resources when applicable.
        Idempotent and safe to call multiple times. Currently releases:
        - OCR engine handles: clears the in-memory cache of _TesseractOcr.
        - Markdown serializer: stateless; no action required here.

    Returns:
        None

    Notes:
        - Call this in test teardown to avoid cross-test state leaks.
        - This function performs no I/O beyond clearing in-memory structures.
    """
    with _OCR_LOCK:
        _OCR_CACHE.clear()


__all__ = [
    "FactoryError",
    "NotConfigured",
    "make_parsers_registry",
    "make_encoding_normalizer",
    "make_markdown_serializer",
    "make_ocr_engine",
    "make_translator",
    "make_anonymizer",
    "make_enricher",
    "make_vector_builder",
    "make_vector_db",
    "preflight",
    "teardown",
]
