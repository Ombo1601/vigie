# Vigie — Ranking method (v0)

Published method. No invisible editorial boost without a logged reason.

## Principle

Rank by **proximity to Quebec City life** and **recency**. Do not rank by ideology, party, or “trust score” invented in secret.

Enrich tags (topic, claims, impact) are **proposals** when present (`enrich_status: proposed`). Until enrich runs, v0 ranks on geography from `sources.yaml` + recency only.

## Formula (v0)

```
score =
  w_geo * geo_proximity
+ w_recency * recency
+ w_tension * has_contradiction   # 0 until clustering exists
+ w_impact * impact_weight        # 0 until enrich proposes impacts
```

### Weights (v0)

| Weight | Value | Meaning |
|--------|-------|---------|
| `w_geo` | 0.60 | Nest proximity dominates |
| `w_recency` | 0.40 | Fresh approaches matter |
| `w_tension` | 0.00 | Reserved — enable when Issues exist |
| `w_impact` | 0.00 | **OFF** — co-founder refuse 2026-09-16; unlock only when gates below pass |

Weights must sum intent clearly; change them only by editing this file and noting the date below.

### w_impact enable gates (co-founder ACT 2026-09-16)

**Decision: do not enable now.** Evidence: **3/354** candidates carry units (0.85%); **housing_count = 0** on this pulse; turning weight on would be three-title theater.

Unlock `w_impact > 0` only when **all** of these hold on the same morning pulse (log numbers in this file before flipping the constant):

| # | Gate | Threshold |
|---|------|-----------|
| 1 | Unit coverage | ≥ **25** candidates with ≥1 unit, **and** ≥ **8%** of store (`n ≥ 200`) |
| 2 | Kind diversity | ≥ **3** hits each for `price`, `bylaw_id`, **and** `housing_count` |
| 3 | Precision hold | Bond-issuance deny + English “unit” deny still green in tests |
| 4 | Score proof | Before/after: top-20 Near me Δ logged; no city scar without units displaced solely by a linked unit chip |

**First unlock formula** (when gates pass — not before):

```
w_geo = 0.55
w_recency = 0.35
w_tension = 0.00
w_impact = 0.10

impact_weight = 1.0 if any proposed unit on the candidate else 0.0
```

Binary presence only — never scale by dollar magnitude (that would crown bond noise if a deny ever slips). Flip requires editing `rank_display.W_*` **and** this table in the same commit/log line.

### geo_proximity

| `nest_role` / `geo` | Score |
|---------------------|-------|
| `primary` / `quebec-city` | 1.00 |
| `province` / `quebec` | 0.55 |
| `linked` | 0.25 |
| unknown | 0.10 |

### recency

Half-life **36 hours**.

```
recency = 0.5 ** (age_hours / 36)
```

Missing `published_at` → `recency = 0`. Download/collection time is never publication
time (a fetched_at fallback would let old undated news look fresh).

### has_contradiction / impact_weight

Both **0** in v0 until cluster + enrich land. Do not fake them.
`impact_weight` stays **0** even after unit extraction (price / bylaw_id / housing_count) —
units are published for the citizen to check; they do not move the score until `w_impact` is turned on by logged ACT.

## Editorial boosts

Forbidden unless:

1. Logged in `data/rank_log.jsonl` with reason, who, when
2. Visible in the UI as “manual boost”

Silent boosts = propaganda with better geography.

## Change log

| Date | Change | Owner |
|------|--------|--------|
| 2026-09-15 | v0 weights: geo 0.60, recency 0.40; tension/impact off | Marcus (co-founder) |
| 2026-09-15 | enrich.py rules-v0 writes proposed geo/topic/impact; w_impact still 0 until we choose to score proposals | Niccolo (co-founder) |
| 2026-09-15 | Display nest + geo_proximity use proposed enrich.geo; primary source without city token no longer auto Near me | Bucky (co-founder) |
| 2026-09-15 | cluster v0.3: Issues require >=2 source voices; drop single-voice theater; FR/EN anchors | Nietzsche (co-founder) |

