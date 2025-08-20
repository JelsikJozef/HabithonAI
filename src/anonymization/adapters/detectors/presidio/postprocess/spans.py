from typing import List
from anonymization.domain.entities import PiiEntity


def trim_whitespace(entities: List[PiiEntity], text: str) -> List[PiiEntity]:
    """Trim leading/trailing whitespace from entity spans.

    Parameters
    - entities: List of PiiEntity to adjust.
    - text: Full source text used to compute trimmed values and offsets.

    Returns
    - New list of PiiEntity with whitespace-trimmed start/end and updated values.
    """
    out: List[PiiEntity] = []
    for e in entities:
        s, e_end = e.start, e.end
        # left trim
        while s < e_end and text[s].isspace():
            s += 1
        # right trim
        while e_end > s and text[e_end - 1].isspace():
            e_end -= 1
        if s == e.start and e_end == e.end:
            out.append(e)
        else:
            out.append(PiiEntity(type=e.type, start=s, end=e_end, value=text[s:e_end], score=e.score, detector=e.detector))
    return out
