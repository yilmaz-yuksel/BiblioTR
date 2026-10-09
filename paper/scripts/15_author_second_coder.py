"""V5 ikinci kodlayıcı: aynı tam adı paylaşan kimliklerden seçilmiş bir alt örneklemin bağımsız kodlanması.

İlk kodlayıcı 94 grubun tamamını (96 karşılaştırma) kodladı. Bu betik ikinci kodlayıcı için bir alt örneklem
tablosu kurar ve iki kodlamanın uyumunu puanlar.

Seçim (sabit tohum):
- zor vakalar: ORCID'i grubun ilk kimliğininkinden farklı olan ve kuralın birleştirmediği tüm kimlikler
  (`author_groups.csv` içinde rule_basis = orcid, rule = 0; karara bağlanamayan kimlik bunların arasındadır);
- rastgele: kalan karşılaştırmalardan `--random` kadar kimlik.
Her seçilen kimlik, grubunun referans (ilk) kimliğiyle birlikte gösterilir. Tablo, ilk kodlayıcının kararlarını
ve notlarını içermez; kanıt sütunları dondurulmuş veritabanından yeniden üretilir (04_freeze_and_sample.py ile
aynı işlev). Seçimin hangi kimliğin hangi tabakadan geldiğini gösteren listesi yalnızca sonuç klasörüne yazılır.

    python paper/scripts/15_author_second_coder.py --dataset fa_alan --build
    python paper/scripts/15_author_second_coder.py --dataset fa_alan --score

Çıktılar:
- paper/data/coding/<veri seti>/kodlama_yazar_K2.xlsx (var olan dosyanın üzerine yazılmaz)
- paper/results/author_second_coder_<veri seti>/selection.csv ve agreement.json
"""

from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import random
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402

from bibliotr.storage import Store  # noqa: E402

SEED = 20261008
N_RANDOM = 25


