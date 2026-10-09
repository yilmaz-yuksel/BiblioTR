"""Kaynak ayrıştırıcısının kapsamı ve sürümler arası farkları (Bölüm 5.2 ve 5.3'teki sayılar).

Makale sayıları dondurulmuş setin yeniden dizinlenmiş kopyasından (2025'e kadarki dergi makaleleri) üretilir:
- kapsam: kaynakların kaçında yıl, kaynak adı, cilt, ilk sayfa, DOI ve TR Dizin bağı var; genel konum kuralı
  kapatıldığında kaynak adı payı; ayrıştırıcının geliştirildiği onda bir pay ile kalan arasında bu payın değişimi;
- kodlanan örneklemin tabakalarına karşılık gelen havuz payları ve bunlarla ağırlıklandırılmış doğruluk
  (11_reference_coding.py --score sonucundan);
- Web of Science çıktısındaki CR satırları (aynı kaynakçada yinelenen kaynaklar bir kez yazılır);
- satır sonunda tireden bölünmüş DOI'ler: 849907e düzeltmesinden önceki sürümün çıktısında tireyle biten
  yarım DOI taşıyan CR satırları;
- ayrıştırıcının elle kodlamadan sonraki revizyonu (git bc38686 -> 0157f25, 29bb81f): en az bir alanı değişen
  kaynaklar ve alan başına değişim.

Eski sürümler git geçmişinden geçici bir klasöre çıkarılır ve ham JSON aynı kayıtlardan yeniden ayrıştırılır;
çalışma ağacına dokunulmaz.

    python paper/scripts/17_reference_coverage.py --dataset fa_alan
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import subprocess
import sys
import tarfile
import tempfile
from collections import Counter
from datetime import datetime, timezone
from io import BytesIO
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bibliotr.storage import Store  # noqa: E402

BEFORE_SPLIT_DOI_FIX = "b9391ef"  # satır sonunda bölünmüş DOI düzeltmesinden (849907e) önce
BEFORE_REVISION = "bc38686"   # elle kodlanan örneklemdeki hatalara göre yapılan revizyondan önce
REVISION = "29bb81f"          # revizyonun son işlemesi
FIELDS = ("author", "year", "source", "volume", "page", "doi")

# Paketin bir sürümüyle ham JSON'dan kaynak alanlarını üreten işçi (ayrı süreçte çalışır).
WORKER = r"""
import json, sqlite3, sys
sys.path.insert(0, sys.argv[1])
import importlib, os
# Paket 9 Ekim 2026'da trdizin'den bibliotr'ye yeniden adlandırıldı; eski sürümler trdizin adını taşır.
pkg = "bibliotr" if os.path.isdir(os.path.join(sys.argv[1], "bibliotr")) else "trdizin"
wos = importlib.import_module(pkg + ".wos")
normalize_publication = importlib.import_module(pkg + ".normalize").normalize_publication
ids = json.load(open(sys.argv[3]))
off = len(sys.argv) > 4 and sys.argv[4] == "no-generic"
if off:  # genel kural: APA ve Vancouver kalıbına uymayan kaynaklarda konum yedeği ve kitap adı
    wos._generic_locator = lambda body: (None, None, None)
    wos._title_sentence = lambda body, apa: None
con = sqlite3.connect("file:" + sys.argv[2] + "?mode=ro", uri=True)
out = []
for pid in ids:
    raw = json.loads(con.execute("SELECT raw_json FROM publications WHERE id = ?", (pid,)).fetchone()[0])
    for i, r in enumerate(normalize_publication(raw).references):
        f = wos.reference_fields(r)
        out.append([pid, i, r.text] + [f.get(k) for k in ("author", "year", "source", "volume", "page", "doi")])
