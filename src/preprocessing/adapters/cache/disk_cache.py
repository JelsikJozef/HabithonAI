"""
SQLite-backed deterministic disk cache for small string segments.

This module provides a local cache adapter that persists small string values
(typically translated text segments) keyed by opaque, deterministic keys.
It is safe for concurrent processes/threads, crash-resilient, and designed for
fully offline operation using SQLite by default.

Public surface:
- CacheError: Exception type with stable short codes.
- DiskCache: Cache adapter exposing open/get/put/delete/clear/stats/capabilities/close.

Determinism & safety:
- Idempotent put(key, value); fast get(key) with optional TTL and LRU eviction.
- No partial reads/writes; SQLite transactions and WAL provide robustness.
- Namespacing support to isolate keys across engines or runs.
- All backend errors mapped to CacheError; no raw sqlite exceptions leak.

Note: Keys must be precomputed and filename-safe; this adapter does not hash or
interpret keys. Values are UTF-8 strings (no binary blobs).
"""
from __future__ import annotations

import os
import sqlite3
import time
from typing import Any, Dict, Optional


class CacheError(Exception):
    """Domain-specific error for cache operations.

    Attributes:
        code: Stable short code for the category. One of: "OPEN_FAILED",
              "GET_FAILED", "PUT_FAILED", "READ_ONLY", "VALUE_TOO_LARGE",
              "EVICT_FAILED".
        message: Human-readable description (non-localized), safe for logs.
        details: Optional structured details dictionary.
    """

    def __init__(self, code: str, message: str, *, details: Optional[Dict[str, Any]] = None) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}

    def __str__(self) -> str:  # pragma: no cover
        return f"{self.code}: {self.message}"


