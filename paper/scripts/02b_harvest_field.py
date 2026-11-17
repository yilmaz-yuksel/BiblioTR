"""Adım 2b: gelişmiş (alan) aramasıyla çekim ve yinelemeli varyant keşfi.

TR Dizin'in gelişmiş araması (`(title : ( "fiziksel aktivite" ))`) tam sözcükle eşleşir: ilgisiz
kayıt getirmez ama Türkçe ekli biçimleri ("fiziksel aktivitenin", "fiziksel aktiviteye") ve
çoğulları kaçırır. Bu betik:

1. Tohum ifadeleri seçilen alanlarda (varsayılan: başlık ve anahtar kelime) VEYA ile arar.
   Alanlar arası `|` TR Dizin'de kesişim verdiği için her alan ayrı sorgulanır, sonuçlar kimliğe göre
   birleştirilir.
2. Bulunan kayıtların başlık, anahtar kelime ve özetlerinde geçen yeni biçimleri keşfeder
   (`--stems` ile verilen köklerle başlayan ifadeler) ve yalnızca yenileri sorgular.
3. Yeni biçim çıkmayana (doygunluk) ya da `--max-rounds`'a kadar tekrarlar.
4. Her kaydın sorgulanan alanda sorgu ifadelerinden birini gerçekten içerip içermediğini denetler
   (sözcüksel kesinlik; arama motoru beklenmedik davranırsa ortaya çıkar).

Kullanım (deponun kök dizininde; yalnızca --run TR Dizin'e istek atar):

    python paper/scripts/02b_harvest_field.py --dataset "fiziksel aktivite" \\
        --seeds "fiziksel aktivite" "physical activity" "physical activities" \\
        --stems "fiziksel aktivite" "physical activit" --fields title keyword --years 1900-2025
    python paper/scripts/02b_harvest_field.py ... --run

Veri setleri (veritabanında):
    <ad>__title, <ad>__keyword   alan başına sonuçlar
    <ad>                         birleşim (analizler bunun üzerinde)

Çıktılar (paper/results/fieldharvest_<tarih>/):
    variants.csv      keşfedilen biçimler: tur, biçim, kayıt sayısı, alan dağılımı, sorgulandı mı
    rounds.csv        tur başına sorgu, alan, yeni kayıt, birleşim büyüklüğü
    strategy.csv      göreli duyarlılık: yalnız tohum ifadeler / tohum + keşfedilenler, alan bazında
    harvest_log.json  parametreler, sorgular, sayılar, sözcüksel kesinlik denetimi
"""

from __future__ import annotations

import argparse
import csv
import json
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bibliotr import query as tq  # noqa: E402
from bibliotr.normalize import fold, normalize_publication  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

DEFAULT_FIELDS = ("title", "keyword")
MAX_ROUNDS = 6


def parse_years(spec: str | None) -> range | None:
    if not spec:
        return None
    lo, _, hi = spec.partition("-")
    return range(int(lo), int(hi or lo) + 1)


def in_range(rec: dict, years: range | None) -> bool:
    if years is None:
        return True
    y = normalize_publication(rec).year
    return y is not None and y in years


