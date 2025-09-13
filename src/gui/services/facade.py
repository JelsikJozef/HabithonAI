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

    def translate_run(
        self,
        cfg: Mapping[str, Any],
        *,
        make_english: bool,
        translate_only: bool,
        translator: str | None = None,
    ) -> dict[str, Any]:
        """Run translate stage via the CLI in-process.

        Returns a dict with keys: code (int), report (dict | None).
        """
        try:
            from preprocessing.presentation import cli as _cli  # type: ignore
        except Exception as e:  # pragma: no cover - environment import issue
            return {"code": 3, "error": f"CLI unavailable: {e}", "report": None}

        # Assemble argv from cfg
        argv: list[str] = [
            "--src",
            str(cfg.get("src", "")),
            "--out",
            str(cfg.get("out", "")),
            "--workers",
            str(cfg.get("runtime", {}).get("workers", 1)),
        ]
        # Overwrite/skip
        ow = bool(cfg.get("write", {}).get("overwrite", False))
        argv.append("--overwrite" if ow else "--skip-existing")
        # Recurse
        if bool(cfg.get("scan", {}).get("recurse", True)):
            argv.append("--recurse")
        else:
            argv.append("--no-recurse")
        # Logging/progress
        ll = str(cfg.get("ui", {}).get("log_level", "INFO"))
        pr = str(cfg.get("ui", {}).get("progress", "auto"))
        argv += ["--log-level", ll, "--progress", pr]

        # Translation flags
        if make_english:
            argv.append("--make-english")
        if translate_only:
            argv.append("--translate-only")
        if translator:
            argv += ["--translator", translator]

        # Optional report: use outputs/logs/cli_run.json under CWD
        import json, os
        from datetime import datetime as _dt

        report_path = os.path.join(
            os.getcwd(),
            "outputs",
            "logs",
            f"gui_cli_run_{_dt.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        argv += ["--report", report_path]

        code = _cli.main(argv)

        payload: dict[str, Any] | None = None
        try:
            if os.path.exists(report_path):
                payload = json.loads(open(report_path, "r", encoding="utf-8").read())
        except Exception:
            payload = None

        return {"code": int(code), "report": payload, "report_path": report_path}

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
