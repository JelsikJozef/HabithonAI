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
from anonymization.adapters.crypto.crypto import Crypto
from anonymization.domain.anonymizer import anonymize as domain_anonymize


# --- Small utility: write JSON diagnostics to outputs/logs ---
def _write_langid_log(payload: dict[str, Any]) -> str:
    import os, json
    from datetime import datetime as _dt

    logs_dir = os.path.join(os.getcwd(), "outputs", "logs")
    try:
        os.makedirs(logs_dir, exist_ok=True)
    except Exception:
        # Best-effort: if mkdir fails, write next to CWD
        logs_dir = os.getcwd()
    ts = _dt.now().strftime("%Y%m%d_%H%M%S_%f")
    path = os.path.join(logs_dir, f"gui_lang_detect_{ts}.json")
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
    except Exception:
        # Swallow logging errors silently; return intended path for reference
        pass
    return path


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

        # Advanced routing overrides from cfg (new)
        routing = dict(cfg.get("routing", {})) if isinstance(cfg.get("routing"), Mapping) else {}
        original_env = {}
        if routing:
            # Add routing environment variables to pass settings to the CLI
            import os

            # Set routing environment variables
            tau_low = routing.get("tau_low")
            if isinstance(tau_low, (int, float)):
                key = "HABITHON_ROUTING_TAU_LOW"
                original_env[key] = os.environ.get(key)
                os.environ[key] = str(float(tau_low))

            delta_close = routing.get("delta_close")
            if isinstance(delta_close, (int, float)):
                key = "HABITHON_ROUTING_DELTA_CLOSE"
                original_env[key] = os.environ.get(key)
                os.environ[key] = str(float(delta_close))

            tau_en = routing.get("tau_en")
            if isinstance(tau_en, (int, float)):
                key = "HABITHON_ROUTING_TAU_EN"
                original_env[key] = os.environ.get(key)
                os.environ[key] = str(float(tau_en))

            similarity_noop = routing.get("similarity_noop_threshold")
            if isinstance(similarity_noop, (int, float)):
                key = "HABITHON_ROUTING_SIM_NOOP"
                original_env[key] = os.environ.get(key)
                os.environ[key] = str(float(similarity_noop))

            probe = routing.get("probe", {})
            if isinstance(probe, dict):
                probe_k = probe.get("k")
                if isinstance(probe_k, int):
                    key = "HABITHON_ROUTING_PROBE_K"
                    original_env[key] = os.environ.get(key)
                    os.environ[key] = str(int(probe_k))

                probe_slice = probe.get("slice_chars")
                if isinstance(probe_slice, int):
                    key = "HABITHON_ROUTING_PROBE_SLICE_CHARS"
                    original_env[key] = os.environ.get(key)
                    os.environ[key] = str(int(probe_slice))

            max_retries = routing.get("max_retries")
            if isinstance(max_retries, int):
                key = "HABITHON_ROUTING_MAX_RETRIES"
                original_env[key] = os.environ.get(key)
                os.environ[key] = str(int(max_retries))

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

        # Execute CLI with routing environment variables set
        try:
            code = _cli.main(argv)
        finally:
            # Restore original environment variables
            if routing:
                for key, original_value in original_env.items():
                    if original_value is None:
                        os.environ.pop(key, None)
                    else:
                        os.environ[key] = original_value

        payload: dict[str, Any] | None = None
        try:
            if os.path.exists(report_path):
                payload = json.loads(open(report_path, "r", encoding="utf-8").read())
        except Exception:
            payload = None

        return {"code": int(code), "report": payload, "report_path": report_path}

    # --- Language detection (enhanced) ---
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
            - Uses FastText LID (lid.176.bin) with the same defaults as the pipeline.

        Returns:
            dict with keys: {"path", "lang", "confidence", "engine", "chars_used"}.
        """
        from pathlib import Path
        from datetime import datetime as _dt
        import os, sys, platform

        # Prepare diagnostics
        diag: dict[str, Any] = {
            "started": _dt.now().astimezone().isoformat(),
            "mode": "basic",
            "file": str(file_path),
        }

        # 1) Load settings and detector (same as CLI)
        try:
            from preprocessing import settings_translation as st  # type: ignore
            from preprocessing.adapters.langid.fasttext_langid import FastTextLangId  # type: ignore
            from preprocessing.domain.errors import DomainError  # type: ignore
        except Exception as e:
            diag["error"] = {"stage": "import", "exc": repr(e)}
            log_path = _write_langid_log(diag)
            return {"error": f"Language detector unavailable: {e} (see {log_path})"}

        langid_cfg = dict(st.TRANSLATION.get("langid", {}))
        model_path = str(langid_cfg.get("model_path"))
        model_abs = os.path.abspath(model_path)
        cfg_candidates = [c.lower() for c in (candidates or langid_cfg.get("candidates", []))]
        if isinstance(max_chars, int) and max_chars > 0:
            langid_cfg["max_chars"] = int(max_chars)
        if isinstance(min_chars, int) and min_chars > 0:
            langid_cfg["min_chars"] = int(min_chars)

        diag["config"] = {
            "model_path": model_path,
            "model_abspath": model_abs,
            "max_chars": int(langid_cfg.get("max_chars", 5000) or 5000),
            "min_chars": int(langid_cfg.get("min_chars", 50) or 50),
            "candidates": list(cfg_candidates or []),
        }
        diag["env"] = {
            "python": sys.version,
            "platform": platform.platform(),
        }
        try:
            import fasttext as _ft  # type: ignore

            diag["fasttext"] = {
                "available": True,
                "version": getattr(_ft, "__version__", "unknown"),
            }
        except Exception as e:
            diag["fasttext"] = {"available": False, "import_exc": e.__class__.__name__}

        _ftdet = FastTextLangId(
            model_path,
            max_chars=int(langid_cfg.get("max_chars", 5000) or 5000),
            min_chars=int(langid_cfg.get("min_chars", 50) or 50),
            candidates=list(cfg_candidates or []),
        )

        # 2) Read or convert file to Markdown text
        p = Path(str(file_path))
        if not p.exists() or not p.is_file():
            diag["error"] = {"stage": "open", "message": f"File not found: {file_path}"}
            log_path = _write_langid_log(diag)
            return {"error": f"File not found: {file_path} (see {log_path})"}
        ext = p.suffix.lower().lstrip(".")
        md_text = ""
        diag["file_info"] = {
            "ext": ext,
            "size": int(p.stat().st_size),
            "mtime": _dt.fromtimestamp(p.stat().st_mtime).isoformat(),
        }
        try:
            if ext in {"md", "markdown", "txt"}:
                md_text = p.read_text(encoding="utf-8", errors="ignore")
            else:
                from preprocessing.domain.models import RawDocument  # type: ignore

                size = p.stat().st_size
                mtime = _dt.fromtimestamp(p.stat().st_mtime)
                raw = RawDocument(path=p, size=size, mtime=mtime, ext=ext)
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
                    diag["error"] = {
                        "stage": "convert",
                        "message": f"Unsupported file type: .{ext}",
                    }
                    log_path = _write_langid_log(diag)
                    return {"error": f"Unsupported file type: .{ext} (see {log_path})"}
                parsed = parser.parse(raw)
                md_text = getattr(parsed, "text_md", "")
        except Exception as e:
            diag["error"] = {"stage": "convert", "exc": repr(e)}
            log_path = _write_langid_log(diag)
            return {"error": f"Failed to read/convert file: {e} (see {log_path})"}

        # 3) Detect language with no fallback
        try:
            # Proactively load to capture load errors distinctly
            _ftdet.load()
            lang, conf = _ftdet.detect(
                md_text, hints={"candidates": cfg_candidates, "path": str(p)}
            )
            # Collect adapter stats if available
            stats = getattr(_ftdet, "_last_stats", None)
            if isinstance(stats, dict):
                diag["adapter_stats"] = stats
            diag["result"] = {"lang": lang, "confidence": float(conf)}
            diag["status"] = "OK"
            log_path = _write_langid_log(diag)
        except Exception as e:
            # Capture DomainError details if present
            err: dict[str, Any] = {"exc": e.__class__.__name__, "str": str(e)}
            if hasattr(e, "code"):
                err["code"] = getattr(e, "code")
            if hasattr(e, "message"):
                err["message"] = getattr(e, "message")
            if hasattr(e, "details"):
                err["details"] = getattr(e, "details")
            diag["error"] = {"stage": "detect", **err}
            diag["status"] = "ERROR"
            log_path = _write_langid_log(diag)
            return {"error": f"Detection failed: {e} (see {log_path})"}

        return {
            "path": str(p),
            "lang": str(diag["result"]["lang"]),
            "confidence": float(diag["result"]["confidence"]),
            "engine": "fasttext-lid176",
            "chars_used": int(diag.get("adapter_stats", {}).get("chars_used", len(md_text or ""))),
            "log": log_path,
        }

    def lang_detect_topk(
        self,
        file_path: str,
        *,
        k: int = 5,
        candidates: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
    ) -> dict[str, Any]:
        """Return top-k language candidates and selection for a single document.

        Returns a dict with keys: {path, lang_code, confidence, topk, flags, chars_used}.
        """
        from pathlib import Path
        from datetime import datetime as _dt
        import os, sys, platform

        diag: dict[str, Any] = {
            "started": _dt.now().astimezone().isoformat(),
            "mode": "topk",
            "file": str(file_path),
        }

        try:
            from preprocessing import settings_translation as st  # type: ignore
            from preprocessing.adapters.langid.fasttext_langid import FastTextLangId  # type: ignore
        except Exception as e:
            diag["error"] = {"stage": "import", "exc": repr(e)}
            log_path = _write_langid_log(diag)
            return {"error": f"Language detector unavailable: {e} (see {log_path})"}

        langid_cfg = dict(st.TRANSLATION.get("langid", {}))
        model_path = str(langid_cfg.get("model_path"))
        model_abs = os.path.abspath(model_path)
        cfg_candidates = [c.lower() for c in (candidates or langid_cfg.get("candidates", []))]
        if isinstance(max_chars, int) and max_chars > 0:
            langid_cfg["max_chars"] = int(max_chars)
        if isinstance(min_chars, int) and min_chars > 0:
            langid_cfg["min_chars"] = int(min_chars)
        diag["config"] = {
            "model_path": model_path,
            "model_abspath": model_abs,
            "max_chars": int(langid_cfg.get("max_chars", 5000) or 5000),
            "min_chars": int(langid_cfg.get("min_chars", 50) or 50),
            "candidates": list(cfg_candidates or []),
            "k": int(k) if k is not None else 5,
        }
        diag["env"] = {"python": sys.version, "platform": platform.platform()}
        try:
            import fasttext as _ft  # type: ignore

            diag["fasttext"] = {
                "available": True,
                "version": getattr(_ft, "__version__", "unknown"),
            }
        except Exception as e:
            diag["fasttext"] = {"available": False, "import_exc": e.__class__.__name__}

        detector = FastTextLangId(
            model_path,
            max_chars=int(langid_cfg.get("max_chars", 5000) or 5000),
            min_chars=int(langid_cfg.get("min_chars", 50) or 50),
            candidates=list(cfg_candidates or []),
        )

        p = Path(str(file_path))
        if not p.exists() or not p.is_file():
            diag["error"] = {"stage": "open", "message": f"File not found: {file_path}"}
            log_path = _write_langid_log(diag)
            return {"error": f"File not found: {file_path} (see {log_path})"}
        ext = p.suffix.lower().lstrip(".")
        md_text = ""
        try:
            if ext in {"md", "markdown", "txt"}:
                md_text = p.read_text(encoding="utf-8", errors="ignore")
            else:
                from preprocessing.domain.models import RawDocument  # type: ignore

                size = p.stat().st_size
                mtime = _dt.fromtimestamp(p.stat().st_mtime)
                raw = RawDocument(path=p, size=size, mtime=mtime, ext=ext)
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
                    diag["error"] = {
                        "stage": "convert",
                        "message": f"Unsupported file type: .{ext}",
                    }
                    log_path = _write_langid_log(diag)
                    return {"error": f"Unsupported file type: .{ext} (see {log_path})"}
                parsed = parser.parse(raw)
                md_text = getattr(parsed, "text_md", "")
        except Exception as e:
            diag["error"] = {"stage": "convert", "exc": repr(e)}
            log_path = _write_langid_log(diag)
            return {"error": f"Failed to read/convert file: {e} (see {log_path})"}

        try:
            detector.load()
            result = detector.detect_topk(
                md_text,
                k=int(k) if k is not None else 5,
                hints={"candidates": cfg_candidates, "path": str(p)},
            )
            stats = getattr(detector, "_last_stats", None)
            if isinstance(stats, dict):
                diag["adapter_stats"] = dict(stats)
            diag["result"] = result
            diag["status"] = "OK"
            log_path = _write_langid_log(diag)
        except Exception as e:
            err: dict[str, Any] = {"exc": e.__class__.__name__, "str": str(e)}
            if hasattr(e, "code"):
                err["code"] = getattr(e, "code")
            if hasattr(e, "message"):
                err["message"] = getattr(e, "message")
            if hasattr(e, "details"):
                err["details"] = getattr(e, "details")
            diag["error"] = {"stage": "detect_topk", **err}
            diag["status"] = "ERROR"
            log_path = _write_langid_log(diag)
            return {"error": f"Detection failed: {e} (see {log_path})"}

        return {
            "path": str(p),
            "lang_code": result.get("lang_code"),
            "confidence": result.get("confidence"),
            "topk": result.get("topk", []),
            "flags": result.get("flags", {}),
            "chars_used": len(md_text or ""),
            "log": log_path,
        }

    def lang_detect_advanced_routing(
        self,
        file_path: str,
        *,
        k: int = 5,
        candidates: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
        routing_config: Mapping[str, Any] | None = None,
        enable_translation_test: bool = False,
    ) -> dict[str, Any]:
        """Analyze language with top-k and simple routing heuristics.

        Returns dict with keys: path, lang_code, confidence, topk, flags, chars_used,
        routing={probe_triggered, selected_src, probe_reason?, translation_test?}.
        """
        from pathlib import Path
        from datetime import datetime as _dt
        import os, sys, platform

        diag: dict[str, Any] = {
            "started": _dt.now().astimezone().isoformat(),
            "mode": "advanced",
            "file": str(file_path),
            "routing_config": dict(routing_config or {}),
        }

        try:
            from preprocessing import settings_translation as st  # type: ignore
            from preprocessing.adapters.langid.fasttext_langid import FastTextLangId  # type: ignore
            from preprocessing.adapters.langid.english_detector_fasttext import (  # type: ignore
                FastTextEnglishDetector,
            )
        except Exception as e:
            diag["error"] = {"stage": "import", "exc": repr(e)}
            log_path = _write_langid_log(diag)
            return {"error": f"Language detector unavailable: {e} (see {log_path})"}

        langid_cfg = dict(st.TRANSLATION.get("langid", {}))
        model_path = str(langid_cfg.get("model_path"))
        model_abs = os.path.abspath(model_path)
        cfg_candidates = [c.lower() for c in (candidates or langid_cfg.get("candidates", []))]
        if isinstance(max_chars, int) and max_chars > 0:
            langid_cfg["max_chars"] = int(max_chars)
        if isinstance(min_chars, int) and min_chars > 0:
            langid_cfg["min_chars"] = int(min_chars)

        diag["config"] = {
            "model_path": model_path,
            "model_abspath": model_abs,
            "max_chars": int(langid_cfg.get("max_chars", 5000) or 5000),
            "min_chars": int(langid_cfg.get("min_chars", 50) or 50),
            "candidates": list(cfg_candidates or []),
            "k": int(k) if k is not None else 5,
        }
        diag["env"] = {"python": sys.version, "platform": platform.platform()}
        try:
            import fasttext as _ft  # type: ignore

            diag["fasttext"] = {
                "available": True,
                "version": getattr(_ft, "__version__", "unknown"),
            }
        except Exception as e:
            diag["fasttext"] = {"available": False, "import_exc": e.__class__.__name__}

        detector = FastTextLangId(
            model_path,
            max_chars=int(langid_cfg.get("max_chars", 5000) or 5000),
            min_chars=int(langid_cfg.get("min_chars", 50) or 50),
            candidates=list(cfg_candidates or []),
        )

        p = Path(str(file_path))
        if not p.exists() or not p.is_file():
            diag["error"] = {"stage": "open", "message": f"File not found: {file_path}"}
            log_path = _write_langid_log(diag)
            return {"error": f"File not found: {file_path} (see {log_path})"}
        ext = p.suffix.lower().lstrip(".")
        md_text = ""
        try:
            if ext in {"md", "markdown", "txt"}:
                md_text = p.read_text(encoding="utf-8", errors="ignore")
            else:
                from preprocessing.domain.models import RawDocument  # type: ignore

                size = p.stat().st_size
                mtime = _dt.fromtimestamp(p.stat().st_mtime)
                raw = RawDocument(path=p, size=size, mtime=mtime, ext=ext)
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
                    diag["error"] = {
                        "stage": "convert",
                        "message": f"Unsupported file type: .{ext}",
                    }
                    log_path = _write_langid_log(diag)
                    return {"error": f"Unsupported file type: .{ext} (see {log_path})"}
                parsed = parser.parse(raw)
                md_text = getattr(parsed, "text_md", "")
        except Exception as e:
            diag["error"] = {"stage": "convert", "exc": repr(e)}
            log_path = _write_langid_log(diag)
            return {"error": f"Failed to read/convert file: {e} (see {log_path})"}

        # Base top-k detection
        try:
            detector.load()
            base = detector.detect_topk(
                md_text,
                k=int(k) if k is not None else 5,
                hints={"candidates": cfg_candidates, "path": str(p)},
            )
            diag["base"] = base
            stats = getattr(detector, "_last_stats", None)
            if isinstance(stats, dict):
                diag["adapter_stats"] = dict(stats)
        except Exception as e:
            err: dict[str, Any] = {"exc": e.__class__.__name__, "str": str(e)}
            if hasattr(e, "code"):
                err["code"] = getattr(e, "code")
            if hasattr(e, "message"):
                err["message"] = getattr(e, "message")
            if hasattr(e, "details"):
                err["details"] = getattr(e, "details")
            diag["error"] = {"stage": "detect_topk", **err}
            diag["status"] = "ERROR"
            log_path = _write_langid_log(diag)
            return {"error": f"Detection failed: {e} (see {log_path})"}

        lang_code = base.get("lang_code")
        confidence = float(base.get("confidence", 0.0) or 0.0)
        topk_list = list(base.get("topk", []))
        flags = dict(base.get("flags", {}))

        # Routing heuristics
        rc = dict(routing_config or {})
        tau_low = float(rc.get("tau_low", 0.70))
        delta_close = float(rc.get("delta_close", 0.05))
        tau_en = float(rc.get("tau_en", 0.90))
        sim_noop = float(rc.get("similarity_noop_threshold", 0.92))

        probe_triggered = confidence < tau_low
        probe_reason = "low_confidence" if probe_triggered else None
        if not probe_triggered and len(topk_list) >= 2:
            # close top-2 by raw score delta
            close = abs(float(topk_list[0]["score"]) - float(topk_list[1]["score"])) < delta_close
            if close:
                probe_triggered = True
                probe_reason = "close_top2"
        selected_src = str(lang_code or "")

        routing: dict[str, Any] = {
            "probe_triggered": bool(probe_triggered),
            "selected_src": selected_src,
        }
        if probe_triggered and probe_reason:
            routing["probe_reason"] = probe_reason

        # Optional translation quality test (approximation without translating)
        if enable_translation_test:
            try:
                en_det = FastTextEnglishDetector(model_path)
                en_conf = float(en_det.english_confidence(md_text))
            except Exception:
                en_conf = 0.0

            # Simple token-overlap similarity as a placeholder (0..1)
            def _tok_set(s: str) -> set[str]:
                return set([t for t in (s or "").lower().split() if t.isalpha() or t.isalnum()])

            tokens = _tok_set(md_text)
            # Without actual translation, treat similarity as 0 (unknown) to avoid false positives
            sim = 0.0 if not tokens else 0.0
            passed = (en_conf >= tau_en) and (sim <= sim_noop)
            routing["translation_test"] = {
                "en_confidence": en_conf,
                "similarity": sim,
                "passed": bool(passed),
            }

        diag["result"] = {
            "lang_code": lang_code,
            "confidence": confidence,
            "topk": topk_list,
            "flags": flags,
            "routing": routing,
        }
        diag["status"] = "OK"
        log_path = _write_langid_log(diag)

        return {
            "path": str(p),
            "lang_code": lang_code,
            "confidence": confidence,
            "topk": topk_list,
            "flags": flags,
            "chars_used": len(md_text or ""),
            "routing": routing,
            "log": log_path,
        }

    def anon_build(self):
        return build_default()

    def _detectors_meta(self, detectors: list[Any]) -> list[dict[str, Any]]:
        """Return lightweight metadata about configured detectors for display.

        Attempts to extract supported languages / fallback info for PresidioDetector, and
        provides a stable name for others.
        """
        out: list[dict[str, Any]] = []
        for d in detectors:
            meta: dict[str, Any] = {"name": getattr(d, "name", d.__class__.__name__.lower())}
            # Presidio specifics
            if meta["name"] == "presidio":
                # These attrs are internal; guard with getattr
                meta["supported_languages"] = list(getattr(d, "_supported_langs", []) or [])
                meta["fallback_language"] = getattr(d, "_fallback_lang", None)
                init_error = getattr(d, "_init_error", None)
                if init_error:
                    meta["init_error"] = str(init_error)
                meta["regex_fallback_enabled"] = not bool(
                    getattr(getattr(d, "settings", None), "disable_regex_fallback", False)
                )
            elif meta["name"] == "regex":
                meta["patterns"] = ["EMAIL", "PHONE"]  # kept in sync with RegexDetector docstring
            out.append(meta)
        return out

    def anon_detect(self, text: str, language: str | None = None) -> dict[str, Any]:
        detectors, _ = build_default()
        meta = self._detectors_meta(detectors)
        r = detect_all(text, detectors, language=language)
        entities = [e.__dict__ for e in r.entities]
        # quick per-detector counts
        counts: dict[str, int] = {}
        for e in entities:
            det = e.get("detector") or "?"
            counts[det] = counts.get(det, 0) + 1
        return {
            "text": r.text,
            "entities": entities,
            "detectors": meta,
            "detector_counts": counts,
            "language_hint": language,
        }

    def anon_pseudonymize(
        self, text: str, context_id: str, language: str | None = None
    ) -> dict[str, Any]:
        detectors, vault = build_default()
        meta = self._detectors_meta(detectors)
        r = pseudonymize(text, detectors, vault, context_id=context_id, language=language)
        mappings = [m.__dict__ for m in r.mappings]
        # mapping stats
        by_type: dict[str, int] = {}
        for m in mappings:
            t = m.get("type") or "?"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "original_text": r.original_text,
            "pseudonymized_text": r.pseudonymized_text,
            "mappings": mappings,
            "context_id": context_id,
            "language_hint": language,
            "detectors": meta,
            "mapping_counts": by_type,
        }

    def anon_deanonymize(self, text: str, context_id: str) -> dict[str, Any]:
        _detectors, vault = build_default()
        r = deanonymize(text, vault, context_id=context_id)
        used = [m.__dict__ for m in r.mappings_used]
        by_type: dict[str, int] = {}
        for m in used:
            t = m.get("type") or "?"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "anonymized_text": r.anonymized_text,
            "restored_text": r.restored_text,
            "mappings_used": used,
            "context_id": context_id,
            "mapping_counts": by_type,
        }

    def anon_anonymize(
        self,
        text: str,
        *,
        context_id: str,
        tenant_id: str | None = None,
        language: str | None = None,
    ) -> dict[str, Any]:
        """Deterministic anonymization using HMAC hash tokens.

        Replaces detected PII with deterministic hash tokens and persists mappings in the vault.
        """
        detectors, vault = build_default()
        meta = self._detectors_meta(detectors)
        crypto = Crypto()
        r = domain_anonymize(
            text,
            detectors,
            crypto,
            vault,
            context_id=context_id,
            tenant_id=tenant_id,
            language=language,
        )
        mappings = [m.__dict__ for m in r.mappings]
        by_type: dict[str, int] = {}
        for m in mappings:
            t = m.get("type") or "?"
            by_type[t] = by_type.get(t, 0) + 1
        return {
            "original_text": r.original_text,
            "anonymized_text": r.pseudonymized_text,
            "mappings": mappings,
            "tenant_id": tenant_id,
            "context_id": context_id,
            "language_hint": language,
            "detectors": meta,
            "mapping_counts": by_type,
        }

    def anon_presidio_readiness(self) -> dict[str, Any]:
        """Return a diagnostic snapshot of Presidio / spaCy readiness.

        Checks:
        - Import of presidio_analyzer
        - Import of spaCy
        - Availability (loadable) of configured spaCy models in PiiSettings
        - Whether regex fallback is enabled

        Returns a dict with keys:
        {
          'presidio_imported': bool,
          'presidio_error': str | None,
          'spacy_imported': bool,
          'spacy_error': str | None,
          'models': [ {name, installed, error?} ],
          'supported_languages_configured': [...],
          'fallback_model': str | None,
          'regex_fallback_enabled': bool,
          'ready': bool,
          'suggested_commands': [str, ...],
        }
        """
        from anonymization.app.config.pii_settings import PiiSettings
        import os

        settings = PiiSettings.from_env()

        presidio_imported = False
        presidio_error: str | None = None
        try:
            import presidio_analyzer  # type: ignore  # noqa: F401

            presidio_imported = True
        except Exception as e:  # pragma: no cover - environment dependent
            presidio_error = str(e)

        spacy_imported = False
        spacy_error: str | None = None
        try:
            import spacy  # type: ignore

            spacy_imported = True
        except Exception as e:  # pragma: no cover
            spacy_error = str(e)
            spacy = None  # type: ignore

        models_checked: list[dict[str, Any]] = []
        missing_models: list[str] = []
        attempted: set[str] = set()
        if spacy_imported:
            # Unique list of configured + fallback
            all_models = list(
                dict.fromkeys(list(settings.language_models.values()) + [settings.fallback_model])
            )
            for model in all_models:
                if not model or model in attempted:
                    continue
                attempted.add(model)
                ok = False
                err: str | None = None
                try:  # pragma: no cover - depends on local environment
                    import spacy as _sp

                    _sp.load(model)
                    ok = True
                except Exception as e:
                    err = str(e)
                    missing_models.append(model)
                models_checked.append({"name": model, "installed": ok, "error": err})

        regex_fallback_enabled = not settings.disable_regex_fallback

        ready = (
            presidio_imported
            and spacy_imported
            and all(m.get("installed") for m in models_checked if m.get("name"))
        )

        suggested_cmds: list[str] = []
        if not presidio_imported:
            suggested_cmds.append("pip install presidio-analyzer presidio-recognizers spacy")
        if spacy_imported and missing_models:
            for m in missing_models:
                # spaCy model downloads typically via python -m spacy download
                suggested_cmds.append(f"python -m spacy download {m}")
        elif not spacy_imported:
            suggested_cmds.append("pip install spacy")

        env_hint = {
            k: os.environ.get(k)
            for k in [
                "ANON_PRESIDIO_LANGS",
                "ANON_PRESIDIO_FALLBACK_MODEL",
                "ANON_PRESIDIO_DISABLE_FALLBACK",
                "ANON_PRESIDIO_PATTERNS",
            ]
            if os.environ.get(k) is not None
        }

        return {
            "presidio_imported": presidio_imported,
            "presidio_error": presidio_error,
            "spacy_imported": spacy_imported,
            "spacy_error": spacy_error,
            "models": models_checked,
            "supported_languages_configured": sorted(settings.language_models.keys()),
            "fallback_model": settings.fallback_model,
            "regex_fallback_enabled": regex_fallback_enabled,
            "ready": ready,
            "suggested_commands": suggested_cmds,
            "env": env_hint,
        }

    # --- Batch Anonymization (new) -------------------------------------------------
    def anon_batch_plan(self, cfg: Mapping[str, Any]) -> dict[str, Any]:
        """Plan which Markdown files would be anonymized.

        cfg keys expected:
            src (str): source folder
            out (str): output folder
            recurse (bool)
            overwrite (bool)
            mode (str): 'deterministic' | 'pseudonymize'
            include_ext (list[str]) optional (default ['.md'])
            skip_suffixes (list[str]) optional (default anonymized suffixes)
        """
        import os
        from datetime import datetime as _dt

        src = str(cfg.get("src") or "").strip()
        out = str(cfg.get("out") or "").strip()
        recurse = bool(cfg.get("recurse", True))
        overwrite = bool(cfg.get("overwrite", False))
        mode = (cfg.get("mode") or "deterministic").lower()
        include_ext = list(cfg.get("include_ext") or [".md"])  # only .md by default
        skip_suffixes = list(
            cfg.get("skip_suffixes") or [".anonymized.md", ".pseudonymized.md", ".restored.md"]
        )

        matched: list[dict[str, Any]] = []
        would_process = 0
        would_skip_existing = 0

        if not src or not os.path.isdir(src):
            return {"error": f"Invalid source folder: {src}"}
        if not out:
            return {"error": "Output folder not specified"}
        if os.path.abspath(src) == os.path.abspath(out):  # safety
            return {"error": "Source and Output folders must differ"}

        for root, dirs, files in os.walk(src):
            for fn in files:
                low = fn.lower()
                # skip already anonymized outputs
                if any(low.endswith(sfx) for sfx in skip_suffixes):
                    continue
                ext = os.path.splitext(low)[1]
                if ext not in include_ext:
                    continue
                src_path = os.path.join(root, fn)
                rel = os.path.relpath(src_path, src)
                base_no_ext = os.path.splitext(rel)[0]
                suffix = ".anonymized.md" if mode == "deterministic" else ".pseudonymized.md"
                out_path = os.path.join(out, base_no_ext + suffix)
                exists = os.path.exists(out_path)
                will = (not exists) or overwrite
                matched.append(
                    {
                        "src": src_path,
                        "rel": rel,
                        "out": out_path,
                        "exists": exists,
                        "will_process": will,
                    }
                )
                if will:
                    would_process += 1
                else:
                    would_skip_existing += 1
            if not recurse:
                break

        return {
            "src": src,
            "out": out,
            "mode": mode,
            "matched": len(matched),
            "would_process": would_process,
            "would_skip_existing": would_skip_existing,
            "overwrite": overwrite,
            "started_at": _dt.now().isoformat(),
        }

    def anon_batch_run(self, cfg: Mapping[str, Any]) -> dict[str, Any]:
        """Run batch anonymization over a folder of Markdown files.

        Returns summary with counts and minimal per-file statuses.
        """
        import os, traceback
        from datetime import datetime as _dt
        from shared.hashing import document_fingerprint

        plan = self.anon_batch_plan(cfg)
        if plan.get("error"):
            return plan

        src = plan["src"]
        out = plan["out"]
        mode = plan["mode"]
        overwrite = bool(cfg.get("overwrite", False))
        language = cfg.get("language") or None
        tenant_id = cfg.get("tenant_id") or None

        try:
            os.makedirs(out, exist_ok=True)
        except Exception:
            return {"error": f"Failed to create output directory: {out}"}

        detectors, vault = build_default()
        crypto = Crypto()

        processed_ok = 0
        skipped_existing = 0
        failed = 0
        files_status: list[dict[str, Any]] = []
        by_type: dict[str, int] = {}

        # Recompute iterable of entries (need detailed info)
        # (Re-run listing quickly to get consistent order)
        entries = []
        include_ext = [".md"]
        skip_suffixes = [".anonymized.md", ".pseudonymized.md", ".restored.md"]
        recurse = bool(cfg.get("recurse", True))
        for root, dirs, files in os.walk(src):
            for fn in files:
                low = fn.lower()
                if any(low.endswith(sfx) for sfx in skip_suffixes):
                    continue
                ext = os.path.splitext(low)[1]
                if ext not in include_ext:
                    continue
                src_path = os.path.join(root, fn)
                rel = os.path.relpath(src_path, src)
                base_no_ext = os.path.splitext(rel)[0]
                suffix = ".anonymized.md" if mode == "deterministic" else ".pseudonymized.md"
                out_path = os.path.join(out, base_no_ext + suffix)
                exists = os.path.exists(out_path)
                entries.append((src_path, rel, out_path, exists))
            if not recurse:
                break

        for src_path, rel, out_path, exists in entries:
            if exists and not overwrite:
                skipped_existing += 1
                files_status.append({"rel": rel, "status": "skipped_exists"})
                continue
            # Ensure subdirectory path exists
            out_dir = os.path.dirname(out_path)
            try:
                os.makedirs(out_dir, exist_ok=True)
            except Exception:
                failed += 1
                files_status.append({"rel": rel, "status": "error", "error": "mkdir_failed"})
                continue
            try:
                with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
                    text = f.read()
                # Derive stable context id
                st = os.stat(src_path)
                fp = document_fingerprint(src_path, size_bytes=st.st_size, mtime=st.st_mtime)
                ctx_id = f"ctx_{fp[:16]}"
                if mode == "deterministic":
                    res = domain_anonymize(
                        text,
                        detectors,
                        crypto,
                        vault,
                        context_id=ctx_id,
                        tenant_id=tenant_id,
                        language=language,
                    )
                    out_text = res.pseudonymized_text
                else:
                    res = pseudonymize(text, detectors, vault, context_id=ctx_id, language=language)
                    out_text = res.pseudonymized_text
                # Aggregate mapping types
                for m in res.mappings:
                    t = m.type or "?"
                    by_type[t] = by_type.get(t, 0) + 1
                with open(out_path, "w", encoding="utf-8") as wf:
                    wf.write(out_text)
                processed_ok += 1
                files_status.append({"rel": rel, "status": "ok", "context_id": ctx_id})
            except Exception as e:  # pragma: no cover - runtime safety
                failed += 1
                files_status.append(
                    {
                        "rel": rel,
                        "status": "error",
                        "error": str(e),
                        "trace": traceback.format_exc(limit=1),
                    }
                )

        ended_at = _dt.now().isoformat()
        return {
            "mode": mode,
            "src": src,
            "out": out,
            "processed_ok": processed_ok,
            "skipped_existing": skipped_existing,
            "failed": failed,
            "total": processed_ok + skipped_existing + failed,
            "mapping_counts": by_type,
            "files": files_status[:200],  # cap to avoid huge payload
            "ended_at": ended_at,
        }
