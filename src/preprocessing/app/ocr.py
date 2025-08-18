from __future__ import annotations

from dataclasses import replace

from ..domain.models import ParsedDocument
from ..domain.ports import OcrPort


class OcrService:
    """Decide whether to run OCR and execute it via the provided port.

    Policy:
    - If len(doc.text) < min_text_len and source.ext is one of {pdf,png,jpg}, perform OCR.
    - Otherwise, return the document unchanged.
    """

    _OCRABLE_EXTS = {"pdf", "png", "jpg"}

    def __init__(self, ocr: OcrPort, *, min_text_len: int = 50) -> None:
        if min_text_len < 0:
            raise ValueError("min_text_len must be >= 0")
        self._ocr = ocr
        self._min_text_len = min_text_len

    def maybe_ocr(self, doc: ParsedDocument, languages: tuple[str, ...]) -> ParsedDocument:
        if len(doc.text or "") >= self._min_text_len:
            return doc
        ext = doc.source.ext.lower()
        if ext not in self._OCRABLE_EXTS:
            return doc
        text = self._ocr.run(doc.source.path, languages=languages)
        # Merge metadata with OCR flag without relying on dict unpacking
        meta = dict(doc.metadata)
        meta["ocr"] = {"applied": True, "languages": list(languages), "ext": ext}
        return replace(doc, text=text, metadata=meta)