| 2026-09-15 | enrich topic rules hardened: word-boundary stems; no bare environnement; no ges matching villages; rent no longer matches different | Niccolo (co-founder) |
| 2026-09-15 | cluster v0.5: named scars only (Maëlyne/Lévis, Marchand); no coroner/tramway glue; README Critical Path | Bucky (co-founder) |
| 2026-09-15 | enrich v0.2: province nest cannot cloak world-fog as quebec; TECHNICAL_PROCESS.md synced to scripts | Marcus (co-founder) |
| 2026-09-15 | cluster v0.4: tighter related (no weak glue onto anchored Issues); readable FR-first questions; README Critical Path | Bucky (co-founder) |

| 2026-09-15 | cluster v0.6: Marchand scar title-or-mayor-agenda only; drop summary hitchhikers (Capitale-Nationale elections) | Niccolo (co-founder) |
| 2026-09-15 | lookout temperance: Province+Linked display top 30 by score; Showing M of N; full set in latest_ranked.json | Bucky (co-founder) |
| 2026-09-15 | lookout header clock: Ranked/Enriched/Ingested from JSON + Sources path; invent nothing | Bucky (co-founder) |
| 2026-09-15 | serve method files: /VISION.md /ranking.md /sources.yaml /RENT.md from disk; lookout links | Bucky (co-founder) |
| 2026-09-15 | Province+Linked display impact-first (proposed topics); Showing line names it; store unchanged | Bucky (co-founder) |
| 2026-09-15 | impact-first: recognize enrich stem energy/hydro (Niccolo refuse) | Bucky (co-founder) |
| 2026-09-15 | enrich energy/hydro: add diesel|gasoline|gallon (price); essence untouched | Bucky (co-founder) |
| 2026-09-15 | lookout: demote /ohdio/ from Near me into Province by score; store untouched | Bucky (co-founder) |
| 2026-09-15 | ohdio demote: pin demoted booths into Province displayed 30 (appear moved) | Bucky (co-founder) |
| 2026-09-15 | enrich topic transport (tramway|tgv|opus|m[?e]tro|autobus) impact mobility; impact-first allowlist | Bucky (co-founder) |
| 2026-09-15 | enrich topic education (éducation|education|écoles|ecoles|schools) impact education; impact-first allowlist | Bucky (co-founder) |
| 2026-09-15 | Lookout UI v2.4: micro-nav above fight list; publisher og:image faces via fetch_media.py (never invent/stock); no rank change | Bucky (co-founder) |
| 2026-09-15 | Lookout UI v2.3: center default Impact Radar (Province+Linked impact-first); fight yields Issue Stage; Near me rail holds | Bucky (co-founder) |
| 2026-09-15 | enrich v0.3: airport/aéroport stem → topic trade + impact trade; never bare private; place-first | Bucky (co-founder) |
| 2026-09-15 | Lookout UI v2.2: desktop command field (fights | Issue Stage | Near me rail); micro Prev/Next; mobile keeps deck; archive door | Bucky (co-founder) |
| 2026-09-15 | Lookout UI v2.1: full-bleed deck; Issue Stage left/right; Same fight side rail only when content on disk; archive readable measure | Bucky (co-founder) |
| 2026-09-15 | Lookout UI v2: Deck + Issue Stage (Issues then Near me); Prev/Next; Open archive for nests; typographic faces only — og:image phase 2; no dwell-time bait | Bucky (co-founder) |
| 2026-09-15 | Lookout UI v1: sticky nest nav; Issues above lists; cards; Same fight/Même combat from store only; no dwell-time bait; presentation-only in rank_display.py | Bucky (co-founder) |
| 2026-09-15 | lookout: shared booth/brief demote (/ohdio/ + /en-bref/) from Near me; pin visible in Province | Bucky (co-founder) |

## How we know we are wrong

- Ranking quietly favors one party line
- Linked national fluff outranks Quebec City housing/hydro/law that hits residents
- We change weights without logging it here

## Lookout UI v2.5 — Near me confession (2026-09-15)

- Kept booth/brief demotion (Ohdio + en-bref leave Near me, pinned in Province).
- Hardened the Showing line: names the moved titles and links `#pin-{id}` into Impact Radar.
- Click jumps to Province pin (showRadar + scroll). No rank change. No silent vanish.

## Cluster v0.7 — more named scars, not more AI (2026-09-15)

- Kept ≥2 distinct `source_id`s. No LLM cluster priest. No bare coroner / bare Duhaime / topic soup.
- Added named scars:
  - `tramway` — title must carry `tramway` **and** (`Marchand`|`Duhaime`)
  - `airport` — place token + privatisation near place; **Province OK** (do not fake quebec-city)
