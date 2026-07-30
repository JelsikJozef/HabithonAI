"""Shared anonymization-sanity guardrail.

Single source of truth for the PII-residue scan used by Step1 (``normalize``) and
Step3 (``analysis.step3_summary``). Pure stdlib, no I/O, no third-party — safe to import
from any layer of the ``preprocessing`` context.

Pseudonym/hash replacement tokens (``h:<kid>:<hex>``, ``t:<kid>:<id>``; see
``anonymization.adapters.crypto.crypto``) are NOT PII — they are the substitutions the
anonymizer puts in place of PII. Their hex/base32 bodies routinely contain runs of digits,
and a 10-digit run inside a token would otherwise false-match the phone pattern (3+3+4).
We therefore strip token spans from a scan copy of the text BEFORE scanning. The PII
patterns themselves are kept strict and unchanged.
"""

from __future__ import annotations

import re
from typing import Any

# Reasonably obvious PII patterns (kept strict — do not loosen).
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_PHONE_RE = re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}")
_SSN_US_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")

# Pseudonym / hash replacement tokens: ``h:<kid>:<hex>`` and ``t:<kid>:<base32id>``.
_PSEUDONYM_TOKEN_RE = re.compile(r"(?<![A-Za-z0-9])[ht]:[A-Za-z0-9_]+:[A-Za-z0-9]+")

_PLACEHOLDER_RE = re.compile(r"\[(?:PERSON|EMAIL|PHONE|ADDRESS|ORG)]")


def anonymization_sanity(text: str) -> tuple[bool, dict[str, Any]]:
    """Scan ``text`` for residual PII after anonymization.

    Returns ``(passed, info)`` where ``passed`` is False if any PII pattern matches.
    ``info`` carries ``patterns`` (per-type match flags) and ``placeholders_present``.
    """
    # Strip replacement tokens first so their bodies cannot false-match PII patterns.
    # Replace with a space (not "") so digits surrounding a token cannot fuse into a
    # spurious run across the removed span.
    scan_text = _PSEUDONYM_TOKEN_RE.sub(" ", text)

    matches = {
        "email": bool(_EMAIL_RE.search(scan_text)),
        "phone": bool(_PHONE_RE.search(scan_text)),
        "ssn_us": bool(_SSN_US_RE.search(scan_text)),
    }
    # Placeholders are evaluated over the original text (tokens do not affect them).
    placeholders_present = bool(_PLACEHOLDER_RE.search(text))
    passed = not any(matches.values())
    info = {"patterns": matches, "placeholders_present": placeholders_present}
    return passed, info
