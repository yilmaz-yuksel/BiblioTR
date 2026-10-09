"""Adım 9: araç ve veri seti güvenilirlik denetimi (yazılım makalesi, doğrulama bölümü).

API'ye erişmeden, yerel veritabanı üzerinde çalışır. Üretilenler (`--out` altında):

- validation.json          Tüm sayısal sonuçlar (aşağıdaki A-F bölümleri)
- author_fullname_collisions.csv   Aynı tam ada sahip farklı TR Dizin yazar kimlikleri (elle hüküm için)
- author_shortname_groups.csv      Aynı kısa adı (Soyad, baş harf) paylaşan kimlik grupları (örneklem için)
- doi_only_links.csv               Yalnızca kaynakçadaki DOI ile bulunan, TR Dizin'in bağlamadığı yerel atıflar
- wos_export.txt                   Denetlenen WoS düz metin dosyası (bibliometrix karşılaştırması için girdi)

Bölümler:
  A  Yeniden dizinleme determinizmi: ham JSON'dan iki kez yeniden üretilen tabloların özetleri aynı mı
  B  Veri seti denetimi: alan doluluğu, yinelenen kayıtlar, ülke yazımları, kaynakça ayrıştırma
  C  Yazar kimliği: kısa ad eşsesliliği (homonimi) ve kimlik bölünmesi (aynı kişi, birden çok kimlik)
  D  Atıf bağlantıları: atıf yapanlar listesi, kaynakça hedef kimliği ve DOI eşleşmesinin tutarlılığı
  E  WoS dosyasının bağımsız ayrıştırıcılarla okunması (wosfile, metaknowledge; kuruluysa)
  F  bibliometrix karşılaştırması: `10_bibliometrix_interop.R` çıktıları varsa belge düzeyinde LCS uyumu

Kullanım:
    python paper/scripts/09_validation_audit.py --db data/trdizin.db --dataset bibliyometri \
        --out paper/results/validation_<tarih>
    Rscript paper/scripts/10_bibliometrix_interop.R paper/results/validation_<tarih>/wos_export.txt \
        paper/results/validation_<tarih> [bibliometrix kaynak klasörü]
    python paper/scripts/09_validation_audit.py ... (yeniden çalıştırınca F bölümü dolar)
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import shutil
import sqlite3
import sys
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from bibliotr import Store, analysis  # noqa: E402
from bibliotr.export import short_name, to_wos  # noqa: E402
from bibliotr.normalize import fold, orcid_status  # noqa: E402
from bibliotr.wos import NORTH_CYPRUS, country_name, is_trnc  # noqa: E402

TABLES = ["publications", "publication_authors", "publication_keywords", "publication_subjects",
          "publication_references", "publication_citing", "dataset_publications"]


def _table_digests(path: Path) -> dict[str, list]:
    con = sqlite3.connect(path)
    out = {}
    for t in TABLES:
        cols = [r[1] for r in con.execute(f"pragma table_info({t})") if r[1] != "fetched_at"]
        rows = sorted(repr(r) for r in con.execute(f"select {','.join(cols)} from {t}"))
        out[t] = [len(rows), hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]]
    con.close()
    return out


def section_a(db: Path) -> dict:
    """Ham JSON'dan yeniden dizinleme: sonuçlar değişmemeli ve işlem eşgüçlü (idempotent) olmalı."""
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "copy.db"
        shutil.copy(db, copy)
        before = _table_digests(copy)
        t0 = time.perf_counter()
        with Store(copy) as s:
            n = s.reindex()
        secs = time.perf_counter() - t0
        after = _table_digests(copy)
        with Store(copy) as s:
            s.reindex()
        after2 = _table_digests(copy)
    return {
        "records_reindexed": n,
        "seconds": round(secs, 2),
        "tables": {t: {"rows": before[t][0], "unchanged": before[t] == after[t],
                       "idempotent": after[t] == after2[t]} for t in TABLES},
        "all_unchanged": all(before[t] == after[t] for t in TABLES),
        "all_idempotent": all(after[t] == after2[t] for t in TABLES),
    }


