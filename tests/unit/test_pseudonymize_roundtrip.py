from anonymization.app.pseudonymize import pseudonymize
from anonymization.app.denomize import deanonymize
from anonymization.adapters.detectors.regex_detector import RegexDetector
from anonymization.adapters.token_vault.file_store import FileTokenVault


def test_pseudonymize_then_deanonymize_roundtrip(tmp_path):
    text = "Contact Alice <alice@example.com> or +421 123 456 789."
    detectors = [RegexDetector()]
    vault_dir = tmp_path / "vault"
    vault = FileTokenVault(base_dir=str(vault_dir))

    ctx = "doc-rt-1"
    res = pseudonymize(text, detectors, vault, context_id=ctx, language="en")

    # Expect at least email and phone to be replaced by tokens
    assert "{{PII:" in res.pseudonymized_text
    assert len(res.mappings) >= 2

    # Deanonymize should restore original text
    restored = deanonymize(res.pseudonymized_text, vault, context_id=ctx)
    assert restored.restored_text == text
