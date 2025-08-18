from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import AsyncIterator, Iterable
import asyncio

from ...domain.models import RawDocument
from ...domain.ports import IngestionPort


class FileSystemIngestion(IngestionPort):
    """Filesystem-based ingestor.

    - Scans `root` recursively using Path.rglob for provided glob patterns.
    - Yields RawDocument with computed size, mtime, and normalized extension.
    - Optional max_size to filter large files.
    - Async watch mode uses lightweight polling to emit new/modified files.
    """

    def __init__(self, *, follow_symlinks: bool = False, max_size: int | None = None) -> None:
        if max_size is not None and max_size < 0:
            raise ValueError("max_size must be >= 0 when provided")
        self._follow_symlinks = bool(follow_symlinks)
        self._max_size = max_size

    def ingest_batch(self, root: Path, globs: tuple[str, ...]) -> Iterable[RawDocument]:
        def _gen() -> Iterable[RawDocument]:
            for p in self._iter_paths(root, globs):
                try:
                    st = p.stat()
                except OSError:
                    # Skip files that disappear or cannot be stat'ed
                    continue
                if self._max_size is not None and st.st_size > self._max_size:
                    continue
                yield self._to_raw(p, st)

        return _gen()

    async def ingest_watch(self, root: Path, globs: tuple[str, ...]) -> AsyncIterator[RawDocument]:
        """Watch for new/changed files under root matching patterns.

        Implementation uses simple polling (no external deps). It tracks the last
        known modification times and yields when a file is first seen or updated.
        """
        # Initial snapshot
        seen_mtimes = {}
        for p in self._iter_paths(root, globs):
            try:
                st = p.stat()
            except OSError:
                continue
            seen_mtimes[p] = st.st_mtime

        # Polling loop
        while True:
            for p in self._iter_paths(root, globs):
                try:
                    st = p.stat()
                except OSError:
                    # File may have been removed between listing and stat
                    continue
                # Filter by size if configured (still remember mtimes to avoid spamming)
                if self._max_size is not None and st.st_size > self._max_size:
                    seen_mtimes[p] = st.st_mtime
                    continue
                mtime = st.st_mtime
                last = seen_mtimes.get(p)
                if last is None or mtime > last:
                    seen_mtimes[p] = mtime
                    yield self._to_raw(p, st)
            # Small sleep to avoid busy-waiting
            await asyncio.sleep(1.0)

    def _iter_paths(self, root: Path, globs: tuple[str, ...]) -> Iterable[Path]:
        """Yield unique file Paths matching any pattern, optionally skipping symlinks."""
        seen = set()
        for pattern in globs:
            # Use rglob for recursive matching; it may traverse into symlinked dirs
            # depending on platform. We filter out symlinked files when follow_symlinks=False.
            try:
                it = root.rglob(pattern)
            except Exception:
                continue
            for p in it:
                # Only files
                try:
                    if not p.is_file():
                        continue
                    if not self._follow_symlinks and p.is_symlink():
                        continue
                except OSError:
                    continue
                if p in seen:
                    continue
                seen.add(p)
                yield p

    @staticmethod
    def _to_raw(path: Path, st) -> RawDocument:
        ext = path.suffix.lstrip(".").lower()
        try:
            mtime_dt = datetime.fromtimestamp(st.st_mtime)
        except Exception:
            # Fallback in case of weird timestamp
            mtime_dt = datetime.now()
        return RawDocument(path=path, size=int(st.st_size), mtime=mtime_dt, ext=ext)
