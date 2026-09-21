# Vigie — Beauty without fog (design law)

Date: 2026-09-16  
Last verified: 2026-09-17  
Method id: `beauty-without-fog-v0.1`  
Surface: Lookout Arrival (`public/explorer.html` via `rank_display.py`). The
resident front door (`public/index.html`) is governed by the v0.2 law below.

Beauty is method made effortless. Fog is chrome that pretends to clarify.

## Do

1. **One composition** in the first viewport: brand · one line · one supporting sentence · optional Since-you-left strip · Approaches · one CTA group. Facets / clock stay after CTA (opt-in / collapsed).
2. **Brand as hero signal** — `arrival-brand` must out-scale the headline. No competing chrome above it.
3. **Expressive type** — display + UI pair (Newsreader / Figtree). No Inter / Roboto / Arial / system-only stack as the voice.
4. **Atmospheric nest depth** — Near me / Province / Linked readable as depth on Approaches (inset nest edge). Palette = Cap Diamant stone · fleuve slate · winter ice. Not purple-on-white. Not cream+terracotta broadsheet.
5. **Full-bleed place atmosphere** only when a real Quebec City life image is on disk under `public/place/` and named in this file. Until then: quiet nest wash only — **refuse** abstract multi-layer AI gradients as “hero.”
6. **Cards only as interaction containers** — Approach buttons. Not decorative card grids in the hero. Glance chips on an Approach are method facts (geo / voices / silence / units), not a hero stat strip.

## Do not

1. Dashboard chrome, score worship, pill clusters, or stat strips in the hero.
2. Bias rainbows, trust meters, floating promo badges.
3. Inset hero collage; multi-layer glow; engagement streaks.
4. Invent news or crown a voice to make the UI look cleaner.
5. Silent personalization / For You theater.
6. Turning on `w_impact` as a visual excuse.

## Place tokens (v0.1)

| Token | Role | Notes |
|-------|------|-------|
| `--bg` / `--bg-deep` | Fleuve / ice wash | Cool slate, not cream |
| `--ink` | Stone reading | High contrast, calm |
| `--accent` | Lookout copper-teal | Cap / copper roof cue — not purple |
| `--nest-near` / `--nest-province` / `--nest-linked` | Nest depth edges | Approaches only |

## Named place images (full-bleed allowed only when listed)

_None yet._ Do not invent a Quebec City photo. Empty `public/place/` is correct.

## Social card (OG image)

`public/assets/og-default.png` (1200×630) is a **graphic identity card**, not a
place photograph: the brief's ice/stone wash, the `vigie.` wordmark with the
copper-teal dot, the promise « Québec, à hauteur de vie », the nest-depth
hairlines (city → province → linked) and the lookout compass glyph — the same
palette and type voice as the page. It is deterministic artwork (System.Drawing,
Georgia + Segoe UI), carries no invented geography, and is served from our own
origin. It does not open the door to a stock or AI place photo: `public/place/`
stays empty until a real, named Quebec City image is listed above.

## Motion (intentional, finite)

- Brand settle on Arrival
- Approach enter stagger (store order)
- Field open (existing)

All honor `prefers-reduced-motion: reduce`.

## Preview images (resident brief)

Publisher `og:image` only, fetched at collection time (`scripts/fetch_brief_media.py`) and served from our own origin — reading the brief never contacts a publisher. Bytes are sniffed, never trusted: no SVG, no mislabeled HTML, 700 KB cap. A silent publisher means no image — not a filler, never stock. Same treatment for every card that has one: full-width strip, 1200/630, hairline `--line` border, `object-fit: cover`; no invented hierarchy. New articles get their image on the next refresh; the lag is honest, placeholders are not.

**Equal skeleton (v0.6.1):** every story card carries a media block of the same height — the publisher's image, or a typographic face (source name in serif, nest label, « Sans image publiée par l'éditeur ») when the publisher published none. No image, no hole: columns stay aligned, and the absence is named, never hidden, never invented. Same law in one and two columns.

## Structural reading (roadworks section only)

The anomaly beacon (`anomalies.md`, `anomaly-beacon-v1`) and street-level joins (`edge.md`, `edge-atlas-v1`) live **inside the Travaux et entraves section** — never the first viewport, never the hero. Fixed-threshold collection facts in one quiet accent-bordered block; no red, no pulse, no alert chrome. Zero measured anomalies means the brief is byte-identical to a brief without the feature. A join always presents itself as « Rapprochement proposé … une mention textuelle, pas une preuve géographique » — never as geographic proof, never as importance.

