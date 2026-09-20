"""Le Registre — the sealed, verifiable record of editions and institutional voice.

Vigie's real asset is not the brief; it is memory of what the followed
institutions said and did not say, edition after edition. This module turns
that memory into a public, append-only, hash-chained record that anyone can
verify with nothing but sha256:

  leaf_n = sha256(canonical_json(record_n))
  root_n = sha256(root_{n-1} || leaf_n)          (root_0 = "" for the genesis seal)

A seal never changes once a later seal exists. Re-running the render step on
the same collection (offline rebuild, hourly roads-only lane) replaces the
last seal with identical bytes instead of appending, because the edition key
is the collection clock (dossier_history.updated_at), never the render clock.

The record carries institution IDs only — no publisher text, no excerpts — so
the public chain never ships publisher content. Silence in the record is
*absence in the collected feeds*, a measured fact of this collection, never a
claim that an institution said nothing anywhere. Stdlib only. Fail-soft: a
missing or corrupt store yields no seal and exit 0; the brief renders anyway.

Usage:
  python scripts/registre.py                 # emit from the stores
  python scripts/registre.py --verify chain.json   # verify a downloaded chain
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import store_io  # noqa: E402

METHOD = "registre-v1 sha256-chain"
ROADS_METHOD = "registre-travaux-v1 sha256-chain"
ORIGIN = "vigieqc.com/registre"
SITE_URL = "https://vigieqc.com"

ISSUES = ROOT / "data" / "issues" / "latest_issues.json"
HISTORY = ROOT / "data" / "issues" / "history.json"
ROADWORKS = ROOT / "data" / "roadworks" / "latest_roadworks.json"
STATE = ROOT / "data" / "registre" / "registre.json"
OUT_DIR = ROOT / "public" / "registre"
OUT_HTML = ROOT / "public" / "registre.html"

PUBLIC_SEAL_CAP = 200      # seals published in chain.json (state keeps all)
VOICE_ROWS_CAP = 400       # per-edition voice rows kept for streak arithmetic
ROADS_SEAL_CAP = 2000      # roadworks roots kept (hourly lane, tiny records)
QUESTION_CAP = 300
HTML_SEALS_SHOWN = 12

_SPOOF = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\xad\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff]")


# --------------------------------------------------------------------------- #
# Primitives
# --------------------------------------------------------------------------- #
def canonical(obj: object) -> bytes:
    """Canonical JSON: sorted keys, no whitespace, UTF-8, non-ASCII kept verbatim."""
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def leaf_of(record: dict) -> str:
    return digest(canonical(record))


def chain_hash(prev_root: str, leaf: str) -> str:
    return digest((prev_root or "").encode("ascii") + leaf.encode("ascii"))


def parse_ts(raw: object) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    try:
        dt = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _after(a: object, b: object) -> bool:
    """True when a is chronologically after b (string compare as last resort)."""
    da, db = parse_ts(a), parse_ts(b)
    if da and db:
        return da > db
    return str(a or "") > str(b or "")


def plain(value: object) -> str:
    raw = re.sub(r"</?[a-zA-Z][^>]*>", " ", str(value or ""))
    return re.sub(r"\s+", " ", _SPOOF.sub("", html.unescape(raw))).strip()


def esc(value: object) -> str:
    return html.escape(_SPOOF.sub("", str(value if value is not None else "")), quote=True)


def load_json(path: Path) -> dict:
    try:
        loaded = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


# --------------------------------------------------------------------------- #
# Edition record
# --------------------------------------------------------------------------- #
def edition_key(payload: dict, history: dict) -> str:
    """The collection clock, never the render clock."""
    key = history.get("updated_at") if isinstance(history, dict) else None
    if isinstance(key, str) and parse_ts(key):
        return parse_ts(key).isoformat()
    fallback = payload.get("clustered_at") if isinstance(payload, dict) else None
    dt = parse_ts(fallback)
    return dt.isoformat() if dt else ""


def edition_record(payload: dict, edition: str) -> dict | None:
    """The sealed content of one edition: IDs and counts, no publisher text.

    The only text carried is Vigie's own subject label for a dossier
    (truncated, never rewritten). Dossiers labelled by an attributed publisher
    headline carry an empty question and their label_kind, so no publisher
    text is ever frozen in the chain.
    """
    if not edition or not isinstance(payload, dict):
        return None
    issues = payload.get("issues")
    if not isinstance(issues, list):
        return None
    followed = sorted({str(i) for i in (payload.get("chancellery_institutions") or []) if isinstance(i, str) and i})
    dossiers: list[dict] = []
    for iss in issues:
        if not isinstance(iss, dict) or not isinstance(iss.get("issue_id"), str):
            continue
        spoke = sorted({str(s) for s in (iss.get("sources") or []) if isinstance(s, str) and s})
        silence = iss.get("silence") if isinstance(iss.get("silence"), dict) else {}
        silent = sorted({
            str(row.get("institution_id") or row.get("source_id"))
            for row in (silence.get("silent") or [])
            if isinstance(row, dict) and (row.get("institution_id") or row.get("source_id"))
        })
        # Only Vigie's own subject labels are sealed. An attributed headline is a
        # publisher's text: it stays in the brief (with attribution, removable
        # the same day under R10) and never enters an immutable record.
        label_kind = str(iss.get("label_kind") or "subject_label")
        question = "" if label_kind == "attributed_headline" else plain(iss.get("question"))[:QUESTION_CAP]
        dossiers.append({
            "issue_id": iss["issue_id"],
            "label_kind": label_kind,
            "question": question,
            "item_count": _int(iss.get("item_count")),
            "official_voice_count": _int(iss.get("official_voice_count")),
            "spoke": spoke,
            "silent": silent,
        })
    dossiers.sort(key=lambda d: d["issue_id"])
    ledger_in = payload.get("change_ledger") if isinstance(payload.get("change_ledger"), dict) else {}
    ledger = {
        "has_previous": bool(ledger_in.get("has_previous")),
        "new": _ids(ledger_in.get("new")),
        "developed": _ids(ledger_in.get("developed")),
        "quiet": _ids(ledger_in.get("quiet")),
    }
    return {
        "method": METHOD,
        "edition": edition,
        "followed": followed,
        "dossiers": dossiers,
        "ledger": ledger,
    }


def _int(value: object) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return 0


def _ids(rows: object) -> list[str]:
    if not isinstance(rows, list):
        return []
    return sorted({str(r.get("issue_id")) for r in rows if isinstance(r, dict) and r.get("issue_id")})


def institution_names(payload: dict) -> dict[str, dict]:
    """id -> {name, kind} harvested from silence rows and voice items."""
    out: dict[str, dict] = {}
    for iss in (payload.get("issues") or []) if isinstance(payload, dict) else []:
        if not isinstance(iss, dict):
            continue
        silence = iss.get("silence") if isinstance(iss.get("silence"), dict) else {}
        for row in silence.get("silent") or []:
            if isinstance(row, dict):
                iid = str(row.get("institution_id") or row.get("source_id") or "")
                if iid:
                    out.setdefault(iid, {})
                    out[iid]["name"] = plain(row.get("institution_name") or row.get("source_name") or iid)[:120]
                    out[iid]["kind"] = "official" if row.get("source_kind") == "official" else "media"
        for voice in iss.get("tensions") or []:
            if isinstance(voice, dict) and voice.get("institution_id"):
                iid = str(voice["institution_id"])
                out.setdefault(iid, {})
                out[iid]["name"] = plain(voice.get("institution_name") or iid)[:120]
                out[iid]["kind"] = "official" if voice.get("source_kind") == "official" else "media"
    return out


def voice_row(record: dict) -> dict:
    """Who spoke / who did not, across the whole edition (institution seats)."""
    spoke: set[str] = set()
    for d in record.get("dossiers") or []:
        spoke.update(d.get("spoke") or [])
    followed = set(record.get("followed") or [])
    established = bool(record.get("dossiers"))
    silent = sorted(followed - spoke) if established else []
    return {
        "edition": record["edition"],
        "spoke": sorted(spoke & followed) if followed else sorted(spoke),
        "silent": silent,
        "established": established,
    }


# --------------------------------------------------------------------------- #
# Chain update (idempotent, forward-only)
# --------------------------------------------------------------------------- #
def empty_state() -> dict:
    return {
        "method": METHOD,
        "origin": ORIGIN,
        "seals": [],
        "voice": [],
        "names": {},
        "travaux": {"method": ROADS_METHOD, "seals": [], "latest_record": None},
    }


def load_state(path: Path = STATE) -> dict:
    loaded = load_json(path)
    if loaded.get("method") != METHOD or not isinstance(loaded.get("seals"), list):
        return empty_state()
    state = empty_state()
    state["seals"] = [s for s in loaded["seals"] if isinstance(s, dict) and isinstance(s.get("record"), dict)]
    state["voice"] = [v for v in (loaded.get("voice") or []) if isinstance(v, dict) and v.get("edition")]
    state["names"] = loaded.get("names") if isinstance(loaded.get("names"), dict) else {}
    trav = loaded.get("travaux") if isinstance(loaded.get("travaux"), dict) else {}
    if trav.get("method") == ROADS_METHOD and isinstance(trav.get("seals"), list):
        state["travaux"]["seals"] = [s for s in trav["seals"] if isinstance(s, dict) and s.get("root")]
        state["travaux"]["latest_record"] = trav.get("latest_record") if isinstance(trav.get("latest_record"), dict) else None
    return state


def seal_edition(state: dict, record: dict) -> tuple[dict, str]:
    """Append (or idempotently replace the last) seal. Returns (state, action)."""
    seals = state["seals"]
    edition = record["edition"]
    if seals:
        last = seals[-1]
        if last.get("edition") == edition:
            prev = seals[-2]["root"] if len(seals) > 1 else ""
            seq = _int(last.get("seq")) or len(seals)
            seals[-1] = _seal(seq, prev, record)
            state["voice"] = [v for v in state["voice"] if v.get("edition") != edition] + [voice_row(record)]
            return state, "replaced"
        if not _after(edition, last.get("edition")):
            return state, "ignored"
        prev = last["root"]
        seq = _int(last.get("seq")) + 1
    else:
        prev, seq = "", 1
    seals.append(_seal(seq, prev, record))
    state["voice"].append(voice_row(record))
    state["voice"] = state["voice"][-VOICE_ROWS_CAP:]
    return state, "appended"


def _seal(seq: int, prev: str, record: dict) -> dict:
    leaf = leaf_of(record)
    return {
        "seq": seq,
        "edition": record["edition"],
        "prev": prev,
        "leaf": leaf,
        "root": chain_hash(prev, leaf),
        "record": record,
    }


def verify_chain(seals: list[dict], anchor_root: str = "") -> tuple[bool, str]:
    """Recompute every leaf and root. anchor_root = root before the first seal given."""
    prev = anchor_root or ""
    for n, seal in enumerate(seals):
        if not isinstance(seal, dict) or not isinstance(seal.get("record"), dict):
            return False, f"seal {n}: malformed"
        if seal.get("prev", "") != prev:
            return False, f"seal {seal.get('seq')}: prev mismatch"
        leaf = leaf_of(seal["record"])
        if seal.get("leaf") != leaf:
            return False, f"seal {seal.get('seq')}: leaf mismatch"
        root = chain_hash(prev, leaf)
        if seal.get("root") != root:
            return False, f"seal {seal.get('seq')}: root mismatch"
        prev = root
    return True, f"{len(seals)} seals verified"


def seal_roadworks(state: dict, roadworks: dict) -> tuple[dict, str]:
    """Hourly official lane: one root per *changed* active obstruction set."""
    rw = roadworks if isinstance(roadworks, dict) else {}
    fetched = parse_ts(rw.get("fetched_at"))
    events = rw.get("events")
    if fetched is None or not isinstance(events, list):
        return state, "no-store"
    active = sorted({str(e.get("event_id")) for e in events if isinstance(e, dict) and e.get("event_id")})
    record = {"method": ROADS_METHOD, "fetched_at": fetched.isoformat(), "active": active}
    signal = digest(canonical(active))
    trav = state["travaux"]
    seals = trav["seals"]
    if seals:
        last = seals[-1]
        if last.get("fetched_at") == record["fetched_at"]:
            prev = seals[-2]["root"] if len(seals) > 1 else ""
            seals[-1] = _roads_seal(_int(last.get("seq")) or len(seals), prev, record, signal)
            trav["latest_record"] = record
            return state, "replaced"
        if not _after(record["fetched_at"], last.get("fetched_at")):
            return state, "ignored"
        if last.get("signal") == signal:
            return state, "unchanged"
        prev, seq = last["root"], _int(last.get("seq")) + 1
    else:
        prev, seq = "", 1
    seals.append(_roads_seal(seq, prev, record, signal))
    trav["seals"] = seals[-ROADS_SEAL_CAP:]
    trav["latest_record"] = record
    return state, "appended"


def _roads_seal(seq: int, prev: str, record: dict, signal: str) -> dict:
    leaf = leaf_of(record)
    return {
        "seq": seq,
        "fetched_at": record["fetched_at"],
        "prev": prev,
        "leaf": leaf,
        "root": chain_hash(prev, leaf),
        "signal": signal,
        "active_count": len(record["active"]),
    }


# --------------------------------------------------------------------------- #
# Derived views
# --------------------------------------------------------------------------- #
def institution_register(state: dict) -> list[dict]:
    """Per-institution voice facts derived from the per-edition voice rows.

    silent_streak counts consecutive *established* editions (at least one
    dossier) from the newest backwards in which the institution did not speak.
    Nothing here is an escalation or a verdict: it is arithmetic over absence.
    """
    rows = sorted((v for v in state.get("voice") or [] if v.get("edition")), key=lambda v: v["edition"])
    names = state.get("names") or {}
    ids: set[str] = set(names)
    for v in rows:
        ids.update(v.get("spoke") or [])
        ids.update(v.get("silent") or [])
    out: list[dict] = []
    established = [v for v in rows if v.get("established")]
    for iid in sorted(ids):
        spoke_eds = [v["edition"] for v in established if iid in (v.get("spoke") or [])]
        silent_eds = [v["edition"] for v in established if iid in (v.get("silent") or [])]
        if not spoke_eds and not silent_eds:
            continue
        streak = 0
        for v in reversed(established):
            if iid in (v.get("silent") or []):
                streak += 1
            elif iid in (v.get("spoke") or []):
                break
            else:
                break
        current = established[-1] if established else None
        meta = names.get(iid) if isinstance(names.get(iid), dict) else {}
        out.append({
            "institution_id": iid,
            "institution_name": str(meta.get("name") or iid),
            "source_kind": str(meta.get("kind") or "media"),
            "editions_spoke": len(spoke_eds),
            "editions_silent": len(silent_eds),
            "last_spoke": spoke_eds[-1] if spoke_eds else None,
            "silent_streak": streak,
            "current": (
                "spoke" if current and iid in (current.get("spoke") or [])
                else "silent" if current and iid in (current.get("silent") or [])
                else "unknown"
            ),
        })
    out.sort(key=lambda r: (0 if r["source_kind"] == "official" else 1, -r["silent_streak"], r["institution_id"]))
    return out


def checkpoint_text(state: dict) -> str:
    seals = state.get("seals") or []
    trav = (state.get("travaux") or {}).get("seals") or []
    if not seals:
        return f"{ORIGIN}\n0\n\n"
    last = seals[-1]
    lines = [ORIGIN, str(last["seq"]), last["root"], "", f"edition {last['edition']}"]
    if trav:
        lines.append(f"travaux {trav[-1]['seq']} {trav[-1]['root']} {trav[-1]['fetched_at']}")
    lines.append(f"method {METHOD}")
    return "\n".join(lines) + "\n"


def public_chain(state: dict) -> dict:
    seals = state.get("seals") or []
    shown = seals[-PUBLIC_SEAL_CAP:]
    anchor = ""
    if len(seals) > len(shown):
        anchor = seals[len(seals) - len(shown) - 1]["root"]
    return {
        "method": METHOD,
        "origin": ORIGIN,
        "size": len(seals),
        "root": seals[-1]["root"] if seals else "",
        "anchor_root": anchor,
        "verify": (
            "leaf = sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'))); "
            "root = sha256(prev + leaf); the first published seal's prev equals anchor_root "
            "(empty for the genesis seal). Script: scripts/registre.py --verify chain.json"
        ),
        "note": (
            "Le silence enregistré est une absence dans les flux collectés par Vigie, "
            "pas la preuve qu'une institution n'a rien dit ailleurs. Identifiants et comptes seulement ; "
            "aucun texte d'éditeur."
        ),
        "seals": shown,
    }


def public_institutions(state: dict) -> dict:
    seals = state.get("seals") or []
    return {
        "method": METHOD,
        "edition": seals[-1]["edition"] if seals else "",
        "edition_count": len([v for v in state.get("voice") or [] if v.get("established")]),
        "note": (
            "Une institution « n'a pas parlé » quand aucun de ses flux suivis n'apparaît dans un dossier "
            "de l'édition. Compteur d'absence, jamais un verdict."
        ),
        "institutions": institution_register(state),
    }


def public_travaux(state: dict) -> dict:
    trav = state.get("travaux") or {}
    seals = trav.get("seals") or []
    return {
        "method": ROADS_METHOD,
        "size": len(seals),
        "root": seals[-1]["root"] if seals else "",
        "latest_record": trav.get("latest_record"),
        "seals": [{k: s.get(k) for k in ("seq", "fetched_at", "prev", "leaf", "root", "active_count")} for s in seals[-PUBLIC_SEAL_CAP:]],
        "note": "Un sceau par changement du jeu d'entraves actives déclaré par la Ville (flux WZDX officiel).",
    }


# --------------------------------------------------------------------------- #
# Human view
# --------------------------------------------------------------------------- #
def _date(value: object) -> str:
    dt = parse_ts(value)
    if dt is None:
        return "—"
    return f'<time datetime="{dt.isoformat()}">{dt:%Y-%m-%d %H:%M} UTC</time>'


def _short(root: str) -> str:
    return esc(root[:12]) if isinstance(root, str) else "—"


def render_registre_html(state: dict) -> str:
    seals = state.get("seals") or []
    last = seals[-1] if seals else None
    register = institution_register(state)
    established_count = len([v for v in state.get("voice") or [] if v.get("established")])
    trav = (state.get("travaux") or {}).get("seals") or []

    if last:
        head_fact = (
            f'<p class="reg-fact"><span class="reg-num">{last["seq"]}</span> édition{"s" if last["seq"] != 1 else ""} scellée{"s" if last["seq"] != 1 else ""}. '
            f'Dernière : {_date(last["edition"])}.</p>'
            f'<p class="reg-root"><span class="eyebrow">RACINE DE LA CHAÎNE</span><code>{esc(last["root"])}</code></p>'
        )
    else:
        head_fact = '<p class="reg-fact">Aucune édition scellée pour l’instant. Le registre commence à la prochaine collecte.</p>'

    officials = [r for r in register if r["source_kind"] == "official"]
    medias = [r for r in register if r["source_kind"] != "official"]

    def _row(r: dict) -> str:
        if r["current"] == "spoke":
            state_txt = "a parlé dans cette édition"
            cls = "spoke"
        elif r["current"] == "silent":
            n = r["silent_streak"]
            state_txt = ("n’a pas parlé dans cette édition" if n <= 1
                         else f"n’a pas parlé depuis <strong>{n}</strong> éditions")
            cls = "silent"
        else:
            state_txt, cls = "état non établi", "unknown"
        last_spoke = f"dernière prise de parole : {_date(r['last_spoke'])}" if r.get("last_spoke") else "aucune prise de parole enregistrée"
        return (
            f'<li class="reg-inst reg-{cls}"><span class="reg-inst-name">{esc(r["institution_name"])}'
            f'{" <span class=\"reg-chip\">officiel</span>" if r["source_kind"] == "official" else ""}</span>'
            f'<span class="reg-inst-state">{state_txt}</span>'
            f'<span class="reg-inst-meta">{last_spoke} · {r["editions_spoke"]} édition{"s" if r["editions_spoke"] != 1 else ""} avec parole · {r["editions_silent"]} sans</span></li>'
        )

    inst_html = ""
    if register:
        inst_html = (
            '<div class="reg-cols">'
            + (f'<div><h3>Institutions officielles</h3><ul class="reg-list">{"".join(_row(r) for r in officials)}</ul></div>' if officials else "")
            + (f'<div><h3>Médias</h3><ul class="reg-list">{"".join(_row(r) for r in medias)}</ul></div>' if medias else "")
            + "</div>"
        )
    else:
        inst_html = '<p class="no-data">Aucune voix enregistrée : il faut au moins une édition avec un dossier.</p>'

    def _seal_row(s: dict) -> str:
        rec = s.get("record") or {}
        v = voice_row(rec)
        led = rec.get("ledger") or {}
        return (
            f'<li class="reg-seal"><a class="reg-seq" href="/memoire/{s["seq"]}.html">n° {s["seq"]}</a>'
            f'<span class="reg-when">{_date(s["edition"])}</span>'
            f'<span class="reg-counts">{len(rec.get("dossiers") or [])} dossier{"s" if len(rec.get("dossiers") or []) != 1 else ""} · '
            f'{len(v["spoke"])} ont parlé · {len(v["silent"])} n’ont pas parlé'
            + (f' · +{len(led.get("new") or [])} / ~{len(led.get("developed") or [])} / −{len(led.get("quiet") or [])}' if led.get("has_previous") else "")
            + f'</span><code class="reg-hash" title="{esc(s["root"])}">{_short(s["root"])}…</code></li>'
        )

    seals_html = (
        f'<ol class="reg-seals" reversed>{"".join(_seal_row(s) for s in reversed(seals[-HTML_SEALS_SHOWN:]))}</ol>'
        if seals else ""
    )
    trav_html = (
        f'<p>{len(trav)} état{"s" if len(trav) != 1 else ""} distinct{"s" if len(trav) != 1 else ""} des entraves actives scellé{"s" if len(trav) != 1 else ""} '
        f'(dernier : {_date(trav[-1]["fetched_at"])}, {trav[-1]["active_count"]} entraves, racine <code>{_short(trav[-1]["root"])}…</code>).</p>'
        if trav else '<p class="no-data">Aucun état des entraves scellé pour l’instant.</p>'
    )

    return f'''<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Le Registre — Vigie</title>
<meta name="description" content="Le registre public de Vigie : chaque édition scellée par sha256, et pour chaque institution suivie, qui a parlé et qui n’a pas parlé, édition après édition.">
<link rel="canonical" href="{SITE_URL}/registre.html"><meta name="robots" content="index, follow">
<link rel="describedby" href="/llms.txt">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"></head>
<body class="reg-body"><a class="skip-link" href="#registre">Aller au registre</a>
<header class="masthead"><a class="wordmark" href="/" aria-label="Vigie, accueil"><svg width="28" height="32" viewBox="0 0 28 32" aria-hidden="true"><path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/></svg>vigie<span class="wordmark-dot">.</span></a><span class="edition">LE REGISTRE</span><nav aria-label="Navigation principale"><a href="/">Le point</a><a href="#voix">Les voix</a><a href="#chaine">La chaîne</a><a href="#verifier">Vérifier</a></nav></header>
<main id="registre">
<section class="intro" aria-labelledby="reg-title"><div><p class="eyebrow">CE QUE PERSONNE NE MESURE : L’ABSENCE</p><h1 id="reg-title">Qui a parlé.<br><em>Qui n’a pas parlé.</em></h1><p class="intro-text">Chaque édition de Vigie est scellée : une empreinte sha256 chaînée à la précédente, publiée ici et lisible par n’importe qui. Le registre garde, institution par institution, la trace de la parole et du silence dans les dossiers collectés. Pas un verdict : une arithmétique de l’absence, vérifiable.</p></div>
<aside class="edition-note" aria-label="État du registre"><p class="eyebrow">ÉTAT DU REGISTRE</p>{head_fact}<p class="fine">Le silence enregistré est une absence dans les flux suivis, jamais la preuve qu’une institution n’a rien dit ailleurs.</p></aside></section>
<section class="reg-section" id="voix" aria-labelledby="voix-title"><div class="section-top"><div><p class="eyebrow">LES VOIX SUIVIES</p><h2 id="voix-title">Le registre des institutions.</h2></div><p class="section-note">{established_count} édition{"s" if established_count != 1 else ""} avec dossiers<br>entrent dans ce compte.</p></div>{inst_html}<p class="fine">Une institution « a parlé » quand un de ses flux suivis apparaît dans un dossier de l’édition (plusieurs flux d’une même institution comptent pour un seul siège). Les compteurs ne s’additionnent qu’aux éditions où au moins un dossier existait.</p></section>
<section class="reg-section" id="chaine" aria-labelledby="chaine-title"><div class="section-top"><div><p class="eyebrow">LA CHAÎNE DES ÉDITIONS</p><h2 id="chaine-title">Scellé, puis chaîné.</h2></div><p class="section-note">Les {min(len(seals), HTML_SEALS_SHOWN)} derniers sceaux.<br>La chaîne complète est dans <a href="/registre/chain.json">chain.json</a> · toutes les éditions : <a href="/memoire.html">la mémoire</a>.</p></div>{seals_html if seals else '<p class="no-data">La chaîne commence à la prochaine édition.</p>'}<h3>Entraves déclarées par la Ville</h3>{trav_html}<p class="fine">Légende des sceaux : dossiers · institutions ayant parlé · institutions n’ayant pas parlé · (+ nouveaux / ~ développés / − disparus depuis l’édition précédente).</p></section>
<section class="method reg-section" id="verifier" aria-labelledby="verif-title"><div><p class="eyebrow">LA CONFIANCE SE VÉRIFIE</p><h2 id="verif-title">Vérifiez-le<br>vous-même.</h2><p>Aucune clé, aucun compte, aucun service tiers. Un terminal suffit.</p></div><div class="method-details"><details open><summary>Comment recalculer la chaîne ?</summary><p>Téléchargez <a href="/registre/chain.json">chain.json</a>. Pour chaque sceau : <code>leaf = sha256(JSON canonique du record)</code> (clés triées, sans espaces, UTF-8) puis <code>root = sha256(prev + leaf)</code>. Le <code>prev</code> du premier sceau publié vaut <code>anchor_root</code> (vide pour le sceau de genèse). La dernière racine doit être celle affichée dans <a href="/registre/checkpoint.txt">checkpoint.txt</a>.</p><p><code>python scripts/registre.py --verify chain.json</code> fait ce calcul, avec la bibliothèque standard seulement.</p></details><details><summary>Que contient un sceau ?</summary><p>Des identifiants et des comptes : les dossiers de l’édition (identifiant, question, nombre d’articles, voix officielles), les institutions qui ont parlé et celles qui n’ont pas parlé, et le journal des changements (nouveaux, développés, disparus). Aucun texte d’éditeur, aucun extrait. Le registre peut être copié et redistribué sans toucher au droit d’auteur de quiconque.</p></details><details><summary>Que prouve-t-il, et que ne prouve-t-il pas ?</summary><p>Il prouve que Vigie a publié une édition donnée avant la suivante, et que cette édition n’a pas été modifiée après coup. Il ne prouve pas la date absolue de publication (aucune autorité d’horodatage) et ne prouve rien sur ce qu’une institution a dit hors des flux suivis. Méthode complète : <a href="/methode/registre.html">la méthode du registre</a>.</p></details><details><summary>Pour les machines</summary><p><a href="/registre/chain.json">chain.json</a> · <a href="/registre/institutions.json">institutions.json</a> · <a href="/registre/travaux.json">travaux.json</a> · <a href="/registre/checkpoint.txt">checkpoint.txt</a> · <a href="/delta/latest.json">delta/latest.json</a> · <a href="/llms.txt">llms.txt</a></p></details></div></section>
</main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>Registre expérimental.</span><a class="legal-link" href="/methode/legal.html">Mentions légales, attribution et retrait</a></footer></body></html>'''


# --------------------------------------------------------------------------- #
# Emit
# --------------------------------------------------------------------------- #
def emit(payload: dict | None = None, roadworks: dict | None = None, *,
         history: dict | None = None, state_path: Path = STATE,
         out_dir: Path = OUT_DIR, out_html: Path = OUT_HTML) -> dict:
    """Update the state from the stores and write every public artefact.

    Returns the updated state. Never raises on bad stores: a missing edition
    simply leaves the chain untouched, and the page still renders.
    """
    payload = payload if isinstance(payload, dict) else load_json(ISSUES)
    history = history if isinstance(history, dict) else load_json(HISTORY)
    roadworks = roadworks if isinstance(roadworks, dict) else load_json(ROADWORKS)
    state = load_state(state_path)
    action = "no-edition"
    record = edition_record(payload, edition_key(payload, history))
    if record is not None:
        for iid, meta in institution_names(payload).items():
            state["names"][iid] = meta
        state, action = seal_edition(state, record)
    state, roads_action = seal_roadworks(state, roadworks)

    state_path.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_json_atomic(state_path, state)
    out_dir.mkdir(parents=True, exist_ok=True)
    store_io.write_json_atomic(out_dir / "chain.json", public_chain(state))
    store_io.write_json_atomic(out_dir / "institutions.json", public_institutions(state))
    store_io.write_json_atomic(out_dir / "travaux.json", public_travaux(state))
    store_io.write_text_atomic(out_dir / "checkpoint.txt", checkpoint_text(state))
    out_html.parent.mkdir(parents=True, exist_ok=True)
    store_io.write_text_atomic(out_html, render_registre_html(state))
    seals = state["seals"]
    print(f"registre: edition {action}, travaux {roads_action}; "
          f"{len(seals)} seals, root {seals[-1]['root'][:12] if seals else '-'} -> {out_html}")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", metavar="CHAIN_JSON", help="Verify a chain.json file and exit")
    args = parser.parse_args()
    if args.verify:
        doc = load_json(Path(args.verify))
        ok, msg = verify_chain(doc.get("seals") or [], str(doc.get("anchor_root") or ""))
        tail = (doc.get("seals") or [{}])[-1].get("root") if doc.get("seals") else ""
        if ok and doc.get("root") and doc.get("root") != tail:
            ok, msg = False, "declared root differs from the last seal"
        print(("OK " if ok else "FAIL ") + msg)
        return 0 if ok else 1
    emit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
