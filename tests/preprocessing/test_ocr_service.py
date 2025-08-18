import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import OcrService  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


class FakeOcr:
    def __init__(self):
        self.calls = []

    def run(self, input_path: Path, *, languages: tuple[str, ...]) -> str:
        self.calls.append((input_path, languages))
        return f"OCR({input_path.name},{','.join(languages)})"


class TestOcrService(unittest.TestCase):
    def _pd(self, ext: str, text: str = "") -> ParsedDocument:
        rd = RawDocument(path=Path(f"file.{ext}"), size=1, mtime=datetime(2024, 1, 1), ext=ext)
        return ParsedDocument(text=text, source=rd, metadata={})

    def test_skips_when_text_long_enough(self):
        ocr = FakeOcr()
        svc = OcrService(ocr, min_text_len=5)
        doc = self._pd("pdf", text="hello")
        out = svc.maybe_ocr(doc, ("en",))
        self.assertIs(out, doc)
        self.assertEqual(len(ocr.calls), 0)

    def test_skips_for_unsupported_ext(self):
        ocr = FakeOcr()
        svc = OcrService(ocr, min_text_len=50)
        doc = self._pd("txt", text="")
        out = svc.maybe_ocr(doc, ("en",))
        self.assertIs(out, doc)
        self.assertEqual(len(ocr.calls), 0)

    def test_runs_ocr_for_short_text_and_supported_ext(self):
        ocr = FakeOcr()
        svc = OcrService(ocr, min_text_len=10)
        doc = self._pd("png", text="short")
        out = svc.maybe_ocr(doc, ("en", "sk"))
        self.assertIsNot(out, doc)
        self.assertTrue(out.metadata.get("ocr", {}).get("applied"))
        self.assertEqual(out.metadata.get("ocr", {}).get("ext"), "png")
        self.assertEqual(out.text, "OCR(file.png,en,sk)")
        self.assertEqual(len(ocr.calls), 1)


if __name__ == '__main__':
    unittest.main()

