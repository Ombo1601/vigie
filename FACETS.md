# Vigie — Life facets (opt-in) v0.1

Date: 2026-09-16  
Method id: `life-facets-v0.1 approaches-reorder-only`  
Code: `scripts/life_facets.py`

## What this is

Declared life lenses a resident may turn on. They **reorder Approaches on Arrival only**.

They do **not**:
- change `latest_ranked.json` or `rank_score`
- change `w_geo` / `w_recency` / `w_tension` / `w_impact` (w_impact stays gated at 0)
- filter Approaches away
- invent a For You feed
- apply without opt-in (default = issues store order)

## Formula (falsifiable)

For each Approach and each **active** facet `F`:

```
score += F.topic_weight   if approach.topic ∈ F.topics
score += F.unit_weight    if any approach unit.kind ∈ F.unit_kinds
```

Sort Approaches by `(-score, store_index)`.  
Ties keep store order. Same issue_id set before and after.

## Catalog

| id | Label | Topics matched | Unit kinds | topic_weight | unit_weight |
|----|-------|----------------|------------|--------------|-------------|
| renter | Renter | housing | price, housing_count | 1.0 | 0.35 |
| transit | Transit | transport | — | 1.0 | 0.0 |
| energy | Energy | energy/hydro, energy, hydro | price | 1.0 | 0.35 |
| civic | Civic | law | bylaw_id | 1.0 | 0.35 |
| health | Health | health | — | 1.0 | 0.0 |

Topics come from proposed enrich/issue topic tags already on disk. Units come from proposed impact units already on the Approach.

## Opt-in

- UI: Arrival “Life facets (opt-in)” — off until the resident toggles.
- Preference may live in `localStorage` (`vigie_facets_v1`) on that device only.
- Clear facets → store order returns.
- Morning ambient digest stays **store order** (shared pulse twin). Facets are a local Arrival lens.

## Kill list

- Silent personalization without this file
- Facet weights leaking into `score_item`
- “For You” labeling
- Left/right or trust meters as facets

## Decision log

| Date | Decision | Owner |
|------|----------|--------|
| 2026-09-16 | Ship v0.1 opt-in reorder; publish this method; w_impact still gated | Bucky + le porteur |
| 2026-09-17 | Verification: MD↔code lockstep guard; unit-kinds on Approach; ambient stays store order | Bucky + le porteur |
