from bibliotr.normalize import fold, get_path, key_tree, normalize_publication


def test_full_record(pubs):
    p = pubs[0]
    assert p.id == "1001"
    assert p.title.startswith("Türkiye'de")
    assert p.year == 2019
    assert p.doi == "10.1234/ABC.2019.1"
    assert p.citation_count == 12
    assert p.journal == "Bilgi Dünyası"
    assert (p.issn, p.eissn, p.volume, p.issue) == ("1302-1111", "2148-2222", "20", "1")
    assert [a.name for a in p.authors] == ["Ali YILDIZ", "Ayşe Kaya"]
    assert p.authors[0].institution == "Hacettepe Üniversitesi"
    assert p.authors[0].orcid == "0000-0001-0000-0001"
    assert p.keywords == ["Bibliyometri", "Atıf analizi", "Bibliometrics", "citation analysis"]
    assert p.subjects == ["Sosyal > Bilgi ve Belge Yönetimi"]
    assert "Bu çalışmada" in p.abstract and "In this study" in p.abstract
    assert p.url.endswith("/tr/yayin/detay/1001")


def test_sparse_record(pubs):
    p = pubs[2]
    assert p.title == "Tek yazarlı bir çalışma"
    assert p.year == 2021
    assert [a.name for a in p.authors] == ["Zeynep Şahin"]
    assert p.citation_count is None and p.keywords == []


def test_string_institution_and_missing_fields():
    p = normalize_publication({"id": 5, "authors": [{"name": "A B", "institutionName": "X Üniv."}, {}]})
    assert len(p.authors) == 1 and p.authors[0].institution == "X Üniv."
    assert p.title is None and p.year is None


def test_fold_turkish():
    assert fold("İSTANBUL") == fold("istanbul") == "istanbul"
    assert fold("YILDIZ") == fold("Yıldız") == "yildiz"
    assert fold("Şahin") == "sahin"


def test_get_path_and_key_tree():
    obj = {"a": [{"b": 1}, {"b": 2}], "c": {"d": "x"}}
    assert get_path(obj, "a.1.b") == 2
    assert get_path(obj, "a.5.b") is None
    tree = key_tree(obj)
    assert tree["a.0.b"] == "1" and tree["c.d"] == "'x'" and "a.*" in tree


def test_girilmemis_institution_is_unmatched():
    from bibliotr.normalize import UNMATCHED_INSTITUTIONS, _is_unmatched

    for v in ("Girilmemiş", "Girilmemişş", "GİRİLMEMİŞ KURUM", "Tanımlanmamış Kurum", None, ""):
        assert _is_unmatched(v, UNMATCHED_INSTITUTIONS), v
    assert not _is_unmatched("HACETTEPE ÜNİVERSİTESİ", UNMATCHED_INSTITUTIONS)
    assert not _is_unmatched("girilmemis", {"diger"})  # önek kuralı yalnızca kurumlarda


def test_girilmemis_placeholder_in_address_gives_no_institution():
    from bibliotr.normalize import parse_authors

    raw = [{"fullName": "Ayşe Yılmaz", "order": 1, "institutionName": "Girilmemişş",
            "institution": {"rootTitle": ["Girilmemişş"], "title": ["Girilmemişş"], "city": [], "country": []}},
           {"fullName": "Ali Kaya", "order": 2, "institutionName": "Girilmemiş",
            "institution": {"rootTitle": ["Tanımlanmamış Kurum"], "title": ["Tanımlanmamış Kurum"]}}]
    a1, a2 = parse_authors(raw)
    assert a1.institution is None and not a1.institution_matched
    assert a2.institution is None


def test_city_and_country_stay_aligned_with_institutions():
    """Eşleşmemiş kurum atılınca şehir/ülke sırası kaymamalı (EGE'ye Berlin yazılıyordu)."""
    from bibliotr import wos
    from bibliotr.normalize import parse_author

    a = parse_author({"name": "Ali Veli", "institution": {
        "rootTitle": ["YABANCI KURUMLAR", "EGE ÜNİVERSİTESİ", "ANKARA ÜNİVERSİTESİ"],
        "rootCode": ["y", "35", "06"], "city": ["BERLIN", "İZMİR", "ANKARA"],
        "country": ["Almanya", "", ""], "type": ["DIGER", "DEVLETUNIVERSITE", "DEVLETUNIVERSITE"]}}, 1)
    assert a.institution == "EGE ÜNİVERSİTESİ; ANKARA ÜNİVERSİTESİ"
    assert a.city == "İZMİR; ANKARA; BERLIN"
    assert a.country == "Türkiye; Türkiye; Almanya"
    assert a.institution_type == "DEVLETUNIVERSITE"
    assert [addr for addr, _ in wos._author_addresses(a, wos.WosOptions())] == [
        "EGE UNIV, Izmir, Turkey.", "ANKARA UNIV, Ankara, Turkey."]

    # Şehri boş gelen kurum, sonraki kurumun şehrini almamalı.
    b = parse_author({"name": "Can Öz", "institution": {
        "rootTitle": ["EGE ÜNİVERSİTESİ", "ANKARA ÜNİVERSİTESİ"], "city": ["", "ANKARA"], "country": ["", ""]}}, 1)
    assert b.city == "; ANKARA" and b.country == "Türkiye"
    assert [addr for addr, _ in wos._author_addresses(b, wos.WosOptions())] == [
        "EGE UNIV, Turkey.", "ANKARA UNIV, Ankara, Turkey."]


