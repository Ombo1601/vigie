"""The substrate — Vigie for machines, without a second brain.

When nobody opens a browser, Vigie survives as the memory that stateless
agents do not have: what changed since a cursor, what each followed institution
actually published, where our own collection failed, and where the official
record stands. Three artefacts, all derived from the same stores as the brief,
all deterministic, all attribution-preserving:

  public/llms.txt          curated map for agents (llms.txt v2; rel="describedby")
  public/index.html.md     Markdown twin of the front door (rel="alternate")
  public/delta/latest.json cursor-addressed edition delta (delta-v1.1, deprecated once v2 ships)
  public/delta/v2/latest.json  the same cursor over events (delta-v2): codes, {fr,en} labels,
                           counts, tier, activity, anchors, lineage; publisher, author, title and
                           URL for current-edition members whose source is enabled

House law holds for machines exactly as for people: titles verbatim, publisher
name, author (when the publisher's feed gives one) and URL on every item, no
rewriting, and no asserted silence — an institution absent from the dossiers is
reported as published-outside-dossiers, as a collection gap of ours, or as not
established. The machine files carry publisher, author, title and URL only:
no excerpt and no summary, so a machine never receives more of a publisher's
text than the brief shows a reader. Stdlib only; no network.

Events (delta-v2) are read from stored files only (data/events/latest_events.json,
data/normalized/latest_enriched.json, sources.yaml, takedowns.yaml), so the hourly
roads-only lane, which re-renders without collecting, re-applies the takedown
rules (R10) itself: a withdrawn voice is dropped from the members AND from every
count, institution list, origin group and anchor derived from it. When the events
data is absent or not built, delta-v2 is omitted (and a stale one removed) with a
printed diagnosis, and every other artefact is byte-identical to a build without
the event layer.
"""
from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import registre  # noqa: E402
import resident_brief as brief  # noqa: E402
import store_io  # noqa: E402

# delta-v1.1: delta-v1 plus the optional `author` on items and label_source
# (R1), and the optional institution lists `withdrawn_on_request` (R10) and
# `cut_by_vigie` (sources.yaml cuts). Purely additive - every delta-v1 key
# keeps its meaning - so a v1 reader still works; the string moves so a
# consumer can tell the shapes apart.
METHOD = "delta-v1.1 edition-cursor"
SITE_URL = "https://vigieqc.com"
OUT_LLMS = ROOT / "public" / "llms.txt"
OUT_MD = ROOT / "public" / "index.html.md"
OUT_DELTA = ROOT / "public" / "delta" / "latest.json"
OUT_DELTA_V2 = ROOT / "public" / "delta" / "v2" / "latest.json"
EVENTS_VIEW = ROOT / "data" / "events" / "latest_events.json"
ENRICHED = ROOT / "data" / "normalized" / "latest_enriched.json"

# delta-v2: events. Same cursor (the registre chain root) as delta-v1.
METHOD_V2 = "delta-v2 edition-cursor"
EVENTS_VIEW_FORMAT = "events-latest-v1"
# delta-v1 is deprecated for 90 days from the first sealed edition at or after
# this instant (an edition clock, never a wall clock), so the sunset date is a
# fact of the chain and does not slide forward with every render.
V1_DEPRECATED_FROM = "2026-10-06T00:00:00+00:00"
V1_SUNSET_DAYS = 90

MD_STORIES_CAP = 24
MD_DOSSIER_ITEMS_CAP = 6
MD_ROADS_CAP = 10
DELTA_ITEMS_CAP = 5
LICENCE_NOTE_EN = f"Titles belong to their publishers; see {SITE_URL}/methode/legal.html."
LICENCE_NOTE_FR = f"Les titres appartiennent à leurs éditeurs ; voir {SITE_URL}/methode/legal.html."


def _plain(value: object, cap: int | None = None) -> str:
    text = brief.plain(value)
    if cap is not None and len(text) > cap:
        text = text[:cap].rstrip() + "…"
    return text


def _md(value: object, cap: int | None = None) -> str:
    """Markdown-safe inline text: verbatim words, neutralised markup characters."""
    text = _plain(value, cap)
    for ch in ("\\", "`", "*", "_", "[", "]", "<", ">", "#", "|"):
        text = text.replace(ch, "\\" + ch)
    return text


def _by(item: object) -> str:
    """Markdown byline prefix "Par {author} · " (empty when the feed gave none)."""
    author = brief.author_of(item)
    return f"Par {_md(author)} · " if author else ""


def _iso(value: object) -> str:
    dt = brief.parse_date(value)
    return dt.isoformat() if dt else ""


def _day(value: object) -> str:
    dt = brief.parse_date(value)
    return f"{dt:%Y-%m-%d}" if dt else "date non précisée"


# --------------------------------------------------------------------------- #
# Shared facts
# --------------------------------------------------------------------------- #
def voices_of(issue: dict, names: dict) -> tuple[list[dict], list[dict]]:
    spoke: list[dict] = []
    for voice in issue.get("tensions") or []:
        if not isinstance(voice, dict) or not voice.get("institution_id"):
            continue
        spoke.append({
            "institution_id": str(voice["institution_id"]),
            "institution_name": _plain(voice.get("institution_name") or voice["institution_id"], 120),
            "source_kind": "official" if voice.get("source_kind") == "official" else "media",
            "item_count": len([i for i in (voice.get("items") or []) if isinstance(i, dict)]),
        })
    spoke.sort(key=lambda v: (0 if v["source_kind"] == "official" else 1, v["institution_id"]))
    silence = issue.get("silence") if isinstance(issue.get("silence"), dict) else {}
    silent: list[dict] = []
    for row in silence.get("silent") or []:
        if not isinstance(row, dict):
            continue
        iid = str(row.get("institution_id") or row.get("source_id") or "")
        if not iid:
            continue
        meta = names.get(iid) if isinstance(names.get(iid), dict) else {}
        silent.append({
            "institution_id": iid,
            "institution_name": _plain(row.get("institution_name") or row.get("source_name") or meta.get("name") or iid, 120),
            "source_kind": "official" if row.get("source_kind") == "official" else "media",
        })
    silent.sort(key=lambda v: (0 if v["source_kind"] == "official" else 1, v["institution_id"]))
    return spoke, silent


