#!/usr/bin/env python3
"""
Convert a cached Hugging Face NLLB snapshot into a local CTranslate2 model directory.

- Input: searches for facebook/nllb-200-distilled-600M under outputs/mt_cache/.../snapshots/*
- Output: writes a CT2 model into resources/models/ct2/nllb-200-distilled-600M (by default)
- Offline-only: never performs network access; requires ctranslate2 to be installed.

Usage:
    python -m scripts.convert_nllb_to_ct2 \
        --snapshot-root outputs/mt_cache/models--facebook--nllb-200-distilled-600M/snapshots \
        --target-dir resources/models/ct2/nllb-200-distilled-600M \
        --compute-type int8

Notes:
    - If multiple snapshots exist, the most recently modified one is used.
    - Common tokenizer files (sentencepiece.*.model) are copied into the CT2 dir if found.
"""
from __future__ import annotations

import argparse
import os
import shutil
from pathlib import Path


def _pick_latest_snapshot(root: Path) -> Path | None:
    if not root.exists() or not root.is_dir():
        return None
    try:
        snaps = [p for p in root.iterdir() if p.is_dir()]
        if not snaps:
            return None
        snaps.sort(key=lambda p: p.stat().st_mtime, reverse=True)
        return snaps[0]
    except Exception:
        return None


def main() -> int:
    parser = argparse.ArgumentParser(description="Convert NLLB snapshot to CT2 model (offline)")
    parser.add_argument(
        "--snapshot-root",
        default=str(
            Path("outputs/mt_cache/models--facebook--nllb-200-distilled-600M/snapshots").resolve()
        ),
        help="Root directory containing HF snapshot subfolders",
    )
    parser.add_argument(
        "--target-dir",
        default=str(Path("resources/models/ct2/nllb-200-distilled-600M").resolve()),
        help="Target directory for the CT2 model",
    )
    parser.add_argument(
        "--compute-type",
        default="int8",
        choices=("int8", "int16", "float16", "float32"),
        help="CT2 quantization/compute type",
    )
    args = parser.parse_args()

    snap_root = Path(args.snapshot_root)
    target = Path(args.target_dir)

    snap = _pick_latest_snapshot(snap_root)
    if snap is None:
        print(f"No snapshots found under: {snap_root}")
        return 1

    # Lazy import ctranslate2
    try:
        from ctranslate2.converters.transformers import TransformersConverter  # type: ignore
    except Exception as e:
        print("Error: ctranslate2 is not installed or not importable:", e)
        print("Install with: pip install ctranslate2")
        return 1

    target.parent.mkdir(parents=True, exist_ok=True)
    tmp_out = target.parent / (target.name + ".tmp")
    if tmp_out.exists():
        shutil.rmtree(tmp_out, ignore_errors=True)

    quant = args.compute_type if args.compute_type in {"int8", "int16", "float16"} else None

    print(f"Converting snapshot: {snap}")
    try:
        conv = TransformersConverter(str(snap))
        conv.convert(str(tmp_out), quantization=quant)
    except Exception as e:
        print("Conversion failed:", e)
        shutil.rmtree(tmp_out, ignore_errors=True)
        return 1

    # Copy tokenizer artifacts if present
    for name in (
        "sentencepiece.model",
        "spm.model",
        "sentencepiece.bpe.model",
        "tokenizer.model",
    ):
        src = snap / name
        if src.exists():
            try:
                shutil.copy2(src, tmp_out / name)
            except Exception:
                pass

    # Atomic move into place
    try:
        if target.exists() and any(target.iterdir()):
            print(f"Target exists and is not empty: {target}")
            shutil.rmtree(tmp_out, ignore_errors=True)
            return 0
        if target.exists():
            shutil.rmtree(target, ignore_errors=True)
        shutil.move(str(tmp_out), str(target))
    except Exception as e:
        print("Failed to finalize CT2 dir:", e)
        shutil.rmtree(tmp_out, ignore_errors=True)
        return 1

    print(f"CT2 model ready at: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
