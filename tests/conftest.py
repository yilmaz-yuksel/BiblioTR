import json
from pathlib import Path

import pytest

from bibliotr.client import extract_records

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def _no_rate_wait():
    """Testlerde TR Dizin hız sınırlayıcısı gerçekten beklemesin; her test boş pencereyle başlasın."""
    from bibliotr.client import SHARED_LIMITER

    SHARED_LIMITER.reset()
    old = SHARED_LIMITER.sleep
    SHARED_LIMITER.sleep = lambda s: None
    yield
    SHARED_LIMITER.sleep = old
    SHARED_LIMITER.reset()


@pytest.fixture
def search_response():
    return json.loads((FIXTURES / "publication_search.json").read_text(encoding="utf-8"))


@pytest.fixture
def raw_records(search_response):
    return extract_records(search_response)


@pytest.fixture
def pubs(raw_records):
    from bibliotr.normalize import normalize_publication

    return [normalize_publication(r) for r in raw_records]


class FakeResponse:
    def __init__(self, payload, status=200, url="http://test"):
        self._payload = payload
        self.status_code = status
        self.url = url
        self.text = json.dumps(payload)

    def json(self):
        return self._payload


class FakeSession:
    """requests.Session yerine geçer; çağrıları kaydeder, sırayla yanıt döner."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, list(params.items()) if isinstance(params, dict) else list(params or [])))
        r = self.responses.pop(0)
        return r(url, params) if callable(r) else r


@pytest.fixture
def fake_session():
    return FakeSession


@pytest.fixture
def detail_response():
    return json.loads((FIXTURES / "publication_by_id.json").read_text(encoding="utf-8"))


@pytest.fixture
def real_pub(detail_response):
    from bibliotr.normalize import normalize_publication

    return normalize_publication(extract_records(detail_response)[0])
