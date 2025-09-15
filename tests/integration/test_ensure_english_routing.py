"""Tests for advanced routing functionality in ensure_english.py"""

from pathlib import Path

import pytest

from src.preprocessing.app.ensure_english import ensure_english_variant
from src.preprocessing.domain.models_markdown import MarkdownDoc


class FakeLangId:
    def __init__(self, code="sk", conf=0.99):
        self.code = code
        self.conf = conf

    def detect(self, text, hints=None, *, context=None):
        return self.code, self.conf

    def detect_topk(self, text, k=5, hints=None, *, context=None):
        """Top-k detection with configurable results for testing routing logic."""
        # Return configurable top-k results for testing
        if hasattr(self, "_topk_results"):
            return self._topk_results

        # Default behavior
        topk = [{"code": self.code, "score": self.conf, "name": f"lang_{self.code}"}]
        if k > 1:
            # Add a second candidate with slightly lower score
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
        """Configure custom top-k results for testing."""
        self._topk_results = results


class FakeEnglishDetector:
    def __init__(self, base_confidence=0.95):
        self.base_confidence = base_confidence
        self._custom_responses = {}

    def english_confidence(self, text):
        # Return custom response if configured, otherwise base confidence
        return self._custom_responses.get(text, self.base_confidence)

    def set_confidence_for_text(self, text, confidence):
        """Configure custom confidence for specific text."""
        self._custom_responses[text] = confidence


class FakeSimilarity:
    def __init__(self, base_similarity=0.1):
        self.base_similarity = base_similarity
        self._custom_similarities = {}

    def similarity(self, text1, text2):
        # Return custom similarity if configured
        key = (text1, text2)
        if key in self._custom_similarities:
            return self._custom_similarities[key]
        # Simple logic: if texts are very similar, return high similarity
        if text1 == text2:
            return 1.0
        if len(text1) > 0 and len(text2) > 0 and text1.lower() == text2.lower():
            return 0.95
        return self.base_similarity

    def set_similarity(self, text1, text2, similarity):
        """Configure custom similarity for specific text pair."""
        self._custom_similarities[(text1, text2)] = similarity


class FakeTranslate:
    def __init__(self):
        self.calls = 0
        self._responses = {}

    def translate_md(self, doc, src_lang, tgt_lang, options=None, *, context=None):
        self.calls += 1
        # Return custom response if configured
        key = (src_lang, doc.text_md)
        if key in self._responses:
            return doc.copy_with(variant="english", lang="en", text_md=self._responses[key])

        # Default: simple translation simulation
        translated_text = doc.text_md.replace("Ahoj", "Hello").replace("svet", "world")
        return doc.copy_with(variant="english", lang="en", text_md=translated_text)

    def set_translation(self, src_lang, input_text, output_text):
        """Configure custom translation response."""
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


# -----------------------------
# Advanced routing tests
# -----------------------------


@pytest.mark.integration
def test_topk_langid_basic(tmp_path):
    """Test that top-k language detection is used when available."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    md_path = src_dir / "test.md"
    md_path.write_text("Ahoj svet\n", encoding="utf-8")

    doc = MarkdownDoc(
        doc_id="d1",
        path=str(md_path),
        variant="original",
        lang=None,
        text_md=md_path.read_text(encoding="utf-8"),
    )

    langid = FakeLangId("sk", 0.85)
    ports = Ports(
        langid,
        FakeTranslate(),
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src_dir),
    )

    res = ensure_english_variant(
        doc, ports, {"overwrite": True, "dry_run": False, "tgt_lang": "en"}
    )

    assert res["status"] == "created"
    assert res["src_lang"] == "sk"
    # Check routing metadata is present
    assert "routing" in res["meta"]
    assert "topk" in res["meta"]["routing"]


@pytest.mark.integration
def test_probe_selection_with_low_confidence(tmp_path):
    """Test that probe selection is triggered for low confidence detection."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    md_path = src_dir / "test.md"
    md_path.write_text("Ahoj svet\n", encoding="utf-8")

    doc = MarkdownDoc(
        doc_id="d1",
        path=str(md_path),
        variant="original",
        lang=None,
        text_md=md_path.read_text(encoding="utf-8"),
    )

    # Set up low confidence detection that should trigger probe
    langid = FakeLangId("sk", 0.60)  # Below tau_low (0.70)
    langid.set_topk_results(
        {
            "lang_code": "sk",
            "confidence": 0.60,
            "topk": [
                {"code": "sk", "score": 0.60, "name": "Slovak"},
                {"code": "de", "score": 0.55, "name": "German"},
            ],
            "flags": {"low_confidence": True, "close_top2": False},
        }
    )

    # Set up english detector to prefer German translation
    english_detector = FakeEnglishDetector()
    english_detector.set_confidence_for_text("Hello world", 0.95)  # High EN confidence

    similarity = FakeSimilarity()
    similarity.set_similarity(
        "Ahoj svet\n", "Hello world", 0.1
    )  # Low similarity (good translation)

    translate = FakeTranslate()
    translate.set_translation("de", "Ahoj svet\n", "Hello world")
    translate.set_translation("sk", "Ahoj svet\n", "Hello world")  # Same output for test

    ports = Ports(
        langid,
        translate,
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src_dir),
        english_detector=english_detector,
        similarity=similarity,
    )

    res = ensure_english_variant(
        doc,
        ports,
        {
            "overwrite": True,
            "dry_run": False,
            "tgt_lang": "en",
            "routing": {"tau_low": 0.70, "tau_en": 0.90},
        },
    )

    assert res["status"] == "created"
    assert "routing" in res["meta"]
    assert res["meta"]["routing"]["probe"] is True  # Probe was triggered
    assert "en_confidence" in res["meta"]["routing"]


