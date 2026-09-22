"""
Vigie v0 — rank candidates and write a static lookout page.
Uses ranking.md weights: geo + recency.
Display nest uses proposed enrich.geo when present.
Issues section from data/issues/latest_issues.json (proposed clusters only).
Display temperance: Province/Linked show top 30 by score; full set stays in ranked JSON.
"""
from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

import store_io
import resident_brief

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = ROOT / "data" / "normalized" / "latest_candidates.json"
ENRICHED = ROOT / "data" / "normalized" / "latest_enriched.json"
ISSUES = ROOT / "data" / "issues" / "latest_issues.json"
ROADWORKS = ROOT / "data" / "roadworks" / "latest_roadworks.json"
CIVIC = ROOT / "data" / "civic" / "latest_consultations.json"
EDGES = ROOT / "data" / "edges" / "latest_edges.json"
ANOMALIES = ROOT / "data" / "anomalies" / "latest_verdict.json"
OUT_JSON = ROOT / "data" / "normalized" / "latest_ranked.json"
OUT_HTML = ROOT / "public" / "index.html"

W_GEO = 0.60
W_RECENCY = 0.40
W_TENSION = 0.00
# Co-founder refuse 2026-09-16: keep 0 until ranking.md enable gates all pass.
W_IMPACT = 0.00
HALF_LIFE_HOURS = 36.0

GEO_SCORE = {
    "primary": 1.0,
    "quebec-city": 1.0,
    "province": 0.55,
    "quebec": 0.55,
    "linked": 0.25,
}


def fmt_stamp(iso: str | None) -> str:
    if not iso:
        return "unknown"
    # Normalize any parseable aware clock to "YYYY-MM-DD HH:MM ZONE";
    # unparseable or zoneless strings pass through untouched (never claim UTC).
    s = str(iso).strip()
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return s
    if dt.tzinfo is None:
        return s
    return f"{dt:%Y-%m-%d %H:%M} {dt.tzname() or 'UTC'}"


def load_clock(ranked_at: str) -> dict:
    """Stamps from files only — invent nothing."""
    enriched_at = None
    if ENRICHED.exists():
        try:
            enriched_at = json.loads(ENRICHED.read_text(encoding="utf-8")).get("enriched_at")
        except Exception:
            enriched_at = None
    ingest_at = None
    runs = sorted((ROOT / "data" / "raw").glob("_run_*.json"))
    if runs:
        try:
            ingest_at = json.loads(runs[-1].read_text(encoding="utf-8")).get("fetched_at")
        except Exception:
            ingest_at = None
    sources_path = "sources.yaml" if (ROOT / "sources.yaml").exists() else "sources.yaml (missing)"
    return {
        "ranked_at": ranked_at,
        "enriched_at": enriched_at,
        "ingest_at": ingest_at,
        "sources": sources_path,
    }


def clock_line(clock: dict) -> str:
    parts = [f"Ranked: {fmt_stamp(clock.get('ranked_at'))}"]
    if clock.get("enriched_at"):
        parts.append(f"Enriched: {fmt_stamp(clock['enriched_at'])}")
    if clock.get("ingest_at"):
        parts.append(f"Ingested: {fmt_stamp(clock['ingest_at'])}")
    parts.append(f"Sources: {clock.get('sources') or 'sources.yaml'}")
    return " · ".join(parts)



# Proposed topic labels that may hit a life ? display preference only (not truth).
IMPACT_TOPICS = frozenset({
    "education",
    "energy/hydro",
    "housing", "energy", "hydro", "law", "health", "trade", "security",
    "death", "economy", "crime", "transport", "infrastructure",
})


def primary_topic(c: dict) -> str:
    if not isinstance(c, dict):
        return "other"
    en = c.get("enrich")
    en = en if isinstance(en, dict) else {}
    topics = en.get("topics") or []
    if topics and isinstance(topics[0], dict):
        return str(topics[0].get("topic") or "other").lower()
    return "other"


def is_impact(c: dict) -> bool:
    if not isinstance(c, dict):
        return False
    t = primary_topic(c)
    if t in IMPACT_TOPICS:
        return True
    # enrich writes compound stems (energy/hydro); count either side
    if "/" in t and any(part in IMPACT_TOPICS for part in t.split("/")):
        return True
    # Falsifiable units also count as life-hit for display preference only
    for imp in (impact_block(c).get("impacts") or []):
        if isinstance(imp, dict) and imp.get("units"):
            return True
    return False


def impact_block(c: dict) -> dict:
    """The proposed enrich object, only when it really is an object."""
    en = c.get("enrich") if isinstance(c, dict) else None
    return en if isinstance(en, dict) else {}


def impact_units(c: dict) -> list[dict]:
    out: list[dict] = []
    if not isinstance(c, dict):
        return out
    for imp in (impact_block(c).get("impacts") or []):
        if not isinstance(imp, dict):
            continue
        for u in imp.get("units") or []:
            if isinstance(u, dict) and (u.get("raw") or u.get("value") is not None):
                out.append(u)
    return out[:3]


def impact_first(items: list[dict]) -> list[dict]:
    """Prefer impact topics by score, then soft topics by score. Display only."""
    impact = [c for c in items if is_impact(c)]
    soft = [c for c in items if not is_impact(c)]
    # items already score-sorted globally; preserve relative order within each bucket
    return impact + soft

def parse_when(c: dict) -> datetime | None:
    # Download time is not publication time. Undated stories stay undated.
    if not isinstance(c, dict):
        return None
    raw = c.get("published_at")
    if not isinstance(raw, str) or not raw.strip():
        return None
    for parser in (lambda s: datetime.fromisoformat(s.replace("Z", "+00:00")), parsedate_to_datetime):
        try:
            result = parser(raw.strip())
            if result.tzinfo is not None:
                return result.astimezone(timezone.utc)
        except (ValueError, TypeError, OverflowError):
            continue
    return None


def display_geo(c: dict) -> str:
    if not isinstance(c, dict):
        return "linked"
    g = impact_block(c).get("geo") or {}
    if isinstance(g, dict) and g.get("geo"):
        return str(g["geo"]).lower()
    # str() before .lower(): a foreign store with a numeric nest_role or an
    # object geo must degrade, never abort the edition with AttributeError.
    nest = str(c.get("nest_role") or "").lower()
    if nest == "primary":
        return "quebec-city"
    if nest == "province":
        return "quebec"
    if nest == "linked":
        return "linked"
    geo = c.get("geo")
    return geo.lower() if isinstance(geo, str) else "linked"


def geo_proximity(c: dict) -> float:
    g = display_geo(c)
    if g in GEO_SCORE:
        return GEO_SCORE[g]
    return 0.10


def recency_score(c: dict, now: datetime) -> float:
    when = parse_when(c)
    if when is None:
        return 0.0
    if (when - now).total_seconds() > 300:
        return 0.0
    age_h = max(0.0, (now - when).total_seconds() / 3600.0)
    return 0.5 ** (age_h / HALF_LIFE_HOURS)


def score_item(c: dict, now: datetime) -> float:
    # w_impact / w_tension reserved — must stay 0 until logged ACT in ranking.md
    return (
        W_GEO * geo_proximity(c)
        + W_RECENCY * recency_score(c, now)
        + W_TENSION * 0.0
        + W_IMPACT * 0.0
    )


def esc(s: str) -> str:
    # Same display-spoofing control strip as the brief: explorer/morning are
    # public surfaces too, and a hostile feed must not inject bidi overrides
    # or zero-width marks into a relayed title there either.
    text = resident_brief.sanitize(s)
    return (
        text
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&#x27;")
    )


def safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


TITLE_CAP = resident_brief.TITLE_CAP


def title_html(value: object, cap: int = TITLE_CAP) -> str:
    """Relayed title, escaped and capped at the published limit.

    The explorer is a public surface too: a 301-500-char feed title must not be
    longer here than on the brief. Truncate before escaping so an entity is
    never split.
    """
    text = resident_brief.sanitize(value if isinstance(value, str) else "")
    text = text or "(no title)"
    if len(text) > cap:
        text = text[: cap - 1].rstrip() + "…"
    return esc(text)


def link_url(value: object) -> str:
    from resident_brief import safe_url
    return safe_url(value) or "#"


def section_for(c: dict) -> str:
    g = display_geo(c)
    if g == "quebec-city":
        return "near"
    if g == "quebec":
        return "province"
    return "linked"



def is_booth_or_brief(c: dict) -> bool:
    """Ohdio rattrapage / en-bref — booth or brief, not a news scar. Display demote only."""
    if not isinstance(c, dict):
        return False
    url = str(c.get("url") or "").lower()
    return "/ohdio/" in url or "/en-bref/" in url


def item_topics(c: dict) -> list[str]:
    topics = impact_block(c).get("topics") or []
    return [str(t.get("topic")) for t in topics if isinstance(t, dict) and t.get("topic")]


def issue_candidate_ids(iss: dict) -> list[str]:
    """Sorted candidate ids: callers slice this list for display, so a set's
    per-process hash order would make byte-identical rebuilds impossible."""
    ids: set[str] = set()
    for t in iss.get("tensions") or []:
        if not isinstance(t, dict):
            continue
        for it in t.get("items") or []:
            if not isinstance(it, dict):
                continue
            cid = it.get("candidate_id")
            if cid:
                ids.add(str(cid))
    return sorted(ids)


def build_continuity(issues: list[dict], ranked: list[dict]) -> dict:
    """Same fight maps from scars already on disk — never a painted for-you feed."""
    by_id = {str(c.get("id")): c for c in ranked if isinstance(c, dict) and c.get("id")}
    id_to_issues: dict[str, list[dict]] = {}
    for iss in issues:
        if not isinstance(iss, dict):
            continue
        for cid in issue_candidate_ids(iss):
            id_to_issues.setdefault(cid, []).append(iss)
    return {"by_id": by_id, "id_to_issues": id_to_issues}


def same_fight_links(c: dict, continuity: dict, *, limit: int = 3) -> list[tuple[str, str]]:
    """Scar brothers only — share an issue_id / named scar on disk. Never bare topic."""
    if not isinstance(c, dict):
        return []
    cid = str(c.get("id") or "")
    by_id = continuity["by_id"]
    seen = {cid}
    out: list[tuple[str, str]] = []
    for iss in continuity["id_to_issues"].get(cid, []):
        for other in issue_candidate_ids(iss):
            if other in seen:
                continue
            oc = by_id.get(other)
            if not oc:
                continue
            seen.add(other)
            out.append((oc.get("title") or "(no title)", oc.get("url") or "#"))
            if len(out) >= limit:
                return out
    return out


def chip_topics(c: dict) -> str:
    labels = [t for t in item_topics(c) if t][:3]
    return "".join(f"<span class='chip'>{esc(lab)}</span>" for lab in labels)


def chip_units(c: dict) -> str:
    """Falsifiable impact units — raw span the citizen can check. Not a score."""
    bits = []
    for u in impact_units(c):
        raw = str(u.get("raw") or "").strip()
        if not raw:
            kind = u.get("kind") or "unit"
            val = u.get("value")
            unit = u.get("unit") or ""
            raw = f"{kind}:{val} {unit}".strip()
        bits.append(f"<span class='chip unit' title='proposed impact unit'>{esc(raw[:48])}</span>")
    return "".join(bits)


APPROACH_MAX = 5
NEST_LABEL = {"near": "Near me", "province": "Province", "linked": "Linked"}


def approach_nest(iss: dict) -> str:
    geos = iss.get("geo_focus") or []
    if "quebec-city" in geos:
        return "near"
    if "quebec" in geos:
        return "province"
    return "linked"


def approach_units_for_issue(iss: dict, by_id: dict) -> list[dict]:
    """Units from scar items already on disk — no new ranking."""
    found: list[dict] = []
    if not isinstance(iss, dict):
        return found
    seen: set[str] = set()
    for cid in issue_candidate_ids(iss):
        c = by_id.get(str(cid))
        if not c:
            continue
        for u in impact_units(c):
            key = f"{u.get('kind')}|{u.get('value')}|{u.get('unit')}"
            if key in seen:
                continue
            seen.add(key)
            found.append(u)
            if len(found) >= 2:
                return found
    return found


def quiet_institution_names(iss: dict, *, limit: int = 2) -> list[str]:
    """Official institutions silent on this scar — glance names, not a bias meter."""
    silence = iss.get("silence") or {} if isinstance(iss, dict) else {}
    if not isinstance(silence, dict):
        silence = {}
    out: list[str] = []
    for s in silence.get("silent") or []:
        if not isinstance(s, dict):
            continue
        if (s.get("source_kind") or "").lower() != "official":
            continue
        name = (
            s.get("institution_name")
            or s.get("source_name")
            or s.get("institution_id")
            or s.get("source_id")
            or ""
        )
        name = str(name).strip()
        if not name:
            continue
        out.append(name)
        if len(out) >= limit:
            break
    return out


def approach_fingerprint(ap: dict) -> str:
    """Stable store fingerprint for Since-you-left — not a personalization score."""
    if not isinstance(ap, dict):
        return ""
    units = ap.get("units")
    if isinstance(units, list):
        n_units = len(units)
    else:
        n_units = safe_int(ap.get("unit_count"))
    silent = ap.get("silent")
    silent_n = 0 if silent is None else safe_int(silent)
    return (
        f"{safe_int(ap.get('voices'))}|"
        f"{silent_n}|"
        f"{1 if ap.get('remix') else 0}|"
        f"{n_units}" + (f"|{ap['content_fp']}" if ap.get("content_fp") else "")
    )


