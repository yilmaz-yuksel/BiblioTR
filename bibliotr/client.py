"""TR Dizin arama API'si için HTTP istemcisi.

Doküman: https://development.trdizin.gov.tr/

Arama uç noktası:
    https://search.trdizin.gov.tr/api/defaultSearch/{entity}/?q=...&order=...&page=...&limit=...

`order`, `page` ve `limit` her istekte zorunludur. Facet parametreleri aynı
anahtarla birden fazla kez gönderilerek çoklu seçim yapılabilir.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Iterator, Mapping, Sequence

import requests

log = logging.getLogger(__name__)

BASE_URL = "https://search.trdizin.gov.tr"
SEARCH_PATH = "/api/defaultSearch/{entity}/"
PUBLIC_PATH = "/api/public/{entity}/{query}"
# Yayının tüm ayrıntıları (kaynakça, atıf yapanlar, diğer atıf kaynakları)
PUBLICATION_BY_ID_PATH = "/api/publicationById/{id}"
# Dergi kimlik bilgileri (dizinlendiği yıllar, konular, editör...)
JOURNAL_BY_ID_PATH = "/api/journalById/{id}"

ENTITIES = ("publication", "journal", "author", "institution")
ALLOWED_LIMITS = (10, 20, 50, 100)
MAX_LIMIT = 100

# Veri tipine göre geçerli sıralama değerleri (dokümandan).
ORDERS: dict[str, tuple[str, ...]] = {
    "publication": (
        "relevance-DESC",
        "publicationYear-DESC",
        "publicationYear-ASC",
        "orderCitationCount-DESC",
        "orderCitationCount-ASC",
        "title-ASC",
        "title-DESC",
    ),
    "journal": ("relevance-DESC", "title-ASC", "title-DESC"),
    "author": (
        "relevance-DESC",
        "title-ASC",
        "title-DESC",
        "orderCitationCount-DESC",
        "orderCitationCount-ASC",
        "orderPublicationCount-DESC",
        "orderPublicationCount-ASC",
    ),
    "institution": ("relevance-DESC", "title-ASC", "title-DESC"),
}

# Veri tipine göre desteklenen facet parametreleri ve (varsa) sabit değerleri.
FACETS: dict[str, dict[str, tuple[str, ...] | None]] = {
    "publication": {
        "facet-documentType": ("PAPER", "PROJECT"),
        "facet-database": ("SOCIAL", "SCIENCE"),
        # Doküman "CLOSE" der, ancak API "CLOSED" döndürür.
        "facet-accessType": ("OPEN", "CLOSED"),
        "facet-authorName": None,
        "facet-publication_year": None,
        "facet-publicationLanguage": ("ENG", "TUR", "GER", "FRE", "ARA", "OTH", "RUS", "ITA", "SPA"),
        "facet-journalName": None,
        "facet-publicationType": (
            "RESEARCH", "COMPILATION", "FACT_PRESENTATION", "OTHER", "BOOK_PRESENTATION",
            "CORRECTION", "EDITORIAL", "LETTER", "LETTER_TO_EDITOR", "MEETING_SUMMARY",
            "REPORT", "SHORT_REPORT", "TRANSLATION", "RETRACTED", "NOTE",
        ),
        "facet-projectGroup": None,
        "facet-facetAuthorInstitution": None,
        "facet-facetAuthorCountry": None,
        "facet-facetAuthorCity": None,
        "facet-subject": None,
    },
    "journal": {
        "facet-year": None,
        "facet-publicationLanguage": ("ENG", "TUR", "GER", "FRE", "ARA", "OTH", "RUS", "ITA", "SPA"),
        "facet-subject": None,
    },
    "author": {
        "facet-authorStatus": ("REAL", "POOL"),
    },
    "institution": {
        "facet-type": ("DEVLETUNIVERSITE", "VAKIFUNIVERSITE", "KAMUKURULUS", "DIGER", "KURUMKURULUS", "ASKERI", "VOD"),
        "facet-status": ("OZEL", "KAMU", "DIGER", "YABANCI", "STK"),
        "facet-country": None,
        "facet-city": None,
    },
}

# Yıl facet'inin veri tipine göre adı (yıl yıl bölerek çekmek için).
YEAR_FACET = {"publication": "facet-publication_year", "journal": "facet-year"}

# Elasticsearch'in varsayılan derin sayfalama sınırı (page * limit bunu aşamaz).
MAX_RESULT_WINDOW = 10_000

# Agregasyon (facet dağılımı) adlarının okunur karşılıkları
AGGREGATION_LABELS = {
    "facet-publication_year": "Yayın yılı",
    "facet-journalName": "Dergi",
    "facet-facetAuthorInstitution": "Yazar kurumu",
    "facet-facetAuthorCity": "Yazar şehri",
    "facet-facetAuthorCountry": "Yazar ülkesi",
    "facet-authorName": "Yazar",
    "facet-subject": "Konu",
    "facet-publicationLanguage": "Dil",
    "facet-publicationType": "Yayın türü",
    "facet-documentType": "Doküman tipi",
    "facet-database": "Veritabanı",
    "facet-accessType": "Erişim",
    "facet-projectGroup": "Proje grubu",
}

FacetInput = Mapping[str, str | int | Sequence[str | int]] | Sequence[tuple[str, str | int]] | None


class TRDizinError(RuntimeError):
    """API isteği başarısız olduğunda fırlatılır."""


@dataclass
class SearchPage:
    """Tek bir arama sayfasının ayrıştırılmış hali."""

    entity: str
    page: int
    limit: int
    total: int
    records: list[dict[str, Any]]
    aggregations: dict[str, list[tuple[str, int]]]
    raw: dict[str, Any] = field(repr=False)
    # Agregasyonda listelenmeyen (kesilen) belge sayısı; >0 ise liste eksiktir.
    aggregation_other: dict[str, int] = field(default_factory=dict)


def facet_pairs(facets: FacetInput) -> list[tuple[str, str]]:
    """Facet girdisini (dict veya tuple listesi) tekrar eden (anahtar, değer) listesine çevirir."""
    if not facets:
        return []
    items = facets.items() if isinstance(facets, Mapping) else facets
    pairs: list[tuple[str, str]] = []
    for key, value in items:
        if not key.startswith("facet-"):
            key = f"facet-{key}"
        values = value if isinstance(value, (list, tuple, set)) else [value]
        for v in values:
            if v is None or str(v).strip() == "":
                continue
            pairs.append((key, str(v).strip()))
    return pairs


def extract_records(data: Any) -> list[dict[str, Any]]:
    """Yanıttan kayıt listesini çıkarır.

    TR Dizin Elasticsearch biçiminde (`hits.hits[]._source`) döner. Her kayda
    `_id` alanı `id` yoksa eklenir. Farklı sarmalayıcılar için de yedek yollar denenir.
    """
    if not isinstance(data, dict):
        return data if isinstance(data, list) else []
    hits = data.get("hits")
    if isinstance(hits, dict) and isinstance(hits.get("hits"), list):
        out = []
        for h in hits["hits"]:
            if not isinstance(h, dict):
                continue
            src = h.get("_source")
            if isinstance(src, dict):
                rec = dict(src)
                if "id" not in rec and h.get("_id") is not None:
                    rec["id"] = h["_id"]
                out.append(rec)
            else:
                out.append(h)
        return out
    for path in (("data", "items"), ("items",), ("data",), ("content",), ("results",)):
        node: Any = data
        for key in path:
            node = node.get(key) if isinstance(node, dict) else None
        if isinstance(node, list):
            return [r for r in node if isinstance(r, dict)]
    return []


def extract_total(data: Any) -> int:
    """Yanıttaki toplam kayıt sayısını bulur."""
    if not isinstance(data, dict):
        return 0
    candidates = [
        (data.get("hits") or {}).get("total") if isinstance(data.get("hits"), dict) else None,
        (data.get("data") or {}).get("totalCount") if isinstance(data.get("data"), dict) else None,
        data.get("totalCount"),
        data.get("total"),
        data.get("totalElements"),
    ]
    for c in candidates:
        if isinstance(c, dict):
            c = c.get("value")
        try:
            if c is not None:
                return int(c)
        except (TypeError, ValueError):
            continue
    return 0


def _find_bucket_node(node: Any) -> dict | None:
    if not isinstance(node, dict):
        return None
    if isinstance(node.get("buckets"), list):
        return node
    for v in node.values():
        found = _find_bucket_node(v)
        if found is not None:
            return found
    return None


def extract_aggregation_other(data: Any) -> dict[str, int]:
    """Her agregasyon için listede yer almayan belge sayısı (`sum_other_doc_count`)."""
    aggs = data.get("aggregations") if isinstance(data, dict) else None
    out: dict[str, int] = {}
    for name, node in (aggs or {}).items():
        b = _find_bucket_node(node)
        if b is not None:
            out[name] = int(b.get("sum_other_doc_count") or 0)
    return out


def extract_aggregations(data: Any) -> dict[str, list[tuple[str, int]]]:
    """Elasticsearch `aggregations` alanını {ad: [(değer, sayı), ...]} biçimine çevirir."""
    aggs = data.get("aggregations") if isinstance(data, dict) else None
    if not isinstance(aggs, dict):
        return {}
    out: dict[str, list[tuple[str, int]]] = {}

    for name, node in aggs.items():
        # İç içe (ör. {"values": {"buckets": ...}}) agregasyonlarda kova düğümünü bul.
        found = _find_bucket_node(node)
        if found is None:
            continue
        rows = []
        for b in found["buckets"]:
            if not isinstance(b, dict):
                continue
            key = str(b.get("key_as_string", b.get("key")))
            if not key.strip():
                key = "(boş)"
            rows.append((key, int(b.get("doc_count", 0))))
        out[name] = rows
    return out


class TRDizinClient:
    """TR Dizin API istemcisi.

    Sunucuyu yormamak için istekler arasında en az `min_interval` saniye beklenir,
    429/5xx yanıtlarında üstel geri çekilme ile yeniden denenir.
    """

    def __init__(
        self,
        base_url: str = BASE_URL,
        min_interval: float = 1.0,
        timeout: float = 30.0,
        max_retries: int = 4,
        session: requests.Session | None = None,
        user_agent: str = "BiblioTR/1.0 (akademik bibliyometrik arastirma)",
    ):
        self.base_url = base_url.rstrip("/")
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self.session.headers.setdefault("User-Agent", user_agent)
        self.session.headers.setdefault("Accept", "application/json")
        self._last_request = 0.0

    # ---- düşük seviye -------------------------------------------------

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last_request)
        if wait > 0:
            time.sleep(wait)

    def get_json(self, url: str, params: Sequence[tuple[str, str]] | Mapping[str, str] | None = None) -> Any:
        """Verilen URL'ye GET isteği atar ve JSON döndürür."""
        if not url.startswith("http"):
            url = self.base_url + url
        last_err: Exception | None = None
        for attempt in range(self.max_retries + 1):
            self._throttle()
            try:
                resp = self.session.get(url, params=params, timeout=self.timeout)
                self._last_request = time.monotonic()
            except requests.RequestException as e:
                self._last_request = time.monotonic()
                last_err = e
                log.warning("İstek hatası (%s), deneme %d: %s", url, attempt + 1, e)
            else:
                if resp.status_code == 200:
                    try:
                        return resp.json()
                    except ValueError as e:
                        raise TRDizinError(f"JSON çözümlenemedi: {resp.url}") from e
                if resp.status_code in (429, 500, 502, 503, 504):
                    last_err = TRDizinError(f"HTTP {resp.status_code}: {resp.url}")
                    log.warning("HTTP %s, deneme %d", resp.status_code, attempt + 1)
                else:
                    raise TRDizinError(f"HTTP {resp.status_code}: {resp.url}\n{resp.text[:500]}")
            if attempt < self.max_retries:
                time.sleep(2 ** (attempt + 1))
        raise TRDizinError(f"İstek {self.max_retries + 1} denemede başarısız: {url}") from last_err

    # ---- arama ---------------------------------------------------------

    @staticmethod
    def build_params(
        q: str,
        order: str = "relevance-DESC",
        page: int = 1,
        limit: int = MAX_LIMIT,
        facets: FacetInput = None,
    ) -> list[tuple[str, str]]:
        if limit not in ALLOWED_LIMITS:
            raise ValueError(f"limit şu değerlerden biri olmalı: {ALLOWED_LIMITS}")
        if page < 1:
            raise ValueError("page 1 veya daha büyük olmalı")
        # q isteğe bağlıdır: yalnızca facet'lerle (ör. konu) arama yapılabilir.
        params = [("q", q)] if q and q.strip() else []
        params += [("order", order), ("page", str(page)), ("limit", str(limit))]
        return params + facet_pairs(facets)

    def search_raw(
        self,
        entity: str,
        q: str,
        order: str = "relevance-DESC",
        page: int = 1,
        limit: int = MAX_LIMIT,
        facets: FacetInput = None,
    ) -> dict[str, Any]:
        """Tek bir arama sayfasının ham JSON yanıtını döndürür."""
        if entity not in ENTITIES:
            raise ValueError(f"entity şu değerlerden biri olmalı: {ENTITIES}")
        params = self.build_params(q, order, page, limit, facets)
        return self.get_json(SEARCH_PATH.format(entity=entity), params)

    def search(
        self,
        entity: str,
        q: str,
        order: str = "relevance-DESC",
        page: int = 1,
        limit: int = MAX_LIMIT,
        facets: FacetInput = None,
    ) -> SearchPage:
        raw = self.search_raw(entity, q, order, page, limit, facets)
        return SearchPage(
            entity=entity,
            page=page,
            limit=limit,
            total=extract_total(raw),
            records=extract_records(raw),
            aggregations=extract_aggregations(raw),
            raw=raw,
            aggregation_other=extract_aggregation_other(raw),
        )

    def profile(self, q: str = "", facets: FacetInput = None, entity: str = "publication") -> SearchPage:
        """Tek istekle tüm sonuç kümesinin facet dağılımlarını (yıl, dergi, kurum, şehir, konu...) getirir.

        Veri çekmeden hızlı bibliyometrik profil çıkarmak için kullanılır.
        """
        return self.search(entity, q, "relevance-DESC", 1, 10, facets)

    def iter_records(
        self,
        entity: str,
        q: str,
        order: str = "relevance-DESC",
        facets: FacetInput = None,
        limit: int = MAX_LIMIT,
        max_records: int | None = None,
        max_pages: int | None = None,
        on_page: Callable[[SearchPage], None] | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Tüm sayfaları sırayla gezerek kayıtları döndürür."""
        page = 1
        yielded = 0
        while True:
            sp = self.search(entity, q, order, page, limit, facets)
            if on_page:
                on_page(sp)
            if not sp.records:
                return
            for rec in sp.records:
                yield rec
                yielded += 1
                if max_records is not None and yielded >= max_records:
                    return
            if sp.total and page * limit >= sp.total:
                return
            if (page + 1) * limit > MAX_RESULT_WINDOW:
                log.warning("Derin sayfalama sınırına (%d) ulaşıldı; toplam %d kaydın kalanı alınamaz. "
                            "Sorguyu yıllara bölün (years=...).", MAX_RESULT_WINDOW, sp.total)
                return
            if len(sp.records) < limit:
                return
            if max_pages is not None and page >= max_pages:
                return
            page += 1

    def harvest(
        self,
        q: str,
        entity: str = "publication",
        order: str = "publicationYear-DESC",
        facets: FacetInput = None,
        years: Iterable[int] | None = None,
        max_records: int | None = None,
        on_page: Callable[[SearchPage], None] | None = None,
        record_filter: Callable[[dict[str, Any]], bool] | None = None,
    ) -> list[dict[str, Any]]:
        """Bir sorgunun tüm sonuçlarını toplar ve kimliğe göre tekilleştirir.

        `record_filter` verilirse yalnızca onu sağlayan kayıtlar tutulur.

        `years` verilirse sorgu yıl yıl bölünür. Bu, arama motorunun derin
        sayfalama sınırına takılmadan büyük sonuç kümelerini çekmeyi sağlar.
        """
        seen: dict[str, dict[str, Any]] = {}
        base = facet_pairs(facets)

        def collect(extra: list[tuple[str, str]]) -> None:
            remaining = None if max_records is None else max_records - len(seen)
            if remaining is not None and remaining <= 0:
                return
            for rec in self.iter_records(entity, q, order, base + extra, on_page=on_page):
                if record_filter and not record_filter(rec):
                    continue
                key = str(rec.get("id") or rec.get("_id") or id(rec))
                seen.setdefault(key, rec)
                if max_records is not None and len(seen) >= max_records:
                    return

        if years is None:
            collect([])
        else:
            year_facet = YEAR_FACET.get(entity)
            if not year_facet:
                raise ValueError(f"{entity} için yıl facet'i yok")
            base = [(k, v) for k, v in base if k != year_facet]
            for y in years:
                collect([(year_facet, str(y))])
        return list(seen.values())

    # ---- dergiler -----------------------------------------------------

    def journal_by_id(self, journal_id: str | int):
        """Derginin kimlik kaydı (`Journal`); bulunamazsa None."""
        from .journals import normalize_journal

        recs = extract_records(self.get_json(JOURNAL_BY_ID_PATH.format(id=journal_id)))
        return normalize_journal(recs[0]) if recs else None

    @staticmethod
    def journal_facets(journal) -> list[tuple[str, str]]:
        """Derginin yayınlarını bulmak için facet'ler (dergi adı ve varsa diğer adı)."""
        return [("facet-journalName", n) for n in journal.search_names]

    def harvest_journal(
        self,
        journal,
        q: str = "",
        facets: FacetInput = None,
        years: Iterable[int] | None = None,
        indexed_years_only: bool = True,
        order: str = "publicationYear-DESC",
        max_records: int | None = None,
        on_page: Callable[[SearchPage], None] | None = None,
    ) -> list[dict[str, Any]]:
        """Bir derginin (kimlik veya `Journal`) yayınlarını çeker.

        Dergi adıyla aranır, sonuçlar `journal.id` ile süzülür. `years` verilmezse ve
        `indexed_years_only` açıksa derginin TR Dizin'de dizinlendiği yıllar kullanılır.
        """
        from .journals import record_journal_id

        if not hasattr(journal, "search_names"):
            found = self.journal_by_id(journal)
            if found is None:
                raise TRDizinError(f"Dergi bulunamadı: {journal}")
            journal = found
        if years is None and indexed_years_only and journal.indexed_years:
            years = journal.indexed_years
        base = [(k, v) for k, v in facet_pairs(facets) if k != "facet-journalName"]
        jid = str(journal.id)
        return self.harvest(
            q, "publication", order, base + self.journal_facets(journal), years=years,
            max_records=max_records, on_page=on_page,
            record_filter=lambda r: record_journal_id(r) in (None, jid),
        )

    # ---- yayın ayrıntıları ---------------------------------------------

    def publication_by_id(self, publication_id: str | int) -> dict[str, Any] | None:
        """Tek yayının tam kaydı (kaynakça `references`, atıf yapanlar `citedReferences` dahil)."""
        recs = extract_records(self.get_json(PUBLICATION_BY_ID_PATH.format(id=publication_id)))
        return recs[0] if recs else None

    def enrich(
        self,
        records: list[dict[str, Any]],
        only_missing: bool = True,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> int:
        """Kayıtları yerinde `publicationById` ayrıntılarıyla tamamlar.

        Arama yanıtı kaynakça ve gelen atıfları içermiyorsa (only_missing=True) yalnızca
        eksik kayıtlar için ek istek atılır. Tamamlanan kayıt sayısını döndürür.
        """
        from .normalize import has_details  # döngüsel içe aktarmayı önle

        todo = [r for r in records if r.get("id") is not None and not (only_missing and has_details(r))]
        done = 0
        for i, rec in enumerate(todo, 1):
            try:
                detail = self.publication_by_id(rec["id"])
            except TRDizinError as e:
                log.warning("Ayrıntı alınamadı (%s): %s", rec["id"], e)
                detail = None
            if detail:
                rec.update(detail)
                done += 1
            if on_progress:
                on_progress(i, len(todo))
        return done

    # ---- tanımlı JSON adresleri ----------------------------------------

    def by_doi(self, doi: str) -> Any:
        return self.get_json(PUBLIC_PATH.format(entity="yayin", query="doi"), {"doi": doi})

    def by_orcid(self, orcid: str) -> Any:
        return self.get_json(PUBLIC_PATH.format(entity="yazar", query="orcid"), {"orcid": orcid})

    def by_issn(self, issn: str) -> Any:
        return self.get_json(PUBLIC_PATH.format(entity="dergi", query="issn"), {"issn": issn})


def detail_url(entity: str, record_id: Any) -> str:
    """Arayüzdeki detay sayfasının adresi."""
    slug = {"publication": "yayin", "journal": "dergi", "author": "yazar", "institution": "kurum"}[entity]
    return f"{BASE_URL}/tr/{slug}/detay/{record_id}"