json.dump(out, sys.stdout, ensure_ascii=False)
"""


# Paketin bir sürümüyle Web of Science çıktısı üretip CR satırlarını sayan işçi.
EXPORT_WORKER = r"""
import json, re, sqlite3, sys
sys.path.insert(0, sys.argv[1])
import importlib, os
# Paket 9 Ekim 2026'da trdizin'den bibliotr'ye yeniden adlandırıldı; eski sürümler trdizin adını taşır.
pkg = "bibliotr" if os.path.isdir(os.path.join(sys.argv[1], "bibliotr")) else "trdizin"
to_wos = importlib.import_module(pkg + ".wos").to_wos
parse_wos = importlib.import_module(pkg + ".wos").parse_wos
normalize_publication = importlib.import_module(pkg + ".normalize").normalize_publication
ids = json.load(open(sys.argv[3]))
con = sqlite3.connect("file:" + sys.argv[2] + "?mode=ro", uri=True)
pubs = [normalize_publication(json.loads(con.execute("SELECT raw_json FROM publications WHERE id = ?",
                                                     (pid,)).fetchone()[0])) for pid in ids]
cr = [c for rec in parse_wos(to_wos(pubs)) for c in rec.get("CR", [])]
json.dump({"cr_lines": len(cr), "cr_with_hyphen_cut_doi": sum(1 for c in cr if re.search(r"DOI \S*-$", c))},
          sys.stdout)
