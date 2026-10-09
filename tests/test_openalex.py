import copy
import json
from pathlib import Path

import pytest

from bibliotr import openalex as oa
from bibliotr.normalize import normalize_publication

FIX = Path(__file__).parent / "fixtures"


def _pub(pid, doi=None, title=None, title_en=None, year=2020):
    raw = copy.deepcopy(json.loads((FIX / "publication_by_id.json").read_text(encoding="utf-8"))["hits"]["hits"][0]["_source"])
    p = normalize_publication(raw)
    p.id, p.doi, p.title, p.title_en, p.year = pid, doi, title, title_en, year
    return p


def test_helpers():
    assert oa.norm_doi("https://doi.org/10.1/AB") == "10.1/ab" and oa.norm_doi(None) is None
    assert oa.title_key("  Fiziksel Aktivite: ÖĞRENCİLERDE   bir_inceleme! ") == "fiziksel aktivite ogrencilerde bir inceleme"
    assert oa.similarity("Öğrencilerde fiziksel aktivite", "OGRENCILERDE FIZIKSEL AKTIVITE.") == 1.0
    assert oa.similarity(None, "x") == 0.0


class Resp:
    def __init__(self, payload, status=200):
        self.payload, self.status_code, self.headers = payload, status, {}

    def json(self):
        return self.payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise oa.requests.HTTPError(str(self.status_code))


class Session:
    def __init__(self, by_doi, by_title, fail_first=False):
        self.by_doi, self.by_title, self.calls, self.fail_first = by_doi, by_title, [], fail_first

    def get(self, url, params=None, timeout=None):
        self.calls.append(params)
        if self.fail_first and len(self.calls) == 1:
            return Resp({}, 429)
        f = params["filter"]
        if f.startswith("doi:"):
            return Resp({"results": [self.by_doi[d] for d in f[4:].split("|") if d in self.by_doi]})
        q = f.removeprefix("title.search:")
        return Resp({"results": self.by_title.get(q, [])})


def test_match_publications(monkeypatch):
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)
    pubs = [
        _pub("1", doi="10.1/A", title="Fiziksel aktivitenin öğrenciler üzerindeki etkisi"),
        _pub("2", doi="10.1/missing", title="Okullarda fiziksel aktivite araştırma eğilimleri", year=2014),
        _pub("3", title="Kısa", title_en="Physical activity levels in physical education classes", year=2021),
        _pub("4", title="Ortaokul öğrencilerinin fiziksel aktiviteye yönelik görüşleri", year=2017),
        _pub("5", title="Ergenlerde fiziksel aktivite düzeyi ve uyku kalitesi ilişkisi", year=2017),
    ]
    by_doi = {"10.1/a": {"id": "W1", "doi": "https://doi.org/10.1/a", "cited_by_count": 7, "publication_year": 2020}}
    by_title = {
        "Okullarda fiziksel aktivite araştırma eğilimleri": [
            {"id": "W2", "title": "Okullarda fiziksel aktivite: araştırma eğilimleri", "publication_year": 2015,
             "cited_by_count": 40, "doi": None}],
        "Physical activity levels in physical education classes": [
            {"id": "W3", "title": "Physical Activity Levels in Physical Education Classes", "publication_year": 2021,
             "cited_by_count": 2}],
        # yıl farkı 3: reddedilir
        "Ortaokul öğrencilerinin fiziksel aktiviteye yönelik görüşleri": [
            {"id": "W4", "title": "Ortaokul öğrencilerinin fiziksel aktiviteye yönelik görüşleri", "publication_year": 2020}],
        # benzer ama farklı başlık: reddedilir
        "Ergenlerde fiziksel aktivite düzeyi ve uyku kalitesi ilişkisi": [
            {"id": "W5", "title": "Yetişkinlerde fiziksel aktivite ve uyku kalitesi", "publication_year": 2017}],
    }
    sess = Session(by_doi, by_title, fail_first=True)
    client = oa.OpenAlexClient(mailto="a@b.c", min_interval=0, session=sess)
    seen = []
    ms = {m.publication_id: m for m in oa.match_publications(pubs, client, on_progress=lambda *a: seen.append(a))}
    assert ms["1"].openalex_id == "W1" and ms["1"].matched_by == "doi" and ms["1"].cited_by_count == 7
    assert ms["2"].openalex_id == "W2" and ms["2"].matched_by == "title" and ms["2"].title_similarity >= 0.92
    assert ms["3"].openalex_id == "W3"  # İngilizce başlıkla bulundu; Türkçe başlık çok kısa
    assert "4" not in ms and "5" not in ms
    assert all(c["mailto"] == "a@b.c" for c in sess.calls)
    assert seen[0] == ("doi", 2, 2) and seen[-1][0] == "title"
    assert not any("Kısa" in c["filter"] for c in sess.calls)

    no_title = oa.match_publications(pubs, oa.OpenAlexClient(min_interval=0, session=Session(by_doi, by_title)),
                                     by_title=False)
    assert [m.publication_id for m in no_title] == ["1"]


