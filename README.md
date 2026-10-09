# BiblioTR

Harvesting, validating and exporting records from TR Dizin, Türkiye's national citation index, for bibliometric analysis.

**English** | [Türkçe](README.tr.md)

[![tests](https://github.com/yilmaz-yuksel/BiblioTR/actions/workflows/tests.yml/badge.svg)](https://github.com/yilmaz-yuksel/BiblioTR/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

A Python toolkit and web interface for harvesting publication records from
[TR Dizin](https://search.trdizin.gov.tr), Türkiye's national citation index operated by
TÜBİTAK ULAKBİM, and for bringing them into mainstream bibliometric workflows. It collects records
by keyword and facet filters through the TR Dizin search API, stores them in SQLite, runs
bibliometric analyses, and exports the data to VOSviewer, Bibliometrix/Biblioshiny, Zotero and
similar tools.

TR Dizin covers a large body of Turkish-language and Türkiye-based scholarship that is only partly
indexed by Web of Science or Scopus, while widely used bibliometric tools cannot read TR Dizin data
directly. This toolkit closes that gap, most notably by writing TR Dizin records in the Web of
Science plain-text format so that they can be analysed with existing software.

> This is an independent research tool. It is not developed, endorsed or supported by TÜBİTAK
> ULAKBİM or TR Dizin.

API documentation: <https://development.trdizin.gov.tr/>

## Features

- **Harvesting**: keyword and facet search, automatic pagination, year-by-year splitting to get
  past the 10,000-record paging limit, rate limiting with exponential back-off
- **Subject index**: browse the TR Dizin subject tree; subject filters accept full values, subject
  names or whole groups, with suggestions for misspelled names
- **Enrichment**: reference lists, citing TR Dizin publications, and citation counts from
  CrossRef, OpenAlex and OpenCitations as reported by TR Dizin
- **Direct OpenAlex matching**: finds records in OpenAlex by DOI and, for records without a DOI,
  by title and year, and stores current OpenAlex citation counts. The OpenAlex counts reported by
  TR Dizin are often stale or zero, so direct counts are shown separately
- **Quick profile**: distributions of years, journals, institutions, cities, subjects and authors
  for an entire result set in a single request, without downloading records
- **Analyses**: descriptive indicators, author metrics (fractional counting, first authorship,
  h- and g-index), Lotka's law, Bradford's law, co-authorship, institution / city / country
  collaboration, keyword and subject co-occurrence, co-citation, bibliographic coupling, direct
  citation networks, community detection, centrality, thematic evolution
- **Thesaurus**: suggestions for merging spelling variants of keywords and institutions,
  Turkish-English keyword pairs and duplicate author profiles (same ORCID); nothing is merged
  without approval. The thesaurus is applied to all analyses and exports and can be exported as
  a VOSviewer thesaurus or a bibliometrix synonyms list. Authors who share a short name
  ("Yilmaz, A") are written distinguishably in the WoS export
- **Export**: CSV, Excel, Web of Science plain text, RIS, BibTeX, VOSviewer map and network files
- **Reproducibility**: raw JSON is stored alongside normalised fields, so field mappings can be
  improved and re-applied (`bibliotr reindex`) without re-harvesting

## Installation

Requires Python 3.10 or later.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Web interface

```bash
streamlit run app.py
```

This opens `http://localhost:8501`. The interface is currently in Turkish. Tabs:

| Tab | Contents |
|---|---|
| Veri Topla (Collect) | Query, all facet filters, year range, preview, harvest and save as a dataset |
| Genel Bakış (Overview) | Summary indicators, annual publications and citations, language / document type / database distributions, most cited papers |
| Yazar / Dergi / Kurum (Authors / Journals / Institutions) | Author metrics, most productive journals, institutions, institution types, cities, keywords |
| Kaynakça ve Atıf (References and Citations) | TR Dizin vs CrossRef / OpenAlex / OpenCitations citation comparison, most cited references, reference age and Price index |
| Yasalar (Laws) | Lotka (author productivity) and Bradford (journal zones) |
| Ağlar (Networks) | Co-authorship, institution / city / country collaboration, keyword and subject co-occurrence, co-citation, bibliographic coupling, direct citation; clusters, centrality; VOSviewer files |
| Tematik Evrim (Thematic Evolution) | Keyword / subject heat map by period |
| Dışa Aktar (Export) | CSV, Excel, WoS plain text, RIS, BibTeX |
| Ham Veri (Raw Data) | Raw JSON fields side by side with normalised fields |

## Command line

```bash
# One page of search results
bibliotr search "bibliometri" --limit 10 --facets

# Profile of the whole result set without downloading records
# (year, journal, institution, city, subject and author distributions); the query is optional
bibliotr profile --facet "subject=Fen > Tıp > Spor Bilimleri" --facet documentType=PAPER --out sport_profile.xlsx

# Journal record (indexed and rejected years, subjects, editor, missing issues)
bibliotr journal 488 --profile
# Harvest all publications of a journal (by default only the years it was indexed in TR Dizin)
bibliotr harvest --journal 488 --dataset sbd

# Subject list (TR Dizin subject index; filter by field, group or subject name)
bibliotr subjects Tıp
# --facet subject=... accepts the full value, a subject name ("spor bilimleri") or a group ("Tıp");
# a group searches all subjects under it. Misspellings get the closest matching subjects suggested.

# Harvest every publication in a subject
bibliotr harvest --facet "subject=Fen > Tıp > Spor Bilimleri" --dataset sport --years 2000-2026

# Harvest all results for a query (split by year) into the "biblio" dataset
bibliotr harvest "bibliometri" --dataset biblio \
    --facet database=SOCIAL --facet publicationLanguage=TUR --facet publicationLanguage=ENG \
    --years 2010-2025

# Fill in missing reference and citation details afterwards
bibliotr enrich --dataset biblio

# Full record of a single publication
bibliotr detail 1163868

# Merge several query datasets (deduplicated) and show their overlap table
bibliotr merge fa fa_tr fa_en --out overlap.csv

# Match a dataset in OpenAlex (DOI, then title + year) and store current citation counts
bibliotr openalex --dataset biblio --mailto you@university.edu

bibliotr datasets
bibliotr stats --dataset biblio

# Export
bibliotr export --dataset biblio --format wos  --out biblio_wos.txt
bibliotr export --dataset biblio --format xlsx --out biblio.xlsx
bibliotr export --dataset biblio --format vosviewer --network keyword --out biblio_kw.txt

# Thesaurus: list merge suggestions, then accept or keep separate by number
bibliotr thesaurus suggest --dataset biblio --field keyword
bibliotr thesaurus suggest --dataset biblio --field keyword --accept 0,2,5 --reject 1
bibliotr thesaurus export --format vosviewer --field keyword --out thesaurus_terms.txt

# Direct lookup by DOI / ORCID / ISSN
bibliotr lookup doi 10.xxxx/yyyy
```

Facet names follow the API documentation; the `facet-` prefix is optional. Repeating a facet
selects several values (OR). All facets and sort orders are listed in the `FACETS` and `ORDERS`
dictionaries in `bibliotr/client.py`.

### From Python / Jupyter

```python
from bibliotr import TRDizinClient, Store
from bibliotr import analysis

client = TRDizinClient(min_interval=1.0)
records = client.harvest("bibliometri", facets={"database": "SOCIAL"}, years=range(2015, 2026))

store = Store("data/trdizin.db")
store.add_records(store.create_dataset("biblio", "bibliometri"), records)

pubs = store.publications("biblio")
analysis.summary(pubs)
analysis.top_counts(pubs, "journal", 20)
g = analysis.field_network(pubs, "author", min_occurrence=2)
```

## Using the data with other tools

- **Biblioshiny (R, bibliometrix)**: load the file produced with `--format wos` via
  *Import raw file(s) → Web of Science → Plaintext*.
- **VOSviewer**: open the WoS file via *Create map based on bibliographic data → Web of Science*,
  or open the `_map.txt` + `_network.txt` files produced with `--format vosviewer` via
  *Create map based on network data*.
- **Zotero / Mendeley / EndNote**: RIS or BibTeX.

Co-citation and bibliographic coupling require reference data. If a record has no references in
TR Dizin, the `CR` field of the WoS file stays empty.

### How the WoS file is prepared for bibliometrix

The WoS file (`bibliotr/wos.py`) follows bibliometrix's parsing rules (`isi2df`,
`metaTagExtraction`, `histNetwork`):

- **RP** (a mandatory field) is written. TR Dizin has no corresponding-author data, so the
  **first author** is used; "Corresponding Author's Countries" therefore shows first authors.
- **C1**: one address per line as `[Authors] INSTITUTION UNIV, City, Turkey.`, with the country
  written as in bibliometrix's country list and inner dots (e.g. "T.C.") removed.
- **C3**: TR Dizin's standardised institution names, which bibliometrix uses for affiliations.
- **CR**: references are rewritten as `SURNAME I, YEAR, SOURCE, Vvol, Ppage, DOI`; the same work
  is written identically in every record (co-citation), and a reference to a record of the data
  set is written exactly as that record's bibliometrix label (local citations / historiograph).
  Records without a DOI cannot receive local citations in bibliometrix.
- **DE / ID**: DE holds author keywords (TR, EN or both). TR Dizin has no Keywords Plus, so ID
  holds the English keywords by default.
- Turkish characters are transliterated to ASCII by default (`--wos-unicode` keeps them).

```bash
bibliotr export --dataset biblio --format wos --out biblio_wos.txt --wos-keywords en
bibliotr wos-check biblio_wos.txt   # countries, affiliations, references and local citations as bibliometrix sees them
```

**OpenAlex citation counts.** TR Dizin reports an OpenAlex citation count for each record
(`otherCitations`). In a test on 162 records, this reported count was zero for 108 records that
OpenAlex itself shows as cited, and never higher than OpenAlex's own count. Use
`bibliotr openalex` (or the button in the *References and Citations* tab) to fetch counts directly;
the comparison table then shows "OpenAlex (TR Dizin)" and "OpenAlex (doğrudan)" (direct) side by side.
Title matching accepts a candidate only when the folded titles are at least 92% similar and the
publication years differ by at most one. OpenAlex charges per request: without an API key the
daily budget allows about 100 title searches (DOI lookups are much cheaper). Get a free key at
<https://openalex.org/settings/api> and pass it with `--api-key` or the `OPENALEX_API_KEY`
environment variable. If the budget runs out, records found so far are kept and a rerun searches
only the rest.

## Data source and field mapping

| Endpoint | Contents |
|---|---|
| `api/defaultSearch/publication/?q=...` | Search results (paged) |
| `api/publicationById/{id}` | Full record: references (`references`), citing TR Dizin publications (`citedReferences`), CrossRef / OpenAlex / OpenCitations citation counts (`otherCitations`) |
| `api/journalById/{id}` | Journal record: title, other title, ISSN, editor, indexed years (`journalYear`: issue months, subjects, missing issues / referee reports) and rejected years. It holds no publications; a journal's publications are searched by name (`facet-journalName`) and filtered by `journal.id` |
| `api/public/yayin/doi`, `yazar/orcid`, `dergi/issn` | Direct lookup by DOI / ORCID / ISSN |

Search responses include reference lists, but the citing publications (`citedReferences`) are
`null`. With `--details auto` (the default), a `publicationById` request is therefore sent only
for publications that have received citations (about one second each).

The `aggregations` field of a search response gives facet distributions for the entire result set.
`bibliotr profile` and the "quick profile" in the interface use it to show the year, journal,
institution, city, subject and author distributions of thousands of publications in one request.
Some distributions, such as the author list, may be truncated; the number of documents outside the
list is shown.

The search engine allows paging through at most 10,000 records; use `--years` for larger sets.

Notes on field mapping (`bibliotr/normalize.py`):

- **Title** is taken from `abstracts[].title` in the publication language. `orderTitle` is meant
  for sorting and may lose spaces (e.g. "BİRSOSYAL").
- **Authors** are ordered by the `order` field; responses may list them in reverse order.
- **Institution**: the parent institution matched by TR Dizin (`institution.rootTitle`, e.g. the
  university) is used; the sub-unit (faculty, department) is kept separately in `unit`. An author
  may have several institutions. For unmatched authors ("Tanımlanmamış Kurum", "Yabancı Kurumlar"),
  institution, city and country are inferred from the raw address (`institutionName`:
  "Institution, Faculty, Department, City, Country"). Because the country field is empty for
  matched domestic institutions, it is taken from the address or defaults to "Türkiye".
- **Reference matching** (for co-citation): exact when the reference is itself in TR Dizin
  (`targetPublication` id) or contains a DOI; otherwise heuristic, using first author surname +
  year + the first words of the title (APA and Vancouver styles are recognised).

If a field comes out empty, inspect the raw structure with `bibliotr inspect "query"`, update the
relevant `*_KEYS` list at the top of `normalize.py`, and run `bibliotr reindex`. Raw JSON is stored,
so no re-harvesting is needed.

## Responsible use

- By default the client waits one second between requests (`--delay`) and retries with
  exponential back-off on 429/5xx errors. Please do not lower this value.
- Check the TR Dizin / ULAKBİM terms of use before bulk harvesting, and cite the data source in
  your publications.
- Splitting broad queries by year with `--years` both avoids deep-paging limits and makes it easier
  to repeat an interrupted harvest in smaller pieces.

## Project structure

```
bibliotr/
  client.py     API client: search, paging, facets, rate limiting, DOI/ORCID/ISSN
  normalize.py  Raw record -> Publication (candidate field names live here)
  storage.py    SQLite: datasets, publications, authors, keywords, raw JSON
  analysis.py   Summary, distributions, author metrics, Lotka, Bradford, networks, thematic evolution
  export.py     CSV, Excel, WoS, RIS, BibTeX, VOSviewer
  openalex.py   Direct OpenAlex matching by DOI and title, current citation counts
  thesaurus.py  Thesaurus: merge suggestions, application, VOSviewer / bibliometrix files
  cli.py        `bibliotr` command
app.py          Streamlit interface
tests/          pytest tests
```

## Tests

```bash
pytest
```

`tests/fixtures/publication_by_id.json` and `tests/fixtures/search_subject.json` are real
(shortened) TR Dizin responses. `tests/fixtures/publication_search.json` is a synthetic example
used to exercise fallback field names.

## Citation

If you use this software in your research, please cite it. Citation metadata is provided in
[`CITATION.cff`](CITATION.cff); GitHub shows it under "Cite this repository".

## License

Released under the [MIT License](LICENSE). Data retrieved from TR Dizin remains subject to the
TR Dizin / ULAKBİM terms of use.
