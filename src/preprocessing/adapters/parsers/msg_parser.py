from __future__ import annotations

from .base import BaseParser
from ...domain.models import ParsedDocument, RawDocument


class MsgParser(BaseParser):
    """Outlook MSG -> text.

    - Extracts headers (From/To/Subject/Date) and body using extract_msg if available.
    - Populates metadata["email"] with basic header fields.
    - Returns body text (subject + two newlines + body) when available; empty text otherwise.
    """

    def parse(self, raw: RawDocument) -> ParsedDocument:
        headers = {"from": None, "to": None, "subject": None, "date": None}
        text = ""
        try:
            import extract_msg  # type: ignore

            msg = extract_msg.Message(str(raw.path))
            # Some attributes may be None depending on the message
            headers["from"] = getattr(msg, "sender", None) or getattr(msg, "from_", None)
            headers["to"] = getattr(msg, "to", None)
            headers["subject"] = getattr(msg, "subject", None)
            headers["date"] = getattr(msg, "date", None)
            body = getattr(msg, "body", None) or ""
            subj = headers["subject"] or ""
            text = (subj + "\n\n" + body).strip()
        except Exception:
            # Dependency missing or parse error: leave text empty, keep headers None
            text = ""
        metadata = {"ext": "msg", "bytes": raw.size, "email": headers}
        return ParsedDocument(text=text, source=raw, metadata=metadata)

