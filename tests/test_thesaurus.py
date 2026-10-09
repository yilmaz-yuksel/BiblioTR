from bibliotr import analysis
from bibliotr import thesaurus as th
from bibliotr.normalize import Author, Publication
from bibliotr.wos import WosOptions, check_wos, parse_wos, to_wos


def _pub(pid, keywords=(), authors=(), tr=None, en=None, year=2020):
    p = Publication(id=pid, title=f"Yayin {pid}", year=year, doi=None, citation_count=1, language="TUR",
                    document_type=None, publication_type="ARTICLE", access_type=None, database=None,
                    journal="Dergi", journal_id="1", issn=None, eissn=None, volume=None, issue=None,
                    start_page=None, end_page=None, abstract=None, url="")
    p.keywords = list(keywords) or list(tr or []) + list(en or [])
    if tr is not None:
        p.keywords_by_language = {"TUR": list(tr), "ENG": list(en or [])}
    p.authors = [a if isinstance(a, Author) else Author(name=a, position=i) for i, a in enumerate(authors)]
    return p


def _au(name, aid=None, orcid=None, inst=None, city=None, country=None, pos=0):
    return Author(name=name, position=pos, author_id=aid, orcid=orcid, institution=inst, city=city, country=country)


def test_normal_key():
    assert th.normal_key("keyword", "Bibliometric Analyses") == th.normal_key("keyword", "bibliometric analysis")
    assert th.normal_key("keyword", "Öğretmenler") == th.normal_key("keyword", "öğretmen")
    assert th.normal_key("institution", "T.C. Hacettepe Univ.") == th.normal_key("institution", "HACETTEPE ÜNİVERSİTESİ")


def test_keyword_suggestions_spelling_and_similarity():
    pubs = [
        _pub("1", ["bibliyometri", "Eğitim"]),
        _pub("2", ["bibliyometri", "eğitim"]),
        _pub("3", ["bibliometri"]),
        _pub("4", ["Bibliometric analyses"]),
        _pub("5", ["bibliometric analysis"]),
        _pub("6", ["bibliometric analysis"]),
    ]
    sugs = th.suggest(pubs, "keyword")
    by_target = {s.target_label: s for s in sugs}
    assert "bibliometric analysis" in by_target
    assert [m[1] for m in by_target["bibliometric analysis"].members] == ["Bibliometric analyses"]
    assert "bibliyometri" in by_target
    assert by_target["bibliyometri"].members[0][1] == "bibliometri"
    assert by_target["bibliyometri"].reason == "Benzer yazılış"
    # Büyük/küçük harf farkı zaten aynı terim sayılır; öneri çıkmaz.
    assert not any("ğitim" in s.target_label for s in sugs)


def test_language_pairs():
    pubs = [_pub(str(i), tr=["bibliyometri", "eğitim"], en=["bibliometrics", "education"]) for i in range(3)]
    sugs = th.suggest(pubs, "keyword")
    pairs = {(s.target_label, s.members[0][1]): s.reason for s in sugs}
    assert pairs[("education", "eğitim")].startswith("Türkçe-İngilizce karşılık (3 yayında")
    # Yazılışı da benzeyen çift bir kez önerilir.
    assert len([k for k in pairs if "bibliometrics" in k]) == 1
    # Tek yayında görülen çift önerilmez.
    assert not [s for s in th.suggest(pubs[:1], "keyword") if s.reason.startswith("Türkçe")]


def test_apply_merges_and_dedupes_within_publication():
    pubs = [_pub("1", tr=["bibliyometri"], en=["bibliometrics"]), _pub("2", ["bibliometri"])]
    t = th.Thesaurus()
    t.add("keyword", "bibliometrics", "bibliyometri")
    t.add("keyword", "Bibliometri", "bibliyometri")
    out = th.apply(pubs, t)
    assert out[0].keywords == ["bibliyometri"]  # iki dil tek terim, bir kez sayılır
    assert out[0].keywords_by_language["ENG"] == ["bibliyometri"]
    assert out[1].keywords == ["bibliyometri"]
    assert pubs[0].keywords == ["bibliyometri", "bibliometrics"]  # girdi değişmez
    top = analysis.top_counts(out, "keyword")
    assert top.iloc[0]["Yayın"] == 2 and len(top) == 1


