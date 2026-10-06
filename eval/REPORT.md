# Event grouping: evaluation report

This report is a template. `eval/run_eval.py --report eval/REPORT.md` fills the
block between the two `eval:results` markers and leaves everything else alone.
It holds measures, counts and pair ids only: no headline, excerpt or other
publisher text, ever (R8, and the public repository carries none).

## Read this first

1. **The gold labels are model-generated.** gold_v0 was labelled by one
   language model in three passes: two labellers and an adjudicator that
   settled their 79 disagreements. Its kappa (0.89) is that model's
   consistency with itself, **not agreement between people**. No human has
   checked any label; 8 pairs the adjudicator could not settle await the
   founder. The gold file records this in `labelers_provenance`; the harness
   prints it on every run and writes it at the top of the results block, so
   the caveat travels with every number. Every figure here is
   **model-labelled and relative**: good for comparing designs on the same
   labels, not a measured real-world precision.
2. **The pairs were sampled through a lexical screen** of the same kind as
   the matcher's features (word, name, place and number overlap). A
   same-event pair the screen could not see is not in the gold set, so every
   recall figure is an **upper bound on screen-findable positives**. The
   French/English pool additionally required a shared name, place or number,
   which is close to what the FR/EN guard asks: fr-en recall under the guard
   is partly circular.
3. **B-cubed is uninformative here.** 229 of the 347 test items are
   singletons in the gold closure: a clusterer that groups nothing scores a
   B-cubed F1 of 0.895, better than every real clusterer. The results block
   shows that `all-singletons` reference row; the cluster-level figure is the
   **induced pairwise F1** (every labelled pair scored by whether the
   clusterer put both items in one group).
4. **The pair split leaks items.** gold_v0 splits pairs by a hash of the
   pair id, so 175 of its 634 items appear in both dev and test. A second,
   **leakage-free protocol** splits by connected component of the item graph
   (no item on both sides), refits and re-chooses the tiers on that dev, and
   reports that test.

## What is measured

The question is narrow. Given two collected items, do they report the **same
real-world happening**? That is what an event-first edition groups.

- **Gold set.** Item pairs labelled `same_event`, `related` (same storyline,
  a distinct happening) or `different`. Ids only. Split into dev and test by
  a hash of the pair id (and, for the leakage-free check, by component).
- **Protocol.** A scorer may be fitted and its thresholds chosen on **dev
  only**. The figures that count are on **test**.
- **Strict metric.** `same_event` is the only positive class: merging a
  `related` pair is a false merge. Merge rates per label and a lenient
  metric (related left out) are shown beside it.
- **Tiers** (docs/EVENTS.md section 4): `certain` = lowest dev threshold
  whose dev precision is >= 0.95 there and at every higher threshold;
  `probable` = median of the dev F1 plateau (within 0.01 of the best F1; the
  single arg-max is shown for contrast); `possible` = lowest dev threshold at
  which >= 90% of the dev pairs at or above it are same_event or related.
  `certain` and `probable` may group articles; `possible` is only ever shown
  as a neighbour.
- **By language pair.** fr-fr, fr-en and en-en.
- **Uncertainty.** Wilson 95% intervals on tier precision; a bootstrap over
  connected components (pairs that share items resample together) for the
  certain tier; a pair bootstrap on strict F1. With a few hundred pairs,
  differences inside an interval are noise.

## Scorers

| name | what it is |
|---|---|
| `baseline` | the live rule, called from `scripts/cluster_issues.py`: named scars, then `features_match`; clusters with the live `event_buckets` |
| `baseline-features` | `features_match` / `same_event` alone, no scars |
| `graded-prior` | the production features (`scripts/event_match.py`) with hand-set prior weights; threshold fitted on dev; no FR/EN guard |
| `graded` | the same features, logistic weights refitted on dev (L2 toward the prior); no guard |
| `graded-guard` | refit on dev + FR/EN guard + tiers chosen on dev: the procedure that produced the shipped constants |
| `shipped` | `scripts/event_match.py` exactly as it ships: frozen `WEIGHTS` and `THRESHOLDS`, FR/EN guard, sticky clusterer `attach()` |

