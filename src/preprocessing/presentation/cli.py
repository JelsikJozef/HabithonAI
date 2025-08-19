from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Sequence
import logging
import sys

from ..adapters import Container
from ..adapters.ingestion.file_system import FileSystemIngestion
from ..app import IngestionService, ParseService
from ..domain.models import RawDocument
from ..settings import Settings


DEFAULT_GLOBS = (
    "**/*.pdf",
    "**/*.txt",
    "**/*.docx",
    "**/*.msg",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
)


def _setup_logging(out_path: Path) -> None:
    # Determine log file path
    if out_path.exists() and out_path.is_dir():
        log_file = out_path / "run.log"
        out_path.mkdir(parents=True, exist_ok=True)
    else:
        # If file or non-existent, place run.log next to it
        out_path.parent.mkdir(parents=True, exist_ok=True)
        log_file = out_path.parent / "run.log"
    # Root logger: stream to stdout + file handler
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    # Clear existing handlers to avoid duplication
    root.handlers.clear()
    fmt = logging.Formatter("%(levelname)s %(name)s: %(message)s")
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(fmt)
    fh = logging.FileHandler(log_file, encoding="utf-8")
    fh.setLevel(logging.INFO)
    fh.setFormatter(fmt)
    root.addHandler(sh)
    root.addHandler(fh)
    logging.getLogger(__name__).info("Logging to %s", log_file)


def _cmd_preprocess(args: argparse.Namespace) -> int:
    root = Path(args.input_dir).resolve()
    out_path = Path(args.out).resolve()
    _setup_logging(out_path)
    globs = tuple(args.globs) if args.globs else DEFAULT_GLOBS

    # Load settings once (loads .env if present)
    try:
        settings = Settings.load()
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2

    # LLM is mandatory; Container will be configured via settings
    try:
        pipe = Container.default_pipeline(out_path, settings=settings, enable_ocr=bool(args.ocr))
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2

    ingestor = FileSystemIngestion()
    ingest = IngestionService(ingestor)
    docs = ingest.ingest_batch(root, globs)

    stats_list = []
    try:
        for idx, doc in enumerate(docs, 1):
            print("Processing file %d: %s" % (idx, getattr(doc, 'path', getattr(doc, 'file_path', 'UNKNOWN'))))
            stat = pipe.process_one(doc, file_index=idx)
            stats_list.append(stat)
    except Exception as e:
        try:
            pipe._serialize.close()
        except Exception:
            pass
        print(json.dumps({"ok": False, "error": "Processing aborted: %s" % str(e)}, ensure_ascii=False))
        return 3

    print(json.dumps({"ok": True, "stats": stats_list, "out": str(out_path)}, ensure_ascii=False))
    pipe._serialize.close()
    return 0


def _cmd_parse_file(args: argparse.Namespace) -> int:
    p = Path(args.path).resolve()
    out_dir = p.parent
    _setup_logging(out_dir)
    if not p.exists() or not p.is_file():
        print(json.dumps({"ok": False, "error": "Not a file: %s" % p}, ensure_ascii=False))
        return 1
    registry = Container.default_registry()
    parse = ParseService(registry)
    st = p.stat()
    ext = p.suffix.lstrip(".").lower()
    raw = RawDocument(path=p, size=st.st_size, mtime=datetime.fromtimestamp(st.st_mtime), ext=ext)
    try:
        doc = parse.parse_one(raw)
    except Exception as e:
        print(json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False))
        return 2
    info = {
        "ok": True,
        "path": str(p),
        "ext": ext,
        "size": st.st_size,
        "text_len": len(doc.text or ""),
        "language": doc.language,
        "metadata_keys": sorted(list((doc.metadata or {}).keys())),
    }
    print(json.dumps(info, ensure_ascii=False))
    return 0


def _cmd_version(args: argparse.Namespace) -> int:
    # Print version and language-detector diagnostics without requiring Settings/OPENAI
    try:
        from .. import __version__ as ver
    except Exception:
        ver = "unknown"
    info: dict[str, object] = {"ok": True, "version": ver}
    # Module origins
    try:
        import preprocessing as pkg  # type: ignore
        import preprocessing.presentation.cli as cli_mod  # type: ignore
        info["package_file"] = getattr(pkg, "__file__", None)
        info["cli_file"] = getattr(cli_mod, "__file__", None)
    except Exception:
        pass
    # numpy version
    try:
        import numpy as _np  # type: ignore
        info["numpy_version"] = getattr(_np, "__version__", "unknown")
    except Exception as e:
        info["numpy_version_error"] = str(e)
    # fastText availability and model path
    try:
        import fasttext  # type: ignore
        info["fasttext_import"] = True
        info["fasttext_version"] = getattr(fasttext, "__version__", "unknown")
    except Exception as e:
        info["fasttext_import"] = False
        info["fasttext_error"] = str(e)
    # Check default model cache path and env override
    try:
        import os
        xdg = os.environ.get("XDG_CACHE_HOME")
        from pathlib import Path as _P
        base = _P(xdg) if xdg else (_P.home() / ".cache")
        cache = base / "habithon" / "fasttext" / "lid.176.ftz"
        info["model_cache"] = str(cache)
        info["model_exists"] = cache.exists()
        info["model_size"] = int(cache.stat().st_size) if cache.exists() else 0
        mp = os.environ.get("PREPROCESSING_FASTTEXT_MODEL") or os.environ.get("LANGUAGE_MODEL_PATH")
        if mp:
            info["model_env_override"] = mp
        # Probe predict() to surface numpy/fasttext runtime issues
        try:
            if info.get("fasttext_import") and cache.exists():
                import fasttext as _ft  # type: ignore
                md = _ft.load_model(str(cache))
                labels, probs = md.predict("hello", k=1)
                # Normalize output
                lbl = str(labels[0]) if labels else ""
                if lbl.startswith("__label__"):
                    lbl = lbl.replace("__label__", "", 1)
                pr = float(probs[0]) if probs else 0.0
                info["fasttext_predict_ok"] = True
                info["fasttext_predict"] = [lbl, pr]
            else:
                info["fasttext_predict_ok"] = False
                info["fasttext_predict_error"] = "model_missing_or_fasttext_not_imported"
        except Exception as e:
            info["fasttext_predict_ok"] = False
            info["fasttext_predict_error"] = str(e)
    except Exception:
        pass
    print(json.dumps(info, ensure_ascii=False))
    return 0


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="preprocessing", description="Preprocessing CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("preprocess", help="Run batch preprocessing on an input directory")
    p1.add_argument("input_dir", help="Input directory to scan")
    p1.add_argument("--out", required=True, help="Output JSONL file path or directory (dir => per-file .jsonl)")
    p1.add_argument("--ocr", action="store_true", help="Enable OCR for PDFs/images")
    p1.add_argument("--globs", action="append", help="Glob pattern(s) to include; can be repeated")
    p1.set_defaults(func=_cmd_preprocess)

    p2 = sub.add_parser("parse-file", help="Parse a single file and print basic metadata")
    p2.add_argument("path", help="Path to a file to parse")
    p2.set_defaults(func=_cmd_parse_file)

    # New: version subcommand
    p3 = sub.add_parser("version", help="Print version and diagnostics")
    p3.set_defaults(func=_cmd_version)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_cli()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
