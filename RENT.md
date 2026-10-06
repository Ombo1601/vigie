# Vigie — Rent (how the lookout stays alive)

Honesty first: free to read does not mean free to run. Someone always pays.

## Now

**Who pays:** the project bearer, out of pocket. No company, no donors, no ads.

**What is covered:** the domain, hosting (the free tiers of GitHub Actions and
Vercel), and the bearer's time. Vigie buys no paid API and runs no language
model: the pipeline is rule-based Python with no third-party service.

**What we do not pretend:** ads, donors, or “the community” are not funding Vigie.
If this wallet closes, Vigie stops. That is the scar we accept.

**Rule:** do not scale ingest, enrich, or outreach beyond what this wallet can hold
for 90 days without stress.

## What free means

- Readers: free to open, free to leave, no account required.
- We do not sell “neutrality.” We publish method (`sources.yaml`, `ranking.md`, this file).
- We do not take editorial money that buys rank, or any money that buys a say over
  what is shown.

## The permanent no

Vigie is non-commercial, permanently (house law R7, decided 2026-09-19; see the
legal page). No advertising, no subscription or paid tier, no sponsorship, no sale
or licensing of data, no paid alerts, no B2B product, no membership that buys
anything. Selling the rank, stealth native ads and “sponsored truth” are the fog
this project exists to watch. The only way this could ever change is after the
explicit authorisation of the publishers whose items Vigie relays, announced
publicly on the legal page first.

## Decision log

Append-only: earlier rows stay as written; later rows say what superseded them.

| Date | Decision | Owner |
|------|----------|--------|
| 2026-09-15 | v0 rent = the project bearer's wallet; free to read; no ads | co-founders |
| 2026-09-16 | Ambient morning digest = presentation of store only (no extra LLM/fetch). Paid alert delivery stays v1 — payload may reuse `data/pulse/latest_morning.*` without a second ranking brain. (Superseded 2026-10-06: there are no paid alerts.) | Bucky + the bearer |
| 2026-09-17 | Widget paste twin `latest_morning.widget.txt` ships with morning JSON/TXT/HTML; same pulse as Arrival + Stage | Bucky + the bearer |
| 2026-09-19 | Non-commercial permanently (R7): no ads, subscription, sponsorship or data resale | the bearer |
| 2026-10-06 | The former “v1 rent candidates” (optional paid alerts, B2B digest, patron membership) are withdrawn and no longer listed. The “LLM API calls” budget line is struck: no language model is used. | the bearer + co-founder |

## Success / failure

- **Right:** readers return; method stays inspectable; the wallet lasts long enough to learn.
- **Wrong:** we scale as if free forever; or we sell the rank and become the fog we watch.
