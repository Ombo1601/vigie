# AGENTS.md — working in Vigie

Vigie is a free, non-commercial, French-first Québec City news brief: static
HTML, Python 3.12+ **stdlib only** (no pip, no SaaS). Read `VISION.md`,
`RENT.md`, `TECHNICAL_PROCESS.md` and `LEGAL_RISK.md` before changing behaviour.
The non-commercial vow, author/image attribution (R1/R2), never-rewrite (R8),
no-circumvention (R9) and same-day opt-out (R10) are permanent house law.

## Where collection runs

The collector is **not** a laptop. `.github/workflows/vigie-refresh.yml` runs every
6 hours: it restores the cross-edition state tarball from the **private**
`Ombo1601/vigie-state` repo (`scripts/state_sync.py restore`, which verifies
then calls `state_pack.py unpack`), runs `scripts/refresh.py` (pipeline →
verify → stage → `vercel deploy --prod`), and persists the state back
(`state_sync.py persist`: `state_pack.py pack`, uploaded to the `state`
release). Secrets: `VERCEL_TOKEN` and `STATE_TOKEN` (contents:write on
`vigie-state`). Vercel git auto-deploy is disabled on purpose — this chain is the
only production writer; a bare `git push` must never publish a data-less build.
The public repo never carries publisher content; `data/` stays gitignored and
only the private state store holds it. The Windows scheduled task remains an
optional fallback and runs the same `refresh.py`.

