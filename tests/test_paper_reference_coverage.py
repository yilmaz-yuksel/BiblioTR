"""paper/scripts/17_reference_coverage.py: kapsam payları ve sürümler arası alan farkları."""

import importlib.util
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("rc17", ROOT / "paper" / "scripts" / "17_reference_coverage.py")
rc = importlib.util.module_from_spec(spec)
sys.modules["rc17"] = rc
spec.loader.exec_module(rc)


def _row(pid, i, text, author="Kaya A", year="2020", source=None, volume=None, page=None, doi=None):
    return [pid, i, text, author, year, source, volume, page, doi]


def test_coverage_splits_tuning_tenth():
    rows = [_row("1", 0, "dev", source="Spor Bilimleri Dergisi"), _row("1", 1, "rest"), _row("2", 0, "rest2", page="5")]
    c = rc.coverage(rows, lambda t: t == "dev")
    assert c["references"] == 3 and c["with_source_pct"] == 33.3 and c["with_page_pct"] == 33.3
    assert c["tuning_tenth"] == {"references": 1, "with_source_pct": 100.0}
    assert c["rest"] == {"references": 2, "with_source_pct": 0.0}


def test_revision_changes_counts_fields():
    before = [_row("1", 0, "a", author="World Health Organization G"), _row("1", 1, "b"), _row("2", 0, "c", year=None)]
    after = [_row("1", 0, "a", author="World Health Organization"), _row("1", 1, "b"), _row("2", 0, "c", year="")]
    r = rc.revision_changes(before, after)
    assert r["compared"] == 3 and r["changed"] == 1 and r["by_field"] == {"author": 1}
