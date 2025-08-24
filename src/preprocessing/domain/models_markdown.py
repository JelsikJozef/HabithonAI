"""
Domain models for Markdown artifacts used across the preprocessing pipeline.

This module defines a canonical, serialization-friendly Markdown document model
(`MarkdownDoc`) and related type aliases and utilities. The model is designed to
be minimal, stable, and safe to persist or exchange between components. It
intentionally avoids framework-specific dependencies and focuses on explicit
invariants:

- Markdown text must be UTF-8 safe and use LF ("\n") newlines by the time it
  leaves the normalizer.
- Metadata must be JSON-serializable.
- The model is immutable; use `copy_with()` to produce a modified instance.

Determinism and Error Handling
------------------------------
Consumers should rely on deterministic `to_dict()` ordering for hashing/auditing
and on the validation utility to enforce invariants at the domain boundary. This
module does not raise adapter/vendor exceptions; callers should map failures to
`preprocessing.domain.errors.NormalizationError` or other domain errors upstream.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from typing import Any, Dict, Literal, Mapping, MutableMapping, Optional
import json

__all__ = [
    "LanguageCode",
    "NormalizationReport",
    "MarkdownStats",
    "MarkdownDoc",
    "validate_markdown_doc",
]


# -----------------------------
# Type aliases (documented)
# -----------------------------

LanguageCode = str
"""Represents a BCP-47/ISO language code.

Examples:
    - "en"
    - "sk"
    - "de-AT"
"""

NormalizationReport = Dict[str, Any]
"""Normalization report attached under ``meta["normalization"]``.

The concrete shape is defined by the encoding/normalization adapter. The value
must be JSON-serializable.
"""

MarkdownStats = Dict[str, int]
"""Optional counters summarizing Markdown structure.

