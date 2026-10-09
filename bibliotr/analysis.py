"""Bibliyometrik analiz fonksiyonları.

Tüm fonksiyonlar `Publication` listesi alır ve pandas DataFrame veya
networkx Graph döndürür; böylece hem Streamlit arayüzünde hem de Jupyter
defterlerinde kullanılabilir.
"""

from __future__ import annotations

import math
from collections import Counter, defaultdict
from itertools import combinations
from typing import Callable, Iterable, Sequence

import networkx as nx
import pandas as pd

from .normalize import Author, Publication, fold

# ---- temel tablolar --------------------------------------------------------


def publications_frame(pubs: Sequence[Publication]) -> pd.DataFrame:
    """Her satırı bir yayın olan düz tablo (çok değerli alanlar `; ` ile birleştirilir)."""
    rows = []
    for p in pubs:
        rows.append({
            "id": p.id,
            "title": p.title,
            "year": p.year,
            "journal": p.journal,
            "authors": "; ".join(p.author_names),
            "n_authors": len(p.authors),
            "institutions": "; ".join(dict.fromkeys(a.institution for a in p.authors if a.institution)),
            "affiliations": " | ".join(a.affiliation for a in p.authors if a.affiliation),
            "cities": "; ".join(dict.fromkeys(a.city for a in p.authors if a.city)),
            "keywords": "; ".join(p.keywords),
            "subjects": "; ".join(p.subjects),
            "citation_count": p.citation_count,
            "citations_crossref": p.citations_crossref,
            "citations_openalex": p.citations_openalex,
            "openalex_id": p.openalex_id,
            "openalex_citations": p.openalex_citations,
            "openalex_matched_by": p.openalex_matched_by,
            "citations_opencitations": p.citations_opencitations,
            "n_references": len(p.references),
            "citing_ids": "; ".join(p.cited_by),
            "view_count": p.view_count,
            "download_count": p.download_count,
            "title_en": p.title_en,
            "title_tr": p.title_tr,
            "doi": p.doi,
            "language": p.language,
            "document_type": p.document_type,
            "publication_type": p.publication_type,
            "access_type": p.access_type,
            "database": p.database,
            "issn": p.issn,
            "eissn": p.eissn,
            "volume": p.volume,
            "issue": p.issue,
            "start_page": p.start_page,
            "end_page": p.end_page,
            "url": p.url,
            "abstract": p.abstract,
        })
    df = pd.DataFrame(rows)
    if not df.empty:
        df["year"] = df["year"].astype("Int64")
        df["citation_count"] = df["citation_count"].astype("Int64")
    return df


def author_key(a: Author) -> str:
    """Yazar kimliği: varsa TR Dizin yazar kimliği/ORCID, yoksa normalize edilmiş ad."""
    if a.author_id:
        return f"id:{a.author_id}"
    if a.orcid:
        return f"orcid:{a.orcid}"
    return f"name:{fold(a.name)}"


def _author_labels(pubs: Iterable[Publication]) -> dict[str, str]:
    """Her yazar anahtarı için en sık görülen ad yazımını seçer."""
    variants: dict[str, Counter] = defaultdict(Counter)
    for p in pubs:
        for a in p.authors:
            variants[author_key(a)][a.name] += 1
    # Eşitlikte daha az BÜYÜK HARFLİ kelime içeren yazımı tercih et ("Ali Yıldız" > "Ali YILDIZ").
    def caps(name: str) -> int:
        return sum(1 for w in name.split() if len(w) > 1 and w.isupper())

    return {k: max(c, key=lambda name: (c[name], -caps(name))) for k, c in variants.items()}


def author_display_labels(pubs: Sequence[Publication]) -> dict[str, str]:
    """Her yazar anahtarı için tekil bir görünen ad.

    Farklı anahtarlar (farklı TR Dizin yazar kimlikleri) aynı ada sahipse, en çok yayını olan yalın
    kalır, diğerlerine sıra eklenir ("Ali Yılmaz (2)"). Böylece sayımlar, sıralamalar ve ağlar
    kimliğe göre yapılır ve `summary` / `lotka` ile tutarlı olur; aynı kişinin bölünmüş kimlikleri
    eşanlamlılar sözlüğüyle birleştirilir.
    """
    labels = _author_labels(pubs)
    counts: Counter = Counter()
    for p in pubs:
        for k in dict.fromkeys(author_key(a) for a in p.authors):
            counts[k] += 1
    groups: dict[str, list[str]] = defaultdict(list)
    for k, name in labels.items():
        groups[fold(name)].append(k)
    out: dict[str, str] = {}
    for ks in groups.values():
        ks.sort(key=lambda k: (-counts[k], k))
        for i, k in enumerate(ks):
            out[k] = labels[k] if i == 0 else f"{labels[k]} ({i + 1})"
    return out


