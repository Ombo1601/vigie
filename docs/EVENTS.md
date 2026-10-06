# EVENTS.md — the Event object

Status: **built, wired behind a switch, off** (2026-10-06). The event builder
(`scripts/events.py`) runs in every full edition as a shadow stage; the event
chain is sealed in the private state only; the event surfaces
(`scripts/evenements.py`) are wired into the render and the release behind
the three-position switch of `scripts/surfaces.py`, committed `off`: until the
founder flips it, the site serves exactly what it served before (section 19).
Sections 1 to 16 are the original spec; section 17 records where the build
deviates from it, and why. Companion specs: `docs/I18N.md` (labels, URLs),
`docs/CHARTE.md` (the public promise), `docs/MIGRATION.md` (build order),
`docs/AUTONOMY.md` (binding: no editorial choice depends on a person).
Every example below is invented. No publisher text appears in this file.

## 1. Why an Event

Today the unit is the **dossier** (`cluster_issues.py`): a named scar or a
complete-link headline cluster, kept only when two institutions speak.
Measured on the private collection history (53 stamped snapshots, 2,139 unique
items, 215 in English), the current matcher forms 51 multi-institution groups
covering 6.4 % of items; 2 of them are bilingual; 1 carries an official voice.
93.6 % of what Vigie collects never reaches a grouped view.

The **Event** is the new unit: one real-world occurrence (a fire, a council
vote, a bridge closure) and **all** coverage of it Vigie collected, whatever the
outlet or language, including events covered by a single source. The brief, the
registre, the Markdown twin and the delta become views of events.

Non-goals: no truth verdict, no "confirmed", no contradiction scoring, no
ideology or reliability score, no summary written by Vigie.

## 2. Terms

| Term | Meaning |
|------|---------|
| item | one collected candidate (`id` = sha256 of canonical URL, from normalize) |
| edition | one collection snapshot, keyed by its collection clock (`normalized_at`) |
| member | an item attached to an event |
| institution | the voice (`sources.yaml` `institution`), sister feeds collapsed |
| owner group | the legal owner behind one or more institutions (section 7) |
| window | the 7-day publication window already used by `cluster_issues` |

## 3. The Event object

