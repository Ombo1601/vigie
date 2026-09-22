"""L'instrument — the departure screen (`/partir.html`).

One screen before leaving: the City's declared obstructions (most restrictive
first), the reader's followed corridors marked on-device, what changed since
the last edition, and the sealed edition number. Situational freshness — not a
real-time feed: an instantané, with the staleness of the official collection
stated, and the official map as the reference.

House law holds: structured change data is never ranked with articles, nothing
is invented, corridors never leave the browser (no account, no position, no
server round-trip), and the page is complete without JavaScript — the corridor
marks are the enhancement.

Usage:
  python scripts/depart.py   # emit public/partir.html from the stores
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import resident_brief as brief  # noqa: E402
import promesse  # noqa: E402
import store_io  # noqa: E402

METHOD = "depart-v1"
OUT = ROOT / "public" / "partir.html"
DEPART_CAP = 6
STALE_HOURS = 6.0
SITE_URL = "https://vigieqc.com"

_WORDMARK_SVG = (
    '<svg width="28" height="32" viewBox="0 0 28 32" aria-hidden="true">'
    '<path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/>'
    "</svg>"
)

# The departure script is a self-hosted asset so every page can carry a
# strict script-src 'self' CSP; the JSON street island stays inline (a data
# block is never executed).
CORRIDORS_JS_ASSET = "/assets/depart.js"


def _event_when(event: dict) -> str:
    start, end = event.get("start_date"), event.get("end_date")
    estimated = "estimated" in (
        str(event.get("start_date_accuracy") or ""), str(event.get("end_date_accuracy") or "")
    )
    note = ' <span class="fine">(estimées)</span>' if estimated else ""
    if start and end:
        return f'Du {brief.date_html(start)} au {brief.date_html(end)}{note}'
    if start:
        return f'Depuis le {brief.date_html(start)}{note}'
    if end:
        return f'Jusqu’au {brief.date_html(end)}{note}'
    return ""


def _ordered_events(events: list[dict]) -> list[dict]:
    ordered = sorted(events, key=lambda e: str(e.get("event_id") or ""))
    ordered.sort(key=lambda e: str(e.get("update_date") or ""), reverse=True)
    ordered.sort(key=lambda e: brief.RW_SEVERITY.get(str(e.get("vehicle_impact") or ""), 6))
    return ordered


def _street_index(events: list[dict]) -> list[dict]:
    """Per declared street: count + the most restrictive active obstruction.

    The departure screen is read on-device: these rows let the browser answer
    « is my street blocked? » for a followed corridor even when that street is
    not among the six most restrictive declarations displayed. Same facts as
    the list, ordered identically — never a second ranking.
    """
    rows: dict[str, dict] = {}
    for event in _ordered_events(events):
        impact = str(event.get("vehicle_impact") or "")
        label = brief.RW_IMPACT_LABELS.get(impact, brief.RW_IMPACT_LABELS["unknown"])
        until = brief.parse_date(event.get("end_date"))
        for raw in event.get("road_names") or []:
            name = brief.plain(str(raw or ""))
            key = brief._street_key(raw)
            if not name or not key:
                continue
            row = rows.get(key)
            if row is None:
                rows[key] = {
                    "key": key, "name": name, "n": 1,
                    "severity": brief.RW_SEVERITY.get(impact, 6),
                    # Only the City's own open-road vocabulary is "open"; an
                    # unmapped impact (severity 6) must never read as open.
                    "open": impact in ("all-lanes-open", "no-lanes-closed"),
                    "impact": impact, "impact_label": label,
                    "until": f"{until:%Y-%m-%d}" if until else "",
                }
            else:
                row["n"] += 1
    return [rows[key] for key in sorted(rows)]


def _rows_html(events: list[dict]) -> str:
    rows = []
    for event in _ordered_events(events)[:DEPART_CAP]:
        impact = str(event.get("vehicle_impact") or "")
        severity = brief.RW_SEVERITY.get(impact, 6)
        kicker = [
            label for label in (
                brief.RW_STATUS_LABELS.get(str(event.get("event_status") or "")),
                brief.RW_EVENT_TYPE_LABELS.get(str(event.get("event_type") or "")),
                brief.RW_IMPACT_LABELS.get(impact),
                brief.RW_DIRECTION_LABELS.get(str(event.get("direction") or "")),
            ) if label
        ]
        road_keys = " ".join(
            key for key in (brief._street_key(raw) for raw in (event.get("road_names") or [])[:6]) if key
        )
        dates = _event_when(event)
        dates_html = f'<p class="depart-dates">{dates}</p>' if dates else ""
        rows.append(
            f'<li class="depart-item rw-sev-{severity}" data-roads="{brief.esc(road_keys)}">'
            f'<p class="depart-roads">{brief.esc(brief._rw_places(event) or "Lieu non précisé")}</p>'
            f'<p class="depart-kicker">{" · ".join(brief.esc(k) for k in kicker)}</p>'
            f"{dates_html}</li>"
        )
    return "".join(rows)


def _question_pick(issues: list[dict]) -> dict | None:
    """The dossier with the longest recorded official silence, deterministic."""
    best = None
    best_editions = -1
    candidates = sorted(
        (i for i in (issues or []) if isinstance(i, dict) and i.get("question")),
        key=lambda i: str(i.get("issue_id") or ""),
    )
    for issue in candidates:
        status = promesse.status_of(issue)
        if not status or status["status"] != "unanswered":
            continue
        if status["editions"] > best_editions:
            best = issue
            best_editions = status["editions"]
    return best if best is not None else (candidates[0] if candidates else None)


def _question_html(issues: list[dict]) -> str:
    issue = _question_pick(issues)
    if issue is None:
        return ""
    question = brief.plain(issue.get("question"))[:160]
    inner = promesse.line_html(issue)
    status_html = f'<p class="depart-question-status">{inner}</p>' if inner else ""
    attributed = str(issue.get("label_kind") or "") == "attributed_headline"
    attrib_html = (
        '<p class="fine">Titre d’un éditeur, cité tel quel.</p>' if attributed else ""
    )
    link = brief.dossier_page_path(issue)
    return (
        '<section class="depart-question" aria-labelledby="depart-question-title">'
        '<h2 id="depart-question-title">La question</h2>'
        f'<p class="depart-question-q">{brief.esc(question)}</p>{attrib_html}{status_html}'
        f'<p class="fine">Le dossier complet : <a href="{brief.esc(link)}">voix, titres et suivi ↗</a>. '
        "Présence dans le dossier, jamais un verdict.</p></section>"
    )


def _changes_html(ledger: dict) -> str:
    ledger = ledger if isinstance(ledger, dict) else {}
    if not ledger.get("has_previous"):
        return '<p class="depart-change-line">La comparaison commence à la prochaine édition.</p>'
    new = brief.safe_int(ledger.get("new_count"))
    developed = brief.safe_int(ledger.get("developed_count"))
    quiet = brief.safe_int(ledger.get("quiet_count"))
    if not (new or developed or quiet):
        return '<p class="depart-change-line">Aucun changement de dossier depuis la dernière édition.</p>'
    bits = []
    if new:
        bits.append(f"+{new} nouveau{'x' if new != 1 else ''}")
    if developed:
        bits.append(f"~{developed} développé{'s' if developed != 1 else ''}")
    if quiet:
        bits.append(f"−{quiet} disparu{'s' if quiet != 1 else ''} de la collecte")
    return (
        f'<p class="depart-change-line">{" · ".join(bits)}.</p>'
        '<p class="fine">« Disparu de la collecte » n’est pas « réglé ». Une absence n’est pas une résolution.</p>'
    )


def _seal_html(state: dict) -> str:
    seals = state.get("seals") if isinstance(state, dict) else None
    if not isinstance(seals, list) or not seals or not isinstance(seals[-1], dict):
        return '<p class="no-data">Aucune édition scellée pour l’instant.</p>'
    last = seals[-1]
    root = str(last.get("root") or "")
    seq = brief.safe_int(last.get("seq"))
    return (
        f'<p class="depart-seal-line">Édition n° <strong>{seq}</strong> — {brief.date_html(last.get("edition"))}.</p>'
        f'<p class="reg-root"><span class="eyebrow">RACINE</span><code>{brief.esc(root[:12])}…</code></p>'
        '<p class="fine">Rien de ce qui précède n’a été réécrit : <a href="/registre.html">le registre</a> · '
        '<a href="/memoire.html">la mémoire</a>.</p>'
    )


def render_depart(roadworks: dict, issues: list[dict], ledger: dict, state: dict,
                  generated_at: str) -> str:
    now = brief.parse_date(generated_at)
    rw = roadworks if isinstance(roadworks, dict) else {}
    events = [e for e in (rw.get("events") or []) if isinstance(e, dict) and e.get("event_id")]
    fetched = brief.parse_date(rw.get("fetched_at"))
    stale = False
    if fetched is not None and now is not None:
        age = (now - fetched).total_seconds()
        stale = age > STALE_HOURS * 3600 or age < -300
    if fetched is None:
        roads_html = '<p class="no-data">Aucune déclaration reçue dans la dernière collecte. Vérifiez la carte officielle avant de partir.</p>'
    elif not events:
        roads_html = (
            '<p class="no-data">Aucune entrave active déclarée dans cette collecte. Cela ne '
            "signifie pas qu’aucun travail n’a lieu sur le réseau.</p>"
        )
    else:
        roads_html = f'<ul class="depart-list">{_rows_html(events)}</ul>'
        shown = min(DEPART_CAP, len(events))
        total = len(events)
        head = (
            "La plus restrictive affichée" if shown == 1
            else f"Les {shown} plus restrictives affichées"
        )
        tail = (
            f"{total} entrave active au total dans le flux officiel."
            if total == 1 else
            f"{total} entraves actives au total dans le flux officiel."
        )
        roads_html += f'<p class="fine">{head} — {tail}</p>'
    stale_html = (
        '<p class="rw-stale warning">Collecte à actualiser : ces déclarations ont plus de six '
        "heures. Vérifiez la carte officielle avant de partir.</p>" if stale else ""
    )
    institution = brief.esc(str(rw.get("institution_name") or "Ville de Québec").strip())
    dataset = brief.safe_url(rw.get("dataset_url"))
    dataset_link = (
        f'<a href="{brief.esc(dataset)}" rel="noopener noreferrer">données officielles</a>'
        if dataset else "données officielles"
    )
    fetched_line = (
        f"Collecte du {brief.date_html(fetched.isoformat())}." if fetched is not None else ""
    )
    island = json.dumps(
        {"method": "depart-streets-v1", "streets": _street_index(events)},
        ensure_ascii=False, separators=(",", ":"),
    ).replace("<", "\\u003c")
    return f"""<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Avant de partir — Vigie</title>
