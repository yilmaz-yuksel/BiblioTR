"""paper/scripts/11_reference_coding.py ve 07/08 betiklerinin veri seti genellemesi için testler."""

import importlib.util
import sys
from pathlib import Path

from openpyxl import load_workbook

from bibliotr import openalex
from bibliotr.storage import Store

ROOT = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "paper" / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


rc = _load("refs11", "11_reference_coding.py")
oa7 = _load("oa07", "07_openalex.py")
tc8 = _load("tc08b", "08_title_match_check.py")


def _rec(rid, title, year, doi=None, refs=(), cited_by=(), doc="PAPER"):
    return {"id": rid, "title": title, "publicationYear": year, "doi": doi, "documentType": doc,
            "journal": {"name": "Dergi"}, "authors": [{"authorId": 10 + rid, "inPublicationName": "Deniz Kaya"}],
            "references": [{"text": t, "targetId": tid} for t, tid in refs],
            "citedReferences": [{"sourceId": str(c)} for c in cited_by]}


def _db(path):
    recs = [
        _rec(1, "Birinci yayin", 2020, doi="10.1000/a1"),
        _rec(2, "Ikinci yayin", 2021, doi="10.1000/a2", cited_by=[3]),
        _rec(3, "Ucuncu yayin", 2022, refs=[
            ("Kaya D. Ikinci yayin. Dergi. 2021;3:10-20.", 2),                      # TR Dizin bağı
            ("Kaya, D. (2020). Birinci yayin. Dergi, 2(1), 5-9. doi:10.1000/a1", None),  # yalnız DOI ile bağ
            ("Arslan E. Baska bir calisma. Spor Dergisi. 2019;7:1-8.", None),
            ("Spor raporu, Ankara, 2018", None),
        ]),
        _rec(4, "Proje kaydi", 2022, doc="PROJECT"),
    ]
    with Store(path) as s:
        s.create_dataset("fa alan")
        s.add_records("fa alan", recs)
    return path


def test_link_and_reference_selection(tmp_path):
    with Store(_db(tmp_path / "t.db"), readonly=True) as s:
        pubs = [p for p in s.publications("fa alan") if p.document_type == "PAPER"]
    links = rc.link_rows(pubs, 10, 1)
    kinds = sorted((r["kind"], r["citing_id"], r["cited_id"]) for r in links)
    assert kinds == [("DOI ile", "3", "1"), ("TR Dizin", "3", "2")]
    refs = rc.reference_rows(pubs, {"vancouver": 5, "apa": 5, "other": 5}, 1)
    # "Kaya D. Ikinci yayin..." geliştirme payında (is_dev), örnekleme girmez
    assert sorted(r["stratum"] for r in refs) == ["apa", "other", "vancouver"]
    assert not any(rc.is_dev(r["text"]) for r in refs)
    apa = next(r for r in refs if r["stratum"] == "apa")
    assert all("..." not in r["cited_authors"] for r in links)  # yazar listesi kısaltılmaz
    assert apa["year"] == "2020" and apa["doi"] == "10.1000/a1" and apa["cr"].startswith("Kaya D, 2020")


def test_build_and_score(tmp_path):
    with Store(_db(tmp_path / "t.db"), readonly=True) as s:
        pubs = [p for p in s.publications("fa alan") if p.document_type == "PAPER"]
    path = tmp_path / "k.xlsx"
    rc.build(path, rc.link_rows(pubs, 10, 1), rc.reference_rows(pubs, rc.STRATA, 1), "fa alan", 1)
    wb = load_workbook(path)
    assert wb.sheetnames == ["Yönerge", "Bağlar", "Kaynaklar"]
    ws = wb["Bağlar"]
    head = [c.value for c in ws[1]]
    col = head.index(rc.LINK_CODE) + 1
    ws.cell(row=2, column=col, value=1)
    ws.cell(row=3, column=col, value=0)
    ws = wb["Kaynaklar"]
    head = [c.value for c in ws[1]]
    ws.cell(row=2, column=head.index("Yıl doğru") + 1, value=1)
    ws.cell(row=3, column=head.index("Yıl doğru") + 1, value="NA")
    ws.cell(row=2, column=head.index("DOI doğru") + 1, value=0)
    wb.save(path)
    res = rc.score(path)
    assert res["links"]["DOI ile"]["correct"] + res["links"]["TR Dizin"]["correct"] == 1
    assert res["references"]["fields"]["Yıl"] == {"correct": 1, "incorrect": 0, "na": 1, "n": 1, "accuracy": 1.0,
                                                  "ci95": rc.wilson(1, 1)}
    assert res["references"]["fields"]["DOI"]["incorrect"] == 1