def section_b(pubs) -> dict:
    n = len(pubs)
    authors = [a for p in pubs for a in p.authors]
    refs = [r for p in pubs for r in p.references]
    doi_counts = Counter(p.doi.lower() for p in pubs if p.doi)
    ty = Counter((fold(p.title or ""), p.year) for p in pubs)
    countries = Counter(c.strip() for a in authors if a.country for c in a.country.split(";") if c.strip())
    folded = defaultdict(set)
    for c in countries:
        folded[fold(c)].add(c)
    # KKTC: kurum ya da şehir KKTC'de olduğu hâlde yazılı ülkesi başka olan yazar adresleri
    trnc = Counter()
    for a in authors:
        insts = [s.strip() for s in a.institution.split(";")] if a.institution else [""]
        ctrs = [s.strip() for s in a.country.split(";")] if a.country else []
        for i, inst in enumerate(insts):
            c = ctrs[i] if i < len(ctrs) else (ctrs[0] if ctrs else "")
            if is_trnc(inst, a.city):
                trnc[country_name(c) or "(yok)"] += 1
    # ORCID denetimi: biçim ve ISO 7064 denetim hanesi; bir kimlikte birden çok geçerli ORCID
    orcid_slots = Counter(orcid_status(a.orcid) for a in authors if a.orcid)
    distinct = {a.orcid.strip().upper(): orcid_status(a.orcid) for a in authors if a.orcid}
    valid_by_key: dict[str, set] = defaultdict(set)
    for a in authors:
        if a.orcid and orcid_status(a.orcid) == "valid":
            valid_by_key[analysis.author_key(a)].add(a.orcid.strip().upper())
    return {
        "records": n,
        "document_types": dict(Counter(p.document_type for p in pubs)),
        "languages": dict(Counter(p.language for p in pubs).most_common()),
        "years": {"min": min(p.year for p in pubs if p.year), "max": max(p.year for p in pubs if p.year),
                  "missing": sum(1 for p in pubs if not p.year),
                  "per_year": dict(sorted(Counter(p.year for p in pubs if p.year).items()))},
        "doi_coverage_pct": round(100 * len([p for p in pubs if p.doi]) / n, 1),
        "duplicate_doi_groups": sum(1 for v in doi_counts.values() if v > 1),
        "duplicate_title_year_groups": sum(1 for v in ty.values() if v > 1),
        "records_without_authors": sum(1 for p in pubs if not p.authors),
        "author_slots": len(authors),
        "author_slots_with_trdizin_id_pct": round(100 * sum(1 for a in authors if a.author_id) / len(authors), 1),
        "author_slots_with_orcid_pct": round(100 * sum(1 for a in authors if a.orcid) / len(authors), 1),
        "orcid_slots_by_status": dict(orcid_slots),
        "orcid_distinct": len(distinct),
        "orcid_distinct_by_status": dict(Counter(distinct.values())),
        "author_keys_with_several_valid_orcids": sum(1 for v in valid_by_key.values() if len(v) > 1),
        "author_slots_without_institution": sum(1 for a in authors if not a.institution),
        "country_spellings": dict(countries.most_common(15)),
        "country_case_variants": {k: sorted(v) for k, v in folded.items() if len(v) > 1},
        "trnc_addresses_by_written_country": dict(trnc.most_common()),
        "trnc_addresses_reassigned": sum(v for k, v in trnc.items() if k != NORTH_CYPRUS),
        "records_with_references_pct": round(100 * sum(1 for p in pubs if p.references) / n, 1),
        "references": len(refs),
        "references_with_year_pct": round(100 * sum(1 for r in refs if r.year) / len(refs), 1) if refs else None,
        "references_with_doi_pct": round(100 * sum(1 for r in refs if r.doi) / len(refs), 1) if refs else None,
        "references_with_trdizin_target_pct": round(100 * sum(1 for r in refs if r.target_id) / len(refs), 1)
        if refs else None,
        "citation_count_equals_citing_list": sum(1 for p in pubs if (p.citation_count or 0) == len(p.cited_by)),
        "external_counts_available": sum(1 for p in pubs if p.citations_crossref is not None),
    }


