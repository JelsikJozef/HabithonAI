from __future__ import annotations

from .base import BaseParser
from ...domain.models import ParsedDocument, RawDocument


class TxtParser(BaseParser):
    """Plain-text parser with light charset detection.

    - Attempts UTF-8 first; if it fails, tries UTF-16 BOM; then latin-1 as fallback.
    - If charset-normalizer or chardet is available, uses it to improve detection.
    - Does not log input data; reads only from raw.path.
    """

    def parse(self, raw: RawDocument) -> ParsedDocument:
        data = (raw.path).read_bytes()
        enc = self._detect_encoding(data)
        try:
            text = data.decode(enc, errors="replace")
        except LookupError:
            # Unknown codec from detector; fallback to utf-8 with replacement
            text = data.decode("utf-8", errors="replace")
            enc = "utf-8"
        meta = {"ext": "txt", "bytes": raw.size}
        return ParsedDocument(text=text, source=raw, charset=enc, metadata=meta)

    def _detect_encoding(self, data: bytes) -> str:
        # Try UTF-8 (with BOM handling)
        try:
            data.decode("utf-8")
            return "utf-8"
        except UnicodeDecodeError:
            pass
        # BOM-based quick checks
        if data.startswith(b"\xff\xfe"):
            return "utf-16-le"
        if data.startswith(b"\xfe\xff"):
            return "utf-16-be"
        # Optional: charset-normalizer
        enc = None
        try:
            from charset_normalizer import from_bytes  # type: ignore

            res = from_bytes(data).best()
            if res is not None and res.encoding:
                enc = res.encoding
        except Exception:
            enc = None
        # Optional: chardet
        if enc is None:
            try:
                import chardet  # type: ignore

                det = chardet.detect(data)
                if det and det.get("encoding"):
                    enc = str(det["encoding"]).lower()
            except Exception:
                enc = None
        return enc or "latin-1"