def items_of(issue: dict, cap: int) -> list[dict]:
    rows: list[dict] = []
    seen: set[str] = set()
    for voice in issue.get("tensions") or []:
        if not isinstance(voice, dict):
            continue
        for it in voice.get("items") or []:
            if not isinstance(it, dict):
                continue
            url = brief.safe_url(it.get("url"))
            title = brief.capped_title(it.get("title"))
            if not url or not title or url in seen:
                continue
            seen.add(url)
            rows.append({
                "title": title,
                "author": brief.author_of(it),
                "url": url,
                "source_name": _plain(it.get("source_name") or it.get("source_id") or "", 120),
                "institution_id": str(it.get("institution_id") or ""),
                "published_at": _iso(it.get("published_at")),
            })
    rows.sort(key=lambda r: (r["published_at"] or "", r["url"]), reverse=True)
    return rows[:cap]


def dossier_view(issue: dict, names: dict, cap: int) -> dict:
    spoke, silent = voices_of(issue, names)
    tracking = issue.get("tracking") if isinstance(issue.get("tracking"), dict) else {}
    view = {
        "issue_id": str(issue.get("issue_id") or ""),
        "question": _plain(issue.get("question"), registre.QUESTION_CAP),
        "label_kind": str(issue.get("label_kind") or ""),
        "geo_focus": sorted(str(g) for g in (issue.get("geo_focus") or []) if isinstance(g, str)),
        "item_count": registre._int(issue.get("item_count")),
        "official_voice_count": registre._int(issue.get("official_voice_count")),
        "media_remix": bool(issue.get("media_remix")),
        "spoke": spoke,
        "silent": silent,
        # Scope guard: this list is dossier-scoped. Read without its scope it
        # looks like an edition-level silence claim, which is exactly the defect
        # corrected on 2026-09-30. The key travels with the data.
        "silent_scope": "dossier",
        "silent_meaning": "absent from this dossier only; not a claim about the edition or the institution",
        "items": items_of(issue, cap),
    }
    if tracking.get("editions_seen") is not None:
        view["tracking"] = {k: tracking.get(k) for k in ("first_seen", "last_seen", "editions_seen", "editions_missed") if tracking.get(k) is not None}
    # Publisher text must travel with its owner: an attributed dossier exposes
    # the source name + URL so a machine can cite it, never just the headline.
    if view["label_kind"] == "attributed_headline":
        source = issue.get("label_source")
        if isinstance(source, dict):
            view["label_source"] = {
                k: source.get(k) for k in ("source_id", "source_name", "url") if source.get(k)
            }
            author = brief.author_of(source)
            if author:
                view["label_source"]["author"] = author
    return view


def roadworks_view(roadworks: dict | None, cap: int) -> dict | None:
    rw = roadworks if isinstance(roadworks, dict) else {}
    events = rw.get("events")
    fetched = _iso(rw.get("fetched_at"))
    if not isinstance(events, list) or not fetched:
        return None
    events = [e for e in events if isinstance(e, dict) and e.get("event_id")]
    ordered = sorted(events, key=lambda e: str(e.get("event_id")))
    ordered.sort(key=lambda e: str(e.get("update_date") or ""), reverse=True)
    ordered.sort(key=lambda e: brief.RW_SEVERITY.get(str(e.get("vehicle_impact") or ""), 6))
    diff = rw.get("diff") if isinstance(rw.get("diff"), dict) else {}
    return {
        "fetched_at": fetched,
        "active_count": len(events),
        "source": _plain(rw.get("institution_name") or rw.get("source_name") or "", 120),
        "dataset_url": brief.safe_url(rw.get("dataset_url")) or None,
        "license_note": _plain(rw.get("license_note"), 200) or None,
        "diff": {
            "has_previous": bool(diff.get("has_previous")),
            "new_count": registre._int(diff.get("new_count")),
            "removed_count": registre._int(diff.get("removed_count")),
            "changed_count": registre._int(diff.get("changed_count")),
        },
        "most_restrictive": [
            {
                "event_id": str(e.get("event_id")),
                "road_names": [_plain(r, 120) for r in (e.get("road_names") or []) if isinstance(r, str)][:4],
                "vehicle_impact": str(e.get("vehicle_impact") or "unknown"),
                "impact_label": brief.RW_IMPACT_LABELS.get(str(e.get("vehicle_impact") or ""), brief.RW_IMPACT_LABELS["unknown"]),
                "start_date": _iso(e.get("start_date")) or None,
                "end_date": _iso(e.get("end_date")) or None,
                "end_date_accuracy": str(e.get("end_date_accuracy") or "") or None,
            }
            for e in ordered[:cap]
        ],
        "note": "Déclarations officielles de la Ville de Québec, relayées telles quelles. Retiré du flux ≠ terminé.",
    }


def story_rows(ranked: list[dict], now: datetime) -> list[dict]:
    rows, _ = brief.prepare_items(ranked or [], now)
    return rows