class DiskCache:
    """Local, deterministic segment cache using SQLite.

    Responsibilities:
    - Persist small string values addressed by opaque, deterministic keys.
    - Provide idempotent put(key, value) and fast get(key) with TTL/LRU options.
    - Remain safe under concurrent processes/threads and resilient to crashes.

    Constructor parameters:
        root_path: Filesystem path to the cache file or directory.
            - When mode=="sqlite": file path to a .sqlite database (created if missing).
        mode: Backend mode ("sqlite" supported). Default "sqlite".
        read_only: If True, disable writes (put/delete/clear). Reads still work
            but won't update last_accessed or lazily purge expired entries.
        max_value_bytes: Guardrail for UTF-8 encoded value size. Values larger
            than this raise CacheError("VALUE_TOO_LARGE").
        max_items: Optional integer cap on total entries; when exceeded, evict
            least-recently accessed entries (LRU) deterministically.
        default_ttl_seconds: Optional default TTL for entries that don't specify
            ttl_seconds in put(). TTL is measured from created_at.
        sync_policy: SQLite PRAGMA synchronous policy: "extra" | "full" |
            "normal" | "off". Default "normal".
        journal_mode: SQLite PRAGMA journal_mode; default "wal" for concurrency.
        namespace: Optional logical namespace string to prefix keys; avoids
            collisions across engines/runs.
        stats_enabled: Whether to track lightweight counters (hits, misses, puts,
            evictions). Not critical; failures never raise.

    Public methods:
        open(): Initialize the backend and create schema/PRAGMAs if needed.
        get(key): Return cached value or None; honors TTL and updates last_accessed.
        put(key, value, *, ttl_seconds=None): Upsert value atomically; enforces
            size limits and LRU eviction.
        delete(key): Remove a single entry; silently returns if missing.
        clear(namespace=None): Delete all entries, or those in a namespace.
        stats(): Return counters and live item count.
        capabilities(): Return engine fingerprint and configuration summary.
        close(): Close DB handle; idempotent.

    Error handling:
        All backend failures map to CacheError with stable short codes; no raw
        sqlite3 exceptions leak to callers.
    """

    def __init__(
        self,
        root_path: str,
        *,
        mode: str = "sqlite",
        read_only: bool = False,
        max_value_bytes: int = 64_000,
        max_items: Optional[int] = None,
        default_ttl_seconds: Optional[int] = None,
        sync_policy: str = "normal",
        journal_mode: str = "wal",
        namespace: Optional[str] = None,
        stats_enabled: bool = True,
    ) -> None:
        self._root_path = root_path
        self._mode = mode
        self._read_only = bool(read_only)
        self._max_value_bytes = int(max_value_bytes)
        self._max_items = int(max_items) if max_items is not None else None
        self._default_ttl = int(default_ttl_seconds) if default_ttl_seconds is not None else None
        self._sync_policy = sync_policy
        self._journal_mode = journal_mode
        self._namespace = namespace
        self._stats_enabled = bool(stats_enabled)

        self._conn: Optional[sqlite3.Connection] = None
        self._stats: Dict[str, int] = {"hits": 0, "misses": 0, "puts": 0, "evictions": 0}

    # ---------------------------- Lifecycle ----------------------------

    def open(self) -> None:
        """Initialize the cache backend and ensure schema/PRAGMAs.

        Returns:
            None. Raises CacheError on failure.

        Raises:
            CacheError("OPEN_FAILED"): If the backend fails to initialize.
        """
        if self._mode != "sqlite":
            raise CacheError("OPEN_FAILED", f"Unsupported mode '{self._mode}'. Only 'sqlite' is implemented.")
        try:
            first_time = not os.path.exists(self._root_path)
            self._conn = sqlite3.connect(self._root_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 3000")
            # WAL journaling enables cross-process concurrency and durability.
            try:
                self._conn.execute(f"PRAGMA journal_mode = {self._journal_mode}")
            except Exception:
                pass
            try:
                self._conn.execute(f"PRAGMA synchronous = {self._sync_policy}")
            except Exception:
                pass
            # Create schema if missing
            with self._conn:
                self._conn.executescript(
                    """
                    CREATE TABLE IF NOT EXISTS entries (
                        key TEXT PRIMARY KEY,
                        value TEXT NOT NULL,
                        created_at INTEGER,
                        last_accessed INTEGER,
                        ttl_seconds INTEGER NULL,
                        size_bytes INTEGER NULL
                    );
                    CREATE INDEX IF NOT EXISTS idx_entries_last_accessed ON entries (last_accessed);
                    """
                )
        except Exception as ex:  # pragma: no cover - environment dependent
            raise CacheError("OPEN_FAILED", f"Failed to open cache: {ex}") from ex

    # ------------------------------ Helpers ----------------------------

    def _apply_ns(self, key: str) -> str:
        return f"{self._namespace}:{key}" if self._namespace else key

    def _now(self) -> int:
        # Millisecond-resolution to avoid same-second ties impacting LRU
        return int(time.time() * 1000)

    # ---------------------------- Operations ---------------------------

    def get(self, key: str) -> Optional[str]:
        """Retrieve a cached value by key, honoring TTL.

        Parameters:
            key: Deterministic, opaque, filename-safe key. The adapter applies
                an optional namespace prefix internally if configured.

        Returns:
            str | None: The cached value if present and not expired; otherwise None.

        Errors:
            Raises CacheError("GET_FAILED") on backend errors only. A missing
            or expired entry is treated as a cache miss and returns None without error.
        """
        if self._conn is None:
            raise CacheError("GET_FAILED", "Cache not opened. Call open() first.")
        nkey = self._apply_ns(key)
        try:
            row = self._conn.execute(
                "SELECT value, created_at, last_accessed, ttl_seconds FROM entries WHERE key = ?",
                (nkey,),
            ).fetchone()
            if row is None:
                if self._stats_enabled:
                    self._stats["misses"] += 1
                return None
            # TTL check based on created_at (milliseconds)
            created_at = int(row["created_at"]) if row["created_at"] is not None else None
            ttl = int(row["ttl_seconds"]) if row["ttl_seconds"] is not None else None
            if ttl is not None and created_at is not None:
                if created_at + (ttl * 1000) <= self._now():
                    # Expired: treat as miss; lazily delete if not read-only
                    if not self._read_only:
                        try:
                            with self._conn:
                                self._conn.execute("DELETE FROM entries WHERE key = ?", (nkey,))
                        except Exception:
                            pass
                    if self._stats_enabled:
                        self._stats["misses"] += 1
                    return None
            val = row["value"]
            # Update last_accessed unless read-only
            if not self._read_only:
                try:
                    with self._conn:
                        self._conn.execute(
                            "UPDATE entries SET last_accessed = ? WHERE key = ?",
                            (self._now(), nkey),
                        )
                except Exception:
                    pass
            if self._stats_enabled:
                self._stats["hits"] += 1
            return val
        except CacheError:
            raise
        except Exception as ex:
            raise CacheError("GET_FAILED", f"Failed to get key: {ex}") from ex

    def put(self, key: str, value: str, *, ttl_seconds: Optional[int] = None) -> None:
        """Insert or update a cached value atomically.

        Parameters:
            key: Deterministic, opaque key used as primary key.
            value: String value. The UTF-8 encoded size must be <= max_value_bytes.
            ttl_seconds: Optional TTL for this entry. If None, default_ttl_seconds
                from the constructor is used. TTL is measured from created_at.

        Returns:
            None.

        Raises:
            CacheError("READ_ONLY"): When read_only=True.
            CacheError("VALUE_TOO_LARGE"): When value exceeds max_value_bytes.
            CacheError("PUT_FAILED"): On backend errors during upsert.
            CacheError("EVICT_FAILED"): On eviction errors when max_items is set.
        """
        if self._conn is None:
            raise CacheError("PUT_FAILED", "Cache not opened. Call open() first.")
        if self._read_only:
            raise CacheError("READ_ONLY", "Cache is read-only; writes are disabled.")

        # Size guardrail
        size_bytes = len(value.encode("utf-8"))
        if size_bytes > self._max_value_bytes:
            raise CacheError("VALUE_TOO_LARGE", f"Value size {size_bytes} exceeds max {self._max_value_bytes} bytes.")

        nkey = self._apply_ns(key)
        now = self._now()
        ttl = int(ttl_seconds) if ttl_seconds is not None else self._default_ttl

        try:
            with self._conn:
                # Use upsert, preserving original created_at on update
                self._conn.execute(
                    """
                    INSERT INTO entries (key, value, created_at, last_accessed, ttl_seconds, size_bytes)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(key) DO UPDATE SET
                        value=excluded.value,
                        last_accessed=excluded.last_accessed,
                        ttl_seconds=excluded.ttl_seconds,
                        size_bytes=excluded.size_bytes
                    """,
                    (nkey, value, now, now, ttl, size_bytes),
                )
            if self._stats_enabled:
                self._stats["puts"] += 1
        except Exception as ex:
            raise CacheError("PUT_FAILED", f"Failed to upsert: {ex}") from ex

        # Enforce LRU cap if configured
        if self._max_items is not None:
            try:
                cnt = int(self._conn.execute("SELECT COUNT(1) FROM entries").fetchone()[0])
                overflow = max(0, cnt - self._max_items)
            except Exception as ex:
                raise CacheError("EVICT_FAILED", f"Failed to count entries: {ex}") from ex
            if overflow > 0:
                try:
                    with self._conn:
                        # Deterministic LRU: by effective access time asc, then key asc
                        keys = [r[0] for r in self._conn.execute(
                            "SELECT key FROM entries ORDER BY COALESCE(last_accessed, created_at) ASC, key ASC LIMIT ?",
                            (overflow,),
                        ).fetchall()]
                        if keys:
                            self._conn.executemany("DELETE FROM entries WHERE key = ?", [(k,) for k in keys])
                    if self._stats_enabled:
                        self._stats["evictions"] += len(keys)
                except Exception as ex:
                    raise CacheError("EVICT_FAILED", f"Eviction failed: {ex}") from ex

    def delete(self, key: str) -> None:
        """Remove a single entry by key.

        Parameters:
            key: Deterministic key to delete. Namespace is applied internally.

        Returns:
            None. Missing entries are ignored.

        Raises:
            CacheError("READ_ONLY"): When read_only=True.
        """
        if self._conn is None:
            # Silent no-op: align with optional nature
            return
        if self._read_only:
            raise CacheError("READ_ONLY", "Cache is read-only; writes are disabled.")
        nkey = self._apply_ns(key)
        try:
            with self._conn:
                self._conn.execute("DELETE FROM entries WHERE key = ?", (nkey,))
        except Exception:
            # Non-critical; ignore
            pass

    def clear(self, namespace: Optional[str] = None) -> None:
        """Delete all entries, or those within a logical namespace.

        Parameters:
            namespace: Optional namespace filter. If provided, only keys with
                the given namespace prefix are removed. If None, all entries are
                deleted.

        Returns:
            None.

        Raises:
            CacheError("READ_ONLY"): When read_only=True.
        """
        if self._conn is None:
            return
        if self._read_only:
            raise CacheError("READ_ONLY", "Cache is read-only; writes are disabled.")
        try:
            with self._conn:
                if namespace is None and not self._namespace:
                    self._conn.execute("DELETE FROM entries")
                else:
                    ns = namespace if namespace is not None else self._namespace
                    prefix = f"{ns}:" if ns else ""
                    if prefix:
                        self._conn.execute("DELETE FROM entries WHERE key LIKE ? ESCAPE '/'", (prefix + '%',))
        except Exception:
            # Non-critical; ignore clear errors
            pass

    def stats(self) -> Dict[str, Any]:
        """Return lightweight counters and current item count.

        Returns:
            dict: A dictionary with keys:
              - hits, misses, puts, evictions (integers).
              - items (int): live item count if available, otherwise 0.
              - backend (str): "sqlite".
        """
        items = 0
        if self._conn is not None:
            try:
                items = int(self._conn.execute("SELECT COUNT(1) FROM entries").fetchone()[0])
            except Exception:
                items = 0
        res = {**self._stats, "items": items, "backend": self._mode}
        return res

    def capabilities(self) -> Dict[str, Any]:
        """Return engine fingerprint and configuration for reporting.

        Returns:
            dict: Summary including name, mode, namespace, limits, and determinism.
        """
        return {
            "name": "disk-cache",
            "mode": self._mode,
            "namespace": self._namespace,
            "max_value_bytes": self._max_value_bytes,
            "max_items": self._max_items,
            "ttl_default": self._default_ttl,
            "deterministic": True,
        }

    def close(self) -> None:
        """Close the cache backend; idempotent.

        Returns:
            None.
        """
        try:
            if self._conn is not None:
                self._conn.close()
        finally:
            self._conn = None


__all__ = ["DiskCache", "CacheError"]