- Order: tramway before marchand so city tram fight is not swallowed by the mayor scar.
- Result on this run: **4** fights (airport 4 voices / Province; maelyne-levis 3; tramway 2; marchand 2). Store still 244.
- Method string: `rules-cluster-v0.7 named-scars-only; tramway title+(Marchand|Duhaime); airport place+privatisation (Province OK); …`

## Lookout UI v2.6 — questions, not wire (2026-09-15)

- Dropped `Plusieurs voix (N) — [outlet lede]` from fight compass and Stage titles.
- Scar-locked neutral questions in `cluster_issues.py` (`SCAR_QUESTIONS`):
  - maelyne-levis → *Que révèle l'enquête du coroner sur la recherche de Maëlyne Lugez à Lévis ?* (colder; no smuggled “manqu”)
  - tramway → *Faut-il arrêter le tramway de Québec ?*
  - marchand → *Quelles priorités pour Québec sous Marchand ?*
  - airport → *Le Canada doit-il privatiser ses grands aéroports ?*
- Voice count stays in fight-meta (`N voices`). Sources face on Stage — no crowned answer.
- Method: `rules-cluster-v0.7-q`. No new scars. No LLM. Store still 244.

## Lookout UI v2.7 — temper the eye, clear the ontology (2026-09-15)

- **Impact Radar** is a **mode** above Fights — not a fifth fight. Fights (4) = four scar-locked questions only. Prev/Next cycle fights only (Radar via mode button).
- **Equal card skeleton** on Radar + Near me: fixed face strip height (5.25rem) — publisher `og:image` or empty typography strip of the same height. No tragedy photo may tower over housing. Still their pictures, never stock. No rank change.
- Header sub shortened (workshop debris thinned). Method/clock still public.
- Store still 244.

## Lookout UI v2.8 — Same fight = real scar only (2026-09-15)

- Same fight links **only** when candidates share a named issue on disk (issue_id / scar). Bare topic fallback killed — no false brothers.
- Demoted booth/brief stay findable from Near me confession (pins) but sit in a **Moved from Near me** strip after impact-first Province Radar — not crowned as first row.
- `cap_slice(..., pin_at="end")`. No new rank weights. No LLM. Store still 244.

## Lookout UI v2.8.1 — every booth/brief out of the crown (2026-09-15)

- Province Radar crown = life-hit only (no /ohdio/, no /en-bref/).
- All booth/brief sit in the **Moved** strip (findable, not crowned); Near me demotions still listed there.
- Linked uses the same split. No rank change. Store still 244.


## Lookout pulse v0.1 — wake without co-founder hands (2026-09-15)

- Scheduled thin Critical Path: ingest → normalize → enrich → cluster → rank_display.
- Cadence: weekdays 08:00 and 17:00 America/New_York (routine lookout-pulse-v0-1).
- Bounded by RENT.md: the project wallet; no fetch_media in the thin loop; no unbounded scale.
- Falsifiable: Ranked clock advances without a co-founder click.

## Faces v0.2 — map-scoped publisher faces (2026-09-15)

- Fetch only what the map shows: Near me + Province/Linked life-hit crown + fight voices (not all 244).
- `og:image` / `twitter:image` only. Never stock. Never AI fill. Empty strip when silent.
- JdQ hardened (browser UA, Referer, retry) — still **403 Forbidden** from their wall; fail = typography (honest).
- Thin `fetch_media` after **morning** pulse only (08:00), not every tick. RENT.md bound.
- This run: map_scope=76, with_image=80, fetched=40. Method string in `latest_faces.json`.

## Lookout Arrival v0.1 — Phase 0 + Phase 1 (2026-09-16)

PDCA: first paint is one composition (Approaches), not the three-zone command dashboard.

### Plan
- Phase 0: `FRICTION.md` names F1–F8 (dashboard, brand, cognitive tax…).
- Phase 1: Arrival = Vigie brand · one line · Approaches (≤5 fights, store order) · CTA opens lookout field.
- No `w_*` changes. No For You. Approaches are presentation of `latest_issues` only.

