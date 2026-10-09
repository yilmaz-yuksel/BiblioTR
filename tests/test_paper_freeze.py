"""paper/scripts/04_freeze_and_sample.py için testler."""

import copy
import importlib.util
import json
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


@pytest.fixture
def db(tmp_path, raw_records):
    """Varsayılan veri seti (fz.TARGET) 5 kayıt; ham birleşimdeki ilgisiz "99" ona girmez."""
    from bibliotr.storage import Store

    base = raw_records[0]

    def rec(rid, tr, en, kw=("fiziksel aktivite", "physical activity")):
        r = copy.deepcopy(base)
        r["id"] = rid
        r["abstracts"] = [{"language": "TUR", "title": tr, "abstract": f"{tr} özeti", "keywords": [kw[0]]},
                          {"language": "ENG", "title": en, "abstract": f"{en} abstract", "keywords": [kw[1]]}]
        return r

    verified = [rec(str(i), f"Fiziksel aktivite {i}", f"Physical activity {i}") for i in range(1, 6)]
    path = tmp_path / "fa.db"
    with Store(path) as s:
        s.create_dataset("fa_ham")
        s.add_records("fa_ham", verified + [rec("99", "Aile", "Family", ("aile", "family"))])
        s.create_dataset(fz.TARGET)
        s.add_records(fz.TARGET, verified)
    return path


def test_freeze_and_sample(db, tmp_path):
    res, cod = tmp_path / "res", tmp_path / "cod"
    info = fz.run(db, res, cod, n=3, stamp="20260101")
    frozen = db.with_name("fa_alan_frozen_20260101.db")
    assert frozen.exists() and info["sha256"] == fz.sha256(frozen)
    assert info["n_records"] == 5 and info["sample_n"] == 3 and "99" not in info["sample_ids"]
    again = fz.run(db, tmp_path / "res2", tmp_path / "cod2", n=3, stamp="20260101")
    assert info["coding_written"] == ["kodlama_K1.xlsx", "kodlama_K2.xlsx"]
    # Başlamış kodlama korunur, --force ile yeniden üretilir.
    (cod / "kodlama_K1.xlsx").write_bytes(b"kodlama")
    kept = fz.run(db, res, cod, n=3, stamp="20260101")
    assert kept["coding_kept"] == ["kodlama_K1.xlsx", "kodlama_K2.xlsx"]
    assert (cod / "kodlama_K1.xlsx").read_bytes() == b"kodlama"
    fz.run(db, res, cod, n=3, stamp="20260101", force=True)
    assert again["sample_ids"] == info["sample_ids"] and again["sha256"] == info["sha256"]  # tekrarlanabilir
    assert json.loads((res / "freeze.json").read_text(encoding="utf-8"))["sample_ids"] == info["sample_ids"]
    ids_csv = (res / "fa_alan_ids.csv").read_text(encoding="utf-8-sig").splitlines()
    assert ids_csv[0].startswith("id,year") and len(ids_csv) == 6

    k1, k2 = load_workbook(cod / "kodlama_K1.xlsx"), load_workbook(cod / "kodlama_K2.xlsx")
    guide = [c.value for c in k1["Yönerge"]["A"]]
    assert "Dikkat: çok anlamlılık" in guide
    assert k1.sheetnames == ["Yönerge", "Kodlama", "İlerleme"] and k2.sheetnames == ["Yönerge", "Kodlama", "İlerleme"]
    s1, s2 = k1["Kodlama"], k2["Kodlama"]
    head1 = [c.value for c in s1[1]]
    head2 = [c.value for c in s2[1]]
    assert "Kurum doğru" in head1 and "Kurum doğru" not in head2 and "Yazarlar (araç)" not in head2
    assert s1.max_row == 4 and s2.max_row == 4
    row = {h_: c.value for h_, c in zip(head1, s1[2])}
    assert row["Başlık (TR)"].startswith("Fiziksel aktivite") and row["Başlık (EN)"].startswith("Physical activity")
    assert row["Özet (EN)"].endswith("abstract") and row["İlgili (1/0)"] is None
    assert s1.cell(row=2, column=3).hyperlink is not None
    assert [s2.cell(row=r, column=2).value for r in (2, 3, 4)] == info["sample_ids"]  # iki dosyada aynı sıra
    prog = [c.value for c in k1["İlerleme"]["B"]]
    assert any(isinstance(v, str) and v.startswith("=COUNTA(Kodlama!$K$2:$K$4") for v in prog)


def _fa_record(base, rid, authors, doc="PAPER", year=2023):
    r = copy.deepcopy(base)
    r.update(id=rid, documentType=doc, publicationYear=year, doi=None)
    r["abstracts"] = [{"language": "ENG", "title": f"Physical activity {rid}", "abstract": "abstract",
                       "keywords": ["physical activity"]}]  # Türkçe başlık yok
    r["authors"] = [{"authorId": aid, "inPublicationName": name, "orcid": orcid,
                     "institutionName": [inst], "city": "ANKARA", "country": "Türkiye"}
                    for aid, name, orcid, inst in authors]
    return r


