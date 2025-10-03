from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from src.preprocessing.segmenter.step2_segment import (
    run_step2,
    Step2Inputs,
    Step2Config,
    _compute_content_hash,
)


@pytest.fixture(autouse=True)
def cleanup_outputs():
    # clean test doc artifacts before/after
    yield
    # Optionally keep artifacts for debugging by commenting this out
    # shutil.rmtree("outputs/artifacts", ignore_errors=True)


def make_step1(doc_uid: str, text: str, meta: dict) -> tuple[str, Path]:
    base = Path("outputs/artifacts") / doc_uid / "step1"
    base.mkdir(parents=True, exist_ok=True)
    (base / "normalized.txt").write_text(text, encoding="utf-8")
    canonical_meta = {
        k: meta[k] for k in ["doc_type", "category", "language", "anonymizer_versions"] if k in meta
    }
    if "source_path" in meta:
        canonical_meta["source_path"] = meta["source_path"]
    content_hash = _compute_content_hash(text, canonical_meta)
    result = {
        "document_uid": doc_uid,
        "content_hash": content_hash,
        "normalized_text": text[:0],  # not needed here
        "canonical_metadata": canonical_meta,
        "normalization_policy_version": "step1-nfc-lf-v1",
        "processing_report": {"timings_ms": {"normalize": 1}},
        "checks": {"determinism_check": {"passed": True}},
        "status": "ok",
    }
    (base / "result.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return content_hash, base


def test_step2_basic_chunking(tmp_path: Path):
    # Prepare a sample normalized markdown text with headings, paragraph, list, code, table
    text = (
        "# Title\n\n"
        "Intro paragraph with some content. It should be part of the first chunk.\n\n"
        "## Section A\n\n"
        "- item one\n- item two\n\n"
        "```python\nprint('hello')\nprint('world')\n```\n\n"
        "| col1 | col2 |\n| ---- | ---- |\n| a    | b    |\n\n" + ("Paragraph text. " * 200)
    )
    doc_uid = "doc_testseg1234"
    meta = {
        "doc_type": "md",
        "category": "unit",
        "language": "en",
        "anonymizer_versions": {"mock": "1"},
    }
    content_hash, _ = make_step1(doc_uid, text, meta)

    cfg = Step2Config(
        target_chunk_chars=1000, hard_max_chunk_chars=1400, min_chunk_chars=200, overlap_chars=150
    )
    result = run_step2(
        Step2Inputs(
            document_uid=doc_uid,
            content_hash=content_hash,
            context={"run_id": "pytest"},
            config=cfg,
        )
    )

    assert isinstance(result, dict)
    assert result.get("status") == "ok", result
    chunks = result["chunks"]
    assert len(chunks) >= 2
    # Check IDs and offsets monotonic
    for i, ch in enumerate(chunks):
        assert ch["chunk_id"].endswith(f"_{i:04d}")
        assert ch["offset_start"] < ch["offset_end"]
        assert ch["char_count"] == len(ch["text"])  # bytes==chars for ASCII used here
        assert ch["overlap_from_prev"] <= cfg.overlap_chars
        if i > 0:
            assert ch["overlap_from_prev"] > 0
    # Artifacts present
    step2_dir = Path("outputs/artifacts") / doc_uid / "step2"
    assert (step2_dir / "result.json").exists()
    assert (step2_dir / "chunks.jsonl").exists()
    assert (step2_dir / "stats.json").exists()
    # QA stats
    stats = result["stats"]
    assert 0.999 <= stats["coverage_ratio"] <= 1.0
