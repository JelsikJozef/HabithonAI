"""Integration tests for the single-document ProcessingOrchestrator.

Covers:
(a) an already-English document running the full anonymize -> Step1 -> Step2 -> Step3
    chain, producing a doc_uid, chunks and metadata, with distinct content giving a
    distinct doc_uid (no collision);
(b) a non-English document taken through translation (via ``ensure_english_variant``
    with a stub translate port) and then the chain;
(c) invariant 1: no original PII appears in Step2 chunks nor in the text handed to the
    (stubbed) LLM.

Anonymization uses the real ``PiiService`` wired with the offline ``RegexDetector`` and a
temp ``FileTokenVault``. The Step3 LLM (``summarize_keywords``) is stubbed -- no real
OpenAI call is made.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from src.anonymization.adapters.detectors.regex_detector import RegexDetector
from src.anonymization.adapters.token_vault.file_store import FileTokenVault
from src.anonymization.app.services.pii_service import PiiService
from src.preprocessing.app.ensure_english import ensure_english_variant
from src.preprocessing.app.processing_orchestrator import (
    PairResult,
    process_document,
    process_document_pair,
)
from src.preprocessing.domain.models_markdown import MarkdownDoc
from src.shared.hashing import derive_context_id

# PII embedded in fixtures; must be caught by RegexDetector (EMAIL/PHONE).
PII_EMAIL = "alice.smith@example.com"
PII_PHONE = "+1-555-123-4567"


def _meta() -> dict[str, Any]:
    return {
        "doc_type": "note",
        "category": "general",
        "language": "en",
        "anonymizer_versions": {"detector": "regex"},
    }


def _meta_sk() -> dict[str, Any]:
    return {
        "doc_type": "note",
        "category": "general",
        "language": "sk",
        "anonymizer_versions": {"detector": "regex"},
    }


def _doc_text(body: str) -> str:
    return (
        "# Quarterly Report\n\n"
        f"{body}\n\n"
        f"Please contact {PII_EMAIL} or call {PII_PHONE} for details.\n\n"
        "## Notes\n\n"
        "The project remains on schedule and within the approved budget.\n"
    )


def _make_anonymizer(tmp_path: Path) -> PiiService:
    return PiiService(
        detectors=[RegexDetector()],
        vault=FileTokenVault(base_dir=str(tmp_path / "vault")),
    )


@pytest.fixture()
def stub_llm(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Patch Step3's LLM call; capture the text it receives. No network."""
    captured: list[str] = []

    def _fake_summarize_keywords(
        text: str, *, model: str, timeout_s: int, seed: int
    ) -> dict[str, Any]:
        captured.append(text)
        return {
            "summary": "The report summarizes quarterly project status and budget.",
            "keywords": ["report", "project", "budget", "schedule", "status"],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        }

    monkeypatch.setattr(
        "src.preprocessing.analysis.step3_summary.summarize_keywords",
        _fake_summarize_keywords,
    )
    return captured


def _assert_no_pii(text: str) -> None:
    assert PII_EMAIL not in text
    assert PII_PHONE not in text


# ---------------------------------------------------------------------------
# (a) already-English document
# ---------------------------------------------------------------------------


def test_already_english_runs_full_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    doc = MarkdownDoc(
        doc_id="d-en",
        path=str(tmp_path / "src" / "a.md"),
        variant="english",
        lang="en",
        text_md=_doc_text("Revenue grew steadily across all regions."),
        meta=_meta(),
    )

    res = process_document(
        doc,
        _make_anonymizer(tmp_path),
        context_id="ctx_test_a",
        context={"run_id": "r-a"},
    )

    assert res.status == "ok", res.errors
    assert res.document_uid
    assert res.content_hash
    assert res.anonymization_mappings >= 2  # email + phone

    # Step2 produced at least one chunk.
    chunks = res.step2["chunks"]
    assert len(chunks) >= 1

    # Step3 produced a one-sentence summary and exactly 5 keywords.
    assert res.step3["summary_one_sentence"]
    assert len(res.step3["keywords_top5"]) == 5

    # The LLM was invoked exactly once via the stub.
    assert len(stub_llm) == 1


