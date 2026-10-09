"""V4: atıf bağları ve kaynak ayrıştırma için elle kodlama tablosu ve puanlama.

Tablo iki kodlama sayfası içerir (paper/data/coding/<veri seti>/kodlama_kaynakca.xlsx):

1. **Bağlar**: veri seti içindeki atıf bağlarının doğruluğu.
   - Yalnızca kaynakçadaki DOI ile bulunan bağların tamamı (TR Dizin bu bağları kurmaz).
   - TR Dizin'in kendi kurduğu bağlardan (kaynak hedef kimliği) sabit tohumla seçilen örneklem.
   Her satırda kaynakçadaki kaynak metni ve bağlanan TR Dizin kaydı yan yana; soru: aynı yayın mı?
2. **Kaynaklar**: kaynak metninden çıkarılan alanların doğruluğu. Kaynak biçimine göre tabakalı
   örneklem (Vancouver, APA, diğer); her alan için 1 / 0 / NA.

Çalıştırma (API gerekmez, dondurulmuş veritabanı salt okunur açılır):

    python paper/scripts/11_reference_coding.py --dataset fa_alan
    python paper/scripts/11_reference_coding.py --dataset fa_alan --score
    python paper/scripts/11_reference_coding.py --dataset fa_alan --recheck   # ayrıştırıcı düzeltmesinden sonra

Ayrıştırıcı kodlama örnekleminde görülen hatalara göre düzeltilirse, örneklem artık test kümesi değildir.
--recheck yalnızca çıktısı değişen hücreleri ayrı bir tabloya (kodlama_kaynakca_ek.xlsx) yazar; kodlayıcı
yeni değerleri kodlar. --score bu tablo doluysa "düzeltme sonrası" doğruluğu da verir: değişmeyen hücrelerin
ilk kodu, değişenlerin yeni kodu. Bu ikinci sayı örneklem içidir ve öyle raporlanır; asıl (dışarıda tutulan)
doğruluk düzeltme öncesi sayıdır.

Çıktılar:
    paper/data/coding/<veri seti>/kodlama_kaynakca.xlsx     (TR Dizin içeriği taşır, depoya girmez)
    paper/results/reference_check_<veri seti>_<tarih>.json  (--score; yalnızca sayılar)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from bibliotr.normalize import _APA_YEAR_RE, _VANCOUVER_YEAR_RE, Publication, fold  # noqa: E402
from bibliotr.storage import Store  # noqa: E402
from bibliotr.wos import WosOptions, reference_cr, reference_fields  # noqa: E402

SEED = 20261002  # bağlar
REF_SEED = 20261003  # kaynaklar (ayrıştırıcı geliştirmesinden sonra yeni tohum)
N_LINKS = 50
STRATA = {"vancouver": 80, "apa": 80, "other": 40}
FIELDS = [("İlk yazar", "author"), ("Yıl", "year"), ("Kaynak adı", "source"), ("Cilt", "volume"),
          ("Sayfa", "page"), ("DOI", "doi")]
LINK_CODE = "Aynı yayın (1/0)"

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
TOOL_FILL = PatternFill("solid", fgColor="E2EFDA")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(name)).strip("_") or "dataset"


def style_of(text: str) -> str:
    if _APA_YEAR_RE.search(text):
        return "apa"
    if _VANCOUVER_YEAR_RE.search(text):
        return "vancouver"
    return "other"


def wilson(k: int, n: int, z: float = 1.96) -> list[float | None]:
    if n == 0:
        return [None, None]
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return [round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)]


# ---- seçim ---------------------------------------------------------------------------


def link_rows(pubs: list[Publication], n_links: int, seed: int) -> list[dict]:
    """Yalnız DOI ile bulunan bağların tamamı + TR Dizin bağlarından örneklem."""
    ids = {p.id for p in pubs}
    by_id = {p.id: p for p in pubs}
    by_doi = {p.doi.lower(): p.id for p in pubs if p.doi}
    trdizin: dict[tuple[str, str], str] = {}
    doi_links: dict[tuple[str, str], str] = {}
    for p in pubs:
        for r in p.references:
            if r.target_id in ids and r.target_id != p.id:
                trdizin.setdefault((p.id, r.target_id), r.text)
            t = None if r.target_id else by_doi.get((r.doi or "").lower())  # TR Dizin bağı öncelikli
            if t and t != p.id:
                doi_links.setdefault((p.id, t), r.text)
    cited_by = {(c, p.id) for p in pubs for c in p.cited_by if c in ids and c != p.id}
    doi_only = sorted(k for k in doi_links if k not in trdizin and k not in cited_by)
    sample = random.Random(seed).sample(sorted(trdizin), min(n_links, len(trdizin)))
    rows = []
    for kind, keys, texts in (("DOI ile", doi_only, doi_links), ("TR Dizin", sample, trdizin)):
        for a, b in keys:
            src, dst = by_id[a], by_id[b]
            rows.append({"kind": kind, "citing_id": a, "citing_year": src.year, "citing_title": src.title or "",
                         "reference": texts[(a, b)], "cited_id": b, "cited_year": dst.year,
                         "cited_title": dst.title or "", "cited_journal": dst.journal or "",
                         "cited_authors": "; ".join(x.name for x in dst.authors),
                         "cited_doi": dst.doi or "", "cited_url": dst.url or ""})
    return rows


def is_dev(text: str) -> bool:
    """Ayrıştırıcının geliştirildiği pay: metnin MD5 özetinin 10'a bölümünden kalan 0 olanlar.

    Kodlama örneklemi bu paydan çekilmez; böylece doğruluk, ayrıştırıcının ayarlanmadığı kaynaklarda ölçülür.
    """
    key = " ".join(text.split()).lower().encode("utf-8")
    return int(hashlib.md5(key).hexdigest(), 16) % 10 == 0


def reference_rows(pubs: list[Publication], strata: dict[str, int], seed: int) -> list[dict]:
    """Kaynak biçimine göre tabakalı örneklem; aynı metin bir kez, geliştirme payı hariç."""
    pools: dict[str, list] = {k: [] for k in strata}
    seen: set[str] = set()
    for p in sorted(pubs, key=lambda x: x.id):
        for r in p.references:
            key = " ".join(r.text.split()).lower()
            if not key or key in seen or is_dev(r.text):
                continue
            seen.add(key)
            pools[style_of(r.text)].append((p, r))
    rng = random.Random(seed)
    rows = []
    opts = WosOptions(ascii=False)
    for stratum, n in strata.items():
        for p, r in rng.sample(pools[stratum], min(n, len(pools[stratum]))):
            f = reference_fields(r)
            rows.append({"stratum": stratum, "pool": len(pools[stratum]), "citing_id": p.id, "text": r.text,
                         **{key: f.get(key) or "" for _, key in FIELDS}, "cr": reference_cr(r, opts)})
    return rows


# ---- Excel ---------------------------------------------------------------------------


def _sheet(wb: Workbook, title: str, cols: list[tuple[str, int, str]], rows: list[list], height: int) -> None:
    ws = wb.create_sheet(title)
    for i, (name, width, _) in enumerate(cols, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        c.border = BORDER
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 32
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
    ws.freeze_panes = "C2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(cols))}{len(rows) + 1}"


def _validate(wb: Workbook, title: str, cols, n: int, names: list[str], options: str) -> None:
    ws = wb[title]
    dv = DataValidation(type="list", formula1=f'"{options}"', allow_blank=True,
                        errorTitle="Geçersiz kod", error=f"Şunlardan birini girin: {options}")
    ws.add_data_validation(dv)
    heads = [c[0] for c in cols]
    for name in names:
        L = get_column_letter(heads.index(name) + 1)
        dv.add(f"{L}2:{L}{n + 1}")


LINK_COLS = [("No", 5, "info"), ("Bağ türü", 10, "info"), ("Atıf yapan ID", 11, "info"), ("Atıf yapan yıl", 8, "info"),
             ("Atıf yapan başlık", 32, "info"), ("Kaynakçadaki kaynak", 60, "info"), ("Bağlanan ID", 11, "info"),
             ("Bağlanan yıl", 8, "tool"), ("Bağlanan başlık", 40, "tool"), ("Bağlanan dergi", 22, "tool"),
             ("Bağlanan yazarlar", 30, "tool"), ("Bağlanan DOI", 20, "tool"), ("Bağlantı", 10, "info"),
             (LINK_CODE, 10, "input"), ("Not", 24, "input")]
REF_COLS = ([("No", 5, "info"), ("Biçim", 10, "info"), ("Atıf yapan ID", 11, "info"), ("Kaynak metni", 70, "info")]
            + [(f"{label} (araç)", 16 if key != "source" else 26, "tool") for label, key in FIELDS]
            + [("WoS CR satırı (araç)", 40, "tool")]
            + [(f"{label} doğru", 9, "input") for label, _ in FIELDS] + [("Not", 24, "input")])


def _link_sheet(wb, links: list[dict]) -> None:
    link_vals = [[i, r["kind"], r["citing_id"], r["citing_year"], r["citing_title"], r["reference"],
                  r["cited_id"], r["cited_year"], r["cited_title"], r["cited_journal"], r["cited_authors"],
                  r["cited_doi"], r["cited_url"], None, None] for i, r in enumerate(links, 1)]
    _sheet(wb, "Bağlar", LINK_COLS, link_vals, 90)
    ws = wb["Bağlar"]
    col = [c[0] for c in LINK_COLS].index("Bağlantı") + 1
    for r in range(2, len(links) + 2):
        cell = ws.cell(row=r, column=col)
        if cell.value:
            cell.hyperlink = cell.value
            cell.value = "Aç"
            cell.font = Font(name=FONT, size=9, color="0563C1", underline="single")
    if links:
        _validate(wb, "Bağlar", LINK_COLS, len(links), [LINK_CODE], "1,0")


def build(path: Path, links: list[dict], refs: list[dict], dataset: str, seed: int,
          ref_seed: int = REF_SEED, keep_links: bool = False) -> None:
    """Kodlama tablosunu yazar. `keep_links` ile var olan dosyanın Bağlar sayfası (kodlarıyla) korunur;
    yalnızca Yönerge ve Kaynaklar sayfaları yeniden üretilir."""
    if keep_links:
        wb = load_workbook(path)
        for name in ("Yönerge", "Kaynaklar"):
            if name in wb.sheetnames:
                del wb[name]
        guide = wb.create_sheet("Yönerge", 0)
    else:
        wb = Workbook()
        guide = wb.active
        guide.title = "Yönerge"
    guide.column_dimensions["A"].width = 24
    guide.column_dimensions["B"].width = 100
    n_doi = sum(1 for r in links if r["kind"] == "DOI ile")
    strata = {}
    for r in refs:
        strata.setdefault(r["stratum"], [0, r["pool"]])[0] += 1
    rows = [
        ("V4: kaynakça ve atıf bağları", None),
        ("Veri seti", f"{dataset} (tohum {seed})"),
        (None, None),
        ("Bağlar sayfası", f"{len(links)} satır: yalnızca DOI ile bulunan {n_doi} bağın tamamı ve TR Dizin'in kurduğu "
                           f"bağlardan {len(links) - n_doi} rastgele bağ. Her satırda atıf yapan yayının kaynakçasındaki "
                           "kaynak metni ile aracın bağladığı TR Dizin kaydı yan yanadır."),
        ("Aynı yayın (1/0)", "1 = kaynak metni gerçekten bağlanan yayını gösteriyor (yazar, yıl, başlık ya da dergi "
                             "uyuşuyor); 0 = başka bir yayın. Gerekirse 'Bağlantı' ile TR Dizin sayfasını açın."),
        (None, None),
        ("Kaynaklar sayfası", f"{len(refs)} kaynak, kaynakça biçimine göre tabakalı (tohum {ref_seed}): "
                              + ", ".join(f"{k} {v[0]} (havuz {v[1]})" for k, v in strata.items())
                              + ". Ayrıştırıcının geliştirildiği kaynaklar (yaklaşık onda biri) örnekleme alınmadı. "
                              "Yeşil sütunlar aracın kaynak metninden çıkardığı alanlardır."),
        ("1 / 0 / NA", "Her alanı yalnızca o satırdaki kaynak metniyle karşılaştırın. 1 = araç değeri metindekiyle "
                       "aynı; 0 = farklı, eksik ya da metinde olduğu hâlde araç boş bırakmış; NA = alan metinde de yok."),
        ("İlk yazar", "Yalnız ilk yazarın soyadı ve ilk harfleri; Türkçe harf ve büyük-küçük harf farkı önemsiz."),
        ("Kaynak adı", "Dergide derginin adı (makale başlığı değil); kitapta kitabın adı (baskı, şehir ve yayınevi "
                       "olmadan). Kısaltma veya büyük harf farkı önemsiz. Tez, rapor, yasa, web sayfası gibi dergi ya da "
                       "kitap olmayan kaynaklarda araç boş bıraktıysa NA, bir şey yazdıysa 0."),
        ("Sayfa", "İlk sayfa (ya da makale numarası)."),
        ("WoS CR satırı", "Bilgi için: araç bu alanlardan bibliometrix'e giden satırı üretir; kodlanmaz."),
        (None, None),
        ("Renkler", "Sarı hücreleri doldurun; diğer hücreleri değiştirmeyin. Kararsız kalırsanız en olası kodu "
                    "yazıp 'Not' sütununa gerekçeyi ekleyin."),
    ]
    for r, (k, v) in enumerate(rows, 1):
        a, b = guide.cell(row=r, column=1, value=k), guide.cell(row=r, column=2, value=v)
        a.font = Font(name=FONT, bold=True, size=13 if r == 1 else 10)
        b.font = Font(name=FONT, size=10)
        b.alignment = Alignment(wrap_text=True, vertical="top")
        a.alignment = Alignment(vertical="top")

    if not keep_links:  # Bağlar korunuyorsa hiç dokunulmaz
        _link_sheet(wb, links)

    ref_vals = [[i, r["stratum"], r["citing_id"], r["text"]] + [r[key] for _, key in FIELDS] + [r["cr"]]
                + [None] * (len(FIELDS) + 1) for i, r in enumerate(refs, 1)]
    _sheet(wb, "Kaynaklar", REF_COLS, ref_vals, 75)
    _validate(wb, "Kaynaklar", REF_COLS, len(refs), [f"{label} doğru" for label, _ in FIELDS], "1,0,NA")
    wb.active = wb.sheetnames.index("Bağlar")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


# ---- puanlama ------------------------------------------------------------------------


def _code(v, allow_na: bool) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = str(v).strip().upper()
    if s in ("1", "1.0"):
        return "1"
    if s in ("0", "0.0"):
        return "0"
    if allow_na and s in ("NA", "N/A"):
        return "NA"
    raise ValueError(f"Geçersiz kod: {v!r}")


def _rows(path: Path, sheet: str) -> list[dict]:
    ws = load_workbook(path, data_only=True)[sheet]
    head = [c.value for c in ws[1]]
    return [dict(zip(head, r)) for r in ws.iter_rows(min_row=2, values_only=True) if any(v is not None for v in r)]


def _acc(codes: list[str | None]) -> dict:
    k, n0, na = codes.count("1"), codes.count("0"), codes.count("NA")
    n = k + n0
    return {"correct": k, "incorrect": n0, "na": na, "n": n, "accuracy": round(k / n, 4) if n else None,
            "ci95": wilson(k, n)}


RECHECK_COLS = [("No", 5, "info"), ("Alan", 10, "info"), ("Biçim", 9, "info"), ("Kaynak metni", 70, "info"),
                ("Önceki değer (araç)", 26, "tool"), ("Yeni değer (araç)", 26, "tool"), ("İlk kod", 7, "info"),
                ("Yeni değer doğru", 9, "input"), ("Not", 24, "input")]
FIELD_KEYS = dict(FIELDS)


def _tool_value(v) -> str | None:
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = str(v).strip()
    return s[:-2] if re.fullmatch(r"\d+\.0", s) else s


def recheck_rows(path: Path, pubs: list[Publication]) -> list[dict]:
    """Kodlanmış örneklemde ayrıştırıcı çıktısı değişen hücreler (önceki ve yeni değer)."""
    by_id = {p.id: p for p in pubs}
    out = []
    for r in _rows(path, "Kaynaklar"):
        p = by_id.get(str(r["Atıf yapan ID"]))
        ref = next((x for x in p.references if x.text == r["Kaynak metni"]), None) if p else None
        if ref is None:
            continue
        f = reference_fields(ref)
        for label, key in FIELDS:
            old, new = _tool_value(r.get(f"{label} (araç)")), _tool_value(f.get(key))
            if (old or "") != (new or ""):
                out.append({"no": r["No"], "field": label, "stratum": r["Biçim"], "text": r["Kaynak metni"],
                            "old": old, "new": new, "code": _code(r.get(f"{label} doğru"), True)})
    return out


def build_recheck(path: Path, rows: list[dict], links: list[dict] | None = None) -> None:
    wb = Workbook()
    guide = wb.active
    guide.title = "Yönerge"
    lines = [("V4 ek kodlama: ayrıştırıcı düzeltmesinden sonra değişen hücreler", None),
             ("Ne yapılır", "Her satır, kodlanmış örneklemde aracın çıktısı değişen bir hücredir. Yalnızca 'Yeni değer' "
                            "sütununu ilk kodlamadaki kurallarla değerlendirin: doğruysa 1, yanlışsa 0, kaynakta o "
                            "alan yoksa ve araç da boş bıraktıysa NA."),
             ("Neden", "Düzeltmeler bu örneklemde görülen hatalardan yapıldı. Düzeltme sonrası doğruluk bu nedenle "
                       "örneklem içi bir sayıdır; makalede asıl doğruluk düzeltme öncesi sayı olarak verilir.")]
    if links:
        lines.append(("Bağlar sayfası", "Düzeltmeden sonra yalnızca DOI ile bulunan ve ilk tabloda olmayan bağlar. "
                                        "İlk tablodaki kuralla kodlayın: kaynak bağlanan yayını gösteriyorsa 1, "
                                        "başka bir yayını gösteriyorsa 0."))
    for i, (k, v) in enumerate(lines, 1):
        a, b = guide.cell(row=i, column=1, value=k), guide.cell(row=i, column=2, value=v)
        a.font = Font(name=FONT, bold=True, size=13 if i == 1 else 10)
        b.font = Font(name=FONT, size=10)
        b.alignment = Alignment(wrap_text=True, vertical="top")
    guide.column_dimensions["A"].width = 18
    guide.column_dimensions["B"].width = 100
    vals = [[r["no"], r["field"], r["stratum"], r["text"], r["old"], r["new"], r["code"], None, None] for r in rows]
    _sheet(wb, "Değişenler", RECHECK_COLS, vals, 45)
    if rows:
        _validate(wb, "Değişenler", RECHECK_COLS, len(rows), ["Yeni değer doğru"], "1,0,NA")
    if links:
        _link_sheet(wb, links)
    wb.active = 1 if rows or not links else wb.sheetnames.index("Bağlar")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def recheck_files(sheet: Path) -> list[Path]:
    """Ek tablolar sırasıyla: kodlama_kaynakca_ek.xlsx, kodlama_kaynakca_ek2.xlsx, ..."""
    out, i = [], 1
    while True:
        f = sheet.with_name(f"kodlama_kaynakca_ek{'' if i == 1 else i}.xlsx")
        if not f.exists():
            return out
        out.append(f)
        i += 1


def recheck_codes(files: list[Path]) -> dict:
    """(No, alan, yeni değer) -> kod; sonraki tablo öncekini geçersiz kılar."""
    codes: dict = {}
    for f in files:
        for r in _rows(f, "Değişenler"):
            codes[(int(r["No"]), r["Alan"], _tool_value(r.get("Yeni değer (araç)")) or "")] = \
                _code(r.get("Yeni değer doğru"), True)
    return codes


def pending_recheck(rows: list[dict], codes: dict) -> list[dict]:
    """Değişen hücrelerden, bugünkü değeriyle henüz kodlanmamış olanlar."""
    return [r for r in rows if codes.get((int(r["no"]), r["field"], r["new"] or "")) is None]


def _link_key(a, b) -> tuple[str, str]:
    return str(a).strip(), str(b).strip()


def new_doi_links(path: Path, links_now: list[dict]) -> list[dict]:
    """Bugünkü yalnız-DOI bağlarından ilk tablonun Bağlar sayfasında olmayanlar."""
    known = {_link_key(r["Atıf yapan ID"], r["Bağlanan ID"]) for r in _rows(path, "Bağlar")}
    return [r for r in links_now
            if r["kind"] == "DOI ile" and _link_key(r["citing_id"], r["cited_id"]) not in known]


def recheck_link_codes(files: list[Path]) -> dict:
    """(atıf yapan, bağlanan) -> kod, ek tabloların Bağlar sayfalarından; sonraki tablo öncekini geçersiz kılar."""
    codes: dict = {}
    for f in files:
        if "Bağlar" in load_workbook(f, read_only=True).sheetnames:
            for r in _rows(f, "Bağlar"):
                codes[_link_key(r["Atıf yapan ID"], r["Bağlanan ID"])] = _code(r.get(LINK_CODE), False)
    return codes


def score_links_after(path: Path, links_now: list[dict], link_codes: dict) -> dict:
    """Bugünkü yalnız-DOI bağlarının doğruluğu: ilk tablodaki kod, ilk tabloda yoksa ek tablodaki kod.

    İlk tabloda olup bugün kurulmayan bağlar (düzeltmenin kaldırdıkları) `removed` altında sayılır."""
    first = {_link_key(r["Atıf yapan ID"], r["Bağlanan ID"]): _code(r.get(LINK_CODE), False)
             for r in _rows(path, "Bağlar") if r.get("Bağ türü") == "DOI ile"}
    now = [_link_key(r["citing_id"], r["cited_id"]) for r in links_now if r["kind"] == "DOI ile"]
    codes = {k: first.get(k) or link_codes.get(k) for k in now}
    missing = sorted(k for k, v in codes.items() if v is None)
    removed = sorted(set(first) - set(now))
    return {"complete": not missing, "missing": [f"{a}->{b}" for a, b in missing], "links": len(now),
            "new_links": sum(1 for k in now if k not in first),
            "removed": [{"link": f"{a}->{b}", "first_code": first[(a, b)]} for a, b in removed],
            **_acc([v for v in codes.values() if v])}


def score_after(path: Path, rows: list[dict], codes: dict) -> dict:
    """Düzeltme sonrası (örneklem içi) alan doğruluğu: değişen hücrelerde yeni kod, diğerlerinde ilk kod.

    `rows`: bugünkü ayrıştırıcıyla değişen hücreler (recheck_rows); her biri bugünkü değeriyle kodlanmış olmalı."""
    redo = {(int(r["no"]), r["field"]): codes.get((int(r["no"]), r["field"], r["new"] or "")) for r in rows}
    missing = [k for k, v in redo.items() if v is None]
    if missing:
        return {"complete": False, "missing": [f"{n} {f}" for n, f in sorted(missing)]}
    refs = _rows(path, "Kaynaklar")
    out = {"complete": True, "changed_cells": len(redo), "fields": {}, "by_stratum": {}}

    def field_codes(sub, label):
        return [redo.get((int(r["No"]), label), _code(r.get(f"{label} doğru"), True)) for r in sub]

    for label, _ in FIELDS:
        out["fields"][label] = _acc(field_codes(refs, label))
    for stratum in STRATA:
        sub = [r for r in refs if r.get("Biçim") == stratum]
        out["by_stratum"][stratum] = {label: _acc(field_codes(sub, label)) for label, _ in FIELDS}
    return out


def score(path: Path) -> dict:
    links = _rows(path, "Bağlar")
    out: dict = {"links": {}}
    for kind in ("DOI ile", "TR Dizin"):
        codes = [_code(r.get(LINK_CODE), False) for r in links if r.get("Bağ türü") == kind]
        coded = [c for c in codes if c]
        out["links"][kind] = {"rows": len(codes), **_acc(coded)}
    refs = _rows(path, "Kaynaklar")
    out["references"] = {"rows": len(refs), "fields": {}, "by_stratum": {}}
    for label, _ in FIELDS:
        codes = [_code(r.get(f"{label} doğru"), True) for r in refs]
        out["references"]["fields"][label] = _acc(codes)
    for stratum in STRATA:
        sub = [r for r in refs if r.get("Biçim") == stratum]
        out["references"]["by_stratum"][stratum] = {
            label: _acc([_code(r.get(f"{label} doğru"), True) for r in sub]) for label, _ in FIELDS}
    return out


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db", default=None, help="Varsayılan: paper/data/ altındaki en yeni <veri seti>_frozen_*.db")
    ap.add_argument("--doc-types", nargs="*", default=["PAPER"])
    ap.add_argument("--max-year", type=int, default=None)
    ap.add_argument("--links", type=int, default=N_LINKS, help="TR Dizin bağlarından örneklem büyüklüğü")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--score", action="store_true", help="Doldurulmuş tabloyu puanla")
    ap.add_argument("--ref-seed", type=int, default=REF_SEED)
    ap.add_argument("--force", action="store_true", help="Var olan tablonun üzerine yaz")
    ap.add_argument("--recheck", action="store_true",
                    help="Kodlanmış örneklemde çıktısı değişen hücreler için ek tablo (kodlama_kaynakca_ek.xlsx)")
    ap.add_argument("--refs-only", action="store_true",
                    help="Var olan tablonun Bağlar sayfasını (kodlarıyla) koru; Kaynaklar sayfasını yeniden üret")
    args = ap.parse_args(argv)
    name = slug(args.dataset)
    sheet = ROOT / "paper" / "data" / "coding" / name / "kodlama_kaynakca.xlsx"
    data_dir = ROOT / "paper" / "data"
    if args.db:
        db = Path(args.db)
    else:
        found = sorted(p for p in data_dir.glob(f"{name}_frozen_*.db") if not p.stem.endswith("_openalex"))
        db = found[-1] if found else None
    changed, codes, links_now, link_codes = [], {}, None, {}
    if (args.score or args.recheck) and sheet.exists() and db is not None and db.exists():
        with Store(db, readonly=True) as store:
            all_pubs = store.publications(args.dataset)
        changed = recheck_rows(sheet, all_pubs)
        codes = recheck_codes(recheck_files(sheet))
        sel = [p for p in all_pubs if (not args.doc_types or (p.document_type or "") in args.doc_types)
               and (not args.max_year or (p.year and p.year <= args.max_year))]
        links_now = [r for r in link_rows(sel, 0, args.seed) if r["kind"] == "DOI ile"]
        link_codes = recheck_link_codes(recheck_files(sheet))
    if args.score:
        if not sheet.exists():
            sys.exit(f"Kodlama tablosu yok: {sheet}")
        res = {"scored_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **score(sheet)}
        if recheck_files(sheet):
            res["references_after_fix_in_sample"] = score_after(sheet, changed, codes)
        if links_now is not None:
            res["doi_links_after_fix"] = score_links_after(sheet, links_now, link_codes)
        out = ROOT / "paper" / "results" / f"reference_check_{name}_{datetime.now():%Y%m%d}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        for kind, v in res["links"].items():
            acc = f"%{100 * v['accuracy']:.1f}" if v["accuracy"] is not None else "-"
            print(f"Bağ ({kind}): {v['correct']}/{v['n']} doğru {acc} (kodlanmamış {v['rows'] - v['n']})")
        for label, v in res["references"]["fields"].items():
            acc = f"%{100 * v['accuracy']:.1f}" if v["accuracy"] is not None else "-"
            print(f"  {label:10} {v['correct']:>3}/{v['n']:<3} {acc:>7}  (NA {v['na']})")
        la = res.get("doi_links_after_fix")
        if la:
            state = (f"{la['correct']}/{la['n']} doğru" if la["complete"]
                     else "eksik kod: " + ", ".join(la["missing"]))
            print(f"Bağ (DOI ile, düzeltme sonrası): {la['links']} bağ, {la['new_links']} yeni, "
                  f"{len(la['removed'])} kaldırıldı; {state}")
        after = res.get("references_after_fix_in_sample")
        if after and after["complete"]:
            print(f"Düzeltme sonrası (örneklem içi; {after['changed_cells']} hücre yeniden kodlandı):")
            for label, v in after["fields"].items():
                acc = f"%{100 * v['accuracy']:.1f}" if v["accuracy"] is not None else "-"
                print(f"  {label:10} {v['correct']:>3}/{v['n']:<3} {acc:>7}  (NA {v['na']})")
        elif after:
            print("Ek kodlama eksik: " + ", ".join(after["missing"]))
        print(f"Yazıldı: {out}")
        return
    if db is None or not db.exists():
        sys.exit("Dondurulmuş veritabanı yok; önce 04_freeze_and_sample.py çalıştırın ya da --db verin.")
    if args.recheck:
        if not sheet.exists():
            sys.exit(f"Kodlama tablosu yok: {sheet}")
        files = recheck_files(sheet)
        rows = pending_recheck(changed, codes)
        new_links = [r for r in new_doi_links(sheet, links_now or [])
                     if link_codes.get(_link_key(r["citing_id"], r["cited_id"])) is None]
        if not rows and not new_links:
            print(f"Kodlanmamış değişen hücre yok ({len(changed)} değişen hücrenin hepsi kodlu); "
                  "kodlanmamış yeni DOI bağı da yok.")
            return
        target = sheet.with_name(f"kodlama_kaynakca_ek{'' if not files else len(files) + 1}.xlsx")
        build_recheck(target, rows, new_links)
        print(f"Değişen hücre: {len(changed)}; kodlanacak: {len(rows)}; yeni DOI bağı: {len(new_links)}"
              f"\nYazıldı: {target}")
        return
    if sheet.exists() and not (args.force or args.refs_only):
        sys.exit(f"Kodlama tablosu zaten var, dokunulmadı: {sheet} (yeniden üretmek için --force; "
                 "yalnız Kaynaklar sayfası için --refs-only)")
    if args.refs_only and not sheet.exists():
        sys.exit(f"--refs-only için var olan tablo gerekir: {sheet}")
    with Store(db, readonly=True) as store:
        pubs = store.publications(args.dataset)
    if args.doc_types:
        pubs = [p for p in pubs if (p.document_type or "") in args.doc_types]
    if args.max_year:
        pubs = [p for p in pubs if p.year and p.year <= args.max_year]
    links = link_rows(pubs, args.links, args.seed)
    refs = reference_rows(pubs, STRATA, args.ref_seed)
    build(sheet, links, refs, args.dataset, args.seed, args.ref_seed, keep_links=args.refs_only)
    n_doi = sum(1 for r in links if r["kind"] == "DOI ile")
    print(f"{db.name}: {len(pubs)} yayın")
    print(f"Bağlar: {len(links)} ({n_doi} yalnız DOI ile, {len(links) - n_doi} TR Dizin örneklemi)")
    print(f"Kaynaklar: {len(refs)} (" + ", ".join(f"{k} {sum(1 for r in refs if r['stratum'] == k)}" for k in STRATA)
          + f")\nYazıldı: {sheet}")


if __name__ == "__main__":
    main()
