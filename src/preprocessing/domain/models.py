from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class RawDocument:
    """Represents a raw input file before parsing.

    Invariants:
    - size >= 0
    - ext is lowercase and without leading dot
    """

    path: Path
    size: int
    mtime: datetime
    ext: str
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:  # type: ignore[override]
        # Validate size
        if self.size < 0:
            raise ValueError("size must be >= 0")
        # Normalize extension to lowercase without leading dot
        normalized = self.ext.lstrip(".").lower()
        if normalized != self.ext:
            object.__setattr__(self, "ext", normalized)

    def as_dict(self) -> dict[str, Any]:
        """Serialize to a plain dict (no file contents)."""
        return {
            "path": str(self.path),
            "size": self.size,
            "mtime": self.mtime.isoformat(),
            "ext": self.ext,
            "meta": dict(self.meta),
        }

    def with_meta(self, **kwargs: Any) -> RawDocument:
        """Return an immutable copy with merged metadata."""
        new_meta = {**self.meta, **kwargs}
        return replace(self, meta=new_meta)


@dataclass(frozen=True, slots=True)
class ParsedDocument:
    """Text result of parsing/OCR/normalization with derived info."""

    text: str
    source: RawDocument
    charset: str | None = None
    language: str | None = None
    hash: str | None = None
    tokens: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:  # type: ignore[override]
        if self.tokens is not None and self.tokens < 0:
            raise ValueError("tokens must be >= 0 when provided")

    def short_text(self, limit: int = 120) -> str:
        """Return a preview of text (trim + ellipsis).

        Semantics:
        - If text length <= limit, return stripped text.
        - If trimming, ensure at least `limit` visible chars; if the cut falls inside a word,
          extend to the end of that word (word boundary) before adding ellipsis.
        """
        if limit <= 0:
            return ""
        s = self.text.strip()
        if len(s) <= limit:
            return s
        cut = max(0, limit)
        # If we're in the middle of a word, extend to the end of the word
        if cut > 0 and cut < len(s):
            is_word_char = str.isalnum
            if is_word_char(s[cut - 1]) and is_word_char(s[cut]):
                i = cut
                while i < len(s) and is_word_char(s[i]):
                    i += 1
                cut = i
        return s[:cut].rstrip() + "..."

    def to_record(self) -> dict[str, Any]:
        """Return a complete record suitable for JSONL output."""
        return {
            "text": self.text,
            "source": self.source.as_dict(),
            "charset": self.charset,
            "language": self.language,
            "hash": self.hash,
            "tokens": self.tokens,
            "metadata": dict(self.metadata),
        }
