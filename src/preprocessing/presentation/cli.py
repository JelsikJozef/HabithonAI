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
from dataclasses import dataclass
from dataclasses import asdict as _dc_asdict, is_dataclass as _dc_is_dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import sys
import time

# Default extension whitelist (case-insensitive; dot optional in user input)
_DEFAULT_INCLUDE_EXT: Tuple[str, ...] = (".docx", ".xlsx", ".pdf", ".msg")


@dataclass(frozen=True)
class EffectiveConfig:
    """Immutable, presentation-layer view of the effective CLI configuration.

    Fields mirror CLI options and are serialized into a pure-data dict passed to the
    app layer. Paths are absolute. All values are deterministic and side-effect free.
    """

    src: Path
    out: Path
    recurse: bool
    include_ext: Tuple[str, ...]
    exclude_glob: Tuple[str, ...]
    max_files: Optional[int]
    overwrite: bool
    assets_subdir: str
    write_meta: str  # {"none","sidecar","inline"}
    workers: int
    on_error: str  # {"skip","fail"}
    dry_run: bool
    log_level: str  # {ERROR,WARNING,INFO,DEBUG}
    progress: str  # {auto,plain,none}
    report: Optional[Path]
    strict: bool
    normalize_eol: str  # {lf,keep}
    locale: Optional[str]
    # New: optional log file path
    log_file: Optional[Path]

    def as_app_config(self) -> Dict[str, Any]:
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

def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
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
            "Deterministic, batch-friendly, and convert-only."
        ),
        add_help=True,
    )

    req = parser.add_argument_group("Required")
    req.add_argument("--src", required=True, help="Source directory to scan (must exist and be readable)")
    req.add_argument(
        "--out",
        required=True,
        help="Output directory for Markdown and assets (created if missing; not created during --dry-run)",
    )

    scan = parser.add_argument_group("Scanning & selection")
    recurse = scan.add_mutually_exclusive_group()
    recurse.add_argument("--recurse", dest="recurse", action="store_true", help="Recurse into subdirectories")
    recurse.add_argument("--no-recurse", dest="recurse", action="store_false", help="Do not recurse into subdirectories")
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
    scan.add_argument("--max-files", type=int, default=None, help="Hard cap on number of files to process")

    outg = parser.add_argument_group("Output & writing")
    ow = outg.add_mutually_exclusive_group()
    ow.add_argument("--overwrite", dest="overwrite", action="store_true", help="Overwrite existing .md outputs")
    ow.add_argument("--skip-existing", dest="overwrite", action="store_false", help="Skip when target .md exists")
    parser.set_defaults(overwrite=False)
    outg.add_argument("--assets-subdir", default="assets", help="Subfolder name for exported assets next to .md")
    outg.add_argument(
        "--write-meta",
        choices=("none", "sidecar", "inline"),
        default="sidecar",
        help=(
            "Metadata writing mode: none|sidecar|inline. Inline may still emit a minimal sidecar if required by app layer."
        ),
    )

    perf = parser.add_argument_group("Performance & reliability")
    perf.add_argument("--workers", type=int, default=1, help="Parallel workers for file-level concurrency")
    perf.add_argument(
        "--on-error",
        choices=("skip", "fail"),
        default="skip",
        help="skip: log and continue; fail: stop immediately and return non-zero",
    )
    perf.add_argument("--dry-run", action="store_true", help="Discover and plan outputs but do not write files")

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
    diag.add_argument("--report", default=None, help="Optional path to write a JSON run report with per-file outcomes")
    # New: optional log file path
    diag.add_argument("--log-file", dest="log_file", default=None, help="Optional path to write a .log file")

    compat = parser.add_argument_group("Compatibility & policies")
    compat.add_argument("--strict", action="store_true", help="Promote certain warnings to errors")
    compat.add_argument(
        "--normalize-eol",
        choices=("lf", "keep"),
        default="lf",
        help="Force LF newlines or keep as produced by adapters",
    )
    compat.add_argument("--locale", default=None, help="Locale hint for human-readable logs only (not rendering)")

    return parser.parse_args(argv)


# ----- Validation and logging setup -----

