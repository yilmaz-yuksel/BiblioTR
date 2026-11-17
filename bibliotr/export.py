"""Veri setini farklı bibliyometrik araçların okuyabileceği biçimlere aktarır.

- CSV / Excel: genel kullanım
- RIS / BibTeX: Zotero, Mendeley, EndNote
- WoS düz metin (plain text): Bibliometrix/Biblioshiny ve VOSviewer doğrudan okuyabilir
- VOSviewer map + network: hazır hesaplanmış ağları VOSviewer'da açmak için
"""

from __future__ import annotations

import io
import re
from typing import Sequence

import networkx as nx
import pandas as pd

from .analysis import detect_communities, publications_frame
from .normalize import Publication

LANGUAGE_NAMES = {
    "TUR": "Turkish", "ENG": "English", "GER": "German", "FRE": "French", "ARA": "Arabic",
    "RUS": "Russian", "ITA": "Italian", "SPA": "Spanish", "KUR": "Kurdish", "AZE": "Azerbaijani",
    "KAZ": "Kazakh", "OTH": "Other",
}

# TR Dizin yayın türü -> WoS doküman tipi
DOC_TYPES = {
    "RESEARCH": "Article",
    "COMPILATION": "Review",
    "FACT_PRESENTATION": "Article",
    "BOOK_PRESENTATION": "Book Review",
    "CORRECTION": "Correction",
    "EDITORIAL": "Editorial Material",
    "LETTER": "Letter",
    "LETTER_TO_EDITOR": "Letter",
    "MEETING_SUMMARY": "Meeting Abstract",
    "REPORT": "Article",
    "SHORT_REPORT": "Article",
    "TRANSLATION": "Article",
    "RETRACTED": "Retracted Publication",
    "NOTE": "Note",
    "OTHER": "Article",
}


# Excel'in kabul etmediği kontrol karakterleri (sekme, satır sonu hariç) ve hücre uzunluk sınırı.
_ILLEGAL_EXCEL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_EXCEL_CELL_LIMIT = 32_767


def _excel_safe(df: pd.DataFrame) -> pd.DataFrame:
    """Metin hücrelerindeki geçersiz karakterleri siler ve çok uzun metinleri kısaltır."""
    def clean(v):
        if isinstance(v, str):
            v = _ILLEGAL_EXCEL_CHARS.sub("", v)
            return v if len(v) <= _EXCEL_CELL_LIMIT else v[: _EXCEL_CELL_LIMIT - 1] + "…"
        return v
    return df.apply(lambda col: col.map(clean) if not pd.api.types.is_numeric_dtype(col) else col)


def _write_sheet(df: pd.DataFrame, xw: pd.ExcelWriter, sheet: str) -> None:
    _excel_safe(df).to_excel(xw, sheet_name=sheet, index=False)


def to_csv(pubs: Sequence[Publication]) -> bytes:
    # Excel'in Türkçe karakterleri doğru açması için BOM'lu UTF-8.
    return publications_frame(pubs).to_csv(index=False).encode("utf-8-sig")


def to_excel(pubs: Sequence[Publication]) -> bytes:
    authors = pd.DataFrame([
        {"publication_id": p.id, "position": a.position, "name": a.name, "author_id": a.author_id,
         "orcid": a.orcid, "institution": a.institution, "institution_type": a.institution_type,
         "institution_status": a.institution_status, "unit": a.unit,
         "institution_matched": a.institution_matched, "city": a.city, "country": a.country,
         "affiliation": a.affiliation}
        for p in pubs for a in p.authors
    ])
    keywords = pd.DataFrame([{"publication_id": p.id, "keyword": k} for p in pubs for k in p.keywords])
    subjects = pd.DataFrame([{"publication_id": p.id, "subject": s} for p in pubs for s in p.subjects])
    references = pd.DataFrame([
        {"publication_id": p.id, "position": r.position, "reference": r.text, "year": r.year,
         "trdizin_id": r.target_id, "key": r.key}
        for p in pubs for r in p.references
    ])
    citing = pd.DataFrame([{"publication_id": p.id, "citing_trdizin_id": c} for p in pubs for c in p.cited_by])
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        _write_sheet(publications_frame(pubs), xw, "Yayinlar")
        _write_sheet(authors, xw, "Yazarlar")
        _write_sheet(keywords, xw, "AnahtarKelimeler")
        _write_sheet(subjects, xw, "Konular")
        _write_sheet(references, xw, "Kaynakca")
        _write_sheet(citing, xw, "AtifYapanlar")
    return buf.getvalue()


def aggregations_frame(aggregations: dict[str, list[tuple[str, int]]], labels: dict[str, str] | None = None
                       ) -> pd.DataFrame:
    """Facet dağılımlarını (agregasyonlar) uzun biçimli tabloya çevirir: Alan, Değer, Yayın."""
    labels = labels or {}
    rows = [
        {"Alan": labels.get(name, name), "Değer": key, "Yayın": count}
        for name, buckets in aggregations.items() for key, count in buckets
    ]
    return pd.DataFrame(rows, columns=["Alan", "Değer", "Yayın"])


def aggregations_to_excel(aggregations: dict[str, list[tuple[str, int]]], labels: dict[str, str] | None = None,
                          meta: dict[str, str] | None = None) -> bytes:
    """Her facet dağılımını ayrı bir sayfaya yazar (ilk sayfada sorgu bilgisi)."""
    labels = labels or {}
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as xw:
        _write_sheet(pd.DataFrame(list((meta or {}).items()), columns=["Bilgi", "Değer"]), xw, "Sorgu")
        for name, buckets in aggregations.items():
            if not buckets:
                continue
            sheet = re.sub(r"[\[\]:*?/\\]", "", labels.get(name, name))[:31]
            _write_sheet(pd.DataFrame(buckets, columns=["Değer", "Yayın"]), xw, sheet)
    return buf.getvalue()