# --------------------------------------------------------------------------- #
# delta/latest.json
# --------------------------------------------------------------------------- #
def build_delta(issues: list[dict], ledger: dict | None, roadworks: dict | None,
                state: dict, status: dict) -> dict:
    seals = state.get("seals") or []
    last = seals[-1] if seals else {}
    prev = seals[-2] if len(seals) > 1 else {}
    names = state.get("names") or {}
    by_id = {str(i.get("issue_id")): i for i in issues if isinstance(i, dict) and i.get("issue_id")}
    ledger = ledger if isinstance(ledger, dict) else {}
    has_previous = bool(ledger.get("has_previous"))

    def _bucket(key: str) -> list[dict]:
        out: list[dict] = []
        for entry in ledger.get(key) or []:
            if not isinstance(entry, dict) or not entry.get("issue_id"):
                continue
            iid = str(entry["issue_id"])
            if iid in by_id:
                view = dossier_view(by_id[iid], names, DELTA_ITEMS_CAP)
            else:
                # Quiet dossiers left the collection: identity, label and owner
                # only — never a publisher headline without its source.
                view = {"issue_id": iid, "question": _plain(entry.get("question"), registre.QUESTION_CAP),
                        "label_kind": str(entry.get("label_kind") or ""),
                        "geo_focus": sorted(str(g) for g in (entry.get("geo_focus") or []) if isinstance(g, str))}
                source = entry.get("label_source")
                if isinstance(source, dict) and view["label_kind"] == "attributed_headline":
                    view["label_source"] = {
                        k: source.get(k) for k in ("source_id", "source_name", "url") if source.get(k)
                    }
            if key == "developed" and isinstance(entry.get("delta"), dict):
                view["delta"] = dict(entry["delta"])
            out.append(view)
        out.sort(key=lambda d: d["issue_id"])
        return out

    register = registre.institution_register(state)

    def _inst(r: dict, *extra: str) -> dict:
        keys = ("institution_id", "institution_name", "source_kind", "current", *extra)
        return {k: r.get(k) for k in keys if k in r}

    return {
        "method": METHOD,
        "site": SITE_URL,
        "attribution": LICENCE_NOTE_EN,
        "edition": last.get("edition") or "",
        "previous_edition": prev.get("edition") or "",
        "cursor": last.get("root") or "",
        "previous_cursor": prev.get("root") or last.get("prev") or "",
        "checkpoint": {"seq": last.get("seq"), "root": last.get("root"), "url": f"{SITE_URL}/registre/checkpoint.txt"} if last else None,
        "collection": {
            "at": status.get("at") or "",
            "feeds_ok": status.get("ok"),
            "feeds_total": status.get("total"),
            "partial": bool(status.get("partial")),
        },
        "dossiers": {
            "has_previous": has_previous,
            "new": _bucket("new") if has_previous else [],
            "developed": _bucket("developed") if has_previous else [],
            "quiet": _bucket("quiet") if has_previous else [],
            "all": [dossier_view(by_id[i], names, DELTA_ITEMS_CAP) for i in sorted(by_id)],
        },
        "institutions": {
            "note": (
                "State per followed institution for this edition. 'published' means its feeds returned "
                "items that no dossier picked up — the institution was NOT silent. 'collection_gap' means "
                "Vigie failed to collect it. No state is ever derived from Vigie's clustering alone. "
                "'withdrawn_on_request' (a publisher's removal request) and 'cut_by_vigie' (Vigie's own "
                "decision to stop following, sources.yaml) appear only when they apply; neither is a silence."
            ),
            "spoke": [_inst(r) for r in register if r["current"] == registre.STATE_SPOKE],
            "published": [_inst(r, "items_collected") for r in register if r["current"] == registre.STATE_PUBLISHED],
            "no_items_collected": [_inst(r) for r in register if r["current"] == registre.STATE_NO_ITEMS],
            "collection_gap": [_inst(r, "collection_gap_streak") for r in register if r["current"] == registre.STATE_COLLECTION_GAP],
            "not_established": [_inst(r) for r in register if r["current"] == registre.STATE_NOT_ESTABLISHED],
            # Withdrawn on the publisher's request (R10): present only when there is one.
            **({"withdrawn_on_request": withdrawn} if (withdrawn := [
                _inst(r, "withdrawn_requested_at") for r in register if r["current"] == registre.STATE_WITHDRAWN
            ]) else {}),
            # Cut by Vigie itself (sources.yaml enabled: false): present only when there is one.
            **({"cut_by_vigie": cut} if (cut := [
                _inst(r, "cut_at") for r in register if r["current"] == registre.STATE_CUT
            ]) else {}),
        },
        "correction": registre.correction_notice(state),
        "roadworks": roadworks_view(roadworks, DELTA_ITEMS_CAP),
        "links": {
            "brief": f"{SITE_URL}/",
            "brief_markdown": f"{SITE_URL}/index.html.md",
            "dossiers": f"{SITE_URL}/dossiers.html",
            "depart": f"{SITE_URL}/partir.html",
            "memoire": f"{SITE_URL}/memoire.html",
            "registre": f"{SITE_URL}/registre.html",
            "chain": f"{SITE_URL}/registre/chain.json",
            "institutions": f"{SITE_URL}/registre/institutions.json",
            "sources": f"{SITE_URL}/methode/sources.html",
            "method": f"{SITE_URL}/methode/registre.html",
            "legal": f"{SITE_URL}/methode/legal.html",
        },
        "rules_for_agents": [
            "Cite the original publisher (source_name + url) and the author when `author` is present, for every item; Vigie is an index, never the author.",
            LICENCE_NOTE_EN,
            "Titles are verbatim publisher titles (truncated only). Do not present Vigie text as a quotation of the publisher.",
            "Never infer that an institution was silent. 'published' means it produced items this edition that no dossier picked up; 'collection_gap' means Vigie failed to collect it. Only 'spoke' means it entered a dossier.",
            "A dossier needs a named subject and two institutions, so most collected items never enter one. Absence from dossiers is a property of Vigie's clustering, not of the institution.",
            "'quiet' dossiers left this collection; nothing is 'resolved' or 'ended'.",
            "Verify this edition against registre/chain.json before treating it as a record.",
        ],
    }