def _split_institutions(a: Author) -> list[str]:
    if not a.institution:
        return []
    return [s.strip() for s in a.institution.split(";") if s.strip()]


def _reference_labels(pubs: Iterable[Publication]) -> dict[str, str]:
    """Her kaynak anahtarı için tek bir okunabilir etiket."""
    variants: dict[str, Counter] = defaultdict(Counter)
    for p in pubs:
        for r in p.references:
            variants[r.key][r.label] += 1
    return {k: c.most_common(1)[0][0] for k, c in variants.items()}


def local_citations(pubs: Sequence[Publication]) -> dict[str, int]:
    """Veri seti içindeki diğer yayınlardan alınan atıf sayısı (bibliometrix 'LCS').

    Hem gelen atıflar (`cited_by`) hem de kaynakçadaki TR Dizin kimlikleri (`target_id`) kullanılır.
    """
    ids = {p.id for p in pubs}
    edges = citation_edges(pubs)
    counts = Counter(dst for _, dst in edges)
    return {i: counts.get(i, 0) for i in ids}


def citation_edges(pubs: Sequence[Publication]) -> set[tuple[str, str]]:
    """Veri seti içindeki (atıf yapan, atıf alan) yayın çiftleri.

    Kaynaklar: gelen atıflar (`cited_by`), kaynakçadaki TR Dizin kimliği ve kaynakçadaki DOI.
    DOI yalnızca TR Dizin'in bir kayda bağlamadığı kaynaklarda kullanılır: kaynak metni bir makaleyi,
    yanlış yazılmış DOI başka bir makaleyi gösterebilir; TR Dizin'in bağı metne dayandığı için önceliklidir.
    """
    ids = {p.id for p in pubs}
    by_doi = {p.doi.lower(): p.id for p in pubs if p.doi}
    edges: set[tuple[str, str]] = set()
    for p in pubs:
        for c in p.cited_by:
            if c in ids and c != p.id:
                edges.add((c, p.id))
        for r in p.references:
            if r.target_id in ids and r.target_id != p.id:
                edges.add((p.id, r.target_id))
            target = None if r.target_id else by_doi.get((r.doi or "").lower())
            if target and target != p.id:
                edges.add((p.id, target))
    return edges


# ---- özet ve dağılımlar ----------------------------------------------------


def summary(pubs: Sequence[Publication]) -> dict[str, float | int | str | None]:
    """Veri setinin temel göstergeleri."""
    n = len(pubs)
    years = [p.year for p in pubs if p.year]
    cites = [p.citation_count or 0 for p in pubs]
    n_auth = [len(p.authors) for p in pubs]
    authors = {author_key(a) for p in pubs for a in p.authors}
    journals = {fold(p.journal) for p in pubs if p.journal}
    keywords = {fold(k) for p in pubs for k in p.keywords}
    single = sum(1 for x in n_auth if x == 1)
    multi = [x for x in n_auth if x > 1]
    with_refs = [len(p.references) for p in pubs if p.references]
    out: dict[str, float | int | str | None] = {
        "Yayın sayısı": n,
        "Zaman aralığı": f"{min(years)}-{max(years)}" if years else None,
        "Dergi sayısı": len(journals),
        "Yazar sayısı": len(authors),
        "Anahtar kelime sayısı": len(keywords),
        "Toplam atıf": sum(cites),
        "Yayın başına ortalama atıf": round(sum(cites) / n, 2) if n else 0,
        "Yayın başına ortalama yazar": round(sum(n_auth) / n, 2) if n else 0,
        "Tek yazarlı yayın oranı (%)": round(100 * single / n, 1) if n else 0,
        # Elango & Rajendran işbirliği indeksi: çok yazarlı yayınlardaki ortalama yazar sayısı.
        "İşbirliği indeksi": round(sum(multi) / len(multi), 2) if multi else 0,
        "Açık erişim oranı (%)": round(100 * sum(1 for p in pubs if (p.access_type or "").upper() == "OPEN") / n, 1)
        if n else 0,
        "Yıllık büyüme oranı (%)": annual_growth_rate(pubs),
        "Kaynakçası olan yayın (%)": round(100 * len(with_refs) / n, 1) if n else 0,
        "Yayın başına ortalama kaynak": round(sum(with_refs) / len(with_refs), 1) if with_refs else 0,
    }
    return out


