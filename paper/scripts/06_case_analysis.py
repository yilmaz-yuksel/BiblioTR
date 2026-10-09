"""Aşama 5: vaka analizi ve D5 atıf kaynakları karşılaştırması (`--dataset`, varsayılan `fa_alan`).

Dondurulmuş veri seti (`--dataset`) üzerinde aracın analiz modülünü uçtan uca çalıştırır. Her adım
ayrı zamanlanır; bir adım hata verirse diğerleri sürer ve hata raporlanır. Böylece bu çalıştırma
aynı zamanda aracın gerçek veri üzerindeki sınamasıdır (süreler makalenin performans bölümüne,
hatalar hata kaydına girer).

Çalıştırma (API gerekmez; şekiller için `pip install -e ".[paper]"`):

    python paper/scripts/06_case_analysis.py

Varsayılan veritabanı: paper/data/ altındaki en yeni `<veri seti>_frozen_*.db`.

Çıktılar:
    paper/results/case_<tarih>/summary.json        göstergeler, Lotka parametreleri, D5, adım süreleri, hatalar
    paper/results/case_<tarih>/tables/*.csv        makale tabloları (toplulaştırılmış bibliyografik veri)
    paper/results/case_<tarih>/figures/*.png|pdf   Şekil 1 yıllık yayın, Şekil 2 Lotka
    paper/data/exports/                            WoS düz metin, RIS, VOSviewer dosyaları (kayıt içeriği
                                                   taşıdığı için depoya girmez; Biblioshiny/VOSviewer sınaması için)
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from bibliotr import analysis, export  # noqa: E402
from bibliotr import thesaurus  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

TARGET = "fa_alan"
# Bibliyometrik göstergeler dergi makaleleriyle sınırlanır; TR Dizin arama sonuçları proje
# kayıtları (PROJECT) da içerir. Arama kesinliği tüm veri seti üzerinden ölçülür (04/05).
DOC_TYPES = ("PAPER",)

# Doğrulanmış varsayılan paletten (dataviz referansı), sabit sırayla; baskı için açık zemin.
SERIES_1 = "#2a78d6"  # mavi
SERIES_2 = "#eb6834"  # turuncu
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
GRID = "#e4e3df"


class Runner:
    """Adımları zamanlar, hataları toplar; bir adımın hatası diğerlerini durdurmaz."""

    def __init__(self) -> None:
        self.timings: dict[str, float] = {}
        self.errors: dict[str, str] = {}

    def step(self, name: str, fn, *args, **kwargs):
        t0 = time.perf_counter()
        try:
            return fn(*args, **kwargs)
        except Exception:  # noqa: BLE001 - hata kaydı amaçlı
            self.errors[name] = traceback.format_exc(limit=3)
            print(f"  HATA [{name}]: {traceback.format_exc(limit=1).strip().splitlines()[-1]}", file=sys.stderr)
            return None
        finally:
            self.timings[name] = round(time.perf_counter() - t0, 3)


def check_frozen(db: Path, results_root: Path) -> dict:
    """Dondurma kaydındaki SHA-256 ile veritabanını karşılaştırır (kayıt yoksa `None`)."""
    import hashlib

    for f in sorted(results_root.glob("freeze_*/freeze.json")):
        info = json.loads(f.read_text(encoding="utf-8"))
        if info.get("frozen_db") == db.name:
            h = hashlib.sha256()
            with open(db, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            return {"freeze_record": f.parent.name, "sha256_ok": h.hexdigest() == info.get("sha256")}
    return {"freeze_record": None, "sha256_ok": None}


def latest_frozen(data_dir: Path, prefix: str = TARGET) -> Path | None:
    dbs = sorted(data_dir.glob(f"{prefix}_frozen_*.db"))
    return dbs[-1] if dbs else None


def save(df: pd.DataFrame | None, path: Path) -> None:
    if df is not None:
        df.to_csv(path, index=False, encoding="utf-8-sig")


# ---- D5: atıf kaynakları -------------------------------------------------------------

# "OpenAlex (TR Dizin)" = TR Dizin'in bildirdiği sayı (sistematik olarak eksik, bkz. bulgular B3);
# "OpenAlex (direct)" = `bibliotr openalex` ile doğrudan alınan sayı (yalnızca alınmışsa gösterilir).
SOURCES = {"TR Dizin": "citation_count", "CrossRef": "citations_crossref",
           "OpenAlex (TR Dizin)": "citations_openalex", "OpenCitations": "citations_opencitations",
           "OpenAlex (direct)": "openalex_citations"}


def citation_frame(pubs) -> pd.DataFrame:
    return pd.DataFrame([{label: getattr(p, attr, None) for label, attr in SOURCES.items()} | {"id": p.id,
                         "has_doi": bool(p.doi)} for p in pubs])


def citation_comparison(pubs) -> dict:
    """Kapsam (veri olan yayın), atıf alan yayın oranı ve kaynak çiftleri arasında Spearman korelasyonu."""
    df = citation_frame(pubs)
    n = len(df)
    sources = [s for s in SOURCES if s != "OpenAlex (direct)" or df[s].notna().any()]
    coverage = []
    for s in sources:
        col = pd.to_numeric(df[s], errors="coerce")  # None içeren (ya da tümü None) sütun sayıya döner
        have = col.notna()
        coverage.append({"source": s, "records_with_data": int(have.sum()),
                         "records_with_data_pct": round(100 * have.mean(), 1) if n else None,
                         "total_citations": int(col.fillna(0).sum()),
                         "cited_records": int((col.fillna(0) > 0).sum()),
                         "mean_citations": round(float(col.mean()), 3) if have.any() else None})
    pairs = []
    names = sources
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            sub = df[[a, b]].dropna()
            # Sabit bir sütunda sıra korelasyonu tanımsızdır.
            ok = len(sub) >= 3 and sub[a].nunique() > 1 and sub[b].nunique() > 1
            rho = sub[a].rank().corr(sub[b].rank()) if ok else None
            pairs.append({"source_a": a, "source_b": b, "n": len(sub),
                          "spearman_rho": None if rho is None or pd.isna(rho) else round(float(rho), 3),
                          "a_greater": int((sub[a] > sub[b]).sum()), "equal": int((sub[a] == sub[b]).sum()),
                          "b_greater": int((sub[a] < sub[b]).sum())})
    return {"n_records": n, "with_doi": int(df["has_doi"].sum()) if n else 0,
            "coverage": coverage, "pairs": pairs}


# ---- şekiller -----------------------------------------------------------------------------


def _mpl():
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        return None
    plt.rcParams.update({
        "font.family": "sans-serif", "font.sans-serif": ["Arial", "Liberation Sans", "DejaVu Sans"],
        "font.size": 9, "axes.edgecolor": TEXT_SECONDARY, "axes.labelcolor": TEXT_PRIMARY,
        "xtick.color": TEXT_SECONDARY, "ytick.color": TEXT_SECONDARY, "axes.spines.top": False,
        "axes.spines.right": False, "axes.grid": True, "grid.color": GRID, "grid.linewidth": 0.6,
        "axes.axisbelow": True, "savefig.dpi": 300, "figure.dpi": 100,
    })
    return plt


def fig_annual(py: pd.DataFrame, out: Path, plt) -> None:
    fig, ax = plt.subplots(figsize=(6.3, 2.8))
    ax.bar(py["year"].astype(int), py["publications"], width=0.72, color=SERIES_1, zorder=2)
    ax.set_xlabel("Publication year")
    ax.set_ylabel("Publications")
    ax.grid(axis="x", visible=False)
    ax.set_xticks(py["year"].astype(int))
    ax.tick_params(axis="x", rotation=90 if len(py) > 12 else 0)
    peak = py.loc[py["publications"].idxmax()]
    ax.annotate(f"{int(peak['publications'])}", (int(peak["year"]), peak["publications"]),
                textcoords="offset points", xytext=(0, 3), ha="center", fontsize=8, color=TEXT_PRIMARY)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"fig1_annual_publications.{ext}")
    plt.close(fig)


def fig_lotka(lt: pd.DataFrame, params: dict, out: Path, plt) -> None:
    fig, ax = plt.subplots(figsize=(4.2, 3.2))
    x, obs, exp = lt["Yayın sayısı"], lt["Gözlenen oran"], lt["Beklenen oran (n=2)"]
    ax.plot(x, exp, color=SERIES_2, linewidth=2, zorder=2, label="Lotka, n = 2")
    ax.scatter(x, obs, s=36, color=SERIES_1, edgecolor="white", linewidth=1.5, zorder=3, label="Observed")
    ax.set_xscale("log")
    ax.set_yscale("log")
    from matplotlib.ticker import FuncFormatter, NullFormatter

    ax.set_xticks(list(x))
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:g}"))
    ax.xaxis.set_minor_formatter(NullFormatter())
    ax.yaxis.set_major_formatter(FuncFormatter(lambda v, _: f"{v:.3g}"))
    ax.set_xlabel("Publications per author (x)")
    ax.set_ylabel("Share of authors")
    ax.legend(frameon=False, loc="upper right")
    n_est = params.get("MLE üs (n)", params.get("Tahmini üs (n)"))
    if n_est is not None:
        how = "MLE" if "MLE üs (n)" in params else "OLS"
        ax.text(0.03, 0.04, f"Fitted n = {n_est:.2f} ({how})", transform=ax.transAxes, fontsize=8,
                color=TEXT_SECONDARY)
    fig.tight_layout()
    for ext in ("png", "pdf"):
        fig.savefig(out / f"fig2_lotka.{ext}")
    plt.close(fig)


# ---- akış ---------------------------------------------------------------------------------


def _slug(name: str) -> str:
    from bibliotr.normalize import fold

    return re.sub(r"[^a-z0-9]+", "_", fold(name)).strip("_") or "dataset"


def _author_counts(pubs) -> dict[str, int]:
    counts: dict[str, int] = {}
    for p in pubs:
        for k in dict.fromkeys(analysis.author_key(a) for a in p.authors):
            counts[k] = counts.get(k, 0) + 1
    return counts


def author_merge_effect(pubs, rule: str = "orcid+institution", top: int = 20) -> dict:
    """Kimliğe göre ve kimlik + kural ile yazar sayımının karşılaştırması (V5 "ne değişir")."""
    thes, merged = thesaurus.apply_author_rule(pubs, rule)
    return merge_effect(pubs, thes, merged, rule, top)


def merge_effect(pubs, thes, merged: int, rule: str, top: int = 20) -> dict:
    """Verilen yazar birleştirmelerinin (sözlük) sayıma, ilk `top` sıralamaya ve Lotka üssüne etkisi."""
    after_pubs = thesaurus.apply(pubs, thes)
    before, after = _author_counts(pubs), _author_counts(after_pubs)

    def ranked(c: dict[str, int]) -> list[str]:
        return [k for k, _ in sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))]

    rb, ra = ranked(before), ranked(after)
    tb, ta = rb[:top], ra[:top]
    union = list(dict.fromkeys(tb + ta))
    # Birleşen kimlik, kümesinin temsilcisi altında sayılır; temsilci dışındaki kimlik sonra sırada yoktur.
    pos_b = {k: i + 1 for i, k in enumerate(rb)}
    pos_a = {k: i + 1 for i, k in enumerate(ra)}
    worst = len(union) + max(len(rb), len(ra))
    frame = pd.DataFrame({"before": [pos_b.get(k, worst) for k in union],
                          "after": [pos_a.get(k, worst) for k in union]})
    # Spearman = sıraların Pearson korelasyonu; scipy gerektirmez (pandas'ın method="spearman" yolu ister).
    rho = frame["before"].rank().corr(frame["after"].rank()) if len(frame) > 1 else None
    lot_b = analysis.lotka(pubs)[1].get("MLE üs (n)")
    lot_a = analysis.lotka(after_pubs)[1].get("MLE üs (n)")
    return {"rule": rule, "identities_merged": merged, "authors_by_id": len(before), "authors_after_rule": len(after),
            "top_n": top, "top_overlap": len(set(tb) & set(ta)),
            "top_spearman": None if rho is None or pd.isna(rho) else round(float(rho), 4),
            "lotka_mle_by_id": lot_b, "lotka_mle_after_rule": lot_a}


def run(db: Path, outdir: Path, export_dir: Path, doc_types: tuple[str, ...] | None = DOC_TYPES,
        dataset: str = TARGET, max_year: int | None = None, author_merge: str = "none",
        keep: set[str] | None = None) -> dict:
    """`keep`: yalnız bu kayıt kimlikleri (V2 sonrası ilgili makaleler; 05_agreement.py relevant_ids.txt)."""
    if not db.exists():
        sys.exit(f"Veritabanı bulunamadı: {db}")
    tables, figures = outdir / "tables", outdir / "figures"
    for d in (tables, figures, export_dir):
        d.mkdir(parents=True, exist_ok=True)
    r = Runner()
    with Store(db, readonly=True) as store:
        pubs = r.step("load", store.publications, dataset) or []
    if not pubs:
        sys.exit(f"'{dataset}' veri seti boş: {db}")
    by_type: dict[str, int] = {}
    for p in pubs:
        by_type[p.document_type or "?"] = by_type.get(p.document_type or "?", 0) + 1
    n_all = len(pubs)
    slug = _slug(dataset)
    if doc_types:
        pubs = [p for p in pubs if (p.document_type or "").upper() in doc_types]
    if max_year:
        pubs = [p for p in pubs if p.year and p.year <= max_year]
    n_before_keep = len(pubs)
    if keep is not None:
        pubs = [p for p in pubs if p.id in keep]
        slug = f"{slug}_ilgili"
    print(f"{n_all} kayıt yüklendi ({db.name}); analize giren {len(pubs)} "
          f"(doküman türü: {', '.join(doc_types) if doc_types else 'tümü'}; dağılım {by_type})", file=sys.stderr)
    if not pubs:
        sys.exit("Süzgeçten sonra kayıt kalmadı; --all-types deneyin.")

    frozen = check_frozen(db, ROOT / "paper" / "results")
    if frozen["sha256_ok"] is False:
        print(f"UYARI: {db.name} dondurma kaydındaki SHA-256 ile uyuşmuyor; dosya değişmiş.", file=sys.stderr)
    result: dict = {"computed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "db": db.name, "frozen_check": frozen, "dataset": dataset, "max_year": max_year, "n_records": n_all, "document_types_in_dataset": by_type,
                    "document_type_filter": list(doc_types) if doc_types else None,
                    "n_publications": len(pubs),
                    "relevant_filter": None if keep is None else {"ids": len(keep), "before": n_before_keep,
                                                                  "after": len(pubs)}}
    result["author_merge_effect"] = r.step("author_merge_effect", author_merge_effect, pubs)
    result["author_merge"] = author_merge
    if author_merge != "none":
        thes, merged = thesaurus.apply_author_rule(pubs, author_merge)
        pubs = thesaurus.apply(pubs, thes)
        print(f"Yazar birleştirme kuralı '{author_merge}': {merged} kimlik birleştirildi.", file=sys.stderr)
    result["summary"] = r.step("summary", analysis.summary, pubs)

    py = r.step("per_year", analysis.per_year, pubs)
    save(py, tables / "t_per_year.csv")
    for field, n, name in (("journal", 20, "t_top_journals"), ("institution", 20, "t_top_institutions"),
                           ("city", 15, "t_top_cities"), ("keyword", 30, "t_top_keywords"),
                           ("subject", 15, "t_top_subjects"), ("language", 10, "t_languages"),
                           ("document_type", 10, "t_document_types"), ("institution_type", 10, "t_institution_types")):
        save(r.step(name, analysis.top_counts, pubs, field, n), tables / f"{name}.csv")
    save(r.step("author_metrics", analysis.author_metrics, pubs, 20), tables / "t_top_authors.csv")
    save(r.step("most_cited", analysis.most_cited, pubs, 15), tables / "t_most_cited.csv")
    save(r.step("top_references", analysis.top_references, pubs, 20), tables / "t_top_references.csv")

    lot = r.step("lotka", analysis.lotka, pubs)
    if lot:
        save(lot[0], tables / "t_lotka.csv")
        result["lotka"] = lot[1]
    br = r.step("bradford", analysis.bradford, pubs)
    save(br, tables / "t_bradford.csv")
    if br is not None and len(br):
        result["bradford_zones"] = br.groupby("Bölge").agg(journals=("Dergi", "count"),
                                                            articles=("Yayın", "sum")).reset_index().to_dict("records")

    # Ağlar: tablo + VOSviewer dosyası
    result["networks"] = {}
    for field, min_occ, top_n in (("author", 2, 100), ("institution", 2, 100), ("city", 1, 100),
                                  ("keyword", 3, 100)):
        g = r.step(f"network_{field}", analysis.field_network, pubs, field, min_occ, 1, top_n)
        if g is None:
            continue
        tbl = r.step(f"network_table_{field}", analysis.network_table, g)
        save(tbl, tables / f"t_network_{field}.csv")
        result["networks"][field] = {"nodes": g.number_of_nodes(), "edges": g.number_of_edges(),
                                     "clusters": int(tbl["Küme"].nunique()) if tbl is not None and len(tbl) else 0,
                                     "min_occurrence": min_occ}
        vos = r.step(f"vosviewer_{field}", export.to_vosviewer, g)
        if vos:
            (export_dir / f"{slug}_{field}_map.txt").write_text(vos[0], encoding="utf-8")
            (export_dir / f"{slug}_{field}_network.txt").write_text(vos[1], encoding="utf-8")
    for name, fn in (("coupling", analysis.coupling_network), ("citation", analysis.citation_network)):
        g = r.step(f"network_{name}", fn, pubs)
        if g is not None:
            result["networks"][name] = {"nodes": g.number_of_nodes(), "edges": g.number_of_edges()}
    lcs = r.step("local_citations", analysis.local_citations, pubs) or {}
    result["local_citations"] = {"records_cited_locally": sum(1 for v in lcs.values() if v),
                                 "total": int(sum(lcs.values()))}

    th = r.step("thematic_keyword", analysis.trend_by_period, pubs, "keyword", 5, 20)
    if th is not None:
        th.to_csv(tables / "t_thematic_keywords.csv", encoding="utf-8-sig")

    # D5
    result["d5_citation_sources"] = r.step("d5", citation_comparison, pubs)
    save(r.step("citation_sources", analysis.citation_sources, pubs), tables / "t_citation_sources.csv")
    if result["d5_citation_sources"]:
        pd.DataFrame(result["d5_citation_sources"]["pairs"]).to_csv(tables / "t_citation_spearman.csv",
                                                                     index=False, encoding="utf-8-sig")

    # Dışa aktarım (Biblioshiny / VOSviewer / Zotero sınaması için)
    for name, fn, fname in (("export_wos", export.to_wos, f"{slug}_wos.txt"), ("export_ris", export.to_ris, f"{slug}.ris"),
                            ("export_bibtex", export.to_bibtex, f"{slug}.bib")):
        txt = r.step(name, fn, pubs)
        if txt is not None:
            (export_dir / fname).write_text(txt, encoding="utf-8")
    xls = r.step("export_excel", export.to_excel, pubs)
    if xls is not None:
        (export_dir / f"{slug}.xlsx").write_bytes(xls)

    # Şekiller
    plt = _mpl()
    if plt is None:
        result["figures"] = "matplotlib yok: pip install -e \".[paper]\""
        print("Şekiller atlandı: matplotlib yüklü değil (pip install -e \".[paper]\").", file=sys.stderr)
    else:
        if py is not None and len(py):
            r.step("fig_annual", fig_annual, py, figures, plt)
        if lot and len(lot[0]):
            r.step("fig_lotka", fig_lotka, lot[0], lot[1], figures, plt)

    result["timings_s"] = r.timings
    result["errors"] = r.errors
    (outdir / "summary.json").write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
    _report(result, outdir, export_dir)
    return result


def _report(res: dict, outdir: Path, export_dir: Path) -> None:
    s = res.get("summary") or {}
    fc = res.get("frozen_check") or {}
    if fc.get("sha256_ok") is not None:
        print(f"\nDondurma kaydı {fc['freeze_record']}: SHA-256 {'tutuyor' if fc['sha256_ok'] else 'TUTMUYOR'}")
    if res.get("document_type_filter"):
        print(f"\n{res['n_records']} kayıttan {res['n_publications']} analize girdi "
              f"(yalnızca {', '.join(res['document_type_filter'])}; dağılım {res['document_types_in_dataset']})")
    print("\n== Özet")
    for k, v in s.items():
        print(f"  {k:34} {v}")
    if res.get("lotka"):
        print("\n== Lotka: " + ", ".join(f"{k} {v}" for k, v in res["lotka"].items()))
    if res.get("bradford_zones"):
        print("== Bradford: " + "; ".join(f"bölge {z['Bölge']}: {z['journals']} dergi, {z['articles']} yayın"
                                          for z in res["bradford_zones"]))
    if res.get("networks"):
        print("== Ağlar: " + "; ".join(f"{k} {v['nodes']} düğüm / {v['edges']} bağ" for k, v in res["networks"].items()))
    d5 = res.get("d5_citation_sources")
    if d5:
        print(f"\n== D5 atıf kaynakları ({d5['n_records']} yayın, DOI'li {d5['with_doi']})")
        for c in d5["coverage"]:
            print(f"  {c['source']:14} veri {c['records_with_data']:>4} (%{c['records_with_data_pct']}), "
                  f"atıf alan {c['cited_records']:>4}, toplam atıf {c['total_citations']}")
        for p in d5["pairs"]:
            print(f"  {p['source_a']} ~ {p['source_b']}: rho {p['spearman_rho']} (n {p['n']})")
    total = sum(res["timings_s"].values())
    print(f"\nToplam analiz süresi {total:.1f} sn; en uzun adımlar: "
          + ", ".join(f"{k} {v:.2f}" for k, v in sorted(res["timings_s"].items(), key=lambda kv: -kv[1])[:3]))
    if res["errors"]:
        print(f"\n!! {len(res['errors'])} adımda hata: {', '.join(res['errors'])} (ayrıntı summary.json)")
    else:
        print("\nTüm adımlar hatasız tamamlandı.")
    print(f"Çıktılar: {outdir}\nDışa aktarımlar: {export_dir}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None, help="Varsayılan: paper/data/ altındaki en yeni <veri seti>_frozen_*.db")
    ap.add_argument("--dataset", default=TARGET, help=f"Analiz edilecek veri seti (varsayılan: {TARGET})")
    ap.add_argument("--max-year", type=int, default=None, help="Bu yıldan sonraki yayınları dışarıda bırak")
    ap.add_argument("--out", default=None)
    ap.add_argument("--all-types", action="store_true", help="Proje kayıtları dahil tüm doküman türlerini analiz et")
    ap.add_argument("--author-merge", choices=thesaurus.AUTHOR_RULES, default="none",
                    help="Analizde yazar kimliklerini kurala göre birleştir (varsayılan: TR Dizin kimliği)")
    ap.add_argument("--relevant", default=None,
                    help="Yalnız bu kimlikler (satır başına bir kimlik; 05_agreement.py relevant_ids.txt)")
    args = ap.parse_args(argv)
    data_dir = ROOT / "paper" / "data"
    db = Path(args.db) if args.db else latest_frozen(data_dir, _slug(args.dataset))
    if db is None:
        sys.exit("Dondurulmuş veritabanı yok; önce 04_freeze_and_sample.py çalıştırın ya da --db verin.")
    out = Path(args.out or ROOT / "paper" / "results" / f"case_{datetime.now():%Y%m%d}")
    keep = None
    if args.relevant:
        keep = {ln.strip() for ln in Path(args.relevant).read_text(encoding="utf-8").splitlines() if ln.strip()}
    res = run(db, out, data_dir / "exports", None if args.all_types else DOC_TYPES, args.dataset, args.max_year,
              args.author_merge, keep)
    if res["errors"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