def test_context_id_derived_and_recorded_in_step1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    text = _doc_text("Revenue grew steadily across all regions.")
    doc = MarkdownDoc(
        doc_id="d-en",
        path=str(tmp_path / "src" / "a.md"),
        variant="english",
        lang="en",
        text_md=text,
        meta=_meta(),
    )

    # No explicit context_id -> orchestrator derives a variant-scoped one from source text.
    res = process_document(doc, _make_anonymizer(tmp_path), context={"run_id": "r"})

    assert res.status == "ok", res.errors
    expected = derive_context_id(text, "english")
    assert res.context_id == expected
    assert expected.endswith("_en")

    # The vault context is recorded in the Step1 artifact so deanonymization-by-doc_uid can
    # resolve the mappings; it does not change the doc_uid derivation.
    result_json = tmp_path / "outputs" / "artifacts" / res.document_uid / "step1" / "result.json"
    persisted = json.loads(result_json.read_text(encoding="utf-8"))
    assert persisted["context_id"] == expected


def test_orig_and_en_get_separate_vault_contexts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    # Same source text processed as two variants must land in distinct vault files, so
    # original and English mappings never mix.
    monkeypatch.chdir(tmp_path)
    text = _doc_text("Identical body across variants.")
    vault_dir = tmp_path / "vault"
    anonymizer = PiiService(
        detectors=[RegexDetector()], vault=FileTokenVault(base_dir=str(vault_dir))
    )

    en_doc = MarkdownDoc(
        doc_id="d",
        path=str(tmp_path / "src" / "a.md"),
        variant="english",
        lang="en",
        text_md=text,
        meta=_meta(),
    )
    res_en = process_document(en_doc, anonymizer, context={"run_id": "r"})
    assert res_en.status == "ok", res_en.errors

    # Anonymize the same text under the "orig" variant context directly via the service.
    orig_ctx = derive_context_id(text, "orig")
    anonymizer.anonymize(text, context_id=orig_ctx, language="en")

    assert res_en.context_id == derive_context_id(text, "english")
    assert res_en.context_id != orig_ctx
    assert (vault_dir / f"{res_en.context_id}.json").exists()
    assert (vault_dir / f"{orig_ctx}.json").exists()


def test_deanonymize_roundtrip_via_recorded_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    # End-to-end: run the chain, then restore the original text from the vault using the
    # context_id recorded in the Step1 artifact.
    monkeypatch.chdir(tmp_path)
    vault_dir = tmp_path / "vault"
    vault = FileTokenVault(base_dir=str(vault_dir))
    anonymizer = PiiService(detectors=[RegexDetector()], vault=vault)

    text = _doc_text("Restore me end to end.")
    doc = MarkdownDoc(
        doc_id="d",
        path=str(tmp_path / "src" / "a.md"),
        variant="english",
        lang="en",
        text_md=text,
        meta=_meta(),
    )
    res = process_document(doc, anonymizer, context={"run_id": "r"})
    assert res.status == "ok", res.errors

    # Resolve context_id from the artifact, then deanonymize the persisted normalized text.
    result_json = tmp_path / "outputs" / "artifacts" / res.document_uid / "step1" / "result.json"
    persisted = json.loads(result_json.read_text(encoding="utf-8"))
    anon_text = persisted["normalized_text"]
    _assert_no_pii(anon_text)

    restored = anonymizer.deanonymize(anon_text, persisted["context_id"]).restored_text
    assert PII_EMAIL in restored
    assert PII_PHONE in restored


def test_distinct_content_yields_distinct_uid_no_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    anonymizer = _make_anonymizer(tmp_path)

    doc1 = MarkdownDoc(
        doc_id="d1",
        path=str(tmp_path / "src" / "a.md"),
        variant="english",
        lang="en",
        text_md=_doc_text("First document body about revenue."),
        meta=_meta(),
    )
    doc2 = MarkdownDoc(
        doc_id="d2",
        path=str(tmp_path / "src" / "b.md"),
        variant="english",
        lang="en",
        text_md=_doc_text("Second document body about costs and risk."),
        meta=_meta(),
    )

    r1 = process_document(doc1, anonymizer, context_id="ctx1")
    r2 = process_document(doc2, anonymizer, context_id="ctx2")

    assert r1.status == "ok", r1.errors
    assert r2.status == "ok", r2.errors
    assert r1.document_uid != r2.document_uid


def test_non_english_step3_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Step3 (the EN-only LLM step) must refuse a non-English variant.
    monkeypatch.chdir(tmp_path)
    doc = MarkdownDoc(
        doc_id="d-sk",
        path=str(tmp_path / "src" / "sk.md"),
        variant="original",
        lang="sk",
        text_md="Ahoj svet.",
        meta=_meta_sk(),
    )
    res = process_document(doc, _make_anonymizer(tmp_path), context_id="ctx_sk")
    assert res.status == "failed"
    assert res.errors and res.errors[0]["code"] == "step3_requires_english"


