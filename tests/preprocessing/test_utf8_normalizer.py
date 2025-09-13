import types

import pytest

from src.preprocessing.adapters.encoding.utf8_normalizer import (
    NormalizationError,
    NormalizerOptions,
    descriptor,
    normalize_doc,
    normalize_text,
)


def test_eol_mixed_to_lf_and_report():
    text = "A\r\nB\rC\n"
    opts = NormalizerOptions(eol_policy="lf")
    out, report = normalize_text(text, opts)
    assert out == "A\nB\nC\n"  # final newline ensured
    assert report["eol_before"] in {"MIXED", "CRLF", "CR", "LF"}
    assert report["eol_after"] == "LF"


def test_nbsp_space_and_control_chars_removed():
    text = "Hello\u00a0World\x01!\n"
    out, report = normalize_text(text, NormalizerOptions())
    assert "\u00a0" not in out
    assert "Hello World!\n" == out
    assert report["control_chars_removed"] >= 1


def test_protect_code_fences_and_inline_and_tables():
    # Tabs and NBSP inside protected regions should remain unchanged
    text = (
        "Before\tline\n"
        "```py\nprint(\t'code')  \n```\n"
        "| a | b |\n| --- | --- |\ncell1 | cell2  \n"
        "Inline: `code\tspan` and NBSP\u00a0here\n"
    )
    opts = NormalizerOptions()
    out, report = normalize_text(text, opts)
    # Outside tab converted
    assert "Before    line" in out
    # Code fence body preserved (tab and two trailing spaces)
    assert "print(\t'code')  " in out
    # Inline code preserved
    assert "`code\tspan`" in out
    # Table header line preserved
    assert "| --- | --- |" in out
    # Counts sensible
    assert report["protected_regions"]["code_fences"] == 1
    assert report["protected_regions"]["inline_code"] >= 1
    assert report["protected_regions"]["table_lines"] >= 2


def test_trailing_spaces_trim_and_blank_collapse():
    text = "Line 1   \n\n\nLine 2\n"
    opts = NormalizerOptions(trim_trailing_spaces="all", collapse_blank_lines_to=1)
    out, report = normalize_text(text, opts)
    assert "Line 1\n\nLine 2\n" == out
    assert report["trailing_spaces_trimmed"] >= 3
    assert report["blank_lines_collapsed"] >= 1


def test_final_newline_added_policy_crlf():
    text = "No newline"
    opts = NormalizerOptions(eol_policy="crlf", ensure_final_newline=True)
    out, report = normalize_text(text, opts)
    assert out.endswith("\r\n")
    assert report["final_newline_added"] is True


def test_unicode_nfkc_normalization():
    text = "Cafe\u0301\n"  # e + combining acute
    out, report = normalize_text(text, NormalizerOptions(unicode_form="NFKC"))
    # After NFKC, use composed character 'é' and drop the combining sequence
    assert "Cafe\u0301" not in out
    assert "é\n" in out
    assert report["unicode_form_after"] == "NFKC"


def test_idempotency():
    text = "A\tB\u00a0C\r\n\n"
    opts = NormalizerOptions()
    out1, report1 = normalize_text(text, opts)
    out2, report2 = normalize_text(out1, opts)
    assert out1 == out2
    assert report2["control_chars_removed"] == 0
    assert report2["tabs_converted"] == 0
    assert report2["trailing_spaces_trimmed"] == 0
    assert report2["blank_lines_collapsed"] == 0
    assert report2["final_newline_added"] is False


def test_unterminated_code_fence_warning():
    text = "```\nunterminated fence..."
    out, report = normalize_text(text, NormalizerOptions())
    assert "unterminated" in " ".join(report.get("warnings", []))
    # Content after opening fence should be preserved as-is including backticks
    assert out.startswith("```")


def test_size_limit_raises():
    opts = NormalizerOptions(max_text_mb=1)
    big = "x" * (2 * 1024 * 1024)
    with pytest.raises(NormalizationError) as ei:
        normalize_text(big, opts)
    assert "input_too_large" in str(ei.value)


def test_normalize_doc_attaches_report_and_sets_encoding():
    doc = types.SimpleNamespace(text_md="Hi\r\n", meta={})
    opts = NormalizerOptions()
    doc2 = normalize_doc(doc, opts)
    assert doc2 is doc
    assert doc.encoding == "utf-8"
    assert doc.text_md.endswith("\n")
    assert "normalization" in doc.meta
    summary = doc.meta["normalization"].get("summary", "")
    assert "EOL=" in summary


def test_descriptor_stability():
    opts = NormalizerOptions()
    s = descriptor(opts)
    assert s.startswith("utf8_normalizer(")
    assert "unicode=NFKC" in s
    assert "eol=LF" in s
    assert "trim=safe" in s
    assert "tabs=spaces_outside_code" in s