def test_chain_is_flattened():
    t = th.Thesaurus()
    t.add("keyword", "a1", "b1")
    t.add("keyword", "b1", "c1")
    assert t.resolve("keyword", "a1") == "c1"
    assert t.entries["keyword"]["a1"] == ("a1", "c1")
    t.add("keyword", "c1", "a1")  # döngü oluşturmaz
    assert t.resolve("keyword", "b1") in ("a1", "c1")


def test_institution_suggest_and_apply_keeps_cities_parallel():
    a1 = _au("Ali Yıldız", "1", inst="HACETTEPE ÜNİVERSİTESİ; Hacettepe Univ.", city="ANKARA; ANKARA",
             country="; ")
    a2 = _au("Ayşe Kaya", "2", inst="HACETTEPE ÜNİVERSİTESİ", city="ANKARA")
    a3 = _au("Can Er", "3", inst="GAZİ ÜNİVERSİTESİ", city="ANKARA")
    a4 = _au("Can Er", "4", inst="GAZİOSMANPAŞA ÜNİVERSİTESİ", city="TOKAT")
    pubs = [_pub("1", authors=[a1, a2]), _pub("2", authors=[a2]), _pub("3", authors=[a3, a4])]
    sugs = th.suggest(pubs, "institution")
    assert len(sugs) == 1
    assert sugs[0].target_label == "HACETTEPE ÜNİVERSİTESİ"
    assert sugs[0].members[0][1] == "Hacettepe Univ."
    t = th.Thesaurus()
    th.accept(t, sugs[0])
    out = th.apply(pubs, t)
    a = out[0].authors[0]
    assert a.institution == "HACETTEPE ÜNİVERSİTESİ" and a.city == "ANKARA"
    assert analysis.top_counts(out, "institution").iloc[0]["Yayın"] == 2


def test_reject_is_remembered():
    pubs = [_pub("1", ["bibliyometri"]), _pub("2", ["bibliyometri"]), _pub("3", ["bibliometri"])]
    t = th.Thesaurus()
    sugs = th.suggest(pubs, "keyword", t)
    th.reject(t, sugs[0])
    assert th.suggest(pubs, "keyword", t) == []
    t2 = th.Thesaurus.from_json(t.to_json())
    assert th.suggest(pubs, "keyword", t2) == []


def test_author_suggestions_and_merge():
    pubs = [
        _pub("1", authors=[_au("Ali Yıldız", "10", orcid="0000-0001-2345-6789", inst="ANKARA ÜNİVERSİTESİ")]),
        _pub("2", authors=[_au("Ali YILDIZ", "11", orcid="0000-0001-2345-6789", inst="GAZİ ÜNİVERSİTESİ")]),
        _pub("3", authors=[_au("Ali Yıldız", "10", inst="ANKARA ÜNİVERSİTESİ")]),
        _pub("4", authors=[_au("Veli Can", "20", inst="EGE ÜNİVERSİTESİ")]),
        _pub("5", authors=[_au("Veli Can", "21", inst="EGE ÜNİVERSİTESİ")]),
        _pub("6", authors=[_au("Veli Can", "22", inst="ATATÜRK ÜNİVERSİTESİ")]),  # farklı kişi
    ]
    sugs = th.suggest(pubs, "author")
    reasons = {(s.target, s.members[0][0]): s.reason for s in sugs}
    assert reasons[("id:10", "id:11")] == "Aynı ORCID"
    assert reasons[("id:20", "id:21")].startswith("Aynı ad ve ortak kurum")
    assert not any("id:22" in (s.target, s.members[0][0]) for s in sugs)
    t = th.Thesaurus()
    for s in sugs:
        th.accept(t, s)
    out = th.apply(pubs, t)
    assert len(analysis.author_metrics(out)) == 3
    keys = {analysis.author_key(p.authors[0]) for p in out}
    assert keys == {"id:10", "id:20", "id:22"}
    assert out[1].authors[0].name == "Ali Yıldız"


