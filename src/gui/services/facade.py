from __future__ import annotations

"""GUI service facade: thin wrappers around app-layer functions.

This isolates Qt views from application logic and keeps call contracts simple.
"""

from dataclasses import asdict, is_dataclass
from typing import Any, Mapping, Callable

from preprocessing.app import convert_only
from anonymization.adapters.container import build_default
from anonymization.app.detect import detect_all
from anonymization.app.pseudonymize import pseudonymize
from anonymization.app.denomize import deanonymize
from anonymization.adapters.crypto.crypto import Crypto
from anonymization.domain.anonymizer import anonymize as domain_anonymize
from shared.llm.openai_client import summarize_keywords, OpenAIClientError  # FIXED import


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


def _is_cancelled(should_cancel: Callable[[], bool] | None) -> bool:
    try:
        return bool(should_cancel and should_cancel())
    except Exception:
        return False


class GuiServices:
    """Synchronous service calls; views should offload to worker threads when long-running."""

    # --- Preprocessing ---
    def convert_plan(
        self,
        cfg: Mapping[str, Any],
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Compute a dry-run plan for Convert without writing any files.

        Returns a dict with keys: candidates (list) and summary {matched, would_convert, would_skip_existing}.
        """
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        scan = dict(cfg.get("scan", {})) if isinstance(cfg.get("scan"), Mapping) else {}
        progress and progress(
            "Plan: scanning source with options -> "
            f"src={cfg.get('src')} out={cfg.get('out')} recurse={bool(scan.get('recurse', True))}"
        )
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        plan = convert_only.plan_folder(cfg["src"], cfg["out"], cfg)
        if _is_cancelled(should_cancel):
            return {"cancelled": True, "partial": _to_dict(plan)}
        # Brief summary to logs
        try:
            sm = plan.summary
            progress and progress(
                f"Plan summary: matched={sm.matched} would_convert={sm.would_convert} would_skip_existing={sm.would_skip_existing}"
            )
        except Exception:
            pass
        return _to_dict(plan)

    def convert_run(
        self,
        cfg: Mapping[str, Any],
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        # Provide richer context on what will happen
        scan = dict(cfg.get("scan", {})) if isinstance(cfg.get("scan"), Mapping) else {}
        write = dict(cfg.get("write", {})) if isinstance(cfg.get("write"), Mapping) else {}
        runtime = dict(cfg.get("runtime", {})) if isinstance(cfg.get("runtime"), Mapping) else {}
        progress and progress(
            "Convert: starting with options -> "
            f"src={cfg.get('src')} out={cfg.get('out')} recurse={bool(scan.get('recurse', True))} "
            f"overwrite={bool(write.get('overwrite', False))} workers={int(runtime.get('workers', 1))}"
        )
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        # Note: convert_only.convert_folder is not cancellable internally; will complete current stage
        res = convert_only.convert_folder(cfg["src"], cfg["out"], cfg)
        try:
            progress and progress(
                f"Convert summary: matched={res.matched} ok={res.converted_ok} "
                f"skip={res.skipped_existing} failed={res.failed}"
            )
        except Exception:
            pass
        progress and progress("Convert finished.")
        if _is_cancelled(should_cancel):
            return {"cancelled": True, "partial": _to_dict(res)}
        return _to_dict(res)

    def translate_run(
        self,
        cfg: Mapping[str, Any],
        *,
        make_english: bool,
        translate_only: bool,
        translator: str | None = None,
        progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Run translate stage via the CLI in a subprocess with cancellable streaming.

        Returns a dict with keys: code (int), report (dict | None), report_path (str).
        """
        import json, os, sys, shlex, subprocess
        from datetime import datetime as _dt

        # Build argv (module mode): python -m preprocessing.presentation.cli ...
        argv: list[str] = [
            sys.executable,
            "-m",
            "preprocessing.presentation.cli",
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
        pr = str(cfg.get("ui", {}).get("progress", "plain"))  # plain for easier streaming
        argv += ["--log-level", ll, "--progress", pr]
        # Translation flags
        if make_english:
            argv.append("--make-english")
        if translate_only:
            argv.append("--translate-only")
        if translator:
            argv += ["--translator", translator]
        # LangID overrides
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

        # Routing env overrides
        routing = dict(cfg.get("routing", {})) if isinstance(cfg.get("routing"), Mapping) else {}
        original_env = {}
        env = os.environ.copy()
        if routing:

            def _set_env(key: str, val: str):
                original_env[key] = env.get(key)
                env[key] = val

            if (v := routing.get("tau_low")) is not None:
                _set_env("HABITHON_ROUTING_TAU_LOW", str(float(v)))
            if (v := routing.get("delta_close")) is not None:
                _set_env("HABITHON_ROUTING_DELTA_CLOSE", str(float(v)))
            if (v := routing.get("tau_en")) is not None:
                _set_env("HABITHON_ROUTING_TAU_EN", str(float(v)))
            if (v := routing.get("similarity_noop_threshold")) is not None:
                _set_env("HABITHON_ROUTING_SIM_NOOP", str(float(v)))
            probe = routing.get("probe") or {}
            if isinstance(probe, Mapping):
                if (v := probe.get("k")) is not None:
                    _set_env("HABITHON_ROUTING_PROBE_K", str(int(v)))
                if (v := probe.get("slice_chars")) is not None:
                    _set_env("HABITHON_ROUTING_PROBE_SLICE_CHARS", str(int(v)))
            if (v := routing.get("max_retries")) is not None:
                _set_env("HABITHON_ROUTING_MAX_RETRIES", str(int(v)))

        # Report path
        report_path = os.path.join(
            os.getcwd(),
            "outputs",
            "logs",
            f"gui_cli_run_{_dt.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        argv += ["--report", report_path]

        # Run as subprocess and stream logs
        progress and progress("CLI argv: " + " ".join(shlex.quote(a) for a in argv))
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        try:
            # Inherit env and ensure PYTHONPATH includes repo/src for local imports
            env = os.environ.copy()
            try:
                from pathlib import Path as _P

                repo_root = _P(__file__).resolve().parents[3]
                src_dir = str(repo_root / "src")
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = src_dir if not existing else f"{src_dir}:{existing}"
            except Exception:
                pass
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,
            )
        except Exception as e:
            return {
                "code": 3,
                "error": f"Failed to start CLI: {e}",
                "report": None,
                "report_path": report_path,
            }

        code = None
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.rstrip("\r\n")
                if line:
                    progress and progress(line)
                if _is_cancelled(should_cancel):
                    progress and progress(
                        "Cancellation requested: terminating translate subprocess…"
                    )
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break
            # Ensure process ended
            try:
                code = proc.wait(timeout=5)
            except Exception:
                code = proc.poll()
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
            # Restore env if we modified it (not strictly needed since we copied)
            pass

        # Load report if present
        payload: dict[str, Any] | None = None
        try:
            if os.path.exists(report_path):
                payload = json.loads(open(report_path, "r", encoding="utf-8").read())
        except Exception:
            payload = None

        if code is None:
            code = 137 if _is_cancelled(should_cancel) else 1
        progress and progress(f"Translate finished with exit code {int(code)}.")
        if _is_cancelled(should_cancel):
            return {
                "cancelled": True,
                "partial": {"code": int(code), "report": payload, "report_path": report_path},
            }
        return {"code": int(code), "report": payload, "report_path": report_path}

    # --- Language detection (enhanced) ---
    def lang_detect_file(
        self,
        file_path: str,
        *,
        candidates: list[str] | None = None,
        max_chars: int | None = None,
        min_chars: int | None = None,
        progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        from pathlib import Path
        from datetime import datetime as _dt
        import os, sys, platform

        progress and progress("Loading language detector…")
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

        progress and progress("Reading/normalizing input…")
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
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
        progress and progress(
            f"Input file: path={p} ext=.{ext} size={diag['file_info']['size']} bytes"
        )
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
        progress and progress(
            "Running FastText detection with "
            f"candidates={cfg_candidates or '-'} max_chars={int(langid_cfg.get('max_chars', 5000))} "
            f"min_chars={int(langid_cfg.get('min_chars', 50))}…"
        )
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
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

        progress and progress(
            f"Detection finished: lang={diag['result']['lang']} conf={float(diag['result']['confidence']):.3f}"
        )
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
        progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Return top-k language candidates and selection for a single document.

        Returns a dict with keys: {path, lang_code, confidence, topk, flags, chars_used}.
        """
        from pathlib import Path
        from datetime import datetime as _dt
        import os, sys, platform

        progress and progress("Loading detector and reading file…")
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

        progress and progress(
            f"Input file: path={p} ext=.{ext} size={int(p.stat().st_size)} bytes; detecting top-{int(k) if k is not None else 5}"
        )
        # 3) Detect language with no fallback
        progress and progress(
            "Running top-k detection with "
            f"candidates={cfg_candidates or '-'} max_chars={int(langid_cfg.get('max_chars', 5000))} "
            f"min_chars={int(langid_cfg.get('min_chars', 50))}…"
        )
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
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

        progress and progress(
            f"Top-k detection finished: primary={result.get('lang_code')} conf={float(result.get('confidence', 0.0) or 0.0):.3f}"
        )
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
        progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        from pathlib import Path
        from datetime import datetime as _dt
        import os, sys, platform

        diag: dict[str, Any] = {
            "started": _dt.now().astimezone().isoformat(),
            "mode": "advanced",
            "file": str(file_path),
            "routing_config": dict(routing_config or {}),
        }
        progress and progress("Loading detector and preparing analysis…")

        # 1) Load settings and detector (same as CLI)
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

        # Routing heuristics configuration (compute early for logging)
        rc = dict(routing_config or {})
        tau_low = float(rc.get("tau_low", 0.70))
        delta_close = float(rc.get("delta_close", 0.05))
        tau_en = float(rc.get("tau_en", 0.90))
        sim_noop = float(rc.get("similarity_noop_threshold", 0.92))

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

        progress and progress(
            f"Input file: path={p} ext=.{ext} size={p.stat().st_size} bytes; advanced routing k={int(k) if k is not None else 5}"
        )
        # 3) Detect language with no fallback
        progress and progress(
            "Running advanced top-k with routing heuristics … "
            f"candidates={cfg_candidates or '-'} tau_low={tau_low:.2f} delta_close={delta_close:.2f}"
        )
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
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
        routing: dict[str, Any] = {}
        probe_triggered = confidence < tau_low
        probe_reason = "low_confidence" if probe_triggered else None
        if not probe_triggered and len(topk_list) >= 2:
            # close top-2 by raw score delta
            close = abs(float(topk_list[0]["score"]) - float(topk_list[1]["score"])) < delta_close
            if close:
                probe_triggered = True
                probe_reason = "close_top2"
        selected_src = str(lang_code or "")

        routing.update(
            {
                "probe_triggered": bool(probe_triggered),
                "selected_src": selected_src,
            }
        )
        if probe_triggered and probe_reason:
            routing["probe_reason"] = probe_reason

        # Optional translation quality test (approximation without translating)
        if enable_translation_test:
            try:
                from preprocessing.adapters.langid.english_detector_fasttext import (  # type: ignore
                    FastTextEnglishDetector,
                )

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

        progress and progress(
            f"Advanced analysis finished: primary={lang_code} conf={confidence:.3f} probe={routing.get('probe_triggered')}"
        )
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

    def anon_detect(
        self,
        text: str,
        language: str | None = None,
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        progress and progress("Running PII detection…")
        detectors, _ = build_default()
        meta = self._detectors_meta(detectors)
        r = detect_all(text, detectors, language=language)
        entities = [e.__dict__ for e in r.entities]
        # quick per-detector counts
        counts: dict[str, int] = {}
        for e in entities:
            det = e.get("detector") or "?"
            counts[det] = counts.get(det, 0) + 1
        progress and progress(f"Detected {len(entities)} entities.")
        return {
            "text": r.text,
            "entities": entities,
            "detectors": meta,
            "detector_counts": counts,
            "language_hint": language,
        }

    def anon_pseudonymize(
        self,
        text: str,
        context_id: str,
        language: str | None = None,
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        progress and progress("Pseudonymizing text…")
        detectors, vault = build_default()
        meta = self._detectors_meta(detectors)
        r = pseudonymize(text, detectors, vault, context_id=context_id, language=language)
        mappings = [m.__dict__ for m in r.mappings]
        # mapping stats
        by_type: dict[str, int] = {}
        for m in mappings:
            t = m.get("type") or "?"
            by_type[t] = by_type.get(t, 0) + 1
        progress and progress(f"Produced {len(mappings)} mappings.")
        return {
            "original_text": r.original_text,
            "pseudonymized_text": r.pseudonymized_text,
            "mappings": mappings,
            "context_id": context_id,
            "language_hint": language,
            "detectors": meta,
            "mapping_counts": by_type,
        }

    def anon_deanonymize(
        self,
        text: str,
        context_id: str,
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        progress and progress("Restoring original text from mappings…")
        _detectors, vault = build_default()
        r = deanonymize(text, vault, context_id=context_id)
        used = [m.__dict__ for m in r.mappings_used]
        by_type: dict[str, int] = {}
        for m in used:
            t = m.get("type") or "?"
            by_type[t] = by_type.get(t, 0) + 1
        progress and progress(f"Used {len(used)} mappings.")
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
        progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        progress and progress("Deterministic anonymization…")
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
        progress and progress(f"Persisted {len(mappings)} mappings.")
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

    def anon_presidio_readiness(
        self,
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        import json, os, sys, subprocess, shlex

        progress and progress("Checking Presidio and spaCy dependencies (isolated)…")

        # Subprocess script: import presidio_analyzer/spacy and probe models safely
        script = r"""
import json, os, importlib.util
res = {
  "presidio_imported": False,
  "presidio_error": None,
  "spacy_imported": False,
  "spacy_error": None,
  "models": [],
  "supported_languages_configured": [],
  "fallback_model": None,
  "regex_fallback_enabled": (str(os.getenv("ANON_PRESIDIO_DISABLE_FALLBACK","0")).strip().lower() not in {"1","true","yes","on"}),
  "ready": False,
  "suggested_commands": [],
  "env": {}
}
# Config
langs = {"en":"en_core_web_sm","de":"de_core_news_sm","sk": os.getenv("ANON_PRESIDIO_FALLBACK_MODEL","xx_ent_wiki_sm") or "xx_ent_wiki_sm"}
# Allow overrides via env
spec = (os.getenv("ANON_PRESIDIO_LANGS") or "").strip()
for part in spec.split(","):
    part = part.strip()
    if not part:
        continue
    if ":" in part:
        k,v = part.split(":",1)
        langs[k.strip()] = v.strip()
fb = os.getenv("ANON_PRESIDIO_FALLBACK_MODEL") or "xx_ent_wiki_sm"
res["fallback_model"] = fb
res["supported_languages_configured"] = sorted(langs.keys())
# Import presidio
try:
    import presidio_analyzer  # noqa: F401
    res["presidio_imported"] = True
except Exception as e:
    res["presidio_error"] = str(e)
# Import spacy and probe models
try:
    import spacy as _sp
    res["spacy_imported"] = True
    attempted = set()
    all_models = list(dict.fromkeys(list(langs.values()) + [fb]))
    for m in all_models:
        if not m or m in attempted:
            continue
        attempted.add(m)
        ok = False
        err = None
        try:
            _sp.load(m)
            ok = True
        except Exception as ex:
            err = str(ex)
        res["models"].append({"name": m, "installed": ok, "error": err})
except Exception as e:
    res["spacy_error"] = str(e)
# Ready flag
res["ready"] = bool(res["presidio_imported"] and res["spacy_imported"] and all((m.get("installed") for m in res.get("models") or [])))
# Suggestions
if not res["presidio_imported"]:
    res["suggested_commands"].append("pip install presidio-analyzer spacy")
if res["spacy_imported"]:
    missing = [m["name"] for m in res.get("models") or [] if not m.get("installed")]
    for m in missing:
        res["suggested_commands"].append(f"python -m spacy download {m}")
else:
    res["suggested_commands"].append("pip install spacy")
# Env hints
for k in [
  "ANON_PRESIDIO_LANGS","ANON_PRESIDIO_FALLBACK_MODEL","ANON_PRESIDIO_DISABLE_FALLBACK","ANON_PRESIDIO_PATTERNS"
]:
    v = os.getenv(k)
    if v is not None:
        res.setdefault("env",{})[k] = v
print(json.dumps(res))
"""
        try:
            proc = subprocess.run(
                [sys.executable, "-c", script], capture_output=True, text=True, check=False
            )
        except Exception as e:
            return {"error": f"Failed to spawn readiness probe: {e}"}

        if _is_cancelled(should_cancel):
            return {"cancelled": True}

        out = (proc.stdout or "").strip()
        err = (proc.stderr or "").strip()
        if err:
            progress and progress("[probe-stderr] " + err.splitlines()[-1])
        if proc.returncode != 0:
            return {"error": f"Probe failed with code {proc.returncode}", "stderr": err}
        try:
            diag = json.loads(out) if out else {}
        except Exception as e:
            diag = {"error": f"Probe returned invalid JSON: {e}", "raw": out}
        progress and progress("Presidio readiness check finished.")
        return diag

    # --- Batch Anonymization (new) -------------------------------------------------
    def anon_batch_plan(
        self,
        cfg: Mapping[str, Any],
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
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

        progress and progress("Scanning source folder for Markdown files…")
        for root, dirs, files in os.walk(src):
            if _is_cancelled(should_cancel):
                break
            for fn in files:
                if _is_cancelled(should_cancel):
                    break
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
        cancelled = _is_cancelled(should_cancel)
        progress and progress(
            f"Plan: matched={len(matched)} would_process={would_process} would_skip={would_skip_existing}"
        )
        return {
            "src": src,
            "out": out,
            "mode": mode,
            "matched": len(matched),
            "would_process": would_process,
            "would_skip_existing": would_skip_existing,
            "overwrite": overwrite,
            "started_at": _dt.now().isoformat(),
            "cancelled": cancelled,
        }

    def anon_batch_run(
        self,
        cfg: Mapping[str, Any],
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        import os, sys, shlex, subprocess, json
        from datetime import datetime as _dt

        # Prepare report path for final JSON
        report_path = os.path.join(
            os.getcwd(),
            "outputs",
            "logs",
            f"gui_anon_batch_{_dt.now().strftime('%Y%m%d_%H%M%S')}.json",
        )
        try:
            os.makedirs(os.path.dirname(report_path), exist_ok=True)
        except Exception:
            pass

        # Build argv for subprocess CLI
        argv: list[str] = [
            sys.executable,
            "-m",
            "anonymization.presentation.batch_cli",
            "--src",
            str(cfg.get("src", "")),
            "--out",
            str(cfg.get("out", "")),
            "--mode",
            str((cfg.get("mode") or "deterministic")).lower(),
            "--report",
            report_path,
        ]
        if bool(cfg.get("recurse", True)):
            argv.append("--recurse")
        else:
            argv.append("--no-recurse")
        if bool(cfg.get("overwrite", False)):
            argv.append("--overwrite")
        if lang := (cfg.get("language") or None):
            argv += ["--language", str(lang)]
        if (tenant := (cfg.get("tenant_id") or None)) and str(
            (cfg.get("mode") or "")
        ).lower() == "deterministic":
            argv += ["--tenant", str(tenant)]

        # Launch subprocess and stream logs
        progress and progress("Batch CLI argv: " + " ".join(shlex.quote(a) for a in argv))
        if _is_cancelled(should_cancel):
            return {"cancelled": True}
        try:
            # Ensure PYTHONPATH includes repo/src for local imports
            env = os.environ.copy()
            try:
                from pathlib import Path as _P

                repo_root = _P(__file__).resolve().parents[3]
                src_dir = str(repo_root / "src")
                existing = env.get("PYTHONPATH", "")
                env["PYTHONPATH"] = src_dir if not existing else f"{src_dir}:{existing}"
            except Exception:
                pass
            proc = subprocess.Popen(
                argv,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=env,
                bufsize=1,
            )
        except Exception as e:
            return {"error": f"Failed to start batch CLI: {e}"}

        last_json_line: str | None = None
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                line = (line or "").rstrip("\r\n")
                if not line:
                    continue
                # Capture potential final JSON payload line while forwarding progress
                if line.startswith("{") and line.endswith("}"):
                    last_json_line = line
                else:
                    progress and progress(line)
                if _is_cancelled(should_cancel):
                    progress and progress("Cancellation requested: terminating batch subprocess…")
                    try:
                        proc.terminate()
                    except Exception:
                        pass
                    try:
                        proc.wait(timeout=5)
                    except Exception:
                        try:
                            proc.kill()
                        except Exception:
                            pass
                    break
            try:
                rc = proc.wait(timeout=5)
            except Exception:
                rc = proc.poll()
        finally:
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass

        if _is_cancelled(should_cancel):
            # Try to return partial report if available
            try:
                if os.path.exists(report_path):
                    payload = json.loads(open(report_path, "r", encoding="utf-8").read())
                    payload["cancelled"] = True
                    return payload
            except Exception:
                pass
            return {"cancelled": True}

        # Prefer report file; fallback to last JSON line
        payload: dict[str, Any] | None = None
        try:
            if os.path.exists(report_path):
                payload = json.loads(open(report_path, "r", encoding="utf-8").read())
        except Exception as e:
            progress and progress(f"Failed to read batch report: {e}")
        if payload is None and last_json_line:
            try:
                payload = json.loads(last_json_line)
            except Exception:
                pass
        if payload is None:
            return {"error": "Batch CLI did not produce a JSON result"}
        return payload

    def step3_run(
        self,
        document_uid: str,
        *,
        content_hash: str | None = None,
        model: str | None = None,
        timeout_s: int | None = None,
        progress: Callable[[str], None] | None = None,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Run Step 3 summarization/keywords for a given document UID.

        Returns the Step 3 result.json payload dict.
        """
        if not document_uid or not str(document_uid).strip():
            return {"error": "document_uid is required"}

        if should_cancel and should_cancel():
            return {"cancelled": True}

        try:
            from preprocessing.analysis.step3_summary import (
                run_step3,
                Step3Inputs,
                Step3Config,
            )
        except Exception as e:
            return {"error": f"Step 3 module unavailable: {e}"}

        cfg = Step3Config()
        if isinstance(model, str) and model.strip():
            cfg.model = model.strip()
        if isinstance(timeout_s, int) and timeout_s > 0:
            cfg.timeout_s = int(timeout_s)

        ctx = {"run_id": "gui"}

        progress and progress(f"Step3: preparing (uid={document_uid[:20]}...) ")
        if should_cancel and should_cancel():
            return {"cancelled": True}

        res = run_step3(
            Step3Inputs(
                document_uid=str(document_uid).strip(),
                content_hash=str(content_hash).strip() if content_hash else None,
                context=ctx,
                config=cfg,
            )
        )

        # Summarize outcome for the GUI log
        status = res.get("status")
        if status == "ok":
            progress and progress(
                "Step3: ok — summary and keywords written to artifacts (no content shown here)."
            )
        else:
            errs = ",".join(
                e.get("code", "?") for e in (res.get("errors") or []) if isinstance(e, dict)
            )
            progress and progress(f"Step3: failed — errors=[{errs}] (see outputs/logs for details)")

        if should_cancel and should_cancel():
            res = {"cancelled": True, "partial": res}

        return res

    def summarize_folder_run(
        self,
        cfg: Mapping[str, Any],
        progress: Callable[[str], None] | None = None,
        *,
        should_cancel: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        """Run LLM summarization over a folder of anonymized Markdown files.

        cfg keys:
          - src (str): source folder
          - pattern (str): glob pattern (e.g., "*.anonymized.md")
          - overwrite (bool): overwrite existing sidecars
          - model (str|None): OpenAI model name
          - timeout_s (int|None): per-file timeout seconds

        For each matched file, writes next to it:
          - <name>.summary.txt
          - <name>.keywords.json

        Returns a dict with counts and per-file statuses:
          {total, ok, skipped, failed, ended_at, files: [{rel, status, error?}], cancelled?}
        """
        from pathlib import Path
        from datetime import datetime as _dt
        import os, json

        src = str(cfg.get("src") or "").strip()
        pattern = str(cfg.get("pattern") or "*.anonymized.md").strip()
        overwrite = bool(cfg.get("overwrite", False))
        model = cfg.get("model") or None
        timeout_s = cfg.get("timeout_s")
        if isinstance(timeout_s, str) and timeout_s.isdigit():
            timeout_s = int(timeout_s)
        if not isinstance(timeout_s, (int, type(None))):
            timeout_s = None

        if not src or not os.path.isdir(src):
            return {"error": f"Invalid source folder: {src}"}

        root = Path(src)
        # Enumerate matches recursively using rglob
        try:
            candidates = [p for p in root.rglob(pattern) if p.is_file()]
        except Exception as e:
            return {"error": f"Invalid pattern '{pattern}': {e}"}

        total = len(candidates)
        ok = 0
        skipped = 0
        failed = 0
        results: list[dict[str, Any]] = []

        progress and progress(
            f"Summarize folder: src={src} pattern='{pattern}' overwrite={overwrite} total={total}"
        )

        for p in candidates:
            if _is_cancelled(should_cancel):
                break
            try:
                rel = str(p.relative_to(root))
            except Exception:
                rel = str(p)

            # Derive sidecar paths next to the input file
            out_summary = p.with_suffix(".summary.txt")
            out_keywords = p.with_suffix(".keywords.json")

            # Skip existing when overwrite is False and both sidecars exist
            if (not overwrite) and out_summary.exists() and out_keywords.exists():
                results.append({"path": str(p), "rel": rel, "status": "skipped"})
                skipped += 1
                continue

            # Read anonymized Markdown
            try:
                text = p.read_text(encoding="utf-8", errors="ignore")
            except Exception as e:
                results.append(
                    {
                        "path": str(p),
                        "rel": rel,
                        "status": "failed",
                        "error": f"read_error: {e}",
                    }
                )
                failed += 1
                continue

            progress and progress(f"Summarizing: {rel}")

            # Call LLM
            try:
                out = summarize_keywords(
                    text, model=model or "gpt-4o-mini", timeout_s=timeout_s or 60, seed=0
                )
                summary = str(out.get("summary", "")).strip()
                keywords = out.get("keywords", []) or []
            except Exception as e:
                results.append(
                    {
                        "path": str(p),
                        "rel": rel,
                        "status": "failed",
                        "error": str(e),
                    }
                )
                failed += 1
                continue

            # Write sidecars
            try:
                out_summary.write_text(
                    summary + ("\n" if summary and not summary.endswith("\n") else ""),
                    encoding="utf-8",
                )
                with open(out_keywords, "w", encoding="utf-8") as f:
                    json.dump(keywords, f, ensure_ascii=False, indent=2)
                results.append({"path": str(p), "rel": rel, "status": "ok"})
                ok += 1
            except Exception as e:
                results.append(
                    {
                        "path": str(p),
                        "rel": rel,
                        "status": "failed",
                        "error": f"write_error: {e}",
                    }
                )
                failed += 1
                continue

        cancelled = _is_cancelled(should_cancel)
        if cancelled:
            progress and progress(
                "Summarize folder: cancellation requested; returning partial results."
            )
        ended_at = _dt.now().astimezone().isoformat()
        # If cancelled mid-loop, adjust total to reflect enumerated candidates; we keep 'total' as all matched
        return {
            "total": total,
            "ok": ok,
            "skipped": skipped,
            "failed": failed,
            "files": results,
            "ended_at": ended_at,
            "cancelled": cancelled,
        }

    def test_model(self, model: str, timeout_s: int | None = 30) -> dict[str, Any]:
        """Run a tiny probe against the given model to verify it can be called.

        Returns a dict with keys: status ('ok'|'failed'), model, summary (if ok), keywords (if ok), error (if failed)
        """
        if not model or not str(model).strip():
            return {"status": "failed", "error": "model name is required"}
        try:
            # Use a very short deterministic prompt that should work for JSON response
            probe = (
                'Please return JSON: {"summary":"one sentence", "keywords":["a","b","c","d","e"]}'
            )
            # Use existing LLM helper; it will omit sampling params for GPT-5 automatically
            from shared.llm.openai_client import (
                summarize_keywords,
            )  # local import to avoid circulars

            out = summarize_keywords(probe, model=model, timeout_s=timeout_s or 30, seed=0)
            # Basic validation
            summary = str(out.get("summary", "")).strip()
            keywords = out.get("keywords", []) or []
            return {"status": "ok", "model": model, "summary": summary, "keywords": keywords}
        except Exception as e:
            return {"status": "failed", "model": model, "error": str(e)}
