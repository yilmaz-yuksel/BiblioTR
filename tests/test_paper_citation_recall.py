"""paper/scripts/16_citation_recall.py: aday havuzu, bağlantı sayımı ve biçim denetimi."""

import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace as NS

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("cr16", ROOT / "paper" / "scripts" / "16_citation_recall.py")
cr = importlib.util.module_from_spec(spec)
sys.modules["cr16"] = cr
spec.loader.exec_module(cr)


def _pub(pid, refs=(), cited_by=(), title="", keywords=()):
    return NS(id=pid, references=[NS(target_id=t) for t in refs], cited_by=list(cited_by), title=title,
              title_en=None, title_tr=None, keywords=list(keywords))


def test_pool_and_linked_inside():
    rel = [_pub("1", refs=["2", "90"], cited_by=["91", "3"]), _pub("2", refs=["90"]), _pub("4")]
    allp = {p.id: p for p in rel} | {"3": _pub("3")}       # 3 sette ama ilgisiz
    rows = {r["id"]: r for r in cr.pool(allp, rel)}
    assert set(rows) == {"90", "91"}
    assert rows["90"]["direction"] == "cited" and rows["90"]["cited_by_set"] == 2
    assert rows["91"]["direction"] == "citing"
    assert cr.linked_inside(rel) == 2                      # 1 -> 2; 4 bağlantısız


def test_has_form_matches_whole_words():
    forms = ["fiziksel aktivite", "physical activity"]
    assert cr.has_form(_pub("1", title="Çocuklarda Fiziksel Aktivite düzeyi"), forms)
    assert cr.has_form(_pub("1", keywords=["Physical Activity"]), forms)
    assert not cr.has_form(_pub("1", title="Fiziksel aktivitenin etkisi"), forms)
    assert cr.wilson(0, 0) is None and cr.wilson(5, 10)[0] < 0.5 < cr.wilson(5, 10)[1]


def test_vocabulary_counts_title_and_abstract_only():
    head = ["Başlık", "Anahtar kelimeler", "Özet"]
    rows = [("Yaşlılarda Fiziksel İnaktivite", None, "Egzersiz önerilir"),
            ("Uyku kalitesi", "öğrenciler", "Fiziksel aktivite düzeyi ölçüldü"),
            ("Kalsiyum alımı", None, None)]
    v = cr.vocabulary(rows, head, ["1", "1", "0"])
    assert v["relevant"]["records"] == 2 and v["not_relevant"]["records"] == 1
    assert v["relevant"]["physical inactivity"] == {"title_or_keyword": 1, "abstract_only": 0}
    assert v["relevant"]["physical activity"] == {"title_or_keyword": 0, "abstract_only": 1}
    assert v["relevant"]["exercise"]["abstract_only"] == 1
