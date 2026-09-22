"""La promesse — what the record already knows about the official voice.

A dossier is a question several institutions answered. The durable history
already records, edition after edition, whether an *official* document was
present. This module turns that arithmetic into one honest line — never a
verdict: « aucun document officiel dans les N éditions suivies » is a measured
presence fact about the collected dossier, not a claim that an institution is
silent elsewhere, and a document entering the dossier is never an "answer".

No new store, no second brain: everything derives from the tracking block
already embedded in `latest_issues.json` by the durable history.

Usage (from scripts/):
  import promesse
  line = promesse.line_of(issue)
"""
from __future__ import annotations

METHOD = "promesse-v1"


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def status_of(issue: dict) -> dict | None:
    """Question status from the recorded timeline, or None when too young.

    Only editions whose record actually carried the official counter are
    counted: a silence claim never covers an edition that was not measured.
    Fewer than two measured editions = no claim at all (the line appears as
    the durable history fills). Deterministic; no clock; malformed entries
    are skipped.
    """
    if not isinstance(issue, dict):
        return None
    tracking = issue.get("tracking") if isinstance(issue.get("tracking"), dict) else {}
    timeline = tracking.get("timeline")
    timeline = timeline if isinstance(timeline, (list, tuple)) else []
    measured = [
        e for e in timeline
        if isinstance(e, dict) and e.get("ts") and "official" in e
    ]
    editions = len(measured)
    if editions < 2:
        return None
    answered_index = None
    answered_at = None
    for index, entry in enumerate(measured):
        if _safe_int(entry.get("official")) >= 1:
            answered_index = index
            answered_at = str(entry.get("ts"))
            break
    return {
        "method": METHOD,
        "status": "answered" if answered_index is not None else "unanswered",
        "editions": editions,
        "answered_index": answered_index,
        "answered_at": answered_at,
        "first_seen": tracking.get("first_seen"),
    }


def line_of(issue: dict) -> str | None:
    """The French sentence, or None when the record cannot carry the claim."""
    status = status_of(issue)
    if status is None:
        return None
    editions = status["editions"]
    if status["status"] == "unanswered":
        unit = "édition suivie" if editions == 1 else "éditions suivies"
        return f"Aucun document officiel dans les {editions} {unit} de ce dossier."
    index = status["answered_index"]
    if index == 0:
        return "Un document officiel est présent dès la première édition suivie de ce dossier."
    label = "édition" if index == 1 else "éditions"
    if index == editions - 1:
        return (
            f"Un document officiel est entré dans ce dossier cette édition — "
            f"après {index} {label} sans document officiel."
        )
    return (
        f"Un document officiel est présent dans ce dossier depuis le {status['answered_at']} — "
        f"après {index} {label} sans document officiel."
    )


def html_of(issue: dict) -> str:
    """A small bordered line for dossier cards and récit pages, or ""."""
    import resident_brief as brief

    line = line_of(issue)
    if not line:
        return ""
    return f'<p class="dossier-promesse">{brief.esc(line)}</p>'
