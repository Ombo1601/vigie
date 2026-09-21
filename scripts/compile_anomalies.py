"""Vigie v0 — anomaly beacon compile (method anomaly-beacon-v1).

Fixed-threshold rules over the official roadworks collection, published in
anomalies.md. An anomaly is a measured collection fact — the City's own
declarations counted by declared rules — never a prediction, never an
importance judgment, never a model. Every verdict carries its rule id, its
thresholds, the counted evidence ids, and a French claim a resident can
verify against the official feed.

Discipline: stdlib only; deterministic (the verdict stamp is the collection
stamp — no wall clock, so a render-only rebuild is byte-identical); fail-soft
(an absent, corrupt or foreign-method store yields an empty verdict and exit
0); counts are exact even when the evidence list is capped.
"""
from __future__ import annotations

import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import ingest_wzdx
import store_io
from edge_atlas import street_key

ROOT = Path(__file__).resolve().parents[1]
ROADWORKS = ROOT / "data" / "roadworks" / "latest_roadworks.json"
OUT_PATH = ROOT / "data" / "anomalies" / "latest_verdict.json"

METHOD = "anomaly-beacon-v1"
ROADWORKS_METHOD = ingest_wzdx.METHOD
ANOMALIES_CAP = 6     # verdict rows kept; the count stays exact
EVIDENCE_CAP = 8      # event ids cited per anomaly; the count stays exact

# The finite rule catalogue. Adding or changing a rule means amending
# anomalies.md first — the tests lock this table against the published file.
RULES = {
    "poussee-declarations": {
        "label": "Poussée de déclarations", "rank": 0, "min_count": 3,
    },
    "fin-reportee": {
        "label": "Fins reportées", "rank": 1, "min_count": 2,
    },
    "concentration": {
        "label": "Concentration", "rank": 2, "min_count": 8, "min_ratio": 4.0,
    },
    "fenetre-depassee": {
        "label": "Fenêtre officielle dépassée", "rank": 3, "min_count": 3,
    },
}


def _num_fr(value: float) -> str:
    """French number wording: integer when whole, one decimal with a comma."""
    number = float(value)
    if number.is_integer():
        return str(int(number))
    return f"{number:.1f}".replace(".", ",")


class _StreetGroup:
    """One street across counted entries: exact ids + declared spellings."""

    __slots__ = ("event_ids", "variants")

    def __init__(self) -> None:
        self.event_ids: list[str] = []
        self.variants: Counter = Counter()

    def add(self, entry: dict) -> None:
        event_id = str(entry.get("event_id") or "").strip()
        if event_id and event_id not in self.event_ids:
            self.event_ids.append(event_id)

    def display(self) -> str:
        if not self.variants:
            return "voie non précisée"
        return sorted(self.variants.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]


def _group_by_street(entries: list, predicate=None) -> dict[str, _StreetGroup]:
    groups: dict[str, _StreetGroup] = {}
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        if predicate is not None and not predicate(entry):
            continue
        for raw in entry.get("road_names") or []:
            key = street_key(raw)
            if not key:
                continue
            group = groups.setdefault(key, _StreetGroup())
            group.add(entry)
            group.variants[str(raw).strip()] += 1
    return groups


def _anomaly(rule_id: str, key: str, group: _StreetGroup, claim: str, **extra) -> dict:
    rule = RULES[rule_id]
    return {
        "rule_id": rule_id,
        "rule_label": rule["label"],
        "street_key": key,
        "street_display": group.display(),
        "count": len(group.event_ids),
        "claim": claim,
        "evidence_event_ids": sorted(group.event_ids)[:EVIDENCE_CAP],
        "status": "proposed",
        **extra,
    }


def rule_poussee(diff: dict) -> list[dict]:
    """Many new declarations on one street inside a single collection."""
    if not diff.get("has_previous"):
        return []
    threshold = RULES["poussee-declarations"]["min_count"]
    out = []
    for key, group in _group_by_street(diff.get("new") or []).items():
        n = len(group.event_ids)
        if n >= threshold:
            # The threshold guarantees n >= 2: the claim is always plural.
            out.append(_anomaly(
                "poussee-declarations", key, group,
                f"{n} nouvelles entraves déclarées sur {group.display()} "
                "dans cette même collecte.",
                threshold=threshold,
            ))
    return out


def rule_fin_reportee(diff: dict) -> list[dict]:
    """The City's own end-date revisions toward later, relayed literally."""
    if not diff.get("has_previous"):
        return []
    threshold = RULES["fin-reportee"]["min_count"]
    entries = [
        e for e in (diff.get("changed") or [])
        if isinstance(e, dict) and e.get("end_date_moved") == "later"
    ]
    out = []
    for key, group in _group_by_street(entries).items():
        n = len(group.event_ids)
        if n >= threshold:
            out.append(_anomaly(
                "fin-reportee", key, group,
                f"La Ville a reporté la fin déclarée de {n} entraves "
                f"sur {group.display()} depuis la dernière collecte.",
                threshold=threshold,
            ))
    return out


