import json

import pytest

from src.preprocessing.presentation import cli


@pytest.mark.e2e
def test_cli_translate_only_offline(tmp_path, monkeypatch):
    # Prepare a small Markdown tree under src
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    # Sentence that detects as 'sk' and yields a confident EN translation (en_conf >= tau_en).
    md = src / "doc.md"
    md.write_text(
        "Spoločnosť minulý rok dosiahla rekordné tržby a zisk vo všetkých regiónoch.\n",
        encoding="utf-8",
    )
    report = tmp_path / "report.json"

    # Run CLI main in translate-only mode (skip convert phase), offline
    argv = [
        "--src",
        str(src),
        "--out",
        str(out),
        "--translate-only",
        "--make-english",
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
    code = cli.main(argv)
    assert code in (0, 1, 3)  # allow 3 when translation components unavailable

    # Report JSON should exist and include translation section
    assert report.exists()
    payload = json.loads(report.read_text(encoding="utf-8"))
    tr = payload.get("translation") or {}
    assert isinstance(tr, dict)
    if code == 3:
        assert "error" in tr
    else:
        # English variant is written under out/en with the enforced "_en" suffix policy.
        en_md = out / "en" / "doc_en.md"
        assert en_md.exists()