`eval/graded_matcher.py` is an adapter over `scripts/event_match.py`, and
`eval/lexicon_fr_en.py` re-exports `scripts/event_lexicon.py`: the harness
measures the code that ships. `eval/shadow_live.py` runs the shipped matcher
on the private stores and prints counts and timings only.

## Known limits of the gold set

- **Model-labelled** (see "Read this first"): no human agreement measured.
- **Screened sampling**: the class balance is not the natural one (almost
  every random pair of items is `different`) and recall is an upper bound.
  Precision on the live stream is unknown for the long tail.
- **No en-en pairs** in gold_v0, and few fr-en ones (49 on test, 17
  positive): fr-en figures carry wide intervals.
- **No pair from the same outlet**: within-outlet follow-ups are untested.
- **Items recur** across pairs (up to three times each): pair-level
  intervals are somewhat optimistic; the component bootstrap and the
  leakage-free split are the honest checks.
- **One giant component**: 175 of the 520 pairs form one connected
  component, so the leakage-free split is lopsided (that component lands on
  dev).
- B-cubed precision counts every unlabelled pair inside a predicted group as
  an error, so it is a lower bound (and see point 3 above).

## Reading the Phase 1 run (hand-written, 2026-10-06, gold_v0)

These notes are not regenerated; rerun and re-read when the gold set or a
scorer changes.

- **Gate (certain-tier precision >= 0.95 on test): not met.** On the pair
  split the shipped `certain` tier is right on 33 of 34 test pairs (0.971),
  but the 95% lower bound is 0.85 (Wilson; 0.90 by component bootstrap). On
  the leakage-free component split it is 38 of 42 (0.905). And the labels are
  one model's. What would open it: founder review of gold_v0 (the 8 uncertain
  pairs first), then a human-labelled sample drawn from the natural stream at
  tiers `certain` and `probable`; about 73 `certain` pairs without an error
  (110 with one) are needed before a 95% lower bound reaches 0.95.
- **The grouped tier is a storyline problem, as in Phase 0.** At `probable`
  and above: precision 0.740, recall 0.803 on test, and most false merges are
  `related` pairs. Features cannot draw the event/storyline line alone.
- **FR/EN guard.** On dev it raised fr-en grouped precision from 0.833 to
  0.947 (recall 0.80 to 0.72); on test from 0.714 to 0.812 (recall 0.882 to
  0.765). The rarity rule for names (at most 1% of the corpus) was chosen on
  dev among 0.5 / 1 / 2%, by highest dev precision with dev recall within 0.1
  of the unguarded matcher; the "number >= 10" floor is a prior (dev could not
  tell it from "any number"). It fails 6 same_event fr-en pairs across dev and
  test: the guard is not purely circular, it costs recall.
- **Merge rule.** The bare "two cross pairs at probable" rule and the shipped
  rule (plus an average cross score at probable) have the same dev induced F1
  (0.812), the shipped one with higher dev precision (0.842 vs 0.800). On an
  unlabelled replay of 49 stamped editions the bare rule glued unrelated
  stories into a 100-member event; the shipped rule's largest is 11.
- **Clusters.** Induced pairwise F1 on test: 0.783 for the shipped sticky
  clusterer, against 0.292 for the live dossier clusterer. The all-singletons
  reference beats every clusterer on B-cubed, which is why B-cubed is not the
  figure.
- **Blocking and speed.** Blocking keeps 172 of 173 same-event gold pairs (the
  lost one is at tier `possible`) while scoring about 1 pair in 10. The live
  edition of 2026-10-06 (325 items) is matched and clustered in about 0.6 s;
  the 49-edition replay averages about 2.2 s per edition (4.4 s at most) with
  windows of up to 1,566 items (one core).
- **Shadow on real stores (`eval/shadow_live.py`, counts only, unlabelled).**
  Live edition 2026-10-06T00:03Z, 325 items: 268 events, 42 with two or more
  members (99 items), 23 with two or more institutions, 7 bilingual; largest
  7; no merge; 17 fr-en pairs above `probable` capped by the guard. Replay of
  the 49 stamped editions (2,139 unique items) as editions, sticky across
  them: 1,595 events, 311 with two or more members (855 items, 40%), 199
  multi-institution, 47 bilingual; largest 11; 11 merges; **0 memberships
  moved**. For scale, the live dossier rule groups 6.4% of items into 51
  multi-institution groups (docs/EVENTS.md section 1). Reach is not
  precision: these groups are unlabelled.