def test_non_english_runs_step1_step2_without_step3(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # With run_step3=False the original (SK) variant flows through Step1 + Step2.
    monkeypatch.chdir(tmp_path)
    doc = MarkdownDoc(
        doc_id="d-sk",
        path=str(tmp_path / "src" / "sk.md"),
        variant="original",
        lang="sk",
        text_md=_doc_text("Tržby rástli rovnomerne vo všetkých regiónoch."),
        meta=_meta_sk(),
    )
    res = process_document(doc, _make_anonymizer(tmp_path), run_step3=False)
    assert res.status == "ok", res.errors
    assert res.variant == "orig"
    assert res.step3 is None
    assert len(res.step2["chunks"]) >= 1
    # No Step3 artifacts for the original variant.
    assert not (tmp_path / "outputs" / "artifacts" / res.document_uid / "step3").exists()


# ---------------------------------------------------------------------------
# (b) translation -> chain
# ---------------------------------------------------------------------------


class _StubLangId:
    def detect(
        self,
        text: str,
        hints: dict[str, Any] | None = None,
        *,
        context: dict[str, Any] | None = None,
    ) -> tuple[str | None, float | None]:
        return "sk", 0.99


class _StubTranslate:
    """Stub translation: keeps body text (incl. PII) but marks the doc English."""

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


class _StubWriter:
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


class _Ctx:
    def __init__(self, out: Path, src: Path) -> None:
        self.out_root = str(out)
        self.src_root = str(src)
        self.assets_subdir = "assets"
        self.assets_layout = "per_doc"
        self.write_meta = "sidecar"
        self.overwrite = True
        self.dry_run = False
        self.ensure_final_newline = True


class _Ports:
    def __init__(self, langid: Any, translate: Any, writer: Any, ctx: Any) -> None:
        self.langid = langid
        self.translate = translate
        self.writer = writer
        self.glossary = None
        self.cache = None
        self.writer_ctx = ctx
        self.english_detector = None
        self.similarity = None
        self.translate_secondary = None


def test_translation_then_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    src_dir = tmp_path / "src"
    src_dir.mkdir(parents=True, exist_ok=True)
    md_path = src_dir / "sk.md"
    body = _doc_text("Tržby rástli rovnomerne.")
    md_path.write_text(body, encoding="utf-8")

    sk_doc = MarkdownDoc(
        doc_id="d-sk",
        path=str(md_path),
        variant="original",
        lang="sk",
        text_md=body,
        meta=_meta(),
    )

    translate = _StubTranslate()
    ports = _Ports(
        langid=_StubLangId(),
        translate=translate,
        writer=_StubWriter(),
        ctx=_Ctx(out=tmp_path / "out", src=src_dir),
    )
    en_res = ensure_english_variant(
        sk_doc,
        ports,
        {
            "overwrite": True,
            "dry_run": False,
            "tgt_lang": "en",
            "en_confidence_threshold": 0.95,
            "copy_when_already_en": False,
        },
    )
    assert translate.calls == 1
    written = en_res.get("written_path")
    assert written

    # Build the English MarkdownDoc from the translation output, then run the chain.
    en_text = Path(written).read_text(encoding="utf-8")
    en_doc = MarkdownDoc(
        doc_id="d-sk",
        path=written,
        variant="english",
        lang="en",
        text_md=en_text,
        meta=_meta(),
    )

    monkeypatch.chdir(tmp_path)
    res = process_document(
        en_doc,
        _make_anonymizer(tmp_path),
        context_id="ctx_sk_en",
        context={"run_id": "r-b"},
    )

    assert res.status == "ok", res.errors
    assert res.document_uid
    assert len(res.step2["chunks"]) >= 1
    assert len(res.step3["keywords_top5"]) == 5


# ---------------------------------------------------------------------------
# (c) invariant 1 -- no PII downstream
# ---------------------------------------------------------------------------


def test_no_pii_in_chunks_or_llm_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    doc = MarkdownDoc(
        doc_id="d-pii",
        path=str(tmp_path / "src" / "p.md"),
        variant="english",
        lang="en",
        text_md=_doc_text("Confidential numbers and contacts below."),
        meta=_meta(),
    )

    res = process_document(doc, _make_anonymizer(tmp_path), context_id="ctx_pii")
    assert res.status == "ok", res.errors

    # No original PII in any Step2 chunk.
    chunks_path = tmp_path / "outputs" / "artifacts" / res.document_uid / "step2" / "chunks.jsonl"
    chunks_text = chunks_path.read_text(encoding="utf-8")
    _assert_no_pii(chunks_text)
    for line in chunks_text.splitlines():
        if line.strip():
            _assert_no_pii(json.loads(line)["text"])

    # The text handed to the (stubbed) LLM is anonymized: no PII, anon tokens present.
    assert len(stub_llm) == 1
    llm_input = stub_llm[0]
    _assert_no_pii(llm_input)
    assert "h:" in llm_input


# ---------------------------------------------------------------------------
# (d) both variants via process_document_pair
# ---------------------------------------------------------------------------


def _read_merged(tmp_path: Path, uid: str) -> dict[str, Any]:
    path = tmp_path / "outputs" / "artifacts" / uid / "metadata_merged.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _sk_and_en_docs(tmp_path: Path) -> tuple[MarkdownDoc, MarkdownDoc]:
    original = MarkdownDoc(
        doc_id="d",
        path=str(tmp_path / "src" / "doc.md"),
        variant="original",
        lang="sk",
        text_md=_doc_text("Tržby rástli rovnomerne vo všetkých regiónoch."),
        meta=_meta_sk(),
    )
    english = MarkdownDoc(
        doc_id="d",
        path=str(tmp_path / "out" / "en" / "doc.md"),
        variant="english",
        lang="en",
        text_md=_doc_text("Revenue grew steadily across all regions."),
        meta=_meta(),
    )
    return original, english


def test_pair_two_variants_distinct_uid_and_bound_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    original, english = _sk_and_en_docs(tmp_path)

    res = process_document_pair(
        original, english, _make_anonymizer(tmp_path), context={"run_id": "r"}
    )

    assert res.status == "ok", res.errors
    assert isinstance(res, PairResult)
    orig_uid = res.original.document_uid
    en_uid = res.english.document_uid

    # Two distinct variants, both segmented.
    assert orig_uid and en_uid and orig_uid != en_uid
    assert res.original.variant == "orig" and res.english.variant == "en"
    assert len(res.original.step2["chunks"]) >= 1
    assert len(res.english.step2["chunks"]) >= 1

    # Step3 ran exactly once (English only); original has no step3 tree.
    assert len(stub_llm) == 1
    assert res.original.step3 is None
    assert not (tmp_path / "outputs" / "artifacts" / orig_uid / "step3").exists()

    # One metadata payload bound to BOTH variants, with preserved cross-links.
    en_meta = _read_merged(tmp_path, en_uid)
    orig_meta = _read_merged(tmp_path, orig_uid)
    assert res.metadata_bound is True
    assert res.links == {"orig": orig_uid, "en": en_uid}
    for m, expected_variant, expected_lang in (
        (en_meta, "en", "en"),
        (orig_meta, "orig", "sk"),
    ):
        assert m["variant"] == expected_variant
        assert m["language"] == expected_lang  # each keeps its OWN canonical metadata
        assert m["summary_one_sentence"] == en_meta["summary_one_sentence"]
        assert m["keywords_top5"] == en_meta["keywords_top5"]
        assert m["metadata_source"] == {"variant": "en", "document_uid": en_uid}
        assert m["variants"] == {"orig": orig_uid, "en": en_uid}


def test_pair_invariant1_no_pii_in_either_variant(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    original, english = _sk_and_en_docs(tmp_path)

    res = process_document_pair(original, english, _make_anonymizer(tmp_path))
    assert res.status == "ok", res.errors

    # No original PII in either variant's chunks.
    for uid in (res.original.document_uid, res.english.document_uid):
        chunks = (tmp_path / "outputs" / "artifacts" / uid / "step2" / "chunks.jsonl").read_text(
            encoding="utf-8"
        )
        _assert_no_pii(chunks)

    # Only the anonymized English text reaches the (stubbed) LLM, exactly once.
    assert len(stub_llm) == 1
    _assert_no_pii(stub_llm[0])
    assert "h:" in stub_llm[0]


def test_pair_already_english_single_chain(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, stub_llm: list[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    # Already-English source: ensure_english yields an English copy of the same text.
    text = _doc_text("Revenue grew steadily across all regions.")
    original = MarkdownDoc(
        doc_id="d",
        path=str(tmp_path / "src" / "a.md"),
        variant="original",
        lang="en",
        text_md=text,
        meta=_meta(),
    )
    english = original.copy_with(variant="english")

    res = process_document_pair(original, english, _make_anonymizer(tmp_path))

    assert res.status == "ok", res.errors
    assert res.original is None  # single chain, no redundant orig variant
    assert res.english.document_uid
    assert res.links == {"en": res.english.document_uid}
    assert len(stub_llm) == 1
    merged = _read_merged(tmp_path, res.english.document_uid)
    assert merged["variant"] == "en"
    assert merged["variants"] == {"en": res.english.document_uid}
