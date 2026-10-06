"""Gate evaluator for the event layer (docs/EVENTS.md section 16, MIGRATION step 6).

python -X utf8 scripts/events_eval.py                      # all four gates, from the private stores
python -X utf8 scripts/events_eval.py --gates 1,4          # a subset
python -X utf8 scripts/events_eval.py --out data/ops/events_quality.json

Computes the four gates that must be green before events replace dossiers on
the front door, from PRIVATE inputs under data/ (gitignored), and writes
COUNTS ONLY to data/ops/events_quality.json: no title, excerpt, URL, quote or
person's name ever reaches the output file or the console.

  1. Tier precision / recall of the shipped matcher (scripts/event_match.py,
     frozen weights and thresholds) on the private gold (data/eval/gold_v0.json),
     with the gold's own labelers_provenance caveat. The gate is met only when
     the 95 % lower bound of the `certain` precision reaches 0.95 AND the gold
     is human-verified (a model-labelled gold can rank designs, never open
     the gate).
  2. Publisher-text leak scan: sealed records, permanent pages and the event
     store, against every title and excerpt of the item stores, by folded word
     4-grams. Counts only.
  3. Id stability: every stamped snapshot replayed in order through the sticky
     matcher; 0 event ids lost and 0 memberships moved.
  4. Main-chain byte identity: the frozen golden fixture is re-emitted by the
     current registre code and compared leaf by leaf and root by root; a live
     state, when present, is re-verified and compared with the git anchor.

Fail-soft: an absent input skips the gate with a stated reason (never a pass),
a fault inside a gate is recorded as that gate's reason, and the exit code is
always 0. Deterministic: no wall clock, sorted keys, no timings in the output.
Python standard library only; it does not import eval/ (the dependency runs
the other way).
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import math
import re
import sys
from datetime import timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(Path(__file__).resolve().parent) not in sys.path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))

import event_lexicon as lex  # noqa: E402
import event_match as em  # noqa: E402
import registre  # noqa: E402
import store_io  # noqa: E402

SCHEMA = 1
METHOD = "events-quality-v1 counts-only"
CERTAIN_GATE = 0.95
LABELS = ("same_event", "related", "different")
LANG_PAIRS = ("fr-fr", "fr-en", "en-en")
GRAM_WORDS = 4
MIN_GRAM_CHARS = 16        # a 4-gram shorter than this is a phrase of small words
MIN_CONTENT_WORD = 5       # a content word has at least this many letters
MIN_CONTENT_WORDS = 2      # and a gram needs this many of them
EXCERPT_CHARS = 600
WINDOW_DAYS = em.EDITION_WINDOW_DAYS
NO_PROVENANCE = ("NOT RECORDED in the gold file: treat every label as unverified "
                 "(set `labelers_provenance` in the gold JSON)")
GATE_NAMES = {
    "1": "gate1_tier_precision",
    "2": "gate2_publisher_text_leak",
    "3": "gate3_id_stability",
    "4": "gate4_main_chain_identity",
}
CAVEAT = ("Counts only. Gate 1 figures are relative to the gold labels: when the provenance is a "
          "language model, they rank designs and are not a measured real-world precision.")


class Skip(Exception):
    """An input is absent or unusable: the gate is skipped with this reason."""


# ---------------------------------------------------------------- small maths
def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    """95 % Wilson score interval for k successes in n (honest at 0 and 1)."""
    if n <= 0:
        return 0.0, 1.0
    p = k / n
    denom = 1.0 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def zero_error_pairs_needed(target: float = CERTAIN_GATE) -> int:
    """Smallest n for which n correct pairs out of n put the Wilson lower bound at target."""
    n = 1
    while wilson(n, n)[0] < target and n < 100000:
        n += 1
    return n


def _r(x: float | None, digits: int = 4) -> float | None:
    return None if x is None else round(float(x), digits)


def band_counts(rows: list[dict], members: tuple[str, ...]) -> dict:
    """Counts and precision/recall for one band of tiers over scored gold rows."""
    positives = sum(1 for r in rows if r["label"] == "same_event")
    pred = [r for r in rows if r["tier"] in members]
    tp = sum(1 for r in pred if r["label"] == "same_event")
    rel = sum(1 for r in pred if r["label"] == "related")
    lo, hi = wilson(tp, len(pred))
    return {
        "n": len(rows), "positives": positives, "predicted": len(pred), "tp": tp,
        "related": rel, "different": len(pred) - tp - rel,
        "precision": _r(tp / len(pred)) if pred else None,
        "recall": _r(tp / positives) if positives else None,
        "precision_ci95": [_r(lo), _r(hi)] if pred else None,
    }


# ---------------------------------------------------------------- loading
def _read_json(path: Path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_gold(path: Path) -> dict:
    if not path.exists():
        raise Skip(f"no gold set at {path} (it lives under the private data/ tree)")
    gold = _read_json(path)
    pairs = gold.get("pairs") if isinstance(gold, dict) else None
    if not isinstance(pairs, list) or not pairs:
        raise Skip(f"gold set {path} is unreadable or has no pairs")
    clean = [p for p in pairs if isinstance(p, dict) and p.get("label") in LABELS
             and p.get("split") in ("dev", "test") and p.get("a_id") and p.get("b_id")]
    if not clean:
        raise Skip(f"gold set {path} has no well-formed pair")
    out = dict(gold)
    out["pairs"] = sorted(clean, key=lambda p: str(p.get("pair_id") or (p["a_id"], p["b_id"])))
    return out


def provenance_of(gold: dict) -> str:
    text = gold.get("labelers_provenance") if isinstance(gold, dict) else None
    return text.strip() if isinstance(text, str) and text.strip() else NO_PROVENANCE


def item_files(data_dir: Path) -> list[Path]:
    files = sorted(data_dir.glob("*_candidates.json"))
    latest = data_dir / "latest_enriched.json"
    if latest.exists():
        files.append(latest)
    return files


def _candidates(payload) -> list[dict]:
    cands = payload.get("candidates") if isinstance(payload, dict) else payload
    return [c for c in (cands if isinstance(cands, list) else []) if isinstance(c, dict) and c.get("id")]


def load_items(data_dir: Path) -> dict[str, dict]:
    """Every unique item of the private stores, keyed by id (first copy wins)."""
    if not data_dir.is_dir():
        raise Skip(f"no item store at {data_dir} (gitignored; unpack the private state)")
    items: dict[str, dict] = {}
    for path in item_files(data_dir):
        for c in _candidates(_read_json(path)):
            items.setdefault(str(c["id"]), c)
    if not items:
        raise Skip(f"no candidate items under {data_dir}")
    return items


def current_items(data_dir: Path) -> dict[str, dict]:
    """The current edition's items: latest_candidates.json plus latest_enriched.json."""
    out: dict[str, dict] = {}
    for name in ("latest_candidates.json", "latest_enriched.json"):
        p = data_dir / name
        if p.exists():
            for c in _candidates(_read_json(p)):
                out.setdefault(str(c["id"]), c)
    return out