def test_main_requires_frozen_db_and_keeps_sheet(tmp_path, monkeypatch):
    import pytest

    monkeypatch.setattr(rc, "ROOT", tmp_path)
    with pytest.raises(SystemExit, match="Dondurulmuş"):
        rc.main(["--dataset", "fa alan"])
    data = tmp_path / "paper" / "data"
    data.mkdir(parents=True)
    _db(data / "fa_alan_frozen_20261002.db")
    rc.main(["--dataset", "fa alan"])
    sheet = data / "coding" / "fa_alan" / "kodlama_kaynakca.xlsx"
    assert sheet.exists()
    with pytest.raises(SystemExit, match="zaten var"):
        rc.main(["--dataset", "fa alan"])


def test_openalex_scripts_use_stored_matches(tmp_path):
    db = _db(tmp_path / "fa_alan_frozen_x_openalex.db")
    with Store(db) as s:
        s.save_openalex([
            openalex.OpenAlexMatch("1", "https://openalex.org/W1", 4, 2020, "10.1000/a1", "article", "tr", "doi"),
            openalex.OpenAlexMatch("3", "https://openalex.org/W3", 2, 2022, None, "article", "tr", "title", 0.95,
                                   "Ucuncu yayin"),
            openalex.OpenAlexMatch("4", "https://openalex.org/W4", 0, 2022, None, "article", "tr", "title", 0.99,
                                   "Proje kaydi"),
        ])
    res = oa7.run(db, tmp_path / "out", dataset="fa alan", doc_types=("PAPER",))
    assert res["source"] == "stored matches" and res["n_records"] == 3
    assert res["found_by"] == {"doi": 1, "title": 1} and res["without_doi_found_by_title"] == 1
    assert res["total_citations"]["openalex_direct"] == 6
    rows = tc8.title_matches(db, "fa alan", ("PAPER",))
    assert [r["id"] for r in rows] == ["3"] and rows[0]["journal"] == "Dergi"  # proje kaydı süzüldü


def test_refs_only_keeps_coded_links(tmp_path):
    with Store(_db(tmp_path / "t.db"), readonly=True) as s:
        pubs = [p for p in s.publications("fa alan") if p.document_type == "PAPER"]
    path = tmp_path / "k.xlsx"
    links = rc.link_rows(pubs, 10, 1)
    rc.build(path, links, rc.reference_rows(pubs, rc.STRATA, 1), "fa alan", 1)
    wb = load_workbook(path)
    ws = wb["Bağlar"]
    col = [c.value for c in ws[1]].index(rc.LINK_CODE) + 1
    ws.cell(row=2, column=col, value=1)
    ws.cell(row=3, column=col, value=0)
    wb.save(path)
    rc.build(path, links, rc.reference_rows(pubs, rc.STRATA, 2), "fa alan", 1, ref_seed=2, keep_links=True)
    wb = load_workbook(path)
    assert wb.sheetnames == ["Yönerge", "Bağlar", "Kaynaklar"]
    ws = wb["Bağlar"]
    assert [ws.cell(row=r, column=col).value for r in (2, 3)] == [1, 0]
    assert ws.cell(row=2, column=[c[0] for c in rc.LINK_COLS].index("Bağlantı") + 1).value == "Aç"
    assert "tohum 2" in str(wb["Yönerge"]["B7"].value)


def test_dev_split_excluded():
    texts = [f"Yazar A. Baslik {i}. Dergi. 2020;{i}:1-5." for i in range(200)]
    dev = [t for t in texts if rc.is_dev(t)]
    assert 5 < len(dev) < 40  # yaklaşık onda biri


