import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import NormalizeService  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


class TestNormalizeService(unittest.TestCase):
    def _pd(self, text: str) -> ParsedDocument:
        rd = RawDocument(path=Path('x.txt'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        return ParsedDocument(text=text, source=rd, metadata={})

    def test_collapse_ws_preserves_newlines(self):
        svc = NormalizeService()
        src = "Line 1\t\t  with   spaces\nLine   2\n\nLine 3   "
        out = svc.normalize(self._pd(src), unicode_nfkc=False, strip_headers=False)
        self.assertEqual(out.text, "Line 1 with spaces\nLine 2\n\nLine 3")
        self.assertTrue(out.metadata.get('normalized', {}).get('collapse_ws'))

    def test_nfkc_normalization(self):
        svc = NormalizeService()
        src = "１／２width Ｔｅｘｔ"
        out = svc.normalize(self._pd(src), collapse_ws=False, strip_headers=False)
        self.assertEqual(out.text, "1/2width Text")
        self.assertTrue(out.metadata.get('normalized', {}).get('unicode_nfkc'))

    def test_strip_headers(self):
        svc = NormalizeService()
        src = "Page 1\n----\nTitle\nContent line\nPage 2/10\n____\nFooter\n"
        out = svc.normalize(self._pd(src), collapse_ws=False, unicode_nfkc=False)
        self.assertEqual(out.text, "Title\nContent line\nFooter")
        self.assertTrue(out.metadata.get('normalized', {}).get('strip_headers'))

    def test_flags_reflect_configuration(self):
        svc = NormalizeService()
        src = " A  B "
        out = svc.normalize(self._pd(src), collapse_ws=False, unicode_nfkc=False, strip_headers=False)
        flags = out.metadata.get('normalized', {})
        self.assertEqual(flags, {"collapse_ws": False, "unicode_nfkc": False, "strip_headers": False})


if __name__ == '__main__':
    unittest.main()

