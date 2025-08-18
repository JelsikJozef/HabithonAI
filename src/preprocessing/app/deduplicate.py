from __future__ import annotations

from ..domain.ports import DedupPort


class DedupService:
    """Duplicate detection based on content hash.

    - check_or_remember(hash) -> bool: True if duplicate already exists; otherwise remember and return False.
    """

    def __init__(self, store: DedupPort) -> None:
        self._store = store

    def check_or_remember(self, content_hash: str) -> bool:
        if self._store.exists(content_hash):
            return True
        self._store.remember(content_hash)
        return False