### Check
- Arrival section before `#command`; field shell hidden until invitation / Approach tap.
- Tests: `test_arrival_lookout` — brand, approaches, no “for you”, W_IMPACT=0, W_GEO=0.60.
- Unit chips on Approach when scar items carry units.

### Act
- Depth (Radar / Stage / Near me) remains behind “Open lookout field”.
- Resident walkthroughs (5) still deferred — instrument ships first.

## Approach polish + Since you left v0.1 — Phase 4 (2026-09-16)

PDCA: glanceable Approaches + visit delta from **store fingerprints only** (never reorder).

### Plan
- Polish: nest labels (Near me/Province/Linked), silence + official-quiet chips, remix edge, `data-issue-id` / `data-fp`.
- Since you left: compare `localStorage` prior pulse to embedded `#vigie-pulse`; badge new/changed **in place**.
- Kill: For You reorder, ranking weight changes.

### Check
- `since_left_delta` unit tests: new / changed / gone / same; order of curr untouched.
- HTML: `#since-left`, `#vigie-pulse`, silence chips, Near me label.
- W_IMPACT still 0.

### Act
- First visit → “First look”; same pulse refresh → “same Approaches”; recluster → factual delta.
- Next: Phase 5 ambient pulse only after Arrival+delta holds in resident walks.

## Phase 4 verification — store timestamp (2026-09-17)

### Plan
- Strip must name store `clustered_at` (compact UTC).
- Identical `clustered_at` short-circuits to same Approaches (fp upgrade must not fake delta).
- Fingerprint v2 includes `unit_count`; pulse payload carries it.

### Check
- `since_left_visit`: first / same-clock / returning delta with badges.
- Live HTML: `formatStoreClock`, `prev.clustered_at` gate, fp `|` count = 4.
- Suite: `tests/test_phase4_since_left.py`.

### Act
- Phase 4 DoD **MET (instrument)**. Clearing `localStorage` resets to First look (expected).

## Ambient morning pulse v0.1 — Phase 5 (2026-09-16)

PDCA: glance channel that cannot diverge from Arrival.

### Plan
- Digest = same `build_approaches` + `pulse_payload` as Lookout Arrival.
- Inputs: `latest_issues` + `latest_ranked` only. No second ranking. No LLM digest. No For You.
- Outputs: `data/pulse/latest_morning.{json,txt}` + `public/morning.html`.
- Hook: `rank_display.main()` emits ambient twin after paint; CLI `scripts/ambient_pulse.py` rebuilds from store.
- Morning 08:00 Critical Path already runs rank_display → digest ships with the pulse (RENT: zero extra LLM/fetch).

### Check
- Identity: digest `issue_ids` + fingerprints + questions == Arrival Approaches (unit tests).
- Rank scores cannot reorder Approaches (high-score decoy proven).
- Arrival CTA → `/morning.html`; deep-link `/?approach=N` opens that fight.
- `W_IMPACT` still 0.

### Act
- Stage copies `morning.html` with `index.html`.
- Optional paid alert channel later may reuse TXT/JSON — still store twin, never a second brain.

## Phase 5 verification — widget + Stage twin (2026-09-17)

### Plan
- Method names Arrival **and** Stage; emit refuses Approaches / `#vigie-pulse` / Stage `issue_id` drift.
- Optional widget paste: `latest_morning.widget.txt` — store facts only.

### Check
- `digest_matches_arrival_pulse` + `digest_matches_stage_fights` gates.
- Live morning JSON approaches == Arrival `#vigie-pulse`.
- Suite: `tests/test_phase5_ambient.py`.

### Act
- Phase 5 DoD **MET (instrument)**. Alert *delivery* remains RENT v1; widget is the payload.

## Life facets v0.1 — Phase 6 (2026-09-16)

PDCA: declared opt-in lenses reorder Approaches only.

### Plan
- Publish `FACETS.md` + `scripts/life_facets.py` catalog (renter, transit, energy, civic, health).
- Formula: topic_weight + optional unit_weight; sort `(-score, store_index)`; never filter.
- Opt-in UI on Arrival; preference device-local (`vigie_facets_v1`). Default = store order.
- Morning ambient digest stays store order (shared twin).
- Kill: silent personalization, For You, leaking into `score_item` / `w_*`.

### Check
- Unit tests: same issue_id set; renter/transit reorder; unit bonus; W_IMPACT=0.
- HTML: `#life-facets`, `#vigie-facets`, link `FACETS.md`.
- Public rank weights unchanged.