## Resident brief — front-end law (v0.2, 2026-09-19)

Surface: the resident front door `public/index.html` (via `resident_brief.py`,
orchestrated by `rank_display.py`). Method id: `brief-front-end-v0.2`.

North star: an effortless epistemic instrument. Orient in ten seconds, verify in
sixty, act and leave. The SOTA we borrow: layer-cake scanning (headings carry the
meaning), progressive disclosure capped at two levels, answer-first (BLUF),
adaptive variable type, persistent wayfinding, and static maps with a legend
(never a decorative basemap).

### Do

1. **Answer first.** The edition opens with « En un coup d'œil »: sentences, each
   a measured fact of this collection and a jump link to its section. Never a
   stat strip, never a score.
2. **One voice, self-hosted.** Newsreader + Figtree, variable, latin/latin-ext,
   served from our own origin. No external font request on any surface.
3. **Adapt to the reader.** `prefers-color-scheme` and `prefers-contrast`; weight
   and grade eased for reversed contrast; `prefers-reduced-motion` honored.
4. **Effortless finding.** Sticky masthead, scroll-spy `aria-current`, and a
   Ctrl/⌘-K command palette (sections + articles + paste-URL jump, or an honest
   « cette URL n’est pas dans cette édition »). All progressive: without JS
   every article still reads.
5. **Continuity on-device.** The reading marker names the newest articles since
   the last visit; never a tracking of world changes.
6. **Honest emptiness.** A quiet edition renders no digest and no live-looking
   chrome; absence is reported as absence.

### Do not

1. No external request, no analytics, no font CDN.
2. No hero stat strip, no score, no bias meter, no engagement device.
3. Never invent a place image or a map; a map ships only with a real basemap and
   an honest legend.
4. Never promote Travaux / structural reading into the hero or the masthead.

### Spatial reading (shipped)

- **Spatial sketch of active obstructions** (v0.2): a static, self-hosted SVG dot
  density over the official coordinates, with a legend, a scale bar and the
  label « schéma de répartition, pas une preuve géographique ». No basemap is
  drawn (none is licensed here); the official map stays the reference. It
  collapses to zero HTML without usable coordinates and never reaches the hero.

### Cross-source reading (shipped)

- **Voice roster** (v0.2): every followed institution in one scannable list —
  spoke (with its usable article count and an official mark) and quiet,
  including media silence the official-only block does not name. Absence stays
  an absence; grouping stays a rapprochement, never a contradiction.
- **Collection timeline** (v0.2): a collapsed per-dossier timeline of the
  editions Vigie recorded (sources / articles / official voices), labelled
  counters — not escalation; an absent edition is not a resolution.
- **Field-level revisions** (v0.3): when the dossier question is reformulated,
  the initial question and up to two later revisions are quoted verbatim in the
  timeline. A wording change is never presented as a change of meaning.
- **Story desk** (v0.4): a dossier is Full Coverage without a left/right bar —
  up to three verbatim headlines (one per institution, official first, then
  date, then nest), a « pourquoi ici » line, a device-local sort, and « trouver
  une institution ». Mute / titles-only / focus are on-device accessories; they
  hide cards, they never rewrite `latest_ranked.json`.
- **Dossier record pages** (v0.5, `recits-v1`): every dossier of the edition
  gets its own addressable page (`/dossiers/<issue_id>.html`, indexed at
  `/dossiers.html`) — the complete confrontation: all voices with all verbatim
  headlines, the full silence roster, the collection timeline and the measured
  evidence. One calm column in the house palette, zero JavaScript, print-first,
  linked from each dossier card and from the sitemap. A page exists only while
  the dossier is in the current edition; absence is never a resolution.
- **Shared edition sentence** (v0.4): « Cette édition est la même pour chaque
  lecteur. Les filtres et les repères restent sur cet appareil. »

### Official civic calendar (v0.4)

The participation calendar (`civic-html-v1`) lives in its own section after
Travaux, never the masthead, never the ranking, never the silence map. Titles
and date windows are the City's words. An absence from the collection is not a
clôture.

### Corridors (shipped)

