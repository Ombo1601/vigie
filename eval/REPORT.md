# Event grouping: evaluation report (Phase 0)

This report is a template. `eval/run_eval.py --report eval/REPORT.md` fills the
block between the two `eval:results` markers and leaves everything else alone.
It holds measures, counts and pair ids only: no headline, excerpt or other
publisher text, ever (R8, and the public repository carries none).

## What is measured

The question is narrow. Given two collected items, do they report the **same
real-world happening**? That is what an event-first edition groups.

- **Gold set.** Item pairs labelled `same_event`, `related` (same storyline,
  a distinct happening) or `different`, by two labellers plus adjudication.
  Ids only. It is split into dev and test by a hash of the pair id.
- **Protocol.** A scorer may be fitted and its threshold chosen on **dev
  only**. The figures that count are on **test**.
- **Strict metric.** `same_event` is the only positive class: merging a
  `related` pair is a false merge. How often each scorer merges `related` and
  `different` pairs is shown separately, and a lenient metric leaves `related`
  pairs out.
- **By language pair.** fr-fr, fr-en and en-en, because grouping French and
  English coverage of one event is the point.
- **PR points and average precision** for graded scorers; binary scorers have
  one operating point.
- **Clusters.** B-cubed against the transitive closure of the gold
  `same_event` pairs, plus the labelled pairs scored through the clusterer.
- **Uncertainty.** A 95% bootstrap interval on test F1 (1000 resamples, fixed
  seed). With about 200 test pairs, differences inside the interval are noise.

## Scorers

| name | what it is |
|---|---|
| `baseline` | the live rule, called from `scripts/cluster_issues.py`: named scars, then `features_match` (headline overlap, FR/EN guard); clusters with the live `event_buckets` |
| `baseline-features` | `features_match` / `same_event` alone, no scars |
| `graded-prior` | `eval/graded_matcher.py` with hand-set prior weights; threshold fitted on dev |
| `graded` | the same features, logistic weights refitted on dev (L2 toward the prior); threshold fitted on dev |

## Known limits of the gold set

- Pairs were **sampled through a similarity screen** (hard negatives, binned
  positives, a French/English pool), so the class balance is not the natural
  one: almost every random pair of items is `different`. Precision on the
  live stream will be higher than here for easy negatives and is unknown for
  the long tail. Read these numbers as relative, not absolute.
- There are **no en-en pairs** in gold_v0, and few fr-en ones (about 50 on
  test): the fr-en figures carry wide intervals.
- No pair comes from the same outlet; within-outlet follow-ups are untested.
- B-cubed precision counts every unlabelled pair inside a predicted group as an
  error, so it is a lower bound.

## Reading the first run (hand-written, 2026-10-06, gold_v0)

These notes are not regenerated; rerun and re-read when the gold set or a
scorer changes.

- **The live rule is precise and nearly blind.** On test it finds about one
  same-event pair in five (recall 0.21, F1 0.33) and **no** French/English
  pair through its own overlap rule: every fr-en pair it groups comes from a
  named scar. Its few false merges are `related` pairs inside a scar.
- **The graded matcher finds the events.** Strict F1 0.77 to 0.78 on test,
  recall 0.83 to 0.90, fr-en F1 about 0.8 on 17 positive pairs. Lenient F1
  (related left out) is about 0.9 to 0.93: it almost never merges unrelated
  stories (3.5 to 5.3% of `different` pairs).
- **Its failure is the storyline.** Most false merges are `related` pairs: a
  public report and the reactions to it, two previews of one sporting event,
  two angles on one diplomatic file, a digest and one of its items. A quarter
  to a third of `related` pairs cross the threshold. This is the boundary the
  product must draw (event versus storyline), and these features cannot draw
  it alone; a storyline layer above events is the natural home for them.
- **Cross-language merges lean on time.** The refit gives French/English
  pairs a positive offset; some fr-en false merges share little more than an
  hour and a country. A fr-en pair should need a shared name, place or number.
- **Fitting moved ranking more than the operating point.** Average precision
  rises from 0.84 (prior) to 0.90 (refit), but F1 at the dev threshold is
  the same within the bootstrap interval (about plus or minus 0.07).
- **Clusters.** Induced pairwise F1 is 0.81 for the refit matcher against
  0.29 for the live clusterer. B-cubed favours the live clusterer (0.89 vs
  0.86) because most gold items are singletons, which a clusterer that groups
  almost nothing gets right by default; with partial labels, B-cubed
  precision is a lower bound.
- **What this does not show.** Behaviour on the natural stream (the gold
  pairs were screened), en-en pairs (none), same-outlet follow-ups (none).

## Results

<!-- eval:results:begin -->

Filled by `eval/run_eval.py` from gold `gold_v0` (sha256 `52c2d0048643dc3a`): 520 of 520 pairs resolved in the private store (0 missing); labeller kappa 0.8923, raw agreement 0.9288. IDF corpus: 2139 unlabelled items. No publisher text below: ids, counts and measures only.

### Headline: TEST split, strict (same_event vs related + different)