### Act
- Facets ship as Arrival lens only; ambient twin remains store order.

## Phase 6 verification — lockstep + gate (2026-09-17)

### Plan
- FACETS.md table must match `FACET_CATALOG` byte-for-weight.
- Reorder never mutates `data-i`; `score_item` never imports facets; ambient ignores facets.

### Check
- `catalog_matches_facets_md`; `w_impact_still_gated`; `test_phase6_facets`.
- Live Arrival: `data-unit-kinds`, status “w_impact gated”.

### Act
- Phase 6 DoD **MET (instrument)**.

## Beauty without fog v0.1 — design law (2026-09-16)

PDCA: make Arrival beautiful without fogging method.

### Plan
- Publish `DESIGN.md` Do/Do-not.
- Brand as hero; one composition; nest depth on Approaches; expressive type.
- Refuse purple/cream cliches, multi-layer radial glow, invented QC photo, score/bias chrome in hero.

### Check
- Tests: `test_design_law` — DESIGN.md linked; no radial-gradient; brand clamp > line; no score in Arrival; nest `data-nest`; W_IMPACT=0.

### Act
- Full-bleed place image deferred until a real file lands in `public/place/` and is named in DESIGN.md.

## Beauty without fog verification (2026-09-17)

### Plan
- Lock DESIGN.md Do/Do-not with live Arrival markers; refuse place invention; de-purple silence chrome.

### Check
- `tests/test_beauty_without_fog.py`: composition order, brand>line, font stacks, no radial/cream/purple, empty `public/place/`.
- Live `public/index.html` carries `data-design="beauty-without-fog-v0.1"`.

### Act
- Beauty-without-fog DoD **MET (instrument)**.

## Phase 0 verification — Freeze truth (2026-09-16)

Co-founder audit against canvas DoD.

### Check
- Friction map + kill list: **present** (`FRICTION.md`).
- Instrument re-walk: F1–F8 remediated on live Arrival; residual F8 closed via `#arrival-ops`.
- Resident walks: **0 / 5** — Phase 0 **incomplete**. Protocol + empty R1–R5 table published. No invented residents.

### Act
- Next Phase 0 work is scheduling five real Quebec City life walkthroughs; do not check Phase 0 done until resident pain is ranked in `FRICTION.md`.
- Tests: `tests/test_phase0_friction.py` locks instrument claims + honest DoD prose.

## Phase 1 verification — Arrival Lookout (2026-09-17)

### Check
- No dashboard in first paint: `#field-shell` hidden/aria-hidden; footer deferred; Arrival composition only.
- Lighthouse mobile (Edge): Performance **94** · Accessibility **100** · Best Practices **100** · SEO **100** (`data/lighthouse_mobile_p1.json`).
- Suite: `test_phase1_arrival` + full unittest OK; `W_IMPACT=0`.

### Act
- Phase 1 DoD **MET**. Next resident work remains Phase 0 when live.

## Phase 2 — Approach objects (2026-09-17)

PDCA: Approach is the glance object — geo nest + voices + silence + units without opening Stage.

### Plan
- Always emit silence count + unit chip (or honest “no units yet”).
- Official Quiet names (≤2) on Approach; full silence map stays in Stage.
- Ambient digest reuses `approach_meta_chips_html` + silence preview (same object).

### Check
- `tests/test_phase2_approaches.py`: glance fields on `build_approaches`; Arrival HTML shows units + Quiet names before Stage silence map; store order unchanged; `W_IMPACT=0`.
- Ambient link HTML always carries silence chip.

### Act
- Phase 2 DoD **MET (instrument)**. Full institution silence map remains Stage-deep by design.

## Phase 3 — Fight theater (2026-09-17)

PDCA: Stage answers who spoke / who didn't in one calm confrontation.

### Plan
- Fight arc (Spoke · Did not speak) above voice panels.
- Institution seats only; demote faces; drop dense status meta.
- Soft-dim side rails when field open — Stage is the confrontation.

### Check
- `tests/test_phase3_fight_theater.py`: arc before confrontation; names in first Stage chunk; live HTML carries `fight-theater`.
- `W_IMPACT=0`; no bias meter.

### Act
- Phase 3 DoD **MET (instrument)**. Rail density polish remains residual Medium.

