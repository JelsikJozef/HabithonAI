import json
import os
from collections.abc import Iterable

from ...domain.entities import TokenMapping
from ...domain.errors import TokenVaultError
from ...domain.ports import TokenVaultPort

# Single canonical on-disk location for the Token Vault. Runtime data lives under
# outputs/ next to outputs/artifacts/ (which context_id is linked to), not inside src/.
# Override via the ANON_VAULT_DIR environment variable.
DEFAULT_VAULT_DIR = "outputs/anonymization_vault"


class FileTokenVault(TokenVaultPort):
    """Stores token mappings per context_id in JSON files under a base directory."""

    def __init__(self, base_dir: str = DEFAULT_VAULT_DIR) -> None:
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _ctx_path(self, context_id: str) -> str:
        safe = "".join(c for c in context_id if c.isalnum() or c in ("-", "_"))
        return os.path.join(self.base_dir, f"{safe}.json")

    def save_mappings(self, context_id: str, mappings: Iterable[TokenMapping]) -> None:
        path = self._ctx_path(context_id)
        try:
            # Merge with existing mappings, de-duplicating by token
            existing: list[TokenMapping] = []
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
                    existing = [TokenMapping(**d) for d in data]
            by_token = {m.token: m for m in existing}
            for m in mappings:
                by_token[m.token] = m
            data_out = [m.__dict__ for m in by_token.values()]
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data_out, f, ensure_ascii=False, indent=2)
        except Exception as e:
            raise TokenVaultError(f"Failed to save mappings for {context_id}: {e}")

    def get_mappings(self, context_id: str) -> list[TokenMapping]:
        path = self._ctx_path(context_id)
        if not os.path.exists(path):
            return []
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            return [TokenMapping(**d) for d in data]
        except Exception as e:
            raise TokenVaultError(f"Failed to load mappings for {context_id}: {e}")

    def clear_context(self, context_id: str) -> None:
        path = self._ctx_path(context_id)
        try:
            if os.path.exists(path):
                os.remove(path)
        except Exception as e:
            raise TokenVaultError(f"Failed to clear context {context_id}: {e}")
