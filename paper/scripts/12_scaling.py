"""V8 ölçeklenme testi: veri seti büyüdükçe süre ve bellek.

Dondurulmuş setin ham kayıtları kopyalanarak 1.000 ile 30.000 kayıt arasında sentetik setler kurulur.
Her kopyada kayıt, yazar ve atıf hedefi kimlikleri kaydırılır; böylece kopyalar birbirinden bağımsız
yayınlar, yazarlar ve atıf ağlarıdır (en kötü duruma yakın: yazar sayısı kayıtla doğrusal büyür).
Kayıt DOI'lerine kopya eki eklenir; kaynakça metinleri değişmez.

Her boyut ayrı bir alt süreçte çalışır; böylece en yüksek bellek (peak RSS) o boyuta aittir.
Ölçülen adımlar: ham JSON'dan dizinleme, yükleme, temel analizler, ağlar, WoS çıktısı ve yeniden dizinleme.

Çıktılar (paper/results/scaling_<tarih>/):
- scaling.json   ortam, boyutlar, adım süreleri, en yüksek bellek, veritabanı boyutu
- scaling.csv    aynı tablo düz biçimde

Kullanım:
    python paper/scripts/12_scaling.py --dataset fa_alan [--sizes 1000,2500,5000,10000,20000,30000]
"""

from __future__ import annotations

import argparse
import copy
import csv
import hashlib
import json
import platform
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

SIZES = (1000, 2500, 5000, 10000, 20000, 30000)
OFFSET = 100_000_000  # kopya başına kimlik kayması


