from bibliotr.storage import Store


def test_roundtrip_and_dedupe(raw_records, tmp_path):
    with Store(tmp_path / "t.db") as s:
        ds = s.create_dataset("biblio", "bibliometri", {"facets": []})
        assert s.add_records(ds, raw_records) == 3
        assert s.add_records("biblio", raw_records[:1]) == 1  # tekrar eklemek çoğaltmaz
        assert s.create_dataset("biblio", "yeni") == ds
        info = s.list_datasets()[0]
        assert info["n"] == 3 and info["query"] == "yeni"
        pubs = s.publications("biblio")
        assert {p.id for p in pubs} == {"1001", "1002", "1003"}
        n_auth = s.conn.execute("SELECT COUNT(*) FROM publication_authors").fetchone()[0]
        assert n_auth == 5
        assert s.reindex() == 3


def test_shared_publications_and_delete(raw_records, tmp_path):
    with Store(tmp_path / "t.db") as s:
        s.add_records(s.create_dataset("a"), raw_records)
        s.add_records(s.create_dataset("b"), raw_records[:1])
        s.delete_dataset("a")
        assert [p.id for p in s.publications()] == ["1001"]
        assert [d["name"] for d in s.list_datasets()] == ["b"]


def test_merge_datasets(raw_records, tmp_path):
    import pytest

    with Store(tmp_path / "t.db") as s:
        s.add_records(s.create_dataset("tr"), raw_records[:2])
        s.add_records(s.create_dataset("en"), raw_records[1:])
        assert s.merge_datasets("hepsi", ["tr", "en", "tr"]) == 3
        assert s.dataset_members("hepsi") == {"1001", "1002", "1003"}
        assert s.dataset_members("tr") == {"1001", "1002"}  # kaynaklar değişmez
        info = {d["name"]: d for d in s.list_datasets()}
        assert info["hepsi"]["params"] == {"merged_from": ["tr", "en"]}
        # Tekrar çalıştırmak çoğaltmaz, silinen kaynak kayıtları hedefte kalır.
        assert s.merge_datasets("hepsi", ["tr"]) == 3
        s.delete_dataset("en")
        assert len(s.publications("hepsi")) == 3
        with pytest.raises(ValueError):
            s.merge_datasets("tr", ["tr"])
        with pytest.raises(KeyError):
            s.merge_datasets("x", ["yok"])


def test_readonly_does_not_touch_file(raw_records, tmp_path):
    import hashlib
    import sqlite3

    import pytest

    db = tmp_path / "frozen.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("d"), raw_records)
    # OpenAlex tablosu olmayan eski bir dondurulmuş dosyayı taklit et
    con = sqlite3.connect(db)
    con.execute("DROP TABLE openalex_works")
    con.commit()
    con.close()
    digest = hashlib.sha256(db.read_bytes()).hexdigest()
    with Store(db, readonly=True) as s:
        assert len(s.publications("d")) == 3 and s.openalex_map() == {}
        with pytest.raises(sqlite3.OperationalError):
            s.create_dataset("x")
    assert hashlib.sha256(db.read_bytes()).hexdigest() == digest
    with pytest.raises(FileNotFoundError):
        Store(tmp_path / "yok.db", readonly=True)


def test_thesaurus_persistence(tmp_path):
    from bibliotr.thesaurus import Thesaurus

    db = tmp_path / "t.db"
    with Store(db) as s:
        assert len(s.load_thesaurus()) == 0 and s.thesaurus_version() == ""
        t = Thesaurus()
        t.add("keyword", "bibliometri", "bibliyometri")
        s.save_thesaurus(t)
        v1 = s.thesaurus_version()
        t.add("keyword", "Bibliometrics", "bibliyometri")
        s.save_thesaurus(t)
        assert s.thesaurus_version() != v1
    with Store(db, readonly=True) as s:
        assert s.load_thesaurus().resolve("keyword", "BIBLIOMETRI") == "bibliyometri"


def test_data_version_changes_with_records_and_openalex():
    """Arayüz önbelleğinin anahtarı: kayıt ya da OpenAlex eşleşmesi değişince değişmeli."""
    from bibliotr.openalex import OpenAlexMatch

    s = Store(":memory:")
    s.add_records(s.create_dataset("d"), [{"id": 1, "title": "a"}])
    v1 = s.data_version("d")
    assert v1 == s.data_version("d")
    s.save_openalex([OpenAlexMatch("1", "W1", 3, 2020, None, None, None, "doi")])
    assert s.data_version("d") != v1
    assert s.data_version("yok") == s.data_version("yok")  # olmayan veri seti hata vermez
    s.close()
