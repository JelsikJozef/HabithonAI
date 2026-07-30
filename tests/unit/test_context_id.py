"""Unit tests for the variant-scoped, content-derived Token Vault context_id.

See CLAUDE.md invariant 4: the context_id binds the Token Vault to a *document variant*.
These tests pin the three properties the rest of the refactor relies on:
- determinism (same source -> same id),
- orig vs en never collide (variant tag), and
- the id is filename-safe so FileTokenVault sanitization is lossless.
"""

from __future__ import annotations

from src.anonymization.adapters.token_vault.file_store import FileTokenVault
from src.anonymization.domain.entities import TokenMapping
from src.shared.hashing import derive_context_id


def test_deterministic() -> None:
    text = "# Title\n\nContact alice@example.com.\n"
    assert derive_context_id(text, "en") == derive_context_id(text, "en")


def test_orig_and_en_differ_for_identical_text() -> None:
    # An already-English document can have byte-identical orig/en text; the variant tag
    # must still keep their vault contexts separate.
    text = "Same content in both variants.\n"
    assert derive_context_id(text, "orig") != derive_context_id(text, "en")


def test_variant_label_aliases() -> None:
    text = "hello\n"
    # MarkdownDoc-style labels map onto the short tags.
    assert derive_context_id(text, "original") == derive_context_id(text, "orig")
    assert derive_context_id(text, "english") == derive_context_id(text, "en")


def test_canonicalization_collapses_newline_noise() -> None:
    # normalize_text canonicalizes newlines + trailing whitespace before hashing.
    assert derive_context_id("a\r\nb \n", "en") == derive_context_id("a\nb\n", "en")


def test_filename_safe_and_lossless_in_vault(tmp_path) -> None:
    cid = derive_context_id("payload\n", "en")
    assert cid.startswith("ctx_") and cid.endswith("_en")
    # Only [a-z0-9_]; FileTokenVault sanitization (strip non-alnum/-/_) is a no-op here.
    assert all(c.isalnum() or c == "_" for c in cid)

    vault = FileTokenVault(base_dir=str(tmp_path / "vault"))
    vault.save_mappings(cid, [TokenMapping(token="h:kid0:abc", value="alice", type="PERSON")])
    got = vault.get_mappings(cid)
    assert [m.value for m in got] == ["alice"]


def test_orig_and_en_use_separate_vault_files(tmp_path) -> None:
    text = "Same content.\n"
    vault = FileTokenVault(base_dir=str(tmp_path / "vault"))
    orig_id = derive_context_id(text, "orig")
    en_id = derive_context_id(text, "en")

    vault.save_mappings(orig_id, [TokenMapping(token="t_orig", value="ORIG", type="PERSON")])
    vault.save_mappings(en_id, [TokenMapping(token="t_en", value="EN", type="PERSON")])

    # Mappings do not bleed across variants.
    assert [m.value for m in vault.get_mappings(orig_id)] == ["ORIG"]
    assert [m.value for m in vault.get_mappings(en_id)] == ["EN"]
