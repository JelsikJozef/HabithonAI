from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.preprocessing.app.normalize import Step1Inputs, run_step1


def read_text(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def test_step1_happy_path_creates_artifacts_and_is_deterministic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Arrange
    bom = "\ufeff"
    input_text = bom + "Hello  \r\nWorld\t!\r\n\r\nEnd"
    meta = {
        "doc_type": "note",
        "category": "general",
        "language": "en",
        "anonymizer_versions": {"rule": "1.0.0"},
        "source_path": "tmp/sample.md",
        "volatile": "ignore_me",
    }
    context = {"run_id": "run123", "tool_versions": {"step": 1}}

    # Use a different outputs root within tmp_path by monkeypatching paths
    from src.preprocessing.app import normalize as step1_mod

    monkeypatch.setattr(step1_mod, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(step1_mod, "LOGS_ROOT", tmp_path / "logs")

    # Act
    result = run_step1(Step1Inputs(text=input_text, meta=meta, context=context))

    # Assert basic shape
    assert result["status"] == "ok"
    uid = result["document_uid"]
    ch = result["content_hash"]
    assert isinstance(uid, str) and uid.startswith("doc_")
    assert len(ch) == 64

    # Normalization checks: LF endings, tabs preserved, trailing spaces preserved
    norm = result["normalized_text"]
    assert "\r" not in norm
    assert "\t" in norm
    assert "Hello  \nWorld\t!\n\nEnd" in norm  # spaces and blank line preserved

    # Determinism: rerun with identical input yields identical identifiers
    result2 = run_step1(Step1Inputs(text=input_text, meta=meta, context=context))
    assert result2["document_uid"] == uid
    assert result2["content_hash"] == ch

    # Artifacts exist
    art_dir = tmp_path / "artifacts" / uid / "step1"
    assert art_dir.is_dir()
    result_json = art_dir / "result.json"
    normalized_txt = art_dir / "normalized.txt"
    summary_txt = art_dir / "summary.txt"
    assert result_json.exists()
    assert normalized_txt.exists()
    assert summary_txt.exists()

    # result.json references document_uid and canonical metadata excludes volatile fields
    payload = json.loads(read_text(result_json))
    assert payload["document_uid"] == uid
    assert "volatile" not in payload["canonical_metadata"]

    # normalized.txt content matches result normalized_text exactly
    assert read_text(normalized_txt) == norm

    # Log file created
    logs = list((tmp_path / "logs").glob(f"step1_{uid}_run123.log"))
    assert logs, "Expected a log file with uid and run_id"


def test_step1_failure_on_non_en_language_does_not_emit_normalized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    # Arrange
    input_text = "No PII here."
    meta = {
        "doc_type": "note",
        "category": "general",
        "language": "sk",  # not en
        "anonymizer_versions": {"rule": "1.0.0"},
    }
    context = {"run_id": "runX"}

    from src.preprocessing.app import normalize as step1_mod

    monkeypatch.setattr(step1_mod, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(step1_mod, "LOGS_ROOT", tmp_path / "logs")

    # Act
    result = run_step1(Step1Inputs(text=input_text, meta=meta, context=context))

    # Assert
    assert result["status"] == "failed"
    uid = result["document_uid"]
    art_dir = tmp_path / "artifacts" / uid / "step1"
    assert art_dir.is_dir()
    assert (art_dir / "result.json").exists()
    assert not (art_dir / "normalized.txt").exists()

    # Checks contain invalid_language error
    errors = result["checks"]["meta_validation"]["errors"]
    assert any(e.get("code") == "invalid_language" for e in errors)


def test_variant_disambiguates_doc_uid_for_already_english_copy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """An already-English doc copied as the English variant has byte-identical text and
    language='en'. The variant is the sole disambiguator: orig vs en must yield distinct
    doc_uid / content_hash so their artifacts never overwrite each other."""
    input_text = "This document is already written in English."
    meta = {
        "doc_type": "note",
        "category": "general",
        "language": "en",
        "anonymizer_versions": {"rule": "1.0.0"},
    }
    context = {"run_id": "runV"}

    from src.preprocessing.app import normalize as step1_mod

    monkeypatch.setattr(step1_mod, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(step1_mod, "LOGS_ROOT", tmp_path / "logs")

    res_orig = run_step1(Step1Inputs(text=input_text, meta=meta, context=context, variant="orig"))
    res_en = run_step1(Step1Inputs(text=input_text, meta=meta, context=context, variant="english"))

    assert res_orig["status"] == "ok"
    assert res_en["status"] == "ok"

    # Distinct identifiers despite identical text + language
    assert res_orig["document_uid"] != res_en["document_uid"]
    assert res_orig["content_hash"] != res_en["content_hash"]

    # Variant is the single disambiguator: present only for the non-original variant
    assert "variant" not in res_orig["canonical_metadata"]
    assert res_en["canonical_metadata"]["variant"] == "en"

    # Artifacts live under distinct doc_uid directories (no overwrite)
    orig_dir = tmp_path / "artifacts" / res_orig["document_uid"] / "step1"
    en_dir = tmp_path / "artifacts" / res_en["document_uid"] / "step1"
    assert orig_dir.is_dir() and en_dir.is_dir()
    assert orig_dir != en_dir


def test_orig_variant_preserves_doc_uid(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """Two original-variant runs of identical content keep the same doc_uid; the default
    variant must not perturb existing identifiers."""
    input_text = "Stable English content."
    meta = {
        "doc_type": "note",
        "category": "general",
        "language": "en",
        "anonymizer_versions": {"rule": "1.0.0"},
    }
    context = {"run_id": "runS"}

    from src.preprocessing.app import normalize as step1_mod

    monkeypatch.setattr(step1_mod, "ARTIFACTS_ROOT", tmp_path / "artifacts")
    monkeypatch.setattr(step1_mod, "LOGS_ROOT", tmp_path / "logs")

    # Default variant ("orig") and explicit "original" must agree, and be stable.
    res_default = run_step1(Step1Inputs(text=input_text, meta=meta, context=context))
    res_original = run_step1(
        Step1Inputs(text=input_text, meta=meta, context=context, variant="original")
    )

    assert res_default["document_uid"] == res_original["document_uid"]
    assert res_default["content_hash"] == res_original["content_hash"]
    assert "variant" not in res_default["canonical_metadata"]
