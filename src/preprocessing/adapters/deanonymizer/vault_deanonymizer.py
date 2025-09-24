# filepath: /Users/jozefjelsik/PycharmProjects/HabithonAI/src/preprocessing/adapters/deanonymizer/vault_deanonymizer.py
from __future__ import annotations

from typing import Any

from ...domain.ports import DeAnonymizer


class VaultDeAnonymizer(DeAnonymizer):
    """Restore original values using the anonymization token vault.

    Uses anonymization.adapters.container.build_default() to get the vault and resolves
    token->value mappings for a context_id. Replacement is done deterministically by
    replacing longest tokens first to avoid partial overlaps.
    """

    def __init__(self, *, vault: Any | None = None) -> None:
        if vault is None:
            try:
                from anonymization.adapters.container import build_default as _build  # type: ignore

                _det, vlt = _build()
                vault = vlt
            except Exception as e:  # pragma: no cover
                raise RuntimeError(f"Token vault unavailable: {e}")
        self._vault = vault

    def restore(self, anonymized_text: str, *, context_id: str) -> str:
        try:
            maps = self._vault.get_mappings(context_id)  # type: ignore[attr-defined]
        except Exception as e:  # pragma: no cover
            raise RuntimeError(f"Vault access failed: {e}")
        # Normalize into list of (token, value)
        pairs: list[tuple[str, str]] = []
        for m in maps or []:
            try:
                tok = m.token if hasattr(m, "token") else m.get("token")
                val = m.value if hasattr(m, "value") else m.get("value")
                if tok is None or val is None:
                    continue
                pairs.append((str(tok), str(val)))
            except Exception:
                continue
        # Replace longest tokens first to minimize accidental partials
        pairs.sort(key=lambda t: len(t[0]), reverse=True)
        out = str(anonymized_text)
        for tok, val in pairs:
            if not tok:
                continue
            out = out.replace(tok, val)
        return out
