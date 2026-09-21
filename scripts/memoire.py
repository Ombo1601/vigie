"""La mémoire — the sealed editions, readable.

The registre seals every edition as a hash-chained record of identifiers and
counts, no publisher text. This emitter turns that chain into a browsable
memory: one page per published seal (dossier questions Vigie labelled, the
change ledger, who spoke and who did not) and an index of every seal. The
archive Google cannot copy: the city's own record, including its silences,
edition after edition.

House law holds: only sealed content is shown (attributed-headline dossiers
keep their empty question — the chain never froze a publisher's words), no
JavaScript, deterministic, fail-soft.

Usage:
  python scripts/memoire.py   # emit public/memoire.html + public/memoire/*.html
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import registre  # noqa: E402
import resident_brief as brief  # noqa: E402
import store_io  # noqa: E402

METHOD = "memoire-v1"
OUT_INDEX = ROOT / "public" / "memoire.html"
OUT_DIR = ROOT / "public" / "memoire"
PUBLIC_CAP = registre.PUBLIC_SEAL_CAP  # same window as chain.json
SITE_URL = "https://vigieqc.com"

_WORDMARK_SVG = (
    '<svg width="28" height="32" viewBox="0 0 28 32" aria-hidden="true">'
    '<path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/>'
    "</svg>"
)

_ATTRIBUTED = "Dossier étiqueté par un titre d’éditeur — non repris dans le registre."
_NOT_LABELLED = "Dossier sans libellé scellé."


def _name(names: dict, iid: str) -> str:
    meta = names.get(iid) if isinstance(names, dict) else None
    if isinstance(meta, dict) and meta.get("name"):
        return str(meta["name"])
    return iid


def _question(entry: dict) -> str:
    question = str(entry.get("question") or "").strip()
    if question:
        return question
    # An empty sealed question is only explained by label_kind. Never claim
    # "titre d'éditeur" for a dossier the chain never labelled that way.
    kind = str(entry.get("label_kind") or "")
    return _ATTRIBUTED if kind == "attributed_headline" else _NOT_LABELLED


def _safe_int(value: object, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _chrome(title: str, desc: str, canonical: str, body: str) -> str:
    return f"""<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>{title} — Vigie</title>