def rule_concentration(events: list[dict]) -> list[dict]:
    """One street carries far more active declarations than the network median."""
    groups = _group_by_street(events)
    if not groups:
        return []
    counts = {k: len(g.event_ids) for k, g in groups.items()}
    median = statistics.median(counts.values())
    if median <= 0:
        return []
    rule = RULES["concentration"]
    out = []
    for key, group in groups.items():
        n = len(group.event_ids)
        if n >= rule["min_count"] and n >= rule["min_ratio"] * median:
            ratio = round(n / median, 1)
            out.append(_anomaly(
                "concentration", key, group,
                f"{group.display()} concentre {n} entraves actives déclarées "
                f"— {_num_fr(ratio)}× la médiane du réseau ({_num_fr(median)}).",
                threshold=rule["min_count"], min_ratio=rule["min_ratio"],
                ratio=ratio, median=median,
            ))
    return out


def rule_fenetre_depassee(events: list[dict], fetched_at) -> list[dict]:
    """Still declared by the City, although the official window has passed.

    A measurement of the feed against itself — Vigie never concludes the works
    ended or that the City is wrong; the official map stays the authority.
    """
    threshold = RULES["fenetre-depassee"]["min_count"]
    overdue = []
    for event in events:
        if not isinstance(event, dict):
            continue
        end = ingest_wzdx._parse_iso(event.get("end_date"))
        if end is not None and end < fetched_at:
            overdue.append(event)
    out = []
    for key, group in _group_by_street(overdue).items():
        n = len(group.event_ids)
        if n >= threshold:
            out.append(_anomaly(
                "fenetre-depassee", key, group,
                f"{n} entraves sur {group.display()} restent déclarées "
                "alors que leur fenêtre officielle est dépassée.",
                threshold=threshold,
            ))
    return out


def compile_verdict(rw: dict) -> dict:
    """Pure: roadworks store in, verdict out. No clock, no I/O."""
    raw_events = rw.get("events")
    events = (
        [e for e in raw_events if isinstance(e, dict) and e.get("event_id")]
        if isinstance(raw_events, list) else []
    )
    diff = rw.get("diff") if isinstance(rw.get("diff"), dict) else {}
    fetched = ingest_wzdx._parse_iso(rw.get("fetched_at"))
    anomalies: list[dict] = []
    anomalies += rule_poussee(diff)
    anomalies += rule_fin_reportee(diff)
    anomalies += rule_concentration(events)
    if fetched is not None:
        anomalies += rule_fenetre_depassee(events, fetched)
    anomalies.sort(key=lambda a: (RULES[a["rule_id"]]["rank"], -a["count"], a["street_key"]))
    return {
        "method": METHOD,
        "status": "proposed",
        "compiled_at": rw.get("fetched_at"),
        "has_store": True,
        "rules": {rid: dict(rule) for rid, rule in sorted(RULES.items())},
        "anomalies": anomalies[:ANOMALIES_CAP],
        "anomaly_count": len(anomalies),
        "anomaly_cap": ANOMALIES_CAP,
        "note": (
            "Faits de collecte mesurés par des règles à seuils fixes publiées dans "
            "anomalies.md. Une anomalie n'est ni une prédiction ni un jugement "
            "d'importance ; la carte officielle reste l'autorité."
        ),
    }


def empty_verdict(note: str) -> dict:
    return {
        "method": METHOD, "status": "proposed", "compiled_at": None,
        "has_store": False,
        "rules": {rid: dict(rule) for rid, rule in sorted(RULES.items())},
        "anomalies": [], "anomaly_count": 0, "anomaly_cap": ANOMALIES_CAP,
        "note": note,
    }


def _load_json(path: Path) -> object:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, RecursionError):
        return None


def main(argv: list[str] | None = None) -> int:
    try:
        rw = _load_json(ROADWORKS)
        if not isinstance(rw, dict) or rw.get("method") != ROADWORKS_METHOD:
            verdict = empty_verdict(
                "Aucune collecte officielle exploitable (données absentes, corrompues "
                "ou d’une autre méthode) : aucune anomalie mesurée."
            )
        else:
            verdict = compile_verdict(rw)
        store_io.write_json_atomic(OUT_PATH, verdict)
        try:
            shown = OUT_PATH.relative_to(ROOT)
        except ValueError:
            shown = OUT_PATH
        print(f"anomalies: {verdict['anomaly_count']} measured "
              f"({len(verdict['anomalies'])} in verdict) -> {shown}")
    except (OSError, ValueError, TypeError, RecursionError, IndexError, KeyError) as exc:
        # Always 0: a missing official feed or an unwritable store must never
        # block the edition.
        print(f"anomalies: FAIL {type(exc).__name__}: {exc} - keeping previous verdict")
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
