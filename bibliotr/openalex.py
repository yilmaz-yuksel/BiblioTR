"""OpenAlex ile doğrudan eşleştirme ve atıf sayıları.

TR Dizin her kayıt için bir OpenAlex atıf sayısı bildirir (`otherCitations`), ancak bu alan
sistematik olarak eksiktir: 162 kayıtlık bir sınamada 108 kayıt TR Dizin'de 0, OpenAlex'te
sıfırdan büyük çıktı, tersi hiç görülmedi. Bu modül kayıtları OpenAlex API'sinde doğrudan
bulur ve güncel atıf sayısını, OpenAlex kimliğini ve yayın yılını döndürür.

Eşleştirme iki yoldan yapılır:

- **DOI** (kesin): 50'lik gruplar halinde `filter=doi:a|b|...`.
- **Başlık** (DOI'si olmayan ya da DOI ile bulunamayan kayıtlar için, isteğe bağlı):
  `filter=title.search:<başlık>`; aday, katlanmış başlık benzerliği en az `TITLE_MIN_SIMILARITY`
  ve yayın yılı farkı en çok 1 ise kabul edilir. Önce yayın dilindeki başlık, eşleşme yoksa
  diğer dildeki başlık denenir.

Kota (2026): OpenAlex kullanım başına ücretlendirir. API anahtarı olmadan günlük bütçe 0,10 $,
ücretsiz anahtarla 1 $; filtreli liste sorgusu 1.000 istekte 0,10 $, arama (title.search)
1.000 istekte 1 $. Yani anahtarsız günde yaklaşık 100 başlık araması yapılabilir. Bütçe dolunca
OpenAlex 429 döndürür; istemci bunu `OpenAlexLimitError` olarak bildirir ve başlık aramasını
durdurur (bulunanlar saklanır; komut yeniden çalıştırıldığında yalnızca kalanlar aranır).
Anahtar: https://openalex.org/settings/api (ortam değişkeni `OPENALEX_API_KEY`).
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import Callable, Iterable

import requests

from .normalize import Publication, fold

log = logging.getLogger(__name__)

API = "https://api.openalex.org/works"
BATCH = 50
FIELDS = "id,doi,title,publication_year,cited_by_count,type,language"
TITLE_MIN_SIMILARITY = 0.92
TITLE_MIN_LENGTH = 20  # daha kısa başlıklar belirsiz eşleşir; başlıkla aranmaz


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d or None


def title_key(title: str | None) -> str:
    """Karşılaştırma için başlık: katlanmış, noktalama ve fazla boşluk atılmış."""
    return " ".join(re.sub(r"[\W_]+", " ", fold(title or "")).split())


def similarity(a: str | None, b: str | None) -> float:
    ka, kb = title_key(a), title_key(b)
    if not ka or not kb:
        return 0.0
    return SequenceMatcher(None, ka, kb).ratio()


@dataclass
class OpenAlexMatch:
    publication_id: str
    openalex_id: str
    cited_by_count: int | None
    publication_year: int | None
    doi: str | None
    type: str | None
    language: str | None
    matched_by: str  # "doi" | "title"
    title_similarity: float | None = None
    title: str | None = None  # OpenAlex'teki başlık (eşleşme kontrolü için)


class OpenAlexLimitError(RuntimeError):
    """OpenAlex kotası doldu (429); yeniden denemek aynı gün işe yaramaz."""


class OpenAlexClient:
    """OpenAlex API'si için küçük istemci: toplu DOI sorgusu, başlık araması, yeniden deneme."""

    def __init__(self, mailto: str | None = None, api_key: str | None = None, min_interval: float = 0.15,
                 timeout: float = 30.0, max_retries: int = 3, session: requests.Session | None = None):
        self.mailto = mailto
        self.api_key = api_key
        self.min_interval = min_interval
        self.timeout = timeout
        self.max_retries = max_retries
        self.session = session or requests.Session()
        self._last = 0.0
        self.calls = {"list": 0, "search": 0}

    @staticmethod
    def _limit_message(resp) -> str:
        headers = getattr(resp, "headers", None) or {}
        remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
        note = f" (kalan bütçe: {remaining})" if remaining is not None else ""
        return ("OpenAlex kotası doldu" + note + ". Ücretsiz bir API anahtarı alın "
                "(https://openalex.org/settings/api) ya da yarın yeniden çalıştırın.")

    def _get(self, params: dict, kind: str = "list") -> dict:
        if self.mailto:
            params = {**params, "mailto": self.mailto}
        if self.api_key:
            params = {**params, "api_key": self.api_key}
        for attempt in range(self.max_retries + 1):
            wait = self.min_interval - (time.monotonic() - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()
            resp = self.session.get(API, params=params, timeout=self.timeout)
            self.calls[kind] = self.calls.get(kind, 0) + 1
            if resp.status_code == 429:
                headers = getattr(resp, "headers", None) or {}
                remaining = headers.get("X-RateLimit-Remaining") or headers.get("x-ratelimit-remaining")
                if attempt == self.max_retries or (remaining is not None and str(remaining).strip() in ("0", "0.0")):
                    raise OpenAlexLimitError(self._limit_message(resp))
                retry_after = headers.get("Retry-After") or headers.get("retry-after")
                time.sleep(float(retry_after) if retry_after and str(retry_after).isdigit() else 2 ** attempt)
                continue
            if resp.status_code >= 500:
                if attempt == self.max_retries:
                    resp.raise_for_status()
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            return resp.json()
        return {}

    def works_by_doi(self, dois: Iterable[str]) -> dict[str, dict]:
        """Normalize DOI -> OpenAlex kaydı (bulunamayanlar yok)."""
        dois = sorted({d for d in (norm_doi(x) for x in dois) if d})
        out: dict[str, dict] = {}
        for i in range(0, len(dois), BATCH):
            chunk = dois[i:i + BATCH]
            data = self._get({"filter": "doi:" + "|".join(chunk), "per-page": BATCH, "select": FIELDS})
            for w in data.get("results", []):
                d = norm_doi(w.get("doi"))
                if d:
                    out[d] = w
        return out

    def search_title(self, title: str, per_page: int = 5) -> list[dict]:
        # Noktalama ve özel karakterler (virgül, iki nokta, |, *, tırnak...) OpenAlex filtre ve arama
        # sözdiziminde anlam taşır ya da 400 hatasına yol açar; yalnızca harf, rakam ve boşluk kalır.
        q = " ".join(re.sub(r"[^\w\s]+|_", " ", title).split())
        if not q:
            return []
        return self._get({"filter": f"title.search:{q}", "per-page": per_page, "select": FIELDS},
                         kind="search").get("results", [])


def best_title_match(pub: Publication, candidates: list[dict],
                     min_similarity: float = TITLE_MIN_SIMILARITY) -> tuple[dict, float] | None:
    """Başlık benzerliği ve yıl ölçütüne uyan en iyi aday."""
    best: tuple[dict, float] | None = None
    for w in candidates:
        year = w.get("publication_year")
        if pub.year and year and abs(pub.year - year) > 1:
            continue
        s = max(similarity(t, w.get("title")) for t in (pub.title, pub.title_en) if t) if (pub.title or pub.title_en) else 0
        if s >= min_similarity and (best is None or s > best[1]):
            best = (w, s)
    return best


def _to_match(pub: Publication, w: dict, how: str, sim: float | None = None) -> OpenAlexMatch:
    return OpenAlexMatch(publication_id=pub.id, openalex_id=w.get("id") or "",
                         cited_by_count=w.get("cited_by_count"), publication_year=w.get("publication_year"),
                         doi=norm_doi(w.get("doi")), type=w.get("type"), language=w.get("language"),
                         matched_by=how, title_similarity=None if sim is None else round(sim, 4),
                         title=w.get("title"))


def match_publications(pubs: list[Publication], client: OpenAlexClient, by_title: bool = True,
                       on_progress: Callable[[str, int, int], None] | None = None,
                       report: dict | None = None) -> list[OpenAlexMatch]:
    """Yayınları önce DOI, sonra (isteğe bağlı) başlıkla OpenAlex'te bulur.

    `report` verilirse doldurulur: başlıkla aranan / aranamayan kayıt sayısı, istek sayıları ve
    kota dolduysa durma nedeni. Kota dolunca o ana kadar bulunanlar yine döndürülür.
    """
    report = {} if report is None else report
    report.update({"doi_queried": 0, "title_searched": 0, "title_not_searched": 0, "stopped": None})
    matches: list[OpenAlexMatch] = []
    with_doi = [p for p in pubs if norm_doi(p.doi)]
    report["doi_queried"] = len(with_doi)
    try:
        found = client.works_by_doi(p.doi for p in with_doi)
    except OpenAlexLimitError as e:
        report["stopped"] = str(e)
        report["title_not_searched"] = len(pubs)
        report["calls"] = dict(client.calls)
        return matches
    done: set[str] = set()
    for p in with_doi:
        w = found.get(norm_doi(p.doi))
        if w:
            matches.append(_to_match(p, w, "doi"))
            done.add(p.id)
    if on_progress:
        on_progress("doi", len(with_doi), len(with_doi))
    if by_title:
        def titles(p: Publication) -> list[str]:
            return list(dict.fromkeys(t for t in (p.title, p.title_en) if t and len(title_key(t)) >= TITLE_MIN_LENGTH))

        rest = [p for p in pubs if p.id not in done and titles(p)]
        for i, p in enumerate(rest, 1):
            best = None
            try:
                for t in titles(p):  # önce yayın dilindeki başlık; eşleşmezse diğeri (kota tasarrufu)
                    best = best_title_match(p, client.search_title(t))
                    if best:
                        break
            except OpenAlexLimitError as e:
                report["stopped"] = str(e)
                report["title_not_searched"] = len(rest) - i + 1
                log.warning("%s", e)
                break
            except requests.RequestException as e:  # tek kaydın ağ hatası tümünü durdurmasın
                log.warning("OpenAlex başlık araması başarısız (%s): %s", p.id, e)
                report["title_not_searched"] += 1
                continue
            report["title_searched"] += 1
            if best:
                matches.append(_to_match(p, best[0], "title", best[1]))
            if on_progress:
                on_progress("title", i, len(rest))
    report["calls"] = dict(client.calls)
    return matches
