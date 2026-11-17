"""Atıf temelli duyarlılık denetimi: ilgili makalelerle atıf yoluyla bağlantılı ama sette olmayan kayıtlar.

Göreli duyarlılık, yöntemin kendi bulduğu sete göre ölçülür. Bu betik setten bağımsız bir aday havuzu kurar:
926 ilgili makalenin kaynakçasında TR Dizin'in bağladığı ve 926 ilgili makaleye atıf veren TR Dizin kayıtlarından
dondurulmuş sette olmayanlar. Havuzdan rastgele bir örneklem çekilir, kayıtları TR Dizin'den alınır, 2025'e
kadarki dergi makaleleri V2 tanımıyla ilgililik için kodlanır ve havuzdaki kaçırılmış ilgili makale sayısı ile
atıfla bağlantılı ilgili makaleler içinde yöntemin bulduğu pay kestirilir.

Adımlar:
    python paper/scripts/16_citation_recall.py --dataset fa_alan --select          # havuz ve örneklem (burada)
    python paper/scripts/16_citation_recall.py --dataset fa_alan --fetch           # kayıtları çek (TR Dizin'e erişen bilgisayarda)
    python paper/scripts/16_citation_recall.py --dataset fa_alan --build           # kodlama tablosu
    python paper/scripts/16_citation_recall.py --dataset fa_alan --score           # kestirim

Çıktılar: paper/results/citation_recall_<veri seti>/ (havuz, örneklem, kestirim; kayıt içeriği yok),
paper/data/citation_recall_<veri seti>.db (çekilen ham kayıtlar; yeniden dağıtılmaz),
paper/data/coding/<veri seti>/kodlama_kacirilan.xlsx (var olan dosyanın üzerine yazılmaz).
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl import Workbook, load_workbook  # noqa: E402
from openpyxl.styles import Alignment, Font, PatternFill  # noqa: E402
from openpyxl.worksheet.datavalidation import DataValidation  # noqa: E402

from bibliotr.normalize import fold  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

SEED = 20261009
N_SAMPLE = 200
MAX_YEAR = 2025
CODE = "İlgili (1/0)"
COLS = [("No", 5), ("TR Dizin ID", 11), ("Yön", 12), ("Yıl", 6), ("Dergi", 24), ("Başlık", 50),
        ("Anahtar kelimeler", 34), ("Özet", 80), ("Bağlantı", 10), (CODE, 9), ("Not", 24)]


def wilson(k: int, n: int, z: float = 1.96) -> list[float] | None:
    if not n:
        return None
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return [round(c - h, 4), round(c + h, 4)]


def paths(name: str) -> dict[str, Path]:
    res = ROOT / "paper" / "results" / f"citation_recall_{name}"
    return {"results": res, "pool": res / "pool.csv", "sample": res / "sample.txt",
            "db": ROOT / "paper" / "data" / f"citation_recall_{name}.db",
            "sheet": ROOT / "paper" / "data" / "coding" / name / "kodlama_kacirilan.xlsx",
            "summary": res / "summary.json"}


def load_set(db: Path, dataset: str, relevant: Path):
    keep = {ln.strip() for ln in relevant.read_text(encoding="utf-8").splitlines() if ln.strip()}
    with Store(db, readonly=True) as s:
        allp = {p.id: p for p in s.publications(dataset)}
    return allp, [allp[i] for i in sorted(keep)]


def pool(allp: dict, rel: list) -> list[dict]:
    """Sette olmayan, ilgili makalelerle atıf yoluyla bağlantılı TR Dizin kayıtları."""
    back: dict[str, int] = {}
    fwd: dict[str, int] = {}
    for p in rel:
        for r in p.references:
            t = str(r.target_id) if r.target_id else None
            if t and t not in allp:
                back[t] = back.get(t, 0) + 1
        for c in p.cited_by:
            c = str(c)
            if c not in allp:
                fwd[c] = fwd.get(c, 0) + 1
    out = []
    for k in sorted(set(back) | set(fwd), key=int):
        d = "both" if k in back and k in fwd else ("cited" if k in back else "citing")
        out.append({"id": k, "direction": d, "cited_by_set": back.get(k, 0), "citing_set": fwd.get(k, 0)})
    return out


def linked_inside(rel: list) -> int:
    """İlgili makalelerden, başka bir ilgili makaleye atıf veren ya da ondan atıf alanların sayısı."""
    ids = {p.id for p in rel}
    linked = set()
    for p in rel:
        for r in p.references:
            t = str(r.target_id) if r.target_id else None
            if t in ids and t != p.id:
                linked |= {p.id, t}
        for c in p.cited_by:
            if str(c) in ids and str(c) != p.id:
                linked |= {p.id, str(c)}
    return len(linked)


def forms(name: str) -> list[str]:
    log = sorted((ROOT / "paper" / "results").glob("fieldharvest_*/harvest_log.json"))[-1]
    return json.loads(log.read_text(encoding="utf-8"))["queried_phrases"]


def _tokens(text: str | None) -> list[str]:
    return re.findall(r"[^\W_]+", fold(text or ""))


def has_form(p, phrases: list[str]) -> bool:
    texts = [p.title, p.title_en, p.title_tr, *p.keywords]
    for t in texts:
        a = _tokens(t)
        for ph in phrases:
            b = _tokens(ph)
            if any(a[i:i + len(b)] == b for i in range(len(a) - len(b) + 1)):
                return True
    return False


def select(name: str, db: Path, dataset: str, relevant: Path, n: int = N_SAMPLE, seed: int = SEED) -> dict:
    p = paths(name)
    allp, rel = load_set(db, dataset, relevant)
    rows = pool(allp, rel)
    p["results"].mkdir(parents=True, exist_ok=True)
    with open(p["pool"], "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["id", "direction", "cited_by_set", "citing_set"])
        w.writeheader()
        w.writerows(rows)
    sample = sorted(random.Random(seed).sample([r["id"] for r in rows], min(n, len(rows))), key=int)
    p["sample"].write_text("\n".join(sample) + "\n", encoding="utf-8")
    return {"pool": len(rows), "sample": len(sample), "seed": seed,
            "by_direction": {d: sum(r["direction"] == d for r in rows) for d in ("cited", "citing", "both")},
            "relevant_linked_inside": linked_inside(rel), "relevant": len(rel)}


def fetch(name: str, delay: float = 1.2) -> dict:
    """Örneklemdeki kayıtları TR Dizin'den çeker (yalnız TR Dizin'e erişen bilgisayarda)."""
    from bibliotr.client import TRDizinClient

    p = paths(name)
    ids = [ln.strip() for ln in p["sample"].read_text(encoding="utf-8").splitlines() if ln.strip()]
    client = TRDizinClient(min_interval=delay)
    recs, missing = [], []
    for i, rid in enumerate(ids, 1):
        try:
            rec = client.publication_by_id(rid)
        except Exception as e:  # tek bir hata tüm çekimi durdurmasın
            rec = None
            print(f"[{i}/{len(ids)}] {rid}: HATA {e}", file=sys.stderr)
        if rec:
            recs.append(rec)
        else:
            missing.append(rid)
        if i % 20 == 0:
            print(f"[{i}/{len(ids)}]", file=sys.stderr)
    with Store(p["db"]) as s:
        s.create_dataset("kacirilan")
        s.add_records("kacirilan", recs)
    return {"fetched": len(recs), "missing": missing, "db": str(p["db"])}


def build(name: str) -> dict:
    p = paths(name)
    if p["sheet"].exists():
        sys.exit(f"Tablo zaten var, dokunulmadı: {p['sheet']}")
    with Store(p["db"], readonly=True) as s:
        pubs = s.publications("kacirilan")
    direction = {r["id"]: r["direction"] for r in csv.DictReader(open(p["pool"], encoding="utf-8"))}
    phrases = forms(name)
    eligible = [x for x in pubs if (x.document_type or "").upper() == "PAPER" and x.year and x.year <= MAX_YEAR]
    with_form = [x.id for x in eligible if has_form(x, phrases)]
    code = [x for x in sorted(eligible, key=lambda x: int(x.id)) if x.id not in with_form]
    label = {"cited": "atıf alan", "citing": "atıf veren", "both": "ikisi de"}
    rows = []
    for i, x in enumerate(code, 1):
        kw = "; ".join(dict.fromkeys(x.keywords))
        titles = "\n".join(dict.fromkeys(t for t in (x.title, x.title_en, x.title_tr) if t))
        rows.append([i, x.id, label.get(direction.get(x.id), ""), x.year, x.journal or "", titles, kw,
                     (x.abstract or "")[:1500], x.url or "", None, None])
    wb = Workbook()
    g = wb.active
    g.title = "Yönerge"
    guide = [("Kaçırılan makale taraması", None),
             ("Ne", "Bu makaleler fiziksel aktivite setindeki ilgili makalelerle atıf yoluyla bağlantılı ama sette "
                    "değil. Başlıklarında ve anahtar kelimelerinde arama biçimlerimizin hiçbiri geçmiyor."),
             ("Kodlama", "V2 tanımı: insanlarda fiziksel aktivite (Caspersen vd., 1985) yayının ana konusu ya da ana "
                         "değişkenlerinden biriyse 1, değilse 0. Hayvan, cihaz ve biyokimyasal anlamda 'aktivite' "
                         "ilgisizdir. Egzersiz ve spor yalnızca fiziksel aktivite çerçevesinde ele alındığında, "
                         "sedanter davranış fiziksel aktivitenin karşıtı olarak incelendiğinde ilgilidir."),
             ("Kaynak", "Başlık, anahtar kelimeler ve özete bakın; gerekirse 'Bağlantı' ile TR Dizin sayfasını açın."),
             ("Örneklem", f"Havuzdan sabit tohumla ({SEED}) çekilen {len(pubs)} kayıttan 2025'e kadarki dergi "
                          f"makaleleri: {len(rows)} satır.")]
    for r, (a, b) in enumerate(guide, 1):
        g.cell(row=r, column=1, value=a).font = Font(bold=True, size=13 if r == 1 else 10)
        c = g.cell(row=r, column=2, value=b)
        c.alignment = Alignment(wrap_text=True, vertical="top")
    g.column_dimensions["A"].width = 14
    g.column_dimensions["B"].width = 110
    ws = wb.create_sheet("Kodlama")
    ws.append([c for c, _ in COLS])
    for i, (c, w) in enumerate(COLS, 1):
        ws.column_dimensions[ws.cell(row=1, column=i).column_letter].width = w
        h = ws.cell(row=1, column=i)
        h.font = Font(bold=True, color="FFFFFF")
        h.fill = PatternFill("solid", fgColor="4F6D8A" if c not in (CODE, "Not") else "B5651D")
        h.alignment = Alignment(wrap_text=True, vertical="center")
    for row in rows:
        ws.append(row)
    for r in range(2, len(rows) + 2):
        ws.row_dimensions[r].height = 110
        for c in range(1, len(COLS) + 1):
            ws.cell(row=r, column=c).alignment = Alignment(wrap_text=True, vertical="top")
        link = ws.cell(row=r, column=[c for c, _ in COLS].index("Bağlantı") + 1)
        if link.value:
            link.hyperlink, link.value = link.value, "Aç"
    dv = DataValidation(type="list", formula1='"1,0"', allow_blank=True)
    ws.add_data_validation(dv)
    col = ws.cell(row=1, column=[c for c, _ in COLS].index(CODE) + 1).column_letter
    dv.add(f"{col}2:{col}{len(rows) + 1}")
    ws.freeze_panes = "C2"
    wb.active = 1
    p["sheet"].parent.mkdir(parents=True, exist_ok=True)
    wb.save(p["sheet"])
    info = {"fetched": len(pubs), "eligible_articles": len(eligible), "eligible_with_form": with_form,
            "to_code": len(rows)}
    (p["results"] / "build.json").write_text(json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8")
    return info


TERMS = {"physical inactivity": r"inaktivite|inactivity",
         "physical activity": r"fiziksel aktivite|physical activit",
         "exercise": r"egzersiz|exercis",
         "sport": r"\bspor|\bsport",
         "walking or steps": r"adim say|yuruyus|step count|walking",
         "healthy lifestyle": r"saglikli yasam|healthy lifestyle|healthy life style",
         "running": r"\bkosu|\brunning"}


def vocabulary(rows: list[tuple], head: list[str], codes: list[str]) -> dict:
    """Kodlanan kayıtlarda hangi sözcüklerin geçtiği: başlık ya da anahtar kelimede, yalnız özette (betimleyici)."""
    ti, ki, ai = head.index("Başlık"), head.index("Anahtar kelimeler"), head.index("Özet")
    out = {}
    for code in ("1", "0"):
        grp = [r for r, c in zip(rows, codes) if c == code]
        block = {"records": len(grp)}
        for term, pat in TERMS.items():
            tk = [bool(re.search(pat, fold(f"{r[ti] or ''} {r[ki] or ''}"))) for r in grp]
            ab = [bool(re.search(pat, fold(str(r[ai] or "")))) for r in grp]
            block[term] = {"title_or_keyword": sum(tk), "abstract_only": sum(a and not t for t, a in zip(tk, ab))}
        out["relevant" if code == "1" else "not_relevant"] = block
    return out


def score(name: str, sel: dict) -> dict:
    p = paths(name)
    build_info = json.loads((p["results"] / "build.json").read_text(encoding="utf-8"))
    ws = load_workbook(p["sheet"], read_only=True, data_only=True)["Kodlama"]
    rows = list(ws.iter_rows(values_only=True))
    ci, di = list(rows[0]).index(CODE), list(rows[0]).index("Yön")
    codes = [None if r[ci] is None else str(r[ci]).strip().split(".")[0] for r in rows[1:]]
    if any(c not in ("0", "1") for c in codes):
        return {"complete": False, "uncoded": sum(c not in ("0", "1") for c in codes)}
    k, n = codes.count("1"), len(codes)
    sample_n, pool_n = sel["sample"], sel["pool"]
    # Havuzun 2025'e kadarki, biçim taşımayan dergi makalesi payı, örneklemdeki kodlanan satırların payıyla kestirilir.
    share = n / build_info["fetched"] if build_info["fetched"] else 0.0
    rate, rate_ci = k / n if n else 0.0, wilson(k, n)
    missed = pool_n * share * rate
    missed_ci = [round(pool_n * share * x, 1) for x in rate_ci] if rate_ci else None
    inside = sel["relevant_linked_inside"]

    def recall(m):
        return round(inside / (inside + m), 4) if inside + m else None

    return {"scored_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "complete": True,
            "pool": pool_n, "sample": sample_n, "fetched": build_info["fetched"],
            "eligible_articles": build_info["eligible_articles"], "coded": n, "relevant": k,
            "relevant_share": round(rate, 4), "relevant_share_ci95": rate_ci,
            "missed_relevant_in_pool": round(missed, 1), "missed_relevant_in_pool_ci95": missed_ci,
            "relevant_linked_inside": inside,
            "recall_among_linked": recall(missed),
            "recall_among_linked_ci95": [recall(missed_ci[1]), recall(missed_ci[0])] if missed_ci else None,
            "relevant_by_direction": {d: sum(1 for r, c in zip(rows[1:], codes) if c == "1" and r[di] == d)
                                      for d in ("atıf alan", "atıf veren", "ikisi de")},
            "vocabulary": vocabulary(rows[1:], list(rows[0]), codes)}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db", default=None, help="İlgili makalelerin veritabanı (varsayılan: en yeni yeniden dizinlenmiş kopya)")
    ap.add_argument("--relevant", default=None)
    ap.add_argument("--n", type=int, default=N_SAMPLE)
    ap.add_argument("--delay", type=float, default=1.2, help="İstekler arası bekleme, saniye (1.2 sn = dakikada 50 istek; TR Dizin limiti dakikada 60/300 istek)")
    for step in ("select", "fetch", "build", "score"):
        ap.add_argument(f"--{step}", action="store_true")
    args = ap.parse_args(argv)
    name = args.dataset.replace(" ", "_")
    p = paths(name)
    if args.select or args.score:
        db = Path(args.db) if args.db else sorted((ROOT / "paper" / "data").glob(f"{name}_frozen_*_reindex_*.db"))[-1]
        relevant = Path(args.relevant) if args.relevant else sorted(
            (ROOT / "paper" / "results").glob(f"agreement_{name}_*/relevant_ids.txt"))[-1]
    if args.select:
        sel = select(name, db, args.dataset, relevant, args.n)
        (p["results"] / "select.json").write_text(json.dumps(sel, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(sel, ensure_ascii=False))
    if args.fetch:
        print(json.dumps(fetch(name, args.delay), ensure_ascii=False))
    if args.build:
        print(json.dumps(build(name), ensure_ascii=False))
    if args.score:
        sel = json.loads((p["results"] / "select.json").read_text(encoding="utf-8"))
        res = score(name, sel)
        p["summary"].write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