def section_c(pubs, out: Path) -> dict:
    by_short: dict[str, set] = defaultdict(set)
    by_full: dict[str, dict] = defaultdict(lambda: defaultdict(lambda: {"n": 0, "orcid": set(), "inst": set(),
                                                                         "names": set()}))
    id_names: dict[str, set] = defaultdict(set)
    orcid_ids: dict[str, set] = defaultdict(set)
    for p in pubs:
        for a in p.authors:
            k = analysis.author_key(a)
            by_short[fold(short_name(a.name))].add(k)
            d = by_full[fold(a.name)][k]
            d["n"] += 1
            d["names"].add(a.name)
            if a.orcid:
                d["orcid"].add(a.orcid)
                orcid_ids[a.orcid].add(k)
            if a.institution:
                d["inst"].add(a.institution)
            id_names[k].add(fold(a.name))
    keys = {analysis.author_key(a) for p in pubs for a in p.authors}
    amb = {s: ks for s, ks in by_short.items() if len(ks) > 1}
    coll = {n: ids for n, ids in by_full.items() if len(ids) > 1}

    with open(out / "author_fullname_collisions.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group", "full_name", "author_key", "n_slots", "orcids", "institutions", "spellings",
                    "same_person (1/0/NA)", "note"])
        for gi, (name, ids) in enumerate(sorted(coll.items()), 1):
            for k, d in sorted(ids.items(), key=lambda kv: -kv[1]["n"]):
                w.writerow([gi, name, k, d["n"], "; ".join(sorted(d["orcid"])), "; ".join(sorted(d["inst"])),
                            "; ".join(sorted(d["names"])), "", ""])
    with open(out / "author_shortname_groups.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["short_name", "n_keys", "author_keys"])
        for s, ks in sorted(amb.items(), key=lambda kv: -len(kv[1])):
            w.writerow([s, len(ks), "; ".join(sorted(ks))])

    def orcid_relation(ids: dict) -> str:
        sets = [d["orcid"] for d in ids.values()]
        filled = [s for s in sets if s]
        if len(filled) < 2:
            return "orcid_missing_on_one_side"
        return "shared_orcid" if set.intersection(*filled) else "different_orcids"

    return {
        "distinct_author_keys": len(keys),
        "distinct_short_names": len(by_short),
        "distinct_full_names": len(by_full),
        "homonymy": {
            "short_names_shared_by_several_keys": len(amb),
            "keys_in_those_groups": sum(len(v) for v in amb.values()),
            "group_size_distribution": dict(sorted(Counter(len(v) for v in amb.values()).items())),
            "largest_groups": [[s, len(v)] for s, v in sorted(amb.items(), key=lambda kv: -len(kv[1]))[:10]],
        },
        "splitting": {
            "full_names_with_several_keys": len(coll),
            "orcid_relation": dict(Counter(orcid_relation(ids) for ids in coll.values())),
            "share_an_institution": sum(1 for ids in coll.values()
                                        if set.intersection(*[d["inst"] for d in ids.values()])),
            "orcid_with_several_keys": sum(1 for v in orcid_ids.values() if len(v) > 1),
            "keys_with_several_spellings": sum(1 for v in id_names.values() if len(v) > 1),
        },
    }


def section_d(pubs, out: Path) -> dict:
    ids = {p.id for p in pubs}
    by_doi = {p.doi.lower(): p.id for p in pubs if p.doi}
    pid = {p.id: p for p in pubs}
    a_set, b_set, c_set = set(), set(), set()
    c_text = {}
    for p in pubs:
        for c in p.cited_by:
            if c in ids and c != p.id:
                a_set.add((c, p.id))
        for r in p.references:
            if r.target_id in ids and r.target_id != p.id:
                b_set.add((p.id, r.target_id))
            t = None if r.target_id else by_doi.get((r.doi or "").lower())  # TR Dizin bağı öncelikli
            if t and t != p.id:
                c_set.add((p.id, t))
                c_text.setdefault((p.id, t), r.text)
    extra = sorted(c_set - (a_set | b_set))
    with open(out / "doi_only_links.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["citing_id", "citing_year", "cited_id", "cited_year", "cited_title", "cited_doi",
                    "reference_text", "correct (1/0)", "note"])
        for a, b in extra:
            w.writerow([a, pid[a].year, b, pid[b].year, pid[b].title, pid[b].doi, c_text[(a, b)], "", ""])
    union = a_set | b_set | c_set
    years = {p.id: p.year for p in pubs}
    lcs = analysis.local_citations(pubs)
    return {
        "edges_from_citing_lists": len(a_set),
        "edges_from_reference_target_ids": len(b_set),
        "edges_from_reference_dois": len(c_set),
        "target_id_edges_not_in_citing_lists": len(b_set - a_set),
        "citing_list_edges_not_in_references": len(a_set - (b_set | c_set)),
        "agreement_citing_vs_target_id_jaccard": round(len(a_set & b_set) / len(a_set | b_set), 4)
        if a_set | b_set else None,
        "doi_only_edges_added": len(extra),
        "union_edges": len(union),
        "time_inconsistent_edges": sum(1 for a, b in union if years.get(a) and years.get(b) and years[a] < years[b]),
        "tool_local_citations_total": sum(lcs.values()),
        "tool_documents_locally_cited": sum(1 for v in lcs.values() if v),
    }


def section_e(wos_path: Path) -> dict:
    res: dict = {}
    try:
        import wosfile
        recs = list(wosfile.records_from(str(wos_path)))
        res["wosfile"] = {"records": len(recs),
                          "distinct_AU": len({a for r in recs for a in (r.get("AU") or [])}),
                          "records_with_CR": sum(1 for r in recs if r.get("CR")),
                          "CR_total": sum(len(r.get("CR") or []) for r in recs)}
    except ImportError:
        res["wosfile"] = "not installed (pip install wosfile)"
    try:
        import metaknowledge as mk
        rc = mk.RecordCollection(str(wos_path))
        bad = rc.badEntries() if hasattr(rc, "badEntries") else []
        res["metaknowledge"] = {"records": len(rc), "bad_records": len(bad)}
    except ImportError:
        res["metaknowledge"] = "not installed (pip install metaknowledge)"
    except Exception as e:  # pragma: no cover - dış kütüphane
        res["metaknowledge"] = f"error: {type(e).__name__}: {e}"
    return res