@pytest.mark.integration
def test_post_translation_validation_success(tmp_path):
    """Test post-translation validation when output meets thresholds."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    md_path = src_dir / "test.md"
    md_path.write_text("Ahoj svet\n", encoding="utf-8")

    doc = MarkdownDoc(
        doc_id="d1",
        path=str(md_path),
        variant="original",
        lang=None,
        text_md=md_path.read_text(encoding="utf-8"),
    )

    langid = FakeLangId("sk", 0.95)

    # Set up successful validation
    english_detector = FakeEnglishDetector()
    english_detector.set_confidence_for_text("Hello world", 0.95)  # Above tau_en (0.90)

    similarity = FakeSimilarity()
    similarity.set_similarity("Ahoj svet\n", "Hello world", 0.1)  # Below sim_noop (0.92)

    translate = FakeTranslate()
    translate.set_translation("sk", "Ahoj svet\n", "Hello world")

    ports = Ports(
        langid,
        translate,
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src_dir),
        english_detector=english_detector,
        similarity=similarity,
    )

    res = ensure_english_variant(
        doc,
        ports,
        {
            "overwrite": True,
            "dry_run": False,
            "tgt_lang": "en",
            "routing": {"tau_en": 0.90, "similarity_noop_threshold": 0.92},
        },
    )

    assert res["status"] == "created"
    assert "routing" in res["meta"]
    assert res["meta"]["routing"]["en_confidence"] >= 0.90
    assert res["meta"]["routing"]["similarity"] < 0.92


@pytest.mark.integration
def test_exhausted_retry_ladder(tmp_path):
    """Test behavior when all retry attempts are exhausted."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    md_path = src_dir / "test.md"
    md_path.write_text("Ahoj svet\n", encoding="utf-8")

    doc = MarkdownDoc(
        doc_id="d1",
        path=str(md_path),
        variant="original",
        lang=None,
        text_md=md_path.read_text(encoding="utf-8"),
    )

    langid = FakeLangId("sk", 0.95)
    langid.set_topk_results(
        {
            "lang_code": "sk",
            "confidence": 0.95,
            "topk": [
                {"code": "sk", "score": 0.95, "name": "Slovak"},
                {"code": "de", "score": 0.85, "name": "German"},
            ],
            "flags": {"low_confidence": False, "close_top2": False},
        }
    )

    # Set up failing validation for all attempts
    english_detector = FakeEnglishDetector()
    english_detector.set_confidence_for_text("Bad translation", 0.30)  # Always below tau_en

    similarity = FakeSimilarity()
    similarity.set_similarity("Ahoj svet\n", "Bad translation", 0.1)

    translate = FakeTranslate()
    translate.set_translation("sk", "Ahoj svet\n", "Bad translation")
    translate.set_translation("de", "Ahoj svet\n", "Bad translation")

    ports = Ports(
        langid,
        translate,
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src_dir),
        english_detector=english_detector,
        similarity=similarity,
    )

    res = ensure_english_variant(
        doc,
        ports,
        {
            "overwrite": True,
            "dry_run": False,
            "tgt_lang": "en",
            "routing": {"tau_en": 0.90, "max_retries": 2},
        },
    )

    assert res["status"] == "failed"
    assert res["error"]["code"] in ["post_validation_failed", "unresolved"]
    assert "routing" in res["meta"]


@pytest.mark.integration
def test_backward_compatibility_without_advanced_ports(tmp_path):
    """Test that system works without english_detector and similarity ports."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()
    md_path = src_dir / "test.md"
    md_path.write_text("Ahoj svet\n", encoding="utf-8")

    doc = MarkdownDoc(
        doc_id="d1",
        path=str(md_path),
        variant="original",
        lang=None,
        text_md=md_path.read_text(encoding="utf-8"),
    )

    # No english_detector or similarity ports
    ports = Ports(
        FakeLangId("sk", 0.95),
        FakeTranslate(),
        FakeWriter(tmp_path / "out"),
        Ctx(tmp_path / "out", src_dir),
    )

    res = ensure_english_variant(
        doc, ports, {"overwrite": True, "dry_run": False, "tgt_lang": "en"}
    )

    assert res["status"] == "created"
    # Should work without advanced routing features
    assert "routing" in res["meta"]
    # But probe should be False since advanced ports not available
    assert res["meta"]["routing"].get("probe", False) is False
