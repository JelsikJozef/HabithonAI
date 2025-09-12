"""Preprocessing settings module.

This module provides a single, authoritative, and immutable Settings object for the
preprocessing application. It builds settings from three sources with strict
precedence and full validation:

    Defaults (in code) < Profile presets < Environment variables/.env < CLI overrides

Design goals
- Deterministic: normalization ensures stable, reproducible behavior across runs.
- Immutable: Settings is a frozen dataclass; callers must rebuild for changes.
- Explicit: Invalid configuration raises SettingsError with actionable messages.
- Forward-compatible: Fields cover convert-only flow today and future pipeline
  (translate, privacy/anonymization toggles, vector store) without importing
  heavy vendor dependencies.

Environment variables
- General
  * APP_PROFILE: str, one of {"default", "fast", "hi_fidelity"} (default: "default")
  * LOG_LEVEL: str, typical values {"CRITICAL","ERROR","WARNING","INFO","DEBUG"} (default: "INFO")
  * STRICT: bool (default: false)
  * DRY_RUN: bool (default: false)
  * WORKERS: int >= 1 (default: 1)
  * ON_ERROR: str, one of {"skip","stop"} (default: "skip")

- Roots
  * SRC_DIR: path, must exist and be a directory
  * OUT_DIR: path, can be absent (caller may create); always normalized to absolute

- Selection
  * RECURSE: bool (default: true)
  * INCLUDE_EXT: comma-separated list, e.g. ".pdf,.docx,.xlsx" (default: .docx,.xlsx,.pdf,.jpg,.jpeg,.msg)
  * EXCLUDE_GLOB: comma-separated list of globs; empty means none
  * MAX_FILES: int > 0, or empty/absent for no limit

- Encoding / Serializer
  * NORMALIZE_EOL: str, one of {"lf","keep"} (default: "lf")
  * UTF8_ENFORCE: bool (default: true) — ensure UTF-8 text encoding invariant
  * ASSETS_SUBDIR: str (default: "assets")
  * WRITE_META: str, one of {"none","sidecar","inline"} (default: "sidecar")
  * OVERWRITE: bool (default: false)
  * REPORT_PATH: path, optional; if provided, normalized to absolute

- Parsers (common)
  * ESCAPE_PIPES_IN_TABLES: bool (default: true)
  * TRIM_TRAILING_SPACES: bool (default: true)

- DOCX
  * DOCX_EXPORT_IMAGES: bool (default depends on profile; default profile: false)
  * DOCX_ASSETS_SUBDIR: str (default: "docx_assets")

- XLSX
  * XLSX_HEADER_ROWS: int >= 0 (default: 1)
  * XLSX_RENDER_MODE: str, one of {"display","raw"} (default: "display")
  * XLSX_MAX_ROWS: int > 0 or empty (no limit) (default: no limit)
  * XLSX_MAX_COLS: int > 0 or empty (no limit) (default: no limit)
  * XLSX_MERGED_CELLS_POLICY: str, one of {"fill","topleft"} (default: "fill")

- PDF
  * PDF_PAGE_RANGE: str like "1-3,5" (optional)
  * PDF_REMOVE_HEADERS_FOOTERS: bool (default: true)
  * PDF_PAGE_DIVIDER: str (default: "\n\n---\n\n")
  * PDF_EXPORT_IMAGES: bool (profile dependent; default profile: false)
  * PDF_ASSETS_SUBDIR: str (default: "pdf_assets")
  * PDF_TABLE_DETECTION: str, one of {"auto","none","camelot","tabula"} (default: "auto")

- OCR (for images and image-only PDFs)
  * OCR_ENABLED: bool (default: true)
  * OCR_LANGS: comma-separated language codes (default: "eng")
  * OCR_CONFIDENCE_THRESHOLD: float between 0 and 1 (default: 0.55)
  * OCR_FAIL_ON_LOW_CONFIDENCE: bool (default: false)
  * OCR_MAX_WORKING_DPI: int > 0 (default: 300)
  * SAVE_PROCESSED_ASSETS: bool (default: false)

- JPG/JPEG
  Currently inherits OCR toggles; dedicated preprocessing switches can be
  added in future without breaking this API.

- MSG (Outlook .msg)
  * MSG_PREFER_BODY: comma-separated priority: e.g., "html,text,rtf" (default: "html,text,rtf")
  * MSG_EXPORT_ASSETS: bool (default: true)
  * MSG_ASSETS_SUBDIR: str (default: "msg_assets")
  * MSG_QUOTED_REPLY_MODE: str, one of {"blockquote","ignore","inline"} (default: "blockquote")

- Translation (forward-compatible; not used by convert-only flow)
  * TRANSLATOR: str, one of {"local:marian","local:m2m","deepl","openai"} (optional)
  * MT_MODEL_ID: str (optional)
  * MT_DEVICE: str (optional; e.g., "cpu","cuda:0")
  * MT_MAX_TOKENS: int > 0 (optional)
  * MT_BATCH_SIZE: int > 0 (optional)

- Privacy (forward-compatible)
  * SAFE_ORDER: bool (default: false) — ignored by convert-only flow; used by later pipeline stages.

- Vector store (forward-compatible)
  * VECTOR_CHUNK_SIZE: int > 0 (optional)
  * VECTOR_CHUNK_OVERLAP: int >= 0 (optional)
  * VECTOR_EMBEDDINGS: str (e.g., "local:all-MiniLM-L6-v2" or vendor model id) (optional)
  * QDRANT_URL: str URL (optional)
  * QDRANT_COLLECTION: str (optional)

Defaults (convert-only)
- recurse=True
- include_ext=[".docx",".xlsx",".pdf",".jpg",".jpeg",".msg"]
- exclude_glob=[]
- max_files=None
- normalize_eol="lf"
- utf8_enforce=True
- assets_subdir="assets"
- write_meta="sidecar"
- overwrite=False
- workers=1
- on_error="skip"
- dry_run=False
- strict=False
- log_level="INFO"
- XLSX: header_rows=1, render_mode="display", merged_cells_policy="fill", max_rows=None, max_cols=None
- PDF: page_range=None, remove_headers_footers=True, page_divider="\n\n---\n\n", export_images=False, table_detection="auto"
- OCR: ocr_enabled=True, ocr_langs="eng", confidence_threshold=0.55, fail_on_low_confidence=False, max_working_dpi=300, save_processed_assets=False
- MSG: prefer_body="html,text,rtf", export_assets=True, quoted_reply_mode="blockquote"

Profiles
- "default" — as above.
- "fast" — fewer OCR steps, no image export, lower DPI and XLSX caps:
  pdf_export_images=False, docx_export_images=False, ocr_max_working_dpi=200,
  xlsx_max_rows=10000, xlsx_max_cols=50, save_processed_assets=False.
- "hi_fidelity" — image export on, stronger cleanup, more assets saved:
  pdf_export_images=True, docx_export_images=True, pdf_remove_headers_footers=True,
  ocr_confidence_threshold>=0.60, save_processed_assets=True, ocr_max_working_dpi=300.

Notes
- This module does not create directories or import heavy vendor libraries.
- Callers should use build_from_env(...) as the single entry-point.
- to_dict() returns a deterministic, JSON-serializable mapping; redacted_dict()
  is available via to_dict(settings, redacted=True).
"""

