"""examples/variant_harvest.py (makaledeki kod örneği) sahte bir istemciyle uçtan uca çalışmalı."""

import re
import runpy
from pathlib import Path

import bibliotr
from bibliotr.normalize import normalize_publication
from bibliotr.query import contains_phrase

ROOT = Path(__file__).resolve().parents[1]


def _rec(rid, title, abstract=""):
    return {"id": rid, "title": title, "publicationYear": 2020, "documentType": "PAPER",
            "journal": {"name": "Dergi"}, "authors": [{"authorId": rid, "inPublicationName": "Deniz Kaya"}],
            "abstracts": [{"language": "TUR", "title": title, "abstract": abstract, "keywords": []}]}


RECORDS = [
    _rec(1, "Yetişkinlerde fiziksel aktivite", "Öğrencilerin fiziksel aktiviteleri de incelendi."),
    _rec(2, "Physical activity in adults"),
    _rec(3, "Çocukların fiziksel aktiviteleri"),     # yalnız ekli biçimle bulunur
    _rec(4, "Kas kuvveti ve denge"),                  # ilgisiz
]


class FakeClient:
    queries: list[str] = []

    def harvest(self, q, *args, **kwargs):
        field, body = re.match(r"\((\w+) : \( (.*) \)\)$", q).groups()
        phrases = re.findall(r'"([^"]+)"', body)
        FakeClient.queries.append(q)
        return [dict(r) for r in RECORDS if contains_phrase(normalize_publication(r), phrases, field)]

    def enrich(self, records, *args, **kwargs):
        return 0


def test_listing_runs_and_finds_suffixed_forms(tmp_path, monkeypatch):
    monkeypatch.setattr(bibliotr, "TRDizinClient", FakeClient)
    monkeypatch.chdir(tmp_path)
    g = runpy.run_path(str(ROOT / "examples" / "variant_harvest.py"))
    assert set(g["records"]) == {"1", "2", "3"}
    assert "fiziksel aktiviteleri" in g["queried"]
    assert (tmp_path / "pa_wos.txt").read_text(encoding="utf-8").count("\nER\n") == 3
