from __future__ import annotations

import argparse
import json
from pathlib import Path
from datetime import datetime
from typing import Sequence

from ..adapters import Container
from ..adapters.ingestion.file_system import FileSystemIngestion
from ..app import IngestionService, ParseService
from ..domain.models import RawDocument


DEFAULT_GLOBS = (
    "**/*.pdf",
    "**/*.txt",
    "**/*.docx",
    "**/*.msg",
    "**/*.png",
    "**/*.jpg",
    "**/*.jpeg",
)


def _cmd_preprocess(args: argparse.Namespace) -> int:
    root = Path(args.input_dir).resolve()
    out_path = Path(args.out).resolve()
    globs = tuple(args.globs) if args.globs else DEFAULT_GLOBS

    pipe = Container.default_pipeline(out_path, enable_ocr=bool(args.ocr), enable_llm=bool(args.llm))

    ingestor = FileSystemIngestion()
    ingest = IngestionService(ingestor)
    docs = ingest.ingest_batch(root, globs)

    stats = pipe.process_many(docs)
    print(json.dumps({"ok": True, "stats": stats, "out": str(out_path)}, ensure_ascii=False))
    pipe._serialize.close()  # ensure close
    return 0


def _cmd_parse_file(args: argparse.Namespace) -> int:
    p = Path(args.path).resolve()
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


def build_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="preprocessing", description="Preprocessing CLI")
    sub = parser.add_subparsers(dest="command", required=True)

    p1 = sub.add_parser("preprocess", help="Run batch preprocessing on an input directory")
    p1.add_argument("input_dir", help="Input directory to scan")
    p1.add_argument("--out", required=True, help="Output JSONL file path")
    p1.add_argument("--ocr", action="store_true", help="Enable OCR for PDFs/images")
    p1.add_argument("--llm", action="store_true", help="Enable LLM enrichment (dummy client by default)")
    p1.add_argument("--globs", action="append", help="Glob pattern(s) to include; can be repeated")
    p1.set_defaults(func=_cmd_preprocess)

    p2 = sub.add_parser("parse-file", help="Parse a single file and print basic metadata")
    p2.add_argument("path", help="Path to a file to parse")
    p2.set_defaults(func=_cmd_parse_file)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_cli()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
