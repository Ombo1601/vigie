# Vigie — Phase 0 friction log (Lookout Arrival)

Date opened: 2026-09-16  
Last verified: 2026-09-16 (instrument re-walk post Phases 1–6 + DESIGN)  
Owner: Bucky + Inventor

## Phase 0 verdict (honest)

| DoD item | Status |
|----------|--------|
| User friction map published | **Met** — F1–F8 below |
| Kill list for UI published | **Met** — still binding |
| No feature-as-substitute for truth | **Met** — instrument map predates Arrival; features did not erase this log |
| **5 resident walkthroughs recorded** | **NOT MET — 0 / 5** |
| **Pain ranked from residents** | **NOT MET** — instrument ranking only |

**Phase 0 is incomplete.** Shipping Arrival before resident walks was a deliberate instrument-first bet. It does **not** satisfy the canvas DoD (“5 resident walkthroughs recorded; pain ranked”). Do not mark Phase 0 done until the resident table below has five real rows.

Refuse: inventing resident quotes, personas, or fake pain scores.

---

## Baseline — first paint (before Arrival redesign)

Method: structured co-founder walkthrough of then-live `public/index.html` (desktop + mobile CSS).

| # | Friction | Evidence on Stage (then) | Severity |
|---|----------|--------------------------|----------|
| F1 | **Dashboard, not lookout** | Desktop `command-field` is 3 zones at once: Fights · Impact Radar · Near me | Critical |
| F2 | **Brand underpowered** | `h1` Vigie clamp ~1.35–1.7rem; subcopy competes with chrome | Critical |
| F3 | **Cognitive tax before assent** | Radar + fight list + near stack + scores + chips before one question lands | Critical |
| F4 | **Silence / units buried** | Silence only inside Issue Stage; units deep in rails | High |
| F5 | **Cream instrument chrome** | `--bg: #f3f0e8` paper dashboard; sticky pill nav | Medium |
| F6 | **“Impact Radar” as default center** | Lands in Province/Linked feed theater | High |
| F7 | **Mobile deck = Issues then Near me** | Dual job; no single arrival gesture | High |
| F8 | **Clock + method row crowd hero** | Operational chrome in first breath | Medium |

## Kill list (binding — must not ship in hero / Arrival)

- For You / personalized feed
- Ranking weight changes (`w_impact` stays 0 until ranking.md gates)
- Bias / trust meters
- Hero stats strips, score worship in Arrival
- Second ranking brain for Approaches (store order unless declared FACETS.md opt-in)
- Invented news or crowning a voice for cleaner UI

---

## Instrument re-walk — live Arrival (2026-09-16)

Evidence: `public/index.html` DOM order + CSS after beauty-without-fog polish.  
Not a resident substitute.

| # | Baseline pain | Live evidence now | Instrument status |
|---|---------------|-------------------|-------------------|
| F1 | Dashboard first | `.field-shell { display: none }` until invitation; Arrival before `#command` | **Remediated (instrument)** |
| F2 | Brand underpowered | `.arrival-brand` clamp from 3.6rem; larger than `.arrival-line` | **Remediated (instrument)** |
| F3 | Cognitive tax | First viewport: brand · line · Approaches · CTA; facets after CTA | **Remediated (instrument)** |
| F4 | Silence / units buried | Approach always shows silence count + official Quiet names + units (or “no units yet”) without Stage | **Remediated (instrument)** |
| F5 | Cream chrome | Fleuve/stone tokens; no `#f3f0e8` / `#f4f1ea`; no radial glow stack | **Remediated (instrument)** |
| F6 | Radar default | Radar lives behind field open; Approaches are first paint | **Remediated (instrument)** |
| F7 | Mobile dual deck first | `#deck` / mobile radar behind `.field-shell` | **Remediated (instrument)** |
| F8 | Clock + method in hero | Collapsed under `<details id="arrival-ops">` (“Clock & method”) | **Remediated (instrument)** |

### Remaining instrument pain (ranked — co-founder only)

| Rank | Pain | Why it still matters | Severity |
|------|------|----------------------|----------|
| 1 | **No resident evidence** | Canvas DoD unmet; we may have fixed the wrong pains | Critical (process) |
| 2 | **Field side rails still compete after assent** | Stage center is emphasized; left/right dimmed — full rail redesign deferred | Medium |
| 3 | **Full silence list length** | Fight arc shows all quiet seats; long chancellery still asks a skim | Low |
| 4 | **No real Quebec City place image** | DESIGN.md refuses inventing one; nest wash ≠ place proof | Low (DESIGN backlog) |

