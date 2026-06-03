"""Clean adapter: MarkdownDoc -> Step1Inputs.

Pure field mapping with no extra logic. Maps the Markdown domain model onto the
Step 1 normalization input DTO so a converted/translated document can be fed into
``run_step1`` without any inference, validation, or side effects.
"""

from __future__ import annotations

from typing import Any

from src.preprocessing.app.normalize import Step1Inputs
from src.preprocessing.domain.models_markdown import MarkdownDoc

# Explicit variant mapping per spec; None/other -> "orig" (Step1Inputs default).
_VARIANT_MAP = {"original": "orig", "english": "en"}

# Stable metadata keys carried into Step 1; all other meta keys are dropped.
_META_KEYS = ("doc_type", "category", "language", "anonymizer_versions")


def markdown_doc_to_step1_inputs(
    doc: MarkdownDoc,
    context: dict[str, Any] | None = None,
) -> Step1Inputs:
    """Map a :class:`MarkdownDoc` to :class:`Step1Inputs`.

    Pure field mapping, no extra logic:
    - ``text_md`` -> ``text``
    - ``meta`` -> only ``doc_type/category/language/anonymizer_versions`` (others dropped)
    - ``context`` (parameter, default ``{}``) -> ``context``
    - ``variant``: "original" -> "orig", "english" -> "en", otherwise "orig"
    """
    meta = {k: doc.meta[k] for k in _META_KEYS if k in doc.meta}
    variant = _VARIANT_MAP.get(doc.variant or "", "orig")
    return Step1Inputs(
        text=doc.text_md,
        meta=meta,
        context=context or {},
        variant=variant,
    )
