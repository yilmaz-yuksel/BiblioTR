"""Anahtar kelime eş anlam sözlüğü: aracın birleştirme önerileri için karar tablosu ve kararların uygulanması.

Kullanım örneğinde (Bölüm 6) Türkçe ve İngilizce biçimler ("fiziksel aktivite" / "physical activity")
ile yazım farkları ("Students" / "Student.") birleştirilmeden ayrı düğüm olur. Araç birleştirme
önerir; hangisinin aynı kavram olduğuna araştırmacı karar verir. Bu betik:

    --build   önerileri kodlama tablosuna yazar (coding/<veri seti>/kodlama_esanlam.xlsx); var olan
              tabloya dokunmaz. Her öneri tabloda tam olarak saklanır (gizli sütunlar), böylece
              uygulama adımı veri seti değişse de aynı öneriyi uygular.
    --apply   doldurulmuş tablodaki kararları sözlüğe çevirir: 1 = birleştir (isteğe bağlı hedef yazımıyla),
              0 = ayrı tut. Sözlük paper/results/thesaurus_<veri seti>_<tarih>/thesaurus.json dosyasına,
              --db-out verilirse o veritabanı kopyasına da yazılır (dondurulmuş veritabanına asla yazılmaz).

    python paper/scripts/13_keyword_thesaurus.py --dataset fa_alan --build
    python paper/scripts/13_keyword_thesaurus.py --dataset fa_alan --apply --db-out paper/data/fa_alan_thesaurus.db
    python paper/scripts/13_keyword_thesaurus.py --dataset fa_alan --export [--relevant ilgili.txt]

    --export  sözlük uygulanmış WoS düz metin dosyası (VOSviewer: Create map based on bibliographic data) ve
              aracın hesapladığı anahtar kelime ağı (VOSviewer map + network) paper/data/exports/ altına;
              birleştirme öncesi/sonrası harita istatistikleri sonuç klasörüne yazılır.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import shutil
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import networkx as nx  # noqa: E402
from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side  # noqa: E402
from openpyxl.utils import get_column_letter  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from bibliotr import analysis, export, thesaurus  # noqa: E402
from bibliotr.normalize import fold  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

FONT = "Arial"
HEADER_FILL = PatternFill("solid", fgColor="1F3864")
INPUT_FILL = PatternFill("solid", fgColor="FFF2CC")
BAND_FILL = PatternFill("solid", fgColor="EEF2F8")
THIN = Side(style="thin", color="BFBFBF")
BORDER = Border(left=THIN, right=THIN, top=THIN, bottom=THIN)
DECISION = "Birleştir (1/0)"
TARGET = "Hedef yazım (isteğe bağlı)"
COLS = [("No", 5, "info"), ("Grup", 6, "info"), ("Tür", 14, "info"), ("Hedef (yayın)", 28, "info"),
        ("Birleşecek biçim (yayın)", 30, "info"), ("Haritada", 9, "info"), ("Gerekçe", 34, "info"),
        (DECISION, 10, "input"), (TARGET, 24, "input"), ("Not", 24, "input"),
        ("_oneri", 4, "hidden"), ("_uye", 4, "hidden")]
KINDS = (("Türkçe-İngilizce", "Türkçe-İngilizce"), ("Yazım farkı", "Yazım farkı"), ("Benzer yazılış", "Benzer yazılış"),
         ("Elle", "Elle"))


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(name)).strip("_") or "dataset"


def kind_of(reason: str) -> str:
    for prefix, label in KINDS:
        if reason.startswith(prefix):
            return label
    return "Diğer"


def load_pubs(db: Path, dataset: str, doc_types: tuple[str, ...], max_year: int | None, keep: set[str] | None):
    with Store(db, readonly=True) as s:
        pubs = s.publications(dataset)
    return [p for p in pubs if (not doc_types or (p.document_type or "").upper() in doc_types)
            and (not max_year or (p.year and p.year <= max_year)) and (keep is None or p.id in keep)]


def map_keywords(pubs, min_occurrence: int = 3, top: int = 100) -> set[str]:
    """Kullanım örneğinin anahtar kelime haritasına giren biçimler (katlanmış): en az 3 yayın, en sık 100."""
    counts = Counter(fold(k) for p in pubs for k in dict.fromkeys(p.keywords))
    return {k for k, c in counts.most_common(top) if c >= min_occurrence}


def build(path: Path, pubs, sugs=None) -> int:
    sugs = thesaurus.suggest(pubs, "keyword") if sugs is None else list(sugs)
    order = {"Türkçe-İngilizce": 0, "Yazım farkı": 1, "Benzer yazılış": 2}
    sugs.sort(key=lambda s: (order.get(kind_of(s.reason), 3), -s.total, fold(s.target_label)))
    on_map = map_keywords(pubs)
    wb = Workbook()
    guide = wb.active
    guide.title = "Yönerge"
    lines = [
        ("Anahtar kelime birleştirme kararları", None),
        ("Ne yapılır", "Her satır bir çifttir: araç 'Birleşecek biçim'i 'Hedef'e katmayı öneriyor. Aynı kavramsa 1, farklı "
                       "kavramsa 0 yazın. 0 verilen çift sözlükte 'ayrı tut' olarak saklanır, bir daha önerilmez. Boş satır "
                       "karar verilmemiş sayılır ve uygulanmaz."),
        ("Grup", "Aynı hedefe önerilen çiftler aynı grup numarasını taşır. Bir gruptaki çiftlere ayrı ayrı karar verilir "
                 "(ör. 'physical activities' 1, 'Physical inactivity' 0)."),
        ("Türkçe-İngilizce", "Aynı yayınlarda birlikte geçen Türkçe ve İngilizce karşılıklar. Birleşince haritada tek düğüm olur."),
        ("Yazım farkı", "Noktalama, büyük-küçük harf, kısaltma ya da çoğul eki farkı."),
        ("Benzer yazılış", "Harf benzerliği yüksek biçimler; en dikkat isteyen grup (ör. 'obezite' / 'obesite' aynı, "
                           "'depresyon' / 'regresyon' farklı)."),
        ("Hedef yazım", "Birleşik düğümün haritada görünecek adı (ör. 'Exercise' yerine 'exercise'). Grubun herhangi bir "
                        "satırına yazmak yeter. Boş bırakılırsa 'Hedef' sütunundaki biçim kullanılır."),
        ("Zincirler", "Türkçe-İngilizce ve yazım satırları birbirine bağlanır: 'fiziksel aktivite' -> 'physical activity' ve "
                      "'fiziksel aktiviteler' -> 'fiziksel aktivite' ikisi de 1 ise üçü tek düğüm olur."),
        ("Haritada", "Evet: biçimlerden biri kullanım örneğinin anahtar kelime haritasına giriyor (en az 3 yayın, en sık 100). "
                     "Zaman kısıtlıysa önce bunlar."),
    ]
    for r, (k, v) in enumerate(lines, 1):
        a, b = guide.cell(row=r, column=1, value=k), guide.cell(row=r, column=2, value=v)
        a.font = Font(name=FONT, bold=True, size=13 if r == 1 else 10)
        b.font = Font(name=FONT, size=10)
        b.alignment = Alignment(wrap_text=True, vertical="top")
    guide.column_dimensions["A"].width = 20
    guide.column_dimensions["B"].width = 100
    ws = wb.create_sheet("Öneriler")
    for i, (name, width, _) in enumerate(COLS, 1):
        c = ws.cell(row=1, column=i, value=name)
        c.font = Font(name=FONT, bold=True, color="FFFFFF")
        c.fill = HEADER_FILL
        c.alignment = Alignment(wrap_text=True, vertical="center", horizontal="center")
        ws.column_dimensions[get_column_letter(i)].width = width
    ws.row_dimensions[1].height = 32
    n = 0
    for g, s in enumerate(sugs, 1):
        payload = json.dumps({"field": s.field, "target": s.target, "target_label": s.target_label,
                              "members": s.members, "target_count": s.target_count, "reason": s.reason,
                              "score": s.score}, ensure_ascii=False)
        for i, m in enumerate(s.members):
            n += 1
            on = {fold(s.target_label), fold(m[1])} & on_map
            vals = [n, g, kind_of(s.reason), f"{s.target_label} ({s.target_count})", f"{m[1]} ({m[2]})",
                    "Evet" if on else "", s.reason, None, None, None, payload if i == 0 else None, i]
            for c, (v, (_, _, kind)) in enumerate(zip(vals, COLS), 1):
                cell = ws.cell(row=n + 1, column=c, value=v)
                cell.font = Font(name=FONT, size=9)
                cell.alignment = Alignment(wrap_text=True, vertical="top")
                cell.border = BORDER
                if kind == "input":
                    cell.fill = INPUT_FILL
                elif g % 2 == 0:
                    cell.fill = BAND_FILL
    for c in (len(COLS) - 1, len(COLS)):
        ws.column_dimensions[get_column_letter(c)].hidden = True
    ws.freeze_panes = "F2"
    ws.auto_filter.ref = f"A1:{get_column_letter(len(COLS) - 2)}{n + 1}"
    dv = DataValidation(type="list", formula1='"1,0"', allow_blank=True, errorTitle="Geçersiz kod",
                        error="1 ya da 0 girin")
    ws.add_data_validation(dv)
    L = get_column_letter([c[0] for c in COLS].index(DECISION) + 1)
    dv.add(f"{L}2:{L}{n + 1}")
    wb.active = 1
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return n


def decisions(path: Path) -> list[tuple[thesaurus.Suggestion, list[str | None], str | None]]:
    """Grup başına (öneri, üye başına kod, hedef yazım). Kod "1", "0" ya da None (boş/geçersiz)."""
    ws = load_workbook(path, data_only=True)["Öneriler"]
    head = [c.value for c in ws[1]]
    groups: dict[int, dict] = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        r = dict(zip(head, row))
        if r["Grup"] is None:
            continue
        g = groups.setdefault(int(r["Grup"]), {"payload": None, "codes": {}, "target": None})
        if r["_oneri"]:
            g["payload"] = json.loads(r["_oneri"])
        code = None if r[DECISION] is None else str(r[DECISION]).strip().split(".")[0]
        g["codes"][int(r["_uye"])] = code if code in ("0", "1") else None
        t = str(r[TARGET]).strip() if r[TARGET] is not None else ""
        if t and not g["target"]:
            g["target"] = t
    out = []
    for _, g in sorted(groups.items()):
        d = g["payload"]
        s = thesaurus.Suggestion(d["field"], d["target"], d["target_label"],
                                 [tuple(m) for m in d["members"]], d["target_count"], d["reason"], d["score"])
        out.append((s, [g["codes"].get(i) for i in range(len(s.members))], g["target"]))
    return out


def merge_groups(rows) -> list[tuple[str, list[str]]]:
    """1 verilen çiftleri bileşenlere toplar: (hedef yazım, birleşecek yazımlar).

    Bir biçim birden çok gruba 1 ile bağlanabilir (ör. 'Kadınlar' hem 'women' hem 'Kadın' grubunda).
    Sözlüğe çift çift eklemek son kararı öncekinin yerine koyar ve düğümü böler; burada aynı bileşendeki
    bütün biçimler tek hedefte toplanır. Hedef: bileşende yazılmış hedef yazım (birden çoksa en büyük
    gruptaki), yoksa en çok yayında geçen biçim; eşitlikte hedefi olduğu grupların toplamı büyük olan."""
    parent: dict[str, str] = {}

    def find(x: str) -> str:
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    spelling: dict[str, tuple[int, str]] = {}
    chosen: dict[str, tuple[int, str]] = {}
    weight: Counter = Counter()
    for s, codes, target in rows:
        yes = [m for m, c in zip(s.members, codes) if c == "1"]
        if not yes:
            continue
        t = fold(s.target_label)
        weight[t] += s.total
        spelling[t] = max(spelling.get(t, (0, "")), (s.target_count, s.target_label))
        for m in yes:
            k = fold(m[1])
            spelling[k] = max(spelling.get(k, (0, "")), (m[2], m[1]))
            parent[find(k)] = find(t)
        if target:
            chosen[t] = max(chosen.get(t, (0, "")), (s.total, target))
    comps: dict[str, list[str]] = {}
    for k in spelling:
        comps.setdefault(find(k), []).append(k)
    out = []
    for keys in comps.values():
        picks = [chosen[k] for k in keys if k in chosen]
        best = max(keys, key=lambda k: (spelling[k][0], weight[k], k))
        label = max(picks)[1] if picks else spelling[best][1]
        out.append((label, sorted(spelling[k][1] for k in keys)))
    return sorted(out)


def decided_thesaurus(rows) -> thesaurus.Thesaurus:
    """Kararlardan sözlük (yalnız bellekte): birleştirmeler ve "ayrı tut" çiftleri."""
    thes = thesaurus.Thesaurus()
    for label, forms in merge_groups(rows):
        for f in forms:
            thes.add("keyword", f, label)
    for s, codes, _ in rows:
        no = [m for m, c in zip(s.members, codes) if c == "0"]
        if no:
            thesaurus.reject(thes, replace(s, members=no))
    return thes


def extra_suggestions(pubs, rows) -> list:
    """İlk tablonun kararları uygulandıktan sonra çıkan yeni öneriler; ilk tabloda zaten sorulan çiftler hariç."""
    asked = {(fold(s.target_label), fold(m[1])) for s, _, _ in rows for m in s.members}
    asked |= {(b, a) for a, b in asked}
    out = []
    for s in thesaurus.suggest(pubs, "keyword", decided_thesaurus(rows)):
        mem = [m for m in s.members if (fold(s.target_label), fold(m[1])) not in asked]
        if mem:
            out.append(replace(s, members=mem))
    return out


MANUAL = "kodlama_esanlam_elle.csv"


def manual_decisions(path: Path):
    """Tabloların önermediği, kodlayıcının elle eklediği birleştirmeler.

    CSV sütunları: hedef_yazim, birlesecek_yazim, not. Her satır, hedef yazımı açıkça verilmiş ve 1 ile
    kodlanmış tek üyeli bir öneri gibi işlenir; böylece tablo kararlarıyla aynı yoldan birleşir."""
    rows = []
    if not path.exists():
        return rows
    with open(path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            target, form = (r.get("hedef_yazim") or "").strip(), (r.get("birlesecek_yazim") or "").strip()
            if target and form:
                s = thesaurus.Suggestion("keyword", fold(target), target, [(fold(form), form, 0)], 0, "Elle", 1.0)
                rows.append((s, ["1"], target))
    return rows


def all_decisions(paths: list[Path]):
    rows = []
    for p in paths:
        if p.exists():
            rows += manual_decisions(p) if p.suffix == ".csv" else decisions(p)
    return rows


def apply(path: "Path | list[Path]", outdir: Path, db_out: Path | None, frozen: Path) -> dict:
    thes = thesaurus.Thesaurus()
    counts = Counter()
    rows = all_decisions(path if isinstance(path, list) else [path])
    for label, forms in merge_groups(rows):
        for f in forms:
            thes.add("keyword", f, label)
    for s, codes, _ in rows:
        no = [m for m, c in zip(s.members, codes) if c == "0"]
        if no:
            thesaurus.reject(thes, replace(s, members=no))
        for c in codes:
            counts[(kind_of(s.reason), c or "boş")] += 1
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "thesaurus.json").write_text(json.dumps(thes.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    if db_out:
        if db_out.resolve() == frozen.resolve():
            sys.exit("Dondurulmuş veritabanına yazılmaz; --db-out bir kopya olmalı.")
        if not db_out.exists():
            shutil.copy(frozen, db_out)
        with Store(db_out) as s:
            s.save_thesaurus(thes)
    (outdir / "vosviewer_thesaurus_keyword.txt").write_text(thesaurus.vosviewer_thesaurus(thes, "keyword"), encoding="utf-8")
    (outdir / "bibliometrix_synonyms_keyword.txt").write_text(thesaurus.bibliometrix_synonyms(thes, "keyword"),
                                                               encoding="utf-8")
    summary = {"pairs": {f"{k[0]}: {k[1]}": v for k, v in sorted(counts.items())},
               "keyword_entries": len(thes.entries["keyword"]), "db_out": str(db_out) if db_out else None}
    (outdir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    return summary


def map_stats(pubs, min_occurrence: int = 3, top: int = 100) -> dict:
    """Kullanım örneğinin anahtar kelime haritası ayarlarıyla (en az 3 yayın, en sık 100) ağ özeti."""
    counts = Counter(fold(k) for p in pubs for k in dict.fromkeys(fold(x) for x in p.keywords))
    g = analysis.field_network(pubs, "keyword", min_occurrence, 1, top)
    tbl = analysis.network_table(g) if g.number_of_nodes() else None
    return {"distinct_keywords": len(counts), "keywords_min_occurrence": sum(c >= min_occurrence for c in counts.values()),
            "nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
            "clusters": int(tbl["Küme"].nunique()) if tbl is not None and len(tbl) else 0,
            "top10": [[n, int(c)] for n, c in sorted(g.nodes(data="count"), key=lambda x: (-x[1], x[0]))[:10]]}


def map_label(label: str) -> str:
    """Haritada tek biçim: küçük harf (VOSviewer'ın kendi dönüşümü gibi); kısaltmalar (COVID-19, BMI) korunur."""
    return " ".join(t if sum(c.isupper() for c in t) >= 2 else t.lower() for t in label.split())


def export_files(pubs, thes, export_dir: Path, outdir: Path, name: str) -> dict:
    after = thesaurus.apply(pubs, thes)
    export_dir.mkdir(parents=True, exist_ok=True)
    (export_dir / f"{name}_thesaurus_wos.txt").write_text(export.to_wos(after), encoding="utf-8")
    g = analysis.field_network(after, "keyword", 3, 1, 100)
    vmap, vnet = export.to_vosviewer(nx.relabel_nodes(g, {n: map_label(n) for n in g.nodes}))
    (export_dir / f"{name}_thesaurus_keyword_map.txt").write_text(vmap, encoding="utf-8")
    (export_dir / f"{name}_thesaurus_keyword_network.txt").write_text(vnet, encoding="utf-8")
    res = {"publications": len(pubs), "before": map_stats(pubs), "after": map_stats(after)}
    outdir.mkdir(parents=True, exist_ok=True)
    (outdir / "map_stats.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    return res


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--doc-types", default="PAPER")
    ap.add_argument("--max-year", type=int, default=2025)
    ap.add_argument("--relevant", default=None, help="Yalnız bu kayıt kimlikleri (satır başına bir kimlik; V2 sonrası)")
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--build-extra", action="store_true",
                    help="İlk tablonun kararlarından sonra çıkan yeni öneriler için ek tablo (kodlama_esanlam_ek.xlsx)")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--db-out", default=None)
    ap.add_argument("--export", action="store_true")
    args = ap.parse_args(argv)
    name = slug(args.dataset)
    if args.db:
        db = Path(args.db)
    else:
        found = sorted(p for p in (ROOT / "paper" / "data").glob(f"{name}_frozen_*.db")
                       if not p.stem.endswith("_openalex"))
        db = found[-1] if found else None
    if db is None or not db.exists():
        sys.exit("Dondurulmuş veritabanı yok; --db verin.")
    sheet = ROOT / "paper" / "data" / "coding" / name / "kodlama_esanlam.xlsx"
    extra = sheet.with_name("kodlama_esanlam_ek.xlsx")
    keep = None
    if args.relevant:
        keep = {ln.strip() for ln in Path(args.relevant).read_text(encoding="utf-8").splitlines() if ln.strip()}
    types = tuple(t.strip().upper() for t in args.doc_types.split(",") if t.strip())
    out = ROOT / "paper" / "results" / f"thesaurus_{name}_{datetime.now():%Y%m%d}"
    if args.build:
        if sheet.exists():
            sys.exit(f"Tablo zaten var, dokunulmadı: {sheet}")
        pubs = load_pubs(db, args.dataset, types, args.max_year, keep)
        n = build(sheet, pubs)
        print(f"{len(pubs)} yayın; {n} çift yazıldı: {sheet}")
    if args.build_extra:
        if extra.exists():
            sys.exit(f"Ek tablo zaten var, dokunulmadı: {extra}")
        if not sheet.exists():
            sys.exit(f"Tablo yok: {sheet}")
        pubs = load_pubs(db, args.dataset, types, args.max_year, keep)
        rows = decisions(sheet)
        n = build(extra, thesaurus.apply(pubs, decided_thesaurus(rows)), extra_suggestions(pubs, rows))
        print(f"{len(pubs)} yayın; {n} yeni çift yazıldı: {extra}")
    if args.apply:
        if not sheet.exists():
            sys.exit(f"Tablo yok: {sheet}")
        res = apply([sheet, extra, sheet.with_name(MANUAL)], out, Path(args.db_out) if args.db_out else None, db)
        print(json.dumps(res, ensure_ascii=False, indent=2))
    if args.export:
        tj = out / "thesaurus.json"
        if not tj.exists():
            sys.exit(f"Önce --apply çalıştırın: {tj} yok")
        thes = thesaurus.Thesaurus.from_dict(json.loads(tj.read_text(encoding="utf-8")))
        pubs = load_pubs(db, args.dataset, types, args.max_year, keep)
        tag = name + ("_ilgili" if keep is not None else "")
        res = export_files(pubs, thes, ROOT / "paper" / "data" / "exports", out / tag, tag)
        print(json.dumps(res, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