def test_search_title_strips_filter_separators():
    sess = Session({}, {})
    oa.OpenAlexClient(min_interval=0, session=sess).search_title("A: b, c|d")
    assert sess.calls[0]["filter"] == "title.search:A b c d"
    oa.OpenAlexClient(min_interval=0, session=sess).search_title("NAZLI ERKAN'IN TİYATRO ESERLERİ (2020)* \"X\"")
    assert sess.calls[1]["filter"] == "title.search:NAZLI ERKAN IN TİYATRO ESERLERİ 2020 X"


def test_limit_error_after_retries(monkeypatch):
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)

    class Always429:
        n = 0

        def get(self, *a, **k):
            Always429.n += 1
            return Resp({}, 429)

    with pytest.raises(oa.OpenAlexLimitError, match="API anahtar"):
        oa.OpenAlexClient(min_interval=0, max_retries=1, session=Always429()).works_by_doi(["10.1/x"])
    assert Always429.n == 2


def test_limit_error_immediately_when_budget_is_zero(monkeypatch):
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)

    class Budget0:
        n = 0

        def get(self, *a, **k):
            Budget0.n += 1
            r = Resp({}, 429)
            r.headers = {"X-RateLimit-Remaining": "0"}
            return r

    with pytest.raises(oa.OpenAlexLimitError, match="kalan bütçe: 0"):
        oa.OpenAlexClient(min_interval=0, max_retries=3, session=Budget0()).search_title("bir iki üç dört beş altı")
    assert Budget0.n == 1  # bütçe sıfırsa yeniden denenmez


def test_title_search_stops_on_quota_and_keeps_matches(monkeypatch):
    monkeypatch.setattr(oa.time, "sleep", lambda s: None)
    pubs = [_pub("1", doi="10.1/A", title="Fiziksel aktivitenin öğrenciler üzerindeki etkisi")] + [
        _pub(str(i), title=f"Fiziksel aktivite ve motivasyon üzerine bir çalışma {i}") for i in range(10, 14)]

    class S:
        searches = 0

        def get(self, url, params=None, timeout=None):
            assert params["api_key"] == "K"
            f = params["filter"]
            if f.startswith("doi:"):
                return Resp({"results": [{"id": "W1", "doi": "10.1/a", "cited_by_count": 1}]})
            S.searches += 1
            if S.searches > 2:
                r = Resp({}, 429)
                r.headers = {"X-RateLimit-Remaining": "0"}
                return r
            return Resp({"results": [{"id": f"T{S.searches}", "title": f.removeprefix("title.search:"),
                                      "publication_year": 2020}]})

    report = {}
    ms = oa.match_publications(pubs, oa.OpenAlexClient(api_key="K", min_interval=0, session=S()), report=report)
    assert [m.openalex_id for m in ms] == ["W1", "T1", "T2"]
    assert report["title_searched"] == 2 and report["title_not_searched"] == 2 and "kota" in report["stopped"]
    assert report["calls"]["search"] == 3 and report["calls"]["list"] == 1


def test_second_language_title_only_when_first_fails(monkeypatch):
    p = _pub("1", title="Beden eğitiminde fiziksel aktivite uygulamaları", title_en="Physical activity practices in physical education")

    class S:
        queries = []

        def get(self, url, params=None, timeout=None):
            q = params["filter"].removeprefix("title.search:")
            S.queries.append(q)
            return Resp({"results": [{"id": "W", "title": q, "publication_year": 2020}]})

    oa.match_publications([p], oa.OpenAlexClient(min_interval=0, session=S()))
    assert S.queries == ["Beden eğitiminde fiziksel aktivite uygulamaları"]  # ilk başlık eşleşti, ikincisi aranmadı
