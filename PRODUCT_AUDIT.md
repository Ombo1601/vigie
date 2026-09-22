# Vigie — co-founder audit and product decisions

Date: 2026-09-17. Scope: local repository, registry, data snapshots, Python pipeline, generated pages, browser behavior and release process. This is a product and implementation assessment, not proof of market demand or the truth of publisher content.

## The judgment

Vigie began as a transparent RSS lookout with an experimental source-comparison interface. Its strongest asset is the refusal to invent news or hide ranking decisions. Its weakest point was the distance between that philosophy and the resident's actual task: **tell me what changed around me, let me check it, and help me decide whether to do anything.**

The product should compete on useful understanding per minute. A resident leaving better oriented is the desired outcome. The changes in this pass establish that front door and repair evidence defects. They do not establish product-market fit, exhaustive coverage, or a defensible business.

## Strengths to preserve

| Strength | Why it matters | Condition |
|---|---|---|
| Québec-first scope | A coherent civic context makes relevance assessable | Require local evidence, not just a local publisher |
| Named, finite source registry | Readers can inspect selection and omissions | Expose unavailable sources and publish changes |
| Source links and excerpts | Evidence stays inspectable | Attribute excerpts and preserve original URLs |
| Public ranking | Editorial choices are visible | Document both score and display rules |
| Source comparison | Readers can inspect different accounts | Never equate grouping with contradiction |
| Static delivery | Cheap hosting and a small operational surface | Avoid unnecessary backend services |
| No account requirement | Removes friction before first value | Make personal storage explicit |
| Existing domain tests | Encodes lessons such as Lévis versus télévision | Test behavior rather than preserving old copy forever |

## Defects and implemented changes

| Finding | Consequence | Correction |
|---|---|---|
| English/internal vocabulary at the entrance | Residents had to learn the prototype | French homepage; older workbench isolated at `/explorer.html` |
| Method presented before useful news | Effort before value | Finite local brief, source drawers, direct civic-service links |
| Download time used as publication time | Undated stories appeared fresh | RFC/ISO parsing; no freshness reward for missing/future dates |
| Build clocks confused with freshness | Old news looked newly checked | Actual collection timestamp, six-hour stale warning, partial/unavailable states |
| Failed/disabled/stale sources resurfaced | Coverage appeared healthier than it was | Latest outcomes, enabled registry, 48-hour raw-snapshot expiry |
| Parsing errors could report success | Broken feeds inflated availability | Both fetch and parse must succeed |
| Publisher URLs influenced geography | World articles leaked into local space | Text evidence and context for ambiguous place names |
| Police topic implied death | Enrichment invented a consequence | Separate security impact label |
| A politician's name joined unrelated stories | False dispute narrative | Separate subjects and conservative event grouping |
| Four hardcoded issues constrained discovery | New local events could not become dossiers | Generic headline grouping with strong precision limits |
| IDs depended on source counts | Continuity broke as reporting grew | Stable identities; content-aware workbench change fingerprints |
| Each edition was a fresh snapshot | Residents could not see what changed between visits | Change ledger diffs proposed dossiers across editions: new / developed / quiet, all `status: proposed` |
| Official real-time change data was link-out only | Residents left the brief to learn what blocks their street | WZDX road-obstruction feed ingested as structured data: attributed finite section, source timestamps, collection diffs, official map as next step |
| Source counts implied confirmation | False certainty | Explicitly unassessed contradiction and independence |
| Claim tests rewarded mere extraction | Provenance could silently vanish | Source containment and provenance validation |
| Unsafe/unbounded feed handling | Network and resource exposure | Public targets, pinned addresses, redirect checks, bounded bodies |
| Incomplete HTML attribute escaping | Feed text could break attributes | Both quote types escaped; executable links rejected |
| Staging copied a hardcoded shortlist | Missing new assets and retained old files | Complete clean snapshot, manifest, link checks and rollback |
| Preview exposed directories/symlinks | Unintended file access | Restricted routes, no listings, consistent GET/HEAD |
| No complete verification command | Unit tests could miss broken delivery | Tests, syntax, staging, links and HTTP byte checks |

## What residents can now do

The homepage starts with Québec and nearby places. Residents can narrow by place mention or topic, search titles and excerpts without sending queries anywhere, inspect sources, keep an article locally and explicitly record a point of reading. The return view reports newly present article URLs; it does not claim to detect changes to the world or revisions within a publisher's article.

