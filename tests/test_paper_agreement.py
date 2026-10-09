"""paper/scripts/05_agreement.py için testler."""

import csv
import importlib.util
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

ROOT = Path(__file__).resolve().parents[1]


def _load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "paper" / "scripts" / file)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


fz = _load("freeze04", "04_freeze_and_sample.py")
ag = _load("agree05", "05_agreement.py")


def test_kappa_and_wilson():
    k = ag.cohen_kappa(list("110010"), list("100011"))
    assert k["observed_agreement"] == pytest.approx(0.6667, abs=1e-4) and k["kappa"] == pytest.approx(0.3333, abs=1e-4)
    assert ag.cohen_kappa(list("111"), list("111"))["kappa"] is None  # beklenen uyum 1
    lo, hi = ag.wilson(90, 100)
    assert lo == pytest.approx(0.8256, abs=1e-3) and hi == pytest.approx(0.9448, abs=1e-3)
    assert ag.wilson(0, 0) == (None, None)
    assert ag.norm_code(1.0) == "1" and ag.norm_code(" na ", allow_na=True) == "NA" and ag.norm_code("") is None
    with pytest.raises(ValueError):
        ag.norm_code("2")


def _book(path, codes, d1=None):
    rows = []
    for no, c in enumerate(codes, 1):
        vals = [no, str(100 + no), "", 2024, "Dergi", f"Başlık {no}", "", "", "", ""] + [None, None]
        if d1 is not None:
            vals += ["", "", ""] + [None] * (len(fz.D1_FIELDS) + 1)
        rows.append(vals)
    fz.build_workbook(path, rows, path.stem, d1 is not None)
    wb = load_workbook(path)
    ws = wb["Kodlama"]
    head = [c.value for c in ws[1]]
    for r, c in enumerate(codes, 2):
        ws.cell(row=r, column=head.index("İlgili (1/0)") + 1, value=c)
        if d1 is not None:
            for f, v in d1[r - 2].items():
                ws.cell(row=r, column=head.index(f"{f} doğru") + 1, value=v)
    wb.save(path)


def test_run(tmp_path):
    cod = tmp_path / "coding"
    cod.mkdir()
    d1 = [{"Başlık": 1, "Kurum": 1}, {"Başlık": 1, "Kurum": 0}, {"Başlık": 1, "Kurum": "NA"},
          {"Başlık": 0, "Kurum": 1}, {"Başlık": 1}, {"Başlık": 1}]
    _book(cod / "kodlama_K1.xlsx", [1, 1, 0, 0, 1, 0], d1)
    _book(cod / "kodlama_K2.xlsx", [1, 0, 0, 0, 1, 1])
    res = ag.run(cod, tmp_path / "out")
    assert res["agreement"]["kappa"] == pytest.approx(0.3333, abs=1e-4)
    assert res["disagreements"] == 2 and res["unresolved"] == 2 and "precision_final" not in res
    d1r = {r["field"]: r for r in res["d1"]}
    assert d1r["Başlık"]["correct"] == 5 and d1r["Başlık"]["n"] == 6
    assert d1r["Kurum"]["accuracy"] == pytest.approx(2 / 3, abs=1e-4) and d1r["Kurum"]["na"] == 1

    # Uzlaşı sütununu doldur ve tekrar çalıştır.
    path = cod / "uzlasi.csv"
    with open(path, encoding="utf-8-sig") as fh:
        rows = list(csv.DictReader(fh))
    assert [r["id"] for r in rows] == ["102", "106"] and rows[0]["baslik_tr"] == "Başlık 2"
    for r in rows:
        r["uzlasi"] = "1"
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    res = ag.run(cod, tmp_path / "out")
    assert res["unresolved"] == 0 and res["precision_final"]["relevant"] == 4
    assert (tmp_path / "out" / "d1_accuracy.csv").exists()


def test_only_k1(tmp_path):
    cod = tmp_path / "c"
    cod.mkdir()
    _book(cod / "kodlama_K1.xlsx", [1, None, 0], [{}, {}, {}])
    res = ag.run(cod, tmp_path / "o")
    assert res["coded_k1"] == 2 and res["agreement"]["n"] == 0 and res["precision_k1"]["n"] == 2
    with pytest.raises(SystemExit):
        ag.run(tmp_path / "yok", tmp_path / "o2")


