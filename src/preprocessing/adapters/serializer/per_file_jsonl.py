from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import logging

from ...domain.errors import SerializationError
from ...domain.ports import SerializerPort

logger = logging.getLogger(__name__)


class PerFileJsonlSerializer(SerializerPort):
    """Writes one JSON object per file into a directory (1→1 mapping).

    - append(record) writes/overwrites <base_dir>/<hash>.jsonl with a single JSON line.
    - If 'hash' is missing in record, uses 'hash_content', else falls back to SHA-1 of text.
    - Transforms record to the sample schema: top-level summary/tags, 'source' as string, PII fields at top-level.
    - Removes nested metadata to avoid duplication after promotion.
    """

    def __init__(self, base_dir: Path, *, ensure_ascii: bool = False) -> None:
        if not isinstance(base_dir, Path):
            raise TypeError("base_dir must be a pathlib.Path")
        self._dir = base_dir
        self._ensure_ascii = bool(ensure_ascii)
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            raise SerializationError("Failed to create output directory: %s" % e)

    def _hash_for(self, record: dict[str, Any]) -> str:
        h = record.get("hash") or record.get("hash_content")
        if isinstance(h, str) and h:
            return h
        # Fallback: compute from text
        try:
            import hashlib
            txt = record.get("text") or ""
            return hashlib.sha1(txt.encode("utf-8", errors="ignore")).hexdigest()
        except Exception:
            return "unknown"

    def _transform(self, record: dict[str, Any]) -> dict[str, Any]:
        # Make a shallow copy
        rec = dict(record)
        meta = rec.get("metadata") or {}
        if not isinstance(meta, dict):
            meta = {}
        # Promote summary/tags to top-level
        if "summary" in meta:
            rec["summary"] = meta.get("summary")
        tags = meta.get("tags") or meta.get("keywords")
        if tags:
            rec["tags"] = list(tags)
        # Convert source object to string path
        src = rec.get("source")
        if isinstance(src, dict):
            path = src.get("path")
            if isinstance(path, str) and path:
                rec["source"] = path
        # PII fields to top-level
        if "pii_entities" in meta and isinstance(meta.get("pii_entities"), list):
            rec["pii_entities"] = meta.get("pii_entities")
        if "pii_count" in meta and isinstance(meta.get("pii_count"), int):
            rec["pii_count"] = meta.get("pii_count")
        # Compute pii_risk if possible
        try:
            cnt = int(rec.get("pii_count") or 0)
            words = int(rec.get("word_count") or 0)
            if words > 0:
                risk = round(cnt / float(words), 4)
                rec["pii_risk"] = risk
        except Exception:
            pass
        # Remove nested metadata to avoid duplication
        if "metadata" in rec:
            del rec["metadata"]
        # Drop redundant convenience duplicates if present
        rec.pop("source_path", None)
        # Keep only hash_content (not plain 'hash')
        if rec.get("hash") and rec.get("hash_content"):
            rec.pop("hash", None)
        return rec

    def append(self, record: dict[str, Any]) -> None:
        rec = self._transform(record)
        try:
            line = json.dumps(rec, ensure_ascii=self._ensure_ascii)
        except Exception as e:
            raise SerializationError("Failed to serialize record to JSON: %s" % e)
        h = self._hash_for(rec)
        out_path = self._dir / f"{h}.jsonl"
        try:
            out_path.write_text(line + "\n", encoding="utf-8")
            logger.info("Wrote JSONL: %s", out_path)
        except Exception as e:
            raise SerializationError("Failed to write JSONL file: %s" % e)

    def close(self) -> None:
        return
