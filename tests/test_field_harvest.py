"""Gelişmiş aramada ifade listesi, varyant keşfi ve 02b_harvest_field.py yinelemeli çekimi."""

import importlib.util
import re
from pathlib import Path

from bibliotr import query as tq
from bibliotr.normalize import normalize_publication

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("fh02b", ROOT / "paper" / "scripts" / "02b_harvest_field.py")
fh = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fh)


def _raw(pid, title_tr=None, title_en=None, abstract_tr="", keywords_tr=(), year=2022, lang="TUR"):
    abstracts = []
    if title_tr is not None:
        abstracts.append({"language": "TUR", "title": title_tr, "abstract": abstract_tr, "keywords": list(keywords_tr)})
    if title_en is not None:
        abstracts.append({"language": "ENG", "title": title_en, "abstract": "", "keywords": []})
    return {"id": pid, "publicationYear": year, "language": lang, "abstracts": abstracts}


def test_phrase_query_and_chunks():
    assert tq.phrase_query("title", ["fiziksel aktivite"]) == '(title : ( "fiziksel aktivite" ))'
    assert tq.phrase_query("title", ["a b", '"c"', "a  b"]) == '(title : ( ("a b" OR "c") ))'
    assert tq.phrase_query("keyword", ["spor"], ["futbol"]) == '(keyword : ( "spor" AND -("futbol") ))'
    assert tq.phrase_query("title", []) == ""
    qs = tq.phrase_queries("title", [f"x{i}" for i in range(45)], max_terms=20)
    assert len(qs) == 3 and qs[0].count(" OR ") == 19 and qs[2].count('"') == 10


def test_contains_phrase_is_whole_word():
    p = normalize_publication(_raw("1", "Gençlerde fiziksel aktivitenin önemi", "Physical activity in youth"))
    assert tq.contains_phrase(p, ["fiziksel aktivitenin"], "title")
    assert not tq.contains_phrase(p, ["fiziksel aktivite"], "title")  # ek tam sözcükte eşleşmez
    assert tq.contains_phrase(p, ["PHYSICAL ACTIVITY"], "title")  # büyük/küçük harf duyarsız
    assert not tq.contains_phrase(p, ["physical activity"], "keyword")


def test_discover_variants_suffixes_plural_and_spellings():
    pubs = [normalize_publication(r) for r in (
        _raw("1", "Fiziksel Aktivite ve Uyku", "Physical activities and sleep",
             "Fiziksel aktivitenin uyku üzerindeki etkisi. FİZİKSEL AKTİVİTEYE katılım."),
        _raw("2", "Fiziksel aktivitenin belirleyicileri", None, "fiziksel aktivitenin rolü",
             ["Fiziksel aktiviteler"]),
    )]
    v = tq.discover_phrase_variants(pubs, ["fiziksel aktivite", "physical activit"])
    forms = {d["form"]: d for d in v.values()}
    assert forms["fiziksel aktivitenin"]["records"] == 2
    assert forms["fiziksel aktivitenin"]["fields"] == {"abstract": 2, "title": 1}
    assert "fiziksel aktiviteye" in forms  # Türkçe küçük harf: "AKTİVİTEYE" -> "aktiviteye"
    assert forms["fiziksel aktiviteler"]["fields"] == {"keyword": 1}
    assert "physical activities" in forms
    assert forms["fiziksel aktivite"]["records"] == 1


class FakeEngine:
    """TR Dizin gelişmiş aramasının sözcüksel davranışı: alan içinde tam ifade, VEYA."""

    def __init__(self, raws):
        self.raws = raws
        self.calls = []

    def __call__(self, q):
        self.calls.append(q)
        field = re.match(r"\((\w+) :", q).group(1)
        phrases = re.findall(r'"([^"]+)"', q)
        out = []
        for r in self.raws:
            if tq.contains_phrase(normalize_publication(r), phrases, field):
                out.append(r)
        return out


def _corpus():
    return [
        _raw("A", "Fiziksel aktivite düzeyleri", None, "Öğrencilerde fiziksel aktivitenin azaldığı görüldü."),
        _raw("B", "Ergenlerde fiziksel aktivitenin belirleyicileri", None, "Okulda fiziksel aktiviteye katılım."),
        _raw("C", "Fiziksel aktiviteye katılım engelleri", None, "Engeller incelendi."),
        _raw("D", "Yaşlılarda denge", None, "Denge eğitimi.", ["Fiziksel aktivite"]),
        _raw("E", "Fiziksel aktivite ve obezite", None, "", year=2026),  # yıl dışı
        _raw("F", "Futbolda sakatlık", None, "Fiziksel aktivite önerileri."),  # yalnız özette
        _raw("G", "Kanatlı hayvan refahı", None, "", ["kümes"]),
    ]


