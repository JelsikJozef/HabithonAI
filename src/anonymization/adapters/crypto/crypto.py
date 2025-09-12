"""Deterministic, side-effect free crypto adapter.

Implements anonymization.domain.ports.Crypto

Constraints:
- No I/O here. Uses key_manager for keys.
- Deterministic and thread-safe.
- No logging of secrets.
"""

from __future__ import annotations

import base64
import hashlib
import hmac

from . import key_manager

PREFIX_HASH = "h"
PREFIX_TOKEN = "t"


def _normalize_tenant(tenant_id: str | None) -> bytes:
    return (tenant_id or "").encode("utf-8")


class Crypto:
    def mask(self, text: str) -> str:
        """Redact preserving length and separators.

        Letters -> 'x', digits -> '9', others unchanged.
        """
        out_chars = []
        for ch in text:
            if ch.isalpha():
                out_chars.append("x")
            elif ch.isdigit():
                out_chars.append("9")
            else:
                out_chars.append(ch)
        return "".join(out_chars)

    def hash(self, text: str, *, tenant_id: str | None = None) -> str:
        """Non-reversible, deterministic per (active_kid, tenant_id, text).

        Returns: h:<kid>:<hex>
        """
        kid, key = key_manager.get_hmac_key(tenant_id)
        mac = hmac.new(key, digestmod=hashlib.sha256)
        mac.update(_normalize_tenant(tenant_id))
        mac.update(b"\x00")
        mac.update(text.encode("utf-8"))
        digest_hex = mac.hexdigest()
        return f"{PREFIX_HASH}:{kid}:{digest_hex}"

    def tokenize(self, text: str, *, tenant_id: str | None = None) -> str:
        """Deterministic token per (active_kid, tenant_id, text).

        Returns: t:<kid>:<id>
        """
        kid, key = key_manager.get_hmac_key(tenant_id)
        mac = hmac.new(key, digestmod=hashlib.sha256)
        mac.update(b"tok")
        mac.update(_normalize_tenant(tenant_id))
        mac.update(b"\x00")
        mac.update(text.encode("utf-8"))
        # Stable string id, shorter than full hex: base32 without padding, lowercase
        tok_id = base64.b32encode(mac.digest()).decode("ascii").rstrip("=").lower()
        return f"{PREFIX_TOKEN}:{kid}:{tok_id}"
