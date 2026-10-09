"""TR Dizin bibliyometri arayüzü (Streamlit).

Çalıştırma:  streamlit run app.py
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import networkx as nx
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bibliotr import __version__, analysis, export, subjects
from bibliotr import query as tq
from bibliotr import thesaurus as th
from bibliotr.client import AGGREGATION_LABELS, FACETS, MAX_RESULT_WINDOW, ORDERS, TRDizinClient, TRDizinError
from bibliotr.journals import years_frame
from bibliotr.normalize import key_tree, normalize_publication
from bibliotr.storage import Store

DB_PATH = "data/trdizin.db"

# Kategorik renk sırası (sabit sırayla atanır; 8'den fazla küme gri "Diğer"e düşer).
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#4a3aa7", "#e34948"]
OTHER = "#9a9a96"
PRIMARY = SERIES[0]

FACET_LABELS = {
    "facet-documentType": "Doküman tipi",
    "facet-database": "Veritabanı",
    "facet-accessType": "Erişim tipi",
    "facet-authorName": "Yazar adı",
    "facet-publication_year": "Yayın yılı",
    "facet-publicationLanguage": "Yayın dili",
    "facet-journalName": "Dergi adı",
    "facet-publicationType": "Yayın türü",
    "facet-projectGroup": "Proje grubu",
    "facet-facetAuthorInstitution": "Yazar kurumu",
    "facet-facetAuthorCountry": "Yazar ülkesi",
    "facet-facetAuthorCity": "Yazar şehri",
    "facet-subject": "Konu (ör. Fen > Tıp > Onkoloji)",
}

ASSETS = Path(__file__).resolve().parent / "assets"
ICON = ASSETS / "bibliotr-icon-tile-512.png"
REPO = "https://github.com/yilmaz-yuksel/BiblioTR"
DEVELOPER = "Yılmaz YÜKSEL"
ABOUT = (f"**BiblioTR {__version__}**: TR Dizin için bibliyometrik veri toplama ve analiz aracı.\n\n"
         f"Geliştirici: {DEVELOPER}, Karabük Üniversitesi\n\n"
         f"Kaynak kod ve atıf bilgisi: {REPO} · MIT lisansı\n\n"
         f"Soru ve hata bildirimi: {REPO}/issues")
st.set_page_config(page_title="BiblioTR", page_icon=str(ICON) if ICON.exists() else "📚", layout="wide",
                   menu_items={"About": ABOUT, "Get help": REPO, "Report a bug": f"{REPO}/issues"})


def sidebar_logo() -> None:
    """Kenar çubuğunun başında logo; koyu temada açık renkli sürüm. Dosya yoksa düz başlık."""
    dark = getattr(getattr(st.context, "theme", None), "type", None) == "dark"
    logo = ASSETS / ("bibliotr-logo-dark.png" if dark else "bibliotr-logo-transparent.png")
    if logo.exists():
        st.sidebar.image(str(logo), width="stretch")
    else:
        st.sidebar.title("BiblioTR")


@st.cache_resource
def get_store() -> Store:
    return Store(DB_PATH)


# `version` (Store.data_version) önbellek anahtarının parçasıdır: veri seti ya da OpenAlex eşleşmeleri
# değişince (uygulama içinden ya da komut satırından) önbellek kendiliğinden yenilenir.
@st.cache_data(show_spinner=False, max_entries=8)
def load_publications(dataset: str, version: str):
    return get_store().publications(dataset)


def clear_data_cache() -> None:
    load_publications.clear()
    load_applied.clear()
    load_suggestions.clear()


@st.cache_data(show_spinner=False, max_entries=8)
def load_applied(dataset: str, version: str, thes_version: str):
    """Eşanlamlılar sözlüğü uygulanmış yayınlar (sözlük kaydedildikçe yenilenir)."""
    return th.apply(load_publications(dataset, version), get_store().load_thesaurus())


@st.cache_data(show_spinner="Öneriler hesaplanıyor...", max_entries=16)
def load_suggestions(dataset: str, version: str, thes_version: str, field: str, threshold: float,
                     min_pairs: int):
    return th.suggest(load_publications(dataset, version), field, get_store().load_thesaurus(), threshold,
                      min_pairs)


def bar_chart(df: pd.DataFrame, label_col: str, value_col: str, title: str) -> go.Figure:
    d = df.iloc[::-1]
    fig = go.Figure(go.Bar(
        x=d[value_col], y=d[label_col].astype(str).str.slice(0, 60), orientation="h",
        marker=dict(color=PRIMARY, cornerradius=4), hovertemplate="%{y}<br>%{x}<extra></extra>",
    ))
    fig.update_layout(
        title=title, height=90 + 30 * len(d), margin=dict(l=10, r=10, t=40, b=10),
        bargap=0.3, xaxis=dict(showgrid=True, gridcolor="rgba(128,128,128,0.15)"),
    )
    return fig


def network_figure(g: nx.Graph, title: str) -> go.Figure:
    if g.number_of_nodes() == 0:
        return go.Figure()
    clusters = analysis.detect_communities(g)
    pos = nx.spring_layout(g, weight="weight", seed=42, k=1.5 / math.sqrt(g.number_of_nodes()))
    ex, ey = [], []
    for a, b in g.edges():
        ex += [pos[a][0], pos[b][0], None]
        ey += [pos[a][1], pos[b][1], None]
    fig = go.Figure(go.Scatter(x=ex, y=ey, mode="lines", line=dict(width=0.6, color="rgba(128,128,128,0.35)"),
                               hoverinfo="skip", showlegend=False))
    max_count = max((d.get("count", 1) for _, d in g.nodes(data=True)), default=1)
    by_cluster: dict[int, list] = {}
    for n in g.nodes:
        c = clusters.get(n, 0)
        by_cluster.setdefault(c if c <= len(SERIES) else 0, []).append(n)
    for c in sorted(by_cluster, key=lambda x: (x == 0, x)):
        nodes = by_cluster[c]
        counts = [g.nodes[n].get("count", 1) for n in nodes]
        top = set(sorted(nodes, key=lambda n: g.nodes[n].get("count", 1), reverse=True)[:8])
        fig.add_trace(go.Scatter(
            x=[pos[n][0] for n in nodes], y=[pos[n][1] for n in nodes], mode="markers+text",
            name=f"Küme {c}" if c else "Diğer kümeler",
            text=[str(n)[:30] if n in top else "" for n in nodes], textposition="top center",
            textfont=dict(size=11),
            customdata=[[str(n), g.nodes[n].get("count", 0), g.degree(n, weight="weight")] for n in nodes],
            hovertemplate="%{customdata[0]}<br>Belge: %{customdata[1]}<br>Bağlantı gücü: %{customdata[2]}"
                          "<extra></extra>",
            marker=dict(size=[8 + 30 * math.sqrt(x / max_count) for x in counts],
                        color=SERIES[c - 1] if c else OTHER, line=dict(width=2, color="rgba(255,255,255,0.9)")),
        ))
    fig.update_layout(
        title=title, height=650, margin=dict(l=10, r=10, t=40, b=10),
        xaxis=dict(visible=False), yaxis=dict(visible=False), legend=dict(orientation="h"),
    )
    return fig


# ---- kenar çubuğu ------------------------------------------------------------

store = get_store()
sidebar_logo()
datasets = store.list_datasets()
names = [d["name"] for d in datasets]
selected = st.sidebar.selectbox("Veri seti", names, index=0 if names else None,
                                placeholder="Önce veri toplayın")
if selected:
    ds_info = next(d for d in datasets if d["name"] == selected)
    data_version = store.data_version(selected)
    st.sidebar.caption(f"{ds_info['n']} yayın · sorgu: “{ds_info['query'] or '-'}” · {ds_info['updated_at'][:16]}")
thes_count = len(store.load_thesaurus())
use_thes = st.sidebar.checkbox(
    f"Eşanlamlılar sözlüğünü uygula ({thes_count} girdi)", value=True,
    help="Onayladığınız yazar, kurum ve anahtar kelime birleştirmeleri tüm analizlere ve dışa aktarımlara "
         "uygulanır. Ham veri değişmez. Sözlüğü **Eşanlamlılar** sekmesinden yönetin.")
min_interval = st.sidebar.number_input("İstekler arası bekleme (sn)", 0.2, 10.0, 1.0, 0.1,
                                       help="TR Dizin sunucusunu yormamak için")
st.sidebar.divider()
st.sidebar.caption(f"BiblioTR {__version__} · [GitHub]({REPO})  \n"
                   f"Geliştirici: {DEVELOPER}  \n[Soru ve hata bildirimi]({REPO}/issues)")

(tab_collect, tab_overview, tab_actors, tab_cites, tab_laws, tab_network, tab_trend, tab_thes, tab_export,
 tab_raw) = st.tabs(
    ["🔎 Veri Topla", "📈 Genel Bakış", "👥 Yazar / Dergi / Kurum", "📚 Kaynakça ve Atıf", "📐 Yasalar",
     "🕸️ Ağlar", "🧭 Tematik Evrim", "🔗 Eşanlamlılar", "💾 Dışa Aktar", "🧪 Ham Veri"]
)

# ---- veri toplama ------------------------------------------------------------

with tab_collect:
    st.subheader("Sorgu oluştur")
    c1, c2 = st.columns([3, 1])
    search_mode = c1.segmented_control(
        "Arama türü", ["Basit", "Gelişmiş"], default="Basit", key="search_mode",
        help="Basit: TR Dizin ana araması (gevşek eşleşme). Gelişmiş: alan seçerek kesin eşleşme "
             "(tümü / herhangi biri / aynen / içermeyen).") or "Basit"
    order = c2.selectbox("Sıralama", ORDERS["publication"], index=1)
    # Gönderilecek sorgular: [(etiket, sorgu)]. Birden çok sorgu varsa sonuçlar kimliğe göre birleştirilir.
    queries: list[tuple[str, str]] = []
    name_hint = ""
    if search_mode == "Basit":
        typed = st.text_input("Arama ifadesi (q, isteğe bağlı)", placeholder="ör. bibliometri, yapay zeka, cancer",
                              help="Boş bırakılırsa yalnızca filtrelerle (ör. konu) arama yapılır. "
                                   "Hazır bir alan sorgusu da yapıştırılabilir: (title : ( \"beden eğitimi\" ))")
        for level, msg in tq.query_notes(typed):
            {"error": st.error, "warning": st.warning}.get(level, st.caption)(msg)
        queries = [("", tq.normalize_query(typed))]
        name_hint = "" if tq.is_field_query(typed) else typed.strip()
    else:
        a1, a2 = st.columns([2, 1])
        adv_fields = a1.pills("Arama alanı", list(tq.SEARCH_FIELDS), selection_mode="multi", default=["title"],
                              format_func=tq.SEARCH_FIELDS.get, key="adv_fields")
        adv_join = "or"
        if len(adv_fields) > 1:
            adv_join = a2.segmented_control(
                "Alanlar", ["or", "and"], default="or", key="adv_join",
                format_func={"or": "Herhangi birinde", "and": "Hepsinde"}.get,
                help="Herhangi birinde: her alan ayrı sorgulanır, sonuçlar kimliğe göre birleştirilir "
                     "(TR Dizin alanlar arası VEYA'yı desteklemez). Hepsinde: koşul seçilen her alanda "
                     "sağlanmalı (tek sorgu).") or "or"
        b1, b2 = st.columns(2)
        terms = tq.FieldTerms(
            all_words=b1.text_input("Kelimelerin tümünü içeren", key="adv_all", placeholder="ör. beden eğitimi"),
            any_words=b2.text_input("Kelimelerin herhangi birini içeren", key="adv_any",
                                    placeholder="ör. eğitimi eğitiminde eğitiminin"),
            phrase=b1.text_input("Kelime grubunu aynen içeren", key="adv_phrase", placeholder="ör. beden eğitimi"),
            exclude=b2.text_input("Kelime gruplarını içermeyen", key="adv_exclude", placeholder="ör. tarihsel gelişimi",
                                  help="Birden çok grup için `;` ile ayırın."),
        )
        queries = tq.field_queries(adv_fields, terms, adv_join)
        name_hint = terms.label()
        if queries:
            st.code("\n".join(q for _, q in queries), language=None, wrap_lines=True)
            if len(queries) > 1:
                st.caption(f"{len(queries)} ayrı sorgu gönderilir; aynı yayın yalnızca bir kez kaydedilir. "
                           "Eşleşme tam sözcükledir: ekli biçimleri (eğitiminde, eğitiminin...) \"herhangi "
                           "birini içeren\" kutusuna ayrıca yazın.")
            else:
                st.caption("Eşleşme tam sözcükledir: ekli biçimleri (eğitiminde, eğitiminin...) \"herhangi birini "
                           "içeren\" kutusuna ayrıca yazın. Sorguyu elle değiştirmek için kopyalayıp *Basit* "
                           "arama türüne yapıştırın.")
        elif terms.exclude.strip():
            st.warning("Yalnızca \"içermeyen\" koşulu ile arama yapılamaz; en az bir olumlu koşul girin.")
        elif not adv_fields:
            st.caption("En az bir arama alanı seçin.")
    queries = [(lab, q) for lab, q in queries if q] or [("", "")]
    query = queries[0][1]
    multi_query = len(queries) > 1

    st.markdown("**Filtreler (facet)**. Aynı filtrede birden çok değer seçmek çoklu seçim (VEYA) anlamına gelir.")
    facets: list[tuple[str, str]] = []
    cols = st.columns(3)
    grid_facets = [(k, c) for k, c in FACETS["publication"].items()
                   if k not in ("facet-publication_year", "facet-subject")]
    for i, (key, choices) in enumerate(grid_facets):
        col = cols[i % 3]
        label = FACET_LABELS.get(key, key)
        if choices:
            vals = col.multiselect(label, choices, key=key)
        else:
            txt = col.text_input(label, key=key, help="Birden çok değer için `;` ile ayırın")
            vals = [v.strip() for v in txt.split(";") if v.strip()]
        facets += [(key, v) for v in vals]

    st.markdown("**Konu** (TR Dizin konu dizini)")
    k1, k2 = st.columns([1, 2])
    subject_groups = subjects.groups()
    group_sel = k1.multiselect(
        "Grup olarak seç", list(subject_groups), key="subject_groups",
        format_func=lambda g: f"{g.replace(' > ', ' › ')} ({len(subject_groups[g])} konu)",
        help="Seçilen grubun (ör. Fen › Tıp) tüm alt konuları aramaya eklenir.",
    )
    subject_sel = k2.multiselect(
        "Konu seç", subjects.all_subjects(), key="subject_items", format_func=subjects.label,
        placeholder="Aramak için yazın, ör. spor, psikoloji, yapay zeka",
    )
    chosen_subjects = list(dict.fromkeys([v for g in group_sel for v in subject_groups[g]] + subject_sel))
    if chosen_subjects:
        st.caption(f"{len(chosen_subjects)} konu seçili (herhangi birine giren yayınlar aranır).")
    facets += [("facet-subject", v) for v in chosen_subjects]

    st.markdown("**Dergi** (isteğe bağlı)")
    j1, j2, j3 = st.columns([1, 1, 2], vertical_alignment="bottom")
    journal_id = j1.text_input("Dergi ID", key="journal_id", placeholder="ör. 488",
                               help="TR Dizin dergi sayfasının adresindeki numara: .../tr/dergi/detay/488")
    if j2.button("Dergiyi getir", disabled=not journal_id.strip()):
        try:
            found = TRDizinClient(min_interval=min_interval).journal_by_id(journal_id.strip())
            if found is None:
                st.error(f"{journal_id} kimlikli dergi bulunamadı.")
            st.session_state["journal"] = found
        except TRDizinError as e:
            st.error(f"API hatası: {e}")
    journal = st.session_state.get("journal")
    if journal and str(journal.id) != journal_id.strip():
        journal = None  # kimlik değişti veya silindi
    only_indexed = False
    if journal:
        only_indexed = j3.checkbox("Yalnızca TR Dizin'de dizinlendiği yıllar", value=True,
                                   help="Yıl aralığı yerine derginin dizinlendiği yıllar taranır.")
        with st.expander(f"📰 {journal.title} (id {journal.id})", expanded=True):
            journal_info = {k: v for k, v in journal.summary().items() if v not in (None, "")}
            c_a, c_b = st.columns([1, 1])
            c_a.dataframe(pd.DataFrame(list(journal_info.items()), columns=["Bilgi", "Değer"]).astype(str),
                          hide_index=True, width="stretch",
                          column_config={"Değer": st.column_config.TextColumn(width="large")})
            c_b.dataframe(years_frame(journal), hide_index=True, width="stretch", height=300)
            st.caption("Konular: " + "; ".join(subjects.label(x) for x in journal.subjects))
            st.caption("Yayınlar dergi adıyla aranır ve dergi kimliğiyle süzülür; aynı adlı başka dergilerin "
                       "yayınları çekilen veriye karışmaz (önizleme ve profil sayıları ada göredir).")
        facets += TRDizinClient.journal_facets(journal)

    y1, y2, y3 = st.columns([1, 1, 2])
    use_years = y3.checkbox("Yıl aralığı uygula (yıl yıl bölerek çeker)", value=True, disabled=only_indexed,
                            help="Büyük sonuç kümelerinde derin sayfalama sınırını aşmak için sorguyu yıllara böler.")
    year_from = y1.number_input("Başlangıç yılı", 1950, 2100, 2015, disabled=not use_years or only_indexed)
    year_to = y2.number_input("Bitiş yılı", 1950, 2100, 2025, disabled=not use_years or only_indexed)

    if only_indexed:
        selected_years = journal.indexed_years
    elif use_years:
        selected_years = list(range(int(year_from), int(year_to) + 1))
    else:
        selected_years = None
    year_facets = [("facet-publication_year", str(y)) for y in selected_years or []]
    can_search = bool(query.strip() or facets)
    if use_years and not only_indexed and year_from > year_to:
        st.error(f"Başlangıç yılı ({int(year_from)}) bitiş yılından ({int(year_to)}) büyük olamaz.")
        can_search = False
    elif not can_search:
        st.caption("Aramak için bir ifade girin veya en az bir filtre seçin.")

    if st.button("Önizle ve profil çıkar", disabled=not can_search,
                 help="Tek istekle ilk 20 kaydı ve TÜM sonuç kümesinin dağılımlarını getirir."):
        try:
            client = TRDizinClient(min_interval=min_interval)
            pages = [(lab, q, client.search("publication", q, order, 1, 20, facets + year_facets))
                     for lab, q in queries]
            sp = pages[0][2]
            st.session_state["preview"] = {
                "total": sp.total, "records": sp.records, "aggregations": sp.aggregations,
                "other": sp.aggregation_other, "query": pages[0][1], "facets": facets + year_facets,
                "parts": [(lab, q, p.total) for lab, q, p in pages],
            }
        except TRDizinError as e:
            st.error(f"API hatası: {e}")

    preview = st.session_state.get("preview")
    if preview:
        parts = preview.get("parts") or []
        if len(parts) > 1:
            totals = [t for _, _, t in parts]
            st.success(("  ·  ".join(f"{lab}: **{t:,}**" for lab, _, t in parts)
                        + f"  ·  birleşim {max(totals):,} ile {sum(totals):,} arasında").replace(",", "."))
            st.caption("Sorgular ayrı gönderildiği için birleşimin kesin sayısı veriyi çekince belli olur. "
                       f"Aşağıdaki kayıtlar ve profil yalnızca \"{parts[0][0]}\" sorgusuna aittir.")
        else:
            st.success(f"Toplam **{preview['total']:,}** yayın bulundu.".replace(",", "."))
        if max([preview["total"]] + [t for _, _, t in parts]) > MAX_RESULT_WINDOW and not selected_years:
            st.warning(f"Arama motoru en fazla {MAX_RESULT_WINDOW:,} kayda sayfalama izni verir. Tümünü "
                       "çekmek için yıl aralığını açın (sorgu yıl yıl bölünür).".replace(",", "."))
        pv_tab, prof_tab = st.tabs(["İlk 20 kayıt", "Hızlı profil (tüm sonuç kümesi)"])
        with pv_tab:
            pv = [normalize_publication(r) for r in preview["records"]]
            st.dataframe(analysis.publications_frame(pv)[["year", "title", "authors", "journal", "citation_count"]],
                         width="stretch", hide_index=True)
        with prof_tab:
            st.caption("Bu dağılımlar TR Dizin'in kendi sayımlarıdır ve veri çekmeden tüm sonuç kümesini kapsar. "
                       "Bir yayın birden çok kurum/şehir/konuya sayılabilir.")
            aggs = {k: v for k, v in preview["aggregations"].items() if v}
            order_keys = [k for k in AGGREGATION_LABELS if k in aggs] + [k for k in aggs if k not in AGGREGATION_LABELS]
            year_key = "facet-publication_year"
            if year_key in aggs:
                yd = pd.DataFrame(aggs[year_key], columns=["Yıl", "Yayın"])
                yd = yd[yd["Yıl"].str.isdigit()].astype({"Yıl": int}).sort_values("Yıl")
                fig = go.Figure(go.Bar(x=yd["Yıl"], y=yd["Yayın"], marker=dict(color=PRIMARY, cornerradius=4),
                                       hovertemplate="%{x}: %{y} yayın<extra></extra>"))
                fig.update_layout(title="Yıllara göre yayın (tüm sonuç kümesi)", height=320,
                                  margin=dict(t=40, b=10), xaxis=dict(tickformat="d"))
                st.plotly_chart(fig, width="stretch")
            cols = st.columns(2)
            i = 0
            for key in order_keys:
                if key == year_key or len(aggs[key]) < 2:
                    continue
                label = AGGREGATION_LABELS.get(key, key)
                df = pd.DataFrame(aggs[key][:15], columns=[label, "Yayın"])
                fig = bar_chart(df, label, "Yayın", f"{label} (ilk 15 / {len(aggs[key])})")
                cols[i % 2].plotly_chart(fig, width="stretch")
                if preview["other"].get(key):
                    cols[i % 2].caption(f"Listede olmayan {preview['other'][key]} belge var (liste kesilmiş).")
                i += 1
            meta = {"Sorgu": preview["query"] or "-", "Toplam yayın": str(preview["total"]),
                    "Facet'ler": "; ".join(f"{k}={v}" for k, v in preview["facets"]) or "-"}
            st.download_button("Tüm dağılımları Excel olarak indir",
                               export.aggregations_to_excel(aggs, AGGREGATION_LABELS, meta), "tr_dizin_profil.xlsx")

    st.divider()
    st.subheader("Tümünü çek ve kaydet")
    d1, d2 = st.columns([2, 1])
    default_name = (name_hint
                    or (journal.title if journal else "")
                    or (group_sel[0].split(" > ")[-1] if group_sel else "")
                    or (subjects.leaf_name(chosen_subjects[0]) if chosen_subjects else ""))
    ds_name = d1.text_input("Veri seti adı", value=default_name[:40])
    max_records = d2.number_input("En fazla kayıt (0 = sınırsız)", 0, 1_000_000, 0, step=100)
    fetch_details = st.checkbox(
        "Atıf yapan yayınları da çek", value=True,
        help="Kaynakça arama yanıtında zaten gelir. Bu yayına atıf yapan TR Dizin yayınları ise gelmez; "
             "bunun için yalnızca atıf almış yayınlara ayrıca /api/publicationById isteği atılır "
             "(her biri ~1 sn). Veri seti içi atıf ağı ve yerel atıf için gereklidir.",
    )
    if st.button("Veriyi çek", type="primary", disabled=not (can_search and ds_name)):
        client = TRDizinClient(min_interval=min_interval)
        years = selected_years
        status = st.status("Veri çekiliyor...", expanded=True)
        fetched = {"n": 0}

        def on_page(sp):
            fetched["n"] += len(sp.records)
            status.write(f"Sayfa {sp.page}: {len(sp.records)} kayıt (bu dilimde toplam {sp.total}) · "
                         f"şimdiye kadar {fetched['n']}")

        try:
            by_id: dict[str, dict] = {}
            found_in: dict[str, set[str]] = {}
            for lab, q in queries:
                if multi_query:
                    status.write(f"**{lab}**: `{q}`")
                if journal:
                    part = client.harvest_journal(journal, q, facets, years=years, indexed_years_only=False,
                                                  order=order, max_records=max_records or None, on_page=on_page)
                else:
                    part = client.harvest(q, "publication", order, facets, years=years,
                                          max_records=max_records or None, on_page=on_page)
                found_in[lab] = {str(r.get("id")) for r in part}
                for r in part:
                    by_id.setdefault(str(r.get("id")), r)
            records = list(by_id.values())
            if fetch_details:
                bar = status.progress(0.0, text="Ayrıntılar çekiliyor...")
                client.enrich(records, on_progress=lambda i, t: bar.progress(
                    i / t, text=f"Ayrıntılar: {i}/{t} (kalan ~{(t - i) * min_interval / 60:.0f} dk)"))
            params = {"facets": facets, "years": years, "order": order}
            if multi_query:
                params["queries"] = [{"label": lab, "query": q, "records": len(found_in[lab])} for lab, q in queries]
            if journal:
                params["journal"] = {"id": journal.id, "title": journal.title}
            shown_query = " ∪ ".join(q for _, q in queries) if multi_query else query
            ds_id = store.create_dataset(ds_name, shown_query or (journal.title if journal else None), params)
            n = store.add_records(ds_id, records)
            status.update(label=f"{n} yayın '{ds_name}' veri setine kaydedildi.", state="complete")
            if multi_query:
                table = analysis.overlap_table(found_in)
                counts = [c for c in table.columns if c not in ("Veri seti", "Birleşimdeki pay (%)")]
                table[counts] = table[counts].astype("Int64")  # BİRLEŞİM satırındaki boşluklar sayıları bozmasın
                st.session_state["harvest_overlap"] = {"dataset": ds_name, "table": table}
            else:
                st.session_state.pop("harvest_overlap", None)
            clear_data_cache()
            st.rerun()
        except TRDizinError as e:
            status.update(label="Hata", state="error")
            st.error(str(e))

    overlap = st.session_state.get("harvest_overlap")
    if overlap:
        st.markdown(f"**Son çekim: '{overlap['dataset']}' veri setinde alanların örtüşmesi**")
        st.dataframe(overlap["table"], hide_index=True, width="stretch")
        st.caption("Her satır bir alan sorgusudur: getirdiği kayıt, yalnızca o alanda bulunan kayıt ve diğer "
                   "alanlarla ortak kayıt sayıları. Son satır kaydedilen birleşimdir.")

    if datasets:
        st.divider()
        st.subheader("Kayıtlı veri setleri")
        st.dataframe(pd.DataFrame([{"Ad": d["name"], "Yayın": d["n"], "Sorgu": d["query"],
                                    "Parametreler": json.dumps(d["params"], ensure_ascii=False),
                                    "Güncelleme": d["updated_at"]} for d in datasets]),
                     width="stretch", hide_index=True)
        with st.expander("Eksik kaynakça/atıf ayrıntılarını tamamla"):
            to_enrich = st.selectbox("Veri seti", names, key="enrich")
            if st.button("Tamamla", key="enrich_btn"):
                client = TRDizinClient(min_interval=min_interval)
                recs = store.raw_records(to_enrich)
                bar = st.progress(0.0)
                n_done = client.enrich(recs, on_progress=lambda i, t: bar.progress(i / t, text=f"{i}/{t}"))
                store.add_records(to_enrich, recs)
                clear_data_cache()
                st.success(f"{n_done} yayın güncellendi.")
        with st.expander("Veri setini sil"):
            to_del = st.selectbox("Silinecek veri seti", names, key="del")
            if st.button("Sil", key="del_btn"):
                store.delete_dataset(to_del)
                clear_data_cache()
                st.rerun()

# ---- analiz sekmeleri ----------------------------------------------------------

raw_pubs = load_publications(selected, data_version) if selected else []
pubs = (load_applied(selected, data_version, store.thesaurus_version())
        if selected and use_thes and thes_count else raw_pubs)


def need_data() -> bool:
    if not pubs:
        st.info("Analiz için önce **Veri Topla** sekmesinden bir veri seti oluşturun ve kenar çubuğundan seçin.")
        return True
    return False


with tab_overview:
    if not need_data():
        s = analysis.summary(pubs)
        items = list(s.items())
        for row in range(0, len(items), 4):
            cols = st.columns(4)
            for col, (k, v) in zip(cols, items[row:row + 4]):
                col.metric(k, "-" if v is None else v)
        py = analysis.per_year(pubs)
        c1, c2 = st.columns(2)
        fig = go.Figure(go.Bar(x=py["year"], y=py["publications"], marker=dict(color=PRIMARY, cornerradius=4),
                               hovertemplate="%{x}: %{y} yayın<extra></extra>"))
        fig.update_layout(title="Yıllara göre yayın sayısı", height=360, margin=dict(t=40, b=10),
                          xaxis=dict(tickformat="d", dtick=1 if len(py) <= 25 else None))
        c1.plotly_chart(fig, width="stretch")
        # Yayın olmayan yıllarda ortalama tanımsızdır; 0 yerine boşluk bırak.
        mean_c = py["mean_citations"].where(py["publications"] > 0)
        fig = go.Figure(go.Scatter(x=py["year"], y=mean_c, mode="lines+markers", connectgaps=False,
                                   line=dict(width=2, color=PRIMARY), marker=dict(size=8),
                                   hovertemplate="%{x}: %{y} atıf/yayın<extra></extra>"))
        fig.update_layout(title="Yayın başına ortalama atıf", height=360, margin=dict(t=40, b=10),
                          xaxis=dict(tickformat="d", dtick=1 if len(py) <= 25 else None))
        c2.plotly_chart(fig, width="stretch")
        with st.expander("Yıllık tablo"):
            st.dataframe(py, width="stretch", hide_index=True)

        c1, c2, c3 = st.columns(3)
        for col, field in zip((c1, c2, c3), ("language", "publication_type", "database")):
            df = analysis.top_counts(pubs, field, 15)
            if not df.empty:
                col.plotly_chart(bar_chart(df, analysis.FIELD_LABELS[field], "Yayın", analysis.FIELD_LABELS[field]),
                                 width="stretch")
        st.subheader("En çok atıf alan yayınlar")
        by = st.radio("Sıralama ölçütü", ["Atıf", "Yerel atıf"], horizontal=True,
                      help="Atıf: TR Dizin'deki toplam atıf. Yerel atıf: bu veri setindeki yayınlardan gelen atıf.")
        st.dataframe(analysis.most_cited(pubs, 25, by=by), width="stretch", hide_index=True,
                     column_config={"Bağlantı": st.column_config.LinkColumn()})

with tab_actors:
    if not need_data():
        n = st.slider("Gösterilecek sayı", 5, 50, 20)
        st.subheader("Yazar metrikleri (veri seti içinde)")
        am = analysis.author_metrics(pubs, n)
        st.dataframe(am, width="stretch", hide_index=True)
        c1, c2 = st.columns(2)
        for i, field in enumerate(("journal", "institution", "city", "keyword", "subject", "country",
                                   "institution_type", "institution_status")):
            df = analysis.top_counts(pubs, field, n)
            if df.empty:
                continue
            (c1 if i % 2 == 0 else c2).plotly_chart(
                bar_chart(df, analysis.FIELD_LABELS[field], "Yayın", f"En üretken: {analysis.FIELD_LABELS[field]}"),
                width="stretch",
            )

with tab_cites:
    if not need_data():
        n_detail = sum(1 for p in pubs if p.has_details)
        if n_detail < len(pubs):
            st.warning(f"{len(pubs) - n_detail} yayında kaynakça veya atıf yapan yayın bilgisi eksik. "
                       "*Veri Topla → Eksik kaynakça/atıf ayrıntılarını tamamla* ile tamamlayabilirsiniz.")
        st.subheader("Atıf kaynaklarının karşılaştırması")
        st.caption("Yalnızca CrossRef/OpenAlex/OpenCitations verisi olan yayınlar üzerinden. "
                   "“OpenAlex (TR Dizin)” TR Dizin'in bildirdiği sayıdır ve çoğu zaman eksiktir; "
                   "güncel sayılar için yayınları OpenAlex'te doğrudan arayın.")
        n_oa = sum(1 for p in pubs if p.openalex_id)
        oc1, oc2, oc3 = st.columns([2, 1, 2])
        oa_mail = oc1.text_input("E-posta (isteğe bağlı)", key="oa_mail", help="Hiçbir yere kaydedilmez.")
        oa_key = oc1.text_input("OpenAlex API anahtarı (isteğe bağlı)", key="oa_key", type="password",
                                help="Anahtarsız günlük kota yaklaşık 100 başlık araması; ücretsiz anahtar "
                                     "(openalex.org/settings/api) 10 katı. Hiçbir yere kaydedilmez.")
        oa_title = oc2.checkbox("Başlıkla da ara", value=True, help="DOI'si olmayan yayınları başlık ve yılla eşleştirir")
        oc3.caption(f"OpenAlex'te bulunmuş: {n_oa}/{len(pubs)} yayın")
        if oc3.button("OpenAlex'ten doğrudan atıf sayılarını al", disabled=not pubs):
            from bibliotr import openalex

            todo = [p for p in pubs if not p.openalex_id]
            bar = st.progress(0.0, text="DOI ile aranıyor…")

            def oa_progress(stage, i, n):
                if stage == "title" and n:
                    bar.progress(i / n, text=f"Başlıkla aranıyor {i}/{n}")

            report: dict = {}
            with st.spinner("OpenAlex sorgulanıyor…"):
                ms = openalex.match_publications(
                    todo, openalex.OpenAlexClient(mailto=oa_mail or None, api_key=oa_key or None),
                    by_title=oa_title, on_progress=oa_progress, report=report)
                store.save_openalex(ms)
            clear_data_cache()
            if report.get("title_not_searched"):
                st.warning(f"{len(ms)} yayın bulundu; {report['title_not_searched']} yayın başlıkla aranamadı. "
                           + (report.get("stopped") or "") + " Düğmeye yeniden basınca yalnızca kalanlar aranır.")
            else:
                st.success(f"{len(ms)} yayın OpenAlex'te bulundu.")
                st.rerun()
        cs = analysis.citation_sources(pubs)
        c1, c2 = st.columns([1, 1])
        c1.dataframe(cs, hide_index=True, width="stretch")
        c2.plotly_chart(bar_chart(cs.infer_objects().fillna(0), "Kaynak", "Toplam atıf", "Toplam atıf"), width="stretch")

        st.subheader("En sık atıf yapılan kaynaklar")
        st.caption("Aynı kaynağın farklı yazımları; ilk yazar soyadı, yıl ve başlığın ilk kelimeleriyle "
                   "eşleştirilir. Kaynak TR Dizin'de ise kimliğiyle kesin eşleşir.")
        st.dataframe(analysis.top_references(pubs, 50), hide_index=True, width="stretch")

        st.subheader("Atıf yapılan kaynakların yaş dağılımı")
        ref_years = [r.year for p in pubs for r in p.references if r.year and p.year and r.year <= p.year]
        if ref_years:
            ages = pd.Series([p.year - r.year for p in pubs for r in p.references
                              if r.year and p.year and r.year <= p.year]).clip(upper=50)
            fig = go.Figure(go.Histogram(x=ages, marker=dict(color=PRIMARY), xbins=dict(size=1),
                                         hovertemplate="%{x} yıl: %{y} kaynak<extra></extra>"))
            fig.update_layout(height=320, xaxis_title="Kaynağın yaşı (yayın yılı - kaynak yılı, 50+ birleşik)",
                              yaxis_title="Kaynak sayısı", margin=dict(t=20, b=10), bargap=0.1)
            st.plotly_chart(fig, width="stretch")
            st.caption(f"Medyan kaynak yaşı: {ages.median():.0f} yıl · Price indeksi "
                       f"(son 5 yıl içindeki kaynak oranı): %{100 * (ages <= 5).mean():.1f}")

with tab_laws:
    if not need_data():
        st.subheader("Lotka yasası (yazar üretkenliği)")
        lt, params = analysis.lotka(pubs)
        if not lt.empty:
            c1, c2 = st.columns([2, 1])
            fig = go.Figure()
            fig.add_trace(go.Scatter(x=lt["Yayın sayısı"], y=lt["Gözlenen oran"], mode="lines+markers",
                                     name="Gözlenen", line=dict(width=2, color=SERIES[0]), marker=dict(size=8)))
            fig.add_trace(go.Scatter(x=lt["Yayın sayısı"], y=lt["Beklenen oran (n=2)"], mode="lines+markers",
                                     name="Beklenen (n=2)", line=dict(width=2, color=SERIES[1], dash="dash"),
                                     marker=dict(size=8)))
            fig.update_layout(height=380, xaxis_title="Yayın sayısı", xaxis_dtick=1, yaxis_title="Yazar oranı",
                              yaxis_type="log", margin=dict(t=20, b=10), legend=dict(orientation="h"))
            c1.plotly_chart(fig, width="stretch")
            c2.json(params)
            c2.dataframe(lt, hide_index=True, width="stretch")

        st.subheader("Bradford yasası (dergi dağılımı)")
        bf = analysis.bradford(pubs)
        if not bf.empty:
            zones = bf.groupby("Bölge").agg(Dergi=("Dergi", "size"), Yayın=("Yayın", "sum")).reset_index()
            c1, c2 = st.columns([1, 2])
            c1.dataframe(zones, hide_index=True, width="stretch")
            c1.caption("Klasik Bradford dağılımında bölgelerdeki dergi sayıları yaklaşık 1 : n : n² oranındadır.")
            c2.dataframe(bf, hide_index=True, width="stretch", height=350)

with tab_network:
    if not need_data():
        c1, c2, c3, c4 = st.columns(4)
        network_labels = {
            "keyword": "Anahtar kelime eş-oluşum", "author": "Ortak yazarlık", "institution": "Kurum işbirliği",
            "city": "Şehir işbirliği", "country": "Ülke işbirliği", "subject": "Konu eş-oluşum",
            "reference": "Ortak atıf (co-citation)", "coupling": "Bibliyografik eşleşme",
            "citation": "Doğrudan atıf (veri seti içi)",
        }
        kind = c1.selectbox("Ağ türü", list(network_labels), format_func=network_labels.get)
        min_occ = c2.number_input("En az geçiş", 1, 100, 2, disabled=kind in ("coupling", "citation"))
        min_w = c3.number_input("En az bağlantı ağırlığı", 1, 100, 1, disabled=kind == "citation")
        top_n = c4.number_input("En fazla düğüm", 10, 1000, 80, disabled=kind == "citation")
        if kind == "coupling":
            g = analysis.coupling_network(pubs, int(min_w), int(top_n))
            st.caption("Düğümler yayınlardır; bağlantı ağırlığı ortak kaynak sayısıdır. Düğüm boyutu: TR Dizin atıfı.")
        elif kind == "citation":
            g = analysis.citation_network(pubs)
            st.caption("Ok yönü atıf yapan yayından atıf alana doğrudur (görselde yönsüz çizilir). "
                       "Düğüm boyutu: yerel atıf.")
        else:
            g = analysis.field_network(pubs, kind, int(min_occ), int(min_w), int(top_n))
            if kind == "reference":
                st.caption("Düğümler atıf yapılan kaynaklardır; iki kaynak aynı yayında birlikte atıf aldıkça "
                           "bağlantı güçlenir.")
        g.remove_nodes_from([n for n in list(g.nodes) if g.degree(n) == 0])
        st.caption(f"{g.number_of_nodes()} düğüm, {g.number_of_edges()} bağlantı")
        if g.number_of_nodes() == 0:
            st.info("Bu eşiklerle ağ oluşmadı. 'En az geçiş' değerini düşürmeyi deneyin.")
        else:
            st.plotly_chart(network_figure(g.to_undirected() if g.is_directed() else g, ""), width="stretch")
        st.dataframe(analysis.network_table(g), width="stretch", hide_index=True)
        map_txt, net_txt = export.to_vosviewer(g)
        d1, d2 = st.columns(2)
        d1.download_button("VOSviewer map dosyası", map_txt, f"{selected}_{kind}_map.txt")
        d2.download_button("VOSviewer network dosyası", net_txt, f"{selected}_{kind}_network.txt")

with tab_trend:
    if not need_data():
        c1, c2, c3 = st.columns(3)
        field = c1.selectbox("Alan", ["keyword", "subject", "journal", "institution"],
                             format_func=lambda f: analysis.FIELD_LABELS[f])
        period = c2.number_input("Dönem uzunluğu (yıl)", 1, 20, 5)
        top = c3.number_input("En sık kaç öğe", 5, 50, 20)
        tr = analysis.trend_by_period(pubs, field, int(period), int(top))
        if tr.empty:
            st.info("Bu alan için veri yok.")
        else:
            fig = go.Figure(go.Heatmap(z=tr.values, x=list(tr.columns), y=[str(i)[:50] for i in tr.index],
                                       colorscale="Blues", xgap=2, ygap=2,
                                       hovertemplate="%{y}<br>%{x}: %{z} yayın<extra></extra>"))
            fig.update_layout(height=max(350, 24 * len(tr) + 80), yaxis=dict(autorange="reversed"),
                              margin=dict(t=20, b=10))
            st.plotly_chart(fig, width="stretch")
            st.dataframe(tr, width="stretch")

with tab_thes:
    st.markdown(
        "Aynı birimin farklı yazımlarını birleştirin. Öneriler **kendiliğinden uygulanmaz**: onayladıklarınız "
        "sözlüğe eklenir, *Ayrı tut* dedikleriniz bir daha önerilmez. Sözlük tüm veri setleri için ortaktır, "
        "ham veriyi değiştirmez ve kenar çubuğundan kapatılabilir."
    )
    thes = store.load_thesaurus()
    counts = {f: len(thes.entries[f]) for f in th.FIELDS}
    mc = st.columns(3)
    for col, f in zip(mc, th.FIELDS):
        col.metric(f"Sözlükte: {th.FIELD_LABELS[f].lower()}", counts[f])

    if not need_data():
        st.subheader("Öneriler")
        f1, f2, f3 = st.columns([1, 1, 1])
        field = f1.radio("Alan", th.FIELDS, format_func=th.FIELD_LABELS.get, horizontal=True, key="thes_field")
        threshold, min_pairs = 1.0, 0
        if field != "author":
            threshold = f2.slider("Benzer yazılış eşiği", 0.75, 1.0, th.DEFAULT_THRESHOLD[field], 0.01,
                                  help="Düşürdükçe daha çok (ama daha az isabetli) öneri çıkar.")
        if field == "keyword":
            min_pairs = f3.number_input("Türkçe-İngilizce karşılık: en az yayın", 0, 50, 2,
                                        help="Aynı yayında aynı sırada verilen Türkçe ve İngilizce anahtar "
                                             "kelimeler en az bu kadar yayında birlikte geçerse önerilir. 0: kapalı.")
        else:
            f3.caption("Yazarlarda öneriler: aynı ORCID, kimliksiz kayıt ile aynı ad, aynı ad ve ortak kurum."
                       if field == "author" else "Kurumlarda kısaltmalar (Üniv., Fak., Hast., T.C.) "
                                                 "önce tek biçime getirilir.")
        sugs = load_suggestions(selected, data_version, store.thesaurus_version(), field, threshold,
                                int(min_pairs))
        shown = sugs[:300]
        if not sugs:
            st.success("Bu alanda öneri yok.")
        else:
            st.caption(f"{len(sugs)} öneri" + (" (ilk 300 gösteriliyor)" if len(sugs) > 300 else "")
                       + ". *Hedef* sütununu düzenleyerek birleşik adı değiştirebilirsiniz.")
            df = th.suggestions_frame(shown)
            df.insert(2, "Ayrı tut", False)
            edited = st.data_editor(
                df, hide_index=True, width="stretch", key=f"thes_editor_{field}_{store.thesaurus_version()}",
                disabled=["no", "Birleşecekler", "Hedef yayın", "Toplam yayın", "Neden", "Benzerlik"],
                column_config={"no": None,
                               "Uygula": st.column_config.CheckboxColumn(help="Sözlüğe ekle"),
                               "Ayrı tut": st.column_config.CheckboxColumn(help="Bir daha önerme")},
            )
            b1, b2 = st.columns([1, 3])
            if b1.button("Seçilenleri kaydet", type="primary"):
                n_acc = n_rej = 0
                for _, row in edited.iterrows():
                    s_ = shown[int(row["no"])]
                    if row["Uygula"]:
                        th.accept(thes, s_, str(row["Hedef"]).strip() or None)
                        n_acc += 1
                    elif row["Ayrı tut"]:
                        th.reject(thes, s_)
                        n_rej += 1
                store.save_thesaurus(thes)
                st.toast(f"{n_acc} öneri eklendi, {n_rej} öneri ayrı tutuldu.")
                st.rerun()
            if b2.button("Tüm önerileri kabul et"):
                for s_ in sugs:
                    th.accept(thes, s_)
                store.save_thesaurus(thes)
                st.rerun()

        with st.expander("Aynı kısa adı taşıyan farklı yazarlar"):
            st.caption("TR Dizin yazar kimliği farklı olduğu için uygulama içinde ayrı sayılırlar. Bibliometrix "
                       "ve VOSviewer ise yazarları adla tanır; WoS dışa aktarımında bu kişiler tam adlarıyla "
                       "(tam ad da aynıysa sıra numarasıyla) yazılır. Aslında aynı kişi olanları *Yazar* "
                       "önerilerinden birleştirin.")
            st.dataframe(th.homonyms(pubs), hide_index=True, width="stretch")

    st.subheader("Sözlük")
    tf = thes.frame()
    if tf.empty:
        st.caption("Sözlük boş.")
    else:
        tf.insert(0, "Sil", False)
        ed = st.data_editor(tf, hide_index=True, width="stretch", key=f"thes_table_{store.thesaurus_version()}",
                            disabled=["Alan", "Varyant", "Hedef"], column_config={"field": None, "key": None})
        if st.button("Seçili girdileri sil"):
            for row in ed[ed["Sil"]].itertuples(index=False):
                thes.remove(row.field, row.key)
            store.save_thesaurus(thes)
            st.rerun()
    with st.form("thes_add", clear_on_submit=True):
        a1, a2, a3, a4 = st.columns([1, 2, 2, 1])
        add_field = a1.selectbox("Alan", ["keyword", "institution"], format_func=th.FIELD_LABELS.get)
        add_var = a2.text_input("Varyant", placeholder="ör. bibliometric analyses")
        add_tgt = a3.text_input("Hedef", placeholder="ör. bibliometric analysis")
        if a4.form_submit_button("Elle ekle") and add_var.strip() and add_tgt.strip():
            thes.add(add_field, add_var.strip(), add_tgt.strip())
            store.save_thesaurus(thes)
            st.rerun()

    st.subheader("Dosyalar")
    d = st.columns(4)
    d[0].download_button("Sözlük (JSON)", thes.to_json(), "trdizin_sozluk.json", "application/json",
                         help="Başka bir bilgisayarda veya projede içe aktarmak için")
    d[1].download_button("VOSviewer: anahtar kelime", th.vosviewer_thesaurus(thes, "keyword"),
                         "thesaurus_terms.txt", help="VOSviewer'da harita oluştururken 'thesaurus file' olarak seçin")
    d[2].download_button("VOSviewer: kurum", th.vosviewer_thesaurus(thes, "institution"),
                         "thesaurus_organizations.txt")
    d[3].download_button("Bibliometrix synonyms", th.bibliometrix_synonyms(thes, "keyword"),
                         "bibliometrix_synonyms.txt",
                         help="Biblioshiny ağ ve tematik analizlerinde 'synonyms' dosyası olarak yükleyin")
    up = st.file_uploader("Sözlük içe aktar (JSON)", type=["json"], key="thes_upload")
    if up is not None and st.button("İçe aktar"):
        try:
            n = thes.merge(th.Thesaurus.from_json(up.getvalue().decode("utf-8")))
        except (ValueError, KeyError) as exc:
            st.error(f"Dosya okunamadı: {exc}")
        else:
            store.save_thesaurus(thes)
            st.toast(f"{n} girdi eklendi.")
            st.rerun()
    st.caption("Uygulamadan dışa aktarılan WoS, CSV, RIS ve BibTeX dosyalarında sözlük zaten uygulanmıştır; "
               "VOSviewer ve bibliometrix sözlük dosyaları, sözlük kapalıyken aktarılan dosyalar veya başka "
               "kaynaklardan gelen veriler içindir.")

with tab_export:
    if not need_data():
        st.markdown(
            "- **CSV / Excel**: genel analiz, SPSS, R, Python\n"
            "- **WoS düz metin**: Biblioshiny (*Web of Science → Plaintext*) ve VOSviewer "
            "(*bibliographic database files → Web of Science*) doğrudan okur\n"
            "- **RIS / BibTeX**: Zotero, Mendeley, EndNote\n"
            "- **VOSviewer map/network**: *Ağlar* sekmesinden"
        )
        with st.expander("Bibliometrix / VOSviewer (WoS) seçenekleri ve uyumluluk denetimi"):
            o1, o2, o3, o4 = st.columns(4)
            wos_kw = o1.selectbox("DE: yazar anahtar kelimeleri", ["all", "en", "tr"],
                                  format_func={"all": "Tümü (TR + EN)", "en": "Yalnız İngilizce",
                                               "tr": "Yalnız Türkçe"}.get)
            wos_id = o2.selectbox("ID (Keywords Plus)", ["en", "tr", "all", "none"],
                                  format_func={"en": "İngilizce anahtar kelimeler", "tr": "Türkçe anahtar kelimeler",
                                               "all": "Tümü", "none": "Boş bırak"}.get,
                                  help="TR Dizin'de Keywords Plus yoktur. Bibliometrix'te bazı analizler varsayılan "
                                       "olarak ID alanını kullandığı için boş kalmasın diye doldurulur.")
            wos_title = o3.selectbox("TI: başlık", ["original", "en"],
                                     format_func={"original": "Yayın dilinde", "en": "İngilizce (varsa)"}.get)
            wos_ascii = o4.checkbox("Türkçe karakterleri ASCII'ye çevir", value=True,
                                    help="Windows'ta R'ın büyük harfe çevirmesinde Türkçe karakterler bozulabilir; "
                                         "WoS'un kendisi de ASCII kullanır.")
            wos_dis = o4.checkbox("Aynı kısa adlı farklı yazarları ayırt et", value=True,
                                  help="Bibliometrix ve VOSviewer yazarları adla tanır; 'Yilmaz, A' adlı farklı "
                                       "kişiler tek kişi sayılır. Açıkken bu yazarlar tam adlarıyla, tam ad da "
                                       "aynıysa sıra numarasıyla yazılır.")
            from bibliotr.wos import WosOptions, check_wos
            wos_text = export.to_wos(pubs, WosOptions(ascii=wos_ascii, keywords=wos_kw, id_keywords=wos_id,
                                                      title=wos_title, disambiguate_authors=wos_dis))
            rep = check_wos(wos_text)
            m = st.columns(4)
            m[0].metric("Ülkesi bulunan belge", f"{rep['documents_with_country']}/{rep['documents']}")
            m[1].metric("Kurum (C3) alanı", f"{rep['documents_with_C3']}/{rep['documents']}")
            m[2].metric("Birden çok yayında geçen kaynak", rep["cited_more_than_once"])
            m[3].metric("Yerel atıf (LCS)", rep["local_citations"])
            if rep["missing_mandatory"]:
                st.warning(f"Eksik zorunlu alanlar (belge sayısı): {rep['missing_mandatory']}")
            st.caption("Bu değerler bibliometrix'in dosyayı okuma kurallarının (isi2df, metaTagExtraction, "
                       "histNetwork) Python taklidiyle hesaplanır. Sorumlu yazar bilgisi TR Dizin'de olmadığı için "
                       "RP alanına ilk yazar yazılır. DOI'si olmayan yayınlar bibliometrix'te yerel atıf alamaz.")
        c = st.columns(5)
        c[0].download_button("CSV", export.to_csv(pubs), f"{selected}.csv", "text/csv")
        c[1].download_button("Excel", export.to_excel(pubs), f"{selected}.xlsx")
        c[2].download_button("WoS (txt)", wos_text, f"{selected}_wos.txt")
        c[3].download_button("RIS", export.to_ris(pubs), f"{selected}.ris")
        c[4].download_button("BibTeX", export.to_bibtex(pubs), f"{selected}.bib")
        st.dataframe(analysis.publications_frame(pubs), width="stretch", hide_index=True)

with tab_raw:
    st.markdown(
        "Bu sekme, kaydedilen **ham JSON**'un (arama + `api/publicationById` ayrıntısı) alan "
        "yapısını ve normalize edilmiş halini yan yana gösterir. Normalize tarafta boş kalan bir alan, "
        "`bibliotr/normalize.py` içindeki aday anahtar listesine eklenmelidir. Ardından `bibliotr reindex` "
        "komutu veriyi yeniden çekmeden günceller."
    )
    if selected:
        raws = store.raw_records(selected, limit=50)
        if raws:
            idx = st.number_input("Kayıt sırası", 0, len(raws) - 1, 0)
            c1, c2 = st.columns(2)
            c1.markdown("**Ham alanlar**")
            c1.dataframe(pd.DataFrame(list(key_tree(raws[idx]).items()), columns=["Yol", "Örnek değer"]),
                         width="stretch", hide_index=True, height=500)
            c2.markdown("**Normalize edilmiş**")
            c2.json(normalize_publication(raws[idx]).to_dict(), expanded=False)
            if st.button("Alan eşlemesini yeniden uygula (reindex)"):
                st.success(f"{store.reindex()} yayın yeniden işlendi.")
                clear_data_cache()
