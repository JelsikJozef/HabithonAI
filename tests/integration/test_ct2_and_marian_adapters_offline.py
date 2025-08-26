import pytest

from src.preprocessing.domain.models_markdown import MarkdownDoc
from src.preprocessing.adapters.translate.ct2_nllb import NllbCTranslate2
from src.preprocessing.adapters.translate.marian_opus import MarianOpus


@pytest.mark.integration
def test_ct2_nllb_no_segments_avoids_loading(tmp_path):
    # Create a dummy model dir that exists but has no tokenizer/model (we won't load)
    model_dir = tmp_path / "ct2_model"
    model_dir.mkdir()
    md = """
```py
print('no segments detectable')
```
"""
    doc = MarkdownDoc(doc_id="d1", path=str(tmp_path/"src.md"), variant="original", lang="sk", text_md=md)

    adapter = NllbCTranslate2(str(model_dir))
    out = adapter.translate_md(doc, src_lang="sk", tgt_lang="en", options={})
    assert out.variant == "english" and out.lang == "en"
    assert out.text_md == md
    meta = out.meta or {}
    assert meta.get("translator", {}).get("engine") == "ct2-nllb"
    assert meta.get("segments", {}).get("total") == 0


@pytest.mark.integration
def test_marian_no_segments_avoids_loading(tmp_path):
    models = {"sk": "Helsinki-NLP/opus-mt-sk-en"}
    md = """
```
code only
```
"""
    doc = MarkdownDoc(doc_id="d2", path=str(tmp_path/"s.md"), variant="original", lang="sk", text_md=md)
    adapter = MarianOpus(models=models, local_files_only=True)
    out = adapter.translate_md(doc, src_lang="sk", tgt_lang="en", options={})
    assert out.variant == "english" and out.lang == "en"
    assert out.text_md == md
    meta = out.meta or {}
    assert meta.get("translator", {}).get("engine") == "marian-opus"
    assert meta.get("segments", {}).get("total") == 0