def test_homonyms_and_wos_disambiguation():
    pubs = [
        _pub("1", authors=[_au("Ahmet Yılmaz", "1", inst="ANKARA ÜNİVERSİTESİ", city="ANKARA")]),
        _pub("2", authors=[_au("Ali Yılmaz", "2", inst="EGE ÜNİVERSİTESİ", city="İZMİR")]),
        _pub("3", authors=[_au("Ali Yılmaz", "3", inst="EGE ÜNİVERSİTESİ", city="İZMİR"),
                           _au("Ahmet Yılmaz", "1", inst="ANKARA ÜNİVERSİTESİ", city="ANKARA", pos=1)]),
    ]
    h = th.homonyms(pubs)
    assert set(h["Kısa ad"]) == {"YILMAZ, A"} and len(h) == 3
    recs = parse_wos(to_wos(pubs))
    au = [r["AU"] for r in recs]
    assert au[0] == ["Yilmaz, Ahmet"]
    assert au[1][0].startswith("Yilmaz, Ali") and au[2][0].startswith("Yilmaz, Ali")
    assert au[1] != au[2]  # aynı tam ada sahip iki farklı kimlik
    assert au[2][1] == "Yilmaz, Ahmet"
    assert recs[0]["RP"][0].startswith("Yilmaz, Ahmet (corresponding author)")
    # Eşsiz kısa adlar değişmez; seçenek kapatılabilir.
    plain = parse_wos(to_wos(pubs, WosOptions(disambiguate_authors=False)))
    assert plain[0]["AU"] == ["Yilmaz, A"]
    assert "RP" not in check_wos(to_wos(pubs))["missing_mandatory"]


def test_tool_files():
    t = th.Thesaurus()
    t.add("keyword", "Bibliometri", "bibliyometri")
    t.add("keyword", "bibliometrics", "bibliyometri")
    t.add("keyword", "Eğitimler", "eğitim")
    vos = th.vosviewer_thesaurus(t, "keyword").splitlines()
    assert vos[0] == "label\treplace by"
    assert "bibliometri\tbibliyometri" in vos and "egitimler\tegitim" in vos and "eğitimler\teğitim" in vos
    syn = th.bibliometrix_synonyms(t, "keyword").splitlines()
    assert "BIBLIYOMETRI;BIBLIOMETRI;BIBLIOMETRICS" in syn and "EGITIM;EGITIMLER" in syn


def test_json_roundtrip():
    t = th.Thesaurus()
    t.add("keyword", "x kelime", "y kelime")
    t.add("author", "id:2", "id:1", name="Ali Yıldız")
    t.keep_separate("institution", "Gazi Üniv", "Gaziosmanpaşa Üniv")
    t2 = th.Thesaurus.from_json(t.to_json())
    assert t2.to_dict() == t.to_dict()
    assert len(t2.frame()) == 2


