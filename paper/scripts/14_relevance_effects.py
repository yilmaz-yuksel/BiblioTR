"""V2 sonrası: arama stratejilerinin göreli duyarlılığı ve tek sorgunun göstergeleri, ilgili makalelerde.

Makalenin Tablo 4'ü (strateji duyarlılığı) ve Tablo 8'i (tek başlık sorgusu ile tam set) önce dondurulmuş
setin tamamında, ilgililik kodlamasından sonra yalnız ilgili makalelerde hesaplanır.

Tohum stratejileri: alan çekimi tohum ifadelerinin hangi kaydı getirdiğini ayrıca saklamaz. TR Dizin'in alan
araması ifadenin sözcüklerini birebir eşleştirdiği için (Bölüm 4.1) tohum kümesi, alanın gerçekten getirdiği
kayıtlar (fa_alan__title / fa_alan__keyword) içinde tohum ifadesini o alanda birebir taşıyanlardır. Betik bu
öykünmenin tam sette kayıtlı sayıları (834, 954) ve tek sorgu veri setini (487 kimlik) aynen verdiğini sınar;
vermezse durur.

    python paper/scripts/14_relevance_effects.py --dataset fa_alan \\
        --relevant paper/results/agreement_fa_alan_<tarih>/relevant_ids.txt

Çıktı: paper/results/relevance_effects_<veri seti>_<tarih>/summary.json (yalnız sayılar).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import importlib.util  # noqa: E402
from collections import Counter, defaultdict  # noqa: E402

import pandas as pd  # noqa: E402

from bibliotr import analysis, thesaurus  # noqa: E402
from bibliotr.normalize import fold  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

SEEDS = ["fiziksel aktivite", "fiziksel etkinlik", "physical activity", "physical activities"]
SINGLE = "fiziksel aktivite"            # tek başlık sorgusu ve onun veri seti
EXPECTED_FULL = {"seeds_title": 834, "seeds_title_keyword": 954, "single": 487}


def _tokens(text: str | None) -> list[str]:
    return re.findall(r"[^\W_]+", fold(text or ""))


def has_phrase(text: str | None, phrase: str) -> bool:
    a, b = _tokens(text), _tokens(phrase)
    n = len(b)
    return any(a[i:i + n] == b for i in range(len(a) - n + 1))


def in_title(p, phrases) -> bool:
    return any(has_phrase(t, s) for t in (p.title, p.title_en, p.title_tr) for s in phrases)


def in_keywords(p, phrases) -> bool:
    return any(has_phrase(k, s) for k in p.keywords for s in phrases)


def strategy_sets(pubs, title_ids: set[str], keyword_ids: set[str], single_ids: set[str]) -> dict[str, set[str]]:
    t = {p.id for p in pubs if p.id in title_ids and in_title(p, SEEDS)}
    k = {p.id for p in pubs if p.id in keyword_ids and in_keywords(p, SEEDS)}
    return {"single": {p.id for p in pubs if p.id in single_ids}, "seeds_title": t, "seeds_title_keyword": t | k,
            "all": {p.id for p in pubs}}


def recall_table(sets: dict[str, set[str]], keep: set[str] | None) -> list[dict]:
    base = sets["all"] if keep is None else sets["all"] & keep
    return [{"strategy": name, "articles": len(s & base), "relative_recall_pct": round(100 * len(s & base) / len(base), 1)}
            for name, s in sets.items()]


def _ranked(pubs, field: str) -> list[str]:
    df = analysis.top_counts(pubs, field, 100000)
    return list(df.iloc[:, 0]) if df is not None and len(df) else []


def top_comparison(a_pubs, b_pubs, field: str, top: int = 20) -> dict:
    """Tablo 8: B'nin (tam set) ve A'nın (tek sorgu) ilk `top` öğesi; ortak sayısı ve Spearman ρ.

    ρ, iki ilk-`top` listesinin birleşimi üzerinde hesaplanır; listede olmayan öğe en sona konur."""
    la, lb = _ranked(a_pubs, field), _ranked(b_pubs, field)
    ka, kb = la[:top], lb[:top]
    union = list(dict.fromkeys(kb + ka))
    worst = len(union) + max(len(la), len(lb))
    pos_a = {k: i + 1 for i, k in enumerate(la)}
    pos_b = {k: i + 1 for i, k in enumerate(lb)}
    frame = pd.DataFrame({"a": [pos_a.get(k, worst) for k in union], "b": [pos_b.get(k, worst) for k in union]})
    rho = frame["a"].rank().corr(frame["b"].rank())
    return {"common": len(set(ka) & set(kb)), "top": top, "spearman": None if pd.isna(rho) else round(float(rho), 2)}


def indicators(pubs) -> dict:
    lot = analysis.lotka(pubs)[1]
    return {"articles": len(pubs),
            "share_not_turkish_pct": round(100 * sum(1 for p in pubs if (p.language or "") != "TUR") / len(pubs), 1),
            "annual_growth_pct": analysis.annual_growth_rate(pubs),
            "lotka_mle": lot.get("MLE üs (n)")}


def single_vs_full(single, full) -> dict:
    return {"single": indicators(single), "full": indicators(full),
            "top20": {f: top_comparison(single, full, f) for f in ("journal", "keyword", "institution")}}


def _case_module():
    spec = importlib.util.spec_from_file_location("case06", ROOT / "paper" / "scripts" / "06_case_analysis.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fullname_thesaurus(pubs) -> tuple[thesaurus.Thesaurus, int]:
    """Aynı tam adı (katlanmış) paylaşan TR Dizin kimliklerini en çok yayını olan kimlik altında birleştirir.

    Bölüm 5.6'daki "tam ada göre birleştirme" sayımı; kütüphanede seçenek değildir, yalnız karşılaştırma içindir."""
    keys: dict[str, Counter] = defaultdict(Counter)
    names: dict[str, Counter] = defaultdict(Counter)
    for p in pubs:
        for a in p.authors:
            k = analysis.author_key(a)
            names[k][a.name] += 1
        for a in {analysis.author_key(a): a for a in p.authors}.values():
            keys[fold(a.name)][analysis.author_key(a)] += 1
    thes, merged = thesaurus.Thesaurus(), 0
    for ids in keys.values():
        if len(ids) < 2:
            continue
        head = sorted(ids, key=lambda k: (-ids[k], k))[0]
        for k in ids:
            if k != head:
                thes.add("author", k, head, name=names[head].most_common(1)[0][0])
                merged += 1
    return thes, merged


