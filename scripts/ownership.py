"""Ownership — who owns each followed institution (docs/EVENTS.md sections 7-8).

The declaration lives in `sources.yaml`, one per source entry (sister feeds of
one institution repeat it):

    ownership_class   public_broadcaster | quebecor | cooperative | independent
                      | government | unverified
    owner_group       one id per owner; members sharing it are one
                      independence group (Radio-Canada and CBC: one group)
    ownership_ref     a public https page that states the ownership
    ownership_asof    ISO date Vigie last read that page

Ownership is a factual claim about a publisher, so a declaration that is not
complete and sourced is read as `unverified`, never guessed: its owner_group is
not used, and the institution counts as its own group. Vigie counts groups; it
never judges an owner.

How events use it (docs/AUTONOMY.md, no one decides): members of one sourced
controlling owner are one origin. A grouping is the sourced fact, not an
opinion: Hydro-Québec shares `etat-quebec` with the Government of Quebec
because its own annual report (its `ownership_ref`) states that the State is
its sole shareholder. And it matters less than it seems: official voices are
DECLARATIONS, counted apart from media reporting (events.py keys them
`declaration:<group>`, never `owner:<group>`), so an owner group shared by an
official source can neither inflate nor deflate a media origin count.
`declares(source_id)` says which side of that line a source stands on.

Fail-soft: a missing or malformed registry prints one diagnosis and yields
empty facts (every source `unverified`), never an exception. No network.

    python -X utf8 scripts/ownership.py           # the declared table
    python -X utf8 scripts/ownership.py --check   # exit 1 on an invalid declaration
"""
from __future__ import annotations

import argparse
import re
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
SOURCES_PATH = ROOT / "sources.yaml"

UNVERIFIED = "unverified"
CLASSES = ("public_broadcaster", "quebecor", "cooperative", "independent", "government", UNVERIFIED)
FIELDS = ("ownership_class", "owner_group", "ownership_ref", "ownership_asof")
LANGUAGES = ("fr", "en")
_GROUP_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

_CACHE: dict[str, tuple[tuple, dict[str, dict]]] = {}


# --------------------------------------------------------------------------- #
# Loading
# --------------------------------------------------------------------------- #
def load(path: Path | str | None = None) -> dict[str, dict]:
    """{source_id: record} for every entry of the `sources:` block, enabled or cut.

    Reuses the chancellery's own minimal parser (ingest_rss.load_sources).
    Cached per file identity (path, size, mtime). Fail-soft: {} on any fault."""
    p = Path(path) if path is not None else SOURCES_PATH
    try:
        st = p.stat()
        stamp = (st.st_size, st.st_mtime_ns)
        key = str(p.resolve())
        hit = _CACHE.get(key)
        if hit is not None and hit[0] == stamp:
            return hit[1]
        import ingest_rss  # noqa: PLC0415 - lazy: keeps the module import cheap

        records = ingest_rss.load_sources(p)
        out = {str(rec["id"]): rec for rec in records if rec.get("id")}
        _CACHE[key] = (stamp, out)
        return out
    except (Exception, SystemExit) as exc:  # noqa: BLE001 - SystemExit: the parser's "no sources block"
        print(f"ownership: could not read {p} ({type(exc).__name__}: {exc}); every source reads as {UNVERIFIED}")
        return {}


def _index(sources) -> dict[str, dict]:
    if sources is None:
        return load()
    if isinstance(sources, dict):
        return {str(k): v for k, v in sources.items() if isinstance(v, dict)}
    out: dict[str, dict] = {}
    try:
        for rec in sources:
            if isinstance(rec, dict) and rec.get("id"):
                out[str(rec["id"])] = rec
    except TypeError:
        return {}
    return out


# --------------------------------------------------------------------------- #
# Checks on one declaration
# --------------------------------------------------------------------------- #
def is_https_ref(value) -> bool:
    if not isinstance(value, str) or not value or any(ch.isspace() for ch in value):
        return False
    try:
        parts = urlsplit(value)
    except ValueError:
        return False
    return parts.scheme == "https" and bool(parts.hostname) and "." in (parts.hostname or "")


def is_iso_date(value) -> bool:
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _problems(rec: dict) -> list[str]:
    """What makes this record's declaration incomplete or invalid (empty = sound)."""
    problems: list[str] = []
    cls = rec.get("ownership_class")
    if cls not in CLASSES:
        problems.append(f"ownership_class {cls!r} is not one of {', '.join(CLASSES)}")
    group = rec.get("owner_group")
    if not isinstance(group, str) or not _GROUP_RE.match(group):
        problems.append(f"owner_group {group!r} is not a lowercase-hyphen id")
    if cls != UNVERIFIED:
        if not is_https_ref(rec.get("ownership_ref")):
            problems.append(f"ownership_ref {rec.get('ownership_ref')!r} is not a public https URL")
    elif rec.get("ownership_ref") not in (None, "") and not is_https_ref(rec.get("ownership_ref")):
        problems.append(f"ownership_ref {rec.get('ownership_ref')!r} is not a public https URL")
    if not is_iso_date(rec.get("ownership_asof")):
        problems.append(f"ownership_asof {rec.get('ownership_asof')!r} is not an ISO date")
    return problems


def _verified(rec: dict | None) -> bool:
    return isinstance(rec, dict) and rec.get("ownership_class") != UNVERIFIED and not _problems(rec)


