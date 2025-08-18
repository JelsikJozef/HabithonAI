import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import LlmEnrichmentService  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


class FakeLlm:
    def summarize(self, text: str) -> str:
        return text[:10] + ("..." if len(text) > 10 else "")

    def keywords(self, text: str, top_k: int = 10) -> list[str]:
        toks = [t.strip(",. ") for t in text.lower().split() if t.strip(",. ")]
        uniq = []
        for t in toks:
            if t not in uniq:
                uniq.append(t)
        return uniq[:top_k]


class TestLlmEnrichmentService(unittest.TestCase):
    def _pd(self, text: str) -> ParsedDocument:
        rd = RawDocument(path=Path('src.txt'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        return ParsedDocument(text=text, source=rd, metadata={})

    def test_summarize_adds_metadata(self):
        svc = LlmEnrichmentService(FakeLlm())
        doc = self._pd("This is a longish text to summarize")
        out = svc.summarize(doc)
        self.assertIsNot(out, doc)
        self.assertIn("summary", out.metadata)
        self.assertTrue(out.metadata["summary"].startswith("This is a "))

    def test_keywords_adds_metadata_with_top_k(self):
        svc = LlmEnrichmentService(FakeLlm())
        doc = self._pd("alpha beta alpha gamma delta epsilon zeta eta theta iota kappa")
        out = svc.keywords(doc, top_k=5)
        self.assertEqual(len(out.metadata.get("keywords", [])), 5)
        self.assertEqual(out.metadata["keywords"], ["alpha", "beta", "gamma", "delta", "epsilon"])


if __name__ == '__main__':
    unittest.main()