# ---------------------------------------------------------------- gate 1
def gate_tiers(gold: dict, items: dict[str, dict]) -> dict:
    """Tier precision/recall of the shipped matcher on the gold pairs."""
    ctx = em.MatchContext([items[k] for k in sorted(items)])
    rows, missing = [], 0
    for p in gold["pairs"]:
        a, b = items.get(str(p["a_id"])), items.get(str(p["b_id"]))
        if a is None or b is None:
            missing += 1
            continue
        s, t = em.pair_tier(a, b, ctx)
        lp = "-".join(sorted((em.language_of(a), em.language_of(b)), key=lambda x: (x != "fr", x)))
        rows.append({"split": p["split"], "label": p["label"], "tier": t, "lang_pair": lp})
    if not rows:
        raise Skip("none of the gold pair ids resolve in the item store")
    bands = {"certain": ("certain",), "grouped": ("certain", "probable")}
    out: dict = {"status": "measured", "matcher": em.METHOD, "thresholds": dict(em.THRESHOLDS),
                 "pairs": len(gold["pairs"]), "resolved": len(rows), "missing": missing,
                 "gold_version": gold.get("version"), "gold_kappa": gold.get("kappa"),
                 "provenance": provenance_of(gold), "labelers": sorted(str(x) for x in gold.get("labelers") or []),
                 "human_verified": bool(gold.get("human_verified")),
                 "splits": {}}
    for split in ("test", "dev", "all"):
        sub = [r for r in rows if split == "all" or r["split"] == split]
        entry = {"n": len(sub),
                 "label_counts": {lab: sum(1 for r in sub if r["label"] == lab) for lab in LABELS},
                 "tier_by_label": {lab: {str(t): sum(1 for r in sub if r["label"] == lab and r["tier"] == t)
                                         for t in ("certain", "probable", "possible", None)}
                                   for lab in LABELS},
                 "bands": {}}
        # JSON keys: None -> "none"
        entry["tier_by_label"] = {lab: {("none" if k == "None" else k): v for k, v in d.items()}
                                  for lab, d in entry["tier_by_label"].items()}
        for band, members in bands.items():
            entry["bands"][band] = {"all": band_counts(sub, members)}
            for lp in LANG_PAIRS:
                lps = [r for r in sub if r["lang_pair"] == lp]
                if lps:
                    entry["bands"][band][lp] = band_counts(lps, members)
        out["splits"][split] = entry
    c = out["splits"]["test"]["bands"]["certain"]["all"]
    lower = c["precision_ci95"][0] if c["precision_ci95"] else None
    point = c["precision"]
    out["certain_test"] = {
        "predicted": c["predicted"], "tp": c["tp"], "precision": point, "precision_lower95": lower,
        "meets_point_estimate": point is not None and point >= CERTAIN_GATE,
        "meets_lower_bound": lower is not None and lower >= CERTAIN_GATE,
        "zero_error_pairs_needed": zero_error_pairs_needed(),
    }
    if out["human_verified"] and out["certain_test"]["meets_lower_bound"]:
        out["verdict"] = "met"
        out["reason"] = "certain-tier lower 95% bound >= 0.95 on a human-verified gold"
    elif not out["human_verified"]:
        out["verdict"] = "not_met"
        out["reason"] = ("the gold is not human-verified (model-labelled): its figures rank designs "
                         "and can never open the gate")
    else:
        out["verdict"] = "not_met"
        out["reason"] = "certain-tier lower 95% bound is below 0.95"
    return out