## Phase 0 reading (hand-written, 2026-10-06, kept for the record)

Written before the caveats above were raised: its figures are model-labelled
too, and its B-cubed remark is superseded by point 3.

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

Filled by `eval/run_eval.py` from gold `gold_v0` (sha256 `44fd3632049cfaab`): 520 of 520 pairs resolved in the private store (0 missing). IDF corpus: 2139 unlabelled items. No publisher text below: ids, counts and measures only.

**Who labelled the gold set:** model-generated: one LLM in three passes (labeller L1, labeller L2, and an adjudicator that settled their 79 disagreements); no human has checked any label; 8 pairs flagged uncertain await the founder's review. Labeller kappa 0.8923, raw agreement 0.9288: with one model in every role this measures the model's consistency with itself, not agreement between people. Every figure below is **model-labelled and relative**: it compares scorers on the same labels; it is not a measured real-world precision.

### Gate: certain-tier precision on TEST (shipped matcher)

- certain tier: 33 of 34 pairs are same_event, precision 0.971 (Wilson 95% 0.851 to 0.995; component bootstrap 95% 0.900 to 1.000); recall 0.465.
- gate (precision >= 0.95): **met on the point estimate**; the lower confidence bound is 0.851, below 0.95: not met with confidence.

### Tiers by language pair and split

Bands: `certain` (tier certain), `grouped` (certain or probable: what may group articles), `probable-band` (probable only), `possible-band` (possible only: shown as neighbours, never merged). Recall is against every same_event pair of the slice. CI: Wilson 95% on TEST precision (pairs share items, so true intervals are somewhat wider).

| scorer | band | pairs | split | n | same_event | predicted | TP | related | different | precision | TEST precision 95% CI | recall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| shipped | certain | all | dev | 309 | 102 | 45 | 43 | 2 | 0 | 0.956 | - | 0.422 |
| shipped | certain | fr-fr | dev | 244 | 77 | 34 | 32 | 2 | 0 | 0.941 | - | 0.416 |
| shipped | certain | fr-en | dev | 65 | 25 | 11 | 11 | 0 | 0 | 1.000 | - | 0.440 |
| shipped | grouped | all | dev | 309 | 102 | 99 | 82 | 16 | 1 | 0.828 | - | 0.804 |
| shipped | grouped | fr-fr | dev | 244 | 77 | 80 | 64 | 16 | 0 | 0.800 | - | 0.831 |
| shipped | grouped | fr-en | dev | 65 | 25 | 19 | 18 | 0 | 1 | 0.947 | - | 0.720 |
| shipped | probable-band | all | dev | 309 | 102 | 54 | 39 | 14 | 1 | 0.722 | - | 0.382 |
| shipped | probable-band | fr-fr | dev | 244 | 77 | 46 | 32 | 14 | 0 | 0.696 | - | 0.416 |
| shipped | probable-band | fr-en | dev | 65 | 25 | 8 | 7 | 0 | 1 | 0.875 | - | 0.280 |
| shipped | possible-band | all | dev | 309 | 102 | 89 | 19 | 53 | 17 | 0.213 | - | 0.186 |
| shipped | possible-band | fr-fr | dev | 244 | 77 | 63 | 12 | 41 | 10 | 0.190 | - | 0.156 |
| shipped | possible-band | fr-en | dev | 65 | 25 | 26 | 7 | 12 | 7 | 0.269 | - | 0.280 |
| shipped | certain | all | test | 211 | 71 | 34 | 33 | 1 | 0 | 0.971 | 0.851 to 0.995 | 0.465 |
| shipped | certain | fr-fr | test | 162 | 54 | 29 | 28 | 1 | 0 | 0.966 | 0.828 to 0.994 | 0.519 |
| shipped | certain | fr-en | test | 49 | 17 | 5 | 5 | 0 | 0 | 1.000 | 0.566 to 1.000 | 0.294 |
| shipped | grouped | all | test | 211 | 71 | 77 | 57 | 19 | 1 | 0.740 | 0.633 to 0.825 | 0.803 |
| shipped | grouped | fr-fr | test | 162 | 54 | 61 | 44 | 17 | 0 | 0.721 | 0.598 to 0.818 | 0.815 |
| shipped | grouped | fr-en | test | 49 | 17 | 16 | 13 | 2 | 1 | 0.812 | 0.570 to 0.934 | 0.765 |
| shipped | probable-band | all | test | 211 | 71 | 43 | 24 | 18 | 1 | 0.558 | 0.411 to 0.696 | 0.338 |
| shipped | probable-band | fr-fr | test | 162 | 54 | 32 | 16 | 16 | 0 | 0.500 | 0.336 to 0.664 | 0.296 |
| shipped | probable-band | fr-en | test | 49 | 17 | 11 | 8 | 2 | 1 | 0.727 | 0.434 to 0.903 | 0.471 |
| shipped | possible-band | all | test | 211 | 71 | 57 | 14 | 34 | 9 | 0.246 | 0.152 to 0.371 | 0.197 |
| shipped | possible-band | fr-fr | test | 162 | 54 | 36 | 10 | 22 | 4 | 0.278 | 0.158 to 0.440 | 0.185 |
| shipped | possible-band | fr-en | test | 49 | 17 | 21 | 4 | 12 | 5 | 0.190 | 0.077 to 0.400 | 0.235 |