@pytest.fixture
def fa_db(tmp_path, raw_records):
    from bibliotr.storage import Store

    base = raw_records[0]
    recs = [
        _fa_record(base, 1, [(10, "Deniz Kaya", "0000-0000-0000-0010", "Ankara Üniversitesi"),
                             (20, "Ece Arslan", "0000-0000-0000-0020", "Gazi Üniversitesi")]),
        _fa_record(base, 2, [(10, "Deniz Kaya", None, "Ankara Üniversitesi"), (30, "Can Öz", None, "Ege Üniversitesi")]),
        _fa_record(base, 3, [(11, "Deniz Kaya", None, "Ankara Üniversitesi")]),  # bölünmüş profil (kurum ortak)
        _fa_record(base, 4, [(21, "Ece Arslan", "0000-0000-0000-0021", "Gazi Üniversitesi")]),  # farklı ORCID
        _fa_record(base, 5, [(31, "Can Öz", None, "Akdeniz Üniversitesi")]),  # karar verilemez
        _fa_record(base, 6, [(40, "Mert Can", None, "Hacettepe Üniversitesi")]),
        _fa_record(base, 7, [(41, "Su Ak", None, "Hacettepe Üniversitesi")], doc="PROJECT"),
    ]
    path = tmp_path / "trdizin.db"
    with Store(path) as s:
        s.create_dataset("fa alan")
        s.add_records("fa alan", recs)
    return path


def test_full_set_scope_d1_and_authors(fa_db, tmp_path):
    res, cod, frz = tmp_path / "res", tmp_path / "cod", tmp_path / "frz"
    kw = dict(n=0, stamp="20261002", dataset="fa alan", scope="fiziksel_aktivite", doc_types=("PAPER",),
              d1_n=3, k2_n=4, authors=True, frozen_dir=frz)
    info = fz.run(fa_db, res, cod, **kw)
    assert (frz / "fa_alan_frozen_20261002.db").exists() and fa_db.exists()
    assert info["n_dataset"] == 7 and info["n_records"] == 6 and info["excluded_by_type"] == {"PROJECT": 1}
    assert info["full_set"] and sorted(info["sample_ids"]) == ["1", "2", "3", "4", "5", "6"]
    assert info["k2_n"] == 4 and info["d1_n"] == 3
    assert info["coding_written"] == ["kodlama_K1.xlsx", "kodlama_K2.xlsx", "kodlama_D1.xlsx", "kodlama_yazar.xlsx"]
    assert info["author_groups"] == 3 and info["author_rule"] == {"institution:1": 1, "orcid:0": 1, "none:0": 1}
    assert fz.run(fa_db, tmp_path / "r2", tmp_path / "c2", **kw)["sample_ids"] == info["sample_ids"]

    k1, k2, d1 = (load_workbook(cod / f"kodlama_{c}.xlsx") for c in ("K1", "K2", "D1"))
    guide = [c.value for c in k1["Yönerge"]["A"]]
    assert "Dikkat: egzersiz ve spor" in guide and "Dikkat: çok anlamlılık" in guide
    assert "fiziksel aktivite" in k1["Yönerge"]["A1"].value
    h1 = [c.value for c in k1["Kodlama"][1]]
    assert "Kurum doğru" not in h1 and "İlgili (1/0)" in h1  # D1 ayrı dosyada
    assert k1["Kodlama"].max_row == 7 and k2["Kodlama"].max_row == 5
    assert [k2["Kodlama"].cell(row=r, column=2).value for r in range(2, 6)] == info["sample_ids"][:4]
    hd = [c.value for c in d1["Kodlama"][1]]
    assert "İlgili (1/0)" not in hd and "Kurum doğru" in hd and d1["Kodlama"].max_row == 4
    row = dict(zip(h1, (c.value for c in k1["Kodlama"][2])))
    assert row["Başlık (TR)"] in ("", None) and row["Başlık (EN)"].startswith("Physical activity")
    prog = [c.value for c in d1["İlerleme"]["A"]]
    assert "Kodlanan (ilgililik)" not in prog and "Kurum doğru" in prog

    ya = load_workbook(cod / "kodlama_yazar.xlsx")["Kodlama"]
    hy = [c.value for c in ya[1]]
    rows = [dict(zip(hy, (c.value for c in r))) for r in ya.iter_rows(min_row=2)]
    assert len(rows) == 6 and sum(r[fz.AUTHOR_SAME] == "referans" for r in rows) == 3
    kaya = [r for r in rows if r["Ad"] == "Deniz Kaya"]
    assert kaya[0]["Yazar kimliği"] == "id:10" and kaya[0]["Yayın"] == 2  # referans en çok yayını olan
    groups_csv = (res / "author_groups.csv").read_text(encoding="utf-8-sig")
    assert "Deniz" not in groups_csv and "id:11" in groups_csv  # özet dosyası ad içermez


def test_cli_rejects_unknown_scope(fa_db):
    with pytest.raises(SystemExit):
        fz.main(["--db", str(fa_db), "--dataset", "fa alan", "--scope", "bilinmeyen"])
