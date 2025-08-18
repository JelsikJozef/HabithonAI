from __future__ import annotations

from abc import ABC, abstractmethod

from ...domain.models import ParsedDocument, RawDocument


class BaseParser(ABC):
    """Base parser interface.

    Contract:
    - parse(raw) -> ParsedDocument
    - May read the input file referenced by raw.path to extract text and basic metadata.
    - Must not perform any external I/O beyond reading the file; avoid logging input data contents.
    """

    @abstractmethod
    def parse(self, raw: RawDocument) -> ParsedDocument:  # pragma: no cover (interface only)
        """Extract text and minimal metadata from the given raw document."""
        raise NotImplementedError

