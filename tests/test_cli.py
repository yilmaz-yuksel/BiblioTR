from bibliotr import cli


def test_parse_helpers():
    assert cli.parse_facets(["database=SOCIAL", "facet-subject=Fen > Tıp > Onkoloji"]) == [
        ("facet-database", "SOCIAL"), ("facet-subject", "Fen > Tıp > Onkoloji")]
    assert cli.parse_years("2019-2021,2024") == [2019, 2020, 2021, 2024]
    assert cli.parse_years(None) is None


def test_export_and_stats(raw_records, tmp_path, capsys):
    from bibliotr.storage import Store

    db = tmp_path / "t.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("d"), raw_records)
    cli.main(["--db", str(db), "stats", "--dataset", "d"])
    assert "Yayın sayısı" in capsys.readouterr().out
    out = tmp_path / "x.txt"
    cli.main(["--db", str(db), "export", "--dataset", "d", "--format", "wos", "--out", str(out)])
    assert out.read_text(encoding="utf-8").startswith("FN ")
    cli.main(["--db", str(db), "export", "--dataset", "d", "--format", "vosviewer", "--network", "author",
              "--min-occurrence", "1", "--out", str(tmp_path / "v.txt")])
    assert (tmp_path / "v_map.txt").exists() and (tmp_path / "v_network.txt").exists()


def test_merge_command(raw_records, tmp_path, capsys):
    from bibliotr.storage import Store

    db = tmp_path / "t.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("a"), raw_records[:2])
        s.add_records(s.create_dataset("b"), raw_records[1:])
    out = tmp_path / "o.csv"
    cli.main(["--db", str(db), "merge", "ab", "a", "b", "--out", str(out)])
    text = capsys.readouterr().out
    assert "BİRLEŞİM" in text and "3 tekil yayın" in text
    assert out.read_text(encoding="utf-8-sig").startswith("Veri seti,Kayıt")


def test_openalex_command(raw_records, tmp_path, capsys, monkeypatch):
    from bibliotr import openalex
    from bibliotr.storage import Store

    db = tmp_path / "t.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("d"), raw_records)
        ids = [p.id for p in s.publications("d")]
    calls = []

    def fake_match(pubs, client, by_title=True, on_progress=None, report=None):
        calls.append((len(pubs), by_title, client.mailto))
        report.update({"title_not_searched": 1, "stopped": "OpenAlex kotası doldu.", "calls": {"search": 5}})
        return [openalex.OpenAlexMatch(pubs[0].id, "W9", 12, 2020, None, "article", "tr", "title", 0.97)]

    monkeypatch.setattr(openalex, "match_publications", fake_match)
    cli.main(["--db", str(db), "openalex", "--dataset", "d", "--mailto", "x@y.z"])
    out = capsys.readouterr().out
    assert "OpenAlex'te bulunan: 1/3 (DOI ile 0, başlıkla 1)" in out and calls == [(3, True, "x@y.z")]
    assert "1 yayın başlıkla aranamadı. OpenAlex kotası doldu." in out and "arama 5" in out
    cli.main(["--db", str(db), "openalex", "--dataset", "d", "--no-title"])  # bulunan tekrar sorulmaz
    assert calls[-1] == (2, False, None)
    with Store(db) as s:
        p = {q.id: q for q in s.publications("d")}[ids[0]]
        assert (p.openalex_id, p.openalex_citations, p.openalex_matched_by) == ("W9", 12, "title")
        from bibliotr import analysis
        rows = analysis.citation_sources(list(s.publications("d")))["Kaynak"].tolist()
        assert rows[-1] == "OpenAlex (doğrudan)" and "OpenAlex (TR Dizin)" in rows
        s.delete_dataset("d")
        assert s.openalex_map() == {}  # yayınla birlikte silinir


