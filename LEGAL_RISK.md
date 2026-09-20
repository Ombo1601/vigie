# LEGAL_RISK.md — Is Vigie legal in Quebec / Canada?

Investigation of 2026-09-19. Engineering due diligence, **not legal advice** — before any
monetization, have a Quebec IP lawyer review this file. Laws cited as they stood on the
investigation date.

**Verdict: the operation is defensible today, with two real exposures (re-hosted images,
publisher terms of use) and a hard rule: staying non-commercial is a legal shield, not just
a business choice.**

---

## 1. What Vigie actually does (the factual record)

Established by code audit, not memory:

| Fact | Where |
|---|---|
| Never copies article bodies. Only RSS titles (≤300 chars, verbatim, truncated never rewritten) and feed summaries | `resident_brief.py` TITLE_CAP |
| Displays an excerpt ≤240 chars, labeled « Extrait du flux de {source} » | `resident_brief.py` article_html |
| Every item: byline (source name + date) + hyperlink to the original (`rel="noopener noreferrer"`) | `resident_brief.py` |
| Images: downloaded from the publisher's own channels (og:image on the article, or `<enclosure>`/`<media:content>` in the publisher's own feed), ≤900 KB, served from vigieqc.com; practice disclosed in the public method section | `fetch_brief_media.py`, method section |
| No image is invented, cropped, or modified; no image without a publisher channel → no image at all | `fetch_brief_media.py` law |
| 403/410/bot walls are respected, never circumvented; no paywall is ever touched | media sentinel retry policy |
| WZDX roadworks: CC-BY 4.0, attribution rendered on the page (« Données : … (CC-BY 4.0, via Données Québec) ») | `resident_brief.py` rw-attr |
| Civic HTML calendar: Ville participation table, `IdProjet` identity, titles and date windows quoted verbatim, each item links to the City fiche; robots.txt respected; never ranked with articles | `ingest_civic.py`, `resident_brief.py` civic section |
| 13 sources, all named in public (`sources.yaml` is served); cuts are logged, never silent | sources.yaml law |
| No accounts, no tracking, no ads, no revenue; saved articles stay in the visitor's browser | method section |
| Dossiers are « proposés », with « Rapprochement automatique à vérifier » disclaimer; questions never assert facts | cluster/rank law |
| Fetcher identifies itself honestly (`Vigie/0.2 (+https://vigieqc.com/legal.md)`; a disclosed browser fallback is used only for hosts that stall automated readers at *transport* level); conditional GETs (ETag/304) minimize load | `ingest_rss.py` |
| Raw feed XML snapshots kept in `data/raw` (internal, never published) | ingest law |
| Known DOM excess: `data-search` attribute embeds up to 2000 chars of feed summary per item (search index), more than the 240 displayed | `resident_brief.py` SUMMARY_CAP — see remediation R4 |

## 2. Online News Act (Bill C-18) — NOT APPLICABLE

The Act (in force June 2023) forces **digital news intermediaries** to pay news businesses.
The Application Regulations set conjunctive thresholds: operator revenue **> $1 billion CAD**
AND a search engine with **≥20 M unique Canadian visitors/month** or a social media service
with **≥20 M Canadian users/month**. Only Google has ever notified the CRTC. Vigie misses
every threshold by orders of magnitude and is neither a search engine nor a social network —
it cannot be designated at any realistic scale. Note the Act defines "making available"
broadly (aggregation/ranking counts), so this exemption rests on the size thresholds, not on
what Vigie does. **Risk: none.**

## 3. Copyright — text (titles + ≤240-char excerpts + links)

**Framework.** Copyright protects original *expression*, not facts or ideas. Canada has no
EU-style database right — collections of facts (the brief, the roadworks table, rankings) are
free. Headlines alone were historically an insubstantial part (*Francis Day & Hunter*); but
**Cedrom-SNi inc. c. Dose Pro inc. (C.S. Québec, 2017)** — the controlling Quebec warning —
held that **titles + opening lines of La Presse / Le Devoir / Le Soleil articles ARE a
substantial part**. Dose Pro, a *paid* press-clipping service, still lost fair dealing
because: profit motive, no traffic to the papers, journalist names not credited, no original
commentary. Vigie indexes the same three papers, so assume a Quebec court would find
title+excerpt reproduction is *prima facie* infringement — and must be saved by fair dealing.

