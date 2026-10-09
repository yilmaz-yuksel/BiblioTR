from bibliotr import cli, subjects


def test_tree_matches_trdizin_structure():
    fen, sosyal = subjects.SUBJECT_TREE
    assert (fen["name"], sosyal["name"]) == ("Fen", "Sosyal")
    assert fen["children"][0] == {"name": "Multidisipliner – Fen", "value": "Fen > Multidisipliner – Fen",
                                  "children": []}
    muh = next(c for c in fen["children"] if c["name"] == "Mühendislik")
    assert muh["value"] == "Muhendislik" and len(muh["children"]) == 46
    assert sosyal["children"][1]["value"] == "Sosyal > Sosyal > Aile Çalışmaları"
    assert len(subjects.all_subjects()) == 199


def test_known_values_from_real_data():
    for v in ("Fen > Tıp > Spor Bilimleri", "Sosyal > Sosyal > Otelcilik, Konaklama, Spor ve Turizm",
              "Fen > Mühendislik > Malzeme Bilimleri, Kâğıt ve Ahşap", "Fen > Diş Hekimliği > Diş Hekimliği"):
        assert subjects.is_known(v)


def test_resolve():
    assert subjects.resolve("spor bilimleri") == ["Fen > Tıp > Spor Bilimleri"]
    assert len(subjects.resolve("TIP")) == 51
    assert len(subjects.resolve("Fen > Mühendislik")) == 46
    assert len(subjects.resolve("sosyal")) == 52
    assert subjects.resolve("olmayan konu") == []
    assert subjects.suggest("Fen > Tip > Spor Bilimler")[0] == "Fen > Tıp > Spor Bilimleri"


def test_labels():
    assert subjects.label("Sosyal > Sosyal > Psikoloji") == "Sosyal › Psikoloji"
    assert subjects.label("Fen > Tıp > Spor Bilimleri") == "Fen › Tıp › Spor Bilimleri"


def test_cli_expands_subject_facets(capsys):
    facets = cli.parse_facets(["subject=Eczacılık", "database=SCIENCE"])
    assert facets == [("facet-subject", "Fen > Eczacılık > Farmakoloji ve Eczacılık"),
                      ("facet-subject", "Fen > Eczacılık > Toksikoloji"), ("facet-database", "SCIENCE")]
    assert cli.parse_facets(["subject=Yanlis Konu"]) == [("facet-subject", "Yanlis Konu")]
    assert "bilinen bir TR Dizin konusu değil" in capsys.readouterr().err
    cli.main(["subjects", "Veterinerlik"])
    assert "Fen > Veterinerlik > Veterinerlik" in capsys.readouterr().out