def approach_fp_of(ap: dict) -> str:
    """Prefer embedded pulse fp; else recompute."""
    if not ap:
        return ""
    raw = ap.get("fp")
    if raw is not None and str(raw) != "":
        return str(raw)
    return approach_fingerprint(ap)


def format_store_clock(iso: str) -> str:
    """Compact UTC store clock for the Since-you-left strip.

    Converts an aware offset to UTC before labelling it (the JS twin does the
    same); a zoneless stamp is never relabelled UTC.
    """
    s = str(iso or "").strip()
    if not s:
        return "unknown"
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except (ValueError, OverflowError):
        return s[:19]
    if dt.tzinfo is None:
        return s[:16].replace("T", " ")
    dt = dt.astimezone(timezone.utc)
    return f"{dt:%Y-%m-%d %H:%M} UTC"


def since_left_delta(prev: list[dict], curr: list[dict]) -> dict:
    """Compare two pulse approach lists by issue_id. Never reorders."""
    prev_map = {
        str(a.get("issue_id") or ""): a
        for a in (prev or []) if isinstance(a, dict) and a.get("issue_id")
    }
    new_ids: list[str] = []
    changed_ids: list[str] = []
    cur_ids: set[str] = set()
    for a in curr or []:
        if not isinstance(a, dict):
            continue
        iid = str(a.get("issue_id") or "")
        if not iid:
            continue
        cur_ids.add(iid)
        if iid not in prev_map:
            new_ids.append(iid)
        elif approach_fp_of(prev_map[iid]) != approach_fp_of(a):
            changed_ids.append(iid)
    gone_ids = [iid for iid in prev_map if iid not in cur_ids]
    return {
        "new": new_ids,
        "changed": changed_ids,
        "gone": gone_ids,
        "same": not new_ids and not changed_ids and not gone_ids,
    }


def since_left_visit(prev: dict | None, pulse: dict) -> dict:
    """Pure Arrival contract: strip copy + in-place badges from store pulse.

    Returning visit uses store ``clustered_at`` — same timestamp ⇒ same Approaches
    (no fingerprint theater). Delta badges never reorder.
    """
    pulse = pulse or {}
    curr = list(pulse.get("approaches") or [])
    clustered_at = str(pulse.get("clustered_at") or "")
    clock = format_store_clock(clustered_at)
    if not prev or not (prev.get("approaches") or []):
        return {
            "kind": "first",
            "message": f"First look — store pulse {clock}.",
            "badges": {},
            "delta": {"new": [], "changed": [], "gone": [], "same": True},
            "clustered_at": clustered_at,
        }
    prev_at = str(prev.get("clustered_at") or "")
    if prev_at and clustered_at and prev_at == clustered_at and since_left_delta(list(prev.get("approaches") or []), curr)["same"]:
        return {
            "kind": "same",
            "message": f"Since you left — same Approaches on this pulse ({clock}).",
            "badges": {},
            "delta": {"new": [], "changed": [], "gone": [], "same": True},
            "clustered_at": clustered_at,
        }
    delta = since_left_delta(list(prev.get("approaches") or []), curr)
    badges: dict[str, str] = {}
    for iid in delta["new"]:
        badges[iid] = "new"
    for iid in delta["changed"]:
        badges[iid] = "changed"
    if delta["same"]:
        msg = (
            f"Since you left — same Approaches "
            f"(store moved {clock}; was {format_store_clock(prev_at)})."
        )
        kind = "same"
    else:
        bits: list[str] = []
        if delta["new"]:
            bits.append(f"{len(delta['new'])} new")
        if delta["changed"]:
            bits.append(f"{len(delta['changed'])} changed")
        if delta["gone"]:
            bits.append(f"{len(delta['gone'])} left Approaches")
        msg = (
            f"Since you left — {' · '.join(bits)} "
            f"(store {clock}; was {format_store_clock(prev_at)} · order unchanged)."
        )
        kind = "delta"
    return {
        "kind": kind,
        "message": msg,
        "badges": badges,
        "delta": delta,
        "clustered_at": clustered_at,
    }


def build_approaches(issues: list[dict], continuity: dict) -> list[dict]:
    """Approaches = fights in store order. Presentation only. Max APPROACH_MAX.

    Phase 2 object: geo nest + voice count + silence + units — glanceable without Stage.
    A malformed store entry is skipped, never rendered and never fatal.
    """
    by_id = (continuity or {}).get("by_id") or {}
    out: list[dict] = []
    for i, iss in enumerate([x for x in (issues or []) if isinstance(x, dict)][:APPROACH_MAX]):
        silence = iss.get("silence") or {}
        if not isinstance(silence, dict):
            silence = {}
        silent_rows = [s for s in (silence.get("silent") or []) if isinstance(s, dict)]
        official_silent = sum(
            1 for s in silent_rows if (s.get("source_kind") or "").lower() == "official"
        )
        topic_obj = iss.get("topic") or {}
        if isinstance(topic_obj, dict):
            topic = str(topic_obj.get("topic") or "other")
        else:
            topic = str(topic_obj or "other")
        silent_count = silence.get("silent_count")
        if silent_count is None:
            silent_count = len(silent_rows)
        tensions = [t for t in (iss.get("tensions") or []) if isinstance(t, dict)]
        out.append(
            {
                "index": i,
                "issue_id": str(iss.get("issue_id") or iss.get("scar") or f"idx-{i}"),
                "scar": iss.get("scar") or "",
                "question": iss.get("question") or "Issue",
                "topic": topic,
                "nest": approach_nest(iss),
                "voices": safe_int(iss.get("source_count")),
                "silent": safe_int(silent_count),
                "official_silent": official_silent,
                "quiet_names": quiet_institution_names(iss),
                "remix": bool(iss.get("media_remix")),
                "units": approach_units_for_issue(iss, by_id),
                "content_fp": hashlib.sha256(json.dumps({
                    "question": iss.get("question"),
                    "evidence": iss.get("evidence"),
                    "items": sorted([
                        {k: it.get(k) for k in ("candidate_id", "title", "url", "published_at", "claims")}
                        for t in tensions for it in (t.get("items") or []) if isinstance(it, dict)
                    ], key=lambda it: str(it.get("candidate_id") or it.get("url") or "")),
                }, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16],
            }
        )
    return out


def pulse_payload(approaches: list[dict], clustered_at: str) -> dict:
    """Embeddable pulse for Since-you-left — store facts only."""
    return {
        "clustered_at": clustered_at,
        "approaches": [
            {
                "issue_id": a.get("issue_id"),
                "scar": a.get("scar"),
                "voices": a.get("voices"),
                "silent": a.get("silent"),
                "remix": bool(a.get("remix")),
                "unit_count": len(a.get("units") or []),
                "fp": approach_fingerprint(a),
            }
            for a in approaches
        ],
    }


def approach_meta_chips_html(ap: dict) -> str:
    """Glance chips for Approach — geo · voices · silence · units. No Stage required."""
    ap = ap if isinstance(ap, dict) else {}
    nest_key = ap.get("nest") or "linked"
    nest_label = esc(NEST_LABEL.get(str(nest_key), str(nest_key)))
    voices = safe_int(ap.get("voices"))
    silent = safe_int(ap.get("silent"))
    chips = [
        f"<span class='chip geo' title='Nest'>{nest_label}</span>",
        f"<span class='chip voices' title='Institutions that spoke'>{voices} voices</span>",
        f"<span class='chip silence' title='Enabled institutions absent this run'>"
        f"{silent} silent</span>",
    ]
    off_s = safe_int(ap.get("official_silent"))
    if off_s:
        chips.append(
            f"<span class='chip official' title='Official institutions quiet'>"
            f"{off_s} official quiet</span>"
        )
    if ap.get("remix"):
        chips.append("<span class='chip remix'>media remix</span>")
    units = [u for u in (ap.get("units") or []) if isinstance(u, dict)]
    unit_bits = []
    for u in units:
        raw = str(u.get("raw") or "").strip()
        if raw:
            unit_bits.append(f"<span class='chip unit' title='Proposed impact unit'>{esc(raw[:40])}</span>")
    if unit_bits:
        chips.extend(unit_bits)
    else:
        chips.append(
            "<span class='chip unit empty' title='No falsifiable unit on this scar yet'>"
            "no units yet</span>"
        )
    return "".join(chips)


def approach_silence_preview_html(ap: dict) -> str:
    """Official quiet names on Approach — counts alone are not enough to glance silence."""
    if not isinstance(ap, dict):
        return ""
    names = [str(n).strip() for n in (ap.get("quiet_names") or []) if str(n).strip()]
    if not names:
        return ""
    shown = " · ".join(esc(n[:42]) for n in names[:2])
    return (
        f"<span class='approach-silence-preview'>"
        f"<span class='approach-silence-label'>Quiet</span> {shown}"
        f"</span>"
    )


def approach_button_html(ap: dict) -> str:
    """Interaction container for one Approach — Phase 2 glance object."""
    ap = ap if isinstance(ap, dict) else {}
    q = esc((ap.get("question") or "Issue")[:110])
    nest_key = ap.get("nest") or "linked"
    issue_id = esc(str(ap.get("issue_id") or ""))
    fp = esc(approach_fingerprint(ap))
    remix_cls = " approach-remix" if ap.get("remix") else ""
    meta = approach_meta_chips_html(ap) + approach_silence_preview_html(ap)
    return (
        f"<button type='button' class='approach{remix_cls}' "
        f"data-i='{safe_int(ap.get('index'))}' data-mode='fight' "
        f"data-issue-id='{issue_id}' data-fp='{fp}' "
        f"data-topic='{esc(str(ap.get('topic') or 'other'))}' "
        f"data-nest='{esc(str(nest_key))}' "
        f"data-voices='{safe_int(ap.get('voices'))}' "
        f"data-silent='{safe_int(ap.get('silent'))}' "
        f"data-units='{len([u for u in (ap.get('units') or []) if isinstance(u, dict)])}' "
        f"data-unit-kinds='{esc(','.join(
            str(u.get('kind') or '').strip()
            for u in (ap.get('units') or [])
            if isinstance(u, dict) and str(u.get('kind') or '').strip()
        ))}' "
        f"data-store-index='{safe_int(ap.get('index'))}'>"
        f"<span class='approach-top'>"
        f"<span class='approach-q'>{q}</span>"
        f"<span class='approach-badge' hidden></span>"
        f"</span>"
        f"<span class='approach-meta'>{meta}</span>"
        f"</button>"
    )



def load_faces() -> dict[str, dict]:
    """Faces are disabled.

    The store is written by fetch_media.py, which no pipeline or refresh step
    runs, so the page rendered faces frozen days behind the edition (mostly
    stale ids), and every image was a remote hotlink to the city site — which
    the attribution posture (R2: publisher images only, re-hosted locally)
    rejects. The seam stays named so a future same-origin /media store can be
    reintroduced deliberately.
    """
    return {}


def face_img(cid: str | None, faces: dict[str, dict] | None, *, css: str = "face") -> str:
    """Equal skeleton: same-origin publisher image or empty strip. Never stock,
    never a remote hotlink."""
    empty = f"<div class='{css} face-empty' aria-hidden='true'></div>"
    if not cid or not faces:
        return empty
    rec = faces.get(str(cid)) or {}
    url = rec.get("image_url")
    if not isinstance(url, str) or not url.startswith("/media/"):
        return empty
    return (
        f"<img class='{css}' src=\"{esc(url)}\" alt=\"\" loading=\"lazy\" "
        f"referrerpolicy=\"no-referrer\" />"
    )