def v1_deprecation(state: dict) -> dict:
    """The deprecation notice carried inside delta-v1 once delta-v2 is published.

    `since` is the first sealed edition at or after V1_DEPRECATED_FROM (the
    current edition when the chain has none yet reached it, never earlier than
    that instant) and `sunset` is 90 days after it: both come from edition
    clocks, so the date is the same on every render and in every replay."""
    floor = registre.parse_ts(V1_DEPRECATED_FROM)
    since = floor
    for seal in state.get("seals") or []:
        when = registre.parse_ts(seal.get("edition")) if isinstance(seal, dict) else None
        if when is not None and when >= floor and (since == floor or when < since):
            since = when
    return {
        "status": "deprecated",
        "since": f"{since:%Y-%m-%d}",
        "sunset": f"{since + timedelta(days=V1_SUNSET_DAYS):%Y-%m-%d}",
        "successor": f"{SITE_URL}/delta/v2/latest.json",
        "note": (
            "delta-v1.1 stays unchanged until the sunset date; it describes dossiers, delta-v2 "
            "describes events (every collected item, any outlet, any language). The cursor is "
            "the same registre chain root in both."
        ),
    }


# --------------------------------------------------------------------------- #
# delta/v2/latest.json — events
# --------------------------------------------------------------------------- #
def _events_module():
    import events  # noqa: PLC0415 - lazy: substrate stays importable without the event layer

    return events


def load_events_input(view_path: Path = EVENTS_VIEW, enriched_path: Path = ENRICHED,
                      reg: object = None) -> tuple[dict | None, str]:
    """(input, diagnosis) read from stored files only. `input` is None, with the
    reason, when delta-v2 cannot be built honestly: no events view, a view that
    was not built, or no takedown/sources registry (R10 fails closed: events
    carry publisher text, so they are never emitted without the withdrawal rules)."""
    view = registre.load_json(view_path)
    if not isinstance(view, dict) or not view:
        return None, f"events view absent or unreadable ({Path(view_path).name})"
    if view.get("format") != EVENTS_VIEW_FORMAT or view.get("status") != "ok" \
            or not isinstance(view.get("events"), list):
        return None, f"events view not built (status {str(view.get('status') or 'unknown')[:40]})"
    if reg is None:
        try:
            reg = _events_module().Registry.load()
        except Exception as exc:  # noqa: BLE001 - diagnosed, delta-v2 omitted
            return None, f"sources/takedowns unreadable ({type(exc).__name__}); R10 cannot be applied"
    enriched = registre.load_json(enriched_path)
    candidates = enriched.get("candidates") if isinstance(enriched, dict) else None
    texts: dict[str, dict] = {}
    for c in candidates if isinstance(candidates, list) else []:
        if isinstance(c, dict) and c.get("id"):
            texts[str(c["id"])] = c
    return {"view": view, "texts": texts, "reg": reg}, ""


def _member_view(row: dict, item: dict | None, reg: object, text_ok: bool) -> dict:
    """One member: codes always; publisher, author, title and URL only for a
    current-edition item whose source is enabled (the R1 diet of delta-v1.1,
    never an excerpt)."""
    out = {
        "item_id": str(row["item_id"]),
        "institution": str(row.get("institution") or ""),
        "language": str(row.get("language") or ""),
        "origin_class": str(row.get("origin_class") or "unknown"),
        "published_at": row.get("published_at") if isinstance(row.get("published_at"), str) else None,
        "first_seen": str(row.get("first_seen") or ""),
        "in_edition": False,
    }
    if not (text_ok and isinstance(item, dict)):
        return out
    url = brief.safe_url(item.get("url"))
    title = brief.capped_title(item.get("title"))
    if not url or not title:
        return out   # a headline never travels without its link
    rec = getattr(reg, "by_id", {}).get(str(row.get("source_id") or "")) or {}
    out["in_edition"] = True
    out["publisher"] = _plain(item.get("source_name") or rec.get("institution_name") or rec.get("name")
                              or row.get("institution") or "", 120)
    author = brief.author_of(item)
    if author:
        out["author"] = author
    out["title"] = title
    out["url"] = url
    return out


