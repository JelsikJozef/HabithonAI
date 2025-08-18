from __future__ import annotations

from dataclasses import replace
import hashlib
import math
import re
from typing import Callable, Optional

from ...domain.models import ParsedDocument
from ...domain.ports import EnrichmentPort


class MetadataEnricher(EnrichmentPort):
    """Heuristic metadata enrichment.

    - language: via provided lang_detector(text)->str or simple heuristic ("sk" vs "en" or None).
    - hash: SHA-256 of text content as hex string.
    - tokens: via provided token_counter(text)->int or whitespace token count.
    - counts: chars, words; reading_time_sec estimated at ~200 wpm.
    - metadata: merged with {"enriched": True} and counts.
    """

    def __init__(
        self,
        lang_detector: Optional[Callable[[str], str]] | None = None,
        token_counter: Optional[Callable[[str], int]] | None = None,
    ) -> None:
        self._lang_detector = lang_detector
        self._token_counter = token_counter

    def enrich(self, doc: ParsedDocument) -> ParsedDocument:
        text = doc.text or ""
        # language
        language = self._detect_language(text)
        # hash
        content_hash = hashlib.sha256(text.encode("utf-8", errors="ignore")).hexdigest()
        # tokens/words
        tokens = self._count_tokens(text)
        # words (alphanumeric sequences)
        words = len(re.findall(r"\w+", text, flags=re.UNICODE))
        # chars
        chars = len(text)
        # reading time (approx 200 wpm)
        reading_time_sec = int(math.ceil(words / (200 / 60))) if words else 0
        # merge metadata
        meta = dict(doc.metadata)
        meta.update({
            "enriched": True,
            "counts": {"chars": chars, "words": words},
            "reading_time_sec": reading_time_sec,
        })
        return replace(
            doc,
            language=language,
            hash=content_hash,
            tokens=tokens,
            metadata=meta,
        )

    def _detect_language(self, text: str) -> Optional[str]:
        if self._lang_detector is not None:
            try:
                lang = self._lang_detector(text)
                if isinstance(lang, str) and lang:
                    return lang
            except Exception:
                pass
        # Simple heuristic: presence of typical Slovak diacritics -> 'sk', else 'en' if ASCII heavy
        if any(ch in text for ch in "áäčďĺľňóôŕšťúýžÁÄČĎĹĽŇÓÔŔŠŤÚÝŽ"):
            return "sk"
        # If majority of characters are ASCII letters/spaces, assume English
        letters = sum(c.isalpha() for c in text)
        ascii_letters = sum(('A' <= c <= 'Z') or ('a' <= c <= 'z') for c in text)
        if letters and ascii_letters / max(letters, 1) > 0.85:
            return "en"
        return None

    def _count_tokens(self, text: str) -> int:
        if self._token_counter is not None:
            try:
                n = self._token_counter(text)
                if isinstance(n, int) and n >= 0:
                    return n
            except Exception:
                pass
        return len(text.split())

