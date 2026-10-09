"""D6 doğrulaması: OpenAlex başlık eşleşmelerinin elle kontrolü.

`bibliotr openalex` DOI'si olmayan (ya da DOI ile bulunamayan) kayıtları başlık ve yılla eşleştirir.
Bu betik başlıkla eşleşen kayıtların tümünü, TR Dizin ve OpenAlex başlıkları yan yana olacak
biçimde bir Excel tablosuna yazar; kodlayıcı her satırda eşleşmenin doğru olup olmadığını işaretler.
Doldurulduktan sonra `--score` ile eşleşme kesinliği (%95 Wilson aralığıyla) hesaplanır.

Çalıştırma (API gerekmez):

    python paper/scripts/08_title_match_check.py            # tabloyu üretir (varsa dokunmaz)
    python paper/scripts/08_title_match_check.py --score    # doldurulmuş tabloyu puanlar

Girdi: OpenAlex bilgisi eklenmiş veritabanı (varsayılan `paper/data/fa_alan_frozen_*_openalex.db`,
salt okunur). OpenAlex başlığı boşsa önce `bibliotr ... openalex --dataset fa_alan --refresh`.

Çıktılar:
    paper/data/coding/<veri seti>/openalex_title_check.xlsx        kontrol tablosu (başlık içerir, depoya girmez)
    paper/results/openalex_title_check_<veri seti>_<tarih>.json    puanlama (yalnızca sayılar)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from bibliotr.normalize import fold  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

TARGET = "fa_alan"
FONT = "Arial"
COLS = [("No", 5), ("TR Dizin ID", 11), ("TR Dizin yılı", 9), ("TR Dizin başlığı", 55),
        ("TR Dizin başlığı (diğer dil)", 45), ("OpenAlex başlığı", 55), ("OpenAlex yılı", 9),
        ("Benzerlik", 9), ("OpenAlex", 12), ("Doğru eşleşme (1/0)", 11), ("Not", 28),
        ("TR Dizin dergisi", 26), ("TR Dizin ilk yazarı", 20), ("TR Dizin", 10)]
CODE_COL = "Doğru eşleşme (1/0)"


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * (p * (1 - p) / n + z * z / (4 * n * n)) ** 0.5 / den
    return round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)


def title_matches(db: Path, dataset: str = TARGET, doc_types: tuple[str, ...] | None = None,
                  max_year: int | None = None) -> list[dict]:
    with Store(db, readonly=True) as store:
        pubs = {p.id: p for p in store.publications(dataset)
                if (not doc_types or (p.document_type or "") in doc_types)
                and (not max_year or (p.year and p.year <= max_year))}
        oa = store.openalex_map()
    rows = []
    for pid, m in sorted(oa.items(), key=lambda kv: (kv[1].get("title_similarity") or 0)):
        if m.get("matched_by") != "title" or pid not in pubs:
            continue
        p = pubs[pid]
        rows.append({"id": pid, "year": p.year, "title": p.title or "", "title_en": p.title_en or "",
                     "oa_title": m.get("title") or "", "oa_year": m.get("publication_year"),
                     "similarity": m.get("title_similarity"), "oa_id": m.get("openalex_id") or "",
                     "journal": p.journal or "", "first_author": p.authors[0].name if p.authors else "",
                     "url": p.url or ""})
    return rows


def build(rows: list[dict], path: Path) -> None:
    wb = Workbook()
    ws = wb.active
    ws.title = "Kontrol"
    for i, (name, width) in enumerate(COLS, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3864")
        c.alignment = Alignment(wrap_text=True, vertical="center")
        ws.column_dimensions[c.column_letter].width = width
    fill = PatternFill("solid", fgColor="FFF2CC")
    for r, row in enumerate(rows, 2):
        vals = [r - 1, row["id"], row["year"], row["title"], row["title_en"], row["oa_title"], row["oa_year"],
                row["similarity"], row["oa_id"], None, None, row.get("journal", ""), row.get("first_author", ""),
                row.get("url", "")]
        for c, v in enumerate(vals, 1):
            cell = ws.cell(row=r, column=c, value=v)
            cell.font = Font(name=FONT, size=9)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            if c in (10, 11):
                cell.fill = fill
        link = ws.cell(row=r, column=9)
        if row["oa_id"]:
            link.hyperlink = row["oa_id"]
            link.value = "Aç"
            link.font = Font(name=FONT, size=9, color="0563C1", underline="single")
        tr = ws.cell(row=r, column=14)
        if tr.value:
            tr.hyperlink = tr.value
            tr.value = "Aç"
            tr.font = Font(name=FONT, size=9, color="0563C1", underline="single")
    dv = DataValidation(type="list", formula1='"1,0"', allow_blank=True)
    ws.add_data_validation(dv)
    dv.add(f"J2:J{len(rows) + 1}")
    ws.freeze_panes = "D2"
    note = wb.create_sheet("Yönerge", 0)
    note.column_dimensions["A"].width = 110
    for r, text in enumerate([
        "OpenAlex başlık eşleşmelerinin kontrolü",
        "Her satırda TR Dizin başlığı ile OpenAlex başlığını karşılaştırın; gerekirse 'Aç' bağlantılarıyla OpenAlex ve TR Dizin kayıtlarına bakın (dergi ve ilk yazar sağdaki sütunlarda).",
        "1 = aynı yayın; 0 = farklı yayın (ör. aynı başlıklı başka bir çalışma, bildiri ve makale sürümü, başka yıl).",
        "Kararsız kalırsanız en olası kodu yazın ve 'Not' sütununa gerekçeyi ekleyin.",
        "Satırlar benzerliğe göre artan sıralıdır: en belirsiz eşleşmeler en üstte.",
    ], 1):
        note.cell(row=r, column=1, value=text).font = Font(name=FONT, bold=r == 1, size=13 if r == 1 else 10)
    wb.active = wb.sheetnames.index("Kontrol")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)


def score(path: Path) -> dict:
    ws = load_workbook(path, data_only=True)["Kontrol"]
    head = [c.value for c in ws[1]]
    col = head.index(CODE_COL)
    codes = []
    for r in ws.iter_rows(min_row=2, values_only=True):
        v = r[col]
        if v is None or str(v).strip() == "":
            continue
        s = str(v).strip()
        if s not in ("1", "0", "1.0", "0.0"):
            raise ValueError(f"Geçersiz kod: {v!r}")
        codes.append(s[0])
    n, k = len(codes), codes.count("1")
    lo, hi = wilson(k, n)
    return {"n_rows": ws.max_row - 1, "coded": n, "correct": k,
            "precision": round(k / n, 4) if n else None, "ci95": [lo, hi]}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None)
    ap.add_argument("--dataset", default=TARGET)
    ap.add_argument("--doc-types", nargs="*", default=None, help="Yalnız bu yayın türleri, ör. PAPER")
    ap.add_argument("--max-year", type=int, default=None)
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--force", action="store_true", help="Var olan kontrol tablosunun üzerine yaz")
    args = ap.parse_args(argv)
    name = re.sub(r"[^a-z0-9]+", "_", fold(args.dataset)).strip("_")
    sheet = ROOT / "paper" / "data" / "coding" / name / "openalex_title_check.xlsx"
    if args.score:
        if not sheet.exists():
            sys.exit(f"Kontrol tablosu yok: {sheet}")
        res = {"scored_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), **score(sheet)}
        out = ROOT / "paper" / "results" / f"openalex_title_check_{name}_{datetime.now():%Y%m%d}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        p = res["precision"]
        print(f"Kodlanan {res['coded']}/{res['n_rows']}, doğru {res['correct']}"
              + (f", kesinlik %{100 * p:.1f} (%95 GA {100 * res['ci95'][0]:.1f}-{100 * res['ci95'][1]:.1f})"
                 if p is not None else "") + f"\nYazıldı: {out}")
        return
    dbs = sorted((ROOT / "paper" / "data").glob(f"{name}_frozen_*_openalex.db"))
    db = Path(args.db) if args.db else (dbs[-1] if dbs else None)
    if db is None or not db.exists():
        sys.exit("OpenAlex bilgisi eklenmiş veritabanı yok; --db verin.")
    if sheet.exists() and not args.force:
        sys.exit(f"Kontrol tablosu zaten var, dokunulmadı: {sheet} (yeniden üretmek için --force)")
    rows = title_matches(db, args.dataset, tuple(args.doc_types) if args.doc_types else None, args.max_year)
    if rows and not any(r["oa_title"] for r in rows):
        print(f"Uyarı: OpenAlex başlıkları boş. Önce `bibliotr --db <db> openalex --dataset {args.dataset} --refresh` "
              "çalıştırın; tablo yine de bağlantılarla üretildi.", file=sys.stderr)
    build(rows, sheet)
    print(f"{len(rows)} başlık eşleşmesi yazıldı: {sheet}")


if __name__ == "__main__":
    main()