def _freeze_module():
    spec = importlib.util.spec_from_file_location("freeze04", ROOT / "paper" / "scripts" / "04_freeze_and_sample.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def select(groups_csv: Path, n_random: int = N_RANDOM, seed: int = SEED) -> list[dict]:
    """Zor vakaların tamamı ve kalanlardan rastgele `n_random` karşılaştırma (yazar anahtarına göre)."""
    with open(groups_csv, encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["rule_basis"] != "reference"]
    hard = [r for r in rows if r["rule_basis"] == "orcid" and r["rule"] == "0"]
    rest = sorted((r for r in rows if r not in hard), key=lambda r: r["author_key"])
    pick = random.Random(seed).sample(rest, min(n_random, len(rest)))
    out = [{"group": r["group"], "author_key": r["author_key"], "stratum": "orcid_conflict"} for r in hard]
    out += [{"group": r["group"], "author_key": r["author_key"], "stratum": "random"} for r in pick]
    return sorted(out, key=lambda r: (int(r["group"]), r["author_key"]))


def subset_groups(groups: list[dict], keys: set[str]) -> list[dict]:
    """Seçilen kimlikleri içeren gruplar; her grupta referans ve yalnız seçilen üyeler."""
    out = []
    for g in groups:
        members = [g["members"][0]] + [d for d in g["members"][1:] if d["key"] in keys]
        if len(members) > 1:
            out.append({"full_name": g["full_name"], "members": members})
    return out


def same_evidence(ws, first: Path) -> int:
    """Kanıt sütunlarını ilk kodlayıcının gördüğü değerlerle eşitler (karar ve not sütunları hariç).

    Normalleştirme kuralları ilk tablodan sonra değiştiği için yeniden üretilen birkaç kurum adı farklı
    çıkabilir; iki kodlayıcı aynı kanıta bakmalıdır. Döndürür: değiştirilen hücre sayısı."""
    src = load_workbook(first, read_only=True)["Kodlama"]
    rows = list(src.iter_rows(values_only=True))
    head = list(rows[0])
    ki, ci = head.index("Yazar kimliği"), head.index("Grubun 1. kimliğiyle aynı kişi (1/0/NA)")
    ev = {r[ki]: r[:ci] for r in rows[1:] if r[ki] is not None}
    changed = 0
    for row in ws.iter_rows(min_row=2):
        old = ev.get(row[ki].value)
        if old is None:
            continue
        for j in range(1, ci):
            if row[j].value != old[j]:
                row[j].value = old[j]
                changed += 1
    return changed


def build(db: Path, dataset: str, groups_csv: Path, sheet: Path, results: Path,
          n_random: int = N_RANDOM, seed: int = SEED, first: Path | None = None) -> dict:
    if sheet.exists():
        sys.exit(f"Tablo zaten var, dokunulmadı: {sheet}")
    freeze = _freeze_module()
    sel = select(groups_csv, n_random, seed)
    keys = {r["author_key"] for r in sel}
    with Store(db, readonly=True) as s:
        pubs = [p for p in s.publications(dataset)
                if (p.document_type or "").upper() == "PAPER" and p.year and p.year <= 2025]
    groups = subset_groups(freeze.author_groups(pubs), keys)
    found = {d["key"] for g in groups for d in g["members"][1:]}
    if found != keys:
        sys.exit(f"Seçilen {len(keys - found)} kimlik dondurulmuş sette bulunamadı")
    freeze.build_author_workbook(sheet, groups)
    wb = load_workbook(sheet)
    copied = same_evidence(wb["Kodlama"], first) if first and first.exists() else 0
    ws = wb["Yönerge"]
    ws["B2"] = (f"{len(groups)} grup, {len(keys)} karşılaştırma. Bu tablo, aynı tam adı paylaşan kimliklerden "
                "seçilmiş bir alt örneklemdir; her grupta yalnız referans ve seçilen kimlikler vardır. İlk "
                "kodlayıcının kararları gösterilmez; lütfen onlara bakmadan kodlayın.")
    wb.save(sheet)
    results.mkdir(parents=True, exist_ok=True)
    with open(results / "selection.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["group", "author_key", "stratum"])
        w.writeheader()
        w.writerows(sel)
    return {"groups": len(groups), "comparisons": len(keys), "evidence_cells_aligned": copied,
            "orcid_conflict": sum(r["stratum"] == "orcid_conflict" for r in sel),
            "random": sum(r["stratum"] == "random" for r in sel), "seed": seed}


def _codes(path: Path) -> dict[str, str | None]:
    ws = load_workbook(path, read_only=True, data_only=True)["Kodlama"]
    rows = list(ws.iter_rows(values_only=True))
    head = list(rows[0])
    ki, ci = head.index("Yazar kimliği"), head.index("Grubun 1. kimliğiyle aynı kişi (1/0/NA)")
    out = {}
    for r in rows[1:]:
        if r[ki] is None:
            continue
        v = None if r[ci] is None else str(r[ci]).strip().upper()
        v = v[:-2] if v and v.endswith(".0") else v
        if v == "REFERANS":
            continue
        out[str(r[ki]).strip()] = v if v in ("1", "0", "NA") else None
    return out


def kappa(pairs: list[tuple[str, str]]) -> float | None:
    n = len(pairs)
    if not n:
        return None
    cats = sorted({c for p in pairs for c in p})
    po = sum(a == b for a, b in pairs) / n
    pe = sum((sum(a == c for a, _ in pairs) / n) * (sum(b == c for _, b in pairs) / n) for c in cats)
    return None if pe == 1 else round((po - pe) / (1 - pe), 4)


def score(first: Path, second: Path, selection: Path) -> dict:
    with open(selection, encoding="utf-8", newline="") as f:
        sel = list(csv.DictReader(f))
    c1, c2 = _codes(first), _codes(second)
    rows = [(r["author_key"], r["stratum"], c1.get(r["author_key"]), c2.get(r["author_key"])) for r in sel]
    missing = [k for k, _, _, b in rows if b is None]

    def block(sub):
        both = [(a, b) for _, _, a, b in sub if a and b]
        decided = [(a, b) for a, b in both if a != "NA" and b != "NA"]
        disagree = [k for k, _, a, b in sub if a and b and a != b]
        return {"comparisons": len(sub), "coded_by_both": len(both),
                "agree": sum(a == b for a, b in both),
                "agreement_pct": round(100 * sum(a == b for a, b in both) / len(both), 1) if both else None,
                "decided_by_both": len(decided), "kappa_1_0": kappa(decided), "kappa_with_na": kappa(both),
                "disagreements": disagree}

    return {"scored_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "complete": not missing, "uncoded": missing, "all": block(rows),
            "orcid_conflict": block([r for r in rows if r[1] == "orcid_conflict"]),
            "random": block([r for r in rows if r[1] == "random"])}


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--build", action="store_true")
    ap.add_argument("--score", action="store_true")
    ap.add_argument("--random", type=int, default=N_RANDOM)
    ap.add_argument("--seed", type=int, default=SEED)
    args = ap.parse_args(argv)
    name = args.dataset.replace(" ", "_")
    coding = ROOT / "paper" / "data" / "coding" / name
    results = ROOT / "paper" / "results" / f"author_second_coder_{name}"
    groups_csv = sorted((ROOT / "paper" / "results").glob(f"freeze_{name}_*/author_groups.csv"))[-1]
    if args.build:
        db = Path(args.db) if args.db else sorted(
            p for p in (ROOT / "paper" / "data").glob(f"{name}_frozen_*.db") if p.stem.count("_") == 3)[-1]
        print(json.dumps(build(db, args.dataset, groups_csv, coding / "kodlama_yazar_K2.xlsx", results,
                               args.random, args.seed, coding / "kodlama_yazar.xlsx"), ensure_ascii=False))
    if args.score:
        res = score(coding / "kodlama_yazar.xlsx", coding / "kodlama_yazar_K2.xlsx", results / "selection.csv")
        results.mkdir(parents=True, exist_ok=True)
        (results / "agreement.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