An editorial **Depuis la dernière édition** section now diffs this edition's proposed dossiers against the previous one — new, developed (more articles or voices), or quiet (absent from this collection). It is public and dossier-level, identical for every reader, and distinct from the personal reading marker above. It never claims resolution: "quiet" means absent from this collection, not fixed; "new" means newly grouped, not more important; "developed" means more articles or voices, not escalation or confirmation.

Only usable publication dates in the seven days before the edition enter the brief. Six articles appear per step, with a stopping point and no infinite scroll. Saved markers do not archive publisher content; articles absent from the current collection are counted as unavailable.

A **Travaux et entraves** section now relays the Ville's official WZDX road-obstruction feed as structured data: finite list (most severe first, capped at eight with an explicit "+ N others" count), original French wording, official start/end dates with the City's own "estimated" marks preserved, per-collection diff tags (new/changed), CC-BY attribution with the collection timestamp, and the official works map as the next step. It is not ranked with articles, computes no personal-route effect, and is not an alert service: a collection older than six hours says so. Removed from a collection is never presented as ended.

Other official links cover RTC information, consultations and snow-removal alerts. These remain useful navigation; their data is **not** ingested or presented as a real-time warning service.

The new homepage uses local CSS/JavaScript and available fonts, with no analytics or location request. Source reading and disclosures remain usable without JavaScript. The older workbench can still load external fonts and publisher thumbnails; it has a different privacy surface and remains experimental.

## The next leap: a local change record

The promising direction is an explainable relationship between **an event, a place, a time, a source and a possible action**. Four slices are now shipped: a deterministic **change ledger** (`scripts/change_ledger.py`, rendered as "Depuis la dernière édition") diffs each edition's proposed dossiers against the previous one, so every persistent event carries a revision signal — new, developed, or quiet; the **first authoritative change source** (`scripts/ingest_wzdx.py`, rendered as "Travaux et entraves") relays the Ville's official WZDX road-obstruction feed with source geometry as a locality guard, official dates, and per-collection diffs; **durable dossier history** (`scripts/dossier_history.py`, store `data/issues/history.json`) gives every proposed dossier its own cross-edition collection facts — first seen, editions seen, editions missed — rendered as a "Suivi depuis…" line on each dossier card; and **per-event roadworks history** (`event_history` inside the roadworks store, `wzdx-event-history-v1`) gives every declared obstruction the same presence facts across collections, rendered as a "Dans nos collectes depuis…" line where an absence is never an end of works. One edition/collection equals one distinct snapshot timestamp (`normalized_at` / `fetched_at`), so offline re-runs never inflate counts, history only moves forward, and a missed edition is an absence from the collection, never a resolution. Both history stores are method-guarded: a foreign-method record is never mixed in or rendered. The remaining capabilities below (rendered source geography, explicit actions and deadlines, field-level revision timelines) are still a product hypothesis, not shipped.

A road-work entry now shows (shipped state of the original six-point specification):