from __future__ import annotations

import os
import re
from collections.abc import Iterable, Mapping, MutableMapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Any

try:  # Version import is lightweight
    from preprocessing import __version__ as _PKG_VERSION
except Exception:  # pragma: no cover - fallback in exotic envs
    _PKG_VERSION = "0.0.0"

try:
    # Optional; we gracefully fall back to a tiny loader
    import dotenv as _dotenv  # type: ignore
except Exception:  # pragma: no cover - optional
    _dotenv = None  # type: ignore

# region Errors
try:
    from preprocessing.domain.errors import PreprocessingError
except Exception:  # pragma: no cover - test isolation fallback

    class PreprocessingError(Exception):
        """Base exception for preprocessing errors (fallback)."""


class SettingsError(PreprocessingError):
    """Configuration error raised for invalid or conflicting settings.

    Use this to signal actionable misconfiguration, e.g., invalid enum value,
    out-of-range numeric input, path not found, or conflicting options.
    """


# endregion

# region Helpers (parsing & normalization)
_BOOL_TRUE = {"1", "true", "yes", "y", "on"}
_BOOL_FALSE = {"0", "false", "no", "n", "off"}

_ALLOWED_ON_ERROR = {"skip", "stop"}
_ALLOWED_EOL = {"lf", "keep"}
_ALLOWED_WRITE_META = {"none", "sidecar", "inline"}
_ALLOWED_XLSX_RENDER_MODE = {"display", "raw"}
_ALLOWED_XLSX_MERGED_POLICY = {"fill", "topleft"}
_ALLOWED_PDF_TABLE_DET = {"auto", "none", "camelot", "tabula"}
_ALLOWED_MSG_REPLY_MODE = {"blockquote", "ignore", "inline"}
_ALLOWED_TRANSLATORS = {"local:marian", "local:m2m", "deepl", "openai"}
_ALLOWED_PROFILES = {"default", "fast", "hi_fidelity"}

_EXT_RE = re.compile(r"^\.\w[\w\d-]*$")
_RANGE_RE = re.compile(r"^\s*\d+(?:\s*-\s*\d+)?(?:\s*,\s*\d+(?:\s*-\s*\d+)?)*\s*$")


def _bool(value: Any, *, default: bool | None = None) -> bool:
    """Coerce common truthy/falsey strings to bool.

    Accepts booleans, ints (0/1), and strings like "true"/"false", "yes"/"no",
    case-insensitive. Raises SettingsError for unsupported inputs when default
    is None; otherwise returns the provided default.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        if default is None:
            raise SettingsError("boolean value required (got None)")
        return default
    if isinstance(value, (int,)):
        return bool(value)
    s = str(value).strip().lower()
    if s in _BOOL_TRUE:
        return True
    if s in _BOOL_FALSE:
        return False
    if default is not None:
        return default
    raise SettingsError(f"invalid boolean value: {value!r}; use one of {_BOOL_TRUE | _BOOL_FALSE}")


def _int(value: Any, *, name: str, min_value: int | None = None) -> int:
    """Parse int and validate optional minimum.

    Raises SettingsError naming the field and allowed range on error.
    """
    try:
        iv = int(value)
    except Exception as e:  # pragma: no cover - trivial
        raise SettingsError(f"{name} must be an integer (got: {value!r})") from e
    if min_value is not None and iv < min_value:
        raise SettingsError(f"{name} must be >= {min_value} (got: {iv})")
    return iv


def _float(
    value: Any, *, name: str, min_value: float | None = None, max_value: float | None = None
) -> float:
    try:
        fv = float(value)
    except Exception as e:
        raise SettingsError(f"{name} must be a number (got: {value!r})") from e
    if min_value is not None and fv < min_value:
        raise SettingsError(f"{name} must be >= {min_value} (got: {fv})")
    if max_value is not None and fv > max_value:
        raise SettingsError(f"{name} must be <= {max_value} (got: {fv})")
    return fv


def _csv(value: Any) -> tuple[str, ...]:
    """Parse a comma-separated list into a tuple of trimmed non-empty strings."""
    if value is None:
        return tuple()
    if isinstance(value, (list, tuple)):
        items = [str(v).strip() for v in value]
    else:
        items = [s.strip() for s in str(value).split(",")]
    return tuple(s for s in items if s)


def _normalize_exts(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize file extensions to deterministic internal form.

    Rules
    - Leading dot enforced
    - Lower-case
    - Deduplicated and sorted lexicographically
    - Must match a conservative pattern (letters/digits/hyphen after dot)
    """
    norm = set()
    for v in values:
        s = str(v).strip().lower()
        if not s:
            continue
        if not s.startswith("."):
            s = "." + s
        if not _EXT_RE.match(s):
            raise SettingsError(f"invalid extension {v!r}; use forms like '.pdf', '.docx', '.xlsx'")
        norm.add(s)
    return tuple(sorted(norm))


def _normalize_globs(values: Iterable[str]) -> tuple[str, ...]:
    """Normalize glob patterns to a deterministic, lexicographically sorted tuple."""
    uniq = {str(v).strip() for v in values if str(v).strip()}
    return tuple(sorted(uniq))


def _normalize_page_range(spec: str | None) -> str | None:
    """Validate and normalize a page range spec like "1-3,5".

    Returns the stripped spec or None. Raises SettingsError if malformed.
    """
    if spec is None or str(spec).strip() == "":
        return None
    s = str(spec).strip()
    if not _RANGE_RE.match(s):
        raise SettingsError(
            "PDF page range must look like '1-3,5' (digits, dashes, commas, optional spaces)"
        )
    return s


def _abs_path(
    value: str | os.PathLike[str], *, name: str, must_exist: bool = False, must_be_dir: bool = False
) -> Path:
    p = Path(value).expanduser().resolve()
    if must_exist and not p.exists():
        raise SettingsError(f"{name} not found: {p}. Provide an existing path.")
    if must_be_dir and not p.is_dir():
        raise SettingsError(f"{name} must be a directory (got: {p}). Provide a directory path.")
    return p


def _enum(value: Any, *, name: str, allowed: Iterable[str]) -> str:
    if value is None:
        raise SettingsError(f"{name} is required.")
    s = str(value).strip().lower()
    allowed_set = {a.lower() for a in allowed}
    if s not in allowed_set:
        raise SettingsError(f"invalid {name}: {value!r}; allowed: {sorted(allowed_set)}")
    return s


