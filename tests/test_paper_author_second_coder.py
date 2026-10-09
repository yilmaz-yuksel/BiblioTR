"""paper/scripts/15_author_second_coder.py: alt örneklem seçimi ve uyum puanı."""

import csv
import importlib.util
import sys
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("sc15", ROOT / "paper" / "scripts" / "15_author_second_coder.py")
sc = importlib.util.module_from_spec(spec)
sys.modules["sc15"] = sc
spec.loader.exec_module(sc)

SAME = "Grubun 1. kimliğiyle aynı kişi (1/0/NA)"


def _groups_csv(path):
    rows = [("1", "id:1", "reference", ""), ("1", "id:2", "orcid", "0"), ("2", "id:3", "reference", ""),
            ("2", "id:4", "institution", "1"), ("3", "id:5", "reference", ""), ("3", "id:6", "none", "0"),
            ("4", "id:7", "reference", ""), ("4", "id:8", "orcid", "1")]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["group", "author_key", "n_pubs", "has_orcid", "n_institutions", "rule", "rule_basis"])
        for g, k, b, r in rows:
            w.writerow([g, k, 1, 0, 1, r, b])


def _sheet(path, codes):
    wb = Workbook()
    ws = wb.active
    ws.title = "Kodlama"
    ws.append(["Grup", "Ad", "Yazar kimliği", SAME, "Not"])
    for k, c in codes.items():
        ws.append([1, "Deniz Kaya", k, c, None])
    wb.save(path)


def test_select_takes_all_orcid_conflicts_and_seeded_random(tmp_path):
    g = tmp_path / "author_groups.csv"
    _groups_csv(g)
    sel = sc.select(g, n_random=2, seed=1)
    assert [r["author_key"] for r in sel if r["stratum"] == "orcid_conflict"] == ["id:2"]
    assert sum(r["stratum"] == "random" for r in sel) == 2
    assert sel == sc.select(g, n_random=2, seed=1)
    assert not {"id:1", "id:3", "id:5", "id:7"} & {r["author_key"] for r in sel}


def test_score_agreement_and_kappa(tmp_path):
    sel = tmp_path / "selection.csv"
    with open(sel, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["group", "author_key", "stratum"])
        w.writeheader()
        for k, s in (("id:2", "orcid_conflict"), ("id:4", "random"), ("id:6", "random"), ("id:8", "random")):
            w.writerow({"group": 1, "author_key": k, "stratum": s})
    _sheet(tmp_path / "k1.xlsx", {"id:1": "referans", "id:2": "0", "id:4": 1, "id:6": 1, "id:8": "NA"})
    _sheet(tmp_path / "k2.xlsx", {"id:1": "referans", "id:2": "0", "id:4": 1, "id:6": 0, "id:8": None})
    res = sc.score(tmp_path / "k1.xlsx", tmp_path / "k2.xlsx", sel)
    assert not res["complete"] and res["uncoded"] == ["id:8"]
    assert res["all"]["coded_by_both"] == 3 and res["all"]["agree"] == 2
    assert res["all"]["disagreements"] == ["id:6"]
    assert sc.kappa([("1", "1"), ("0", "0")]) == 1.0
