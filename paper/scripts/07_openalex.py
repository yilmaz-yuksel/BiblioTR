"""D5 doğrulaması ve D6: dondurulmuş veri setini OpenAlex ile doğrudan karşılaştırma.

İlk vaka analizinde TR Dizin'in bildirdiği OpenAlex atıf sayıları şüpheli çıktı (DOI'li 174
kaydın yalnızca 6'sı atıf almış görünüyor; CrossRef'te 109). Tek kayıtlık kontrol: TR Dizin
OpenAlex = 0, OpenAlex API = 20 (10.30794/pausbed.1124926). Bu betik bunu tüm veri setinde sınar
ve aynı zamanda kayıtların OpenAlex'teki kapsamını (D6) ölçer.

Her DOI OpenAlex API'sinde toplu sorgulanır (istek başına 50 DOI). Hesaplananlar:
    - Bulunma oranı (DOI'li kayıtlar içinde) ve DOI'siz kayıt sayısı
    - OpenAlex'in doğrudan atıf sayısı ile TR Dizin'in bildirdiği OpenAlex / CrossRef /
      OpenCitations sayıları ve TR Dizin atıfı arasında Spearman korelasyonu
    - TR Dizin'in bildirdiği OpenAlex sayısının doğrudan sayıdan düşük / eşit / yüksek olduğu kayıtlar
    - Yayın yılı uyuşmazlıkları

Çalıştırma (TR Dizin gerekmez, OpenAlex'e erişim gerekir):

    python paper/scripts/07_openalex.py --mailto adiniz@kurum.edu.tr

Başlık eşleştirmesi de istenirse önce `bibliotr --db <kopya>_openalex.db openalex --dataset <ad>` DOI ve
başlık eşleşmelerini veritabanı kopyasına yazar; bu betik o kopyayı verilince kayıtlı eşleşmeleri
kullanır (API'ye yeniden gitmez):

    python paper/scripts/07_openalex.py --dataset fa_alan --db paper/data/fa_alan_frozen_20261002_openalex.db

`--mailto` OpenAlex'in "polite pool"una girmek için önerilir; e-posta depoya yazılmaz.

Çıktılar (paper/results/openalex_<veri seti>_<tarih>/):
    openalex_matches.csv   kayıt bazında: DOI, bulundu mu, OpenAlex kimliği, atıf sayıları, yıllar
    summary.json           oranlar, korelasyonlar, karşılaştırma sayıları, sorgu tarihi
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402
import requests  # noqa: E402

from bibliotr.normalize import fold  # noqa: E402
from bibliotr.storage import Store  # noqa: E402

TARGET = "fa_alan"
API = "https://api.openalex.org/works"
BATCH = 50
FIELDS = "id,doi,publication_year,cited_by_count,type,language"


def norm_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    d = doi.strip().lower()
    for prefix in ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "doi:"):
        if d.startswith(prefix):
            d = d[len(prefix):]
    return d or None


def fetch_openalex(dois: list[str], session: requests.Session | None = None, mailto: str | None = None,
                   delay: float = 0.2, max_retries: int = 4) -> dict[str, dict]:
    """DOI -> OpenAlex kaydı (bulunamayanlar sözlükte yer almaz)."""
    session = session or requests.Session()
    out: dict[str, dict] = {}
    for i in range(0, len(dois), BATCH):
        chunk = dois[i:i + BATCH]
        params = {"filter": "doi:" + "|".join(chunk), "per-page": BATCH, "select": FIELDS}
        if mailto:
            params["mailto"] = mailto
        for attempt in range(max_retries + 1):
            resp = session.get(API, params=params, timeout=30)
            if resp.status_code == 429 or resp.status_code >= 500:
                if attempt == max_retries:
                    resp.raise_for_status()
                time.sleep(2 ** attempt)
                continue
            resp.raise_for_status()
            break
        for w in resp.json().get("results", []):
            d = norm_doi(w.get("doi"))
            if d:
                out[d] = w
        print(f"  OpenAlex {min(i + BATCH, len(dois))}/{len(dois)}", file=sys.stderr)
        time.sleep(delay)
    return out


def spearman(a: pd.Series, b: pd.Series) -> dict:
    sub = pd.DataFrame({"a": a, "b": b}).dropna()
    ok = len(sub) >= 3 and sub["a"].nunique() > 1 and sub["b"].nunique() > 1
    rho = sub["a"].rank().corr(sub["b"].rank()) if ok else None
    return {"n": len(sub), "rho": None if rho is None or pd.isna(rho) else round(float(rho), 3)}


def compare(rows: list[dict]) -> dict:
    df = pd.DataFrame(rows)
    # None içeren (ya da tümü None) sayı sütunları nesne türünde kalmasın
    for c in ("oa_cited_by_count", "trdizin_reported_openalex", "crossref", "opencitations", "trdizin",
              "year_trdizin", "year_openalex"):
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")
    with_doi = df[df["doi"].notna()]
    found = with_doi[with_doi["found"] == 1]
    if "matched_by" in df:  # atıf karşılaştırmaları tüm eşleşen kayıtlarla (DOI ve başlık)
        found_all = df[df["found"] == 1]
    else:
        found_all = found
    res: dict = {
        "n_records": len(df), "with_doi": len(with_doi), "without_doi": int(df["doi"].isna().sum()),
        "found_in_openalex": len(found),
        "found_pct_of_doi": round(100 * len(found) / len(with_doi), 1) if len(with_doi) else None,
        "found_pct_of_all": round(100 * len(found) / len(df), 1) if len(df) else None,
    }
    if "matched_by" in df:
        res["found_by"] = {"doi": int((df["matched_by"] == "doi").sum()),
                           "title": int((df["matched_by"] == "title").sum())}
        res["found_total"] = int(df["found"].sum())
        res["found_pct_total"] = round(100 * res["found_total"] / len(df), 1) if len(df) else None
        res["without_doi_found_by_title"] = int(((df["doi"].isna()) & (df["matched_by"] == "title")).sum())
    found = found_all
    if len(found):
        direct, reported = found["oa_cited_by_count"], found["trdizin_reported_openalex"]
        both = found[reported.notna()]
        res["reported_vs_direct"] = {
            "n": len(both),
            "reported_lower": int((both["trdizin_reported_openalex"] < both["oa_cited_by_count"]).sum()),
            "equal": int((both["trdizin_reported_openalex"] == both["oa_cited_by_count"]).sum()),
            "reported_higher": int((both["trdizin_reported_openalex"] > both["oa_cited_by_count"]).sum()),
            "reported_zero_direct_positive": int(((both["trdizin_reported_openalex"] == 0)
                                                  & (both["oa_cited_by_count"] > 0)).sum()),
        }
        res["cited_records"] = {
            "openalex_direct": int((direct > 0).sum()),
            "trdizin_reported_openalex": int((reported.fillna(0) > 0).sum()),
            "crossref": int((found["crossref"].fillna(0) > 0).sum()),
            "trdizin": int((found["trdizin"].fillna(0) > 0).sum()),
        }
        res["total_citations"] = {k: int(found[c].fillna(0).sum()) for k, c in (
            ("openalex_direct", "oa_cited_by_count"), ("trdizin_reported_openalex", "trdizin_reported_openalex"),
            ("crossref", "crossref"), ("opencitations", "opencitations"), ("trdizin", "trdizin"))}
        res["spearman_with_openalex_direct"] = {k: spearman(direct, found[c]) for k, c in (
            ("trdizin", "trdizin"), ("crossref", "crossref"), ("opencitations", "opencitations"),
            ("trdizin_reported_openalex", "trdizin_reported_openalex"))}
        yrs = found[found["year_trdizin"].notna() & found["year_openalex"].notna()]
        diff = yrs["year_trdizin"] - yrs["year_openalex"]
        res["year_mismatch"] = {"n": len(yrs), "mismatched": int((diff != 0).sum()),
                                "trdizin_later": int((diff > 0).sum()), "trdizin_earlier": int((diff < 0).sum())}
    return res


def run(db: Path, outdir: Path, session: requests.Session | None = None, mailto: str | None = None,
        delay: float = 0.2, dataset: str = TARGET, doc_types: tuple[str, ...] | None = None,
        max_year: int | None = None, fetch: bool = False) -> dict:
    if not db.exists():
        sys.exit(f"Veritabanı bulunamadı: {db}")
    with Store(db, readonly=True) as store:
        pubs = store.publications(dataset)
    if doc_types:
        pubs = [p for p in pubs if (p.document_type or "") in doc_types]
    if max_year:
        pubs = [p for p in pubs if p.year and p.year <= max_year]
    if not pubs:
        sys.exit(f"'{dataset}' veri seti boş: {db}")
    stored = not fetch and any(p.openalex_id for p in pubs)
    rows = []
    if stored:
        print(f"{len(pubs)} kayıt; veritabanındaki OpenAlex eşleşmeleri kullanılıyor (DOI ve başlık).",
              file=sys.stderr)
        for p in pubs:
            rows.append({"id": p.id, "doi": norm_doi(p.doi), "found": int(bool(p.openalex_id)),
                         "matched_by": p.openalex_matched_by or "", "openalex_id": p.openalex_id,
                         "oa_cited_by_count": p.openalex_citations,
                         "trdizin": p.citation_count, "trdizin_reported_openalex": p.citations_openalex,
                         "crossref": p.citations_crossref, "opencitations": p.citations_opencitations,
                         "year_trdizin": p.year, "year_openalex": p.openalex_year, "oa_type": None,
                         "oa_language": None})
    else:
        dois = sorted({d for d in (norm_doi(p.doi) for p in pubs) if d})
        print(f"{len(pubs)} kayıt, {len(dois)} DOI sorgulanacak.", file=sys.stderr)
        found = fetch_openalex(dois, session, mailto, delay)
        for p in pubs:
            d = norm_doi(p.doi)
            w = found.get(d) if d else None
            rows.append({"id": p.id, "doi": d, "found": int(w is not None), "matched_by": "doi" if w else "",
                         "openalex_id": (w or {}).get("id"), "oa_cited_by_count": (w or {}).get("cited_by_count"),
                         "trdizin": p.citation_count, "trdizin_reported_openalex": p.citations_openalex,
                         "crossref": p.citations_crossref, "opencitations": p.citations_opencitations,
                         "year_trdizin": p.year, "year_openalex": (w or {}).get("publication_year"),
                         "oa_type": (w or {}).get("type"), "oa_language": (w or {}).get("language")})
    outdir.mkdir(parents=True, exist_ok=True)
    with open(outdir / "openalex_matches.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    res = {"queried_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "db": db.name,
           "dataset": dataset, "source": "stored matches" if stored else "DOI query", **compare(rows)}
    (outdir / "summary.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    _report(res, outdir)
    return res


def _report(res: dict, outdir: Path) -> None:
    print(f"\nKayıt {res['n_records']}, DOI'li {res['with_doi']}, DOI'siz {res['without_doi']}")
    print(f"OpenAlex'te bulunan: {res['found_in_openalex']} (DOI'lilerin %{res['found_pct_of_doi']}, "
          f"tümünün %{res['found_pct_of_all']})")
    rv = res.get("reported_vs_direct")
    if rv:
        print(f"TR Dizin'in bildirdiği OpenAlex sayısı, doğrudan sayıya göre (n {rv['n']}): düşük {rv['reported_lower']}, "
              f"eşit {rv['equal']}, yüksek {rv['reported_higher']}; bildirilen 0 ama gerçekte > 0: "
              f"{rv['reported_zero_direct_positive']}")
        c = res["cited_records"]
        print(f"Atıf alan kayıt: OpenAlex doğrudan {c['openalex_direct']}, TR Dizin'in bildirdiği OpenAlex "
              f"{c['trdizin_reported_openalex']}, CrossRef {c['crossref']}, TR Dizin {c['trdizin']}")
        print("Toplam atıf: " + ", ".join(f"{k} {v}" for k, v in res["total_citations"].items()))
        print("OpenAlex (doğrudan) ile Spearman: " + ", ".join(
            f"{k} {v['rho']} (n {v['n']})" for k, v in res["spearman_with_openalex_direct"].items()))
        y = res["year_mismatch"]
        print(f"Yıl uyuşmazlığı: {y['mismatched']}/{y['n']} (TR Dizin daha geç {y['trdizin_later']}, "
              f"daha erken {y['trdizin_earlier']})")
    print(f"Çıktılar: {outdir}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=None,
                    help="Varsayılan: paper/data/ altındaki en yeni <veri seti>_frozen_*_openalex.db, yoksa _frozen_*.db")
    ap.add_argument("--dataset", default=TARGET)
    ap.add_argument("--doc-types", nargs="*", default=None, help="Yalnız bu yayın türleri, ör. PAPER")
    ap.add_argument("--max-year", type=int, default=None)
    ap.add_argument("--fetch", action="store_true", help="Kayıtlı eşleşmeler olsa da DOI'leri API'den sorgula")
    ap.add_argument("--mailto", default=None, help="OpenAlex polite pool için e-posta (depoya yazılmaz)")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    data_dir = ROOT / "paper" / "data"
    name = re.sub(r"[^a-z0-9]+", "_", fold(args.dataset)).strip("_")
    dbs = sorted(data_dir.glob(f"{name}_frozen_*_openalex.db")) or sorted(
        p for p in data_dir.glob(f"{name}_frozen_*.db") if not p.stem.endswith("_openalex"))
    db = Path(args.db) if args.db else (dbs[-1] if dbs else None)
    if db is None:
        sys.exit("Dondurulmuş veritabanı yok; önce 04_freeze_and_sample.py çalıştırın ya da --db verin.")
    out = Path(args.out or ROOT / "paper" / "results" / f"openalex_{name}_{datetime.now():%Y%m%d}")
    run(db, out, mailto=args.mailto, dataset=args.dataset,
        doc_types=tuple(args.doc_types) if args.doc_types else None, max_year=args.max_year, fetch=args.fetch)


if __name__ == "__main__":
    main()
