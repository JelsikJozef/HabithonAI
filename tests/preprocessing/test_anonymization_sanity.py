from __future__ import annotations

from src.preprocessing.app.guardrails import anonymization_sanity


def test_hash_token_with_10_digit_run_is_not_pii() -> None:
    """A pseudonym hash token whose hex body deliberately contains 10 consecutive
    digits must NOT be flagged as a phone number (it is a replacement, not PII)."""
    text = "Customer h:kid0:a1234567890bcdef0011223344556677 paid the invoice."
    passed, info = anonymization_sanity(text)
    assert passed is True
    assert info["patterns"]["phone"] is False


def test_token_token_with_digit_run_is_not_pii() -> None:
    """The base32 ``t:<kid>:<id>`` token form is also masked before scanning."""
    text = "Record t:kid0:mfrgg2di1234567890mzxw6 was archived."
    passed, info = anonymization_sanity(text)
    assert passed is True
    assert info["patterns"]["phone"] is False


def test_multiple_tokens_do_not_false_match() -> None:
    """Several tokens in one document — none may produce a false PII hit."""
    text = (
        "From h:kid0:1112223333abc to h:kid0:def4445556666 regarding "
        "t:kid0:gg7778889990 status update."
    )
    passed, info = anonymization_sanity(text)
    assert passed is True
    assert not any(info["patterns"].values())


def test_real_phone_still_detected() -> None:
    """Regression guard: the phone pattern must stay strict and keep catching a real
    phone number (the fix masks tokens, it does not loosen the regex)."""
    passed, info = anonymization_sanity("Call me at +1 415 555 0123 tomorrow.")
    assert passed is False
    assert info["patterns"]["phone"] is True


def test_real_email_still_detected() -> None:
    passed, info = anonymization_sanity("Reach me at user@example.com please.")
    assert passed is False
    assert info["patterns"]["email"] is True


def test_real_ssn_still_detected() -> None:
    passed, info = anonymization_sanity("SSN 123-45-6789 on file.")
    assert passed is False
    assert info["patterns"]["ssn_us"] is True


def test_real_phone_adjacent_to_token_still_detected() -> None:
    """Masking a token must not swallow a genuine phone number sitting next to it."""
    text = "Owner h:kid0:abcdef0123 phone +1 415 555 0123."
    passed, info = anonymization_sanity(text)
    assert passed is False
    assert info["patterns"]["phone"] is True


def test_placeholders_reported_over_original_text() -> None:
    passed, info = anonymization_sanity("Signed by [PERSON] at [ORG].")
    assert passed is True
    assert info["placeholders_present"] is True