def annual_growth_rate(pubs: Sequence[Publication]) -> float | None:
    """Bileşik yıllık büyüme oranı (bibliometrix'teki 'Annual Growth Rate')."""
    counts = Counter(p.year for p in pubs if p.year)
    if len(counts) < 2:
        return None
    y0, y1 = min(counts), max(counts)
    if counts[y0] == 0 or y1 == y0:
        return None
    return round(100 * ((counts[y1] / counts[y0]) ** (1 / (y1 - y0)) - 1), 2)


def per_year(pubs: Sequence[Publication], fill_gaps: bool = True) -> pd.DataFrame:
    """Yıllara göre yayın sayısı, toplam ve ortalama atıf, kümülatif yayın."""
    df = pd.DataFrame(
        [{"year": p.year, "citations": p.citation_count or 0} for p in pubs if p.year]
    )
    if df.empty:
        return pd.DataFrame(columns=["year", "publications", "citations", "mean_citations", "cumulative"])
    g = df.groupby("year").agg(publications=("year", "size"), citations=("citations", "sum")).reset_index()
    if fill_gaps:
        full = pd.DataFrame({"year": range(int(g["year"].min()), int(g["year"].max()) + 1)})
        g = full.merge(g, on="year", how="left").fillna(0)
    g["publications"] = g["publications"].astype(int)
    g["citations"] = g["citations"].astype(int)
    g["mean_citations"] = (g["citations"] / g["publications"].where(g["publications"] > 0)).round(2).fillna(0)
    g["cumulative"] = g["publications"].cumsum()
    return g


def country_label(value: str, institution: str | None = None, city: str | None = None) -> str:
    """Ülke adının tek bir görünen biçimi: "TÜRKİYE", "Turkey", "Türkiye" -> "Türkiye"; "Çin", "China" -> "China".

    Bilinen ülkeler bibliometrix ülke listesindeki adla birleştirilir (WoS dışa aktarımıyla aynı
    eşleme); Türkiye Türkçe yazılır. KKTC ayrı ülkedir ("North Cyprus"): kurum ya da şehir KKTC'deyse
    yazılı ülke ("Türkiye", "Kıbrıs") ne olursa olsun KKTC sayılır. Tanınmayan değer olduğu gibi kalır.
    """
    from .wos import resolve_country

    value = (value or "").strip()
    canon = resolve_country(value, institution, city)
    if not canon:
        return value
    if canon == "TURKEY":
        return "Türkiye"
    if canon in ("USA", "UK"):
        return canon
    return " ".join(w.capitalize() for w in canon.split())


def author_countries(a) -> list[str]:
    """Yazarın adres ülkeleri (görünen ad); çok kurumlu yazarda kurum, şehir ve ülke aynı sıradadır."""
    insts = [s.strip() for s in a.institution.split(";")] if a.institution else []
    cities = [s.strip() for s in a.city.split(";")] if a.city else []
    countries = [s.strip() for s in a.country.split(";")] if a.country else []
    out = []
    for i in range(max(len(insts), len(countries), 1)):
        c = countries[i] if i < len(countries) else (countries[0] if countries else "")
        inst = insts[i] if i < len(insts) else ""
        city = cities[i] if i < len(cities) else (cities[0] if cities else "")
        label = country_label(c, inst, city)
        if label:
            out.append(label)
    return list(dict.fromkeys(out))


_FIELD_GETTERS: dict[str, Callable[[Publication], list[str]]] = {
    "journal": lambda p: [p.journal] if p.journal else [],
    "keyword": lambda p: p.keywords,
    "subject": lambda p: p.subjects,
    "language": lambda p: [p.language] if p.language else [],
    "publication_type": lambda p: [p.publication_type] if p.publication_type else [],
    "document_type": lambda p: [p.document_type] if p.document_type else [],
    "database": lambda p: [p.database] if p.database else [],
    "access_type": lambda p: [p.access_type] if p.access_type else [],
    "institution": lambda p: list(dict.fromkeys(i for a in p.authors for i in _split_institutions(a))),
    "institution_type": lambda p: list(dict.fromkeys(a.institution_type for a in p.authors if a.institution_type)),
    "institution_status": lambda p: list(dict.fromkeys(a.institution_status for a in p.authors
                                                       if a.institution_status)),
    "city": lambda p: list(dict.fromkeys(c.strip() for a in p.authors if a.city
                                         for c in a.city.split(";") if c.strip())),
    "country": lambda p: list(dict.fromkeys(c for a in p.authors for c in author_countries(a))),
}