Possible keys include ``headings``, ``tables``, ``images``, and ``links``. The
set is not fixed and may be extended by adapters.
"""


# -----------------------------
# Data model
# -----------------------------


@dataclass(frozen=True, slots=True)
class MarkdownDoc:
    """Immutable, serialization-friendly Markdown artifact.

    Description:
        A lightweight data object representing one Markdown document and its
        minimal metadata. The object is immutable; use :meth:`copy_with` to
        create a modified copy.

    Invariants:
        - ``text_md`` is UTF-8 encodable with strict errors and uses only LF
          ("\n") line endings (no CR/LFCR).
        - ``encoding`` is the canonical string literal ``"utf-8"`` once the
          normalizer has finalized the text.
        - ``meta`` is JSON-serializable (no bytes, pathlib objects, or callables).

    Args:
        doc_id: Stable identifier provided by upstream components.
        path: Absolute source file path of the original input (not the output .md).
        variant: Optional pipeline variant label such as ``"original"`` or
            ``"english"``. May be ``None`` for convert-only flows.
        lang: Optional BCP-47/ISO language code when known; may be ``None``.
        text_md: Markdown content string with LF newlines.
        encoding: Expected to be ``"utf-8"`` once normalized.
        meta: Freeform, JSON-serializable metadata from adapters.

    Notes:
        The class is frozen and uses ``slots`` for memory efficiency and
        hashability. Use :meth:`copy_with` for changes.
    """

    doc_id: str
    path: str
    variant: Optional[Literal["original", "english"]]
    lang: Optional[LanguageCode]
    text_md: str
    encoding: Literal["utf-8"] = "utf-8"
    meta: Dict[str, Any] = field(default_factory=dict)

    def copy_with(self, **changes: Any) -> "MarkdownDoc":
        """Return a new instance with selected fields replaced.

        Args:
            **changes: Field overrides by name.

        Returns:
            MarkdownDoc: A new instance reflecting the provided changes.

        Notes:
            This method preserves immutability by creating a new object using
            ``dataclasses.replace`` under the hood.
        """

        return replace(self, **changes)

    def to_dict(self, *, redacted: bool = False) -> Dict[str, Any]:
        """Serialize the document to a deterministic plain ``dict``.

        Args:
            redacted: When ``True``, redact potentially sensitive values in
                ``meta`` using a conservative, best-effort strategy.

        Returns:
            dict: A JSON-serializable dictionary with a stable key order.

        Notes:
            - Keys are emitted in a deterministic order to ensure reproducible
              hashing/auditing. The ordering is ``doc_id, path, variant, lang,
              encoding, text_md, meta``.
            - Redaction is shallow and conservative; callers with stricter
              requirements should implement domain-specific redaction upstream.
        """

        meta_obj: Mapping[str, Any] | MutableMapping[str, Any] = self.meta
        if redacted:
            meta_obj = _redact_meta(meta_obj)

        # Deterministic key ordering
        return {
            "doc_id": self.doc_id,
            "path": self.path,
            "variant": self.variant,
            "lang": self.lang,
            "encoding": self.encoding,
            "text_md": self.text_md,
            "meta": dict(meta_obj),
        }

    def size_bytes(self) -> int:
        """Return the size of ``text_md`` when encoded as UTF-8.

        Returns:
            int: Number of bytes of the UTF-8 encoding of ``text_md``.
        """

        return len(self.text_md.encode("utf-8"))

    def summary(self) -> str:
        """Return a short, human-readable summary string.

        Returns:
            str: A compact summary including ``doc_id``, ``variant/lang``, and
            size in bytes; may include notable metadata flags.

        Notes:
            The output is deterministic for the same input.
        """

        v = self.variant or "-"
        l = self.lang or "-"
        size = self.size_bytes()
        flags: list[str] = []
        m = self.meta or {}
        if isinstance(m, Mapping):
            if m.get("warnings"):
                flags.append("warn")
            if m.get("assets_to_copy") or m.get("assets_to_write"):
                flags.append("assets")
            if m.get("normalization"):
                flags.append("norm")
        suffix = f" ({','.join(flags)})" if flags else ""
        return f"{self.doc_id} [{v}|{l}] {size}B{suffix}"


# -----------------------------
# Validation utilities
# -----------------------------


def validate_markdown_doc(doc: MarkdownDoc) -> None:
    """Validate invariants for a :class:`MarkdownDoc` instance.

    Args:
        doc: The document to validate.

    Raises:
        ValueError: If any invariant is violated, including:
            - ``encoding`` is not ``"utf-8"``.
            - ``text_md`` contains CR ("\r") newlines or NUL ("\x00").
            - ``text_md`` is not strictly UTF-8 encodable.
            - ``meta`` is not JSON-serializable.

    Notes:
        This function is intentionally strict and should be called at domain
        boundaries (e.g., before serialization). It does not attempt to repair
        invalid content.
    """

    if doc.encoding != "utf-8":
        raise ValueError("encoding must be 'utf-8' once normalized")

    txt = doc.text_md
    if "\r" in txt:
        raise ValueError("text_md must use LF newlines only (found CR)")
    if "\x00" in txt:
        raise ValueError("text_md must not contain NUL characters")

    # Ensure strict UTF-8 encodability
    try:
        _ = txt.encode("utf-8", errors="strict")
    except UnicodeEncodeError as e:
        raise ValueError(f"text_md is not strictly UTF-8 encodable: {e}") from e

    # Ensure meta serializability
    try:
        json.dumps(doc.meta)
    except TypeError as e:
        raise ValueError(f"meta is not JSON-serializable: {e}") from e


# -----------------------------
# Internal helpers
# -----------------------------


def _redact_meta(meta: Mapping[str, Any] | MutableMapping[str, Any]) -> Dict[str, Any]:
    """Return a shallowly redacted copy of ``meta``.

    Args:
        meta: Original metadata mapping.

    Returns:
        dict: A shallow copy with values for common sensitive keys replaced with
        ``"[redacted]"``.

    Notes:
        Redaction is best-effort and shallow by design. It avoids mutating the
        input mapping and guarantees JSON-serializability of the output.
    """

    sensitive_keys: set[str] = {
        "path",
        "email",
        "token",
        "access_token",
        "api_key",
        "secret",
        "id_number",
    }
    out: Dict[str, Any] = {}
    for k, v in dict(meta).items():
        if isinstance(k, str) and k.lower() in sensitive_keys:
            out[k] = "[redacted]"
        else:
            out[k] = v
    return out