Thresholds (probable is also the strict operating point):

- graded-prior: certain 0.9587, probable 0.4480, possible 0.1582 (chosen on dev); single dev F1 arg-max 0.4434
- graded: certain 0.8714, probable 0.3985, possible 0.0984 (chosen on dev); single dev F1 arg-max 0.4630
- graded-guard: certain 0.8714, probable 0.3985, possible 0.0984 (chosen on dev); single dev F1 arg-max 0.4630
- shipped: certain 0.8713, probable 0.3985, possible 0.0984 (frozen in scripts/event_match.py); dev would choose certain 0.8714, probable 0.3985, possible 0.0984; single dev F1 arg-max 0.4630

### FR/EN guard (shipped thresholds, with and without the guard)

A French/English pair must share a specific place, a rare name (at most 1% of the corpus) or a number >= 10 that is not a year, or it can never be grouped.

| split | pairs | guard fails (same_event / related / different) | grouped without guard: predicted, P, R | grouped with guard: predicted, P, R |
|---|---|---|---|---|
| dev | fr-en | 2 / 13 / 21 | 24, 0.833, 0.800 | 19, 0.947, 0.720 |
| dev | all | 2 / 13 / 21 | 104, 0.808, 0.824 | 99, 0.828, 0.804 |
| test | fr-en | 4 / 12 / 10 | 21, 0.714, 0.882 | 16, 0.812, 0.765 |
| test | all | 4 / 12 / 10 | 82, 0.720, 0.831 | 77, 0.740, 0.803 |

### Headline: TEST split, strict (same_event vs related + different)

| scorer | threshold (dev) | precision | recall | F1 | F1 95% CI | avg precision | related merged | different merged |
|---|---|---|---|---|---|---|---|---|
| baseline | 0.500 | 0.750 | 0.211 | 0.330 | 0.194 to 0.457 | binary | 0.060 | 0.000 |
| baseline-features | 0.500 | 1.000 | 0.183 | 0.310 | 0.169 to 0.435 | binary | 0.000 | 0.000 |
| graded-prior | 0.448 | 0.688 | 0.901 | 0.780 | 0.706 to 0.846 | 0.837 | 0.313 | 0.053 |
| graded | 0.399 | 0.720 | 0.831 | 0.771 | 0.690 to 0.841 | 0.904 | 0.253 | 0.035 |
| graded-guard | 0.399 | 0.740 | 0.803 | 0.770 | 0.682 to 0.840 | 0.883 | 0.229 | 0.018 |
| shipped | 0.399 | 0.740 | 0.803 | 0.770 | 0.682 to 0.840 | 0.883 | 0.229 | 0.018 |

Test label counts: same_event 71, related 83, different 57

### Lenient (related pairs left out) and DEV for reference

