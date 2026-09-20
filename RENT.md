# Vigie — Rent (how the lookout stays alive)

Honesty first: free to read does not mean free to run. Someone always pays.

## v0 (now)

**Who pays:** the project bearer, out of pocket for now. No company, no donors,
no ads.

**What is covered:** domain (when bought), hosting, LLM API calls for enrich, your time.

**What we do not pretend:** ads, donors, or “the community” are not funding v0. If this wallet closes, Vigie stops. That is the scar we accept until a real rent exists.

**Rule:** do not scale ingest, enrich, or marketing beyond what this wallet can hold for 90 days without stress.

## What free means

- Readers: free to open, free to leave, no account required for v0.
- We do not sell “neutrality.” We publish method (`sources.yaml`, `ranking.md`, this file).
- We do not take editorial money that buys rank. Any paid path must be labeled and must not silently boost stories.

## v1 candidates (choose later, after the thin loop works)

Pick at most one primary rent. Do not invent all of them on day one.

1. **Optional alerts** — pay for “impact near me” digests (housing, hydro, law) without changing the free public rank.
2. **B2B Quebec City impact digest** — weekly package for orgs (landlords, clinics, local firms) that need nested claim maps; same method, delivered as a product.
3. **Patron / membership** — support the lookout; no rank purchase; patrons get early features, not a louder megaphone.
4. **Hard no for v0/v1:** selling the rank itself; stealth native ads in the feed; “sponsored truth.”

## Decision log

| Date | Decision | Owner |
|------|----------|--------|
| 2026-09-15 | v0 rent = the project bearer's wallet; free to read; no ads | co-founders |
| 2026-09-16 | Ambient morning digest = presentation of store only (no extra LLM/fetch). Paid alert delivery stays v1 — payload may reuse `data/pulse/latest_morning.*` without a second ranking brain | Bucky + the bearer |
| 2026-09-17 | Widget paste twin `latest_morning.widget.txt` ships with morning JSON/TXT/HTML; same pulse as Arrival + Stage | Bucky + the bearer |
| TBD | Choose v1 primary rent after first scarred ingest + display | the bearer |

## Success / failure

- **Right:** readers return; method stays inspectable; wallet lasts long enough to learn.
- **Wrong:** we scale as if free forever; or we sell the rank and become the fog we watch.

Next scar after this file: ingest enabled RSS from `sources.yaml` into `data/raw`.
