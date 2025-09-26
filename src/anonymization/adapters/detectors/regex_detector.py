import re
from typing import List

from ...domain.entities import PiiEntity


class RegexDetector:
    """Lightweight, offline regex detector for common PII.

    Detects:
    - EMAIL
    - PHONE (simple international/local patterns)
    """

    name = "regex"

    _re_email = re.compile(r"(?i)(?P<val>[a-z0-9._%+-]+@[a-z0-9.-]+\.[a-z]{2,})")
    _re_phone = re.compile(r"(?P<val>\+?\d[\d\s().-]{7,}\d)")

    def detect(self, text: str, language: str | None = None) -> List[PiiEntity]:  # type: ignore[override]
        s = text or ""
        entities: List[PiiEntity] = []
        for m in self._re_email.finditer(s):
            val = m.group("val")
            entities.append(
                PiiEntity(
                    type="EMAIL",
                    start=m.start("val"),
                    end=m.end("val"),
                    value=val,
                    score=0.9,
                    detector=self.name,
                )
            )
        for m in self._re_phone.finditer(s):
            val = m.group("val")
            # Filter out emails already captured (phones overlapping emails)
            entities.append(
                PiiEntity(
                    type="PHONE",
                    start=m.start("val"),
                    end=m.end("val"),
                    value=val,
                    score=0.6,
                    detector=self.name,
                )
            )
        return entities