def event_v2(e: dict, texts: dict, reg: object) -> dict | None:
    """One event for machines, or None when no member survives the takedown rules.

    Every count is recomputed from the members that remain: the builder counts
    a withdrawn member's institution, language and origin until its next full
    run, and a withdrawn voice is never credited or counted (R10)."""
    ev = _events_module()
    in_edition = {str(i) for i in e.get("in_edition") or []}
    shown: list[dict] = []
    gone: set[str] = set()
    for row in e.get("members") or []:
        if not isinstance(row, dict) or not row.get("item_id"):
            continue
        iid = str(row["item_id"])
        item = texts.get(iid)
        host = ev.article_host(item.get("url")) if isinstance(item, dict) else ""
        if reg.withdrawn(row, host) or (isinstance(item, dict) and reg.rules.match_item(item)):
            gone.add(iid)
            continue
        shown.append(row)
    if not shown:
        return None
    ids = [str(r["item_id"]) for r in shown]
    rows = {str(r["item_id"]): r for r in shown}
    copies = [p for p in e.get("copies") or [] if isinstance(p, list) and len(p) == 2 and set(p) <= set(ids)]
    indep = ev.independence(ids, rows, copies)
    members = [
        _member_view(r, texts.get(str(r["item_id"])), reg,
                     str(r["item_id"]) in in_edition and str(r.get("source_id") or "") in reg.enabled)
        for r in shown
    ]
    classes: dict[str, int] = {}
    for r in shown:
        key = str(r.get("origin_class") or "unknown")
        classes[key] = classes.get(key, 0) + 1
    institutions = sorted({str(r.get("institution") or "") for r in shown} - {""})
    languages = sorted({str(r.get("language")) for r in shown if r.get("language") in ("fr", "en")})
    pairs = [p for p in e.get("language_pairs") or [] if isinstance(p, dict)
             and p.get("fr") in rows and p.get("en") in rows]
    anchors = sorted(
        {(str(a.get("type") or ""), str(a.get("ref") or ""))
         for a in e.get("anchors") or []
         if isinstance(a, dict) and a.get("type") and a.get("ref")
         and not (a["type"] in ev.ITEM_ANCHORS and str(a["ref"]) in gone)})
    lineage = e.get("lineage") if isinstance(e.get("lineage"), dict) else {}
    eid = str(e["event_id"])
    label = e.get("label") if isinstance(e.get("label"), dict) else {}
    return {
        "event_id": eid,
        "type": str(e.get("type") or ""),
        "family": str(e.get("family") or ""),
        "places": [str(p) for p in e.get("places") or []],
        "label": {"fr": _plain(label.get("fr"), 120), "en": _plain(label.get("en"), 120)},
        "activity": str(e.get("activity") or ""),
        "window_state": str(e.get("window_state") or ""),
        # The weakest matcher link that grouped the members, as the matcher
        # measured it. A grouping is automatic: how far to trust it is the
        # published measure (links.quality), never a word of ours.
        "tier": (str(e["tier"]) if e.get("tier") and len(ids) > 1 else None),
        "grouping": "automatic",
        "born_edition": str(e.get("born_edition") or ""),
        "last_edition": str(e.get("last_edition") or ""),
        "counts": {
            "members": len(ids),
            "members_in_edition": sum(1 for m in members if m["in_edition"]),
            "institutions": len(institutions),
            "reporting_origins": int(indep["reporting_count"]),
            "origins": int(indep["count"]),
            "declarations": len(indep["declarations"]),
            "languages": len(languages),
            "language_pairs": len(pairs),
        },
        "origin_classes": {k: classes[k] for k in sorted(classes)},
        "institutions": institutions,
        "languages": languages,
        "anchors": [{"type": t, "ref": r} for t, r in anchors],
        "lineage": {
            "merged_into": str(lineage.get("merged_into") or ""),
            "absorbed": sorted(str(x) for x in lineage.get("absorbed") or []),
            "detached": sorted(str(x) for x in lineage.get("detached") or []),
        },
        "seals": sorted(int(s) for s in e.get("seals") or [] if isinstance(s, int) and s >= 1),
        "pages": {"fr": f"{SITE_URL}/evenements/{eid}.html", "en": f"{SITE_URL}/en/evenements/{eid}.html"},
        "members": members,
    }


def build_delta_v2(view: dict, texts: dict, reg: object, state: dict, status: dict) -> dict:
    """delta-v2: every event of the current edition, same cursor as delta-v1.

    Events come in event_id order (a deterministic order, not a ranking: Vigie's
    display order is explained on its own pages); `activity` buckets them."""
    seals = state.get("seals") or []
    last = seals[-1] if seals else {}
    prev = seals[-2] if len(seals) > 1 else {}
    out_events: list[dict] = []
    for e in sorted((x for x in view.get("events") or [] if isinstance(x, dict) and x.get("event_id")),
                    key=lambda x: str(x["event_id"])):
        built = event_v2(e, texts, reg)
        if built is not None:
            out_events.append(built)
    buckets = {k: [e["event_id"] for e in out_events if e["activity"] == k] for k in ("new", "developed", "quiet")}
    events_edition = str(view.get("edition") or "")
    return {
        "method": METHOD_V2,
        "site": SITE_URL,
        "attribution": LICENCE_NOTE_EN,
        "edition": last.get("edition") or "",
        "previous_edition": prev.get("edition") or "",
        "cursor": last.get("root") or "",
        "previous_cursor": prev.get("root") or last.get("prev") or "",
        "checkpoint": {"seq": last.get("seq"), "root": last.get("root"), "url": f"{SITE_URL}/registre/checkpoint.txt"} if last else None,
        "collection": {
            "at": status.get("at") or "",
            "feeds_ok": status.get("ok"),
            "feeds_total": status.get("total"),
            "partial": bool(status.get("partial")),
        },
        # The event layer's own edition clock, stated rather than assumed: when
        # it differs from `edition`, the events are those of the last full build.
        "events_edition": events_edition,
        "events_edition_is_cursor_edition": bool(events_edition) and events_edition == (last.get("edition") or ""),
        "event_count": len(out_events),
        "by_activity": buckets,
        "events": out_events,
        "links": {
            "events": f"{SITE_URL}/evenements.html",
            "events_en": f"{SITE_URL}/en/evenements.html",
            "events_json": f"{SITE_URL}/evenements/latest.json",
            "quality": f"{SITE_URL}/qualite.json",
            "delta_v1": f"{SITE_URL}/delta/latest.json",
            "brief": f"{SITE_URL}/",
            "chain": f"{SITE_URL}/registre/chain.json",
            "sources": f"{SITE_URL}/methode/sources.html",
            "method": f"{SITE_URL}/methode/registre.html",
            "legal": f"{SITE_URL}/methode/legal.html",
        },
        "rules_for_agents": [
            "Cite the original publisher (publisher + url) and the author when `author` is present, for every member that carries a title; Vigie is an index, never the author.",
            LICENCE_NOTE_EN,
            "Titles are verbatim publisher titles (truncated only), never an excerpt and never translated. Members without `url` carry codes only: their text is not current or their source is no longer followed.",
            "Events are grouped automatically by published rules. `tier` is the weakest matcher link; `links.quality` publishes how often such groupings were right. Do not read a tier as a confirmation.",
            "`counts.origins` counts independent origins by rule (shared owner, wire credit, relayed communiqué, near-duplicate text); `counts.declarations` are official statements, never counted as corroboration (`counts.reporting_origins` excludes them).",
            "`anchors` are pointers to official records found by a named rule; they are never a confirmation.",
            "An event absent from a later edition has left the collection window; nothing is 'resolved' or 'ended'.",
            "Verify this edition against registre/chain.json before treating it as a record.",
        ],
    }


