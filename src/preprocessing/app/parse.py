from __future__ import annotations

from typing import Iterable

from ..domain.errors import ParserNotFoundError
from ..domain.models import ParsedDocument, RawDocument
from ..domain.ports import ParserRegistryPort


class ParseService:
    """Parser selection and conversion to ParsedDocument.

    - Delegates parsing to a concrete parser selected by file extension.
    - Raises ParserNotFoundError when no parser is registered for the extension.
    """

    def __init__(self, registry: ParserRegistryPort) -> None:
        self._registry = registry

    def parse_one(self, raw: RawDocument) -> ParsedDocument:
        ext = raw.ext.lower()
        # Prefer using registry.supported() to decide; avoids coupling to registry errors.
        if hasattr(self._registry, "supported") and ext not in self._registry.supported():
            raise ParserNotFoundError(f"No parser for extension: {ext}")
        try:
            parser = self._registry.get(ext)
        except (KeyError, LookupError) as e:
            raise ParserNotFoundError(f"No parser for extension: {ext}") from e
        # Expect parser to provide parse(raw) -> ParsedDocument
        return parser.parse(raw)

    def parse_many(self, raws: Iterable[RawDocument]) -> Iterable[ParsedDocument]:
        def _gen() -> Iterable[ParsedDocument]:
            for r in raws:
                # No logging here, let exceptions propagate as requested.
                yield self.parse_one(r)
        return _gen()
