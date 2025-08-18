import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import QualityService  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


class FakeQuality:
    def __init__(self, metrics: dict[str, object] | None = None):
        self._metrics = metrics or {"readability": 0.8}

    def evaluate(self, doc: ParsedDocument) -> dict[str, object]:
        # Return base metrics; could depend on doc if needed
        return dict(self._metrics)


class TestQualityService(unittest.TestCase):
    def _pd(self, text: str) -> ParsedDocument:
        rd = RawDocument(path=Path('doc.txt'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        return ParsedDocument(text=text, source=rd, metadata={})

    def test_ok_within_thresholds(self):
        svc = QualityService(FakeQuality(), min_chars=5, max_chars=50)
        out = svc.evaluate(self._pd("hello world"))
        self.assertTrue(out["ok"])
        self.assertEqual(out["reasons"], [])
        self.assertGreaterEqual(out["length"], 5)
        self.assertIn("readability", out)

    def test_too_short_and_too_long_reasons(self):
        svc_short = QualityService(FakeQuality(), min_chars=20)
        res_short = svc_short.evaluate(self._pd("short"))
        self.assertFalse(res_short["ok"])
        self.assertIn("too_short", res_short["reasons"])

        svc_long = QualityService(FakeQuality(), min_chars=0, max_chars=3)
        res_long = svc_long.evaluate(self._pd("abcd"))
        self.assertFalse(res_long["ok"])
        self.assertIn("too_long", res_long["reasons"])


if __name__ == '__main__':
    unittest.main()

