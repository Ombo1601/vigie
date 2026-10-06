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

ENRICHED = ROOT / "data" / "normalized" / "latest_enriched.json"
FEED_HEALTH = ROOT / "data" / "ops" / "feed_health.json"
SOURCES_PATH = ROOT / "sources.yaml"

# Sealed records gained collection facts at schema 2. Schema-1 seals (the ones
# published before the correction) carry no `collection` key, so their voice
# state is reported as not established rather than guessed at.
RECORD_SCHEMA = 2

# Voice states. An institution is NEVER called silent on the strength of
# Vigie's own clustering: a dossier needs a named scar and two institutions, so
# the great majority of collected items never enter one. Measuring "did they
# speak" with "did our clustering group them" reports our rules as their
# behaviour — and, when a feed fails, reports our outage as their silence.
STATE_SPOKE = "spoke"                    # appeared in a dossier of this edition
STATE_PUBLISHED = "published"            # feeds returned items; none clustered
STATE_NO_ITEMS = "no_items"              # feeds answered, zero items in the window
STATE_COLLECTION_GAP = "collection_gap"  # our fetch failed: Vigie's fault, not theirs
STATE_NOT_ESTABLISHED = "not_established"  # sealed before collection facts existed
# Derived at render time from takedowns.yaml, never sealed: a publisher's
# removal applies to the views, the published seals stay byte-identical.
STATE_WITHDRAWN = "withdrawn"            # withdrawn on the publisher's request (R10)
# Derived at render time from sources.yaml, never sealed: Vigie itself stopped
# following the institution (every RSS feed enabled: false + cut_reason/cut_at).
# Only applies once an edition no longer follows it; a measured state wins.
STATE_CUT = "cut"                        # cut by Vigie (its own decision, logged)

# The correction is a constant, never a wall clock: ledgers stay reproducible.
CORRECTION_DATE = "2026-09-30"
CORRECTION_WRONG = (
    "Jusqu’à cette date, le registre définissait « n’a pas parlé » par « absent de tout dossier "
    "de l’édition ». Un dossier exige un sujet nommé et deux institutions : la plupart des articles "
    "collectés n’entrent donc dans aucun dossier. Le registre mesurait les règles de rapprochement "
    "de Vigie, pas la parole des institutions — et il présentait comme un silence institutionnel "
    "des éditions où les flux avaient pourtant été collectés avec succès."
)
CORRECTION_NOW = (
    "Depuis cette date, chaque édition scelle le nombre d’articles réellement collectés par "
    "institution. Une institution dont les flux ont rendu des articles est « a publié, hors dossier » ; "
    "une collecte en échec est signalée comme une lacune de Vigie. Aucun compteur ne s’accumule "
    "contre une institution du fait de nos règles de rapprochement."
)
CORRECTION_CHAIN = (
    "Les sceaux déjà publiés restent octet pour octet identiques : une chaîne ne se réécrit pas. "
    "Seule leur lecture a changé — elle est désormais « non établi » plutôt qu’une accusation."
)

# Includes unpaired surrogates: a JSON escape can carry \ud800, which would
# make canonical() raise UnicodeEncodeError inside the seal.
_SPOOF = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\x7f\xad\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\ud800-\udfff]"
)


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


def institution_collection(source_status: object, sources_path: Path = SOURCES_PATH) -> dict:
    """Per-institution collection facts for one edition: counts only.

    This is the evidence that separates "they said nothing we could see" from
    "we saw plenty" and from "our fetch failed". It reuses
    cluster_issues.collapse_institutions so that "institution" has exactly one
    definition in the codebase — a register that collapsed feeds differently
    from the clustering would be wrong in a new way.

    Fail-soft: any problem yields {} and the register reports the state as not
    established rather than guessing.
    """
    if not isinstance(source_status, dict) or not source_status:
        return {}
    try:
        import cluster_issues  # noqa: PLC0415 - lazy: keeps the module import cheap
        import ingest_rss  # noqa: PLC0415

        chancellery = ingest_rss.load_enabled_rss(Path(sources_path))
        institutions = cluster_issues.collapse_institutions(chancellery)
    except Exception:  # noqa: BLE001 - a missing mapping must never fake a silence
        return {}
    out: dict[str, dict] = {}
    for inst in institutions:
        iid = str(inst.get("institution_id") or "")
        if not iid:
            continue
        feeds = [str(f) for f in (inst.get("feed_ids") or []) if f]
        items = ok = 0
        for fid in feeds:
            row = source_status.get(fid)
            if not isinstance(row, dict):
                continue
            items += _int(row.get("candidate_count"))
            # A feed answered and parsed: "ok" with no parse error. Anything else
            # is our gap, not the institution's silence.
            if row.get("status") == "ok" and not row.get("error"):
                ok += 1
        out[iid] = {"items": items, "feeds_ok": ok, "feeds_total": len(feeds)}
    return out


