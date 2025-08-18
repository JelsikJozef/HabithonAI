import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import SerializeService  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


class FakeSink:
    def __init__(self):
        self.records = []
        self.closed = False

    def append(self, record: dict):
        self.records.append(record)

    def close(self):
        self.closed = True


class TestSerializeService(unittest.TestCase):
    def _pd(self, text: str) -> ParsedDocument:
        rd = RawDocument(path=Path('a/b/c.pdf'), size=10, mtime=datetime(2024, 1, 1, 1, 2, 3), ext='pdf', meta={'t': 'X'})
        return ParsedDocument(text=text, source=rd, charset='utf-8', language='sk', hash='h', tokens=1, metadata={'pages': 3})

    def test_append_and_close(self):
        sink = FakeSink()
        svc = SerializeService(sink)
        doc = self._pd("hello")
        svc.append(doc)
        self.assertEqual(len(sink.records), 1)
        rec = sink.records[0]
        self.assertEqual(rec["text"], "hello")
        self.assertEqual(rec["source"]["ext"], "pdf")
        self.assertEqual(rec["metadata"]["pages"], 3)
        svc.close()
        self.assertTrue(sink.closed)


if __name__ == '__main__':
    unittest.main()

