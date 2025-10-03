from __future__ import annotations

from pathlib import Path
import json

import pytest

from src.preprocessing.adapters.serializer.md_writer import (
    MarkdownWriter,
    WriterContext,
    descriptor,
)


class Doc:
    def __init__(self, path: str, text: str, meta: dict | None = None):
        self.path = path
        self.text_md = text
        self.meta = meta or {}


def make_ctx(out: Path, src: Path, **over):
    return WriterContext(
        out_root=str(out.resolve()),
        src_root=str(src.resolve()),
        assets_subdir="assets",
        assets_layout=over.get("assets_layout", "per_doc"),
        write_meta=over.get("write_meta", "sidecar"),
        overwrite=over.get("overwrite", True),
        dry_run=over.get("dry_run", False),
        ensure_final_newline=over.get("ensure_final_newline", True),
    )


def test_compute_paths_and_sanitization(tmp_path: Path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    out.mkdir()

    # Path with unsafe name and outside src_root fallback
    p = src / "CON.md"
    p.write_text("x", encoding="utf-8")
    doc = Doc(str(p), "Hello")

    w = MarkdownWriter()
    ctx = make_ctx(out, src)
    targets = w.compute_paths(doc, ctx)

    out_md = Path(targets.out_md_path)
    assert str(out_md).startswith(str(out))
    # Windows reserved names are prefixed with underscore
    assert out_md.name == "_CON.md"

    # Assets path is within out_root
    assets = Path(targets.assets_dir)
    assert str(assets).startswith(str(out))


def test_write_dry_run_and_descriptor(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    p = src / "a.md"
    p.write_text("hi", encoding="utf-8")
    doc = Doc(str(p), "Content without trailing newline")

    w = MarkdownWriter()
    ctx = make_ctx(out, src, dry_run=True)

    res = w.write(doc, ctx)
    assert res.status == "dry_run"
    # Out path is under out root and preserves relative structure
    out_md_parent = Path(res.out_md_path).parent
    assert str(out_md_parent).startswith(str(out))

    d = descriptor(ctx)
    assert d.startswith("md_writer(") and "meta=sidecar" in d


def test_write_sidecar_and_newline(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    p = src / "b.md"
    p.write_text("hi", encoding="utf-8")
    doc = Doc(str(p), "Line 1")

    w = MarkdownWriter()
    ctx = make_ctx(out, src, ensure_final_newline=True)

    res = w.write(doc, ctx)
    assert res.status == "ok"
    md_path = Path(res.out_md_path)
    assert md_path.exists()
    # Final newline enforced
    assert md_path.read_text(encoding="utf-8").endswith("\n")

    # Sidecar exists and is valid JSON
    sidecar = md_path.with_suffix(".meta.json")
    assert sidecar.exists()
    payload = json.loads(sidecar.read_text(encoding="utf-8"))
    assert isinstance(payload.get("source"), dict)


def test_assets_copy_and_write(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    p = src / "c.md"
    p.write_text("hi", encoding="utf-8")

    img = tmp_path / "logo.png"
    img.write_bytes(b"PNGDATA")

    doc = Doc(
        str(p),
        "Body",
        meta={
            "assets_to_copy": [str(img)],
            "assets_to_write": [
                {"name": "data.bin", "bytes": b"\x00\x01"},
            ],
        },
    )

    w = MarkdownWriter()
    ctx = make_ctx(out, src, overwrite=True)
    res = w.write(doc, ctx)
    assert res.status == "ok"

    assets_dir = Path(res.assets_dir)
    assert assets_dir.exists()
    assert any(p.name.startswith("logo") for p in assets_dir.iterdir())
    assert (assets_dir / "data.bin").exists()


def test_overwrite_policy_and_trim_extra_newlines(tmp_path: Path):
    src = tmp_path / "src"
    src.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    p = src / "d.md"
    p.write_text("hi", encoding="utf-8")

    # First write with many trailing newlines and overwrite allowed
    doc = Doc(str(p), "X\n\n\n")
    w = MarkdownWriter()
    ctx = make_ctx(out, src, overwrite=True, ensure_final_newline=False)
    res1 = w.write(doc, ctx)
    assert res1.status == "ok"
    md = Path(res1.out_md_path).read_text(encoding="utf-8")
    # Should collapse to a single terminal newline
    assert md.endswith("\n") and not md.endswith("\n\n")

    # Second write with overwrite disabled -> skip_existing
    ctx2 = make_ctx(out, src, overwrite=False)
    res2 = w.write(doc, ctx2)
    assert res2.status == "skip_existing"


def test_compute_paths_english_suffix(tmp_path: Path):
    src = tmp_path / "src"
    out = tmp_path / "out"
    src.mkdir()
    out.mkdir()
    p = src / "report.md"
    p.write_text("hello", encoding="utf-8")

    class EDoc:
        def __init__(self, path: str, text: str):
            self.path = path
            self.text_md = text
            self.variant = "english"
            self.lang = "en"
            self.meta = {}

    doc = EDoc(str(p), "Hello world")
    w = MarkdownWriter()
    ctx = make_ctx(out, src)
    targets = w.compute_paths(doc, ctx)

    assert targets.out_md_path.endswith("report_en.md")
    assert Path(targets.assets_dir).name.endswith("report_en")
