from __future__ import annotations

from typing import Any

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
        self._sink.append(record)

    def close(self) -> None:
        self._sink.close()