# ---------------------------------------------------------------- gate 2
_URL = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
_TAG = re.compile(r"<[^>]*>")


def folded_text(text: object) -> str:
    """A text after URL and tag removal and folding (lowercase, no accents, single spaces)."""
    return lex.fold(html.unescape(_TAG.sub(" ", _URL.sub(" ", str(text or "")))))


def distinctive(gram: str) -> bool:
    """Two content words and enough letters: a phrase of small words proves nothing."""
    words = gram.split()
    return (len(gram.replace(" ", "")) >= MIN_GRAM_CHARS
            and sum(1 for w in words if len(w) >= MIN_CONTENT_WORD) >= MIN_CONTENT_WORDS)


def grams_of(text: object, n: int = GRAM_WORDS, mask: tuple[str, ...] = ()) -> set[str]:
    """Distinctive folded word n-grams of a text. `mask` holds folded Vigie names
    (institutions): they are blanked first, so a gram never straddles one."""
    folded = " " + folded_text(text) + " "
    for name in mask:
        folded = folded.replace(" " + name + " ", " # ")
    out: set[str] = set()
    for run in folded.split("#"):
        words = run.split()
        out |= {g for g in (" ".join(words[i:i + n]) for i in range(len(words) - n + 1)) if distinctive(g)}
    return out


def item_gram_index(items: dict[str, dict]) -> dict[str, set[str]]:
    """gram -> ids of the items whose title or excerpt carries it."""
    index: dict[str, set[str]] = {}
    for iid in sorted(items):
        it = items[iid]
        # title and excerpt separately, so a gram never straddles the two
        for part in (str(it.get("title") or ""), str(it.get("summary") or "")[:EXCERPT_CHARS]):
            for g in grams_of(part):
                index.setdefault(g, set()).add(iid)
    return index


def vigie_grams(root: Path) -> set[str]:
    """Vigie's own words, which may legitimately appear in sealed records and pages:
    the subject-label questions sealed in the registre and the vocabulary labels."""
    out: set[str] = set()
    state = _read_json(root / "data" / "registre" / "registre.json")
    for seal in (state.get("seals") if isinstance(state, dict) else None) or []:
        rec = seal.get("record") if isinstance(seal, dict) else None
        for d in (rec.get("dossiers") if isinstance(rec, dict) else None) or []:
            if isinstance(d, dict) and d.get("label_kind", "subject_label") != "attributed_headline":
                out |= grams_of(d.get("question"))
    try:
        import vocabulaire as voc

        v = voc.vocabulary()
        for t in v.get("types", []):
            for lang in ("fr", "en"):
                out |= grams_of(voc.type_label(t["code"], lang))
        for p in v.get("places", []):
            for lang in ("fr", "en"):
                out |= grams_of(voc.place_label(p["code"], lang))
    except Exception:  # noqa: BLE001  (an unreadable vocabulary only shrinks the allow-list)
        pass
    return out