**State moves fail closed** (the tarball is the only copy of the registre).
Restore calls it a first run only on a definitive answer — repository visible
*and* release 404, or a release with no state asset at all — and never while
`anchors/checkpoint.txt` names a seal; any other failure (401/403, 429, 5xx,
network, timeout, garbage) is retried 3× and then fails the job before
`refresh.py` runs. `state.tar.gz` must match its listed size (and GitHub's own
asset digest, when the API lists one), one of the sha256 lines of
`state.tar.gz.sha256` (absent only for the one-time legacy archive or a persist
killed while replacing that sidecar: accepted with a warning). Then two
witnesses judge **both** registre chains, the edition seals and the hourly
roadworks (`travaux`) seals: the git anchor (its seal lines and its `travaux`
line; it may lag, because only the full refresh commits it) and the heads the
last persist wrote into the sidecar (`# edition|travaux <seq> <root>`, written
after its deploy, so the roads lane's seals are witnessed too). A chain that
forks a witness is refused; one that stops before a witnessed seal is
"behind"; a lagging witness is fine. An `anchors/checkpoint.txt` that is
present but malformed fails the restore (an absent one witnesses nothing).
When `state.tar.gz` is missing, unverified, or behind, restore inspects every
dated copy that GitHub's digest or a sidecar line vouches for, keeps those
that satisfy every witness, and takes the highest head seal (then the newest
name) (`::warning:: … restored from state-…`); that run's persist re-publishes
it as `state.tar.gz`. A roads-lane persist killed after its sidecar landed has
no dated copy holding its roadworks seals, so the restore **halts** rather than
re-mint already-published roadworks seq numbers with new roots. Outcome
(`restored|fresh|failed`, source, seal count, digest, size) goes to
`$GITHUB_OUTPUT`; persist runs only after `restored`/`fresh`. It refuses an
edition or travaux chain that shrank or forked or lost
`registre/registre.json`, and a member count or size below half of the
restored state — regenerated caches (`data/media/brief/`, `data/raw/_bodies/`)
excluded — unless the repository variable `VIGIE_STATE_ALLOW_SHRINK=1`
(logged). The full refresh uploads an immutable `state-YYYYMMDDTHHMMSSZ.tar.gz`
first and confirms it; then the sidecar goes up **before** `state.tar.gz`
(`--clobber` deletes before it uploads): the new digest (also under the dated
copy's name), the restored one, the earlier lines carried forward (the newest 8
rolling digests and 24 dated-copy lines, so the dated copies stay vouched for
without GitHub's own asset digest), then the new heads. A run killed at any
point leaves a rolling asset that a sidecar line vouches for, or none, which
the dated copies answer; then it prunes to the newest 12 dated copies. Without
GitHub's digest, a full persist killed after its dated upload but before its
new sidecar landed halts loudly (nothing vouches for the new copy yet). The
roads lane replaces the rolling pair only, through the same checks. Each restore or persist runs
inside an 8-minute budget (every gh call bounded, 150 s per transfer, step
`timeout-minutes: 10`): a hung transfer fails the step and raises the alert
instead of being cancelled silently by the job timeout. The order deploy →
anchor commit → persist is deliberate: a persist that fails after the anchor
is a loud halt, not a silent fork, and the dated copy it uploaded first is what
the next restore recovers from. When the restore refuses, follow the runbook
below ("When the state restore refuses").

**Deploy guards (`refresh.py`).** A production deploy runs only when
`GITHUB_ACTIONS=true` (exit 2 otherwise; `--no-deploy` always works). Before
`vercel deploy`, the chain guard compares the registre about to ship (the
`data/registre` state, verified with `registre.verify_chain`, and the staged
`deploy/public/registre/checkpoint.txt`, which must equal its tip) with the
committed `anchors/checkpoint.txt` and the live `/registre/checkpoint.txt`: a
shorter chain, a genesis reset or another root at a published seq is refused;
a full run must also have sealed a new edition (the roads lane need not). An
unreachable live site is a WARN and the anchor decides; a missing anchor passes
only when the live checkpoint is a definite 404 (true first run). After the
deploy the live checkpoint is re-read (spaced retries) and a persistent
mismatch fails the run. Break-glass, logged loudly: `VIGIE_ALLOW_LOCAL_DEPLOY=1`,
`VIGIE_SKIP_CHAIN_GUARD=1`. The root `vercel.json` `buildCommand` refuses on
purpose, so a bare `vercel --prod` from the repo root fails instead of
publishing a data-less build.

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
  the collection clock so re-renders never mint a new seal. **A published seal is
  immutable**: if a re-render recomputes a different leaf (because the record
  gained a field), the old seal is kept and the divergence is printed — moving an
  anchored root would break the chain's only promise. Keeps per-edition
  voice rows (per institution: entered a dossier / published outside one /
  no items collected / **our** collection failed / not established) and a roadworks chain
  (one root per change of the City's active set). State:
  `data/registre/registre.json` (packed with the private state). Public:
  `public/registre.html`, `public/registre/{checkpoint.txt,chain.json,institutions.json,travaux.json}`.
  Verify any downloaded chain with `python -X utf8 scripts/registre.py --verify chain.json`.
- `scripts/memoire.py` — **La mémoire**: `public/memoire.html` +
  `public/memoire/<seq>.html` — the sealed chain made readable: per edition,
  the dossiers (Vigie's labels only — attributed headlines stay empty), the
  voices with their measured state, and the ledger. Zero JavaScript; pages
  exist only for published seals.
- `scripts/depart.py` — **Avant de partir**: `public/partir.html`, the
  departure instrument — most-restrictive declared obstructions, the reader's
  corridors marked on-device (island + literal folding, no account, no
  position), what changed since the last edition, the seal. One small
  self-hosted script (`/assets/depart.js`) is the only enhancement, so every
  page can carry a strict `script-src 'self'` CSP; the page is complete
  without it.
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
twins; human surfaces link only the pages (the sole `.md` link left on a human
surface is the labelled `/index.html.md` twin; `llms.txt` is the machine
index). No served file names the founder.
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

### When the state restore refuses

The restore already answers the common faults on its own (dated-copy fallback,
carried-forward sidecar, above). When it still refuses — "older than what was
published", "older than what the last persist recorded", "refusing an
unverified state", "an interrupted persist", "a forked chain", "malformed"
anchor — the error lists every dated copy on the release and why each was
passed over. From a clean checkout of this repo, with a token that can read and
write `Ombo1601/vigie-state`:

1. `gh release download state --repo Ombo1601/vigie-state --pattern 'state-*.tar.gz' --pattern state.tar.gz.sha256 --dir recovery`
   (drop the second pattern when the release has no sidecar).
2. `python3 -X utf8 scripts/state_sync.py inspect recovery/<copy> --sidecar recovery/state.tar.gz.sha256`,
   highest seal first: prints the sha256, members, both chain heads, and exits
   0 only when the copy holds the seals `anchors/checkpoint.txt` and the
   sidecar's `# edition|travaux` lines witnessed.
3. `cp recovery/<copy> state.tar.gz && { sha256sum state.tar.gz; grep '^#' recovery/state.tar.gz.sha256; } > state.tar.gz.sha256`
   (keep the old sidecar's `#` witness lines: they are what stops a later restore
   from accepting a copy that lacks the roads lane's seals; `inspect` in step 2
   already proved this copy satisfies them. Drop the `grep` when there is no sidecar.)
4. **The sidecar must be on the release before the archive**, as persist does
   (`--clobber` deletes first, and one `gh release upload` with both files may
   upload them in any order): `gh release upload state state.tar.gz.sha256 --repo Ombo1601/vigie-state --clobber`,
   and only once it succeeded `gh release upload state state.tar.gz --repo Ombo1601/vigie-state --clobber`.
5. Re-run the workflow (`gh workflow run vigie-refresh.yml`).

Last resort — no copy holds the witnessed seals (the run died before its dated
copy existed, or a roads-lane persist died after its sidecar): never start
fresh, never move the anchor back and never upload an older copy as is; all
three fork a published chain. The missing seals are public, as the very
objects `data/registre/registre.json` keeps: edition seals in
`https://vigieqc.com/registre/chain.json` (append to `seals`), roadworks seals in
`https://vigieqc.com/registre/travaux.json` (append to `travaux.seals`; the public
objects omit `signal`, so the next roads run seals its current set once more —
a valid extension). Unpack the newest qualifying copy (`state_pack.py unpack`),
append the missing seal objects verbatim, check with `python -X utf8 scripts/registre.py --verify data/registre/registre.json`,
`python -X utf8 scripts/state_pack.py pack state.tar.gz`,
`{ sha256sum state.tar.gz; grep '^#' recovery/state.tar.gz.sha256; } > state.tar.gz.sha256`, then steps 4–5. The voice rows
of those editions are not recoverable; record that loss in the repository
history rather than hiding it. A fork ("a forked chain") is never repaired by
these steps alone: find which copy matches the published `chain.json` /
`travaux.json` first. A malformed anchor is restored from git history
(`git log -- anchors/checkpoint.txt`), never deleted.

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
