"""Vigie v0 — Edge Atlas: literal street-level joins (method edge-atlas-v1).

The official roadworks feed names streets; the proposed dossiers talk about
streets. The atlas normalizes declared street names into canonical keys and
matches them LITERALLY against dossier text: a shared street name is a
proposed relation, never geographic proof. No fuzzy matching, no inference,
no model — every join a resident sees is reproducible by reading the two
texts side by side.

Discipline mirrors the roadworks store: stdlib only; deterministic (no wall
clock — stamps come from the stores themselves, so a render-only rebuild is
byte-identical); fail-soft (an absent, corrupt or foreign-method store yields
an empty atlas and exit 0, never a dead pipeline); forward-only data (the
atlas is rebuilt from scratch each edition, it accumulates nothing).

Geometry (per-street centroid) is joined by event id from the latest append-only
raw GeoJSON snapshot; when the snapshot is unavailable the atlas still compiles
from declared road_names alone, with null centroids.
"""
from __future__ import annotations

import itertools
import json
import math
import re
import sys
import unicodedata
from collections import Counter
from pathlib import Path

import ingest_wzdx
import store_io

ROOT = Path(__file__).resolve().parents[1]
ROADWORKS = ROOT / "data" / "roadworks" / "latest_roadworks.json"
ISSUES = ROOT / "data" / "issues" / "latest_issues.json"
RAW_DIR = ROOT / "data" / "raw"
OUT_PATH = ROOT / "data" / "edges" / "latest_edges.json"

METHOD = "edge-atlas-v1"
ROADWORKS_METHOD = ingest_wzdx.METHOD
EVENT_IDS_CAP = 12        # evidence ids kept per street; counts stay exact
MATCHED_ISSUES_CAP = 5    # dossiers kept per street; the index stays exact
ISSUE_STREETS_CAP = 5     # streets kept per dossier
PHRASE_VARIANTS_CAP = 8   # match phrases per street
PHRASE_TOKEN_CAP = 6      # tokens beyond this skip variant expansion
PHRASE_PRODUCT_CAP = 64   # cartesian budget before falling back to the base form
PHRASE_MIN_LEN = 6        # shorter phrases are too ambiguous to match literally
TEXT_CAP = 6000           # dossier text scanned per issue
CENTROID_PRECISION = 5    # ~1 m; keeps the store deterministic

# Canonical descriptors. The feed writes full forms ("Boulevard", "Rue",
# "RTE-175"); news text may abbreviate. Both sides fold to the same token.
DESCRIPTOR_MAP = {
    "boulevard": "boulevard", "boul": "boulevard", "blvd": "boulevard", "bd": "boulevard",
    "rue": "rue", "r": "rue",
    "avenue": "avenue", "av": "avenue", "ave": "avenue",
    "chemin": "chemin", "ch": "chemin", "chm": "chemin",
    "route": "route", "rt": "route", "rte": "route",
    "autoroute": "autoroute", "aut": "autoroute",
    "cote": "cote", "quai": "quai", "place": "place", "carre": "carre",
    "lien": "lien", "ruelle": "ruelle", "pont": "pont", "pont-tunnel": "pont-tunnel",
    "allee": "allee", "terrasse": "terrasse", "impasse": "impasse", "mail": "mail",
}
# Hyphenated feed forms: RTE-175 -> route-175, AUT-740 -> autoroute-740.
HYPHEN_DESCRIPTORS = {"rte": "route", "rt": "route", "aut": "autoroute"}
# Trailing compass tokens on declared names ("76e Rue O", "Boulevard Charest
# Est"). Full words fold to the single letter — in street names only; in free
# text "est" stays a French word.
DIRECTIONS = frozenset({"o", "e", "n", "s"})
DIRECTION_WORDS = {"ouest": "o", "est": "e", "nord": "n", "sud": "s"}
_APOSTROPHES = re.compile(r"['\u2018\u2019\u02bc`]")