def vigie_names(root: Path, items: dict[str, dict]) -> tuple[str, ...]:
    """Folded names of the institutions and sources (Vigie's own words, found
    verbatim in registre pages), longest first."""
    names: set[str] = set()
    state = _read_json(root / "data" / "registre" / "registre.json")
    for meta in ((state.get("names") if isinstance(state, dict) else None) or {}).values():
        if isinstance(meta, dict):
            names.add(folded_text(meta.get("name")))
    for it in items.values():
        for key in ("institution_name", "source_name"):
            names.add(folded_text(it.get(key)))
    return tuple(sorted((n for n in names if len(n) >= 4), key=lambda n: (-len(n), n)))


def leak_targets(root: Path) -> dict[str, list[Path]]:
    """The files that must never carry publisher text, by category."""
    roots = [root / "public", root / "deploy" / "public"]
    sealed = [root / "data" / "registre" / "registre.json"]
    pages: list[Path] = []
    store = [root / "data" / "events" / "latest_events.json", root / "data" / "events" / "store.json"]
    for base in roots:
        sealed += [base / "registre" / n for n in ("chain.json", "institutions.json", "travaux.json", "checkpoint.txt")]
        pages += [base / "memoire.html", base / "registre.html"]
        pages += sorted((base / "memoire").glob("*.html")) if (base / "memoire").is_dir() else []
        pages += sorted((base / "evenements").glob("*.html")) if (base / "evenements").is_dir() else []
        store.append(base / "evenements" / "latest.json")
    return {"sealed_records": sealed, "permanent_pages": pages, "event_store": store}


def gate_leak(root: Path, data_dir: Path, targets: dict[str, list[Path]] | None = None) -> dict:
    """Scan sealed records, permanent pages and the event store for publisher text."""
    history = load_items(data_dir)
    current = current_items(data_dir) or history
    targets = leak_targets(root) if targets is None else targets
    index = item_gram_index(history)
    cur_ids = set(current)
    allowed = vigie_grams(root)
    mask = vigie_names(root, history)
    out: dict = {"status": "measured", "gram_words": GRAM_WORDS, "min_gram_chars": MIN_GRAM_CHARS,
                 "min_content_words": MIN_CONTENT_WORDS,
                 "history_items": len(history), "current_items": len(current),
                 "distinct_item_grams": len(index), "vigie_allowed_grams": len(allowed),
                 "categories": {}}
    scanned_total = hits_total = 0
    for cat in sorted(targets):
        files = [p for p in dict.fromkeys(targets[cat]) if p.is_file()]
        row = {"files_scanned": 0, "files_with_hits": 0, "grams_matched": 0, "items_matched": 0,
               "items_matched_current": 0, "explained_by_vigie_label": 0}
        matched_items: set[str] = set()
        for path in files:
            try:
                text = path.read_text(encoding="utf-8")
            except (OSError, UnicodeDecodeError):
                continue
            row["files_scanned"] += 1
            tg = grams_of(text, mask=mask) & index.keys()
            explained = tg & allowed
            tg -= explained
            row["explained_by_vigie_label"] += len(explained)
            if tg:
                row["files_with_hits"] += 1
                row["grams_matched"] += len(tg)
                for g in tg:
                    matched_items |= index[g]
        row["items_matched"] = len(matched_items)
        row["items_matched_current"] = len(matched_items & cur_ids)
        out["categories"][cat] = row
        scanned_total += row["files_scanned"]
        hits_total += row["grams_matched"]
    out["files_scanned"] = scanned_total
    out["grams_matched"] = hits_total
    if scanned_total == 0:
        out["status"] = "skipped"
        out["reason"] = "no sealed record, permanent page or event store file exists to scan"
        out["verdict"] = "not_measurable"
    else:
        out["verdict"] = "met" if hits_total == 0 else "not_met"
        out["reason"] = ("no publisher 4-gram found in any scanned file" if hits_total == 0
                         else f"{hits_total} publisher 4-grams found: a record or page carries publisher text")
    return out


# ---------------------------------------------------------------- gate 3
def _member_owner(events: list[dict]) -> dict[str, str]:
    return {(m["item_id"] if isinstance(m, dict) else str(m)): e["event_id"] for e in events for m in e["members"]}


def stamped_snapshots(data_dir: Path) -> list[Path]:
    return [f for f in sorted(data_dir.glob("*_candidates.json")) if f.name[:1].isdigit()]


