"""End-to-end test of the mdify CLI wired to the ProcessingOrchestrator.

Runs the full pipeline through ``cli.main`` for one SK document (-> original + English
variants) and one EN document (-> single chain), asserting the orchestrator artifacts,
the bound metadata, the readable hashed_documents/_anon.md (EN), invariant 1 (no original
PII downstream), and that Step3 (LLM) runs exactly once per English chain.

Translation needs offline NMT models that are not present here, so ``_run_translation_phase``
is stubbed to emit the English variants + pairs; the Step3 LLM is stubbed too. Everything
else (anonymization, Step1/2/3, artifact persistence) runs for real via the CLI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.preprocessing.presentation import cli
from src.preprocessing.domain.models_markdown import MarkdownDoc

PII_EMAIL = "alice.smith@example.com"
PII_PHONE = "+1-555-123-4567"


def _body(intro: str) -> str:
    return (
        "# Report\n\n"
        f"{intro}\n\n"
        f"Please contact {PII_EMAIL} or call {PII_PHONE} for details.\n\n"
        "## Notes\n\nThe project remains on schedule and within budget.\n"
    )


SK_SRC = _body("Tržby rástli rovnomerne vo všetkých regiónoch.")
EN_SRC = _body("Revenue grew steadily across all regions.")
EN_TRANSLATION = _body("Revenue grew steadily across all regions (translated).")


@pytest.fixture()
def stub_translation_and_llm(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Stub the (model-dependent) translation phase and the Step3 LLM call."""
    captured: list[str] = []

    def _fake_translation(ns, cfg, convert_code, results):  # type: ignore[no-untyped-def]
        en_root = cfg.out / "en"
        en_root.mkdir(parents=True, exist_ok=True)
        pairs: list[tuple[Any, str, str | None]] = []
        for p in sorted(Path(cfg.src).glob("*.md")):
            text = p.read_text(encoding="utf-8")
            src_lang = "sk" if p.stem == "sk" else "en"
            orig = MarkdownDoc(
                doc_id=f"md::{p.name}",
                path=str(p),
                variant="original",
                lang=None,
                text_md=text,
                meta={},
            )
            en_text = EN_TRANSLATION if src_lang == "sk" else text
            en_path = en_root / f"{p.stem}_en.md"
            en_path.write_text(en_text, encoding="utf-8")
            pairs.append((orig, str(en_path), src_lang))
        return -1, {"docs_total": len(pairs)}, pairs

    def _fake_summarize(text: str, *, model: str, timeout_s: int, seed: int) -> dict[str, Any]:
        captured.append(text)
        return {
            "summary": "The report summarizes quarterly project status and budget.",
            "keywords": ["report", "project", "budget", "schedule", "status"],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(cli, "_run_translation_phase", _fake_translation)
    monkeypatch.setattr(
        "src.preprocessing.analysis.step3_summary.summarize_keywords", _fake_summarize
    )
    return captured


def _setup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path, Path]:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANON_DETECTORS", "regex")
    monkeypatch.setenv("ANON_VAULT_DIR", str(tmp_path / "vault"))
    src = tmp_path / "src"
    src.mkdir()
    (src / "sk.md").write_text(SK_SRC, encoding="utf-8")
    (src / "en.md").write_text(EN_SRC, encoding="utf-8")
    out = tmp_path / "out"
    report = tmp_path / "report.json"
    return src, out, report


def _pipeline_results(report: Path) -> dict[str, dict[str, Any]]:
    payload = json.loads(report.read_text(encoding="utf-8"))
    rows = payload["pipeline"]["results"]
    return {r["doc_id"]: r for r in rows}


def _no_pii(text: str) -> None:
    assert PII_EMAIL not in text
    assert PII_PHONE not in text


def _artifacts(tmp_path: Path) -> Path:
    return tmp_path / "outputs" / "artifacts"


