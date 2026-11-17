# Replication package

Scripts, protocol and results behind the BiblioTR software paper. The use case is physical activity research
indexed in TR Dizin (dataset `fa_alan`, 967 journal articles 1997-2025, frozen on 2 October 2026; 926 judged
relevant).

```
protocol/   04_validation_protocol_20261002.md: the validation design and thresholds as fixed before coding,
            the coding timeline and every design change made afterwards
scripts/    numbered scripts, run from the repository root
results/    small outputs of the scripts: counts, logs, record identifiers, scores, figures
figures/    Figs. 1-4 of the paper (Fig. 4 drawn in VOSviewer from the files of script 13)
data/       local databases and coding sheets; never in the repository (.gitignore)
```

## What is and is not included

TR Dizin's terms do not allow its records to be redistributed. The package therefore holds record identifiers,
aggregate results and manual codes keyed by identifier, but no titles, abstracts, author lists or reference
texts:

- `results/freeze_fa_alan_20261002/freeze.json`: the frozen set (identifiers, SHA-256 of the database, coding samples).
- `results/agreement_fa_alan_20261007/relevant_ids.txt` and `not_relevant_ids.txt`: the relevance outcome.
- `results/codes_fa_alan/`: every manual code (relevance, field accuracy, citation links, cited-reference fields,
  author identity, OpenAlex title matches, keyword merges, citation-based recall), written by script 18.
- Files that would contain record content (Web of Science exports, bibliometrix tables, coding sheets) are
  regenerated locally by the scripts below.

A record can be opened at `https://search.trdizin.gov.tr/tr/yayin/detay/<id>` and harvested again with the
identifiers (`bibliotr detail <id>`, or the harvest scripts).

## Scripts and where their numbers appear

| Script | Purpose | Results | Paper |
|---|---|---|---|
| 01_probe_queries.py `--set fa_alan` | How TR Dizin's two search modes behave | `probe_fa_alan_20261009/` | Table 3 |
| 02b_harvest_field.py `--run` | Field queries with iterative variant discovery | `fieldharvest_20261002/` | 4.1-4.2, Fig. 2 |
| 04_freeze_and_sample.py | Freeze the dataset (SHA-256); build the relevance, field and author coding sheets | `freeze_fa_alan_20261002/` | 4.3, 5 (frozen set), 5.6 (author groups) |
| 05_agreement.py | Coder agreement, precision, field accuracy, author identity | `agreement_fa_alan_20261007/`, `author_identity_fa_alan_20261002.json` | 5.6, 5.7, 5.9 (V2, V3, V5) |
| 06_case_analysis.py | Use case: performance analysis, Lotka, Bradford, networks | `case_fa_alan_ilgili_20261007/` (relevant articles), `case_fa_alan_20261007/` (all) | 6.1-6.2 |
| 07_openalex.py, 08_title_match_check.py | OpenAlex matching and the check of every title match | `openalex_fa_alan_20261006/` | 5.8 (V7) |
| 09_validation_audit.py, 10_bibliometrix_interop.R | Reindexing, field coverage, citation links, bibliometrix, wosfile and metaknowledge | `validation_fa_alan_20261007/validation.json` | 5.2-5.5, Table 7 (V4, V6, V8) |
| 11_reference_coding.py | Citation-link and cited-reference coding sheets and scores | `reference_check_fa_alan_20261008.json` | 5.2, Table 6 (V4) |
| 12_scaling.py | Run time and memory from 1,000 to 30,000 records | `scaling_20261008/`, `scaling_20261008_windows/` | 5.4 (V8) |
| 13_keyword_thesaurus.py | Keyword merge decisions, thesaurus, VOSviewer files | `thesaurus_fa_alan_20261008/` | 6.2, Fig. 4 |
| 14_relevance_effects.py | Strategy recall on the relevant set, Lotka bootstrap, author-merge effects | `relevance_effects_fa_alan_20261008/` | Table 4, 5.6, 6.1, Fig. 3 (articles per year and language), 6.3 and Table 8 |
| 15_author_second_coder.py | Second coder for author identity | `author_second_coder_fa_alan/` | 5.6 (V5) |
| 16_citation_recall.py | Citation-based recall check | `citation_recall_fa_alan/` | 5.9, 7.4 |
| 17_reference_coverage.py | Reference-parser coverage and the effect of parser revisions | `reference_coverage_fa_alan_20261009.json` | 5.2, 5.3 |
| 18_export_codes.py | Manual codes without record content | `codes_fa_alan/` | Section 8 |
| 19_figures.py `--png` | Figures 1-3 (English and Turkish) as SVG and 600-dpi PNG; Fig. 3 from script 14's per-year counts | `../figures/` | Figs. 1-3 |
| (06, 09, 14 rerun) | Rerun of the use case and automatic checks with release 1.0.0 | `rerun_v1.0.0_20261012/rerun_check.json` | 5.4 |
| (06, 09, 14 rerun) | The same rerun with release 1.1.0 (TR Dizin API update) | `rerun_v1.1.0_20261117/rerun_check.json` | 5.4 |

Where a folder exists under two dates, the later one holds the figures in the paper.

## Reproducing

```bash
pip install -e ".[paper]"
python paper/scripts/02b_harvest_field.py --run ...      # needs access to TR Dizin; parameters as recorded
                                                         # in results/fieldharvest_20261002/harvest_log.json
python paper/scripts/04_freeze_and_sample.py --dataset fa_alan --scope fiziksel_aktivite --doc-types PAPER --n 0 --d1-n 150 --authors
cp paper/data/fa_alan_frozen_20261002.db paper/data/fa_alan_frozen_20261002_reindex.db   # work on a copy
python -c "from bibliotr.storage import Store; s = Store('paper/data/fa_alan_frozen_20261002_reindex.db'); s.reindex(); s.close()"
python paper/scripts/06_case_analysis.py --db paper/data/fa_alan_frozen_20261002_reindex.db --dataset fa_alan \
    --max-year 2025 --relevant paper/results/agreement_fa_alan_20261007/relevant_ids.txt
```

Only 01, 02b and 16 (`--fetch`) contact TR Dizin and only 07 contacts OpenAlex; every other script reads the
local database. The frozen database is opened read-only and its SHA-256 is checked against `freeze.json`;
analyses that add data (OpenAlex, thesaurus) work on a copy. A harvest made today will differ slightly from the
frozen set because TR Dizin keeps indexing; the identifiers in `freeze.json` fix the set used in the paper.

Scripts that score manual coding (05, 08, 11, 15, 16) read the coding sheets under `data/coding/fa_alan/`. To
rerun them, rebuild the blank sheets with the build step of each script and fill the code columns from
`results/codes_fa_alan/`.