| scorer | threshold (dev) | precision | recall | F1 | F1 95% CI | avg precision | related merged | different merged |
|---|---|---|---|---|---|---|---|---|
| baseline | 0.500 | 0.750 | 0.211 | 0.330 | 0.194 to 0.457 | binary | 0.060 | 0.000 |
| baseline-features | 0.500 | 1.000 | 0.183 | 0.310 | 0.169 to 0.435 | binary | 0.000 | 0.000 |
| graded-prior | 0.448 | 0.688 | 0.901 | 0.780 | 0.706 to 0.846 | 0.837 | 0.313 | 0.053 |
| graded | 0.399 | 0.720 | 0.831 | 0.771 | 0.690 to 0.841 | 0.904 | 0.253 | 0.035 |

Test label counts: same_event 71, related 83, different 57

### Lenient (related pairs left out) and DEV for reference

| scorer | test lenient P | test lenient R | test lenient F1 | dev strict P | dev strict R | dev strict F1 |
|---|---|---|---|---|---|---|
| baseline | 1.000 | 0.211 | 0.349 | 0.810 | 0.167 | 0.276 |
| baseline-features | 1.000 | 0.183 | 0.310 | 1.000 | 0.127 | 0.226 |
| graded-prior | 0.955 | 0.901 | 0.928 | 0.679 | 0.873 | 0.764 |
| graded | 0.967 | 0.831 | 0.894 | 0.808 | 0.824 | 0.816 |

### TEST by language pair (strict)

| scorer | pair | n | same_event | TP | FP | FN | related merged | precision | recall | F1 |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | fr-fr | 162 | 54 | 13 | 4 | 41 | 4 | 0.765 | 0.241 | 0.366 |
| baseline | fr-en | 49 | 17 | 2 | 1 | 15 | 1 | 0.667 | 0.118 | 0.200 |
| baseline | en-en | 0 | 0 | - | - | - | - | n/a | n/a | n/a |
| baseline-features | fr-fr | 162 | 54 | 13 | 0 | 41 | 0 | 1.000 | 0.241 | 0.388 |
| baseline-features | fr-en | 49 | 17 | 0 | 0 | 17 | 0 | 0.000 | 0.000 | 0.000 |
| baseline-features | en-en | 0 | 0 | - | - | - | - | n/a | n/a | n/a |
| graded-prior | fr-fr | 162 | 54 | 50 | 26 | 4 | 24 | 0.658 | 0.926 | 0.769 |
| graded-prior | fr-en | 49 | 17 | 14 | 3 | 3 | 2 | 0.824 | 0.824 | 0.824 |
| graded-prior | en-en | 0 | 0 | - | - | - | - | n/a | n/a | n/a |
| graded | fr-fr | 162 | 54 | 44 | 17 | 10 | 17 | 0.721 | 0.815 | 0.765 |
| graded | fr-en | 49 | 17 | 15 | 6 | 2 | 4 | 0.714 | 0.882 | 0.789 |
| graded | en-en | 0 | 0 | - | - | - | - | n/a | n/a | n/a |

### TEST clusters

B-cubed compares predicted groups with the transitive closure of the gold same_event pairs over the items of the split. The gold set labels sampled pairs, not every pair, so a predicted group that joins two unlabelled items is counted against the scorer: read B-cubed precision as a lower bound. Induced pairwise scores the labelled pairs by whether the clusterer put both items in one group.

| scorer | items | predicted groups (largest) | gold groups (largest) | B3 precision | B3 recall | B3 F1 | induced P | induced R | induced F1 | closure conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 347 | 27 (8) | 52 (4) | 0.929 | 0.848 | 0.887 | 0.722 | 0.183 | 0.292 | 0 |
| baseline-features | 347 | 24 (2) | 52 (4) | 0.968 | 0.842 | 0.901 | 1.000 | 0.155 | 0.268 | 0 |
| graded-prior | 347 | 77 (11) | 52 (4) | 0.725 | 0.972 | 0.831 | 0.698 | 0.845 | 0.764 | 0 |
| graded | 347 | 73 (8) | 52 (4) | 0.778 | 0.970 | 0.864 | 0.797 | 0.831 | 0.814 | 0 |

### TEST precision-recall points

- baseline: t=1.000 P=0.750 R=0.211; t=0.000 P=0.336 R=1.000
- baseline-features: t=1.000 P=1.000 R=0.183; t=0.000 P=0.336 R=1.000
- graded-prior: t=1.000 P=1.000 R=0.014; t=0.973 P=0.950 R=0.268; t=0.906 P=0.821 R=0.451; t=0.778 P=0.814 R=0.676; t=0.616 P=0.731 R=0.803; t=0.390 P=0.670 R=0.915; t=0.271 P=0.586 R=0.958; t=0.211 P=0.519 R=0.986; t=0.136 P=0.461 R=1.000; t=0.084 P=0.410 R=1.000; t=0.022 P=0.370 R=1.000; t=0.001 P=0.336 R=1.000
- graded: t=1.000 P=1.000 R=0.014; t=0.969 P=1.000 R=0.282; t=0.825 P=0.949 R=0.521; t=0.664 P=0.845 R=0.690; t=0.435 P=0.766 R=0.831; t=0.222 P=0.680 R=0.930; t=0.138 P=0.586 R=0.958; t=0.098 P=0.526 R=1.000; t=0.055 P=0.461 R=1.000; t=0.030 P=0.410 R=1.000; t=0.015 P=0.370 R=1.000; t=0.000 P=0.336 R=1.000

