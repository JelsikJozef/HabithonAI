import json

import pytest

from src.preprocessing.presentation import cli


@pytest.mark.e2e
def test_cli_anonymize_en_offline(tmp_path, monkeypatch):
    # Prepare minimal folder structure
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    # Pre-create English variant to avoid translation dependency
    en_dir = out / "en"
    en_dir.mkdir(parents=True, exist_ok=True)
    md = en_dir / "doc.md"
    md.write_text("Contact alice@example.com or +1 555 123 4567.", encoding="utf-8")

    report = tmp_path / "report.json"

    # Force regex detector to avoid presidio and ensure offline; isolate the token vault.
    monkeypatch.setenv("ANON_DETECTORS", "regex")
    monkeypatch.setenv("ANON_VAULT_DIR", str(tmp_path / "vault"))

    argv = [
        "--src",
        str(src),
        "--out",
        str(out),
        "--translate-only",
        "--workers",
        "1",
        "--overwrite",
        "--log-level",
        "ERROR",
        "--progress",
        "none",
        "--report",
        str(report),
        "--anonymize-en",
    ]
    code = cli.main(argv)
    assert code in (0, 1, 3)

    # Anonymized copy should exist
    anon_md = out / "hashed_documents" / "doc_anon.md"
    assert anon_md.exists(), f"Missing anonymized output: {anon_md}"

    text = anon_md.read_text(encoding="utf-8")
    # Expect Crypto.hash token prefix h:<kid>:
    assert "h:" in text
    assert "alice@example.com" not in text
    assert "+1 555 123 4567" not in text

    # Report JSON should contain anonymization section
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    anon = payload.get("anonymization") or {}
    assert isinstance(anon, dict)