| Field | Type | Rule |
|-------|------|------|
| `event_id` | string | `ev-` + 16 hex; minted once, never recomputed (section 4) |
| `schema` | int | `1` |
| `method` | string | `events-v1 rules` plus rule-set version |
| `type` | code | controlled event type (`docs/I18N.md` table A) or `unclassified` |
| `places` | codes | controlled places (table B), most specific first; never empty (falls back to a scope code) |
| `label` | object | `{fr, en}` built from `type` and `places[0]` only (section 5) |
| `born_edition` | ISO UTC | edition clock that minted the id |
| `last_edition` | ISO UTC | latest edition that added a member |
| `window_state` | enum | `in_window` or `out_of_window` (Vigie's window, never "ended"/"resolved") |
| `activity` | enum | this edition: `new`, `developed`, `quiet` (same words as the change ledger) |
| `members` | list | timeline rows (section 6), sorted |
| `institutions` | ids | distinct institutions among members, sorted |
| `languages` | codes | distinct `fr`/`en` among members, sorted |
| `independence` | object | groups and count (section 8) |
| `facts` | list | extracted facts with attribution (section 9) |
| `anchors` | list | official-record anchors (section 10) |
| `language_pairs` | list | FR/EN member pairs (section 11) |
| `lineage` | object | `merged_into`, `absorbed`, `detached` (section 4) |
| `seals` | ints | `seq` numbers of event-chain seals that carry this event |

No field holds a title, an excerpt, a quote or a person's name outside the
controlled entity list. Publisher text is joined at render time from the
current edition store only (section 13).

## 4. Identity: ids that survive outlets joining or leaving

1. **Mint.** When an item matches no in-window event, a new event is born with
   `event_id = "ev-" + sha256("event-v1|" + anchor_item_id)[:16]`, where
   `anchor_item_id` is that founding item's id. Item ids are already stable
   (sha256 of canonical URL). Count, type, label, outlets never enter the id.
2. **Store, never recompute.** The id is written to the private event store
   (`data/events/store.json`) and read back next edition. Re-running the rules
   never re-derives an id from current membership, so an outlet joining,
   leaving, editing its URL or opting out (R10) cannot move it.
3. **Sticky membership.** An item joins at most one event, once. Later editions
   may add members; they never move or remove one by rule. Matching is
   **tiered**, not `features_match` alone: `scripts/event_match.py` scores a
   pair 0..1 with its reasons (table below), gives it a tier, and a newcomer
   links to an event when its **average score over the event's compared
   members reaches the `probable` threshold and at least one of them is at
   tier `probable` or above** (average link, chosen on dev over complete and
   single link). Compared members are those within 7 days of the newcomer
   whose text is still in the window; a member whose text is gone keeps its
   membership and is no longer compared.
4. **Attach order.** New items of an edition are processed in
   `(published_at or first_seen, item_id)` order. Among the events it links
   to, a newcomer joins the one with the highest average score; ties by most
   matching members, then oldest `born_edition`, then smallest `event_id`.
   For matching, an instant outside the years 1990 to 2100 (UTC) is not a
   date: feeds stamp `0001-01-01`, `1970-01-01` or `9999-12-31` for
   "unknown". It counts as absent and the item falls back to `first_seen`,
   then to the edition clock, so one such item can never stop an edition's
   events from being built.
5. **Merge.** Two in-window events merge only when at least two cross pairs
   are at tier `probable` or above **and** the average score over their
   comparable cross pairs reaches the `probable` threshold, and (decided by
   the event builder, the `compatible` hook) both share `type` and
   `places[0]`. The average condition is not decoration: on a replay of 49
   stamped editions the two-pair rule alone glued unrelated stories into one
   100-member "event" (96 merges); with it the largest event has 11 members
   (11 merges), at the same dev F1. The older event (by `born_edition`, then
   `event_id`) keeps its id; the younger records `lineage.merged_into` and
   stops accepting members; no member moves. Its page redirects by a static
   link, never disappears.
6. **Split / detach.** Never automatic. A correction (corrections ledger, see
   `docs/CHARTE.md`) may detach an item; the event keeps its id, records
   `lineage.detached: [item_id]`, and the item mints its own event.
7. **Anchor leaves.** If the founding item is withdrawn (opt-out, 404) the id
   stands: identity is history, not current content.
8. **Window.** An event accepts members while its newest member is within 72 h
   (the current `EVENT_SPAN_HOURS`) and the edition is within the 7-day window.
   After that it is `out_of_window`; a later related item starts a new event.
9. **Named scars.** Existing scar dossiers (tramway, airport, ...) become
   event *types*, not events: a seven-week saga is many events of type
   `tramway-project`, browsable by type. Continuity across events is a
   type plus place filter, never an id.

### Tiers, guard and reasons (`scripts/event_match.py`)

| Tier | Pair score | What it may do |
|------|-----------|----------------|
| `certain` | ≥ 0.8713 | group articles |
| `probable` | ≥ 0.3985 | group articles |
| `possible` | ≥ 0.0984 | shown only as **neighbours** ("voisins, non regroupés"); never merged |
| none | below | nothing |

- **Score.** A logistic sum of measured components (publication gap, shared
  numbers, dates, capitalised names, lexicon places / institutions / event
  types, bilingual word overlap, headline overlap, character 4-gram
  similarity, place and event-type conflicts). Weights were fitted once on the
  dev split of the private gold set and are frozen in the module; IDF tables
  come from the window's items, never from labels.
- **Thresholds**, chosen on dev only: `certain` is the lowest dev threshold
  whose dev precision is ≥ 0.95 there and at every higher threshold;
  `probable` is the median of the dev F1 plateau (within 0.01 of the best F1;
  one arg-max is noisy); `possible` is the lowest dev threshold at which ≥ 90 %
  of the dev pairs at or above it are the same event or the same storyline.
- **FR/EN guard.** A French/English pair must share a specific place, a rare
  name (carried by at most 1 % of the window, never a broad place or a role)
  or a number ≥ 10 that is not a year; otherwise its tier is capped at
  `possible`. Same-owner pairs (CBC / Radio-Canada) are matched like any
  other: independence is counted in section 8, never by the matcher.
- **Reasons.** Every grouping explains itself: each non-zero component with
  its signed weight (they add up to the score's log-odds), quoting single
  words or numbers as each outlet wrote them and Vigie's lexicon labels
  ("60 000 ≙ 60,000", "pont Pierre-Laporte ≙ Pierre Laporte Bridge"), never a
  stem; the headline reason cites headline words only. Reasons are computed at
  render time and are **never stored in the event store or sealed**: only
  ids, tiers and scores are.
- **Blocking.** Only pairs that share a rare key (a token, name, number or
  lexicon term carried by at most max(30, 4 %) of the window) and were
  published at most 7 days apart are scored. On the gold items it keeps 172 of
  173 same-event pairs (the lost one is at tier `possible`) and scores about
  one pair in ten; a 325-item edition is clustered in about 0.6 s.

## 5. Label: event type times place

`label.fr = TYPE_FR[type] + " · " + PLACE_FR[places[0]]`, same for `en`.
Example (invented): `fire-building` × `limoilou` →
"Incendie de bâtiment · Limoilou" / "Building fire · Limoilou".

- Types and places come only from the controlled vocabulary in `docs/I18N.md`.
  A label is never built from a headline, so it can be sealed and kept forever.
- Type assignment: a rules lexicon per type (FR and EN keyword lists, folded,
  word-bounded), evaluated over member titles in sorted member order; the type
  with most member hits wins, ties by table order. Zero hits: `unclassified`.
  Status is always `proposed`.
- Place assignment: existing `enrich` geo, `cluster_issues` place hints and
  road names mapped to place codes; specificity order quartier > arrondissement
  > site/corridor > neighbour municipality > scope. Fallback from item geo:
  `quebec-city`, `province`, `ottawa`.
- Labels may be re-worded in the vocabulary; codes never change. Sealed records
  carry codes, never label strings.

## 6. Timeline of sources

One row per member, sorted by `(first_seen, published_at, item_id)`:

| Field | Rule |
|-------|------|
| `item_id` | candidate id |
| `institution`, `source_id` | from `sources.yaml` |
| `language` | declared source language (`docs/I18N.md` section 4) |
| `published_at` | publisher timestamp, UTC, or `null` when absent/invalid (never guessed) |
| `first_seen` | collection clock of the first edition containing the item; never a wall clock |
| `origin_class` | section 7 |
| `ownership_class`, `owner_group` | section 7 |
| `url` | canonical URL (private store; public only while the source is enabled) |

Displayed lag = `first_seen - published_at` (shows Vigie's own delay, not the
outlet's speed). `published_at` after `first_seen` + 6 h is flagged
`date_suspect`, never corrected.

## 7. Origin and ownership

### Origin class (per member)

| Code | Assigned when | Never inferred from |
|------|---------------|---------------------|
| `official` | the source is `source_kind: official` (the item is the communique) | — |
| `wire` | author/credit field names a wire agency from a closed list (La Presse Canadienne, The Canadian Press, AFP, Reuters, AP), or a closed list of wire credit markers appears | outlet size, topic |
| `press_release` | a media item carries a closed-list relay marker (for example "par voie de communiqué", "in a news release") | similarity to an official item alone |
| `own_reporting` | a named author byline that is not on the wire list and no wire or relay marker | the absence of a wire marker alone |
| `unknown` | everything else | — |

`unknown` is the default and an honest, displayed value. Markers are read from
publisher text at processing time; only the class code and the rule id are kept.

### Ownership class (per institution, declared in `sources.yaml`)

| Institution | `ownership_class` | `owner_group` |
|-------------|-------------------|---------------|
| radio-canada | `public_broadcaster` | `cbc-radio-canada` |
| cbc | `public_broadcaster` | `cbc-radio-canada` |
| journal-de-quebec | `quebecor` | `quebecor` |
| le-soleil | `cooperative` | `cn2i` |
| le-devoir | `independent` | `le-devoir` |
| la-presse | `independent` | `la-presse` |
| ville-quebec | `government` | `ville-quebec` |
| gouv-quebec | `government` | `etat-quebec` |
| hydro-quebec | `government` | `etat-quebec` |

Each row cites a public ownership source in `sources.yaml` (`ownership_ref`).
The founder confirms the table before it ships (Hydro-Québec grouped with the
State: a decision, section 16).

## 8. Independence (counted, never judged)

Members are joined into **independence groups** with a union-find, in sorted
order: same `owner_group` → one group; members with `origin_class: wire` →
one group per agency; `press_release` members → joined with any `official`
member of the same institution named as issuer, else one group per issuer.
Output: `{"groups": [[item_ids...]...], "count": n}`. Display: "3 sources,
2 indépendantes au sens de la méthode". Radio-Canada plus CBC is one group: a
FR/EN pair from them is a language pair, not two independent voices. The word
"confirmé" is never produced.

## 9. Facts table

Facts are values, not prose. Each row:

```json
{"slot": {"kind": "count", "unit": "persons_injured", "subject": "fire-building"},
 "values": [{"value": 2, "stated_by": ["<item_id>"], "institutions": ["<inst>"]},
            {"value": 3, "stated_by": ["<item_id>"], "institutions": ["<inst>"]}],
 "divergent": true, "method": "facts-v1", "status": "proposed"}
```

| Kind | Value form | Source of rules |
|------|------------|-----------------|
| `count` | integer + unit code (`persons_dead`, `persons_injured`, `persons_arrested`, `housing_units`, `jobs`, `vehicles`, `buildings`, `customers_without_power`) | new lexicon, reuses `enrich.RE_HOUSING_COUNT` style |
| `amount` | integer cents CAD + unit (`total`, `per_month`, `per_year`, `percent_bp` for basis points) | `enrich.propose_impact_units` patterns |
| `date` | ISO date the source states for the event (not publication date) | new, closed month-name lists FR/EN |
| `place` | place code or road name from `road_places` | vocabulary + `cluster_issues._LOCATION` |
| `entity` | institution id, or a public-office id from a closed list (mayor, ministers, police services) | closed list only |

Rules: no float (cents, basis points); a slot with two values is shown side by
side with attribution and `divergent: true`, never reconciled; persons who are
not on the public-office list are never extracted (victims, minors, accused
private persons stay out of every Vigie field).

## 10. Official-record anchors

An anchor is a pointer to an official record, attached by a named rule. It is
never a confirmation (`official_source_is_confirmation` stays `false`).

| `type` | `ref` | Rule |
|--------|-------|------|
| `official_item` | member item id from an official source | membership |
| `roadwork` | WZDX feature id | same road name + overlapping dates, type in the roads family |
| `consultation` | civic store id (`ingest_civic`) | same place code + type `public-consultation` |
| `outage` | Hydro-Québec item id | type `power-outage` + same place |
| `edition_seal` | main registre `seq` | event present in that edition |

Row: `{"type", "ref", "rule", "status": "linked_by_rule"}`, sorted by
`(type, ref)`. An event with no anchor shows "aucun document officiel rattaché"
— a measured absence, never an accusation.

## 11. Language pairs

For every FR member and EN member that satisfied the bilingual branch of
`features_match` directly, emit `{"fr": item_id, "en": item_id, "rule":
"bilingual-complete-link", "same_owner": bool}`. Sorted by `(fr, en)`.
`same_owner: true` (CBC/Radio-Canada) is shown, so a translated twin is never
read as two witnesses. Events with only EN coverage are displayed as such.

## 12. Sealing

The existing chain (`registre-v1 sha256-chain`, `scripts/registre.py`) is not
touched: `edition_record` keeps its exact fields, so every existing seal and
every future main seal stays byte-identical to what today's code produces.

Events get a **separate chain**, `registre-evenements-v1 sha256-chain`, in the
same state file under a new key `evenements` (as `travaux` is today), using the
same primitives (`canonical`, `leaf_of`, `chain_hash`, imported, not copied).
One seal per edition, keyed by the same collection clock, idempotent on re-render,
immutable once published (a divergent leaf is printed and the old seal kept).
Record:

```json
{"method": "registre-evenements-v1 sha256-chain",
 "edition": "<collection clock>",
 "edition_root": "<root of the main seal of this edition, or empty>",
 "events": [{"event_id": "ev-…", "type": "fire-building", "places": ["limoilou"],
             "activity": "new", "institutions": ["…"], "languages": ["en", "fr"],
             "member_count": 4, "independent_count": 3,
             "origin": {"official": 1, "own_reporting": 2, "unknown": 1},
             "anchors": [{"type": "roadwork", "ref": "…"}],
             "merged_into": ""}]}
```

Never sealed: URLs (R10 must be able to remove them), titles, excerpts, quotes,
fact values, person names. Folding events into the main record (schema 3) is a
later founder decision, not part of this spec.

## 13. Retention

| Data | Where | Kept |
|------|-------|------|
| titles, excerpts, quotes | `data/normalized/latest_*.json` (existing) | current edition only; never copied into the event store |
| event store (ids, members, times, classes, anchors, URLs) | `data/events/store.json`, private, packed with state | 400 editions, like voice rows |
| fact values from media | event store | while `in_window`; then reduced to counts per slot |
| fact values stated by an official member | event store | kept (public record) |
| URLs | event store, current pages | while the source is enabled; R10 opt-out purges them the same day |
| sealed event records | registre state, `public/registre/` | forever, codes and counts only |

Pages: the **current-edition** event page may show attributed verbatim titles
and excerpts (≤ 240 chars, R1/R8). The **permanent** event page
(`/evenements/<event_id>.html`) carries only Vigie labels, ids, institution
names, times, links, counts, anchors and seals — no publisher text, ever.

## 14. JSON Schema (store and `public/evenements/latest.json`)

```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "$id": "https://vigieqc.com/schemas/event-v1.json",
  "type": "object",
  "additionalProperties": false,
  "required": ["event_id", "schema", "method", "type", "places", "label",
               "born_edition", "last_edition", "window_state", "activity",
               "members", "institutions", "languages", "independence",
               "facts", "anchors", "language_pairs", "lineage", "seals"],
  "properties": {
    "event_id": {"type": "string", "pattern": "^ev-[0-9a-f]{16}$"},
    "schema": {"const": 1},
    "method": {"type": "string"},
    "type": {"type": "string", "pattern": "^[a-z0-9-]{2,48}$"},
    "places": {"type": "array", "minItems": 1, "items": {"type": "string", "pattern": "^[a-z0-9-]{2,48}$"}},
    "label": {"type": "object", "additionalProperties": false, "required": ["fr", "en"],
              "properties": {"fr": {"type": "string", "maxLength": 120}, "en": {"type": "string", "maxLength": 120}}},
    "born_edition": {"type": "string", "format": "date-time"},
    "last_edition": {"type": "string", "format": "date-time"},
    "window_state": {"enum": ["in_window", "out_of_window"]},
    "activity": {"enum": ["new", "developed", "quiet"]},
    "members": {"type": "array", "minItems": 1, "items": {
      "type": "object", "additionalProperties": false,
      "required": ["item_id", "institution", "source_id", "language", "published_at",
                   "first_seen", "origin_class", "ownership_class", "owner_group"],
      "properties": {
        "item_id": {"type": "string", "pattern": "^[0-9a-f]{16,64}$"},
        "institution": {"type": "string"}, "source_id": {"type": "string"},
        "language": {"enum": ["fr", "en"]},
        "published_at": {"type": ["string", "null"], "format": "date-time"},
        "first_seen": {"type": "string", "format": "date-time"},
        "origin_class": {"enum": ["official", "wire", "press_release", "own_reporting", "unknown"]},
        "origin_rule": {"type": "string"},
        "ownership_class": {"enum": ["public_broadcaster", "quebecor", "cooperative", "independent", "government"]},
        "owner_group": {"type": "string"},
        "url": {"type": "string", "format": "uri"},
        "date_suspect": {"type": "boolean"}}}},
    "institutions": {"type": "array", "items": {"type": "string"}},
    "languages": {"type": "array", "items": {"enum": ["fr", "en"]}},
    "independence": {"type": "object", "required": ["groups", "count"],
      "properties": {"groups": {"type": "array", "items": {"type": "array", "items": {"type": "string"}}},
                     "count": {"type": "integer", "minimum": 1}}},
    "facts": {"type": "array", "items": {"type": "object",
      "required": ["slot", "values", "divergent", "method", "status"],
      "properties": {
        "slot": {"type": "object", "required": ["kind", "unit", "subject"],
                 "properties": {"kind": {"enum": ["count", "amount", "date", "place", "entity"]},
                                "unit": {"type": "string"}, "subject": {"type": "string"}}},
        "values": {"type": "array", "minItems": 1, "items": {"type": "object",
          "required": ["value", "stated_by", "institutions"],
          "properties": {"value": {"type": ["integer", "string"]},
                         "stated_by": {"type": "array", "items": {"type": "string"}},
                         "institutions": {"type": "array", "items": {"type": "string"}}}}},
        "divergent": {"type": "boolean"}, "method": {"type": "string"},
        "status": {"const": "proposed"}}}},
    "anchors": {"type": "array", "items": {"type": "object", "additionalProperties": false,
      "required": ["type", "ref", "rule", "status"],
      "properties": {"type": {"enum": ["official_item", "roadwork", "consultation", "outage", "edition_seal"]},
                     "ref": {"type": "string"}, "rule": {"type": "string"},
                     "status": {"const": "linked_by_rule"}}}},
    "language_pairs": {"type": "array", "items": {"type": "object",
      "required": ["fr", "en", "rule", "same_owner"],
      "properties": {"fr": {"type": "string"}, "en": {"type": "string"},
                     "rule": {"type": "string"}, "same_owner": {"type": "boolean"}}}},
    "lineage": {"type": "object", "properties": {
      "merged_into": {"type": "string"},
      "absorbed": {"type": "array", "items": {"type": "string"}},
      "detached": {"type": "array", "items": {"type": "string"}}}},
    "seals": {"type": "array", "items": {"type": "integer", "minimum": 1}}
  }
}
```

The project stays stdlib-only: tests validate with a small hand-written checker
for this schema (required keys, enums, patterns), not a third-party library.
The public `latest.json` drops `url` for disabled sources and `facts` values
from media once out of window.

## 15. Determinism

- Inputs: the ordered stamped snapshots, `sources.yaml`, the vocabulary, the
  corrections ledger, the previous event store. Same inputs → byte-identical
  store, pages and seals. Replaying all snapshots from an empty store must
  reproduce the live store (a test does this on invented fixtures).
- Clocks: only collection clocks (`normalized_at`) and publisher timestamps.
  No `datetime.now()` in any stored field.
- Order: every list sorted by an explicit key ending in an id; dict output via
  `sort_keys=True`; iteration over sets always through `sorted()`.
- Text: NFC; matching uses the existing `folded()`; ids from sha256 of ASCII
  canonical strings.
- Numbers: integers only (cents, basis points, counts).
- Writes: `store_io.write_json_atomic`; canonical JSON for anything hashed.
- Failure: a corrupt store yields no events and exit 0; the brief renders from
  dossiers as today (fail-soft, diagnosed in the log).

## 16. Gates and open decisions

Gates before events replace dossiers on the front door (measured, published):

1. Pairing precision ≥ 0.95 at tier `certain` on a private **hand-labelled**
   gold set (counts only are published, never the pairs' text).
2. Zero publisher text in sealed records and permanent pages (a test scans
   them against current titles and excerpts).
3. Id stability: 0 id changes across a full replay of the snapshot history.
4. Every existing main-chain seal byte-identical (golden test).

**Where gate 1 stands (2026-10-06, `eval/REPORT.md`).** Every number below is
**model-labelled and relative**: gold_v0 (520 pairs) was labelled by one
language model in three passes (two labellers and an adjudicator); its kappa
0.89 is the model's consistency with itself, no human has checked a label,
and 8 pairs await the founder. Its pairs were sampled through a lexical
screen of the same kind as the matcher's features, so recall is an upper
bound on screen-findable pairs. These figures rank designs; they are not a
measured real-world precision, and gate 1 cannot be passed on gold_v0.

| Measure (shipped matcher, TEST) | Value | 95% interval |
|---|---|---|
| `certain`, pair split | 33 of 34 same-event: precision 0.971, recall 0.465 | Wilson 0.851–0.995; component bootstrap 0.90–1.00 |
| `certain`, leakage-free component split (no item on both sides; refit and tiers on its dev) | 38 of 42: precision 0.905, recall 0.551 | component bootstrap 0.75–1.00 |
| grouped (`certain` or `probable`), pair split | 57 of 77: precision 0.740, recall 0.803 | Wilson 0.633–0.825 |
| grouped, French/English pairs only | 13 of 16: precision 0.812, recall 0.765 | Wilson 0.570–0.934 |
| clusters (sticky clusterer), induced pairwise | P 0.778, R 0.789, F1 0.783 | — |

Verdict: **not met**. The point estimate clears 0.95 on the pair split, but
its lower bound does not, and the leakage-free split falls to 0.905. Most
grouped-tier false merges are `related` pairs (same storyline, distinct
happening). What would open the gate: founder review of the gold (the 8
uncertain pairs first), then a human-labelled sample drawn from the natural
stream at tier `certain` and `probable` (not through the screen); showing
precision ≥ 0.95 with 95 % confidence takes about 73 `certain` pairs without a
single error (110 with one). If that sample misses, raise `certain` on a new
dev split before shipping the word.

Founder decisions: (a) Hydro-Québec in owner group `etat-quebec`;
(b) wire/relay marker lists; (c) public-office entity list; (d) whether event
seals ever fold into the main record (schema 3); (e) until gate 1 passes on
human labels, whether the brief may show "Regroupement certain" at all or
only "probable"; (f) the 8 uncertain gold pairs.

## 17. Deviations from this spec (tranche C, 2026-10-06)

Each rule below replaces what an earlier section says, under docs/AUTONOMY.md:
computed from evidence, explained by the view, recomputed every edition. The
measurements are counts on scratch copies of the private data (the live
edition of 2026-10-06T00:03Z, 325 candidates, 194 events; and a replay of the
52 stamped snapshots of the local history), never publisher text.

### 17.1 Places (sections 3 and 5)

- **No evidence, no place.** enrich's geo `linked` means "no explicit local
  evidence" (its own reason text): an absence. It supports no place at all.
  The fallback is the new scope `unplaced` ("Lieu non établi"), never the
  city: before this change 98 of the 194 live events (50.5 %) carried
  `quebec-city` with no evidence. `elsewhere` ("Hors Québec") is said only on
  positive evidence: a headline naming a place outside Quebec (lexicon), or
  enrich's world-fog branch (a foreign token and no Quebec token). Mapping
  every `linked` item to `elsewhere` was rejected on the evidence: in the live
  edition 108 of the 114 member rows of the fallback events carry no
  positive evidence of any place, and 48 of the 114 come from the three
  Québec City newsrooms (many are local stories the lexicon cannot place).
- **Geo evidence**: `quebec-city` → `quebec-city`, `quebec` → `province`,
  world-fog `linked` → `elsewhere`, federal evidence → `ottawa`; any other
  `linked` → none. Per item the code is stored as `geo_place`.
- **Plurality, not "first city member wins".** Each member casts one vote, its
  best evidence: a specific place its headline names; else a strict city geo
  token; else a scope its headline names; else its other geo evidence. The
  AREA (capital, province, federal, elsewhere) is the plurality (ties: more
  named votes, then that order); `places[0]` is, inside that area, the named
  specific place when one exists, else its scope (ties: votes, named votes,
  specificity, table order). The rest of `places` stays most specific first.
  `place_basis` (`named`, `geo`, `fallback`) and `place_votes` (`{code:
  {votes, named}}`) keep the evidence visible. `geo` (for ranking) is the
  plurality of the member geos, ties to the more local.
- **Ottawa only when the evidence is federal.** Bare "Ottawa" is the usual
  metonym of the federal government ("Ottawa annonce", "qu'Ottawa"). "à
  Ottawa" and "d'Ottawa" usually locate a story in the city (Ontario,
  `elsewhere`: "police d'Ottawa", "un suspect arrêté à Ottawa"), EXCEPT where
  the phrase addresses or quotes the federal government, which stays
  `ottawa`: a request or reproach made to it ("demande à Ottawa", "réclame
  ... à Ottawa", "exige d'Ottawa", with up to 4 words between the verb and
  Ottawa), what it pays or decides ("aide", "argent", "transferts", "refus",
  "financement", "décision ... d'Ottawa", "auprès d'Ottawa") and Parliament
  ("parlementaire à Ottawa", English "Parliament Hill"; Québec City's
  own "colline Parlementaire" stays its site). The lexicon lists these
  phrases once per rule: the `ottawa` rule `keep`s them through its mask, the
  `elsewhere` rule masks them. A place rule may carry its own `mask` (phrases
  removed before its keywords are tested, longest first) and `keep` (phrases
  its mask never removes); `~` between two words stands for up to 4 words.
  "paris" as bets ("paris sportifs", "paris en ligne", "les paris", "paris
  et casinos", ...) is masked from `elsewhere`. The province lexicon gains
  Quebec towns and regions outside the capital (Montréal, Gatineau,
  Trois-Rivières, ...).
- Measured. Live edition, `places[0]`: `quebec-city` 117 → 17 (with no
  evidence 98 → 0), `province` 57 → 69, `ottawa` 13 → 3, `elsewhere` 0 → 23,
  `unplaced` 0 → 75; events with place evidence 49.5 % → 61.3 %. Replay of the
  history (surviving events): `quebec-city` 887 → 90 (791 with no evidence →
  0), evidence share 46.3 % → 56.9 %. The federal-addressee and bet phrases
  change 7 of the 2,139 history headlines (4 from `elsewhere` to `ottawa`, one
  of them keeping `elsewhere` for an Ontario city it also names; 2 bet
  headlines from `elsewhere` to no place; 1 English "Parliament Hill" from
  the city's site to `ottawa`) and none of the 325 live candidates (the 10
  city-of-Ottawa headlines of the live edition stay `elsewhere`).

#### Table B additions

| Code | Kind | FR | EN |
|------|------|----|----|
| elsewhere | scope | Hors Québec | Outside Quebec |
| unplaced | scope | Lieu non établi | Place not established |

`unplaced` is the place twin of `unclassified`: no keyword can name it, and a
label whose basis is `fallback` names no place.

### 17.2 Window (sections 4.8 and 6)

A member whose `published_at` is suspect (more than 6 h after its first
collection) keeps its date as given but is dated by its `first_seen`: for the
window (`in_window`, facts live or reduced) and for the matcher (the event's
span), so a future date never holds an event open.

### 17.3 Independence (sections 7 and 8; AUTONOMY decision table)

- Official members are **declarations**: keyed `declaration:<owner group>`
  (never `owner:`), so they merge only with declarations of the same owner and
  with a media text that copies them. The view exposes
  `reporting_origin_count` (origins with no official member: media reporting
  only) apart from `declared_by` (the declaring institutions).
- Merge rules, each with its reason code on every merged origin
  (`independence.origins[].reasons`): `same-owner` (sourced owner group),
  `same-institution` (no sourced owner), `same-wire-credit` (`basis` prior
  for the closed list cp, afp, reuters, ap; `measured` for any other credit
  seen in two or more owner groups within the 7-day window, recomputed every
  edition), `same-release` (relays of a communiqué, issuer unknown to the
  rules: one origin), `near-duplicate` (word 3-shingles, Jaccard at least
  3/5, at least 6 shingles each side, whoever owns the outlets). The bounds
  are printed in every view (`rules`). `independence.keys` keeps the member
  keys so a page can say why two members are one origin.
- **The relations are a union** (AUTONOMY's first row: same owner OR same
  wire credit OR same communiqué OR near-duplicate). A rule key is ADDED to
  the member's owner key (`owner:<group>`, else `institution:<id>`), never
  put in its place: a closed-list wire copy carries `owner:<group>` and
  `wire:<agency>`, a relay carries `owner:<group>` and `release:...`, a copy
  carrying a measured credit carries `owner:<group>` and `wire:<credit>`.
  Keying a credited copy by its credit alone (the rule of tranche B, and of
  this branch before review) split two outlets of one sourced owner into two
  origins as soon as their shared credit was measured as a wire: a
  measurement raised the count. With the union, adding a credit to the
  measured wires can only join origins (a test holds it monotone). One
  origin is the closure of every relation (union-find), so a wire copy also
  bridges its outlet's owner to the other outlets carrying the same credit:
  outlet A's own story, A's CP copy, B's CP copy and B's own story are one
  origin. That is the rule as AUTONOMY writes it, conservative (it never
  inflates independence); whether a wire copy should instead count apart
  from its outlet's own reporting is open to the founder (it would amend
  AUTONOMY's first row).
- Agence QMI is no longer a founder flag: events.py reads credits (codes
  only, stored per item as `credits`) and applies the measured rule. The
  flag `AGENCE_QMI_IS_WIRE` is removed from origin.py (tranche D): its
  `origin_of(item, source, wires)` and `classify_batch(..., wires=...)` follow
  the same measurement when the caller passes it (`events.measured_wires`),
  and without one an agency credit outside the closed list is an organisation
  credit (`unknown`, never own reporting), exactly the former default.
  Measured: 0 bylines credit Agence QMI in the 2,139 history items (the
  459 Journal de Québec items carry no author field), so the rule decides
  nothing today; the closed-list credits seen are cp (cn2i, le-devoir), afp
  and ap (le-devoir).
- Hydro-Québec in `etat-quebec` is the sourced fact (its annual report: the
  State is its sole shareholder), no longer a founder decision (section 16
  (a)); both it and the Government are declarations, so the grouping can
  neither inflate nor deflate a media origin count.
- Measured on the live edition: multi-institution events 17, their
  `reporting_origin_count` {1: 2, 2: 13, 3: 2}, the same as before (no QMI
  credit, no official/media copy pair in that edition); 7 events carry a
  declaration. Replay: multi-institution view events 1,794 before and after.
  The union changes no origin today: recomputed both ways, 0 of the 194 live
  events and 0 of the 10,379 replayed view events change their origin groups
  (the wire copies and relays seen, 6 and 1 live, 40 and 2 over the replay,
  never share an event with a member of the same owner or the same credit;
  merge reasons summed over the replayed views: `same-owner` 1,707,
  `near-duplicate` 77, no `same-wire-credit`, no `same-release`).

### 17.4 R10 in stored views (sections 6, 13)

The store keeps the ids of a withdrawn member (sticky membership, sticky
pairs); every derived field is computed over the members still present. The
view never lets a renderer credit or count a withdrawn voice: the member is
absent from `members`, listed under `withdrawn` (with `withdrawn_count`), and
absent from institutions, languages, independence, copies, language pairs,
fact attributions and item anchors; `member_count` counts the members
present. The silence roster gains the state `withdrawn` ("retiré à la
demande de l'éditeur"), scope `institution` (takedowns.yaml withdrew the whole
voice; listed in every event) or `article` (its items here only), and says
`not_established` only when the edition carries no collection fact for the
institution.

**Every derived field describes the members present**, in the builder and at
render time alike: type, places, `place_basis`, `place_votes`, label,
family and geo (`event_codes`), tier (`event_tier`: the weakest recorded
link among the members present), the window (`window_state_at`: a withdrawn
member's date never holds an event in window), independence, and facts with
their reduced counts (`event_facts`). Reduced counts keep, per dropped value,
the ids that stated it (`facts_reduced[].stated_by`, ids only, never the
value), so a takedown after the reduction takes its statements out of the
counts; a value kept because an official member stated it joins the counts
when that member is withdrawn; a kept row's `divergent` describes the values
it shows. The agency-credit measurement (`credits`, the window's wires)
never counts a withdrawn member either.

**Render time.** The view carries what those functions read: per member
present, its stored codes (`member_codes`: type scores, places, geo,
geo_place, credits, article domain, the join action and link, and its fact
atoms that can reach an event, while in window), and at the top level the
window's credit observations (`credit_observations`: per credit, item id,
source id, article domain and owner group of each member counted) with the
measurement (`credits`). `events.apply_takedowns(view, rules)` takes the
withdrawn members out of the observations, re-measures the wires, and puts
every event through `strip_withdrawn`, which recomputes every field above
with the builder's own functions; an event nothing touched comes out as it
went in (a test and the live edition hold an active rule that withdraws
nothing to an identical view). The registre seals the result.

Measured (scratch copies). Live edition, six single-source takedowns (six
large newsrooms): 4 to 8 events touched each, after `apply_takedowns`
0 events whose codes differ from `event_codes` over the members present
and 0 whose `place_votes` count more votes than members, idempotent (before
this fix, per source: up to 4 stale `place_votes`, 3 stale types, 2 stale
first places, 3 events counting more votes than members).
History, three points of the replay times the same six sources, the edition
without the withdrawn source's articles (collection drops them once the
takedown is listed): `apply_takedowns` of the view built before the takedown
equals the rebuild under it, byte for byte, in 18 of 18 runs (190 withdrawn
members kept by sticky membership). Cost: `apply_takedowns` 0.07 s on the
live view; the view grows from 714,638 to 810,903 bytes (member codes 63 kB).

What a rebuild cannot share with a stored view, and stays as built: when the
withdrawn articles are in the very edition the view was built from, the
matcher compared them with the others, so the link an article joining in
that edition was attached with, the neighbours, an event minted by a
withdrawn article (its id), `activity`/`last_edition` (an event "developed"
by a withdrawn article) and the members other articles chose may differ from
a rebuild that never read them. The stored view also lists those articles
by id under `withdrawn` and says `withdrawn` (scope `article`) for a voice
whose only article there was one of them, where the rebuild states that
voice's measured collection state. The next full edition rebuilds all of
it. Measured on the same 18 history runs with the withdrawn articles left
in the edition: every difference is one of these (counted per event and
run: 35 events in only one of the two views; among events with the same
members, join links differ in 110, neighbours in 3,078, activity in 1, the
`withdrawn` list in 13, the silence roster in 2; 7 events whose members the
matcher chose differently). Anchors other than item anchors are official
records matched by rule and stay as found (sticky, section 10). Once an
event's facts are reduced its atoms are gone, so the hedge codes
(`qualifiers`) of a value kept for an official member are not re-derived
when a member that co-stated the value is withdrawn (its id leaves the
value; its hedge code may stay).

### 17.5 Facts (section 9)

facts.py keeps a road token only when it has the shape of a name (a route
number of 1 to 3 digits, or a word of at least 3 letters that is not a
function word, a generic place noun or an adjective of "école"/"hôpital").
An event carries place facts as vocabulary codes only: a road name with no
corridor code, or any atom stored before the guard, never reaches a view.
Measured on the history: 178 road atoms → 105, with every function word gone
("a", "au", "après", "entre", "est", ...).

### 17.6 Event chain (section 12)

- `member_count` is the rows present (withdrawn excluded) and the `origin`
  counts are counted over the same rows, so they add up to it; a lean view
  without rows counts its members as `unknown`; `independent_count` above
  `member_count` is sealed as 0 (not established). `independent_count` keeps
  this section's meaning (every origin, declarations included); the
  media-only `reporting_origin_count` is a view field, not sealed, so the
  record's shape is unchanged.
- The registre re-applies an active takedown to the stored view before it
  seals (`events.apply_takedowns`, every derived field recomputed over the
  members present: section 17.4), so a seal minted from a view older than
  the request never credits the withdrawn voice nor carries a type or a
  place it decided; a seal already recorded is never rewritten.
- State growth is bounded: the newest 12 seals stay FULL (header, `dropped`
  diagnosis and record); every older seal is compacted to `{leaf, root}`. The
  chain stays verifiable from genesis by hash linkage alone (prev = the root
  before it, seq = its position, root = sha256(prev || leaf)); the record
  behind an old leaf is no longer in the private state. Past the newest
  twelve, an edition adds one pair of hashes (about 170 stored bytes).
  Compaction changes nothing the restore and persist witnesses judge (they
  read the edition and travaux chains only).

### 17.7 Shadow fields

Store and view: `place_votes`, `reporting_origin_count`, `declared_by`,
`independence.keys`, `independence.origins`, `facts_reduced[].stated_by`;
item codes: `geo_place`, `credits`. View only: `withdrawn_count`,
`member_codes`, silence rows `scope`, top-level `rules`, `credits` and
`credit_observations`. Method string: `events-v1 rules r2`.

## 18. Machine layer: delta-v2

`scripts/substrate.py` writes `public/delta/v2/latest.json` (method
`delta-v2 edition-cursor`) next to the unchanged `delta/latest.json`
(delta-v1.1). Same cursor: the main registre chain root. Inputs are stored
files only (`data/events/latest_events.json`, `data/normalized/latest_enriched.json`,
`sources.yaml`, `takedowns.yaml`), so the hourly roads-only lane produces the
same file from the same inputs.

Per event: `event_id`, codes (`type`, `family`, `places`), `{fr, en}` label,
`activity`, `window_state`, `tier` (the weakest matcher link, `null` for a
single-member event) with `grouping: "automatic"`, `counts` (`members`,
`members_in_edition`, `institutions`, `origins`, `reporting_origins`,
`declarations`, `languages`, `language_pairs`), `origin_classes`,
`institutions`, `languages`, `anchors` (`type` + `ref` only), `lineage`,
`seals`, page URLs in both languages, and `members`. A member always carries
codes; only a member of the **current** edition whose source is enabled also
carries `publisher`, `author` (when the feed gave one), `title` and `url`
(never an excerpt, never a summary, never a fact value).

R10 is re-applied at emit time: a withdrawn member is dropped, and every count,
institution, language, origin group and item anchor is **recomputed** from the
members that remain (the builder's own counts still include a withdrawn member
until its next full run). An event left with no member disappears. Events are
listed in `event_id` order (a deterministic order, not a ranking); `by_activity`
buckets them.

Absent or unbuilt events data: delta-v2 is omitted (a stale file is removed),
the reason is printed, delta-v1 and the Markdown twin are byte-identical to a
build without the event layer, and `llms.txt` lists no event pages.

Deprecation: once delta-v2 is published, delta-v1 carries
`deprecation: {status, since, sunset, successor, note}`. `since` is the first
sealed edition clock at or after 2026-10-06 and `sunset` is 90 days later, so
the date comes from the chain and does not slide with each render.

"Published" follows the switch of section 19: delta-v2 is written in
`preview` and `live`, listed in `llms.txt` and announced as delta-v1's
successor only in `live`, and neither written nor announced while `off`.

## 19. Wiring: the switch (tranche D, 2026-10-06)

`scripts/surfaces.py` holds `EVENTS_SURFACES = "off" | "preview" | "live"`,
committed `off`. Flipping it is a founder act made by a reviewed commit
(docs/AUTONOMY.md: approving what production serves is an act, not an
editorial choice). `VIGIE_EVENTS_SURFACES` overrides it for local runs and
tests only, never under GitHub Actions; any value other than the three modes
is `off`, printed as a diagnosis.

| | off | preview | live |
|---|---|---|---|
| render (`rank_display`) | no event emitter | `evenements.emit` (noindex) | `evenements.emit` + front door |
| `/` | the brief | the brief, untouched | the events front door (FR) |
| `/en/` | — | — | the events front door (EN) |
| `/le-point.html` | — | — | the brief, canonical updated, linked as the full river |
| `/evenements.html`, `/en/evenements.html`, event pages | not staged | staged, `noindex, nofollow` | staged, indexable (`/evenements.html` serves the front door with canonical `/`) |
| `/evenements/latest.json`, `/qualite.json` | not staged | staged | staged |
| `/delta/v2/latest.json` | not written | written, not listed | written, listed in `llms.txt`, delta-v1 deprecated |
| sitemap | as before | as before (omits them) | `/`, `/en/` with xhtml hreflang alternates, `/le-point.html` |
| sources page | as before | + ownership as structure | + ownership as structure |
| release gate (`stage_public`) | nothing new | required files, hreflang targets, noindex, `/` is the brief, front door ≤ 120 KB | required files, hreflang targets, indexable, `/` is the events door and links the river, the brief's canonical, sitemap targets, front door ≤ 120 KB |
| `vercel.json` (staged) | the config of before (event rules left out) | full | full |

- The emitter runs in the full edition and in the hourly roads-only
  re-render alike; it reads the stored view and never runs `events.py`. The
  roadworks block is refreshed by the lane; with the published ranking, so is
  what the rule derives from the roadworks and their clock (criterion 1 and
  the ages printed under "Pourquoi ici ?"); nothing else moves.
- R10 holds in every mode: `events.apply_takedowns` re-applies the active
  takedowns to the stored view at every render (it was handed a `Registry` in
  place of the rules before this tranche and faulted on every takedown), then
  the page builder's own filter; the release gate refuses a tree that still
  references a withdrawn item. A voice whose article here was withdrawn reads
  "retiré à la demande de l'éditeur" in the silence panel; a voice withdrawn
  whole is named nowhere on the event pages. The dossier layer (brief,
  récits, delta-v1, Markdown twin, explorer) re-renders dossiers clustered
  before the request in the render-only lanes: `takedown.filter_issues` now
  moves a voice withdrawn whole out of their silence map into `withdrawn`,
  as a full edition does, so it is never counted "absente de ce dossier".
- In live mode a fault of the event emitter leaves `/` empty (the previous
  front door is removed before it runs), so the release is refused,
  diagnosed, and the previous production release stays up; setting the
  switch back to `off` restores the brief at `/` on the next run.
- Known follow-ups, not done here: the record-layer pages (registre, mémoire,
  départ, récits, affiche, méthode) still call `/` "Le point"; in live mode it
  is the events front door and the brief is one link away (the river link,
  and their `/#travaux` links land on the front door's roadworks block, which
  carries that id). The English mirror of those pages (MIGRATION step 10) is
  not started, so English pages flag them as French-only.

### 19.1 End-to-end proof on the live state (2026-10-06)

A copy of the production state restored on 2026-10-06 (edition
2026-10-06T00:03Z, 325 candidates, 194 events, 767 declared obstructions of
which 172 with all lanes closed, collected 02:53Z), per mode: `events.py`,
`pipeline.py --render-only --stage`, `verify.py` with the data (Windows,
Python 3.14; nothing deployed).

- **off** is the release of `phase1/base` built without the event stores,
  byte for byte, except the four published method files this tranche edits
  (`ranking.md`, `REGISTRE.md`, `methode/classement.html`,
  `methode/registre.html`) and the manifest. Against `phase1/base` built with
  the stores (what a full run produced there), off also drops
  `delta/v2/latest.json`, the llms.txt "Events" section (links to pages
  nobody served) and delta-v1's deprecation: the defect the switch closes.
  Same release with or without the stores; same bytes under two
  `PYTHONHASHSEED` values in live mode (561 of 561 files).
- **Sizes** (staged files, MB): off 166, 7.54; preview 559, 13.51; live
  561, 13.60; live with a store replayed from 51 earlier editions (382
  records: 194 current, 186 permanent, 2 merged) 937, 18.38. Front door
  44.2 KB (budget 120 KB); largest event page 30.8 KB. The chain (events,
  render and stage, verify) took 102 s off, 134 s preview, 135 s live; the
  unit suite is 82 to 87 s of each, staging 2 s off and 12 to 14 s in
  preview and live.
- **Pages**: one h1 each, hreflang pairs reciprocal and staged, `noindex`
  on all 390 preview event pages and none live, sitemap targets staged, no
  inline style, script or handler on any event page, no external resource;
  served locally with the headers of `vercel.json`, the front door and an
  event page log no CSP violation; no horizontal scroll and no overlapping
  text at 375 and 1280 px on the two front doors and six event pages. No
  publisher 5-gram (from the 16,984 titles and excerpts of the live edition
  and the history) on any of the 376 permanent and merged pages; every
  current page carries some, as it should.
- **R10** (a source takedown, then the hourly lane's render-only path): no
  id, URL, title, author or excerpt of the voice in any staged file in any
  mode; the event pages do not name it.
- **Hourly lane** (a newer roadworks collection, render-only, pinned
  clock): the 388 event pages, `qualite.json` and `delta/v2` are unchanged;
  on the front doors and `latest.json` only the roadworks block and what the
  ranking derives from the roadworks clock (the ages under "Pourquoi ici ?",
  `rank_criteria.now`) move, as stated above. The wall clock moves no event
  file.
- **Fixed by this proof**: the hour ticks of every event timeline were
  pinned like out-of-window members (a misplaced hour on all 194 pages, two
  hours printed at one place on 161); `verify.py` failed after every live render (two checks read
  the events door as the brief), which would have blocked every release
  once the switch is live; a voice withdrawn whole stayed "absente de ce
  dossier" in the dossier layer until the next full edition; the stager
  wrote three CRLF files on Windows.
- **Open, not done here**: the brief carries two `style=` attributes (the
  dossier bar, `--voices`/`--silent`) that its CSP blocks, at `/` today and
  at `/le-point.html` in live; in the brief's story cards the photo credit
  overlaps the kicker line by a few pixels; the live front door does not
  carry `rel="describedby" href="/llms.txt"` (the brief does); staging takes
  12 to 22 s in preview and live on Windows (about 4 ms per file open), 2 s
  off.