# ---- ad biçimlendirme ------------------------------------------------------


def split_name(name: str) -> tuple[str, str]:
    """'Ali Veli YILDIZ' -> ('Yıldız', 'Ali Veli'); 'Yıldız, Ali' -> ('Yıldız', 'Ali')."""
    name = re.sub(r"\s+", " ", name).strip()
    if "," in name:
        last, first = (s.strip() for s in name.split(",", 1))
        return last, first
    parts = name.split(" ")
    if len(parts) == 1:
        return parts[0], ""
    return parts[-1], " ".join(parts[:-1])


def _title_case(s: str) -> str:
    if s.isupper():
        s = s.replace("I", "ı").replace("İ", "i").lower()
        return " ".join(w[:1].replace("i", "İ").replace("ı", "I").upper() + w[1:] for w in s.split(" "))
    return s


def full_name(name: str) -> str:
    last, first = split_name(name)
    last = _title_case(last)
    return f"{last}, {first}" if first else last


def short_name(name: str) -> str:
    last, first = split_name(name)
    initials = "".join(p[0].upper() for p in re.split(r"[\s\-.]+", first) if p)
    last = _title_case(last)
    return f"{last}, {initials}" if initials else last


# ---- WoS düz metin ---------------------------------------------------------


def to_wos(pubs: Sequence[Publication], options=None) -> str:
    """Web of Science 'Plain Text' biçimi (bibliometrix/Biblioshiny ve VOSviewer uyumlu).

    Biblioshiny'de: Import raw file(s) -> Database: Web of Science -> Format: Plaintext.
    VOSviewer'da: Create map based on bibliographic data -> Web of Science.
    Ayrıntılar ve seçenekler: `bibliotr.wos`.
    """
    from .wos import to_wos as _to_wos

    return _to_wos(pubs, options)


# ---- RIS / BibTeX ----------------------------------------------------------


def to_ris(pubs: Sequence[Publication]) -> str:
    lines: list[str] = []
    for p in pubs:
        # TR Dizin proje kayıtları dergi makalesi değildir; RIS'te en yakın tür rapordur.
        lines.append("TY  - RPRT" if (p.document_type or "").upper() == "PROJECT" else "TY  - JOUR")
        for a in p.authors:
            lines.append(f"AU  - {full_name(a.name)}")
        for tag, val in (
            ("TI", p.title), ("T2", p.journal), ("PY", p.year), ("VL", p.volume), ("IS", p.issue),
            ("SP", p.start_page), ("EP", p.end_page), ("DO", p.doi), ("SN", p.issn or p.eissn),
            ("LA", p.language), ("AB", p.abstract), ("UR", p.url),
        ):
            if val:
                text = re.sub(r"\s+", " ", str(val)).strip()
                lines.append(f"{tag}  - {text}")
        for k in p.keywords:
            lines.append(f"KW  - {k}")
        lines.append(f"ID  - TRDIZIN{p.id}")
        lines.append("ER  - ")
        lines.append("")
    return "\n".join(lines)


# DOI ve URL alanları LaTeX'te olduğu gibi (verbatim) okunur; kaçış eklenirse adres bozulur.
_BIB_VERBATIM = ("doi", "url")


def _bib_escape(s: str, verbatim: bool = False) -> str:
    s = re.sub(r"\s+", " ", s).strip()
    if verbatim:
        return s
    s = s.replace("\\", "\\\\")
    return re.sub(r"([{}&%#$_])", r"\\\1", s)


def to_bibtex(pubs: Sequence[Publication]) -> str:
    entries = []
    for p in pubs:
        fields = {
            "author": " and ".join(full_name(a.name) for a in p.authors),
            "title": p.title,
            "journal": p.journal,
            "year": p.year,
            "volume": p.volume,
            "number": p.issue,
            "pages": f"{p.start_page}--{p.end_page}" if p.start_page and p.end_page else p.start_page,
            "doi": p.doi,
            "issn": p.issn or p.eissn,
            "keywords": ", ".join(p.keywords),
            "url": p.url,
            "abstract": p.abstract,
        }
        body = ",\n".join(f"  {k} = {{{_bib_escape(str(v), k in _BIB_VERBATIM)}}}" for k, v in fields.items() if v)
        kind = "techreport" if (p.document_type or "").upper() == "PROJECT" else "article"
        entries.append(f"@{kind}{{trdizin{p.id},\n{body}\n}}")
    return "\n\n".join(entries) + "\n"


# ---- VOSviewer -------------------------------------------------------------


def to_vosviewer(g: nx.Graph) -> tuple[str, str]:
    """(map dosyası, network dosyası) döndürür.

    VOSviewer'da: Create map based on network data -> VOSviewer map file + network file.
    """
    clusters = detect_communities(g)
    ids = {n: i for i, n in enumerate(g.nodes, 1)}
    map_lines = ["id\tlabel\tweight<Documents>\tweight<Total link strength>\tcluster"]
    for n, d in g.nodes(data=True):
        label = str(n).replace("\t", " ").replace("\n", " ")
        map_lines.append(
            f"{ids[n]}\t{label}\t{d.get('count', 0)}\t{g.degree(n, weight='weight')}\t{clusters.get(n, 1)}"
        )
    net_lines = [f"{ids[a]}\t{ids[b]}\t{w}" for a, b, w in g.edges(data="weight", default=1)]
    return "\n".join(map_lines) + "\n", "\n".join(net_lines) + "\n"
