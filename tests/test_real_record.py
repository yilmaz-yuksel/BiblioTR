"""Gerçek TR Dizin yanıtının yapısını koruyan kurgusal kayıtla (api/publicationById/1163868) testler."""

import copy

from bibliotr import analysis, export
from bibliotr.client import TRDizinClient, extract_records
from bibliotr.normalize import Reference, normalize_publication, reference_key
from bibliotr.storage import Store

from .conftest import FakeResponse


def test_title_from_abstracts_not_order_title(real_pub):
    assert real_pub.title.startswith("BİR SPOR BİLİMLERİ")
    assert real_pub.title_en.startswith("CONTENT ANALYSIS AS A RESEARCH APPROACH")


def test_basic_fields(real_pub):
    p = real_pub
    assert (p.id, p.year, p.doi, p.citation_count) == ("1163868", 2023, "10.5555/yusbed.1187342", 6)
    assert (p.database, p.document_type, p.publication_type, p.access_type) == ("SOCIAL", "PAPER", "RESEARCH", "OPEN")
    assert (p.issue, p.volume, p.start_page, p.end_page) == ("55", None, "235", "258")
    assert p.publish_date == "2023-03-03"
    assert (p.citations_crossref, p.citations_openalex, p.citations_opencitations) == (16, 0, 11)
    assert (p.view_count, p.download_count) == (32, 11)
    assert p.has_details


def test_authors_sorted_by_order(real_pub):
    assert [a.name for a in real_pub.authors] == ["Ömer Kemal ÖZGEN", "SERHAT KEREM KARAOGLU", "Kaan E. İLGEN"]
    assert [a.position for a in real_pub.authors] == [1, 2, 3]


def test_matched_and_unmatched_institutions(real_pub):
    ozgen, karaoglu, ilgen = real_pub.authors
    assert ozgen.institution_matched and ozgen.institution == "ORTA DOĞU TEKNİK ÜNİVERSİTESİ"
    assert (ozgen.city, ozgen.country, ozgen.institution_type) == ("ANKARA", "Türkiye", "DEVLETUNIVERSITE")
    # "Tanımlanmamış Kurum" / "Diğer" yerine adresten tahmin
    assert not ilgen.institution_matched
    assert (ilgen.institution, ilgen.city, ilgen.country) == ("Özyeğin Üniversitesi", "İstanbul", "Türkiye")
    assert ilgen.institution_code is None
    # Eşleşmemiş ODTÜ adı, eşleşmiş olanla aynı sayılır
    top = analysis.top_counts([real_pub], "institution", 5)
    assert top.set_index("Kurum")["Yayın"].to_dict() == {"ORTA DOĞU TEKNİK ÜNİVERSİTESİ": 1, "Özyeğin Üniversitesi": 1}


def test_references_and_citing(real_pub):
    refs = real_pub.references
    assert len(refs) == 5 and [r.position for r in refs] == [1, 3, 37, 39, 43]
    assert refs[0].year == 2001 and refs[2].year == 1926 and refs[4].year == 1993
    assert refs[3].target_id == "184334" and refs[3].key == "trdizin:184334"
    assert refs[2].key == "hartley|1926|the seasonal distribution of"
    assert real_pub.cited_by == ["1228999", "1272030", "1362149"]


def test_reference_key_matches_across_formats():
    a = Reference("Hartley, B. J. (1926). The seasonal distribution of athletic performance. JCAS.", 1)
    b = Reference("HARTLEY BJ (1926) “The Seasonal Distribution of Athletic Performance”, J. Cumb.", 1)
    assert reference_key(a) == reference_key(b)


def test_wos_contains_references(real_pub):
    txt = export.to_wos([real_pub])
    assert "CR Barlow A, 2001, BORDERS OF DISCIPLINES\n" in txt.replace("\r", "")  # kitap: başlık, şehir/yayınevi yok
    assert "   Hartley BJ, 1926, JOURNAL OF THE CUMBERLAND ACADEMY OF SCIENCES, V16, P317" in txt
    assert "NR 5" in txt
    assert "AU Ozgen, OK\n   Karaoglu, SK\n   Ilgen, KE" in txt
    # Eşleşmiş (ODTÜ) ve adresten çıkarılmış (Orta Doğu Teknik Üniversitesi) kurum tek adreste birleşir
    assert "C1 [Ozgen, Omer Kemal; Karaoglu, Serhat Kerem] ORTA DOGU TEKNIK UNIV, Ankara, Turkey." in txt
    assert "   [Ilgen, Kaan E] OZYEGIN UNIV, Istanbul, Turkey." in txt


