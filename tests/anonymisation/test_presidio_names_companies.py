import os
import sys
import unittest

# Ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

try:
    import presidio_analyzer  # type: ignore
    PRESIDIO_AVAILABLE = True
except Exception:
    PRESIDIO_AVAILABLE = False

from anonymization.adapters.detectors.adapter import PresidioDetector  # type: ignore
from anonymization.adapters.token_vault.file_store import FileTokenVault  # type: ignore
from anonymization.app.pseudonymize import pseudonymize  # type: ignore
from anonymization.app.denomize import deanonymize  # type: ignore


@unittest.skipUnless(PRESIDIO_AVAILABLE, "presidio_analyzer not available; skipping PERSON/ORGANIZATION tests")
class TestPresidioNamesCompanies(unittest.TestCase):
    def test_german_person_and_company(self):
        text = "Herr Peter Müller arbeitet bei Beispiel GmbH & Co. KG in München."
        det = PresidioDetector()
        ents = det.detect(text, language='de')
        vals = [(e.type, e.value) for e in ents]
        self.assertTrue(any(t == 'PERSON' and 'Peter Müller' in v for t, v in vals))
        self.assertTrue(any(t == 'ORGANIZATION' and 'Beispiel GmbH' in v for t, v in vals))
        # Pseudonymize and ensure NAME/COMPANY token prefixes used
        vault = FileTokenVault(base_dir=os.path.join(os.getcwd(), '.vault_test'))
        ctx = 'ctx:de'
        pres = pseudonymize(text, [det], vault, ctx, language='de')
        self.assertIn('{{PII:NAME:', pres.pseudonymized_text)
        self.assertIn('{{PII:COMPANY:', pres.pseudonymized_text)
        der = deanonymize(pres.pseudonymized_text, vault, ctx)
        self.assertEqual(der.restored_text, text)

    def test_slovak_person_and_company(self):
        text = "Pán Ján Novák je konateľ spoločnosti Profidecon s.r.o. so sídlom v Bratislave."
        det = PresidioDetector()
        ents = det.detect(text, language='sk')
        vals = [(e.type, e.value) for e in ents]
        self.assertTrue(any(t == 'PERSON' and 'Ján Novák' in v for t, v in vals))
        self.assertTrue(any(t == 'ORGANIZATION' and 'Profidecon s.r.o.' in v for t, v in vals))
        # Pseudonymize and ensure NAME/COMPANY token prefixes used
        vault = FileTokenVault(base_dir=os.path.join(os.getcwd(), '.vault_test'))
        ctx = 'ctx:sk'
        pres = pseudonymize(text, [det], vault, ctx, language='sk')
        self.assertIn('{{PII:NAME:', pres.pseudonymized_text)
        self.assertIn('{{PII:COMPANY:', pres.pseudonymized_text)
        der = deanonymize(pres.pseudonymized_text, vault, ctx)
        self.assertEqual(der.restored_text, text)

    def test_german_no_false_positive_person_on_generic_caps(self):
        text = "Ihre Steuernummer der Organisation finden Sie in Ihrem Benutzerkonto."
        det = PresidioDetector()
        ents = det.detect(text, language='de')
        self.assertFalse(any(e.type == 'PERSON' for e in ents), msg=f"Unexpected PERSON: {[(e.type, e.value, e.score) for e in ents]}")

    def test_slovak_no_false_positive_person_on_generic_caps(self):
        text = "Steuernummer der Organisation"  # mixed-language edge but includes caps
        det = PresidioDetector()
        ents = det.detect(text, language='sk')
        self.assertFalse(any(e.type == 'PERSON' for e in ents), msg=f"Unexpected PERSON: {[(e.type, e.value, e.score) for e in ents]}")


if __name__ == '__main__':
    unittest.main()
