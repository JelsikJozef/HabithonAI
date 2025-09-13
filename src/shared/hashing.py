"""
Shared hashing utilities — stable IDs & content hashes.

Purpose
- Provide deterministic identifiers for documents and chunks, and integrity hashes for content.
- These IDs drive deduplication, TokenVault context binding, Qdrant point IDs, and audit trails.

Key functions
- normalize_text(): Canonicalize text prior to hashing (UTF-8, NFKC, LF newlines, trimmed lines).
- content_hash(): Strong digest (SHA-256 by default) over normalized content.
- document_fingerprint(): Trackable fingerprint from path + size + mtime (for audits), distinct from content hash.
- make_document_id(): Stable, human-opaque ID derived from content hash (optional HMAC salt), prefixable.
- chunk_hash(): Hash at chunk granularity.
- chunk_id(): Human-traceable chunk identifier combining doc_id, variant, and sequential index.

Notes
- Do not treat a hash as encryption; use HMAC with a secret if inputs must be concealed.
- Prefer content-derived hashes for deduplication across directories/machines.
"""

from __future__ import annotations

import hashlib
import hmac
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Literal

__all__ = [
    "normalize_text",
    "content_hash",
    "document_fingerprint",
    "make_document_id",
    "chunk_hash",
    "chunk_id",
]


def _to_utf8_bytes(text: str) -> bytes:
    if isinstance(text, bytes):  # type: ignore[unreachable]
        # Defensive: we declare str; keep fallback if refactored.
        return text  # type: ignore[return-value]
    return text.encode("utf-8", errors="strict")


def normalize_text(
    text: str,
    *,
    strip_trailing_spaces: bool = True,
    ensure_final_newline: bool = False,
) -> str:
    """
    Normalize input text to a canonical form before hashing.

    Steps
    - Unicode NFKC normalization
    - Canonicalize newlines to LF ("\n")
    - Optionally strip trailing spaces/tabs from each line
    - Optionally ensure a single trailing newline at EOF

    Parameters
    - text: Input text
    - strip_trailing_spaces: If True, rstrip() per line before rejoining
    - ensure_final_newline: If True, append a trailing "\n" if not present

    Returns
    - Canonicalized text string
    """
    if text is None:
        text = ""

    # Unicode normalization
    out = unicodedata.normalize("NFKC", text)

    # Canonicalize newlines to LF
    out = out.replace("\r\n", "\n").replace("\r", "\n")

    if strip_trailing_spaces:
        lines = [ln.rstrip(" \t") for ln in out.split("\n")]
        out = "\n".join(lines)

    if ensure_final_newline and (not out.endswith("\n")):
        out = out + "\n"

    return out


def _new_hasher(algo: Literal["sha256", "sha1"]) -> hashlib._Hash:
    if algo == "sha256":
        return hashlib.sha256()
    if algo == "sha1":
        return hashlib.sha1()
    raise ValueError(f"Unsupported hash algorithm: {algo}")


def content_hash(
    canonical_text: str,
    *,
    algo: Literal["sha256", "sha1"] = "sha256",
) -> str:
    """
    Compute a strong digest over canonical content.

    Parameters
    - canonical_text: Text that was already normalized (use normalize_text first)
    - algo: Digest algorithm; prefer sha256 for logs/payloads

    Returns
    - Hex digest string
    """
    h = _new_hasher(algo)
    h.update(_to_utf8_bytes(canonical_text))
    return h.hexdigest()


def _canon_path(path: str | Path, base: str | Path | None = None) -> str:
    p = Path(path)
    if base is not None:
        try:
            p = Path(p).resolve().relative_to(Path(base).resolve())
        except Exception:
            p = Path(path)
    # Normalize separators to POSIX style for stability
    return p.as_posix()


