import os
import sys
import json
import base64
import unittest

# Ensure src/ is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', 'src')))

from anonymization.adapters.crypto import key_manager


class TestKeyManager(unittest.TestCase):
    def setUp(self):
        keys = {
            "kidA": base64.b64encode(b"A" * 32).decode("ascii"),
            "kidB": base64.b64encode(b"B" * 32).decode("ascii"),
        }
        self.keyset = {"active_kid": "kidA", "keys": keys}
        os.environ["ANON_KEYSET"] = json.dumps(self.keyset)

    def tearDown(self):
        os.environ.pop("ANON_KEYSET", None)

    def test_active_kid_honored(self):
        kid, key = key_manager.get_hmac_key(tenant_id="t1")
        self.assertEqual(kid, "kidA")
        # rotate
        ks = dict(self.keyset)
        ks["active_kid"] = "kidB"
        os.environ["ANON_KEYSET"] = json.dumps(ks)
        kid2, key2 = key_manager.get_hmac_key(tenant_id="t1")
        self.assertEqual(kid2, "kidB")
        self.assertNotEqual(key, key2)

    def test_same_tenant_same_key(self):
        _, k1 = key_manager.get_hmac_key(tenant_id="tenant-1")
        _, k2 = key_manager.get_hmac_key(tenant_id="tenant-1")
        self.assertEqual(k1, k2)

    def test_different_tenant_different_key(self):
        _, k1 = key_manager.get_hmac_key(tenant_id="t1")
        _, k2 = key_manager.get_hmac_key(tenant_id="t2")
        self.assertNotEqual(k1, k2)

    def test_rotation_preserves_old_keys(self):
        all_before = key_manager.get_all_hmac_keys(tenant_id="t1")
        self.assertIn("kidA", all_before)
        self.assertIn("kidB", all_before)
        ks = dict(self.keyset)
        ks["active_kid"] = "kidB"
        os.environ["ANON_KEYSET"] = json.dumps(ks)
        all_after = key_manager.get_all_hmac_keys(tenant_id="t1")
        self.assertIn("kidA", all_after)
        self.assertIn("kidB", all_after)
        self.assertEqual(set(all_before.keys()), set(all_after.keys()))


if __name__ == "__main__":
    unittest.main()
