"""Sayım tutarlılığı: yazar kimliği, ülke adları, Unicode, Türkçe başlık, Lotka MLE, WoS kanonik adlar."""

import math
import random

from bibliotr import analysis
from bibliotr.normalize import Author, Publication, as_text, normalize_publication
from bibliotr.wos import WosOptions, parse_wos, to_wos


def _pub(pid, authors, year=2020, country=None):
    p = Publication(id=pid, title=f"Yayin {pid}", year=year, doi=None, citation_count=1, language="TUR",
                    document_type="PAPER", publication_type="ARTICLE", access_type=None, database=None,
                    journal="Dergi", journal_id="1", issn=None, eissn=None, volume=None, issue=None,
                    start_page=None, end_page=None, abstract=None, url="")
    p.authors = [Author(name=n, position=i, author_id=aid, country=country) for i, (n, aid) in enumerate(authors)]
    return p


def _split_identity():
    # Aynı ada sahip iki kimlik (TR Dizin'de bölünmüş profil ya da gerçek eşsesli) + ad yazımı değişen bir kimlik
    return [
        _pub("1", [("Deniz Kaya", "10"), ("Ayşe Demir", "30")]),
        _pub("2", [("Deniz Kaya", "10")]),
        _pub("3", [("Deniz Kaya", "11"), ("Ayşe Yıldız Demir", "30")]),
        _pub("4", [("Ayşe Demir", "30")]),
    ]


def test_author_counts_follow_identity_everywhere():
    pubs = _split_identity()
    assert analysis.summary(pubs)["Yazar sayısı"] == 3
    top = analysis.top_counts(pubs, "author", 10)
    counts = dict(zip(top["Yazar"], top["Yayın"]))
    assert counts == {"Ayşe Demir": 3, "Deniz Kaya": 2, "Deniz Kaya (2)": 1}  # ad değil kimlik sayılır
    am = analysis.author_metrics(pubs, None)
    assert sorted(am["Yazar"]) == sorted(counts)
    lot, params = analysis.lotka(pubs)
    assert params["Yazar sayısı"] == 3 == len(top)
    g = analysis.field_network(pubs, "author", 1)
    assert set(g.nodes) == set(counts)


def test_wos_author_names_are_canonical_per_identity():
    pubs = _split_identity()
    recs = parse_wos(to_wos(pubs))
    # Kimlik 30 iki yazımla geçiyor; her kayıtta en sık yazım (Ayşe Demir) kullanılır.
    assert recs[0]["AU"][1] == recs[2]["AU"][1] == recs[3]["AU"][0] == "Demir, A"
    assert recs[2]["AF"][1] == "Demir, Ayse"
    # Aynı tam ada sahip iki kimlik ayırt edilebilir yazılır.
    assert recs[0]["AU"][0] == "Kaya, Deniz" and recs[2]["AU"][0] == "Kaya, Deniz 2"
    plain = parse_wos(to_wos(pubs, WosOptions(disambiguate_authors=False)))
    assert plain[2]["AU"] == ["Kaya, D", "Demir, A"]


def test_country_labels_merge_spellings():
    pubs = [_pub("1", [("A B", "1")], country="TÜRKİYE"), _pub("2", [("C D", "2")], country="Turkey"),
            _pub("3", [("E F", "3")], country="Türkiye"), _pub("4", [("G H", "4")], country="Çin; China")]
    top = analysis.top_counts(pubs, "country", 10)
    assert dict(zip(top["Ülke"], top["Yayın"])) == {"Türkiye": 3, "China": 1}
    assert analysis.country_label("Atlantis") == "Atlantis"


def test_text_is_nfc_and_turkish_title_kept():
    assert as_text("Türkiye") == "Türkiye"
    raw = {"id": "9", "language": "ENG", "publicationYear": 2021,
           "abstracts": [{"language": "ENG", "title": "Physical activity"},
                         {"language": "TUR", "title": "Fiziksel aktivite"}]}
    p = normalize_publication(raw)
    assert (p.title, p.title_en, p.title_tr) == ("Physical activity", "Physical activity", "Fiziksel aktivite")
    raw["abstracts"] = raw["abstracts"][:1]  # bazı İngilizce yayınlarda Türkçe başlık yok
    assert normalize_publication(raw).title_tr is None
    assert parse_wos(to_wos([normalize_publication(raw)], WosOptions(title="tr")))[0]["TI"] == ["Physical activity"]


