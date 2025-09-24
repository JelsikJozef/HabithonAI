# filepath: /Users/jozefjelsik/PycharmProjects/HabithonAI/tests/unit/test_anonymized_storage.py
from pathlib import Path
from src.preprocessing.adapters.storage.anonymized_storage import AnonymizedFileStorage


ess = AnonymizedFileStorage


def test_storage_compute_target_path(tmp_path):
    out = tmp_path / "out"
    en = out / "en" / "sub"
    en.mkdir(parents=True, exist_ok=True)
    src = en / "file.md"
    src.write_text("x", encoding="utf-8")

    st = AnonymizedFileStorage(out)
    dst = st.compute_target_path(src)

    assert dst == (out / "hashed_documents" / "sub" / "file_anon.md")


def test_storage_write_and_read(tmp_path):
    out = tmp_path / "out"
    en = out / "en"
    en.mkdir(parents=True, exist_ok=True)
    src = en / "doc.md"
    src.write_text("hello", encoding="utf-8")

    st = AnonymizedFileStorage(out)
    dst = st.write(src, "content\r\nwith\rnewlines")

    assert dst.exists()
    assert dst.read_text(encoding="utf-8") == "content\nwith\nnewlines"