- **Saved corridors** (v0.2): the reader follows up to twelve declared street
  names; Vigie marks the displayed declarations that literally name them and
  counts them across the whole active collection (via a small street index
  island). On-device only (`vigie.corridors.v1`), no geolocation, no computed
  route effect, no server round-trip. A shared name is never geographic proof.

### Smartphone (v0.3)

Most readers arrive on a phone, so the brief is mobile-first, still without any
external request:

- **Finger-sized targets**: every control gets a ≥44 px hit area on coarse
  pointers (`@media (pointer:coarse)`).
- **No focus zoom**: fields are ≥16 px on small screens, so iOS never zooms the
  page when the search box is focused.
- **Safe areas**: `viewport-fit=cover` + `env(safe-area-inset-*)` keep the sticky
  masthead and content clear of notches.
- **Readable small text**: the 8–10 px kickers/labels are raised on small screens,
  and long titles/excerpts wrap (`overflow-wrap:anywhere`) instead of overflowing.
- **Installable**: `site.webmanifest` + 192/512 icons + `apple-touch-icon`, so
  "Add to Home Screen" gives a proper Vigie icon and standalone display. It is a
  convenience, not an app: no service worker, no offline cache, no tracking.
- **Immutable images**: stored media is content-addressed
  (`sha256(bytes)[:20].<ext>`) and served
  `Cache-Control: public, max-age=31536000, immutable`, so a phone never
  re-downloads an image and a replaced publisher image gets a new URL instead of
  a stale cached copy. Intrinsic `width`/`height` come from the file header, so
  the browser reserves the box without a decoder.

**No generated `srcset`.** The feeds declare no smaller variants
(`media:thumbnail` absent) and the pipeline is stdlib-only — there is no
resizing. One honest image per card, content-addressed and cached forever, is
the right trade until a real variant source exists.

### Roadmap (designed, not shipped)

- **Resident validation** (product, not code): the five walkthroughs in
  `FRICTION.md` remain the open risk.
- **Saved-corridor validation**: the daily-mobility experiment with real
  repeated-corridor travellers.

### Wide composition (v0.6, shipped)

The edition is a **front page**, not a document. On desktop (≥1180 px) the canvas
grows to the screen (container up to 1840 px, fluid margins), and every section
flows in columns instead of one monolithic rail:

- **Stories** — two columns; **Travaux, Consultations, Dossiers** — two columns
  each; **Depuis la dernière édition** — three columns with spanning headers;
  **Méthode** — two columns. Scroll depth roughly halves.
- **The digest as persistent wayfinding** — « En un coup d'œil » becomes a
  sticky strip under the masthead while the stories scroll, so the answer-first
  sentences stay reachable at any depth.
- **Order is method** — the DOM order and section order never change; width is
  layout, order is the published method. Record pages (récits) keep a document
  measure: they are records to read, not front pages to scan.
- Mobile and tablet stacks are untouched; everything collapses to the existing
  single column below 1180 px. Zero DOM change, zero JavaScript, deterministic
  output, print untouched (glance hidden, links expanded).

### Do not (wide composition)

1. No section reordering, no dashboards, no three-zone command field on the
   resident front door — the front page is editorial, the workbench stays in
   the explorer.
2. No fixed viewport tricks: everything is fluid `clamp()`/`min()` within the
   existing palette and tokens.

### The instrument — three dials (v0.7, shipped)

Spatial composition is done; the missing layer was *temporal* composition.
The edition now has three moments, each its own surface, all fed by the same
sealed stores:

- **Le départ** (`/partir.html`): the departure moment — six most restrictive
  declared obstructions, the reader's corridors marked on-device with their
  own worst restriction, what changed, the seal. Situational freshness, never
  a real-time feed; the official map stays the reference.