class FieldHarvester:
    """Alan sorgularını çalıştırır, sonuçları alan başına ve birleşik olarak tutar.

    `search(query) -> list[ham kayıt]` dışarıdan verilir (testlerde sahte istemci kullanılır).
    """

    def __init__(self, search, fields: tuple[str, ...], years: range | None = None,
                 exclude: list[str] | None = None, max_terms: int = 20, verbose: bool = False):
        self.verbose = verbose
        self.search = search
        self.fields = fields
        self.years = years
        self.exclude = exclude or []
        self.max_terms = max_terms
        self.by_field: dict[str, dict[str, dict]] = {f: {} for f in fields}
        self.phrase_hits: dict[str, dict[str, set[str]]] = {}  # biçim -> alan -> kayıt kimlikleri
        self.queries: list[dict] = []

    @property
    def union(self) -> dict[str, dict]:
        out: dict[str, dict] = {}
        for recs in self.by_field.values():
            for k, r in recs.items():
                out.setdefault(k, r)
        return out

    def run_phrases(self, phrases: list[str], round_no: int) -> list[dict]:
        rows = []
        for field in self.fields:
            for chunk_start in range(0, len(phrases), self.max_terms):
                chunk = phrases[chunk_start:chunk_start + self.max_terms]
                q = tq.phrase_query(field, chunk, self.exclude)
                if not q:
                    continue
                before = len(self.union)
                recs = [r for r in self.search(q) if in_range(r, self.years)]
                new_in_field = 0
                for r in recs:
                    k = str(r.get("id"))
                    if k not in self.by_field[field]:
                        new_in_field += 1
                    self.by_field[field].setdefault(k, r)
                    pub = normalize_publication(r)
                    for ph in chunk:
                        if tq.contains_phrase(pub, [ph], field):
                            self.phrase_hits.setdefault(fold(ph), {}).setdefault(field, set()).add(k)
                row = {"round": round_no, "field": field, "n_phrases": len(chunk), "query": q,
                       "returned": len(recs), "new_in_field": new_in_field,
                       "new_in_union": len(self.union) - before, "union": len(self.union)}
                self.queries.append(row)
                rows.append(row)
                if self.verbose:
                    print(f"  tur {round_no} {field:8} {len(recs):>5} kayıt (yeni {row['new_in_union']:>4}, "
                          f"birleşim {row['union']:>5})  {', '.join(chunk)[:70]}", flush=True)
        return rows

    def lexical_check(self, all_phrases: list[str]) -> dict:
        """Her alan sonucunda kaydın o alanda sorgu ifadelerinden birini içerip içermediği."""
        out = {}
        for field, recs in self.by_field.items():
            bad = [k for k, r in recs.items()
                   if not tq.contains_phrase(normalize_publication(r), all_phrases, field)]
            out[field] = {"records": len(recs), "without_phrase": len(bad), "examples": bad[:10]}
        return out


def probe_queries(seeds: list[str], fields: list[str]) -> list[tuple[str, str]]:
    """Sözdizimi yoklaması için (açıklama, sorgu) çiftleri."""
    a = tq._phrase(seeds[0])
    b = tq._phrase(seeds[1]) if len(seeds) > 1 else a
    f = fields[0]
    w1, w2 = a.split()[0], b.split()[0]
    out = [
        ("tek ifade", f'({f} : ( "{a}" ))'),
        ("ikinci ifade", f'({f} : ( "{b}" ))'),
        ("iki ifade VEYA, iç parantezli", f'({f} : ( ("{a}" OR "{b}") ))'),
        ("iki ifade VEYA, iç parantezsiz", f'({f} : ( "{a}" OR "{b}" ))'),
        ("iki sözcük VEYA, iç parantezli", f'({f} : ( ("{w1}" OR "{w2}") ))'),
        ("iki sözcük VEYA, iç parantezsiz", f'({f} : ( "{w1}" OR "{w2}" ))'),
    ]
    for g in fields[1:]:
        out.append((f"tek ifade, {g}", f'({g} : ( "{a}" ))'))
    return out


def harvest(search, seeds: list[str], stems: list[str], fields: tuple[str, ...], years: range | None,
            exclude: list[str] | None = None, max_rounds: int = MAX_ROUNDS, min_records: int = 1,
            max_terms: int = 20, verbose: bool = False) -> dict:
    """Tohumlardan başlayıp doygunluğa kadar varyant keşfi; sonuç sözlüğü (kayıtlar dahil)."""
    h = FieldHarvester(search, fields, years, exclude, max_terms, verbose)
    queried: dict[str, str] = {}
    variants_log: list[dict] = []
    todo = [tq._phrase(s) for s in seeds if tq._phrase(s)]
    seed_keys = {fold(s) for s in todo}
    rounds = 0
    seed_union = None
    for round_no in range(max_rounds + 1):
        if not todo:
            break
        for ph in todo:
            queried[fold(ph)] = ph
        h.run_phrases(todo, round_no)
        rounds = round_no
        if round_no == 0:
            seed_union = {f: set(recs) for f, recs in h.by_field.items()}
        pubs = [normalize_publication(r) for r in h.union.values()]
        found = tq.discover_phrase_variants(pubs, stems or seeds)
        todo = []
        for key, info in found.items():
            if key in queried or info["records"] < min_records:
                continue
            todo.append(info["form"])
            variants_log.append({"round_found": round_no, "form": info["form"], "records_seen": info["records"],
                                 "fields_seen": json.dumps(info["fields"], ensure_ascii=False)})
    saturated = not todo
    final = {f: set(recs) for f, recs in h.by_field.items()}
    union_final = set(h.union)
    strategy = []
    for f in fields:
        strategy.append({"strategy": f"seeds only, {f}", "records": len(seed_union.get(f, set())) if seed_union else 0})
        strategy.append({"strategy": f"seeds + discovered, {f}", "records": len(final[f])})
    seed_all = set().union(*seed_union.values()) if seed_union else set()
    strategy.append({"strategy": "seeds only, all fields", "records": len(seed_all)})
    strategy.append({"strategy": "seeds + discovered, all fields", "records": len(union_final)})
    for row in strategy:
        row["relative_recall_pct"] = round(100 * row["records"] / len(union_final), 1) if union_final else None
    hits = []
    for key, ph in queried.items():
        per_field = h.phrase_hits.get(key, {})
        all_ids = set().union(*per_field.values()) if per_field else set()
        hits.append({"form": ph, "seed": key in seed_keys,
                     **{f"records_{f}": len(per_field.get(f, set())) for f in fields},
                     "records_any_field": len(all_ids)})
    return {"records": h.union, "by_field": h.by_field, "queries": h.queries, "variants": variants_log,
            "phrase_hits": hits, "strategy": strategy, "rounds": rounds, "saturated": saturated,
            "queried_phrases": list(queried.values()),
            "lexical_check": h.lexical_check(list(queried.values()))}