<meta name="description" content="Un écran avant de quitter : les entraves déclarées par la Ville, vos rues suivies, et ce qui a changé depuis la dernière édition.">
<link rel="canonical" href="{SITE_URL}/partir.html"><meta name="robots" content="index, follow">
<link rel="describedby" href="/llms.txt">
<meta property="og:type" content="website"><meta property="og:site_name" content="Vigie"><meta property="og:locale" content="fr_CA">
<meta property="og:title" content="Avant de partir — Vigie"><meta property="og:description" content="Les entraves déclarées, vos rues, ce qui a changé. Un écran."><meta property="og:url" content="{SITE_URL}/partir.html">
<meta property="og:image" content="{brief.SITE_OG_IMAGE}">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="Avant de partir — Vigie">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"></head>
<body class="depart-body"><a class="skip-link" href="#depart">Aller au départ</a>
<header class="masthead"><a class="wordmark" href="/" aria-label="Vigie, accueil">{_WORDMARK_SVG}vigie<span class="wordmark-dot">.</span></a><span class="edition">AVANT DE PARTIR</span><nav aria-label="Navigation principale"><a href="/">Le point</a><a href="/partir.html">Le départ</a><a href="/memoire.html">La mémoire</a><a href="/registre.html">Le registre</a><a href="/methode/legal.html">Mentions légales</a></nav></header>
<main id="depart">
<header class="depart-head"><p class="eyebrow">UN ÉCRAN. PUIS LA PORTE.</p>
<h1 class="recit-title">Avant de partir.</h1>
<p class="recit-attrib">Les entraves déclarées par la Ville, vos rues suivies, ce qui a changé depuis la dernière édition. Rien d’autre.</p></header>
<div class="depart-grid">
<section class="depart-roads" aria-labelledby="depart-roads-title"><h2 id="depart-roads-title">Les entraves actives</h2>
{stale_html}{roads_html}
<p class="fine">Source : {institution} — flux officiel (CC-BY 4.0, via Données Québec). {fetched_line}
<a href="/#travaux">Tout voir</a> · {dataset_link} · <a href="{brief.RW_MAP_URL}" rel="noopener noreferrer">carte officielle ↗</a></p></section>
<div class="depart-side">
<section class="depart-corridors" id="depart-corridors" aria-labelledby="depart-corridors-title"><h2 id="depart-corridors-title">Vos corridors</h2>
<p class="depart-hint" id="depart-hint">Vos rues suivies apparaîtront ici — sur cet appareil seulement. Suivez-les depuis <a href="/#travaux">le point local</a>.</p>
<ul class="depart-corridor-list" id="depart-corridor-list" hidden></ul>
<p class="fine" id="depart-corridor-status"></p>
<p class="fine">Correspondance littérale, aucune position demandée, aucun effet calculé sur votre trajet.</p></section>
{_question_html(issues)}
<section class="depart-changes" aria-labelledby="depart-changes-title"><h2 id="depart-changes-title">Depuis la dernière édition</h2>
{_changes_html(ledger)}</section>
<section class="depart-seal" aria-labelledby="depart-seal-title"><h2 id="depart-seal-title">L’édition</h2>
{_seal_html(state)}</section>
</div></div>
<p class="fine depart-note">Un instantané, pas une alerte en temps réel : la carte officielle reste la référence. Aucun compte, aucun suivi — vos corridors restent dans ce navigateur.</p>
</main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>L’instrument du départ.</span><a class="legal-link" href="/methode/legal.html">Mentions légales, attribution et retrait</a></footer>
<script type="application/json" id="vigie-streets">{island}</script>
<script src="{CORRIDORS_JS_ASSET}" defer></script>
</body></html>
"""


def emit(roadworks: dict, issues: list[dict], ledger: dict, state: dict,
         generated_at: str, *, out: Path = OUT) -> dict:
    """Write the departure screen. Fail-soft: a corrupt store still renders an honest page."""
    try:
        page = render_depart(roadworks, issues, ledger, state, generated_at)
    except (OSError, ValueError, TypeError) as exc:
        print(f"depart: skipped ({type(exc).__name__}: {exc})")
        return {"method": METHOD, "written": False}
    out.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_text_atomic(out, page)
    print(f"depart: ecran de depart -> {out}")
    return {"method": METHOD, "written": True}


def main() -> int:
    import registre

    issues_doc = registre.load_json(ROOT / "data" / "issues" / "latest_issues.json")
    state = registre.load_state()
    emit(
        registre.load_json(registre.ROADWORKS),
        issues_doc.get("issues") if isinstance(issues_doc.get("issues"), list) else [],
        issues_doc.get("change_ledger") if isinstance(issues_doc.get("change_ledger"), dict) else {},
        state,
        str(issues_doc.get("clustered_at") or ""),
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
