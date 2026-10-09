import json

import pytest

from bibliotr import cli
from bibliotr.client import TRDizinClient, extract_records
from bibliotr.journals import normalize_journal, years_frame
from bibliotr.storage import Store

from .conftest import FIXTURES, FakeResponse


@pytest.fixture
def journal_response():
    return json.loads((FIXTURES / "journal_by_id.json").read_text(encoding="utf-8"))


@pytest.fixture
def journal(journal_response):
    return normalize_journal(extract_records(journal_response)[0])


def test_normalize_journal(journal):
    j = journal
    assert (j.id, j.title, j.other_title) == ("488", "Hareket ve Antrenman Bilimleri Dergisi", "Anatolian Journal of Movement and Training Sciences")
    assert (j.issn, j.eissn, j.first_publish_year) == (None, "2667-4009", 1991)
    assert j.indexed_years == [2003, 2004, 2009, 2019, 2022, 2024]  # sıralı
    assert j.rejected_years == [2002, 2005, 2006, 2007, 2008]
    assert j.editors == ["Prof. Dr. Nilgün Aksoy"] and j.citation_count == 989 and j.is_active is True
    assert j.languages == ["TUR", "ENG"] and j.databases == ["SCIENCE", "SOCIAL"]
    assert j.subjects[0] == "Fen > Tıp > Biyokimya ve Moleküler Biyoloji" and "Sosyal > Sosyal > Tarih" in j.subjects
    y2024 = j.years[-1]
    assert (y2024.periods, y2024.missing_issue_count, y2024.missing_referee_report_count) == ([3, 6, 9, 12], 3, 1)
    assert j.search_names == ["Hareket ve Antrenman Bilimleri Dergisi", "Anatolian Journal of Movement and Training Sciences"]


def test_summary_and_years_frame(journal):
    s = journal.summary()
    assert s["TR Dizin'de dizinlendiği yıllar"] == "2003-2004, 2009, 2019, 2022, 2024"
    assert s["TR Dizin sayfası"].endswith("/tr/dergi/detay/488")
    df = years_frame(journal)
    assert df["Yıl"].tolist() == [2003, 2004, 2009, 2019, 2022, 2024]
    assert df.loc[df["Yıl"] == 2022, "Uzman uyarısı"].item() == 1


def _page(records, total):
    return {"hits": {"total": {"value": total}, "hits": [{"_id": str(r["id"]), "_source": r} for r in records]}}


def test_harvest_journal_filters_by_id_and_uses_indexed_years(journal_response, fake_session):
    own = {"id": 1, "journal": {"id": "488", "name": "Hareket ve Antrenman Bilimleri Dergisi"}, "publicationYear": 2003}
    other = {"id": 2, "journal": {"id": "999", "name": "Hareket ve Antrenman Bilimleri Dergisi"}, "publicationYear": 2003}
    pages = [FakeResponse(journal_response)] + [FakeResponse(_page([own, other], 2))] + \
            [FakeResponse(_page([], 0))] * 5
    s = fake_session(pages)
    c = TRDizinClient(min_interval=0, session=s)
    recs = c.harvest_journal("488")
    assert [r["id"] for r in recs] == [1]  # aynı adlı başka dergi (999) elendi
    assert s.calls[0][0].endswith("/api/journalById/488")
    search_calls = s.calls[1:]
    assert [dict(c[1])["facet-publication_year"] for c in search_calls] == ["2003", "2004", "2009", "2019",
                                                                             "2022", "2024"]
    names = [v for k, v in search_calls[0][1] if k == "facet-journalName"]
    assert names == ["Hareket ve Antrenman Bilimleri Dergisi", "Anatolian Journal of Movement and Training Sciences"]


def test_cli_journal_and_harvest(journal_response, tmp_path, monkeypatch, capsys):
    own = {"id": 7, "journal": {"id": "488", "name": "Hareket ve Antrenman Bilimleri Dergisi"}, "publicationYear": 2024,
           "references": [], "orderCitationCount": 0}
    responses = iter([journal_response, journal_response] + [_page([own], 1)] + [_page([], 0)] * 10)

    def fake_get(self, url, params=None, timeout=None):
        return FakeResponse(next(responses))

    monkeypatch.setattr("requests.Session.get", fake_get)
    cli.main(["--delay", "0", "journal", "488"])
    out = capsys.readouterr().out
    assert "Anatolian Journal of Movement and Training Sciences" in out and "2003-2004, 2009" in out and "Eksik sayı" in out

    db = tmp_path / "t.db"
    cli.main(["--delay", "0", "--db", str(db), "harvest", "--journal", "488", "--dataset", "sbd"])
    with Store(db) as st:
        d = st.list_datasets()[0]
        assert d["n"] == 1 and d["query"] == "Hareket ve Antrenman Bilimleri Dergisi"
        assert d["params"]["journal"] == {"id": "488", "title": "Hareket ve Antrenman Bilimleri Dergisi"}
