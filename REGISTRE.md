# Le Registre — the sealed record of editions and voice

Vigie's brief is a view. The record is the product. This file is the published
method of that record: what is sealed, how, what it proves, what it does not.

## Why a register

Media measure presence. Nobody notarises absence. A resident, a councillor, a
tenant union or a journalist can today say "the City said nothing about this",
but cannot *show* it. The register turns that into a checkable fact — and, just
as importantly, into a fact that **does not overreach**:

> in edition n (collection clock T), institution X entered no dossier; its feeds
> returned 9 items that our clustering did not group; or our fetch of it failed.

Those are three different facts and only the last one is about Vigie. Conflating
them is how an aggregator ends up accusing a public body of silence it never
observed.

## Correction of 2026-09-30

Until this date the register defined "did not speak" as "absent from every
dossier of the edition". A dossier requires a named subject **and two
institutions**, so most collected items never enter one: the register was
measuring Vigie's clustering rules and publishing them as institutional
behaviour. On live data it asserted `editions_spoke: 0` for Hydro‑Québec and the
Ville de Québec while their feeds returned 40 and 9 items, collected
successfully, in the same edition.

A seal is concerned when it carries no collection facts. The affected range is
**derived from the chain**, never fixed in prose: `chain.json` →
`correction` (`affects_seal_min`, `affects_seal_max`, `first_seal_with_facts`),
also printed on the registre page and the mémoire index. (An earlier version of
this page named a fixed range that the live chain has since outgrown.)

The seals are **not** rewritten — a chain that can be edited is not a chain.
Instead:

- every new seal carries per-institution collected-item counts (`record_schema: 2`);
- seals without those facts report `not_established`, never a silence;
- `institutions.json`, `chain.json`, `delta/latest.json`, the Markdown twin, the
  registre page, the mémoire pages and the affiche all carry the correction,
  with the affected seal range derived from the chain rather than hard-coded.

## What is sealed

One record per edition (`registre-v1 sha256-chain`), identifiers and counts
only — no publisher text, no excerpt, no image:

```json
{
  "method": "registre-v1 sha256-chain",
  "edition": "<collection clock, ISO-8601 UTC>",
  "record_schema": 2,
  "followed": ["<institution ids from sources.yaml>"],
  "collection": {"ville-quebec": {"items": 9, "feeds_ok": 1, "feeds_total": 1}},
  "dossiers": [
    {"issue_id": "…", "label_kind": "subject_label",
     "question": "<Vigie's own subject label, truncated — empty when the dossier is labelled by an attributed publisher headline>",
     "item_count": 4, "official_voice_count": 1,
     "spoke": ["ville-quebec", "le-soleil"], "silent": ["gouv-quebec", "…"]}
  ],
  "ledger": {"has_previous": true, "new": ["…"], "developed": ["…"], "quiet": ["…"]}
}
```

`dossiers[].silent` stays **dossier-scoped** and honest: "absent from this
dossier". The edition-level claim is derived from `collection`, and is one of
five states:

| State | Meaning |
|---|---|
| `spoke` | one of its followed feeds appears in a dossier of this edition |
| `published` | its feeds returned items; none entered a dossier — **not a silence** |
| `no_items` | its feeds answered but yielded nothing inside the 7-day window |
| `collection_gap` | at least one of its feeds failed — **Vigie's** outage |
| `not_established` | sealed before collection facts existed; no claim is made |

Only `collection_gap` accumulates a streak (`collection_gap_streak`), and it
accumulates against Vigie. `collection` is sealed only when the enriched store's
own collection clock equals the edition key, so a mismatched edition can never
borrow another edition's counts.

`edition` is the collection clock (`dossier_history.updated_at`), never the
render clock: an offline rebuild or the roads-only re-render re-seals
the same edition to identical bytes instead of minting a new one.

## How it is chained

```
leaf_n = sha256( canonical_json(record_n) )
root_n = sha256( root_{n-1} || leaf_n )        root_0 = "" (genesis)
```

`canonical_json` = `json.dumps(record, sort_keys=True, ensure_ascii=False,
separators=(",", ":"))`, UTF‑8. Both hashes are lower‑case hex; the
concatenation is of the hex strings.

Published files (all static, all CORS‑open, no key needed):

| File | Content |
|------|---------|
| `/registre/checkpoint.txt` | origin, chain size, current root, edition stamp, latest roadworks root |
| `/registre/chain.json` | the last 200 seals with their records, plus `anchor_root` (the root before the first published seal) |
| `/registre/institutions.json` | per followed institution: state this edition (`spoke` / `published` / `no_items` / `collection_gap` / `not_established`), collected item counts, per-state edition counters, last time it entered a dossier, and Vigie's own `collection_gap_streak` |
| `/registre/travaux.json` | the roadworks chain: one root per *change* of the City's active obstruction set |
| `/registre.html` | the human view |

