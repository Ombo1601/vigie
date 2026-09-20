# AGENTS.md — working in Vigie

Vigie is a free, non-commercial, French-first Québec City news brief: static
HTML, Python 3.12+ **stdlib only** (no pip, no SaaS). Read `VISION.md`,
`RENT.md`, `TECHNICAL_PROCESS.md` and `LEGAL_RISK.md` before changing behaviour.
The non-commercial vow, author/image attribution (R1/R2), never-rewrite (R8),
no-circumvention (R9) and same-day opt-out (R10) are permanent house law.

## Where collection runs

The collector is **not** a laptop. `.github/workflows/vigie-refresh.yml` runs every
6 hours: it restores the cross-edition state tarball from the **private**
`Ombo1601/vigie-state` repo (`scripts/state_pack.py unpack`), runs
`scripts/refresh.py` (pipeline → verify → stage → `vercel deploy --prod`), and
persists the state back (`state_pack.py pack`, uploaded as the `state` release
asset). Secrets: `VERCEL_TOKEN` and `STATE_TOKEN` (contents:write on
`vigie-state`). Vercel git auto-deploy is disabled on purpose — this chain is the
only production writer; a bare `git push` must never publish a data-less build.
The public repo never carries publisher content; `data/` stays gitignored and
only the private state store holds it. The Windows scheduled task remains an
optional fallback and runs the same `refresh.py`.

When `data/` is needed locally, seed it by unpacking the private state tarball;
never commit it.

A **failed refresh opens (or comments on) a GitHub issue** titled “Vigie refresh
failed (automated)”; the next successful run closes it — diagnosed silence, no
external alerting service. `.github/workflows/keepalive.yml` commits a monthly
heartbeat so GitHub never disables the schedules (it does that after ~60 days
without repository activity). `.github/workflows/ci.yml` runs
`verify.py --code-only` on every push/PR, so the six-hourly refresh is not the
first place a broken commit surfaces.

`.github/workflows/vigie-roads.yml` runs **hourly** and refreshes only the
official WZDX lane (`refresh.py --roads-only`): ingest → anomalies/edges →
`pipeline --render-only` → verify → deploy, and **only when the declared
obstructions actually changed** (a content signal ignores collection clocks and
presence counters). It never runs normalize/enrich/cluster, so it creates no
edition and leaves the change ledger, dossier history and edition metrics
untouched. It shares the `vigie-refresh` concurrency group with the full refresh.

### The record layer (registre · mémoire · substrate · récits · méthode · départ · affiche)

The HTML brief is a *view*; the record is the product. Seven emitters run
inside the render step (`rank_display.main`, after the brief and the ambient
twin), from the same stores, with no second brain:

- `scripts/registre.py` — **Le Registre**: seals every edition
  (`leaf = sha256(canonical record)`, `root = sha256(prev || leaf)`), keyed by
  the collection clock so re-renders never mint a new seal; keeps per-edition
  voice rows (who spoke / who did not, institution seats) and a roadworks chain
  (one root per change of the City's active set). State:
  `data/registre/registre.json` (packed with the private state). Public:
  `public/registre.html`, `public/registre/{checkpoint.txt,chain.json,institutions.json,travaux.json}`.
  Verify any downloaded chain with `python -X utf8 scripts/registre.py --verify chain.json`.
- `scripts/memoire.py` — **La mémoire**: `public/memoire.html` +
  `public/memoire/<seq>.html` — the sealed chain made readable: per edition,
  the dossiers (Vigie's labels only — attributed headlines stay empty), the
  voices, who spoke and who did not, and the ledger. Zero JavaScript; pages
  exist only for published seals.
- `scripts/depart.py` — **Avant de partir**: `public/partir.html`, the
  departure instrument — most-restrictive declared obstructions, the reader's
  corridors marked on-device (island + literal folding, no account, no
  position), what changed since the last edition, the seal. One small inline
  script is the only enhancement; the page is complete without it.
- `scripts/substrate.py` — the machine layer: `public/llms.txt` (llms.txt v2),
  `public/index.html.md` (Markdown twin, advertised with
  `rel="alternate" type="text/markdown"`; the brief also carries
  `rel="describedby" href="/llms.txt"`), `public/delta/latest.json`
  (`delta-v1`, cursor = chain root).
- `scripts/recits.py` — **Les Récits**: `public/dossiers.html` + one complete,
  addressable record page per current-edition dossier (`public/dossiers/<issue_id>.html`):
  every voice with every verbatim headline, the full silence roster, the
  collection timeline, measured evidence. Zero JavaScript; pages exist only for
  the current edition (a quiet dossier keeps its counters in the registre,
  never its page — no lingering publisher text).
- `scripts/method_site.py` — **La Méthode**: `public/methode/<slug>.html` +
  `public/methode/index.html` — every published method file as a first-class
  page (zero JS, print-first, cross-links remapped to pages; the sources page
  is data, never raw YAML). The `.md`/`.yaml` files stay staged as the machine
  twins; human surfaces link only the pages (the sole `.md` link left anywhere
  is the labelled `/index.html.md` twin). No served file names the founder.
- `scripts/affiche.py` — **L'affiche**: `public/affiche.html`, a print-first
  neighbourhood sheet (`public/assets/affiche.css`), no JavaScript.

All seven are fail-soft *inside the render* (a fault is printed, the brief
still renders) but the front door links to `/registre.html`, `/affiche.html`,
`/partir.html`, `/memoire.html`, `/dossiers.html`, `/methode/`, `/llms.txt`
and `/index.html.md`, so `stage_public.validate_site` turns a
missing artefact into a blocked release — diagnosed, never silent. The method
is published as `REGISTRE.md`. After a successful deploy the refresh workflow
commits `anchors/checkpoint.txt` to this repo (identifiers only): git history
is the third-party ordering of the seals, and the commit keeps the schedules
alive.

### Discoverability

Every staged release carries `robots.txt` (permissive; points at the sitemap) and
`sitemap.xml` (front door + published method files), generated by
`stage_public.py`. The brief head carries canonical, `robots`, Open Graph and
Twitter metadata. After a successful deploy `refresh.py` pings **IndexNow**
(`api.indexnow.org`, key file `public/<key>.txt`), which covers Bing — and so
DuckDuckGo — plus Yandex, Seznam and Naver. Google discovers the site through the
sitemap: submit `https://vigieqc.com/sitemap.xml` once in Google Search Console
and Bing Webmaster Tools (owner action; needs their account).

### Rotating the secrets

- **`VERCEL_TOKEN`** — Vercel → Account Settings → Tokens → create one with
  access to the `deemto` team, then
  `gh secret set VERCEL_TOKEN --repo Ombo1601/vigie`.
- **`STATE_TOKEN`** — GitHub → Settings → Developer settings → Personal access
  tokens → **Fine-grained** → resource owner `Ombo1601`, repository access
  *Only select repositories → `Ombo1601/vigie-state`*, permission
  **Contents: Read and write** (release assets included), then
  `gh secret set STATE_TOKEN --repo Ombo1601/vigie`. Put the expiry in a calendar.
- The failure-alert steps use the workflow's own `GITHUB_TOKEN` (`issues: write`),
  never `STATE_TOKEN`.

## Commands (from the repo root)

```text
python -X utf8 scripts/pipeline.py                 # full online collection + render
python -X utf8 scripts/pipeline.py --offline       # rebuild from cached snapshots (no network)
python -X utf8 scripts/pipeline.py --render-only   # re-render from existing stores
python -X utf8 scripts/verify.py                   # tests + syntax + stage + HTTP smoke
python -X utf8 scripts/verify.py --code-only       # code checks without downloaded data
python -X utf8 scripts/serve.py                    # local preview: http://127.0.0.1:8765/
python -X utf8 scripts/refresh.py                  # collect -> verify -> stage -> deploy (production)
```

Run `python -X utf8 scripts/verify.py` (or `--code-only`) after every change.
Production deploys **only** through `scripts/refresh.py`; never deploy
`deploy/public` by hand and never commit `.env.local`, `data/` or `deploy/`.
Tests alone: `python -X utf8 -m unittest discover -s tests`.

## Layout

| Path | Role |
|------|------|
| `scripts/*.py` | one pipeline stage per file, orchestrated by `scripts/pipeline.py` |
| `scripts/registre.py`, `memoire.py`, `substrate.py`, `recits.py`, `method_site.py`, `depart.py`, `affiche.py` | the record layer, emitted by the render step (see above) |
| `anchors/checkpoint.txt` | registre checkpoint committed by the refresh workflow after each deploy |
| `tests/` | unittest (stdlib), live-data checks are guarded with `skipTest` |
| `public/` | hand-authored assets; generated `*.html` is gitignored |
| `public/assets/` | authoritative CSS/JS for the brief |
| `data/` | runtime stores, raw snapshots, ops ledgers (gitignored) |
| `deploy/` | staged release (gitignored) |
| root `*.md` | published method ("law of the house"); `/legal.md` is served |

## Conventions

- **Stdlib only.** No new dependencies, no network in render/verify.
- **Fail-soft.** A corrupt store yields empty facts and exit 0; a feed outage
  never blocks the news pipeline.
- **Atomic writes.** Every store write goes through `scripts/store_io.py`
  (unique-temp replace); identical raw snapshots are hard-linked, never recopied.
- **Diagnosed silence.** Every absence is a recorded fact; absence is never
  "resolved" or "ended". Absence of facts is reported as absence, not health.
- **No rewriting.** Publisher titles/excerpts stay verbatim (truncated only);
  bare emails are never published; 403/410/paywalls are never circumvented.
- **Deterministic output.** No wall clock in ledgers, sorted iteration, stable
  ids (`sha256` of canonical URL). Same inputs must rebuild byte-identically.
- **Line endings.** Text is LF (`.gitattributes`); keep `sources.yaml` LF.
