from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, Iterable
import uuid

from ..domain.models import RawDocument
from ..domain.ports import IngestionPort


class IngestionService:
    """Orchestrates batch and watch ingestion.

    - No direct I/O; delegates to the provided IngestionPort.
    - Validates inputs for both modes.
    - Enriches batch items with a shared batch_id metadata.
    - Watch mode yields items as-is (retry/backoff handled by adapter).
    """

    def __init__(self, ingestor: IngestionPort) -> None:
        self._ingestor = ingestor

    def ingest_batch(self, root: Path, globs: tuple[str, ...]) -> Iterable[RawDocument]:
        """Validate inputs, delegate to port, and enrich documents with batch metadata.

        Returns a lazy iterator that yields RawDocument with an added batch_id in meta.
        """
        self._validate_args(root, globs)
        batch_id = str(uuid.uuid4())

        def _generator() -> Iterable[RawDocument]:
            for doc in self._ingestor.ingest_batch(root, globs):
                if not isinstance(doc, RawDocument):
                    raise TypeError("ingestor yielded non-RawDocument")
                yield doc.with_meta(batch_id=batch_id)

        return _generator()

    async def ingest_watch(self, root: Path, globs: tuple[str, ...]) -> AsyncIterator[RawDocument]:
        """Delegate to port and stream documents; adapter handles resilience/retries."""
        self._validate_args(root, globs)
        async for doc in self._ingestor.ingest_watch(root, globs):
            yield doc

    @staticmethod
    def _validate_args(root: Path, globs: tuple[str, ...]) -> None:
        if not isinstance(root, Path):
            raise TypeError("root must be a pathlib.Path")
        if not isinstance(globs, tuple):
            raise TypeError("globs must be a tuple[str, ...]")
        if not globs:
            raise ValueError("globs must not be empty")
        for g in globs:
            if not isinstance(g, str):
                raise TypeError("globs must contain strings")
            if not g.strip():
                raise ValueError("glob patterns must be non-empty strings")

