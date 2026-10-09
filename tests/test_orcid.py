"""ORCID biçim ve denetim hanesi kontrolü ile 09 denetimindeki ORCID metrikleri."""

import importlib.util
import sys
from pathlib import Path

from bibliotr.normalize import Author, Publication, orcid_status

ROOT = Path(__file__).resolve().parents[1]


def test_orcid_status():
    # Kurgusal kimlikler (0000-0002-1825-0097: ORCID belgelerindeki kurgusal kişi)
    assert orcid_status("0000-0002-1825-0097") == "valid"
    assert orcid_status("0000-0002-7842-081X") == "valid"
    assert orcid_status("https://orcid.org/0000-0002-4999-6836") == "valid"
    assert orcid_status("0000000218250097") == "valid"
    assert orcid_status("0000-0002-1825-0098") == "bad_checksum"  # son hane yanlış
    assert orcid_status("0000-0002-1852-0097") == "bad_checksum"  # iki rakamın yeri değişmiş
    assert orcid_status("000-0002-1825-0097") == "bad_format"     # eksik rakam
    assert orcid_status("0000-0002-1825-009") == "bad_format"
    assert orcid_status("") is None and orcid_status(None) is None


def test_audit_reports_orcid_quality():
    spec = importlib.util.spec_from_file_location("audit09o", ROOT / "paper" / "scripts" / "09_validation_audit.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["audit09o"] = mod
    spec.loader.exec_module(mod)
    a1 = Author("Deniz Kaya", 1, author_id="1", orcid="0000-0002-1825-0097")
    a2 = Author("Deniz Kaya", 1, author_id="1", orcid="0000-0002-7842-081X")   # aynı kimlikte ikinci geçerli
    a3 = Author("Ece Arslan", 2, author_id="2", orcid="0000-0002-1825-0098")   # denetim hanesi hatalı
    a4 = Author("Ece Arslan", 1, author_id="2", orcid="000-0002-1825-0097")    # biçim hatalı
    pubs = []
    for pid, authors in (("1", [a1, a3]), ("2", [a2, a4])):
        p = Publication(id=pid, title=f"Yayin {pid}", year=2020, doi=None, citation_count=0, language="TUR",
                        document_type="PAPER", publication_type="ARTICLE", access_type=None, database=None,
                        journal="Dergi", journal_id="1", issn=None, eissn=None, volume=None, issue=None,
                        start_page=None, end_page=None, abstract=None, url="")
        p.authors = authors
        pubs.append(p)
    res = mod.section_b(pubs)
    assert res["orcid_slots_by_status"] == {"valid": 2, "bad_checksum": 1, "bad_format": 1}
    assert res["orcid_distinct"] == 4
    assert res["author_keys_with_several_valid_orcids"] == 1