FIELD_LABELS = {
    "author": "Yazar",
    "journal": "Dergi",
    "keyword": "Anahtar kelime",
    "subject": "Konu",
    "institution": "Kurum",
    "city": "Şehir",
    "country": "Ülke",
    "language": "Dil",
    "publication_type": "Yayın türü",
    "document_type": "Doküman tipi",
    "database": "Veritabanı",
    "access_type": "Erişim",
    "institution_type": "Kurum tipi",
    "institution_status": "Kurum statüsü",
    "reference": "Kaynak",
}


def items_per_publication(pubs: Sequence[Publication], field: str) -> list[list[str]]:
    """Her yayın için ilgili alanın (tekilleştirilmiş) değer listesi."""
    if field == "author":
        labels = author_display_labels(pubs)
        return [list(dict.fromkeys(labels[author_key(a)] for a in p.authors)) for p in pubs]
    if field == "reference":
        ref_labels = _reference_labels(pubs)
        return [list(dict.fromkeys(ref_labels[r.key] for r in p.references)) for p in pubs]
    getter = _FIELD_GETTERS[field]
    out = []
    for p in pubs:
        # Aynı yayında aynı değer (büyük/küçük harf farkıyla) bir kez sayılsın.
        seen: dict[str, str] = {}
        for v in getter(p):
            seen.setdefault(fold(v), v)
        out.append(list(seen.values()))
    return out


def top_counts(pubs: Sequence[Publication], field: str, n: int = 20) -> pd.DataFrame:
    """Bir alandaki en sık değerler, yayın sayısı ve toplam atıf ile."""
    counts: Counter = Counter()
    cites: Counter = Counter()
    label: dict[str, str] = {}
    for p, items in zip(pubs, items_per_publication(pubs, field)):
        for v in items:
            k = fold(v)
            label.setdefault(k, v)
            counts[k] += 1
            cites[k] += p.citation_count or 0
    rows = [
        {FIELD_LABELS.get(field, field): label[k], "Yayın": c, "Atıf": cites[k]}
        for k, c in counts.most_common(n)
    ]
    return pd.DataFrame(rows)


def most_cited(pubs: Sequence[Publication], n: int = 20, by: str = "Atıf") -> pd.DataFrame:
    """En çok atıf alan yayınlar.

    Atıf: TR Dizin toplam atıf; Yerel atıf: veri setindeki diğer yayınlardan alınan atıf.
    """
    lcs = local_citations(pubs)
    df = pd.DataFrame([
        {
            "Başlık": p.title,
            "Yazarlar": "; ".join(p.author_names),
            "Yıl": p.year,
            "Dergi": p.journal,
            "Atıf": p.citation_count or 0,
            "Yerel atıf": lcs.get(p.id, 0),
            "CrossRef": p.citations_crossref,
            "OpenCitations": p.citations_opencitations,
            "DOI": p.doi,
            "Bağlantı": p.url,
        }
        for p in pubs
    ])
    if df.empty:
        return df
    return df.sort_values([by, "Atıf"], ascending=False).head(n).reset_index(drop=True)


def top_references(pubs: Sequence[Publication], n: int = 30) -> pd.DataFrame:
    """Veri setindeki yayınların en sık atıf yaptığı kaynaklar."""
    counts: Counter = Counter()
    sample: dict[str, str] = {}
    target: dict[str, str | None] = {}
    for p in pubs:
        for r in {r.key: r for r in p.references}.values():
            counts[r.key] += 1
            sample.setdefault(r.key, r.text)
            target.setdefault(r.key, r.target_id)
    labels = _reference_labels(pubs)
    return pd.DataFrame([
        {
            "Kaynak": labels[k],
            "Atıf yapan yayın": c,
            "TR Dizin'de": "Evet" if target[k] else "",
            "Örnek metin": sample[k],
        }
        for k, c in counts.most_common(n)
    ])


def citation_sources(pubs: Sequence[Publication]) -> pd.DataFrame:
    """TR Dizin, CrossRef, OpenAlex ve OpenCitations atıf sayılarının karşılaştırması.

    Karşılaştırma adil olsun diye yalnızca dış atıf verisi olan yayınlar üzerinden hesaplanır.
    "OpenAlex (TR Dizin)" TR Dizin'in bildirdiği değerdir ve sistematik olarak eksik olabilir;
    `bibliotr openalex` ile doğrudan alınan sayılar varsa "OpenAlex (doğrudan)" satırında gösterilir.
    """
    pubs = [p for p in pubs if any(v is not None for v in (
        p.citations_crossref, p.citations_openalex, p.citations_opencitations, p.openalex_citations))]
    sources = [
        ("TR Dizin", lambda p: p.citation_count),
        ("CrossRef", lambda p: p.citations_crossref),
        ("OpenAlex (TR Dizin)", lambda p: p.citations_openalex),
        ("OpenCitations", lambda p: p.citations_opencitations),
    ]
    if any(p.openalex_citations is not None for p in pubs):
        sources.append(("OpenAlex (doğrudan)", lambda p: p.openalex_citations))
    rows = []
    for label, get in sources:
        vals = [get(p) for p in pubs if get(p) is not None]
        rows.append({
            "Kaynak": label,
            "Veri olan yayın": len(vals),
            "Toplam atıf": sum(vals),
            "Ortalama": round(sum(vals) / len(vals), 2) if vals else float("nan"),  # NaN: sütun sayısal kalır
            "Atıf alan yayın (%)": round(100 * sum(1 for v in vals if v > 0) / len(vals), 1) if vals else float("nan"),
        })
    return pd.DataFrame(rows)


