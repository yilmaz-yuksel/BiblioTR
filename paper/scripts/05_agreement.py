"""Aşama 3, adım 4: kodlayıcı uyumu, arama kesinliği ve D1 alan doğruluğu.

Girdiler (paper/data/coding/ ya da --dataset ile paper/data/coding/<veri seti>/):
    kodlama_K1.xlsx   ilgililik (+ D1, ayrı D1 dosyası yoksa) (birinci kodlayıcı)
    kodlama_K2.xlsx   ilgililik (ikinci kodlayıcı; yoksa yalnızca K1 raporlanır)
    kodlama_D1.xlsx   (isteğe bağlı) ayrı örneklemde D1 alan doğruluğu
    kodlama_yazar.xlsx (isteğe bağlı) yazar kimliği hükümleri; dondurma özetindeki author_groups.csv
                      bulunursa aracın kural önerisiyle karşılaştırılır
    uzlasi.csv        (isteğe bağlı) anlaşmazlıkların uzlaşı kodları; bu betik ilk çalıştığında
                      anlaşmazlık listesiyle birlikte oluşturulur, "uzlasi" sütunu doldurulur

Hesaplananlar:
    - Cohen kappa ve ham uyum (ilgililik, iki kodlayıcının da kodladığı kayıtlar)
    - Arama kesinliği: uzlaşı sonrası (varsa), yoksa kodlayıcı bazında; %95 Wilson aralığı
    - D1: alan bazında doğruluk = 1 / (1 + 0), NA hariç; %95 Wilson aralığı
    - Yazar kimliği: aynı ada sahip kimliklerin ne kadarı aynı kişi (bölünmüş profil); kural önerisinin
      (ORCID / ortak kurum) kodlamaya göre doğruluğu; tam ad, TR Dizin kimliği ve kimlik + kural ile
      yazar saymanın B-cubed kesinlik, duyarlılık ve F1 değerleri

Çalıştırma:

    python paper/scripts/05_agreement.py
    python paper/scripts/05_agreement.py --dataset fa_alan

Çıktılar:
    paper/results/agreement_<tarih>/agreement.json, d1_accuracy.csv   (yalnızca sayılar)
    paper/data/coding/uzlasi.csv   anlaşmazlıklar ve uzlaşı sütunu (başlık içerir, depoya girmez)
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402

from bibliotr.normalize import fold  # noqa: E402

REL = "İlgili (1/0)"
SAME = "Grubun 1. kimliğiyle aynı kişi (1/0/NA)"
D1_FIELDS = ["Başlık", "Yazarlar", "Yazar sırası", "Yıl", "Dergi", "Kurum", "DOI"]


def slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", fold(name)).strip("_") or "dataset"


def norm_code(v, allow_na: bool = False) -> str | None:
    """Hücre değerini '1', '0', 'NA' ya da None'a çevirir; geçersiz değer hata verir."""
    if v is None or (isinstance(v, str) and not v.strip()):
        return None
    s = str(v).strip().upper()
    if s in ("1", "1.0"):
        return "1"
    if s in ("0", "0.0"):
        return "0"
    if allow_na and s in ("NA", "N/A", "YOK"):
        return "NA"
    raise ValueError(f"Geçersiz kod: {v!r}")


def read_sheet(path: Path) -> dict[str, dict]:
    """Kodlama sayfasını {TR Dizin ID: {sütun: değer}} olarak okur."""
    ws = load_workbook(path, data_only=True)["Kodlama"]
    head = [c.value for c in ws[1]]
    out = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        row = dict(zip(head, r))
        rid = row.get("TR Dizin ID")
        if rid is not None:
            out[str(rid)] = row
    return out


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float | None, float | None]:
    if n == 0:
        return None, None
    p = k / n
    den = 1 + z * z / n
    mid = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return round(max(0.0, mid - half), 4), round(min(1.0, mid + half), 4)


