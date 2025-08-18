from __future__ import annotations

import re
import unicodedata
from dataclasses import replace
from typing import Dict, Any

from ..domain.models import ParsedDocument


class NormalizeService:
    """Text cleaning pipeline with configurable steps.

    Steps order:
    1) collapse whitespace
    2) Unicode NFKC normalization
    3) strip headers/footers (heuristics)
    """

    def normalize(
        self,
        doc: ParsedDocument,
        *,
        collapse_ws: bool = True,
        unicode_nfkc: bool = True,
        strip_headers: bool = True,
    ) -> ParsedDocument:
        text = doc.text
        if collapse_ws:
            text = self._collapse_ws(text)
        if unicode_nfkc:
            text = self._nfkc(text)
        if strip_headers:
            text = self._strip_headers(text)

        # Update metadata flags under a namespaced key
        flags: Dict[str, Any] = {
            "collapse_ws": bool(collapse_ws),
            "unicode_nfkc": bool(unicode_nfkc),
            "strip_headers": bool(strip_headers),
        }
        new_meta = {**doc.metadata, "normalized": flags}
        return replace(doc, text=text, metadata=new_meta)

    @staticmethod
    def _collapse_ws(text: str) -> str:
        """Collapse horizontal whitespace but preserve newlines.

        - Replaces runs of spaces/tabs/form-feeds/vertical-tabs with a single space.
        - Leaves line breaks (\n, \r\n) intact so downstream line-based heuristics work.
        """
        # First, replace horizontal whitespace runs with single space
        text = re.sub(r"[ \t\f\v]+", " ", text)
        # Trim trailing spaces before newlines
        text = re.sub(r"[ \t\f\v]+(?=\r?\n)", "", text)
        # Also trim leading spaces at start of lines
        text = re.sub(r"(?m)^[ \t\f\v]+", "", text)
        return text

    @staticmethod
    def _strip_headers(text: str) -> str:
        """Remove common header/footer lines (simple heuristics).

        Heuristics:
        - Drop lines like "Page 1" or "Page 1/10" (case-insensitive)
        - Drop lines consisting mostly of punctuation (e.g., "---", "____")
        """
        lines = text.splitlines()
        out: list[str] = []
        page_re = re.compile(r"^\s*page\s+\d+(\s*/\s*\d+)?\s*$", re.IGNORECASE)
        punct_re = re.compile(r"^[\W_]{3,}$")
        for ln in lines:
            if page_re.match(ln) or punct_re.match(ln):
                continue
            out.append(ln)
        return "\n".join(out)

    @staticmethod
    def _nfkc(text: str) -> str:
        """Apply Unicode NFKC normalization."""
        return unicodedata.normalize("NFKC", text)
