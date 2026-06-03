from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from src.preprocessing.app.ensure_english import ensure_english_variant
from src.preprocessing.domain.models_markdown import MarkdownDoc


class StubLangId:
    def __init__(self, code: str | None, conf: float | None) -> None:
        self.code = code
        self.conf = conf

    def detect(
        self,
        text: str,
        hints: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[str | None, float | None]:
        return self.code, self.conf


class StubTranslate:
    def __init__(self) -> None:
        self.calls = 0

    def translate_md(
        self,
        doc: MarkdownDoc,
        src_lang: str,
        tgt_lang: str,
        options: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> MarkdownDoc:
        self.calls += 1
        return doc.copy_with(variant="english", lang="en")

    def capabilities(self) -> dict[str, Any]:
        return {"name": "stub", "calls": self.calls}


class StubWriter:
    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def compute_paths(self, doc: MarkdownDoc, ctx: Any) -> dict[str, str | None]:
        src = Path(doc.path)
        try:
            rel = src.resolve().relative_to(Path(ctx.src_root).resolve())
        except Exception:
            rel = Path(src.name)
        out = Path(ctx.out_root) / "en" / rel
        return {
            "out_md_path": str(out.with_suffix(".md")),
            "assets_dir": str(out.parent / ctx.assets_subdir),
            "sidecar_meta_path": None,
        }

    def write(self, doc: MarkdownDoc, ctx: Any) -> dict[str, Any]:
        paths = self.compute_paths(doc, ctx)
        out_md = Path(str(paths["out_md_path"]))
        out_md.parent.mkdir(parents=True, exist_ok=True)
        text = doc.text_md.replace("\r\n", "\n").replace("\r", "\n")
        out_md.write_text(text, encoding="utf-8", newline="\n")
        return {
            "status": "ok",
            "out_md_path": str(out_md),
            "assets_dir": paths.get("assets_dir"),
            "assets_written": 0,
            "bytes_written_md": len(text.encode("utf-8")),
            "bytes_written_assets": 0,
            "sidecar_written": False,
            "renamed_assets": [],
            "warnings": [],
            "error": None,
        }


class Ctx:
    def __init__(self, out: Path, src: Path) -> None:
        self.out_root = str(out)
        self.src_root = str(src)
        self.assets_subdir = "assets"
        self.assets_layout = "per_doc"
        self.write_meta = "sidecar"
        self.overwrite = True
        self.dry_run = False
        self.ensure_final_newline = True


class Ports:
    def __init__(
        self,
        langid: StubLangId,
        translate: StubTranslate,
        writer: StubWriter,
        ctx: Ctx,
    ) -> None:
        self.langid = langid
        self.translate = translate
        self.writer = writer
        self.glossary = None
        self.cache = None
        self.writer_ctx = ctx
        self.english_detector = None
        self.similarity = None
        self.translate_secondary = None


@pytest.mark.integration
@pytest.mark.parametrize(
    "doc_lang, det_code, det_conf, en_threshold, expect_skip",
    [
        ("en", None, None, 0.95, True),
        ("EN", None, None, 0.95, True),
        ("En", None, None, 0.95, True),
        (None, "en", 0.50, 0.95, True),
        (None, None, 0.99, 0.95, True),
        (None, None, 0.50, 0.95, False),
        (None, "sk", 0.99, 0.95, False),
        (None, "sk", 0.50, 0.95, False),
    ],
)
def test_skip_predicate_matrix(
    tmp_path: Path,
    doc_lang: str | None,
    det_code: str | None,
    det_conf: float | None,
    en_threshold: float,
    expect_skip: bool,
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    md_path = src_dir / "a.md"
    md_path.write_text("Some text\n", encoding="utf-8")
    doc = MarkdownDoc(
        doc_id="d",
        path=str(md_path),
        variant="original",
        lang=doc_lang,
        text_md="Some text\n",
    )
    ports = Ports(
        langid=StubLangId(det_code, det_conf),
        translate=StubTranslate(),
        writer=StubWriter(tmp_path / "out"),
        ctx=Ctx(out=tmp_path / "out", src=src_dir),
    )
    res = ensure_english_variant(
        doc,
        ports,
        {
            "overwrite": True,
            "dry_run": True,
            "tgt_lang": "en",
            "en_confidence_threshold": en_threshold,
            "copy_when_already_en": False,
        },
    )
    if expect_skip:
        assert res["status"] == "skipped_already_en"
        assert ports.translate.calls == 0
    else:
        assert res["status"] != "skipped_already_en"