def write_outputs(res: dict, outdir: Path, params: dict) -> None:
    outdir.mkdir(parents=True, exist_ok=True)

    def dump(rows: list[dict], name: str) -> None:
        if not rows:
            return
        with open(outdir / name, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)

    dump(res["variants"], "variants.csv")
    dump(res["queries"], "rounds.csv")
    dump(res["strategy"], "strategy.csv")
    dump(sorted(res["phrase_hits"], key=lambda r: -r["records_any_field"]), "phrase_hits.csv")
    log = {"run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "params": params,
           "python": platform.python_version(), "rounds": res["rounds"], "saturated": res["saturated"],
           "n_queries": len(res["queries"]), "records_union": len(res["records"]),
           "records_by_field": {f: len(r) for f, r in res["by_field"].items()},
           "queried_phrases": res["queried_phrases"], "lexical_check": res["lexical_check"]}
    (outdir / "harvest_log.json").write_text(json.dumps(log, ensure_ascii=False, indent=2), encoding="utf-8")


def store_results(db: Path, name: str, res: dict, params: dict) -> None:
    with Store(db) as store:
        for field, recs in res["by_field"].items():
            ds = f"{name}__{field}"
            if ds in {d["name"] for d in store.list_datasets()}:
                store.delete_dataset(ds)
            store.create_dataset(ds, " ∪ ".join(q["query"] for q in res["queries"] if q["field"] == field),
                                 params | {"field": field})
            store.add_records(ds, list(recs.values()))
        if name in {d["name"] for d in store.list_datasets()}:
            store.delete_dataset(name)
        store.create_dataset(name, " ∪ ".join(q["query"] for q in res["queries"]), params)
        store.add_records(name, list(res["records"].values()))


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", required=True, help="Birleşim veri setinin adı")
    ap.add_argument("--seeds", nargs="+", required=True, help="Tohum ifadeler (aynen aranır)")
    ap.add_argument("--stems", nargs="+", default=None,
                    help="Varyant keşfi için kök ifadeler; son sözcük önek olarak eşleşir "
                         "(\"physical activit\" -> activity, activities). Varsayılan: tohumlar")
    ap.add_argument("--fields", nargs="+", default=list(DEFAULT_FIELDS), choices=sorted(tq.SEARCH_FIELDS))
    ap.add_argument("--exclude", nargs="*", default=[], help="Dışlanacak ifadeler (alan sorgusunda -(...))")
    ap.add_argument("--years", default=None, help="Örn. 1990-2025: aralık dışındaki kayıtlar atılır")
    ap.add_argument("--split-years", action="store_true",
                    help="Sorguyu yıl yıl böl (yalnızca bir sorgu 10.000 kaydı aşıyorsa gerekir; istek sayısını artırır)")
    ap.add_argument("--max-rounds", type=int, default=MAX_ROUNDS)
    ap.add_argument("--min-records", type=int, default=1, help="Bir biçimin sorgulanması için en az kayıt sayısı")
    ap.add_argument("--max-terms", type=int, default=1,
                    help="Tek sorgudaki en çok ifade (VEYA ile). Varsayılan 1: her ifade ayrı sorgulanır; "
                         "--probe ile VEYA'nın çalıştığı görülürse artırılabilir")
    ap.add_argument("--probe", action="store_true",
                    help="Çekim yapmadan sözdizimi yoklaması: her biçimin toplam kayıt sayısını yazdırır")
    ap.add_argument("--db", default="data/trdizin.db")
    ap.add_argument("--out", default=None)
    ap.add_argument("--delay", type=float, default=1.2, help="İstekler arası bekleme, saniye (1.2 sn = dakikada 50 istek; TR Dizin limiti dakikada 60/300 istek)")
    ap.add_argument("--run", action="store_true", help="TR Dizin'e istek at (yoksa yalnızca plan)")
    ap.add_argument("--overwrite", action="store_true",
                    help="Aynı adlı veri setleri varsa üzerine yaz (varsayılan: başka bir ad istenir)")
    args = ap.parse_args(argv)

    years = parse_years(args.years)
    params = {"seeds": args.seeds, "stems": args.stems or args.seeds, "fields": args.fields,
              "exclude": args.exclude, "years": args.years, "max_rounds": args.max_rounds,
              "min_records": args.min_records}
    print("Tur 0 sorguları:")
    for f in args.fields:
        for q in tq.phrase_queries(f, args.seeds, args.exclude, args.max_terms):
            print(f"  {q}")
    if not args.run and not args.probe:
        print("\nKuru çalıştırma; sözdizimi yoklaması için --probe, çekim için --run ekleyin.")
        return

    if args.run and not args.overwrite and Path(args.db).exists():
        with Store(args.db, readonly=True) as st:
            existing = {d["name"] for d in st.list_datasets()}
        clash = sorted(existing & {args.dataset, *(f"{args.dataset}__{f}" for f in args.fields)})
        if clash:
            sys.exit(f"Bu adlarda veri seti zaten var: {', '.join(clash)}. Başka bir --dataset adı verin "
                     "ya da üzerine yazmak için --overwrite ekleyin.")

    from bibliotr.client import TRDizinClient

    client = TRDizinClient(min_interval=args.delay)
    if args.probe:
        print("\nSözdizimi yoklaması (toplam kayıt):")
        for label, q in probe_queries(args.seeds, args.fields):
            total = client.search("publication", q, "relevance-DESC", 1, 10).total
            print(f"  {total:>7}  {label:34} {q}")
        # Yıl filtresi (facet) alan sorgusuyla birlikte çalışıyor mu? (--split-years bunu kullanır)
        q0 = probe_queries(args.seeds, args.fields)[0][1]
        for y in (2022, 2023):
            total = client.search("publication", q0, "relevance-DESC", 1, 10,
                                  [("facet-publication_year", str(y))]).total
            print(f"  {total:>7}  {'tek ifade + yıl filtresi ' + str(y):34} {q0}")
        print("\nVEYA'lı sorgular 0 ya da tek ifadeden az veriyorsa varsayılan --max-terms 1 kullanın.")
        return
    yrs = list(years) if years and args.split_years else None

    def search(q: str) -> list[dict]:
        return client.harvest(q, "publication", "publicationYear-DESC", None, years=yrs)

    print("\nÇekim:")
    res = harvest(search, args.seeds, args.stems or args.seeds, tuple(args.fields), years, args.exclude,
                  args.max_rounds, args.min_records, args.max_terms, verbose=True)
    if not res["records"]:
        print("\n!! Hiç kayıt gelmedi. --probe ile sözdizimini sınayın; veritabanına yazılmadı.")
        return
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    outdir = Path(args.out or ROOT / "paper" / "results" / f"fieldharvest_{stamp}")
    write_outputs(res, outdir, params)
    store_results(Path(args.db), args.dataset, res, params)
    print(f"\n{res['rounds'] + 1} tur, {len(res['queries'])} sorgu, doygunluk: {'evet' if res['saturated'] else 'HAYIR'}")
    print(f"Birleşim: {len(res['records'])} kayıt; alan başına: "
          + ", ".join(f"{f} {len(r)}" for f, r in res["by_field"].items()))
    for row in res["strategy"]:
        print(f"  {row['strategy']:34} {row['records']:>6}  (%{row['relative_recall_pct']})")
    bad = {f: c["without_phrase"] for f, c in res["lexical_check"].items() if c["without_phrase"]}
    print("Sözcüksel denetim: " + ("tüm kayıtlar sorgu ifadesini içeriyor" if not bad else f"ifadesiz kayıt {bad}"))
    print(f"Çıktılar: {outdir}\nVeri setleri: {args.dataset}, " + ", ".join(f"{args.dataset}__{f}" for f in args.fields))


if __name__ == "__main__":
    main()
