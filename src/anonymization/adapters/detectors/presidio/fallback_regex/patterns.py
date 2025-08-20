import re

EMAIL_RE = re.compile(r"(?P<email>[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,})")
PHONE_RE = re.compile(r"(?P<phone>(?:\+\d{1,3}[\s-]?)?(?:\(?\d{2,4}\)?[\s-]?)?\d{3,4}[\s-]?\d{3,4})")
IPV4_RE = re.compile(r"(?P<ip>\b(?:\d{1,3}\.){3}\d{1,3}\b)")
CREDIT_CARD_RE = re.compile(r"(?P<cc>\b(?:\d[ -]*?){13,19}\b)")


def luhn_valid(digits: str) -> bool:
    """Validate a numeric string using the Luhn checksum algorithm.

    Parameters
    - digits: String consisting only of numeric characters (0-9).

    Returns
    - True if the string passes the Luhn check, otherwise False.
    """
    s = 0
    alt = False
    for ch in reversed(digits):
        d = ord(ch) - 48
        if alt:
            d *= 2
            if d > 9:
                d -= 9
        s += d
        alt = not alt
    return s % 10 == 0
