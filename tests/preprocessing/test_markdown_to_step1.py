from __future__ import annotations

from src.preprocessing.app.markdown_to_step1 import markdown_doc_to_step1_inputs
from src.preprocessing.app.normalize import Step1Inputs
from src.preprocessing.domain.models_markdown import MarkdownDoc


def _doc(variant: str | None = "original", meta: dict | None = None) -> MarkdownDoc:
    return MarkdownDoc(
        doc_id="md::sample.md",
        path="/abs/sample.md",
        variant=variant,  # type: ignore[arg-type]
        lang="en",
        text_md="# Title\n\nBody text.",
        meta=meta
        if meta is not None
        else {
            "doc_type": "note",
            "category": "general",
            "language": "en",
            "anonymizer_versions": {"rule": "1.0.0"},
        },
    )


def test_field_mapping_selects_only_allowed_meta_keys_and_passes_context():
    doc = _doc(
        meta={
            "doc_type": "note",
            "category": "general",
            "language": "en",
            "anonymizer_versions": {"rule": "1.0.0"},
            "volatile": "ignore_me",  # extra key must be dropped
            "source_path": "tmp/sample.md",  # not in allowed keys -> dropped
        }
    )

    result = markdown_doc_to_step1_inputs(doc, context={"run_id": "r1"})

    assert isinstance(result, Step1Inputs)
    assert result.text == doc.text_md
    assert result.meta == {
        "doc_type": "note",
        "category": "general",
        "language": "en",
        "anonymizer_versions": {"rule": "1.0.0"},
    }
    assert "volatile" not in result.meta
    assert "source_path" not in result.meta
    assert result.context == {"run_id": "r1"}


def test_variant_mapping_original_to_orig():
    result = markdown_doc_to_step1_inputs(_doc(variant="original"))
    assert result.variant == "orig"


def test_variant_mapping_english_to_en():
    result = markdown_doc_to_step1_inputs(_doc(variant="english"))
    assert result.variant == "en"


def test_variant_none_defaults_to_orig():
    result = markdown_doc_to_step1_inputs(_doc(variant=None))
    assert result.variant == "orig"


def test_default_context_is_empty_dict():
    result = markdown_doc_to_step1_inputs(_doc())
    assert result.context == {}