Verify with the standard library only:

```text
python scripts/registre.py --verify chain.json
```

or by hand: recompute each leaf from its record, chain the roots from
`anchor_root`, and compare the last root with `checkpoint.txt`.

## The roadworks chain

The official roads lane (`refresh.py --roads-only`, scheduled about hourly, actually less often) re-renders when the
declared obstruction set changes. Each distinct active set gets one seal
(`registre-travaux-v1`): record = `{fetched_at, active: [event ids]}`. Only
the latest record is published in full (`latest_record`); the earlier seals
are roots. Removed from the feed ≠ ended; the seal records presence, not
completion.

## What it proves — and does not

- **Proves** that a given edition existed before the next one was sealed, and
  that its content (dossier set, who spoke, who did not, the change ledger) has
  not been altered afterwards.
- **Does not prove** an absolute wall-clock date: there is no external
  timestamp authority. The public git history of the anchor file
  (`anchors/checkpoint.txt`, committed by the refresh workflow after each
  deploy) gives an independent, third-party-hosted ordering; readers who need
  stronger evidence can timestamp a checkpoint themselves (OpenTimestamps,
  RFC 3161).
- **Does not prove** anything about what an institution said outside the
  followed feeds, and it does not assert silence at all: an institution absent
  from the dossiers is reported as `published`, `no_items`, `collection_gap` or
  `not_established`, each a fact about *our* collection.
- **Does not prove** the chain survived an adversary who can rewrite the whole
  repository: recomputing every leaf from genesis passes every self-check. Only
  the external anchor (git history) defeats that, and it is best-effort —
  `continue-on-error`, so a missed anchor leaves an edition unwitnessed rather
  than failing the refresh. Treat "verify it yourself" as proof of
  self-consistency, not of truth.
- **Does not** publish or redistribute publisher content. The chain can be
  copied, mirrored and cited freely without touching anyone's copyright.

## For machines

The register is the memory that stateless agents lack. `/delta/latest.json`
(`delta-v1.1`) carries the current edition keyed by its chain root (`cursor`),
with `previous_cursor`, the new / developed / quiet dossiers with verbatim
titles, `author` (when the publisher's feed gave one) and publisher URLs — never
an excerpt or a summary: the machine files carry publisher, author, title and
URL only — the per-institution state (`spoke`, `published`,
`no_items_collected`, `collection_gap`, `not_established`, with collected item
counts), and the roadworks diff. `/llms.txt` maps these files; the front door
advertises `/index.html.md` as its Markdown twin (`rel="alternate"`).
`delta-v1.1` is `delta-v1` plus an `author` on items (empty string when the feed gave none) and on
`label_source`, and a top-level `attribution` notice; every v1 key keeps its
meaning, so a v1 reader still works. Every machine file says that titles belong
to their publishers and points to `/methode/legal.html`.

Rules for agents, repeated inside every machine file: cite the publisher, not
Vigie; titles are verbatim; **never infer that an institution was silent** —
absence from dossiers is a property of Vigie's clustering; "quiet" dossiers left
the collection and are never "resolved"; verify against the chain before calling
an edition a record.

## Known limits (deliberately not hidden)

- **Seal growth is unbounded.** Every seal keeps its full record; measured cost
  is ~10 KB per edition, so ~15 MB/year in the private state tarball that the
  refresh workflow downloads *and* uploads at every scheduled refresh (about every six hours). `voice` rows prune
  at 400 while `seals` do not, so beyond that point the chain and the register
  describe different windows. Not yet fixed: it degrades over months, not days.
- **`checkpoint.txt` has a variable-length line.** The `travaux` line is absent
  when no roadworks state was ever sealed, so fixed-index consumers must parse
  by prefix, not by line number. The workflow reads lines 2 and 3, which are
  stable.
- **`/registre/institutions.json` is a derived view.** It is recomputed from the
  voice rows on every render, so its window is `VOICE_ROWS_CAP`, not the chain.

## House law that binds this file

Non-commercial (RENT.md). Attribution and never-rewrite (legal.md, R1/R2/R8).
Same-day opt-out (R10): a publisher's removal applies to future editions;
sealed past records carry only identifiers and counts, never their text.
Diagnosed silence: absence is a recorded fact and is never "resolved".
