"""Life facets — opt-in Approaches reorder only.

House law:
- Facets are declared in FACETS.md and this catalog. Never silent.
- Reorder Approaches presentation only. Never mutate latest_ranked / score_item.
- Never filter Approaches away. Never touch w_impact / w_geo / w_*.
- Default = store order (no facets active).

Method string: life-facets-v0.1
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any

METHOD = "life-facets-v0.1 approaches-reorder-only"
METHOD_FILE = "FACETS.md"

# Published catalog — keep in lockstep with FACETS.md.
# topic_weight / unit_weight are presentation boosts, not ranking.md weights.
FACET_CATALOG: list[dict[str, Any]] = [
    {
        "id": "renter",
        "label": "Renter",
        "blurb": "Housing and rent life",
        "topics": ["housing"],
        "unit_kinds": ["price", "housing_count"],
        "topic_weight": 1.0,
        "unit_weight": 0.35,
    },
    {
        "id": "transit",
        "label": "Transit",
        "blurb": "Tram, bus, metro life",
        "topics": ["transport"],
        "unit_kinds": [],
        "topic_weight": 1.0,
        "unit_weight": 0.0,
    },
    {
        "id": "energy",
        "label": "Energy",
        "blurb": "Hydro, fuel, power rates",
        "topics": ["energy/hydro", "energy", "hydro"],
        "unit_kinds": ["price"],
        "topic_weight": 1.0,
        "unit_weight": 0.35,
    },
    {
        "id": "civic",
        "label": "Civic",
        "blurb": "Bylaws and law that bind the city",
        "topics": ["law"],
        "unit_kinds": ["bylaw_id"],
        "topic_weight": 1.0,
        "unit_weight": 0.35,
    },
    {
        "id": "health",
        "label": "Health",
        "blurb": "Care and hospitals",
        "topics": ["health"],
        "unit_kinds": [],
        "topic_weight": 1.0,
        "unit_weight": 0.0,
    },
]

FACET_BY_ID = {str(f["id"]): f for f in FACET_CATALOG}


def normalize_facet_ids(raw: list[str] | None) -> list[str]:
    """Keep declared ids only, stable unique order as in FACET_CATALOG."""
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return []
    wanted = {str(x).strip() for x in raw if str(x).strip()}
    return [f["id"] for f in FACET_CATALOG if f["id"] in wanted]


def issue_topic(iss: dict | None) -> str:
    if not isinstance(iss, dict):
        return "other"
    topic_obj = iss.get("topic") or {}
    if isinstance(topic_obj, dict):
        return str(topic_obj.get("topic") or "other")
    return str(topic_obj or "other")


def unit_kinds_of(approach: dict) -> list[str]:
    kinds: list[str] = []
    seen: set[str] = set()
    if not isinstance(approach, dict):
        return kinds
    for u in approach.get("units") or []:
        if not isinstance(u, dict):
            continue
        k = str(u.get("kind") or "").strip()
        if k and k not in seen:
            seen.add(k)
            kinds.append(k)
    return kinds


def annotate_approaches(approaches: list[dict], issues: list[dict] | None = None) -> list[dict]:
    """Attach topic + unit_kinds for facet scoring. Does not reorder."""
    by_id: dict[str, dict] = {}
    if not isinstance(approaches, (list, tuple)):
        return []
    for iss in (issues if isinstance(issues, (list, tuple)) else []):
        if not isinstance(iss, dict):
            continue
        iid = str(iss.get("issue_id") or iss.get("scar") or "")
        if iid:
            by_id[iid] = iss
    out: list[dict] = []
    for ap in approaches:
        if not isinstance(ap, dict):
            continue
        row = dict(ap)
        iid = str(row.get("issue_id") or "")
        if "topic" not in row or not row.get("topic"):
            row["topic"] = issue_topic(by_id.get(iid))
        row["unit_kinds"] = unit_kinds_of(row)
        try:
            default_index = int(row.get("index")) if row.get("index") is not None else len(out)
        except (TypeError, ValueError):
            default_index = len(out)
        row["store_index"] = default_index
        out.append(row)
    return out


def facet_score(approach: dict, active_facets: list[str]) -> float:
    """Presentation score only — never a rank_score component."""
    ids = normalize_facet_ids(active_facets)
    if not ids:
        return 0.0
    if not isinstance(approach, dict):
        return 0.0
    topic = str(approach.get("topic") or "other")
    kinds = set(approach.get("unit_kinds") or unit_kinds_of(approach))
    score = 0.0
    for fid in ids:
        facet = FACET_BY_ID.get(fid)
        if not facet:
            continue
        topics = {str(t) for t in (facet.get("topics") or [])}
        if topic in topics:
            score += float(facet.get("topic_weight") or 0.0)
        unit_kinds = {str(k) for k in (facet.get("unit_kinds") or [])}
        if unit_kinds and kinds.intersection(unit_kinds):
            score += float(facet.get("unit_weight") or 0.0)
    return round(score, 6)


def reorder_approaches(
    approaches: list[dict],
    active_facets: list[str] | None,
    *,
    issues: list[dict] | None = None,
) -> list[dict]:
    """Reorder a copy of Approaches by facet score. Same set. Store index tie-break.

    Empty / unknown facets → original store order (annotated copy).
    Never drops a row: a malformed (non-object) row cannot be scored and keeps
    its original relative order at the end. Never mutates input list objects.
    """
    annotated = annotate_approaches(approaches, issues)
    others = [ap for ap in approaches if not isinstance(ap, dict)] if isinstance(approaches, (list, tuple)) else []
    ids = normalize_facet_ids(active_facets)
    if not ids:
        return annotated + others
    decorated = [
        (
            -facet_score(ap, ids),
            int(ap.get("store_index") if ap.get("store_index") is not None else i),
            ap,
        )
        for i, ap in enumerate(annotated)
    ]
    decorated.sort(key=lambda t: (t[0], t[1]))
    return [deepcopy(t[2]) for t in decorated] + others


def same_approach_set(before: list[dict], after: list[dict]) -> bool:
    """Identity: same issue_ids as a multiset — reorder only."""
    def ids(rows: list[dict] | None) -> list[str]:
        if not isinstance(rows, (list, tuple)):
            return []
        return sorted(str(x.get("issue_id") or "") for x in rows if isinstance(x, dict))

    return ids(before) == ids(after)


def catalog_payload() -> dict:
    """Embeddable method payload for Arrival + tests."""
    return {
        "method": METHOD,
        "method_file": METHOD_FILE,
        "note": (
            "Opt-in life facets reorder Approaches only. "
            "Public rank weights unchanged. w_impact stays gated."
        ),
        "facets": FACET_CATALOG,
        "formula": (
            "score = sum_F (topic_weight if topic in F.topics) "
            "+ (unit_weight if any unit.kind in F.unit_kinds); "
            "sort by (-score, store_index). No filter."
        ),
    }


def signals_payload(approaches: list[dict], issues: list[dict] | None = None) -> dict:
    """Per-approach signals for client reorder — store facts only."""
    annotated = annotate_approaches(approaches, issues)
    return {
        "method": METHOD,
        "approaches": [
            {
                "issue_id": a.get("issue_id"),
                "store_index": int(a.get("store_index") if a.get("store_index") is not None else i),
                "topic": a.get("topic") or "other",
                "unit_kinds": list(a.get("unit_kinds") or []),
            }
            for i, a in enumerate(annotated)
        ],
    }


def parse_facets_md_rows(md_text: str) -> list[dict]:
    """Parse published catalog table from FACETS.md — lockstep guard."""
    if not isinstance(md_text, str):
        return []
    rows: list[dict] = []
    in_catalog = False
    for line in md_text.splitlines():
        if line.strip().startswith("## Catalog"):
            in_catalog = True
            continue
        if in_catalog and line.startswith("## "):
            break
        if not in_catalog or not line.strip().startswith("|"):
            continue
        parts = [p.strip() for p in line.strip().strip("|").split("|")]
        if len(parts) < 6:
            continue
        fid = parts[0]
        if not fid or fid == "id" or fid.startswith("-") or set(fid) <= {"-"}:
            continue
        topics_raw = parts[2]
        units_raw = parts[3]
        topics = (
            []
            if topics_raw in ("—", "-", "–", "")
            else [t.strip() for t in topics_raw.split(",") if t.strip()]
        )
        unit_kinds = (
            []
            if units_raw in ("—", "-", "–", "")
            else [u.strip() for u in units_raw.split(",") if u.strip()]
        )
        try:
            topic_weight = float(parts[4])
            unit_weight = float(parts[5])
        except ValueError:
            continue
        rows.append(
            {
                "id": fid,
                "label": parts[1],
                "topics": topics,
                "unit_kinds": unit_kinds,
                "topic_weight": topic_weight,
                "unit_weight": unit_weight,
            }
        )
    return rows


def catalog_matches_facets_md(md_text: str) -> tuple[bool, list[str]]:
    """True when FACET_CATALOG weights/topics match published FACETS.md table."""
    errors: list[str] = []
    rows = parse_facets_md_rows(md_text)
    md_ids = [r["id"] for r in rows]
    code_ids = [f["id"] for f in FACET_CATALOG]
    if md_ids != code_ids:
        errors.append(f"id order/set md={md_ids} code={code_ids}")
    by_md = {r["id"]: r for r in rows}
    for f in FACET_CATALOG:
        fid = f["id"]
        row = by_md.get(fid)
        if not row:
            errors.append(f"missing md row for {fid}")
            continue
        if list(f.get("topics") or []) != list(row.get("topics") or []):
            errors.append(f"{fid} topics md={row.get('topics')} code={f.get('topics')}")
        if list(f.get("unit_kinds") or []) != list(row.get("unit_kinds") or []):
            errors.append(
                f"{fid} unit_kinds md={row.get('unit_kinds')} code={f.get('unit_kinds')}"
            )
        if float(f.get("topic_weight") or 0) != float(row.get("topic_weight") or 0):
            errors.append(f"{fid} topic_weight mismatch")
        if float(f.get("unit_weight") or 0) != float(row.get("unit_weight") or 0):
            errors.append(f"{fid} unit_weight mismatch")
        if str(f.get("label") or "") != str(row.get("label") or ""):
            errors.append(f"{fid} label mismatch")
    return (not errors, errors)


def w_impact_still_gated() -> bool:
    """Facets must never open the impact ranking gate."""
    import rank_display

    return float(rank_display.W_IMPACT) == 0.0
