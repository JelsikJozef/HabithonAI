import os
import sys
import unittest
from datetime import datetime
from pathlib import Path
import asyncio
from typing import AsyncIterator, Iterable

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import IngestionService  # type: ignore
from preprocessing.domain.models import RawDocument  # type: ignore
from preprocessing.domain.ports import IngestionPort  # type: ignore


class FakeIngestor(IngestionPort):
    def __init__(self, batch_docs: list[RawDocument] | None = None, watch_docs: list[RawDocument] | None = None):
        self._batch_docs = batch_docs or []
        self._watch_docs = watch_docs or []

    def ingest_batch(self, root: Path, globs: tuple[str, ...]) -> Iterable[RawDocument]:
        return iter(self._batch_docs)

    async def ingest_watch(self, root: Path, globs: tuple[str, ...]) -> AsyncIterator[RawDocument]:
        for d in self._watch_docs:
            yield d


class TestIngestionService(unittest.TestCase):
    def _mk_raw(self, name: str = 'a.txt') -> RawDocument:
        return RawDocument(path=Path(name), size=1, mtime=datetime(2024, 1, 1), ext='txt')

    def test_batch_enriches_with_same_batch_id(self):
        docs = [self._mk_raw('a.txt'), self._mk_raw('b.txt')]
        svc = IngestionService(FakeIngestor(batch_docs=docs))
        out = list(svc.ingest_batch(Path('/root'), ('**/*.txt',)))
        self.assertEqual(len(out), 2)
        bid1 = out[0].meta.get('batch_id')
        bid2 = out[1].meta.get('batch_id')
        self.assertIsInstance(bid1, str)
        self.assertEqual(bid1, bid2)
        # Ensure original docs unchanged (immutability)
        self.assertEqual(docs[0].meta, {})

    def test_validation_errors(self):
        svc = IngestionService(FakeIngestor())
        with self.assertRaises(TypeError):
            svc.ingest_batch('not-a-path', ('*.txt',))  # type: ignore[arg-type]
        with self.assertRaises(TypeError):
            svc.ingest_batch(Path('.'), ['*.txt'])  # type: ignore[arg-type]
        with self.assertRaises(ValueError):
            svc.ingest_batch(Path('.'), ())
        with self.assertRaises(ValueError):
            svc.ingest_batch(Path('.'), ('',))

    def test_watch_delegates_and_yields_as_is(self):
        docs = [self._mk_raw('w1.txt'), self._mk_raw('w2.txt')]
        svc = IngestionService(FakeIngestor(watch_docs=docs))

        async def collect():
            out = []
            async for d in svc.ingest_watch(Path('/root'), ('**/*.txt',)):
                out.append(d)
            return out

        out_docs = asyncio.get_event_loop().run_until_complete(collect())
        self.assertEqual(out_docs, docs)

    def test_type_enforcement_from_port(self):
        class BadIngestor(FakeIngestor):
            def ingest_batch(self, root: Path, globs: tuple[str, ...]):  # type: ignore[override]
                return iter(["not-a-raw-doc"])  # type: ignore[list-item]

        svc = IngestionService(BadIngestor())
        it = svc.ingest_batch(Path('.'), ('*.txt',))
        with self.assertRaises(TypeError):
            next(iter(it))


if __name__ == '__main__':
    unittest.main()