---

## Resident walkthrough protocol (required for Phase 0 close)

**Goal:** five Quebec City–life residents (or near-daily visitors), not co-founders.  
**Task:** open the live French brief cold; think aloud ~5–8 minutes; no coaching.  
**Surface:** the deployed front door — **https://vigieqc.com** (custom domain; the alias https://vigie-deemto.vercel.app also routes to production). Aligned 2026-09-18: this script replaces the old English workbench walk (Arrival/Approaches/Life facets/Stage); the questions now follow the live brief’s sections.

### Script (same for R1–R5)

1. Open `/` on their phone or laptop (note which). No tab of the site already open.
2. Ask: “Sans faire défiler — qu’est-ce que c’est ?” (“Without scrolling — what is this?”)
3. **Le point** (`#essentiel`): “What does this tell you about your city today?”
4. **Ce qui a changé** (`#changements`): “Can you tell what moved since the last edition? Does the ‘Suivi depuis…’ line on a dossier mean anything to you?”
5. **Travaux et entraves** (`#travaux`): “You’re crossing town tomorrow — what here would help? What’s missing?”
6. **Les dossiers** (`#dossiers`): “Who spoke / who stayed silent — can you tell? Does the silence line change your trust?”
7. **Repères utiles** (`#agir`): “Would you tap any of these? Which? Why?”
8. Ask: “What would you tap first? What would you ignore?”
9. Stop before pitching Vision.

Sessions may be remote (screen share) or in person. Record only with consent; quotes land in the table below only with permission. No session, no row — the table stays empty rather than filled with invented residents (kill-list law).

### Record row (fill only with real sessions)

| ID | Date | Device | Role (self-described) | First words (quote) | Pain tags (from F1–F8 or new) | Severity 1–5 | Notes / clip link |
|----|------|--------|------------------------|---------------------|-------------------------------|--------------|-------------------|
| R1 | — | — | — | — | — | — | *awaiting* |
| R2 | — | — | — | — | — | — | *awaiting* |
| R3 | — | — | — | — | — | — | *awaiting* |
| R4 | — | — | — | — | — | — | *awaiting* |
| R5 | — | — | — | — | — | — | *awaiting* |

### Pain ranking rule (after R1–R5)

1. Count tag frequency across five walks.
2. Weight by median severity.
3. Publish a new table **“Resident pain ranked”** in this file — replace instrument rank where residents disagree.
4. Only then check `[x]` on Phase 0 DoD.

---

## Definition of done — Phase 0

- [x] Friction table published (baseline F1–F8)
- [x] Kill list published and still binding
- [x] Instrument re-walk logged against live Arrival (2026-09-16)
- [x] Residual F8 closed on instrument (ops behind details)
- [ ] Five resident walkthroughs recorded (0 / 5)
- [ ] Resident pain ranked and published here

---

## Phase 1 status (2026-09-16)

- [x] Arrival first viewport: brand · line · Approaches · CTA
- [x] Lookout field hidden until invitation / Approach tap
- [x] No For You; `w_impact` unchanged (0)
- [x] Tests in `tests/test_arrival_lookout.py`

## Phase 2 status (2026-09-17) — Approach objects

Canvas DoD: *Replace card lists with Approach (geo + unit + voice count); Unit/silence visible without opening Stage.*

- [x] Approaches are interaction objects (buttons), not archive cards
- [x] Glance: nest (geo) · voices · silent count · official quiet · units or “no units yet”
- [x] Official Quiet name preview on Approach (≤2) — silence glanceable without Stage
- [x] Ambient morning digest shares same meta chips + silence preview
- [x] Store order unchanged; `w_impact` still 0
- [x] Tests: `tests/test_phase2_approaches.py`

**Phase 2 DoD: MET (instrument).**

## Phase 3 status (2026-09-17) — Fight theater

Canvas DoD: *Stage as calm confrontation; institution voices; silence arc; who spoke / who didn't in <10s.*

