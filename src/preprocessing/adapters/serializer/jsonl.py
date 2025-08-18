from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ...domain.errors import SerializationError
from ...domain.ports import SerializerPort


class JsonlSerializer(SerializerPort):
    """Write records as JSON Lines to a file.

    - Each append(record) serializes the dict as one JSON object per line.
    - Constructor opens the file in append mode and creates parent dirs if needed.
    - close() flushes and closes the underlying file handle.
    - Raises SerializationError on invalid JSON or I/O errors.
    """

    def __init__(self, output_path: Path, *, ensure_ascii: bool = False) -> None:
        if not isinstance(output_path, Path):
            raise TypeError("output_path must be a pathlib.Path")
        self._path = output_path
        self._ensure_ascii = bool(ensure_ascii)
        self._fh = None
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            # Open in append mode to allow incremental writes
            self._fh = self._path.open("a", encoding="utf-8")
        except Exception as e:
            raise SerializationError("Failed to open output file: %s" % e)

    def append(self, record: dict[str, Any]) -> None:
        if self._fh is None:
            raise SerializationError("Serializer is closed")
        try:
            line = json.dumps(record, ensure_ascii=self._ensure_ascii)
        except Exception as e:
            raise SerializationError("Failed to serialize record to JSON: %s" % e)
        try:
            self._fh.write(line + "\n")
            self._fh.flush()
        except Exception as e:
            raise SerializationError("Failed to write JSONL line: %s" % e)

    def close(self) -> None:
        if self._fh is not None:
            try:
                self._fh.flush()
                self._fh.close()
            finally:
                self._fh = None
