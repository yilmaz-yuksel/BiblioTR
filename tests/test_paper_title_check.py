"""paper/scripts/08_title_match_check.py için testler."""

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest
from openpyxl import load_workbook

from bibliotr import openalex
from bibliotr.storage import Store

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("tc08", ROOT / "paper" / "scripts" / "08_title_match_check.py")
tc = importlib.util.module_from_spec(spec)
sys.modules["tc08"] = tc
spec.loader.exec_module(tc)


def test_build_and_score(raw_records, tmp_path):
    db = tmp_path / "fa_alan_frozen_x_openalex.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("fa_alan"), raw_records)
        ids = [p.id for p in s.publications("fa_alan")]
        s.save_openalex([
            openalex.OpenAlexMatch(ids[0], "https://openalex.org/W1", 3, 2020, None, "article", "tr", "title", 0.95, "Başlık A"),
            openalex.OpenAlexMatch(ids[1], "https://openalex.org/W2", 1, 2021, "10.1/x", "article", "tr", "doi"),
            openalex.OpenAlexMatch(ids[2], "https://openalex.org/W3", 0, 2019, None, "article", "en", "title", 0.93, "B"),
        ])
    rows = tc.title_matches(db)
    assert [r["oa_id"] for r in rows] == ["https://openalex.org/W3", "https://openalex.org/W1"]  # belirsiz olan üstte
    sheet = tmp_path / "check.xlsx"
    tc.build(rows, sheet)
    wb = load_workbook(sheet)
    ws = wb["Kontrol"]
    assert ws.cell(row=2, column=6).value == "B" and ws.cell(row=2, column=9).hyperlink is not None
    ws.cell(row=2, column=10, value=1)
    ws.cell(row=3, column=10, value=0)
    wb.save(sheet)
    res = tc.score(sheet)
    assert res["coded"] == 2 and res["correct"] == 1 and res["precision"] == 0.5
    ws.cell(row=2, column=10, value="x")
    wb.save(sheet)
    with pytest.raises(ValueError):
        tc.score(sheet)


def test_old_openalex_table_is_migrated(raw_records, tmp_path):
    """title sütunu olmadan oluşturulmuş openalex_works tablosu açılışta taşınır."""
    db = tmp_path / "old.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("fa"), raw_records)
    con = sqlite3.connect(db)
    con.execute("ALTER TABLE openalex_works DROP COLUMN title")
    con.commit()
    con.close()
    with Store(db) as s:
        cols = {r["name"] for r in s.conn.execute("PRAGMA table_info(openalex_works)")}
        assert "title" in cols