def h_index(citations: Iterable[int]) -> int:
    c = sorted((x or 0 for x in citations), reverse=True)
    return sum(1 for i, x in enumerate(c, 1) if x >= i)


def g_index(citations: Iterable[int]) -> int:
    c = sorted((x or 0 for x in citations), reverse=True)
    total, g = 0, 0
    for i, x in enumerate(c, 1):
        total += x
        if total >= i * i:
            g = i
    return g


def author_metrics(pubs: Sequence[Publication], n: int | None = 50) -> pd.DataFrame:
    """Yazar bazında yayın, kesirli yayın, ilk yazarlık, atıf, h ve g indeksi (veri seti içinde)."""
    labels = author_display_labels(pubs)
    data: dict[str, dict] = defaultdict(lambda: {"pubs": 0, "frac": 0.0, "first": 0, "cites": [], "years": [],
                                                 "inst": Counter()})
    for p in pubs:
        keys = list(dict.fromkeys(author_key(a) for a in p.authors))
        for a in p.authors:
            k = author_key(a)
            for i in _split_institutions(a):
                data[k]["inst"][i] += 1
        for pos, k in enumerate(keys):
            d = data[k]
            d["pubs"] += 1
            d["frac"] += 1 / len(keys)
            d["first"] += pos == 0
            d["cites"].append(p.citation_count or 0)
            if p.year:
                d["years"].append(p.year)
    rows = []
    for k, d in data.items():
        rows.append({
            "Yazar": labels.get(k, k),
            "Yayın": d["pubs"],
            "Kesirli yayın": round(d["frac"], 2),
            "İlk yazar": d["first"],
            "Atıf": sum(d["cites"]),
            "h-indeksi": h_index(d["cites"]),
            "g-indeksi": g_index(d["cites"]),
            "İlk yıl": min(d["years"]) if d["years"] else None,
            "Son yıl": max(d["years"]) if d["years"] else None,
            "Kurum": d["inst"].most_common(1)[0][0] if d["inst"] else None,
        })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values(["Yayın", "Atıf"], ascending=False).reset_index(drop=True)
    return df.head(n) if n else df


# ---- bibliyometrik yasalar -------------------------------------------------


def lotka(pubs: Sequence[Publication]) -> tuple[pd.DataFrame, dict[str, float]]:
    """Lotka yasası: x yayın yapan yazar sayısı ~ C / x^n.

    Gözlenen dağılım ile en küçük kareler (log-log) ile tahmin edilen üs
    ve klasik n=2 beklentisi birlikte döndürülür.
    """
    per_author = Counter(k for p in pubs for k in {author_key(a) for a in p.authors})
    dist = Counter(per_author.values())
    total = sum(dist.values())
    if not total:
        return pd.DataFrame(columns=["Yayın sayısı", "Yazar sayısı", "Gözlenen oran", "Beklenen oran (n=2)"]), {}
    xs = sorted(dist)
    # Klasik Lotka (n=2): C = 6 / pi^2
    c2 = 6 / math.pi ** 2
    rows = [
        {
            "Yayın sayısı": x,
            "Yazar sayısı": dist[x],
            "Gözlenen oran": round(dist[x] / total, 4),
            "Beklenen oran (n=2)": round(c2 / x ** 2, 4),
        }
        for x in xs
    ]
    params: dict[str, float] = {"Yazar sayısı": total}
    if len(xs) >= 2:
        lx = [math.log10(x) for x in xs]
        ly = [math.log10(dist[x] / total) for x in xs]
        mx, my = sum(lx) / len(lx), sum(ly) / len(ly)
        sxx = sum((a - mx) ** 2 for a in lx)
        slope = sum((a - mx) * (b - my) for a, b in zip(lx, ly)) / sxx if sxx else 0
        params["Tahmini üs (n)"] = round(-slope, 3)
        params["Tahmini C"] = round(10 ** (my - slope * mx), 4)
        # Log-log en küçük kareler kuyruktaki tekil noktalara duyarlıdır; asıl tahmin olarak kesikli
        # kuvvet yasasının en çok olabilirlik kestirimi (xmin = 1) ve Kolmogorov-Smirnov uyumu verilir
        # (Clauset, Shalizi ve Newman, 2009; Pao, 1985'teki 1,63/sqrt(N) kritik değeriyle).
        n_mle = lotka_mle(per_author.values())
        if n_mle is not None:
            params["MLE üs (n)"] = round(n_mle, 3)
            params["KS D (MLE)"] = round(_lotka_ks(dist, total, n_mle), 4)
            params["KS kritik değer (0,01)"] = round(1.63 / math.sqrt(total), 4)
    return pd.DataFrame(rows), params


