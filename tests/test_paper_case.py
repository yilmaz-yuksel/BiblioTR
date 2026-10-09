"""paper/scripts/06_case_analysis.py için testler."""

import copy
import importlib.util
import json
import random
import sys
from pathlib import Path

import pytest

from bibliotr.storage import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("case06", ROOT / "paper" / "scripts" / "06_case_analysis.py")
cs = importlib.util.module_from_spec(spec)
sys.modules["case06"] = cs
spec.loader.exec_module(cs)


def synthetic_db(path, raw_records, n=40, seed=1):
    """Test kayıtlarını çoğaltarak yıl ve yazar dağılımı olan bir veri seti kurar."""
    rng = random.Random(seed)
    recs = []
    for i in range(n):
        r = copy.deepcopy(raw_records[i % len(raw_records)])
        r["id"] = str(5000 + i)
        for key in ("publicationYear", "year"):
            if key in r:
                r[key] = rng.randint(2014, 2025)
        recs.append(r)
    with Store(path) as s:
        s.add_records(s.create_dataset("fa_alan"), recs)
    return path


def test_case_analysis(tmp_path, raw_records):
    db = synthetic_db(tmp_path / "fa_alan_frozen_20260101.db", raw_records)
    out, exp = tmp_path / "case", tmp_path / "exports"
    res = cs.run(db, out, exp, doc_types=None)
    assert res["errors"] == {}, res["errors"]
    assert res["n_publications"] == 40 and res["summary"]["Yayın sayısı"] == 40
    for t in ("t_per_year", "t_top_journals", "t_top_authors", "t_lotka", "t_bradford", "t_citation_spearman",
              "t_network_author", "t_thematic_keywords"):
        assert (out / "tables" / f"{t}.csv").exists(), t
    assert (exp / "fa_alan_wos.txt").read_text(encoding="utf-8").startswith("FN ")
    assert (exp / "fa_alan_keyword_map.txt").exists() or "keyword" not in res["networks"]
    d5 = res["d5_citation_sources"]
    assert [c["source"] for c in d5["coverage"]] == list(cs.SOURCES)[:4] and len(d5["pairs"]) == 6
    assert {"load", "summary", "d5"} <= set(res["timings_s"])
    saved = json.loads((out / "summary.json").read_text(encoding="utf-8"))
    assert saved["dataset"] == "fa_alan"


def test_step_error_is_recorded():
    r = cs.Runner()
    assert r.step("ok", lambda: 1) == 1
    assert r.step("bad", lambda: 1 / 0) is None
    assert "ZeroDivisionError" in r.errors["bad"] and set(r.timings) == {"ok", "bad"}


def test_citation_comparison():
    class P:
        def __init__(self, i, tr, cr, oa, oc, doi="x"):
            self.id, self.citation_count, self.citations_crossref = i, tr, cr
            self.citations_openalex, self.citations_opencitations, self.doi = oa, oc, doi

    pubs = [P("1", 5, 4, 6, None), P("2", 0, 0, 1, None), P("3", 2, 1, 3, None), P("4", 9, None, 12, 1, None)]
    d = cs.citation_comparison(pubs)
    cov = {c["source"]: c for c in d["coverage"]}
    assert cov["OpenCitations"]["records_with_data"] == 1 and cov["TR Dizin"]["total_citations"] == 16
    pair = {(p["source_a"], p["source_b"]): p for p in d["pairs"]}
    assert pair[("TR Dizin", "OpenAlex (TR Dizin)")]["spearman_rho"] == pytest.approx(1.0)
    assert pair[("TR Dizin", "OpenCitations")]["spearman_rho"] is None  # n < 3
    assert d["with_doi"] == 3
    pubs[0].openalex_citations, pubs[1].openalex_citations, pubs[2].openalex_citations = 20, 3, 8
    d2 = cs.citation_comparison(pubs)
    assert d2["coverage"][-1]["source"] == "OpenAlex (direct)" and len(d2["pairs"]) == 10


def test_latest_frozen(tmp_path):
    assert cs.latest_frozen(tmp_path) is None
    for s in ("20260101", "20260301"):
        (tmp_path / f"fa_alan_frozen_{s}.db").touch()
    assert cs.latest_frozen(tmp_path).name == "fa_alan_frozen_20260301.db"


def test_document_type_filter(tmp_path, raw_records):
    recs = []
    for i in range(6):
        r = copy.deepcopy(raw_records[i % len(raw_records)])
        r["id"] = str(7000 + i)
        r["documentType"] = "PROJECT" if i < 2 else "PAPER"
        recs.append(r)
    db = tmp_path / "fa_alan_frozen_x.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("fa_alan"), recs)
    res = cs.run(db, tmp_path / "o", tmp_path / "e")
    assert res["n_records"] == 6 and res["n_publications"] == 4
    assert res["document_types_in_dataset"] == {"PROJECT": 2, "PAPER": 4} and res["document_type_filter"] == ["PAPER"]
    with pytest.raises(SystemExit):
        cs.run(db, tmp_path / "o2", tmp_path / "e2", doc_types=("BOOK",))


def test_check_frozen(tmp_path, monkeypatch):
    import hashlib

    db = tmp_path / "fa_alan_frozen_20260101.db"
    db.write_bytes(b"abc")
    res = tmp_path / "results"
    (res / "freeze_fa_alan_20260101").mkdir(parents=True)
    rec = {"frozen_db": db.name, "sha256": hashlib.sha256(b"abc").hexdigest()}
    (res / "freeze_fa_alan_20260101" / "freeze.json").write_text(json.dumps(rec), encoding="utf-8")
    assert cs.check_frozen(db, res) == {"freeze_record": "freeze_fa_alan_20260101", "sha256_ok": True}
    db.write_bytes(b"abcd")
    assert cs.check_frozen(db, res)["sha256_ok"] is False
    assert cs.check_frozen(tmp_path / "baska.db", res)["freeze_record"] is None


def test_author_merge_effect_and_option(tmp_path, raw_records):
    from bibliotr.normalize import Author, Publication

    def pub(pid, aid):
        p = Publication(id=pid, title=f"T{pid}", year=2020, doi=None, citation_count=0, language="TUR",
                        document_type="PAPER", publication_type="ARTICLE", access_type=None, database=None,
                        journal="Dergi", journal_id="1", issn=None, eissn=None, volume=None, issue=None,
                        start_page=None, end_page=None, abstract=None, url="")
        p.authors = [Author(name="Deniz Kaya", position=0, author_id=aid, institution="Ankara Üniversitesi")]
        return p

    eff = cs.author_merge_effect([pub("1", "10"), pub("2", "10"), pub("3", "11")], top=5)
    assert eff["identities_merged"] == 1 and eff["authors_by_id"] == 2 and eff["authors_after_rule"] == 1
    assert eff["top_overlap"] == 1
    db = synthetic_db(tmp_path / "fa_alan_frozen_20260101.db", raw_records)
    res = cs.run(db, tmp_path / "case", tmp_path / "exp", doc_types=None, author_merge="orcid+institution")
    assert res["errors"] == {} and res["author_merge"] == "orcid+institution"
    assert {"authors_by_id", "lotka_mle_by_id", "top_spearman"} <= set(res["author_merge_effect"])