def test_as_int_and_clean_doi_edge_cases():
    from bibliotr.normalize import as_int, clean_doi

    assert as_int("inf") is None and as_int("nan") is None and as_int("2021-05-01") == 2021
    assert clean_doi("DOI 10.1234/abc") == "10.1234/abc"
    assert clean_doi("doi:10.1234/abc") == "10.1234/abc"
    assert clean_doi("https://doi.org/10.1234/abc") == "10.1234/abc"
    assert clean_doi("10.1234/doi10.5") == "10.1234/doi10.5"


def test_affiliation_guess_rejects_units_and_postcodes():
    """Adresin sondan ikinci parçası körlemesine şehir sayılmamalı (şehir ağında bölüm adı çıkıyordu)."""
    from bibliotr.normalize import _split_affiliation as guess

    assert guess("X Hastanesi, Vasküler ve Girişimsel Radyoloji Anabilim Dalı, Türkiye")[1:] == (None, "Türkiye")
    assert guess("Özyeğin Üniversitesi, Mimarlık Fakültesi, İstanbul, Türkiye")[1:] == ("İstanbul", "Türkiye")
    assert guess("Univ. of Cambridge, Dept. of Engineering, 7a JJ Thomson Avenue, Cambridge, CB2 1PZ, "
                 "United Kingdom")[1:] == ("Cambridge", "United Kingdom")
    assert guess("Assiut University, Faculty of Engineering, Asyut 71516, Egypt")[1:] == ("Asyut", "Egypt")
    assert guess("Ohio State University, Columbus, OH, USA")[1:] == ("Columbus", "USA")
    # Son parça ülke değilse ne şehir ne ülke üretilir.
    assert guess("Ürdün Üniversitesi, Lisansüstü Eğitim Okulu, İslam Hukuku")[1:] == (None, None)
    # Kurumun kendisi şehir sayılmaz.
    assert guess("Kolkata, India") == ("Kolkata", None, "India")


def test_extract_doi_joins_line_broken_doi():
    from bibliotr.normalize import extract_doi

    assert extract_doi("Nalbant F. Beslenme. 2020. doi:10.5555/s41899-020- 0712-5.") == "10.5555/s41899-020-0712-5"
    assert extract_doi("https://doi.org/10.5555/s0099- 4108(00)00187-3") == "10.5555/s0099-4108(00)00187-3"
    assert extract_doi("doi:10.1000/abc- Erişim tarihi: 2020") is None  # yarım DOI kullanılmaz
    assert extract_doi("doi:10.1000/ABC.") == "10.1000/abc"


def test_affiliation_picks_organisation_not_department():
    from bibliotr.normalize import _split_affiliation

    assert _split_affiliation("School of Kinesiology, Louisiana State University, Baton Rouge, LA, USA")[0] \
        == "Louisiana State University"
    assert _split_affiliation("Department of Physical Medicine and Rehabilitation, University Clinics of Kinshasa, "
                              "University of Kinshasa, Kinshasa, Democratic Republic of Congo")[0] == "University of Kinshasa"
    # Türkçe adres kurumla başlar: değişmez
    assert _split_affiliation("Hacettepe Üniversitesi, Spor Bilimleri Fakültesi, Ankara, Türkiye")[0] \
        == "Hacettepe Üniversitesi"
    assert _split_affiliation("Department of Surgery, Firouzgar Hospital, Tehran, Iran")[0] == "Firouzgar Hospital"
    assert _split_affiliation("Spor Kulübü, Ankara")[0] == "Spor Kulübü"


def test_extract_doi_joins_split_after_prefix_or_short_segment():
    from bibliotr.normalize import extract_doi

    assert extract_doi("Ozan T. Yürüyüş. 2018;4:76-85. https://doi.org/10.1016/j. abcd.2017.11.004") == \
        "10.1016/j.abcd.2017.11.004"
    assert extract_doi("Kaya D. Denge. 2002;166:111-7. doi: 10.1164/ xyz.166.1.at11") == "10.1164/xyz.166.1.at11"
    # Sonraki cümle DOI'ye eklenmez.
    assert extract_doi("doi:10.1000/abcdef. Erişim 2020") == "10.1000/abcdef"


def test_reference_year_guards():
    from bibliotr.normalize import guess_reference_year

    assert guess_reference_year("Kaya D. Kitap. ISBN 952-10-2084-9. Ankara, 2004.") == 2004   # 2084 yıl değil
    assert guess_reference_year("Ak, B. (2024b – online first). Başlık. Dergi, 1-26.") == 2024
    assert guess_reference_year("Ak B. Koronavirüs (2019-nCoV) salgını. Dergi. 2020;9:103-4.") == 2020
    assert guess_reference_year("Ak B. Yetişkinler, 2011-2014. Veri Özeti. 2015;(219):1-8.") == 2015
    assert guess_reference_year("Ak B. Başlık. Spor Bil Derg1998;30(6):992-1008.") == 1998
