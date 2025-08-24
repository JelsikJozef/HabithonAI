"""
Consistent, contextual identifiers for documents, contexts, and vector point IDs.

Purpose
- Centralize identifier conventions so every layer uses the same shapes and rules.

Generated identifiers
- doc_id: primary identifier for a document (typically derived from hashing.py)
- context_id: TokenVault scope tying pseudonymization to a document variant
- point_id: stable Qdrant point IDs for chunks

Conventions
- Variant suffixing: embed the variant into IDs so contexts never collide (docid:en, docid:orig)
- Human-safe characters: restrict to [a-z0-9-_:.]
- Length bounds: default max length 128 bytes
- Deterministic ordering: chunk indices are sequential and stable

Versioning
- If you rotate formats, you can prefix IDs (e.g., v2_) using the 'version' parameter.
"""
from __future__ import annotations

from typing import Optional
import hashlib
import re

from .hashing import normalize_text, make_document_id

SAFE_CHARS = set("abcdefghijklmnopqrstuvwxyz0123456789-_.:")
MAX_ID_LEN_DEFAULT = 128

__all__ = [
    "to_safe",
    "trim_id",
    "doc_id_from_text",
    "ensure_doc_id",
    "context_id",
    "point_id",
]


def to_safe(value: str) -> str:
    """Lowercase and map invalid characters to '-'; collapse consecutive '-' and strip.

    Allowed chars: [a-z0-9-_.:]
    """
    if value is None:
        return ""
    s = str(value).lower().replace(" ", "-")
    s = "".join(c if c in SAFE_CHARS else "-" for c in s)
    # collapse multiple dashes
    s = re.sub(r"-+", "-", s)
    return s.strip("-:._")


def trim_id(value: str, *, max_len: int = MAX_ID_LEN_DEFAULT) -> str:
    """Trim an ID to max_len characters.

    If too long, prefer keeping the end (often contains variant/index). If trimming would
    produce an empty or tiny prefix, replace the middle with a short hash.
    """
    if len(value) <= max_len:
        return value
    # Keep suffix (last 24 chars) to preserve variant/index
    suffix = value[-24:]
    prefix_room = max_len - len(suffix) - 1  # 1 for a separator '-'
    if prefix_room < 8:
        # Hash whole value and use fixed short core
        h = hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]
        return f"v{h}-{suffix}"[-max_len:]
    return f"{value[:prefix_room]}-{suffix}"


def doc_id_from_text(
    canonical_text: str,
    *,
    salt: Optional[str] = None,
    version: Optional[str] = None,
    length: int = 16,
    max_len: int = MAX_ID_LEN_DEFAULT,
) -> str:
    """
    Generate a stable, human-opaque doc_id from canonical content.

    - Uses hashing.make_document_id under the hood.
    - Adds optional version prefix (e.g., 'v2_') for future migrations.
    - Enforces safe characters and length.
    """
    norm = normalize_text(canonical_text)
    core = make_document_id(norm, salt=salt, length=length, prefix="doc", algo="sha1")
    if version:
        candidate = f"{to_safe(version)}_{core}"
    else:
        candidate = core
    safe = to_safe(candidate)
    return trim_id(safe, max_len=max_len)


def ensure_doc_id(doc_id: str, *, max_len: int = MAX_ID_LEN_DEFAULT) -> str:
    """Sanitize and enforce length on an externally-provided doc_id."""
    return trim_id(to_safe(doc_id), max_len=max_len)


def _join_and_limit(parts: list[str], *, max_len: int) -> str:
    raw = ":".join(p for p in parts if p)
    return trim_id(to_safe(raw), max_len=max_len)


def context_id(doc_id: str, variant: str, *, max_len: int = MAX_ID_LEN_DEFAULT, prefix: Optional[str] = None) -> str:
    """
    Build a context_id unique per variant to scope TokenVault mappings.

    Format (canonical): {doc_id}:{variant}
    Optionally add a prefix like 'ctx' -> ctx:{doc_id}:{variant}
    """
    d = ensure_doc_id(doc_id, max_len=max_len)
    v = to_safe(variant)
    parts = ([to_safe(prefix)] if prefix else []) + [d, v]
    return _join_and_limit(parts, max_len=max_len)


def point_id(
    doc_id: str,
    variant: str,
    index: int,
    *,
    width: int = 6,
    max_len: int = MAX_ID_LEN_DEFAULT,
    prefix: Optional[str] = None,
) -> str:
    """
    Create a stable Qdrant point ID for a chunk.

    Format: {doc_id}:{variant}:{index:0{width}d} (optionally prefixed)
    """
    d = ensure_doc_id(doc_id, max_len=max_len)
    v = to_safe(variant)
    idx = f"{max(0, int(index)):0{int(width)}d}"
    parts = ([to_safe(prefix)] if prefix else []) + [d, v, idx]
    return _join_and_limit(parts, max_len=max_len)

