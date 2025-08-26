import json
from pathlib import Path

import pytest

from src.preprocessing.presentation import cli


@pytest.mark.e2e
def test_cli_translate_only_offline(tmp_path, monkeypatch):
    # Prepare a small Markdown tree under src
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    # Content crafted to trigger heuristic 'sk' detection (contains " a je ")
    md = src / "doc.md"
    md.write_text("Toto a je test.\n", encoding="utf-8")
    report = tmp_path / "report.json"

    # Run CLI main in translate-only mode (skip convert phase), offline
    argv = [
        "--src", str(src),
        "--out", str(out),
        "--translate-only",
        "--make-english",
        "--workers", "1",
        "--overwrite",
        "--log-level", "ERROR",
        "--progress", "none",
        "--report", str(report),
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
        # English variant should be written under out/en
        en_md = out / "en" / "doc.md"
        assert en_md.exists()
