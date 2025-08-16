import re
from typing import List, Optional
from ...domain.entities import PiiEntity
from ...domain.ports import DetectorPort


class RegexDetector(DetectorPort):
    name = "regex"

    # Basic, pragmatic regex patterns; not perfect but useful.
    EMAIL_RE = re.compile(r"(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
    # International numbers with +, spaces, dashes, parentheses; require at least 7 digits
    PHONE_RE = re.compile(r"(?P<phone>(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4})")
    IPV4_RE = re.compile(r"(?P<ip>\b(?:\d{1,3}\.){3}\d{1,3}\b)")
    CREDIT_CARD_RE = re.compile(r"(?P<cc>\b(?:\d[ -]*?){13,19}\b)")

    def _detect_with_regex(self, text: str, pattern: re.Pattern, label: str) -> List[PiiEntity]:
        out: List[PiiEntity] = []
        for m in pattern.finditer(text):
            val = m.group(0)
            start, end = m.span()
            # Quick sanity checks for IP and CC
            if label == "IP":
                octets = val.split(".")
                if any(int(o) > 255 for o in octets if o.isdigit()):
                    continue
            if label == "CREDIT_CARD":
                digits = re.sub(r"\D", "", val)
                if len(digits) < 13 or len(digits) > 19:
                    continue
            out.append(PiiEntity(type=label, start=start, end=end, value=val, score=0.6, detector=self.name))
        return out

    def detect(self, text: str, language: Optional[str] = None) -> List[PiiEntity]:
        entities: List[PiiEntity] = []
        entities.extend(self._detect_with_regex(text, self.EMAIL_RE, "EMAIL"))
        entities.extend(self._detect_with_regex(text, self.PHONE_RE, "PHONE"))
        entities.extend(self._detect_with_regex(text, self.IPV4_RE, "IP"))
        entities.extend(self._detect_with_regex(text, self.CREDIT_CARD_RE, "CREDIT_CARD"))
        return entities