| scorer | test lenient P | test lenient R | test lenient F1 | dev strict P | dev strict R | dev strict F1 |
|---|---|---|---|---|---|---|
| baseline | 1.000 | 0.211 | 0.349 | 0.810 | 0.167 | 0.276 |
| baseline-features | 1.000 | 0.183 | 0.310 | 1.000 | 0.127 | 0.226 |
| graded-prior | 0.955 | 0.901 | 0.928 | 0.679 | 0.873 | 0.764 |
| graded | 0.967 | 0.831 | 0.894 | 0.808 | 0.824 | 0.816 |
| graded-guard | 0.983 | 0.803 | 0.884 | 0.828 | 0.804 | 0.816 |
| shipped | 0.983 | 0.803 | 0.884 | 0.828 | 0.804 | 0.816 |

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
| graded-guard | fr-fr | 162 | 54 | 44 | 17 | 10 | 17 | 0.721 | 0.815 | 0.765 |
| graded-guard | fr-en | 49 | 17 | 13 | 3 | 4 | 2 | 0.812 | 0.765 | 0.788 |
| graded-guard | en-en | 0 | 0 | - | - | - | - | n/a | n/a | n/a |
| shipped | fr-fr | 162 | 54 | 44 | 17 | 10 | 17 | 0.721 | 0.815 | 0.765 |
| shipped | fr-en | 49 | 17 | 13 | 3 | 4 | 2 | 0.812 | 0.765 | 0.788 |
| shipped | en-en | 0 | 0 | - | - | - | - | n/a | n/a | n/a |

### TEST clusters

**Induced pairwise F1 is the cluster-level figure**: it scores every labelled pair by whether the clusterer put both items in one group (graded scorers use the production sticky clusterer, `event_match.attach`). B-cubed is shown for completeness but is **uninformative here**: 229 of 347 test items are singletons in the gold closure, so a clusterer that groups nothing scores well (the `all-singletons` row), and with partial labels B-cubed precision counts every unlabelled pair in a group as an error.

| scorer | items | predicted groups (largest) | gold groups (largest) | induced P | induced R | induced F1 | B3 precision | B3 recall | B3 F1 | closure conflicts |
|---|---|---|---|---|---|---|---|---|---|---|
| baseline | 347 | 27 (8) | 52 (4) | 0.722 | 0.183 | 0.292 | 0.929 | 0.848 | 0.887 | 0 |
| baseline-features | 347 | 24 (2) | 52 (4) | 1.000 | 0.155 | 0.268 | 0.968 | 0.842 | 0.901 | 0 |
| graded-prior | 347 | 83 (8) | 52 (4) | 0.699 | 0.817 | 0.753 | 0.727 | 0.966 | 0.830 | 0 |
| graded | 347 | 74 (9) | 52 (4) | 0.770 | 0.803 | 0.786 | 0.764 | 0.964 | 0.853 | 0 |
| graded-guard | 347 | 73 (8) | 52 (4) | 0.778 | 0.789 | 0.783 | 0.782 | 0.962 | 0.863 | 0 |
| shipped | 347 | 73 (8) | 52 (4) | 0.778 | 0.789 | 0.783 | 0.782 | 0.962 | 0.863 | 0 |
| all-singletons (reference) | 347 | 0 (1) | 52 (4) | n/a | 0.000 | n/a | 1.000 | 0.810 | 0.895 | 0 |

### Candidate blocking (shipped)

Over the 634 items of the gold pairs (about one production window), blocking keeps 20995 of 200661 pairs. Gold pairs kept: same_event 172 of 173, related 169 of 199, different 78 of 148. Lost pairs by label/tier: different/none 62, different/possible 8, related/none 18, related/possible 11, related/probable 1, same_event/possible 1.

### Leakage-free split by connected component of items

The 159 connected components of the item graph go wholly to dev or test by a hash of their item ids (no item on both sides). Largest component: 175 pairs, on dev. Pairs: dev 348, test 172; test labels same_event 69, related 57, different 46. Weights refit and tiers chosen on component-dev only (the shipped procedure).