def test_iterative_harvest_saturates_and_reports_recall():
    eng = FakeEngine(_corpus())
    res = fh.harvest(eng, ["fiziksel aktivite"], ["fiziksel aktivite"], ("title", "keyword"), range(1990, 2026))
    assert set(res["records"]) == {"A", "B", "C", "D"}
    assert res["saturated"] and res["rounds"] == 2
    assert set(res["by_field"]["title"]) == {"A", "B", "C"} and set(res["by_field"]["keyword"]) == {"D"}
    strat = {r["strategy"]: r for r in res["strategy"]}
    assert strat["seeds only, all fields"]["records"] == 2  # A (başlık) + D (anahtar kelime)
    assert strat["seeds + discovered, all fields"]["relative_recall_pct"] == 100.0
    assert strat["seeds only, all fields"]["relative_recall_pct"] == 50.0
    found = {v["form"] for v in res["variants"]}
    assert {"fiziksel aktivitenin", "fiziksel aktiviteye"} <= found
    assert all(c["without_phrase"] == 0 for c in res["lexical_check"].values())
    hits = {h["form"]: h for h in res["phrase_hits"]}
    assert hits["fiziksel aktivite"]["seed"] and hits["fiziksel aktivite"]["records_any_field"] == 2
    assert hits["fiziksel aktiviteye"]["records_title"] == 1
    # Her turda yalnızca yeni biçimler sorgulanır.
    assert len(eng.calls) == len(res["queries"]) == 2 * (res["rounds"] + 1)


def test_min_records_and_exclude():
    eng = FakeEngine(_corpus())
    res = fh.harvest(eng, ["fiziksel aktivite"], None, ("title",), None, exclude=["obezite"], min_records=2)
    assert all('-("obezite")' in q["query"] for q in res["queries"])
    assert {v["form"] for v in res["variants"]} == set()  # her ekli biçim tek kayıtta


def test_outputs_and_store(tmp_path):
    from bibliotr.storage import Store

    eng = FakeEngine(_corpus())
    res = fh.harvest(eng, ["fiziksel aktivite"], None, ("title", "keyword"), range(1990, 2026))
    fh.write_outputs(res, tmp_path / "out", {"seeds": ["fiziksel aktivite"]})
    for name in ("variants.csv", "rounds.csv", "strategy.csv", "phrase_hits.csv", "harvest_log.json"):
        assert (tmp_path / "out" / name).exists()
    db = tmp_path / "t.db"
    fh.store_results(db, "fa", res, {"seeds": ["fiziksel aktivite"]})
    fh.store_results(db, "fa", res, {"seeds": ["fiziksel aktivite"]})  # yeniden çalıştırma üzerine yazar
    with Store(db, readonly=True) as s:
        names = {d["name"]: d["n"] for d in s.list_datasets()}
    assert names == {"fa": 4, "fa__title": 3, "fa__keyword": 1}


def test_dry_run_prints_plan(capsys):
    fh.main(["--dataset", "x", "--seeds", "fiziksel aktivite", "physical activity", "--fields", "title"])
    out = capsys.readouterr().out
    assert "--run" in out


def test_probe_queries_cover_or_syntaxes():
    qs = dict(fh.probe_queries(["fiziksel aktivite", "physical activity"], ["title", "keyword"]))
    assert qs["tek ifade"] == '(title : ( "fiziksel aktivite" ))'
    assert qs["iki ifade VEYA, iç parantezsiz"] == '(title : ( "fiziksel aktivite" OR "physical activity" ))'
    assert qs["tek ifade, keyword"] == '(keyword : ( "fiziksel aktivite" ))'


def test_default_is_one_phrase_per_query(capsys):
    fh.main(["--dataset", "x", "--seeds", "a b", "c d", "--fields", "title"])
    out = capsys.readouterr().out
    assert '(title : ( "a b" ))' in out and '(title : ( "c d" ))' in out and " OR " not in out


def test_run_refuses_to_overwrite_existing_dataset(tmp_path):
    import pytest
    from bibliotr.storage import Store

    db = tmp_path / "t.db"
    with Store(db) as s:
        s.create_dataset("fiziksel aktivite")
    with pytest.raises(SystemExit, match="zaten var"):
        fh.main(["--dataset", "fiziksel aktivite", "--seeds", "fiziksel aktivite", "--db", str(db), "--run"])
