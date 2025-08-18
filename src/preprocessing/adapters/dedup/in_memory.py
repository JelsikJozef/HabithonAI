from __future__ import annotations

from ...domain.ports import DedupPort


class InMemoryDedup(DedupPort):
    """Trivial in-memory deduplication store backed by a set of hashes."""

    def __init__(self) -> None:
        self._seen = set()

    def exists(self, content_hash: str) -> bool:
        return content_hash in self._seen

    def remember(self, content_hash: str) -> None:
        self._seen.add(content_hash)
