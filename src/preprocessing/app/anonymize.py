# filepath: /Users/jozefjelsik/PycharmProjects/HabithonAI/src/preprocessing/app/anonymize.py
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class AnonymizeResult:
    document_id: str
    src_path: str
    anonymized_text: str
    mappings: list[dict[str, Any]]
    context_id: str | None


def anonymize_translated_document(
    document_id: str,
    en_md_path: str | Path,
    *,
    context_id: str | None = None,
    tenant_id: str | None = None,
    language: str = "en",
    # Optional dependency injection for testing/extensibility
    detectors: Any | None = None,
    crypto: Any | None = None,
    vault: Any | None = None,
    anonymize_func: Callable[..., Any] | None = None,
) -> AnonymizeResult:
    """Anonymize a translated English Markdown file using deterministic hashing.

    Inputs:
    - document_id: human-readable label for the document (e.g., "md::rel/path.md").
    - en_md_path: path to the English Markdown file to anonymize
    - context_id: variant-scoped Token Vault namespace. When ``None`` it is derived from the
      English source text via :func:`derive_context_id` (variant ``"en"``), so the same id
      is reproducible later (e.g. during deanonymization) from the still-present EN file.

    Behavior:
    - Loads the Markdown text from disk
    - Calls anonymization.domain.anonymizer.anonymize(text, detectors, crypto, vault, ...)
    - Returns anonymized text and token mappings; does not write files

    Error modes:
    - Raises exceptions from I/O or anonymization call; caller decides policy

    """
    from src.shared.hashing import derive_context_id

    p = Path(en_md_path)
    text = p.read_text(encoding="utf-8")
    ctx_id = context_id if context_id is not None else derive_context_id(text, "en")

    # Lazy import defaults when not injected
    if anonymize_func is None:
        try:
            from anonymization.domain.anonymizer import anonymize as _anon  # type: ignore
        except Exception as e:  # pragma: no cover - surfaced to caller
            raise RuntimeError(f"Anonymization service unavailable: {e}")
        anonymize_func = _anon

    if detectors is None or crypto is None or vault is None:
        # Build defaults from adapters
        try:
            from anonymization.adapters.container import build_default as _build  # type: ignore
            from anonymization.adapters.crypto.crypto import Crypto as _Crypto  # type: ignore
        except Exception as e:  # pragma: no cover - surfaced to caller
            raise RuntimeError(f"Anonymization adapters unavailable: {e}")
        dets, vlt = _build()
        detectors = detectors or dets
        vault = vault or vlt
        crypto = crypto or _Crypto()

    res = anonymize_func(
        text,
        detectors,
        crypto,
        vault,
        context_id=ctx_id,
        tenant_id=tenant_id,
        language=language,
    )

    # Normalize output to primitives
    if isinstance(res, dict):
        anonymized_text = str(res.get("pseudonymized_text", ""))
        mappings = list(res.get("mappings", []) or [])
    else:
        anonymized_text = str(getattr(res, "pseudonymized_text", ""))
        mappings = list(getattr(res, "mappings", []) or [])

    # Coerce mappings to dicts with token/type/value keys when objects
    norm_maps: list[dict[str, Any]] = []
    for m in mappings:
        if isinstance(m, dict):
            norm_maps.append(
                {
                    "token": m.get("token"),
                    "value": m.get("value"),
                    "type": m.get("type"),
                }
            )
        else:
            norm_maps.append(
                {
                    "token": getattr(m, "token", None),
                    "value": getattr(m, "value", None),
                    "type": getattr(m, "type", None),
                }
            )

    return AnonymizeResult(
        document_id=document_id,
        src_path=str(p.resolve()),
        anonymized_text=anonymized_text,
        mappings=norm_maps,
        context_id=ctx_id,
    )
