# Le Registre — the sealed record of editions and voice

Vigie's brief is a view. The record is the product. This file is the published
method of that record: what is sealed, how, what it proves, what it does not.

## Why a register

Media measure presence. Nobody notarises absence. A resident, a councillor, a
tenant union or a journalist can today say "the City said nothing about this",
but cannot *show* it. The register turns that into a checkable fact:

> in edition n (collection clock T), institution X did not appear in any
> dossier of the collected feeds, for the k‑th consecutive edition.

Silence in the register is **absence in the feeds Vigie follows**
(`sources.yaml`), measured at collection time. It is never a claim that an
institution said nothing anywhere. Counters are arithmetic over absence, not
an escalation and not a verdict.

## What is sealed

One record per edition (`registre-v1 sha256-chain`), identifiers and counts
only — no publisher text, no excerpt, no image:

```json
{
  "method": "registre-v1 sha256-chain",
  "edition": "<collection clock, ISO-8601 UTC>",
  "followed": ["<institution ids from sources.yaml>"],
  "dossiers": [
    {"issue_id": "…", "label_kind": "subject_label",
     "question": "<Vigie's own subject label, truncated — empty when the dossier is labelled by an attributed publisher headline>",
     "item_count": 4, "official_voice_count": 1,
     "spoke": ["ville-quebec", "le-soleil"], "silent": ["gouv-quebec", "…"]}
  ],
  "ledger": {"has_previous": true, "new": ["…"], "developed": ["…"], "quiet": ["…"]}
}
```

`edition` is the collection clock (`dossier_history.updated_at`), never the
render clock: an offline rebuild or the hourly roads-only re-render re-seals
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
| `/registre/institutions.json` | per followed institution: spoke/silent this edition, silent streak, last time it spoke |
| `/registre/travaux.json` | the roadworks chain: one root per *change* of the City's active obstruction set |
| `/registre.html` | the human view |

Verify with the standard library only:

```text
python scripts/registre.py --verify chain.json
```

or by hand: recompute each leaf from its record, chain the roots from
`anchor_root`, and compare the last root with `checkpoint.txt`.

## The roadworks chain

The hourly official lane (`refresh.py --roads-only`) re-renders when the
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
  followed feeds, nor why it was silent.
- **Does not** publish or redistribute publisher content. The chain can be
  copied, mirrored and cited freely without touching anyone's copyright.

## For machines

The register is the memory that stateless agents lack. `/delta/latest.json`
(`delta-v1`) carries the current edition keyed by its chain root (`cursor`),
with `previous_cursor`, the new / developed / quiet dossiers with verbatim
titles and publisher URLs, the institutions that spoke and did not (with
streaks), and the roadworks diff. `/llms.txt` maps these files; the front door
advertises `/index.html.md` as its Markdown twin (`rel="alternate"`).

Rules for agents, repeated inside every machine file: cite the publisher, not
Vigie; titles are verbatim; "silent" is absence in the collected feeds;
"quiet" dossiers left the collection and are never "resolved"; verify against
the chain before calling an edition a record.

## House law that binds this file

Non-commercial (RENT.md). Attribution and never-rewrite (legal.md, R1/R2/R8).
Same-day opt-out (R10): a publisher's removal applies to future editions;
sealed past records carry only identifiers and counts, never their text.
Diagnosed silence: absence is a recorded fact and is never "resolved".
