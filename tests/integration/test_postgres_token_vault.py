import sqlite3
import pytest

from anonymization.adapters.token_vault.postgres_store import PostgresTokenVault
from anonymization.domain.entities import TokenMapping
from anonymization.domain.errors import TokenVaultError


@pytest.fixture
def sqlite_vault():
    # Use in-memory sqlite to simulate DB-API connection
    def conn_factory():
        conn = sqlite3.connect(":memory:")
        # Enable returning rows as tuples
        return conn

    vault = PostgresTokenVault(conn_factory=conn_factory, table="test_pii_tokens")
    vault.ensure_schema()
    return vault


def test_save_and_get_mappings(sqlite_vault):
    vault = sqlite_vault
    # Save mappings
    mappings = [
        TokenMapping(token="t1", value="Alice", type="PERSON"),
        TokenMapping(token="t2", value="Bob", type="PERSON"),
    ]
    vault.save_mappings("ctx", mappings)
    # Retrieve mappings
    loaded = vault.get_mappings("ctx")
    tokens = {m.token for m in loaded}
    assert tokens == {"t1", "t2"}
    # Ensure stored values
    val_map = {m.token: m.value for m in loaded}
    assert val_map["t1"] == "Alice"
    assert val_map["t2"] == "Bob"


def test_clear_context(sqlite_vault):
    vault = sqlite_vault
    mappings = [TokenMapping(token="t3", value="Charlie", type="PERSON")]
    vault.save_mappings("ctx2", mappings)
    assert vault.get_mappings("ctx2")
    # Clear
    vault.clear_context("ctx2")
    assert vault.get_mappings("ctx2") == []


def test_error_on_invalid_save(sqlite_vault, monkeypatch):
    # Force error in upsert
    vault = sqlite_vault
    monkeypatch.setattr(
        vault, "upsert", lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("fail"))
    )
    with pytest.raises(TokenVaultError):
        vault.save_mappings("ctx", [TokenMapping(token="x", value="V", type="T")])
