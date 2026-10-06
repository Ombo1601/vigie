# eval/ — event-grouping evaluation (Phase 0)

Additive tooling for the event-first direction. Nothing here runs in the live
pipeline: `scripts/` does not import `eval/`, `refresh.py` and `pipeline.py`
never call it, and nothing in this folder is staged or deployed. Python 3.12+
standard library only, deterministic, LF.

| file | role |
|---|---|
| `run_eval.py` | the harness: loads gold pairs and items, runs pluggable scorers, reports strict / lenient P, R, F1, per language pair, PR points, average precision, a bootstrap interval, B-cubed clusters, and fills `REPORT.md` |
| `baseline_current.py` | the **current** rule as a scorer: calls `cluster_issues.event_scar`, `features_match` and `event_buckets` directly (no re-implementation) |
| `graded_matcher.py` | graded 0..1 same-event similarity with explanations (time window, numbers and dates, capitalised names, lexicon places / institutions / event types, bilingual stemmed tokens, character 4-gram TF-IDF cosine), logistic weights fitted on dev |
| `lexicon_fr_en.py` | 260+ French/English entries: Québec City places and roads, public institutions, parties, projects, event types, roles |
| `REPORT.md` | the report template; the harness fills the marked results block |

## Data (private, never committed)

- **Gold set**: `--gold`, default `data/eval/gold_v0.json`. Pairs of item ids
  with a label (`same_event`, `related`, `different`) and a split (`dev`,
  `test`). Ids only.
- **Items**: `--data-dir`, default `data/normalized`; the harness reads every
  `*_candidates.json` plus `latest_enriched.json` and keeps the first copy of
  each id. These are the ignored private stores (unpack the state tarball to
  get them locally).
- When either is absent (CI, a fresh checkout) the harness prints
  `eval: skipped: ...` and exits 0.

The public repository must never carry publisher text. The report and
`--json` results hold ids, counts and measures only (a test checks this).
`--dump-errors DIR` writes the test errors **with headlines and reasons** for
review; it refuses any path inside the repository other than `data/`.

## Run

```text
python -X utf8 eval/run_eval.py                                   # all scorers, print summary
python -X utf8 eval/run_eval.py --report eval/REPORT.md           # fill the report
python -X utf8 eval/run_eval.py --json data/eval/results.json --dump-errors data/eval
python -X utf8 eval/run_eval.py --scorers baseline,graded --no-clusters
python -X utf8 -m unittest discover -s tests -p "test_eval_*.py"  # synthetic fixtures only
```

## Protocol

- Fit and choose thresholds on **dev** only; report **test**. The threshold is
  the median of every dev threshold within 0.01 F1 of the best (one arg-max is
  noisy on a few hundred pairs).
- Strict: `same_event` positive, `related` and `different` negative. The
  `related` and `different` merge rates and a lenient metric (related left
  out) are shown beside it.
- Per language pair: fr-fr, fr-en, en-en.
- Clusters: the baseline uses the live `event_buckets` (complete link);
  other scorers use a greedy average-link clusterer over pairs at most 7 days
  apart, at the dev threshold.
- The IDF tables of the graded matcher come from every unlabelled item in the
  store, never from labels.

## Plug in a scorer

Any object with `.name` and `.score(a, b) -> float` in [0, 1]. Optional:
`.binary` (fixed 0.5 threshold), `.prepare(corpus)`, `.fit(triples)` where a
triple is `(item_a, item_b, 1 if same_event else 0)` from dev,
`.contributions(a, b)` (component names reach the report), `.explain(a, b)`
(reasons, for private dumps), `.cluster(items, threshold)`.

```text
python -X utf8 eval/run_eval.py --scorers baseline --scorer-spec mymodule:MyScorer
```

## Reading a graded score

`GradedMatcher.explain(a, b)` returns the strongest reasons, signed, for
example `+ same place: Limoilou`, `+ same numbers: 60000`,
`+ published 2 h apart`, `- different places: Beauport vs Limoilou`. That is
what an event page can show when it says why articles are grouped.