## Impact units v0.1 — price / bylaw_id / housing_count (2026-09-16)

PDCA: falsifiable units on impacts. Citizen can check the raw span. **Not** a reason to turn on `w_impact` yet.

Schema (all `status: proposed`):
`impacts[].units[]` → `kind` (`price`|`bylaw_id`|`housing_count`), `value`, `unit`
(`CAD`|`CAD_per_month`|`CAD_per_L`|`cents_per_kWh`|`percent`|`logements`|`id`), `raw`, `method`, `field`.

Precision guards:
- Price needs life context (loyer/tarif/facture/essence…) — bond `émission d'obligations` denied.
- Housing needs `N logements|habitations|housing units` — English “unity/unit” theater denied.
- Bylaw: `projet de loi N`, `règlement N`, `loi N`, `bill N`.

### Check (this run)

```
Ran 78 tests · OK (1 skip)
enrich 92.3% / cluster 93.0% AST statements

impact units on 3/354 candidates (thin RSS titles — honest)
W_IMPACT = 0.00; score(with units) == score(without)
rank_components.w_impact = 0 on every ranked row
Stage chips: class='chip unit' when raw span present
```

### Act

- **Co-founder decision 2026-09-16: REFUSE enable.** `w_impact` stays **0.00**.
- Gates for first unlock are published under Weights above (25 + 8%, all three kinds ≥3, tests green, Near me Δ logged).
- Extractors stay on; chips stay on; **no silent weight** between now and a gate-passing ACT.
- This run: 3/354 (price×2, bylaw×1, housing×0) — far under gate.

## Voice = institution v0.1 — not RSS feed (2026-09-16)

PDCA: sister desks are one house. Fight gate and silence seat use `institution`, not feed `id`.

Chancellery: every enabled RSS carries `institution` + `institution_name`.
Collapse: CBC Montreal+Politics → `cbc`; Radio-Canada Québec/National/Ottawa → `radio-canada`.
12 feeds → **9** institutions.

Schema delta (`silence` v0.2):
`scope: enabled_institutions`, `enabled_feed_count`, `silent[].institution_id` + `feed_ids`.
Issue: `sources` = institution ids; `source_feeds` = RSS ids that spoke; Stage label = institution name.

### Check (this run)

```
Ran 66 tests · OK (1 skip)
cluster 93.0% / enrich 96.4% AST statements

maelyne   spoke [cbc, le-soleil, radio-canada]  silent 6 / 9
marchand  spoke [journal-de-quebec, le-soleil, radio-canada]  silent 6 / 9
airport   spoke [cbc, radio-canada]  feeds [cbc-montreal, cbc-politics, radio-canada-quebec]  silent 7 / 9
          ← was 3 feed-voices; CBC desks now one institution

spoke ∩ silent = ∅; spoke+silent = enabled institutions
two CBC feeds alone → dropped_single_voice (no fight theater)
```

### Act

- Do not invent institution from URL host heuristics — explicit YAML only (missing → feed id 1:1).
- Step 5 next: Impact units (who pays / who waits) with measurable units — still no left/right meter.
- Silence remains run-scoped to the named scar cluster.

## Silence map v0.1 — who did not speak on this scar (2026-09-16)

PDCA: for each fight, `silence = enabled sources.yaml − voices` (superseded by institution collapse above).

Schema (`status: proposed`):
`scope: enabled_chancellery` → now `enabled_institutions`,
`spoke_count`, `silent_count`, `enabled_count`,
`silent[]` with institution identity (+ feed_ids audit).
Official absences sort first. Note on disk: not a bias meter.

### Check (this run)

- Was: 12 enabled feeds; each fight spoke 3 → silent 9.
- Now: 9 institutions; airport silent 7 after CBC collapse (honest).
- Official silent first on all scars: ville / gouv / hydro (media remix).
- Stage: “Did not speak / N'a pas couvert” strip; fight meta shows `silent N`.
- Tests include complement identity: spoke ∩ silent = ∅; spoke+silent = enabled.

### Act

- Do not turn silence into a left/right or “reliability” score.
- Step 4 (voice=institution) **shipped** — CBC / Radio-Canada sister feeds share one seat.
- Silence is run-scoped to the named scar cluster — not “never covered the topic elsewhere.”