def _validate_args(ns: argparse.Namespace) -> Tuple[Optional[EffectiveConfig], Optional[str]]:
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
    include_ext: List[str] = [
        _normalize_ext(x) for x in (ns.include_ext or list(_DEFAULT_INCLUDE_EXT))
    ]
    if not include_ext:
        return None, "--include-ext cannot be empty"

    # Exclude glob patterns
    exclude_glob: List[str] = list(ns.exclude_glob or [])

    report_path: Optional[Path] = None
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
    log_file_path: Optional[Path] = None
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


def _setup_logging(level: str, *, log_file: Optional[Path] = None) -> None:
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

def _call_app_convert(config: EffectiveConfig) -> Tuple[int, List[Mapping[str, Any]], Mapping[str, Any]]:
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
            res: List[Mapping[str, Any]] = []
            for c in candidates:
                # status SKIP for plan preview (we are not converting)
                rec: Dict[str, Any] = {
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
    results: List[Mapping[str, Any]] = []
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
                status = "OK" if status_raw == "ok" else ("SKIP" if status_raw == "skip" else ("FAIL" if status_raw == "fail" else status_raw.upper() or "?"))
                rec: Dict[str, Any] = {
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


def _render_progress(results: Iterable[Mapping[str, Any]], *, mode: str) -> List[Mapping[str, Any]]:
    collected: List[Mapping[str, Any]] = []
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


def _summarize(collected: List[Mapping[str, Any]], extra: Mapping[str, Any]) -> Mapping[str, Any]:
    total = len(collected)
    ok = sum(1 for r in collected if str(r.get("status", "")).upper() == "OK")
    skip = sum(1 for r in collected if str(r.get("status", "")).upper() == "SKIP")
    fail = sum(1 for r in collected if str(r.get("status", "")).upper() == "FAIL")
    sum_map: Dict[str, Any] = {
        "scanned": extra.get("scanned", total),
        "matched": extra.get("matched", total),
        "converted": ok,
        "skipped": skip,
        "failed": fail,
        "duration_sec": extra.get("duration_sec"),
    }
    return sum_map


# ----- Public entry point -----

def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the mdify CLI, parse options, and invoke the conversion app entry point.

    Description:
        Orchestrates the entire CLI flow: parse arguments, validate inputs, set up
        logging, print a startup banner, call the application-layer conversion
        function, render progress and per-file outcomes, optionally write a run
        report JSON, and return an appropriate exit code. The CLI performs no
        heavy lifting itself; it is deterministic and suitable for batch use.

    Args:
        argv (Sequence[str] | None):
            Optional override for command-line arguments. When None, sys.argv[1:]
            is used. Supply a custom sequence for testing.

    Returns:
        int: Process exit code, with the following semantics:
            0 → all selected files converted successfully (warnings allowed)
            1 → completed with some errors (at least one file failed)
            2 → argument/usage error (invalid paths, conflicting flags)
            3 → fatal initialization error (e.g., app layer cannot be created)

    Raises:
        None directly. Exceptions are caught and translated into the appropriate
        exit code and concise user-facing messages.

    Notes:
        - CLI flags override environment or default configuration.
        - In --dry-run mode, no filesystem writes are performed and the output
        directory is not created.
        - In --write-meta=inline mode, the application may still emit a minimal
        sidecar for machine-readable stats if necessary; this is documented by
        the app layer and preserved here for clarity.

    Examples:
        mdify --src ./docs --out ./out/md --include-ext .pdf .docx --skip-existing --workers 4
        mdify --src /data/in --out /data/out --no-recurse --max-files 100 --dry-run --progress plain
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

    try:
        code, results, summary = _call_app_convert(cfg)
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

    # Optional JSON run report
    if cfg.report:
        try:
            payload = {
                "started": datetime.now(timezone.utc).isoformat(),
                "config": cfg.as_app_config(),
                "results": list(results),
                "summary": dict(final_summary),
            }
            with cfg.report.open("w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
            logging.debug("Wrote report to %s", str(cfg.report))
        except Exception as e:
            # Respect --on-error policy: reports are best-effort; treat as warning unless strict
            if cfg.strict:
                logging.error("Failed to write report: %s", e)
                return 1
            logging.warning("Failed to write report: %s", e)

    # Map app-layer suggested code to spec exit codes (prefer more severe)
    if code not in (0, 1):
        # Treat any unexpected code as fatal init error
        return 3
    return code


if __name__ == "__main__":
    raise SystemExit(main())
