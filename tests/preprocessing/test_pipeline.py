import os
import sys
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '../../', 'src')))

from preprocessing.app import PreprocessPipeline  # type: ignore
from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore


# Fakes matching app service interfaces expected by the pipeline
class FakeParse:
    def __init__(self, text: str):
        self.text = text

    def parse_one(self, raw: RawDocument) -> ParsedDocument:
        return ParsedDocument(text=self.text, source=raw)


class FakeNormalize:
    def normalize(self, doc: ParsedDocument) -> ParsedDocument:
        return doc


class FakeMeta:
    def __init__(self, hash_value: str = "h1"):
        self.hash_value = hash_value

    def run(self, doc: ParsedDocument) -> ParsedDocument:
        # Simulate enrichment by adding hash and language/tokens
        return ParsedDocument(
            text=doc.text,
            source=doc.source,
            language='en',
            hash=self.hash_value,
            tokens=len(doc.text.split()),
            metadata=dict(doc.metadata),
        )


class FakeDedup:
    def __init__(self, dup_hashes: set[str] | None = None):
        self.dup_hashes = dup_hashes or set()

    def check_or_remember(self, content_hash: str) -> bool:
        if content_hash in self.dup_hashes:
            return True
        self.dup_hashes.add(content_hash)
        return False


class FakeQuality:
    def __init__(self, ok: bool = True, reasons: list[str] | None = None):
        self._ok = ok
        self._reasons = reasons or []

    def evaluate(self, doc: ParsedDocument) -> dict:
        return {"ok": self._ok, "reasons": list(self._reasons)}


class CapturingSerialize:
    def __init__(self):
        self.docs: list[ParsedDocument] = []

    def append(self, doc: ParsedDocument) -> None:
        self.docs.append(doc)

    def close(self) -> None:
        pass


class FakeOcr:
    def __init__(self, new_text: str):
        self.new_text = new_text

    def maybe_ocr(self, doc: ParsedDocument, languages: tuple[str, ...]) -> ParsedDocument:
        return ParsedDocument(text=self.new_text, source=doc.source, metadata={**doc.metadata, "ocr": True})


class FakeLlm:
    def summarize(self, doc: ParsedDocument) -> ParsedDocument:
        return ParsedDocument(text=doc.text, source=doc.source, metadata={**doc.metadata, "summary": doc.text[:8]})

    def keywords(self, doc: ParsedDocument, top_k: int = 10) -> ParsedDocument:
        return ParsedDocument(text=doc.text, source=doc.source, metadata={**doc.metadata, "keywords": ["a","b"]})


class TestPreprocessPipeline(unittest.TestCase):
    def _raw(self, name: str, ext: str) -> RawDocument:
        return RawDocument(path=Path(name), size=1, mtime=datetime(2024, 1, 1), ext=ext)

    def test_happy_path(self):
        parse = FakeParse("some sufficiently long text")
        norm = FakeNormalize()
        meta = FakeMeta("hash1")
        dedup = FakeDedup()
        quality = FakeQuality(ok=True)
        serialize = CapturingSerialize()
        pipe = PreprocessPipeline(parse, norm, meta, dedup, quality, serialize)
        res = pipe.process_one(self._raw("a.txt", "txt"))
        self.assertTrue(res["ok"]) \
            and self.assertFalse(res["skipped"]) \
            and self.assertEqual(res["reasons"], [])
        self.assertEqual(len(serialize.docs), 1)

    def test_duplicate_skipped(self):
        parse = FakeParse("text")
        norm = FakeNormalize()
        meta = FakeMeta("dup")
        dedup = FakeDedup({"dup"})
        quality = FakeQuality(ok=True)
        serialize = CapturingSerialize()
        pipe = PreprocessPipeline(parse, norm, meta, dedup, quality, serialize)
        res = pipe.process_one(self._raw("b.txt", "txt"))
        self.assertFalse(res["ok"])
        self.assertTrue(res["skipped"])
        self.assertIn("duplicate", res["reasons"])
        self.assertEqual(len(serialize.docs), 0)

    def test_quality_fail(self):
        parse = FakeParse("short")
        norm = FakeNormalize()
        meta = FakeMeta("h2")
        dedup = FakeDedup()
        quality = FakeQuality(ok=False, reasons=["too_short"])
        serialize = CapturingSerialize()
        pipe = PreprocessPipeline(parse, norm, meta, dedup, quality, serialize)
        res = pipe.process_one(self._raw("c.txt", "txt"))
        self.assertFalse(res["ok"])
        self.assertTrue(res["skipped"])
        self.assertIn("too_short", res["reasons"])
        self.assertEqual(len(serialize.docs), 0)

    def test_ocr_and_llm(self):
        parse = FakeParse("")
        norm = FakeNormalize()
        meta = FakeMeta("h3")
        dedup = FakeDedup()
        quality = FakeQuality(ok=True)
        serialize = CapturingSerialize()
        ocr = FakeOcr("ocr text that is long enough")
        llm = FakeLlm()
        pipe = PreprocessPipeline(parse, norm, meta, dedup, quality, serialize, ocr=ocr, llm=llm)
        res = pipe.process_one(self._raw("d.pdf", "pdf"), do_llm=True)
        self.assertTrue(res["ok"])
        self.assertEqual(len(serialize.docs), 1)
        self.assertIn("summary", serialize.docs[0].metadata)
        self.assertIn("keywords", serialize.docs[0].metadata)

    def test_process_many_stats(self):
        parse = FakeParse("ok text")
        norm = FakeNormalize()
        meta = FakeMeta()
        dedup = FakeDedup({"dup"})
        # Quality will be OK except simulate a fail via different pipeline instance
        quality_ok = FakeQuality(ok=True)
        serialize = CapturingSerialize()
        pipe = PreprocessPipeline(parse, norm, meta, dedup, quality_ok, serialize)
        raws = [
            self._raw("1.txt", "txt"),  # ok
            self._raw("2.txt", "txt"),  # duplicate after meta sets same hash? We'll tweak meta per run
            self._raw("3.txt", "txt"),  # ok
        ]
        # For the second item, swap meta to yield a duplicate hash
        def process_indexed(i, raw):
            if i == 1:
                pipe._meta = FakeMeta("dup")  # inject duplicate hash
            else:
                pipe._meta = FakeMeta("h" + str(i))
            return pipe.process_one(raw)

        stats = {"total": 0, "processed": 0, "skipped": 0, "duplicate": 0, "failed": 0}
        for i, r in enumerate(raws):
            stats["total"] += 1
            res = process_indexed(i, r)
            if res.get("skipped"):
                stats["skipped"] += 1
                if "duplicate" in res.get("reasons", []):
                    stats["duplicate"] += 1
            if res.get("ok"):
                stats["processed"] += 1
        # Basic assertions
        self.assertEqual(stats["total"], 3)
        self.assertEqual(stats["processed"], 2)
        self.assertEqual(stats["duplicate"], 1)


if __name__ == '__main__':
    unittest.main()

