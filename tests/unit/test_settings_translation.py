import json
from collections.abc import Mapping

from src.preprocessing import settings_translation as st


def _thaw(obj):
    if isinstance(obj, Mapping):
        return {k: _thaw(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set)):
        return [_thaw(v) for v in obj]
    return obj


def test_validate_translation_settings_ok_marian(tmp_path, monkeypatch):
    # Create dummy langid model file and cache parent
    lid = tmp_path / "lid.176.bin"
    lid.write_bytes(b"dummy")
    cache_parent = tmp_path / "cache"
    cache_parent.mkdir()
    cache_file = cache_parent / "mt_cache.sqlite"

    cfg = _thaw(st.TRANSLATION)
    cfg["engine"] = "marian_opus"
    cfg["langid"]["model_path"] = str(lid)
    cfg["langid"]["candidates"] = ["sk", "de", "en"]
    cfg["cache"]["root_path"] = str(cache_file)
    # Minimal marian models mapping covering candidates
    cfg["marian"]["models"] = {"sk": "Helsinki-NLP/opus-mt-sk-en", "de": "Helsinki-NLP/opus-mt-de-en"}
    cfg["marian"]["device"] = "cpu"
    cfg["marian"]["local_files_only"] = True

    ok, issues = st.validate_translation_settings(cfg)
    assert ok is True
    assert issues == []


def test_validate_translation_settings_ct2_reports_missing_paths(tmp_path):
    cfg = _thaw(st.TRANSLATION)
    cfg["engine"] = "ct2_nllb"
    # Point to non-existing paths to force issues
    cfg["ct2_nllb"]["model_dir"] = str(tmp_path / "nope")  # missing
    cfg["langid"]["model_path"] = str(tmp_path / "missing.bin")
    cfg["cache"]["root_path"] = str(tmp_path / "subdir" / "mt_cache.sqlite")

    ok, issues = st.validate_translation_settings(cfg)
    assert ok is False
    assert any("LangID model not found" in s for s in issues)
    assert any("CT2/NLLB model_dir not found" in s for s in issues)