def _zeta(s: float, terms: int = 1000) -> float:
    """Riemann zeta (s > 1): ilk terimlerin toplamı + Euler-Maclaurin kuyruk düzeltmesi."""
    n = terms
    head = sum(k ** -s for k in range(1, n))
    return head + n ** (1 - s) / (s - 1) + 0.5 * n ** -s + s * n ** (-s - 1) / 12


def lotka_mle(productivities: Iterable[int], lo: float = 1.01, hi: float = 8.0) -> float | None:
    """Kesikli kuvvet yasası üssünün en çok olabilirlik kestirimi (xmin = 1).

    log L(n) = -N log ζ(n) - n Σ log x_i; tek tepeli olduğu için altın oran aramasıyla bulunur.
    """
    xs = [int(x) for x in productivities if x and x >= 1]
    if len(xs) < 2 or len(set(xs)) < 2:
        return None
    n_obs, slog = len(xs), sum(math.log(x) for x in xs)

    def nll(a: float) -> float:
        return n_obs * math.log(_zeta(a)) + a * slog

    g = (math.sqrt(5) - 1) / 2
    a, b = lo, hi
    c, d = b - g * (b - a), a + g * (b - a)
    for _ in range(80):
        if nll(c) < nll(d):
            b, d = d, c
            c = b - g * (b - a)
        else:
            a, c = c, d
            d = a + g * (b - a)
    return (a + b) / 2


def _lotka_ks(dist: Counter, total: int, n: float) -> float:
    """Gözlenen ve kestirilen (zeta ile ölçeklenmiş) birikimli dağılımlar arasındaki en büyük fark."""
    z = _zeta(n)
    obs = fit = d = 0.0
    for x in range(1, max(dist) + 1):
        obs += dist.get(x, 0) / total
        fit += x ** -n / z
        d = max(d, abs(obs - fit))
    return d


def lotka_bootstrap(productivities: Iterable[int], n_boot: int = 1000, seed: int = 0,
                    max_x: int = 100_000) -> dict[str, float] | None:
    """Lotka uyumunun parametrik bootstrap p değeri (Clauset, Shalizi ve Newman, 2009; xmin = 1 sabit).

    Üs aynı veriden kestirildiği için Kolmogorov-Smirnov uzaklığı hazır kritik değerle karşılaştırılamaz.
    Kestirilen üsle kesikli kuvvet yasasından N büyüklüğünde `n_boot` örneklem çekilir, her birinde üs
    yeniden kestirilip KS uzaklığı hesaplanır; p, gözlenenden büyük ya da eşit uzaklıkların payıdır.
    Lotka yasası tüm yazarları kapsadığı için xmin 1'de sabit tutulur. Aynı `seed` aynı sonucu verir.
    """
    import numpy as np

    xs = [int(x) for x in productivities if x and x >= 1]
    n_hat = lotka_mle(xs)
    if n_hat is None:
        return None
    total = len(xs)
    d_obs = _lotka_ks(Counter(xs), total, n_hat)
    support = np.arange(1, max_x + 1, dtype=float)
    pmf = support ** -n_hat
    cdf = np.cumsum(pmf / pmf.sum())
    rng = np.random.default_rng(seed)
    sims = []
    for _ in range(n_boot):
        sample = (np.searchsorted(cdf, rng.random(total), side="right") + 1).tolist()
        n_b = lotka_mle(sample)
        sims.append(float("inf") if n_b is None else _lotka_ks(Counter(sample), total, n_b))
    exceed = sum(d >= d_obs for d in sims)
    return {"n": round(n_hat, 3), "ks_d": round(d_obs, 4), "p": round(exceed / n_boot, 3),
            "ks_d_boot_q95": round(float(np.quantile(sims, 0.95)), 4),
            "n_boot": n_boot, "seed": seed, "xmin": 1, "authors": total}