def card_html(c: dict, continuity: dict | None = None) -> str:
    c = c if isinstance(c, dict) else {}
    title = title_html(c.get("title"))
    url = esc(link_url(c.get("url")))
    source = esc(c.get("source_name") or c.get("source_id") or "")
    try:
        score = float(c.get("rank_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    geo = esc(display_geo(c))
    chips = chip_topics(c) + chip_units(c)
    cont = ""
    if continuity is not None:
        links = same_fight_links(c, continuity)
        if links:
            bits = [
                f"<a class='same' href=\"{esc(link_url(u))}\" target=\"_blank\" rel=\"noopener\">{esc(t[:72])}</a>"
                for t, u in links
            ]
            cont = (
                "<div class='same-fight'>"
                "<span class='same-label'>Same fight / Même combat</span> "
                + " · ".join(bits)
                + "</div>"
            )
    return (
        "<article class='card'>"
        f"<a class='card-title' href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a>"
        f"<div class='card-meta'><span class='chip source'>{source}</span>"
        f"<span class='chip geo'>{geo}</span>{chips}"
        f"<span class='score'>score {score:.3f}</span></div>"
        f"{cont}"
        "</article>"
    )


def spoke_institutions_for_stage(iss: dict) -> list[dict]:
    """Institution seats that spoke on this scar — store tensions, not a feed desk list."""
    out: list[dict] = []
    seen: set[str] = set()
    for t in iss.get("tensions") or []:
        if not isinstance(t, dict):
            continue
        iid = str(t.get("institution_id") or "").strip()
        name = str(
            t.get("institution_name")
            or str(t.get("label") or "").replace("voice:", "")
            or iid
            or "voice"
        ).strip()
        key = iid or name.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(
            {
                "institution_id": iid,
                "institution_name": name,
                "source_kind": (t.get("source_kind") or "media").lower(),
            }
        )
    return out


def fight_theater_arc_html(iss: dict) -> str:
    """Spoke + silence arc — answer who spoke / who didn't without scrolling archaeology."""
    iss = iss if isinstance(iss, dict) else {}
    silence = iss.get("silence") or {}
    if not isinstance(silence, dict):
        silence = {}
    silent_rows = [s for s in (silence.get("silent") or []) if isinstance(s, dict)]
    silent_n = safe_int(silence.get("silent_count"), len(silent_rows))
    spoke = spoke_institutions_for_stage(iss)
    spoke_n = len(spoke) or safe_int(silence.get("spoke_count"), safe_int(iss.get("source_count")))

    spoke_bits: list[str] = []
    for s in spoke:
        kind = s.get("source_kind") or "media"
        kind_chip = (
            "<span class='chip official'>official</span>"
            if kind == "official"
            else "<span class='chip media'>media</span>"
        )
        spoke_bits.append(
            f"<li class='arc-item spoke'>"
            f"<span class='arc-name'>{esc(s['institution_name'][:48])}</span> {kind_chip}"
            f"</li>"
        )
    if not spoke_bits:
        spoke_bits.append("<li class='arc-item empty'>No institution voice yet.</li>")

    quiet_bits: list[str] = []
    for s in silent_rows:
        kind = (s.get("source_kind") or "media").lower()
        kind_chip = (
            "<span class='chip official'>official</span>"
            if kind == "official"
            else "<span class='chip media'>media</span>"
        )
        name = str(
            s.get("institution_name")
            or s.get("source_name")
            or s.get("institution_id")
            or s.get("source_id")
            or ""
        ).strip()
        if not name:
            continue
        quiet_bits.append(
            f"<li class='arc-item quiet'>"
            f"<span class='arc-name'>{esc(name[:48])}</span> {kind_chip}"
            f"</li>"
        )
    if not quiet_bits:
        quiet_bits.append(
            "<li class='arc-item empty'>No enabled institution absent this run.</li>"
        )

    return (
        "<section class='fight-arc' aria-label='Who spoke and who did not'>"
        "<div class='arc-lane spoke-lane'>"
        f"<h3 class='arc-title'>Spoke <span class='n'>({spoke_n})</span></h3>"
        f"<ul class='arc-list'>{''.join(spoke_bits)}</ul>"
        "</div>"
        "<div class='arc-lane quiet-lane'>"
        f"<h3 class='arc-title'>Did not speak <span class='n'>({silent_n})</span></h3>"
        "<p class='arc-note'>Enabled institutions absent this run "
        "(sister feeds share one seat). Presence and absence only — not a bias meter.</p>"
        f"<ul class='arc-list silent-list'>{''.join(quiet_bits)}</ul>"
        "</div>"
        "</section>"
    )


def issue_stage_html(iss: dict, continuity: dict | None = None, *, panel_id: str = "", faces: dict | None = None) -> str:
    """Fight theater: calm confrontation — institution voices + silence arc first."""
    iss = iss if isinstance(iss, dict) else {}
    q = esc(iss.get("question") or "Issue")
    topic_obj = iss.get("topic") or {}
    topic = topic_obj.get("topic") if isinstance(topic_obj, dict) else topic_obj
    topic = topic or "other"
    remix = bool(iss.get("media_remix"))
    silence = iss.get("silence") or {}
    if not isinstance(silence, dict):
        silence = {}
    silent_rows = [s for s in (silence.get("silent") or []) if isinstance(s, dict)]
    silent_n = safe_int(silence.get("silent_count"), len(silent_rows))
    spoke_n = len(spoke_institutions_for_stage(iss)) or safe_int(
        silence.get("spoke_count"), safe_int(iss.get("source_count"))
    )
    remix_note = ""
    if remix:
        remix_note = (
            "<p class='rule remix-warn'>"
            "Media remix — no official document voice on this scar yet. "
            "Judgment yours; do not confuse coverage with the record."
            "</p>"
        )
    else:
        remix_note = (
            "<p class='rule'>"
            "Official text beside journalism — no crowned answer."
            "</p>"
        )
    arc = fight_theater_arc_html(iss)
    panels = []
    for t in (iss.get("tensions") or [])[:4]:
        if not isinstance(t, dict):
            continue
        label = esc(
            str(
                t.get("institution_name")
                or str(t.get("label") or "voice").replace("voice:", "")
            )
        )
        kind = (t.get("source_kind") or "media").lower()
        kind_chip = (
            "<span class='chip official'>official</span>"
            if kind == "official"
            else "<span class='chip media'>media</span>"
        )
        items_html = []
        for it in (t.get("items") or [])[:3]:
            if not isinstance(it, dict):
                continue
            vface = face_img(it.get("candidate_id"), faces)
            claims_html = ""
            claim_bits = []
            for cl in (it.get("claims") or [])[:2]:
                if not isinstance(cl, dict):
                    continue
                cq = esc((cl.get("quote") or "")[:160])
                if not cq:
                    continue
                sp = esc(cl.get("speaker") or "")
                who = f"<span class='claim-speaker'>{sp}</span> — " if sp else ""
                claim_bits.append(
                    f"<li class='claim'>{who}<span class='claim-q'>« {cq} »</span></li>"
                )
            if claim_bits:
                claims_html = "<ul class='claims'>" + "".join(claim_bits) + "</ul>"
            items_html.append(
                f"<a class='voice-link' href=\"{esc(link_url(it.get('url')))}\" "
                f"target=\"_blank\" rel=\"noopener\">{esc(it.get('title') or '(no title)')}</a>"
                f"{claims_html}"
                f"{vface}"
            )
        panels.append(
            "<div class='voice-panel'>"
            f"<div class='voice-name'>{label} {kind_chip}</div>"
            + "".join(items_html)
            + "</div>"
        )
    by_id = (continuity or {}).get("by_id") or {}
    related = []
    for cid in list(issue_candidate_ids(iss))[:4]:
        oc = by_id.get(cid)
        if oc:
            related.append(
                f"<a class='same' href=\"{esc(link_url(oc.get('url')))}\" target=\"_blank\" rel=\"noopener\">"
                f"{esc((oc.get('title') or '')[:72])}</a>"
            )
    same = ""
    if related:
        same = (
            "<div class='same-fight'>"
            "<span class='same-label'>Same fight / Même combat</span> "
            + " · ".join(related)
            + "</div>"
        )
    stage_cls = "stage face-pair confrontation" if len(panels) >= 2 else "stage confrontation"
    pid = f" id='{esc(panel_id)}'" if panel_id else ""
    return (
        f"<article class='issue-stage fight-theater'{pid} data-kind='issue'>"
        "<div class='kind-chip'>Fight</div>"
        f"<h2 class='stage-title'>{q}</h2>"
        f"<div class='card-meta fight-glance'>"
        f"<span class='chip'>{esc(topic)}</span>"
        f"<span class='chip voices'>{spoke_n} spoke</span>"
        f"<span class='chip silence'>{silent_n} silent</span>"
        f"</div>"
        f"{arc}"
        f"{remix_note}"
        f"<h3 class='confrontation-title'>Voices face each other</h3>"
        f"<div class='{stage_cls}'>"
        f"{''.join(panels) if panels else '<p class=\"empty\">No voices yet.</p>'}"
        f"</div>"
        f"{same}"
        "</article>"
    )


def near_rail_item(c: dict, continuity: dict | None = None, faces: dict | None = None, *, pin: bool = True) -> str:
    """Compact Near me approach for the persistent right rail."""
    c = c if isinstance(c, dict) else {}
    title = title_html(c.get("title"))
    url = esc(link_url(c.get("url")))
    source = esc(c.get("source_name") or c.get("source_id") or "")
    geo = esc(display_geo(c))
    chips = chip_topics(c) + chip_units(c)
    kind = (c.get("source_kind") or "media").lower()
    kind_chip = (
        "<span class='chip official'>official</span>"
        if kind == "official"
        else ""
    )
    cont = ""
    if continuity is not None:
        links = same_fight_links(c, continuity, limit=2)
        if links:
            bits = [
                f"<a class='same' href=\"{esc(link_url(u))}\" target=\"_blank\" rel=\"noopener\">{esc(t[:48])}</a>"
                for t, u in links
            ]
            cont = (
                "<div class='same-fight'>"
                "<span class='same-label'>Same fight</span> "
                + " · ".join(bits)
                + "</div>"
            )
    face = face_img(c.get("id"), faces)
    cid = str(c.get("id") or "").strip()
    pin_attr = f' id="pin-{esc(cid)}"' if (pin and cid) else ""
    return (
        f"<article class='near-item'{pin_attr}>"
        f"{face}"
        f"<a class='near-title' href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a>"
        f"<div class='card-meta'><span class='chip source'>{source}</span>"
        f"{kind_chip}"
        f"<span class='chip geo'>{geo}</span>{chips}</div>"
        f"{cont}"
        "</article>"
    )


def news_deck_html(c: dict, continuity: dict | None = None) -> str:
    """Mobile deck card for Near me."""
    c = c if isinstance(c, dict) else {}
    title = title_html(c.get("title"))
    url = esc(link_url(c.get("url")))
    source = esc(c.get("source_name") or c.get("source_id") or "")
    try:
        score = float(c.get("rank_score") or 0)
    except (TypeError, ValueError):
        score = 0.0
    geo = esc(display_geo(c))
    chips = chip_topics(c) + chip_units(c)
    cont = ""
    if continuity is not None:
        links = same_fight_links(c, continuity)
        if links:
            bits = [
                f"<a class='same' href=\"{esc(link_url(u))}\" target=\"_blank\" rel=\"noopener\">{esc(t[:72])}</a>"
                for t, u in links
            ]
            cont = (
                "<div class='same-fight'>"
                "<span class='same-label'>Same fight / Même combat</span> "
                + " · ".join(bits)
                + "</div>"
            )
    return (
        "<article class='deck-card news-card' data-kind='near'>"
        "<div class='kind-chip'>Near me</div>"
        f"<a class='deck-title' href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a>"
        f"<div class='card-meta'><span class='chip source'>{source}</span>"
        f"<span class='chip geo'>{geo}</span>{chips}"
        f"<span class='score'>score {score:.3f}</span></div>"
        f"<p class='lede-act'><a class='read' href=\"{url}\" target=\"_blank\" rel=\"noopener\">Read the approach</a></p>"
        f"{cont}"
        "</article>"
    )


def cap_slice(
    items: list[dict],
    *,
    cap: int | None,
    impact_prefer: bool,
    pin_items: list[dict] | None,
    pin_at: str = "start",
) -> tuple[list[dict], int]:
    """pin_at: start = crown pins; end = keep findable without leading (UI v2.8)."""
    items = [c for c in (items or []) if isinstance(c, dict)]
    pins = [c for c in (pin_items or []) if isinstance(c, dict)]
    total = len(items)
    ordered = impact_first(items) if impact_prefer else list(items)
    if cap is None:
        return ordered, total
    pin_urls = {(c.get("url") or "") for c in pins}
    rest = [c for c in ordered if (c.get("url") or "") not in pin_urls]
    item_urls = {(c.get("url") or "") for c in items}
    pins = [c for c in pins if (c.get("url") or "") in item_urls]
    room = max(0, cap - len(pins))
    if pin_at == "end":
        shown = rest[:room] + pins
    else:
        shown = pins + rest[:room]
    seen = set()
    dedup = []
    for c in shown:
        u = c.get("url") or id(c)
        if u in seen:
            continue
        seen.add(u)
        dedup.append(c)
    return dedup[:cap], total


def archive_block(
    title: str,
    sid: str,
    items: list[dict],
    continuity: dict,
    *,
    cap: int | None = None,
    impact_prefer: bool = False,
    extra_note: str | None = None,
    pin_items: list[dict] | None = None,
) -> str:
    if not items:
        return (
            f"<section id='{esc(sid)}' class='nest'>"
            f"<h3>{esc(title)}</h3>"
            "<p class='empty'>Nothing in this nest yet.</p></section>"
        )
    shown, total = cap_slice(items, cap=cap, impact_prefer=impact_prefer, pin_items=pin_items)
    cards = "".join(card_html(c, continuity) for c in shown)
    cap_note = ""
    if cap is not None:
        tag = " (impact-first)" if impact_prefer else ""
        cap_note = f"<p class='cap'>Showing {len(shown)} of {total}{tag} - full set in latest_ranked.json</p>"
    if extra_note:
        cap_note = (cap_note or "") + f"<p class='cap'>{esc(extra_note)}</p>"
    return (
        f"<section id='{esc(sid)}' class='nest'>"
        f"<h3>{esc(title)} <span class='n'>({total})</span></h3>"
        f"{cap_note}"
        f"<div class='cards'>{cards}</div></section>"
    )


def render_html(ranked: list[dict], generated_at: str, issues: list[dict] | None = None, clock: dict | None = None) -> str:
    clock = clock or {"ranked_at": generated_at, "sources": "sources.yaml"}
    ranked = [c for c in (ranked or []) if isinstance(c, dict)]
    issues = [i for i in (issues or []) if isinstance(i, dict)]
    continuity = build_continuity(issues, ranked)
    faces_map = load_faces()
    buckets = {"near": [], "province": [], "linked": []}
    demoted_booth_items: list[dict] = []
    for c in ranked:
        nest = section_for(c)
        if nest == "near" and is_booth_or_brief(c):
            nest = "province"
            demoted_booth_items.append(c)
        buckets[nest].append(c)
    demoted_booth = len(demoted_booth_items)
    DISPLAY_CAP = 30
    issue_list = issues[:12]

    # Impact Radar: Province + Linked impact-first (same cap logic as archive)
    # Province crown = life-hit only. Every /ohdio/ and /en-bref/ → Moved strip (UI v2.8.1).
    prov_ordered = impact_first(buckets["province"])
    prov_life = [c for c in prov_ordered if not is_booth_or_brief(c)]
    prov_booths = [c for c in prov_ordered if is_booth_or_brief(c)]
    demoted_urls = {(c.get("url") or "") for c in demoted_booth_items}
    # Ensure Near me demotions stay findable even if not in province ordered set
    seen_booth = {(c.get("url") or "") for c in demoted_booth_items}
    booth_rest = [c for c in prov_booths if (c.get("url") or "") not in seen_booth]
    prov_impact = prov_life[:DISPLAY_CAP]
    prov_moved: list[dict] = []
    seen_m: set[str] = set()
    for c in list(demoted_booth_items) + booth_rest:
        u = c.get("url") or ""
        if u in seen_m:
            continue
        seen_m.add(u)
        prov_moved.append(c)
    prov_moved = prov_moved[:15]
    prov_total = len(buckets["province"])

    linked_ordered = impact_first(buckets["linked"])
    linked_life = [c for c in linked_ordered if not is_booth_or_brief(c)]
    linked_booths = [c for c in linked_ordered if is_booth_or_brief(c)]
    linked_impact = linked_life[:DISPLAY_CAP]
    linked_moved = linked_booths[:10]
    linked_total = len(buckets["linked"])

    radar_prov = "".join(near_rail_item(c, continuity, faces_map) for c in prov_impact) or "<p class='empty'>Nothing in Province yet.</p>"
    radar_moved = ""
    if prov_moved:
        n_near = sum(1 for c in prov_moved if (c.get("url") or "") in demoted_urls)
        radar_moved = (
            f"<p class='cap' style='margin-top:1rem'><strong>{len(prov_moved)} booth/brief</strong> "
            f"(Ohdio / en-bref — findable, not crowned"
            + (f"; {n_near} from Near me" if n_near else "")
            + ")</p>"
            f"<div class='radar-grid radar-moved'>{''.join(near_rail_item(c, continuity, faces_map) for c in prov_moved)}</div>"
        )
    radar_linked = "".join(near_rail_item(c, continuity, faces_map) for c in linked_impact) or "<p class='empty'>Nothing Linked yet.</p>"
    linked_moved_html = ""
    if linked_moved:
        linked_moved_html = (
            f"<p class='cap' style='margin-top:.75rem'><strong>{len(linked_moved)} booth/brief in Linked</strong> "
            "(not crowned)</p>"
            f"<div class='radar-grid radar-moved'>{''.join(near_rail_item(c, continuity, faces_map) for c in linked_moved)}</div>"
        )
    radar_prov_m = "".join(near_rail_item(c, continuity, faces_map, pin=False) for c in prov_impact) or "<p class='empty'>Nothing in Province yet.</p>"
    radar_moved_m = ""
    if prov_moved:
        n_near = sum(1 for c in prov_moved if (c.get("url") or "") in demoted_urls)
        radar_moved_m = (
            f"<p class='cap' style='margin-top:1rem'><strong>{len(prov_moved)} booth/brief</strong> "
            f"(Ohdio / en-bref — findable, not crowned"
            + (f"; {n_near} from Near me" if n_near else "")
            + ")</p>"
            f"<div class='radar-grid'>{''.join(near_rail_item(c, continuity, faces_map, pin=False) for c in prov_moved)}</div>"
        )
    radar_linked_m = "".join(near_rail_item(c, continuity, faces_map, pin=False) for c in linked_impact) or "<p class='empty'>Nothing Linked yet.</p>"
    linked_moved_html_m = ""
    if linked_moved:
        linked_moved_html_m = (
            f"<p class='cap' style='margin-top:.75rem'><strong>{len(linked_moved)} booth/brief in Linked</strong> "
            "(not crowned)</p>"
            f"<div class='radar-grid'>{''.join(near_rail_item(c, continuity, faces_map, pin=False) for c in linked_moved)}</div>"
        )
    radar_html = (
        "<div class='impact-radar' data-panel='radar'>"
        "<div class='kind-chip'>Impact Radar</div>"
        "<h2 class='stage-title'>Province &amp; Linked — impact first</h2>"
        "<p class='rule'>Life-hit approaches already on disk. Not a painted feed. Pick a fight on the left to open the Stage.</p>"
        f"<p class='cap'>Province showing {len(prov_impact)} of {prov_total} (life-hit)"
        + " · life-hit only; booth/brief in Moved strip"
        + "</p>"
        f"<div class='radar-grid'>{radar_prov}</div>"
        f"{radar_moved}"
        f"<p class='cap' style='margin-top:1rem'>Linked showing {len(linked_impact)} of {linked_total} (impact-first; booth/brief below)</p>"
        f"<div class='radar-grid'>{radar_linked}</div>"
        f"{linked_moved_html}"
        "</div>"
    )

    radar_html_mobile = (
        "<div class='impact-radar' data-panel='radar'>"
        "<div class='kind-chip'>Impact Radar</div>"
        "<h2 class='stage-title'>Province &amp; Linked — impact first</h2>"
        "<p class='rule'>Life-hit approaches already on disk. Not a painted feed. Pick a fight on the left to open the Stage.</p>"
        f"<p class='cap'>Province showing {len(prov_impact)} of {prov_total} (life-hit)"
        + " · life-hit only; booth/brief in Moved strip"
        + "</p>"
        f"<div class='radar-grid'>{radar_prov_m}</div>"
        f"{radar_moved_m}"
        f"<p class='cap' style='margin-top:1rem'>Linked showing {len(linked_impact)} of {linked_total} (impact-first; booth/brief below)</p>"
        f"<div class='radar-grid'>{radar_linked_m}</div>"
        f"{linked_moved_html_m}"
        "</div>"
    )

    # Left compass: mode Radar above; Fights list = questions only (UI v2.7)
    compass_bits = []
    for i, iss in enumerate(issue_list):
        q = esc((iss.get("question") or "Issue")[:90])
        topic_obj = iss.get("topic") or {}
        topic = topic_obj.get("topic") if isinstance(topic_obj, dict) else topic_obj
        topic = esc(str(topic or "other"))
        silence = iss.get("silence") if isinstance(iss.get("silence"), dict) else {}
        compass_bits.append(
            f"<button type='button' class='fight-btn' data-i='{i}' data-mode='fight'>"
            f"<span class='fight-q'>{q}</span>"
            f"<span class='fight-meta'>{topic} · {esc(str(iss.get('source_count', 0)))} voices"
            + (
                f" · official {esc(str(iss.get('official_voice_count', 0)))}"
                if iss.get("official_voice_count")
                else " · media remix"
            )
            + (
                f" · silent {esc(str(silence.get('silent_count', 0)))}"
                if silence.get("silent_count") is not None
                else ""
            )
            + "</span>"
            "</button>"
        )
    if not issue_list:
        compass_bits.append("<p class='empty'>No Issues yet — Stage waits; Radar still feeds.</p>")

    # Center: Radar default; Issue stages hidden until a fight is picked
    stage_panels = []
    for i, iss in enumerate(issue_list):
        stage_panels.append(
            f"<div class='stage-panel' hidden data-i='{i}'>"
            + issue_stage_html(iss, continuity, panel_id=f"stage-{i}", faces=faces_map)
            + "</div>"
        )

    # Right Near me rail
    near_items = "".join(near_rail_item(c, continuity, faces_map, pin=False) for c in buckets["near"]) or "<p class='empty'>Nothing Near me yet.</p>"
    near_note = ""
    if demoted_booth:
        # A #pin- link is only honest when that pin is actually rendered (the
        # Moved strip is capped and deduped): anything else links out to the
        # publisher, so staging can never reject the edition over a dead anchor.
        pinned_ids = {str(c.get("id")) for c in prov_moved if c.get("id")}
        moved_bits = []
        for c in demoted_booth_items:
            cid = str(c.get("id") or "").strip()
            title = title_html(c.get("title"), 72)
            if cid and cid in pinned_ids:
                moved_bits.append(
                    f"<li><a class='moved-pin' href=\"#pin-{esc(cid)}\">{title}</a></li>"
                )
                continue
            url = esc(link_url(c.get("url")))
            if url:
                moved_bits.append(
                    f"<li><a href=\"{url}\" target=\"_blank\" rel=\"noopener\">{title}</a></li>"
                )
            else:
                moved_bits.append(f"<li>{title}</li>")
        moved_list = (
            "<ul class='moved-list'>" + "".join(moved_bits) + "</ul>"
            if moved_bits
            else ""
        )
        near_note = (
            f"<p class='cap'>Showing {len(buckets['near'])} of "
            f"{len(buckets['near']) + demoted_booth} — "
            f"<strong>{demoted_booth} moved → Province</strong> "
            f"(booth/brief format cut)</p>"
            f"{moved_list}"
        )

    # Mobile deck: Issues then Near me
    slides: list[str] = []
    for iss in issue_list:
        slides.append(issue_stage_html(iss, continuity, faces=faces_map))
    for c in buckets["near"]:
        slides.append(news_deck_html(c, continuity))
    deck_inner = "".join(
        f"<div class='slide' data-i='{i}'{' hidden' if i else ''}>{html}</div>"
        for i, html in enumerate(slides)
    ) if slides else "<div class='slide'><p class='empty'>Deck empty — run the Critical Path.</p></div>"
    n_slides = max(1, len(slides))

    prov_note = None
    if demoted_booth:
        prov_note = f"Includes {demoted_booth} booth/brief demoted from Near me (pinned visible)"
    archive = (
        archive_block(
            "Near me — Quebec City",
            "near-archive",
            buckets["near"],
            continuity,
            extra_note=(
                f"Showing {len(buckets['near'])} of {len(buckets['near']) + demoted_booth} — "
                f"{demoted_booth} booth/brief named on the Near me rail (jumped to Province pins)"
                if demoted_booth
                else None
            ),
        )
        + archive_block(
            "Province — Quebec",
            "province",
            buckets["province"],
            continuity,
            cap=DISPLAY_CAP,
            impact_prefer=True,
            pin_items=demoted_booth_items,
            extra_note=prov_note,
        )
        + archive_block(
            "Linked — hits that may matter here",
            "linked",
            buckets["linked"],
            continuity,
            cap=DISPLAY_CAP,
            impact_prefer=True,
        )
    )

    n_issues = len(issue_list)
    approaches = build_approaches(issue_list, continuity)
    approaches_html = "".join(approach_button_html(a) for a in approaches)
    if not approaches_html:
        approaches_html = (
            "<p class='empty approach-empty'>"
            "No Approaches yet — scars need ≥2 institutions. Lookout field still holds Near me."
            "</p>"
        )
    clustered_at = generated_at
    for iss in issue_list:
        if iss.get("clustered_at"):
            clustered_at = str(iss["clustered_at"])
            break
    pulse = pulse_payload(approaches, clustered_at)
    # Safe embed: no HTML esc (breaks JSON); neutralize script breakers only.
    pulse_json = (
        json.dumps(pulse, ensure_ascii=False, separators=(",", ":"))
        .replace("<", "\\u003c")
    )

    import life_facets

    facet_catalog = life_facets.catalog_payload()
    facet_signals = life_facets.signals_payload(approaches, issue_list)
    facets_json = (
        json.dumps(
            {"catalog": facet_catalog, "signals": facet_signals},
            ensure_ascii=False,
            separators=(",", ":"),
        )
        .replace("<", "\\u003c")
    )
    facet_toggles = "".join(
        (
            "<label class='facet-opt'>"
            f"<input type='checkbox' name='facet' value='{esc(f['id'])}' "
            f"data-facet='{esc(f['id'])}' />"
            f"<span class='facet-label'>{esc(f['label'])}</span>"
            f"<span class='facet-blurb'>{esc(f.get('blurb') or '')}</span>"
            "</label>"
        )
        for f in life_facets.FACET_CATALOG
    )

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <meta name="description" content="Vigie — Quebec City lookout. What approaches life here: Approaches from public method, not a feed." />
  <meta name="theme-color" content="#0b4f4a" />
  <link rel="icon" href="/favicon.svg" type="image/svg+xml" />
  <link rel="canonical" href="https://vigieqc.com/explorer.html" />
  <title>Vigie — Quebec City lookout</title>
  <link rel="stylesheet" href="/assets/fonts.css" />
  <style>
    :root {{
      /* beauty-without-fog-v0.1 — Cap Diamant stone · fleuve slate · winter ice */
      --bg: #d4dde4;
      --bg-deep: #b7c5d0;
      --paper: #eef2f5;
      --ink: #121a20;
      --muted: #4a5864;
      --line: #a8b6c2;
      --accent: #0b4f4a;
      --accent-soft: #d3e4e1;
      --chip: #e2e8ec;
      --mist: rgba(238,242,245,.72);
      --nest-near: #0b4f4a;
      --nest-province: #3d5a6c;
      --nest-linked: #7a8792;
      --radius: 10px;
      --font-display: "Newsreader", "Iowan Old Style", "Palatino Linotype", Palatino, serif;
      --font-ui: "Figtree", "Avenir Next", "Segoe UI", sans-serif;
      color: var(--ink);
      background: var(--bg);
      line-height: 1.45;
      font-family: var(--font-ui);
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0; padding: 0 0 2.5rem;
      min-height: 100vh;
      /* Nest wash only — refuse multi-layer AI glow / inventing a QC photo hero */
      background:
        linear-gradient(180deg, #e8eef2 0%, var(--bg) 42%, var(--bg-deep) 100%);
      background-attachment: fixed;
    }}
    .wrap {{ width: 100%; max-width: none; margin: 0; padding: .85rem clamp(.85rem, 2.5vw, 1.75rem) 0; }}
    .skip {{
      position: absolute; left: .75rem; top: .75rem; z-index: 50;
      transform: translateY(-120%);
      background: var(--accent); color: #f3f7f6; text-decoration: none;
      padding: .4rem .75rem; border-radius: 6px; font-size: .85rem; font-weight: 600;
    }}
    .skip:focus {{ transform: none; }}

    /* === Arrival — one composition (DESIGN.md) === */
    .arrival {{
      min-height: calc(100vh - 2rem);
      display: flex; flex-direction: column; justify-content: center;
      padding: clamp(1.4rem, 5vh, 2.75rem) 0 clamp(1.6rem, 5vh, 3rem);
      max-width: 40rem;
    }}
    .arrival-brand {{
      font-family: var(--font-display);
      font-size: clamp(3.6rem, 14vw, 6.25rem);
      font-weight: 600;
      letter-spacing: -.045em;
      line-height: .92;
      margin: 0 0 .7rem;
      color: var(--ink);
      animation: vigie-brand-in .6s ease both;
    }}
    .arrival-line {{
      font-family: var(--font-display);
      font-size: clamp(1.25rem, 3.2vw, 1.7rem);
      font-weight: 500;
      letter-spacing: -.02em;
      line-height: 1.22;
      margin: 0 0 .5rem;
      max-width: 22ch;
      color: var(--ink);
    }}
    .arrival-sub {{
      color: var(--muted);
      margin: 0 0 1rem;
      font-size: .98rem;
      max-width: 34rem;
    }}
    .since-left {{
      margin: 0 0 .95rem;
      padding: 0 0 .55rem;
      border: none;
      border-bottom: 1px solid var(--line);
      border-radius: 0;
      background: transparent;
      font-size: .84rem;
      color: var(--muted);
      max-width: 36rem;
    }}
    .since-left[hidden] {{ display: none; }}
    .since-left strong {{ color: var(--ink); font-weight: 600; }}
    .approaches {{
      display: grid; gap: .55rem;
      margin: 0 0 1.25rem;
    }}
    .approach {{
      display: block; width: 100%; text-align: left; cursor: pointer; font: inherit;
      background: var(--mist);
      border: 1px solid var(--line);
      border-radius: var(--radius);
      padding: .9rem 1rem .85rem 1.05rem;
      color: var(--ink);
      box-shadow: inset 3px 0 0 var(--nest-linked);
      transition: border-color .2s ease, background .2s ease, transform .18s ease;
      animation: vigie-approach-in .5s ease both;
    }}
    .approach[data-nest="near"] {{ box-shadow: inset 3px 0 0 var(--nest-near); }}
    .approach[data-nest="province"] {{ box-shadow: inset 3px 0 0 var(--nest-province); }}
    .approach[data-nest="linked"] {{ box-shadow: inset 3px 0 0 var(--nest-linked); }}
    .approaches .approach:nth-child(1) {{ animation-delay: .04s; }}
    .approaches .approach:nth-child(2) {{ animation-delay: .09s; }}
    .approaches .approach:nth-child(3) {{ animation-delay: .14s; }}
    .approaches .approach:nth-child(4) {{ animation-delay: .19s; }}
    .approaches .approach:nth-child(5) {{ animation-delay: .24s; }}
    .approach-remix {{
      background: color-mix(in srgb, var(--mist) 88%, #c4a574 12%);
    }}
    .approach-new {{
      border-color: var(--accent);
      background: var(--paper);
    }}
    .approach-changed {{
      border-color: color-mix(in srgb, var(--accent) 50%, var(--line));
    }}
    .approach:hover, .approach:focus-visible {{
      border-color: var(--accent);
      background: var(--paper);
      outline: none;
    }}
    .approach:active {{ transform: translateY(1px); }}
    .approach-top {{
      display: flex; gap: .55rem; align-items: flex-start; justify-content: space-between;
    }}
    .approach-q {{
      display: block;
      font-family: var(--font-display);
      font-size: 1.08rem;
      font-weight: 500;
      letter-spacing: -.015em;
      line-height: 1.3;
      flex: 1;
    }}
    .approach-badge {{
      flex: 0 0 auto;
      font-size: .68rem;
      font-weight: 700;
      letter-spacing: .03em;
      text-transform: uppercase;
      color: var(--accent);
      background: transparent;
      border: 1px solid var(--accent);
      border-radius: 4px;
      padding: .12rem .4rem;
    }}
    .approach-meta {{
      display: flex; flex-wrap: wrap; gap: .3rem; align-items: center;
      margin-top: .5rem; font-size: .74rem; color: var(--muted);
    }}
    .approach-silence-preview {{
      display: block; width: 100%;
      margin-top: .35rem; font-size: .72rem; color: var(--muted);
      line-height: 1.35;
    }}
    .approach-silence-label {{
      font-weight: 700; letter-spacing: .02em; text-transform: uppercase;
      font-size: .66rem; color: var(--accent); margin-right: .25rem;
    }}
    .arrival .chip {{
      border-radius: 4px;
      background: transparent;
      border: 1px solid var(--line);
      padding: .12rem .4rem;
    }}
    .arrival .chip.geo {{ color: var(--accent); font-weight: 600; border-color: color-mix(in srgb, var(--accent) 35%, var(--line)); }}
    .arrival .chip.silence {{ color: #3d4a54; border-color: #b8c4ce; }}
    .arrival .chip.remix {{ color: #6b3a12; border-color: #d9c4a4; }}
    .arrival .chip.unit {{ color: #1e4d2b; font-weight: 600; border-color: #b7cbb8; }}
    .arrival .chip.unit.empty {{ color: var(--muted); font-weight: 500; border-style: dashed; }}
    .arrival .chip.official {{ color: #1e4d2b; font-weight: 700; border-color: #b7cbb8; }}
    .arrival .chip.voices {{ color: var(--ink); }}
    .approach-empty {{ margin: 0; color: var(--muted); }}
    .arrival-cta {{
      display: flex; flex-wrap: wrap; gap: .65rem 1rem;
      align-items: center;
      margin: 0 0 1rem;
    }}
    .arrival-cta .cta-primary {{
      background: var(--accent); color: #f3f7f6; border: none;
      border-radius: 8px; padding: .58rem 1.2rem;
      font: inherit; font-weight: 600; font-size: .92rem; cursor: pointer;
    }}
    .arrival-cta .cta-primary:hover {{ filter: brightness(1.06); }}
    .arrival-cta a {{
      color: var(--accent); text-decoration: none; font-size: .88rem; font-weight: 500;
      padding: .2rem 0;
    }}
    .arrival-cta a:hover {{ text-decoration: underline; }}
    .facets {{
      margin: 0 0 .85rem; max-width: 36rem;
      border: none;
      border-top: 1px solid var(--line);
      border-radius: 0;
      background: transparent;
      padding: .65rem 0 0;
    }}
    .facets summary {{
      cursor: pointer; font-weight: 600; font-size: .86rem; color: var(--muted);
      list-style: none;
    }}
    .facets summary::-webkit-details-marker {{ display: none; }}
    .facets[open] summary {{ color: var(--ink); }}
    .facets-note {{
      margin: .45rem 0 .65rem; font-size: .76rem; color: var(--muted); line-height: 1.35;
    }}
    .facet-toggles {{ display: grid; gap: .4rem; }}
    .facet-opt {{
      display: grid; grid-template-columns: auto 1fr; gap: .1rem .55rem;
      align-items: baseline; font-size: .84rem; cursor: pointer;
    }}
    .facet-opt input {{ margin: .2rem 0 0; }}
    .facet-label {{ font-weight: 600; }}
    .facet-blurb {{ grid-column: 2; font-size: .75rem; color: var(--muted); }}
    .facets-actions {{ margin-top: .55rem; display: flex; gap: .75rem; align-items: center; }}
    .facets-actions button {{
      background: transparent; border: 1px solid var(--line); border-radius: 6px;
      padding: .25rem .65rem; font: inherit; font-size: .76rem; cursor: pointer; color: var(--accent);
    }}
    .facets-status {{
      margin: 0 0 .75rem; font-size: .78rem; color: var(--muted); max-width: 36rem;
    }}
    .facets-status[hidden] {{ display: none; }}
    .facets-status strong {{ color: var(--ink); font-weight: 600; }}
    .arrival-ops {{
      margin: .35rem 0 0; max-width: 36rem;
      border: none; border-top: 1px solid var(--line);
      padding: .55rem 0 0; background: transparent;
    }}
    .arrival-ops summary {{
      cursor: pointer; font-size: .78rem; font-weight: 600; color: var(--muted);
      list-style: none;
    }}
    .arrival-ops summary::-webkit-details-marker {{ display: none; }}
    .arrival-ops[open] summary {{ color: var(--ink); }}
    .arrival .clock {{
      font-size: .72rem; margin: .45rem 0 0; color: color-mix(in srgb, var(--muted) 80%, transparent);
      font-variant-numeric: tabular-nums;
    }}
    .arrival .methods {{ font-size: .72rem; margin: .25rem 0 0; opacity: .92; }}
    .arrival .methods a {{ color: var(--accent); margin-right: .65rem; text-decoration: none; }}
    .arrival .methods a:hover {{ text-decoration: underline; }}

    @keyframes vigie-brand-in {{
      from {{ opacity: 0; transform: translateY(10px); }}
      to {{ opacity: 1; transform: none; }}
    }}
    @keyframes vigie-approach-in {{
      from {{ opacity: 0; transform: translateY(8px); }}
      to {{ opacity: 1; transform: none; }}
    }}

    .field-shell {{ display: none; }}
    body.field-open .field-shell {{ display: block; }}
    /* Keep UA [hidden] from fighting field-open if attribute lingers */
    body.field-open .field-shell[hidden] {{ display: block; }}
    footer.site-foot {{ display: none; }}
    body.field-open footer.site-foot {{ display: block; }}
    body.field-open .arrival {{
      min-height: 0;
      padding: .85rem 0 1rem;
      border-bottom: 1px solid var(--line);
      margin-bottom: .75rem;
    }}
    body.field-open .arrival-brand {{ font-size: clamp(1.8rem, 4vw, 2.4rem); animation: none; }}
    body.field-open .arrival-line {{ font-size: 1.05rem; max-width: none; }}
    body.field-open .approaches {{ margin-bottom: .75rem; }}
    body.field-open .approach {{ animation: none; }}

    .topnav {{
      position: sticky; top: 0; z-index: 30;
      display: none; flex-wrap: wrap; gap: .4rem; align-items: center;
      padding: .45rem 0; margin: 0 0 .75rem;
      background: color-mix(in srgb, var(--bg) 88%, transparent);
      border-bottom: 1px solid var(--line);
    }}
    body.field-open .topnav {{ display: flex; }}
    .topnav a, .topnav button.linkish {{
      text-decoration: none; color: var(--ink); background: var(--paper);
      border: 1px solid var(--line); border-radius: 6px; padding: .3rem .65rem;
      font-size: .8rem; cursor: pointer; font: inherit;
    }}
    .topnav a:hover, .topnav button.linkish:hover {{ border-color: var(--accent); color: var(--accent); }}
    .chip {{
      display: inline-block; background: var(--chip); color: var(--muted);
      border-radius: 4px; padding: .1rem .45rem; font-size: .7rem;
    }}
    .chip.source {{ background: var(--accent-soft); color: var(--accent); font-weight: 600; }}
    .chip.geo {{ background: #dce6ec; color: var(--accent); }}
    .chip.unit {{ background: #e8f0e9; color: #1e4d2b; font-weight: 600; }}
    .chip.official {{ background: #e8f0e9; color: #1e4d2b; font-weight: 700; }}
    .chip.media {{ background: var(--chip); color: var(--muted); }}
    .chip.silence {{ background: #e4e8ec; color: #3d4a54; }}
    .chip.remix {{ background: #f3e8dc; color: #6b3a12; }}
    .remix-warn {{ color: #6b3a12; background: #f7efe4; border: 1px solid #e4d3b8; border-radius: 8px; padding: .45rem .6rem; }}
    .claims {{ list-style: none; margin: .35rem 0 0; padding: 0; display: grid; gap: .25rem; }}
    .claim {{ font-size: .78rem; line-height: 1.35; color: var(--ink); background: #eef2f4; border-left: 3px solid var(--accent); padding: .3rem .45rem; }}
    .claim-speaker {{ font-weight: 700; color: var(--accent); }}
    .claim-q {{ font-style: italic; }}
    .silence {{
      margin-top: .85rem; padding-top: .65rem; border-top: 1px dashed var(--line);
    }}
    .silence-title {{
      font-size: .88rem; margin: 0 0 .35rem; letter-spacing: -.01em;
    }}
    .silent-list {{
      list-style: none; margin: .35rem 0 0; padding: 0;
      display: flex; flex-wrap: wrap; gap: .35rem .55rem;
    }}
    .silent-item {{ font-size: .78rem; }}
    .silent-name {{ font-weight: 600; }}
    .fight-theater .fight-glance {{ margin: .25rem 0 .55rem; }}
    .fight-arc {{
      display: grid;
      grid-template-columns: 1fr 1fr;
      gap: .75rem;
      margin: .35rem 0 .75rem;
      padding: .7rem .75rem;
      border: 1px solid var(--line);
      border-radius: 10px;
      background: color-mix(in srgb, var(--accent-soft) 55%, #fff);
    }}
    .arc-lane {{ min-width: 0; }}
    .arc-title {{
      font-family: var(--font-display);
      font-size: .95rem; margin: 0 0 .35rem; letter-spacing: -.01em;
    }}
    .quiet-lane .arc-title {{ color: #3d4a54; }}
    .spoke-lane .arc-title {{ color: var(--accent); }}
    .arc-note {{ font-size: .72rem; color: var(--muted); margin: 0 0 .4rem; line-height: 1.35; }}
    .arc-list {{
      list-style: none; margin: 0; padding: 0;
      display: flex; flex-wrap: wrap; gap: .3rem .45rem;
    }}
    .arc-item {{ font-size: .78rem; }}
    .arc-item.empty {{ color: var(--muted); }}
    .arc-name {{ font-weight: 650; }}
    .confrontation-title {{
      font-size: .8rem; font-weight: 700; letter-spacing: .04em;
      text-transform: uppercase; color: var(--muted); margin: .55rem 0 .35rem;
    }}
    .stage.confrontation .voice-panel .face {{
      height: 3.5rem; max-height: 3.5rem; margin-top: .45rem; margin-bottom: 0;
      opacity: .92;
    }}
    @media (max-width: 700px) {{
      .fight-arc {{ grid-template-columns: 1fr; }}
    }}
    body.field-open .zone-left,
    body.field-open .zone-right {{
      opacity: .72;
    }}
    body.field-open .zone-center {{
      box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 28%, var(--line));
    }}
    .rule {{ font-size: .82rem; color: var(--muted); margin: .35rem 0 .55rem; }}
    .cap {{ font-size: .78rem; color: var(--muted); margin: .25rem 0 .45rem; }}
    .empty {{ color: var(--muted); font-size: .9rem; }}
    .kind-chip {{
      display: inline-block; font-size: .72rem; font-weight: 700; letter-spacing: .04em;
      text-transform: uppercase; color: var(--accent); margin-bottom: .35rem;
    }}
    .stage-title {{
      font-family: var(--font-display);
      font-size: 1.25rem; margin: 0 0 .45rem; letter-spacing: -.02em;
    }}
    .card-meta {{ display: flex; flex-wrap: wrap; gap: .3rem; align-items: center; margin: .35rem 0; }}
    .stage {{ display: grid; gap: .65rem; margin: .55rem 0; }}
    .stage.face-pair {{ grid-template-columns: 1fr 1fr; gap: .75rem; }}
    .voice-panel {{
      background: var(--paper); border: 1px solid var(--line); border-radius: 10px; padding: .65rem .7rem;
    }}
    .voice-name {{ font-weight: 700; font-size: .88rem; margin-bottom: .35rem; }}
    .voice-link {{
      display: block; color: var(--accent); text-decoration: none; font-size: .86rem; margin: .25rem 0;
    }}
    .voice-link:hover {{ color: var(--accent); text-decoration: underline; }}
    .same-fight {{ margin-top: .55rem; font-size: .8rem; }}
    .same-label {{ color: var(--muted); margin-right: .25rem; }}
    .same {{ color: var(--accent); text-decoration: none; }}
    .same:hover {{ text-decoration: underline; }}
    .issue-stage {{
      background: var(--paper); border: 1px solid var(--line);
      border-radius: var(--radius); padding: 1rem; margin-bottom: .55rem;
    }}
    .command-field {{ display: none; }}
    @media (min-width: 960px) {{
      body.field-open .command-field {{
        display: grid;
        grid-template-columns: minmax(14rem, 18rem) minmax(0, 1fr) minmax(14rem, 18rem);
        gap: .85rem; align-items: start;
      }}
      body.field-open .mobile-deck {{ display: none !important; }}
      body.field-open .mobile-radar {{ display: none !important; }}
    }}
    .zone {{
      background: var(--paper); border: 1px solid var(--line);
      border-radius: var(--radius); padding: .85rem;
    }}
    .zone h2 {{
      font-family: var(--font-display);
      font-size: 1.05rem; margin: 0 0 .55rem;
    }}
    .fight-list {{ display: grid; gap: .4rem; }}
    .fight-btn {{
      display: block; width: 100%; text-align: left; cursor: pointer; font: inherit;
      background: #fff; border: 1px solid var(--line); border-radius: 10px;
      padding: .55rem .6rem; color: var(--ink);
    }}
    .fight-btn:hover {{ border-color: var(--accent); }}
    .fight-btn.active {{
      border-color: var(--accent); background: var(--accent-soft);
      box-shadow: inset 3px 0 0 var(--accent);
    }}
    .fight-q {{ display: block; font-size: .84rem; font-weight: 650; }}
    .fight-meta {{ display: block; font-size: .7rem; color: var(--muted); margin-top: .25rem; }}
    .micro-nav {{ display: flex; gap: .35rem; margin: 0 0 .55rem; }}
    .micro-nav button {{
      background: transparent; border: 1px solid var(--line); border-radius: 999px;
      padding: .25rem .65rem; font: inherit; font-size: .75rem; cursor: pointer; color: var(--ink);
    }}
    .micro-nav button:disabled {{ opacity: .35; }}
    .near-stack {{ display: grid; gap: .45rem; max-height: 70vh; overflow: auto; }}
    .near-item {{
      border: 1px solid var(--line); border-radius: 10px; padding: .55rem .6rem; background: #fff;
    }}
    .near-title {{
      display: block; color: var(--accent); text-decoration: none; font-weight: 650; font-size: .86rem;
    }}
    .near-title:hover {{ text-decoration: underline; }}
    .moved-note {{ font-size: .78rem; color: var(--muted); margin: 0 0 .55rem; }}
    .moved-note ul {{ margin: .25rem 0 0; padding-left: 1.1rem; }}
    .moved-pin {{ color: var(--accent); }}
    .impact-radar {{ min-height: 12rem; }}
    .radar-grid {{
      display: grid; grid-template-columns: repeat(auto-fill, minmax(14rem, 1fr));
      gap: .55rem; margin-top: .35rem;
    }}
    .mobile-radar {{ display: none; margin-bottom: .85rem; }}
    @media (max-width: 959px) {{
      body.field-open .mobile-radar {{ display: block; }}
    }}
    .face {{
      display: block; width: 100%; aspect-ratio: 16 / 9; object-fit: cover;
      border-radius: 8px; margin: 0 0 .45rem; background: var(--chip);
    }}
    .near-item .face, .radar-grid .face {{
      height: 5.25rem; aspect-ratio: auto; max-height: 5.25rem;
      object-fit: cover; width: 100%;
    }}
    .face.face-empty {{
      background: var(--chip);
      border: 1px dashed var(--line);
    }}
    .voice-panel .face {{ height: 5.25rem; aspect-ratio: auto; max-height: 5.25rem; margin-bottom: .4rem; }}
    .mode-radar {{
      display: block; width: 100%; text-align: left; cursor: pointer; font: inherit;
      background: #fff; border: 1px solid var(--line); border-radius: 10px;
      padding: .55rem .6rem; color: var(--ink); margin: 0 0 .75rem;
    }}
    .mode-radar:hover {{ border-color: var(--accent); }}
    .mode-radar.active {{
      border-color: var(--accent); background: var(--accent-soft);
      box-shadow: inset 3px 0 0 var(--accent);
    }}
    .mode-radar .fight-q {{ display: block; font-size: .84rem; font-weight: 650; }}
    .mode-radar .fight-meta {{ display: block; font-size: .7rem; color: var(--muted); margin-top: .25rem; }}
    .mobile-deck {{
      display: none;
      background: var(--paper); border: 1px solid var(--line);
      border-radius: var(--radius); padding: 1rem;
      margin-bottom: .85rem;
    }}
    @media (max-width: 959px) {{
      body.field-open .mobile-deck {{ display: block; }}
    }}
    .deck-toolbar {{
      display: flex; flex-wrap: wrap; justify-content: space-between; gap: .5rem; margin-bottom: .75rem;
    }}
    .deck-count {{ font-size: .8rem; color: var(--muted); }}
    .deck-nav button {{
      background: var(--accent); color: #fff; border: none; border-radius: 999px;
      padding: .4rem .9rem; font-size: .84rem; cursor: pointer; font: inherit; font-weight: 600;
    }}
    .deck-nav button.ghost {{ background: transparent; color: var(--accent); border: 1px solid var(--line); }}
    .deck-nav button:disabled {{ opacity: .35; }}
    .deck-title {{
      display: block; color: var(--accent); text-decoration: none;
      font-size: 1.2rem; font-weight: 700; margin: 0 0 .4rem;
    }}
    .lede-act {{ margin: .7rem 0 .35rem; }}
    .read {{
      display: inline-block; background: var(--accent); color: #fff; text-decoration: none;
      border-radius: 999px; padding: .45rem .95rem; font-weight: 600; font-size: .86rem;
    }}
    #archive {{ display: none; margin-top: .5rem; }}
    #archive.open {{ display: block; }}
    #archive .archive-inner {{ max-width: 44rem; }}
    .nest {{
      background: var(--paper); border: 1px solid var(--line);
      border-radius: var(--radius); padding: 1rem; margin-bottom: .75rem;
    }}
    .nest h3 {{ font-size: 1rem; margin: 0 0 .45rem; font-family: var(--font-display); }}
    .n {{ color: var(--muted); font-weight: 500; }}
    .cards {{ display: grid; gap: .55rem; }}
    .card {{ border: 1px solid var(--line); border-radius: 10px; padding: .7rem .75rem; background: #fff; }}
    .card-title {{ display: block; color: var(--accent); text-decoration: none; font-weight: 650; font-size: .95rem; }}
    .card-title:hover {{ text-decoration: underline; }}
    .score {{ font-size: .7rem; color: var(--muted); }}
    footer {{
      font-size: .78rem; color: var(--muted); margin-top: 1.25rem;
      padding-top: .85rem; border-top: 1px solid var(--line); max-width: 52rem;
    }}
    footer a {{ color: var(--accent); }}
    @media (max-width: 959px) {{
      .stage.face-pair {{ grid-template-columns: 1fr; }}
    }}
    @media (prefers-reduced-motion: reduce) {{
      .approach {{ transition: none; animation: none !important; }}
      .arrival-brand {{ animation: none !important; }}
    }}
  </style>
</head>
<body>
  <aside style="padding:12px 24px;background:#152f3a;color:white;font-size:13px">
    <a href="/" style="color:white">← Retour au point local</a> · Atelier expérimental.
    Les dossiers sont des rapprochements proposés, pas des contradictions démontrées.
    Plusieurs sources ne prouvent pas plusieurs confirmations indépendantes.
    Une absence dans les flux ne prouve pas un silence éditorial.
  </aside>
  <div class="wrap">
    <a class="skip" href="#approaches">Skip to Approaches</a>
    <section class="arrival" id="arrival" aria-label="Lookout arrival" data-design="beauty-without-fog-v0.1" data-phase="arrival-v0.1">
      <p class="arrival-brand">Vigie</p>
      <h1 class="arrival-line">What approaches Quebec City life</h1>
      <p class="arrival-sub">Method public. Judgment yours. Not a feed.</p>
      <p class="since-left" id="since-left" hidden aria-live="polite"></p>
      <div class="approaches" id="approaches" aria-label="Approaches" tabindex="-1">
        {approaches_html}
      </div>
      <script type="application/json" id="vigie-pulse">{pulse_json}</script>
      <script type="application/json" id="vigie-facets">{facets_json}</script>
      <div class="arrival-cta">
        <button type="button" class="cta-primary" id="btn-open-field">Open lookout field</button>
        <a href="/morning.html" id="link-morning">Morning pulse</a>
        <a href="#archive" id="link-archive-arrival">Archive</a>
        <a href="#method" id="link-method-arrival">Method</a>
      </div>
      <details class="facets" id="life-facets">
        <summary>Life facets (opt-in)</summary>
        <p class="facets-note">
          Reorders Approaches only. Does not change public rank.
          Method: <a href="/methode/facettes.html">FACETS</a>. w_impact stays gated.
        </p>
        <div class="facet-toggles" id="facet-toggles">{facet_toggles}</div>
        <div class="facets-actions">
          <button type="button" id="facets-clear">Clear facets</button>
          <a href="/methode/facettes.html">Published weights</a>
        </div>
      </details>
      <p class="facets-status" id="facets-status" hidden aria-live="polite"></p>
      <details class="arrival-ops" id="arrival-ops">
        <summary>Clock &amp; method</summary>
        <p class="clock">{esc(clock_line(clock))}</p>
        <p class="methods" id="method">Method (real files): <a href="/methode/vision.html">VISION</a><a href="/methode/classement.html">ranking</a><a href="/methode/sources.html">sources</a><a href="/methode/financement.html">RENT</a><a href="/methode/frictions.html">FRICTION</a><a href="/methode/facettes.html">FACETS</a><a href="/methode/design.html">DESIGN</a></p>
      </details>
    </section>

    <div class="field-shell" id="field-shell" hidden aria-hidden="true">
    <nav class="topnav" aria-label="Lookout">
      <a href="#arrival">Arrival</a>
      <a href="#command">Lookout field</a>
      <button type="button" class="linkish" id="btn-archive" aria-expanded="false">Open archive</button>
      <a href="#method">Method</a>
    </nav>

    <section id="command" class="command-field" aria-label="Lookout field">
      <aside class="zone zone-left">
        <button type="button" class="mode-radar active" data-mode="radar" id="btn-radar">
          <span class="fight-q">Impact Radar</span>
          <span class="fight-meta">Province + Linked · impact-first · mode</span>
        </button>
        <h2>Fights <span class="n">({n_issues})</span></h2>
        <div class="micro-nav">
          <button type="button" id="iss-prev" disabled>Prev</button>
          <button type="button" id="iss-next">Next</button>
        </div>
        <div class="fight-list" id="fight-list">{''.join(compass_bits)}</div>
      </aside>
      <main class="zone zone-center" id="issue-stage-zone">
        {radar_html}
        {''.join(stage_panels)}
      </main>
      <aside class="zone zone-right" id="near">
        <h2>Near me <span class="n">({len(buckets["near"])})</span></h2>
        {near_note}
        <div class="near-stack">{near_items}</div>
      </aside>
    </section>

    <section id="mobile-radar" class="mobile-radar" aria-label="Impact Radar">
      <div class="zone">
        {radar_html_mobile}
      </div>
    </section>
    <section id="deck" class="mobile-deck" aria-label="Mobile deck">
      <div class="deck-toolbar">
        <span class="deck-count"><span id="deck-pos">1</span> / {n_slides} · Issues then Near me</span>
        <div class="deck-nav">
          <button type="button" class="ghost" id="deck-prev" disabled>Prev</button>
          <button type="button" id="deck-next">Next</button>
        </div>
      </div>
      <div id="deck-slides">{deck_inner}</div>
    </section>

    <div id="archive" aria-label="Archive nests">
      <div class="archive-inner">
      <p class="cap">Archive — Province and Linked for patient eyes. Full set stays in latest_ranked.json. Not the face of the lookout.</p>
      {archive}
      </div>
    </div>
    </div>
    <footer class="site-foot">Aggregate only. Arrival is Approaches from fights on disk — never a painted personalization feed. Beauty without fog: DESIGN. Life facets are opt-in and reorder Approaches only (FACETS). Lookout field (Radar / Stage / Near me) opens on invitation. Same fight only from scars. No infinite scroll. We clarify; we do not bait dwell-time. Links: <a href="/methode/vision.html">VISION</a> · <a href="/methode/classement.html">ranking</a> · <a href="/methode/sources.html">sources</a> · <a href="/methode/financement.html">RENT</a> · <a href="/methode/frictions.html">FRICTION</a> · <a href="/methode/facettes.html">FACETS</a> · <a href="/methode/design.html">DESIGN</a>.</footer>
  </div>
  <script>
  (function () {{
    function openField() {{
      document.body.classList.add("field-open");
      var shell = document.getElementById("field-shell");
      if (shell) {{
        shell.hidden = false;
        shell.setAttribute("aria-hidden", "false");
      }}
    }}
    var PULSE_KEY = "vigie_visit_v1";
    function readPrev() {{
      try {{
        var p = JSON.parse(localStorage.getItem(PULSE_KEY) || "null");
        return p && Array.isArray(p.approaches) && p.approaches.every(function(a) {{ return a && typeof a === "object"; }}) ? p : null;
      }}
      catch (e) {{ return null; }}
    }}
    function writePulse(pulse) {{
      try {{
        localStorage.setItem(PULSE_KEY, JSON.stringify({{
          seen_at: new Date().toISOString(),
          clustered_at: pulse.clustered_at,
          approaches: pulse.approaches || []
        }}));
      }} catch (e) {{}}
    }}
    function formatStoreClock(iso) {{
      var s = String(iso || "").trim();
      if (!s || !Number.isFinite(Date.parse(s))) return "unknown";
      s = new Date(s).toISOString();
      var t = s.indexOf("T");
      if (t > 0) {{
        var date = s.slice(0, t);
        var hhmm = s.slice(t + 1, t + 6);
        return date + " " + hhmm + " UTC";
      }}
      return s.slice(0, 19);
    }}
    function fpOf(a) {{
      if (!a) return "";
      if (a.fp != null && String(a.fp) !== "") return String(a.fp);
      var silent = (a.silent == null) ? 0 : a.silent;
      var units = (typeof a.unit_count === "number")
        ? a.unit_count
        : ((a.units && a.units.length) || 0);
      return String(a.voices || 0) + "|" + String(silent) + "|"
        + (a.remix ? "1" : "0") + "|" + String(units);
    }}
    function applySinceLeft() {{
      var el = document.getElementById("since-left");
      var raw = document.getElementById("vigie-pulse");
      if (!el || !raw) return;
      var pulse;
      try {{ pulse = JSON.parse(raw.textContent || "{{}}"); }}
      catch (e) {{ return; }}
      var curr = pulse.approaches || [];
      var prev = readPrev();
      var buttons = Array.prototype.slice.call(document.querySelectorAll(".approaches .approach"));
      var clock = formatStoreClock(pulse.clustered_at);
      if (!prev || !prev.approaches || !prev.approaches.length) {{
        el.hidden = false;
        el.innerHTML = "<strong>First look</strong> — store pulse " + clock + ".";
        writePulse(pulse);
        return;
      }}
      /* Compare actual contents even when a store timestamp is reused. */
      var prevMap = Object.create(null);
      prev.approaches.forEach(function (a) {{
        if (a && a.issue_id) prevMap[a.issue_id] = a;
      }});
      var curIds = Object.create(null);
      var nNew = 0, nChanged = 0;
      buttons.forEach(function (btn) {{
        var id = btn.getAttribute("data-issue-id") || "";
        var fp = btn.getAttribute("data-fp") || "";
        curIds[id] = true;
        var badge = btn.querySelector(".approach-badge");
        if (!prevMap[id]) {{
          nNew += 1;
          btn.classList.add("approach-new");
          if (badge) {{ badge.hidden = false; badge.textContent = "new"; }}
        }} else if (fpOf(prevMap[id]) !== fp) {{
          nChanged += 1;
          btn.classList.add("approach-changed");
          if (badge) {{ badge.hidden = false; badge.textContent = "changed"; }}
        }}
      }});
      var gone = 0;
      Object.keys(prevMap).forEach(function (id) {{ if (!curIds[id]) gone += 1; }});
      el.hidden = false;
      var was = formatStoreClock(prev.clustered_at);
      if (nNew === 0 && nChanged === 0 && gone === 0) {{
        el.innerHTML = "<strong>Since you left</strong> — same Approaches (store moved "
          + clock + "; was " + was + ").";
      }} else {{
        var bits = [];
        if (nNew) bits.push(nNew + " new");
        if (nChanged) bits.push(nChanged + " changed");
        if (gone) bits.push(gone + " left Approaches");
        el.innerHTML = "<strong>Since you left</strong> — " + bits.join(" · ")
          + " (store " + clock + "; was " + was + " · order unchanged)";
      }}
      writePulse(pulse);
    }}
    applySinceLeft();

    var FACET_KEY = "vigie_facets_v1";
    function readFacets() {{
      try {{
        var raw = JSON.parse(localStorage.getItem(FACET_KEY) || "[]");
        return Array.isArray(raw) ? raw.map(String) : [];
      }} catch (e) {{ return []; }}
    }}
    function writeFacets(ids) {{
      try {{ localStorage.setItem(FACET_KEY, JSON.stringify(ids || [])); }}
      catch (e) {{}}
    }}
    function loadFacetBundle() {{
      var raw = document.getElementById("vigie-facets");
      if (!raw) return null;
      try {{ return JSON.parse(raw.textContent || "{{}}"); }}
      catch (e) {{ return null; }}
    }}
    function facetScore(sig, facets, catalog) {{
      if (!facets || !facets.length) return 0;
      var topic = String((sig && sig.topic) || "other");
      var kinds = {{}};
      ((sig && sig.unit_kinds) || []).forEach(function (k) {{ kinds[String(k)] = true; }});
      var byId = {{}};
      ((catalog && catalog.facets) || []).forEach(function (f) {{
        if (f && f.id) byId[String(f.id)] = f;
      }});
      var score = 0;
      facets.forEach(function (fid) {{
        var f = byId[String(fid)];
        if (!f) return;
        var topics = f.topics || [];
        for (var i = 0; i < topics.length; i++) {{
          if (String(topics[i]) === topic) {{
            score += Number(f.topic_weight || 0);
            break;
          }}
        }}
        var uk = f.unit_kinds || [];
        for (var j = 0; j < uk.length; j++) {{
          if (kinds[String(uk[j])]) {{
            score += Number(f.unit_weight || 0);
            break;
          }}
        }}
      }});
      return score;
    }}
    function applyLifeFacets() {{
      var root = document.getElementById("approaches");
      var status = document.getElementById("facets-status");
      var bundle = loadFacetBundle();
      if (!root || !bundle) return;
      var catalog = bundle.catalog || {{}};
      var signals = (bundle.signals && bundle.signals.approaches) || [];
      var sigMap = {{}};
      signals.forEach(function (s) {{
        if (s && s.issue_id) sigMap[String(s.issue_id)] = s;
      }});
      var checks = Array.prototype.slice.call(
        document.querySelectorAll("#facet-toggles input[data-facet]")
      );
      var saved = readFacets();
      var allowed = {{}};
      ((catalog.facets) || []).forEach(function (f) {{
        if (f && f.id) allowed[String(f.id)] = true;
      }});
      var active = [];
      checks.forEach(function (inp) {{
        var id = inp.getAttribute("data-facet") || "";
        var on = allowed[id] && saved.indexOf(id) >= 0;
        inp.checked = !!on;
        if (on) active.push(id);
      }});
      var buttons = Array.prototype.slice.call(root.querySelectorAll(".approach"));
      if (!buttons.length) return;
      var decorated = buttons.map(function (btn, i) {{
        var id = btn.getAttribute("data-issue-id") || "";
        var attrKinds = (btn.getAttribute("data-unit-kinds") || "")
          .split(",").map(function (k) {{ return String(k || "").trim(); }})
          .filter(Boolean);
        var sig = sigMap[id]
          ? {{
              topic: sigMap[id].topic,
              unit_kinds: (sigMap[id].unit_kinds && sigMap[id].unit_kinds.length)
                ? sigMap[id].unit_kinds
                : attrKinds,
              store_index: sigMap[id].store_index
            }}
          : {{
              topic: btn.getAttribute("data-topic") || "other",
              unit_kinds: attrKinds,
              store_index: parseInt(btn.getAttribute("data-store-index") || String(i), 10)
            }};
        var storeIdx = sig.store_index;
        if (storeIdx === undefined || storeIdx === null || isNaN(storeIdx)) storeIdx = i;
        return {{
          btn: btn,
          score: facetScore(sig, active, catalog),
          store_index: Number(storeIdx)
        }};
      }});
      if (active.length) {{
        decorated.sort(function (a, b) {{
          if (b.score !== a.score) return b.score - a.score;
          return a.store_index - b.store_index;
        }});
      }} else {{
        decorated.sort(function (a, b) {{ return a.store_index - b.store_index; }});
      }}
      decorated.forEach(function (row) {{ root.appendChild(row.btn); }});
      if (status) {{
        if (!active.length) {{
          status.hidden = true;
          status.textContent = "";
        }} else {{
          status.hidden = false;
          status.innerHTML = "<strong>Life facets on</strong> — " + active.join(", ")
             + " <span style='opacity:.85'>(Approaches reorder only · public rank unchanged · w_impact gated · <a href='/methode/facettes.html'>FACETS</a>)</span>";
        }}
      }}
      var box = document.getElementById("life-facets");
      if (box && active.length) box.open = true;
    }}
    function persistFacetsFromUi() {{
      var checks = Array.prototype.slice.call(
        document.querySelectorAll("#facet-toggles input[data-facet]")
      );
      var active = [];
      checks.forEach(function (inp) {{
        if (inp.checked) active.push(inp.getAttribute("data-facet") || "");
      }});
      writeFacets(active.filter(Boolean));
      applyLifeFacets();
    }}
    document.querySelectorAll("#facet-toggles input[data-facet]").forEach(function (inp) {{
      inp.addEventListener("change", persistFacetsFromUi);
    }});
    var btnClearFacets = document.getElementById("facets-clear");
    if (btnClearFacets) btnClearFacets.addEventListener("click", function () {{
      writeFacets([]);
      applyLifeFacets();
    }});
    applyLifeFacets();

    var btnOpen = document.getElementById("btn-open-field");
    if (btnOpen) btnOpen.addEventListener("click", function () {{
      openField();
      var cmd = document.getElementById("command");
      if (cmd) cmd.scrollIntoView({{ behavior: "smooth", block: "start" }});
    }});
    var linkArchArr = document.getElementById("link-archive-arrival");
    if (linkArchArr) linkArchArr.addEventListener("click", function () {{
      openField();
      // The anchor target starts display:none; reveal it and keep the topnav
      // toggle in sync, or the promised Archive never appears.
      var archiveEl = document.getElementById("archive");
      if (archiveEl) archiveEl.classList.add("open");
      var archBtn = document.getElementById("btn-archive");
      if (archBtn) {{
        archBtn.setAttribute("aria-expanded", "true");
        archBtn.textContent = "Close archive";
      }}
    }});
    var linkMethodArr = document.getElementById("link-method-arrival");
    if (linkMethodArr) linkMethodArr.addEventListener("click", function () {{
      var ops = document.getElementById("arrival-ops");
      if (ops) ops.open = true;
    }});

    var fightBtns = Array.prototype.slice.call(document.querySelectorAll(".command-field .fight-list .fight-btn"));
    var approachBtns = Array.prototype.slice.call(document.querySelectorAll(".approaches .approach"));
    var modeRadar = document.getElementById("btn-radar");
    var panels = Array.prototype.slice.call(document.querySelectorAll(".command-field .stage-panel"));
    var radars = Array.prototype.slice.call(document.querySelectorAll(".command-field .impact-radar"));
    var issPrev = document.getElementById("iss-prev");
    var issNext = document.getElementById("iss-next");
    var pos = -1;
    function setActiveBtn(mode, i) {{
      if (modeRadar) {{
        if (mode === "radar") modeRadar.classList.add("active");
        else modeRadar.classList.remove("active");
      }}
      fightBtns.forEach(function (b) {{
        var bi = b.getAttribute("data-i");
        var on = (mode === "fight" && String(i) === bi);
        if (on) b.classList.add("active");
        else b.classList.remove("active");
      }});
      approachBtns.forEach(function (b) {{
        var bi = b.getAttribute("data-i");
        var on = (mode === "fight" && String(i) === bi);
        if (on) b.classList.add("active");
        else b.classList.remove("active");
      }});
    }}
    function syncNav() {{
      if (!panels.length) {{
        if (issPrev) issPrev.disabled = true;
        if (issNext) issNext.disabled = true;
        return;
      }}
      if (pos < 0) {{
        if (issPrev) issPrev.disabled = true;
        if (issNext) issNext.disabled = false;
      }} else {{
        if (issPrev) issPrev.disabled = pos <= 0;
        if (issNext) issNext.disabled = pos >= panels.length - 1;
      }}
    }}
    function showRadar() {{
      pos = -1;
      radars.forEach(function (r) {{ r.removeAttribute("hidden"); }});
      panels.forEach(function (p) {{ p.setAttribute("hidden", ""); }});
      setActiveBtn("radar", -1);
      syncNav();
    }}
    function showIssue(i) {{
      if (!panels.length) {{ showRadar(); return; }}
      openField();
      pos = Math.max(0, Math.min(i, panels.length - 1));
      radars.forEach(function (r) {{ r.setAttribute("hidden", ""); }});
      panels.forEach(function (p, j) {{
        if (j === pos) p.removeAttribute("hidden");
        else p.setAttribute("hidden", "");
      }});
      setActiveBtn("fight", pos);
      syncNav();
      var zone = document.getElementById("issue-stage-zone");
      if (zone) zone.scrollIntoView({{ behavior: "smooth", block: "nearest" }});
    }}
    if (modeRadar) modeRadar.addEventListener("click", function () {{ openField(); showRadar(); }});
    fightBtns.forEach(function (b) {{
      b.addEventListener("click", function () {{
        showIssue(parseInt(b.getAttribute("data-i"), 10));
      }});
    }});
    approachBtns.forEach(function (b) {{
      b.addEventListener("click", function () {{
        showIssue(parseInt(b.getAttribute("data-i"), 10));
      }});
    }});
    if (issPrev) issPrev.addEventListener("click", function () {{ showIssue(pos - 1); }});
    if (issNext) issNext.addEventListener("click", function () {{
      if (pos < 0) showIssue(0);
      else showIssue(pos + 1);
    }});
    showRadar();
    var params = new URLSearchParams(location.search);
    var apParam = params.get("approach");
    if (apParam !== null && apParam !== "") {{
      var apIdx = parseInt(apParam, 10);
      if (!isNaN(apIdx)) showIssue(apIdx);
    }}

    document.querySelectorAll(".moved-pin").forEach(function (a) {{
      a.addEventListener("click", function (ev) {{
        openField();
        var href = a.getAttribute("href") || "";
        if (href.charAt(0) !== "#") return;
        var el = document.querySelector(href);
        if (!el) return;
        ev.preventDefault();
        el.scrollIntoView({{ behavior: "smooth", block: "center" }});
        el.classList.add("pin-flash");
        setTimeout(function () {{ el.classList.remove("pin-flash"); }}, 2200);
      }});
    }});

    var slides = Array.prototype.slice.call(document.querySelectorAll("#deck-slides .slide"));
    var dpos = 0;
    var elPos = document.getElementById("deck-pos");
    var btnPrev = document.getElementById("deck-prev");
    var btnNext = document.getElementById("deck-next");
    function showDeck(i) {{
      if (!slides.length) return;
      dpos = Math.max(0, Math.min(i, slides.length - 1));
      slides.forEach(function (s, j) {{
        if (j === dpos) s.removeAttribute("hidden");
        else s.setAttribute("hidden", "");
      }});
      if (elPos) elPos.textContent = String(dpos + 1);
      if (btnPrev) btnPrev.disabled = dpos === 0;
      if (btnNext) btnNext.disabled = dpos >= slides.length - 1;
    }}
    if (btnPrev) btnPrev.addEventListener("click", function () {{ showDeck(dpos - 1); }});
    if (btnNext) btnNext.addEventListener("click", function () {{ showDeck(dpos + 1); }});
    showDeck(0);

    var archive = document.getElementById("archive");
    var btnArch = document.getElementById("btn-archive");
    if (btnArch && archive) {{
      btnArch.addEventListener("click", function () {{
        openField();
        var open = archive.classList.toggle("open");
        btnArch.setAttribute("aria-expanded", open ? "true" : "false");
        btnArch.textContent = open ? "Close archive" : "Open archive";
        if (open) archive.scrollIntoView({{ behavior: "smooth", block: "start" }});
      }});
    }}
  }})();
  </script>
</body>
</html>
"""

def _load_candidate_store(path: Path) -> list[dict] | None:
    """Read a candidate/enriched store, or None when unreadable/misshapen.

    The primary store is the one input that must never traceback: a truncated
    JSON or a foreign shape (list, scalar entries) falls back to the other
    store, and a clear diagnosis stops the chain if neither is usable.
    """
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None
    if isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        rows = payload.get("candidates")
    else:
        return None
    if not isinstance(rows, list):
        return None
    return [c for c in rows if isinstance(c, dict)]


def main() -> None:
    candidates: list[dict] | None = None
    src: Path | None = None
    for path in (ENRICHED, CANDIDATES):
        if not path.exists():
            continue
        loaded = _load_candidate_store(path)
        if loaded is not None:
            candidates, src = loaded, path
            break
    if candidates is None:
        raise SystemExit(f"No readable candidates in {CANDIDATES}. Run normalize first.")
    print(f"rank input: {src}")
    now = datetime.now(timezone.utc)
    ranked = []
    for c in candidates:
        item = dict(c)
        item["rank_score"] = round(score_item(c, now), 6)
        item["display_geo"] = display_geo(c)
        item["rank_components"] = {
            "geo_proximity": geo_proximity(c),
            "recency": round(recency_score(c, now), 6),
            "w_geo": W_GEO,
            "w_recency": W_RECENCY,
            "w_tension": W_TENSION,
            "w_impact": W_IMPACT,
        }
        ranked.append(item)
    ranked.sort(key=lambda x: x["rank_score"], reverse=True)

    out = {
        "ranked_at": now.isoformat(),
        "method": "ranking.md v0.1 geo+recency; display_geo from enrich when proposed",
        "candidate_count": len(ranked),
        "candidates": ranked,
    }
    store_io.write_json_atomic(OUT_JSON, out)

    issues: list[dict] = []
    ledger: dict = {}
    issues_doc: dict = {}
    if ISSUES.exists():
        try:
            loaded = json.loads(ISSUES.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            loaded = None
        if isinstance(loaded, dict):
            issues_doc = loaded
            issues = loaded.get("issues") if isinstance(loaded.get("issues"), list) else []
            ledger = loaded.get("change_ledger") if isinstance(loaded.get("change_ledger"), dict) else {}
            print(f"issues: {len(issues)} from {ISSUES}")
        else:
            # A corrupt/partial dossier store renders no dossier section instead
            # of aborting the chain after the ranked store was already written.
            print(f"issues: unreadable store {ISSUES} - rendering without dossiers")

    # Official roadworks store: structured change data, never ranked with articles.
    # A missing or corrupt store renders no section rather than failing the edition.
    roadworks: dict = {}
    if ROADWORKS.exists():
        try:
            loaded = json.loads(ROADWORKS.read_text(encoding="utf-8"))
            roadworks = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError, RecursionError):
            roadworks = {}
        if roadworks:
            print(f"roadworks: {len(roadworks.get('events') or [])} active events from {ROADWORKS}")

    # Official participation calendar: HTML table, never ranked with articles.
    civic: dict = {}
    if CIVIC.exists():
        try:
            loaded = json.loads(CIVIC.read_text(encoding="utf-8"))
            civic = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError, RecursionError):
            civic = {}
        if civic:
            print(f"civic: {len(civic.get('events') or [])} consultations from {CIVIC}")

    # Sidecar stores compiled from the official collection: street-level joins
    # (edge-atlas-v1) and fixed-threshold anomaly verdicts (anomaly-beacon-v1).
    # Missing, corrupt or foreign-method stores render nothing rather than fail.
    def _load_sidecar(path: Path) -> dict:
        if not path.exists():
            return {}
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, RecursionError):
            return {}
        return loaded if isinstance(loaded, dict) else {}

    edges = _load_sidecar(EDGES)
    anomalies = _load_sidecar(ANOMALIES)
    if edges:
        print(f"edges: {edges.get('street_count', 0)} streets, "
              f"{edges.get('matched_issue_count', 0)} matched dossiers from {EDGES}")
    if anomalies:
        print(f"anomalies: {anomalies.get('anomaly_count', 0)} measured from {ANOMALIES}")

    OUT_HTML.parent.mkdir(parents=True, exist_ok=True)
    clock = load_clock(now.isoformat())
    # Keep the experimental evidence workbench accessible without making
    # residents learn its vocabulary before reading their local news.
    import resident_brief

    store_io.write_text_atomic(
        OUT_HTML.parent / "explorer.html",
        render_html(ranked, now.isoformat(), issues, clock),
    )
    store_io.write_text_atomic(
        OUT_HTML,
        resident_brief.render_brief(ranked, now.isoformat(), issues, ledger=ledger,
                                    roadworks=roadworks, anomalies=anomalies, edges=edges,
                                    civic=civic),
    )
    near = sum(1 for c in ranked if section_for(c) == "near")
    prov = sum(1 for c in ranked if section_for(c) == "province")
    linked = sum(1 for c in ranked if section_for(c) == "linked")
    print(f"ranked {len(ranked)} -> {OUT_JSON}")
    print(f"lookout -> {OUT_HTML}")
    print(f"nests: near={near} province={prov} linked={linked}")
    print(f"display: province/linked cap={30} (full set in ranked JSON)")
    booth_n = sum(1 for c in ranked if section_for(c) == "near" and is_booth_or_brief(c))
    print(f"display: booth/brief demoted from Near me to Province: {booth_n}")
    print(f"clock: {clock_line(clock)}")
    if ranked:
        print("top:", str(ranked[0].get("title") or "")[:100], ranked[0]["rank_score"])

    # Ambient twin — same store Approaches; never a second ranking.
    import ambient_pulse

    ambient_pulse.emit_from_store(
        issues=issues,
        ranked=ranked,
        ranked_at=now.isoformat(),
    )

    # The record layer: the sealed registre (edition chain + voice register),
    # the machine substrate (llms.txt, Markdown twin, delta) and the printable
    # affiche. Same stores, no second brain. Idempotent on the collection clock,
    # so the hourly roads-only re-render never mints a new edition seal. A fault
    # here is diagnosed and reported; it never blocks the brief.
    import affiche
    import depart
    import memoire
    import method_site
    import recits
    import registre
    import substrate

    # Each emitter is independently fail-soft: one fault is printed and the
    # others still run, so a registre fault can never leave a fresh brief
    # beside a stale memory/index (the "all seven are fail-soft" house law).
    def _emit(name, fn):
        try:
            return fn()
        except Exception as exc:  # diagnosed, never silent
            print(f"{name}: FAILED ({type(exc).__name__}: {exc}); brief still rendered")
            return None

    state = _emit("registre", lambda: registre.emit(issues_doc, roadworks))
    if not isinstance(state, dict):
        # Registre failed: render the rest from the on-disk (last good) state
        # instead of skipping every later emitter.
        state = registre.load_state()
    _emit("memoire", lambda: memoire.emit(state, issues))
    _emit("substrate", lambda: substrate.emit(ranked, issues, ledger, roadworks, state, now.isoformat()))
    _emit("recits", lambda: recits.emit(issues, ranked, ledger, roadworks, edges))
    _emit("methode", method_site.emit)
    _emit("depart", lambda: depart.emit(roadworks, issues, ledger, state, now.isoformat()))
    _emit("affiche", lambda: affiche.emit(ranked, issues, roadworks, state, now.isoformat()))


if __name__ == "__main__":
    main()