"""


def export_counts(tree: Path, db: Path, ids: list[str], tmp: Path) -> dict:
    idf = tmp / "ids.json"
    idf.write_text(json.dumps(ids), encoding="utf-8")
    args = [sys.executable, "-I", "-c", EXPORT_WORKER, str(tree), str(db), str(idf)]
    return json.loads(subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8").stdout)


def _module(name: str, file: str):
    spec = importlib.util.spec_from_file_location(name, ROOT / "paper" / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def checkout(rev: str, into: Path) -> Path:
    """Paketin yeniden adlandırmadan önceki (`trdizin` adlı) bir sürümünü `into` altına çıkarır."""
    data = subprocess.run(["git", "-C", str(ROOT), "archive", rev, "trdizin"], check=True,
                          capture_output=True).stdout
    with tarfile.open(fileobj=BytesIO(data)) as tar:
        tar.extractall(into, filter="data")
    return into


def fields(tree: Path, db: Path, ids: list[str], tmp: Path, no_generic: bool = False) -> list[list]:
    idf = tmp / "ids.json"
    idf.write_text(json.dumps(ids), encoding="utf-8")
    args = [sys.executable, "-I", "-c", WORKER, str(tree), str(db), str(idf)] + (["no-generic"] if no_generic else [])
    return json.loads(subprocess.run(args, check=True, capture_output=True, text=True, encoding="utf-8").stdout)


def pct(k: int, n: int) -> float:
    return round(100 * k / n, 1) if n else 0.0


def coverage(rows: list[list], is_dev) -> dict:
    n = len(rows)
    idx = {k: i + 3 for i, k in enumerate(FIELDS)}
    out = {"references": n, **{f"with_{k}_pct": pct(sum(1 for r in rows if r[idx[k]]), n) for k in FIELDS}}
    dev = [r for r in rows if is_dev(r[2])]
    rest = [r for r in rows if not is_dev(r[2])]
    out["tuning_tenth"] = {"references": len(dev), "with_source_pct": pct(sum(1 for r in dev if r[idx['source']]), len(dev))}
    out["rest"] = {"references": len(rest), "with_source_pct": pct(sum(1 for r in rest if r[idx['source']]), len(rest))}
    return out


def style_weights(pubs, ref_check: dict) -> dict:
    """Örneklem havuzlarının (tekil, geliştirme payı dışı kaynaklar) payları ve ağırlıklı doğruluk."""
    m11 = _module("rc11", "11_reference_coding.py")
    seen, pools = set(), Counter()
    for p in sorted(pubs, key=lambda x: x.id):
        for r in p.references:
            key = " ".join(r.text.split()).lower()
            if key and key not in seen and not m11.is_dev(r.text):
                seen.add(key)
                pools[m11.style_of(r.text)] += 1
    n = sum(pools.values())
    w = {k: pools[k] / n for k in pools}
    by = ref_check["references"]["by_stratum"]
    weighted = {f: round(100 * sum(w[s] * by[s][f]["accuracy"] for s in w), 1) for f in ("İlk yazar", "Yıl")}
    return {"pool": dict(pools), "share_pct": {k: pct(v, n) for k, v in pools.items()}, "weighted_accuracy_pct": weighted}


def revision_changes(before: list[list], after: list[list]) -> dict:
    a = {(r[0], r[1]): r for r in after}
    changed, per_field = 0, Counter()
    for r in before:
        s = a.get((r[0], r[1]))
        if s is None:
            continue
        diff = [k for i, k in enumerate(FIELDS) if (r[i + 3] or None) != (s[i + 3] or None)]
        changed += bool(diff)
        per_field.update(diff)
    return {"compared": len(before), "changed": changed, "changed_pct": pct(changed, len(before)),
            "by_field": dict(per_field.most_common())}


def run(db: Path, dataset: str, ref_check: Path) -> dict:
    m11 = _module("rc11", "11_reference_coding.py")
    with Store(db, readonly=True) as s:
        pubs = [p for p in s.publications(dataset)
                if (p.document_type or "").upper() == "PAPER" and p.year and p.year <= 2025]
    ids = sorted(p.id for p in pubs)
    with tempfile.TemporaryDirectory() as t:
        tmp = Path(t)
        now = fields(ROOT, db, ids, tmp)
        no_generic = fields(ROOT, db, ids, tmp, no_generic=True)
        old = fields(checkout(BEFORE_REVISION, tmp / "before"), db, ids, tmp)
        old_no_generic = fields(tmp / "before", db, ids, tmp, no_generic=True)
        export_now = export_counts(ROOT, db, ids, tmp)
        export_split = export_counts(checkout(BEFORE_SPLIT_DOI_FIX, tmp / "split"), db, ids, tmp)
    return {
        "computed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "db": str(db.relative_to(ROOT)) if db.is_relative_to(ROOT) else str(db), "dataset": dataset,
        "articles": len(pubs), "articles_with_references": sum(1 for p in pubs if p.references),
        "coverage_current": coverage(now, m11.is_dev),
        "coverage_current_without_generic_rule": coverage(no_generic, m11.is_dev),
        "coverage_before_revision": coverage(old, m11.is_dev),
        "coverage_before_revision_without_generic_rule": coverage(old_no_generic, m11.is_dev),
        "with_trdizin_link_pct": pct(sum(1 for p in pubs for r in p.references if r.target_id),
                                     sum(len(p.references) for p in pubs)),
        "wos_export": {"references": len(now), "cr_lines": export_now["cr_lines"],
                       "repeated_within_list": len(now) - export_now["cr_lines"],
                       "cr_with_hyphen_cut_doi": export_now["cr_with_hyphen_cut_doi"]},
        "wos_export_before_split_doi_fix": {"version": BEFORE_SPLIT_DOI_FIX, **export_split},
        "sample_strata": style_weights(pubs, json.loads(ref_check.read_text(encoding="utf-8"))),
        "parser_revision": {"before": BEFORE_REVISION, "after": REVISION, **revision_changes(old, now)},
    }


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--db", default=None)
    args = ap.parse_args(argv)
    name = args.dataset.replace(" ", "_")
    db = Path(args.db) if args.db else sorted((ROOT / "paper" / "data").glob(f"{name}_frozen_*_reindex_*.db"))[-1]
    ref_check = sorted((ROOT / "paper" / "results").glob(f"reference_check_{name}_*.json"))[-1]
    res = run(db, args.dataset, ref_check)
    stamp = re.sub(r"\D", "", res["computed_at_utc"])[:8]
    out = ROOT / "paper" / "results" / f"reference_coverage_{name}_{stamp}.json"
    out.write_text(json.dumps(res, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
