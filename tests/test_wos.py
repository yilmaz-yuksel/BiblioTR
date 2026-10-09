"""WoS dışa aktarımının bibliometrix kurallarına uygunluğu (R/isi2df.R, metaTagExtraction.R, histNetwork.R)."""

import copy
import re

import pytest

from bibliotr import wos
from bibliotr.client import extract_records
from bibliotr.normalize import Reference, normalize_publication

from .conftest import FIXTURES


@pytest.fixture
def subject_response():
    import json

    return json.loads((FIXTURES / "search_subject.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("value,expected", [
    ("Türkiye", "TURKEY"), ("TÜRKİYE", "TURKEY"), ("Turkey", "TURKEY"), ("Kırgızistan", "KYRGYZSTAN"),
    ("Kyrgyzstan", "KYRGYZSTAN"), ("Amerika Birleşik Devletleri", "USA"), ("Birleşik Krallık", "UNITED KINGDOM"),
    ("Kıbrıs Cumhuriyeti", "CYPRUS"), ("Güney Kore", "KOREA"), ("Diğer", None), ("", None),
])
def test_country_names_match_bibliometrix_list(value, expected):
    assert wos.country_name(value) == expected
    if expected:
        assert expected in wos.BIBLIOMETRIX_COUNTRIES


def test_institution_and_dotted_abbreviations():
    assert wos.institution_short("HACETTEPE ÜNİVERSİTESİ") == "HACETTEPE UNIV"
    assert wos.institution_short("T.C. SAĞLIK BAKANLIĞI") == "TC SAGLIK BAKANLIGI"
    assert wos.institution_short("Kyrgyz - Turkish Manas University") == "KYRGYZ - TURKISH MANAS UNIV"


def _all_pubs(detail_response, subject_response):
    return [normalize_publication(r) for r in extract_records(detail_response) + extract_records(subject_response)]


def test_mandatory_fields_and_addresses(detail_response, subject_response):
    txt = wos.to_wos(_all_pubs(detail_response, subject_response))
    report = wos.check_wos(txt)
    assert report["missing_mandatory"] == {}
    assert report["documents_with_country"] == report["documents"] == 3
    assert report["documents_with_C3"] == 3
    for rec in wos.parse_wos(txt):
        for addr in rec.get("C1", []):
            body = re.sub(r"\[.*?\] ", "", addr)
            assert body.endswith(".") and body.count(".") == 1  # tek nokta: adres sonu
        for cr in rec.get("CR", []):
            assert ";" not in cr
        assert txt.isascii()


def test_same_work_is_written_identically(detail_response):
    base = normalize_publication(extract_records(detail_response)[0])
    other = copy.deepcopy(base)
    other.id, other.doi = "2", None
    # Aynı eser farklı biçimde (Vancouver, DOI'li) yazılmış
    other.references = [Reference("1. Hartley BJ. The seasonal distribution of athletic performance. "
                                  "J Cumb Acad Sci. 1926;16:317-23.", 1)]
    base.references = [Reference("Hartley, B. J. (1926). “The seasonal distribution of athletic performance”, "
                                 "Journal of the Cumberland Academy of Sciences, 16(12), 317-323.", 1)]
    txt = wos.to_wos([base, other])
    crs = [r["CR"][0] for r in wos.parse_wos(txt)]
    assert crs[0] == crs[1] == "Hartley BJ, 1926, JOURNAL OF THE CUMBERLAND ACADEMY OF SCIENCES, V16, P317"
    assert wos.check_wos(txt)["cited_more_than_once"] == 1


def test_local_citation_label_matches_bibliometrix(detail_response):
    cited = normalize_publication(extract_records(detail_response)[0])  # DOI'li, id 1163868
    citing = copy.deepcopy(cited)
    citing.id, citing.doi, citing.year = "999", "10.1/xyz", 2025
    citing.references = [Reference("Özgen, Ö. K. vd. (2023). İçerik analizi...", 1, target_id="1163868")]
    txt = wos.to_wos([cited, citing])
    cr = wos.parse_wos(txt)[1]["CR"][0]
    assert cr == ("Ozgen OK, 2023, YESILIRMAK UNIVERSITESI SOSYAL BILIMLER ENSTITUSU DERGISI, P235, "
                  "DOI 10.5555/yusbed.1187342")
    report = wos.check_wos(txt)
    assert report["local_citations"] == 1 and report["documents_locally_cited"] == 1