## Claims v0.1 — quote + speaker objects (2026-09-16)

PDCA probe on 354 titles/summaries → high-precision only.

| Pattern | Probe hits | Kept in v0.1 |
|---------|------------|--------------|
| Guillemets «…» | 50 | yes (+ speaker if affirme/dit nearby) |
| Selon X, … | 3 | yes |
| Name verb: … | 35 | yes |
| Bare title-colon | 58 | **no** (Élections 2026 : … is not a speaker) |
| Short labels («Face-à-Face») | — | denied (min 12 chars + deny list) |

Schema (all `status: proposed`):
`quote`, `speaker` (nullable), `attribution` (`said`|`selon`|`reported`), `method`, `field`.

### Check (this run)

- claims on **50/354** candidates (was 0/354 forever-empty).
- attributions: said 18 / reported 43 / selon 2; **20** with speaker.
- Fights carry claims on voices: maelyne 1, airport 4, marchand 2.
- Stage HTML renders claim strips (`class='claim'` × 14).
- Tests: 59 OK; enrich AST statements 96.4%.

### Act

- Empty `claims[]` remains only when no speech pattern exists — honest silence, not a permanent stub.
- Known scar: EN NAME greed can swallow appositives (“Coalition Interjeunes youth coalition”). Tighten later; do not LLM-fill.
- No title-colon. No crowned “truth” paraphrase.

## Primary documents v0.1 — Ville / Gouv / Hydro / Soleil (2026-09-16)

PDCA probe → enable only proven feeds (ceiling still 12 RSS).

| Source | Feed (probed) | kind | Cap |
|--------|---------------|------|-----|
| Ville de Québec | `Rss/rss.aspx?f=gen` | official | 30 |
| Gouv QC | `quebec.ca/fil-de-presse.rss` | official | 30 |
| Hydro-Québec | `nouvelles/rss/5/salle-de-nouvelles` | official | **40** (raw 1424 this run) |
| Le Soleil | Arc `outboundfeeds/rss/category/actualites` | media (paper of record) | 40 |

- `source_kind` flows ingest → normalize → enrich → cluster → Stage.
- Enrich: official primary → quebec-city; official province → quebec (world-fog still linked).
- Cluster v0.9: official tensions sort first; `official_voice_count` + `media_remix` flag.
- Lookout: official chip; Stage warns when scar is media-only.

### Check (this run)

- 12/12 feeds OK; 354 candidates; official=61 (ville 11 / gouv 10 / hydro 40).
- Hydro `capped=true` (RENT.md held).
- Fights still **media_remix=true** (maelyne / airport / marchand) — no official title hit named scars today. Flag is honest, not a painted victory.
- Le Soleil joined maelyne + marchand as media paper-of-record voice.

### Act

- Do not invent official↔scar glue. Next PDCA: only add named scars when an official text and a media fight share a proper noun on disk.
- Borough Ville feeds (cite/sfoy) stay deferred — f=gen is the thin official voice.

## Enrich + cluster v0.4 / v0.8 — word-boundary geo (2026-09-16)

- `STRICT_CITY`: place tokens use `(?<![\w])…(?![\w])`. `l[ée]vis` no longer matches `télévision` / `television`.
- Bare `tramway` removed from city crown; `tramway de Québec` still crowns.
- Cluster maelyne scar: same Lévis word-bound — TV + police is not a place scar.
- Tests: `tests/test_levis_not_television.py`, `tests/test_enrich_geo.py`, `tests/test_cluster_scars.py`.
- Live proof this run: LA helicopter display_geo=`quebec` (was `quebec-city`); Near me 19 → 15.
- No rank weight change. Method strings: `rules-v0.4-word-boundary-geo`, `rules-cluster-v0.8-q`.

## Public ship v0.1 — stage past localhost (2026-09-15)

- Staged thin static root: `deploy/public/` = lookout `index.html` + method files (`VISION.md`, `ranking.md`, `sources.yaml`, `RENT.md`).
- Helper: `scripts/stage_public.py` (run after rank_display before publish).
- Host deploy blocked in group room (Auto-review not available there). The bearer continues publishing from a private chat with Bucky — cheap static host, wallet under RENT.md, no SaaS theater, method files stay public.
- Falsifiable when live: stranger opens Vigie without `serve.py` / 127.0.0.1.
