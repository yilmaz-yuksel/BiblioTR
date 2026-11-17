import pytest

from bibliotr.client import (
    TRDizinClient,
    TRDizinError,
    extract_aggregations,
    extract_records,
    extract_total,
    facet_pairs,
)

from .conftest import FakeResponse


def es_page(ids, total):
    return {"hits": {"total": {"value": total}, "hits": [{"_id": str(i), "_source": {"title": f"T{i}"}} for i in ids]}}


def test_extract_helpers(search_response):
    recs = extract_records(search_response)
    assert [r["id"] for r in recs] == [1001, 1002, 1003]
    assert extract_total(search_response) == 3
    aggs = extract_aggregations(search_response)
    assert aggs["publicationYear"] == [("2021", 2), ("2019", 1)]
    assert aggs["language"] == [("TUR", 2)]


def test_extract_alternative_shapes():
    assert extract_total({"hits": {"total": 7}}) == 7
    assert extract_total({"data": {"totalCount": 5}}) == 5
    assert extract_records({"data": {"items": [{"id": 1}]}}) == [{"id": 1}]
    # _source içinde id yoksa _id kullanılır
    assert extract_records(es_page([9], 1))[0]["id"] == "9"


def test_facet_pairs_supports_repeats_and_prefix():
    pairs = facet_pairs({"database": ["SOCIAL", "SCIENCE"], "facet-publication_year": 2021, "x": ""})
    assert pairs == [
        ("facet-database", "SOCIAL"),
        ("facet-database", "SCIENCE"),
        ("facet-publication_year", "2021"),
    ]


def test_build_params_validates():
    p = TRDizinClient.build_params("cancer", "publicationYear-DESC", 2, 50, [("facet-database", "SCIENCE")])
    assert p[:4] == [("q", "cancer"), ("order", "publicationYear-DESC"), ("page", "2"), ("limit", "50")]
    assert ("facet-database", "SCIENCE") in p
    with pytest.raises(ValueError):
        TRDizinClient.build_params("x", limit=30)


def test_iter_records_paginates_until_total(fake_session):
    s = fake_session([FakeResponse(es_page(range(10), 25)), FakeResponse(es_page(range(10, 20), 25)),
                      FakeResponse(es_page(range(20, 25), 25))])
    c = TRDizinClient(min_interval=0, session=s)
    recs = list(c.iter_records("publication", "q", limit=10))
    assert len(recs) == 25
    assert [dict(call[1])["page"] for call in s.calls] == ["1", "2", "3"]
    assert s.calls[0][0].endswith("/api/defaultSearch/publication/")


def test_iter_records_respects_max(fake_session):
    s = fake_session([FakeResponse(es_page(range(10), 100))])
    c = TRDizinClient(min_interval=0, session=s)
    assert len(list(c.iter_records("publication", "q", limit=10, max_records=5))) == 5
    assert len(s.calls) == 1


def test_harvest_splits_by_year_and_dedupes(fake_session):
    s = fake_session([FakeResponse(es_page([1, 2], 2)), FakeResponse(es_page([2, 3], 2))])
    c = TRDizinClient(min_interval=0, session=s)
    recs = c.harvest("q", facets={"publication_year": 1999, "database": "SOCIAL"}, years=[2020, 2021])
    assert sorted(r["id"] for r in recs) == ["1", "2", "3"]
    years = [[v for k, v in call[1] if k == "facet-publication_year"] for call in s.calls]
    assert years == [["2020"], ["2021"]]  # kullanıcının yıl facet'i yıl bölmesiyle değişir
    assert all(("facet-database", "SOCIAL") in call[1] for call in s.calls)


def test_retries_then_fails(fake_session, monkeypatch):
    monkeypatch.setattr("bibliotr.client.time.sleep", lambda s: None)
    s = fake_session([FakeResponse({}, 503), FakeResponse(es_page([1], 1))])
    c = TRDizinClient(min_interval=0, session=s, max_retries=2)
    assert c.search("publication", "q").total == 1

    s = fake_session([FakeResponse({}, 404)])
    with pytest.raises(TRDizinError):
        TRDizinClient(min_interval=0, session=s).search("publication", "q")


def test_public_lookups(fake_session):
    s = fake_session([FakeResponse({"id": 1})] * 3)
    c = TRDizinClient(min_interval=0, session=s)
    c.by_doi("10.1/x"), c.by_orcid("0000"), c.by_issn("1234-5678")
    assert [call[0].split("/api/public/")[1] for call in s.calls] == ["yayin/doi/", "yazar/orcid/", "dergi/issn/"]
    assert s.calls[0][1] == [("doi", "10.1/x")]
