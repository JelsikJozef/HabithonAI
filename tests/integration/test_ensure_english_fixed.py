from pathlib import Path

import pytest

from src.preprocessing.app.ensure_english import ensure_english_for_batch, ensure_english_variant
from src.preprocessing.domain.models_markdown import MarkdownDoc


class FakeLangId:
    def __init__(self, code="sk", conf=0.99):
        self.code = code
        self.conf = conf

    def detect(self, text, hints=None, *, context=None):
        return self.code, self.conf

    def detect_topk(self, text, k=5, hints=None, *, context=None):
        """Top-k detection with configurable results for testing routing logic."""
        if hasattr(self, "_topk_results"):
            return self._topk_results

        topk = [{"code": self.code, "score": self.conf, "name": f"lang_{self.code}"}]
        if k > 1:
            alt_code = "de" if self.code == "sk" else "sk"
            topk.append({"code": alt_code, "score": self.conf - 0.1, "name": f"lang_{alt_code}"})

        return {
            "lang_code": self.code,
            "confidence": self.conf,
            "topk": topk[:k],
            "flags": {
                "low_confidence": self.conf < 0.70,
                "close_top2": len(topk) > 1 and abs(topk[0]["score"] - topk[1]["score"]) < 0.05,
            },
        }

    def set_topk_results(self, results):
        self._topk_results = results


class FakeEnglishDetector:
    def __init__(self, base_confidence=0.95):
        self.base_confidence = base_confidence
        self._custom_responses = {}

    def english_confidence(self, text):
        return self._custom_responses.get(text, self.base_confidence)

    def set_confidence_for_text(self, text, confidence):
        self._custom_responses[text] = confidence


class FakeSimilarity:
    def __init__(self, base_similarity=0.1):
        self.base_similarity = base_similarity
        self._custom_similarities = {}

    def similarity(self, text1, text2):
        key = (text1, text2)
        if key in self._custom_similarities:
            return self._custom_similarities[key]
        if text1 == text2:
            return 1.0
        if len(text1) > 0 and len(text2) > 0 and text1.lower() == text2.lower():
            return 0.95
        return self.base_similarity

    def set_similarity(self, text1, text2, similarity):
        self._custom_similarities[(text1, text2)] = similarity


class FakeTranslate:
    def __init__(self):
        self.calls = 0
        self._responses = {}

    def translate_md(self, doc, src_lang, tgt_lang, options=None, *, context=None):
        self.calls += 1
        key = (src_lang, doc.text_md)
        if key in self._responses:
            return doc.copy_with(variant="english", lang="en", text_md=self._responses[key])

        translated_text = doc.text_md.replace("Ahoj", "Hello").replace("svet", "world")
        return doc.copy_with(variant="english", lang="en", text_md=translated_text)

    def set_translation(self, src_lang, input_text, output_text):
        self._responses[(src_lang, input_text)] = output_text

    def capabilities(self):
        return {"name": "fake", "calls": self.calls}


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
        return {
            "out_md_path": str(out.with_suffix(".md")),
            "assets_dir": str(out.parent / ctx.assets_subdir),
            "sidecar_meta_path": None,
        }

    def write(self, doc, ctx):
        paths = self.compute_paths(doc, ctx)
        out_md = Path(paths["out_md_path"])
        out_md.parent.mkdir(parents=True, exist_ok=True)
        text = doc.text_md.replace("\r\n", "\n").replace("\r", "\n")
        out_md.write_text(text, encoding="utf-8", newline="\n")
        return {
            "status": "ok",
            "out_md_path": str(out_md),
            "assets_dir": str(Path(paths["assets_dir"])) if paths.get("assets_dir") else None,
            "assets_written": 0,
            "bytes_written_md": len(text.encode("utf-8")),
            "bytes_written_assets": 0,
            "sidecar_written": False,
            "renamed_assets": [],
            "warnings": [],
            "error": None,
        }


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
    def __init__(
        self,
        langid,
        translate,
        writer,
        ctx,
        english_detector=None,
        similarity=None,
        translate_secondary=None,
    ):
        self.langid = langid
        self.translate = translate
        self.writer = writer
        self.glossary = None
        self.cache = None
        self.writer_ctx = ctx
        self.english_detector = english_detector
        self.similarity = similarity
        self.translate_secondary = translate_secondary


@pytest.mark.integration
def test_created_flow(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    md_path = src_dir / "a.md"
    md_path.write_text("Ahoj svet\n", encoding="utf-8")
    doc = MarkdownDoc(
        doc_id="d1",
        path=str(md_path),
        variant="original",
        lang=None,
        text_md=md_path.read_text(encoding="utf-8"),
    )
    ports = Ports(
        FakeLangId("sk", 0.99),
        FakeTranslate(),
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src_dir),
    )
    res = ensure_english_variant(
        doc, ports, {"overwrite": True, "dry_run": False, "tgt_lang": "en"}
    )
    assert res["status"] == "created"
    assert Path(res["written_path"]).exists()


@pytest.mark.integration
def test_skipped_already_en_dry_run(tmp_path):
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    md_path = src_dir / "b.md"
    md_path.write_text("Hello world\n", encoding="utf-8")
    doc = MarkdownDoc(
        doc_id="d2",
        path=str(md_path),
        variant="original",
        lang="en",
        text_md=md_path.read_text(encoding="utf-8"),
    )
    ports = Ports(
        FakeLangId("en", 0.99),
        FakeTranslate(),
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src_dir),
    )
    res = ensure_english_variant(
        doc, ports, {"overwrite": False, "dry_run": True, "tgt_lang": "en"}
    )
    assert res["status"] == "skipped_already_en"
    assert isinstance(res["written_path"], str)


@pytest.mark.integration
def test_batch_workers_and_counts(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    paths = []
    for i in range(3):
        p = src / f"f{i}.md"
        p.write_text("Ahoj svet\n", encoding="utf-8")
        paths.append(p)
    docs = [
        MarkdownDoc(
            doc_id=f"d{i}",
            path=str(p),
            variant="original",
            lang=None,
            text_md=p.read_text(encoding="utf-8"),
        )
        for i, p in enumerate(paths)
    ]
    ports = Ports(
        FakeLangId("sk", 0.99),
        FakeTranslate(),
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src),
    )
    batch = ensure_english_for_batch(
        docs, ports, {"workers": 2, "on_error": "skip", "tgt_lang": "en", "overwrite": True}
    )
    assert batch["total"] == 3
    assert batch["created"] == 3
