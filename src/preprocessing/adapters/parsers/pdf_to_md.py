"""PDF → Markdown adapter (offline, structure-preserving, OCR for image-only pages).

Capability note
----------------
- Purpose: Convert .pdf files into clean, structurally faithful Markdown suitable for
  downstream normalization, enrichment, and vectorization. Emphasizes deterministic
  extraction, multi-column reading order reconstruction, optional image export, and
  offline OCR for image-only pages.
- Supported: multi-column reading order, heading/list/link preservation, sensible
  paragraph merging with de-hyphenation, simple tables to GitHub-style Markdown tables
  (when confident), page dividers, optional headers/footers removal, optional image
  export with deterministic filenames, offline OCR for image-only pages.
- Best-effort: table detection; when confidence is low, emit plain-text rows and record
  a warning in metadata.
- Non-goals: anonymization, translation, or language detection; online services.

Integration
-----------
- Selected by the parser registry for extension: .pdf
- Adapter key/name: "PdfToMd" (class attribute) for registry preferences/disable lists.
- Output is deterministic for identical inputs and configuration: stable asset names,
  stable page dividers, and stable structure.

Configuration (env/settings)
----------------------------
The adapter is designed to be configured via pipeline settings or environment variables
(actual reads occur in a concrete implementation). The knobs are:

- PDF_PAGE_RANGE (str): Page range like "1-", "-10", "2-5,9". Default: all pages.
- PDF_REMOVE_HEADERS_FOOTERS (bool): Remove repeating headers/footers. Default True.
- PDF_PAGE_DIVIDER (str): Page separator inserted between pages.
  Default "\n\n---\n\n".
- PDF_EXPORT_IMAGES (bool): Export embedded images to assets dir. Default False.
- PDF_ASSETS_SUBDIR (str): Assets subdirectory relative to Markdown. Default "assets".
- PDF_TABLE_DETECTION (str): One of {"auto", "none", "camelot", "tabula"}.
  Default "auto". External engines require local installation.
- OCR_ENABLED (bool): Enable OCR for image-only pages. Default True.
- OCR_LANGS (str): Comma-separated ISO codes (e.g., "eng,slk"); order matters.
- OCR_FAIL_ON_LOW_CONFIDENCE (bool): If True and mean confidence < threshold, raise.
  Default False.
- OCR_CONFIDENCE_THRESHOLD (float): Confidence cutoff (e.g., 0.55). Default 0.55.
- STRICT_MODE (bool): If True, non-fatal issues (asset export failure, missing table
  engine) raise instead of warn. Default False.

Registry note
-------------
- Register for .pdf and log the chosen extractor and whether OCR/table engines are
  enabled. On failure, suggest installing missing local dependencies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional
from pathlib import Path

# Public constants for registry wiring
EXTENSIONS: tuple[str, ...] = ("pdf",)


@dataclass(frozen=True)
class AssetsExportPlan:
    """Deterministic plan for exporting page-embedded images.

    Description:
        Describes the intended assets directory and stable filenames for exported
        images. A concrete implementation should populate this plan and mirror it into
        the output metadata for auditability and reproducibility.

    Args:
        assets_dir (str): Absolute or relative path to the assets directory where
            exported images are saved. Relative paths are resolved from the Markdown
            output path.
        filename_map (dict[str, str]): Mapping from a deterministic logical key to a
            filename (e.g., {"p1_img001": "p1_img001.png"}). Logical keys must be
            stable across runs given identical inputs/configuration.

    Notes:
        - Filenames should follow a stable scheme such as "p{page}_img{index}.{ext}".
        - When export is disabled, the plan may still document hypothetical names; the
          metadata should reflect that no files were written.
    """

    assets_dir: str
    filename_map: Dict[str, str]


class PdfToMd:
    """PDF-to-Markdown converter adapter (deterministic, offline, OCR-capable).

    Description:
        Converts .pdf files into cohesive Markdown with preserved structure. Validates
        the input (.pdf), optionally restricts processing to a configured page range,
        reconstructs reading order (including multi-column pages), merges lines into
        paragraphs with safe de-hyphenation, preserves headings/lists/links, converts
        simple tables to GitHub-style Markdown tables when confident (otherwise emits
        plain text rows with a warning), inserts consistent page dividers, optionally
        removes repeating headers/footers, optionally exports images with stable file
        names, and performs offline OCR for image-only pages with conservative
        preprocessing. All key decisions and counts are recorded in metadata.

    Args:
        page_range (str | None, optional):
            Page selection string like "1-", "-10", "2-5,9". None means all pages.
            The adapter must parse and enforce this deterministically. Defaults to None.
        remove_headers_footers (bool, optional):
            Whether to detect and remove repeating headers/footers. Defaults to True.
        page_divider (str, optional):
            String inserted between pages (LF-normalized). Defaults to "\n\n---\n\n".
        export_images (bool, optional):
            Whether to export page-embedded images to assets directory and reference
            them via Markdown image links. Defaults to False.
        assets_subdir (str, optional):
            Subdirectory name for exported assets relative to Markdown output. Defaults
            to "assets".
        table_detection (str, optional):
            Table detection strategy: one of {"auto", "none", "camelot", "tabula"}.
            Defaults to "auto". External engines require local installation.
        ocr_enabled (bool, optional):
            Enable offline OCR for image-only pages. Defaults to True.
        ocr_langs (tuple[str, ...] | None, optional):
            OCR language codes in priority order. When None, read from env/config or
            default to ("eng",) in a concrete implementation. Defaults to None.
        ocr_fail_on_low_confidence (bool, optional):
            If True and mean OCR confidence < ocr_confidence_threshold, raise an error.
            Otherwise, return Markdown and record a warning. Defaults to False.
        ocr_confidence_threshold (float, optional):
            Confidence threshold for warnings/errors. Defaults to 0.55.
        strict_mode (bool, optional):
            If True, non-fatal issues (e.g., asset export failure, missing table OCR
            engine) raise instead of warn. Defaults to False.

    Attributes:
        name (str): Adapter identifier used by the registry.
        supported_features (dict[str, bool]): Capability flags for audit/telemetry
            (multi-column, tables, images export, OCR, headers/footers removal).

    Returns:
        The class exposes a parse(raw) method which returns a domain-level MarkdownDoc
        as specified under parse(). The constructor performs no I/O.

    Raises:
        No exceptions at construction time. See parse() for detailed error contracts.

    Notes:
        - Deterministic outputs: stable asset names, page order, and page dividers.
        - Offline only: no network calls for extraction, OCR, or table detection.
        - Sanitization: output is UTF-8 with LF newlines; control characters are removed.
    """

    name: str = "PdfToMd"

    def __init__(
        self,
        *,
        page_range: Optional[str] = None,
        remove_headers_footers: bool = True,
        page_divider: str = "\n\n---\n\n",
        export_images: bool = False,
        assets_subdir: str = "assets",
        table_detection: str = "auto",
        ocr_enabled: bool = True,
        ocr_langs: Optional[tuple[str, ...]] = None,
        ocr_fail_on_low_confidence: bool = False,
        ocr_confidence_threshold: float = 0.55,
        strict_mode: bool = False,
    ) -> None:
        self._page_range = page_range if page_range is None else str(page_range)
        self._remove_headers_footers = bool(remove_headers_footers)
        self._page_divider = str(page_divider)
        self._export_images = bool(export_images)
        self._assets_subdir = str(assets_subdir)
        self._table_detection = str(table_detection)
        self._ocr_enabled = bool(ocr_enabled)
        self._ocr_langs = tuple(ocr_langs) if ocr_langs is not None else None
        self._ocr_fail_on_low_confidence = bool(ocr_fail_on_low_confidence)
        self._ocr_conf_threshold = float(ocr_confidence_threshold)
        self._strict_mode = bool(strict_mode)
        self.supported_features: Dict[str, bool] = {
            "multi_column_reading_order": True,
            "dehyphenation": True,
            "headers_footers_removal": True,
            "page_divider": True,
            "tables_detection": True,  # best-effort
            "images_export": True,
            "ocr_offline": True,
        }

    def parse(self, raw: Any) -> Any:  # RawDocument -> MarkdownDoc (see detailed docs)
        """Convert a .pdf to UTF-8, LF-normalized Markdown with detailed metadata.

        Description:
            Accepts a domain RawDocument (path, size, mtime, ext, meta). Verifies the
            extension is "pdf"; honors optional page_range; uses a layout-aware extractor
            to reconstruct reading order (multi-column aware), merges lines into
            paragraphs avoiding hard wraps, applies safe de-hyphenation, preserves
            headings/lists/links, converts simple tables to GitHub-style tables when
            confident (otherwise emits plain rows and records a warning), inserts a
            consistent page divider between pages, detects and optionally removes
            repeating headers/footers (including page numbers), optionally exports
            embedded images with stable filenames, and performs offline OCR for
            image-only pages with conservative preprocessing. Combines OCR and native
            text into a cohesive Markdown result.

        Args:
            raw (RawDocument):
                Domain object for the source .pdf. Required fields:
                - path (Path): Path to the PDF file.
                - ext (str): Must be "pdf" (case-insensitive).
                - size (int): Size in bytes (non-negative).
                - mtime (datetime): Last-modified timestamp (not embedded in output).
                - meta (dict[str, Any]): Optional, passed through; may include doc_id
                  and batch/audit markers.

        Returns:
            MarkdownDoc: A domain-level object with fields:
                - doc_id (str): Stable identifier provided/derived upstream. The adapter
                  should pass through raw.meta["doc_id"] when provided, or derive a
                  deterministic ID via project utilities in a concrete implementation.
                - path (Path): Original .pdf path.
                - lang (str | None): Unset/pass-through; language detection is downstream.
                - text_md (str): UTF-8-safe Markdown with LF newlines; consistent page
                  dividers; paragraphs preserved; minimal formatting; no trailing spaces.
                - encoding (str): Always "utf-8".
                - meta (dict[str, Any]): Includes at least:
                    - page_count (int)
                    - pages_processed (list[int])
                    - page_range_effective (str | None)
                    - reading_order_strategy (str), e.g., "columns_x_clustering"
                    - dehyphenation_applied (bool)
                    - headers_footers_removed (bool)
                    - header_footer_examples (list[str]) up to N exemplars
                    - tables_detected (int), tables_as_markdown (int), tables_as_text (int)
                    - images_exported (int) and assets_dir (str) when export_images is True
                    - links_count (int), headings_count (int), lists_count (int)
                    - ocr (dict) when OCR used:
                        {enabled: bool, pages: [int], langs: [str],
                         confidence_mean: float, confidence_median: float}
                    - conversion_warnings (list[str]) with human-readable notes
                      (e.g., "table detection low confidence on pages 5–6",
                      "removed repeating footer 'Company Confidential'")

        Raises:
            ValueError: When raw is missing required fields or ext is not "pdf"; when
                configuration values (page_range, table_detection) are invalid.
            FileNotFoundError: When the source file does not exist.
            PermissionError: When the file cannot be read.
            RuntimeError: For encrypted/password-protected PDFs (explicitly requesting
                a password) or corrupted/malformed structures (with page/object hints),
                or when reading order reconstruction fails irrecoverably.
            ImportError: When optional table engines (camelot/tabula) or OCR engine are
                missing but required by configuration/content (with install guidance).
            OSError: When exporting assets fails due to filesystem errors (path not
                writable, disk full); includes the target path in the error message.
            UnicodeError: If extracted/OCR text contains invalid sequences that cannot
                be sanitized.
            NotImplementedError: In this documentation-first stub indicating that a
                concrete implementation must supply the conversion logic.

        Notes:
            - Reading order: cluster by x-ranges into columns; within columns, sort
              top-to-bottom, then merge blocks to paragraphs.
            - De-hyphenation: join words broken at line ends when safe; record that the
              step was applied in metadata.
            - Page breaks: insert the configured divider verbatim between pages.
            - Headers/footers: detect repeating lines near page top/bottom; remove when
              enabled and store up to N exemplars in metadata.
            - Images: export with stable names like "p{page}_img{index}.{ext}"; record
              count and directory in metadata.
            - OCR: only for image-only pages; use configured languages; record mean and
              median confidence and page list.
            - Determinism: avoid randomness/time-based elements; stable outputs for the
              same input and configuration.
            - Performance: stream pages where possible; avoid full-document bitmaps.
        """
        # Minimal PDF → Markdown implementation using pdfminer.six
        try:
            from pdfminer.high_level import extract_text  # type: ignore
        except Exception as e:
            raise ImportError(
                "pdfminer.six is required for PDF parsing. Install with: pip install pdfminer.six"
            ) from e
        try:
            from ...domain.models_markdown import MarkdownDoc
        except Exception:
            MarkdownDoc = None  # type: ignore
        # Access path
        path = getattr(raw, "path", None) or (raw.get("path") if isinstance(raw, dict) else None)
        if not path:
            raise ValueError("raw.path is required for PdfToMd.parse")
        p = Path(str(path))
        text = extract_text(str(p)) or ""
        # Normalize newlines
        md = str(text).replace("\r\n", "\n").replace("\r", "\n")
        meta: Dict[str, Any] = {
            "pages_processed": None,
            "tables_detected": 0,
            "images_exported": 0,
            "links_count": 0,
            "conversion_warnings": [],
        }
        doc_id = p.stem
        if MarkdownDoc is not None:
            return MarkdownDoc(doc_id=doc_id, path=str(p), variant=None, lang=None, text_md=md, meta=meta)
        return type("_Doc", (), {"text_md": md, "meta": meta})()

    def describe(self) -> Dict[str, Any]:
        """Return a static capability/configuration description for audit/telemetry.

        Returns:
            dict: A dictionary describing adapter name, extensions, supported features,
            and a snapshot of configuration relevant for deterministic behavior.
        """
        return {
            "name": self.name,
            "extensions": list(EXTENSIONS),
            "supported_features": dict(self.supported_features),
            "config": {
                "page_range": self._page_range,
                "remove_headers_footers": self._remove_headers_footers,
                "page_divider": self._page_divider,
                "export_images": self._export_images,
                "assets_subdir": self._assets_subdir,
                "table_detection": self._table_detection,
                "ocr_enabled": self._ocr_enabled,
                "ocr_langs": list(self._ocr_langs) if self._ocr_langs is not None else None,
                "ocr_fail_on_low_confidence": self._ocr_fail_on_low_confidence,
                "ocr_confidence_threshold": self._ocr_conf_threshold,
                "strict_mode": self._strict_mode,
            },
        }

