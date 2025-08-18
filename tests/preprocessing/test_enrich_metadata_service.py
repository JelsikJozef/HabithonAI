import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import MetadataEnrichmentService  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


class FakeEnricher:
    def enrich(self, doc: ParsedDocument) -> ParsedDocument:
        # Simulate enrichment: language, hash, tokens, counts
        text = doc.text
        metadata = dict(doc.metadata)
        metadata.update({
            "counts": {"lines": len(text.splitlines()), "chars": len(text)},
        })
        return ParsedDocument(
            text=text,
            source=doc.source,
            charset=doc.charset,
            language="en" if text else None,
            hash=str(len(text)),
            tokens=len(text.split()),
            metadata=metadata,
        )


class TestMetadataEnrichmentService(unittest.TestCase):
    def _pd(self, text: str) -> ParsedDocument:
        rd = RawDocument(path=Path('doc.txt'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        return ParsedDocument(text=text, source=rd, metadata={})

    def test_delegates_and_returns_enriched_doc(self):
        enricher = FakeEnricher()
        svc = MetadataEnrichmentService(enricher)
        doc = self._pd("hello world\nthis is a test")
        out = svc.run(doc)
        self.assertIsNot(out, doc)
        self.assertEqual(out.language, "en")
        self.assertEqual(out.hash, str(len(doc.text)))
        self.assertEqual(out.tokens, len(doc.text.split()))
        self.assertEqual(out.metadata.get("counts", {}).get("lines"), 2)


if __name__ == '__main__':
    unittest.main()

