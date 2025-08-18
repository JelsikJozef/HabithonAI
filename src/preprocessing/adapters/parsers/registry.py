from __future__ import annotations

from typing import Mapping, Set

from ...domain.errors import ParserNotFoundError
# Accept duck-typed parsers; BaseParser is optional for typing only
try:
    from .base import BaseParser  # type: ignore
except Exception:  # pragma: no cover
    BaseParser = object  # type: ignore


class ParserRegistry:
    """Mapping-based registry: extension (lowercase, no dot) -> parser instance.

    - get(ext): return parser for normalized ext; raise ParserNotFoundError if missing.
    - supported(): return set of registered extensions.
    """

    def __init__(self, parsers: Mapping[str, object]):
        # Store with normalized keys
        self._parsers = {}
        for k, v in parsers.items():
            if not isinstance(k, str):
                raise TypeError("parser key must be a string extension")
            norm = k.lstrip(".").lower()
            if not norm:
                raise ValueError("extension key must be non-empty")
            self._parsers[norm] = v

    def get(self, ext: str):
        key = ext.lstrip(".").lower()
        if key not in self._parsers:
            raise ParserNotFoundError("No parser for extension: %s" % key)
        return self._parsers[key]

    def supported(self) -> Set[str]:
        return set(self._parsers.keys())