<meta name="description" content="{desc}">
<link rel="canonical" href="{canonical}"><meta name="robots" content="index, follow">
<link rel="describedby" href="/llms.txt">
<meta property="og:type" content="website"><meta property="og:site_name" content="Vigie"><meta property="og:locale" content="fr_CA">
<meta property="og:title" content="{title} — Vigie"><meta property="og:description" content="{desc}"><meta property="og:url" content="{canonical}">
<meta property="og:image" content="{brief.SITE_OG_IMAGE}">
<meta name="twitter:card" content="summary"><meta name="twitter:title" content="{title} — Vigie"><meta name="twitter:description" content="{desc}">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"></head>
<body class="memoire-body"><a class="skip-link" href="#memoire">Aller à la mémoire</a>
<header class="masthead"><a class="wordmark" href="/" aria-label="Vigie, accueil">{_WORDMARK_SVG}vigie<span class="wordmark-dot">.</span></a><span class="edition">LA MÉMOIRE</span><nav aria-label="Navigation principale"><a href="/">Le point</a><a href="/memoire.html">La mémoire</a><a href="/registre.html">Le registre</a><a href="/methode/index.html">La méthode</a><a href="/methode/legal.html">Mentions légales</a></nav></header>
<main id="memoire">
{body}
</main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>Les éditions passées, scellées.</span><a class="legal-link" href="/methode/legal.html">Mentions légales, attribution et retrait</a></footer></body></html>
"""


def _voices_html(record: dict, names: dict) -> str:
    row = registre.voice_row(record)
    spoke = [row_i for row_i in (row.get("spoke") or [])]
    silent = [row_i for row_i in (row.get("silent") or [])]
    if not spoke and not silent:
        return ""
    def chips(ids: list[str]) -> str:
        out = []
        for iid in ids:
            meta = names.get(iid) if isinstance(names, dict) else None
            official = isinstance(meta, dict) and meta.get("kind") == "official"
            cls = "memoire-official" if official else ""
            out.append(f'<li class="{cls}">{brief.esc(_name(names, iid))}</li>')
        return "".join(out)
    return (
        '<section class="memoire-section"><h2>Les voix de l’édition</h2>'
        '<div class="memoire-voices">'
        f'<div><h3>Ont parlé <span class="n">({len(spoke)})</span></h3><ul>{chips(spoke) or "<li>—</li>"}</ul></div>'
        f'<div><h3>N’ont pas parlé <span class="n">({len(silent)})</span></h3><ul>{chips(silent) or "<li>—</li>"}</ul></div>'
        "</div>"
        '<p class="fine">Absence dans les flux suivis pendant cette édition — jamais la preuve '
        "qu’une institution s’est tue ailleurs.</p></section>"
    )


def _ledger_html(record: dict, dossier_questions: dict[str, str]) -> str:
    ledger = record.get("ledger") if isinstance(record.get("ledger"), dict) else {}
    if not ledger.get("has_previous"):
        return (
            '<section class="memoire-section"><h2>Depuis l’édition précédente</h2>'
            "<p>Première édition scellée : aucune comparaison.</p></section>"
        )
    def rows(ids: list, *, quiet: bool = False) -> str:
        out = []
        for iid in ids:
            label = dossier_questions.get(str(iid))
            if label:
                text = brief.esc(label)
            elif quiet:
                text = f'Dossier {brief.esc(str(iid)[:10])}… — identifiant seul dans le registre.'
            else:
                text = brief.esc(_ATTRIBUTED)
            out.append(f"<li>{text}</li>")
        return "".join(out)
    groups = []
    for key, title in (("new", "Nouveaux dossiers"), ("developed", "Dossiers développés"),
                       ("quiet", "Disparus de cette collecte — jamais « réglés »")):
        ids = [str(i) for i in (ledger.get(key) or []) if isinstance(i, str)]
        note = (
            '<p class="fine">« Disparu de la collecte » n’est pas « réglé » ; une absence n’est pas une résolution.</p>'
            if key == "quiet" and ids else ""
        )
        groups.append(
            f'<div class="memoire-group"><h3>{title} <span class="n">({len(ids)})</span></h3>'
            f'<ul class="memoire-lines">{rows(ids, quiet=(key == "quiet")) or "<li>—</li>"}</ul>{note}</div>'
        )
    return '<section class="memoire-section"><h2>Depuis l’édition précédente</h2>' + "".join(groups) + "</section>"


def _dossiers_html(record: dict, names: dict, current_routes: dict[str, str]) -> str:
    dossiers = [d for d in (record.get("dossiers") or []) if isinstance(d, dict) and d.get("issue_id")]
    if not dossiers:
        return (
            '<section class="memoire-section"><h2>Les dossiers</h2>'
            "<p>Aucun dossier à plusieurs voix dans cette édition.</p></section>"
        )
    items = []
    for dossier in sorted(dossiers, key=lambda d: str(d.get("issue_id"))):
        iid = str(dossier["issue_id"])
        route = current_routes.get(iid)
        question = brief.esc(_question(dossier))
        title = f'<a href="{brief.esc(route)}">{question} ↗</a>' if route else question
        spoke = len([s for s in (dossier.get("spoke") or []) if isinstance(s, str)])
        silent = len([s for s in (dossier.get("silent") or []) if isinstance(s, str)])
        items.append(
            '<li class="memoire-dossier">'
            f'<p class="memoire-dossier-q">{title}</p>'
            f'<p class="memoire-dossier-meta">{brief.safe_int(dossier.get("item_count"))} articles · '
            f'{spoke} institution{"s" if spoke != 1 else ""} ont parlé · '
            f'{silent} n’{"ont" if silent != 1 else "a"} pas parlé · '
            f'{brief.safe_int(dossier.get("official_voice_count"))} voix officielle'
            f'{"s" if brief.safe_int(dossier.get("official_voice_count")) != 1 else ""}'
            "</p></li>"
        )
    return (
        '<section class="memoire-section"><h2>Les dossiers</h2>'
        f'<ul class="memoire-dossiers">{"".join(items)}</ul></section>'
    )


def _nav_html(seq: int, prev_seq: int | None, next_seq: int | None) -> str:
    left = (
        f'<a href="/memoire/{prev_seq}.html">← Édition n° {prev_seq}</a>'
        if prev_seq is not None else '<span class="fine">Début de la chaîne.</span>'
    )
    right = (
        f'<a href="/memoire/{next_seq}.html">Édition n° {next_seq} →</a>'
        if next_seq is not None else '<span class="fine">Dernière édition scellée.</span>'
    )
    return f'<nav class="memoire-nav" aria-label="Éditions voisines">{left}<a href="/memoire.html">Toutes les éditions</a>{right}</nav>'


def render_edition(seal: dict, names: dict, *, prev_seq: int | None, next_seq: int | None,
                   current_routes: dict[str, str] | None = None) -> str:
    record = seal.get("record") if isinstance(seal.get("record"), dict) else {}
    seq = _safe_int(seal.get("seq"))
    root = str(seal.get("root") or "")
    edition = seal.get("edition")
    questions = {
        str(d.get("issue_id")): _question(d)
        for d in (record.get("dossiers") or [])
        if isinstance(d, dict) and d.get("issue_id")
    }
    body = (
        '<header class="memoire-head"><p class="eyebrow">ÉDITION SCELLÉE</p>'
        f'<h1 class="recit-title">Édition n° {seq} — {brief.date_html(edition)}.</h1>'
        f'<p class="recit-attrib">Racine <code>{brief.esc(root[:16])}…</code> · '
        f'<a href="/registre/chain.json">vérifier la chaîne</a>. Identifiants et comptes seulement — '
        "aucun texte d’éditeur dans le registre.</p></header>"
        f"{_nav_html(seq, prev_seq, next_seq)}"
        f"{_dossiers_html(record, names, current_routes or {})}"
        f"{_voices_html(record, names)}"
        f"{_ledger_html(record, questions)}"
        '<p class="fine">Scellé le ' + brief.date_html(edition) + " — rien de ce qui précède n’a été réécrit.</p>"
    )
    return _chrome(
        f"Édition n° {seq} — La mémoire",
        f"Édition n° {seq} du registre de Vigie : dossiers rapprochés, voix officielles, qui a parlé et qui n’a pas parlé.",
        f"{SITE_URL}/memoire/{seq}.html",
        body,
    )


def render_index(seals: list[dict], names: dict, *, total: int | None = None) -> str:
    items = []
    for seal in sorted(seals, key=lambda s: _safe_int(s.get("seq")), reverse=True):
        record = seal.get("record") if isinstance(seal.get("record"), dict) else {}
        seq = _safe_int(seal.get("seq"))
        row = registre.voice_row(record)
        ledger = record.get("ledger") if isinstance(record.get("ledger"), dict) else {}
        changes = ""
        if ledger.get("has_previous"):
            changes = (
                f" · +{len(ledger.get('new') or [])} / ~{len(ledger.get('developed') or [])} "
                f"/ −{len(ledger.get('quiet') or [])}"
            )
        items.append(
            '<li class="memoire-item">'
            f'<span class="memoire-seq">N° {seq}</span>'
            f'<span class="memoire-counts">{brief.date_html(seal.get("edition"))} · '
            f'{len(record.get("dossiers") or [])} dossier{"s" if len(record.get("dossiers") or []) != 1 else ""} · '
            f'{len(row.get("spoke") or [])} ont parlé · {len(row.get("silent") or [])} n’ont pas parlé{changes}</span>'
            f'<a class="memoire-go" href="/memoire/{seq}.html">Lire ↗</a></li>'
        )
    if items:
        listing = f'<ol class="memoire-list">{"".join(items)}</ol>'
        # The real chain size, not the capped page count: reporting "200
        # éditions" when 350 exist would be an absence reported as the whole.
        chain_size = total if isinstance(total, int) and total >= len(seals) else len(seals)
        note = (
            f"{chain_size} édition{'s' if chain_size != 1 else ''} scellée{'s' if chain_size != 1 else ''}"
            + (f" — les {len(seals)} dernières." if chain_size > len(seals) else ".")
        )
    else:
        listing = (
            '<p class="no-data">La chaîne commence à la prochaine édition. Rien n’est simulé : '
            "la mémoire n’affiche que ce qui a été scellé.</p>"
        )
        note = "Aucune édition scellée pour l’instant."
    body = (
        '<header class="memoire-head"><p class="eyebrow">LE REGISTRE, LISIBLE</p>'
        '<h1 class="recit-title">La mémoire de la ville.</h1>'
        '<p class="recit-attrib">Chaque édition scellée, lisible : les dossiers rapprochés, les voix '
        "officielles, qui a parlé et qui n’a pas parlé. Aucun texte d’éditeur, aucun verdict — la "
        "chaîne des éditions, édition par édition.</p></header>"
        f'<p class="section-note">{note}</p>'
        f"{listing}"
        '<p class="fine">La vérification reste dans <a href="/registre.html">le registre</a> : '
        'chaque racine se recalcule avec sha256, sans compte ni clé.</p>'
    )
    return _chrome(
        "La mémoire", "Toutes les éditions scellées de Vigie : dossiers, voix officielles et silences, édition par édition.",
        f"{SITE_URL}/memoire.html", body,
    )


def emit(state: dict, issues: list[dict] | None = None, *, out_index: Path = OUT_INDEX,
         out_dir: Path = OUT_DIR) -> dict:
    """Write the memory index + one page per published seal. Fail-soft."""
    seals = [
        s for s in ((state or {}).get("seals") or [])
        if isinstance(s, dict) and isinstance(s.get("record"), dict) and s.get("record")
    ]
    published = seals[-PUBLIC_CAP:]
    names = (state or {}).get("names") if isinstance(state, dict) else {}
    names = names if isinstance(names, dict) else {}
    current_routes: dict[str, str] = {}
    for issue in issues or []:
        if isinstance(issue, dict) and issue.get("issue_id"):
            current_routes[str(issue["issue_id"])] = brief.dossier_page_path(issue)
    out_dir.mkdir(parents=True, exist_ok=True)
    written: set[str] = set()
    seqs = [_safe_int(s.get("seq")) for s in published]
    for index, seal in enumerate(published):
        seq = seqs[index]
        page = render_edition(
            seal, names,
            prev_seq=seqs[index - 1] if index > 0 else None,
            next_seq=seqs[index + 1] if index + 1 < len(published) else None,
            current_routes=current_routes,
        )
        store_io.write_text_atomic(out_dir / f"{seq}.html", page)
        written.add(f"{seq}.html")
    for stale in sorted(out_dir.glob("*.html")):
        if stale.name not in written:
            try:
                stale.unlink()
            except OSError:
                pass
    store_io.write_text_atomic(out_index, render_index(published, names, total=len(seals)))
    print(f"memoire: {len(written)} edition(s) lisibles -> {out_index}")
    return {"method": METHOD, "editions": len(written)}


def main() -> int:
    issues_doc = registre.load_json(registre.ISSUES)
    emit(
        registre.load_state(),
        issues_doc.get("issues") if isinstance(issues_doc.get("issues"), list) else [],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