def replay(data_dir: Path) -> dict:
    """Replay every stamped snapshot in order as one edition (the shadow protocol)."""
    files = stamped_snapshots(data_dir)
    if not files:
        raise Skip(f"no stamped *_candidates.json under {data_dir}")
    events: list[dict] = []
    seen: dict[str, tuple] = {}
    editions = moved = lost = unreadable = 0
    ever_ids: set[str] = set()
    minted = attached = merges = 0
    for path in files:
        payload = _read_json(path)
        items = _candidates(payload)
        clock = em._clock(str(payload.get("normalized_at") or "")) if isinstance(payload, dict) else None
        if clock is None or not items:
            unreadable += payload is None
            continue
        edition = str(payload["normalized_at"])
        editions += 1
        for c in items:
            seen.setdefault(str(c["id"]), (clock, c))
        horizon = clock - timedelta(days=WINDOW_DAYS)
        texts = {k: c for k, (t, c) in sorted(seen.items()) if t >= horizon}
        before = _member_owner(events)
        ids_before = {e["event_id"] for e in events}
        ctx = em.MatchContext(list(texts.values()))
        events, decisions = em.attach(events, items, ctx, edition=edition, items_by_id=texts)
        after = _member_owner(events)
        moved += sum(1 for k, v in before.items() if after.get(k) != v)
        lost += len(ids_before - {e["event_id"] for e in events})
        ever_ids |= {e["event_id"] for e in events}
        minted += sum(1 for d in decisions if d.get("action") == "minted")
        attached += sum(1 for d in decisions if d.get("action") == "attached")
        merges += sum(1 for d in decisions if d.get("action") == "merged")
    if editions == 0:
        raise Skip("no stamped snapshot carries a usable collection clock and items")
    digest = hashlib.sha256(json.dumps(
        sorted((e["event_id"], sorted(_member_owner([e]))) for e in events),
        separators=(",", ":")).encode("utf-8")).hexdigest()
    return {"events": events, "editions": editions, "unique_items": len(seen), "memberships_moved": moved,
            "event_ids_lost": lost, "event_ids_total": len(ever_ids), "minted": minted, "attached": attached,
            "merges": merges, "unreadable_snapshots": unreadable, "final_digest": digest}


def gate_stability(data_dir: Path, root: Path | None = None) -> dict:
    res = replay(data_dir)
    events = res.pop("events")
    changes = res["memberships_moved"] + res["event_ids_lost"]
    out = {"status": "measured", "protocol": "replay of every stamped snapshot, sticky attach (events.py absent: "
           "eval/shadow_live.py protocol)", **res, "id_changes": changes}
    live = None
    if root is not None:
        for name in ("store.json", "latest_events.json"):
            doc = _read_json(root / "data" / "events" / name)
            rows = doc.get("events") if isinstance(doc, dict) else doc
            if isinstance(rows, list):
                live = {e["event_id"] for e in rows if isinstance(e, dict) and e.get("event_id")}
                break
    if live is not None:
        mine = {e["event_id"] for e in events}
        out["live_store"] = {"events": len(live), "ids_reproduced_by_replay": len(live & mine)}
    out["verdict"] = "met" if changes == 0 else "not_met"
    out["reason"] = ("0 event ids lost and 0 memberships moved across the replay" if changes == 0
                     else f"{changes} id changes across the replay")
    return out


# ---------------------------------------------------------------- gate 4
def reemit_chain(editions: list[dict]) -> list[dict]:
    """Seal the golden fixture's editions with the current registre code."""
    state = registre.empty_state()
    for ed in editions:
        record = registre.edition_record(ed["payload"], ed["edition"], ed.get("collection"))
        if record is None:
            raise Skip("a golden edition no longer yields a record")
        registre.seal_edition(state, record)
    return [{"seq": s["seq"], "leaf": s["leaf"], "root": s["root"],
             "record_bytes": len(registre.canonical(s["record"]))} for s in state["seals"]]


