"""paper/scripts/12_scaling.py: sentetik kopyalar bağımsız yayın, yazar ve atıf ağları olmalı."""

import importlib.util
import sys
from pathlib import Path

from bibliotr import analysis
from bibliotr.normalize import normalize_publication

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("scale12", ROOT / "paper" / "scripts" / "12_scaling.py")
sc = importlib.util.module_from_spec(spec)
sys.modules["scale12"] = sc
spec.loader.exec_module(sc)


def _raw(rid, cites=None, cited_by=()):
    return {"id": rid, "title": f"Yayin {rid}", "publicationYear": 2020, "doi": f"10.1000/x{rid}",
            "documentType": "PAPER", "journal": {"name": "Dergi"},
            "authors": [{"authorId": 500 + rid, "inPublicationName": "Deniz Kaya", "orcid": ""}],
            "references": [{"text": "Kaya D. Yayin. Dergi. 2019;1:1-2.", "targetPublication": cites or 0}],
            "citedReferences": [{"sourceId": str(c)} for c in cited_by]}


def test_replicas_are_independent():
    base = [_raw(1), _raw(2, cites=1), _raw(3)]
    base[0]["citedReferences"] = [{"sourceId": "2"}]
    raws = sc.synthetic(base, 7)
    assert len(raws) == 7 and raws[0] is base[0]
    pubs = [normalize_publication(r) for r in raws]
    assert len({p.id for p in pubs}) == 7                          # kimlikler çakışmaz
    assert len({analysis.author_key(p.authors[0]) for p in pubs}) == 7
    assert len({p.doi for p in pubs}) == 7
    # 2 -> 1 atfı her kopyada kendi içinde kalır: 1, 1+OFFSET, 1+2*OFFSET
    lc = analysis.local_citations(pubs)
    assert {k: v for k, v in lc.items() if v} == {"1": 1, str(1 + sc.OFFSET): 1}
    assert raws[4]["references"][0]["targetPublication"] == 1 + sc.OFFSET
    assert raws[3]["references"][0]["targetPublication"] == 0       # hedefsiz kaynak öyle kalır
    assert base[1]["id"] == 2                                       # taban kayıtlar değişmez


def test_peak_rss_reports_a_number():
    v = sc.peak_rss_mb()
    assert v is not None and v > 0  # Linux, macOS ve Windows'ta ölçülebilmeli