@pytest.mark.e2e
def test_cli_pipeline_two_variants_and_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_translation_and_llm: list[str]
) -> None:
    src, out, report = _setup(tmp_path, monkeypatch)

    code = cli.main(
        [
            "--src",
            str(src),
            "--out",
            str(out),
            "--translate-only",
            "--make-english",
            "--anonymize-en",
            "--workers",
            "1",
            "--overwrite",
            "--log-level",
            "ERROR",
            "--progress",
            "none",
            "--report",
            str(report),
        ]
    )
    assert code in (0, 1)

    rows = _pipeline_results(report)
    sk = rows["md::sk.md"]
    en = rows["md::en.md"]
    assert sk["status"] == "ok" and en["status"] == "ok"

    art = _artifacts(tmp_path)

    # SK doc -> two distinct variants, both segmented; only EN has step3.
    orig_uid, en_uid = sk["orig_uid"], sk["en_uid"]
    assert orig_uid and en_uid and orig_uid != en_uid
    assert (art / orig_uid / "step2" / "chunks.jsonl").exists()
    assert (art / en_uid / "step2" / "chunks.jsonl").exists()
    assert (art / en_uid / "step3").exists()
    assert not (art / orig_uid / "step3").exists()

    # One metadata payload bound to BOTH SK variants with preserved cross-links.
    assert sk["metadata_bound"] is True
    en_meta = json.loads((art / en_uid / "metadata_merged.json").read_text(encoding="utf-8"))
    orig_meta = json.loads((art / orig_uid / "metadata_merged.json").read_text(encoding="utf-8"))
    for m, variant, lang in ((en_meta, "en", "en"), (orig_meta, "orig", "sk")):
        assert m["variant"] == variant
        assert m["language"] == lang
        assert m["keywords_top5"] == en_meta["keywords_top5"]
        assert m["metadata_source"] == {"variant": "en", "document_uid": en_uid}
        assert m["variants"] == {"orig": orig_uid, "en": en_uid}

    # EN doc -> single chain (already English), one uid, with step3.
    assert en["orig_uid"] is None
    assert en["en_uid"] and (art / en["en_uid"] / "step3").exists()

    # Step3 LLM ran exactly twice: SK's English variant + the EN document.
    assert len(stub_translation_and_llm) == 2

    # Readable anonymized markdown for EN variants under hashed_documents/.
    anon_files = list((out / "hashed_documents").rglob("*_anon.md"))
    assert anon_files
    for f in anon_files:
        _no_pii(f.read_text(encoding="utf-8"))

    # Invariant 1: no original PII in any variant's chunks, and tokens present.
    for uid in (orig_uid, en_uid, en["en_uid"]):
        chunks = (art / uid / "step2" / "chunks.jsonl").read_text(encoding="utf-8")
        _no_pii(chunks)
    for text in stub_translation_and_llm:
        _no_pii(text)
        assert "h:" in text


@pytest.mark.e2e
def test_cli_pipeline_offline_skips_step3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_translation_and_llm: list[str]
) -> None:
    src, out, report = _setup(tmp_path, monkeypatch)

    code = cli.main(
        [
            "--src",
            str(src),
            "--out",
            str(out),
            "--translate-only",
            "--make-english",
            "--anonymize-en",
            "--offline",
            "--workers",
            "1",
            "--overwrite",
            "--log-level",
            "ERROR",
            "--progress",
            "none",
            "--report",
            str(report),
        ]
    )
    assert code in (0, 1)

    # Step3 never called offline; no metadata payload bound.
    assert len(stub_translation_and_llm) == 0

    rows = _pipeline_results(report)
    sk = rows["md::sk.md"]
    assert sk["status"] == "ok" and sk["metadata_bound"] is False

    art = _artifacts(tmp_path)
    orig_uid, en_uid = sk["orig_uid"], sk["en_uid"]
    # Step1 + Step2 still produced for both variants; no step3 / metadata_merged.
    for uid in (orig_uid, en_uid):
        assert (art / uid / "step1" / "normalized.txt").exists()
        assert (art / uid / "step2" / "chunks.jsonl").exists()
        assert not (art / uid / "step3").exists()
        assert not (art / uid / "metadata_merged.json").exists()

    # hashed_documents still written for the EN variant.
    assert list((out / "hashed_documents").rglob("*_anon.md"))