def test_keyword_and_ascii_options(detail_response):
    p = normalize_publication(extract_records(detail_response)[0])
    en = wos.to_wos([p], wos.WosOptions(keywords="en", id_keywords="tr"))
    assert "DE Content analysis; Qualitative research" in en and "ID icerik analizi; nitel arastirma" in en
    raw = wos.to_wos([p], wos.WosOptions(ascii=False, id_keywords="none"))
    assert "AU Özgen, ÖK" in raw and "\nID " not in raw
    assert "TI CONTENT ANALYSIS AS A RESEARCH" in wos.to_wos([p], wos.WosOptions(title="en"))


def test_cli_export_and_check(detail_response, tmp_path, capsys):
    from bibliotr import cli
    from bibliotr.storage import Store

    db = tmp_path / "t.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("d"), extract_records(detail_response))
    out = tmp_path / "wos.txt"
    cli.main(["--db", str(db), "export", "--dataset", "d", "--format", "wos", "--out", str(out),
              "--wos-keywords", "en"])
    text = capsys.readouterr().out
    assert "Ülkesi bulunan belge: 1/1" in text and "TURKEY" in text
    assert "DE Content analysis;" in out.read_text(encoding="utf-8")
    cli.main(["wos-check", str(out)])
    assert "Kurum alanı (C3) olan belge: 1/1" in capsys.readouterr().out


def test_cr_doi_with_semicolon_is_not_split():
    from bibliotr.normalize import Reference

    ref = Reference(text="Kaya D (1999). Baslik. Dergi, 15, 205. doi:10.5555/(sici)1520-7599(199905/06)15:33.0.co;2-o",
                    position=0)
    assert ";" in (ref.doi or "")
    cr = wos.reference_cr(ref, wos.WosOptions())
    assert ";" not in cr and "%3B2-o" in cr


def test_reference_fields_beyond_apa_and_vancouver():
    from bibliotr.normalize import Reference

    def f(text):
        r = wos.reference_fields(Reference(text=text, position=1))
        return r["source"], r["volume"], r["page"]

    # Virgülle ayrılmış dergi künyesi, "pp." ve sonda yıl
    assert f('Kaya, D., "Spor ve sağlık" Spor Bilimleri Dergisi, 3(2). pp. 45-54, 2001.') == ("Spor Bilimleri Dergisi", "3", "45")
    assert f("Arslan E, Kaya D, Yorgunluk ve uyku, Hemşirelik Dergisi, 2014, 7(2), 90-113.") == ("Hemşirelik Dergisi", "7", "90")
    assert f("Öz C: Egzersiz ve ağrı. Ağrı Dergisi 25: 26–44, 2011") == ("Ağrı Dergisi", "25", "26")
    assert f("Ak S, Kaya D. Beslenme. Spor Hekimliği Dergisi, 2010, 54, 48–53.") == ("Spor Hekimliği Dergisi", "54", "48")
    # Ay ve gün içeren Vancouver tarihi kaynak adı sayılmaz
    assert f("Kaya D. Uyku ve egzersiz. J Sport Sci. 2017 Dec 7;84:225-31.") == ("J Sport Sci", "84", "225")
    # Erişim tarihi sayfa sayılmaz
    assert f("Kaya, D. (2015). Okul ve spor. Eğitim Dergisi, 5(2). 23.05.2023 tarihinde erişildi.") == ("Eğitim Dergisi", "5", None)
    # Kitap: kaynak adı başlık; baskı notu ve yayın yeri atılır
    assert f("Kaya, D. (2019). Spor psikolojisi (3. Baskı). Ankara: Gazi Kitabevi.") == ("Spor psikolojisi", None, None)
    assert f("Kaya D, Öz C. 2016. Antrenman bilgisi. Ankara: Spor Yayınevi.") == ("Antrenman bilgisi", None, None)
    # Tez ve bağlantılar: kaynak adı yok, "[Link]" ve "pp." CR satırına girmez
    assert f("Kaya D. Sporcularda uyku. Yüksek Lisans Tezi, Gazi Üniversitesi, 2010.") == (None, None, None)
    cr = wos.reference_cr(Reference(text="Kaya D, Öz C. Gençlerde sağlık. Ankara: Sağlık Bakanlığı; 2012. [Link]", position=1),
                          wos.WosOptions())
    assert "LINK" not in cr and " PP" not in cr