- [x] Stage = `fight-theater`: Spoke + Did not speak arc before voice panels
- [x] Institution names (not feed-desk archaeology) on arc + voice panels
- [x] Dense `status/items/sources` meta soup removed; glance chips only
- [x] Faces demoted under links (name + claims first)
- [x] Side zones soft-dim when field open; center Stage emphasized
- [x] No bias meter / For You; `w_impact` still 0
- [x] Tests: `tests/test_phase3_fight_theater.py`

**Phase 3 DoD: MET (instrument).**

## Phase 1 verification — Arrival Lookout (2026-09-17)

Canvas DoD: *No dashboard in first paint; Lighthouse mobile pass.*

### Check (live `public/index.html`)
- Composition: brand → line → Approaches → CTA; facets/ops after CTA (collapsed).
- Field: `#field-shell` ships `hidden` + `aria-hidden="true"`; `openField()` reveals; CSS `display:none` until `body.field-open`.
- Site footer deferred until field open (not in first-paint surface).
- Skip link, viewport, meta description, favicon.svg, async font load.
- Kill list holds on Arrival; `W_IMPACT=0`.
- Tests: `tests/test_phase1_arrival.py` + suite green.

### Lighthouse mobile (Edge headless → `http://127.0.0.1:8767/`)
Recorded in `data/lighthouse_mobile_p1.json`:

| Category | Score |
|----------|------:|
| Performance | **94** |
| Accessibility | **100** |
| Best Practices | **100** |
| SEO | **100** |

**Phase 1 DoD: MET.**

### Act
- Phase 0 resident walks remain deferred until live (separate).
- Side-rail density after assent soft-dimmed in Phase 3; deeper rail redesign deferred.
## Phase 4 status (2026-09-16) — Approach polish + Since you left

- [x] Glance chips: nest label · voices · silent · official quiet · units
- [x] Store fingerprint `data-fp` + `issue_id` (no reorder)
- [x] `#since-left` strip from localStorage vs `#vigie-pulse`
- [x] Badges new/changed in place only

## Phase 4 verification (2026-09-17) — store-timestamp delta

Canvas DoD: *Local delta strip from last visit (store timestamp); returning visit shows delta approaches / fights.*

- [x] Strip shows store clock (`clustered_at` compact UTC)
- [x] Same `clustered_at` ⇒ “same Approaches” (no fingerprint false delta)
- [x] New store pulse ⇒ new/changed badges in place + “left Approaches” count
- [x] Fingerprint v2: `voices|silent|remix|unit_count`
- [x] Pure contract `since_left_visit` mirrored by Arrival JS
- [x] Tests: `tests/test_phase4_since_left.py`
- [x] No For You / no reorder; `w_impact` still 0

**Phase 4 DoD: MET (instrument).**

## Phase 5 status (2026-09-16) — Ambient morning pulse

- [x] Digest from same store builders as Arrival (`ambient_pulse.py`)
- [x] Identity gate refuses write if Approaches diverge
- [x] `public/morning.html` + `data/pulse/latest_morning.{json,txt}`
- [x] Arrival CTA “Morning pulse”; `?approach=` deep-link
- [x] No second ranking / no LLM / no For You; `w_impact` unchanged
- [ ] Optional alert delivery channel (RENT v1) — deferred; payload ready as store twin

## Phase 5 verification (2026-09-17) — same pulse as Stage

Canvas DoD: *Morning digest + optional widget copy from same store; one pulse = same method as Stage; no second ranking priest.*

- [x] Method `ambient-pulse-v0.2 same-store-as-arrival-and-stage`
- [x] Emit gates: Approaches identity · Arrival `#vigie-pulse` twin · Stage fight `issue_id` set
- [x] Optional widget: `data/pulse/latest_morning.widget.txt` (paste twin, no LLM)
- [x] TXT carries silence + unit honesty (`no units yet`)
- [x] Tests: `tests/test_phase5_ambient.py` + ambient suite
- [x] RENT alert *delivery* still deferred; widget/json/txt are the payload

**Phase 5 DoD: MET (instrument).** Delivery channel remains RENT v1.

## Phase 6 status (2026-09-16) — Life facets (opt-in)

- [x] Published `FACETS.md` facet→weight method
- [x] Catalog: renter · transit · energy · civic · health
- [x] Arrival opt-in UI; localStorage; Clear restores store order
- [x] Reorder Approaches only — same issue_id set; `data-i` / store_index preserved
- [x] Does not mutate `latest_ranked` / `score_item`; `w_impact` still 0
- [x] Morning digest remains store-order twin

