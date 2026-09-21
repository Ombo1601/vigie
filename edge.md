# Vigie — Edge Atlas (street-level joins) v0.1

Date: 2026-09-19  
Method id: `edge-atlas-v1`  
Code: `scripts/edge_atlas.py` · Store: `data/edges/latest_edges.json`

## What this is

A literal, compile-time join between two declared vocabularies:

- the street names the City declares in its official roadworks feed (WZDX), and
- the street names that appear in the text of the edition's proposed dossiers.

When the same canonical street name appears on both sides, Vigie proposes a
relation — nothing more. It is a reading aid: « this dossier talks about a
street the official collection currently declares obstructed ».

It is **not**:

- geographic proof (no coordinate is ever matched against an article)
- a ranking input (`rank_score`, `w_geo`, `w_recency` never see the atlas)
- a filter (no dossier or obstruction is ever hidden or promoted)
- fuzzy or semantic matching (no model, no embedding, no edit distance)

## Formula (falsifiable)

1. **Normalization.** Each declared name is folded (accents, case),
   apostrophes become hyphens, periods are dropped (`Boul. → boul →
   boulevard`), descriptors canonicalize
   (`boul./blvd/bd → boulevard`, `rte-/rt- → route-`, `aut- → autoroute-`,
   `av/ave → avenue`, `ch/chm → chemin`), `saint-/sainte-` contract to
   `st-/ste-`. A trailing compass token — single letter (`O/E/N/S`) or full
   word (`Ouest/Est/Nord/Sud`), both folded to the letter — is part of the
   key: Rue St-Joseph E ≠ Rue St-Joseph O, and Boulevard Charest Est =
   Boulevard Charest E.
2. **Key.** `street_key = canonical tokens joined by "-"`, direction included.
3. **Phrases.** Per street, the literal spaced phrases (full and
   directionless, hyphenated and spaced variants of hyphenated tokens,
   ≥ 6 characters, ≤ 8 variants) that may match text.
4. **Text folding.** Dossier text (question, label headline, member titles and
   summaries — declared text only, capped at 6000 characters) passes through
   the *same* token canonicalizer. Compass *words* are never folded in text
   (« est » is a French word); the directionless phrase carries that match.
5. **Match.** One literal alternation, longest phrase first, with boundaries
   that reject hyphen-continuations and digit-prefixes: `rue st-jean` does not
   match « rue Saint-Jean-Baptiste »; `3e rue` does not match « 13e rue ».
   A match is a shared *name with its descriptor* — bare proper names
   (« Chapelle », « Entente ») never match alone.
6. **Output.** `streets[key] = {display, active_count, centroid, event_ids≤12,
   matched_issue_ids≤5}` and `issues[issue_id] = {streets≤5}`. Caps truncate
   evidence lists; counts stay exact. `display` = most frequent declared
   spelling, ties lexicographic. `centroid` = mean of the event geometries
   from the latest append-only raw snapshot, rounded to 5 decimals (~1 m);
   `null` when no snapshot is available.

## Determinism and fail-soft

- No wall clock: `built_at` is the newer of the roadworks collection stamp and
  the dossiers' `clustered_at` — both store facts. A render-only rebuild is
  byte-identical.
- The atlas is rebuilt from scratch each edition; it accumulates nothing, so
  it can never inflate.
- Absent, corrupt or foreign-method stores produce an empty atlas and exit 0.
  The pipeline and the edition never depend on it.
- The renderer re-validates the method string: a foreign atlas renders zero
  HTML, byte-identical to no atlas at all.

## Rendering contract

- On an obstruction card: one line, only when a dossier of this edition
  literally names the same street — wording « Rapprochement proposé … une
  mention textuelle, pas une preuve géographique », link to `#dossiers`.
- On a dossier card: one line, only when the roadworks section rendered
  (anchor safety) and the matched street has ≥ 1 active declaration — link to
  `#travaux`.
- Both lines collapse to zero HTML when the atlas is absent or empty.

## Known limits (published, not hidden)

- Literal matching only: « 1re Avenue » in prose will not match the feed's
  « 1e Avenue ». A miss is honest; a guess would not be.
- A shared name is not a shared place: « Chemin Ste-Foy » the street and
  « Sainte-Foy » the borough are different tokens; only the named street with
  its descriptor matches.
- The atlas sees only the current collection. It makes no claim about history.

## Kill list

- Coordinate-matching articles against obstructions (geocoding prose)
- Atlas keys leaking into `score_item`, `rank_score` or the morning twin
- Fuzzy/semantic/embedding matching
- Inventing a street name the City or a source did not declare

## Decision log

| Date | Decision | Owner |
|------|----------|--------|
| 2026-09-19 | Ship v0.1 literal joins; publish this method; ranking untouched | Bucky + le porteur |