def test_citation_analyses(real_pub, detail_response):
    # Aynı kaynaklara atıf yapan ve ilk yayına atıf yapan ikinci bir yayın kur.
    raw = copy.deepcopy(extract_records(detail_response)[0])
    raw.update(id=1228999, orderCitationCount=0, citedReferences=[])
    raw["references"] = raw["references"][:3] + [
        {"context": "x", "order": 99, "targetPublication": 1163868, "year": "2023"}]
    other = normalize_publication(raw)
    pubs = [real_pub, other]

    assert analysis.citation_edges(pubs) == {("1228999", "1163868")}
    assert analysis.local_citations(pubs) == {"1163868": 1, "1228999": 0}
    mc = analysis.most_cited(pubs, by="Yerel atıf")
    assert mc.iloc[0]["Yerel atıf"] == 1

    tr = analysis.top_references(pubs, 3)
    assert tr["Atıf yapan yayın"].tolist() == [2, 2, 2]

    cg = analysis.coupling_network(pubs)
    assert cg.number_of_edges() == 1 and list(cg.edges(data="weight"))[0][2] == 3

    cn = analysis.citation_network(pubs)
    assert cn.is_directed() and cn.number_of_edges() == 1

    cocit = analysis.field_network(pubs, "reference", min_occurrence=2)
    assert cocit.number_of_nodes() == 3 and cocit.number_of_edges() == 3
    assert not analysis.network_table(cn).empty

    src = analysis.citation_sources(pubs).set_index("Kaynak")
    assert src.loc["CrossRef", "Toplam atıf"] == 32


def test_enrich_only_missing(detail_response, fake_session):
    search_rec = {"id": 1163868, "title": "x"}
    already = {"id": 5, "references": [], "citedReferences": []}
    s = fake_session([FakeResponse(detail_response)])
    c = TRDizinClient(min_interval=0, session=s)
    records = [search_rec, already]
    assert c.enrich(records) == 1
    assert len(s.calls) == 1 and s.calls[0][0].endswith("/api/publicationById/1163868")
    assert len(normalize_publication(records[0]).references) == 5


def test_storage_references_and_migration(detail_response, tmp_path):
    import sqlite3

    db = tmp_path / "old.db"
    # Eski şemayı taklit et: yeni sütunlar yok.
    con = sqlite3.connect(db)
    con.executescript("""
        CREATE TABLE publications (id TEXT PRIMARY KEY, title TEXT, year INTEGER, doi TEXT,
            citation_count INTEGER, language TEXT, document_type TEXT, publication_type TEXT,
            access_type TEXT, database TEXT, journal TEXT, journal_id TEXT, issn TEXT, eissn TEXT,
            volume TEXT, issue TEXT, start_page TEXT, end_page TEXT, abstract TEXT, url TEXT,
            raw_json TEXT NOT NULL, fetched_at TEXT NOT NULL);
        CREATE TABLE publication_authors (publication_id TEXT, position INTEGER, name TEXT, author_id TEXT,
            orcid TEXT, institution TEXT, city TEXT, country TEXT, PRIMARY KEY (publication_id, position));
        CREATE TABLE publication_references (publication_id TEXT, position INTEGER, reference TEXT,
            PRIMARY KEY (publication_id, position));
    """)
    con.close()
    with Store(db) as s:
        s.add_records(s.create_dataset("d"), extract_records(detail_response))
        rows = s.conn.execute(
            "SELECT position, target_id, year FROM publication_references ORDER BY position").fetchall()
        assert [tuple(r) for r in rows][3] == (39, "184334", 2015)
        assert s.conn.execute("SELECT COUNT(*) FROM publication_citing").fetchone()[0] == 3
        row = s.conn.execute("SELECT title_en, citations_crossref, has_details FROM publications").fetchone()
        assert row["citations_crossref"] == 16 and row["has_details"] == 1
        types = s.conn.execute("SELECT institution_type FROM publication_authors ORDER BY position").fetchall()
        assert types[0][0] == "DEVLETUNIVERSITE"