def peak_rss_mb() -> float | None:
    """Bu sürecin en yüksek bellek kullanımı (MB). Linux/macOS: resource; Windows: psapi."""
    try:
        import resource

        r = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(r / (1024 * 1024) if sys.platform == "darwin" else r / 1024, 1)
    except ImportError:
        pass
    try:  # Windows: GetProcessMemoryInfo; tutamaç türleri açıkça verilmezse 64 bitte çağrı sessizce başarısız olur
        import ctypes
        from ctypes import wintypes

        class PMC(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        try:
            get_info = kernel32.K32GetProcessMemoryInfo  # Windows 7 ve sonrası
        except AttributeError:
            get_info = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        get_info.argtypes = [wintypes.HANDLE, ctypes.POINTER(PMC), wintypes.DWORD]
        get_info.restype = wintypes.BOOL
        pmc = PMC()
        pmc.cb = ctypes.sizeof(PMC)
        if get_info(kernel32.GetCurrentProcess(), ctypes.byref(pmc), pmc.cb):
            return round(pmc.PeakWorkingSetSize / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass
    return None


def _shift(value, k: int):
    """Sayısal kimliği k. kopya için kaydırır; boş ya da 0 (hedef yok) değerler aynı kalır."""
    if value in (None, "", 0, "0"):
        return value
    try:
        n = int(value)
    except (TypeError, ValueError):
        return value
    shifted = n + k * OFFSET
    return str(shifted) if isinstance(value, str) else shifted


def replicate(raw: dict, k: int) -> dict:
    """Ham kaydın k. kopyası: kayıt, yazar ve atıf kimlikleri kaydırılmış, DOI'ye ek konmuş."""
    if k == 0:
        return raw
    r = copy.deepcopy(raw)
    r["id"] = _shift(r.get("id"), k)
    if r.get("doi"):
        r["doi"] = f"{r['doi']}.r{k}"
    for a in r.get("authors") or []:
        if isinstance(a, dict):
            for key in ("authorId", "relationId"):
                if key in a:
                    a[key] = _shift(a[key], k)
            if a.get("orcid"):
                a["orcid"] = f"{a['orcid']}-r{k}"  # kopyalar ORCID üzerinden birleşmesin
    for ref in r.get("references") or []:
        if isinstance(ref, dict):
            for key in ("targetPublication", "targetId", "publicationId"):
                if key in ref:
                    ref[key] = _shift(ref[key], k)
    for key in ("citedReferences", "citingPublications"):
        for c in r.get(key) or []:
            if isinstance(c, dict):
                for kk in ("sourceId", "publicationId"):
                    if kk in c:
                        c[kk] = _shift(c[kk], k)
    return r


def synthetic(base: list[dict], n: int) -> list[dict]:
    return [replicate(base[i % len(base)], i // len(base)) for i in range(n)]


def child(n: int, base_path: Path, workdir: Path) -> dict:
    """Bir boyutun ölçümü (alt süreçte çalışır)."""
    from bibliotr import analysis
    from bibliotr.export import to_wos
    from bibliotr.storage import Store

    base = json.loads(base_path.read_text(encoding="utf-8"))
    rss_import = peak_rss_mb()
    raws = synthetic(base, n)
    steps: dict[str, float] = {}

    def timed(name, fn, *a, **kw):
        t0 = time.perf_counter()
        out = fn(*a, **kw)
        steps[name] = round(time.perf_counter() - t0, 3)
        return out

    db = workdir / f"scale_{n}.db"
    with Store(db) as s:
        timed("index_raw_json", lambda: s.add_records(s.create_dataset("scale"), raws))
    del raws
    with Store(db, readonly=True) as s:
        pubs = timed("load", s.publications, "scale")
    timed("summary", analysis.summary, pubs)
    timed("per_year", analysis.per_year, pubs)
    timed("author_metrics", analysis.author_metrics, pubs, None)
    timed("lotka", analysis.lotka, pubs)
    timed("bradford", analysis.bradford, pubs)
    timed("top_references", analysis.top_references, pubs)
    timed("local_citations", analysis.local_citations, pubs)
    timed("citation_network", analysis.citation_network, pubs)
    timed("coauthor_network_top100", analysis.field_network, pubs, "author")
    timed("keyword_network_top100", analysis.field_network, pubs, "keyword")
    timed("coupling_network_top100", analysis.coupling_network, pubs)
    wos = timed("wos_export", to_wos, pubs)
    with Store(db) as s:
        timed("reindex", s.reindex)
    return {
        "records": n, "publications_loaded": len(pubs),
        "authors": len({analysis.author_key(a) for p in pubs for a in p.authors}),
        "references": sum(len(p.references) for p in pubs),
        "steps_seconds": steps,
        "total_seconds": round(sum(steps.values()), 2),
        "peak_rss_mb": peak_rss_mb(), "rss_after_imports_mb": rss_import,
        "db_mb": round(db.stat().st_size / (1024 * 1024), 1),
        "wos_mb": round(len(wos.encode("utf-8")) / (1024 * 1024), 1),
        "wos_sha256": hashlib.sha256(wos.encode("utf-8")).hexdigest(),
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=None, help="Dondurulmuş veritabanı (varsayılan: paper/data/<veri seti>_frozen_*.db)")
    ap.add_argument("--dataset", required=False)
    ap.add_argument("--sizes", default=",".join(map(str, SIZES)))
    ap.add_argument("--max-year", type=int, default=2025)
    ap.add_argument("--doc-types", default="PAPER")
    ap.add_argument("--repeat", type=int, default=1, help="Her boyut kaç kez ölçülsün (süre için en küçüğü)")
    ap.add_argument("--out", default=None)
    ap.add_argument("--child", type=int, default=None, help=argparse.SUPPRESS)
    ap.add_argument("--base", default=None, help=argparse.SUPPRESS)
    ap.add_argument("--workdir", default=None, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)

    if args.child:
        print(json.dumps(child(args.child, Path(args.base), Path(args.workdir))))
        return

    from bibliotr.storage import Store

    if not args.dataset:
        sys.exit("--dataset gerekli")
    slug = args.dataset.replace(" ", "_")
    if args.db:
        db = Path(args.db)
    else:
        found = sorted(p for p in (ROOT / "paper" / "data").glob(f"{slug}_frozen_*.db")
                       if not p.stem.endswith("_openalex"))
        db = found[-1] if found else None
    if db is None or not db.exists():
        sys.exit("Dondurulmuş veritabanı yok; --db verin.")
    types = {t.strip().upper() for t in args.doc_types.split(",") if t.strip()}
    with Store(db, readonly=True) as s:
        raws = s.raw_records(args.dataset)
        pubs = s.publications(args.dataset)
    keep = {p.id for p in pubs if (not types or (p.document_type or "").upper() in types)
            and (not args.max_year or (p.year and p.year <= args.max_year))}
    base = [r for r in raws if str(r.get("id")) in keep]
    sizes = [int(x) for x in args.sizes.split(",") if x.strip()]
    out = Path(args.out) if args.out else ROOT / "paper" / "results" / f"scaling_{datetime.now():%Y%m%d}"
    out.mkdir(parents=True, exist_ok=True)

    import pandas as pd
    import networkx as nx

    env = {"python": platform.python_version(), "platform": platform.platform(), "machine": platform.machine(),
           "processor": platform.processor() or None, "cpu_count": __import__("os").cpu_count(),
           "pandas": pd.__version__, "networkx": nx.__version__}
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        base_path = Path(tmp) / "base.json"
        base_path.write_text(json.dumps(base, ensure_ascii=False), encoding="utf-8")
        for n in sizes:
            runs = []
            for _ in range(max(1, args.repeat)):
                work = Path(tmp) / f"w{n}_{len(runs)}"
                work.mkdir()
                cp = subprocess.run([sys.executable, __file__, "--child", str(n), "--base", str(base_path),
                                     "--workdir", str(work)], capture_output=True, text=True, encoding="utf-8")
                if cp.returncode != 0:
                    sys.exit(f"{n} kayıt başarısız:\n{cp.stderr[-2000:]}")
                runs.append(json.loads(cp.stdout.strip().splitlines()[-1]))
            best = min(runs, key=lambda r: r["total_seconds"])
            best["identical_wos_across_repeats"] = len({r["wos_sha256"] for r in runs}) == 1
            best["repeats"] = len(runs)
            rows.append(best)
            print(f"{n:>6} kayıt: {best['total_seconds']:>7.1f} sn, en yüksek bellek {best['peak_rss_mb']} MB, "
                  f"veritabanı {best['db_mb']} MB", flush=True)
    res = {"run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"), "db": db.name,
           "dataset": args.dataset, "base_records": len(base), "environment": env, "results": rows}
    (out / "scaling.json").write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    step_names = list(rows[0]["steps_seconds"]) if rows else []
    with open(out / "scaling.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["records", "authors", "references", "total_seconds", "peak_rss_mb", "db_mb", "wos_mb"]
                   + step_names)
        for r in rows:
            w.writerow([r["records"], r["authors"], r["references"], r["total_seconds"], r["peak_rss_mb"],
                        r["db_mb"], r["wos_mb"]] + [r["steps_seconds"][s] for s in step_names])
    print(f"Yazıldı: {out}")


if __name__ == "__main__":
    main()