def edition_record(payload: dict, edition: str, collection: dict | None = None) -> dict | None:
    """The sealed content of one edition: IDs and counts, no publisher text.

    The only text carried is Vigie's own subject label for a dossier
    (truncated, never rewritten). Dossiers labelled by an attributed publisher
    headline carry an empty question and their label_kind, so no publisher
    text is ever frozen in the chain.

    `collection` (schema 2) adds per-institution collected-item counts, so a
    later reader can tell institutional quiet from a Vigie collection gap. It is
    sealed only when present, so replaying a historical edition without those
    facts reproduces the original bytes exactly.
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
        # Only Vigie's own subject labels are sealed. An explicit
        # attributed_headline is the publisher's text: it stays in the brief
        # (attributed, removable the same day under R10) and never enters an
        # immutable record. A missing label_kind is the legacy (pre-attribution)
        # shape, i.e. a Vigie subject label.
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
    record = {
        "method": METHOD,
        "edition": edition,
        "followed": followed,
        "dossiers": dossiers,
        "ledger": ledger,
    }
    if isinstance(collection, dict) and collection:
        keep = set(followed)
        sealed = {
            iid: {k: _int(v.get(k)) for k in ("items", "feeds_ok", "feeds_total")}
            for iid, v in sorted(collection.items())
            if iid in keep and isinstance(v, dict)
        }
        if sealed:
            record["record_schema"] = RECORD_SCHEMA
            record["collection"] = sealed
    return record


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


VOICE_KEYS = ("spoke", "published", "no_items", "collection_gap", "not_established")


def voice_row(record: dict) -> dict:
    """Who spoke, who published without entering a dossier, and where *we* failed.

    An institution absent from every dossier is classified by the collection
    facts sealed with the edition, never by our clustering alone:

      published       its feeds returned items this edition (they were not quiet)
      no_items        its feeds answered but yielded nothing in the window
      collection_gap  at least one of its feeds failed — Vigie's outage, not theirs
      not_established sealed before collection facts existed: no claim is made

    `absent_from_dossiers` stays as the one factual absence the register asserts.
    The old edition-level `silent` list is gone on purpose: any consumer still
    looking for it must be updated rather than keep publishing the false claim.
    """
    record = record if isinstance(record, dict) else {}
    spoke_set: set[str] = set()
    for d in record.get("dossiers") or []:
        if isinstance(d, dict):
            spoke_set.update(str(s) for s in (d.get("spoke") or []) if s)
    followed = sorted({str(i) for i in (record.get("followed") or []) if i})
    established = bool(record.get("dossiers"))
    collection = record.get("collection") if isinstance(record.get("collection"), dict) else {}

    published: list[str] = []
    no_items: list[str] = []
    gap: list[str] = []
    not_established: list[str] = []
    for iid in followed:
        if iid in spoke_set:
            continue
        facts = collection.get(iid)
        if not isinstance(facts, dict):
            not_established.append(iid)
            continue
        feeds_total = _int(facts.get("feeds_total"))
        if feeds_total and _int(facts.get("feeds_ok")) < feeds_total:
            gap.append(iid)
        elif _int(facts.get("items")) > 0:
            published.append(iid)
        else:
            no_items.append(iid)

    return {
        "edition": record.get("edition"),
        "established": established,
        "spoke": sorted(spoke_set & set(followed)) if followed else sorted(spoke_set),
        "published": published,
        "no_items": no_items,
        "collection_gap": gap,
        "not_established": not_established,
        "absent_from_dossiers": sorted(set(followed) - spoke_set) if established else [],
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


def migrate_voice_row(row: dict) -> dict:
    """Bring a pre-correction voice row into the current vocabulary.

    Old rows carry `silent` = followed minus spoke, with no collection facts to
    say which of those institutions actually published. That distinction is
    unknowable after the fact, so the honest migration is `not_established`:
    the institution keeps its place in the history, and no silence is asserted.
    Without this, every institution that only ever appeared in an old `silent`
    list would drop out of the register entirely.
    """
    if any(key in row for key in ("published", "no_items", "collection_gap", "not_established")):
        return row
    legacy = row.get("silent")
    if not isinstance(legacy, list):
        return row
    out = {k: v for k, v in row.items() if k != "silent"}
    out["not_established"] = sorted({str(i) for i in legacy if i})
    return out


def load_state(path: Path = STATE) -> dict:
    loaded = load_json(path)
    if loaded.get("method") != METHOD or not isinstance(loaded.get("seals"), list):
        return empty_state()
    state = empty_state()
    # A seal is only usable by every reader (memoire, substrate, affiche) when
    # its identity fields are present: a corrupt/older store carrying a record
    # without seq/root must render nothing, never KeyError downstream.
    state["seals"] = [
        s for s in loaded["seals"]
        if isinstance(s, dict)
        and isinstance(s.get("record"), dict)
        and isinstance(s.get("seq"), int)
        and isinstance(s.get("root"), str)
        and isinstance(s["record"].get("edition"), str)
        and isinstance(s.get("edition"), str)
    ]
    state["voice"] = [
        migrate_voice_row(v) for v in (loaded.get("voice") or [])
        if isinstance(v, dict) and v.get("edition")
    ]
    state["names"] = loaded.get("names") if isinstance(loaded.get("names"), dict) else {}
    trav = loaded.get("travaux") if isinstance(loaded.get("travaux"), dict) else {}
    if trav.get("method") == ROADS_METHOD and isinstance(trav.get("seals"), list):
        state["travaux"]["seals"] = [s for s in trav["seals"] if isinstance(s, dict) and s.get("root")]
        state["travaux"]["latest_record"] = trav.get("latest_record") if isinstance(trav.get("latest_record"), dict) else None
    return state


def seal_edition(state: dict, record: dict) -> tuple[dict, str]:
    """Append a new seal, or confirm the existing one. Returns (state, action).

    A seal that already exists is **immutable**. Re-rendering the same edition
    must reproduce the same leaf; if it does not — because the record gained a
    field, as it did at schema 2 — the published seal is kept and the divergence
    is printed. Rewriting it would move a root the git anchor already witnessed,
    which is the one thing a chain must never do.
    """
    seals = state["seals"]
    edition = record["edition"]
    if seals:
        last = seals[-1]
        if last.get("edition") == edition:
            recomputed = leaf_of(record)
            if recomputed == last.get("leaf"):
                # Identical bytes: genuinely idempotent, nothing to do.
                return state, "replaced"
            print(f"registre: edition {edition} recomputes to a different leaf "
                  f"({str(last.get('leaf'))[:12]} -> {recomputed[:12]}); keeping the "
                  f"published seal — a chain is append-only, an anchored root never moves")
            return state, "diverged-kept"
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
        try:
            leaf = leaf_of(seal["record"])
            root = chain_hash(prev, leaf)
        except (TypeError, ValueError, UnicodeEncodeError):
            # A hostile/corrupt chain (surrogate text, non-hex prev) is a
            # verification failure, never a traceback.
            return False, f"seal {seal.get('seq')}: uncomputable record"
        if seal.get("leaf") != leaf:
            return False, f"seal {seal.get('seq')}: leaf mismatch"
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
_STATE_RANK = {
    STATE_COLLECTION_GAP: 0,   # our failure first: the register is self-critical
    STATE_NO_ITEMS: 1,
    STATE_NOT_ESTABLISHED: 2,
    STATE_PUBLISHED: 3,
    STATE_SPOKE: 4,
    STATE_WITHDRAWN: 5,
    STATE_CUT: 6,
}


STATE_LABEL_FR = {
    STATE_SPOKE: "a parlé dans un dossier de cette édition",
    STATE_PUBLISHED: "a publié, hors dossier",
    STATE_NO_ITEMS: "aucun article collecté dans la fenêtre de 7 jours",
    STATE_COLLECTION_GAP: "collecte en échec — lacune de Vigie, pas un silence",
    STATE_NOT_ESTABLISHED: "état non établi",
    STATE_WITHDRAWN: "retirée à la demande de l’éditeur",
    STATE_CUT: "plus suivie : source coupée par Vigie, raison consignée",
}

# Short column / counter headings, same single source. "no_items" is its own
# fact (feeds answered, nothing in the window): it is NOT a collection gap.
# "withdrawn" (R10) and "cut" are derived at render time, never sealed: neither
# is a gap nor a silence, and each has its own heading.
STATE_HEADING_FR = {
    STATE_SPOKE: "Dans un dossier",
    STATE_PUBLISHED: "Publié, hors dossier",
    STATE_NO_ITEMS: "Aucun article collecté (flux répondus)",
    STATE_COLLECTION_GAP: "Collecte manquée par Vigie",
    STATE_NOT_ESTABLISHED: "Non établi",
    STATE_WITHDRAWN: "Retirée à la demande de l’éditeur",
    STATE_CUT: "Plus suivie (coupée par Vigie)",
}


def state_heading_fr(state: str) -> str:
    return STATE_HEADING_FR.get(state, STATE_HEADING_FR[STATE_NOT_ESTABLISHED])


def state_label_fr(row: dict) -> str:
    """One honest French sentence for an institution's state in the latest edition.

    Single source of wording: the registre page, the Markdown twin and the
    affiche must not each invent their own phrasing for the same fact.
    """
    stt = str(row.get("current") or STATE_NOT_ESTABLISHED)
    label = STATE_LABEL_FR.get(stt, STATE_LABEL_FR[STATE_NOT_ESTABLISHED])
    if stt == STATE_PUBLISHED and isinstance(row.get("items_collected"), int):
        n = row["items_collected"]
        label += f" ({n} article{'s' if n != 1 else ''} collecté{'s' if n != 1 else ''})"
    if stt == STATE_COLLECTION_GAP and _int(row.get("collection_gap_streak")) > 1:
        label += f" depuis {row['collection_gap_streak']} éditions"
    if stt == STATE_CUT and isinstance(row.get("cut_at"), str) and row["cut_at"]:
        label += f" (coupe du {row['cut_at']})"
    return label


def latest_collection(state: dict) -> dict:
    """Collection facts sealed with the newest edition ({} for schema-1 seals)."""
    seals = state.get("seals") or []
    if not seals:
        return {}
    record = seals[-1].get("record") if isinstance(seals[-1], dict) else None
    collection = record.get("collection") if isinstance(record, dict) else None
    return collection if isinstance(collection, dict) else {}


def withdrawn_institutions(sources_path: Path | None = None) -> dict[str, dict]:
    """Institutions withdrawn on their publisher's request (takedowns.yaml).
    Fail-soft: a problem yields {} — the release gate diagnoses the file."""
    try:
        import takedown  # noqa: PLC0415 - lazy: keeps the module import cheap

        return takedown.withdrawn_institutions(sources_path=sources_path or SOURCES_PATH)
    except Exception:  # noqa: BLE001
        return {}


def cut_institutions(sources_path: Path | None = None) -> dict[str, dict]:
    """Institutions Vigie itself stopped following: every RSS feed of the
    institution is `enabled: false` in sources.yaml (with cut_reason/cut_at).

    One cut sister feed does not cut the voice; the last one does. Derived from
    the published registry, never sealed. Fail-soft: a problem yields {}.
    """
    try:
        import ingest_rss  # noqa: PLC0415 - lazy: keeps the module import cheap

        records = ingest_rss.load_sources(Path(sources_path or SOURCES_PATH))
    except Exception:  # noqa: BLE001 - a missing registry must never fake a state
        return {}
    by_inst: dict[str, list[dict]] = {}
    for rec in records:
        if rec.get("type") != "rss":
            continue
        iid = str(rec.get("institution") or rec.get("id") or "")
        if iid:
            by_inst.setdefault(iid, []).append(rec)
    out: dict[str, dict] = {}
    for iid, feeds in sorted(by_inst.items()):
        if any(f.get("enabled") is True for f in feeds):
            continue
        first = feeds[0]
        dates = sorted(str(f["cut_at"]) for f in feeds if f.get("cut_at"))
        out[iid] = {
            "institution_id": iid,
            "institution_name": str(first.get("institution_name") or first.get("name") or iid),
            "source_kind": "official" if str(first.get("source_kind") or "") == "official" else "media",
            "feed_ids": sorted(str(f.get("id")) for f in feeds),
            "cut_at": dates[-1] if dates else None,
        }
    return out


def institution_register(state: dict, withdrawn: dict[str, dict] | None = None,
                         cut: dict[str, dict] | None = None) -> list[dict]:
    """Per-institution voice facts derived from the per-edition voice rows.

    Only one streak is ever accumulated, and it counts *Vigie's* failure to
    collect — never an institution's supposed silence. "Published, outside a
    dossier" is the normal state of a followed institution, because a dossier
    needs a named scar and two institutions; treating that as silence was the
    defect corrected on CORRECTION_DATE.

    An institution withdrawn on its publisher's request is reported as such
    (STATE_WITHDRAWN) instead of drifting to "not established" or vanishing.
    An institution Vigie cut itself (sources.yaml) and that the newest edition
    no longer follows is reported as STATE_CUT, never as "not established".
    This is a derived view: the sealed records are never touched.
    """
    rows = sorted((v for v in state.get("voice") or [] if v.get("edition")), key=lambda v: str(v["edition"]))
    names = state.get("names") or {}
    collection = latest_collection(state)
    withdrawn = withdrawn if isinstance(withdrawn, dict) else withdrawn_institutions()
    cut = cut if isinstance(cut, dict) else cut_institutions()
    ids: set[str] = set(names) | set(withdrawn)
    for v in rows:
        for key in (*VOICE_KEYS, "absent_from_dossiers"):
            ids.update(str(i) for i in (v.get(key) or []))
    out: list[dict] = []
    for iid in sorted(ids):
        counts = {key: 0 for key in VOICE_KEYS}
        last_spoke = None
        for v in rows:
            for key in VOICE_KEYS:
                if iid in (v.get(key) or []):
                    counts[key] += 1
                    if key == "spoke":
                        last_spoke = v.get("edition")
        if not any(counts.values()) and iid not in withdrawn:
            continue
        gap_streak = 0
        for v in reversed(rows):
            if iid in (v.get("collection_gap") or []):
                gap_streak += 1
            else:
                break
        current = STATE_NOT_ESTABLISHED
        followed_now = False
        if rows:
            newest = rows[-1]
            for key in VOICE_KEYS:
                if iid in (newest.get(key) or []):
                    current = key
                    followed_now = True
                    break
        pulled = withdrawn.get(iid) if isinstance(withdrawn.get(iid), dict) else {}
        dropped = cut.get(iid) if isinstance(cut.get(iid), dict) and not followed_now else {}
        if pulled:
            current = STATE_WITHDRAWN
        elif dropped:
            current = STATE_CUT
        facts = collection.get(iid) if isinstance(collection.get(iid), dict) else {}
        measured = counts["spoke"] + counts["published"] + counts["no_items"] + counts["collection_gap"]
        meta = names.get(iid) if isinstance(names.get(iid), dict) else {}
        row = {
            "institution_id": iid,
            "institution_name": str(meta.get("name") or pulled.get("institution_name")
                                    or dropped.get("institution_name") or iid),
            "source_kind": str(meta.get("kind") or pulled.get("source_kind")
                               or dropped.get("source_kind") or "media"),
            "current": current,
            "items_collected": _int(facts.get("items")) if facts else None,
            "feeds_ok": _int(facts.get("feeds_ok")) if facts else None,
            "feeds_total": _int(facts.get("feeds_total")) if facts else None,
            "editions_spoke": counts["spoke"],
            "editions_published_outside_dossiers": counts["published"],
            "editions_no_items_collected": counts["no_items"],
            "editions_collection_gap": counts["collection_gap"],
            "editions_not_established": counts["not_established"],
            "editions_measured": measured,
            "last_spoke": last_spoke,
            "collection_gap_streak": gap_streak,
        }
        if pulled:
            row["withdrawn_requested_at"] = pulled.get("requested_at")
        elif dropped:
            row["cut_at"] = dropped.get("cut_at")
        out.append(row)
    out.sort(key=lambda r: (
        0 if r["source_kind"] == "official" else 1,
        _STATE_RANK.get(r["current"], 9),
        r["institution_id"],
    ))
    return out


def correction_notice(state: dict) -> dict | None:
    """Which seals were published under the old definition. Derived, never hard-coded.

    The seals themselves are untouched — a chain is not rewritten. This states,
    precisely and verifiably, which of them predate the collection facts, so a
    reader who downloads an old record is not left with a false impression.
    """
    affected = [
        _int(s.get("seq"))
        for s in (state.get("seals") or [])
        if isinstance(s, dict) and isinstance(s.get("record"), dict)
        and not isinstance(s["record"].get("collection"), dict)
    ]
    affected = [n for n in affected if n]
    if not affected:
        return None
    return {
        "method": "registre-correction-v1",
        "corrected_at": CORRECTION_DATE,
        "affects_seal_min": min(affected),
        "affects_seal_max": max(affected),
        "affects_count": len(affected),
        # First seal after the affected range that carries collection facts.
        "first_seal_with_facts": next(
            (n for n in sorted(_int(s.get("seq")) for s in (state.get("seals") or [])
                               if isinstance(s, dict) and isinstance(s.get("record"), dict)
                               and isinstance(s["record"].get("collection"), dict))
             if n > max(affected)),
            None,
        ),
        "wrong": CORRECTION_WRONG,
        "now": CORRECTION_NOW,
        "chain": CORRECTION_CHAIN,
    }


def checkpoint_text(state: dict) -> str:
    seals = state.get("seals") or []
    trav = (state.get("travaux") or {}).get("seals") or []
    if not seals:
        return f"{ORIGIN}\n0\n\n"
    last = seals[-1]
    lines = [ORIGIN, str(last.get("seq", "")), str(last.get("root", "")), "",
             f"edition {last.get('edition', '')}"]
    if trav:
        t = trav[-1]
        lines.append(f"travaux {t.get('seq', '')} {t.get('root', '')} {t.get('fetched_at', '')}")
    lines.append(f"method {METHOD}")
    return "\n".join(lines) + "\n"


def public_chain(state: dict) -> dict:
    seals = state.get("seals") or []
    shown = seals[-PUBLIC_SEAL_CAP:]
    anchor = ""
    if len(seals) > len(shown):
        anchor = seals[len(seals) - len(shown) - 1].get("root", "")
    return {
        "method": METHOD,
        "origin": ORIGIN,
        "size": len(seals),
        "root": seals[-1].get("root", "") if seals else "",
        "anchor_root": anchor,
        "verify": (
            "leaf = sha256(json.dumps(record, sort_keys=True, ensure_ascii=False, separators=(',', ':'))); "
            "root = sha256(prev + leaf); the first published seal's prev equals anchor_root "
            "(empty for the genesis seal). Script: scripts/registre.py --verify chain.json"
        ),
        "note": (
            "Chaque sceau porte les identifiants et les comptes d'une édition, dont le nombre "
            "d'articles réellement collectés par institution. « absent_from_dossiers » est un fait : "
            "l'institution n'est entrée dans aucun dossier de cette édition. Ce n'est pas un silence — "
            "un dossier exige un sujet nommé et deux institutions. Aucun texte d'éditeur."
        ),
        "correction": correction_notice(state),
        "seals": shown,
    }


def public_institutions(state: dict) -> dict:
    seals = state.get("seals") or []
    rows = state.get("voice") or []
    return {
        "method": METHOD,
        "record_schema": RECORD_SCHEMA,
        "edition": seals[-1].get("edition", "") if seals else "",
        "edition_count": len(rows),
        "edition_count_with_dossiers": len([v for v in rows if v.get("established")]),
        "states": {
            STATE_SPOKE: "un de ses flux suivis apparaît dans un dossier de l'édition",
            STATE_PUBLISHED: "ses flux ont rendu des articles cette édition, aucun n'est entré dans un dossier — l'institution n'a pas été silencieuse",
            STATE_NO_ITEMS: "ses flux ont répondu mais n'ont rendu aucun article dans la fenêtre de 7 jours",
            STATE_COLLECTION_GAP: "au moins un de ses flux a échoué : lacune de collecte de Vigie, jamais un silence institutionnel",
            STATE_NOT_ESTABLISHED: "édition scellée avant que les faits de collecte existent : aucune affirmation",
            STATE_WITHDRAWN: "retirée à la demande de l'éditeur (takedowns.yaml) : plus collectée ni relayée ; les sceaux déjà publiés restent intacts",
            STATE_CUT: "plus suivie : tous ses flux sont coupés par Vigie dans sources.yaml (cut_reason, cut_at) ; décision de Vigie, jamais un silence de l'institution ; les sceaux déjà publiés restent intacts",
        },
        "note": (
            "Aucun compteur ne s'accumule contre une institution du fait des règles de rapprochement "
            "de Vigie. Le seul compteur de suite (collection_gap_streak) mesure les échecs de collecte "
            "de Vigie. Une absence de faits est rapportée comme une absence, jamais comme une santé."
        ),
        "correction": correction_notice(state),
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
    correction = correction_notice(state)

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
        items = r.get("items_collected")
        collected = f"{items} article{'s' if items != 1 else ''} collecté{'s' if items != 1 else ''} cette édition" if isinstance(items, int) else "collecte non établie pour cette édition"
        if r["current"] == STATE_SPOKE:
            state_txt, cls = "a parlé dans un dossier de cette édition", "spoke"
        elif r["current"] == STATE_PUBLISHED:
            state_txt, cls = f"a publié, hors dossier — {collected}", "published"
        elif r["current"] == STATE_COLLECTION_GAP:
            n = _int(r.get("collection_gap_streak"))
            state_txt = "collecte en échec — lacune de Vigie"
            if n > 1:
                state_txt += f" depuis <strong>{n}</strong> éditions"
            cls = "gap"
        elif r["current"] == STATE_NO_ITEMS:
            state_txt, cls = "aucun article collecté dans la fenêtre — flux répondus", "noitems"
        elif r["current"] == STATE_WITHDRAWN:
            state_txt, cls = "retirée à la demande de l’éditeur — plus collectée ni relayée", "withdrawn"
        elif r["current"] == STATE_CUT:
            when = f" le {esc(r['cut_at'])}" if isinstance(r.get("cut_at"), str) and r["cut_at"] else ""
            state_txt, cls = (
                f'plus suivie — source coupée par Vigie{when} '
                '(<a href="/methode/sources.html#coupes">raison consignée</a>)', "cut")
        else:
            state_txt, cls = "état non établi (édition antérieure à la correction)", "unknown"
        last_spoke = (f"dernière parole en dossier : {_date(r['last_spoke'])}" if r.get("last_spoke")
                      else "aucune parole en dossier enregistrée")
        measured = _int(r.get("editions_measured"))
        return (
            f'<li class="reg-inst reg-{cls}"><span class="reg-inst-name">{esc(r["institution_name"])}'
            f'{" <span class=\"reg-chip\">officiel</span>" if r["source_kind"] == "official" else ""}</span>'
            f'<span class="reg-inst-state">{state_txt}</span>'
            f'<span class="reg-inst-meta">{last_spoke} · {r["editions_spoke"]} édition{"s" if r["editions_spoke"] != 1 else ""} en dossier · '
            f'{r["editions_published_outside_dossiers"]} publiée{"s" if r["editions_published_outside_dossiers"] != 1 else ""} hors dossier · '
            f'{measured} mesurée{"s" if measured != 1 else ""}</span></li>'
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
        voice_parts = [f'{len(v["spoke"])} en dossier']
        if v["published"]:
            voice_parts.append(f'{len(v["published"])} publié{"s" if len(v["published"]) != 1 else ""} hors dossier')
        if v["collection_gap"]:
            voice_parts.append(f'{len(v["collection_gap"])} en lacune de collecte')
        if v["no_items"]:
            voice_parts.append(f'{len(v["no_items"])} sans article collecté')
        if v["not_established"]:
            voice_parts.append(f'{len(v["not_established"])} non établi{"s" if len(v["not_established"]) != 1 else ""}')
        return (
            f'<li class="reg-seal"><a class="reg-seq" href="/memoire/{s.get("seq", "")}.html">n° {s.get("seq", "")}</a>'
            f'<span class="reg-when">{_date(s.get("edition"))}</span>'
            f'<span class="reg-counts">{len(rec.get("dossiers") or [])} dossier{"s" if len(rec.get("dossiers") or []) != 1 else ""} · '
            + " · ".join(voice_parts)
            + (f' · +{len(led.get("new") or [])} / ~{len(led.get("developed") or [])} / −{len(led.get("quiet") or [])}' if led.get("has_previous") else "")
            + f'</span><code class="reg-hash" title="{esc(s.get("root", ""))}">{_short(s.get("root", ""))}…</code></li>'
        )

    seals_html = (
        f'<ol class="reg-seals" reversed>{"".join(_seal_row(s) for s in reversed(seals[-HTML_SEALS_SHOWN:]))}</ol>'
        if seals else ""
    )
    trav_html = (
        f'<p>{len(trav)} état{"s" if len(trav) != 1 else ""} distinct{"s" if len(trav) != 1 else ""} des entraves actives scellé{"s" if len(trav) != 1 else ""} '
        f'(dernier : {_date(trav[-1].get("fetched_at"))}, {_int(trav[-1].get("active_count"))} entraves, racine <code>{_short(trav[-1].get("root", ""))}…</code>).</p>'
        if trav else '<p class="no-data">Aucun état des entraves scellé pour l’instant.</p>'
    )

    # A correction is published, not applied silently: the affected seals stay
    # byte-identical, and the reader is told which ones predate the fix.
    correction_html = ""
    if correction:
        lo, hi = correction["affects_seal_min"], correction["affects_seal_max"]
        span = f"n° {lo}" if lo == hi else f"n° {lo} à {hi}"
        correction_html = (
            '<section class="reg-correction" id="correction" aria-labelledby="correction-title">'
            '<p class="eyebrow">CORRECTION PUBLIÉE</p>'
            f'<h2 id="correction-title">Le registre a mal mesuré le silence des sceaux {esc(span)}.</h2>'
            f'<p>{esc(correction["wrong"])}</p>'
            f'<p>{esc(correction["now"])}</p>'
            f'<p class="fine">{esc(correction["chain"])} Correction du {_date(correction["corrected_at"])} · '
            f'{correction["affects_count"]} sceau{"s" if correction["affects_count"] != 1 else ""} concerné{"s" if correction["affects_count"] != 1 else ""} · '
            + (f'premier sceau avec mesure de collecte : n° {correction["first_seal_with_facts"]} · '
               if correction.get("first_seal_with_facts") else "") +
            'méthode : <a href="/methode/registre.html">la méthode du registre</a>.</p>'
            '</section>'
        )

    return f'''<!doctype html>
<html lang="fr-CA"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>Le Registre — Vigie</title>
<meta name="description" content="Le registre public de Vigie : chaque édition scellée par sha256, et pour chaque institution suivie, ce qu’elle a publié et ce que notre collecte a manqué, édition après édition.">
<link rel="canonical" href="{SITE_URL}/registre.html"><meta name="robots" content="index, follow">
<link rel="describedby" href="/llms.txt">
<meta name="theme-color" content="#f5f8f8" media="(prefers-color-scheme: light)"><meta name="theme-color" content="#0e1518" media="(prefers-color-scheme: dark)"><meta name="color-scheme" content="light dark"><meta name="referrer" content="no-referrer">
<link rel="icon" href="/favicon.svg" type="image/svg+xml"><link rel="stylesheet" href="/assets/fonts.css"><link rel="stylesheet" href="/assets/brief.css"></head>
<body class="reg-body"><a class="skip-link" href="#registre">Aller au registre</a>
<header class="masthead"><a class="wordmark" href="/" aria-label="Vigie, accueil"><svg width="28" height="32" viewBox="0 0 28 32" aria-hidden="true"><path d="M2 5 14 28 26 5M8 5l6 12 6-12" fill="none" stroke="currentColor" stroke-width="2.5"/></svg>vigie<span class="wordmark-dot">.</span></a><span class="edition">LE REGISTRE</span><nav aria-label="Navigation principale"><a href="/">Le point</a><a href="#voix">Les voix</a><a href="#chaine">La chaîne</a><a href="#verifier">Vérifier</a></nav></header>
<main id="registre">
<section class="intro" aria-labelledby="reg-title"><div><p class="eyebrow">LA PRÉSENCE MESURÉE, JAMAIS PRÉSUMÉE</p><h1 id="reg-title">Qui a parlé.<br><em>Ce que nous avons manqué.</em></h1><p class="intro-text">Chaque édition de Vigie est scellée : une empreinte sha256 chaînée à la précédente, publiée ici et lisible par n’importe qui. Pour chaque institution suivie, le registre distingue ce qu’elle a publié de ce que <strong>notre</strong> collecte a manqué. Une institution absente de nos dossiers n’est pas une institution muette : un dossier exige un sujet nommé et deux voix.</p></div>
<aside class="edition-note" aria-label="État du registre"><p class="eyebrow">ÉTAT DU REGISTRE</p>{head_fact}<p class="fine">Chaque édition scelle le nombre d’articles réellement collectés par institution. Une lacune de collecte est signalée comme la nôtre, jamais comme un silence.</p></aside></section>
{correction_html}
<section class="reg-section" id="voix" aria-labelledby="voix-title"><div class="section-top"><div><p class="eyebrow">LES VOIX SUIVIES</p><h2 id="voix-title">Le registre des institutions.</h2></div><p class="section-note">{established_count} édition{"s" if established_count != 1 else ""} avec dossiers<br>sur {len(state.get("voice") or [])} scellée{"s" if len(state.get("voice") or []) != 1 else ""}.</p></div>{inst_html}<p class="fine">« A parlé » = un de ses flux suivis apparaît dans un dossier de l’édition (plusieurs flux d’une même institution comptent pour un seul siège). « A publié, hors dossier » = ses flux ont rendu des articles, mais aucun n’est entré dans un dossier — ce n’est pas un silence. Nos propres échecs de collecte sont listés en premier : le registre est d’abord critique envers lui-même.</p></section>
<section class="reg-section" id="chaine" aria-labelledby="chaine-title"><div class="section-top"><div><p class="eyebrow">LA CHAÎNE DES ÉDITIONS</p><h2 id="chaine-title">Scellé, puis chaîné.</h2></div><p class="section-note">Les {min(len(seals), HTML_SEALS_SHOWN)} derniers sceaux.<br>La chaîne complète est dans <a href="/registre/chain.json">chain.json</a> · toutes les éditions : <a href="/memoire.html">la mémoire</a>.</p></div>{seals_html if seals else '<p class="no-data">La chaîne commence à la prochaine édition.</p>'}<h3>Entraves déclarées par la Ville</h3>{trav_html}<p class="fine">Légende des sceaux : dossiers · institutions entrées dans un dossier · institutions ayant publié hors dossier · lacunes de collecte de Vigie · (+ nouveaux / ~ développés / − disparus depuis l’édition précédente).</p></section>
<section class="method reg-section" id="verifier" aria-labelledby="verif-title"><div><p class="eyebrow">LA CONFIANCE SE VÉRIFIE</p><h2 id="verif-title">Vérifiez-le<br>vous-même.</h2><p>Aucune clé, aucun compte, aucun service tiers. Un terminal suffit.</p></div><div class="method-details"><details open><summary>Comment recalculer la chaîne ?</summary><p>Téléchargez <a href="/registre/chain.json">chain.json</a>. Pour chaque sceau : <code>leaf = sha256(JSON canonique du record)</code> (clés triées, sans espaces, UTF-8) puis <code>root = sha256(prev + leaf)</code>. Le <code>prev</code> du premier sceau publié vaut <code>anchor_root</code> (vide pour le sceau de genèse). La dernière racine doit être celle affichée dans <a href="/registre/checkpoint.txt">checkpoint.txt</a>.</p><p><code>python scripts/registre.py --verify chain.json</code> fait ce calcul, avec la bibliothèque standard seulement.</p></details><details><summary>Que contient un sceau ?</summary><p>Des identifiants et des comptes : les dossiers de l’édition (identifiant, question, nombre d’articles, voix officielles), les institutions entrées dans un dossier, et — depuis la correction — le nombre d’articles réellement collectés par institution, qui sépare un flux muet d’un flux que nous n’avons pas su lire. Aucun texte d’éditeur, aucun extrait. Le registre peut être copié et redistribué sans toucher au droit d’auteur de quiconque.</p></details><details><summary>Que prouve-t-il, et que ne prouve-t-il pas ?</summary><p>Il prouve que Vigie a publié une édition donnée avant la suivante, et que cette édition n’a pas été modifiée après coup. Il ne prouve pas la date absolue de publication (aucune autorité d’horodatage) et ne prouve rien sur ce qu’une institution a dit hors des flux suivis. Méthode complète : <a href="/methode/registre.html">la méthode du registre</a>.</p></details><details><summary>Pour les machines</summary><p><a href="/registre/chain.json">chain.json</a> · <a href="/registre/institutions.json">institutions.json</a> · <a href="/registre/travaux.json">travaux.json</a> · <a href="/registre/checkpoint.txt">checkpoint.txt</a> · <a href="/delta/latest.json">delta/latest.json</a> · <a href="/llms.txt">llms.txt</a></p></details></div></section>
</main>
<footer><a class="wordmark" href="/">vigie<span class="wordmark-dot">.</span></a><p>Un peu plus au courant.<br>Un peu plus libre de votre temps.</p><span>Fait pour Québec.<br>Registre expérimental.</span><a class="legal-link" href="/methode/legal.html">Mentions légales, attribution et retrait</a></footer></body></html>'''


# --------------------------------------------------------------------------- #
# Emit
# --------------------------------------------------------------------------- #
def collection_for_edition(edition: str, enriched: dict | None = None, *,
                          enriched_path: Path = ENRICHED,
                          sources_path: Path = SOURCES_PATH) -> dict:
    """Collection facts, but only when they belong to *this* edition.

    Attaching the wrong edition's counts would be worse than attaching none:
    the seal would look measured while being false. So the enriched store's own
    collection clock must equal the edition key, otherwise no facts are sealed
    and the register honestly reports the state as not established.
    """
    enriched = enriched if isinstance(enriched, dict) else load_json(enriched_path)
    status = enriched.get("source_status")
    if not isinstance(status, dict) or not status:
        return {}
    stamp = parse_ts(enriched.get("normalized_at"))
    edition_dt = parse_ts(edition)
    if not stamp or not edition_dt or stamp != edition_dt:
        return {}
    return institution_collection(status, sources_path)


def emit(payload: dict | None = None, roadworks: dict | None = None, *,
         history: dict | None = None, collection: dict | None = None,
         state_path: Path = STATE,
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
    edition = edition_key(payload, history)
    if collection is None:
        collection = collection_for_edition(edition, sources_path=SOURCES_PATH)
    record = edition_record(payload, edition, collection)
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
    root_short = str(seals[-1].get("root", ""))[:12] if seals else "-"
    print(f"registre: edition {action}, travaux {roads_action}; "
          f"{len(seals)} seals, root {root_short}; "
          f"collection facts for {len(record.get('collection') or {}) if record else 0} institutions -> {out_html}")
    return state


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", metavar="CHAIN_JSON", help="Verify a chain.json file and exit")
    args = parser.parse_args()
    if args.verify:
        doc = load_json(Path(args.verify))
        raw_seals = doc.get("seals") if isinstance(doc.get("seals"), list) else []
        ok, msg = verify_chain(raw_seals, str(doc.get("anchor_root") or ""))
        last = raw_seals[-1] if raw_seals and isinstance(raw_seals[-1], dict) else {}
        tail = last.get("root") if isinstance(last.get("root"), str) else ""
        if ok and doc.get("root") and doc.get("root") != tail:
            ok, msg = False, "declared root differs from the last seal"
        print(("OK " if ok else "FAIL ") + msg)
        return 0 if ok else 1
    emit()
    return 0


if __name__ == "__main__":
    sys.exit(main())
