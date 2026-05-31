from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

import pytest

from src.preprocessing.app.normalize import Step1Inputs, run_step1


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8")


def _write(p: Path, text: str) -> None:
    p.write_text(text, encoding="utf-8")


def _mk_step1(
    tmp_path: Path, text: str = "An anonymized note without PII.", run_id: str = "r1"
) -> Dict[str, Any]:
    meta = {
        "doc_type": "note",
        "category": "general",
        "language": "en",
        "anonymizer_versions": {"rule": "1.0.0"},
    }
    context = {"run_id": run_id}

    # Run step1 with patched roots
    from src.preprocessing.app import normalize as step1_mod

    step1_mod.ARTIFACTS_ROOT = tmp_path / "artifacts"
    step1_mod.LOGS_ROOT = tmp_path / "logs"

    result = run_step1(Step1Inputs(text=text, meta=meta, context=context))
    assert result["status"] == "ok", f"Step1 failed unexpectedly: {result}"
    return result


def test_step3_happy_path_and_idempotent(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Arrange: create Step1 artifacts
    s1 = _mk_step1(
        tmp_path, text=("This is a sanitized document about project planning and timelines.\n" * 3)
    )
    uid = s1["document_uid"]
    ch = s1["content_hash"]

    # Prepare Step3 module with patched roots
    from src.preprocessing.analysis import step3_summary as step3_mod

    step3_mod.ARTIFACTS_ROOT = tmp_path / "artifacts"
    step3_mod.LOGS_ROOT = tmp_path / "logs"

    # Mock LLM client: deterministic output
    def fake_summarize(
        text: str, model: str = "gpt-5-mini", timeout_s: int = 60, seed: int | None = 0
    ) -> Dict[str, Any]:
        assert "@" not in text  # no PII
        return {
            "summary": "This document outlines project planning timelines.",
            "keywords": [
                "project planning",
                "timelines",
                "milestones",
                "resource allocation",
                "risk management",
            ],
            "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
        }

    monkeypatch.setattr(step3_mod, "summarize_keywords", fake_summarize)

    # Act
    out1 = step3_mod.run_step3(
        step3_mod.Step3Inputs(document_uid=uid, content_hash=ch, context={"run_id": "runA"})
    )
    out2 = step3_mod.run_step3(
        step3_mod.Step3Inputs(document_uid=uid, content_hash=ch, context={"run_id": "runA"})
    )

    # Assert
    assert out1["status"] == "ok"
    assert out2["status"] == "ok"
    step3_dir = tmp_path / "artifacts" / uid / "step3"
    assert (step3_dir / "result.json").exists()
    assert (step3_dir / "summary.txt").exists()
    assert (step3_dir / "keywords.json").exists()

    # Check merged metadata
    merged_path = tmp_path / "artifacts" / uid / "metadata_merged.json"
    assert merged_path.exists()
    merged = json.loads(_read(merged_path))
    assert (
        merged.get("summary_one_sentence") == "This document outlines project planning timelines."
    )
    assert merged.get("keywords_top5") == [
        "project planning",
        "timelines",
        "milestones",
        "resource allocation",
        "risk management",
    ]

    # Idempotency: result.json unchanged between runs
    r1 = json.loads(_read(step3_dir / "result.json"))
    r2 = json.loads(_read(step3_dir / "result.json"))
    assert r1["summary_one_sentence"] == r2["summary_one_sentence"]
    assert r1["keywords_top5"] == r2["keywords_top5"]

    # Log secrecy: ensure no raw content is logged
    logs = list((tmp_path / "logs").glob(f"step3_{uid}_*.log"))
    assert logs, "Expected a step3 log file"
    for lp in logs:
        log_txt = _read(lp)
        assert "OPENAI_API_KEY" not in log_txt
        assert "This is a sanitized document" not in log_txt


def test_step3_hash_mismatch_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    s1 = _mk_step1(tmp_path)
    uid = s1["document_uid"]

    from src.preprocessing.analysis import step3_summary as step3_mod

    step3_mod.ARTIFACTS_ROOT = tmp_path / "artifacts"
    step3_mod.LOGS_ROOT = tmp_path / "logs"

    out = step3_mod.run_step3(
        step3_mod.Step3Inputs(document_uid=uid, content_hash="deadbeef", context={"run_id": "r"})
    )
    assert out["status"] == "failed"
    assert any(e.get("code") == "hash_mismatch" for e in out.get("errors", []))

    step3_dir = tmp_path / "artifacts" / uid / "step3"
    assert (step3_dir / "result.json").exists()
    assert not (step3_dir / "summary.txt").exists()
    assert not (step3_dir / "keywords.json").exists()


def test_step3_invalid_keywords_shape_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    s1 = _mk_step1(tmp_path)
    uid = s1["document_uid"]
    ch = s1["content_hash"]

    from src.preprocessing.analysis import step3_summary as step3_mod

    step3_mod.ARTIFACTS_ROOT = tmp_path / "artifacts"
    step3_mod.LOGS_ROOT = tmp_path / "logs"

    def fake_bad_summarize(
        text: str, model: str = "gpt-5-mini", timeout_s: int = 60, seed: int | None = 0
    ) -> Dict[str, Any]:
        return {
            "summary": "This is valid one sentence.",
            "keywords": ["One", "one", "TWO", "three", "four"],  # invalid: uppercase and duplicates
            "usage": {},
        }

    monkeypatch.setattr(step3_mod, "summarize_keywords", fake_bad_summarize)

    out = step3_mod.run_step3(
        step3_mod.Step3Inputs(document_uid=uid, content_hash=ch, context={"run_id": "r"})
    )
    assert out["status"] == "failed"
    assert any(e.get("code") == "invalid_keywords_shape" for e in out.get("errors", []))

    step3_dir = tmp_path / "artifacts" / uid / "step3"
    assert (step3_dir / "result.json").exists()
    assert not (step3_dir / "summary.txt").exists()
    assert not (step3_dir / "keywords.json").exists()


def test_step3_too_large_guard(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    s1 = _mk_step1(tmp_path, text="A short text.")
    uid = s1["document_uid"]
    ch = s1["content_hash"]

    from src.preprocessing.analysis import step3_summary as step3_mod

    step3_mod.ARTIFACTS_ROOT = tmp_path / "artifacts"
    step3_mod.LOGS_ROOT = tmp_path / "logs"

    # Force a tiny max_input_chars to trigger guard
    cfg = step3_mod.Step3Config(max_input_chars=5)
    out = step3_mod.run_step3(
        step3_mod.Step3Inputs(
            document_uid=uid, content_hash=ch, context={"run_id": "r"}, config=cfg
        )
    )
    assert out["status"] == "failed"
    assert any(e.get("code") == "too_large" for e in out.get("errors", []))


def test_step3_passes_with_pseudonym_token_containing_digit_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """End-to-end: a Step1 output whose text contains a pseudonym token with a 10-digit
    run must NOT be rejected by Step3's anonymization-sanity guardrail. The token is a
    replacement, not a phone number; Step3 should proceed to summarization."""
    token_text = "Customer h:kid0:a1234567890bcdef0011223344556677 paid the invoice on time. " * 2
    s1 = _mk_step1(tmp_path, text=token_text)
    uid = s1["document_uid"]
    ch = s1["content_hash"]

    from src.preprocessing.analysis import step3_summary as step3_mod

    step3_mod.ARTIFACTS_ROOT = tmp_path / "artifacts"
    step3_mod.LOGS_ROOT = tmp_path / "logs"

    def fake_summarize(
        text: str, model: str = "gpt-5-mini", timeout_s: int = 60, seed: int | None = 0
    ) -> Dict[str, Any]:
        return {
            "summary": "The customer settled the invoice on time.",
            "keywords": ["customer", "invoice", "payment", "billing", "settlement"],
            "usage": {},
        }

    monkeypatch.setattr(step3_mod, "summarize_keywords", fake_summarize)

    out = step3_mod.run_step3(
        step3_mod.Step3Inputs(document_uid=uid, content_hash=ch, context={"run_id": "rTok"})
    )

    assert out["status"] == "ok"
    assert not any(e.get("code") == "anonymization_failed" for e in out.get("errors", []))


def test_step3_anonymization_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    # Create clean Step1 first
    s1 = _mk_step1(tmp_path, text="Clean base text.")
    uid = s1["document_uid"]
    ch = s1["content_hash"]

    # Now inject PII into normalized.txt to simulate leak discovered in step3
    norm_path = tmp_path / "artifacts" / uid / "step1" / "normalized.txt"
    _write(norm_path, "Contact me at user@example.com for details.")

    from src.preprocessing.analysis import step3_summary as step3_mod

    step3_mod.ARTIFACTS_ROOT = tmp_path / "artifacts"
    step3_mod.LOGS_ROOT = tmp_path / "logs"

    out = step3_mod.run_step3(
        step3_mod.Step3Inputs(document_uid=uid, content_hash=ch, context={"run_id": "r"})
    )
    assert out["status"] == "failed"
    assert any(e.get("code") == "anonymization_failed" for e in out.get("errors", []))

    step3_dir = tmp_path / "artifacts" / uid / "step3"
    assert (step3_dir / "result.json").exists()
    assert not (step3_dir / "summary.txt").exists()
    assert not (step3_dir / "keywords.json").exists()
