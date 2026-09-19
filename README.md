# Vigie

**Québec, à hauteur de vie.** A finite local briefing: understand what was published, inspect the sources, find a useful next step, and get on with your day.

French-first, account-free, static HTML. No paid APIs, analytics, geolocation, or generated news on the resident homepage. Search and reading markers stay in the browser.

## Run

Python 3.12+ and Node.js for verification. No package installation required. Tested on Windows with Python 3.14.

```text
python -X utf8 scripts/pipeline.py
python -X utf8 scripts/verify.py
python -X utf8 scripts/serve.py
```

Open http://127.0.0.1:8765/

Use `--port 8771` if the default port is occupied.

```text
python -X utf8 scripts/pipeline.py --offline      # rebuild cached RSS, no network
python -X utf8 scripts/pipeline.py --render-only  # render existing enriched data
python -X utf8 scripts/verify.py --rebuild        # tests + offline rebuild + release checks
python -X utf8 scripts/verify.py --code-only      # code checks without downloaded data
python -X utf8 scripts/check_claims.py            # extraction provenance, not truth verification
python -X utf8 scripts/refresh.py                 # collect, verify, deploy to production (Vercel)
```

Production: **https://vigieqc.com** (custom domain; Vercel aliases route there too). Source repo: **https://github.com/Ombo1601/vigie** (public; secrets and data stay out of version control). Verification stages a complete release in `deploy/public/`, checks links and asset hashes, and tests real HTTP GET/HEAD responses. It does not upload. Staging is serialized (lock + atomic swap) and an empty edition is refused, so a good production site is never overwritten by a partial build. `scripts/refresh.py` is the upload path — pipeline → verify → re-link → `vercel deploy --prod` — and the **GitHub Actions workflow** (`.github/workflows/vigie-refresh.yml`, every 6 h) is the runner: it restores the cross-edition state from the **private** `Ombo1601/vigie-state` store (`scripts/state_pack.py`), refreshes, and persists it back, so nothing depends on a laptop. Publisher content never enters the public repo; the Windows scheduled task is an optional local fallback, and every failing step leaves the previous production site up. A failed run opens a GitHub issue that the next successful run closes, and a monthly heartbeat (`.github/workflows/keepalive.yml`) keeps the schedules from being disabled after 60 days without repo activity; `.github/workflows/ci.yml` verifies every push, and an hourly `.github/workflows/vigie-roads.yml` refreshes the official roadworks reading between editions without creating one (it deploys only when the declarations changed). Vercel git auto-deploy is disabled (`vercel.json` `git.deploymentEnabled: false`), so this verified chain is the only production writer and a bare push can no longer replace the brief with a data-less build. A clean checkout still needs one online pipeline run to produce real data.

## Product surfaces

- `/`: French resident brief. Six articles per step, source excerpts, comparisons, place/topic/search filters, saved articles and an explicit reading marker. Two honest change surfaces: “Travaux et entraves” (official WZDX roadwork data, attributed, never ranked with articles; each entry carries its collection presence — first seen, collections seen/missed, an absence never an end; City date revisions and declared endings are relayed literally, never as verified resolutions) and “Depuis la dernière édition” (dossier-level edition diff, shown only when a prior edition exists). The roadworks section also carries two compile-time structural readings, both collapsing to zero HTML when empty: a fixed-threshold anomaly beacon (`anomalies.md` — measured collection facts, never predictions) and proposed street-level joins between declared obstructions and dossiers that literally name the same street (`edge.md` — a shared name, never geographic proof). Each dossier also carries its durable collection history (“Suivi depuis…”: first seen, editions seen/missed — one edition is one collection snapshot, and a missed edition is an absence, never a resolution).
- `/explorer.html`: older experimental evidence workbench, retained for inspection with explicit limitations.
- `/morning.html`: experimental dossier companion from the same issue store.

The brief uses publication dates during the seven days preceding the edition. It starts with Québec and nearby places; broader feeds require an explicit territory choice. Neighborhoods are mentions in source text, not guarantees of geographic impact. Saved markers do not archive publisher articles.

**Front-end law (v0.2).** The brief is an answer-first instrument: « En un coup d'œil » opens the edition with measured facts and jump links to the sections; a sticky masthead, scroll-spy and a Ctrl/⌘-K command palette make finding effortless; continuity (the newest articles since your marker) stays on-device. Type is self-hosted variable Newsreader/Figtree — no external font request on any surface — with `prefers-color-scheme` and `prefers-contrast` support. `DESIGN.md` is the law. Each dossier also shows a voice roster (every followed institution, spoke or quiet) and a collapsed collection timeline; the roadworks section carries a static, self-hosted spatial scheme (dot density with legend and scale — a distribution reading, never a map or geographic proof) and opt-in saved corridors (declared street names, literal match, on-device only, no geolocation, no computed route effect).

Read [PRODUCT_AUDIT.md](PRODUCT_AUDIT.md) for the co-founder assessment, implementation decisions, remaining limits and next experiments.

## Law of the house

| File | Role |
|------|------|
| `VISION.md` | Vow — vision / mission / kill list |
| `sources.yaml` | Finite source chancellery |
| `RENT.md` | Who pays (v0 = Inventor wallet) |
| `ranking.md` | Published weights + change log |
| `FRICTION.md` | Arrival friction log + kill list |
| `FACETS.md` | Opt-in life facets (reorder-only) |
| `DESIGN.md` | Beauty without fog (composition law) |
| `edge.md` | Edge Atlas — literal street-level joins (`edge-atlas-v1`) |
| `anomalies.md` | Anomaly beacon — fixed-threshold structural rules (`anomaly-beacon-v1`) |
| `TECHNICAL_PROCESS.md` | How the pipe works |

## Pipe

ingest (RSS + official WZDX roadworks, conditional fetches) → feed health → normalize → enrich (proposed) → cluster dossiers → edge atlas + anomaly rules (over the official collection) → brief media (publisher images — og:image or the feed's own media — served locally, every miss diagnosed) → rank → resident brief + explorer + morning → edition metrics + watchdog (machine-room ledgers in `data/ops/`)

Classifications and dossiers are provisional. Named institutions do not prove independent ownership or reporting. Publication, collection, grouping and build time remain distinct. WZDX roadwork data is structured official change data, not articles: it bypasses normalize/enrich/cluster/rank, renders in its own finite brief section with attribution and collection diffs, and a feed outage never blocks the news pipeline. Removed from a collection is never reported as ended or resolved.

Source files and `public/assets/` are authoritative. Data snapshots, generated HTML, deployment output, caches and historical patch logs are excluded from version control.

## Success / failure

Wrong: skim-feed; Near me full of world wire; single-voice “Issues”; selling the rank.

Right: a resident returns because city → province → linked chains beat propaganda fog on something that hits their life.
