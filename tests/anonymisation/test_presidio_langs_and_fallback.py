import os
import sys
import tempfile
import unittest

# Ensure src is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from anonymization.adapters.detectors.presidio_detector import PresidioDetector  # type: ignore
from anonymization.adapters.token_vault.file_store import FileTokenVault  # type: ignore
from anonymization.app.pseudonymize import pseudonymize  # type: ignore
from anonymization.app.denomize import deanonymize  # type: ignore


class TestPresidioLanguagesAndFallback(unittest.TestCase):
    def test_detect_sk_email_phone(self):
        text = "Kontaktujte nás na profidecon@profidecon.com alebo +421 905 123 456."
        det = PresidioDetector()
        ents = det.detect(text, language='sk')
        vals = {(e.type, e.value) for e in ents}
        self.assertIn(('EMAIL', 'profidecon@profidecon.com'), vals)
        # Phone may be normalized by Presidio; accept presence of a PHONE entity that covers the digits
        self.assertTrue(any(e.type in ('PHONE', 'PHONE_NUMBER') and '+421' in e.value for e in ents))

    def test_pseudonymize_deanonymize_roundtrip(self):
        text = "Kontaktujte nás na profidecon@profidecon.com alebo +421 905 123 456."
        det = PresidioDetector()
        with tempfile.TemporaryDirectory() as td:
            vault = FileTokenVault(base_dir=td)
            ctx = 'ctx:unit'
            pres = pseudonymize(text, [det], vault, ctx, language='sk')
            # Pseudonymized text should differ and include token markers
            self.assertNotEqual(pres.pseudonymized_text, text)
            self.assertIn('{{PII:', pres.pseudonymized_text)
            # Roundtrip restores original
            der = deanonymize(pres.pseudonymized_text, vault, ctx)
            self.assertEqual(der.restored_text, text)

    def test_unsupported_language_uses_fallback(self):
        text = "Email alice@example.com a číslo +49 171 2345678"
        det = PresidioDetector()
        ents = det.detect(text, language='xx')  # unsupported code
        # Should not raise and should detect at least the email via fallback
        self.assertTrue(any(e.type in ('EMAIL', 'EMAIL_ADDRESS') and 'alice@example.com' in e.value for e in ents))


if __name__ == '__main__':
    unittest.main()

