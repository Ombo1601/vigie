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
   Ctrl/⌘-K command palette (sections + articles). All progressive: without JS
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

### Corridors (shipped)

- **Saved corridors** (v0.2): the reader follows up to twelve declared street
  names; Vigie marks the displayed declarations that literally name them and
  counts them across the whole active collection (via a small street index
  island). On-device only (`vigie.corridors.v1`), no geolocation, no computed
  route effect, no server round-trip. A shared name is never geographic proof.

### Roadmap (designed, not shipped)

- **Field-level revisions**: question / label changes over time, not only counters.

## Decision log

| Date | Decision | Owner |
|------|----------|--------|
| 2026-09-16 | Ship law + Arrival polish; no invented QC photo; nest wash only | Bucky + Inventor |
| 2026-09-17 | Verification: composition order; silence chrome de-purpled to slate; place/ empty; tests lock Do/Do-not | Bucky + Inventor |
| TBD | Add full-bleed only with a real named place image in `public/place/` and a row above | Inventor |
| 2026-09-18 | Brief preview images: publisher og:image, locally re-hosted and sniffed; never stock, never invented | Bucky + Inventor |
| 2026-09-19 | Edge Atlas + anomaly beacon: structural reading confined to the Travaux section; hero untouched; both collapse byte-identical when empty | Bucky + Inventor |
| 2026-09-19 | Brief front-end v0.2: answer-first digest, self-hosted variable type, dark/contrast adaptation, sticky masthead + Ctrl/⌘-K palette, on-device continuity; no external font request on any surface | Bucky + Inventor |
| 2026-09-19 | Roadworks stays out of hero and masthead; one quiet digest jump link is allowed (regression renamed to check the masthead only) | Bucky + Inventor |
| 2026-09-19 | Phase E spatial reading: store carries each event's first official vertex; the brief draws a static self-hosted dot-density scheme with legend and scale, no basemap, no geographic-proof claim | Bucky + Inventor |
| 2026-09-19 | Phase F cross-source reading: per-dossier voice roster (spoke + quiet, media silence named) and a collapsed collection timeline from the durable history (counters only, no escalation) | Bucky + Inventor |
| 2026-09-19 | Saved corridors: opt-in declared street names, literal match, on-device only, no geolocation and no route effect; the street index island counts over every active event | Bucky + Inventor |
| 2026-09-19 | Social card: deterministic graphic OG image (palette, wordmark, nest hairlines, compass glyph) — not a place photo; `public/place/` stays empty | Bucky + Inventor |
