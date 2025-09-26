from __future__ import annotations

"""Convert-only CLI entry point (command: mdify).

Capabilities note:
    This module implements a thin, presentation-layer command-line interface to run a
    "folder → Markdown" conversion flow. It performs only argument parsing, input
    validation, logging/progress UI, and delegates the actual work to an app-layer
    entry point. It does not import or directly depend on parser/adapter libraries.

    Conversion logic, parser selection, encoding/newline normalization, and writing
    semantics are owned by the application layer, which is expected to expose a
    function like `convert_only.convert_folder(...)` or, alternatively, a batch
    pipeline entry such as `pipeline.run_batch(..., only='convert')`.

    Determinism: Given identical inputs and the same configuration, outputs (paths
    and filenames) must be identical across runs. The CLI preserves relative folder
    structure from --src into --out and relies on the app layer for any policy
    enforcement.
"""

import argparse
import json
import logging
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import asdict as _dc_asdict
from dataclasses import dataclass
from dataclasses import is_dataclass as _dc_is_dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# Default extension whitelist (case-insensitive; dot optional in user input)
_DEFAULT_INCLUDE_EXT: tuple[str, ...] = (".docx", ".xlsx", ".pdf", ".msg")


@dataclass(frozen=True)
class EffectiveConfig:
    """Immutable, presentation-layer view of the effective CLI configuration.

    Fields mirror CLI options and are serialized into a pure-data dict passed to the
    app layer. Paths are absolute. All values are deterministic and side-effect free.
    """

    src: Path
    out: Path
    recurse: bool
    include_ext: tuple[str, ...]
    exclude_glob: tuple[str, ...]
    max_files: int | None
    overwrite: bool
    assets_subdir: str
    write_meta: str  # {"none","sidecar","inline"}
    workers: int
    on_error: str  # {"skip","fail"}
    dry_run: bool
    log_level: str  # {ERROR,WARNING,INFO,DEBUG}
    progress: str  # {auto,plain,none}
    report: Path | None
    strict: bool
    normalize_eol: str  # {lf,keep}
    locale: str | None
    # New: optional log file path
    log_file: Path | None

    def as_app_config(self) -> dict[str, Any]:
        """Return a pure-data dict appropriate for the application layer.

        The returned mapping contains only JSON-serializable primitives, except for
        absolute paths which are converted to string paths. The app layer is free
        to ignore unknown keys as needed for forward compatibility.
        """
        return {
            "src": str(self.src),
            "out": str(self.out),
            "scan": {
                "recurse": self.recurse,
                "include_ext": list(self.include_ext),
                "exclude_glob": list(self.exclude_glob),
                "max_files": self.max_files,
            },
            "write": {
                "overwrite": self.overwrite,
                "assets_subdir": self.assets_subdir,
                "write_meta": self.write_meta,
                "normalize_eol": self.normalize_eol,
            },
            "runtime": {
                "workers": self.workers,
                "on_error": self.on_error,
                "dry_run": self.dry_run,
                "strict": self.strict,
            },
            "ui": {
                "log_level": self.log_level,
                "progress": self.progress,
                "locale": self.locale,
            },
            "report": str(self.report) if self.report else None,
            # Include log_file for transparency (app layer ignores it)
            "log_file": str(self.log_file) if self.log_file else None,
        }


# ----- Utilities -----


def _normalize_ext(ext: str) -> str:
    s = ext.strip().lower()
    return s if s.startswith(".") else f".{s}" if s else s


def _is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