def test_separate_d1_and_author_identity(tmp_path):
    from bibliotr.normalize import Author, Publication

    cod = tmp_path / "c"
    cod.mkdir()
    _book(cod / "kodlama_K1.xlsx", [1, 1, 0])  # D1 sütunu yok
    rows = [[no, str(200 + no), "", 2024, "Dergi", "", f"Title {no}", "", "", "", "", "", ""]
            + [None] * (len(fz.D1_FIELDS) + 1) for no in (1, 2)]
    fz.build_workbook(cod / "kodlama_D1.xlsx", rows, "D1", True, "fiziksel_aktivite", relevance=False)
    wb = load_workbook(cod / "kodlama_D1.xlsx")
    ws = wb["Kodlama"]
    head = [c.value for c in ws[1]]
    ws.cell(row=2, column=head.index("Kurum doğru") + 1, value=1)
    ws.cell(row=3, column=head.index("Kurum doğru") + 1, value=0)
    wb.save(cod / "kodlama_D1.xlsx")

    def pub(pid, name, aid, orcid, inst):
        p = Publication(id=pid, title=f"T{pid}", year=2020, doi=None, citation_count=0, language="TUR",
                        document_type="PAPER", publication_type="ARTICLE", access_type=None, database=None,
                        journal="Dergi", journal_id="1", issn=None, eissn=None, volume=None, issue=None,
                        start_page=None, end_page=None, abstract=None, url="")
        p.authors = [Author(name=name, position=0, author_id=aid, orcid=orcid, institution=inst)]
        return p

    pubs = [pub("1", "Deniz Kaya", "10", None, "Ankara"), pub("2", "Deniz Kaya", "10", None, "Ankara"),
            pub("3", "Deniz Kaya", "11", None, "Ankara"), pub("4", "Ece Arslan", "20", "o1", "Gazi"),
            pub("5", "Ece Arslan", "21", "o2", "Gazi")]
    groups = fz.author_groups(pubs)
    fz.build_author_workbook(cod / "kodlama_yazar.xlsx", groups)
    gcsv = tmp_path / "author_groups.csv"
    with open(gcsv, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["group", "author_key", "n_pubs", "has_orcid", "n_institutions", "rule", "rule_basis"])
        for gi, g in enumerate(groups, 1):
            for d in g["members"]:
                w.writerow([gi, d["key"], len(d["pubs"]), 0, 1, d["rule"], d["rule_basis"]])
    wb = load_workbook(cod / "kodlama_yazar.xlsx")
    ws = wb["Kodlama"]
    head = [c.value for c in ws[1]]
    col = head.index(fz.AUTHOR_SAME) + 1
    for r in range(2, ws.max_row + 1):
        key = ws.cell(row=r, column=head.index("Yazar kimliği") + 1).value
        if key == "id:11":
            ws.cell(row=r, column=col, value=1)
        elif key == "id:21":
            ws.cell(row=r, column=col, value=1)  # kodlayıcı ORCID farkına rağmen aynı kişi dedi
    wb.save(cod / "kodlama_yazar.xlsx")

    res = ag.run(cod, tmp_path / "o", gcsv)
    assert res["d1_source"] == "kodlama_D1.xlsx" and res["n_d1"] == 2
    kurum = {r["field"]: r for r in res["d1"]}["Kurum"]
    assert kurum["correct"] == 1 and kurum["incorrect"] == 1
    ai = res["author_identity"]
    assert ai["comparisons"] == 2 and ai["same_person"] == 2 and ai["split_share"] == 1.0
    assert ai["rule_vs_coding"] == {"institution": {"n": 1, "rule_correct": 1, "coded_same": 1},
                                    "orcid": {"n": 1, "rule_correct": 0, "coded_same": 1}}
    b = ai["bcubed"]  # doğru kümeler: {10, 11} ve {20, 21}
    assert b["full_name"]["f1"] == 1.0 and b["trdizin_id"]["precision"] == 1.0 and b["trdizin_id"]["recall"] == 0.5
    assert b["id_rule"]["recall"] == 0.75 and b["id_rule"]["precision"] == 1.0


def test_bcubed():
    truth = {"a": "x", "b": "x", "c": "y"}
    assert ag.bcubed(truth, {"a": 1, "b": 2, "c": 3}) == {"n": 3, "precision": 1.0, "recall": 0.6667, "f1": 0.8}
    assert ag.bcubed(truth, {"a": 1, "b": 1, "c": 1})["precision"] == pytest.approx(0.5556, abs=1e-4)
    assert ag.bcubed(truth, truth)["f1"] == 1.0
    assert ag.bcubed(truth, {})["n"] == 0
