import os
import sys
import unittest

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from anonymization.domain.entities import PiiEntity, merge_overlapping_entities
from anonymization.adapters.detectors.presidio.postprocess.organization import trim_organization_entities


class TestMergeAndTrim(unittest.TestCase):
    def test_merge_prefers_person_over_org_overlap(self):
        text = "Herr Peter Müller arbeitet bei Beispiel GmbH & Co. KG in München."
        # ORGANIZATION wrongly spans the whole phrase (as observed bug)
        org = PiiEntity(
            type="ORGANIZATION",
            start=0,
            end=text.index(" in München."),
            value=text[0:text.index(" in München.")],
            score=0.5,
            detector="presidio",
        )
        # PERSON inside
        person_start = text.index("Peter Müller")
        person = PiiEntity(
            type="PERSON",
            start=person_start,
            end=person_start + len("Peter Müller"),
            value="Peter Müller",
            score=0.9,
            detector="presidio",
        )
        merged = merge_overlapping_entities([org, person])
        # PERSON should survive and ORG dropped due to overlap and priority
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].type, "PERSON")
        self.assertEqual(merged[0].value, "Peter Müller")

    def test_de_org_trim_excludes_preceding_phrase(self):
        text = "Herr Peter Müller arbeitet bei Beispiel GmbH & Co. KG in München."
        # Simulate raw ORG detection that over-includes
        org = PiiEntity(
            type="ORGANIZATION",
            start=0,
            end=text.index(" in München."),
            value=text[0:text.index(" in München.")],
            score=0.5,
            detector="presidio",
        )
        trimmed = trim_organization_entities([org], text, language="de")
        self.assertEqual(len(trimmed), 1)
        t = trimmed[0]
        self.assertEqual(t.type, "ORGANIZATION")
        # Expect it to start at "Beispiel" now
        expected_start = text.index("Beispiel")
        self.assertEqual(t.start, expected_start)
        self.assertTrue(t.value.startswith("Beispiel"))
        self.assertTrue(t.value.endswith("GmbH & Co. KG"))


if __name__ == "__main__":
    unittest.main()
