from bibliotr import analysis


def test_summary(pubs):
    s = analysis.summary(pubs)
    assert s["Yayın sayısı"] == 3
    assert s["Zaman aralığı"] == "2019-2021"
    assert s["Yazar sayısı"] == 4  # Ali Yıldız iki yazımla ama aynı authorId ile
    assert s["Dergi sayısı"] == 2  # "Bilgi Dünyası" ve "BİLGİ DÜNYASI" birleşir
    assert s["Toplam atıf"] == 15
    assert s["Tek yazarlı yayın oranı (%)"] == 33.3


def test_per_year_fills_gaps(pubs):
    py = analysis.per_year(pubs)
    assert py["year"].tolist() == [2019, 2020, 2021]
    assert py["publications"].tolist() == [1, 0, 2]
    assert py["cumulative"].tolist() == [1, 1, 3]


def test_top_counts_and_metrics(pubs):
    top = analysis.top_counts(pubs, "author", 1)
    assert top.iloc[0].tolist() == ["Ali Yıldız", 2, 15]
    kw = analysis.top_counts(pubs, "keyword", 1)
    assert kw.iloc[0]["Yayın"] == 2  # "Bibliyometri" + "bibliyometri"
    am = analysis.author_metrics(pubs)
    ali = am.iloc[0]
    assert ali["Yayın"] == 2 and ali["İlk yazar"] == 2 and ali["h-indeksi"] == 2
    assert analysis.h_index([10, 8, 5, 4, 3]) == 4
    assert analysis.g_index([10, 8, 5, 4, 3]) == 5


def test_laws(pubs):
    lt, params = analysis.lotka(pubs)
    assert lt["Yayın sayısı"].tolist() == [1, 2]
    assert lt["Yazar sayısı"].tolist() == [3, 1]
    assert "Tahmini üs (n)" in params
    bf = analysis.bradford(pubs)
    assert bf.iloc[0]["Dergi"] == "Bilgi Dünyası" and bf.iloc[0]["Yayın"] == 2


def test_networks(pubs):
    g = analysis.field_network(pubs, "author")
    assert g.number_of_nodes() == 4 and g.number_of_edges() == 2
    kg = analysis.field_network(pubs, "keyword", min_occurrence=2)
    assert list(kg.nodes) == ["Bibliyometri"]
    tbl = analysis.network_table(analysis.field_network(pubs, "institution"))
    assert set(tbl.columns) >= {"Düğüm", "Derece", "Küme"}


def test_trend(pubs):
    tr = analysis.trend_by_period(pubs, "keyword", period=2, top_n=3)
    assert list(tr.columns) == ["2019-2020", "2021-2022"]
    assert tr.loc["Bibliyometri"].tolist() == [1, 1]


def test_overlap_table():
    t = analysis.overlap_table({"a": {"1", "2", "3"}, "b": {"3", "4"}, "c": set()})
    rows = {r["Veri seti"]: r for r in t.to_dict("records")}
    assert rows["a"]["Kayıt"] == 3 and rows["a"]["Yalnızca bu sette"] == 2 and rows["a"]["b"] == 1
    assert rows["b"]["Yalnızca bu sette"] == 1 and rows["b"]["Birleşimdeki pay (%)"] == 50.0
    assert rows["c"]["Kayıt"] == 0 and rows["BİRLEŞİM"]["Kayıt"] == 4
    assert analysis.overlap_table({}).iloc[-1]["Kayıt"] == 0


def test_lotka_bootstrap_accepts_power_law_and_rejects_other():
    import numpy as np

    from bibliotr import analysis

    rng = np.random.default_rng(1)
    support = np.arange(1, 10_001, dtype=float)
    cdf = np.cumsum(support ** -3.0 / (support ** -3.0).sum())
    pl = (np.searchsorted(cdf, rng.random(1500), side="right") + 1).tolist()
    res = analysis.lotka_bootstrap(pl, n_boot=200, seed=3)
    assert res["p"] > 0.1 and abs(res["n"] - 3.0) < 0.2
    assert analysis.lotka_bootstrap(pl, n_boot=200, seed=3) == res      # aynı tohum, aynı sonuç
    flat = [1] * 600 + [2] * 500 + [3] * 400                            # kuvvet yasası değil
    assert analysis.lotka_bootstrap(flat, n_boot=200, seed=3)["p"] < 0.05
