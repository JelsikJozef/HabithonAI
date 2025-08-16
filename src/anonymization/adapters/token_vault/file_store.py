import json
import os
from typing import Iterable, List
from ...domain.entities import TokenMapping
from ...domain.ports import TokenVaultPort
from ...domain.errors import TokenVaultError


class FileTokenVault(TokenVaultPort):
    """Stores token mappings per context_id in JSON files under a base directory."""

    def __init__(self, base_dir: str = ".anonymization_vault") -> None:
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    def _ctx_path(self, context_id: str) -> str:
        safe = "".join(c for c in context_id if c.isalnum() or c in ("-", "_"))
        return os.path.join(self.base_dir, f"{safe}.json")

    def save_mappings(self, context_id: str, mappings: Iterable[TokenMapping]) -> None:
        path = self._ctx_path(context_id)
        try:
            # Merge with existing mappings, de-duplicating by token
            existing: List[TokenMapping] = []
            if os.path.exists(path):
                with open(path, "r", encoding="utf-8") as f:
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

    def get_mappings(self, context_id: str) -> List[TokenMapping]:
        path = self._ctx_path(context_id)
        if not os.path.exists(path):
            return []
        try:
            with open(path, "r", encoding="utf-8") as f:
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