def gate_main_chain(root: Path, golden_path: Path) -> dict:
    golden = _read_json(golden_path)
    if not isinstance(golden, dict) or not isinstance(golden.get("editions"), list) \
            or not isinstance(golden.get("expected"), list):
        raise Skip(f"no golden fixture at {golden_path}")
    got = reemit_chain(golden["editions"])
    want = golden["expected"]
    same = [i for i in range(min(len(got), len(want))) if got[i] == want[i]]
    out: dict = {"status": "measured", "golden_editions": len(want), "golden_seals_identical": len(same),
                 "golden_seals_reemitted": len(got), "method": registre.METHOD}
    ok = len(got) == len(want) and len(same) == len(want)
    state = _read_json(root / "data" / "registre" / "registre.json")
    if isinstance(state, dict) and isinstance(state.get("seals"), list) and state["seals"]:
        seals = [s for s in state["seals"] if isinstance(s, dict)]
        verified, _ = registre.verify_chain(seals)
        live = {"seals": len(seals), "chain_recomputes_byte_identical": bool(verified)}
        anchor = (root / "anchors" / "checkpoint.txt")
        if anchor.is_file():
            lines = anchor.read_text(encoding="utf-8").splitlines()
            try:
                seq, aroot = int(lines[1]), lines[2].strip()
            except (IndexError, ValueError):
                seq, aroot = None, ""
            if seq is not None:
                match = [s for s in seals if s.get("seq") == seq]
                live["anchor_seq"] = seq
                # A state that stops before the anchored seal is an older copy (a
                # developer checkout), not a fork: the anchor cannot be compared.
                live["anchor_comparable"] = len(seals) >= seq
                if live["anchor_comparable"]:
                    live["anchor_root_present_in_state"] = bool(match and match[0].get("root") == aroot)
                    ok = ok and live["anchor_root_present_in_state"]
        out["live_state"] = live
        ok = ok and bool(verified)
    else:
        out["live_state"] = {"present": False}
    out["verdict"] = "met" if ok else "not_met"
    out["reason"] = ("golden fixture and live chain recompute byte-identically" if ok
                     else "the main chain no longer recomputes identically to its golden or anchor")
    return out


# ---------------------------------------------------------------- driver
def run(root: Path, *, gold_path: Path | None = None, data_dir: Path | None = None,
        golden_path: Path | None = None, gates: str = "1,2,3,4", log=print) -> dict:
    gold_path = gold_path or root / "data" / "eval" / "gold_v0.json"
    data_dir = data_dir or root / "data" / "normalized"
    golden_path = golden_path or root / "tests" / "golden" / "main_chain.json"
    wanted = [g.strip() for g in gates.split(",") if g.strip() in GATE_NAMES]
    jobs = {
        "1": lambda: gate_tiers(load_gold(gold_path), load_items(data_dir)),
        "2": lambda: gate_leak(root, data_dir),
        "3": lambda: gate_stability(data_dir, root),
        "4": lambda: gate_main_chain(root, golden_path),
    }
    out: dict = {"schema": SCHEMA, "method": METHOD, "caveat": CAVEAT, "gates": {}}
    for g in wanted:
        name = GATE_NAMES[g]
        try:
            out["gates"][name] = jobs[g]()
        except Skip as why:
            out["gates"][name] = {"status": "skipped", "reason": str(why), "verdict": "not_measurable"}
        except Exception as exc:  # noqa: BLE001  (fail-soft: a fault is a diagnosed skip)
            out["gates"][name] = {"status": "skipped", "verdict": "not_measurable",
                                  "reason": f"{exc.__class__.__name__} inside the gate: {str(exc)[:160]}"}
        log(f"events_eval: {name}: {out['gates'][name]['verdict']} ({out['gates'][name].get('reason', '')})")
    out["summary"] = {GATE_NAMES[g]: out["gates"][GATE_NAMES[g]]["verdict"] for g in wanted}
    out["all_gates_met"] = bool(wanted) and all(v == "met" for v in out["summary"].values())
    return out


def write_out(path: Path, result: dict) -> None:
    store_io.write_text_atomic(path, json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", type=Path, default=ROOT, help="repository root (default: this checkout)")
    ap.add_argument("--gold", type=Path, help="private gold (default data/eval/gold_v0.json)")
    ap.add_argument("--data-dir", type=Path, help="item stores (default data/normalized)")
    ap.add_argument("--golden", type=Path, help="main-chain golden fixture (default tests/golden/main_chain.json)")
    ap.add_argument("--out", type=Path, help="counts file (default data/ops/events_quality.json)")
    ap.add_argument("--gates", default="1,2,3,4", help="comma list of gates to run")
    args = ap.parse_args(argv)
    root = args.root.resolve()
    try:
        result = run(root, gold_path=args.gold, data_dir=args.data_dir, golden_path=args.golden, gates=args.gates)
        out = args.out or root / "data" / "ops" / "events_quality.json"
        write_out(out, result)
        print(f"events_eval: counts written to {out}")
    except Exception as exc:  # noqa: BLE001  (fail-soft: diagnose, exit 0)
        print(f"events_eval: skipped: {exc.__class__.__name__}: {str(exc)[:200]}. Nothing measured; exit 0.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
