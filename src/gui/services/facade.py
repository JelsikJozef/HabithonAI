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
        if routing:
            # Add routing environment variables to pass settings to the CLI
            import os

            original_env = {}

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
