from __future__ import annotations

import re

from anonymization.domain.entities import PiiEntity

# German company suffixes
DE_ORG_SUFFIX_RE = re.compile(r"\b(?:GmbH(?:\s*&\s*Co\.?\s*KG)?|AG|KG|OHG|UG|GbR|e\.V\.|e\.K\.)\b")
# Capitalized token (allowing German letters) or special company fragments
DE_CAP_TOKEN_RE = re.compile(r"^(?:[A-ZÄÖÜ][\w'’\-\.äöüÄÖÜß]*|&|Co\.?|KG)$")
# Lowercase connector tokens we don't want to include
DE_LOWER_CONNECTORS = {"bei", "der", "die", "das", "des", "den", "im", "in", "und"}


def _left_trim_to_company_start(text: str, start: int, end: int) -> int:
    """Given a span [start,end) which contains a German company suffix, trim the left
    boundary to the first contiguous capitalized token sequence preceding the suffix,
    ignoring lowercase connectors like 'bei'.
    """
    sub = text[start:end]
    m = DE_ORG_SUFFIX_RE.search(sub)
    if not m:
        return start
    # Global indices
    sfx_start = start + m.start()
    # Walk left from sfx_start to find contiguous allowed tokens
    i = sfx_start
    first_tok_start = sfx_start
    # Move left skipping spaces
    while i > start:
        # Skip spaces to the left
        j = i - 1
        while j >= start and text[j].isspace():
            j -= 1
        if j < start:
            break
        # Find token start
        tok_end = j + 1
        k = j
        while k >= start and not text[k].isspace():
            k -= 1
        tok_start = k + 1
        token = text[tok_start:tok_end]
        # Stop if lowercase connector
        if token.lower() in DE_LOWER_CONNECTORS:
            break
        if DE_CAP_TOKEN_RE.match(token):
            first_tok_start = tok_start
            i = tok_start
            continue
        # Not an allowed capitalized token; stop
        break
    return first_tok_start


def trim_organization_entities(
    entities: list[PiiEntity], text: str, language: str | None
) -> list[PiiEntity]:
    """Trim ORGANIZATION spans to exclude preceding lowercase phrases (German).

    For German, ensure ORGANIZATION spans start at the company name and include the
    suffix; prevents spans like 'Herr Peter Müller arbeitet bei Beispiel GmbH & Co. KG'.
    """
    if not entities:
        return []
    out: list[PiiEntity] = []
    for e in entities:
        if e.type != "ORGANIZATION" or language != "de":
            out.append(e)
            continue
        new_start = _left_trim_to_company_start(text, e.start, e.end)
        if new_start != e.start and new_start < e.end:
            out.append(
                PiiEntity(
                    type=e.type,
                    start=new_start,
                    end=e.end,
                    value=text[new_start : e.end],
                    score=e.score,
                    detector=e.detector,
                )
            )
        else:
            out.append(e)
    return out
