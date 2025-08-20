from typing import List
import re
from .patterns import EMAIL_RE, PHONE_RE, IPV4_RE, CREDIT_CARD_RE, luhn_valid
from anonymization.domain.entities import PiiEntity


def detect_with_regex(text: str, detector_name: str = "presidio") -> List[PiiEntity]:
    """Detect common PII using regex as a resilient fallback.

    Parameters
    - text: Input text to scan for emails, phones, IPv4 addresses, and credit cards.
    - detector_name: Name to stamp on produced PiiEntity.detector field (default: "presidio").

    Returns
    - List of PiiEntity matches with conservative confidence scores;
      credit cards are validated with Luhn and IPv4 octets checked for range (0-255).
    """
    out: List[PiiEntity] = []
    for m in EMAIL_RE.finditer(text):
        s, e = m.span()
        out.append(PiiEntity(type="EMAIL", start=s, end=e, value=m.group(0), score=0.6, detector=detector_name))
    for m in PHONE_RE.finditer(text):
        s, e = m.span()
        out.append(PiiEntity(type="PHONE", start=s, end=e, value=m.group(0), score=0.5, detector=detector_name))
    for m in IPV4_RE.finditer(text):
        val = m.group(0)
        octets = val.split(".")
        if any((not o.isdigit()) or int(o) > 255 for o in octets):
            continue
        s, e = m.span()
        out.append(PiiEntity(type="IP", start=s, end=e, value=val, score=0.5, detector=detector_name))
    for m in CREDIT_CARD_RE.finditer(text):
        digits = re.sub(r"\D", "", m.group(0))
        if 13 <= len(digits) <= 19 and luhn_valid(digits):
            s, e = m.span()
            out.append(PiiEntity(type="CREDIT_CARD", start=s, end=e, value=m.group(0), score=0.5, detector=detector_name))
    return out
