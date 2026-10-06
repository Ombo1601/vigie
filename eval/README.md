# eval/ — event-grouping evaluation

Evaluation tooling for the event-first direction. Nothing here runs in the
live pipeline: `scripts/` does not import `eval/`, `refresh.py` and
`pipeline.py` never call it, and nothing in this folder is staged or
deployed. The dependency runs the other way: since Phase 1 the harness
imports the **production** matcher (`scripts/event_match.py`) and lexicon
(`scripts/event_lexicon.py`), so it measures what ships. Python 3.12+
standard library only, deterministic, LF.

## Read this first: what the gold labels are

- **Model-generated, not human.** gold_v0 was labelled by one language model
  in three passes (two labellers, then an adjudicator that settled their 79
  disagreements). Its kappa 0.89 is that model's consistency with itself,
  **not agreement between people**. No human has checked any label; 8 pairs
  the adjudicator could not settle await the founder
  (`data/eval/gold_v0_uncertain.json`, private).
- **The caveat travels with the data.** The gold file carries a
  `labelers_provenance` string; `run_eval.py` prints it on every run and
  writes it at the top of the results block. A gold file without it is
  reported as "NOT RECORDED: treat every label as unverified". When a person
  relabels or checks pairs, update that string (and the `version`).
- **Screened sampling.** The pairs were drawn through a lexical screen of the
  same kind as the matcher's features, so recall figures are upper bounds on
  screen-findable positives, and French/English recall under the FR/EN guard
  is partly circular (the fr-en pool already required a shared name, place
  or number).
- **Read every number as model-labelled and relative**: it ranks designs on
  the same labels; it is not a measured real-world precision.

| file | role |
|---|---|
| `run_eval.py` | the harness: loads gold pairs and items, runs pluggable scorers, chooses tiers on dev, reports tier x language pair x split with Wilson intervals, strict / lenient P, R, F1, PR points, the FR/EN guard effect, blocking recall, induced pairwise cluster F1 (with an all-singletons B-cubed reference), a leakage-free split by connected component, and fills `REPORT.md` |
| `baseline_current.py` | the **current** rule as a scorer: calls `cluster_issues.event_scar`, `features_match` and `event_buckets` directly (no re-implementation) |
| `graded_matcher.py` | adapter over `scripts/event_match.py` (features, scores, guard, tiers, the sticky clusterer); only the dev refit (`fit_weights`) lives here |
| `lexicon_fr_en.py` | re-exports `scripts/event_lexicon.py` (260+ French/English entries) |
| `shadow_live.py` | runs the shipped matcher on the private stores (current edition, or `--replay` of every stamped snapshot as editions) and prints counts and timings only |
| `REPORT.md` | the report template; the harness fills the marked results block |

## Data (private, never committed)

- **Gold set**: `--gold`, default `data/eval/gold_v0.json`. Pairs of item ids
  with a label (`same_event`, `related`, `different`) and a split (`dev`,
  `test`), plus `labelers_provenance`. Ids only.
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
python -X utf8 eval/run_eval.py --scorers baseline,shipped --no-clusters --no-component-split
python -X utf8 eval/shadow_live.py --data-dir data/normalized --replay   # counts only
python -X utf8 -m unittest discover -s tests -p "test_eval_*.py"  # synthetic fixtures only
```

## Protocol

- Fit and choose thresholds on **dev** only; report **test**.
- Tiers (docs/EVENTS.md section 4): `certain` = lowest dev threshold whose dev
  precision is >= 0.95 there and at every higher threshold (a tier must not
  hang on one lucky pocket of the curve); `probable` = median of every dev
  threshold within 0.01 F1 of the best (one arg-max is noisy; the report shows
  it for contrast); `possible` = lowest dev threshold at which >= 90% of the
  dev pairs at or above it are same_event or related. A pair failing the
  FR/EN guard is never grouped (its merge score is 0 for tier choice).
- The `shipped` scorer never re-chooses: it reads the frozen
  `event_match.WEIGHTS` and `THRESHOLDS` and the report prints what dev would
  choose today, so drift is visible.
- Strict: `same_event` positive, `related` and `different` negative. The
  `related` and `different` merge rates and a lenient metric (related left
  out) are shown beside it.
- Per language pair: fr-fr, fr-en, en-en; Wilson 95% intervals on TEST tier
  precision; a bootstrap over connected components for the certain tier.
- **Leakage-free split**: the pair split shares items between dev and test
  (175 of 634 items in gold_v0). `run_component_split` assigns every
  connected component of the item graph wholly to dev or test by a hash of its
  item ids, refits and re-chooses tiers on that dev, and reports that test.
- Clusters: the baseline uses the live `event_buckets` (complete link); the
  graded scorers use the production sticky clusterer `event_match.attach`
  (one batch, attach order, average link, merge rule). The cluster-level
  figure is **induced pairwise F1**; B-cubed is shown with an
  `all-singletons` reference row because most gold items are singletons.
- Blocking: the share of gold pairs that `event_match.candidate_pairs` keeps,
  by label and by tier.
- The IDF tables come from every unlabelled item in the store, never from
  labels.

## Plug in a scorer

Any object with `.name` and `.score(a, b) -> float` in [0, 1]. Optional:
`.binary` (fixed 0.5 threshold), `.prepare(corpus)`, `.fit(triples)` where a
triple is `(item_a, item_b, 1 if same_event else 0)` from dev,
`.guard_ok(a, b)` (pairs failing it are never grouped), `.fixed_thresholds`
(tiers not re-chosen), `.set_thresholds(tiers)`, `.contributions(a, b)`
(component names reach the report), `.explain(a, b)` (reasons, for private
dumps), `.cluster(items, threshold)`.

```text
python -X utf8 eval/run_eval.py --scorers baseline --scorer-spec mymodule:MyScorer
```

## Reading a score

`event_match.score(a, b, ctx)` returns the probability and every reason,
signed, in French and English, quoting words and numbers as each outlet
wrote them (never a stem): `+ nombres communs : 60 000 ≙ 60,000`,
`+ même lieu : Limoilou`, `+ publiés à 2 h d'intervalle`,
`- lieux différents : Beauport / Limoilou`, then the model's starting point,
so the listed weights add up to the score's log-odds. That is what an event
page can show when it says why articles are grouped. Reasons are computed at
render time and never stored or sealed.