def fold(text: object) -> str:
    """Accent-folded, case-folded text. Deterministic; never locale-dependent."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", str(text or "").casefold())
        if not unicodedata.combining(ch)
    )


def canon_token(token: object) -> str:
    """One whitespace token -> canonical form.

    Folds accents and case, maps apostrophes to hyphens (l'Église -> l-eglise),
    canonicalizes descriptors (boul. -> boulevard, RTE-175 -> route-175) and
    contracts saint/sainte (Sainte-Foy -> ste-foy). Compass words are NOT
    mapped here: "est" is also a French word, so direction handling belongs to
    street-name parsing only, never to free-text folding.
    """
    tok = _APOSTROPHES.sub("-", fold(token)).replace(".", "").strip("-")
    if not tok:
        return ""
    if tok in DESCRIPTOR_MAP:
        return DESCRIPTOR_MAP[tok]
    head, sep, tail = tok.partition("-")
    if sep and tail and head in HYPHEN_DESCRIPTORS:
        return f"{HYPHEN_DESCRIPTORS[head]}-{tail}"
    if tok.startswith("saint-"):
        return "st-" + tok[len("saint-"):]
    if tok.startswith("sainte-"):
        return "ste-" + tok[len("sainte-"):]
    if tok == "saint":
        return "st"
    if tok == "sainte":
        return "ste"
    return tok


def street_parts(raw: object) -> tuple[list[str], str | None]:
    """Declared street name -> (canonical base tokens, direction token | None)."""
    if not isinstance(raw, str):
        return [], None
    tokens = [canon_token(t) for t in raw.split()]
    tokens = [t for t in tokens if t]
    if not tokens:
        return [], None
    direction = None
    if len(tokens) >= 2:
        last = tokens[-1]
        if last in DIRECTIONS:
            direction = last
            tokens = tokens[:-1]
        elif last in DIRECTION_WORDS:
            direction = DIRECTION_WORDS[last]
            tokens = tokens[:-1]
    return tokens, direction


def street_key(raw: object) -> str | None:
    """Canonical street identity. Direction stays part of the key: Rue
    St-Joseph E and Rue St-Joseph O are different declared edges."""
    tokens, direction = street_parts(raw)
    if not tokens:
        return None
    return "-".join(tokens + ([direction] if direction else []))


def _token_variants(raw_token: str, canon: str) -> list[str]:
    """Match variants for one canonical token: the canonical form, its spaced
    form when hyphenated, and the folded original (RTE-175 keeps matching
    text that writes "rte-175" or "route 175")."""
    variants = {canon}
    folded_original = _APOSTROPHES.sub("-", fold(raw_token)).strip("-")
    if folded_original:
        variants.add(folded_original)
    out = set()
    for variant in variants:
        out.add(variant)
        if "-" in variant:
            out.add(variant.replace("-", " "))
    return sorted(out)


def phrase_variants(raw: object) -> list[str]:
    """Literal spaced phrases that may match folded dossier text.

    Includes the full declared form (with direction) and the directionless
    base, so "boulevard René-Lévesque Ouest" in prose matches the declared
    "Boulevard René-Lévesque O" through the base phrase. Deterministic order.
    """
    if not isinstance(raw, str):
        return []
    raw_tokens = [t for t in raw.split() if t.strip()]
    tokens, direction = street_parts(raw)
    if not tokens:
        return []
    # Align canonical tokens with their raw origin for variant generation.
    canon_aligned: list[str] = []
    raw_aligned: list[str] = []
    for raw_token in raw_tokens:
        canon = canon_token(raw_token)
        if not canon:
            continue
        canon_aligned.append(canon)
        raw_aligned.append(raw_token)
    if direction:
        canon_aligned = canon_aligned[:-1]
        raw_aligned = raw_aligned[:-1]
    per_token = [_token_variants(rt, ct) for rt, ct in zip(raw_aligned, canon_aligned, strict=False)]
    # Bound the cartesian product before materializing it: a hostile or absurd
    # multi-token road name would otherwise form 4^N phrases (16 tokens ~ hours
    # and gigabytes). The base canonical phrase is always kept.
    budget = math.prod(len(v) for v in per_token)
    if len(per_token) > PHRASE_TOKEN_CAP or budget > PHRASE_PRODUCT_CAP:
        combos = [" ".join(canon_aligned)]
    else:
        combos = [" ".join(combo) for combo in itertools.product(*per_token)]
    if direction:
        combos += [f"{combo} {direction}" for combo in combos]
    phrases = sorted(
        {c for c in combos if len(c) >= PHRASE_MIN_LEN},
        key=lambda p: (-len(p), p),
    )
    return phrases[:PHRASE_VARIANTS_CAP]


def fold_text(text: object) -> str:
    """Dossier text -> canonical folded form, token by token, so the same
    normalizer serves both sides of the join."""
    capped = str(text or "")[:TEXT_CAP]
    capped = _APOSTROPHES.sub("-", fold(capped))
    return " ".join(t for t in (canon_token(tok) for tok in capped.split()) if t)


def build_matcher(street_phrases: dict[str, list[str]]) -> tuple[re.Pattern, dict[str, set[str]]]:
    """One literal alternation over all street phrases, longest first.

    Boundaries reject hyphen-continuations: "rue st-jean" must not match
    inside "rue Saint-Jean-Baptiste", and "3e rue" must not match "13e rue".
    """
    phrase_to_keys: dict[str, set[str]] = {}
    for key, phrases in street_phrases.items():
        for phrase in phrases:
            phrase_to_keys.setdefault(phrase, set()).add(key)
    ordered = sorted(phrase_to_keys, key=lambda p: (-len(p), p))
    pattern = re.compile(
        "|".join(f"(?<![\\w-]){re.escape(p)}(?![\\w-])" for p in ordered)
    ) if ordered else re.compile(r"(?!)")
    return pattern, phrase_to_keys


def streets_in_text(text: object, pattern: re.Pattern, phrase_to_keys: dict[str, set[str]]) -> list[str]:
    folded = fold_text(text)
    keys: set[str] = set()
    for match in pattern.finditer(folded):
        keys.update(phrase_to_keys.get(match.group(0), ()))
    return sorted(keys)[:ISSUE_STREETS_CAP]


def load_geometry(raw_dir: Path, source_id: object) -> dict[str, list[tuple[float, float]]]:
    """event_id -> (lon, lat) points from the latest append-only raw snapshot.
    A missing or corrupt snapshot yields no geometry; the atlas still compiles."""
    snapshot = ingest_wzdx.latest_snapshot(raw_dir, str(source_id or ""))
    if snapshot is None:
        return {}
    try:
        doc = json.loads(snapshot.read_bytes())
    except (OSError, ValueError, RecursionError):
        # Deeply nested JSON raises RecursionError, not ValueError. The raw
        # snapshot is archived before parsing, so a hostile roadworks document
        # must degrade to "no geometry" instead of killing the whole chain.
        return {}
    features = doc.get("features") if isinstance(doc, dict) else None
    geometry: dict[str, list[tuple[float, float]]] = {}
    for feature in features if isinstance(features, list) else []:
        if not isinstance(feature, dict):
            continue
        event_id = str(feature.get("id") or "").strip()
        points = ingest_wzdx._points(feature.get("geometry"))
        if event_id and points:
            geometry[event_id] = points
    return geometry


def _display_name(variant_counts: dict[str, int]) -> str:
    """Most frequent declared spelling; ties break lexicographically."""
    if not variant_counts:
        return ""
    return sorted(variant_counts.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def build_streets(events: list[dict], geometry: dict | None = None) -> dict[str, dict]:
    """Canonical street key -> declared facts from this collection only."""
    geometry = geometry if isinstance(geometry, dict) else {}
    per_key: dict[str, dict] = {}
    for event in events:
        if not isinstance(event, dict):
            continue
        event_id = str(event.get("event_id") or "").strip()
        if not event_id:
            continue
        seen: set[str] = set()
        for raw in event.get("road_names") or []:
            key = street_key(raw)
            if not key or key in seen:
                continue
            seen.add(key)
            rec = per_key.setdefault(
                key, {"event_ids": [], "variant_counts": Counter(), "phrases": set(), "points": []}
            )
            rec["event_ids"].append(event_id)
            raw_str = str(raw).strip()
            rec["variant_counts"][raw_str] += 1
            rec["phrases"].update(phrase_variants(raw_str))
            points = geometry.get(event_id)
            if points:
                rec["points"].extend(points)
    streets: dict[str, dict] = {}
    for key in sorted(per_key):
        rec = per_key[key]
        centroid = None
        if rec["points"]:
            centroid = {
                "lat": round(sum(p[1] for p in rec["points"]) / len(rec["points"]), CENTROID_PRECISION),
                "lon": round(sum(p[0] for p in rec["points"]) / len(rec["points"]), CENTROID_PRECISION),
            }
        streets[key] = {
            "display": _display_name(rec["variant_counts"]),
            "active_count": len(rec["event_ids"]),
            "centroid": centroid,
            "event_ids": sorted(rec["event_ids"])[:EVENT_IDS_CAP],
            "matched_issue_ids": [],
            "phrases": rec["phrases"],
        }
    return streets


def issue_blob(issue: dict) -> str:
    """All declared dossier text Vigie may match against: the question, the
    label headline, and member titles/summaries. Never invented text."""
    parts = [str(issue.get("question") or "")]
    label_source = issue.get("label_source")
    if isinstance(label_source, dict):
        parts.append(str(label_source.get("title") or ""))
    for tension in issue.get("tensions") or []:
        if not isinstance(tension, dict):
            continue
        for item in tension.get("items") or []:
            if not isinstance(item, dict):
                continue
            parts.append(str(item.get("title") or ""))
            parts.append(str(item.get("summary") or ""))
    return " ".join(parts)[:TEXT_CAP]


def compile_atlas(rw: dict, issues_doc: dict | None = None, geometry: dict | None = None) -> dict:
    """Pure: stores in, atlas out. No clock, no I/O, no randomness."""
    events = [e for e in (rw.get("events") or []) if isinstance(e, dict) and e.get("event_id")]
    streets = build_streets(events, geometry)
    pattern, phrase_to_keys = build_matcher({k: sorted(v["phrases"]) for k, v in streets.items()})
    issues = [
        i for i in ((issues_doc or {}).get("issues") or [])
        if isinstance(i, dict) and i.get("issue_id")
    ]
    issue_index: dict[str, dict] = {}
    for issue in issues:
        issue_id = str(issue["issue_id"])
        keys = [k for k in streets_in_text(issue_blob(issue), pattern, phrase_to_keys) if k in streets]
        if not keys:
            continue
        issue_index[issue_id] = {"streets": keys}
        for key in keys:
            matched = streets[key]["matched_issue_ids"]
            if issue_id not in matched and len(matched) < MATCHED_ISSUES_CAP:
                matched.append(issue_id)
    for rec in streets.values():
        rec.pop("phrases", None)
        rec["matched_issue_ids"] = sorted(rec["matched_issue_ids"])
    return {
        "method": METHOD,
        "status": "proposed",
        "built_at": _latest_stamp(rw.get("fetched_at"),
                                  (issues_doc or {}).get("clustered_at")),
        "issues_clustered_at": (issues_doc or {}).get("clustered_at"),
        "source_id": rw.get("source_id"),
        "street_count": len(streets),
        "streets": {k: streets[k] for k in sorted(streets)},
        "issues": {k: issue_index[k] for k in sorted(issue_index)},
        "matched_issue_count": len(issue_index),
        "note": (
            "Rapprochement littéral par nom de rue normalisé : une mention textuelle "
            "partagée, pas une preuve géographique. Les seuils et la grammaire de "
            "normalisation sont publiés dans edge.md."
        ),
    }


def empty_atlas(note: str) -> dict:
    return {
        "method": METHOD, "status": "proposed", "built_at": None,
        "issues_clustered_at": None, "source_id": None, "street_count": 0,
        "streets": {}, "issues": {}, "matched_issue_count": 0, "note": note,
    }


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def _latest_stamp(*values: object) -> str | None:
    """The newest input stamp (data time), never the build clock. A compile
    that consumed two stores must not claim to predate one of them."""
    parsed = [(dt, str(value)) for dt, value in
              ((ingest_wzdx._parse_iso(value), value) for value in values) if dt is not None]
    return max(parsed)[1] if parsed else None


def main(argv: list[str] | None = None) -> int:
    try:
        rw = _load_json(ROADWORKS)
        if not isinstance(rw, dict) or rw.get("method") != ROADWORKS_METHOD:
            atlas = empty_atlas(
                "Aucune collecte officielle exploitable (données absentes, corrompues "
                "ou d'une autre méthode) : aucun rapprochement proposé."
            )
        else:
            issues_doc = _load_json(ISSUES)
            geometry = load_geometry(RAW_DIR, rw.get("source_id"))
            atlas = compile_atlas(rw, issues_doc if isinstance(issues_doc, dict) else {}, geometry)
        store_io.write_json_atomic(OUT_PATH, atlas)
        try:
            shown = OUT_PATH.relative_to(ROOT)
        except ValueError:
            shown = OUT_PATH
        print(f"edge atlas: {atlas['street_count']} street(s), "
              f"{atlas['matched_issue_count']} matched dossier(s) -> {shown}")
    except (OSError, ValueError, TypeError, RecursionError) as exc:
        # Always 0: the atlas is a reading aid; its absence never blocks an
        # edition, and an unwritable store must not kill the refresh chain.
        print(f"edge atlas: FAIL {type(exc).__name__}: {exc} - keeping previous store")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
