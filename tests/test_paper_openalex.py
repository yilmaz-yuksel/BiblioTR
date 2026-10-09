"""paper/scripts/07_openalex.py için testler (ağ erişimi olmadan)."""

import copy
import importlib.util
import json
import sys
from pathlib import Path

import pytest

from bibliotr.storage import Store

ROOT = Path(__file__).resolve().parents[1]
FIX = ROOT / "tests" / "fixtures"
spec = importlib.util.spec_from_file_location("oa07", ROOT / "paper" / "scripts" / "07_openalex.py")
oa = importlib.util.module_from_spec(spec)
sys.modules["oa07"] = oa
spec.loader.exec_module(oa)


def test_norm_doi():
    assert oa.norm_doi("https://doi.org/10.5555/YUSBED.1187342") == "10.5555/yusbed.1187342"
    assert oa.norm_doi("doi:10.1/X ") == "10.1/x" and oa.norm_doi("") is None and oa.norm_doi(None) is None


class FakeResp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code = payload, status

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeSession:
    """DOI filtresini çözüp bilinen DOI'ler için OpenAlex yanıtı döndürür; ilk istekte 429 verir."""

    def __init__(self, works):
        self.works, self.calls = works, []

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if len(self.calls) == 1:
            return FakeResp({}, 429)
        asked = params["filter"].removeprefix("doi:").split("|")
        return FakeResp({"results": [self.works[d] for d in asked if d in self.works]})


@pytest.fixture
def db(tmp_path):
    base = json.loads((FIX / "publication_by_id.json").read_text(encoding="utf-8"))["hits"]["hits"][0]["_source"]
    recs = []
    for i, (doi, oac) in enumerate([("10.1/a", 0), ("10.1/b", 3), ("10.1/c", 0), (None, 0), ("10.1/zz", 0)]):
        r = copy.deepcopy(base)
        r["id"] = str(900 + i)
        r["doi"] = doi
        r["otherCitations"] = [{"origin": "CrossRef", "count": 5 + i}, {"origin": "OpenAlex", "count": oac},
                               {"origin": "OpenCitations", "count": 2 + i}]
        recs.append(r)
    path = tmp_path / "fa_alan_frozen_x.db"
    with Store(path) as s:
        s.add_records(s.create_dataset("fa_alan"), recs)
    return path


def test_run(db, tmp_path, monkeypatch):
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)
    y = json.loads((FIX / "publication_by_id.json").read_text(encoding="utf-8"))["hits"]["hits"][0]["_source"].get("publicationYear")
    works = {"10.1/a": {"id": "W1", "doi": "https://doi.org/10.1/A", "cited_by_count": 7, "publication_year": 2000},
             "10.1/b": {"id": "W2", "doi": "https://doi.org/10.1/b", "cited_by_count": 3, "publication_year": y},
             "10.1/c": {"id": "W3", "doi": "https://doi.org/10.1/c", "cited_by_count": 9, "publication_year": y}}
    sess = FakeSession(works)
    res = oa.run(db, tmp_path / "out", session=sess, mailto="x@y.z", delay=0)
    assert sess.calls[-1]["mailto"] == "x@y.z" and len(sess.calls) == 2  # 429 sonrası yeniden deneme
    assert res["with_doi"] == 4 and res["without_doi"] == 1 and res["found_in_openalex"] == 3
    rv = res["reported_vs_direct"]
    assert (rv["reported_lower"], rv["equal"], rv["reported_higher"]) == (2, 1, 0)
    assert rv["reported_zero_direct_positive"] == 2
    assert res["cited_records"]["openalex_direct"] == 3 and res["cited_records"]["trdizin_reported_openalex"] == 1
    assert res["year_mismatch"]["mismatched"] == 1
    assert (tmp_path / "out" / "openalex_matches.csv").read_text(encoding="utf-8-sig").startswith("id,doi,found")


def test_batching(monkeypatch):
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)

    class S:
        n = 0

        def get(self, url, params=None, timeout=None):
            S.n += 1
            assert len(params["filter"].removeprefix("doi:").split("|")) <= oa.BATCH
            return FakeResp({"results": []})

    oa.fetch_openalex([f"10.1/{i}" for i in range(120)], S(), delay=0)
    assert S.n == 3
