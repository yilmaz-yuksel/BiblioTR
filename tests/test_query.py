"""Gelişmiş arama sorgusu oluşturma ve denetleme."""

from bibliotr import query as tq
from bibliotr.query import FieldTerms


def test_four_boxes_match_trdizin_form():
    """TR Dizin formunun ürettiği parçalarla aynı; dış parantez her zaman eklenir."""
    t = FieldTerms(all_words="beden eğitimi", any_words="beden eğitimi", phrase="beden eğitimi",
                   exclude="beden eğitimi")
    assert tq.field_query("title", t) == (
        '(title : ( "beden" AND "eğitimi" AND ("beden" OR "eğitimi") AND "beden eğitimi" AND -("beden eğitimi") ))')
    assert tq.field_query("title", FieldTerms(phrase=' "beden   eğitimi" ')) == '(title : ( "beden eğitimi" ))'
    assert tq.field_query("abstract", FieldTerms(all_words="spor", exclude="futbol; hentbol topu")) == (
        '(abstract : ( "spor" AND -("futbol") AND -("hentbol topu") ))')
    # Yalnızca dışlama ya da boş form sorgu üretmez.
    assert tq.field_query("title", FieldTerms(exclude="futbol")) == ""
    assert tq.field_queries(["title"], FieldTerms()) == []


def test_fields_or_gives_separate_queries_and_gives_one():
    t = FieldTerms(phrase="beden eğitimi")
    assert tq.field_queries(["title", "abstract"], t, "or") == [
        ("Başlık", '(title : ( "beden eğitimi" ))'), ("Özet", '(abstract : ( "beden eğitimi" ))')]
    assert tq.field_queries(["title", "abstract"], t, "and") == [
        ("Başlık ve Özet", '(title : ( "beden eğitimi" ) | abstract : ( "beden eğitimi" ))')]
    assert tq.field_queries(["title"], t, "and") == [("Başlık", '(title : ( "beden eğitimi" ))')]


def test_normalize_adds_missing_outer_parentheses():
    bare = 'title : ( "beden" AND "eğitimi" )'
    assert tq.normalize_query(bare) == f"({bare})"
    assert tq.normalize_query(f"({bare})") == f"({bare})"
    two = 'title : ( "a" ) | abstract : ( "b" )'
    assert tq.normalize_query(two) == f"({two})"
    assert tq.normalize_query("beden eğitimi") == "beden eğitimi"
    assert tq.normalize_query("  ") == ""


def test_query_notes():
    levels = lambda q: [lv for lv, _ in tq.query_notes(q)]  # noqa: E731
    assert levels("") == []
    assert levels('"beden eğitimi"') == ["error"]          # ana aramada tırnak 0 sonuç döndürür
    assert levels("beden eğitimi") == ["info"]             # gevşek eşleşme notu
    assert levels('(title : ( "beden" ))') == []
    assert levels('title : ( "beden" )') == ["info"]       # dış parantez eklendi
    assert levels('(title : ( "a" ) | abstract : ( "a" ))') == ["warning"]  # | kesişim verir
