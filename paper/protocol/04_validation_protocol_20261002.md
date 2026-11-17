# Validation protocol, physical activity set (fixed 2 October 2026)

This file records the validation design of the trdizin-biblio software paper as it stood before any manual
coding was done. The text below is copied verbatim from revision 6 of the manuscript draft (Claude Docs
document "trdizin-biblio", English tab), whose Table 5 is headed "Status as of 2 October 2026". At that
revision the coding sheets had been created but none had been filled in (status column: "Coding tables
ready", "Pending").

The thresholds in Table 5 have not changed since. Changes to the design made after coding are listed at the
end of this file.

The toolkit was called trdizin-biblio until 9 October 2026, when it was renamed BiblioTR; the text below
keeps the old name.

## Timeline

| Date (UTC+3) | Event | Evidence |
|---|---|---|
| 2026-10-02 03:12 | Frozen set written (987 records, 967 journal articles) | `paper/data/fa_alan_frozen_20261002.db`, SHA-256 348363d7…3e8d8; `paper/results/freeze_fa_alan_20261002/freeze.json` |
| 2026-10-02 03:25 | Blank relevance and field coding sheets (K1, K2, D1) created | file dates of the blank sheets |
| 2026-10-02, before 15:01 | Blank author identity sheet created | listed as already present (`coding_kept`) in `freeze.json`; a blank copy was saved at 23:50 |
| 2026-10-02 15:01 | Script 04 run again to add the author-identity groups; `freeze.json` rewritten (its `frozen_at_utc` is 12:01 UTC, the time of this run). The database was not changed (same SHA-256) and no coding sheet was overwritten (`coding_written` empty; K1, K2, D1 and the author sheet in `coding_kept`) | `freeze.json` |
| 2026-10-02 | Validation design and thresholds written into the draft (revision 6) | this file |
| 2026-10-06 01:53 to 02:32 | Relevance (K1, K2) and field (D1) coding completed | file dates of the coded sheets |
| 2026-10-06 14:01 | OpenAlex title-match check completed | file date of the coded sheet |
| 2026-10-07 15:31 to 15:43 | Reference and keyword-thesaurus coding completed | file dates of the coded sheets |
| 2026-10-12 | Use-case figures and automatic checks rerun with release 1.0.0 on a fresh rebuild of the frozen database: every table and number identical, Web of Science export byte-identical | `paper/results/rerun_v1.0.0_20261012/rerun_check.json` |
| 2026-11-17 | The same rerun with release 1.1.0, which follows TR Dizin's updated API rules: every table and number identical, Web of Science export byte-identical | `paper/results/rerun_v1.1.0_20261117/rerun_check.json` |

The draft revisions carry no independent time stamp; the date of revision 6 comes from its own Table 5
caption and agrees with the file dates above.

## Section 4.3 at revision 6

Each check has a measure, a reference standard and a threshold written into the study protocol before any
coding (Table 5). Results that miss a threshold are reported, not dropped. Proportions carry Wilson 95%
intervals.

- **Relevance coding (V2).** Two coders code all 967 journal articles in the frozen set independently and
  blind to each other. A record is relevant when physical activity in humans, in the sense of Caspersen et
  al. (1985), is its main topic or one of its main variables. Animal, device and biochemical uses of
  "activity" are not relevant. Exercise and sport count only when framed as physical activity, and sedentary
  behaviour counts when studied as its counterpart.
- **Author identity (V5).** All 94 groups of TR Dizin identities that share a full name (190 identities) are
  judged as same or different persons from ORCID, institution, co-authors, topics and profile pages. Four
  ways of counting authors are then scored with B-cubed precision, recall and F1: short name, full name, TR
  Dizin identifier, and identifier merged on shared ORCID or same name and institution.
- **Lotka's law.** The exponent is fitted by discrete maximum likelihood and tested with a
  Kolmogorov-Smirnov statistic (Clauset et al., 2009); the log-log least-squares fit is shown only for
  comparison.

## Table 5 at revision 6: Validation checks. Status as of 2 October 2026

| Check | Question | Measure | Sample and reference | Threshold | Status |
|---|---|---|---|---|---|
| V1 | Is the code correct? | Passing tests, coverage, Python versions | Offline tests on recorded API responses | All pass; coverage ≥ 90% | 168 of 168 pass |
| V2 | Does retrieval find the right records? | Precision, Cohen's kappa, relative recall, saturation | All 967 articles, two blind coders | κ ≥ 0.80; precision ≥ 90% | Coding tables ready |
| V3 | Are fields extracted correctly? | Accuracy per field | 150 random records against their TR Dizin pages: title, authors, author order, year, journal, institution, DOI | Each field ≥ 98%; institution ≥ 95% | Coding table ready |
| V4 | Are references and citation links reliable? | Link agreement (Jaccard), DOI link precision, cited-reference field accuracy | Citing lists against reference targets (automatic); all DOI links and 200 stratified references (manual) | Link precision ≥ 95%; year ≥ 98%; author ≥ 90% | Automatic part done |
| V5 | Are authors told apart correctly? | B-cubed precision, recall, F1; effect on Lotka and top-20 rankings | 94 same-name groups (190 identities) | Reported | Coding table ready |
| V6 | Do standard tools reproduce the numbers? | Equality of counts; document-level local citation agreement | WoS export read by bibliometrix 5.5, metaknowledge and wosfile | Equal counts; every difference explained | Preliminary run done |
| V7 | Are links to OpenAlex correct? | Match precision, coverage, reported against direct citation counts | Whole frozen set; all title matches checked by hand | Title-match precision ≥ 95% | Pending |
| V8 | Is the pipeline reproducible and scalable? | Identical outputs, SHA-256, run time and memory | Reindex from raw JSON; Windows and Linux; 1k to 30k records | Byte-identical outputs | Reindex check done |

## Changes after coding

No threshold was changed. These parts of the design changed after coding began; the paper reports each one.

- **V4 sample.** A random sample of 50 TR Dizin links was added to the hand check, next to all DOI-only links.
- **V5 counting methods.** Three counting methods are scored instead of four. Within the sampled groups the
  short name forms the same clusters as the full name, so it is not scored separately.
- **V2 recall.** Relative recall is reported on the 926 relevant articles. A planned report of articles that
  appear only in abstracts was dropped because V2 does not measure it.
- **Lotka's law.** After an independent review on 8 October 2026, the goodness of fit is tested with a
  parametric bootstrap p-value (1,000 samples, xmin = 1) instead of an asymptotic critical value.
- **V5 second coder.** After the same review, a second coder, blind to the first coder's judgements,
  independently judged 40 of the 96 comparisons: all 15 whose ORCID differs from that of the group's first
  identity and 25 drawn at random (seed 20261008) from the rest. Script 15 builds the sheet and scores the
  agreement.
- **V2 citation-based recall.** After the same review, a check that starts outside the retrieved set was
  added (9 October 2026). The pool is every TR Dizin record that is linked by citation to the 926 relevant
  articles and is not in the frozen set (2,193 records); 200 were drawn at random (seed 20261009), and the
  191 journal articles up to 2025 among them were coded by one coder with the V2 definition. The check has
  no threshold. Script 16 draws the sample, builds the sheet and scores it.
  The coder reviewed the sheet once more after the first scoring and changed 15 judgements from relevant to
  not relevant (37 relevant in the first pass, 22 in the final count; recall among citation-linked relevant
  articles about 55% and 67%). The paper reports the final count; both sheets are
  kept (`kodlama_kacirilan_ilk_gecis.xlsx` is the first pass).
