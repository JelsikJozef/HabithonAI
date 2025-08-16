"""Versioned key access with tenant-scoped derivation.

API:
- load_keyset() -> KeySet
- get_hmac_key(tenant_id: str | None) -> tuple[kid, key_bytes]
- get_all_hmac_keys(tenant_id: str | None) -> dict[kid, key_bytes]

Key source:
- Environment variable ANON_KEYSET containing JSON: {"active_kid": "kid1", "keys": {"kid1": "base64", ...}}
- If not set, falls back to a deterministic built-in keyset for tests only.

Constraints:
- No logging/printing of keys.
- Deterministic per tenant.
- Supports rotation by honoring active_kid and keeping previous keys.
"""
from __future__ import annotations

import base64
import dataclasses
import hashlib
import hmac
import json
import os
from typing import Dict, Tuple, Optional


@dataclasses.dataclass(frozen=True)
class KeySet:
    active_kid: str
    keys: Dict[str, bytes]


_ENV_VAR = "ANON_KEYSET"


def _default_keyset() -> KeySet:
    # Deterministic, insecure default for tests only
    keys = {
        "kid0": b"\x00" * 32,
    }
    return KeySet(active_kid="kid0", keys=keys)


def _load_env_keyset() -> Optional[KeySet]:
    s = os.getenv(_ENV_VAR)
    if not s:
        return None
    obj = json.loads(s)
    active = obj["active_kid"]
    raw_keys = obj["keys"]
    keys: Dict[str, bytes] = {}
    for kid, b64 in raw_keys.items():
        keys[kid] = base64.b64decode(b64)
    return KeySet(active_kid=active, keys=keys)


def load_keyset() -> KeySet:
    ks = _load_env_keyset()
    if ks is None:
        ks = _default_keyset()
    return ks


def _derive_subkey(master: bytes, tenant_id: Optional[str]) -> bytes:
    """Derive tenant-scoped subkey deterministically using HMAC-SHA256.

    subkey = HMAC(master, b"tenant:" + tenant + b"\x00", SHA256)
    """
    tenant = (tenant_id or "").encode("utf-8")
    mac = hmac.new(master, digestmod=hashlib.sha256)
    mac.update(b"tenant:")
    mac.update(tenant)
    mac.update(b"\x00")
    return mac.digest()


def get_hmac_key(tenant_id: Optional[str] = None) -> Tuple[str, bytes]:
    ks = load_keyset()
    active = ks.active_kid
    master = ks.keys[active]
    return active, _derive_subkey(master, tenant_id)


def get_all_hmac_keys(tenant_id: Optional[str] = None) -> Dict[str, bytes]:
    ks = load_keyset()
    return {kid: _derive_subkey(k, tenant_id) for kid, k in ks.keys.items()}

