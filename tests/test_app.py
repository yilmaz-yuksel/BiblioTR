"""Streamlit arayüzünün duman testi."""

import json
from pathlib import Path

import pytest

pytest.importorskip("streamlit")
from streamlit.testing.v1 import AppTest  # noqa: E402

from bibliotr.client import extract_records  # noqa: E402
from bibliotr.journals import normalize_journal  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

from .conftest import FIXTURES  # noqa: E402

APP = Path(__file__).resolve().parents[1] / "app.py"


@pytest.fixture(autouse=True)
def _fresh_streamlit_cache():
    """`get_store` önbelleğe alınır; her test kendi geçici veritabanını açsın."""
    import streamlit as st

    st.cache_resource.clear()
    st.cache_data.clear()
    yield
    st.cache_resource.clear()
    st.cache_data.clear()


def test_app_with_dataset_and_journal(tmp_path, monkeypatch):
    """Dergi kartı açıkken veri seti analizi çalışmalı (değişken adı çakışması: KeyError 'updated_at')."""
    monkeypatch.chdir(tmp_path)
    with Store(tmp_path / "data" / "trdizin.db") as s:
        recs = extract_records(json.loads((FIXTURES / "search_subject.json").read_text(encoding="utf-8")))
        s.add_records(s.create_dataset("spor"), recs)
    journal = normalize_journal(extract_records(
        json.loads((FIXTURES / "journal_by_id.json").read_text(encoding="utf-8")))[0])

    at = AppTest.from_file(str(APP), default_timeout=60)
    at.session_state["journal"] = journal
    at.session_state["journal_id"] = "488"
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("Hareket ve Antrenman Bilimleri Dergisi" in e.label for e in at.expander)
    assert at.metric[0].value == "2"  # Genel Bakış: veri setindeki yayın sayısı


def test_thesaurus_tab(tmp_path, monkeypatch):
    """Eşanlamlılar sekmesi: öneri tablosundan kabul edilen girdi sözlüğe ve analizlere yansır."""
    from bibliotr import thesaurus as th

    monkeypatch.chdir(tmp_path)
    db = tmp_path / "data" / "trdizin.db"
    with Store(db) as s:
        recs = extract_records(json.loads((FIXTURES / "publication_search.json").read_text(encoding="utf-8")))
        s.add_records(s.create_dataset("d"), recs)
        t = th.Thesaurus()
        t.add("keyword", "Bibliometrics", "Bibliyometri")
        s.save_thesaurus(t)

    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert "(1 girdi)" in at.sidebar.checkbox[0].label
    assert any(b.label == "Seçili girdileri sil" for b in at.button)
    at.radio(key="thes_field").set_value("institution").run()
    assert not at.exception, [e.value for e in at.exception]
    at.radio(key="thes_field").set_value("author").run()
    assert not at.exception, [e.value for e in at.exception]
    at.sidebar.checkbox[0].uncheck().run()
    assert not at.exception, [e.value for e in at.exception]


def test_advanced_search_builder(tmp_path, monkeypatch):
    """Gelişmiş arama: kutulardan sorgu üretilir; iki alan seçilince ayrı sorgular gösterilir."""
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    at.session_state["search_mode"] = "Gelişmiş"
    at.run()
    at.text_input(key="adv_phrase").set_value("beden eğitimi").run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.code[0].value == '(title : ( "beden eğitimi" ))'
    at.session_state["adv_fields"] = ["title", "abstract"]
    at.run()
    assert not at.exception, [e.value for e in at.exception]
    assert at.code[0].value == '(title : ( "beden eğitimi" ))\n(abstract : ( "beden eğitimi" ))'
    at.session_state["adv_join"] = "and"
    at.run()
    assert at.code[0].value == '(title : ( "beden eğitimi" ) | abstract : ( "beden eğitimi" ))'


def test_simple_search_warns_about_quotes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    at = AppTest.from_file(str(APP), default_timeout=60)
    at.run()
    box = next(t for t in at.text_input if t.label.startswith("Arama ifadesi"))
    box.set_value('"beden eğitimi"').run()
    assert not at.exception, [e.value for e in at.exception]
    assert any("çift tırnağı desteklemez" in e.value for e in at.error)
