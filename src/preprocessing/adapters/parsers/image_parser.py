from __future__ import annotations

from .base import BaseParser
from ...domain.models import ParsedDocument, RawDocument


class ImageParser(BaseParser):
    """Image -> text (basic; no OCR or minimal inline OCR if available).

    Behavior:
    - If pytesseract and PIL are available, perform inline OCR and return the text.
    - Otherwise, return empty text and set metadata["needs_ocr"] = True.
    - Always include minimal metadata: ext and bytes.
    """

    def parse(self, raw: RawDocument) -> ParsedDocument:
        metadata = {"ext": raw.ext, "bytes": raw.size}
        # Try inline OCR
        text = ""
        try:
            import pytesseract  # type: ignore
            from PIL import Image  # type: ignore

            img = Image.open(raw.path)
            text = pytesseract.image_to_string(img) or ""
            metadata["ocr_inline"] = True
        except Exception:
            metadata["needs_ocr"] = True
            text = ""
        # Normalize metadata ext to lower-case without dot
        metadata["ext"] = str(metadata["ext"]).lstrip(".").lower()
        return ParsedDocument(text=text, source=raw, metadata=metadata)
