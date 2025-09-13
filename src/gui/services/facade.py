from __future__ import annotations

"""GUI service facade: thin wrappers around app-layer functions.

This isolates Qt views from application logic and keeps call contracts simple.
"""

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping

from preprocessing.app import convert_only
from anonymization.adapters.container import build_default
from anonymization.app.detect import detect_all
from anonymization.app.pseudonymize import pseudonymize
from anonymization.app.denomize import deanonymize


def _to_dict(obj: Any) -> Any:
    if is_dataclass(obj):
        return asdict(obj)
    if isinstance(obj, (list, tuple)):
        return [_to_dict(x) for x in obj]
    if isinstance(obj, dict):
        return {k: _to_dict(v) for k, v in obj.items()}
    return obj


class GuiServices:
    """Synchronous service calls; views should offload to worker threads when long-running."""

    # --- Preprocessing ---
    def convert_plan(self, cfg: Mapping[str, Any]) -> dict[str, Any]:
        plan = convert_only.plan_folder(cfg["src"], cfg["out"], cfg)
        return _to_dict(plan)

    def convert_run(self, cfg: Mapping[str, Any]) -> dict[str, Any]:
        res = convert_only.convert_folder(cfg["src"], cfg["out"], cfg)
        return _to_dict(res)

    # --- Anonymization ---
    def anon_build(self):
        return build_default()

    def anon_detect(self, text: str, language: str | None = None) -> dict[str, Any]:
        detectors, _ = build_default()
        r = detect_all(text, detectors, language=language)
        return {"text": r.text, "entities": [e.__dict__ for e in r.entities]}

    def anon_pseudonymize(
        self, text: str, context_id: str, language: str | None = None
    ) -> dict[str, Any]:
        detectors, vault = build_default()
        r = pseudonymize(text, detectors, vault, context_id=context_id, language=language)
        return {
            "original_text": r.original_text,
            "pseudonymized_text": r.pseudonymized_text,
            "mappings": [m.__dict__ for m in r.mappings],
        }

    def anon_deanonymize(self, text: str, context_id: str) -> dict[str, Any]:
        _detectors, vault = build_default()
        r = deanonymize(text, vault, context_id=context_id)
        return {
            "anonymized_text": r.anonymized_text,
            "restored_text": r.restored_text,
            "mappings_used": [m.__dict__ for m in r.mappings_used],
        }
