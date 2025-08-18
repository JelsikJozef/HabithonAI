import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
from typing import Set

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import ParseService  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore
from preprocessing.domain.errors import ParserNotFoundError  # type: ignore


class FakeParser:
    def parse(self, raw: RawDocument) -> ParsedDocument:
        return ParsedDocument(text=f"parsed:{raw.path.name}", source=raw, language='en')


class ErroringParser:
    def parse(self, raw: RawDocument) -> ParsedDocument:
        raise RuntimeError("parse failed")


class FakeRegistry:
    def __init__(self, mapping: dict[str, object]):
        self._mapping = mapping

    def get(self, ext: str):
        return self._mapping[ext]

    def supported(self) -> Set[str]:
        return set(self._mapping.keys())


class TestParseService(unittest.TestCase):
    def _raw(self, name: str, ext: str) -> RawDocument:
        return RawDocument(path=Path(name), size=1, mtime=datetime(2024, 1, 1), ext=ext)

    def test_parse_one_happy_path(self):
        reg = FakeRegistry({"txt": FakeParser()})
        svc = ParseService(reg)
        raw = self._raw("a.txt", "txt")
        out = svc.parse_one(raw)
        self.assertIsInstance(out, ParsedDocument)
        self.assertEqual(out.text, "parsed:a.txt")
        self.assertEqual(out.source, raw)

    def test_parse_one_unsupported_ext(self):
        reg = FakeRegistry({"pdf": FakeParser()})
        svc = ParseService(reg)
        with self.assertRaises(ParserNotFoundError):
            svc.parse_one(self._raw("a.txt", "txt"))

    def test_parse_many_generator_and_error_propagation(self):
        reg = FakeRegistry({"txt": FakeParser(), "bin": ErroringParser()})
        svc = ParseService(reg)
        raws = [self._raw("a.txt", "txt"), self._raw("b.bin", "bin")]
        it = svc.parse_many(raws)
        first = next(iter(it))
        self.assertEqual(first.text, "parsed:a.txt")
        # Recreate iterator to consume in order for the exception
        it2 = svc.parse_many(raws)
        next(iter(it2))  # consume first
        with self.assertRaises(RuntimeError):
            next(iter(it2))


if __name__ == '__main__':
    unittest.main()
