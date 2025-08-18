import os
import sys
import json
import types
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

# Ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from preprocessing.domain.models import RawDocument, ParsedDocument  # type: ignore
from preprocessing.app.llm_enrich import LlmEnrichmentService  # type: ignore
from preprocessing.adapters.enrichment.llm_openai_enricher import OpenAiLlmEnricher  # type: ignore
from preprocessing.app.llm_anonymize import LlmAnonymisationService  # type: ignore
from preprocessing.adapters.serializer.jsonl import JsonlSerializer  # type: ignore
from preprocessing.app.serialize import SerializeService  # type: ignore


class _FakeOpenAiClient:
    """Fake OpenAI client responding with Slovak JSON and preserving tokens."""

    def __init__(self, summary=None, tags=None):
        self.summary = summary or "Stručné zhrnutie v slovenčine."
        self.tags = tags or ["kľúč", "značka"]

    def generate_summary_and_tags(self, *, text, model=None, max_tokens=512):
        # If the text contains a token, echo it in outputs to test de-anonymization
        token = None
        for part in text.split():
            if part.startswith("{{PII:") and part.endswith("}}"):  # naive token check
                token = part
                break
        s = self.summary
        t = list(self.tags)
        if token:
            s = "Kontakt: " + token
            t = ["kontakt", token]
        return s, t


class TestLlmEnrichmentUnit(unittest.TestCase):
    def test_pseudonymize_and_deanonymize_roundtrip(self):
        from anonymization.adapters.container import build_default as build_anon  # type: ignore
        detectors, vault = build_anon()
        anonymizer = LlmAnonymisationService(detectors, vault)
        fake_client = _FakeOpenAiClient()
        llm_port = OpenAiLlmEnricher(fake_client)  # type: ignore[arg-type]
        svc = LlmEnrichmentService(llm_port, anonymizer=anonymizer)
        rd = RawDocument(path=Path('src.txt'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        doc = ParsedDocument(text="Kontaktujte nás na profidecon@profidecon.com", source=rd, metadata={})
        out = svc.summarize(doc)
        out = svc.keywords(out, top_k=5)
        # Summary and tags present and de-anonymized (contains email, not token)
        self.assertIn("summary", out.metadata)
        self.assertIn("tags", out.metadata)
        self.assertIn("profidecon@profidecon.com", out.metadata["summary"])  # de-anonymized
        self.assertTrue(any("profidecon@profidecon.com" in t for t in out.metadata["tags"]))

    def test_slovak_outputs(self):
        fake_client = _FakeOpenAiClient(summary="Krátke zhrnutie.", tags=["značky", "kľúčové slová"])  # type: ignore[arg-type]
        # No anonymizer for this test
        llm_port = OpenAiLlmEnricher(fake_client)  # type: ignore[arg-type]
        svc = LlmEnrichmentService(llm_port)
        rd = RawDocument(path=Path('d.txt'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        doc = ParsedDocument(text="Nejaký slovenský text.", source=rd, metadata={})
        out = svc.summarize(doc)
        out = svc.keywords(out, top_k=2)
        self.assertEqual(out.metadata.get("summary"), "Krátke zhrnutie.")
        self.assertEqual(out.metadata.get("tags"), ["značky", "kľúčové slová"])

    def test_jsonl_keeps_summary_and_tags_in_metadata(self):
        fake_client = _FakeOpenAiClient(summary="Sumár", tags=["tag1"])  # type: ignore[arg-type]
        llm_port = OpenAiLlmEnricher(fake_client)  # type: ignore[arg-type]
        svc = LlmEnrichmentService(llm_port)
        rd = RawDocument(path=Path('e.txt'), size=1, mtime=datetime(2024, 1, 1), ext='txt')
        doc = ParsedDocument(text="Ahoj", source=rd, metadata={})
        out = svc.summarize(doc)
        out = svc.keywords(out, top_k=3)
        # Serialize to JSONL sink
        tmp = tempfile.NamedTemporaryFile(delete=False)
        tmp_path = Path(tmp.name)
        tmp.close()
        try:
            sink = JsonlSerializer(tmp_path)
            ser = SerializeService(sink)
            ser.append(out)
            ser.close()
            # Read line back
            data = tmp_path.read_text(encoding='utf-8').strip().splitlines()
            rec = json.loads(data[0])
            self.assertIn("metadata", rec)
            self.assertIn("summary", rec["metadata"])  # nested in metadata
            self.assertIn("tags", rec["metadata"])      # nested in metadata
            self.assertNotIn("summary", rec)  # not top-level
            self.assertNotIn("tags", rec)     # not top-level
        finally:
            try:
                tmp_path.unlink()
            except Exception:
                pass


class TestCliIntegration(unittest.TestCase):
    def setUp(self):
        # Stub the OpenAI client module used by Container so --llm works without network
        mod = types.ModuleType('preprocessing.adapters.enrichment.openai_client')
        setattr(mod, 'OpenAiClient', _FakeOpenAiClient)  # Container imports this name
        sys.modules['preprocessing.adapters.enrichment.openai_client'] = mod

    def tearDown(self):
        # Clean stub
        sys.modules.pop('preprocessing.adapters.enrichment.openai_client', None)

    def test_cli_llm_flow_writes_summary_and_tags(self):
        from preprocessing.presentation.cli import main as cli_main  # import after stubbing
        # Create temp input dir and file
        with tempfile.TemporaryDirectory() as tmpd:
            root = Path(tmpd)
            _ = (root / 'note.txt').write_text('Kontakt: profidecon@profidecon.com', encoding='utf-8')
            out = root / 'out.jsonl'
            # Run CLI: preprocessing preprocess ROOT --out out.jsonl --llm
            argv = ['preprocess', str(root), '--out', str(out), '--llm']
            rc = cli_main(argv)
            self.assertEqual(rc, 0)
            # Verify JSONL contains metadata.summary and metadata.tags
            lines = out.read_text(encoding='utf-8').strip().splitlines()
            self.assertGreaterEqual(len(lines), 1)
            rec = json.loads(lines[0])
            self.assertIn('metadata', rec)
            self.assertIn('summary', rec['metadata'])
            self.assertIn('tags', rec['metadata'])
            # Summary should contain the email (de-anonymized)
            self.assertIn('profidecon@profidecon.com', rec['metadata']['summary'])


if __name__ == '__main__':
    unittest.main()