def bradford(pubs: Sequence[Publication], zones: int = 3) -> pd.DataFrame:
    """Bradford yasası: dergileri, her biri yaklaşık eşit sayıda makale içeren bölgelere ayırır."""
    counts = Counter()
    label: dict[str, str] = {}
    for p in pubs:
        if p.journal:
            k = fold(p.journal)
            label.setdefault(k, p.journal)
            counts[k] += 1
    if not counts:
        return pd.DataFrame(columns=["Sıra", "Dergi", "Yayın", "Kümülatif", "Bölge"])
    total = sum(counts.values())
    rows, cum = [], 0
    for rank, (k, c) in enumerate(counts.most_common(), 1):
        cum += c
        # Bölge, dergiye ait makalelerin başladığı kümülatif noktaya göre belirlenir.
        zone = min(zones, int((cum - c) / total * zones) + 1)
        rows.append({"Sıra": rank, "Dergi": label[k], "Yayın": c, "Kümülatif": cum, "Bölge": zone})
    return pd.DataFrame(rows)


# ---- ağlar -----------------------------------------------------------------


def cooccurrence_network(
    items_per_doc: Iterable[Sequence[str]],
    min_occurrence: int = 1,
    min_weight: int = 1,
    top_n: int | None = None,
) -> nx.Graph:
    """Aynı belgede birlikte görülen öğelerin ağırlıklı ağı.

    Düğüm özniteliği `count`: öğenin geçtiği belge sayısı.
    Kenar özniteliği `weight`: birlikte geçtikleri belge sayısı.
    """
    docs = [list(dict.fromkeys(d)) for d in items_per_doc]
    counts = Counter(i for d in docs for i in d)
    keep = {k for k, c in counts.items() if c >= min_occurrence}
    if top_n:
        keep &= {k for k, _ in counts.most_common(top_n)}
    g = nx.Graph()
    # Düğümler sıralı eklenir: küme (set) sırası çalıştırmadan çalıştırmaya değişir (PYTHONHASHSEED) ve
    # Louvain sonucu ekleme sırasına bağlıdır; sıralı ekleme aynı veride hep aynı kümeleri verir.
    for k in sorted(keep):
        g.add_node(k, count=counts[k])
    for d in docs:
        for a, b in combinations(sorted(i for i in d if i in keep), 2):
            if g.has_edge(a, b):
                g[a][b]["weight"] += 1
            else:
                g.add_edge(a, b, weight=1)
    weak = [(a, b) for a, b, w in g.edges(data="weight") if w < min_weight]
    g.remove_edges_from(weak)
    return g


def field_network(
    pubs: Sequence[Publication], field: str, min_occurrence: int = 1, min_weight: int = 1, top_n: int | None = 100
) -> nx.Graph:
    """Ortak yazarlık (`author`), kurum/şehir/ülke işbirliği veya anahtar kelime eş-oluşum ağı."""
    docs = items_per_publication(pubs, field)
    if field in ("keyword", "subject"):
        # Anahtar kelimelerde büyük/küçük harf farkını birleştir.
        label: dict[str, str] = {}
        folded = []
        for d in docs:
            row = []
            for v in d:
                label.setdefault(fold(v), v)
                row.append(label[fold(v)])
            folded.append(row)
        docs = folded
    return cooccurrence_network(docs, min_occurrence, min_weight, top_n)


def _pub_label(p: Publication) -> str:
    first = p.authors[0].name.split()[-1] if p.authors else "?"
    return f"{first} ({p.year or 't.y.'}) #{p.id}"


def coupling_network(pubs: Sequence[Publication], min_weight: int = 1, top_n: int | None = 100) -> nx.Graph:
    """Bibliyografik eşleşme: ortak kaynak sayısı kadar ağırlıklı yayın-yayın ağı."""
    docs = [(p, {r.key for r in p.references}) for p in pubs if p.references]
    if top_n:
        docs = sorted(docs, key=lambda d: d[0].citation_count or 0, reverse=True)[:top_n]
    g = nx.Graph()
    for p, _ in docs:
        g.add_node(_pub_label(p), count=p.citation_count or 0, id=p.id, title=p.title)
    for (p, a), (q, b) in combinations(docs, 2):
        w = len(a & b)
        if w >= min_weight:
            g.add_edge(_pub_label(p), _pub_label(q), weight=w)
    return g