def _load_dotenv_if_present(env: MutableMapping[str, str]) -> None:
    """Load .env into env mapping if present; does not override explicit env.

    Priority: existing os.environ values win over .env.
    Minimal parser used when python-dotenv is unavailable.
    """
    cwd = Path.cwd()
    candidate = cwd / ".env"
    if not candidate.exists():
        return
    try:
        if _dotenv is not None:
            # Load without overriding existing env
            _dotenv.load_dotenv(dotenv_path=str(candidate), override=False)  # type: ignore
            return
    except Exception:
        # Fallback to manual parse
        pass
    try:
        with candidate.open("r", encoding="utf-8") as fh:
            for line in fh:
                s = line.strip()
                if not s or s.startswith("#"):
                    continue
                if "=" not in s:
                    continue
                k, v = s.split("=", 1)
                k = k.strip()
                v = v.strip().strip('"').strip("'")
                if k and k not in env:
                    env[k] = v
    except Exception:
        # Silently ignore malformed .env; explicit env/CLI still work
        return


# endregion

# region Settings dataclass


@dataclass(frozen=True)
class Settings:
    """Immutable configuration for a preprocessing run.

    Build instances via build_from_env() or apply_overrides() to enforce
    precedence, normalization, and validation. The object is frozen; any
    attempted mutation raises FrozenInstanceError.

    Attributes
        app_name: Application identifier string.
        app_version: Semantic version string.
        profile: Tuning bundle: "default", "fast", or "hi_fidelity".
        log_level: Log verbosity level.
        strict: If true, treat non-critical warnings as errors.
        dry_run: If true, perform scan and planning only; do not write outputs.
        workers: Parallel worker processes/threads to use (>=1).
        on_error: Policy for individual file failures: "skip" or "stop".

        src_dir: Absolute source directory to scan; must exist.
        out_dir: Absolute output base directory; may not exist yet.

        recurse: Whether to recursively scan subdirectories.
        include_ext: Whitelist of file extensions to process (normalized, sorted).
        exclude_glob: Glob patterns to exclude deterministically.
        max_files: Optional limit of files to process; None means no limit.

        normalize_eol: "lf" to canonicalize, "keep" to preserve input newlines.
        utf8_enforce: Ensure decoded text is UTF-8; if false, best-effort decode.

        assets_subdir: Subdirectory under out_dir for extracted assets.
        write_meta: Metadata writing policy: "none", "sidecar", or "inline".
        overwrite: Overwrite existing outputs if true; otherwise skip.
        report_path: Optional absolute report path to write a run report.

        escape_pipes_in_tables: Escape pipe characters in Markdown tables.
        trim_trailing_spaces: Trim trailing spaces from output text.

        docx_export_images: Export images from DOCX when available.
        docx_assets_subdir: Subdir for DOCX embedded assets.

        xlsx_header_rows: Number of header rows to promote.
        xlsx_render_mode: "display" applies basic formatting; "raw" uses raw values.
        xlsx_max_rows: Optional rows cap for large spreadsheets.
        xlsx_max_cols: Optional columns cap for large spreadsheets.
        xlsx_merged_cells_policy: How to fill merged cells: "fill" or "topleft".

        pdf_page_range: Optional string like "1-3,5" to limit pages.
        pdf_remove_headers_footers: Heuristic removal of headers/footers.
        pdf_page_divider: Divider inserted between pages in combined text.
        pdf_export_images: Export images from PDFs.
        pdf_assets_subdir: Subdir for PDF extracted assets.
        pdf_table_detection: Table langid engine: "auto", "none", "camelot", or "tabula".

        ocr_enabled: Enable OCR for image-only content.
        ocr_langs: Tuple of language codes for OCR engine.
        ocr_confidence_threshold: Float in [0,1]; quality gating for OCR text.
        ocr_fail_on_low_confidence: If true, treat low-confidence OCR as error.
        ocr_max_working_dpi: Maximum DPI to rasterize pages/images for OCR.
        save_processed_assets: Save intermediate/preprocessed images for audit.

        msg_prefer_body: Priority of body formats to extract from .msg files.
        msg_export_assets: Export attachments and inline assets from .msg.
        msg_assets_subdir: Subdir for .msg extracted assets.
        msg_quoted_reply_mode: Handling of quoted replies in emails.

        translator: Forward-compatible translator backend name.
        mt_model_id: Forward-compatible machine translate model id.
        mt_device: Forward-compatible device string (e.g., "cpu", "cuda:0").
        mt_max_tokens: Forward-compatible MT token cap.
        mt_batch_size: Forward-compatible MT batch size.

        safe_order: Forward-compatible privacy knob; ignored by convert-only.

        vector_chunk_size: Forward-compatible chunk size for embeddings.
        vector_chunk_overlap: Forward-compatible chunk overlap.
        vector_embeddings: Forward-compatible embeddings model name.
        qdrant_url: Forward-compatible Qdrant URL.
        qdrant_collection: Forward-compatible Qdrant collection name.

        origin: Immutable mapping of critical settings to their source
            ("default", "profile", "env", or "cli"). Provided for audit.
    """

    # General
    app_name: str = "habithon-preprocessing"
    app_version: str = _PKG_VERSION
    profile: str = "default"
    log_level: str = "INFO"
    strict: bool = False
    dry_run: bool = False
    workers: int = 1
    on_error: str = "skip"

    # Roots
    src_dir: Path = field(default_factory=lambda: Path.cwd())
    out_dir: Path = field(default_factory=lambda: Path.cwd() / "out")

    # Selection
    recurse: bool = True
    include_ext: tuple[str, ...] = (
        ".docx",
        ".xlsx",
        ".pdf",
        ".jpg",
        ".jpeg",
        ".msg",
    )
    exclude_glob: tuple[str, ...] = tuple()
    max_files: int | None = None

    # Encoding / Serializer
    normalize_eol: str = "lf"
    utf8_enforce: bool = True
    assets_subdir: str = "assets"
    write_meta: str = "sidecar"
    overwrite: bool = False
    report_path: Path | None = None

    # Parsers (common)
    escape_pipes_in_tables: bool = True
    trim_trailing_spaces: bool = True

    # DOCX
    docx_export_images: bool = False
    docx_assets_subdir: str = "docx_assets"

    # XLSX
    xlsx_header_rows: int = 1
    xlsx_render_mode: str = "display"
    xlsx_max_rows: int | None = None
    xlsx_max_cols: int | None = None
    xlsx_merged_cells_policy: str = "fill"

    # PDF
    pdf_page_range: str | None = None
    pdf_remove_headers_footers: bool = True
    pdf_page_divider: str = "\n\n---\n\n"
    pdf_export_images: bool = False
    pdf_assets_subdir: str = "pdf_assets"
    pdf_table_detection: str = "auto"

    # OCR
    ocr_enabled: bool = True
    ocr_langs: tuple[str, ...] = ("eng",)
    ocr_confidence_threshold: float = 0.55
    ocr_fail_on_low_confidence: bool = False
    ocr_max_working_dpi: int = 300
    save_processed_assets: bool = False

    # MSG
    msg_prefer_body: tuple[str, ...] = ("html", "text", "rtf")
    msg_export_assets: bool = True
    msg_assets_subdir: str = "msg_assets"
    msg_quoted_reply_mode: str = "blockquote"

    # Translation (forward-compatible)
    translator: str | None = None
    mt_model_id: str | None = None
    mt_device: str | None = None
    mt_max_tokens: int | None = None
    mt_batch_size: int | None = None

    # Privacy (forward-compatible)
    safe_order: bool = False

    # Vector store (forward-compatible)
    vector_chunk_size: int | None = None
    vector_chunk_overlap: int | None = None
    vector_embeddings: str | None = None
    qdrant_url: str | None = None
    qdrant_collection: str | None = None

    # Audit
    origin: Mapping[str, str] = field(default_factory=lambda: MappingProxyType({}))