def test_recheck_and_after_fix_score(tmp_path):
    from openpyxl import Workbook

    from bibliotr.normalize import normalize_publication

    text = "Referans3. Yıldız K, Demir D. Uyku ve egzersiz. Spor Derg. 2006;12(1):28-34."
    pub = normalize_publication({"id": 7, "title": "T", "publicationYear": 2020, "documentType": "PAPER",
                                 "journal": {"name": "Dergi"}, "references": [{"text": text}]})
    sheet = tmp_path / "kodlama_kaynakca.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Kaynaklar"
    head = [c[0] for c in rc.REF_COLS]
    ws.append(head)
    row = {"No": 1, "Biçim": "vancouver", "Atıf yapan ID": "7", "Kaynak metni": text,
           "İlk yazar (araç)": "Referans3 Y", "Yıl (araç)": "2006", "Kaynak adı (araç)": "Spor Derg",
           "Cilt (araç)": "12", "Sayfa (araç)": "28", "DOI (araç)": None,
           "İlk yazar doğru": 0, "Yıl doğru": 1, "Kaynak adı doğru": 1, "Cilt doğru": 1, "Sayfa doğru": 1,
           "DOI doğru": "NA"}
    ws.append([row.get(h) for h in head])
    wb.save(sheet)
    rows = rc.recheck_rows(sheet, [pub])
    assert [(r["field"], r["old"], r["new"], r["code"]) for r in rows] == [("İlk yazar", "Referans3 Y", "Yıldız K", "0")]
    redo = tmp_path / "kodlama_kaynakca_ek.xlsx"
    rc.build_recheck(redo, rows)
    codes = rc.recheck_codes(rc.recheck_files(sheet))
    assert rc.score_after(sheet, rows, codes) == {"complete": False, "missing": ["1 İlk yazar"]}
    wb = load_workbook(redo)
    ws = wb["Değişenler"]
    ws.cell(row=2, column=[c[0] for c in rc.RECHECK_COLS].index("Yeni değer doğru") + 1, value=1)
    wb.save(redo)
    after = rc.score_after(sheet, rows, rc.recheck_codes(rc.recheck_files(sheet)))
    assert after["changed_cells"] == 1 and after["fields"]["İlk yazar"]["accuracy"] == 1.0
    assert after["fields"]["DOI"]["na"] == 1
    # Ayrıştırıcı yeniden değişirse eski kod yeni değere taşınmaz: hücre yeniden kodlanmalı.
    moved = [dict(rows[0], new="Yıldız KX")]
    assert rc.pending_recheck(moved, rc.recheck_codes(rc.recheck_files(sheet))) == moved


def test_new_doi_link_recheck_and_score(tmp_path):
    from openpyxl import Workbook

    sheet = tmp_path / "kodlama_kaynakca.xlsx"
    wb = Workbook()
    ws = wb.active
    ws.title = "Bağlar"
    head = [c[0] for c in rc.LINK_COLS]
    ws.append(head)
    for no, a, b, code in ((1, "3", "1", 1), (2, "5", "2", 0)):  # 5->2 düzeltmeyle kalkan yanlış bağ
        row = {"No": no, "Bağ türü": "DOI ile", "Atıf yapan ID": a, "Bağlanan ID": b, rc.LINK_CODE: code}
        ws.append([row.get(h) for h in head])
    wb.create_sheet("Kaynaklar").append([c[0] for c in rc.REF_COLS])
    wb.save(sheet)

    def link(a, b):
        return {"kind": "DOI ile", "citing_id": a, "citing_year": 2025, "citing_title": "T", "reference": "R",
                "cited_id": b, "cited_year": 2022, "cited_title": "C", "cited_journal": "Dergi",
                "cited_authors": "Deniz Kaya", "cited_doi": "10.1000/x", "cited_url": ""}

    now = [link("3", "1"), link("6", "4")]
    new = rc.new_doi_links(sheet, now)
    assert [(r["citing_id"], r["cited_id"]) for r in new] == [("6", "4")]
    res = rc.score_links_after(sheet, now, rc.recheck_link_codes(rc.recheck_files(sheet)))
    assert not res["complete"] and res["missing"] == ["6->4"]
    redo = tmp_path / "kodlama_kaynakca_ek.xlsx"
    rc.build_recheck(redo, [], new)
    wb = load_workbook(redo)
    assert wb.active.title == "Bağlar"
    wb["Bağlar"].cell(row=2, column=head.index(rc.LINK_CODE) + 1, value=1)
    wb.save(redo)
    res = rc.score_links_after(sheet, now, rc.recheck_link_codes(rc.recheck_files(sheet)))
    assert res["complete"] and (res["correct"], res["n"], res["new_links"]) == (2, 2, 1)
    assert res["removed"] == [{"link": "5->2", "first_code": "0"}]
