"""paper/scripts/18_export_codes.py: yalnız kimlik ve kod sütunları dışa aktarılır."""

import importlib.util
import sys
from pathlib import Path

from openpyxl import Workbook

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("ec18", ROOT / "paper" / "scripts" / "18_export_codes.py")
ec = importlib.util.module_from_spec(spec)
sys.modules["ec18"] = ec
spec.loader.exec_module(ec)


def test_pick_keeps_only_named_columns_and_cleans_codes(tmp_path):
    wb = Workbook()
    ws = wb.active
    ws.title = "Kodlama"
    ws.append(["No", "TR Dizin ID", "Başlık", "İlgili (1/0)", "Not"])
    ws.append([1, 101, "Kurgusal başlık", 1.0, "uzun not"])
    ws.append([2, 102, "Başka başlık", "na", None])
    ws.append([None, None, None, None, None])
    path = tmp_path / "k.xlsx"
    wb.save(path)
    rows = ec.pick(path, "Kodlama", {"TR Dizin ID": "id", "İlgili (1/0)": "code"}, {"code"}, "id")
    assert rows == [{"id": "101", "code": "1"}, {"id": "102", "code": "NA"}]
    assert ec.write(tmp_path / "out.csv", rows) == 2
    assert (tmp_path / "out.csv").read_text(encoding="utf-8").splitlines() == ["id,code", "101,1", "102,NA"]
