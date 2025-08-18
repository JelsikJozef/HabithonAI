from __future__ import annotations

from pathlib import Path
from typing import Literal

from ...domain.errors import OcrError
from ...domain.ports import OcrPort


class PdfOcr(OcrPort):
    """OCR adapter for PDF and image inputs using Tesseract.

    - For PDFs: renders pages to images via pdf2image and OCRs each page.
    - For images: OCRs the image directly.
    - Joins page texts with newlines and returns a single string.
    - Raises OcrError when required binaries/dependencies are missing or conversion fails.
    """

    _IMG_EXTS = {"png", "jpg", "jpeg", "tif", "tiff", "bmp"}

    def __init__(self, engine: Literal["tesseract"], *, dpi: int = 300) -> None:
        if engine != "tesseract":
            raise ValueError("Only 'tesseract' engine is supported")
        if dpi <= 0:
            raise ValueError("dpi must be > 0")
        self._engine = engine
        self._dpi = dpi

    def run(self, input_path: Path, *, languages: tuple[str, ...]) -> str:
        ext = input_path.suffix.lstrip(".").lower()
        if ext == "pdf":
            return self._ocr_pdf(input_path, languages)
        if ext in self._IMG_EXTS:
            return self._ocr_image(input_path, languages)
        # Fallback: try as image first, then as PDF
        try:
            return self._ocr_image(input_path, languages)
        except OcrError:
            return self._ocr_pdf(input_path, languages)

    def _ocr_pdf(self, input_path: Path, languages: tuple[str, ...]) -> str:
        try:
            from pdf2image import convert_from_path  # type: ignore
        except Exception:
            raise OcrError("pdf2image not available for PDF OCR")
        # Render pages
        try:
            images = convert_from_path(str(input_path), dpi=self._dpi)
        except Exception as e:
            raise OcrError("Failed to render PDF to images: %s" % e)
        if not images:
            return ""
        texts = []
        for img in images:
            texts.append(self._ocr_pil_image(img, languages))
        return "\n".join(t.strip() for t in texts if t is not None)

    def _ocr_image(self, input_path: Path, languages: tuple[str, ...]) -> str:
        try:
            from PIL import Image  # type: ignore
        except Exception:
            raise OcrError("PIL (Pillow) not available for image OCR")
        try:
            img = Image.open(input_path)
        except Exception as e:
            raise OcrError("Failed to open image: %s" % e)
        return self._ocr_pil_image(img, languages)

    @staticmethod
    def _lang_arg(languages: tuple[str, ...]) -> dict:
        if not languages:
            return {}
        return {"lang": "+".join(languages)}

    def _ocr_pil_image(self, image, languages: tuple[str, ...]) -> str:
        try:
            import pytesseract  # type: ignore
            from pytesseract import TesseractNotFoundError  # type: ignore
        except Exception:
            raise OcrError("pytesseract not available for OCR")
        try:
            kwargs = self._lang_arg(languages)
            text = pytesseract.image_to_string(image, **kwargs)
            return text or ""
        except TesseractNotFoundError:
            raise OcrError("Tesseract binary not found")
        except Exception as e:
            raise OcrError("Tesseract OCR failed: %s" % e)