def test_thesaurus_command(raw_records, tmp_path, capsys):
    from bibliotr.storage import Store

    db = tmp_path / "t.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("d"), raw_records)
    base = ["--db", str(db), "thesaurus"]
    cli.main(base + ["add", "keyword", "Bibliometrics", "Bibliyometri"])
    cli.main(base + ["add", "keyword", "citation analysis", "Atıf analizi"])
    cli.main(base + ["list"])
    assert "Bibliometrics" in capsys.readouterr().out
    cli.main(["--db", str(db), "stats", "--dataset", "d"])
    captured = capsys.readouterr()
    assert "sözlüğü uygulandı (2 girdi" in captured.err
    # Uygulanmış halde "Bibliyometri" iki yayında, "Bibliometrics" hiç görünmez.
    kw_table = captured.out.split("En sık anahtar kelime:")[1]
    assert "Bibliometrics" not in kw_table and "Bibliyometri" in kw_table
    cli.main(["--db", str(db), "stats", "--dataset", "d", "--raw"])
    assert "Bibliometrics" in capsys.readouterr().out.split("En sık anahtar kelime:")[1]
    out = tmp_path / "vos.txt"
    cli.main(base + ["export", "--format", "vosviewer", "--field", "keyword", "--out", str(out)])
    assert "bibliometrics\tbibliyometri" in out.read_text(encoding="utf-8")
    js = tmp_path / "t.json"
    cli.main(base + ["export", "--format", "json", "--out", str(js)])
    cli.main(base + ["remove", "keyword", "Bibliometrics"])
    cli.main(base + ["import", str(js)])
    assert "1 girdi eklendi" in capsys.readouterr().out
    cli.main(base + ["suggest", "--dataset", "d", "--field", "institution"])
    cli.main(base + ["homonyms", "--dataset", "d"])
    assert "Kısa adı çakışan farklı yazar yok" in capsys.readouterr().out


def test_author_merge_options(tmp_path, capsys):
    from bibliotr.storage import Store

    def rec(rid, aid, inst):
        return {"id": rid, "title": f"Yayın {rid}", "publicationYear": 2020, "documentType": "PAPER",
                "authors": [{"authorId": aid, "inPublicationName": "Deniz Kaya", "institutionName": [inst]}]}

    db = tmp_path / "t.db"
    with Store(db) as s:
        s.add_records(s.create_dataset("d"), [rec(1, 10, "Ankara Üniversitesi"), rec(2, 11, "Ankara Üniversitesi")])
    cli.main(["--db", str(db), "stats", "--dataset", "d"])
    assert "Yazar sayısı                     2" in capsys.readouterr().out
    cli.main(["--db", str(db), "stats", "--dataset", "d", "--author-merge", "orcid+institution"])
    cap = capsys.readouterr()
    assert "1 yazar kimliği birleştirildi" in cap.err and "Yazar sayısı                     1" in cap.out
    base = ["--db", str(db), "thesaurus", "auto", "--dataset", "d"]
    cli.main(base)
    assert "1 yazar kimliği birleşir" in capsys.readouterr().out
    with Store(db) as s:
        assert len(s.load_thesaurus()) == 0  # --save olmadan kaydedilmez
    cli.main(base + ["--save"])
    with Store(db) as s:
        assert len(s.load_thesaurus()) == 1


def test_output_survives_windows_console_encoding(monkeypatch):
    """Windows'ta yönlendirilmiş çıktı cp1254 olur; o kodlamada olmayan karakter komutu çökertmemeli."""
    import io
    import sys

    from bibliotr import cli

    raw = io.BytesIO()
    stream = io.TextIOWrapper(raw, encoding="cp1254", newline="\n")  # Windows'ta da \n yazılsın
    monkeypatch.setattr(sys, "stdout", stream)
    cli._safe_streams()
    print("β-alanin ve VO₂max üzerine")
    stream.flush()
    assert raw.getvalue().decode("cp1254") == "?-alanin ve VO?max üzerine\n"