def fullname_effect(pubs, case) -> dict:
    thes, merged = fullname_thesaurus(pubs)
    return case.merge_effect(pubs, thes, merged, "full_name")


def lotka_fit(pubs, n_boot: int = 1000, seed: int = 2026) -> dict | None:
    """Lotka üssü ve parametrik bootstrap uyum p değeri (yazar anahtarına göre, xmin = 1)."""
    per_author = Counter(k for p in pubs for k in {analysis.author_key(a) for a in p.authors})
    return analysis.lotka_bootstrap(per_author.values(), n_boot=n_boot, seed=seed)


def per_year_language(pubs) -> list[dict]:
    rows: dict[int, dict] = {}
    for p in pubs:
        if not p.year:
            continue
        r = rows.setdefault(p.year, {"year": p.year, "turkish": 0, "english_or_other": 0})
        r["turkish" if (p.language or "") == "TUR" else "english_or_other"] += 1
    return [rows[y] for y in sorted(rows)]


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db", default=None)
    ap.add_argument("--max-year", type=int, default=2025)
    ap.add_argument("--relevant", required=True, help="05_agreement.py relevant_ids.txt")
    args = ap.parse_args(argv)
    name = re.sub(r"[^a-z0-9]+", "_", fold(args.dataset)).strip("_")
    db = Path(args.db) if args.db else sorted((ROOT / "paper" / "data").glob(f"{name}_frozen_*.db"))[-1]
    keep = {ln.strip() for ln in Path(args.relevant).read_text(encoding="utf-8").splitlines() if ln.strip()}
    with Store(db, readonly=True) as s:
        full = [p for p in s.publications(args.dataset)
                if (p.document_type or "").upper() == "PAPER" and p.year and p.year <= args.max_year]
        title_ids = {p.id for p in s.publications(f"{args.dataset}__title")}
        keyword_ids = {p.id for p in s.publications(f"{args.dataset}__keyword")}
        single_ids = {p.id for p in s.publications(SINGLE)}
    sets = strategy_sets(full, title_ids, keyword_ids, single_ids)
    got = {k: len(sets[k]) for k in EXPECTED_FULL}
    if got != EXPECTED_FULL:
        sys.exit(f"Öykünme kayıtlı sayıları vermiyor: {got} != {EXPECTED_FULL}")
    if not keep <= sets["all"]:
        sys.exit(f"İlgili listesinde sette olmayan {len(keep - sets['all'])} kimlik var")
    rel = [p for p in full if p.id in keep]
    case = _case_module()
    res = {"db": db.name, "dataset": args.dataset, "max_year": args.max_year, "relevant_file": Path(args.relevant).name,
           "emulation_check": got,
           "strategy_recall": {"all_articles": recall_table(sets, None), "relevant_articles": recall_table(sets, keep)},
           "single_vs_full": {"all_articles": single_vs_full([p for p in full if p.id in sets["single"]], full),
                              "relevant_articles": single_vs_full([p for p in rel if p.id in sets["single"]], rel)},
           "per_year_language_relevant": per_year_language(rel),
           "lotka_bootstrap": {"all_articles": lotka_fit(full), "relevant_articles": lotka_fit(rel)},
           "author_merge": {k: {"rule": case.author_merge_effect(v), "full_name": fullname_effect(v, case)}
                            for k, v in (("all_articles", full), ("relevant_articles", rel))},
           "not_relevant_by_strategy": {k: len(v - keep) for k, v in sets.items()}}
    out = ROOT / "paper" / "results" / f"relevance_effects_{name}_{datetime.now():%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)
    (out / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: res[k] for k in ("strategy_recall", "single_vs_full", "author_merge", "lotka_bootstrap",
                                               "not_relevant_by_strategy")},
                     ensure_ascii=False, indent=1))
    print(f"Yazıldı: {out / 'summary.json'}")


if __name__ == "__main__":
    main()