# --------------------------------------------------------------------------- #
# Public helpers
# --------------------------------------------------------------------------- #
def ownership_class_of(source_id, sources=None) -> str:
    """The declared class, or `unverified` when the declaration is missing,
    incomplete, unsourced, or the source is unknown."""
    rec = _index(sources).get(str(source_id))
    if not _verified(rec):
        return UNVERIFIED
    return str(rec["ownership_class"])


def owner_group_of(source_id, sources=None) -> str | None:
    """The declared owner group, only for a sound, sourced declaration; else None."""
    rec = _index(sources).get(str(source_id))
    if not _verified(rec):
        return None
    return str(rec["owner_group"])


def independence_key(source_id, sources=None) -> str:
    """The key that joins members into one independence group (EVENTS.md section 8).

    `owner:<group>` for a sourced declaration (Radio-Canada and CBC share
    `owner:cbc-radio-canada`); otherwise `institution:<id>` (sister feeds stay
    one voice, but no ownership link is claimed without a source); a source
    unknown to the registry is `source:<id>`."""
    sid = str(source_id)
    idx = _index(sources)
    rec = idx.get(sid)
    if _verified(rec):
        return f"owner:{rec['owner_group']}"
    if isinstance(rec, dict) and rec.get("institution"):
        return f"institution:{rec['institution']}"
    return f"source:{sid}"


def declares(source_id, sources=None) -> bool:
    """True when the source is official (`source_kind: official`, sources.yaml):
    its items are declarations, shown as anchors ("déclaré par"), never counted
    as independent corroboration of media reporting. Unknown source: False."""
    rec = _index(sources).get(str(source_id))
    return isinstance(rec, dict) and str(rec.get("source_kind") or "").strip().lower() == "official"


def declaration(source_id, sources=None) -> dict:
    """The ownership facts of one source as stored-ready values (sorted keys)."""
    sid = str(source_id)
    rec = _index(sources).get(sid) or {}
    ok = _verified(rec)
    return {
        "institution": str(rec.get("institution") or "") or None,
        "owner_group": str(rec["owner_group"]) if ok else None,
        "ownership_asof": str(rec.get("ownership_asof")) if ok else None,
        "ownership_class": str(rec["ownership_class"]) if ok else UNVERIFIED,
        "ownership_ref": str(rec.get("ownership_ref")) if ok else None,
        "source_id": sid,
    }


def validate(sources=None) -> list[str]:
    """Every problem in the registry's ownership declarations, sorted.

    Each source needs the four fields (ownership_ref may be absent only when
    the class is `unverified`) and a declared language (fr/en); sister feeds of
    one institution must declare the same ownership."""
    idx = _index(sources)
    errors: list[str] = []
    by_inst: dict[str, list[tuple[str, tuple]]] = {}
    for sid in sorted(idx):
        rec = idx[sid]
        for problem in _problems(rec):
            errors.append(f"{sid}: {problem}")
        if rec.get("language") not in LANGUAGES:
            errors.append(f"{sid}: language {rec.get('language')!r} is not one of {', '.join(LANGUAGES)}")
        inst = str(rec.get("institution") or "")
        if inst:
            by_inst.setdefault(inst, []).append((sid, tuple(rec.get(f) for f in FIELDS)))
    for inst in sorted(by_inst):
        decls = {d for _, d in by_inst[inst]}
        if len(decls) > 1:
            ids = ", ".join(s for s, _ in by_inst[inst])
            errors.append(f"institution {inst}: sister feeds declare different ownership ({ids})")
    classes_by_group: dict[str, set[str]] = {}
    for sid in sorted(idx):
        rec = idx[sid]
        if _verified(rec):
            classes_by_group.setdefault(str(rec["owner_group"]), set()).add(str(rec["ownership_class"]))
    for group in sorted(classes_by_group):
        if len(classes_by_group[group]) > 1:
            errors.append(f"owner_group {group}: declared with several classes ({', '.join(sorted(classes_by_group[group]))})")
    return errors


def table(sources=None) -> list[dict]:
    """One row per institution (sorted): its sound declaration or `unverified`."""
    idx = _index(sources)
    rows: dict[str, dict] = {}
    for sid in sorted(idx):
        inst = str(idx[sid].get("institution") or sid)
        if inst in rows:
            continue
        row = declaration(sid, idx)
        row["institution"] = inst
        row["independence_key"] = independence_key(sid, idx)
        row["declares"] = declares(sid, idx)
        rows[inst] = row
    return [rows[k] for k in sorted(rows)]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--sources", default=str(SOURCES_PATH))
    ap.add_argument("--check", action="store_true", help="exit 1 when a declaration is invalid")
    args = ap.parse_args(argv)
    idx = load(args.sources)
    for row in table(idx):
        print(f"{row['institution']:<20} {row['ownership_class']:<19} {row['independence_key']:<28} "
              f"{'declares' if row['declares'] else 'reports':<9} {row['ownership_asof'] or '-'} "
              f"{row['ownership_ref'] or '-'}")
    errors = validate(idx) if idx else ["no sources could be read"]
    for err in errors:
        print(f"ownership: {err}")
    print(f"ownership: {len(idx)} source(s), {len(errors)} problem(s)")
    return 1 if (args.check and errors) else 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.exit(main())
