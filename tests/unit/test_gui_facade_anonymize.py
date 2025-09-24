from gui.services.facade import GuiServices


def test_gui_facade_deterministic_anonymize(tmp_path, monkeypatch):
    # Force regex detector and isolate vault dir
    monkeypatch.setenv("ANON_DETECTORS", "regex")
    vault_dir = tmp_path / "vaultg"
    monkeypatch.setenv("ANON_VAULT_DIR", str(vault_dir))

    svc = GuiServices()
    text = "Email alice@example.com"
    res1 = svc.anon_anonymize(text, context_id="ctxG", tenant_id="acme", language="en")
    res2 = svc.anon_anonymize(text, context_id="ctxG", tenant_id="acme", language="en")

    # Anonymized output should contain deterministic hash token prefix
    anon = res1.get("anonymized_text", "")
    assert "h:" in anon
    # Mapping token stable across calls for same input/tenant
    t1 = res1["mappings"][0]["token"] if res1.get("mappings") else None
    t2 = res2["mappings"][0]["token"] if res2.get("mappings") else None
    assert t1 == t2 and t1 is not None

    # Vault file should be created
    files = list(vault_dir.glob("*.json"))
    assert files, "Vault did not create any context file"
