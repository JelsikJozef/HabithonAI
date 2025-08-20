import os
import sys
import re
import unittest
import tempfile

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

try:
    import presidio_analyzer  # type: ignore
    PRESIDIO_AVAILABLE = True
except Exception:
    PRESIDIO_AVAILABLE = False

from anonymization.adapters.container import build_default  # type: ignore
from anonymization.app.detect import detect_all  # type: ignore
from anonymization.app.pseudonymize import pseudonymize  # type: ignore
from anonymization.app.denomize import deanonymize  # type: ignore
from anonymization.adapters.token_vault.file_store import FileTokenVault  # type: ignore


NAME_TOKEN_RE = re.compile(r"\{\{PII:NAME:\d+:[0-9a-fA-F]{8}\}\}")
COMPANY_TOKEN_RE = re.compile(r"\{\{PII:COMPANY:\d+:[0-9a-fA-F]{8}\}\}")


def _assert_tokens(text: str):
    assert NAME_TOKEN_RE.search(text), f"Missing NAME token in: {text!r}"
    assert COMPANY_TOKEN_RE.search(text), f"Missing COMPANY token in: {text!r}"


@unittest.skipUnless(PRESIDIO_AVAILABLE, "presidio_analyzer not available; skipping name/company anonymization tests")
class TestNameCompanyAnonymization(unittest.TestCase):
    def setUp(self) -> None:
        self.detectors, _vault = build_default()

    def test_slovak_detection_and_roundtrip(self):
        text = "Pán Ján Novák pracuje v spoločnosti Profidecon s.r.o. v Bratislave."
        ents = detect_all(text, self.detectors, language='sk').entities
        vals = [(e.type, e.value) for e in ents]
        self.assertTrue(any(t == 'PERSON' and 'Ján Novák' in v for t, v in vals))
        self.assertTrue(any(t == 'ORGANIZATION' and 'Profidecon s.r.o.' in v for t, v in vals))
        with tempfile.TemporaryDirectory() as td:
            vault = FileTokenVault(base_dir=td)
            pseudo = pseudonymize(text, self.detectors, vault, context_id='ctx:sk', language='sk')
            self.assertNotEqual(pseudo.pseudonymized_text, text)
            _assert_tokens(pseudo.pseudonymized_text)
            der = deanonymize(pseudo.pseudonymized_text, vault, 'ctx:sk')
            self.assertEqual(der.restored_text, text)

    def test_german_detection_and_roundtrip(self):
        text = "Herr Peter Müller arbeitet bei Beispiel GmbH & Co. KG in München."
        ents = detect_all(text, self.detectors, language='de').entities
        vals = [(e.type, e.value) for e in ents]
        self.assertTrue(any(t == 'PERSON' and 'Peter Müller' in v for t, v in vals))
        self.assertTrue(any(t == 'ORGANIZATION' and 'Beispiel GmbH' in v for t, v in vals))
        with tempfile.TemporaryDirectory() as td:
            vault = FileTokenVault(base_dir=td)
            pseudo = pseudonymize(text, self.detectors, vault, context_id='ctx:de', language='de')
            self.assertNotEqual(pseudo.pseudonymized_text, text)
            _assert_tokens(pseudo.pseudonymized_text)
            der = deanonymize(pseudo.pseudonymized_text, vault, 'ctx:de')
            self.assertEqual(der.restored_text, text)

    def test_english_detection_and_roundtrip(self):
        text = "Alice Johnson works at OpenAI, Inc. in San Francisco."
        ents = detect_all(text, self.detectors, language='en').entities
        vals = [(e.type, e.value) for e in ents]
        # Allow partial if model has limited recall; but expect PERSON and ORGANIZATION ideally
        self.assertTrue(any(t == 'PERSON' for t, _ in vals))
        self.assertTrue(any(t == 'ORGANIZATION' for t, _ in vals))
        with tempfile.TemporaryDirectory() as td:
            vault = FileTokenVault(base_dir=td)
            pseudo = pseudonymize(text, self.detectors, vault, context_id='ctx:en', language='en')
            self.assertNotEqual(pseudo.pseudonymized_text, text)
            _assert_tokens(pseudo.pseudonymized_text)
            der = deanonymize(pseudo.pseudonymized_text, vault, 'ctx:en')
            self.assertEqual(der.restored_text, text)


if __name__ == '__main__':
    unittest.main()

