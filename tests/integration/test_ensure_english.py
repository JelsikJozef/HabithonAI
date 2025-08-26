import types
import json
import os
from pathlib import Path

import pytest

from src.preprocessing.app.ensure_english import ensure_english_variant, ensure_english_for_batch
from src.preprocessing.domain.models_markdown import MarkdownDoc


class FakeLangId:
    def __init__(self, code="sk", conf=0.99):
        self.code = code
        self.conf = conf
    def detect(self, text, hints=None, *, context=None):
        return self.code, self.conf

class FakeTranslate:
    def __init__(self):
        self.calls = 0
    def translate_md(self, doc, src_lang, tgt_lang, options=None, *, context=None):
        self.calls += 1
        return doc.copy_with(variant="english", lang="en")
    def capabilities(self):
        return {"name": "fake"}

class FakeWriter:
    def __init__(self, root: Path):
        self.root = Path(root)
    def compute_paths(self, doc, ctx):
        src = Path(doc.path)
        try:
            rel = src.resolve().relative_to(Path(ctx.src_root).resolve())
        except Exception:
            rel = Path(src.name)
        out = Path(ctx.out_root) / "en" / rel
        return {"out_md_path": str(out.with_suffix(".md")), "assets_dir": str(out.parent/ctx.assets_subdir), "sidecar_meta_path": None}
    def write(self, doc, ctx):
        paths = self.compute_paths(doc, ctx)
        out_md = Path(paths["out_md_path"])  # type: ignore[index]
        out_md.parent.mkdir(parents=True, exist_ok=True)
        text = doc.text_md.replace("\r\n", "\n").replace("\r", "\n")
        out_md.write_text(text, encoding="utf-8", newline="\n")
        return {"status": "ok", "out_md_path": str(out_md), "assets_dir": str(Path(paths["assets_dir"])) if paths.get("assets_dir") else None, "assets_written": 0, "bytes_written_md": len(text.encode("utf-8")), "bytes_written_assets": 0, "sidecar_written": False, "renamed_assets": [], "warnings": [], "error": None}

class Ctx:
    def __init__(self, out, src):
        self.out_root = str(out)
        self.src_root = str(src)
        self.assets_subdir = "assets"
        self.assets_layout = "per_doc"
        self.write_meta = "sidecar"
        self.overwrite = True
        self.dry_run = False
        self.ensure_final_newline = True

class Ports:
    def __init__(self, langid, translate, writer, ctx):
        self.langid = langid
        self.translate = translate
        self.writer = writer
        self.glossary = None
        self.cache = None
        self.writer_ctx = ctx


@pytest.mark.integration
def test_created_flow(tmp_path):
    src_dir = tmp_path / "src"; src_dir.mkdir()
    md_path = src_dir / "a.md"; md_path.write_text("Ahoj svet\n", encoding="utf-8")
    doc = MarkdownDoc(doc_id="d1", path=str(md_path), variant="original", lang=None, text_md=md_path.read_text(encoding="utf-8"))
    ports = Ports(FakeLangId("sk", 0.99), FakeTranslate(), FakeWriter(tmp_path/"out"), Ctx(tmp_path/"out", src_dir))
    res = ensure_english_variant(doc, ports, {"overwrite": True, "dry_run": False, "tgt_lang": "en"})
    assert res["status"] == "created"
    assert Path(res["written_path"]).exists()


@pytest.mark.integration
def test_skipped_already_en_dry_run(tmp_path):
    src_dir = tmp_path / "src"; src_dir.mkdir()
    md_path = src_dir / "b.md"; md_path.write_text("Hello world\n", encoding="utf-8")
    doc = MarkdownDoc(doc_id="d2", path=str(md_path), variant="original", lang="en", text_md=md_path.read_text(encoding="utf-8"))
    ports = Ports(FakeLangId("en", 0.99), FakeTranslate(), FakeWriter(tmp_path/"out"), Ctx(tmp_path/"out", src_dir))
    res = ensure_english_variant(doc, ports, {"overwrite": False, "dry_run": True, "tgt_lang": "en"})
    assert res["status"] == "skipped_already_en"
    assert isinstance(res["written_path"], str)


@pytest.mark.integration
def test_batch_workers_and_counts(tmp_path):
    src = tmp_path/"src"; src.mkdir()
    paths = []
    for i in range(3):
        p = src / f"f{i}.md"; p.write_text("Ahoj svet\n", encoding="utf-8"); paths.append(p)
    docs = [MarkdownDoc(doc_id=f"d{i}", path=str(p), variant="original", lang=None, text_md=p.read_text(encoding="utf-8")) for i,p in enumerate(paths)]
    ports = Ports(FakeLangId("sk", 0.99), FakeTranslate(), FakeWriter(tmp_path/"out"), Ctx(tmp_path/"out", src))
    batch = ensure_english_for_batch(docs, ports, {"workers": 2, "on_error": "skip", "tgt_lang": "en", "overwrite": True})
    assert batch["total"] == 3
    assert batch["created"] == 3
