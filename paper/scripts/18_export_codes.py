"""Elle kodlamaları kayıt kimliğiyle, kayıt içeriği olmadan dışa aktarır (tekrar paketi).

Kodlama tabloları TR Dizin kayıtlarının başlık, özet, yazar ve kaynak metinlerini içerir; TR Dizin'in kullanım
koşulları bunların yeniden dağıtılmasına izin vermez. Bu betik her tablodan yalnızca kimlikleri ve kodları alır;
başlık, özet, yazar adı, kurum, kaynak metni ve serbest metin notları dışarıda kalır. Okuyucu kayıtları
kimliklerle TR Dizin'den çeker, kodları bu dosyalardan okur.

    python paper/scripts/18_export_codes.py --dataset fa_alan

Çıktı: paper/results/codes_<veri seti>/ altında CSV dosyaları ve bir README.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from openpyxl import load_workbook  # noqa: E402


def _rows(path: Path, sheet: str) -> tuple[list[str], list[tuple]]:
    ws = load_workbook(path, read_only=True, data_only=True)[sheet]
    it = ws.iter_rows(values_only=True)
    head = [str(c) if c is not None else "" for c in next(it)]
    return head, [r for r in it if any(v is not None for v in r)]


def _code(v) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s.upper() if s.lower() == "na" else s


def pick(path: Path, sheet: str, columns: dict[str, str], codes: set[str], key: str | None = None) -> list[dict]:
    """`columns`: tablo sütunu -> çıktı sütunu. `codes` içindeki çıktı sütunları kod olarak temizlenir."""
    head, rows = _rows(path, sheet)
    idx = {out: head.index(src) for src, out in columns.items()}
    out = []
    for r in rows:
        rec = {k: (_code(r[i]) if k in codes else ("" if r[i] is None else str(r[i]).strip())) for k, i in idx.items()}
        if key and not rec[key]:
            continue
        out.append(rec)
    return out


def write(path: Path, rows: list[dict]) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["empty"])
        w.writeheader()
        w.writerows(rows)
    return len(rows)


def export(coding: Path, out: Path) -> dict:
    n = {}
    # V2: ilgililik, iki kör kodlayıcı ve uzlaşı
    k1 = {r["id"]: r["code"] for r in pick(coding / "kodlama_K1.xlsx", "Kodlama", {"TR Dizin ID": "id", "İlgili (1/0)": "code"}, {"code"}, "id")}
    k2 = {r["id"]: r["code"] for r in pick(coding / "kodlama_K2.xlsx", "Kodlama", {"TR Dizin ID": "id", "İlgili (1/0)": "code"}, {"code"}, "id")}
    with open(coding / "uzlasi.csv", encoding="utf-8-sig", newline="") as f:
        consensus = {r["id"]: r["uzlasi"] for r in csv.DictReader(f)}
    rel = [{"id": i, "coder1": k1[i], "coder2": k2.get(i, ""),
            "final": consensus.get(i, k1[i] if k1[i] == k2.get(i) else "")} for i in k1]
    n["relevance_V2.csv"] = write(out / "relevance_V2.csv", rel)
    # V3: alan doğruluğu
    d1_cols = {"TR Dizin ID": "id", "Başlık doğru": "title", "Yazarlar doğru": "authors", "Yazar sırası doğru": "author_order",
               "Yıl doğru": "year", "Dergi doğru": "journal", "Kurum doğru": "institution", "DOI doğru": "doi"}
    n["fields_V3.csv"] = write(out / "fields_V3.csv", pick(coding / "kodlama_D1.xlsx", "Kodlama", d1_cols,
                                                          set(d1_cols.values()) - {"id"}, "id"))
    # V4: atıf bağları ve kaynak alanları (kaynak metni yok; satırlar 11_reference_coding.py'nin sabit tohumlu sırasıdır)
    link_cols = {"No": "row", "Bağ türü": "kind", "Atıf yapan ID": "citing_id", "Bağlanan ID": "cited_id", "Aynı yayın (1/0)": "code"}
    links = pick(coding / "kodlama_kaynakca.xlsx", "Bağlar", link_cols, {"code"}, "citing_id")
    extra = coding / "kodlama_kaynakca_ek3.xlsx"
    if extra.exists():
        links += [{**r, "row": f"ek3-{r['row']}"} for r in pick(extra, "Bağlar", link_cols, {"code"}, "citing_id")]
    n["links_V4.csv"] = write(out / "links_V4.csv", links)
    ref_cols = {"No": "row", "Biçim": "style", "Atıf yapan ID": "citing_id", "İlk yazar doğru": "first_author",
                "Yıl doğru": "year", "Kaynak adı doğru": "source", "Cilt doğru": "volume", "Sayfa doğru": "page", "DOI doğru": "doi"}
    n["references_V4.csv"] = write(out / "references_V4.csv", pick(coding / "kodlama_kaynakca.xlsx", "Kaynaklar", ref_cols,
                                                                  {"first_author", "year", "source", "volume", "page", "doi"}, "citing_id"))
    rechecks = []
    for name in ("kodlama_kaynakca_ek.xlsx", "kodlama_kaynakca_ek2.xlsx", "kodlama_kaynakca_ek3.xlsx"):
        if (coding / name).exists():
            rechecks += [{"sheet": name.removesuffix(".xlsx"), **r} for r in pick(
                coding / name, "Değişenler", {"No": "row", "Alan": "field", "Biçim": "style", "İlk kod": "first_code",
                                              "Yeni değer doğru": "code"}, {"first_code", "code"}, "row")]
    n["references_recheck_V4.csv"] = write(out / "references_recheck_V4.csv", rechecks)
    # V5: yazar kimliği, iki kodlayıcı
    col = "Grubun 1. kimliğiyle aynı kişi (1/0/NA)"
    c2 = {r["author_key"]: r["code"] for r in pick(coding / "kodlama_yazar_K2.xlsx", "Kodlama", {"Yazar kimliği": "author_key", col: "code"}, {"code"}, "author_key")}
    authors = [{**r, "coder2": c2.get(r["author_key"], "") if r["coder1"].upper() != "REFERANS" else ""}
               for r in pick(coding / "kodlama_yazar.xlsx", "Kodlama", {"Grup": "group", "Yazar kimliği": "author_key", col: "coder1"}, {"coder1"}, "author_key")]
    for r in authors:
        r["coder1"] = "reference" if r["coder1"].upper() == "REFERANS" else r["coder1"]
    n["authors_V5.csv"] = write(out / "authors_V5.csv", authors)
    # V7: OpenAlex başlık eşleşmeleri
    if (coding / "openalex_title_check.xlsx").exists():
        n["openalex_titles_V7.csv"] = write(out / "openalex_titles_V7.csv", pick(
            coding / "openalex_title_check.xlsx", "Kontrol", {"TR Dizin ID": "id", "Doğru eşleşme (1/0)": "code"},
            {"code"}, "id"))
    # Anahtar kelime eş anlam kararları (anahtar kelimeler sözlük terimidir, kayıt içeriği değil)
    syn_cols = {"Grup": "group", "Tür": "kind", "Hedef (yayın)": "target", "Birleşecek biçim (yayın)": "variant",
                "Birleştir (1/0)": "merge", "Hedef yazım (isteğe bağlı)": "target_spelling"}
    syn = []
    for name in ("kodlama_esanlam.xlsx", "kodlama_esanlam_ek.xlsx"):
        syn += [{"sheet": name.removesuffix(".xlsx"), **r} for r in pick(coding / name, "Öneriler", syn_cols, {"merge"}, "target")]
    if (coding / "kodlama_esanlam_elle.csv").exists():
        with open(coding / "kodlama_esanlam_elle.csv", encoding="utf-8-sig", newline="") as f:
            syn += [{"sheet": "kodlama_esanlam_elle", "group": "", "kind": "Elle", "target": r["hedef_yazim"],
                     "variant": r["birlesecek_yazim"], "merge": "1", "target_spelling": ""} for r in csv.DictReader(f)]
    n["keyword_merges.csv"] = write(out / "keyword_merges.csv", syn)
    # Atıf temelli duyarlılık
    cols = {"TR Dizin ID": "id", "Yön": "direction", "İlgili (1/0)": "code"}
    recall = pick(coding / "kodlama_kacirilan.xlsx", "Kodlama", cols, {"code"}, "id")
    first = coding / "kodlama_kacirilan_ilk_gecis.xlsx"  # kodlayıcının ilk puanlamadan önceki geçişi (protokole bakınız)
    if first.exists():
        before = {r["id"]: r["code"] for r in pick(first, "Kodlama", cols, {"code"}, "id")}
        recall = [{**r, "first_pass": before.get(r["id"], "")} for r in recall]
    n["citation_recall.csv"] = write(out / "citation_recall.csv", recall)
    readme = (
        "Manual codes, keyed by TR Dizin record identifier (https://search.trdizin.gov.tr/tr/yayin/detay/<id>).\n"
        "Titles, abstracts, author names, institutions, reference texts and free-text notes are left out because\n"
        "TR Dizin's terms do not allow records to be redistributed. Rows of links_V4, references_V4 and\n"
        "references_recheck_V4 follow the fixed-seed order of paper/scripts/11_reference_coding.py.\n"
        "citation_recall: `code` is the final judgement used in the paper; `first_pass` is the coder's first pass,\n"
        "before a review of the sheet (see paper/protocol/04_validation_protocol_20261002.md).\n"
        "Codes: 1 = yes / correct / relevant, 0 = no, NA = not applicable or undecided, reference = the first\n"
        "identity of an author group.\n\n" + "\n".join(f"{k}: {v} rows" for k, v in n.items()) + "\n")
    (out / "README.txt").write_text(readme, encoding="utf-8")
    return n


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dataset", required=True)
    args = ap.parse_args(argv)
    name = args.dataset.replace(" ", "_")
    res = export(ROOT / "paper" / "data" / "coding" / name, ROOT / "paper" / "results" / f"codes_{name}")
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