# endregion

# region Builders

_CRITICAL_ORIGIN_FIELDS = (
    "src_dir",
    "out_dir",
    "recurse",
    "include_ext",
    "exclude_glob",
    "max_files",
    "assets_subdir",
    "write_meta",
    "overwrite",
    "report_path",
)


def _apply_profile_defaults(base: dict[str, Any], profile: str) -> None:
    p = profile.lower().strip()
    if p not in _ALLOWED_PROFILES:
        raise SettingsError(f"invalid profile {profile!r}; allowed: {sorted(_ALLOWED_PROFILES)}")
    base["profile"] = p
    if p == "fast":
        base.update(
            {
                "docx_export_images": False,
                "pdf_export_images": False,
                "ocr_max_working_dpi": 200,
                "xlsx_max_rows": 10000,
                "xlsx_max_cols": 50,
                "save_processed_assets": False,
            }
        )
    elif p == "hi_fidelity":
        base.update(
            {
                "docx_export_images": True,
                "pdf_export_images": True,
                "pdf_remove_headers_footers": True,
                "ocr_confidence_threshold": max(base.get("ocr_confidence_threshold", 0.55), 0.60),
                "save_processed_assets": True,
                "ocr_max_working_dpi": max(base.get("ocr_max_working_dpi", 300), 300),
            }
        )


