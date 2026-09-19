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
