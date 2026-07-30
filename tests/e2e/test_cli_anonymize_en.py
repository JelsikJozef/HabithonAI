"""E2E: mdify --anonymize-en writes a readable anonymized markdown for the EN variant.

After the switch to the ProcessingOrchestrator, --anonymize-en runs the per-document
pipeline and persists a human-facing ``hashed_documents/<rel>_anon.md`` for the English
variant (3.2.5 layout) plus the orchestrator artifacts. Runs with --offline so no OpenAI
call is made. Translation needs offline NMT models that are absent here, so the translation
phase is stubbed to emit the English variant + pair.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.preprocessing.presentation import cli
from src.preprocessing.domain.models_markdown import MarkdownDoc

PII_EMAIL = "alice@example.com"
PII_PHONE = "+1 555 123 4567"
DOC = f"# Doc\n\nContact {PII_EMAIL} or call {PII_PHONE}.\n"


@pytest.fixture()
def stub_translation(monkeypatch: pytest.MonkeyPatch) -> None:
    def _fake_translation(ns, cfg, convert_code, results):  # type: ignore[no-untyped-def]
        en_root = cfg.out / "en"
        en_root.mkdir(parents=True, exist_ok=True)
        pairs: list[tuple[Any, str, str | None]] = []
        for p in sorted(Path(cfg.src).glob("*.md")):
            text = p.read_text(encoding="utf-8")
            orig = MarkdownDoc(
                doc_id=f"md::{p.name}",
                path=str(p),
                variant="original",
                lang=None,
                text_md=text,
                meta={},
            )
            en_path = en_root / f"{p.stem}_en.md"
            en_path.write_text(text, encoding="utf-8")  # already English -> copy
            pairs.append((orig, str(en_path), "en"))
        return -1, {"docs_total": len(pairs)}, pairs

    monkeypatch.setattr(cli, "_run_translation_phase", _fake_translation)


@pytest.mark.e2e
def test_cli_anonymize_en_offline(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_translation: None
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ANON_DETECTORS", "regex")
    monkeypatch.setenv("ANON_VAULT_DIR", str(tmp_path / "vault"))

    src = tmp_path / "src"
    src.mkdir()
    (src / "doc.md").write_text(DOC, encoding="utf-8")
    out = tmp_path / "out"
    report = tmp_path / "report.json"

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

    # Readable anonymized markdown for the EN variant under hashed_documents/.
    anon_files = list((out / "hashed_documents").rglob("*_anon.md"))
    assert anon_files, "expected an anonymized markdown copy for the English variant"
    text = anon_files[0].read_text(encoding="utf-8")
    assert "h:" in text  # Crypto.hash token prefix
    assert PII_EMAIL not in text
    assert PII_PHONE not in text

    # Report contains the pipeline section with one processed document.
    payload = json.loads(report.read_text(encoding="utf-8"))
    pipeline = payload.get("pipeline") or {}
    assert pipeline.get("processed") == 1
    assert pipeline["results"][0]["status"] == "ok"
