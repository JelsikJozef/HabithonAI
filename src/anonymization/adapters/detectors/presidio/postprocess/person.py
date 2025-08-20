from typing import List, Optional
from anonymization.domain.entities import PiiEntity

# Simple salutation lexicons for trimming
SK_SALUTATIONS = {"pán", "pan", "pani", "slečna", "slecna", "Pán", "Pani", "Slečna"}
SK_TITLES = {"Ing.", "Mgr.", "Bc.", "PhDr.", "MUDr.", "JUDr.", "RNDr."}
DE_SALUTATIONS = {"Herr", "Herrn", "Hr.", "Frau", "Fr."}
DE_TITLES = {"Dr.", "Prof.", "Dipl.-Ing."}


def _strip_prefix_tokens(text: str, start: int, end: int, vocab: set[str]) -> int:
    """Advance start index past known prefix tokens (e.g., salutations/titles).

    Parameters
    - text: Full source text.
    - start: Start offset of the PERSON span.
    - end: End offset of the PERSON span (exclusive).
    - vocab: Set of string tokens to strip from the beginning of the span.

    Returns
    - New start offset after skipping matching tokens and a single following space.
    """
    i = start
    while i < end:
        # extract next token
        j = i
        while j < end and not text[j].isspace():
            j += 1
        token = text[i:j]
        if token in vocab:
            i = j
            # skip exactly one space if present
            if i < end and text[i].isspace():
                i += 1
        else:
            break
    return i


def trim_person_entities(entities: List[PiiEntity], text: str, language: Optional[str]) -> List[PiiEntity]:
    """Trim salutations/titles from PERSON spans based on language conventions.

    Parameters
    - entities: List of detected entities to post-process.
    - text: Full source text from which spans were extracted.
    - language: Optional language hint (e.g., "sk", "de") guiding which lexicon to use.

    Returns
    - New list of PiiEntity where PERSON spans no longer include salutation/title prefixes.
    """
    out: List[PiiEntity] = []
    for e in entities:
        if e.type != "PERSON":
            out.append(e)
            continue
        s, t = e.start, e.end
        if language == "sk":
            s2 = _strip_prefix_tokens(text, s, t, SK_SALUTATIONS | SK_TITLES)
        elif language == "de":
            s2 = _strip_prefix_tokens(text, s, t, DE_SALUTATIONS | DE_TITLES)
        else:
            s2 = s
        if s2 != s and s2 < t:
            out.append(PiiEntity(type=e.type, start=s2, end=t, value=text[s2:t], score=e.score, detector=e.detector))
        else:
            out.append(e)
    return out
