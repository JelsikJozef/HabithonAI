import sys
import types

import pytest


def _install_fasttext_stub(monkeypatch, router):
    class _StubModel:
        def __init__(self, r):
            self._r = r
        def predict(self, sample, k=1):
            return self._r(sample)
    class _StubFastText:
        def __init__(self, r):
            self._r = r
        def load_model(self, path):
            return _StubModel(self._r)
    stub = _StubFastText(router)
    mod = types.SimpleNamespace(load_model=stub.load_model)
    monkeypatch.setitem(sys.modules, 'fasttext', mod)


def _router(sample: str):
    s = (sample or '').lower()
    # Low confidence for short/ambiguous
    if len(s) < 4 or s.strip() in {"ok", "hi", "yo"}:
        return (["__label__en"], [0.30])
    # Slovak cues
    if any(tok in s for tok in ["ahoj", "ďakujem", "slovensk", "šťastný"]):
        return (["__label__sk"], [0.95])
    # German cues
    if any(tok in s for tok in ["guten tag", "hallo", "ich bin", "über"]):
        return (["__label__de"], [0.96])
    # French cues
    if any(tok in s for tok in ["bonjour", "merci", "français"]):
        return (["__label__fr"], [0.97])
    # English cues (default high confidence)
    if any(tok in s for tok in ["hello", "this is a test", "english"]):
        return (["__label__en"], [0.98])
    # Fallback high-confidence English
    return (["__label__en"], [0.90])


@pytest.fixture()
def detector(tmp_path, monkeypatch):
    # Install stub fasttext before importing and instantiating detector
    _install_fasttext_stub(monkeypatch, _router)
    # Import after stub installed
    from preprocessing.adapters.language import FastTextLanguageDetector  # type: ignore
    # Create a dummy model file so constructor doesn't try to download
    model_path = tmp_path / "lid.176.ftz"
    model_path.write_bytes(b"")
    return FastTextLanguageDetector(model_path=str(model_path), min_confidence=0.5)


def test_detects_known_languages(detector):
    # Slovak
    sk_text = "Ahoj svet! Ďakujem za pomoc."
    assert detector.detect(sk_text) == "sk"
    # English
    en_text = "Hello, this is a test of the language detector."
    assert detector.detect(en_text) == "en"
    # German
    de_text = "Guten Tag! Ich bin eine Sprache."
    assert detector.detect(de_text) == "de"
    # French (as an additional language)
    fr_text = "Bonjour et merci pour votre attention."
    assert detector.detect(fr_text) == "fr"


def test_returns_none_for_low_confidence(detector):
    ambiguous = "OK"  # too short/ambiguous -> low confidence
    assert detector.detect(ambiguous) is None
    empty = "   "
    assert detector.detect(empty) is None