**Fair dealing (s. 29.2 news reporting; s. 29 research — *CCH*, 2004 SCC 13: user's right,
large and liberal).** Purpose: informing residents about their city = news reporting; the
Cedrom court's objection ("no commentary") is weaker here — Vigie adds curation, geo-ranking,
dossiers, published method, and drives every reader to the original via link. The six CCH
factors as applied to Vigie:

| Factor | Vigie | Weight |
|---|---|---|
| Purpose | News reporting for residents; non-commercial; no paywall on Vigie's side | Strong ✓ |
| Character | Single finite edition/day, ~350 items, each a short excerpt; not multiple copies per client (Dose Pro's flaw) | Strong ✓ |
| Amount | ≤240 chars displayed + verbatim title — the minimum that lets a reader decide to click (SOCAN v. Bell "preview" logic, 2012 SCC 36) | Strong ✓ (but see R4: DOM carries 2000) |
| Alternatives | None: you cannot preview an article without quoting it; CCH says an available licence is irrelevant to fairness | Strong ✓ |
| Nature of work | Published news, published in RSS feeds *for* redistribution; links increase dissemination | ✓ |
| Effect on market | Links send traffic to publishers (Dose Pro's fatal flaw was the opposite: clients never visited the papers) | Strong ✓ |

Geist's summary of the settled position: headlines + links + one-or-two-sentence summaries by
platforms are "generally permitted under Canada's fair dealing rules and do not require a
licence." **Risk: low** — provided attribution keeps improving (s. 29.2 *requires* source
**and author name if given in the source**; Cedrom and *Stross* both turned on missing author
credit → remediation R1).

## 4. Copyright — images (the biggest exposure)

Vigie **reproduces and re-hosts** publisher photos (download → served from vigieqc.com).
That is reproduction + communication to the public of a *whole work* — the most protected act:

- **Stross v. Trend Hunter (FCA 2021)**: re-hosted photographs were reproduced "essentially
  in their entirety"; fair dealing for news reporting **failed** partly because the
  photographer's name was not mentioned — **a hyperlink to the crediting page is NOT enough**
  (s. 29.2(b)). Damages were small (~$4 k + ~$9.5 k costs) because use was brief and
  non-substitutive.
- **Trader Corp v. CarGurus (Ont. 2017)**: even *hotlinking/framing* is "making available by
  telecommunication" (s. 2.4(1.1)) — statutory damages $305 k over thousands of photos
  (**commercial** infringement). So switching to hotlinks would NOT automatically be safer in
  Canada; it would also break the privacy doctrine (browser never contacts publishers).

Vigie's distinguishing facts: images come **only from the publisher's own distribution
channels** (og:image, or media attached to their own feed — JdQ's MRSS attachments exist
precisely to be rendered by feed readers: a strong implied-licence argument), unmodified,
size-capped, beside full source attribution and a link, non-commercial, disclosed publicly in
the method section, takedown-friendly. **Risk: moderate while non-commercial; the single most
likely subject of a claim if Vigie gains traction.**

**The commercial cliff (Copyright Act s. 38.1):** non-commercial infringement → statutory
damages **$100–$5,000 for ALL works combined**; commercial → **$500–$20,000 PER work**. With
60 images per edition, monetizing without licences converts a $5 k ceiling into a
six-figure exposure. This is why R7 (licences before revenue) is a hard gate.

## 5. Contract / terms of use (the realistic first blow)

A lawsuit is unlikely; a **cease-and-desist is the realistic scenario** if traction grows:

- **CBC** (terms on file): feeds "for personal, noncommercial use"; display/excerpt/link
  allowed on "personal web site… for personal, noncommercial purposes", links must redirect,
  no distortion, attribution « CBC », removal on request. Vigie is non-commercial but not
  *personal* → strictly, permission needed (permissions@cbc.ca).
- **Radio-Canada**: same posture ("contact them before commercial reuse" — already logged in
  sources.yaml license_note).
- **Quebecor (JdQ)** — the most aggressive house in Quebec (blocked Google over C-18,
  litigious history): terms prohibit compilations/reproduction "**except as expressly
  permitted by these General Terms or applicable law**". The "applicable law" carve-out
  preserves fair dealing, but Quebecor is the most likely sender of a letter.
- **Le Soleil / Coops de l'information, Le Devoir, La Presse**: standard all-rights-reserved
  ToS; Cedrom shows the papers do sue over systematic title+lead reproduction — but against a
  *paid* clipping service, not a free attributed linker.

Enforceability nuance: these are browsewrap terms Vigie never assented to; Canadian courts
require reasonable notice + assent for browsewrap, and statutory fair dealing cannot be
contracted away *in rem* (it binds parties, not the right). Still, once Vigie *knows* the
terms (this file is knowledge), continuing is a worse factual posture. **Mitigation is cheap:
the sources.yaml cut law (disable + cut_reason, never silent) is already the compliance
mechanism — a C&D becomes a one-line config change, and the brief degrades honestly
(coverage section reports the silence).** Risk: medium likelihood of a letter at traction,
low severity.

## 6. Everything else

- **Anti-circumvention (s. 41 TPM)**: Vigie respects 403/410 and never cracks a paywall or bot
  wall (JdQ's 403s are *diagnosed and worked around only via the publisher's own feed media*).
  Codify as permanent law (R9). **No exposure as long as this holds.**
- **Defamation (Quebec art. 1457 CCQ / common law)**: *Crookes v. Newton* (2011 SCC 47) — a
  hyperlink, by itself, is **never** publication; liability only if Vigie's own text repeats
  or endorses defamatory content. Vigie repeats only the publisher's own headline/excerpt,
  attributed, and dossiers carry « proposé / à vérifier » disclaimers and never crown an
  answer. **Risk: very low.**
- **Charter of the French Language (Bill 96)**: commercial websites serving Quebec must be
  available in French (art. 52; OQLF fines $3 k–$30 k). Vigie is French-first end to end;
  English items are labeled « Article en anglais » (third-party content, quoted). Also, the
  Charter binds *entreprises* (profit-seeking acts) — Vigie currently isn't one. **Compliant.**
- **Privacy (Law 25 / PIPEDA)**: no accounts, no tracking, no profiling, no location; saved
  articles stay on-device; method section says so. News images may depict people, but they
  were lawfully published by the publisher. **Minimal.**
- **Open data / Crown**: WZDX = CC-BY 4.0 **with attribution rendered on the page** ✓.
  Ville/Gouv QC/Hydro communiqués are published *for* redistribution (Crown copyright allows
  reproduction with attribution; quebec.ca reuse terms). **Compliant.**
- **Database rights**: none in Canada. The compilation itself (selection/ranking) could attract
  a thin compilation copyright owned *by Vigie*, not owed to anyone.

## 7. Risk matrix

| Scenario | Likelihood (now → with traction) | Severity | Notes |
|---|---|---|---|
| Online News Act designation | none → none | — | $1 B / 20 M thresholds |
| C&D from Quebecor / RC / CBC | low → **medium** | **low** | remedy = cut source (mechanism exists) |
| Copyright claim, images | low → medium | low non-comm. ($100–5 k total) / **high commercial ($500–20 k per work)** | R1, R2, R7 shrink this |
| Copyright claim, text | very low → low | low | fair dealing strong; R1 completes s. 29.2 |
| Defamation | very low | low | Crookes + disclaimers |
| OQLF / language | none | — | French-first |
| Privacy (Law 25) | very low | low | no personal data operations |

## 8. Remediations (ordered; R1–R4 are code, cheap, do before traction)

**Status 2026-09-19: R1–R6 implemented in code** (author byline from `dc:creator`/`author`/
`media:credit`, « Photo : {source} » caption on every re-hosted image, public `/legal.md`
linked from the brief footer, `data-search` shrunk to title + displayed excerpt, honest
`Vigie/0.2 (+https://vigieqc.com/legal.md)` UA with disclosed per-host browser fallback for
transport-level stalls only, 30-day raw-snapshot retention). R7–R10 codified below and in
TECHNICAL_PROCESS.md. Tests: `tests/test_legal_remediations.py`.

- **R1 — Author attribution (s. 29.2(b))**: capture `<dc:creator>` / `<media:credit>` /
  byline when the feed provides it and render it (« Par {author} — {source} »). Cedrom and
  Stross were both *lost on missing author names*. This is the single highest-value fix.
- **R2 — Image credit line**: render « Photo : {source} » adjacent to every re-hosted image
  (plus author when R1 has one).
- **R3 — Public legal page** (`/legal.md` or method subsection): what Vigie copies and why
  (fair dealing, news reporting, attribution), non-commercial declaration, **named contact +
  takedown commitment** (any publisher request honored within one edition), pointer to
  sources.yaml. Notice-and-takedown posture is what kept Stross damages nominal.
- **R4 — Shrink the DOM excess**: `data-search` should carry title + the displayed ≤240-char
  excerpt, not the 2000-char summary. Aligns the *amount* factor with what a visitor sees.
- **R5 — Honest User-Agent**: `Vigie/0.2 (+https://vigieqc.com/legal.md)` without the Chrome
  spoof prefix where feeds accept it; keep browser fallback only for origins that block all
  automated readers, and say so on the legal page (good-faith optics).
- **R6 — Retention**: cap `data/raw` XML history (e.g. 30 days) — internal copies are the
  least defensible ones; conditional fetching already minimizes new bytes.
- **R7 — Monetization gate (HARD LAW)**: no revenue, no ads, no paid tier, no sponsored
  placement until (a) written permissions from CBC (permissions@cbc.ca), Radio-Canada,
  Quebecor, or those sources are cut, and (b) an IP lawyer signs off. s. 38.1 makes the
  non-commercial state a $5 k-total ceiling; commercialization multiplies exposure per work.
  **Founder decision (2026-09-19): Vigie stays free and non-commercial permanently** — the
  goal is public access to information, not revenue. This gate is therefore standing law,
  published on `/legal.md`; the permissions above are only needed if that decision is ever
  reversed, and would have to be obtained *before* the reversal.
- **R8 — Never rewrite**: titles/excerpts stay verbatim (already law: "truncated, never
  padded or rewritten") — distortion would break CBC's terms and add defamation surface.
- **R9 — Codify no-circumvention**: respecting 403/410/paywalls becomes explicit law in
  TECHNICAL_PROCESS.md, so no future feature "fixes" a bot wall.
- **R10 — Honor opt-outs**: a publisher asking to leave → `enabled: false` + `cut_reason`,
  same day. The silence map then reports the cut honestly — the doctrine already matches the
  legal remedy.

## 9. Bottom line

Vigie today is a **free, attributed, link-out, short-excerpt, publisher-channel-only**
aggregator — the posture Canadian fair dealing exists to protect (*CCH*, *SOCAN v. Bell*,
Berne art. 10(1) quotation right), and structurally outside the Online News Act. The Quebec
precedent that punished aggregation (*Cedrom*) punished a **paid** clipping service that sent
**no traffic** and credited **no authors** — Vigie is the mirror image on all three axes.
The real exposures are (1) re-hosted whole images and (2) "personal use" feed terms at the
public broadcasters and Quebecor; both are managed by R1–R3 now and gated hard by R7 before
any money ever touches the project. If a letter arrives, the answer is already built: cut the
source in one line, report the silence honestly, and keep serving everyone else.
