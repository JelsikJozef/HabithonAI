# filepath: /Users/jozefjelsik/PycharmProjects/HabithonAI/tests/e2e/test_cli_metadata_vector.py
from pathlib import Path
import json
import pytest

from src.preprocessing.presentation import cli


@pytest.mark.e2e
def test_cli_metadata_and_vector_store(tmp_path, monkeypatch):
    # Prepare minimal folder structure and pre-created English variant
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    en_dir = out / "en"
    en_dir.mkdir(parents=True, exist_ok=True)
    md = en_dir / "doc.md"
    md.write_text("Contact alice@example.com or +1 555 123 4567.", encoding="utf-8")

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
        "--anonymize-en",
        "--with-metadata",
        "--vector-store",
    ]
    code = cli.main(argv)
    assert code in (0, 1, 3)

    # Check anonymized file. The .map.json mapping sidecar is gone: token mappings live
    # only in the unified domain Token Vault now.
    anon_md = out / "hashed_documents" / "doc_anon.md"
    assert anon_md.exists()
    map_sidecar = Path(str(anon_md) + ".map.json")
    assert not map_sidecar.exists()
    meta_sidecar = Path(str(anon_md) + ".meta.json")
    assert meta_sidecar.exists()

    # Token mappings were persisted to the unified vault keyed by the variant-scoped id.
    vault_files = list((tmp_path / "vault").glob("ctx_*_en.json"))
    assert vault_files, "expected a variant-scoped vault file under ANON_VAULT_DIR"

    # Vector store JSONL
    vs_jsonl = out / "vector_store" / "records.jsonl"
    assert vs_jsonl.exists()
    lines = vs_jsonl.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) >= 1
    rec = json.loads(lines[-1])
    assert rec.get("document_id") == "md::doc.md"
    # Metadata present
    assert isinstance(rec.get("metadata"), dict)
    # De-anonymized preview should contain original PII
    assert "alice@example.com" in rec.get("restored_preview", "")
    assert "+1 555 123 4567" in rec.get("restored_preview", "")