# ----- Argument parsing -----


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse command-line arguments for the mdify CLI.

    Description:
        Builds and parses the CLI interface for the convert-only Markdown batch
        conversion tool. Flags cover scanning/selection, output/writing policies,
        performance/reliability, logging/progress, and compatibility options. CLI
        flags override environment defaults; the application layer may still
        enforce policies such as LF newlines.

    Args:
        argv (Sequence[str] | None):
            Optional custom argument list. When None, sys.argv[1:] is used. Provide
            a sequence during unit testing or embedding. Values are not modified;
            parsing is deterministic.

    Returns:
        argparse.Namespace: Parsed and minimally normalized options. Notably:
            - src (str): Absolute or relative path to source directory; required.
            - out (str): Absolute or relative path to output directory; required.
            - recurse (bool): Whether to traverse subdirectories.
            - include_ext (list[str]): Whitelist of extensions (with or without dot).
            - exclude_glob (list[str): Glob patterns to exclude (may be empty).
            - max_files (int | None): Cap on files processed; None when unspecified.
            - overwrite (bool): True to overwrite existing markdown, else skip.
            - assets_subdir (str): Subfolder name for assets next to each .md.
            - write_meta (str): One of {none,sidecar,inline}.
            - workers (int): Number of worker processes/threads for concurrency.
            - on_error (str): One of {skip,fail}.
            - dry_run (bool): True to plan only and write nothing.
            - log_level (str): One of {ERROR,WARNING,INFO,DEBUG}.
            - progress (str): One of {auto,plain,none}.
            - report (str | None): Optional path to write a JSON run report.
            - strict (bool): True to promote certain warnings to errors.
            - normalize_eol (str): One of {lf,keep}.
            - locale (str | None): Locale hint for human-readable logs only.
            - log_file (str | None): Optional path to write logs to a file.
            - make_english (bool): When set, run translate(EN) for Markdown outputs.

    Raises:
        SystemExit: When -h/--help is requested. For invalid combinations, prefer
            to return exit code 2 from main() after validation rather than raising
            here to produce cleaner, user-friendly messages.

    Notes:
        - Extensions are case-insensitive and may be provided with or without a
          leading dot. They are normalized later to lowercase with dot.
        - Use argument groups to keep --help organized and skimmable.
    """
    parser = argparse.ArgumentParser(
        prog="mdify",
        description=(
            "Convert files in a folder to Markdown, preserving structure and assets. "
            "Deterministic, batch-friendly, and convert-only. Optionally, create an English variant."
        ),
        add_help=True,
    )

    req = parser.add_argument_group("Required")
    req.add_argument(
        "--src", required=True, help="Source directory to scan (must exist and be readable)"
    )
    req.add_argument(
        "--out",
        required=True,
        help="Output directory for Markdown and assets (created if missing; not created during --dry-run)",
    )

    scan = parser.add_argument_group("Scanning & selection")
    recurse = scan.add_mutually_exclusive_group()
    recurse.add_argument(
        "--recurse", dest="recurse", action="store_true", help="Recurse into subdirectories"
    )
    recurse.add_argument(
        "--no-recurse",
        dest="recurse",
        action="store_false",
        help="Do not recurse into subdirectories",
    )
    parser.set_defaults(recurse=True)
    scan.add_argument(
        "--include-ext",
        nargs="+",
        metavar="EXT",
        default=list(_DEFAULT_INCLUDE_EXT),
        help="Whitelist of extensions to process (e.g., .docx .pdf .xlsx .msg); case-insensitive",
    )
    scan.add_argument(
        "--exclude-glob",
        nargs="+",
        metavar="PATTERN",
        default=[],
        help="Glob patterns relative to --src to skip (e.g., **/tmp/** **/~$*)",
    )
    scan.add_argument(
        "--max-files", type=int, default=None, help="Hard cap on number of files to process"
    )

    outg = parser.add_argument_group("Output & writing")
    ow = outg.add_mutually_exclusive_group()
    ow.add_argument(
        "--overwrite", dest="overwrite", action="store_true", help="Overwrite existing .md outputs"
    )
    ow.add_argument(
        "--skip-existing",
        dest="overwrite",
        action="store_false",
        help="Skip when target .md exists",
    )
    parser.set_defaults(overwrite=False)
    outg.add_argument(
        "--assets-subdir", default="assets", help="Subfolder name for exported assets next to .md"
    )
    outg.add_argument(
        "--write-meta",
        choices=("none", "sidecar", "inline"),
        default="sidecar",
        help=(
            "Metadata writing mode: none|sidecar|inline. Inline may still emit a minimal sidecar if required by app layer."
        ),
    )

    perf = parser.add_argument_group("Performance & reliability")
    perf.add_argument(
        "--workers", type=int, default=1, help="Parallel workers for file-level concurrency"
    )
    perf.add_argument(
        "--on-error",
        choices=("skip", "fail"),
        default="skip",
        help="skip: log and continue; fail: stop immediately and return non-zero",
    )
    perf.add_argument(
        "--dry-run", action="store_true", help="Discover and plan outputs but do not write files"
    )

    # Translation flags (additive; only used when --make-english)
    tr = parser.add_argument_group("Translation (English variant)")
    tr.add_argument(
        "--make-english",
        action="store_true",
        help="Create/refresh English Markdown variants (offline)",
    )
    tr.add_argument(
        "--translator",
        choices=("ct2_nllb", "marian_opus"),
        default=None,
        help="Translation engine to use (overrides settings_translation.engine)",
    )
    tr.add_argument("--tgt-lang", default="en", help="Target language code (default: en)")
    tr.add_argument(
        "--lang-detect", default="fast", help="Language detection engine hint (symbolic)"
    )
    tr.add_argument(
        "--lang-candidates",
        default=None,
        help="Comma-separated language hints (e.g., sk,de,cs,pl,hu,en) for the detector",
    )
    tr.add_argument(
        "--lang-max-chars",
        type=int,
        default=None,
        help="Maximum characters of cleaned text to analyze for language detection (default from settings)",
    )
    tr.add_argument(
        "--lang-min-chars",
        type=int,
        default=None,
        help="Minimum characters required before trusting detector scores (default from settings)",
    )
    tr.add_argument(
        "--segment-max-chars", type=int, default=None, help="Soft limit for per-segment size"
    )
    tr.add_argument("--translate-link-label", choices=("true", "false"), default=None)
    tr.add_argument("--translate-alt-text", choices=("true", "false"), default=None)
    tr.add_argument("--translate-table-cells", choices=("true", "false"), default=None)
    tr.add_argument("--collapse-softbreaks", choices=("true", "false"), default=None)
    tr.add_argument(
        "--glossary-id", default=None, help="Glossary identifier to use (if enabled in settings)"
    )
    tr.add_argument(
        "--glossary-mode",
        choices=("pre", "post", "both", "none"),
        default=None,
        help="Override glossary mode for this run",
    )
    tr.add_argument(
        "--mt-cache", dest="mt_cache", default=None, help="Override translation cache root path"
    )
    tr.add_argument(
        "--cache-disabled", action="store_true", help="Disable translation cache during this run"
    )
    tr.add_argument(
        "--translate-on-error",
        choices=("skip", "fail_fast"),
        default="skip",
        help="Translation error policy: skip to continue; fail_fast to abort on first error",
    )
    tr.add_argument(
        "--translate-only",
        action="store_true",
        help="Skip convert phase and translate Markdown under --src",
    )

    # Anonymization of English variant
    anon = parser.add_argument_group("Anonymization (English variant)")
    anon.add_argument(
        "--anonymize-en",
        action="store_true",
        help=(
            "After creating English variants, also write anonymized copies under out/hashed_documents "
            "with the _anon.md suffix"
        ),
    )
    anon.add_argument(
        "--anon-tenant",
        default=None,
        help="Optional tenant identifier to scope hashing tokens (default: none)",
    )

    # Metadata + Vector store
    meta = parser.add_argument_group("Metadata & Vector store")
    meta.add_argument(
        "--with-metadata",
        action="store_true",
        help="Generate summary+tags metadata from anonymized English content",
    )
    meta.add_argument(
        "--vector-store",
        action="store_true",
        help="Persist records into a vector store (local JSONL placeholder by default)",
    )

    diag = parser.add_argument_group("Logging & diagnostics")
    diag.add_argument(
        "--log-level",
        choices=("ERROR", "WARNING", "INFO", "DEBUG"),
        default="INFO",
        help="Console log verbosity",
    )
    diag.add_argument(
        "--progress",
        choices=("auto", "plain", "none"),
        default="auto",
        help="auto: progress bar if TTY (degrades to plain); plain: periodic lines; none: silent",
    )
    diag.add_argument(
        "--report",
        default=None,
        help="Optional path to write a JSON run report",
    )
    # New: optional log file path
    diag.add_argument(
        "--log-file", dest="log_file", default=None, help="Optional path to write a .log file"
    )

    compat = parser.add_argument_group("Compatibility & policies")
    compat.add_argument("--strict", action="store_true", help="Promote certain warnings to errors")
    compat.add_argument(
        "--normalize-eol",
        choices=("lf", "keep"),
        default="lf",
        help="Force LF newlines or keep as produced by adapters",
    )
    compat.add_argument(
        "--locale", default=None, help="Locale hint for human-readable logs only (not rendering)"
    )

    return parser.parse_args(argv)


# ----- Validation and logging setup -----


def _validate_args(ns: argparse.Namespace) -> tuple[EffectiveConfig | None, str | None]:
    # Paths
    try:
        src = Path(ns.src).expanduser().resolve()
    except Exception:
        return None, "Invalid --src path"
    if not src.exists() or not src.is_dir():
        return None, f"--src must be an existing directory: {ns.src}"
    try:
        out = Path(ns.out).expanduser().resolve()
    except Exception:
        return None, "Invalid --out path"
    # Do not create out on dry-run; when not dry-run, create if missing.
    if not ns.dry_run:
        try:
            out.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return None, f"Cannot create or access --out directory: {out} ({e})"

    # Workers
    if ns.workers is None or ns.workers < 1:
        return None, "--workers must be >= 1"

    # Max files
    if ns.max_files is not None and ns.max_files < 1:
        return None, "--max-files must be a positive integer"

    # Include extensions
    include_ext: list[str] = [
        _normalize_ext(x) for x in (ns.include_ext or list(_DEFAULT_INCLUDE_EXT))
    ]
    if not include_ext:
        return None, "--include-ext cannot be empty"

    # Exclude glob patterns
    exclude_glob: list[str] = list(ns.exclude_glob or [])

    report_path: Path | None = None
    if ns.report:
        try:
            rp = Path(ns.report).expanduser()
            report_path = rp.resolve()
            # Ensure parent directory exists when not dry-run
            if not ns.dry_run:
                report_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            return None, f"Invalid --report path: {ns.report} ({e})"

    # New: log file path (default to ./outputs/logs/cli_run.log when not provided)
    log_file_path: Path | None = None
    try:
        lf_arg = getattr(ns, "log_file", None)
        if lf_arg:
            lf = Path(lf_arg).expanduser()
        else:
            lf = Path.cwd() / "outputs" / "logs" / "cli_run.log"
        log_file_path = lf.resolve() if lf.is_absolute() else (Path.cwd() / lf).resolve()
        if not ns.dry_run:
            log_file_path.parent.mkdir(parents=True, exist_ok=True)
    except Exception as e:
        return None, f"Invalid --log-file path: {getattr(ns, 'log_file', None)} ({e})"

    cfg = EffectiveConfig(
        src=src,
        out=out,
        recurse=bool(ns.recurse),
        include_ext=tuple(include_ext),
        exclude_glob=tuple(exclude_glob),
        max_files=ns.max_files if ns.max_files is not None else None,
        overwrite=bool(ns.overwrite),
        assets_subdir=str(ns.assets_subdir or "assets"),
        write_meta=str(ns.write_meta),
        workers=int(ns.workers),
        on_error=str(ns.on_error),
        dry_run=bool(ns.dry_run),
        log_level=str(ns.log_level),
        progress=str(ns.progress),
        report=report_path,
        strict=bool(ns.strict),
        normalize_eol=str(ns.normalize_eol),
        locale=str(ns.locale) if ns.locale else None,
        log_file=log_file_path,
    )
    return cfg, None


def _setup_logging(level: str, *, log_file: Path | None = None) -> None:
    root = logging.getLogger()
    root.handlers.clear()
    lvl = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(lvl)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    # Console
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(lvl)
    sh.setFormatter(fmt)
    root.addHandler(sh)
    # Optional file handler
    if log_file is not None:
        try:
            fh = logging.FileHandler(str(log_file), mode="a", encoding="utf-8")
            fh.setLevel(lvl)
            fh.setFormatter(fmt)
            root.addHandler(fh)
        except Exception as e:
            # Fall back to console only but warn once
            logging.warning("Failed to set up file logging (%s): %s", str(log_file), e)


# ----- App layer integration -----


def _call_app_convert(
    config: EffectiveConfig,
) -> tuple[int, list[Mapping[str, Any]], Mapping[str, Any]]:
    """Invoke the application conversion entry point and collect results.

    The function attempts, in order, to call the following app-layer APIs:
      1) preprocessing.app.convert_only.convert_folder(src, out, config_dict)
      2) preprocessing.app.pipeline.run_batch(src, out, only='convert', config=config_dict)

    The called function is expected to either:
      - yield per-file result dicts, and optionally return a summary at the end; or
      - return a dict with keys like {results: [...], summary: {...}}; or
      - return a tuple (results, summary).

    Returns:
        tuple[int, list[Mapping[str, Any]], Mapping[str, Any]]: (exit_code, results, summary)
            exit_code is 0 on success, 1 when some files failed, 3 on fatal init error.
            results is a list of per-file result records.
            summary is a mapping with aggregate counts/timings.

    Notes:
        - This wrapper does not perform conversion on its own; it only adapts
          common shapes to a normalized structure.
        - When the app entry point cannot be imported or called, a fatal code 3 is
          returned with empty results and a summary containing an error.
    """
    app_cfg = config.as_app_config()

    # Dry-run: use plan_folder to avoid real parsing
    if config.dry_run:
        try:
            try:
                from ..app import convert_only as _conv  # type: ignore
            except Exception:
                from preprocessing.app import convert_only as _conv  # type: ignore
            plan_func = getattr(_conv, "plan_folder", None)
            if not callable(plan_func):
                logging.error("plan_folder not available in app layer")
                return 3, [], {"error": "plan_folder not available"}
            try:
                mod_path = getattr(_conv, "__file__", None)
                if mod_path:
                    logging.info("Using convert_only(plan) from: %s", mod_path)
            except Exception:
                pass
            plan = plan_func(config.src, config.out, app_cfg)
            # Plan is likely a dataclass; coerce to dict if so
            if _dc_is_dataclass(plan):
                p = _dc_asdict(plan)
            else:
                p = plan  # assume mapping-like
            candidates = p.get("candidates", [])
            res: list[Mapping[str, Any]] = []
            for c in candidates:
                # status SKIP for plan preview (we are not converting)
                rec: dict[str, Any] = {
                    "status": "SKIP",
                    "src": c.get("src_path") or c.get("src") or "?",
                    "dst": c.get("out_md_path") or c.get("dst") or "?",
                    "adapter": c.get("adapter_key") or c.get("adapter") or "?",
                }
                res.append(rec)
            summ = p.get("summary", {})
            summary = {
                "scanned": summ.get("matched"),
                "matched": summ.get("matched"),
                "converted": 0,
                "skipped": len(res),
                "failed": 0,
                "duration_sec": 0.0,
            }
            return 0, res, summary
        except Exception as e:
            logging.error("Dry-run planning failed: %s", e)
            return 3, [], {"error": f"dry_run plan failed: {e}"}

    # Attempt 1: convert_only.convert_folder (prefer src-layout relative import)
    try:
        try:
            from ..app import convert_only as _conv  # type: ignore
        except Exception:
            from preprocessing.app import convert_only as _conv  # type: ignore

        func = getattr(_conv, "convert_folder", None)
        if callable(func):
            try:
                mod_path = getattr(_conv, "__file__", None)
                if mod_path:
                    logging.info("Using convert_only from: %s", mod_path)
            except Exception:
                pass
            res = func(config.src, config.out, app_cfg)  # type: ignore[arg-type]
        else:
            raise ImportError("convert_only.convert_folder not found")
    except Exception:
        # Attempt 2: pipeline.run_batch(..., only='convert') with robust imports
        try:
            try:
                from ..app import pipeline as _pipe  # type: ignore
            except Exception:
                from preprocessing.app import pipeline as _pipe  # type: ignore

            func2 = getattr(_pipe, "run_batch", None)
            if callable(func2):
                try:
                    mod_path2 = getattr(_pipe, "__file__", None)
                    if mod_path2:
                        logging.info("Using pipeline from: %s", mod_path2)
                except Exception:
                    pass
                res = func2(config.src, config.out, only="convert", config=app_cfg)  # type: ignore[arg-type]
            else:
                raise ImportError("pipeline.run_batch not found")
        except Exception as e:
            logging.error("App layer unavailable: %s", e)
            return 3, [], {"error": f"App layer unavailable: {e}"}

    # Normalize results
    results: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] = {}

    def _append_normalized(item: Mapping[str, Any]) -> None:
        results.append(item)

    try:
        logging.info("App return type: %s", type(res))
    except Exception:
        pass

    # Accept RunResult dataclass from app layer
    if _dc_is_dataclass(res):  # type: ignore[arg-type]
        try:
            run_dict = _dc_asdict(res)  # type: ignore[arg-type]
            try:
                logging.info(
                    "RunResult counts | scanned=%s matched=%s files=%s",
                    run_dict.get("scanned"),
                    run_dict.get("matched"),
                    len(run_dict.get("files", []) or []),
                )
            except Exception:
                pass
            files = run_dict.get("files", []) or []
            for fr in files:
                # Map FileResult -> CLI record shape
                status_raw = str(fr.get("status", "")).lower()
                status = (
                    "OK"
                    if status_raw == "ok"
                    else (
                        "SKIP"
                        if status_raw == "skip"
                        else ("FAIL" if status_raw == "fail" else status_raw.upper() or "?")
                    )
                )
                rec: dict[str, Any] = {
                    "status": status,
                    "src": fr.get("src_path") or fr.get("source") or "?",
                    "dst": fr.get("out_md_path") or fr.get("target") or "?",
                    "adapter": fr.get("adapter_key") or fr.get("adapter") or "?",
                }
                # Extract error message when present
                err = fr.get("error")
                if isinstance(err, Mapping):
                    msg = err.get("message") or err.get("msg")
                    if msg:
                        rec["message"] = str(msg)
                    code = err.get("code")
                    if code:
                        rec["error_code"] = str(code)
                dur_ms = fr.get("duration_ms")
                if isinstance(dur_ms, (int, float)):
                    rec["duration"] = float(dur_ms) / 1000.0
                _append_normalized(rec)
            # Build summary
            started = run_dict.get("started_at")
            ended = run_dict.get("ended_at")
            dur = None
            try:
                if isinstance(started, str) and isinstance(ended, str):
                    t0 = datetime.fromisoformat(started)
                    t1 = datetime.fromisoformat(ended)
                    dur = max(0.0, (t1 - t0).total_seconds())
            except Exception:
                dur = None
            summary = {
                "scanned": run_dict.get("scanned"),
                "matched": run_dict.get("matched"),
                "converted": run_dict.get("converted_ok"),
                "skipped": run_dict.get("skipped_existing"),
                "failed": run_dict.get("failed"),
                "duration_sec": dur,
            }
        except Exception as e:  # Fallback if unexpected shape
            logging.error("Cannot normalize RunResult: %s", e)
            return 3, [], {"error": f"Unknown app return shape (dataclass): {e}"}

    # If res is an iterable (generator) of per-file results, consume it
    elif isinstance(res, Iterable) and not isinstance(res, (dict, tuple)):
        start = time.time()
        for item in res:  # type: ignore[assignment]
            if isinstance(item, Mapping):
                _append_normalized(item)  # type: ignore[arg-type]
        summary = {"duration_sec": time.time() - start}
    elif isinstance(res, tuple) and len(res) == 2 and isinstance(res[0], Iterable):
        for item in res[0]:  # type: ignore[index]
            if isinstance(item, Mapping):
                _append_normalized(item)
        s = res[1]  # type: ignore[index]
        summary = s if isinstance(s, Mapping) else {}
    elif isinstance(res, Mapping):
        arr = res.get("results") if hasattr(res, "get") else None  # type: ignore[assignment]
        if isinstance(arr, Iterable):
            for item in arr:  # type: ignore[assignment]
                if isinstance(item, Mapping):
                    _append_normalized(item)
        s = res.get("summary") if hasattr(res, "get") else None  # type: ignore[assignment]
        summary = s if isinstance(s, Mapping) else {}
    else:
        # Unknown shape; treat as fatal
        logging.error("Unknown app return shape: %r", type(res))
        return 3, [], {"error": "Unknown app return shape"}

    # Exit code deduction: if any FAIL, return 1; otherwise 0
    any_fail = any((str(r.get("status", "")).upper() == "FAIL") for r in results)
    code = 1 if any_fail else 0
    return code, results, summary


# ----- Rendering -----


def _banner(cfg: EffectiveConfig) -> None:
    logging.info(
        "mdify starting | src=%s out=%s recurse=%s include=%s workers=%d",
        str(cfg.src),
        str(cfg.out),
        "yes" if cfg.recurse else "no",
        ",".join(cfg.include_ext),
        cfg.workers,
    )


def _render_progress(results: Iterable[Mapping[str, Any]], *, mode: str) -> list[Mapping[str, Any]]:
    collected: list[Mapping[str, Any]] = []
    # For now, print per-file lines as they arrive (plain mode). If mode is none, collect silently.
    for rec in results:
        collected.append(rec)
        if mode == "none":
            continue
        status = str(rec.get("status", "")).upper() or "?"
        src = rec.get("src") or rec.get("source") or "?"
        dst = rec.get("dst") or rec.get("target") or "?"
        adapter = rec.get("adapter") or rec.get("parser") or "?"
        dur = rec.get("duration") or rec.get("time") or rec.get("elapsed_sec") or None
        dur_s = f" {float(dur):.2f}s" if isinstance(dur, (int, float)) else ""
        msg = rec.get("message")
        extra = f" | {msg}" if msg and status == "FAIL" else ""
        logging.info("%s %s -> %s | %s%s%s", status, src, dst, adapter, dur_s, extra)
    return collected


def _summarize(collected: list[Mapping[str, Any]], extra: Mapping[str, Any]) -> Mapping[str, Any]:
    total = len(collected)
    ok = sum(1 for r in collected if str(r.get("status", "")).upper() == "OK")
    skip = sum(1 for r in collected if str(r.get("status", "")).upper() == "SKIP")
    fail = sum(1 for r in collected if str(r.get("status", "")).upper() == "FAIL")
    sum_map: dict[str, Any] = {
        "scanned": extra.get("scanned", total),
        "matched": extra.get("matched", total),
        "converted": ok,
        "skipped": skip,
        "failed": fail,
        "duration_sec": extra.get("duration_sec"),
    }
    return sum_map


# ----- Translation wiring (lazy) -----


def _bool_from_flag(value: str | None) -> bool | None:
    if value is None:
        return None
    s = str(value).strip().lower()
    if s in {"true", "1", "yes", "y", "on"}:
        return True
    if s in {"false", "0", "no", "n", "off"}:
        return False
    return None


def _load_md_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        # Fallback with universal newlines
        with path.open("r", encoding="utf-8", errors="replace") as f:
            return f.read()


def _enumerate_md_originals_from_convert(
    results: list[Mapping[str, Any]], out_root: Path
) -> list[Path]:
    md_paths: list[Path] = []
    for rec in results:
        st = str(rec.get("status", "")).upper()
        if st not in {"OK", "SKIP"}:
            continue
        dst = rec.get("dst")
        if not dst:
            continue
        p = Path(str(dst)).resolve()
        # Ensure under out_root when possible; still include if path exists
        if p.exists():
            md_paths.append(p)
    # Deterministic order
    md_paths.sort(key=lambda x: str(x))
    return md_paths


def _enumerate_md_originals_from_dir(src_dir: Path) -> list[Path]:
    md_paths: list[Path] = []
    for dp, _dns, fns in __import__("os").walk(src_dir):
        d = Path(dp)
        for name in fns:
            if name.lower().endswith(".md"):
                md_paths.append((d / name).resolve())
    md_paths.sort(key=lambda x: str(x))
    return md_paths


def _enumerate_md_under(root: Path) -> list[Path]:
    return _enumerate_md_originals_from_dir(root)


def _try_autobuild_ct2_model(settings: dict) -> bool:
    """Attempt to build a CT2 model dir from an offline HF cache snapshot if missing.

    - Searches outputs/mt_cache/models--facebook--nllb-200-distilled-600M/snapshots/*
    - Uses ctranslate2.converters.TransformersConverter to convert into ct2_nllb.model_dir
    - Copies tokenizer files if present; never accesses network. Returns True on success.
    """
    try:
        ct2_cfg = dict(settings.get("ct2_nllb", {}))
        target_dir = str(ct2_cfg.get("model_dir") or "").strip()
        if not target_dir:
            return False
        from pathlib import Path as _P
        import shutil as _sh
        import os as _os

        tgt = _P(target_dir)
        if tgt.is_dir():
            return True
        roots = [
            _P("outputs/mt_cache/models--facebook--nllb-200-distilled-600M/snapshots"),
            _P("outputs/mt_cache/hub/models--facebook--nllb-200-distilled-600M/snapshots"),
        ]
        snap: _P | None = None
        for r in roots:
            if r.exists() and r.is_dir():
                snaps = [r / s for s in _os.listdir(r) if (r / s).is_dir()]
                if snaps:
                    snaps.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                    snap = snaps[0]
                    break
        if snap is None:
            return False
        # Lazy import; skip if unavailable
        try:
            from ctranslate2.converters.transformers import TransformersConverter  # type: ignore
        except Exception:
            return False
        tgt.parent.mkdir(parents=True, exist_ok=True)
        tmp_out = tgt.parent / (tgt.name + ".tmp")
        if tmp_out.exists():
            try:
                _sh.rmtree(tmp_out)
            except Exception:
                return False
        # Quantization selection
        compute_type = str(ct2_cfg.get("compute_type", "int8") or "int8").lower()
        quant = compute_type if compute_type in {"int8", "int16", "float16"} else None
        try:
            conv = TransformersConverter(str(snap))
            conv.convert(str(tmp_out), quantization=quant)
        except Exception:
            try:
                if tmp_out.exists():
                    _sh.rmtree(tmp_out)
            except Exception:
                pass
            return False
        for name in (
            "sentencepiece.model",
            "spm.model",
            "sentencepiece.bpe.model",
            "tokenizer.model",
        ):
            src = snap / name
            if src.exists():
                try:
                    _sh.copy2(src, tmp_out / name)
                except Exception:
                    pass
        try:
            _sh.move(str(tmp_out), str(tgt))
        except Exception:
            try:
                if tmp_out.exists():
                    _sh.rmtree(tmp_out)
            except Exception:
                pass
            return False
        settings.setdefault("ct2_nllb", {})["model_dir"] = str(tgt)
        return tgt.is_dir()
    except Exception:
        return False


def _run_translation_phase(
    ns: argparse.Namespace,
    cfg: EffectiveConfig,
    convert_code: int,
    convert_results: list[Mapping[str, Any]],
) -> tuple[int, dict | None]:
    """Run translate(EN) flow if requested; returns (exit_code_override, report_section).

    exit_code_override: 0/1/3 to override main code, or -1 to keep convert_code.
    report_section: Optional JSON-serializable section to merge into main report under key "translation".
    """
    if not getattr(ns, "make_english", False):
        return -1, None

    # Lazy imports to avoid heavy startup
    try:
        from .. import settings_translation as st  # type: ignore
        from ..app.ensure_english import ensure_english_for_batch  # type: ignore
        from ..domain.models_markdown import MarkdownDoc  # type: ignore
        from ..domain.ports import WriterContext  # type: ignore
    except Exception as e:
        logging.error("Translation components unavailable: %s", e)
        return 3, {"error": f"Translation components unavailable: {e}"}

    # Start from settings and apply CLI overrides (pure data)
    settings = dict(st.TRANSLATION)
    engine_cli = getattr(ns, "translator", None)
    if engine_cli:
        settings["engine"] = str(engine_cli)
    tgt_lang = (getattr(ns, "tgt_lang", "en") or "en").lower()
    if tgt_lang != "en":
        logging.warning("Non-default tgt_lang requested: %s (project default is 'en')", tgt_lang)
    # Segmenter overrides
    seg = dict(settings.get("segmenter", {}))
    if ns.segment_max_chars is not None:
        seg["segment_max_chars"] = int(ns.segment_max_chars)
    for flag, key in [
        (ns.translate_link_label, "translate_link_label"),
        (ns.translate_alt_text, "translate_alt_text"),
        (ns.translate_table_cells, "translate_table_cells"),
        (ns.collapse_softbreaks, "collapse_softbreaks"),
    ]:
        b = _bool_from_flag(flag)
        if b is not None:
            seg[key] = bool(b)
    settings["segmenter"] = seg
    # Langid overrides
    langid_cfg = dict(settings.get("langid", {}))
    cand = getattr(ns, "lang_candidates", None)
    if cand:
        langid_cfg["candidates"] = [s.strip().lower() for s in str(cand).split(",") if s.strip()]
    # Apply optional overrides for detector window
    mx = getattr(ns, "lang_max_chars", None)
    if mx is not None:
        try:
            langid_cfg["max_chars"] = int(mx)
        except Exception:
            pass
    mn = getattr(ns, "lang_min_chars", None)
    if mn is not None:
        try:
            langid_cfg["min_chars"] = int(mn)
        except Exception:
            pass
    settings["langid"] = langid_cfg
    # Glossary
    gls = dict(settings.get("glossary", {}))
    if ns.glossary_id is not None:
        gls["glossary_id"] = ns.glossary_id
        gls["enabled"] = True if ns.glossary_id else gls.get("enabled", False)
    if ns.glossary_mode is not None:
        gls["mode"] = ns.glossary_mode
    settings["glossary"] = gls
    # Cache
    cache = dict(settings.get("cache", {}))
    if getattr(ns, "mt_cache", None):
        cache["root_path"] = ns.mt_cache
    if getattr(ns, "cache_disabled", False):
        cache["enabled"] = False
    settings["cache"] = cache
    # IO for translation
    io_cfg = dict(settings.get("io", {}))
    io_cfg["workers"] = int(getattr(ns, "workers", io_cfg.get("workers", 1)))
    io_cfg["dry_run"] = bool(getattr(ns, "dry_run", io_cfg.get("dry_run", False)))
    io_cfg["overwrite"] = bool(getattr(ns, "overwrite", io_cfg.get("overwrite", False)))
    io_cfg["on_error"] = str(getattr(ns, "translate_on_error", io_cfg.get("on_error", "skip")))
    settings["io"] = io_cfg

    # Validate settings early
    ok, issues = st.validate_translation_settings(settings)
    if not ok:
        # Try offline auto-build when CT2 dir is missing
        missing_ct2 = next((m for m in issues if "CT2/NLLB model_dir not found" in str(m)), None)
        if missing_ct2:
            try:
                built = _try_autobuild_ct2_model(settings)
            except Exception:
                built = False
            if built:
                ok, issues = st.validate_translation_settings(settings)
        # Fallback to Marian if CT2 still unavailable
        if not ok and str(settings.get("engine")) == "ct2_nllb":
            logging.warning(
                "CT2 model unavailable; falling back to Marian OPUS (offline) for this run"
            )
            settings["engine"] = "marian_opus"
            # Prefer outputs/mt_cache when available
            try:
                from pathlib import Path as _P

                mar = dict(settings.get("marian", {}))
                cache_dir = mar.get("hf_cache_dir")
                if not cache_dir or not _P(str(cache_dir)).exists():
                    oc = _P("outputs/mt_cache")
                    if oc.exists():
                        mar["hf_cache_dir"] = str(oc)
                        settings["marian"] = mar
            except Exception:
                pass
            ok, issues = st.validate_translation_settings(settings)
    if not ok:
        for msg in issues:
            logging.error("Config: %s", msg)
        logging.error("Translation settings invalid; aborting")
        return 3, {"error": "Translation settings invalid", "issues": issues}

    logging.info("Translate capabilities: %s", st.capabilities_summary(settings))

    # Build real adapters (langid, translator, optional glossary/cache) according to settings
    try:
        # LangID: fastText
        from ..adapters.langid.fasttext_langid import FastTextLangId  # type: ignore
        from ..domain.ports import LanguageDetectError as _LangDetectErr  # type: ignore

        langid_cfg = dict(settings.get("langid", {}))
        _ft = FastTextLangId(
            str(langid_cfg.get("model_path")),
            max_chars=int(langid_cfg.get("max_chars", 5000) or 5000),
            min_chars=int(langid_cfg.get("min_chars", 50) or 50),
            candidates=list(langid_cfg.get("candidates", []) or []),
        )

        # Lightweight heuristic fallback to avoid hard-fail on FT runtime errors
        class _HeuristicLangId:
            def detect(
                self,
                text: str,
                hints: dict[str, Any] | None = None,
                *,
                context: dict[str, Any] | None = None,
            ) -> tuple[str, float]:  # type: ignore[override]
                s = (text or "")[: max(0, int(settings.get("langid", {}).get("max_chars", 5000)))]
                s = s.lower()
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
            def __init__(self, primary: Any, fallback: Any) -> None:
                self._p = primary
                self._f = fallback

            def detect(
                self,
                text: str,
                hints: dict[str, Any] | None = None,
                *,
                context: dict[str, Any] | None = None,
            ) -> tuple[str, float]:  # type: ignore[override]
                try:
                    return self._p.detect(text, hints, context=context)
                except _LangDetectErr:
                    return self._f.detect(text, hints, context=context)

            def capabilities(self) -> dict:
                return {"name": "ft-with-heuristic-fallback", "deterministic": True}

        langid = _FallbackLangId(_ft, _HeuristicLangId())

        # Optional cache
        cache = None
        cache_cfg = dict(settings.get("cache", {}))
        if bool(cache_cfg.get("enabled", False)):
            from ..adapters.cache.disk_cache import DiskCache  # type: ignore

            root_path = str(cache_cfg.get("root_path"))
            # Ensure parent directory exists
            try:
                from pathlib import Path as _P

                _P(root_path).parent.mkdir(parents=True, exist_ok=True)
            except Exception:
                pass
            cache = DiskCache(
                root_path,
                mode=str(cache_cfg.get("backend", "sqlite") or "sqlite"),
                max_value_bytes=int(cache_cfg.get("max_value_bytes", 1000000) or 1000000),
                max_items=cache_cfg.get("max_items"),
                default_ttl_seconds=cache_cfg.get("default_ttl_seconds"),
                namespace=str(cache_cfg.get("namespace", "")) or None,
            )
            try:
                cache.open()
            except Exception as e:
                logging.warning("MT cache unavailable (%s); continuing without cache", e)
                cache = None

        # Optional glossary
        glossary = None
        gl_cfg = dict(settings.get("glossary", {}))
        if bool(gl_cfg.get("enabled", False)):
            from ..adapters.glossary.sqlite_glossary import SqliteGlossary  # type: ignore

            glossary = SqliteGlossary(
                db_path=str(gl_cfg.get("db_path")),
                default_glossary_id=gl_cfg.get("glossary_id"),
                regex_enabled=bool(gl_cfg.get("regex_enabled", False)),
            )
            try:
                glossary.load(gl_cfg.get("glossary_id"))
            except Exception as e:
                logging.warning("Glossary unavailable (%s); continuing without glossary", e)
                glossary = None

        # Translator engine
        engine = (engine_cli or settings.get("engine") or "marian_opus").strip()
        seg_opts = dict(settings.get("segmenter", {}))
        dec = dict(settings.get("decoding", {}))
        if engine == "marian_opus":
            from ..adapters.translate.marian_opus import MarianOpus  # type: ignore

            mar = dict(settings.get("marian", {}))
            dec_m = dict(dec.get("marian", {}))
            translator = MarianOpus(
                models=dict(mar.get("models", {})),
                device=str(mar.get("device", "cpu")),
                dtype=str(mar.get("dtype", "auto")),
                num_beams=int(dec_m.get("num_beams", 4) or 4),
                length_penalty=float(dec_m.get("length_penalty", 1.0) or 1.0),
                max_batch_size=int(dec_m.get("max_batch_size", 16) or 16),
                max_new_tokens=int(dec_m.get("max_new_tokens", 256) or 256),
                no_repeat_ngram_size=dec_m.get("no_repeat_ngram_size"),
                segmenter_options=seg_opts,
                glossary_mode=str(gl_cfg.get("mode", "none")),
                cache_enabled=bool(cache_cfg.get("enabled", False)),
                seed=dec_m.get("seed"),
                local_files_only=bool(mar.get("local_files_only", True)),
                hf_cache_dir=mar.get("hf_cache_dir"),
                glossary=glossary,
                cache=cache,
            )
        elif engine == "ct2_nllb":
            from ..adapters.translate.ct2_nllb import NllbCTranslate2  # type: ignore

            ct2 = dict(settings.get("ct2_nllb", {}))
            dec_c = dict(dec.get("ct2", {}))
            translator = NllbCTranslate2(
                model_dir=str(ct2.get("model_dir")),
                src_lang_map=dict(ct2.get("src_lang_map", {})),
                tgt_lang_code=str(ct2.get("tgt_lang_code", "eng_Latn")),
                compute_type=str(ct2.get("compute_type", "int8")),
                device=str(ct2.get("device", "cpu")),
                num_threads=int(ct2.get("num_threads", 1) or 1),
                beam_size=int(dec_c.get("beam_size", 4) or 4),
                length_penalty=float(dec_c.get("length_penalty", 1.0) or 1.0),
                max_batch_size=int(dec_c.get("max_batch_size", 8) or 8),
                max_tokens=int(dec_c.get("max_tokens", 256) or 256),
                segmenter_options=seg_opts,
                glossary_mode=str(gl_cfg.get("mode", "none")),
                cache_enabled=bool(cache_cfg.get("enabled", False)),
                seed=dec_c.get("seed"),
                glossary=glossary,
                cache=cache,
            )
            # Eager-load to validate model dir early
            try:
                translator.load()  # type: ignore[attr-defined]
            except Exception as e:
                logging.error("CT2/NLLB load failed: %s", e)
                return 3, {"error": f"CT2/NLLB load failed: {e}"}
        else:
            logging.error("Unknown translator engine: %s", engine)
            return 2, {"error": f"Unknown translator engine: {engine}"}

    except Exception as e:
        logging.error("Translation components unavailable: %s", e)
        return 3, {"error": f"Translation components unavailable: {e}"}

    # Prepare writer context
    class _SimpleWriterCtx:
        def __init__(
            self,
            *,
            out_root: Path,
            src_root: Path,
            assets_subdir: str,
            write_meta: str,
            overwrite: bool,
            dry_run: bool,
        ) -> None:
            self.out_root = str(out_root)
            self.src_root = str(src_root)
            self.assets_subdir = str(assets_subdir)
            self.assets_layout = "per_doc"
            self.write_meta = str(write_meta)
            self.overwrite = bool(overwrite)
            self.dry_run = bool(dry_run)
            self.ensure_final_newline = True

    class _SimpleWriter:
        def compute_paths(self, doc: Any, ctx: WriterContext) -> dict[str, str | None]:  # type: ignore[override]
            in_path = Path(getattr(doc, "path"))
            src_root = Path(ctx.src_root)
            out_root = Path(ctx.out_root)
            # English variants go under out_root/en/<rel>
            try:
                rel = in_path.resolve().relative_to(src_root.resolve())
            except Exception:
                # Fallback: treat as flat under out_root/en
                rel = Path(in_path.name)
            en_root = out_root / "en"
            out_md_path = (en_root / rel).with_suffix(".md")
            assets_dir = out_md_path.parent / ctx.assets_subdir
            sidecar_path = (
                out_md_path.with_suffix(out_md_path.suffix + ".meta.json")
                if ctx.write_meta == "sidecar"
                else None
            )
            return {
                "out_md_path": str(out_md_path.resolve()),
                "assets_dir": str(assets_dir.resolve()),
                "sidecar_meta_path": str(sidecar_path) if sidecar_path else None,
            }

        def write(self, doc: Any, ctx: WriterContext) -> dict[str, Any]:  # type: ignore[override]
            paths = self.compute_paths(doc, ctx)
            out_md = Path(paths["out_md_path"])  # type: ignore[index]
            out_md.parent.mkdir(parents=True, exist_ok=True)
            status = "dry_run" if ctx.dry_run else "ok"
            bytes_written = None
            if not ctx.dry_run:
                text = getattr(doc, "text_md")
                # Always LF
                text_lf = str(text).replace("\r\n", "\n").replace("\r", "\n")
                if ctx.write_meta == "inline":
                    meta = getattr(doc, "meta", {}) or {}
                    header = (
                        f"<!-- meta: {json.dumps(meta, sort_keys=True, ensure_ascii=False)} -->\n"
                    )
                    text_lf = header + text_lf
                out_md.write_text(text_lf, encoding="utf-8", newline="\n")
                bytes_written = len(text_lf.encode("utf-8"))
                if ctx.write_meta == "sidecar":
                    sidecar = Path(paths["sidecar_meta_path"])  # type: ignore[index]
                    sidecar.parent.mkdir(parents=True, exist_ok=True)
                    meta = getattr(doc, "meta", {}) or {}
                    sidecar.write_text(
                        json.dumps(meta, ensure_ascii=False, sort_keys=True),
                        encoding="utf-8",
                        newline="\n",
                    )
            return {
                "status": status,
                "out_md_path": str(out_md),
                "assets_dir": str(Path(paths["assets_dir"])) if paths.get("assets_dir") else None,
                "assets_written": 0,
                "bytes_written_md": bytes_written,
                "bytes_written_assets": 0,
                "sidecar_written": ctx.write_meta == "sidecar" and not ctx.dry_run,
                "renamed_assets": [],
                "warnings": [],
                "error": None,
            }

    writer_ctx = _SimpleWriterCtx(
        out_root=cfg.out,
        src_root=cfg.out if not ns.translate_only else cfg.src,  # type: ignore[arg-type]
        assets_subdir=cfg.assets_subdir,
        write_meta=cfg.write_meta,
        overwrite=cfg.overwrite,
        dry_run=cfg.dry_run,
    )

    # Determine original Markdown inputs
    if getattr(ns, "translate_only", False):
        md_inputs = _enumerate_md_originals_from_dir(cfg.src)
        if not md_inputs:
            logging.error("--translate-only: no .md files found under %s", str(cfg.src))
    else:
        md_inputs = _enumerate_md_originals_from_convert(convert_results, cfg.out)
        # Fallback: if none were converted and src has .md files, treat as translate-only
        if not md_inputs:
            guess = _enumerate_md_originals_from_dir(cfg.src)
            if guess:
                logging.info(
                    "No converted outputs detected; switching to translate-only mode over Markdown under --src"
                )
                md_inputs = guess

    # Build MarkdownDoc list
    docs = []
    for p in md_inputs:
        try:
            text_md = _load_md_text(p)
        except Exception as e:
            logging.warning("Unable to read Markdown: %s (%s)", str(p), e)
            continue
        # Stable doc_id: path relative to src_root (or out root) with POSIX separators
        try:
            rel = p.resolve().relative_to(Path(writer_ctx.src_root).resolve()).as_posix()
        except Exception:
            rel = p.name
        doc_id = f"md::{rel}"
        docs.append(
            MarkdownDoc(
                doc_id=doc_id, path=str(p), variant="original", lang=None, text_md=text_md, meta={}
            )
        )

    # Ports bundle expected by ensure_english
    class _Ports:
        def __init__(self) -> None:
            self.langid = langid
            self.translate = translator
            self.writer = _SimpleWriter()
            self.glossary = glossary
            self.cache = cache
            self.writer_ctx = writer_ctx

    ports = _Ports()

    # Build cfg for batch ensure
    en_cfg: dict[str, Any] = {
        "tgt_lang": tgt_lang,
        "style": "natural",
        "glossary_id": ns.glossary_id,
        "max_segment_chars": seg.get("segment_max_chars"),
        "strict": bool(settings.get("strict", True)),
        "overwrite": bool(io_cfg.get("overwrite", False)),
        "dry_run": bool(io_cfg.get("dry_run", False)),
        "copy_when_already_en": True,
        "en_confidence_threshold": 0.95,
        "workers": int(io_cfg.get("workers", 1)),
        "on_error": str(io_cfg.get("on_error", "skip")),
        "writer_ctx": writer_ctx,
    }

    logging.info(
        "Translate starting | docs=%d on_error=%s workers=%d",
        len(docs),
        en_cfg["on_error"],
        en_cfg["workers"],
    )

    batch = ensure_english_for_batch(
        docs, ports, en_cfg, context={"run_id": datetime.now(UTC).isoformat()}
    )

    # Render translation summary
    logging.info(
        "EN Summary | total=%s created=%s skipped_exists=%s skipped_already_en=%s failed=%s wall=%.2fs",
        batch.get("total"),
        batch.get("created"),
        batch.get("skipped_exists"),
        batch.get("skipped_already_en"),
        batch.get("failed"),
        float(batch.get("wall_millis") or 0.0) / 1000.0,
    )

    # Determine exit code override
    exit_override = -1
    if en_cfg["on_error"] == "fail_fast" and int(batch.get("failed", 0)) > 0:
        exit_override = 3
    elif int(batch.get("failed", 0)) > 0 and convert_code == 0:
        # Propagate non-fatal translation failures as code 1 when convert-only succeeded
        exit_override = 1

    report_section = {
        "engine_fingerprint": batch.get("engines", {}),
        "docs_total": batch.get("total", 0),
        "created": batch.get("created", 0),
        "skipped_exists": batch.get("skipped_exists", 0),
        "skipped_already_en": batch.get("skipped_already_en", 0),
        "failed": batch.get("failed", 0),
        "results": batch.get("results", []),
    }

    return exit_override, report_section


def _run_anonymize_phase(
    ns: argparse.Namespace,
    cfg: EffectiveConfig,
    *,
    en_root: Path,
) -> tuple[int, dict | None]:
    if not getattr(ns, "anonymize_en", False):
        return -1, None
    try:
        # App-layer use-case and storage adapter
        from ..app.anonymize import anonymize_translated_document as _anon_usecase  # type: ignore
        from ..adapters.storage.anonymized_storage import (  # type: ignore
            AnonymizedFileStorage as _Storage,
        )
    except Exception as e:
        logging.error("Anonymization components unavailable: %s", e)
        return 3, {"error": f"Anonymization components unavailable: {e}"}

    tenant_id = getattr(ns, "anon_tenant", None)

    # Enumerate English Markdown and prepare output storage
    en_root = en_root.resolve()
    if not en_root.exists():
        logging.warning("English root not found: %s", str(en_root))
        return 0, {"docs_total": 0, "created": 0, "failed": 0, "results": []}
    storage = _Storage(cfg.out)

    md_paths = _enumerate_md_under(en_root)
    results: list[dict[str, Any]] = []
    created = 0
    failed = 0
    for p in md_paths:
        try:
            rel = p.resolve().relative_to(en_root)
        except Exception:
            rel = p.name
        # Stable document id for context grouping
        doc_rel = rel.as_posix() if isinstance(rel, Path) else str(rel)
        document_id = f"md::{doc_rel}"
        try:
            # Run use-case (pure)
            res = _anon_usecase(document_id, p, tenant_id=tenant_id, language="en")
            # Persist anonymized copy
            if not cfg.dry_run:
                dst = storage.write(p, res.anonymized_text)
                # Also persist mappings sidecar for offline de-anonymization
                try:
                    sidecar = Path(str(dst) + ".map.json")
                    sidecar.write_text(
                        json.dumps(
                            {"context_id": res.context_id or document_id, "mappings": res.mappings},
                            ensure_ascii=False,
                            indent=2,
                        ),
                        encoding="utf-8",
                        newline="\n",
                    )
                except Exception as e:
                    logging.warning("Failed to write mapping sidecar for %s: %s", str(dst), e)
            else:
                dst = storage.compute_target_path(p)
            created += 1
            logging.info(
                "Anonymizing %s -> %s",
                Path(p).name,
                Path(dst).name,
            )
            results.append(
                {
                    "status": "ok",
                    "src": str(p),
                    "dst": str(dst),
                    "mappings": len(res.mappings),
                }
            )
        except Exception as e:
            failed += 1
            results.append({"status": "failed", "src": str(p), "error": str(e)})

    section = {
        "docs_total": len(md_paths),
        "created": created,
        "failed": failed,
        "results": results,
        "out_root": str(storage.hashed_root.resolve()),
    }
    # Exit override only when all failed and there were docs
    exit_override = 1 if failed and failed == len(md_paths) else -1
    return exit_override, section


def _run_metadata_and_vector_phase(
    ns: argparse.Namespace,
    cfg: EffectiveConfig,
    *,
    hashed_root: Path,
) -> tuple[int, dict | None]:
    if not (getattr(ns, "with_metadata", False) or getattr(ns, "vector_store", False)):
        return -1, None
    try:
        from ..adapters.storage.anonymized_storage import AnonymizedFileStorage as _Storage  # type: ignore
    except Exception as e:
        logging.error("Storage adapter unavailable: %s", e)
        return 3, {"error": f"Storage adapter unavailable: {e}"}

    # Simple offline metadata generator
    class _SimpleMetadataGen:
        STOP = {
            "the",
            "and",
            "for",
            "with",
            "that",
            "this",
            "from",
            "have",
            "are",
            "not",
            "you",
            "your",
            "has",
            "was",
            "but",
            "his",
            "her",
            "its",
            "our",
            "their",
        }

        def generate(self, document_text: str) -> dict:
            txt = (document_text or "").strip()
            # Summary: first non-empty line (max 200 chars)
            first_line = next((l.strip() for l in txt.splitlines() if l.strip()), "")
            summary = (first_line[:200]).strip()
            # Tags: top distinct words
            import re

            words = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", txt)]
            freq: dict[str, int] = {}
            for w in words:
                if w in self.STOP:
                    continue
                freq[w] = freq.get(w, 0) + 1
            tags = [w for w, _c in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0]))[:8]]
            return {"summary": summary, "tags": tags}

    storage = _Storage(cfg.out)
    hashed_root = hashed_root.resolve()
    if not hashed_root.exists():
        logging.warning("Anonymized root not found: %s", str(hashed_root))
        return 0, {"docs_total": 0, "processed": 0, "failed": 0, "results": []}

    # Enumerate anonymized markdown files
    anon_paths: list[Path] = []
    for dp, _dns, fns in __import__("os").walk(hashed_root):
        d = Path(dp)
        for name in fns:
            if name.lower().endswith("_anon.md"):
                anon_paths.append((d / name).resolve())
    anon_paths.sort(key=lambda p: str(p))

    results: list[dict[str, Any]] = []
    processed = 0
    failed = 0
    gen = _SimpleMetadataGen()

    # Prepare vector store JSONL path
    vs_dir = cfg.out / "vector_store"
    vs_dir.mkdir(parents=True, exist_ok=True)
    vs_jsonl = vs_dir / "records.jsonl"

    for a in anon_paths:
        try:
            # Derive document_id from English relative path
            en_path = storage.resolve_en_path(a)
            try:
                rel = en_path.resolve().relative_to((cfg.out / "en").resolve()).as_posix()
            except Exception:
                rel = en_path.name
            document_id = f"md::{rel}"

            text = _load_md_text(a)
            metadata: dict[str, Any] | None = None
            if getattr(ns, "with_metadata", False):
                logging.info("Generating metadata for %s", a.name)
                metadata = gen.generate(text)
                # Write metadata sidecar next to anonymized file
                try:
                    meta_sidecar = Path(str(a) + ".meta.json")
                    meta_sidecar.write_text(
                        json.dumps(metadata, ensure_ascii=False, indent=2),
                        encoding="utf-8",
                        newline="\n",
                    )
                except Exception as e:
                    logging.warning("Failed to write metadata sidecar for %s: %s", str(a), e)

            # De-anonymize using mapping sidecar
            restored_text = text
            try:
                sidecar = Path(str(a) + ".map.json")
                if sidecar.exists():
                    data = json.loads(sidecar.read_text(encoding="utf-8"))
                    maps = data.get("mappings") or []
                    # longest-first replacement
                    pairs: list[tuple[str, str]] = []
                    for m in maps:
                        tok = m.get("token")
                        val = m.get("value")
                        if tok and val:
                            pairs.append((str(tok), str(val)))
                    pairs.sort(key=lambda t: len(t[0]), reverse=True)
                    out = text
                    for tok, val in pairs:
                        out = out.replace(tok, val)
                    restored_text = out
                else:
                    logging.warning(
                        "Mapping sidecar missing for %s; skipping de-anonymization", a.name
                    )
            except Exception as e:
                logging.warning("De-anonymization failed for %s: %s", a.name, e)

            # Vector store persistence (JSONL placeholder)
            if getattr(ns, "vector_store", False):
                logging.info("Storing into vector store: %s", document_id)
                rec = {
                    "document_id": document_id,
                    "path_anonymized": str(a),
                    "path_english": str(en_path),
                    "metadata": metadata or {},
                    "anonymized_preview": (text[:200] or ""),
                    "restored_preview": (restored_text[:200] or ""),
                }
                # Append one line JSON
                with vs_jsonl.open("a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")

            processed += 1
            results.append(
                {
                    "status": "ok",
                    "doc_id": document_id,
                    "anon": str(a),
                    "en": str(en_path),
                }
            )
        except Exception as e:
            failed += 1
            results.append({"status": "failed", "anon": str(a), "error": str(e)})

    section = {
        "docs_total": len(anon_paths),
        "processed": processed,
        "failed": failed,
        "results": results,
        "vector_store_path": str(vs_jsonl),
    }
    exit_override = 1 if failed and failed == len(anon_paths) else -1
    return exit_override, section


# ----- Public entry point -----


def main(argv: Sequence[str] | None = None) -> int:
    """Run the mdify CLI, parse options, and invoke the conversion app entry point.

    Description:
        Orchestrates the entire CLI flow: parse arguments, validate inputs, set up
        logging, print a startup banner, call the application-layer conversion
        function, render progress and per-file outcomes, optionally write a run
        report JSON, and return an appropriate exit code. The CLI performs no
        heavy lifting itself; it is deterministic and suitable for batch use.
    """
    ns = parse_args(argv)

    # Logging early with requested level (may be downgraded after validation if needed)
    # We do a minimal parse to capture --log-file for initial setup after validation.

    cfg, err = _validate_args(ns)
    if err or cfg is None:
        # Set up console logging to ensure the error is visible
        _setup_logging(getattr(ns, "log_level", "INFO"))
        logging.error(err or "Invalid arguments")
        return 2

    # Now set up logging including optional file handler
    _setup_logging(cfg.log_level, log_file=cfg.log_file)

    _banner(cfg)

    # Route progress mode
    progress_mode = cfg.progress
    if progress_mode == "auto":
        progress_mode = "plain" if _is_tty() else "plain"  # degrade to plain in this implementation

    # If translate-only requested, skip convert and enumerate .md under --src
    convert_code = 0
    results: list[Mapping[str, Any]] = []
    summary: Mapping[str, Any] = {"matched": 0, "scanned": 0, "duration_sec": 0.0}
    if getattr(ns, "translate_only", False):
        logging.info("Translate-only mode: skipping convert phase")
    else:
        try:
            convert_code, results, summary = _call_app_convert(cfg)
        except Exception as e:
            logging.error("Fatal initialization error: %s", e)
            return 3

        # Per-file results rendering
        results = _render_progress(results, mode=progress_mode)

        # Summary
        final_summary = _summarize(results, summary)
        logging.info(
            "Summary | scanned=%s matched=%s converted=%s skipped=%s failed=%s duration=%.2fs",
            final_summary.get("scanned"),
            final_summary.get("matched"),
            final_summary.get("converted"),
            final_summary.get("skipped"),
            final_summary.get("failed"),
            float(final_summary.get("duration_sec") or 0.0),
        )

    # Optional translation phase
    exit_override, translation_section = _run_translation_phase(ns, cfg, convert_code, results)

    # Optional anonymization phase over out/en
    anon_exit, anonym_section = _run_anonymize_phase(ns, cfg, en_root=cfg.out / "en")
    if anon_exit in (0, 1, 2, 3):
        # Prefer more severe exit code
        if anon_exit > (exit_override if exit_override in (0, 1, 2, 3) else -1):
            exit_override = anon_exit

    # Optional metadata + vector phase over out/hashed_documents
    meta_exit, meta_section = _run_metadata_and_vector_phase(
        ns, cfg, hashed_root=cfg.out / "hashed_documents"
    )
    if meta_exit in (0, 1, 2, 3):
        if meta_exit > (exit_override if exit_override in (0, 1, 2, 3) else -1):
            exit_override = meta_exit

    # Optional JSON run report
    if cfg.report:
        try:
            payload = {
                "started": datetime.now(UTC).isoformat(),
                "config": cfg.as_app_config(),
                "results": list(results),
                "summary": dict(_summarize(results, summary)),
            }
            if translation_section is not None:
                payload["translation"] = translation_section
            if anonym_section is not None:
                payload["anonymization"] = anonym_section
            if meta_section is not None:
                payload["metadata_vector"] = meta_section
            with cfg.report.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logging.debug("Wrote report to %s", str(cfg.report))
        except Exception as e:
            # Respect --on-error policy: reports are best-effort; treat as warning unless strict
            if cfg.strict:
                logging.error("Failed to write report: %s", e)
                return 1
            logging.warning("Failed to write report: %s", e)

    # Decide final exit code
    if exit_override in (0, 1, 2, 3):
        return int(exit_override)
    # Map app-layer suggested code to spec exit codes (prefer more severe)
    if convert_code not in (0, 1):
        # Treat any unexpected code as fatal init error
        return 3
    return convert_code


if __name__ == "__main__":
    raise SystemExit(main())