def _build_from_mapping(
    env: Mapping[str, str], cli_overrides: Mapping[str, Any] | None = None
) -> Settings:
    # Start with explicit defaults in dict form
    cfg: dict[str, Any] = {
        # general
        "app_name": "habithon-preprocessing",
        "app_version": _PKG_VERSION,
        "profile": "default",
        "log_level": "INFO",
        "strict": False,
        "dry_run": False,
        "workers": 1,
        "on_error": "skip",
        # roots
        # src_dir and out_dir intentionally absent until required parsing
        # selection
        "recurse": True,
        "include_ext": (".docx", ".xlsx", ".pdf", ".jpg", ".jpeg", ".msg"),
        "exclude_glob": tuple(),
        "max_files": None,
        # encoding/serializer
        "normalize_eol": "lf",
        "utf8_enforce": True,
        "assets_subdir": "assets",
        "write_meta": "sidecar",
        "overwrite": False,
        "report_path": None,
        # common parsers
        "escape_pipes_in_tables": True,
        "trim_trailing_spaces": True,
        # docx
        "docx_export_images": False,
        "docx_assets_subdir": "docx_assets",
        # xlsx
        "xlsx_header_rows": 1,
        "xlsx_render_mode": "display",
        "xlsx_max_rows": None,
        "xlsx_max_cols": None,
        "xlsx_merged_cells_policy": "fill",
        # pdf
        "pdf_page_range": None,
        "pdf_remove_headers_footers": True,
        "pdf_page_divider": "\n\n---\n\n",
        "pdf_export_images": False,
        "pdf_assets_subdir": "pdf_assets",
        "pdf_table_detection": "auto",
        # ocr
        "ocr_enabled": True,
        "ocr_langs": ("eng",),
        "ocr_confidence_threshold": 0.55,
        "ocr_fail_on_low_confidence": False,
        "ocr_max_working_dpi": 300,
        "save_processed_assets": False,
        # msg
        "msg_prefer_body": ("html", "text", "rtf"),
        "msg_export_assets": True,
        "msg_assets_subdir": "msg_assets",
        "msg_quoted_reply_mode": "blockquote",
        # translate
        "translator": None,
        "mt_model_id": None,
        "mt_device": None,
        "mt_max_tokens": None,
        "mt_batch_size": None,
        # privacy
        "safe_order": False,
        # vector
        "vector_chunk_size": None,
        "vector_chunk_overlap": None,
        "vector_embeddings": None,
        "qdrant_url": None,
        "qdrant_collection": None,
    }

    origin: dict[str, str] = {}

    # 1) Profile first (env-provided profile value can change it)
    # If APP_PROFILE present, use it; else default
    profile_env = env.get("APP_PROFILE", cfg["profile"]) or cfg["profile"]
    _apply_profile_defaults(cfg, str(profile_env))

    # 2) Apply environment variables (with parsing/validation)
    # General
    if (v := env.get("LOG_LEVEL")) is not None:
        cfg["log_level"] = str(v).strip().upper()
    if (v := env.get("STRICT")) is not None:
        cfg["strict"] = _bool(v)
    if (v := env.get("DRY_RUN")) is not None:
        cfg["dry_run"] = _bool(v)
    if (v := env.get("WORKERS")) is not None:
        cfg["workers"] = _int(v, name="workers", min_value=1)
    if (v := env.get("ON_ERROR")) is not None:
        cfg["on_error"] = _enum(v, name="on_error", allowed=_ALLOWED_ON_ERROR)

    # Roots
    if (v := env.get("SRC_DIR")) is not None:
        cfg["src_dir"] = _abs_path(v, name="SRC_DIR", must_exist=True, must_be_dir=True)
        origin["src_dir"] = "env"
    if (v := env.get("OUT_DIR")) is not None:
        cfg["out_dir"] = _abs_path(v, name="OUT_DIR", must_exist=False, must_be_dir=False)
        origin["out_dir"] = "env"

    # Selection
    if (v := env.get("RECURSE")) is not None:
        cfg["recurse"] = _bool(v)
        origin["recurse"] = "env"
    if (v := env.get("INCLUDE_EXT")) is not None:
        cfg["include_ext"] = _normalize_exts(_csv(v))
        origin["include_ext"] = "env"
    if (v := env.get("EXCLUDE_GLOB")) is not None:
        cfg["exclude_glob"] = _normalize_globs(_csv(v))
        origin["exclude_glob"] = "env"
    if (v := env.get("MAX_FILES")) is not None and str(v).strip() != "":
        iv = _int(v, name="max_files", min_value=1)
        cfg["max_files"] = iv
        origin["max_files"] = "env"

    # Encoding / Serializer
    if (v := env.get("NORMALIZE_EOL")) is not None:
        cfg["normalize_eol"] = _enum(v, name="normalize_eol", allowed=_ALLOWED_EOL)
    if (v := env.get("UTF8_ENFORCE")) is not None:
        cfg["utf8_enforce"] = _bool(v)
    if (v := env.get("ASSETS_SUBDIR")) is not None:
        cfg["assets_subdir"] = str(v).strip()
        origin["assets_subdir"] = "env"
    if (v := env.get("WRITE_META")) is not None:
        cfg["write_meta"] = _enum(v, name="write_meta", allowed=_ALLOWED_WRITE_META)
        origin["write_meta"] = "env"
    if (v := env.get("OVERWRITE")) is not None:
        cfg["overwrite"] = _bool(v)
        origin["overwrite"] = "env"
    if (v := env.get("REPORT_PATH")) is not None and str(v).strip() != "":
        cfg["report_path"] = _abs_path(v, name="REPORT_PATH", must_exist=False, must_be_dir=False)
        origin["report_path"] = "env"

    # Parsers (common)
    if (v := env.get("ESCAPE_PIPES_IN_TABLES")) is not None:
        cfg["escape_pipes_in_tables"] = _bool(v)
    if (v := env.get("TRIM_TRAILING_SPACES")) is not None:
        cfg["trim_trailing_spaces"] = _bool(v)

    # DOCX
    if (v := env.get("DOCX_EXPORT_IMAGES")) is not None:
        cfg["docx_export_images"] = _bool(v)
    if (v := env.get("DOCX_ASSETS_SUBDIR")) is not None:
        cfg["docx_assets_subdir"] = str(v).strip()

    # XLSX
    if (v := env.get("XLSX_HEADER_ROWS")) is not None:
        cfg["xlsx_header_rows"] = _int(v, name="xlsx_header_rows", min_value=0)
    if (v := env.get("XLSX_RENDER_MODE")) is not None:
        cfg["xlsx_render_mode"] = _enum(
            v, name="xlsx_render_mode", allowed=_ALLOWED_XLSX_RENDER_MODE
        )
    if (v := env.get("XLSX_MAX_ROWS")) is not None and str(v).strip() != "":
        cfg["xlsx_max_rows"] = _int(v, name="xlsx_max_rows", min_value=1)
    if (v := env.get("XLSX_MAX_COLS")) is not None and str(v).strip() != "":
        cfg["xlsx_max_cols"] = _int(v, name="xlsx_max_cols", min_value=1)
    if (v := env.get("XLSX_MERGED_CELLS_POLICY")) is not None:
        cfg["xlsx_merged_cells_policy"] = _enum(
            v, name="xlsx_merged_cells_policy", allowed=_ALLOWED_XLSX_MERGED_POLICY
        )

    # PDF
    if (v := env.get("PDF_PAGE_RANGE")) is not None:
        cfg["pdf_page_range"] = _normalize_page_range(v)
    if (v := env.get("PDF_REMOVE_HEADERS_FOOTERS")) is not None:
        cfg["pdf_remove_headers_footers"] = _bool(v)
    if (v := env.get("PDF_PAGE_DIVIDER")) is not None:
        cfg["pdf_page_divider"] = str(v)
    if (v := env.get("PDF_EXPORT_IMAGES")) is not None:
        cfg["pdf_export_images"] = _bool(v)
    if (v := env.get("PDF_ASSETS_SUBDIR")) is not None:
        cfg["pdf_assets_subdir"] = str(v).strip()
    if (v := env.get("PDF_TABLE_DETECTION")) is not None:
        cfg["pdf_table_detection"] = _enum(
            v, name="pdf_table_detection", allowed=_ALLOWED_PDF_TABLE_DET
        )

    # OCR
    if (v := env.get("OCR_ENABLED")) is not None:
        cfg["ocr_enabled"] = _bool(v)
    if (v := env.get("OCR_LANGS")) is not None:
        langs = tuple(x.lower() for x in _csv(v)) or ("eng",)
        cfg["ocr_langs"] = langs
    if (v := env.get("OCR_CONFIDENCE_THRESHOLD")) is not None:
        cfg["ocr_confidence_threshold"] = _float(
            v, name="ocr_confidence_threshold", min_value=0.0, max_value=1.0
        )
    if (v := env.get("OCR_FAIL_ON_LOW_CONFIDENCE")) is not None:
        cfg["ocr_fail_on_low_confidence"] = _bool(v)
    if (v := env.get("OCR_MAX_WORKING_DPI")) is not None:
        cfg["ocr_max_working_dpi"] = _int(v, name="ocr_max_working_dpi", min_value=1)
    if (v := env.get("SAVE_PROCESSED_ASSETS")) is not None:
        cfg["save_processed_assets"] = _bool(v)

    # MSG
    if (v := env.get("MSG_PREFER_BODY")) is not None:
        prefs = tuple(x.lower() for x in _csv(v))
        cfg["msg_prefer_body"] = prefs or ("html", "text", "rtf")
    if (v := env.get("MSG_EXPORT_ASSETS")) is not None:
        cfg["msg_export_assets"] = _bool(v)
    if (v := env.get("MSG_ASSETS_SUBDIR")) is not None:
        cfg["msg_assets_subdir"] = str(v).strip()
    if (v := env.get("MSG_QUOTED_REPLY_MODE")) is not None:
        cfg["msg_quoted_reply_mode"] = _enum(
            v, name="msg_quoted_reply_mode", allowed=_ALLOWED_MSG_REPLY_MODE
        )

    # Translation
    if (v := env.get("TRANSLATOR")) is not None and str(v).strip() != "":
        cfg["translator"] = _enum(v, name="translator", allowed=_ALLOWED_TRANSLATORS)
    if (v := env.get("MT_MODEL_ID")) is not None and str(v).strip() != "":
        cfg["mt_model_id"] = str(v).strip()
    if (v := env.get("MT_DEVICE")) is not None and str(v).strip() != "":
        cfg["mt_device"] = str(v).strip()
    if (v := env.get("MT_MAX_TOKENS")) is not None and str(v).strip() != "":
        cfg["mt_max_tokens"] = _int(v, name="mt_max_tokens", min_value=1)
    if (v := env.get("MT_BATCH_SIZE")) is not None and str(v).strip() != "":
        cfg["mt_batch_size"] = _int(v, name="mt_batch_size", min_value=1)

    # Privacy
    if (v := env.get("SAFE_ORDER")) is not None:
        cfg["safe_order"] = _bool(v)

    # Vector store
    if (v := env.get("VECTOR_CHUNK_SIZE")) is not None and str(v).strip() != "":
        cfg["vector_chunk_size"] = _int(v, name="vector_chunk_size", min_value=1)
    if (v := env.get("VECTOR_CHUNK_OVERLAP")) is not None and str(v).strip() != "":
        cfg["vector_chunk_overlap"] = _int(v, name="vector_chunk_overlap", min_value=0)
    if (v := env.get("VECTOR_EMBEDDINGS")) is not None and str(v).strip() != "":
        cfg["vector_embeddings"] = str(v).strip()
    if (v := env.get("QDRANT_URL")) is not None and str(v).strip() != "":
        cfg["qdrant_url"] = str(v).strip()
    if (v := env.get("QDRANT_COLLECTION")) is not None and str(v).strip() != "":
        cfg["qdrant_collection"] = str(v).strip()

    # 3) Apply CLI overrides with validation; track origin for critical fields
    if cli_overrides:
        for k, v in cli_overrides.items():
            kn = str(k)
            if kn == "src_dir":
                cfg[kn] = _abs_path(v, name="src_dir", must_exist=True, must_be_dir=True)
            elif kn == "out_dir":
                cfg[kn] = _abs_path(v, name="out_dir", must_exist=False, must_be_dir=False)
            elif kn == "recurse":
                cfg[kn] = _bool(v)
            elif kn == "include_ext":
                cfg[kn] = _normalize_exts(v if isinstance(v, (list, tuple)) else _csv(v))
            elif kn == "exclude_glob":
                cfg[kn] = _normalize_globs(v if isinstance(v, (list, tuple)) else _csv(v))
            elif kn == "max_files":
                cfg[kn] = None if v is None else _int(v, name="max_files", min_value=1)
            elif kn == "assets_subdir":
                cfg[kn] = str(v).strip()
            elif kn == "write_meta":
                cfg[kn] = _enum(v, name="write_meta", allowed=_ALLOWED_WRITE_META)
            elif kn == "overwrite":
                cfg[kn] = _bool(v)
            elif kn == "report_path":
                cfg[kn] = (
                    None if v in (None, "") else _abs_path(v, name="report_path", must_exist=False)
                )
            else:
                # Generic assignment; additional specific constraints validated below
                cfg[kn] = v
            if kn in _CRITICAL_ORIGIN_FIELDS:
                origin[kn] = "cli"

    # Required fields: src_dir, out_dir
    # If not provided, try environment (already set) or fail
    if "src_dir" not in cfg:
        raise SettingsError(
            "SRC_DIR must be provided (env or CLI) and point to an existing directory."
        )
    if "out_dir" not in cfg:
        raise SettingsError("OUT_DIR must be provided (env or CLI) to determine output location.")

    # Secondary validations and coercions
    if cfg["workers"] < 1:
        raise SettingsError(f"workers must be >= 1 (got: {cfg['workers']})")
    if cfg["max_files"] is not None and cfg["max_files"] <= 0:
        raise SettingsError(f"max_files must be > 0 (got: {cfg['max_files']})")
    if cfg["normalize_eol"] not in _ALLOWED_EOL:
        raise SettingsError(
            f"normalize_eol must be one of {sorted(_ALLOWED_EOL)} (got: {cfg['normalize_eol']!r})"
        )
    if cfg["on_error"] not in _ALLOWED_ON_ERROR:
        raise SettingsError(
            f"on_error must be one of {sorted(_ALLOWED_ON_ERROR)} (got: {cfg['on_error']!r})"
        )
    if cfg["write_meta"] not in _ALLOWED_WRITE_META:
        raise SettingsError(
            f"write_meta must be one of {sorted(_ALLOWED_WRITE_META)} (got: {cfg['write_meta']!r})"
        )
    if cfg["xlsx_render_mode"] not in _ALLOWED_XLSX_RENDER_MODE:
        raise SettingsError(
            f"xlsx_render_mode must be one of {sorted(_ALLOWED_XLSX_RENDER_MODE)} (got: {cfg['xlsx_render_mode']!r})"
        )
    if cfg["xlsx_merged_cells_policy"] not in _ALLOWED_XLSX_MERGED_POLICY:
        raise SettingsError(
            f"xlsx_merged_cells_policy must be one of {sorted(_ALLOWED_XLSX_MERGED_POLICY)} (got: {cfg['xlsx_merged_cells_policy']!r})"
        )
    if cfg["pdf_table_detection"] not in _ALLOWED_PDF_TABLE_DET:
        raise SettingsError(
            f"pdf_table_detection must be one of {sorted(_ALLOWED_PDF_TABLE_DET)} (got: {cfg['pdf_table_detection']!r})"
        )
    if cfg.get("translator") is not None and cfg["translator"] not in _ALLOWED_TRANSLATORS:
        raise SettingsError(
            f"translator must be one of {sorted(_ALLOWED_TRANSLATORS)} (got: {cfg['translator']!r})"
        )

    # Normalize extension and globs deterministically
    cfg["include_ext"] = _normalize_exts(cfg.get("include_ext", ()))
    cfg["exclude_glob"] = _normalize_globs(cfg.get("exclude_glob", ()))

    # Normalize MSG body preference values to deterministic tuple
    cfg["msg_prefer_body"] = tuple(
        str(x).lower().strip() for x in cfg.get("msg_prefer_body", ()) if str(x).strip()
    )
    if not cfg["msg_prefer_body"]:
        cfg["msg_prefer_body"] = ("html", "text", "rtf")

    # If write_meta="inline", note: callers may still write minimal sidecars for assets; documented behavior.

    # Build immutable origin mapping; fill unspecified critical fields with inferred source
    # Determine source for those not explicitly set: default or profile vs env
    for f in _CRITICAL_ORIGIN_FIELDS:
        if f not in origin:
            # If value equals profile-adjusted default but env had no explicit set, mark as profile or default
            # Simplify: if profile != default, mark as profile; else default
            origin[f] = "profile" if cfg["profile"] != "default" else "default"

    origin_proxy = MappingProxyType(dict(origin))

    # Create Settings instance (frozen)
    s = Settings(
        app_name=cfg["app_name"],
        app_version=cfg["app_version"],
        profile=cfg["profile"],
        log_level=cfg["log_level"],
        strict=cfg["strict"],
        dry_run=cfg["dry_run"],
        workers=cfg["workers"],
        on_error=cfg["on_error"],
        src_dir=cfg["src_dir"],
        out_dir=cfg["out_dir"],
        recurse=cfg["recurse"],
        include_ext=cfg["include_ext"],
        exclude_glob=cfg["exclude_glob"],
        max_files=cfg["max_files"],
        normalize_eol=cfg["normalize_eol"],
        utf8_enforce=cfg["utf8_enforce"],
        assets_subdir=cfg["assets_subdir"],
        write_meta=cfg["write_meta"],
        overwrite=cfg["overwrite"],
        report_path=cfg["report_path"],
        escape_pipes_in_tables=cfg["escape_pipes_in_tables"],
        trim_trailing_spaces=cfg["trim_trailing_spaces"],
        docx_export_images=cfg["docx_export_images"],
        docx_assets_subdir=cfg["docx_assets_subdir"],
        xlsx_header_rows=cfg["xlsx_header_rows"],
        xlsx_render_mode=cfg["xlsx_render_mode"],
        xlsx_max_rows=cfg["xlsx_max_rows"],
        xlsx_max_cols=cfg["xlsx_max_cols"],
        xlsx_merged_cells_policy=cfg["xlsx_merged_cells_policy"],
        pdf_page_range=cfg["pdf_page_range"],
        pdf_remove_headers_footers=cfg["pdf_remove_headers_footers"],
        pdf_page_divider=cfg["pdf_page_divider"],
        pdf_export_images=cfg["pdf_export_images"],
        pdf_assets_subdir=cfg["pdf_assets_subdir"],
        pdf_table_detection=cfg["pdf_table_detection"],
        ocr_enabled=cfg["ocr_enabled"],
        ocr_langs=cfg["ocr_langs"],
        ocr_confidence_threshold=cfg["ocr_confidence_threshold"],
        ocr_fail_on_low_confidence=cfg["ocr_fail_on_low_confidence"],
        ocr_max_working_dpi=cfg["ocr_max_working_dpi"],
        save_processed_assets=cfg["save_processed_assets"],
        msg_prefer_body=cfg["msg_prefer_body"],
        msg_export_assets=cfg["msg_export_assets"],
        msg_assets_subdir=cfg["msg_assets_subdir"],
        msg_quoted_reply_mode=cfg["msg_quoted_reply_mode"],
        translator=cfg["translator"],
        mt_model_id=cfg["mt_model_id"],
        mt_device=cfg["mt_device"],
        mt_max_tokens=cfg["mt_max_tokens"],
        mt_batch_size=cfg["mt_batch_size"],
        safe_order=cfg["safe_order"],
        vector_chunk_size=cfg["vector_chunk_size"],
        vector_chunk_overlap=cfg["vector_chunk_overlap"],
        vector_embeddings=cfg["vector_embeddings"],
        qdrant_url=cfg["qdrant_url"],
        qdrant_collection=cfg["qdrant_collection"],
        origin=origin_proxy,
    )
    return s