# --------------------------------------------------------------------------- #
# index.html.md
# --------------------------------------------------------------------------- #
def render_markdown(rows: list[dict], issues: list[dict], ledger: dict | None, roadworks: dict | None,
                    state: dict, status: dict) -> str:
    seals = state.get("seals") or []
    last = seals[-1] if seals else {}
    names = state.get("names") or {}
    ledger = ledger if isinstance(ledger, dict) else {}
    lines: list[str] = []
    lines.append("# Vigie — Québec, à hauteur de vie")
    lines.append("")
    lines.append("> Un point local pour la ville de Québec : titres d’éditeurs cités tels quels (avec leur auteur, leur éditeur et leur lien), dossiers où plusieurs institutions se répondent, entraves officielles, et un registre scellé qui distingue ce que chaque institution suivie a publié de ce que notre collecte a manqué. Vigie n’écrit pas la nouvelle et ne décide pas de ce qui est vrai.")
    lines.append("")
    if status.get("at"):
        lines.append(f"- Collecte : {status['at']} — {status.get('ok', 0)} flux disponibles sur {status.get('total', 0)}" + (" (collecte partielle)" if status.get("partial") else ""))
    if last:
        lines.append(f"- Édition scellée n° {last.get('seq')} : `{last.get('root')}` — vérifiable dans [chain.json]({SITE_URL}/registre/chain.json)")
    lines.append(f"- {LICENCE_NOTE_FR}")
    lines.append(f"- Version HTML : {SITE_URL}/ · Delta machine : {SITE_URL}/delta/latest.json · Carte du site pour agents : {SITE_URL}/llms.txt")
    lines.append("")

    lines.append("## Le point")
    lines.append("")
    if rows:
        for r in rows[:MD_STORIES_CAP]:
            src = _md(r.get("source_name") or r.get("source_id") or "source")
            lines.append(f"- **{_md(r.get('title'))}** — {_by(r)}{src}, {_day(r.get('published'))}. <{r['url']}>")
    else:
        lines.append("Aucun article récent avec une date de publication exploitable dans cette collecte.")
    lines.append("")

    lines.append("## Ce que les sources racontent ensemble")
    lines.append("")
    dossiers = [i for i in issues if isinstance(i, dict) and i.get("issue_id")]
    if dossiers:
        for iss in dossiers:
            view = dossier_view(iss, names, MD_DOSSIER_ITEMS_CAP)
            heading = _md(view["question"]) or view["issue_id"]
            if view.get("label_kind") == "attributed_headline":
                label_source = iss.get("label_source") if isinstance(iss.get("label_source"), dict) else {}
                owner = _md(label_source.get("source_name") or "", 120)
                heading += f" — titre de {owner}" if owner else " — titre d’un éditeur, cité tel quel"
                label_author = brief.author_of(label_source)
                if label_author:
                    heading += f", par {_md(label_author)}"
            lines.append(f"### {heading}")
            spoke = ", ".join(f"{_md(v['institution_name'])}{' (officiel)' if v['source_kind'] == 'official' else ''}" for v in view["spoke"])
            silent = ", ".join(f"{_md(v['institution_name'])}{' (officiel)' if v['source_kind'] == 'official' else ''}" for v in view["silent"])
            lines.append(f"- Dans ce dossier ({len(view['spoke'])}) : {spoke or '—'}")
            # Dossier-scoped, and labelled as such: this list says who is absent
            # from THIS dossier, never who was silent across the collection.
            lines.append(f"- Absents de ce dossier ({len(view['silent'])}) : {silent or '—'}")
            if view.get("media_remix"):
                lines.append("- Aucun texte officiel dans ce dossier : reprise médiatique seulement.")
            for it in view["items"]:
                lines.append(f"- {_md(it['title'])} — {_by(it)}{_md(it['source_name'])}, {_day(it['published_at'])}. <{it['url']}>")
            lines.append("")
    else:
        lines.append("Aucun dossier à plusieurs voix dans cette édition.")
        lines.append("")
    if dossiers:
        lines.append(f"Vue complète : {SITE_URL}/dossiers.html (un dossier par page, tous les titres tels quels).")
        lines.append("")

    if ledger.get("has_previous"):
        lines.append("## Depuis la dernière édition")
        lines.append("")
        for key, label in (("new", "Nouveaux dossiers"), ("developed", "Dossiers développés"), ("quiet", "Disparus de cette collecte (jamais « résolus »)")):
            entries = [e for e in (ledger.get(key) or []) if isinstance(e, dict)]
            names_out = []
            for e in entries[:6]:
                if str(e.get("label_kind") or "") == "attributed_headline":
                    src = e.get("label_source") if isinstance(e.get("label_source"), dict) else {}
                    owner = _md(src.get("source_name") or src.get("source_id") or "", 120)
                    names_out.append(f"titre d’un éditeur ({owner})" if owner else "titre d’un éditeur")
                else:
                    names_out.append(_md(e.get("question"), 120))
            lines.append(f"- {label} : {len(entries)}" + (" — " + "; ".join(names_out) if names_out else ""))
        lines.append("")

    rw = roadworks_view(roadworks, MD_ROADS_CAP)
    if rw:
        lines.append("## Travaux et entraves déclarés par la Ville")
        lines.append("")
        lines.append(f"- {rw['active_count']} entraves actives dans la collecte du {rw['fetched_at']}" + (
            f" ; depuis la collecte précédente : +{rw['diff']['new_count']} nouvelles, {rw['diff']['changed_count']} modifiées, {rw['diff']['removed_count']} retirées du flux (retiré ≠ terminé)."
            if rw["diff"]["has_previous"] else "."))
        for e in rw["most_restrictive"]:
            roads = ", ".join(_md(r) for r in e["road_names"]) or "voie non précisée"
            until = f", jusqu’au {_day(e['end_date'])}" + (" (estimé)" if e.get("end_date_accuracy") == "estimated" else "") if e.get("end_date") else ""
            lines.append(f"- {roads} — {e['impact_label']}{until}.")
        if rw.get("dataset_url"):
            lines.append(f"- Source officielle : <{rw['dataset_url']}>")
        lines.append("")

    register = registre.institution_register(state)
    if register:
        lines.append("## Le registre des voix")
        lines.append("")
        for r in register:
            tag = " (officiel)" if r["source_kind"] == "official" else ""
            lines.append(f"- {_md(r['institution_name'])}{tag} : {registre.state_label_fr(r)}.")
        lines.append("")
        lines.append("« Publié, hors dossier » n’est pas un silence : un dossier exige un sujet nommé et deux institutions. « Collecte en échec » est une lacune de Vigie.")
        correction = registre.correction_notice(state)
        if correction:
            lines.append("")
            lines.append(f"Correction du {correction['corrected_at']} : les sceaux {correction['affects_seal_min']} à {correction['affects_seal_max']}{(' (le premier sceau avec mesure de collecte est le ' + str(correction['first_seal_with_facts']) + ')') if correction.get('first_seal_with_facts') else ''} avaient été publiés avec une définition du silence qui mesurait les règles de rapprochement de Vigie. Ils restent inchangés — une chaîne ne se réécrit pas — et leur lecture est désormais « non établi ».")
        lines.append("")
        lines.append(f"Registre complet et chaîne des éditions : {SITE_URL}/registre.html")
        lines.append("")

    lines.append("## Attribution et règles")
    lines.append("")
    lines.append("- Chaque titre appartient à son éditeur ; citez l’éditeur et son lien, jamais Vigie comme auteur.")
    lines.append("- Vigie n’affirme jamais qu’une institution s’est tue. Une institution absente des dossiers a publié sans y entrer, ou bien notre collecte a échoué ; les deux sont distingués dans le registre.")
    lines.append(f"- Sources suivies : {SITE_URL}/methode/sources.html · Méthode : {SITE_URL}/methode/classement.html, {SITE_URL}/methode/registre.html · Mentions légales et retrait : {SITE_URL}/methode/legal.html")
    lines.append("")
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# llms.txt
# --------------------------------------------------------------------------- #
def llms_events_section(state: dict) -> str:
    """The events block of llms.txt, present only when delta-v2 is published."""
    dep = v1_deprecation(state)
    return f"""## Events

- [Événements]({SITE_URL}/evenements.html) · [Events]({SITE_URL}/en/evenements.html): one page per real-world event, with every collected article about it, whatever the outlet or language, the origin count, the official-record anchors and the reason it is shown where it is.
- Event pages: `{SITE_URL}/evenements/<event_id>.html` (French) and `{SITE_URL}/en/evenements/<event_id>.html` (English); the ids are listed in the files below.
- [Events JSON]({SITE_URL}/evenements/latest.json): the current-edition events, language-neutral (codes, with {{fr, en}} labels).
- [Delta v2]({SITE_URL}/delta/v2/latest.json): machine-readable edition delta over events (delta-v2) — codes, {{fr, en}} labels, counts (members, institutions, independent origins, languages), tier, activity, anchors (type + ref), lineage, and per current member the publisher, author, title and URL (titles only, no excerpts); cursor = chain root, the same as delta-v1.
- [Quality]({SITE_URL}/qualite.json): counts only — how many groupings were checked, by whom, and how often they were right.
- Delta v1 ({SITE_URL}/delta/latest.json) is deprecated since {dep["since"]} and stays unchanged until {dep["sunset"]}.

"""


