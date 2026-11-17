"""TR Dizin kullanım limitleri ve Kasım 2026 doküman güncellemesi (https://development.trdizin.gov.tr/)."""

import pytest

from bibliotr import client as cl
from bibliotr.client import (
    DEFAULT_PER_MINUTE,
    MAX_PER_MINUTE,
    RateLimiter,
    TRDizinClient,
    TRDizinError,
    detail_url,
    resolve_rate,
    validate_request,
)

from .conftest import FakeResponse


class FakeClock:
    def __init__(self):
        self.t = 1000.0
        self.slept = []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def test_resolve_rate_defaults_and_caps():
    assert resolve_rate() == DEFAULT_PER_MINUTE == 50
    assert resolve_rate(min_interval=1.0) == 60
    assert resolve_rate(per_minute=10_000) == MAX_PER_MINUTE == 240
    assert resolve_rate(min_interval=0) == MAX_PER_MINUTE
    assert resolve_rate(per_minute=0) == 1
    assert MAX_PER_MINUTE < cl.RATE_LIMITS["tr"] and DEFAULT_PER_MINUTE < cl.RATE_LIMITS["intl"]


def test_limiter_spacing_and_window():
    clock = FakeClock()
    lim = RateLimiter(clock=clock, sleep=clock.sleep)
    for _ in range(3):
        lim.wait(2)
    # dakikada 2 istek: ikinci istek 30 sn sonra, üçüncü ilkinin 60 sn sonrasında
    assert clock.slept == [30.0, 30.0]
    clock.t += 5
    lim.wait(2)
    assert clock.slept[-1] == 25.0


def test_limiter_never_exceeds_per_minute_in_any_window():
    clock = FakeClock()
    lim = RateLimiter(clock=clock, sleep=clock.sleep)
    times = []
    for _ in range(200):
        lim.wait(50)
        times.append(clock.t)
    for i, t0 in enumerate(times):
        assert sum(1 for t in times[i:] if t - t0 < 60) <= 50


def test_shared_limiter_counts_requests_of_all_clients(fake_session):
    clock = FakeClock()
    lim = RateLimiter(clock=clock, sleep=clock.sleep)
    s = fake_session([FakeResponse({"id": 1})] * 3)
    TRDizinClient(session=s, per_minute=2, limiter=lim).by_doi("10.1/a")
    TRDizinClient(session=s, per_minute=2, limiter=lim).by_doi("10.1/b")
    TRDizinClient(session=s, per_minute=2, limiter=lim).by_doi("10.1/c")
    assert clock.slept == [30.0, 30.0]


class HeaderResponse(FakeResponse):
    def __init__(self, payload, status=200, headers=None):
        super().__init__(payload, status)
        self.headers = headers or {}


def test_429_waits_slows_down_and_retries_once(fake_session, monkeypatch):
    waits = []
    monkeypatch.setattr("bibliotr.client.time.sleep", waits.append)
    s = fake_session([HeaderResponse({}, 429, {"Retry-After": "7"}), FakeResponse({"id": 1})])
    c = TRDizinClient(session=s)
    assert c.by_doi("10.1/a") == {"id": 1}
    assert waits == [7.0] and c.per_minute == DEFAULT_PER_MINUTE // 2

    s = fake_session([HeaderResponse({}, 429), HeaderResponse({}, 429), FakeResponse({"id": 1})])
    c = TRDizinClient(session=s)
    with pytest.raises(TRDizinError, match="429"):
        c.by_doi("10.1/a")
    assert len(s.calls) == 2 and waits[-1] == cl.RATE_WINDOW  # Retry-After yoksa bir pencere


def test_403_is_not_retried(fake_session):
    s = fake_session([FakeResponse({}, 403), FakeResponse({"id": 1})])
    with pytest.raises(TRDizinError, match="engellenmiş"):
        TRDizinClient(session=s).by_doi("10.1/a")
    assert len(s.calls) == 1


def test_validate_request_follows_documentation():
    validate_request("publication", "relevance-DESC", [("facet-publicationLanguage", v) for v in ("KUR", "AZE", "KAZ")])
    validate_request("publication", "title-ASC", [("facet-publicationType", "NOTE"), ("facet-accessType", "CLOSED")])
    validate_request("institution", "title-DESC", [("facet-status", "KISI")])
    validate_request("author", "orderPublicationCount-DESC", [("facet-authorStatus", "REAL")])
    validate_request("publisher", "orderJournalCount-DESC",
                     [("facet-personType", "UNIVERSTY"), ("facet-personType", "STATE_AGENCY")])
    with pytest.raises(ValueError, match="accessType"):
        validate_request("publication", "relevance-DESC", [("facet-accessType", "CLOSE")])
    with pytest.raises(ValueError, match="desteklenmiyor"):
        validate_request("publication", "relevance-DESC", [("facet-authorStatus", "REAL")])
    with pytest.raises(ValueError, match="order"):
        validate_request("journal", "orderCitationCount-DESC", [])
    with pytest.raises(ValueError, match="UNIVERSTY"):
        validate_request("publisher", "relevance-DESC", [("facet-personType", "UNIVERSITY")])


def test_publisher_search_and_detail_url(fake_session):
    s = fake_session([FakeResponse({"hits": {"total": {"value": 0}, "hits": []}})])
    TRDizinClient(session=s).search("publisher", "üniversitesi", "orderJournalCount-DESC", 1, 20,
                                    {"personType": ["SOCIETY", "STATE_AGENCY"]})
    url, params = s.calls[0]
    assert url.endswith("/api/defaultSearch/publisher/")
    assert ("facet-personType", "SOCIETY") in params and ("facet-personType", "STATE_AGENCY") in params
    assert detail_url("publisher", 974) == "https://search.trdizin.gov.tr/tr/yayinci/detay/974"


def test_new_codes_in_exports():
    from bibliotr.export import DOC_TYPES, LANGUAGE_NAMES

    assert DOC_TYPES["NOTE"] == "Note"
    assert {LANGUAGE_NAMES[k] for k in ("KUR", "AZE", "KAZ")} == {"Kurdish", "Azerbaijani", "Kazakh"}