def document_fingerprint(
    path: str | Path,
    size_bytes: int,
    mtime: float | int | datetime,
    *,
    base: str | Path | None = None,
    algo: Literal["sha1", "sha256"] = "sha1",
) -> str:
    """
    Build a trackable fingerprint from path + size + mtime.

    This is distinct from the content hash and is intended for audits and re-processing
    diagnostics. It changes if the file is moved relative to the chosen base.

    Parameters
    - path: Absolute or relative file path
    - size_bytes: File size in bytes
    - mtime: Modification time (seconds since epoch or datetime)
    - base: Optional base dir to compute a stable relative path
    - algo: Digest algorithm (sha1 is sufficient for short opaque IDs)

    Returns
    - Hex digest string (opaque, short-term stability goal)
    """
    rel = _canon_path(path, base)
    if isinstance(mtime, datetime):
        # Use integer nanoseconds if available, else seconds
        ts = int(mtime.timestamp() * 1_000_000_000)
    else:
        # accept float or int seconds
        ts = int(float(mtime) * 1_000_000_000)
    payload = f"{rel}|{int(size_bytes)}|{ts}"
    h = _new_hasher(algo)
    h.update(_to_utf8_bytes(payload))
    return h.hexdigest()


def make_document_id(
    canonical_text: str,
    *,
    salt: str | None = None,
    length: int = 16,
    prefix: str = "doc",
    algo: Literal["sha1", "sha256"] = "sha1",
) -> str:
    """
    Derive a stable, human-opaque document ID from canonical content.

    Parameters
    - canonical_text: Canonical content (use normalize_text first)
    - salt: Optional secret for HMAC. If provided, use HMAC(algo) over text.
    - length: Number of hex chars from the full digest to include (>=8 recommended)
    - prefix: Prefix to make IDs recognizable, e.g., "doc"
    - algo: Hash algorithm to use

    Returns
    - ID string, e.g., "doc_1a2b3c4d5e6f7a8b"
    """
    raw = _to_utf8_bytes(canonical_text)
    if salt:
        dig = hmac.new(_to_utf8_bytes(salt), raw, getattr(hashlib, algo)).hexdigest()
    else:
        h = _new_hasher(algo)
        h.update(raw)
        dig = h.hexdigest()
    core = dig[: max(8, int(length))]
    return f"{prefix}_{core}"


def chunk_hash(
    chunk_text: str,
    *,
    doc_id: str | None = None,
    index: int | None = None,
    variant: str | None = None,
    algo: Literal["sha256", "sha1"] = "sha256",
) -> str:
    """
    Compute a chunk-level hash over normalized chunk text and optional metadata.

    Parameters
    - chunk_text: Text content of the chunk (normalize first if desired)
    - doc_id: Optional document ID to bind the hash context
    - index: Optional chunk index
    - variant: Optional variant label (e.g., "orig", "ocr")
    - algo: Digest algorithm

    Returns
    - Hex digest string
    """
    h = _new_hasher(algo)
    h.update(_to_utf8_bytes(chunk_text))
    if doc_id is not None:
        h.update(b"|id:")
        h.update(_to_utf8_bytes(doc_id))
    if variant is not None:
        h.update(b"|var:")
        h.update(_to_utf8_bytes(variant))
    if index is not None:
        h.update(b"|i:")
        h.update(_to_utf8_bytes(str(int(index))))
    return h.hexdigest()


def chunk_id(
    doc_id: str,
    index: int,
    variant: str = "orig",
    *,
    width: int = 4,
    prefix: str = "chunk",
) -> str:
    """
    Create a human-traceable chunk identifier.

    Format: "{prefix}-{doc_id}-{variant}-{index:0{width}d}"

    Parameters
    - doc_id: Document ID (e.g., from make_document_id)
    - index: Sequential chunk index starting at 0 or 1
    - variant: Variant label (default: "orig")
    - width: Zero-pad width for the index (default: 4)
    - prefix: Prefix for chunk IDs (default: "chunk")

    Returns
    - Chunk ID string
    """
    idx = max(0, int(index))
    fmt = f"{{:0{int(width)}d}}"
    return f"{prefix}-{doc_id}-{variant}-{fmt.format(idx)}"
