from __future__ import annotations

from typing import Any
from pathlib import Path

from ..domain.models import ParsedDocument
from ..domain.ports import SerializerPort


class SerializeService:
    """Responsible for writing output records via the provided sink.

    - append(doc): converts ParsedDocument to a record dict and delegates to sink.append.
    - close(): delegates to sink.close.
    """

    def __init__(self, sink: SerializerPort) -> None:
        self._sink = sink

    def append(self, doc: ParsedDocument) -> None:
        record = doc.to_record()
        # Keep summary/tags inside metadata; do not promote to top-level
        meta = record.get("metadata", {}) or {}
        # Additional fields to align with external schema while keeping tests intact
        # Path as a convenience copy alongside the structured source
        try:
            record["source_path"] = record.get("source", {}).get("path")
        except Exception:
            pass
        # Derive category from parent directory of source path if available
        try:
            sp = record.get("source_path")
            if sp:
                parent = Path(sp).parent.name
                if parent:
                    record.setdefault("category", parent)
        except Exception:
            pass
        # Subject from parsed email metadata if available
        subj = None
        try:
            subj = (meta.get("email", {}) or {}).get("subject")
        except Exception:
            subj = None
        if subj:
            record["subject"] = subj
        # Character/word counts
        counts = meta.get("counts") or {}
        if isinstance(counts, dict):
            if counts.get("chars") is not None:
                record["char_count"] = counts.get("chars")
            if counts.get("words") is not None:
                record["word_count"] = counts.get("words")
        # Derived counts from text
        text = record.get("text") or ""
        if isinstance(text, str):
            # Simple sentence count heuristic: split on . ! ?
            import re

            sentences = [s for s in re.split(r"[.!?]+\s+", text.strip()) if s]
            record.setdefault("sentence_count", len(sentences))
            # Paragraphs separated by blank lines
            paragraphs = [p for p in re.split(r"\n\s*\n", text.strip()) if p]
            record.setdefault("paragraph_count", len(paragraphs) if text.strip() else 0)
            # Lines and text size in bytes
            record.setdefault("line_count", text.count("\n") + 1 if text else 0)
            try:
                record.setdefault("size_bytes_text", len(text.encode("utf-8")))
            except Exception:
                pass
        # Token estimate mirrors tokens
        if doc.tokens is not None:
            record["token_estimate"] = doc.tokens
        # Hash convenience copy
        if doc.hash:
            record["hash_content"] = doc.hash
        # File size and timestamps
        try:
            record["size_bytes"] = record.get("source", {}).get("size")
            # mtime ISO is in source; add epoch ts convenience
            from datetime import datetime

            mtime_iso = record.get("source", {}).get("mtime")
            if isinstance(mtime_iso, str):
                try:
                    dt = datetime.fromisoformat(mtime_iso)
                    record.setdefault("modified_ts", dt.timestamp())
                    record.setdefault("created_ts", dt.timestamp())
                except Exception:
                    pass
        except Exception:
            pass
        # is_duplicate is always False for serialized docs (duplicates are skipped earlier)
        record.setdefault("is_duplicate", False)
        # requires_split: downstream can decide; default False
        record.setdefault("requires_split", False)
        self._sink.append(record)

    def close(self) -> None:
        self._sink.close()
