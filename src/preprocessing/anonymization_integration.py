from __future__ import annotations

from dataclasses import replace
from typing import Any, Callable, Iterable

from .domain.models import ParsedDocument


class AnonymizationBridge:
    """Bridge to use anonymization package use-cases within preprocessing.

    Constructor accepts either callables or concrete use-case instances. The callables/instances
    must be pre-configured to run detection/pseudonymization when invoked with text (and optionally language).

    - detect_pii: Callable[[str], list[dict]] | object with .run(text) or .detect(text)
    - pseudonymize: Callable[[str], dict] | object with .run(text) or .pseudonymize(text)
    - policy: any object/dict; when policy indicates text should be included, we add text_pseudo.
    """

    def __init__(
        self,
        detect_pii: Callable[..., Any] | Any,
        pseudonymize: Callable[..., Any] | Any,
        *,
        policy: Any,
    ) -> None:
        self._detect = detect_pii
        self._pseudonymize = pseudonymize
        self._policy = policy

    def run(self, doc: ParsedDocument) -> ParsedDocument:
        text = doc.text or ""
        language = doc.language
        # Run detection
        try:
            det_res = self._call_detect(text, language)
        except Exception:
            det_res = []
        entities = self._normalize_entities(det_res)
        pii_count = len(entities)

        # Run pseudonymization
        pseudo_text = ""
        mappings: list[dict[str, Any]] = []
        try:
            pseudo_res = self._call_pseudonymize(text, language)
            pseudo_text, mappings = self._normalize_pseudonymize(pseudo_res)
        except Exception:
            pseudo_text, mappings = "", []
        token_spans = [{"token": m.get("token"), "type": m.get("type")} for m in mappings if m.get("token")]
        changed = bool(pseudo_text) and pseudo_text != text

        # Merge metadata
        meta = dict(doc.metadata)
        meta.update({
            "pii_entities": entities,
            "pii_count": pii_count,
            "token_spans": token_spans,
            "pseudonymized": changed,
        })
        if self._include_text():
            meta["text_pseudo"] = pseudo_text if changed else text

        return replace(doc, metadata=meta)

    # Internals
    def _call_detect(self, text: str, language: str | None) -> Any:
        d = self._detect
        # Try common interfaces without logging sensitive text
        if hasattr(d, "run") and callable(getattr(d, "run")):
            try:
                return d.run(text=text, language=language)
            except TypeError:
                return d.run(text)
        if hasattr(d, "detect") and callable(getattr(d, "detect")):
            try:
                return d.detect(text, language=language)
            except TypeError:
                return d.detect(text)
        if callable(d):
            try:
                return d(text=text, language=language)
            except TypeError:
                return d(text)
        return []

    def _call_pseudonymize(self, text: str, language: str | None) -> Any:
        p = self._pseudonymize
        if hasattr(p, "run") and callable(getattr(p, "run")):
            try:
                return p.run(text=text, language=language)
            except TypeError:
                return p.run(text)
        if hasattr(p, "pseudonymize") and callable(getattr(p, "pseudonymize")):
            try:
                return p.pseudonymize(text, language=language)
            except TypeError:
                return p.pseudonymize(text)
        if callable(p):
            try:
                return p(text=text, language=language)
            except TypeError:
                return p(text)
        return {"pseudonymized_text": "", "mappings": []}

    @staticmethod
    def _normalize_entities(result: Any) -> list[dict[str, Any]]:
        # Expected shapes:
        # - list[dict]
        # - DetectionResult with .entities (list of PiiEntity)
        # - list[PiiEntity]
        ents: list[dict[str, Any]] = []
        if result is None:
            return ents
        # DetectionResult-like
        if hasattr(result, "entities"):
            items = getattr(result, "entities")
        else:
            items = result
        if isinstance(items, dict):
            # Single entity dict
            items = [items]
        for e in items or []:
            if isinstance(e, dict):
                ent = {
                    "type": e.get("type"),
                    "start": int(e.get("start", 0)),
                    "end": int(e.get("end", 0)),
                    "value": e.get("value"),
                    "score": e.get("score"),
                    "detector": e.get("detector"),
                }
            else:
                # Object with attributes
                ent = {
                    "type": getattr(e, "type", None),
                    "start": int(getattr(e, "start", 0)),
                    "end": int(getattr(e, "end", 0)),
                    "value": getattr(e, "value", None),
                    "score": getattr(e, "score", None),
                    "detector": getattr(e, "detector", None),
                }
            ents.append(ent)
        return ents

    @staticmethod
    def _normalize_pseudonymize(result: Any) -> tuple[str, list[dict[str, Any]]]:
        # Expected shapes:
        # - dict with keys pseudonymized_text and mappings (list[TokenMapping|dict])
        # - PseudonymizationResult with .pseudonymized_text and .mappings
        if result is None:
            return "", []
        if isinstance(result, dict):
            text = str(result.get("pseudonymized_text", ""))
            maps = result.get("mappings", []) or []
        else:
            text = str(getattr(result, "pseudonymized_text", ""))
            maps = getattr(result, "mappings", []) or []
        norm_maps: list[dict[str, Any]] = []
        for m in maps:
            if isinstance(m, dict):
                norm_maps.append({"token": m.get("token"), "value": m.get("value"), "type": m.get("type")})
            else:
                norm_maps.append({"token": getattr(m, "token", None), "value": getattr(m, "value", None), "type": getattr(m, "type", None)})
        return text, norm_maps

    def _include_text(self) -> bool:
        p = self._policy
        # Check dict-like
        if isinstance(p, dict):
            return bool(p.get("include_text", False))
        # Check attribute
        if hasattr(p, "include_text"):
            try:
                return bool(getattr(p, "include_text"))
            except Exception:
                return False
        return False