1. What the official source says changed, with original wording. (Shipped: the City's French `description` is relayed verbatim; `update_date` orders the list but is not yet displayed.)
2. Where it applies, using source coordinates or a documented boundary. (Partial: coordinates gate a documented metro bbox and street names are relayed; geography is not yet rendered on a map — the official map link is the next step.)
3. When it starts and ends, including unknown or revised dates. (Shipped: official start/end with the City's "estimated" accuracy marks preserved; missing dates render as "Date non précisée".)
4. Which source fields support the consequence, with inferred effects marked. (Partial: status, impact, direction and restrictions are stored per event; nothing is inferred, and no personal consequence is ever computed.)
5. A useful next step, such as checking the official map or route. (Shipped: the official works map, plus the dataset link for verification.)
6. Its revision history: added, changed, postponed, resolved or unavailable. (Shipped: the change ledger reports new / developed / quiet between editions; the roadworks diff reports new / changed / removed between collections, with "removed" never meaning ended; each dossier and each roadwork entry carries durable presence facts ("Suivi depuis…" / "Dans nos collectes depuis…"); City date revisions render with their literal direction ("Fin reportée" / "Fin avancée" tags from the City's own dates — unparseable dates stay directionless); and events the feed still carries under an ended status (completed/cancelled/archived) are reported as the City's literal declaration ("La Ville déclare…") with an explicit not-a-field-verification note — never a Vigie-verified resolution. Unavailable is the outage path: previous store kept plus the stale warning. Field-level revision timelines remain roadmap.)

This requires better underlying information and durable event identity. An LLM might eventually help extract structured fields; it cannot replace source provenance, correction handling, geographic validation or measured error rates.

### First experiment: daily mobility

Start with one recurring job: **will something change my usual trip?** Validate with a small group of Québec residents who repeatedly travel the same corridors. Record useful discoveries and false alarms with their permission; do not add hidden behavioral tracking.

The Ville publishes an official [WZDX road-obstruction dataset](https://www.donneesquebec.ca/recherche/dataset/entraves-a-la-circulation-en-temps-reel-de-la-ville-de-quebec). Engineering verification is done and the source is integrated: the endpoint (`https://quebec.gewi.com/wzdx/pull`) served 904 GeoJSON features on 2026-09-17 with official start/end dates with accuracy marks, French descriptions, lane-level impact and the City's own status vocabulary (active/planned/pending, relayed as literal labels — a planned window is never presented as a current closure). It renders in the brief's "Travaux et entraves" section under CC-BY 4.0 attribution. Operational uptime remains unverified over time; a feed outage keeps the previous store, prints the failure and never blocks the news pipeline. The [RTC information page](https://www.rtcquebec.ca/restez-informe) provides official route/alert context; actual API availability and conditions must still be checked before promising integration.

**Correction (2026-09-17, caught before first deploy):** the WZDX `data_source_id` property is feed-level on this endpoint — every feature carries `TIC-Quebec/1` — and the first integration wrongly used it as the event key, which collapsed the whole collection into one identity and degenerated the diff to a permanent +0/−0/~0. Real event identity is the GeoJSON feature-level `id` (e.g. `ACL-20260917-EC-001`), verified stable across two collections 6 h apart. Stores built under the degenerate identity are method-guarded: a different `method` version is never compared against, so an identity change cannot masquerade as mass additions/removals (`wzdx-roadworks-v2`). Duplicate feature ids inside one collection (the feed repeats a few) are deduplicated first-in-feed-order and counted, never silently dropped. First real collection diff (16:31 → 22:48 UTC): +13 new, −24 removed, ~32 changed; 21 of the 24 removed carried an official end date already passed and are flagged `official_end_date_passed` — the other 3 are honestly "absent from this collection", not asserted ended.

The first official change source is shipped, and multi-edition revision history is live on both surfaces — dossiers and roadworks events carry collection facts per edition, absence never rendered as resolution or end; next are explicit saved places/corridors. Avoid requiring a home address. “We could not check this route” must be as clear as “a change was reported.” The resident validation loop (small group of repeated-corridor travelers, recorded discoveries and false alarms) has not started.

### Second experiment: decisions before deadlines

The City's [participation portal](https://participationcitoyenne.ville.quebec.qc.ca/) is a potential source for consultations. A later feature should surface the official closing date, affected area, original proposal and participation link. Extracted dates need source-level validation before any reminder or calendar promise.

### What could become defensible

Reliable local event identity, revision history, corrections, tested geography and source relationships could make Vigie difficult to replace. A generic chat box or opaque truth score would not. This is a strategic hypothesis; proprietary advantage and willingness to pay are unproven.

## Remaining constraints

- **Coverage:** RSS is incomplete and can be truncated. Municipal communications are not an emergency-alert feed. Neighborhood/community coverage is uneven.
- **Relevance:** text rules remain provisional. They can miss paraphrases or overmatch names. Québec and environs includes Lévis and nearby communities; it is not a City boundary filter.
- **Dossiers:** conservative lexical grouping misses bilingual and differently worded reports. Newsroom identity is not independent ownership/reporting.
- **Consequences:** extracted quantities cannot reliably calculate a policy's effect on a particular resident. No such outcome should be implied.
- **Operations:** the release is **deployed** to Vercel project `deemto/vigie` — canonical URL **`https://vigieqc.com`** (apex and www both serve production over a wildcard certificate; verified 200 end-to-end on 2026-09-18), with the stable aliases `https://vigie-deemto.vercel.app` and `https://vigie-eight-psi.vercel.app` also routing to production; per-deployment URLs rotate with every refresh and are recorded in `data/ops/refresh.log`, not here. Vercel assigns a project's first deployment to production automatically; later deployments are previews unless `--prod` is passed. **Refresh is automated (2026-09-18):** `python -X utf8 scripts/refresh.py` runs the whole chain — online collection (`pipeline.py --stage`), gate (`verify.py` plain; never `--rebuild`, which would collapse honest edition diffs), re-link, and `vercel deploy --prod` — with a lock file against overlapping runs and an append log at `data/ops/refresh.log`. Windows scheduled task `Vigie Refresh` runs `scripts/refresh.cmd` every 6 hours (the roadworks stale threshold); it is interactive-only, so it fires while the user is logged on, and any failing step stops the chain, leaving the previous production site up. Manual deploy playbook (staging wipes `deploy/public/`, including its Vercel link, so re-link each time): `python -X utf8 scripts/pipeline.py --stage` → `vercel link --yes --scope deemto --project vigie --cwd deploy/public` → `vercel deploy deploy/public -y --no-wait --prod` → `vercel inspect <url>`. Scar: deploying the unlinked directory once created a stray `public` project (the CLI names projects after the deployed directory); it was removed, and the link step above prevents recurrence. Custom domain: **`vigieqc.com` is live** — purchased by the user 2026-09-17 (Vercel registrar, $11.25/yr, expires 2027-09-17; the CLI refuses agent-driven purchases via `purchase_requires_user`, so the buy was interactive), attached with `vercel domains add vigieqc.com vigie --scope deemto`; DNS is automatic on Vercel nameservers (apex ALIAS + wildcard ALIAS + CAA), wildcard TLS issued, and every later production deployment is assigned to the domain automatically. No alert delivery, production monitoring or production backup service was configured here.
- **Source control and the 2026-09-18/19 deploy incident:** the repository is public on GitHub (**https://github.com/Ombo1601/vigie**; secrets and data stay unversioned). Two scars were earned and are documented here: (1) a Vercel account suspension for an overdue balance turned the whole site into HTTP 402 — serving, deploys and the git connection all fail until the owner settles billing, and no deploy can fix that; (2) after reactivation, CLI deploys launched from a git-linked directory were instantly blocked (`seatBlock: TEAM_ACCESS_REQUIRED` — the CLI attaches the local origin's commit metadata to the deployment, and Vercel refuses deployments whose commit author cannot be verified against a *connected* repository), so production restores must deploy from a copy outside the git tree until the Vercel GitHub App is installed and `vercel git connect` succeeds. The repository-root `vercel.json` configures git-built deployments only (build command `python3 -X utf8 scripts/pipeline.py --stage`, output `deploy/public`, same headers as the staged release); CLI deploys of `deploy/public` read their config from the deployment root and never run that build. Git-built editions start without cross-edition memory because `data/` is not versioned — the six-hour `Vigie Refresh` chain restores the continuity-bearing edition.
- **Publisher permissions:** reuse notes are in the registry. This pass did not establish commercial redistribution or image-use arrangements.
- **Corrections:** there is no staffed correction inbox or confirmed response commitment. An original article may change without RSS reflecting the revision.
- **Accessibility:** semantic controls, focus, reduced motion and responsive layouts are implemented; full assistive-technology auditing remains separate.
- **Technical debt:** the legacy workbench remains a large generated HTML module with some brittle historical tests. It is isolated, not fully rearchitected.
- **Business:** founder-backed funding, retention, pricing and willingness to pay remain unvalidated. Do not purchase growth before earning repeat use.

## Addendum (2026-09-22) — what changed since the audit

Dated corrections to the shipped-state claims above; the 2026-09-17/19
record stands as written.

- **Runner:** the collector now runs on GitHub Actions (`.github/workflows/vigie-refresh.yml`,
  every 6 h; `.github/workflows/vigie-roads.yml` hourly for the official roadworks lane),
  with cross-edition state in the private `Ombo1601/vigie-state` store. The Windows
  scheduled task is an optional fallback. Vercel git auto-deploy is disabled, so this
  verified chain is the only production writer.
- **Alerting/observability:** a failed refresh opens (or comments on) a GitHub issue the
  next success closes (`.github/actions/ops-alert`); machine-room ledgers
  (`feed_health`, `media_health`, `edition_metrics`, `watchdog`) are compiled every run.
- **Consultations ingested:** the Ville participation calendar is now a structured
  `civic-html` source (`scripts/ingest_civic.py`), rendered verbatim in its own section —
  no longer "useful navigation" only.
- **Brief paging:** twelve articles per step (lean-scan 6 → 12, DESIGN.md v0.6.1), not six.
- **Record layer:** the brief is one view over a sealed record — Le Registre (sha256
  edition chain, `REGISTRE.md`), La Mémoire, Les Récits, La Méthode, Avant de partir,
  L'affiche, and the machine substrate (`llms.txt`, Markdown twin, delta). See AGENTS.md.

## Acceptance

Run `python -X utf8 scripts/verify.py --rebuild` for current tests, syntax and staged HTTP delivery, and `python -X utf8 scripts/check_claims.py` for extraction integrity. The final execution report records exact counts; an old document count must not masquerade as current verification.

Product acceptance requires watching residents find a relevant development, identify its source/date, recognize uncertainty and reach a useful next step without assistance. Measure time and mistakes. A paradigm shift is earned by those outcomes, not declared by the interface.