# endregion

# region Public API


def build_from_env(cli_overrides: dict[str, Any] | None = None) -> Settings:
    """Build immutable Settings from defaults, profile, env/.env, and CLI overrides.

    The precedence model is: Defaults < Profile < Environment/.env < CLI overrides.

    Args:
        cli_overrides: Optional dictionary of explicit CLI values to apply on top
            of env-derived configuration. Keys correspond to Settings field names.
            Accepted types mirror Settings field types; lists may be provided for
            list-like fields (e.g., include_ext, exclude_glob). Paths can be str
            or Path. Validation and normalization are applied.

    Returns:
        A frozen Settings instance with normalized values and an immutable origin
        mapping for critical fields.

    Raises:
        SettingsError: If required roots are missing, paths are invalid, numeric
            values are out-of-range, or enums contain unsupported values.

    Notes:
        - This function will read a local .env file if present. Values already
          present in the process environment are not overridden by .env.
        - Directory creation is not performed; callers should create out_dir if
          needed.
    """
    # Load .env without clobbering explicit environment
    env = dict(os.environ)
    _load_dotenv_if_present(env)
    # Re-read after potential .env load
    env = dict(os.environ) | env  # ensure os.environ precedence

    return _build_from_mapping(env, cli_overrides)


def apply_overrides(settings: Settings, overrides: dict[str, Any]) -> Settings:
    """Return a new Settings with overrides applied and fully validated.

    This function re-applies precedence with the provided overrides on top of
    the original settings' environment-derived values.

    Args:
        settings: Existing immutable Settings instance to base on.
        overrides: Mapping of fields to new values. Types and validation match
            those in build_from_env().

    Returns:
        A new frozen Settings with the overrides applied.

    Raises:
        SettingsError: If an override contains invalid values or conflicts.

    Example:
        >>> s1 = build_from_env({"src_dir": "/data/in", "out_dir": "/data/out"})
        >>> s2 = apply_overrides(s1, {"workers": 4, "overwrite": True})
    """
    # Seed env-like mapping from current settings' to_dict; treat as defaults
    base = to_dict(settings)

    # Convert certain complex types back to env-like strings where necessary
    # but we'll pass as native types to _build_from_mapping via cli_overrides
    env: dict[str, str] = {}

    # Populate only keys that are env sourced; rest will come from cfg defaults
    # We reuse build logic; pass all current fields via cli to preserve values
    current_as_cli: dict[str, Any] = {
        # Map directly; include roots and criticals
        "src_dir": settings.src_dir,
        "out_dir": settings.out_dir,
        "recurse": settings.recurse,
        "include_ext": settings.include_ext,
        "exclude_glob": settings.exclude_glob,
        "max_files": settings.max_files,
        "assets_subdir": settings.assets_subdir,
        "write_meta": settings.write_meta,
        "overwrite": settings.overwrite,
        "report_path": settings.report_path,
        # General
        "profile": settings.profile,
        "log_level": settings.log_level,
        "strict": settings.strict,
        "dry_run": settings.dry_run,
        "workers": settings.workers,
        "on_error": settings.on_error,
        # Parsers and formats
        "normalize_eol": settings.normalize_eol,
        "utf8_enforce": settings.utf8_enforce,
        "escape_pipes_in_tables": settings.escape_pipes_in_tables,
        "trim_trailing_spaces": settings.trim_trailing_spaces,
        "docx_export_images": settings.docx_export_images,
        "docx_assets_subdir": settings.docx_assets_subdir,
        "xlsx_header_rows": settings.xlsx_header_rows,
        "xlsx_render_mode": settings.xlsx_render_mode,
        "xlsx_max_rows": settings.xlsx_max_rows,
        "xlsx_max_cols": settings.xlsx_max_cols,
        "xlsx_merged_cells_policy": settings.xlsx_merged_cells_policy,
        "pdf_page_range": settings.pdf_page_range,
        "pdf_remove_headers_footers": settings.pdf_remove_headers_footers,
        "pdf_page_divider": settings.pdf_page_divider,
        "pdf_export_images": settings.pdf_export_images,
        "pdf_assets_subdir": settings.pdf_assets_subdir,
        "pdf_table_detection": settings.pdf_table_detection,
        "ocr_enabled": settings.ocr_enabled,
        "ocr_langs": settings.ocr_langs,
        "ocr_confidence_threshold": settings.ocr_confidence_threshold,
        "ocr_fail_on_low_confidence": settings.ocr_fail_on_low_confidence,
        "ocr_max_working_dpi": settings.ocr_max_working_dpi,
        "save_processed_assets": settings.save_processed_assets,
        "msg_prefer_body": settings.msg_prefer_body,
        "msg_export_assets": settings.msg_export_assets,
        "msg_assets_subdir": settings.msg_assets_subdir,
        "msg_quoted_reply_mode": settings.msg_quoted_reply_mode,
        # Forward-compatible
        "translator": settings.translator,
        "mt_model_id": settings.mt_model_id,
        "mt_device": settings.mt_device,
        "mt_max_tokens": settings.mt_max_tokens,
        "mt_batch_size": settings.mt_batch_size,
        "safe_order": settings.safe_order,
        "vector_chunk_size": settings.vector_chunk_size,
        "vector_chunk_overlap": settings.vector_chunk_overlap,
        "vector_embeddings": settings.vector_embeddings,
        "qdrant_url": settings.qdrant_url,
        "qdrant_collection": settings.qdrant_collection,
    }

    # Apply overrides on top
    merged_cli = {**current_as_cli, **(overrides or {})}

    return _build_from_mapping(env, merged_cli)