def render_llms_txt(state: dict, status: dict, *, events: bool = False) -> str:
    seals = state.get("seals") or []
    last = seals[-1] if seals else {}
    edition = last.get("edition") or status.get("at") or ""
    events_block = llms_events_section(state) if events else ""
    return f"""# Vigie

> Vigie is a free, non-commercial lookout for Québec City (French-first). It aggregates a finite, published list of sources, never rewrites them, groups articles into dossiers where several institutions speak, relays the City's official roadworks feed, and keeps a sealed register (sha256 chain) of every edition: what each followed institution published, and what Vigie's own collection missed. Vigie never asserts that an institution was silent — a dossier needs a named subject and two institutions, so absence from dossiers is a property of Vigie's clustering, not of the institution. Judgment stays with the reader.

{LICENCE_NOTE_EN}

Current edition: {edition or "unknown"}. Rules for agents: cite the original publisher (name + URL, and the author when given) for every item; Vigie is an index, never the author. Do not present dossier questions as publisher quotations. Never infer that an institution was silent — use the per-institution state (spoke / published / no_items_collected / collection_gap / not_established; withdrawn_on_request and cut_by_vigie appear only when they apply). Treat "quiet" as "left this collection", never "resolved". Verify an edition against the chain before calling it a record.

## Edition

- [Le point (Markdown)]({SITE_URL}/index.html.md): the front door as plain Markdown — stories with publisher, author and URL (titles only, no excerpts), dossiers with their voices, official roadworks, the voice register.
- [Delta]({SITE_URL}/delta/latest.json): machine-readable edition delta (delta-v1.1, additive over delta-v1) — new / developed / quiet dossiers with items, per-institution state with collected item counts, roadworks diff, cursor = chain root.
- [Dossiers complets (HTML)]({SITE_URL}/dossiers.html): one record page per dossier of the current edition — every voice with every verbatim headline, the collection timeline, and the institutions absent from that dossier.

{events_block}## Registre

- [Checkpoint]({SITE_URL}/registre/checkpoint.txt): origin, chain size, current root, edition stamp.
- [Chain]({SITE_URL}/registre/chain.json): the sealed editions (records + sha256 leaves and roots); verify with `scripts/registre.py --verify`.
- [Institutions]({SITE_URL}/registre/institutions.json): per followed institution — its state this edition (spoke / published / no_items / collection_gap / not_established, or withdrawn / cut when they apply), collected item counts, and Vigie's own collection-gap streak.
- [Roadworks chain]({SITE_URL}/registre/travaux.json): one root per change of the City's active obstruction set.
- [Method]({SITE_URL}/methode/registre.html): what a seal contains, what it proves, what it does not.

## Method

- [All method pages]({SITE_URL}/methode/index.html): the human-readable method, one page per rule.
- [Ranking]({SITE_URL}/methode/classement.html): published ranking (geo + recency; no clicks, no ads).
- [Sources]({SITE_URL}/methode/sources.html): the finite list of followed feeds, institutions, cuts and licence notes.
- [Funding]({SITE_URL}/methode/financement.html): who pays, what is never for sale.
- [Vision]({SITE_URL}/methode/vision.html): what Vigie is and refuses to be.
- [Legal, attribution, opt-out]({SITE_URL}/methode/legal.html): publisher rights, same-day removal.
- [Anomalies]({SITE_URL}/methode/anomalies.html): fixed-threshold roadworks anomaly rules.
- [Edges]({SITE_URL}/methode/rues.html): literal street-name joins between roadworks and dossiers.
- Raw sources (machine-readable, same content): /VISION.md /ranking.md /sources.yaml /RENT.md /FRICTION.md /FACETS.md /DESIGN.md /edge.md /anomalies.md /legal.md /REGISTRE.md.

## Optional

- [Front door (HTML)]({SITE_URL}/): the resident brief.
- [Avant de partir]({SITE_URL}/partir.html): the departure screen — declared obstructions, followed corridors (on-device), what changed.
- [La mémoire]({SITE_URL}/memoire.html): every sealed edition, readable — dossiers, voices, published items and collection gaps, edition by edition.
- [Registre (HTML)]({SITE_URL}/registre.html): the human view of the register.
- [L'affiche]({SITE_URL}/affiche.html): the printable neighbourhood sheet.
- [Explorer]({SITE_URL}/explorer.html): evidence workbench (English).
- [Morning pulse]({SITE_URL}/morning.html): ambient digest of the same store.
"""