- **La question** (on the brief's dossiers, the récit pages and the départ):
  the promise line — « Aucun document officiel dans les N éditions suivies » —
  measured only over editions whose record carried the counter, stated as
  presence, never as an answer, never as a verdict.
- **La mémoire** (`/memoire.html`): the sealed chain made readable — per
  edition, the dossiers, the voices, who spoke and who did not. The archive
  Google cannot copy: the record of the city *including its silences*.

Law: order never changes, no real-time chase, no accounts, no tracking; the
departure screen's corridor marks live only in the reader's browser, and the
memory shows only what was sealed (Vigie's labels only).

## Decision log

| Date | Decision | Owner |
|------|----------|--------|
| 2026-09-16 | Ship law + Arrival polish; no invented QC photo; nest wash only | Bucky + le porteur |
| 2026-09-17 | Verification: composition order; silence chrome de-purpled to slate; place/ empty; tests lock Do/Do-not | Bucky + le porteur |
| TBD | Add full-bleed only with a real named place image in `public/place/` and a row above | le porteur |
| 2026-09-18 | Brief preview images: publisher og:image, locally re-hosted and sniffed; never stock, never invented | Bucky + le porteur |
| 2026-09-19 | Edge Atlas + anomaly beacon: structural reading confined to the Travaux section; hero untouched; both collapse byte-identical when empty | Bucky + le porteur |
| 2026-09-19 | Brief front-end v0.2: answer-first digest, self-hosted variable type, dark/contrast adaptation, sticky masthead + Ctrl/⌘-K palette, on-device continuity; no external font request on any surface | Bucky + le porteur |
| 2026-09-19 | Roadworks stays out of hero and masthead; one quiet digest jump link is allowed (regression renamed to check the masthead only) | Bucky + le porteur |
| 2026-09-19 | Phase E spatial reading: store carries each event's first official vertex; the brief draws a static self-hosted dot-density scheme with legend and scale, no basemap, no geographic-proof claim | Bucky + le porteur |
| 2026-09-19 | Phase F cross-source reading: per-dossier voice roster (spoke + quiet, media silence named) and a collapsed collection timeline from the durable history (counters only, no escalation) | Bucky + le porteur |
| 2026-09-19 | Saved corridors: opt-in declared street names, literal match, on-device only, no geolocation and no route effect; the street index island counts over every active event | Bucky + le porteur |
| 2026-09-19 | Social card: deterministic graphic OG image (palette, wordmark, nest hairlines, compass glyph) — not a place photo; `public/place/` stays empty | Bucky + le porteur |
| 2026-09-20 | Smartphone pass: 44 px touch targets, no iOS focus zoom, safe-area insets, readable small text, installable manifest + icons (no service worker) | Bucky + le porteur |
| 2026-09-20 | Story desk (face-on headlines, pourquoi ici, paste-URL, on-device lenses); civic HTML participation collector; conservative FR/EN event join | Bucky + le porteur |
| 2026-09-20 | Images: content-addressed filenames + immutable `/media/*` caching + header-measured dimensions; generated `srcset` refused (no publisher variants, no stdlib resizing) | Bucky + le porteur |
| 2026-09-20 | Dossier record pages (récits-v1): addressable per-dossier pages from the same stores, zero JS, edition-scoped retention — no lingering publisher text | Bucky + le porteur |
| 2026-09-20 | Wide composition (v0.6): full-width modular front page on desktop — 2-col stories/travaux/civic/dossiers, 3-col changes, sticky glance wayfinding; order never changes, mobile untouched, zero DOM/JS | Bucky + le porteur |
| 2026-09-20 | Equal card skeleton (v0.6.1): fixed-height media block on every story — publisher image or a typographic face naming the absence; corridors rebuilt as an aligned chip strip; lean-scan first view 6 → 12 cards | Bucky + le porteur |
| 2026-09-20 | Method pages (methode-v1): the method files become first-class pages under /methode/ (house chrome, zero JS, print-first); humans never land on a raw .md/.yaml again — only the labelled /index.html.md twin remains linked; no served file names the founder | Bucky + le porteur |
| 2026-09-20 | The instrument — three dials (depart-v1, promesse-v1, memoire-v1): the departure screen (situational freshness, on-device corridors), the promise line (official presence counted only over measured editions), and the readable memory of sealed editions | Bucky + le porteur |
| 2026-09-20 | Instrument surface: seal line with the live registre chain root; edition silence bar (who spoke / who did not) before the departure strip; departure strip before the editorial brief; dossier constellation (nest zones, voice bar) on wide screens | Bucky + le porteur |
| 2026-09-20 | Regard croisé reading order: framing meta → verbatim headlines → full chambre (official mark on quiet institutions) → official-only silence names; « trouver une institution » indexes the whole chambre; the absence disclaimer appears once, on the roster | Bucky + le porteur |
