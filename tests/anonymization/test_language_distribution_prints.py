import os
import sys
import re
import unittest
import tempfile
from typing import Optional, List, Tuple

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


# Match tokens like {{PII:NAME:1:deadbeef}}
NAME_TOKEN_RE = re.compile(r"\{\{PII:NAME:\d+:[0-9a-fA-F]{8}}}")
COMPANY_TOKEN_RE = re.compile(r"\{\{PII:COMPANY:\d+:[0-9a-fA-F]{8}}}")


def _print_case_header(idx: int, lang: str, text: str):
    print(f"\n--- Case #{idx+1} [{lang}] ---")
    print(f"Input: {text}")


def _print_expected_vs_got(expected_person: Optional[str], expected_org: Optional[str], got_pairs: List[Tuple[str, str]]):
    exp_p = expected_person or "<none>"
    exp_o = expected_org or "<none>"
    print(f"Expected PERSON contains: {exp_p}")
    print(f"Expected ORGANIZATION contains: {exp_o}")
    print(f"Detected entities: {got_pairs}")
    ok_p = any(t == 'PERSON' and (expected_person and expected_person in v) for t, v in got_pairs) if expected_person else any(t == 'PERSON' for t, _ in got_pairs)
    ok_o = any(t == 'ORGANIZATION' and (expected_org and expected_org in v) for t, v in got_pairs) if expected_org else any(t == 'ORGANIZATION' for t, _ in got_pairs)
    print(f"PERSON matches expectation: {ok_p}")
    print(f"ORGANIZATION matches expectation: {ok_o}")


@unittest.skipUnless(PRESIDIO_AVAILABLE, "presidio_analyzer not available; skipping language distribution tests")
class TestLanguageDistributionPrints(unittest.TestCase):
    def setUp(self) -> None:
        self.detectors, _ = build_default()

    def test_language_distribution_and_roundtrip(self):
        # 10 cases: 4 de (40%), 4 sk (40%), 2 en (20%)
        cases = [
            # German (de)
            {
                'lang': 'de',
                'text': 'Herr Peter Müller arbeitet bei Beispiel GmbH & Co. KG in München.',
                'expect_person': 'Peter Müller',
                'expect_org': 'Beispiel GmbH',
            },
            {
                'lang': 'de',
                'text': 'Frau Anna Schmidt ist Angestellte der Acme AG in Berlin.',
                'expect_person': 'Anna Schmidt',
                'expect_org': 'Acme AG',
            },
            {
                'lang': 'de',
                'text': 'Dr. Karl-Heinz Meier traf sich mit Contoso GmbH in Hamburg.',
                'expect_person': 'Karl-Heinz Meier',
                'expect_org': 'Contoso GmbH',
            },
            {
                'lang': 'de',
                'text': 'Max Mustermann arbeitet für Musterfirma GmbH in Köln.',
                'expect_person': 'Max Mustermann',
                'expect_org': 'Musterfirma GmbH',
            },
            # Slovak (sk)
            {
                'lang': 'sk',
                'text': 'Pán Ján Novák pracuje v spoločnosti Profidecon s.r.o. v Bratislave.',
                'expect_person': 'Ján Novák',
                'expect_org': 'Profidecon s.r.o.',
            },
            {
                'lang': 'sk',
                'text': 'Pani Anna Kováčová je zamestnankyňou firmy Acme a.s. v Košiciach.',
                'expect_person': 'Anna Kováčová',
                'expect_org': 'Acme a.s.',
            },
            {
                'lang': 'sk',
                'text': 'Ing. Peter Horváth spolupracuje so spoločnosťou Contoso, a.s. v Žiline.',
                'expect_person': 'Peter Horváth',
                'expect_org': 'Contoso, a.s.',
            },
            {
                'lang': 'sk',
                'text': 'Mária Šimková pracuje v spoločnosti Uvt s.r.o. v Nitre.',
                'expect_person': 'Mária Šimková',
                'expect_org': 'Uvt s.r.o.',
            },
            # English (en)
            {
                'lang': 'en',
                'text': 'Alice Johnson works at OpenAI, Inc. in San Francisco.',
                'expect_person': 'Alice Johnson',
                'expect_org': 'OpenAI',
            },
            {
                'lang': 'en',
                'text': 'Bob Smith is employed by Contoso Ltd in London.',
                'expect_person': 'Bob Smith',
                'expect_org': 'Contoso Ltd',
            },
        ]

        # Sanity on distribution
        counts = {'de': 0, 'sk': 0, 'en': 0}
        for c in cases:
            counts[c['lang']] += 1
        print(f"Planned distribution: {counts}")

        for idx, case in enumerate(cases):
            lang = case['lang']
            text = case['text']
            exp_p = case.get('expect_person')
            exp_o = case.get('expect_org')

            _print_case_header(idx, lang, text)

            # Detect
            det_res = detect_all(text, self.detectors, language=lang)
            pairs = [(e.type, e.value) for e in det_res.entities]
            _print_expected_vs_got(exp_p, exp_o, pairs)

            # Basic assertions: at least one PERSON and one ORGANIZATION detected
            self.assertTrue(any(t == 'PERSON' for t, _ in pairs), msg=f"No PERSON detected; entities={pairs}")
            self.assertTrue(any(t == 'ORGANIZATION' for t, _ in pairs), msg=f"No ORGANIZATION detected; entities={pairs}")

            # Pseudonymize + roundtrip
            with tempfile.TemporaryDirectory() as td:
                vault = FileTokenVault(base_dir=td)
                ctx = f"ctx:{lang}:{idx}"
                pseudo = pseudonymize(text, self.detectors, vault, context_id=ctx, language=lang)
                print(f"Pseudonymized: {pseudo.pseudonymized_text}")
                self.assertTrue(NAME_TOKEN_RE.search(pseudo.pseudonymized_text) is not None)
                self.assertTrue(COMPANY_TOKEN_RE.search(pseudo.pseudonymized_text) is not None)
                der = deanonymize(pseudo.pseudonymized_text, vault, ctx)
                print(f"Roundtrip expected: {text}")
                print(f"Roundtrip got     : {der.restored_text}")
                self.assertEqual(der.restored_text, text)


if __name__ == '__main__':
    unittest.main(verbosity=2)
