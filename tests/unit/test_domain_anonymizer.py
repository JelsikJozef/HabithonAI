import pytest
import re
from anonymization.domain.anonymizer import anonymize
from anonymization.domain.entities import PiiEntity, TokenMapping, PseudonymizationResult
from anonymization.adapters.crypto.crypto import Crypto
from anonymization.domain.errors import AnonymizationError


# Dummy detector that finds all occurrences of a target word as PERSON
class DummyDetector:
    name = "dummy"

    def __init__(self, word):
        self.word = word

    def detect(self, text: str, language=None):
        entities = []
        for match in re.finditer(re.escape(self.word), text):
            entities.append(
                PiiEntity(type="PERSON", start=match.start(), end=match.end(), value=match.group())
            )
        return entities


class DummyVault:
    def __init__(self):
        self.saved = []

    def save_mappings(self, context_id, mappings):
        self.saved.append((context_id, list(mappings)))

    def get_mappings(self, context_id):
        raise NotImplementedError

    def clear_context(self, context_id):
        raise NotImplementedError


@pytest.fixture
def crypto():
    return Crypto()


@pytest.fixture
def vault():
    return DummyVault()


def test_anonymize_basic(crypto, vault):
    text = "Hello Alice, meet Alice."
    detectors = [DummyDetector("Alice")]
    ctx = "test1"
    res = anonymize(text, detectors, crypto, vault, context_id=ctx)
    # Expect two tokens
    assert isinstance(res, PseudonymizationResult)
    # Check mappings saved
    assert vault.saved, "Mappings were not saved"
    saved_ctx, mappings = vault.saved[0]
    assert saved_ctx == ctx
    assert len(mappings) == 2
    # Check tokens deterministic: identical values produce identical tokens
    tokens = [m.token for m in mappings]
    assert tokens[0] == tokens[1]
    # Anonymized text contains the hash token in place of 'Alice'
    assert tokens[0] in res.pseudonymized_text


def test_anonymize_no_pii(crypto, vault):
    text = "No persons here."
    detectors = [DummyDetector("Bob")]
    ctx = "test2"
    res = anonymize(text, detectors, crypto, vault, context_id=ctx)
    assert res.pseudonymized_text == text
    # Ensure empty mappings
    assert vault.saved and vault.saved[0][1] == []


def test_anonymize_error_in_detector(crypto, vault):
    class BadDetector:
        name = "bad"

        def detect(self, text, language=None):
            raise RuntimeError("fail")

    text = "Test"
    with pytest.raises(AnonymizationError):
        anonymize(text, [BadDetector()], crypto, vault, context_id="ctx")
