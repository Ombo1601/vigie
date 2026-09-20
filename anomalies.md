# Vigie — Anomaly beacon (structural reading) v0.1

Date: 2026-09-19  
Method id: `anomaly-beacon-v1`  
Code: `scripts/compile_anomalies.py` · Store: `data/anomalies/latest_verdict.json`

## What this is

A finite catalogue of **fixed-threshold rules** measured over the City's own
official roadworks collection. An anomaly is a counted collection fact —
« the City declared 3 new obstructions on this street in one collection » —
never a prediction, never an importance judgment, never a model output.

The beacon renders inside the *Travaux et entraves* section only, never in the
first viewport (DESIGN.md). With zero measured anomalies the brief is
byte-identical to a brief without the feature.

## Rule catalogue (falsifiable)

All rules group entries by `street_key` (edge-atlas-v1 normalization). One
entry naming several streets counts once per street. Counts are exact;
evidence lists are capped at 8 ids.

| id | Label | Fires when | Claim wording (French) |
|----|-------|-----------|------------------------|
| `poussee-declarations` | Poussée de déclarations | ≥ 3 events **new in this collection** (diff `new`) share a street | « N nouvelles entraves déclarées sur X dans cette même collecte. » |
| `fin-reportee` | Fins reportées | ≥ 2 events on a street carry the City's own `end_date_moved: later` revision (diff `changed`) | « La Ville a reporté la fin déclarée de N entraves sur X depuis la dernière collecte. » |
| `concentration` | Concentration | a street carries ≥ 8 active declarations **and** ≥ 4× the network median (median over streets with ≥ 1 active event) | « X concentre N entraves actives déclarées — R× la médiane du réseau (M). » |
| `fenetre-depassee` | Fenêtre officielle dépassée | ≥ 3 events on a street remain declared while their official `end_date` precedes the collection stamp | « N entraves sur X restent déclarées alors que leur fenêtre officielle est dépassée. » |

Ratios and medians render in French notation (comma decimal, one digit).

## Honesty rules

- `fin-reportee` relays the **City's own revision direction**, literally.
  Postponed ≠ extended works; the beacon never concludes anything.
- `fenetre-depassee` measures the feed against itself. It never claims the
  works ended, nor that the City is wrong; the official map stays the
  authority.
- Diff-based rules require `has_previous`: a first collection never implies a
  comparison that did not happen.
- Every verdict row carries `rule_id`, thresholds, exact `count`, capped
  `evidence_event_ids`, and `status: proposed`.
- Verdict rows are capped at 6 (sorted by rule rank, then count descending,
  then street key); the brief shows at most 3. `anomaly_count` stays exact.

## Determinism and fail-soft

- No wall clock: `compiled_at` is the collection stamp; a render-only rebuild
  is byte-identical.
- Absent, corrupt or foreign-method roadworks store → empty verdict
  (`has_store: false`), exit 0. The edition never depends on the beacon.
- The renderer re-validates the method string and the claim presence: a
  foreign or empty verdict renders zero HTML.

## Amendment path

Adding, removing or re-thresholding a rule means amending this file **first**;
the test suite locks the code catalogue against this table (same lockstep
discipline as ranking.md ↔ `score_item` and FACETS.md ↔ `life_facets`).

## Kill list

- Predictive or probabilistic anomaly « scores »
- Anomaly visibility leaking into `rank_score` or the morning twin
- Alert chrome: red banners, pulses, sirens, push notifications
- Anomaly rules over article data (the beacon reads the official feed only)

## Decision log

| Date | Decision | Owner |
|------|----------|--------|
| 2026-09-19 | Ship v0.1 four-rule catalogue; beacon inside Travaux section only | Bucky + le porteur |
