from anonymization.app.denomize import deanonymize
from anonymization.domain.entities import TokenMapping
from anonymization.adapters.token_vault.file_store import FileTokenVault


def test_deanonymize_replaces_longest_tokens_first(tmp_path):
    vault = FileTokenVault(base_dir=str(tmp_path / "vault"))
    ctx = "ctx-ol"
    # Two tokens where one is a substring of the other
    long_tok = "{{PII:NAME:1:abcdef12}}"
    short_tok = "{{PII:NAME:1:abc}}"
    text = f"Hello {long_tok} and {short_tok}!"

    # Save mappings in vault
    vault.save_mappings(
        ctx,
        [
            TokenMapping(token=long_tok, value="Alice", type="PERSON"),
            TokenMapping(token=short_tok, value="Al", type="PERSON"),
        ],
    )

    res = deanonymize(text, vault, context_id=ctx)
    assert res.restored_text == "Hello Alice and Al!"
