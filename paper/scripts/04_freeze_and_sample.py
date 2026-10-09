"""Aşama 3, adım 3: veri setini dondurma ve ilgililik / alan doğruluğu / yazar kimliği kodlama tabloları.

1. **Dondurma**: veritabanı tarihli olarak kopyalanır (`<veri seti>_frozen_<tarih>.db`), SHA-256 özeti
   ve veri setinin kimlik listesi kaydedilir. Sonraki tüm analizler dondurulmuş kopya üzerinde yapılır.
2. **Kayıt seçimi**: `--n` kadar kayıt sabit tohumla seçilir; `--n 0` veri setinin tamamını tohumla
   karıştırılmış sırada verir. `--doc-types` ile yayın türü süzülür (ör. yalnız PAPER).
3. **Kodlama tabloları** (paper/data/coding/<veri seti>/, depoya girmez: TR Dizin içeriği taşır):
   - `kodlama_K1.xlsx`: ilgililik (+ `--d1-n` verilmezse D1 alan doğruluğu)
   - `kodlama_K2.xlsx`: yalnızca ilgililik (ikinci kodlayıcı için kör kodlama; `--k2-n` ile alt küme)
   - `kodlama_D1.xlsx`: (`--d1-n` verilirse) ayrı rastgele örneklemde D1 alan doğruluğu
   - `kodlama_yazar.xlsx`: (`--authors`) aynı tam ada sahip farklı TR Dizin yazar kimlikleri; aynı kişi mi?

Kapsam tanımı ve kodlama yönergesi `--scope` ile seçilir (varsayılan ve tek seçenek: fiziksel_aktivite).
Varsayılan veri seti `fa_alan`, varsayılan veritabanı `data/trdizin.db`'dir.

Çalıştırma (API gerekmez):

    python paper/scripts/04_freeze_and_sample.py --db data/trdizin.db --dataset fa_alan \
        --scope fiziksel_aktivite --doc-types PAPER --n 0 --d1-n 150 --authors

Çıktılar:
    paper/data/<veri seti>_frozen_<tarih>.db
    paper/data/coding/<veri seti>/kodlama_*.xlsx
    paper/results/freeze_<veri seti>_<tarih>/freeze.json       özet, kayıt sayıları, tohum, seçilen kimlikler
    paper/results/freeze_<veri seti>_<tarih>/<veri seti>_ids.csv  dondurulmuş veri setinin kimlik listesi
    paper/results/freeze_<veri seti>_<tarih>/author_groups.csv   yazar grupları ve kural önerisi (ad içermez)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import random
import re
import shutil
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from bibliotr import analysis  # noqa: E402
from bibliotr.normalize import Publication, fold  # noqa: E402
from bibliotr.storage import Store  # noqa: E402
from bibliotr.thesaurus import author_clusters  # noqa: E402

TARGET = "fa_alan"
SAMPLE_N = 100
SEED = 20260929

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")  # kodlayıcının dolduracağı hücreler
TOOL_FILL = PatternFill("solid", fgColor="E2EFDA")  # araç çıktısı (D1'de kontrol edilecek)
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)

# (başlık, genişlik, tür) — tür: info | input | tool
RELEVANCE_COLS = [
    ("No", 5, "info"), ("TR Dizin ID", 11, "info"), ("Bağlantı", 12, "info"), ("Yıl", 6, "info"),
    ("Dergi", 22, "info"), ("Başlık (TR)", 40, "info"), ("Başlık (EN)", 40, "info"),
    ("Özet (TR)", 70, "info"), ("Özet (EN)", 70, "info"), ("Anahtar kelimeler", 30, "info"),
    ("İlgili (1/0)", 10, "input"), ("İlgililik notu", 28, "input"),
]
D1_FIELDS = ["Başlık", "Yazarlar", "Yazar sırası", "Yıl", "Dergi", "Kurum", "DOI"]
D1_COLS = [
    ("Yazarlar (araç)", 30, "tool"), ("Kurumlar (araç)", 40, "tool"), ("DOI (araç)", 22, "tool"),
    *[(f"{f} doğru", 10, "input") for f in D1_FIELDS],
    ("D1 notu", 28, "input"),
]
AUTHOR_COLS = [
    ("Grup", 6, "info"), ("Ad", 24, "info"), ("Yazar kimliği", 12, "info"), ("Yayın", 6, "info"),
    ("Yıllar", 11, "info"), ("ORCID", 20, "info"), ("Kurumlar", 40, "info"), ("Ortak yazarlar", 30, "info"),
    ("Örnek yayınlar", 60, "info"), ("Bağlantılar", 12, "info"),
    ("Grubun 1. kimliğiyle aynı kişi (1/0/NA)", 14, "input"), ("Not", 28, "input"),
]
AUTHOR_SAME = "Grubun 1. kimliğiyle aynı kişi (1/0/NA)"

SCOPES = {
    "fiziksel_aktivite": {
        "title": "TR Dizin fiziksel aktivite veri seti: kodlama tablosu",
        "rows": [
            ("Kapsam tanımı", "Fiziksel aktivite: iskelet kaslarıyla üretilen ve enerji harcamasını artıran her "
                              "türlü bedensel hareket (Caspersen, Powell ve Christenson, 1985). Alan; fiziksel "
                              "aktivitenin düzeyini, ölçülmesini, belirleyicilerini, sağlık ve diğer sonuçlarını, "
                              "artırılmasına yönelik müdahaleleri ve fiziksel hareketsizliği (sedanter davranış) kapsar."),
            ("İlgili (1)", "İnsanlarda fiziksel aktivite yayının ana konusu ya da ana değişkenlerinden biri "
                           "(ör. fiziksel aktivite düzeyi ile uyku kalitesi ilişkisi, fiziksel aktivite ölçeği "
                           "uyarlaması, okulda fiziksel aktiviteyi artırma programı)."),
            ("İlgisiz (0)", "Fiziksel aktivite yalnızca geçerken anılıyor (ör. pek çok kontrol değişkeninden biri, "
                            "yalnızca öneri cümlesinde) ya da sözcük başka bir anlamda kullanılıyor."),
            ("Dikkat: çok anlamlılık", "'Aktivite / activity' hayvanlarda (ör. hayvan davranışı, kümes ya da ahır "
                                       "aktivitesi), cihaz ve sensörlerde ya da kimyasal ve biyolojik anlamda "
                                       "(enzim, antioksidan, antimikrobiyal, elektriksel aktivite) kullanılabilir. Bu "
                                       "yayınlar İLGİSİZ (0) kodlanır; notuna 'başka anlam' yazılır."),
            ("Dikkat: egzersiz ve spor", "Egzersiz, antrenman ve spor çalışmaları ancak fiziksel aktivite "
                                         "çerçevesinde ele alındığında İLGİLİ (1) kodlanır (ör. 'düzenli egzersiz "
                                         "olarak fiziksel aktivite', 'spora katılım ve fiziksel aktivite düzeyi'). "
                                         "Fiziksel aktiviteyle bağı kurulmayan sporcu performansı, antrenman "
                                         "fizyolojisi veya spor yönetimi çalışmaları İLGİSİZ (0) kodlanır."),
            ("Dikkat: hareketsizlik", "Sedanter davranış, oturma süresi ve ekran süresi fiziksel aktivite "
                                      "çerçevesinde incelendiğinde İLGİLİ (1) kodlanır."),
        ],
        "example": "Üniversite öğrencilerinde fiziksel aktivite düzeyi ile uyku kalitesi ilişkisi",
    },
}


# ---- dondurma ----------------------------------------------------------------


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(name)).strip("_") or "dataset"


def freeze(db: Path, stamp: str, frozen_dir: Path | None = None, name: str | None = None) -> Path:
    """Veritabanının tarihli kopyasını oluşturur; kopya varsa üzerine yazmaz."""
    frozen = (frozen_dir or db.parent) / f"{name or db.stem}_frozen_{stamp}.db"
    if not frozen.exists():
        frozen.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(db, frozen)
    return frozen


# ---- kayıt metinleri -------------------------------------------------------------


def texts_by_language(raw: dict) -> dict[str, dict[str, str]]:
    """abstracts[] -> {"TUR": {"title":..., "abstract":...}, "ENG": {...}}."""
    out: dict[str, dict[str, str]] = {}
    for ab in raw.get("abstracts") or []:
        if not isinstance(ab, dict):
            continue
        lang = (ab.get("language") or "").upper()[:3] or "?"
        slot = out.setdefault(lang, {})
        for key in ("title", "abstract"):
            if isinstance(ab.get(key), str) and ab[key].strip() and key not in slot:
                slot[key] = ab[key].strip()
    return out


def author_lines(p: Publication) -> str:
    return "\n".join(f"{a.position}. {a.name}" for a in sorted(p.authors, key=lambda a: a.position))


def institution_lines(p: Publication) -> str:
    lines = []
    for a in sorted(p.authors, key=lambda a: a.position):
        place = ", ".join(x for x in (a.city, a.country) if x)
        lines.append(f"{a.position}. {a.institution or '-'}" + (f" ({place})" if place else ""))
    return "\n".join(lines)


def row_values(no: int, p: Publication, raw: dict, with_d1: bool, relevance: bool = True) -> list:
    t = texts_by_language(raw)
    tr, en = t.get("TUR", {}), t.get("ENG", {})
    vals = [no, p.id, p.url, p.year, p.journal or "", tr.get("title", p.title_tr or ""),
            en.get("title", p.title_en or ""), tr.get("abstract", ""), en.get("abstract", ""),
            "; ".join(p.keywords)]
    if relevance:
        vals += [None, None]
    if with_d1:
        vals += [author_lines(p), institution_lines(p), p.doi or ""] + [None] * (len(D1_FIELDS) + 1)
    return vals


# ---- Excel -------------------------------------------------------------------------


def _style_header(ws, cols) -> None:
    for i, (name, width, kind) in enumerate(cols, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 32
    ws.freeze_panes = "C2"


def _write_rows(ws, rows) -> None:
    for r, (k, v) in enumerate(rows, 1):
        a, b = ws.cell(row=r, column=1, value=k), ws.cell(row=r, column=2, value=v)
        a.font = Font(name=FONT, bold=True, size=13 if r == 1 else 10)
        b.font = Font(name=FONT, size=10)
        b.alignment = Alignment(wrap_text=True, vertical="top")
        a.alignment = Alignment(vertical="top")


def _guide(wb: Workbook, coder: str, with_d1: bool, n: int, scope: str = "fiziksel_aktivite",
           relevance: bool = True, selection: str | None = None) -> None:
    sc = SCOPES[scope]
    ws = wb.create_sheet("Yönerge", 0)
    ws.column_dimensions["A"].width = 26
    ws.column_dimensions["B"].width = 100
    rows = [
        (sc["title"] if relevance else sc["title"].replace("kodlama tablosu", "alan doğruluğu (D1) tablosu"), None),
        ("Kodlayıcı", coder),
        ("Kayıt sayısı", f"{n} ({selection or f'rastgele örneklem, tohum {SEED}'})"),
        (None, None),
    ]
    if relevance:
        rows += list(sc["rows"]) + [
            ("Nasıl kodlanır", "Başlık, özet ve anahtar kelimeleri okuyun. Kararsız kalırsanız en olası kodu "
                               "yazın ve 'İlgililik notu' sütununa gerekçeyi ekleyin."),
            ("Bağımsızlık", "Kodlamayı diğer kodlayıcıyla konuşmadan tamamlayın. Uyum hesaplandıktan sonra "
                            "anlaşmazlıklar birlikte çözülür."),
        ]
    if with_d1:
        rows += [
            (None, None),
            ("D1: alan doğruluğu", "Yeşil sütunlar aracın çıkardığı bilgilerdir. Her kayıt için 'Bağlantı' "
                                   "sütunundaki TR Dizin sayfasını açın ve aşağıdaki alanları karşılaştırın."),
            ("1 / 0 / NA", "1 = araç çıktısı TR Dizin sayfasıyla aynı; 0 = farklı veya eksik; "
                           "NA = alan TR Dizin sayfasında da yok."),
            ("Başlık doğru", "Başlık (TR veya yayın dilindeki) sayfadakiyle aynı; boşluk ve harf kaybı yok. "
                             "İngilizce yayınlarda Türkçe başlık olmayabilir; bu durumda İngilizce başlığa bakın."),
            ("Yazarlar doğru", "Tüm yazarlar eksiksiz ve adları doğru."),
            ("Yazar sırası doğru", "Yazarlar sayfadaki sırayla listelenmiş."),
            ("Yıl / Dergi / DOI doğru", "Sayfadaki bilgiyle aynı."),
            ("Kurum doğru", "Her yazarın üst kurumu (üniversite) doğru; alt birim (fakülte, bölüm) gerekmez."),
        ]
    example = []
    if relevance:
        example.append(f"İlgili (1/0): 1 | İlgililik notu: {sc['example']}")
    if with_d1:
        example.append("Başlık doğru: 1 | Kurum doğru: 0 | D1 notu: 2. yazarın kurumu eksik")
    rows += [
        (None, None),
        ("Renkler", "Sarı hücreler sizin dolduracağınız hücrelerdir; diğer hücreleri değiştirmeyin."),
        ("Örnek satır", " | ".join(example)),
        ("İlerleme", "'İlerleme' sayfası kodlanan kayıt sayısını ve oranları otomatik hesaplar."),
    ]
    _write_rows(ws, rows)


def _progress(wb: Workbook, cols, n: int) -> None:
    ws = wb.create_sheet("İlerleme")
    ws.column_dimensions["A"].width = 30
    ws.column_dimensions["B"].width = 14
    names = [c[0] for c in cols]
    last = n + 1

    def col(name: str) -> str:
        letter = get_column_letter(names.index(name) + 1)
        return f"Kodlama!${letter}$2:${letter}${last}"

    rows = [("Gösterge", "Değer"), ("Kayıt sayısı", n)]
    if "İlgili (1/0)" in names:
        rel = col("İlgili (1/0)")
        rows += [
            ("Kodlanan (ilgililik)", f"=COUNTA({rel})"),
            ("İlgili (1)", f"=COUNTIF({rel},1)"),
            ("İlgisiz (0)", f"=COUNTIF({rel},0)"),
            ("İlgili oranı (kodlananlar içinde)", "=IFERROR(B4/B3,\"\")"),
        ]
    if "Başlık doğru" in names:
        rows.append(("Kodlanan (D1, başlık)", f"=COUNTA({col('Başlık doğru')})"))
        rows.append(("D1 alan doğruluğu (1 / (1+0))", None))
        for f in D1_FIELDS:
            rng = col(f"{f} doğru")
            rows.append((f"{f} doğru", f"=IFERROR(COUNTIF({rng},1)/(COUNTIF({rng},1)+COUNTIF({rng},0)),\"\")"))
    for r, (k, v) in enumerate(rows, 1):
        a, b = ws.cell(row=r, column=1, value=k), ws.cell(row=r, column=2, value=v)
        a.font = Font(name=FONT, bold=r == 1 or v is None)
        b.font = Font(name=FONT, bold=r == 1)
        if isinstance(v, str) and ("B4/B3" in v or "COUNTIF(" in v and "/" in v):
            b.number_format = "0.0%"
    ws.cell(row=len(rows) + 2, column=1,
            value="Oranlar formüldür; Kodlama sayfası doldukça güncellenir.").font = Font(name=FONT, italic=True, size=9)


def _fill_sheet(ws, cols, rows, height: int = 120) -> None:
    _style_header(ws, cols)
    for r, vals in enumerate(rows, 2):
        for c, (v, (_, _, kind)) in enumerate(zip(vals, cols), 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = Font(name=FONT, size=9)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            cell.border = BORDER
            if kind == "input":
                cell.fill = INPUT_FILL
            elif kind == "tool":
                cell.fill = TOOL_FILL
        ws.row_dimensions[r].height = height


def build_workbook(path: Path, rows: list[list], coder: str, with_d1: bool, scope: str = "fiziksel_aktivite",
                   relevance: bool = True, selection: str | None = None) -> None:
    cols = RELEVANCE_COLS[:10] + (RELEVANCE_COLS[10:] if relevance else []) + (D1_COLS if with_d1 else [])
    wb = Workbook()
    ws = wb.active
    ws.title = "Kodlama"
    _fill_sheet(ws, cols, rows)
    for r in range(2, len(rows) + 2):
        link = ws.cell(row=r, column=3)
        if link.value:
            link.hyperlink = link.value
            link.value = "Aç"
            link.font = Font(name=FONT, size=9, color="0563C1", underline="single")
    last = len(rows) + 1
    names = [c[0] for c in cols]
    if relevance:
        dv_rel = DataValidation(type="list", formula1='"1,0"', allow_blank=True,
                                errorTitle="Geçersiz kod", error="1 (ilgili) veya 0 (ilgisiz) girin.")
        ws.add_data_validation(dv_rel)
        L = get_column_letter(names.index("İlgili (1/0)") + 1)
        dv_rel.add(f"{L}2:{L}{last}")
    if with_d1:
        dv_d1 = DataValidation(type="list", formula1='"1,0,NA"', allow_blank=True,
                               errorTitle="Geçersiz kod", error="1, 0 veya NA girin.")
        ws.add_data_validation(dv_d1)
        for f in D1_FIELDS:
            L = get_column_letter(names.index(f"{f} doğru") + 1)
            dv_d1.add(f"{L}2:{L}{last}")
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last}"
    _guide(wb, coder, with_d1, len(rows), scope, relevance, selection)
    _progress(wb, cols, len(rows))
    wb.active = wb.sheetnames.index("Kodlama")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---- yazar kimliği ---------------------------------------------------------------


def author_groups(pubs: list[Publication]) -> list[dict]:
    """Aynı tam ada (katlanmış) sahip birden çok TR Dizin yazar kimliğini gruplar.

    Her kimlik için yayın, yıl, ORCID, kurum ve ortak yazar bilgisi toplanır. Kural önerisi,
    `bibliotr.thesaurus.author_clusters(..., "orcid+institution")` ile referansla aynı kümeye düşüp
    düşmediğidir (1/0); dayanağı (orcid / institution / none) ayrıca yazılır. Öneri yalnızca özet
    dosyasına yazılır, kodlama tablosunda gösterilmez.
    """
    by_full: dict[str, dict[str, dict]] = defaultdict(dict)
    for p in pubs:
        for a in p.authors:
            k = analysis.author_key(a)
            d = by_full[fold(a.name)].setdefault(k, {"key": k, "names": Counter(), "pubs": [], "years": set(),
                                                     "orcid": set(), "inst": set(), "co": Counter()})
            d["names"][a.name] += 1
            d["pubs"].append(p)
            if p.year:
                d["years"].add(p.year)
            if a.orcid:
                d["orcid"].add(a.orcid)
            if a.institution:
                d["inst"].add(a.institution)
            for b in p.authors:
                if analysis.author_key(b) != k:
                    d["co"][b.name] += 1
    clusters = author_clusters(pubs, "orcid+institution")  # bibliotr.thesaurus ile aynı kural
    groups = []
    for name, ids in sorted(by_full.items()):
        if len(ids) < 2:
            continue
        members = sorted(ids.values(), key=lambda d: (-len(d["pubs"]), d["key"]))
        ref = members[0]
        head = clusters.get(ref["key"], ref["key"])
        for d in members[1:]:
            d["rule"] = "1" if clusters.get(d["key"], d["key"]) == head else "0"
            if ref["orcid"] and d["orcid"]:
                d["rule_basis"] = "orcid"
            elif ref["inst"] & d["inst"]:
                d["rule_basis"] = "institution"
            else:
                d["rule_basis"] = "none"
        ref["rule"], ref["rule_basis"] = "", "reference"
        groups.append({"full_name": name, "members": members})
    return groups


def build_author_workbook(path: Path, groups: list[dict]) -> None:
    cols = AUTHOR_COLS
    rows = []
    for gi, g in enumerate(groups, 1):
        for j, d in enumerate(g["members"]):
            yrs = sorted(d["years"])
            pubs = sorted(d["pubs"], key=lambda p: (p.year or 0, p.id))
            rows.append([
                gi, d["names"].most_common(1)[0][0], d["key"], len(d["pubs"]),
                f"{yrs[0]}-{yrs[-1]}" if len(yrs) > 1 else (str(yrs[0]) if yrs else ""),
                "; ".join(sorted(d["orcid"])), "\n".join(sorted(d["inst"])),
                "; ".join(n for n, _ in d["co"].most_common(5)),
                "\n".join(f"{p.year or '-'} | {p.journal or '-'} | {p.title}" for p in pubs[:3]),
                "\n".join(p.url for p in pubs[:3] if p.url),
                "referans" if j == 0 else None, None,
            ])
    wb = Workbook()
    ws = wb.active
    ws.title = "Kodlama"
    _fill_sheet(ws, cols, rows, height=75)
    last = len(rows) + 1
    names = [c[0] for c in cols]
    L = get_column_letter(names.index(AUTHOR_SAME) + 1)
    dv = DataValidation(type="list", formula1='"1,0,NA,referans"', allow_blank=True,
                        errorTitle="Geçersiz kod", error="1, 0 veya NA girin.")
    ws.add_data_validation(dv)
    dv.add(f"{L}2:{L}{last}")
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{last}"
    # Gruplar arasında okunabilirlik için dönüşümlü gölge
    shade = PatternFill("solid", fgColor="F2F2F2")
    for r, vals in enumerate(rows, 2):
        if vals[0] % 2 == 0:
            for c in range(1, names.index(AUTHOR_SAME) + 1):
                ws.cell(row=r, column=c).fill = shade

    g = wb.create_sheet("Yönerge", 0)
    g.column_dimensions["A"].width = 26
    g.column_dimensions["B"].width = 100
    _write_rows(g, [
        ("TR Dizin yazar kimliği: hüküm tablosu", None),
        ("Grup sayısı", f"{len(groups)} grup, {len(rows)} kimlik"),
        (None, None),
        ("Amaç", "TR Dizin aynı tam ada sahip yazarlara farklı yazar kimlikleri verebilir. Bu bir profilin "
                 "bölünmesi (aynı kişi) ya da gerçek ad benzerliği (farklı kişiler) olabilir. Tablo, aracın "
                 "kimliğe dayalı yazar sayımlarının ne kadar şiştiğini ölçmek için kullanılır."),
        ("Nasıl kodlanır", "Her grupta ilk satır (en çok yayını olan kimlik) referanstır. Diğer her satır için "
                           "bu kimliğin referansla AYNI KİŞİ olup olmadığını kodlayın."),
        ("1 / 0 / NA", "1 = aynı kişi; 0 = farklı kişiler; NA = elinizdeki bilgiyle karar verilemiyor."),
        ("Kanıtlar", "ORCID, kurum, ortak yazarlar, yayın konusu, dergi ve yıllar. Gerekirse 'Bağlantılar' "
                     "sütunundaki TR Dizin sayfalarını ya da yazarın ORCID / kurum sayfasını açın. Farklı ORCID "
                     "kimlikleri genellikle farklı kişilere işaret eder; kurum değişikliği tek başına farklı kişi "
                     "anlamına gelmez."),
        ("Not", "Kararınızın dayanağını kısaca yazın (ör. 'aynı ORCID', 'farklı alan ve kurum')."),
        ("Bağımsızlık", "Kodlamayı tek başınıza yapın; ikinci kodlayıcı aynı tabloyu ayrıca kodlayabilir."),
    ])
    wb.active = wb.sheetnames.index("Kodlama")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---- akış --------------------------------------------------------------------------


def run(db: Path, results_dir: Path, coding_dir: Path, n: int = SAMPLE_N, seed: int = SEED,
        stamp: str | None = None, force: bool = False, dataset: str = TARGET, scope: str = "fiziksel_aktivite",
        doc_types: tuple[str, ...] | None = None, max_year: int | None = None, d1_n: int | None = None,
        k2_n: int | None = None, authors: bool = False, frozen_dir: Path | None = None) -> dict:
    if not db.exists():
        sys.exit(f"Veritabanı bulunamadı: {db}")
    if scope not in SCOPES:
        sys.exit(f"Bilinmeyen kapsam: {scope} (seçenekler: {', '.join(SCOPES)})")
    name = slug(dataset)
    stamp = stamp or datetime.now().strftime("%Y%m%d")
    frozen = freeze(db, stamp, frozen_dir, name)
    digest = sha256(frozen)
    with Store(frozen, readonly=True) as store:
        if dataset not in {d["name"] for d in store.list_datasets()}:
            sys.exit(f"'{dataset}' veri seti yok: {frozen}")
        all_pubs = {p.id: p for p in store.publications(dataset)}
        raws = {str(r.get("id")): r for r in store.raw_records(dataset)}
    if not all_pubs:
        sys.exit(f"'{dataset}' veri seti boş: {frozen}")

    def in_scope(p: Publication) -> bool:
        if doc_types and (p.document_type or "") not in doc_types:
            return False
        return not (max_year and p.year and p.year > max_year)

    pubs = {i: p for i, p in all_pubs.items() if in_scope(p)}
    ids = sorted(pubs)
    rng = random.Random(seed)
    full = n <= 0 or n >= len(ids)
    sample = rng.sample(ids, len(ids) if n <= 0 else min(n, len(ids)))
    selection = (f"veri setinin tamamı, tohum {seed} ile karıştırılmış sıra" if full
                 else f"rastgele örneklem, tohum {seed}")
    k2_ids = sample[:k2_n] if k2_n else sample
    d1_ids = random.Random(seed + 1).sample(ids, min(d1_n, len(ids))) if d1_n else []

    results_dir.mkdir(parents=True, exist_ok=True)
    with open(results_dir / f"{name}_ids.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["id", "year", "doi", "journal", "in_sample", "document_type", "in_scope", "in_k2", "in_d1"])
        s, s2, sd = set(sample), set(k2_ids), set(d1_ids)
        for i in sorted(all_pubs):
            p = all_pubs[i]
            w.writerow([i, p.year, p.doi or "", p.journal or "", int(i in s), p.document_type or "",
                        int(i in pubs), int(i in s2), int(i in sd)])
    info = {"frozen_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "frozen_db": frozen.name, "sha256": digest, "dataset": dataset, "scope": scope,
            "doc_types": list(doc_types) if doc_types else None, "max_year": max_year,
            "n_dataset": len(all_pubs), "n_records": len(ids),
            "excluded_by_type": dict(Counter(p.document_type or "" for i, p in all_pubs.items() if i not in pubs)),
            "sample_seed": seed, "sample_n": len(sample), "full_set": full, "sample_ids": sample,
            "k2_n": len(k2_ids), "d1_seed": seed + 1 if d1_n else None, "d1_n": len(d1_ids), "d1_ids": d1_ids}

    jobs = [("K1", d1_n is None, True, sample), ("K2", False, True, k2_ids)]
    if d1_n:
        jobs.append(("D1", True, False, d1_ids))
    written, kept = [], []
    for coder, with_d1, relevance, chosen in jobs:
        path = coding_dir / f"kodlama_{coder}.xlsx"
        if path.exists() and not force:
            kept.append(path.name)  # başlamış bir kodlamanın üzerine yazma
            continue
        rows = [row_values(no, pubs[i], raws.get(i, {}), with_d1, relevance) for no, i in enumerate(chosen, 1)]
        sel = selection if coder != "D1" else f"rastgele örneklem, tohum {seed + 1}"
        if coder == "K2" and k2_n:
            sel = f"K1 sırasının ilk {len(chosen)} kaydı ({selection})"
        build_workbook(path, rows, coder if coder != "D1" else "D1 (alan doğruluğu)", with_d1, scope,
                       relevance, sel)
        written.append(path.name)

    if authors:
        groups = author_groups([pubs[i] for i in ids])
        with open(results_dir / "author_groups.csv", "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["group", "author_key", "n_pubs", "has_orcid", "n_institutions", "rule", "rule_basis"])
            for gi, g in enumerate(groups, 1):
                for d in g["members"]:
                    w.writerow([gi, d["key"], len(d["pubs"]), int(bool(d["orcid"])), len(d["inst"]),
                                d["rule"], d["rule_basis"]])
        info["author_groups"] = len(groups)
        info["author_keys_in_groups"] = sum(len(g["members"]) for g in groups)
        info["author_rule"] = dict(Counter(d["rule_basis"] + ":" + d["rule"] for g in groups
                                           for d in g["members"][1:]))
        path = coding_dir / "kodlama_yazar.xlsx"
        if path.exists() and not force:
            kept.append(path.name)
        else:
            build_author_workbook(path, groups)
            written.append(path.name)
    info["coding_written"], info["coding_kept"] = written, kept
    (results_dir / "freeze.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"Dondurulan: {frozen}\nSHA-256: {digest}")
    print(f"Veri seti '{dataset}': {len(all_pubs)} kayıt, kapsamda {len(ids)}"
          + (f" (dışarıda: {info['excluded_by_type']})" if info["excluded_by_type"] else ""))
    print(f"İlgililik: K1 {len(sample)}, K2 {len(k2_ids)} kayıt ({selection})"
          + (f"; D1 ayrı örneklem {len(d1_ids)}" if d1_n else ""))
    if authors:
        print(f"Yazar kimliği: {info['author_groups']} grup, {info['author_keys_in_groups']} kimlik")
    print(f"Kodlama tabloları: {coding_dir}\nÖzet: {results_dir}")
    if kept:
        print(f"Var olan kodlama dosyalarına dokunulmadı: {', '.join(kept)} (yeniden üretmek için --force)")
    return info


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/trdizin.db")
    ap.add_argument("--dataset", default=TARGET, help=f"Dondurulacak veri seti (varsayılan: {TARGET})")
    ap.add_argument("--scope", default="fiziksel_aktivite", choices=sorted(SCOPES),
                    help="Kodlama yönergesi (varsayılan: fiziksel_aktivite)")
    ap.add_argument("--doc-types", nargs="*", default=None, help="Yalnız bu yayın türleri, ör. PAPER")
    ap.add_argument("--max-year", type=int, default=None)
    ap.add_argument("--n", type=int, default=SAMPLE_N, help="İlgililik kayıt sayısı; 0 = tamamı")
    ap.add_argument("--k2-n", type=int, default=None, help="K2 yalnız K1 sırasının ilk N kaydını kodlar")
    ap.add_argument("--d1-n", type=int, default=None, help="D1 için ayrı rastgele örneklem büyüklüğü")
    ap.add_argument("--authors", action="store_true", help="Yazar kimliği hüküm tablosunu da üret")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--force", action="store_true", help="Var olan kodlama dosyalarının üzerine yaz")
    args = ap.parse_args(argv)
    stamp = datetime.now().strftime("%Y%m%d")
    name = slug(args.dataset)
    results = ROOT / "paper" / "results" / f"freeze_{name}_{stamp}"
    coding = ROOT / "paper" / "data" / "coding" / name
    run(Path(args.db), results, coding, args.n, args.seed, stamp, args.force, args.dataset, args.scope,
        tuple(args.doc_types) if args.doc_types else None, args.max_year, args.d1_n, args.k2_n, args.authors,
        ROOT / "paper" / "data")


if __name__ == "__main__":
    main()