## Phase 6 verification (2026-09-17) — published weights + gate

Canvas DoD: *Declared facets reorder Approaches only; published facet→weight method; w_impact still gated.*

- [x] `catalog_matches_facets_md` lockstep (FACETS.md table ↔ `FACET_CATALOG`)
- [x] `score_item` free of facet imports; `W_IMPACT=0`
- [x] `data-i` / `data-store-index` preserved after reorder; Stage index intact
- [x] `data-unit-kinds` on Approach + JS fallback when signals thin
- [x] Ambient morning stays store order (facets are Arrival lens only)
- [x] Status copy names w_impact gated; tests: `tests/test_phase6_facets.py`

**Phase 6 DoD: MET (instrument).**

## Beauty without fog (2026-09-16) — design law

- [x] Published `DESIGN.md` (Do / Do not)
- [x] Arrival: brand hero · Approaches · CTA; facets after CTA (opt-in)
- [x] Nest depth inset on Approaches; fleuve/stone palette (no purple/cream)
- [x] Nest wash only — no invented QC photo; no multi-layer radial glow
- [x] No score / bias / trust chrome in hero; `w_impact` still 0
- [x] Clock & method collapsed (`#arrival-ops`) — residual F8 instrument close

## Beauty without fog verification (2026-09-17)

Canvas / co-founder DoD: *One composition; brand hero; expressive type; nest depth; no purple/cream cliches; no invented place glow; cards only as Approach.*

- [x] DOM order: brand → line → sub → Approaches → CTA → facets → ops
- [x] Brand clamp floor (3.6rem) > line (1.25rem); Newsreader + Figtree stacks
- [x] No radial-gradient / cream hex / purple indigo / lavender silence chrome
- [x] `public/place/` empty until named in DESIGN.md
- [x] Arrival has no decorative `.card`, no score/bias/For You
- [x] Tests: `tests/test_beauty_without_fog.py` (+ `test_design_law.py`)

**Beauty-without-fog DoD: MET (instrument).** Full-bleed QC place image remains backlog.

## Resident walkthrough — ready-to-run script (2026-09-19)

Phase 0 still shows **0/5**. Everything we have shipped is instrument evidence
until this table holds five real rows. This replaces the older English-workbench
walk; use the live French brief at `https://vigieqc.com`.

**Who:** five Quebec City–life residents (or near-daily visitors). Not co-founders.
**Setup:** their own phone or laptop, cold (no introduction), 5–8 minutes.
**You:** say only the task line below. Do not teach, point, or defend a choice.
Record what they *do*, not what they say they would like.

**Task (read verbatim):**
> « Ouvrez vigieqc.com. Imaginez que vous voulez savoir ce qui a bougé à Québec
> cette semaine. Prenez cinq minutes. Dites à voix haute ce que vous cherchez,
> ce que vous comprenez, et ce qui vous bloque. Dites-moi quand vous avez fini. »

**Then ask, in this order (no leading questions):**
1. Qu’est-ce qui a attiré votre œil en premier ?
2. Qu’est-ce que cette page vous dit de Québec aujourd’hui ?
3. Avez-vous trouvé quelque chose que vous pouviez vérifier vous-même ? Comment ?
4. À quel moment avez-vous hésité ou perdu le fil ?
5. Qu’avez-vous cherché sans le trouver ?
6. Reviendriez-vous demain ? Pourquoi, ou pourquoi pas ?

**Capture sheet** — one row per person, raw notes, no paraphrase:

| # | Date | Profil (quartier, device) | Temps | Où ça a bloqué (section/étape) | Trouvé utile | Reviendrait ? | Sévérité |
|---|------|---------------------------|-------|-------------------------------|--------------|---------------|----------|
| 1 |  |  |  |  |  |  |  |
| 2 |  |  |  |  |  |  |  |
| 3 |  |  |  |  |  |  |  |
| 4 |  |  |  |  |  |  |  |
| 5 |  |  |  |  |  |  |  |

**Rules:** no invented quotes, no personas, no fake severity. A pain ranks only
from a real row. After five rows: rank the pains here and write the Phase 0 close.
