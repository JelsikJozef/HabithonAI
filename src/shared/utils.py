from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from pathlib import Path

__all__ = [
    "utc_now_iso",
    "atomic_write_text",
    "newline_lf",
    "shorten_middle",
    "require_non_empty_str",
    "validate_variant",
    "validate_language_code",
    "require_non_empty_content",
]

# Track last timestamp to ensure monotonic non-decreasing sequence within a run
_last_iso_ts: str | None = None


def utc_now_iso() -> str:
    """Return an ISO-8601 UTC timestamp with Z suffix and seconds precision.

    Example: '2025-08-21T19:00:00Z'. Ensures monotonic non-decreasing output within a single run.
    """
    global _last_iso_ts
    now = datetime.now(UTC)
    ts = now.replace(microsecond=0).strftime("%Y-%m-%dT%H:%M:%SZ")
    if _last_iso_ts is not None and ts < _last_iso_ts:
        # Clock skew; bump to last
        ts = _last_iso_ts
    elif _last_iso_ts is not None and ts == _last_iso_ts:
        # Same-second collision; bump by 1 second
        try:
            base = datetime.strptime(_last_iso_ts, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)
            ts = (base + timedelta(seconds=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
        except Exception:
            # Fallback: append nothing, keep as-is
            pass
    _last_iso_ts = ts
    return ts


def atomic_write_text(
    target: os.PathLike | str,
    text: str,
    *,
    encoding: str = "utf-8",
    mode: int = 0o640,
) -> Path:
    """Atomically write text to target path within the same filesystem directory.

    Steps
    - Create parent directories if needed
    - Write to a temporary file in the same directory
    - Flush and fsync
    - Set sane permissions (default 0640)
    - Atomic rename (os.replace) to final path

    Returns the final Path. Raises RuntimeError with context on failure.
    """
    p = Path(target)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        # Create a temporary file in the target directory
        with tempfile.NamedTemporaryFile(
            "w", dir=str(p.parent), delete=False, encoding=encoding
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(text)
            tmp.flush()
            os.fsync(tmp.fileno())
        # Set permissions before moving
        try:
            os.chmod(tmp_path, mode)
        except Exception:
            # Non-fatal on some filesystems; continue
            pass
        # Atomically replace the target
        os.replace(tmp_path, p)
        # Ensure final mode
        try:
            os.chmod(p, mode)
        except Exception:
            pass
        return p
    except Exception as e:
        raise RuntimeError(
            f"atomic_write_text failed for {p} (size={len(text)} bytes, encoding={encoding}): {e}"
        ) from e


def newline_lf(text: str) -> str:
    """Canonicalize newlines to LF ("\n")."""
    if text is None:
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def shorten_middle(s: str, max_len: int = 120) -> str:
    """Shorten long strings for logs: keep head and tail, connect with '...'.

    Example: 'abcdef' with max_len=5 -> 'ab...f'
    """
    if s is None:
        return ""
    if len(s) <= max_len or max_len < 5:
        return s
    # Keep approx 2/3 head, 1/3 tail, accounting for '...'
    head = (max_len - 3) * 2 // 3
    tail = (max_len - 3) - head
    return f"{s[:head]}...{s[-tail:]}"


def require_non_empty_str(value: str | None, name: str) -> str:
    """Validate a non-empty string; strip and return it."""
    if value is None:
        raise ValueError(f"{name} is required (got None). Provide a non-empty string.")
    s = str(value).strip()
    if not s:
        raise ValueError(
            f"{name} must be a non-empty string (got: {repr(value)}). Provide a non-empty value."
        )
    return s


def validate_variant(variant: str, allowed: Iterable[str] = ("original", "english")) -> str:
    """Ensure variant is one of the allowed values; return normalized value."""
    v = require_non_empty_str(variant, "variant").lower()
    allowed_set = {a.lower() for a in allowed}
    if v not in allowed_set:
        raise ValueError(
            f"variant must be one of {sorted(allowed_set)} (got: {variant!r}). "
            f"Use a supported variant name."
        )
    return v


_LANG_RE = re.compile(r"^[a-z]{2}(-[A-Z]{2})?$")


def validate_language_code(code: str) -> str:
    """Basic language code validation: 'xx' or 'xx-YY'."""
    c = require_non_empty_str(code, "language code")
    if not _LANG_RE.match(c):
        raise ValueError(
            f"language code must match 'xx' or 'xx-YY' (got: {code!r}). "
            f"Examples: 'en', 'sk', 'en-US', 'de-DE'."
        )
    return c


def require_non_empty_content(text: str | None, context: str = "content") -> str:
    """Ensure content is not empty before hashing/chunking/embedding."""
    s = require_non_empty_str(text, context)
    if not s.strip():
        raise ValueError(f"{context} cannot be empty or whitespace-only. Provide meaningful input.")
    return s