def to_dict(settings: Settings, redacted: bool = False) -> dict[str, Any]:
    """Convert Settings to a deterministic, JSON-serializable dictionary.

    Args:
        settings: The Settings instance to serialize.
        redacted: If true, omit or mask future-sensitive fields (API keys, tokens).
            Currently, no sensitive fields are present; kept for forward-compat.

    Returns:
        A plain dict with stable key ordering grouped by conceptual areas.

    Notes:
        - Paths are stringified as absolute POSIX-like paths for stability.
        - Tuples are serialized to lists for JSON friendliness.
    """

    def P(p: Path | None) -> str | None:
        return None if p is None else str(p)

    # Compute grouping order deterministically
    data: dict[str, Any] = {}
    data.update(
        {
            "app_name": settings.app_name,
            "app_version": settings.app_version,
            "profile": settings.profile,
            "log_level": settings.log_level,
            "strict": settings.strict,
            "dry_run": settings.dry_run,
            "workers": settings.workers,
            "on_error": settings.on_error,
        }
    )
    data.update(
        {
            "src_dir": P(settings.src_dir),
            "out_dir": P(settings.out_dir),
        }
    )
    data.update(
        {
            "recurse": settings.recurse,
            "include_ext": list(settings.include_ext),
            "exclude_glob": list(settings.exclude_glob),
            "max_files": settings.max_files,
        }
    )
    data.update(
        {
            "normalize_eol": settings.normalize_eol,
            "utf8_enforce": settings.utf8_enforce,
            "assets_subdir": settings.assets_subdir,
            "write_meta": settings.write_meta,
            "overwrite": settings.overwrite,
            "report_path": P(settings.report_path),
        }
    )
    data.update(
        {
            "escape_pipes_in_tables": settings.escape_pipes_in_tables,
            "trim_trailing_spaces": settings.trim_trailing_spaces,
        }
    )
    data.update(
        {
            "docx_export_images": settings.docx_export_images,
            "docx_assets_subdir": settings.docx_assets_subdir,
        }
    )
    data.update(
        {
            "xlsx_header_rows": settings.xlsx_header_rows,
            "xlsx_render_mode": settings.xlsx_render_mode,
            "xlsx_max_rows": settings.xlsx_max_rows,
            "xlsx_max_cols": settings.xlsx_max_cols,
            "xlsx_merged_cells_policy": settings.xlsx_merged_cells_policy,
        }
    )
    data.update(
        {
            "pdf_page_range": settings.pdf_page_range,
            "pdf_remove_headers_footers": settings.pdf_remove_headers_footers,
            "pdf_page_divider": settings.pdf_page_divider,
            "pdf_export_images": settings.pdf_export_images,
            "pdf_assets_subdir": settings.pdf_assets_subdir,
            "pdf_table_detection": settings.pdf_table_detection,
        }
    )
    data.update(
        {
            "ocr_enabled": settings.ocr_enabled,
            "ocr_langs": list(settings.ocr_langs),
            "ocr_confidence_threshold": settings.ocr_confidence_threshold,
            "ocr_fail_on_low_confidence": settings.ocr_fail_on_low_confidence,
            "ocr_max_working_dpi": settings.ocr_max_working_dpi,
            "save_processed_assets": settings.save_processed_assets,
        }
    )
    data.update(
        {
            "msg_prefer_body": list(settings.msg_prefer_body),
            "msg_export_assets": settings.msg_export_assets,
            "msg_assets_subdir": settings.msg_assets_subdir,
            "msg_quoted_reply_mode": settings.msg_quoted_reply_mode,
        }
    )
    data.update(
        {
            "translator": settings.translator,
            "mt_model_id": settings.mt_model_id,
            "mt_device": settings.mt_device,
            "mt_max_tokens": settings.mt_max_tokens,
            "mt_batch_size": settings.mt_batch_size,
            "safe_order": settings.safe_order,
        }
    )
    data.update(
        {
            "vector_chunk_size": settings.vector_chunk_size,
            "vector_chunk_overlap": settings.vector_chunk_overlap,
            "vector_embeddings": settings.vector_embeddings,
            "qdrant_url": settings.qdrant_url,
            "qdrant_collection": settings.qdrant_collection,
        }
    )

    if redacted:
        # No sensitive fields currently; placeholder for future redaction policy
        pass

    # Include origin mapping at the end for audit
    data["origin"] = dict(settings.origin)
    return data


def summary(settings: Settings) -> str:
    """Return a compact summary string for logs.

    Includes key fields: src, out, recurse, include_ext, workers, write_meta,
    overwrite.

    Args:
        settings: The Settings to summarize.

    Returns:
        Short one-line string suitable for logs.
    """
    exts = ",".join(settings.include_ext)
    return (
        f"src={settings.src_dir} out={settings.out_dir} recurse={settings.recurse} "
        f"ext=[{exts}] workers={settings.workers} write_meta={settings.write_meta} "
        f"overwrite={settings.overwrite}"
    )


def effective_include_ext(settings: Settings) -> tuple[str, ...]:
    """Return the normalized, deterministic extension whitelist.

    Args:
        settings: The Settings instance.

    Returns:
        A tuple of unique, lower-case extensions with leading dots, sorted
        lexicographically.
    """
    return settings.include_ext


# endregion
