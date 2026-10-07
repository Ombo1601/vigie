# MIGRATION.md — from dossiers to events, one shippable step at a time

Status (2026-10-06): steps 0 to 8 are built (tranches A to C). Steps 9, 11
and 12 are built and **wired behind the switch** `scripts/surfaces.py` (tranche
D), committed `off`: `preview` is step 9 without a public link (staged,
`noindex`), `live` is step 11 (the front door switches; the brief moves to
`/le-point.html`) with step 12's delta-v2 published. Step 10's English record
layer (registre, mémoire, partir, affiche, méthode in English) and step 13 are
not started. Section 19 of `docs/EVENTS.md` describes what each position
serves.

Original plan: **Phase 0**. Order matters: each step ships alone, behind tests,
and leaves the live brief unchanged until step 9. Every step ends with
`python -X utf8 scripts/verify.py` green and deploys only through
`scripts/refresh.py`. Specs: `docs/EVENTS.md`, `docs/I18N.md`, `docs/CHARTE.md`.

## Principles

- **Shadow first.** New stages write new stores and change no existing output
  until a measured gate opens.
- **Import, never fork.** The event layer reuses `cluster_issues.features_match`,
  `registre.canonical/leaf_of/chain_hash`, `store_io`, `enrich` patterns.
- **Fail-soft.** A new stage that faults prints and exits 0; the brief renders
  from dossiers as today.
- **Byte-identity guards.** Before any render change, golden tests freeze the
  current outputs on invented fixtures; a step may only change the bytes it
  says it changes.
- **Tests use invented fixtures.** Real data stays in `data/` (private); only
  counts measured on it are ever committed.

## Steps

### 0. Specs (this change)
Files: `docs/*.md` (new). No code. Tension: none; examples are invented.

### 1. Controlled vocabulary as data
Files (new): `scripts/vocabulaire.py` (loader + `label(type, place, lang)`),
`scripts/vocabulaire.json` (tables A and B of `docs/I18N.md`),
`tests/test_vocabulaire.py` (codes unique, ASCII pattern, FR and EN non-empty,
≥ 60 types, ≥ 30 places, every `_PLACE_HINTS` entry maps to a code).
Tension: Vigie's own words only, so no R8 risk; label wording is a public
method change, so it is listed in `ranking.md`'s changelog (step 9).

### 2. Ownership in the source list
Files: `sources.yaml` (+ `ownership_class`, `owner_group`, `ownership_ref`
per enabled source), `scripts/ingest_rss.py` (pass-through only),
`tests/test_ownership.py`. Tension: `sources.yaml` must stay LF and parseable by
the minimal parser (test it with the existing loader); ownership is a factual
claim about publishers, so each row cites a public reference and the founder
signs off the table.

### 3. Origin classifier (shadow)
Files (new): `scripts/origin.py` (pure `origin_of(item, source) -> (class,
rule_id)`), `tests/test_origin.py`. Not wired into the pipeline yet.
Tension: reads publisher text for wire/relay markers; R8 holds because only the
class code and rule id are kept. Default `unknown`: never infer
`own_reporting` from a missing marker.

### 4. Fact extraction (shadow)
Files (new): `scripts/facts.py`, `tests/test_facts.py`. Reuses
`enrich.propose_impact_units` and `enrich._parse_number`; integers only.
Tension: privacy — the public-office entity list is closed; private persons are
never extracted. Divergent values are kept side by side, never reconciled.

### 5. Event builder (shadow store)
Files (new): `scripts/events.py`, `tests/test_events.py`
(identity, sticky membership, merge, replay determinism, schema checker).
Files touched: `scripts/pipeline.py` (add `events.py` after `cluster_issues.py`
in the full run only; never in `--render-only`), `scripts/state_pack.py`
(add `events/store.json` to `EXPLICIT`, or every CI run re-mints every id).
Outputs: `data/events/store.json`, `data/events/latest_events.json`,
`data/ops/events_shadow.json` (counts: events, multi-institution, bilingual,
anchored, unclassified; dossiers vs events overlap).
Tension: the only step touching the live pipeline before the gate; it must be
fail-soft and write nothing under `public/`. Determinism: no wall clock; the
roads-only lane never runs it.