def _rule_pubs():
    # Kurgusal yazarlar: 10/11 aynı ad + ortak kurum (bölünmüş profil), 20/21 aynı ad ama farklı ORCID,
    # 30/31 aynı ad, kurum ortak değil, 40/41 farklı ad ama aynı ORCID.
    return [
        _pub("1", authors=[_au("Deniz Kaya", "10", inst="Ankara Üniversitesi"),
                           _au("Ece Arslan", "20", orcid="0000-0000-0000-0020", inst="Gazi Üniversitesi", pos=1)]),
        _pub("2", authors=[_au("Deniz Kaya", "10", inst="Ankara Üniversitesi")]),
        _pub("3", authors=[_au("DENİZ KAYA", "11", inst="Ankara Üniversitesi; Hacettepe Üniversitesi")]),
        _pub("4", authors=[_au("Ece Arslan", "21", orcid="0000-0000-0000-0021", inst="Gazi Üniversitesi")]),
        _pub("5", authors=[_au("Can Öz", "30", inst="Ege Üniversitesi")]),
        _pub("6", authors=[_au("Can Öz", "31", inst="Akdeniz Üniversitesi")]),
        _pub("7", authors=[_au("Su Ak", "40", orcid="0000-0000-0000-0040")]),
        _pub("8", authors=[_au("Su Akın", "41", orcid="0000-0000-0000-0040")]),
    ]


def test_author_rule_pairs_and_clusters():
    pubs = _rule_pubs()
    assert th.author_rule_pairs(pubs, "none") == []
    assert th.author_rule_pairs(pubs, "orcid") == [("id:40", "id:41", "orcid")]
    pairs = th.author_rule_pairs(pubs, "orcid+institution")
    assert pairs == [("id:10", "id:11", "institution"), ("id:40", "id:41", "orcid")]  # 20/21 ORCID çelişkisi, 30/31 kurum yok
    clusters = th.author_clusters(pubs, "orcid+institution")
    assert clusters == {"id:10": "id:10", "id:11": "id:10", "id:40": "id:40", "id:41": "id:40"}
    sep = th.Thesaurus()
    sep.keep_separate("author", "id:10", "id:11")
    assert "id:11" not in th.author_clusters(pubs, "orcid+institution", sep)
    import pytest

    with pytest.raises(ValueError):
        th.author_rule_pairs(pubs, "isim")


def test_apply_author_rule_counts_and_keeps_input():
    pubs = _rule_pubs()
    base = th.Thesaurus()
    new, merged = th.apply_author_rule(pubs, "orcid+institution", base)
    assert merged == 2 and len(base) == 0 and len(new) == 2
    after = th.apply(pubs, new)
    assert analysis.summary(pubs)["Yazar sayısı"] == 8 and analysis.summary(after)["Yazar sayısı"] == 6
    counts = dict(zip(*[analysis.top_counts(after, "author", 10)[c] for c in ("Yazar", "Yayın")]))
    assert counts["Deniz Kaya"] == 3 and counts["Su Ak"] == 2
    assert th.apply_author_rule(pubs, "none", base)[1] == 0
    # WoS çıktısında birleşen kimlik tek yazımla geçer.
    recs = parse_wos(to_wos(after))
    assert recs[2]["AU"] == recs[0]["AU"][:1]


def test_language_pairs_ignore_list_order():
    # İngilizce liste farklı sırada (ör. MeSH sırası): sıra eşleşmesi yok, birliktelik var.
    # Sıra eşleşmesi tutarlı biçimde yanlış çifti ("obezite" / "Child") gösterir; birliktelik doğruyu gösterir.
    pubs = [_pub(str(i), tr=["obezite", "çocuk"], en=["Child", "Obesity"]) for i in range(3)]
    pubs += [_pub("o", tr=["obezite"], en=["Obesity"]), _pub("c", tr=["çocuk", "spor"], en=["Child", "sport"]),
             _pub("s", tr=["spor"], en=["sport"])]
    pairs = {(s.target_label, s.members[0][1]) for s in th.suggest(pubs, "keyword") if s.reason.startswith("Türkçe")}
    assert {tuple(sorted(p)) for p in pairs} == {("Obesity", "obezite"), ("Child", "çocuk")}
    # Bir terimin iki eşi eşit sıklıktaysa karşılık belirsizdir; önerilmez.
    amb = [_pub("a", tr=["denge"], en=["balance", "posture"]), _pub("b", tr=["denge"], en=["balance", "posture"])]
    assert not [s for s in th.suggest(amb, "keyword") if s.reason.startswith("Türkçe")]
