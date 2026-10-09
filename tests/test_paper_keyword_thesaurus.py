"""paper/scripts/13_keyword_thesaurus.py: çift başına karar tablosu ve kararların sözlüğe çevrilmesi."""

import importlib.util
import json
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

from bibliotr.normalize import Publication
from bibliotr.storage import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("kw13", ROOT / "paper" / "scripts" / "13_keyword_thesaurus.py")
kw = importlib.util.module_from_spec(spec)
sys.modules["kw13"] = kw
spec.loader.exec_module(kw)


def _pub(pid, tr, en):
    p = Publication(id=pid, title=f"Yayin {pid}", year=2020, doi=None, citation_count=1, language="TUR",
                    document_type="PAPER", publication_type="ARTICLE", access_type=None, database=None,
                    journal="Dergi", journal_id="1", issn=None, eissn=None, volume=None, issue=None,
                    start_page=None, end_page=None, abstract=None, url="")
    p.keywords = list(tr) + list(en)
    p.keywords_by_language = {"TUR": list(tr), "ENG": list(en)}
    return p


def _pubs():
    pubs = [_pub(str(i), ["egzersiz"], ["exercise"]) for i in range(4)]
    pubs += [_pub("10", ["egzersizler"], []), _pub("11", ["egzersizler"], []), _pub("12", ["egzersiz."], [])]
    return pubs


def _fill(path, codes, target=None):
    wb = load_workbook(path)
    ws = wb["Öneriler"]
    head = [c.value for c in ws[1]]
    dcol, tcol, mcol = head.index(kw.DECISION) + 1, head.index(kw.TARGET) + 1, head.index("Birleşecek biçim (yayın)") + 1
    for r in range(2, ws.max_row + 1):
        form = ws.cell(row=r, column=mcol).value.rsplit(" (", 1)[0]
        if form in codes:
            ws.cell(row=r, column=dcol, value=codes[form])
        if target and form == target[0]:
            ws.cell(row=r, column=tcol, value=target[1])
    wb.save(path)


def test_build_one_row_per_pair_and_apply(tmp_path):
    sheet = tmp_path / "kodlama_esanlam.xlsx"
    n = kw.build(sheet, _pubs())
    rows = list(load_workbook(sheet)["Öneriler"].iter_rows(min_row=2, values_only=True))
    assert n == len(rows) >= 3
    forms = {r[4].rsplit(" (", 1)[0] for r in rows}
    assert {"egzersiz", "egzersizler", "egzersiz."} <= forms | {r[3].rsplit(" (", 1)[0] for r in rows}
    # Türkçe-İngilizce çiftleri önce gelir; aynı gruptaki çiftler aynı grup numarasını taşır.
    assert rows[0][2] == "Türkçe-İngilizce"
    groups = [r[1] for r in rows]
    assert groups == sorted(groups)

    # Türkçe-İngilizce: 1 ve hedef yazım küçük harf; yazım farkları: biri 1, biri 0.
    tr_en_member = rows[0][4].rsplit(" (", 1)[0]
    _fill(sheet, {tr_en_member: 1, "egzersizler": 1, "egzersiz.": 0}, target=(tr_en_member, "exercise"))
    frozen = tmp_path / "frozen.db"
    Store(frozen).close()
    out = tmp_path / "out"
    res = kw.apply(sheet, out, tmp_path / "copy.db", frozen)
    thes = kw.thesaurus.Thesaurus.from_dict(json.loads((out / "thesaurus.json").read_text(encoding="utf-8")))
    assert thes.resolve("keyword", "egzersizler") == "exercise"   # zincir: egzersizler -> egzersiz -> exercise
    assert thes.resolve("keyword", "egzersiz") == "exercise"
    assert thes.resolve("keyword", "egzersiz.") == "egzersiz."     # 0: ayrı tutulur
    assert thes.is_separate("keyword", "egzersiz.", "egzersiz")
    assert res["pairs"]["Yazım farkı: 0"] == 1
    with Store(tmp_path / "copy.db", readonly=True) as s:
        assert s.load_thesaurus().resolve("keyword", "egzersiz") == "exercise"
    with pytest.raises(SystemExit):
        kw.apply(sheet, out, frozen, frozen)


def test_build_refuses_existing_sheet(tmp_path, monkeypatch):
    monkeypatch.setattr(kw, "ROOT", tmp_path)
    sheet = tmp_path / "paper" / "data" / "coding" / "x" / "kodlama_esanlam.xlsx"
    sheet.parent.mkdir(parents=True)
    sheet.write_bytes(b"kodlama")
    db = tmp_path / "x.db"
    Store(db).close()
    with pytest.raises(SystemExit, match="zaten var"):
        kw.main(["--dataset", "x", "--db", str(db), "--build"])
    assert sheet.read_bytes() == b"kodlama"


def test_shared_variant_joins_both_groups():
    S = kw.thesaurus.Suggestion
    rows = [(S("keyword", "women", "women", [("kadinlar", "Kadınlar", 3)], 6, "Türkçe-İngilizce", 1.0), ["1"], None),
            (S("keyword", "kadin", "Kadın", [("woman", "Woman", 2)], 4, "Türkçe-İngilizce", 1.0), ["1"], None),
            (S("keyword", "kadin", "Kadın", [("kadinlar", "Kadınlar", 3)], 4, "Yazım farkı", 1.0), ["1"], None),
            (S("keyword", "life quality", "Life quality", [("x", "life qualşty", 1)], 2, "Benzer yazılış", 0.9), ["1"],
             "quality of life")]
    groups = dict(kw.merge_groups(rows))
    assert groups["women"] == ["Kadın", "Kadınlar", "Woman", "women"]   # en sık biçim hedef olur
    assert groups["quality of life"] == ["Life quality", "life qualşty"]  # yazılan hedef yazım öncelikli


def test_extra_suggestions_skip_pairs_already_asked():
    S = kw.thesaurus.Suggestion
    pubs = [_pub(str(i), ["egzersiz", "obezite"], ["Obesity", "exercise"]) for i in range(3)]
    pubs += [_pub("o", ["obezite"], ["Obesity"]), _pub("e", ["egzersiz"], ["exercise"])]
    rows = [(S("keyword", "exercise", "exercise", [("egzersiz", "egzersiz", 3)], 3, "Türkçe-İngilizce", 1.0), ["1"], None)]
    extra = kw.extra_suggestions(pubs, rows)
    pairs = {(s.target_label, m[1]) for s in extra for m in s.members}
    assert len(pairs) == 1 and {fold_pair for p in pairs for fold_pair in p} == {"Obesity", "obezite"}


def test_manual_decisions_merge_like_sheet_decisions(tmp_path):
    S = kw.thesaurus.Suggestion
    manual = tmp_path / kw.MANUAL
    manual.write_text("hedef_yazim,birlesecek_yazim,not\nMobile application,mobil uygulama,elle\n", encoding="utf-8")
    rows = kw.all_decisions([manual])
    rows += [(S("keyword", "mobil uygulama", "Mobil uygulama", [("mobil uygulamalar", "mobil uygulamalar", 2)], 3,
                "Yazım farkı", 1.0), ["1"], None)]
    groups = dict(kw.merge_groups(rows))
    assert groups["Mobile application"] == ["Mobil uygulama", "Mobile application", "mobil uygulamalar"]
    thes = kw.decided_thesaurus(rows)
    assert thes.resolve("keyword", "mobil uygulamalar") == "Mobile application"
    assert kw.manual_decisions(tmp_path / "yok.csv") == []