| scorer | band | pairs | split | n | same_event | predicted | TP | related | different | precision | TEST precision 95% CI | recall |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| component split | certain | all | test | 172 | 69 | 42 | 38 | 4 | 0 | 0.905 | 0.779 to 0.962 | 0.551 |
| component split | certain | fr-fr | test | 136 | 52 | 32 | 28 | 4 | 0 | 0.875 | 0.719 to 0.950 | 0.538 |
| component split | certain | fr-en | test | 36 | 17 | 10 | 10 | 0 | 0 | 1.000 | 0.722 to 1.000 | 0.588 |
| component split | grouped | all | test | 172 | 69 | 88 | 62 | 23 | 3 | 0.705 | 0.602 to 0.790 | 0.899 |
| component split | grouped | fr-fr | test | 136 | 52 | 64 | 45 | 19 | 0 | 0.703 | 0.582 to 0.801 | 0.865 |
| component split | grouped | fr-en | test | 36 | 17 | 24 | 17 | 4 | 3 | 0.708 | 0.508 to 0.851 | 1.000 |
| component split | probable-band | all | test | 172 | 69 | 46 | 24 | 19 | 3 | 0.522 | 0.381 to 0.659 | 0.348 |
| component split | probable-band | fr-fr | test | 136 | 52 | 32 | 17 | 15 | 0 | 0.531 | 0.364 to 0.691 | 0.327 |
| component split | probable-band | fr-en | test | 36 | 17 | 14 | 7 | 4 | 3 | 0.500 | 0.268 to 0.732 | 0.412 |
| component split | possible-band | all | test | 172 | 69 | 28 | 6 | 18 | 4 | 0.214 | 0.102 to 0.395 | 0.087 |
| component split | possible-band | fr-fr | test | 136 | 52 | 25 | 6 | 17 | 2 | 0.240 | 0.115 to 0.434 | 0.115 |
| component split | possible-band | fr-en | test | 36 | 17 | 3 | 0 | 1 | 2 | 0.000 | 0.000 to 0.561 | 0.000 |

Component-dev tiers: certain 0.8433, probable 0.3126, possible 0.0917. Component-test strict at probable: P 0.705, R 0.899, F1 0.790; induced pairwise F1 0.800; certain-tier component bootstrap 95% 0.750 to 1.000.

### TEST precision-recall points

- baseline: t=1.000 P=0.750 R=0.211; t=0.000 P=0.336 R=1.000
- baseline-features: t=1.000 P=1.000 R=0.183; t=0.000 P=0.336 R=1.000
- graded-prior: t=1.000 P=1.000 R=0.014; t=0.973 P=0.950 R=0.268; t=0.906 P=0.821 R=0.451; t=0.778 P=0.814 R=0.676; t=0.616 P=0.731 R=0.803; t=0.390 P=0.670 R=0.915; t=0.271 P=0.586 R=0.958; t=0.211 P=0.519 R=0.986; t=0.136 P=0.461 R=1.000; t=0.084 P=0.410 R=1.000; t=0.022 P=0.370 R=1.000; t=0.001 P=0.336 R=1.000
- graded: t=1.000 P=1.000 R=0.014; t=0.969 P=1.000 R=0.282; t=0.825 P=0.949 R=0.521; t=0.664 P=0.845 R=0.690; t=0.435 P=0.766 R=0.831; t=0.222 P=0.680 R=0.930; t=0.138 P=0.586 R=0.958; t=0.098 P=0.526 R=1.000; t=0.055 P=0.461 R=1.000; t=0.030 P=0.410 R=1.000; t=0.015 P=0.370 R=1.000; t=0.000 P=0.336 R=1.000
- graded-guard: t=1.000 P=1.000 R=0.014; t=0.984 P=1.000 R=0.254; t=0.890 P=0.971 R=0.465; t=0.758 P=0.902 R=0.648; t=0.478 P=0.838 R=0.803; t=0.283 P=0.721 R=0.873; t=0.139 P=0.627 R=0.901; t=0.097 P=0.563 R=0.944; t=0.050 P=0.493 R=0.944; t=0.026 P=0.438 R=0.944; t=0.007 P=0.396 R=0.944; t=0.000 P=0.336 R=1.000
- shipped: t=1.000 P=1.000 R=0.014; t=0.984 P=1.000 R=0.254; t=0.890 P=0.971 R=0.465; t=0.758 P=0.902 R=0.648; t=0.478 P=0.838 R=0.803; t=0.283 P=0.721 R=0.873; t=0.139 P=0.627 R=0.901; t=0.097 P=0.563 R=0.944; t=0.050 P=0.493 R=0.944; t=0.026 P=0.438 R=0.944; t=0.007 P=0.396 R=0.944; t=0.000 P=0.336 R=1.000