### Fitted weights (log-odds per unit of evidence)

- graded-prior: bias -4.00, time +1.50, far -2.00, numbers +1.00, dates +0.50, names +2.00, place +1.00, conflict -1.00, terms +1.00, event +0.50, event_conflict -0.50, tokens +4.00, title +2.00, chars +3.00, cross +0.00, cross_tokens +2.00, cross_chars -1.00
- graded: bias -5.38, time +3.07, far -2.02, numbers +1.57, dates +0.93, names +0.56, place +0.33, conflict -1.00, terms +0.73, event +0.47, event_conflict -0.73, tokens +4.96, title +1.87, chars +4.74, cross +1.20, cross_tokens +2.52, cross_chars -0.14

### TEST errors (pair ids only; strongest components)

- baseline: 5 false merges (by label: related 5; by language pair: fr-en 1, fr-fr 4), 56 missed (by language pair: fr-en 15, fr-fr 41)
  - `p0ca15d3039` false_merge gold=related fr-en score=1.000
  - `p2322440b19` false_merge gold=related fr-fr score=1.000
  - `p567024c8cc` false_merge gold=related fr-fr score=1.000
  - `p60fef7e3fa` false_merge gold=related fr-fr score=1.000
  - `pc65bf39a81` false_merge gold=related fr-fr score=1.000
  - `p019c07f8da` missed gold=same_event fr-fr score=0.000
  - `p064c6dea0e` missed gold=same_event fr-fr score=0.000
  - `p0725780b66` missed gold=same_event fr-fr score=0.000
  - `p14a42b8c33` missed gold=same_event fr-en score=0.000
  - `p161f62e7aa` missed gold=same_event fr-fr score=0.000
- baseline-features: 0 false merges (by label: none; by language pair: none), 58 missed (by language pair: fr-en 17, fr-fr 41)
  - `p019c07f8da` missed gold=same_event fr-fr score=0.000
  - `p064c6dea0e` missed gold=same_event fr-fr score=0.000
  - `p0725780b66` missed gold=same_event fr-fr score=0.000
  - `p14a42b8c33` missed gold=same_event fr-en score=0.000
  - `p161f62e7aa` missed gold=same_event fr-fr score=0.000
- graded-prior: 29 false merges (by label: different 3, related 26; by language pair: fr-en 3, fr-fr 26), 7 missed (by language pair: fr-en 3, fr-fr 4)
  - `p7bca63ab0c` false_merge gold=related fr-fr score=0.980; names, chars, title
  - `p2322440b19` false_merge gold=related fr-fr score=0.956; names, title, chars
  - `pc65bf39a81` false_merge gold=related fr-fr score=0.945; names, title, chars
  - `p567024c8cc` false_merge gold=related fr-fr score=0.933; names, title, chars
  - `p60fef7e3fa` false_merge gold=related fr-fr score=0.926; time, chars, numbers
  - `p7988132295` missed gold=same_event fr-en score=0.136; time, chars, title
  - `p9dabf753bf` missed gold=same_event fr-fr score=0.226; chars, title, event
  - `p6d3a42be05` missed gold=same_event fr-en score=0.252; time, title, terms
  - `p76f8927638` missed gold=same_event fr-fr score=0.277; terms, chars, title
  - `p3f1d927957` missed gold=same_event fr-fr score=0.320; time, title, names
- graded: 23 false merges (by label: different 2, related 21; by language pair: fr-en 6, fr-fr 17), 12 missed (by language pair: fr-en 2, fr-fr 10)
  - `p60fef7e3fa` false_merge gold=related fr-fr score=0.912; time, chars, numbers
  - `p2322440b19` false_merge gold=related fr-fr score=0.871; chars, time, title
  - `p7bca63ab0c` false_merge gold=related fr-fr score=0.771; chars, tokens, title
  - `p9b9037f6a3` false_merge gold=related fr-fr score=0.766; time, chars, tokens
  - `pc65bf39a81` false_merge gold=related fr-fr score=0.761; chars, tokens, title
  - `p9dabf753bf` missed gold=same_event fr-fr score=0.107; chars, title, time
  - `p16dc7b7f7c` missed gold=same_event fr-fr score=0.112; chars, title, tokens
  - `p76f8927638` missed gold=same_event fr-fr score=0.129; chars, title, terms
  - `pdd19cf5684` missed gold=same_event fr-fr score=0.155; chars, time, title
  - `pc09ab82f45` missed gold=same_event fr-fr score=0.204; time, chars, title

<!-- eval:results:end -->
