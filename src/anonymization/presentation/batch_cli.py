import argparse
import json
import os
from datetime import datetime as _dt
from pathlib import Path

from ..adapters.container import build_default
from ..adapters.crypto.crypto import Crypto
from ..app.pseudonymize import pseudonymize
from ..domain.anonymizer import anonymize as domain_anonymize
from shared.hashing import document_fingerprint


def _scan_entries(src: str, recurse: bool) -> list[tuple[str, str]]:
    entries: list[tuple[str, str]] = []
    base = os.path.abspath(src)
    for root, dirs, files in os.walk(base):
        for fn in files:
            low = fn.lower()
            # Skip already produced outputs
            if (
                low.endswith(".anonymized.md")
                or low.endswith(".pseudonymized.md")
                or low.endswith(".restored.md")
            ):
                continue
            if not low.endswith(".md"):
                continue
            p = os.path.join(root, fn)
            rel = os.path.relpath(p, base)
            entries.append((p, rel))
        if not recurse:
            break
    return entries


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Batch anonymization CLI")
    ap.add_argument("--src", required=True, help="Source folder with Markdown files")
    ap.add_argument("--out", required=True, help="Output folder")
    ap.add_argument("--mode", choices=["deterministic", "pseudonymize"], default="deterministic")
    ap.add_argument("--language", default=None, help="Optional language hint, e.g. en, de, sk")
    ap.add_argument(
        "--tenant", dest="tenant_id", default=None, help="Tenant ID (deterministic mode only)"
    )
    ap.add_argument(
        "--no-recurse", dest="recurse", action="store_false", help="Do not traverse subfolders"
    )
    ap.add_argument(
        "--recurse", dest="recurse", action="store_true", help="Traverse subfolders (default)"
    )
    ap.add_argument("--overwrite", action="store_true", help="Overwrite existing outputs")
    ap.add_argument("--report", default=None, help="Optional path to write final JSON result")
    ap.set_defaults(recurse=True)
    args = ap.parse_args(argv)

    src = os.path.abspath(args.src)
    out = os.path.abspath(args.out)

    if not os.path.isdir(src):
        print(json.dumps({"error": f"Invalid source folder: {src}"}))
        return 2
    os.makedirs(out, exist_ok=True)

    entries = _scan_entries(src, recurse=bool(args.recurse))
    total = len(entries)

    detectors, vault = build_default()
    crypto = Crypto()

    processed_ok = 0
    skipped_existing = 0
    failed = 0
    files_status: list[dict] = []
    mapping_counts: dict[str, int] = {}

    for idx, (src_path, rel) in enumerate(entries, 1):
        # Determine output path & existence
        base_no_ext = os.path.splitext(rel)[0]
        suffix = ".anonymized.md" if args.mode == "deterministic" else ".pseudonymized.md"
        out_path = os.path.join(out, base_no_ext + suffix)
        exists = os.path.exists(out_path)
        if exists and not args.overwrite:
            skipped_existing += 1
            files_status.append({"rel": rel, "status": "skipped_exists"})
            print(f"PROGRESS [{idx}/{total}] SKIP exists: {rel}")
            continue
        os.makedirs(os.path.dirname(out_path), exist_ok=True)
        try:
            with open(src_path, "r", encoding="utf-8", errors="ignore") as f:
                text = f.read()
            st = os.stat(src_path)
            fp = document_fingerprint(src_path, size_bytes=st.st_size, mtime=st.st_mtime)
            ctx_id = f"ctx_{fp[:16]}"
            if args.mode == "deterministic":
                res = domain_anonymize(
                    text,
                    detectors,
                    crypto,
                    vault,
                    context_id=ctx_id,
                    tenant_id=args.tenant_id,
                    language=args.language,
                )
                out_text = res.pseudonymized_text
                for m in res.mappings:
                    t = m.type or "?"
                    mapping_counts[t] = mapping_counts.get(t, 0) + 1
            else:
                res = pseudonymize(
                    text, detectors, vault, context_id=ctx_id, language=args.language
                )
                out_text = res.pseudonymized_text
                for m in res.mappings:
                    t = m.type or "?"
                    mapping_counts[t] = mapping_counts.get(t, 0) + 1
            os.makedirs(os.path.dirname(out_path), exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as wf:
                wf.write(out_text)
            processed_ok += 1
            files_status.append({"rel": rel, "status": "ok", "context_id": ctx_id})
            print(f"PROGRESS [{idx}/{total}] OK: {rel}")
        except Exception as e:
            failed += 1
            files_status.append({"rel": rel, "status": "error", "error": str(e)})
            print(f"PROGRESS [{idx}/{total}] ERROR: {rel}: {e}")

    payload = {
        "mode": args.mode,
        "src": src,
        "out": out,
        "processed_ok": processed_ok,
        "skipped_existing": skipped_existing,
        "failed": failed,
        "total": processed_ok + skipped_existing + failed,
        "mapping_counts": mapping_counts,
        "files": files_status[:200],
        "ended_at": _dt.now().isoformat(),
        "cancelled": False,
    }

    if args.report:
        try:
            os.makedirs(os.path.dirname(args.report), exist_ok=True)
        except Exception:
            pass
        try:
            with open(args.report, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    print(json.dumps(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