### Weights (log-odds per unit of evidence)

- graded-prior: bias -4.00, time +1.50, far -2.00, numbers +1.00, dates +0.50, names +2.00, place +1.00, conflict -1.00, terms +1.00, event +0.50, event_conflict -0.50, tokens +4.00, title +2.00, chars +3.00, cross +0.00, cross_tokens +2.00, cross_chars -1.00
- graded: bias -5.38, time +3.07, far -2.02, numbers +1.57, dates +0.93, names +0.56, place +0.33, conflict -1.00, terms +0.73, event +0.47, event_conflict -0.73, tokens +4.96, title +1.87, chars +4.74, cross +1.20, cross_tokens +2.52, cross_chars -0.14
- graded-guard: bias -5.38, time +3.07, far -2.02, numbers +1.57, dates +0.93, names +0.56, place +0.33, conflict -1.00, terms +0.73, event +0.47, event_conflict -0.73, tokens +4.96, title +1.87, chars +4.74, cross +1.20, cross_tokens +2.52, cross_chars -0.14
- shipped: bias -5.38, time +3.07, far -2.02, numbers +1.57, dates +0.93, names +0.56, place +0.33, conflict -1.00, terms +0.73, event +0.47, event_conflict -0.73, tokens +4.96, title +1.87, chars +4.74, cross +1.20, cross_tokens +2.52, cross_chars -0.14

### TEST errors at the strict operating point (pair ids only; strongest components)

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
- graded-guard: 20 false merges (by label: different 1, related 19; by language pair: fr-en 3, fr-fr 17), 14 missed (by language pair: fr-en 4, fr-fr 10)
  - `p60fef7e3fa` false_merge gold=related fr-fr score=0.912; time, chars, numbers
  - `p2322440b19` false_merge gold=related fr-fr score=0.871; chars, time, title
  - `p7bca63ab0c` false_merge gold=related fr-fr score=0.771; chars, tokens, title
  - `p9b9037f6a3` false_merge gold=related fr-fr score=0.766; time, chars, tokens
  - `pc65bf39a81` false_merge gold=related fr-fr score=0.761; chars, tokens, title
  - `p1ad0166122` missed gold=same_event fr-en score=0.000 (raw 0.653, failed the FR/EN guard); time, cross, names
  - `p5b1e8addf1` missed gold=same_event fr-en score=0.000 (raw 0.667, failed the FR/EN guard); cross, chars, tokens
  - `p6d3a42be05` missed gold=same_event fr-en score=0.000 (raw 0.376, failed the FR/EN guard); time, cross, chars
  - `p7988132295` missed gold=same_event fr-en score=0.000 (raw 0.346, failed the FR/EN guard); time, cross, chars
  - `p9dabf753bf` missed gold=same_event fr-fr score=0.107; chars, title, time
- shipped: 20 false merges (by label: different 1, related 19; by language pair: fr-en 3, fr-fr 17), 14 missed (by language pair: fr-en 4, fr-fr 10)
  - `p60fef7e3fa` false_merge gold=related fr-fr score=0.912; time, chars, numbers
  - `p2322440b19` false_merge gold=related fr-fr score=0.871; chars, time, title
  - `p7bca63ab0c` false_merge gold=related fr-fr score=0.771; chars, tokens, title
  - `p9b9037f6a3` false_merge gold=related fr-fr score=0.766; time, chars, tokens
  - `pc65bf39a81` false_merge gold=related fr-fr score=0.761; chars, tokens, title
  - `p1ad0166122` missed gold=same_event fr-en score=0.000 (raw 0.653, failed the FR/EN guard); time, cross, names
  - `p5b1e8addf1` missed gold=same_event fr-en score=0.000 (raw 0.667, failed the FR/EN guard); cross, chars, tokens
  - `p6d3a42be05` missed gold=same_event fr-en score=0.000 (raw 0.376, failed the FR/EN guard); time, cross, chars
  - `p7988132295` missed gold=same_event fr-en score=0.000 (raw 0.346, failed the FR/EN guard); time, cross, chars
  - `p9dabf753bf` missed gold=same_event fr-fr score=0.107; chars, title, time

<!-- eval:results:end -->