def cohen_kappa(a: list[str], b: list[str]) -> dict:
    """İkili kodlar için Cohen kappa ve ham uyum."""
    n = len(a)
    if n == 0:
        return {"n": 0, "observed_agreement": None, "kappa": None}
    po = sum(x == y for x, y in zip(a, b)) / n
    cats = sorted(set(a) | set(b))
    pe = sum((a.count(c) / n) * (b.count(c) / n) for c in cats)
    kappa = None if pe == 1 else (po - pe) / (1 - pe)
    return {"n": n, "observed_agreement": round(po, 4), "expected_agreement": round(pe, 4),
            "kappa": None if kappa is None else round(kappa, 4)}


def proportion(codes: list[str]) -> dict:
    k, n = codes.count("1"), sum(1 for c in codes if c in ("1", "0"))
    lo, hi = wilson(k, n)
    return {"n": n, "relevant": k, "precision": round(k / n, 4) if n else None, "ci95": [lo, hi]}


def read_consensus(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    out = {}
    with open(path, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            code = norm_code(row.get("uzlasi"))
            if code is not None:
                out[row["id"]] = code
    return out


def bcubed(truth: dict[str, str], pred: dict[str, str]) -> dict:
    """B-cubed kesinlik, duyarlılık ve F1 (öğe başına ortalama). truth/pred: öğe -> küme etiketi."""
    items = [i for i in truth if i in pred]
    if not items:
        return {"n": 0, "precision": None, "recall": None, "f1": None}
    t_members: dict[str, set] = {}
    p_members: dict[str, set] = {}
    for i in items:
        t_members.setdefault(truth[i], set()).add(i)
        p_members.setdefault(pred[i], set()).add(i)
    prec = rec = 0.0
    for i in items:
        t, q = t_members[truth[i]], p_members[pred[i]]
        both = len(t & q)
        prec += both / len(q)
        rec += both / len(t)
    prec, rec = prec / len(items), rec / len(items)
    f1 = 2 * prec * rec / (prec + rec) if prec + rec else 0.0
    return {"n": len(items), "precision": round(prec, 4), "recall": round(rec, 4), "f1": round(f1, 4)}


def author_identity(path: Path, groups_csv: Path | None) -> dict | None:
    """kodlama_yazar.xlsx hükümlerini özetler; varsa kural önerisiyle karşılaştırır ve
    yazar sayma yollarını B-cubed ile puanlar."""
    if not path.exists():
        return None
    ws = load_workbook(path, data_only=True)["Kodlama"]
    head = [c.value for c in ws[1]]
    codes: dict[str, str | None] = {}
    groups: dict[str, list[str]] = {}
    refs: dict[str, str] = {}
    for r in ws.iter_rows(min_row=2, values_only=True):
        row = dict(zip(head, r))
        v = row.get(SAME)
        key, grp = str(row.get("Yazar kimliği")), str(row.get("Grup"))
        if isinstance(v, str) and v.strip().lower() == "referans":
            refs[grp] = key
            continue
        codes[key] = norm_code(v, allow_na=True)
        groups.setdefault(grp, []).append(key)
    coded = [c for c in codes.values() if c is not None]
    k, n0, na = coded.count("1"), coded.count("0"), coded.count("NA")
    lo, hi = wilson(k, k + n0)
    out = {"comparisons": len(codes), "coded": len(coded), "same_person": k, "different_person": n0, "na": na,
           "split_share": round(k / (k + n0), 4) if k + n0 else None, "ci95": [lo, hi],
           "groups": len(groups),
           "groups_all_same": sum(1 for ks in groups.values()
                                  if ks and all(codes.get(x) == "1" for x in ks))}
    rule: dict[str, dict] = {}
    if groups_csv and groups_csv.exists():
        with open(groups_csv, encoding="utf-8-sig") as f:
            rule = {r["author_key"]: r for r in csv.DictReader(f) if r["rule_basis"] != "reference"}
        by_basis: dict[str, dict] = {}
        for key, c in codes.items():
            r = rule.get(key)
            if r is None or c not in ("1", "0"):
                continue
            b = by_basis.setdefault(r["rule_basis"], {"n": 0, "rule_correct": 0, "coded_same": 0})
            b["n"] += 1
            b["coded_same"] += c == "1"
            b["rule_correct"] += r["rule"] == c
        out["rule_vs_coding"] = by_basis

    # B-cubed: yalnız tüm üyeleri 1/0 kodlanmış gruplar (NA'lı üye gruptan çıkarılır).
    truth, pred = {}, {"full_name": {}, "trdizin_id": {}, "id_rule": {}}
    for g, members in groups.items():
        ref = refs.get(g)
        if ref is None:
            continue
        kept = [m for m in members if codes.get(m) in ("1", "0")]
        if not kept:
            continue
        for item in [ref] + kept:
            truth[item] = f"{g}:ref" if item == ref or codes.get(item) == "1" else f"{g}:{item}"
            pred["full_name"][item] = g
            pred["trdizin_id"][item] = item
            if rule:
                same = item == ref or (rule.get(item) or {}).get("rule") == "1"
                pred["id_rule"][item] = f"{g}:ref" if same else f"{g}:{item}"
    out["bcubed"] = {name: bcubed(truth, p) for name, p in pred.items() if p}
    out["bcubed_note"] = ("Kısa ad bu örneklemde tam adla aynı kümeleri verir (aynı tam ad aynı kısa adı "
                          "taşır); kısa adın ek birleştirmeleri kodlanmadığı için ayrıca puanlanmaz.")
    return out


def run(coding_dir: Path, outdir: Path, groups_csv: Path | None = None) -> dict:
    k1_path, k2_path = coding_dir / "kodlama_K1.xlsx", coding_dir / "kodlama_K2.xlsx"
    if not k1_path.exists():
        sys.exit(f"Kodlama dosyası bulunamadı: {k1_path}")
    k1 = read_sheet(k1_path)
    k2 = read_sheet(k2_path) if k2_path.exists() else {}
    c1 = {i: norm_code(r.get(REL)) for i, r in k1.items()}
    c2 = {i: norm_code(r.get(REL)) for i, r in k2.items()}
    result: dict = {"computed_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    "n_sample": len(k1), "coded_k1": sum(v is not None for v in c1.values()),
                    "coded_k2": sum(v is not None for v in c2.values())}

    both = [i for i in k1 if c1.get(i) is not None and c2.get(i) is not None]
    result["agreement"] = cohen_kappa([c1[i] for i in both], [c2[i] for i in both])
    result["precision_k1"] = proportion([c for c in c1.values() if c])
    if k2:
        result["precision_k2"] = proportion([c for c in c2.values() if c])

    disagreements = [i for i in both if c1[i] != c2[i]]
    cons_path = coding_dir / "uzlasi.csv"
    consensus = read_consensus(cons_path)
    if disagreements and not cons_path.exists():
        with open(cons_path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(["id", "baslik_tr", "K1", "K2", "K1_notu", "K2_notu", "uzlasi"])
            for i in disagreements:
                w.writerow([i, k1[i].get("Başlık (TR)") or k1[i].get("Başlık (EN)") or "", c1[i], c2[i],
                            k1[i].get("İlgililik notu") or "", k2[i].get("İlgililik notu") or "", ""])
    result["disagreements"] = len(disagreements)
    unresolved = [i for i in disagreements if i not in consensus]
    result["unresolved"] = len(unresolved)
    final_codes: dict[str, str] = {}
    if both and not unresolved:
        final_codes = {i: consensus.get(i, c1[i]) for i in both}
        result["precision_final"] = proportion(list(final_codes.values()))

    d1_path = coding_dir / "kodlama_D1.xlsx"
    d1_sheet = read_sheet(d1_path) if d1_path.exists() else k1
    result["d1_source"] = d1_path.name if d1_path.exists() else k1_path.name
    result["n_d1"] = len(d1_sheet)
    d1_rows = []
    for f in D1_FIELDS:
        codes = [norm_code(r.get(f"{f} doğru"), allow_na=True) for r in d1_sheet.values()]
        k, n0, na = codes.count("1"), codes.count("0"), codes.count("NA")
        n = k + n0
        lo, hi = wilson(k, n)
        d1_rows.append({"field": f, "correct": k, "incorrect": n0, "na": na, "n": n,
                        "accuracy": round(k / n, 4) if n else None, "ci95_low": lo, "ci95_high": hi})
    result["d1"] = d1_rows
    ai = author_identity(coding_dir / "kodlama_yazar.xlsx", groups_csv)
    if ai is not None:
        result["author_identity"] = ai

    outdir.mkdir(parents=True, exist_ok=True)
    if final_codes:
        # Kesin ilgili küme (yalnız kimlikler): kullanım örneği ve harita bu listeyle yeniden hesaplanır.
        rel = sorted((i for i, c in final_codes.items() if c == "1"), key=lambda x: (len(x), x))
        out_ = sorted((i for i, c in final_codes.items() if c == "0"), key=lambda x: (len(x), x))
        (outdir / "relevant_ids.txt").write_text("\n".join(rel) + "\n", encoding="utf-8")
        (outdir / "not_relevant_ids.txt").write_text("\n".join(out_) + "\n", encoding="utf-8")
        result["relevant_ids_file"] = "relevant_ids.txt"
    (outdir / "agreement.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    with open(outdir / "d1_accuracy.csv", "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(d1_rows[0]))
        w.writeheader()
        w.writerows(d1_rows)

    a = result["agreement"]
    print(f"Örneklem {result['n_sample']}; kodlanan K1 {result['coded_k1']}, K2 {result['coded_k2']}")
    if a["n"]:
        print(f"Uyum (n={a['n']}): ham %{100 * a['observed_agreement']:.1f}, Cohen kappa {a['kappa']}")
        print(f"Anlaşmazlık {result['disagreements']}, uzlaşı bekleyen {result['unresolved']}"
              + (f" -> {cons_path}" if result["unresolved"] else ""))
    for key, label in (("precision_final", "Arama kesinliği (uzlaşı)"), ("precision_k1", "Kesinlik K1"),
                       ("precision_k2", "Kesinlik K2")):
        p = result.get(key)
        if p and p["n"]:
            print(f"{label}: {p['relevant']}/{p['n']} = %{100 * p['precision']:.1f} "
                  f"(%95 GA {100 * p['ci95'][0]:.1f}-{100 * p['ci95'][1]:.1f})")
    print(f"\nD1 alan doğruluğu ({result['d1_source']}, n={result['n_d1']}):")
    for r in d1_rows:
        acc = f"%{100 * r['accuracy']:.1f}" if r["accuracy"] is not None else "-"
        print(f"  {r['field']:14} {r['correct']:>3}/{r['n']:<3} {acc:>7}  (NA {r['na']})")
    if ai is not None:
        print(f"\nYazar kimliği: {ai['coded']}/{ai['comparisons']} karşılaştırma kodlandı; aynı kişi "
              f"{ai['same_person']}, farklı {ai['different_person']}, NA {ai['na']}")
        for basis, b in (ai.get("rule_vs_coding") or {}).items():
            print(f"  kural ({basis}): {b['rule_correct']}/{b['n']} doğru")
        labels = {"full_name": "tam ad", "trdizin_id": "TR Dizin kimliği", "id_rule": "kimlik + kural"}
        for name, b in ai.get("bcubed", {}).items():
            if b["n"]:
                print(f"  B-cubed {labels[name]:18} P {b['precision']:.3f}  R {b['recall']:.3f}  F1 {b['f1']:.3f}"
                      f"  (n={b['n']})")
    print(f"\nÇıktılar: {outdir}")
    return result


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dataset", default="fa_alan", help="Veri seti adı; kodlama klasörü coding/<veri seti>/")
    ap.add_argument("--coding", default=None)
    ap.add_argument("--freeze-dir", default=None, help="author_groups.csv içeren dondurma özeti klasörü")
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)
    name = slug(args.dataset) if args.dataset else None
    coding = Path(args.coding or ROOT / "paper" / "data" / "coding" / (name or ""))
    freeze_dir = Path(args.freeze_dir) if args.freeze_dir else None
    if freeze_dir is None and name:
        found = sorted((ROOT / "paper" / "results").glob(f"freeze_{name}_*"))
        freeze_dir = found[-1] if found else None
    tag = f"{name}_" if name else ""
    out = Path(args.out or ROOT / "paper" / "results" / f"agreement_{tag}{datetime.now():%Y%m%d}")
    run(coding, out, freeze_dir / "author_groups.csv" if freeze_dir else None)


if __name__ == "__main__":
    main()
