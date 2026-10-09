from bibliotr import analysis, export


def test_names():
    assert export.full_name("Ali Veli YILDIZ") == "Yıldız, Ali Veli"
    assert export.short_name("Ali Veli YILDIZ") == "Yıldız, AV"
    assert export.short_name("İSMAİL IŞIK") == "Işık, İ"
    assert export.full_name("Kaya, Ayşe") == "Kaya, Ayşe"


def test_wos(pubs):
    txt = export.to_wos(pubs)
    assert txt.startswith("FN Clarivate Analytics Web of Science\nVR 1.0")
    assert txt.count("\nER\n") == 3 and txt.rstrip().endswith("EF")
    assert "AU Yildiz, A\n   Kaya, A" in txt  # varsayılan: ASCII
    assert "DE Bibliyometri; Atif analizi" in txt
    assert "DT Review" in txt and "LA Turkish" in txt
    assert "C1 [Yildiz, Ali] HACETTEPE UNIV, Ankara, Turkey." in txt
    assert "RP Yildiz, A (corresponding author), HACETTEPE UNIV, Ankara, Turkey." in txt
    assert "UT TRDIZIN:1001" in txt


def test_ris_bib_csv_excel(pubs):
    ris = export.to_ris(pubs)
    assert ris.count("TY  - JOUR") == 3 and "DO  - 10.1234/ABC.2019.1" in ris
    bib = export.to_bibtex(pubs)
    assert "@article{trdizin1001," in bib and "pages = {1--20}" in bib
    csv = export.to_csv(pubs).decode("utf-8-sig")
    assert csv.splitlines()[0].startswith("id,title,year")
    assert export.to_excel(pubs)[:2] == b"PK"


def test_vosviewer(pubs):
    g = analysis.field_network(pubs, "author")
    m, n = export.to_vosviewer(g)
    assert m.splitlines()[0].startswith("id\tlabel\tweight<Documents>")
    assert len(m.splitlines()) == 5 and len(n.strip().splitlines()) == 2


def test_excel_removes_illegal_characters(pubs):
    from bibliotr.normalize import Reference

    # Kontrol karakteri içeren kaynak (openpyxl IllegalCharacterError üretirdi)
    pubs[0].references.append(Reference("Hukuki Haber.\x02 https://example.org (Erişim Tarihi: 28.05.2020).", 99))
    pubs[0].title = "Başlık\x0bdevamı"
    data = export.to_excel(pubs)
    import io

    import pandas as pd
    refs = pd.read_excel(io.BytesIO(data), sheet_name="Kaynakca")
    assert "Hukuki Haber. https://example.org (Erişim Tarihi: 28.05.2020)." in refs["reference"].tolist()
    titles = pd.read_excel(io.BytesIO(data), sheet_name="Yayinlar")["title"].tolist()
    assert "Başlıkdevamı" in titles


def test_normalize_strips_control_characters():
    from bibliotr.normalize import normalize_publication

    p = normalize_publication({"id": 1, "title": "A\x02B", "references": [{"context": "Ref\x1f text", "order": 1}]})
    assert p.title == "AB" and p.references[0].text == "Ref text"


def test_bibtex_escapes_and_project_type(pubs):
    pubs[0].title = "Cost_benefit of R&D: 50% #1 for $5 {x}"
    pubs[0].doi = "10.1234/a_b%c"
    pubs[1].document_type = "PROJECT"
    bib = export.to_bibtex(pubs)
    assert r"title = {Cost\_benefit of R\&D: 50\% \#1 for \$5 \{x\}}" in bib
    assert "doi = {10.1234/a_b%c}" in bib  # DOI olduğu gibi kalır
    assert bib.count("@techreport{") == 1 and bib.count("@article{") == len(pubs) - 1
    ris = export.to_ris(pubs)
    assert ris.count("TY  - RPRT") == 1 and ris.count("TY  - JOUR") == len(pubs) - 1