# --------------------------------------------------------------------------- #
# Emit
# --------------------------------------------------------------------------- #
_AUTO = object()


def emit(ranked: list[dict], issues: list[dict], ledger: dict | None, roadworks: dict | None,
         state: dict, generated_at: str, *, run: dict | None = None,
         out_llms: Path = OUT_LLMS, out_md: Path = OUT_MD, out_delta: Path = OUT_DELTA,
         out_delta_v2: Path = OUT_DELTA_V2, events_input: dict | None | object = _AUTO) -> None:
    """Write the machine files. `events_input` is {"view", "texts", "reg"} (tests),
    None (no event layer), or left alone to read the stored files."""
    now = brief.parse_date(generated_at) or datetime.now(timezone.utc)
    run = brief.latest_run() if run is None else run
    status = brief.collection_status(run, now)
    rows = story_rows(ranked, now)
    if events_input is _AUTO:
        try:
            events_input, why = load_events_input()
        except Exception as exc:  # noqa: BLE001 - fail-soft: delta-v2 omitted, everything else unchanged
            events_input, why = None, f"{type(exc).__name__}: {exc}"
    else:
        why = "no events input"
    v2 = None
    if isinstance(events_input, dict):
        try:
            v2 = build_delta_v2(events_input["view"], events_input["texts"], events_input["reg"], state, status)
        except Exception as exc:  # noqa: BLE001 - fail-soft: delta-v2 omitted, everything else unchanged
            why = f"build failed ({type(exc).__name__}: {exc})"
    for path in (out_llms, out_md, out_delta):
        path.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_text_atomic(out_llms, render_llms_txt(state, status, events=v2 is not None))
    store_io.write_text_atomic(out_md, render_markdown(rows, issues, ledger, roadworks, state, status))
    delta = build_delta(issues, ledger, roadworks, state, status)
    if v2 is not None:
        # delta-v1 is marked deprecated only when its successor is published.
        delta["deprecation"] = v1_deprecation(state)
    store_io.write_json_atomic(out_delta, delta)
    if v2 is not None:
        out_delta_v2.parent.mkdir(parents=True, exist_ok=True)
        store_io.write_json_atomic(out_delta_v2, v2)
        tail = f", delta/v2/latest.json ({v2['event_count']} events)"
    else:
        # A stale delta-v2 must never outlive its inputs: it may carry text a
        # takedown has since withdrawn.
        try:
            Path(out_delta_v2).unlink()
        except OSError:
            pass
        tail = ""
        print(f"substrate: delta-v2 omitted ({why})")
    print(f"substrate: llms.txt, index.html.md ({len(rows)} stories), delta/latest.json{tail} -> {out_delta.parent}")


def main() -> int:
    ranked_doc = registre.load_json(ROOT / "data" / "normalized" / "latest_ranked.json")
    issues_doc = registre.load_json(registre.ISSUES)
    state = registre.load_state()
    emit(
        ranked_doc.get("candidates") or [],
        issues_doc.get("issues") if isinstance(issues_doc.get("issues"), list) else [],
        issues_doc.get("change_ledger") if isinstance(issues_doc.get("change_ledger"), dict) else {},
        registre.load_json(registre.ROADWORKS),
        state,
        str(ranked_doc.get("ranked_at") or datetime.now(timezone.utc).isoformat()),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
