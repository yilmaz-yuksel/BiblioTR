"""Gerçek arama yanıtının yapısını koruyan kurgusal kayıtlarla (konu facet'i, q olmadan) testler."""

import json

import pytest

from bibliotr import analysis, cli, export
from bibliotr.client import TRDizinClient, extract_aggregation_other, extract_aggregations, extract_records
from bibliotr.normalize import Reference, has_details, normalize_publication

from .conftest import FIXTURES, FakeResponse


@pytest.fixture
def subject_response():
    return json.loads((FIXTURES / "search_subject.json").read_text(encoding="utf-8"))


@pytest.fixture
def subject_pubs(subject_response):
    return [normalize_publication(r) for r in extract_records(subject_response)]


def test_query_is_optional():
    params = TRDizinClient.build_params("", "publicationYear-DESC", 1, 20,
                                        [("facet-subject", "Fen > Tıp > Spor Bilimleri")])
    assert [k for k, _ in params] == ["order", "page", "limit", "facet-subject"]


def test_nested_aggregations_and_other_counts(subject_response):
    aggs = extract_aggregations(subject_response)
    assert aggs["facet-authorName"][0] == ("nermin oktay", 45)
    assert aggs["facet-publication_year"][0] == ("2023", 603)
    assert ("(boş)", 759) in aggs["facet-facetAuthorCity"]
    assert aggs["facet-projectGroup"] == []
    other = extract_aggregation_other(subject_response)
    assert other["facet-authorName"] == 12337 and other["facet-journalName"] == 0


def test_title_skips_empty_language_title(subject_pubs):
    assert subject_pubs[0].title.startswith("Return-to-Play")  # orderTitle'daki "Returnto" değil
    assert subject_pubs[0].start_page is None and subject_pubs[0].end_page is None  # "0"
    assert subject_pubs[0].volume == "17"


def test_multi_institution_author_uses_root_titles(subject_pubs):
    inal, akyar = subject_pubs[0].authors
    assert inal.name == "Volkan İnal" and inal.position == 1
    assert akyar.institution == "T.C. SAĞLIK BAKANLIĞI; İSTANBUL ÜNİVERSİTESİ"
    assert akyar.city == "ANKARA; İSTANBUL" and akyar.country == "Türkiye"
    assert akyar.unit.startswith("İSTANBUL ÜNİVERSİTESİ > CERRAHPAŞA")
    assert (inal.institution_type, inal.institution_status) == ("OZELHASTANE", "OZEL")


def test_foreign_institution_falls_back_to_affiliation(subject_pubs):
    bayir, unlu = subject_pubs[1].authors
    assert (bayir.institution, bayir.country) == ("HAKKARİ ÜNİVERSİTESİ", "Türkiye")
    assert not unlu.institution_matched
    assert (unlu.institution, unlu.city, unlu.country) == ("Kyrgyz - Turkish Manas University", "Bishkek", "Kırgızistan")
    cities = analysis.top_counts(subject_pubs, "city", 10)["Şehir"].tolist()
    assert "ANKARA" in cities and "İSTANBUL" in cities


def test_vancouver_references_use_doi_and_journal_year(subject_pubs):
    refs = subject_pubs[0].references
    assert refs[0].year == 2022  # başlıktaki "2001/02" değil
    assert refs[0].key == "doi:10.5555/njcs-2021-105407"
    assert refs[2].key == "vermeulen|2002|sonography of tendons eur"
    assert refs[2].label.startswith("Vermeulen (2002) Sonography of tendons")


@pytest.mark.parametrize("text,key", [
    ("Jansen, H.P.F. ve Van Dijk, A.F.J. 1993. \"Co-term-based Field Maps\", Policy Research Review.",
     "jansen|1993|co term based field"),
    ("[12] Hartley BJ. The seasonal distribution of athletic performance. J Cumb Acad Sci. 1926;16:317.",
     "hartley|1926|the seasonal distribution of"),
])
def test_reference_formats(text, key):
    assert Reference(text, 1).key == key


def test_has_details_only_needed_for_cited_pubs(subject_response):
    first, second = extract_records(subject_response)
    assert has_details(first)        # atıf yok, kaynakça var: ek istek gerekmez
    assert not has_details(second)   # 3 atıf ama citedReferences null: ek istek gerekir


def test_doi_based_citation_edge(subject_pubs):
    citing, cited = subject_pubs
    cited.doi = "10.5555/0363546511402218"  # ilk yayının kaynakçasındaki DOI
    assert analysis.citation_edges([citing, cited]) == {(citing.id, cited.id)}


def test_profile_excel_and_cli(subject_response, tmp_path, monkeypatch, capsys):
    aggs = extract_aggregations(subject_response)
    xlsx = export.aggregations_to_excel(aggs, {"facet-journalName": "Dergi"}, {"Sorgu": "-"})
    assert xlsx[:2] == b"PK"
    frame = export.aggregations_frame(aggs)
    assert set(frame.columns) == {"Alan", "Değer", "Yayın"}

    calls = []

    def fake_get(self, url, params=None, timeout=None):
        calls.append(list(params))
        return FakeResponse(subject_response)

    monkeypatch.setattr("requests.Session.get", fake_get)
    out = tmp_path / "p.xlsx"
    cli.main(["--delay", "0", "profile", "--facet", "subject=Fen > Tıp > Spor Bilimleri", "--out", str(out)])
    text = capsys.readouterr().out
    assert "Toplam yayın: 6575" in text and "GAZİ ÜNİVERSİTESİ" in text and "listede olmayan: 12337" in text
    assert out.exists()
    assert ("facet-subject", "Fen > Tıp > Spor Bilimleri") in calls[0]
    assert all(k != "q" for k, _ in calls[0])
