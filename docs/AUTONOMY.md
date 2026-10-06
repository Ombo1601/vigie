# AUTONOMY.md — what Vigie decides from evidence, so that no person has to

Status: **design rule, Phase 1**. Companion: `docs/EVENTS.md`, `docs/I18N.md`,
`docs/CHARTE.md` (draft). Applies to every reader-visible choice.

## The principle

Vigie is an aggregator. If its output depended on its founder's taste, the
founder would be a hidden hand, which is exactly what the "sans main cachée"
promise forbids. So a choice that changes what a reader sees is allowed only
when it is:

1. a **rule**, published in plain words (`ranking.md`, the method pages);
2. computed from **observable inputs**: what the feeds say, text similarity,
   time, official records, measured quality, sourced facts;
3. **explained on the page** where it applies ("Pourquoi ici ?", the tier
   chip, the origin panel), with the real values;
4. **recomputed every edition**, never frozen by an opinion;
5. **deterministic** (same inputs, same bytes), and logged when it changes.

What stays human is only what the law attaches to a person: opening accounts
or a mailbox, asking a publisher for permission, handling a takedown notice,
approving a production deploy. Those are acts, not editorial choices.

## Decision table

| Matter | Rule (no one decides) | Inputs | Shown as |
|---|---|---|---|
| Who counts as an independent origin | Two members are one origin if they share a sourced controlling owner (`owner_group`), OR carry the same wire credit, OR relay the same communiqué, OR their text is a near-duplicate (shingle overlap above a published bound, whoever owns them). | `sources.yaml` ownership keys with their public references; item text (compared at build time, never stored) | "3 articles → 2 origins", with the reason per merged origin |
| Hydro-Québec vs Gouvernement du Québec | Follows the sourced legal fact: the State is Hydro-Québec's sole shareholder, so they share an owner group. More importantly, official voices are **declarations**, never counted as independent corroboration of media reporting; they appear as anchors ("déclaré par"). The question stops mattering. | ownership fact + source kind | the origin panel lists declarations apart from reporting |
| Agence QMI and other agency credits | A credit counts as a wire when copy carrying it is observed in two or more distinct owner groups within the window; inside one group it is that group's own byline. Closed lists (La Presse Canadienne, CP, AFP, Reuters, AP) stay as priors, not as opinions. | bylines/credits across the edition | origin chip "agence" with the rule id |
| Whether a grouping says "certain" | The chip text follows the measured quality, published automatically as counts in `quality.json`. With no human-checked evidence the chip reads "regroupé automatiquement", with the measured precision and who labelled the evidence. It switches to "certain" only when the figure computed from human-checked samples clears the published bar (precision lower bound at least 0.95). Thresholds are refit from labelled data on a dev split whenever labels change. | `events_eval` output; label provenance | the chip and a link to the numbers |
| Ordering of events | Successive criteria, not hand-set weights: (1) what affects you now (an active official obstruction, alert or outage in the place), (2) geographic scope, (3) number of independent origins, capped, (4) freshness. Cut-offs are quantiles of the current edition, so a quiet week and an election night each rank by their own distribution. | event store, roadworks, anchors, edition distribution | "Pourquoi ici ?" with the actual values |
| Event labels and types | Controlled vocabulary, assigned by published lexicons; "unclassified" when evidence is thin. | titles of members | the label; vocabulary changes go in the `ranking.md` changelog |
| Admitting, keeping or cutting a source | Machine-checkable criteria: robots allow, honest-identity fetch answers within budget, valid feed, declared language and ownership with a citation, license note recorded. A source that fails is cut automatically with a published reason and date, re-probed on a schedule, and returns by itself when the criteria hold again. A brand-new publisher enters as a reviewed candidate entry (code review), never as a taste decision; asking for permission is a human act. | probes, `sources.yaml` | the public sources page |
| Takedown | Legal duty, no discretion: the entry removes the item and its URL in the same run; the id and counts stand. | `takedowns.yaml` | the "withdrawn" chip |
| Corrections of a grouping | A wrong grouping reported by a reader, or contradicted by a later merge or split rule, is recorded in a public ledger with its date; nobody argues the tone. | reports, lineage | the ledger |
| Wording FR/EN | Controlled vocabulary, reviewed once, then maintained as data; Vigie never translates publisher text. | catalogue | everywhere |

## Consequences for the build

- `events.py` derives independence with the rule above (copy detection is a
  build-time comparison; no text is stored).
- The pages read `quality.json` to word the tier chip; they never hard-code
  "certain".
- Ranking is a pure function with the criteria above and returns its own
  explanation; its constants are quantiles, not opinions.
- Every rule lives in one place and is mirrored in `ranking.md`.
