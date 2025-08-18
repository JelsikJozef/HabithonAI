from __future__ import annotations

from .base import BaseParser
from ...domain.models import ParsedDocument, RawDocument


class PdfParser(BaseParser):
    """PDF -> text via pdfminer.six (if available).

    - Extracts text from all pages.
    - Counts pages and sets metadata["pdf_pages"].
    - If pdfminer is not available or parsing fails, returns empty text with pages=0.
    - OCR for too-short text is handled later by OcrService.
    """

    def parse(self, raw: RawDocument) -> ParsedDocument:
        text = ""
        pages = 0
        try:
            from pdfminer.high_level import extract_text  # type: ignore
            from pdfminer.pdfpage import PDFPage  # type: ignore

            # Count pages
            with open(raw.path, "rb") as fh:
                pages = sum(1 for _ in PDFPage.get_pages(fh))
            # Extract text
            text = extract_text(str(raw.path)) or ""
        except Exception:
            text = ""
            pages = 0
        metadata = {"ext": "pdf", "bytes": raw.size, "pdf_pages": int(pages)}
        return ParsedDocument(text=text, source=raw, metadata=metadata)