def test_disambiguated_author_names_have_no_periods():
    """bibliometrix atıf etiketinde noktayı boşluğa çevirir, belge etiketinde çevirmez: AU noktasız olmalı."""
    from bibliotr.normalize import Author, Publication
    from bibliotr.wos import WosOptions, author_map

    def pub(pid, name, aid):
        p = Publication(id=pid, title="T", year=2020, doi=None, citation_count=0, language="TUR",
                        document_type="PAPER", publication_type="ARTICLE", access_type=None, database=None,
                        journal="Dergi", journal_id="1", issn=None, eissn=None, volume=None, issue=None,
                        start_page=None, end_page=None, abstract=None, url="")
        p.authors = [Author(name=name, position=1, author_id=aid)]
        return p

    # İkisinin kısa adı da "Tasci, FN": tam adla ayırt edilir
    m = author_map([pub("1", "F. Nurten TAŞÇI", "1"), pub("2", "Fatma Nurten TAŞÇI", "2")], WosOptions())
    assert m["id:1"] == "Tasci, F Nurten" and m["id:2"] == "Tasci, Fatma Nurten"


@pytest.mark.parametrize("text, author", [
    ("Referans25. Yılmaztepe K, Demir D. Egzersiz ve uyku. Spor Derg. 2006;12(1):28-34.", "Yılmaztepe K"),
    ("Sağlık Kurumu. (2000). Okul yönetmeliği. Ankara: Devlet Basımevi.", "Sağlık Kurumu"),
    ("World Sport Organization. Global recommendations on activity. 2021.", "World Sport Organization"),
    ("ABC (Ankara Beslenme Cemiyeti) (2012a) Rapor. Bülten, 61:9-39.", "ABC"),
    ("Toprak, W.R., O'connell, E.R. (1962). Boy ve kuvvet. Research Quarterly, 33(3), 439-443.", "Toprak WR"),
    ("16. Durmaz II HL. (2009). Spor fırsatları. Journal of Sport, 31 (4), 417-433.", "Durmaz HL"),
    ("Pekmez Jr, R. S., & Kılıç, R. T. (1984). Egzersiz. Dergi, 1(2), 3-4.", "Pekmez RS"),
    ("[11] I. B. Karaca, S. L. Naz, Psikomotor yavaşlama, Psychiatry Res., 155(3), 211–220, 2009.", "Karaca IB"),
    ("[35] H. Arslan and H. Bora, Eds., Feature Selection. Chapman and Hall/CRC, 2007.", "Arslan H"),
    ("Okan, Ş.Uzun, Ş. (2021). Diyalog. Dergi, 1(2), 3-4.", "Okan Ş"),
])
def test_reference_first_author_formats(text, author):
    assert wos.reference_fields(Reference(text=text, position=1))["author"] == author


def test_reference_source_cleanup():
    def f(text):
        return wos.reference_fields(Reference(text=text, position=1))

    assert f("18. Kaya I, Ak C. Başlık. Osmangazi J Med [Article in Turkish] 2008;30:19–28.")["source"] == "Osmangazi J Med"
    assert f("15. Kaya S, Ak B. Başlık. Turk J Rehabil. Nisan 2021;32(1):44-50.")["source"] == "Turk J Rehabil"
    assert f("Ak HA, Bal WA. Başlık. Clin Med Pediatr. &nbsp;2019;13:1179.")["source"] == "Clin Med Pediatr"
    # "Kastamonu" ay adı değildir.
    assert f("39. Ak N, Bal B. Başlık. Sağlık Akad Kastamonu. 2019;4(3):88-210.")["source"] == "Sağlık Akad Kastamonu"
    r = f("Tunç C, Ham S (2004): Başlık. Med Sci Sports Exerc, 36, 9, 1567-1573.")
    assert (r["volume"], r["page"]) == ("36", "1567")
    r = f("Ak, Q. (2018). Başlık. Journal of Diabetes Research, 2018, 7198274.")
    assert (r["source"], r["volume"], r["page"]) == ("Journal of Diabetes Research", None, "7198274")
    assert f("Ak, U. (1992). Research Methods in Business. Canada: John Willey & Sons.")["source"] == \
        "Research Methods in Business"
    assert f("Bakanlık. (2000). Taşımalı ilköğretim yönetmeliği. Ankara: Devlet Basımevi.")["source"] is None