def citation_network(pubs: Sequence[Publication]) -> nx.DiGraph:
    """Veri seti içindeki doğrudan atıf ağı (ok: atıf yapan -> atıf alan)."""
    by_id = {p.id: p for p in pubs}
    lcs = local_citations(pubs)
    g = nx.DiGraph()
    for src, dst in sorted(citation_edges(pubs)):  # sıralı: çıktı çalıştırmadan bağımsız
        for i in (src, dst):
            if i not in g:
                g.add_node(i, label=_pub_label(by_id[i]), count=lcs.get(i, 0), title=by_id[i].title)
        g.add_edge(src, dst, weight=1)
    return nx.relabel_nodes(g, {n: d["label"] for n, d in g.nodes(data=True)})


def detect_communities(g: nx.Graph) -> dict[str, int]:
    """Louvain (varsa) veya açgözlü modülerlik ile küme ataması."""
    if g.is_directed():
        g = g.to_undirected()
    if g.number_of_edges() == 0:
        return {n: i + 1 for i, n in enumerate(g.nodes)}
    try:
        comms = nx.community.louvain_communities(g, weight="weight", seed=42)
    except AttributeError:  # eski networkx
        comms = nx.community.greedy_modularity_communities(g, weight="weight")
    ordered = sorted(comms, key=len, reverse=True)
    return {n: i + 1 for i, c in enumerate(ordered) for n in c}


def network_table(g: nx.Graph) -> pd.DataFrame:
    """Ağdaki düğümlerin merkezilik ölçüleri ve küme numarası."""
    if g.number_of_nodes() == 0:
        return pd.DataFrame(columns=["Düğüm", "Belge", "Derece", "Bağlantı gücü", "Arasındalık", "Küme"])
    clusters = detect_communities(g)
    betw = nx.betweenness_centrality(g, weight=None) if g.number_of_nodes() <= 2000 else {}
    rows = [
        {
            "Düğüm": n,
            "Belge": d.get("count", 0),
            "Derece": g.degree(n),
            "Bağlantı gücü": g.degree(n, weight="weight"),
            "Arasındalık": round(betw.get(n, 0.0), 4),
            "Küme": clusters.get(n),
        }
        for n, d in g.nodes(data=True)
    ]
    return pd.DataFrame(rows).sort_values(["Bağlantı gücü", "Belge"], ascending=False).reset_index(drop=True)


# ---- tematik evrim ---------------------------------------------------------


def trend_by_period(
    pubs: Sequence[Publication], field: str = "keyword", period: int = 5, top_n: int = 15
) -> pd.DataFrame:
    """Dönemlere göre en sık değerlerin (ör. anahtar kelime) sayıları: satır=değer, sütun=dönem."""
    years = [p.year for p in pubs if p.year]
    if not years:
        return pd.DataFrame()
    start = min(years)
    rows = []
    for p, items in zip(pubs, items_per_publication(pubs, field)):
        if not p.year:
            continue
        s = start + ((p.year - start) // period) * period
        label = f"{s}-{s + period - 1}"
        for v in items:
            rows.append({"period": label, "item": v, "key": fold(v)})
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    names = df.groupby("key")["item"].agg(lambda s: s.value_counts().index[0])
    top = df["key"].value_counts().head(top_n).index
    pivot = (
        df[df["key"].isin(top)].groupby(["key", "period"]).size().unstack(fill_value=0)
    )
    pivot.index = [names[k] for k in pivot.index]
    pivot = pivot[sorted(pivot.columns)]
    return pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).index]


# ---- arama stratejisi: veri seti örtüşmesi -----------------------------------


def overlap_table(sets: dict[str, set[str]]) -> pd.DataFrame:
    """Birden çok sorgunun (veri setinin) getirdiği kayıtların örtüşmesi.

    Her satır bir veri setidir: kayıt sayısı, yalnızca o sette bulunan kayıtlar,
    birleşime katkı oranı ve diğer setlerle ikili kesişimler. Son satır birleşimdir.
    Arama stratejisini raporlamak (hangi terim ne kadar yeni kayıt getirdi) için
    kullanılır.
    """
    names = list(sets)
    union = set().union(*sets.values()) if sets else set()
    rows = []
    for name in names:
        s = sets[name]
        others = set().union(*(sets[o] for o in names if o != name))
        row: dict[str, object] = {
            "Veri seti": name,
            "Kayıt": len(s),
            "Yalnızca bu sette": len(s - others),
            "Birleşimdeki pay (%)": round(100 * len(s) / len(union), 1) if union else 0.0,
        }
        for o in names:
            row[o] = len(s & sets[o])
        rows.append(row)
    total: dict[str, object] = {"Veri seti": "BİRLEŞİM", "Kayıt": len(union),
                                "Yalnızca bu sette": None, "Birleşimdeki pay (%)": 100.0 if union else 0.0}
    total.update({o: None for o in names})
    rows.append(total)
    return pd.DataFrame(rows)
