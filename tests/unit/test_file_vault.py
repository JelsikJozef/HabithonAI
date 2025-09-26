from anonymization.adapters.token_vault.file_store import FileTokenVault
from anonymization.domain.entities import TokenMapping


def test_file_vault_save_get_clear(tmp_path):
    vault = FileTokenVault(base_dir=str(tmp_path / "vault"))
    ctx = "ctx-file-1"
    maps = [
        TokenMapping(token="tok1", value="Alice", type="PERSON"),
        TokenMapping(token="tok2", value="bob@example.com", type="EMAIL"),
    ]
    vault.save_mappings(ctx, maps)

    loaded = vault.get_mappings(ctx)
    assert {m.token for m in loaded} == {"tok1", "tok2"}
    assert {m.value for m in loaded} == {"Alice", "bob@example.com"}

    # Save duplicate token with different value should keep last one
    vault.save_mappings(ctx, [TokenMapping(token="tok1", value="Alice A.", type="PERSON")])
    loaded2 = vault.get_mappings(ctx)
    val_map = {m.token: m.value for m in loaded2}
    assert val_map["tok1"] == "Alice A."

    vault.clear_context(ctx)
    assert vault.get_mappings(ctx) == []