def test_lotka_mle_recovers_exponent():
    random.seed(7)
    z = analysis._zeta(2.5)
    cum, c = [], 0.0
    for k in range(1, 4000):
        c += k ** -2.5 / z
        cum.append(c)
    import bisect

    xs = [bisect.bisect_left(cum, random.random()) + 1 for _ in range(4000)]
    assert abs(analysis.lotka_mle(xs) - 2.5) < 0.08
    assert abs(analysis._zeta(2) - math.pi ** 2 / 6) < 1e-9
    assert analysis.lotka_mle([1, 1, 1]) is None
    _, params = analysis.lotka(_split_identity())
    assert {"MLE üs (n)", "KS D (MLE)", "KS kritik değer (0,01)"} <= set(params)


def test_trnc_is_a_separate_country_decided_by_institution():
    from bibliotr import wos

    def one(country, inst=None, city=None):
        p = _pub("1", [("A B", "1")], country=country)
        p.authors[0].institution, p.authors[0].city = inst, city
        return analysis.author_countries(p.authors[0])

    assert one("Kuzey Kıbrıs Türk Cumhuriyeti") == one("KKTC") == one("TRNC") == ["North Cyprus"]
    assert one("Türkiye", "DOĞU AKDENİZ ÜNİVERSİTESİ") == ["North Cyprus"]  # TR Dizin ülkeyi yanlış yazmış
    assert one("Kıbrıs Cumhuriyeti", "LEFKE AVRUPA ÜNİVERSİTESİ") == ["North Cyprus"]
    assert one("Cyprus", "YAKIN DOĞU ÜNİVERSİTESİ") == one(None, None, "Girne") == ["North Cyprus"]
    assert one("Cyprus", "University of Nicosia") == one("Kıbrıs") == ["Cyprus"]
    assert one("Türkiye", "Doğu Akdeniz Kalkınma Ajansı") == ["Türkiye"]  # Türkiye'deki benzer ad
    assert one("Türkiye; Kıbrıs", "Hacettepe Üniversitesi; Yakın Doğu Üniversitesi") == ["Türkiye", "North Cyprus"]
    assert one(None) == []
    pubs = [_pub("1", [("A B", "1")], country="Türkiye"), _pub("2", [("C D", "2")], country="KKTC")]
    top = analysis.top_counts(pubs, "country", 10)
    assert dict(zip(top["Ülke"], top["Yayın"])) == {"Türkiye": 1, "North Cyprus": 1}
    # WoS adresi "North Cyprus." yazılır; bibliometrix bunu "CYPRUS." eşleşmesiyle Kıbrıs sayar.
    rec = parse_wos(to_wos([pubs[1]]))[0]
    assert rec["C1"][0].endswith("North Cyprus.")
    assert wos.check_wos(to_wos([pubs[1]]))["countries"] == {"CYPRUS": 1}


def test_doi_link_only_when_trdizin_has_no_target():
    """Kaynak metni A makalesini anıp B'nin DOI'sini yazmışsa TR Dizin'in A bağı geçerli; DOI'den B'ye bağ kurulmaz."""
    from bibliotr.normalize import Reference
    from bibliotr.wos import WosOptions, canonical_references

    a, b, citing, other = _pub("1", [("Deniz Kaya", "10")]), _pub("2", [("Deniz Kaya", "10")]), \
        _pub("3", [("Ece Arslan", "20")]), _pub("4", [("Ece Arslan", "20")])
    b.doi = "10.1000/b"
    # TR Dizin A'ya bağlamış ama metindeki DOI B'yi gösteriyor (yazarın hatası)
    r1 = Reference("Kaya D. Makale A. Dergi A. 2020. doi:10.1000/B", 1, target_id="1")
    r2 = Reference("Kaya D. Makale B. Dergi B. 2020. doi:10.1000/b", 1)  # bağsız: DOI kullanılır
    citing.references, other.references = [r1], [r2]
    pubs = [a, b, citing, other]
    assert analysis.citation_edges(pubs) == {("3", "1"), ("4", "2")}
    cr = canonical_references(pubs, WosOptions())
    assert cr[r1.key] != cr[r2.key]  # r1 A'nın etiketiyle, r2 B'nin etiketiyle yazılır


def test_network_clusters_do_not_depend_on_hash_seed():
    """Küme ataması Python'un karma tohumuna (PYTHONHASHSEED) bağlı olmamalı: aynı veri, aynı kümeler."""
    import os
    import subprocess
    import sys

    code = (
        "import random\n"
        "from bibliotr import analysis\n"
        "rnd = random.Random(7)\n"
        "words = [f'kelime{i}' for i in range(60)]\n"
        "docs = [rnd.sample(words, 4) for _ in range(150)]\n"
        "g = analysis.cooccurrence_network(docs)\n"
        "c = analysis.detect_communities(g)\n"
        "print(sorted(c.items()))\n"
    )
    outs = set()
    for seed in ("1", "2", "3", "4"):
        env = dict(os.environ, PYTHONHASHSEED=seed)
        outs.add(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                                check=True).stdout)
    assert len(outs) == 1
