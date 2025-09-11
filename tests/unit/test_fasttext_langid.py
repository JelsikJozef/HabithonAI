import types
import pytest

from src.preprocessing.adapters.langid.fasttext_langid import FastTextLangId


class DummyModel:
    def __init__(self, labels):
        self._labels = labels
    def predict(self, text, k=1):
        # Return first k labels and descending scores deterministically
        labs = [f"__label__{l}" for l in self._labels[:k]]
        scores = [1.0 - (i * 0.1) for i in range(len(labs))]
        return labs, scores


def test_preprocess_and_short_text_best_effort(monkeypatch):
    lid = FastTextLangId(model_path="/tmp/does-not-exist.bin", max_chars=100, min_chars=50)
    # Avoid importing fasttext; pretend loaded
    lid._loaded = True
    lid._model = DummyModel(["sk", "de"])  # type: ignore[attr-defined]
    out, had_code = lid._preprocess_md("```\ncode\n``` text [lbl](http://x) ![alt](img) <b>html</b>")
    # Code removed; tags stripped but inner text kept; link label and alt present
    assert "code" not in out
    assert "lbl" in out
    assert "alt" in out
    assert "<" not in out and ">" not in out
    lang, conf = lid.detect("Hi")  # too short → best effort
    assert isinstance(lang, str) and 0.0 <= conf <= 1.0


def test_candidates_bias_and_predict(monkeypatch):
    lid = FastTextLangId(model_path="/tmp/x.bin", candidates=["de", "sk"], max_chars=100, min_chars=1)
    lid._loaded = True
    lid._model = DummyModel(["fr", "de"])  # type: ignore[attr-defined]
    lang, conf = lid.detect("Bonjour and und", hints={"candidates": ["de", "sk"]})
    assert lang == "de"
    assert 0.2 <= conf <= 1.0
