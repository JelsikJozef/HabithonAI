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

        # LangID overrides from cfg (optional)
        langid = dict(cfg.get("langid", {})) if isinstance(cfg.get("langid"), Mapping) else {}
        cands = langid.get("candidates")
        if isinstance(cands, (list, tuple)) and cands:
            argv += [
                "--lang-candidates",
                ",".join([str(c).strip().lower() for c in cands if str(c).strip()]),
            ]
        mx = langid.get("max_chars")
        if isinstance(mx, int) and mx > 0:
            argv += ["--lang-max-chars", str(mx)]
        mn = langid.get("min_chars")
        if isinstance(mn, int) and mn > 0:
            argv += ["--lang-min-chars", str(mn)]

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

    # --- Language detection (new) ---
    def lang_detect_file(
        self,
        file_path: str,
        *,
        candidates: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
    ) -> dict[str, Any]:
        """Detect language of a single document using the pipeline's detector.

        Behavior:
            - Converts non-Markdown inputs to Markdown in-memory using existing adapters.
            - Uses FastText LID (lid.176.bin) with the same defaults as the pipeline,
              with a deterministic heuristic fallback when FastText is unavailable.

        Returns:
            dict with keys: {"path", "lang", "confidence", "engine", "chars_used"}.
        """
        import os
        from pathlib import Path
        from datetime import datetime as _dt
        from typing import Any as _Any

        # 1) Load settings and detector (same as CLI)
        try:
            from preprocessing import settings_translation as st  # type: ignore
            from preprocessing.adapters.langid.fasttext_langid import FastTextLangId  # type: ignore
            from preprocessing.domain.ports import LanguageDetectError as _LangDetectErr  # type: ignore
        except Exception as e:
            return {"error": f"Language detector unavailable: {e}"}

        langid_cfg = dict(st.TRANSLATION.get("langid", {}))
        model_path = str(langid_cfg.get("model_path"))
        # Override candidates if provided
        cfg_candidates = [c.lower() for c in (candidates or langid_cfg.get("candidates", []))]
        # Apply optional window overrides
        if isinstance(max_chars, int) and max_chars > 0:
            langid_cfg["max_chars"] = int(max_chars)
        if isinstance(min_chars, int) and min_chars > 0:
            langid_cfg["min_chars"] = int(min_chars)

        _ft = FastTextLangId(
            model_path,
            max_chars=int(langid_cfg.get("max_chars", 5000) or 5000),
            min_chars=int(langid_cfg.get("min_chars", 50) or 50),
            candidates=list(cfg_candidates or []),
        )

        class _HeuristicLangId:
            def detect(
                self,
                text: str,
                hints: dict[str, _Any] | None = None,
                *,
                context: dict[str, _Any] | None = None,
            ) -> tuple[str, float]:
                s = (text or "")[: max(0, int(langid_cfg.get("max_chars", 5000)))].lower()
                if any(tok in s for tok in [" der ", " die ", " und ", " ist ", " nicht "]):
                    return "de", 0.80
                if any(tok in s for tok in [" a je ", " že ", " nie ", " pre ", " ktoré "]):
                    return "sk", 0.75
                if any(tok in s for tok in [" a je ", " že ", " není ", " pro ", " které "]):
                    return "cs", 0.70
                if any(tok in s for tok in [" oraz ", " nie ", " jest ", " ale "]):
                    return "pl", 0.70
                if any(tok in s for tok in [" és ", " nem ", " van ", " hogy "]):
                    return "hu", 0.70
                if any(tok in s for tok in [" the ", " and ", " is ", " not ", " for "]):
                    return "en", 0.85
                return "en", 0.50

        class _FallbackLangId:
            def __init__(self, primary: _Any, fallback: _Any) -> None:
                self._p = primary
                self._f = fallback

            def detect(
                self,
                text: str,
                hints: dict[str, _Any] | None = None,
                *,
                context: dict[str, _Any] | None = None,
            ) -> tuple[str, float]:
                try:
                    return self._p.detect(text, hints, context=context)
                except _LangDetectErr:
                    return self._f.detect(text, hints, context=context)

            def capabilities(self) -> dict:
                return {"name": "ft-with-heuristic-fallback", "deterministic": True}

        detector = _FallbackLangId(_ft, _HeuristicLangId())

        # 2) Read or convert file to Markdown text
        p = Path(str(file_path))
        if not p.exists() or not p.is_file():
            return {"error": f"File not found: {file_path}"}
        ext = p.suffix.lower().lstrip(".")
        md_text = ""
        try:
            if ext in {"md", "markdown", "txt"}:
                md_text = p.read_text(encoding="utf-8", errors="ignore")
            else:
                # Build a RawDocument and route to appropriate adapter
                from preprocessing.domain.models import RawDocument  # type: ignore

                size = p.stat().st_size
                mtime = _dt.fromtimestamp(p.stat().st_mtime)
                raw = RawDocument(path=p, size=size, mtime=mtime, ext=ext)
                # Minimal adapter mapping (reuse existing offline adapters)
                parser = None
                if ext == "docx":
                    from preprocessing.adapters.parsers.docx_to_md import DocxToMd as _Docx  # type: ignore

                    parser = _Docx()
                elif ext == "pdf":
                    from preprocessing.adapters.parsers.pdf_to_md import PdfToMd as _Pdf  # type: ignore

                    parser = _Pdf()
                elif ext == "xlsx":
                    from preprocessing.adapters.parsers.xlsx_to_md import XlsxToMd as _Xlsx  # type: ignore

                    parser = _Xlsx()  # type: ignore
                elif ext == "msg":
                    from preprocessing.adapters.parsers.msg_to_md import MsgToMd as _Msg  # type: ignore

                    parser = _Msg()
                elif ext in {"jpg", "jpeg"}:
                    from preprocessing.adapters.parsers.jpg_to_md import JpgToMd as _Jpg  # type: ignore

                    parser = _Jpg()  # type: ignore
                else:
                    return {"error": f"Unsupported file type: .{ext}"}
                parsed = parser.parse(raw)
                # Adapters return MarkdownDoc or a fallback with .text_md
                md_text = getattr(parsed, "text_md", "")
        except Exception as e:
            return {"error": f"Failed to read/convert file: {e}"}

        # 3) Detect language
        try:
            lang, conf = detector.detect(
                md_text, hints={"candidates": cfg_candidates, "path": str(p)}
            )
        except Exception as e:
            return {"error": f"Detection failed: {e}"}

        engine = getattr(detector, "capabilities", lambda: {"name": "unknown"})()
        return {
            "path": str(p),
            "lang": str(lang),
            "confidence": float(conf),
            "engine": engine.get("name") if isinstance(engine, dict) else str(engine),
            "chars_used": len(md_text or ""),
        }

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