def section_f(pubs, out: Path) -> dict | None:
    hist, index = out / "bx_histdata.csv", out / "bx_index.csv"
    if not (hist.exists() and index.exists()):
        return None
    sr_to_ut = {}
    with open(index, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sr_to_ut[row["SR"].strip()] = (row["UT"] or "").replace("TRDIZIN:", "")
    lcs = analysis.local_citations(pubs)
    tc = {p.id: p.citation_count or 0 for p in pubs}
    has_doi = {p.id: bool(p.doi) for p in pubs}
    rows, seen = [], set()
    with open(hist, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            sr = row["Paper"].split(" DOI")[0].strip()
            pid_ = sr_to_ut.get(sr)
            if not pid_ or pid_ in seen or pid_ not in lcs:
                continue
            seen.add(pid_)
            rows.append((pid_, int(float(row["LCS"])), lcs[pid_]))
    def agree(sel):
        return round(sum(1 for _, b, t in sel if b == t) / len(sel), 4) if sel else None
    summary_path = out / "bx_summary.csv"
    bx_summary = {}
    if summary_path.exists():
        with open(summary_path, encoding="utf-8") as f:
            bx_summary = {r["key"]: r["value"] for r in csv.DictReader(f)}
    tool = analysis.summary(pubs)
    return {
        "documents_compared": len(rows),
        "bibliometrix_lcs_total": sum(b for _, b, _ in rows),
        "tool_lcs_total": sum(t for _, _, t in rows),
        "exact_agreement": agree(rows),
        "exact_agreement_with_doi": agree([r for r in rows if has_doi[r[0]]]),
        "exact_agreement_without_doi": agree([r for r in rows if not has_doi[r[0]]]),
        "bibliometrix_lcs_exceeds_total_citations": sum(1 for p_, b, _ in rows if b > tc[p_]),
        "bibliometrix_summary": bx_summary,
        "tool_summary": {k: v for k, v in tool.items()},
    }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/trdizin.db")
    ap.add_argument("--dataset", required=True)
    ap.add_argument("--out", default=None)
    ap.add_argument("--max-year", type=int, default=None, help="Bu yıldan sonraki kayıtları dışarıda bırak")
    ap.add_argument("--papers-only", action="store_true", help="Yalnızca PAPER doküman türü")
    ap.add_argument("--skip-reindex", action="store_true")
    args = ap.parse_args()

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d")
    out = Path(args.out or ROOT / "paper" / "results" / f"validation_{stamp}")
    out.mkdir(parents=True, exist_ok=True)
    db = Path(args.db)

    with Store(db, readonly=True) as s:
        pubs = s.publications(args.dataset)
    if args.max_year:
        pubs = [p for p in pubs if p.year and p.year <= args.max_year]
    if args.papers_only:
        pubs = [p for p in pubs if (p.document_type or "").upper() == "PAPER"]

    report: dict = {"run_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "db": str(db), "dataset": args.dataset, "max_year": args.max_year,
                    "papers_only": args.papers_only, "python": sys.version.split()[0]}
    t0 = time.perf_counter()
    previous = out / "validation.json"
    if not args.skip_reindex:
        report["A_reindex_determinism"] = section_a(db)
    elif previous.exists():
        report["A_reindex_determinism"] = json.loads(previous.read_text(encoding="utf-8")).get("A_reindex_determinism")
    report["B_dataset_audit"] = section_b(pubs)
    report["C_author_identity"] = section_c(pubs, out)
    report["D_citation_links"] = section_d(pubs, out)
    wos_path = out / "wos_export.txt"
    if not wos_path.exists():
        t1 = time.perf_counter()
        wos_path.write_text(to_wos(pubs), encoding="utf-8")
        report["wos_export_seconds"] = round(time.perf_counter() - t1, 2)
    report["E_independent_parsers"] = section_e(wos_path)
    report["F_bibliometrix"] = section_f(pubs, out)
    report["total_seconds"] = round(time.perf_counter() - t0, 2)
    (out / "validation.json").write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                                         encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k not in ("B_dataset_audit",)}, ensure_ascii=False,
                     indent=1, default=str)[:6000])
    print(f"\nÇıktılar: {out}")


if __name__ == "__main__":
    main()
