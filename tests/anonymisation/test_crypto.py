import os
import sys
import unittest
from typing import Any

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from anonymization.adapters.token_vault.postgres_store import PostgresTokenVault


class _FakeCursor:
    def __init__(self, store: dict):
        self.store = store
        self._last = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def execute(self, sql: str, params: tuple | None = None):        # Very naive SQL dispatcher just based on verbs
        s = sql.strip().lower()
        if s.startswith("create table"):
            # ignore schema creation
            return
        if s.startswith("insert into"):
            tenant_id, token_id, pii_type, value_enc = params
            key = (tenant_id or "", token_id)
            if key not in self.store:
                self.store[key] = (pii_type, bytes(value_enc))
            return
        if s.startswith("select"):
            tenant_id, token_id = params
            key = (tenant_id or "", token_id)
            if key in self.store:
                pii_type, val = self.store[key]
                self._last = (val,)
            else:
                self._last = None
            return
        # otherwise ignore

    def fetchone(self):
        return self._last


class _FakeConn:
    def __init__(self, store: dict):
        self.store = store

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def cursor(self) -> Any:
        return _FakeCursor(self.store)

    def close(self):
        pass


class TestPostgresStore(unittest.TestCase):
    def setUp(self):
        self._store = {}
        self.vault = PostgresTokenVault(conn_factory=lambda: _FakeConn(self._store))
        self.vault.ensure_schema()

    def test_upsert_and_lookup(self):
        tenant = "tenantA"
        tok = "t:kidX:abc123"
        self.vault.upsert(tok, tenant, "EMAIL", "alice@example.com")
        # idempotent
        self.vault.upsert(tok, tenant, "EMAIL", "alice@example.com")
        val = self.vault.lookup(tok, tenant)
        self.assertEqual(val, "alice@example.com")

    def test_tenants_isolated(self):
        tok = "t:kidX:abc123"
        self.vault.upsert(tok, "tenantA", "EMAIL", "a@x.com")
        self.vault.upsert(tok, "tenantB", "EMAIL", "b@x.com")
        self.assertEqual(self.vault.lookup(tok, "tenantA"), "a@x.com")
        self.assertEqual(self.vault.lookup(tok, "tenantB"), "b@x.com")
        self.assertIsNone(self.vault.lookup("t:kidX:zzz", "tenantA"))


if __name__ == '__main__':
    unittest.main()
import os
import sys
import unittest

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from anonymization.adapters.crypto.crypto import Crypto


class TestCrypto(unittest.TestCase):
    def setUp(self):
        # Two versioned keys for rotation tests
        self.keyset = {
            "active_kid": "kidA",
            "keys": {
                "kidA": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",  # 32 zero bytes
                "kidB": "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE=",  # 32 x 0x01
            },
        }
        os.environ["ANON_KEYSET"] = __import__("json").dumps(self.keyset)
        self.crypto = Crypto()

    def tearDown(self):
        os.environ.pop("ANON_KEYSET", None)

    def test_mask_preserves_shape(self):
        s = "Alice-123_ Z"
        masked = self.crypto.mask(s)
        self.assertEqual(len(s), len(masked))
        self.assertEqual(masked, "xxxxx-999_ x")

    def test_hash_is_stable_and_prefixed(self):
        h1 = self.crypto.hash("secret", tenant_id="t1")
        h2 = self.crypto.hash("secret", tenant_id="t1")
        self.assertEqual(h1, h2)
        self.assertTrue(h1.startswith("h:kidA:"))
        self.assertNotIn("secret", h1)

    def test_hash_changes_with_tenant_or_kid(self):
        h1 = self.crypto.hash("secret", tenant_id="t1")
        h2 = self.crypto.hash("secret", tenant_id="t2")
        self.assertNotEqual(h1, h2)
        # rotate active key
        ks = self.keyset.copy()
        ks["active_kid"] = "kidB"
        os.environ["ANON_KEYSET"] = __import__("json").dumps(ks)
        h3 = self.crypto.hash("secret", tenant_id="t1")
        self.assertNotEqual(h1, h3)
        self.assertTrue(h3.startswith("h:kidB:"))

    def test_tokenize_is_stable_and_prefixed(self):
        t1 = self.crypto.tokenize("alice@example.com", tenant_id="tenantX")
        t2 = self.crypto.tokenize("alice@example.com", tenant_id="tenantX")
        self.assertEqual(t1, t2)
        self.assertTrue(t1.startswith("t:kidA:"))


if __name__ == "__main__":
    unittest.main()