### 6. Measured quality, privately labelled
Files (new): `scripts/events_eval.py` reading a private gold file under
`data/eval/` (gitignored), writing counts to `data/ops/events_quality.json`.
Gate numbers (EVENTS.md section 16) are computed here.
Tension: the gold set holds publisher titles, so it never leaves `data/`; only
precision, recall and sample sizes may be published.

### 7. Event chain (shadow seal)
Files: `scripts/registre.py` (new `evenements` key in state, new
`seal_events()`; `edition_record`, `seal_edition`, `voice_row` untouched),
`tests/test_registre.py` (+ golden test: a fixture chain re-emitted is
byte-identical; `--verify` of an old `chain.json` still passes).
Public output: none yet (state only).
Tension: immutability — once public, event seals cannot be rewritten, so the
chain stays private until the gate opens; a bug before then costs nothing.

### 8. Component renderer and i18n catalogue
Files (new): `scripts/composants.py` (pure functions returning escaped HTML:
`chip`, `timeline_row`, `fact_table`, `anchor_list`, `lang_link`, `head_meta`
with hreflang/canonical), `scripts/i18n/fr.json`, `scripts/i18n/en.json`,
`scripts/i18n.py` (`t(key, lang)`, deterministic FR/EN date and number
formatters), `tests/test_composants.py`, `tests/test_i18n.py` (key parity,
no missing EN key, formatter cases).
Not yet used by existing pages. Tension: stdlib only; no template engine;
every string escaped through the existing `esc()` rules.

### 9. Event pages, French (first public change)
Files (new): `scripts/evenements.py` emitting `public/evenements.html`,
`public/evenements/<event_id>.html`, `public/evenements/latest.json`.
Files touched: `scripts/rank_display.py` (one more `_emit` in the record layer),
`scripts/stage_public.py` (sitemap: index only), `vercel.json` (CSP for new
paths), `scripts/substrate.py` (`llms.txt` lists the pages),
`ranking.md` (method + vocabulary changelog), `REGISTRE.md` (event chain made
public), `AGENTS.md` (record layer becomes eight emitters), `.gitignore`
(generated pages). The front door gains one link; dossiers stay as they are.
Tension: R8/R1 — current-edition pages show verbatim attributed text; permanent
pages show none (test scans them against current titles). R10 — an opt-out
removes URLs from the store and every non-sealed page the same day.

### 10. English mirror
Files: `scripts/evenements.py`, `scripts/registre.py`, `scripts/memoire.py`,
`scripts/depart.py`, `scripts/affiche.py`, `scripts/method_site.py`,
`scripts/substrate.py` (each gains `lang` and writes under `public/en/`),
`scripts/stage_public.py` (hreflang targets must exist, sitemap alternates),
`vercel.json`, English method sources in a new `en/` folder (human
translations reviewed by the founder, each stamped "the French version prevails").
Ship surface by surface; a surface is linked only when complete.
Tension: Charter of the French Language risks (I18N.md section 7) confirmed
first; publisher text is never translated; FR stays default and complete.

### 11. Front door switches to events
Files: `scripts/resident_brief.py` (dossier section rendered from events via
`composants`; dossiers with two institutions are the multi-voice events),
`scripts/rank_display.py` (event ordering = published rule), `ranking.md`
(published before the switch), `scripts/recits.py` (dossier pages redirect to
event pages), `tests/test_resident_brief.py`, `tests/test_brief_dossiers.py`.
Gate: all four EVENTS.md section 16 gates green for 14 consecutive editions.
Tension: ranking change is public law, announced one edition ahead; the
registre main chain keeps sealing `dossiers` from `cluster_issues` unchanged.

### 12. Machine layer
Files: `scripts/substrate.py` (`delta-v2` with events; `delta-v1` kept for 90
days and marked deprecated), `tests/test_substrate.py`.
Tension: consumers of `delta-v1` keep working; cursor stays the chain root.

### 13. Retire the dossier label path
Files: `scripts/cluster_issues.py` (drop `attributed_headline` labels once no
view reads them; scars become event types), tests updated.
Tension: the main chain still seals `label_kind`/`question`; `edition_record`
keeps reading whatever `cluster_issues` emits, so old seals verify and new seals
simply carry subject labels. Folding events into the main record (schema 3)
stays a separate founder decision.

## Rollback

Steps 1–8 add files or keys only: revert the commit. Steps 9–13 revert by commit
plus one `refresh.py` run; sealed event records already published stay (a chain
is append-only) and the registre page states that the event layer was paused.
