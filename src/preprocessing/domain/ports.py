from __future__ import annotations

from pathlib import Path
from typing import Any, AsyncIterator, Iterable, Protocol, TYPE_CHECKING

from .models import ParsedDocument, RawDocument

if TYPE_CHECKING:  # Only for typing; avoids runtime dependency on adapters
    from ..adapters.parsers.base import BaseParser  # pragma: no cover


class IngestionPort(Protocol):
    """Source of documents.

    - ingest_batch: find all files matching globs under root and return RawDocument iterator
    - ingest_watch: stream newly created/modified files (watch mode)
    """

    def ingest_batch(self, root: Path, globs: tuple[str, ...]) -> Iterable[RawDocument]:
        ...

    async def ingest_watch(self, root: Path, globs: tuple[str, ...]) -> AsyncIterator[RawDocument]:
        ...


class ParserRegistryPort(Protocol):
    """Select a concrete parser by extension/mime."""

    def get(self, ext: str) -> "BaseParser":  # noqa: F821 (forward ref for type checkers)
        ...

    def supported(self) -> set[str]:
        ...


class OcrPort(Protocol):
    """OCR for images/PDF with low text content."""

    def run(self, input_path: Path, *, languages: tuple[str, ...]) -> str:
        ...


class EnrichmentPort(Protocol):
    """Heuristics and metadata enrichment."""

    def enrich(self, doc: ParsedDocument) -> ParsedDocument:
        ...


class LlmEnrichmentPort(Protocol):
    """Semantic enrichment (summary, keywords)."""

    def summarize(self, text: str) -> str:
        ...

    def keywords(self, text: str, top_k: int = 10) -> list[str]:
        ...


class DedupPort(Protocol):
    """Document-level duplicates detection."""

    def exists(self, content_hash: str) -> bool:
        ...

    def remember(self, content_hash: str) -> None:
        ...


class QualityPort(Protocol):
    """Quality checks for text/data."""

    def evaluate(self, doc: ParsedDocument) -> dict[str, Any]:
        ...


class SerializerPort(Protocol):
    """Output records writer."""

    def append(self, record: dict[str, Any]) -> None:
        ...

    def close(self) -> None:
        ...

