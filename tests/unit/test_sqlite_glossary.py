import sqlite3
import pytest

from src.preprocessing.adapters.glossary.sqlite_glossary import SqliteGlossary, GlossaryError


def init_db(path):
    g = SqliteGlossary(path)
    # Bootstrap schema using public API (creates table if missing)
    _tmp_id = g.add_term("bootstrap", pattern="tmp", replacement="tmp")
    # Optional: cleanup
    g.remove_term(_tmp_id)
    return g


def test_literal_rules_priority_and_boundaries(tmp_path):
    db = tmp_path / "gloss.sqlite"
    g = init_db(str(db))
    # Add overlapping rules with priorities
    id1 = g.add_term("g1", pattern="ABC", replacement="X", priority=50, word_boundary=True)
    id2 = g.add_term("g1", pattern="AB", replacement="Y", priority=10, word_boundary=True)
    g.load("g1")
    # Priority makes AB win first, then remaining C left intact
    out = g.apply("AB C ABX ABC", src_lang="sk", tgt_lang="en", mode="post", glossary_id="g1")
    assert out.count("Y") >= 1  # AB replaced at least once


def test_case_sensitivity_and_disable_boundaries(tmp_path):
    db = tmp_path / "g.sqlite"
    g = init_db(str(db))
    g.add_term("g1", pattern="word", replacement="W", case_sensitive=False, word_boundary=True)
    g.add_term("g1", pattern="inside", replacement="I", case_sensitive=True, word_boundary=False)
    g.load("g1")
    assert g.apply("A word.", src_lang="cs", tgt_lang="en", mode="post", glossary_id="g1") == "A W."
    # Case-sensitive should not match here
    assert g.apply("Inside", src_lang="cs", tgt_lang="en", mode="post", glossary_id="g1") == "Inside"


def test_regex_disabled_by_default_and_errors_when_enabled(tmp_path):
    db = tmp_path / "rg.sqlite"
    g = SqliteGlossary(str(db), regex_enabled=False)
    # Initialize schema
    _tmp_id = g.add_term("g1", pattern="seed", replacement="seed")
    g.remove_term(_tmp_id)
    g.load("g1")
    g.add_term("g1", pattern="a.c", replacement="ZZ", mode="post")
    # Acts as literal because regex disabled
    assert g.apply("a.c", src_lang="sk", tgt_lang="en", mode="post", glossary_id="g1") == "ZZ"

    g2 = SqliteGlossary(str(db), regex_enabled=True)
    g2.load("g1")
    # Adding is allowed; validation happens at apply time when regex is enabled
    g2.add_term("g1", pattern="a.c", replacement="R")
    with pytest.raises(GlossaryError):
        g2.apply("a.c", src_lang="sk", tgt_lang="en", mode="post", glossary_id="g1")
