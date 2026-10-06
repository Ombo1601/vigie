"""The event seal chain (registre-evenements-v1): shadow, state only.

Law under test (docs/EVENTS.md section 12, docs/MIGRATION.md step 7): one seal
per edition, keyed by the collection clock, built with the registre's own
primitives; a re-render of the same edition never mints or alters a seal; a
seal is immutable once recorded; the record carries codes and counts only,
never a URL, a title, an excerpt, a quote, a fact value or a person's name
(every code must belong to its closed list: a slug-shaped name is refused,
and every value left out is counted); the City's roadwork ids are sealed
verbatim, as the travaux chain seals them; a missing, corrupt, foreign,
unbuilt or hostile event view mints nothing and leaves the edition seal and
every public artefact byte-identical; a record can never grow, then collapse,
the private state past the persist guard; the chain survives a state
pack/unpack round trip and leaves the state witnesses untouched.

Every fixture is invented. No publisher text appears in this file.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import random
import subprocess
import sys
import tarfile
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import registre
import state_pack
import state_sync

E1 = "2026-09-10T12:00:00+00:00"
E2 = "2026-09-10T18:00:00+00:00"
E3 = "2026-09-11T00:00:00+00:00"
SECTION_12_KEYS = {"event_id", "type", "places", "activity", "institutions", "languages",
                   "member_count", "independent_count", "origin", "anchors", "merged_into"}

# Invented publisher-side strings: none of them may ever reach a sealed record.
TITLE = "Un titre inventé pour le test, mot pour mot"
EXCERPT = "Un extrait inventé qui ne doit jamais être scellé"
QUOTE = "« une citation inventée »"
PERSON = "Gaston Inventé-Personne"
URL = "https://exemple.test/article/42"
FACT_VALUE = 7654321


def item_id(name: str) -> str:
    return hashlib.sha256(f"https://exemple.test/{name}".encode()).hexdigest()


def ev_id(name: str) -> str:
    return "ev-" + hashlib.sha256(f"event-v1|{item_id(name)}".encode()).hexdigest()[:16]


def member(name: str, *, institution="le-soleil", language="fr", origin="own_reporting", **extra) -> dict:
    row = {"item_id": item_id(name), "institution": institution, "source_id": institution,
           "language": language, "published_at": "2026-09-10T10:00:00+00:00",
           "first_seen": "2026-09-10T11:00:00+00:00", "origin_class": origin,
           "ownership_class": "cooperative", "owner_group": institution,
           # publisher text and links a view may carry for the current page:
           "url": f"{URL}/{name}", "title": TITLE, "excerpt": EXCERPT, "author": PERSON}
    row.update(extra)
    return row


def event(name: str, *, members=None, **fields) -> dict:
    members = members if members is not None else [member(name)]
    doc = {
        "event_id": ev_id(name), "schema": 1, "method": "events-v1 rules",
        "type": "fire-building", "places": ["limoilou", "quebec-city"],
        "label": {"fr": "Incendie de bâtiment · Limoilou", "en": "Building fire · Limoilou"},
        "born_edition": E1, "last_edition": E1, "window_state": "in_window", "activity": "new",
        "members": members,
        "institutions": sorted({m["institution"] for m in members if isinstance(m, dict) and "institution" in m}),
        "languages": sorted({m["language"] for m in members if isinstance(m, dict) and "language" in m}),
        "independence": {"groups": [[m["item_id"]] for m in members if isinstance(m, dict)],
                         "count": len([m for m in members if isinstance(m, dict)])},
        "facts": [{"slot": {"kind": "count", "unit": "persons_injured", "subject": "fire-building"},
                   "values": [{"value": FACT_VALUE, "stated_by": [item_id(name)], "institutions": ["le-soleil"]}],
                   "divergent": False, "method": "facts-v1", "status": "proposed"},
                  {"slot": {"kind": "entity", "unit": "public_office", "subject": "fire-building"},
                   "values": [{"value": PERSON, "stated_by": [item_id(name)], "institutions": ["le-soleil"]}],
                   "divergent": False, "method": "facts-v1", "status": "proposed"}],
        "anchors": [],
        "language_pairs": [],
        "lineage": {"merged_into": "", "absorbed": [], "detached": []},
        "seals": [],
        "quotes": [QUOTE],
    }
    doc.update(fields)
    return doc


def view(edition: str, events: list[dict], key: str = "edition") -> dict:
    return {key: edition, "schema": 1, "events": events}


def rich_view(edition: str = E1) -> dict:
    """Three events: a bilingual one with an official anchor, a solo one, a merged one."""
    fire = event("fire", members=[
        member("fire-1", institution="radio-canada", language="fr", origin="own_reporting"),
        member("fire-2", institution="cbc", language="en", origin="wire"),
        member("fire-3", institution="ville-quebec", language="fr", origin="official"),
        member("fire-4", institution="le-soleil", language="fr", origin="nonsense-class"),
    ], independence={"groups": [[item_id("fire-1"), item_id("fire-2")], [item_id("fire-3")],
                                [item_id("fire-4")]], "count": 3},
        anchors=[{"type": "official_item", "ref": item_id("fire-3"), "rule": "membership",
                  "status": "linked_by_rule", "title": TITLE, "url": URL},
                 {"type": "roadwork", "ref": "20240231406-1", "rule": "same-road", "status": "linked_by_rule"},
                 {"type": "edition_seal", "ref": 57, "rule": "present", "status": "linked_by_rule"}])
    solo = event("solo", activity="quiet", type="municipal-bylaw", places=["quebec-city"])
    merged = event("merged", activity="developed", lineage={"merged_into": ev_id("fire")})
    return view(edition, [solo, merged, fire])


def seal_view(state: dict, doc: dict, edition: str) -> tuple[dict, str]:
    record, why = registre.events_record(doc, edition, registre.edition_root_for(state, edition))
    if record is None:
        return state, why
    return registre.seal_events(state, record)


def snapshot(state: dict) -> bytes:
    return json.dumps(state, sort_keys=True, ensure_ascii=False).encode("utf-8")


# The institution ids a record may carry come from sources.yaml. These tests
# run against an invented registry (one cut source included), never the live one.
SOURCES_FIXTURE = """\
sources:
  - id: radio-canada-inventee
    institution: radio-canada
    type: rss
    enabled: true
  - id: cbc-inventee
    institution: cbc
    type: rss
    enabled: false
  - id: le-soleil
    type: rss
    enabled: true
  - id: ville-quebec
    type: rss
    source_kind: official
    enabled: true
  - id: wzdx-inventee
    institution: ville-quebec
    type: wzdx
    enabled: true
"""
FIXTURE_INSTITUTIONS = {"cbc", "le-soleil", "radio-canada", "ville-quebec"}
_MODULE_PATCHES: list = []


def setUpModule() -> None:  # noqa: N802 - unittest hook
    tmp = tempfile.TemporaryDirectory()
    _MODULE_PATCHES.append(tmp)
    path = Path(tmp.name) / "sources.yaml"
    path.write_text(SOURCES_FIXTURE, encoding="utf-8")
    patch = mock.patch.object(registre, "SOURCES_PATH", path)
    patch.start()
    _MODULE_PATCHES.append(patch)


def tearDownModule() -> None:  # noqa: N802 - unittest hook
    while _MODULE_PATCHES:
        handle = _MODULE_PATCHES.pop()
        if hasattr(handle, "stop"):
            handle.stop()
        else:
            handle.cleanup()


# --------------------------------------------------------------------------- #
# The record: codes and counts only
# --------------------------------------------------------------------------- #
class Record(unittest.TestCase):
    def test_record_has_exactly_the_section_12_shape(self):
        record, why = registre.events_record(rich_view(), E1, "ab" * 32)
        self.assertEqual(why, "")
        self.assertEqual(set(record), {"method", "edition", "edition_root", "events"})
        self.assertEqual(record["method"], "registre-evenements-v1 sha256-chain")
        self.assertEqual(record["edition"], E1)
        self.assertEqual(record["edition_root"], "ab" * 32)
        self.assertEqual([e["event_id"] for e in record["events"]], sorted(e["event_id"] for e in record["events"]))
        for entry in record["events"]:
            self.assertEqual(set(entry), SECTION_12_KEYS)
        fire = next(e for e in record["events"] if e["event_id"] == ev_id("fire"))
        self.assertEqual(fire["type"], "fire-building")
        self.assertEqual(fire["places"], ["limoilou", "quebec-city"])   # most specific first, kept
        self.assertEqual(fire["institutions"], ["cbc", "le-soleil", "radio-canada", "ville-quebec"])
        self.assertEqual(fire["languages"], ["en", "fr"])
        self.assertEqual(fire["member_count"], 4)
        self.assertEqual(fire["independent_count"], 3)
        # an unknown class is counted as the spec's default, never guessed
        self.assertEqual(fire["origin"], {"official": 1, "own_reporting": 1, "unknown": 1, "wire": 1})
        self.assertEqual(fire["anchors"], [{"type": "edition_seal", "ref": "57"},
                                           {"type": "official_item", "ref": item_id("fire-3")},
                                           {"type": "roadwork", "ref": "20240231406-1"}])
        merged = next(e for e in record["events"] if e["event_id"] == ev_id("merged"))
        self.assertEqual(merged["merged_into"], ev_id("fire"))
        self.assertEqual(merged["activity"], "developed")

    def test_no_publisher_text_url_fact_value_or_person_is_ever_sealed(self):
        record, _ = registre.events_record(rich_view(), E1, "")
        sealed = registre.canonical(record).decode("utf-8")
        for needle in (TITLE, EXCERPT, QUOTE, PERSON, "Gaston", URL, "exemple.test", "https://", "http",
                       str(FACT_VALUE), "Incendie de bâtiment", "Building fire", "persons_injured",
                       "facts", "label", "url", "title", "excerpt", "author", "quotes", "language_pairs"):
            self.assertNotIn(needle, sealed, needle)
        # Only ASCII codes, ids and digits survive.
        self.assertTrue(sealed.isascii())

    def test_free_text_in_code_fields_is_dropped_never_sealed(self):
        hostile = event("hostile", type="Incendie à Limoilou", activity="terminé",
                        places=["Limoilou", "saint roch", URL, "x", "a" * 49, "limoilou", "limoilou"],
                        institutions=["le-soleil", "Le Soleil", "../etc", "radio canada"],
                        languages=["fr", "de", "FR", "en"],
                        lineage={"merged_into": PERSON},
                        anchors=[{"type": "official_item", "ref": URL},
                                 {"type": "official_item", "ref": "not-hex"},
                                 {"type": "roadwork", "ref": URL},
                                 {"type": "roadwork", "ref": "R-1"},
                                 {"type": "consultation", "ref": 767},
                                 {"type": "edition_seal", "ref": True},
                                 {"type": "edition_seal", "ref": "0"},
                                 {"type": "verdict", "ref": "R-2"},
                                 {"type": ["roadwork"], "ref": "R-3"},
                                 "R-4"])
        dropped: dict = {}
        record, why = registre.events_record(view(E1, [hostile]), E1, "", dropped=dropped)
        self.assertEqual(why, "")
        entry = record["events"][0]
        self.assertEqual(entry["type"], "")            # not established, never rewritten
        self.assertEqual(entry["activity"], "")
        self.assertEqual(entry["places"], ["limoilou"])
        self.assertEqual(entry["institutions"], ["le-soleil"])
        self.assertEqual(entry["languages"], ["en", "fr"])
        self.assertEqual(entry["merged_into"], "")
        self.assertEqual(entry["anchors"], [{"type": "consultation", "ref": "767"},
                                            {"type": "roadwork", "ref": "R-1"}])
        self.assertNotIn("Limoilou", registre.canonical(record).decode())
        # every stated value left out is counted (a duplicate is the same value)
        self.assertEqual(dropped, {"type": 1, "activity": 1, "places": 5, "institutions": 3,
                                   "languages": 2, "merged_into": 1, "anchors": 8})

    def test_counts_are_read_never_invented(self):
        bare = event("bare", members=[item_id("bare-1"), item_id("bare-1"), "not-an-id", 42,
                                      {"item_id": item_id("bare-2"), "origin_class": "wire"}],
                     independence={"groups": [[item_id("bare-1")], [item_id("bare-2")]]},
                     institutions=None, languages=None)
        entry = registre.event_entry(bare)
        self.assertEqual(entry["member_count"], 2)                    # once each, ids only
        self.assertEqual(entry["origin"], {"unknown": 1, "wire": 1})  # a bare id has no class
        self.assertEqual(entry["independent_count"], 2)               # count absent: its groups
        self.assertEqual(entry["institutions"], [])                   # absent stays absent
        for count in (True, 2.0, "3", -1, 10**100):
            entry = registre.event_entry(event("c", independence={"count": count}))
            self.assertEqual(entry["independent_count"], 0, count)    # no groups: not established
        too_many = registre.event_entry(event("t", independence={"count": 5}))
        self.assertEqual(too_many["independent_count"], 0)            # 5 groups for 1 member
        lean = registre.event_entry({"event_id": ev_id("lean"), "member_count": 4,
                                     "independence": {"count": 3}})
        self.assertEqual((lean["member_count"], lean["independent_count"]), (4, 3))

    def test_record_is_independent_of_input_order(self):
        doc = rich_view()
        shuffled = json.loads(json.dumps(doc))
        rng = random.Random(7)
        rng.shuffle(shuffled["events"])
        for ev in shuffled["events"]:
            rng.shuffle(ev["members"])
            rng.shuffle(ev["anchors"])
            ev["institutions"].reverse()
        a, _ = registre.events_record(doc, E1, "")
        b, _ = registre.events_record(shuffled, E1, "")
        self.assertEqual(registre.canonical(a), registre.canonical(b))

    def test_leaf_is_independent_of_the_hash_seed(self):
        code = ("import json, sys; sys.path.insert(0, sys.argv[1]); sys.path.insert(0, sys.argv[2]);"
                "import registre, test_registre_events as t;"
                "rec, _ = registre.events_record(t.rich_view(), t.E1, '');"
                "print(registre.leaf_of(rec))")
        leaves = set()
        for seed in ("0", "1", "4242"):
            env = dict(os.environ, PYTHONHASHSEED=seed)
            proc = subprocess.run([sys.executable, "-X", "utf8", "-c", code,
                                   str(harness.ROOT / "scripts"), str(harness.ROOT / "tests")],
                                  capture_output=True, text=True, env=env, timeout=120)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            leaves.add(proc.stdout.strip())
        self.assertEqual(len(leaves), 1, leaves)

    def test_out_of_window_events_are_not_part_of_the_edition(self):
        gone = event("gone", window_state="out_of_window", activity="quiet")
        record, _ = registre.events_record(view(E1, [gone, event("here")]), E1, "")
        self.assertEqual([e["event_id"] for e in record["events"]], [ev_id("here")])

    def test_an_edition_without_events_is_sealed_as_such(self):
        record, why = registre.events_record(view(E1, []), E1, "")
        self.assertEqual(why, "")
        self.assertEqual(record["events"], [])


class EditionGate(unittest.TestCase):
    """A view is sealed only for the edition it declares, never borrowed."""

    def test_the_view_must_declare_this_edition(self):
        self.assertIsNone(registre.events_record(view(E2, [event("a")]), E1, "")[0])
        self.assertIn("edition-mismatch", registre.events_record(view(E2, [event("a")]), E1, "")[1])
        self.assertIn("no edition clock", registre.events_record({"events": [event("a")]}, E1, "")[1])
        self.assertEqual(registre.events_record(view(E1, [event("a")]), "", "")[1], "no-edition")
        # an instant outside datetime's range is no clock, never a traceback
        out_of_range = "9999-12-31T23:59:59-23:59"
        self.assertIn("no edition clock", registre.events_record(view(out_of_range, []), E1, "")[1])
        self.assertEqual(registre.events_record(view(E1, []), out_of_range, "")[1], "no-edition")
        bad = [{"seq": 1, "edition": out_of_range, "prev": "", "leaf": "a" * 64,
                "root": registre.chain_hash("", "a" * 64)}]
        self.assertFalse(registre.verify_event_chain(bad)[0])

    def test_equivalent_clock_spellings_seal_under_the_edition_key(self):
        for key, clock in (("edition", "2026-09-10T12:00:00Z"), ("normalized_at", "2026-09-10T08:00:00-04:00"),
                           ("edition_clock", E1)):
            record, why = registre.events_record(view(clock, [event("a")], key=key), E1, "")
            self.assertEqual(why, "", key)
            self.assertEqual(record["edition"], E1)   # the main chain's own key string

    def test_malformed_views_are_not_sealed_with_holes(self):
        bad = [
            ([event("a"), "not-an-event"], "entry 1"),
            ([event("a"), {"event_id": "ev-XYZ"}], "entry 1"),
            ([event("a"), dict(event("b"), event_id=None)], "entry 1"),
            ([event("a"), dict(event("a"), type="strike")], "listed twice"),
        ]
        for events, why in bad:
            record, reason = registre.events_record(view(E1, events), E1, "")
            self.assertIsNone(record)
            self.assertIn(why, reason)
        self.assertIn("no events list", registre.events_record({"edition": E1, "events": {}}, E1, "")[1])
        self.assertIn("not an object", registre.events_record([event("a")], E1, "")[1])
        # the same event listed twice identically is one event
        record, _ = registre.events_record(view(E1, [event("a"), event("a")]), E1, "")
        self.assertEqual(len(record["events"]), 1)

    def test_too_many_events_are_refused(self):
        with mock.patch.object(registre, "EVENTS_MAX", 2):
            record, why = registre.events_record(view(E1, [event("a"), event("b"), event("c")]), E1, "")
        self.assertIsNone(record)
        self.assertIn("too-many", why)

    def test_edition_root_is_the_main_seal_of_the_same_edition(self):
        state = registre.empty_state()
        main = {"method": registre.METHOD, "edition": E1, "followed": [], "dossiers": [],
                "ledger": {"has_previous": False, "new": [], "developed": [], "quiet": []}}
        state, _ = registre.seal_edition(state, main)
        self.assertEqual(registre.edition_root_for(state, "2026-09-10T12:00:00Z"), state["seals"][0]["root"])
        self.assertEqual(registre.edition_root_for(state, E2), "")
        record, _ = registre.events_record(view(E1, []), E1, "not-a-root")
        self.assertEqual(record["edition_root"], "")


# --------------------------------------------------------------------------- #
# The chain: idempotent, immutable, forward-only
# --------------------------------------------------------------------------- #
class Chain(unittest.TestCase):
    def test_two_editions_seal_in_order_and_verify(self):
        state = registre.empty_state()
        state, a1 = seal_view(state, rich_view(E1), E1)
        state, a2 = seal_view(state, view(E2, [event("later")]), E2)
        self.assertEqual((a1, a2), ("appended", "appended"))
        seals = state["evenements"]["seals"]
        self.assertEqual([s["seq"] for s in seals], [1, 2])
        self.assertEqual(seals[0]["prev"], "")
        self.assertEqual(seals[1]["prev"], seals[0]["root"])
        self.assertEqual(seals[0]["leaf"], registre.leaf_of(seals[0]["record"]))
        self.assertEqual(seals[0]["root"], registre.chain_hash("", seals[0]["leaf"]))
        self.assertEqual(seals[0]["event_count"], 3)
        self.assertEqual(registre.verify_event_chain(seals), (True, "2 event seals verified (2 with their record, 0 by hash linkage)"))
        # The edition chain is untouched by the event chain.
        self.assertEqual(state["seals"], [])

    def test_rerender_of_the_same_edition_never_mints_or_alters(self):
        state = registre.empty_state()
        state, _ = seal_view(state, rich_view(E1), E1)
        before = snapshot(state)
        for _ in range(3):
            state, action = seal_view(state, rich_view(E1), E1)
            self.assertEqual(action, "confirmed")
        self.assertEqual(snapshot(state), before)

    def test_a_recorded_seal_is_immutable_when_the_view_changes(self):
        state = registre.empty_state()
        state, _ = seal_view(state, rich_view(E1), E1)
        before = snapshot(state)
        changed = rich_view(E1)
        changed["events"].append(event("late-joiner"))
        out = io.StringIO()
        with redirect_stdout(out):
            state, action = seal_view(state, changed, E1)
        self.assertEqual(action, "diverged-kept")
        self.assertIn("keeping the recorded event seal", out.getvalue())
        self.assertEqual(snapshot(state), before, "a recorded seal must not move")
        state, action = seal_view(state, view(E2, [event("late-joiner")]), E2)
        self.assertEqual(action, "appended")
        self.assertEqual(state["evenements"]["seals"][1]["prev"], state["evenements"]["seals"][0]["root"])

    def test_an_older_edition_is_ignored(self):
        state = registre.empty_state()
        state, _ = seal_view(state, view(E2, [event("a")]), E2)
        before = snapshot(state)
        state, action = seal_view(state, view(E1, [event("a")]), E1)
        self.assertEqual(action, "ignored")
        self.assertEqual(snapshot(state), before)

    def test_tampering_is_detected(self):
        state = registre.empty_state()
        for n, edition in enumerate((E1, E2, E3)):
            state, _ = seal_view(state, view(edition, [event(f"e{n}")]), edition)
        good = state["evenements"]["seals"]
        self.assertTrue(registre.verify_event_chain(good)[0])
        cases = {
            "leaf mismatch": lambda s: s[1]["record"]["events"][0].update(member_count=99),
            "root mismatch": lambda s: s[1].update(root="0" * 64),
            "prev mismatch": lambda s: s[2].update(prev="f" * 64),
            "out of order": lambda s: s[2].update(seq=7),
            "not after": lambda s: s[2].update(edition=E1),
            "does not belong": lambda s: s[1]["record"].update(method=registre.METHOD),
            "malformed leaf": lambda s: s[0].update(leaf="xyz"),
        }
        for why, tamper in cases.items():
            seals = json.loads(json.dumps(good))
            tamper(seals)
            if why == "does not belong":   # keep the leaf consistent: only the binding is wrong
                seals[1]["leaf"] = registre.leaf_of(seals[1]["record"])
            ok, msg = registre.verify_event_chain(seals)
            self.assertFalse(ok, why)
            self.assertIn(why, msg)
        self.assertFalse(registre.verify_event_chain([{"seq": 1}, "x"])[0])
        self.assertFalse(registre.verify_event_chain("nope")[0])
        hostile = [{"seq": 1, "edition": E1, "prev": "", "leaf": "a" * 64, "root": "b" * 64,
                    "record": {"bad": "\ud800"}}]
        self.assertFalse(registre.verify_event_chain(hostile)[0])
        self.assertFalse(registre.verify_event_chain(good, anchor_root="é")[0])

    def test_the_newest_12_seals_stay_full_and_older_ones_keep_leaf_and_root(self):
        state = registre.empty_state()
        total = registre.EVENTS_RECORD_CAP + 3
        heads = []
        for n in range(total):
            edition = edition_at(n)
            state, action = seal_view(state, view(edition, [event(f"r{n}")]), edition)
            self.assertEqual(action, "appended")
            heads.append({k: state["evenements"]["seals"][-1][k] for k in ("seq", "edition", "prev", "leaf", "root")})
        seals = state["evenements"]["seals"]
        self.assertEqual(len(seals), total)
        self.assertEqual([("record" in s) for s in seals], [False] * 3 + [True] * registre.EVENTS_RECORD_CAP)
        # An older seal is compacted to exactly {leaf, root}: the hashes never change.
        for n in range(3):
            self.assertEqual(seals[n], {"leaf": heads[n]["leaf"], "root": heads[n]["root"]})
        self.assertEqual([{k: s[k] for k in ("seq", "edition", "prev", "leaf", "root")} for s in seals[3:]], heads[3:])
        self.assertTrue(all(s["event_count"] == 1 for s in seals[3:]))
        ok, msg = registre.verify_event_chain(seals)
        self.assertTrue(ok, msg)
        self.assertEqual(msg, f"{total} event seals verified ({registre.EVENTS_RECORD_CAP} with their record, "
                              f"3 by hash linkage)")
        # A re-render of the newest edition still confirms, and the chain continues at the right seq.
        last = seals[-1]["edition"]
        state, action = seal_view(state, view(last, [event(f"r{total - 1}")]), last)
        self.assertEqual(action, "confirmed")
        state, action = seal_view(state, view(edition_at(total), [event("next")]), edition_at(total))
        self.assertEqual(action, "appended")
        seals = state["evenements"]["seals"]
        self.assertEqual((seals[-1]["seq"], seals[-1]["prev"]), (total + 1, heads[-1]["root"]))
        self.assertEqual(seals[3], {"leaf": heads[3]["leaf"], "root": heads[3]["root"]}, "one more compacted")
        self.assertTrue(registre.verify_event_chain(seals)[0])

    def test_state_growth_is_bounded_beyond_the_full_seals(self):
        """Past the newest twelve, each edition adds only a {leaf, root} pair."""
        state = registre.empty_state()
        sizes = []
        for n in range(registre.EVENTS_RECORD_CAP + 6):
            edition = edition_at(n)
            state, _ = seal_view(state, view(edition, [event(f"g{n}")]), edition)
            sizes.append(len(json.dumps(state["evenements"], indent=2)))
        steps = {b - a for a, b in zip(sizes[registre.EVENTS_RECORD_CAP:], sizes[registre.EVENTS_RECORD_CAP + 1:])}
        self.assertEqual(len(steps), 1, steps)
        self.assertLess(steps.pop(), 200, "about one compacted pair of hashes per edition")

    def test_a_byte_budget_bounds_the_state_against_a_runaway_view(self):
        """A faulty builder listing thousands of events must not bloat the only
        copy of the registre: older records go first, and a record over the
        budget is not minted at all (no record is kept "whatever its size")."""
        state = registre.empty_state()
        big = [event(f"big{n}") for n in range(40)]
        size = registre.record_stored_bytes(registre.events_record(view(E1, big), E1, "")[0])
        with mock.patch.object(registre, "EVENTS_RECORD_BYTES", size * 2 + 10):
            for edition in (E1, E2, E3):
                state, _ = seal_view(state, view(edition, big), edition)
            self.assertEqual([("record" in s) for s in state["evenements"]["seals"]], [False, True, True])
            before = snapshot(state)
            with mock.patch.object(registre, "EVENTS_RECORD_BYTES", size - 1):
                state, action = seal_view(state, view("2026-09-11T06:00:00+00:00", big), "2026-09-11T06:00:00+00:00")
            self.assertTrue(action.startswith("record-oversized"), action)
            self.assertEqual(snapshot(state), before)
        self.assertTrue(registre.verify_event_chain(state["evenements"]["seals"])[0])


def compacted_state(total: int | None = None) -> dict:
    """A state whose event chain holds compacted seals ahead of the full ones."""
    state = registre.empty_state()
    for n in range(total if total is not None else registre.EVENTS_RECORD_CAP + 4):
        edition = edition_at(n)
        rec = {"method": registre.METHOD, "edition": edition, "followed": [], "dossiers": [],
               "ledger": {"has_previous": n > 0, "new": [], "developed": [], "quiet": []}}
        state, _ = registre.seal_edition(state, rec)
        state, _ = seal_view(state, view(edition, [event(f"c{n}")]), edition)
    return state


class CompactedChain(unittest.TestCase):
    """A compacted seal is verified by hash linkage alone; tampering shows."""

    def setUp(self):
        self.state = compacted_state()
        self.seals = self.state["evenements"]["seals"]
        self.assertEqual(sum(1 for s in self.seals if set(s) == {"leaf", "root"}), 4)

    def test_a_compacted_chain_verifies_from_genesis(self):
        ok, msg = registre.verify_event_chain(self.seals)
        self.assertTrue(ok, msg)
        self.assertIn("4 by hash linkage", msg)
        # The roots are the roots minted at the time: recompute the linkage by hand.
        prev = ""
        for s in self.seals:
            prev = hashlib.sha256((prev + s["leaf"]).encode("ascii")).hexdigest()
            self.assertEqual(s["root"], prev)

    def test_tampering_with_a_compacted_seal_is_detected(self):
        cases = {
            "root mismatch": lambda s: s[1].update(leaf="0" * 64),
            "malformed leaf": lambda s: s[2].update(leaf="not-hex"),
            "prev mismatch": lambda s: s[4].update(prev="f" * 64),          # the first full seal
            "out of order": lambda s: s[4].update(seq=99),
        }
        for why, tamper in cases.items():
            seals = json.loads(json.dumps(self.seals))
            tamper(seals)
            ok, msg = registre.verify_event_chain(seals)
            self.assertFalse(ok, why)
            self.assertIn(why, msg)
        dropped = json.loads(json.dumps(self.seals))
        del dropped[1]                                   # a missing seal breaks the linkage
        self.assertFalse(registre.verify_event_chain(dropped)[0])
        extra = json.loads(json.dumps(self.seals))
        extra[0]["edition"] = E1                          # neither compacted nor a full seal
        self.assertFalse(registre.verify_event_chain(extra)[0])

    def test_a_chain_cut_at_a_compacted_seal_verifies_from_its_anchor(self):
        anchor = self.seals[1]["root"]
        self.assertTrue(registre.verify_event_chain(self.seals[2:], anchor_root=anchor)[0])
        self.assertFalse(registre.verify_event_chain(self.seals[2:])[0], "without its anchor, no genesis")

    def test_load_state_keeps_compacted_seals_and_the_cli_verifies_them(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registre.json"
            registre.store_io.write_json_atomic(path, self.state)
            loaded = registre.load_state(path)
            self.assertEqual(loaded["evenements"]["seals"], self.seals)
            export = Path(tmp) / "export.json"
            registre.store_io.write_json_atomic(export, registre.evenements_export(loaded))
            out = io.StringIO()
            with redirect_stdout(out):
                self.assertEqual(registre.verify_events_cli(export), 0)
            self.assertIn("OK", out.getvalue())

    def test_legacy_headers_without_records_are_compacted_at_the_next_seal(self):
        state = compacted_state(3)
        seals = state["evenements"]["seals"]
        legacy = [{k: v for k, v in s.items() if k != "record"} for s in seals[:2]] + seals[2:]
        state["evenements"]["seals"] = legacy
        self.assertTrue(registre.verify_event_chain(legacy)[0])
        state, action = seal_view(state, view(edition_at(3), [event("c3")]), edition_at(3))
        self.assertEqual(action, "appended")
        self.assertEqual(state["evenements"]["seals"][-1]["seq"], 4)
        self.assertTrue(registre.verify_event_chain(state["evenements"]["seals"])[0])

    def test_wave_1_witnesses_see_the_same_chains_compacted_or_not(self):
        """The restore and persist witnesses judge the edition and travaux
        chains only: compaction of the shadow event chain changes none of
        their facts, and never trips the persist guard."""
        full = json.loads(json.dumps(self.state))
        total = len(self.seals)
        with mock.patch.object(registre, "EVENTS_RECORD_CAP", total + 1):
            uncompacted = compacted_state(total)
        self.assertEqual(len(uncompacted["evenements"]["seals"]), total)
        self.assertFalse(any(set(s) == {"leaf", "root"} for s in uncompacted["evenements"]["seals"]))
        with tempfile.TemporaryDirectory() as tmp:
            a = state_sync.inspect_archive(StateWitnesses.archive(None, Path(tmp) / "a.tar.gz", uncompacted))
            b = state_sync.inspect_archive(StateWitnesses.archive(None, Path(tmp) / "b.tar.gz", full))
        for key in ("registre", "seals", "seal_count", "head_seq", "head_root", "travaux", "travaux_seq",
                    "travaux_root"):
            self.assertEqual(a[key], b[key], key)
        witnesses = [state_sync.Witness(state_sync.EDITION, len(full["seals"]), full["seals"][-1]["root"],
                                        state_sync.ANCHORED)]
        self.assertEqual(state_sync.judge(b, witnesses).kind, "")
        baseline = {"seal_count": a["seal_count"], "head_seq": a["head_seq"], "head_root": a["head_root"],
                    "travaux_seq": a["travaux_seq"], "travaux_root": a["travaux_root"],
                    "durable_members": a["durable_members"], "durable_bytes": a["durable_bytes"]}
        self.assertEqual(state_sync.persist_refusals(baseline, b, allow_shrink=False), ([], []),
                         "compacting the event chain never reads as a shrink of the state")
        self.assertEqual(registre.checkpoint_text(full), registre.checkpoint_text(uncompacted))
        self.assertNotIn("evenements", state_sync.sidecar_text("c" * 64, "", witnesses=state_sync.heads(b)))


class MemberCountR10(unittest.TestCase):
    def test_member_count_is_the_rows_present_and_origin_adds_up_to_it(self):
        ev = rich_view()["events"][2]                    # the fire: four rows
        gone = ev["members"][1]["item_id"]
        ev = dict(ev, withdrawn=[gone])
        ev["members"] = [dict(ev["members"][0], withdrawn=True)] + ev["members"][1:]
        entry = registre.event_entry(ev)
        self.assertEqual(entry["member_count"], 2, "a withdrawn member is never counted")
        self.assertEqual(sum(entry["origin"].values()), entry["member_count"])
        self.assertLessEqual(entry["independent_count"], entry["member_count"])
        lean = registre.event_entry({"event_id": ev_id("lean"), "member_count": 4, "independence": {"count": 3}})
        self.assertEqual((lean["member_count"], lean["origin"]), (4, {"unknown": 4}))
        nobody = registre.event_entry(dict(ev, members=[], withdrawn=[]))
        self.assertEqual((nobody["member_count"], nobody["independent_count"], nobody["origin"]), (0, 0, {}))

    def test_an_active_takedown_is_applied_before_sealing(self):
        import takedown
        doc = rich_view(E1)
        url = next(m["url"] for m in doc["events"][2]["members"] if m["institution"] == "cbc")
        rules = takedown.Rules([{"id": "td-t", "kind": "url", "value": takedown._canon(url) or url,
                                 "requested_at": "2026-09-10", "by": "CBC", "status": "active"}])
        with mock.patch.object(takedown, "load_rules", return_value=rules):
            state, action = registre.seal_events_soft(registre.empty_state(), E1, doc)
        self.assertEqual(action, "appended")
        fire = next(e for e in state["evenements"]["seals"][0]["record"]["events"] if e["event_id"] == ev_id("fire"))
        self.assertNotIn("cbc", fire["institutions"])
        self.assertEqual(fire["member_count"], 3)
        self.assertEqual(sum(fire["origin"].values()), 3)
        # Without an active takedown the view is sealed as it is.
        state, _ = registre.seal_events_soft(registre.empty_state(), E1, rich_view(E1))
        fire = next(e for e in state["evenements"]["seals"][0]["record"]["events"] if e["event_id"] == ev_id("fire"))
        self.assertEqual(fire["member_count"], 4)


# --------------------------------------------------------------------------- #
# State: old states load, corrupt chains are dropped, round trips
# --------------------------------------------------------------------------- #
def legacy_state() -> dict:
    """A state written before the event chain existed (no `evenements` key)."""
    rec = {"method": registre.METHOD, "edition": E1, "followed": ["le-soleil"], "dossiers": [],
           "ledger": {"has_previous": False, "new": [], "developed": [], "quiet": []}}
    leaf = registre.leaf_of(rec)
    return {"method": registre.METHOD, "origin": registre.ORIGIN,
            "seals": [{"seq": 1, "edition": E1, "prev": "", "leaf": leaf,
                       "root": registre.chain_hash("", leaf), "record": rec}],
            "voice": [registre.voice_row(rec)], "names": {},
            "travaux": {"method": registre.ROADS_METHOD, "seals": [], "latest_record": None}}


class StateCompat(unittest.TestCase):
    def test_a_state_without_the_key_loads_and_keeps_its_seals(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registre.json"
            old = legacy_state()
            path.write_text(json.dumps(old), encoding="utf-8")
            state = registre.load_state(path)
            self.assertEqual(state["evenements"], {"method": registre.EVENTS_METHOD, "seals": []})
            self.assertEqual(state["seals"], old["seals"])
            self.assertTrue(registre.verify_chain(state["seals"])[0])

    def test_load_state_round_trips_the_event_chain(self):
        state = registre.empty_state()
        state, _ = seal_view(state, rich_view(E1), E1)
        state, _ = seal_view(state, view(E2, [event("b")]), E2)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registre.json"
            path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
            loaded = registre.load_state(path)
        self.assertEqual(loaded["evenements"], state["evenements"])

    def test_corrupt_event_seals_are_dropped_and_a_foreign_method_ignored(self):
        state = registre.empty_state()
        state, _ = seal_view(state, view(E1, [event("a")]), E1)
        good = state["evenements"]["seals"][0]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "registre.json"
            doc = legacy_state()
            doc["evenements"] = {"method": registre.EVENTS_METHOD,
                                 "seals": [good, {"seq": True, "edition": E2, "prev": "", "leaf": "", "root": ""},
                                           {"seq": 3, "edition": E3}, "x", {**good, "seq": 4, "record": None}]}
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(registre.load_state(path)["evenements"]["seals"], [good])
            doc["evenements"] = {"method": "something-else", "seals": [good]}
            path.write_text(json.dumps(doc), encoding="utf-8")
            self.assertEqual(registre.load_state(path)["evenements"]["seals"], [])
            doc["evenements"] = ["not", "a", "chain"]
            path.write_text(json.dumps(doc), encoding="utf-8")
            loaded = registre.load_state(path)
            self.assertEqual(loaded["evenements"]["seals"], [])
            self.assertEqual(loaded["seals"], doc["seals"])

    def test_the_chain_survives_a_state_pack_unpack_round_trip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            state = registre.empty_state()
            for n, edition in enumerate((E1, E2, E3)):
                state, _ = seal_view(state, view(edition, [event(f"p{n}")]), edition)
            path = root / "data" / "registre" / "registre.json"
            path.parent.mkdir(parents=True)
            registre.store_io.write_json_atomic(path, state)
            original = path.read_bytes()
            with mock.patch.multiple(state_pack, ROOT=root, DATA=root / "data"):
                archive = root / "state.tar.gz"
                state_pack.pack(archive)
                path.unlink()
                state_pack.unpack(archive)
            self.assertEqual(path.read_bytes(), original)
            loaded = registre.load_state(path)
            self.assertEqual(loaded["evenements"], state["evenements"])
            self.assertTrue(registre.verify_event_chain(loaded["evenements"]["seals"])[0])
            # and the chain continues from the restored head
            loaded, action = seal_view(loaded, view("2026-09-11T06:00:00+00:00", [event("p3")]),
                                       "2026-09-11T06:00:00+00:00")
            self.assertEqual(action, "appended")
            self.assertEqual(loaded["evenements"]["seals"][-1]["seq"], 4)


class StateWitnesses(unittest.TestCase):
    """Wave 1 witness logic judges the edition and travaux chains only.

    Decision: the event chain gets no witness line while it is a shadow chain.
    A witness exists to stop a restore from forking a *published* chain; this
    one is published nowhere, and a witness on it would let a shadow feature
    halt the production restore. These tests pin that the key changes nothing.
    """

    def archive(self, path: Path, doc: dict) -> Path:
        body = json.dumps(doc, ensure_ascii=False, indent=2).encode("utf-8")
        with tarfile.open(path, "w:gz") as tar:
            info = tarfile.TarInfo(state_sync.REGISTRE_MEMBER)
            info.size = len(body)
            tar.addfile(info, io.BytesIO(body))
            filler = tarfile.TarInfo("data/raw/src/snap.xml")
            filler.size = 1000
            tar.addfile(filler, io.BytesIO(b"x" * 1000))
        return path

    def states(self) -> tuple[dict, dict]:
        state = registre.empty_state()
        for n, edition in enumerate((E1, E2)):
            rec = {"method": registre.METHOD, "edition": edition, "followed": [], "dossiers": [],
                   "ledger": {"has_previous": n > 0, "new": [], "developed": [], "quiet": []}}
            state, _ = registre.seal_edition(state, rec)
            state, _ = seal_view(state, view(edition, [event(f"w{n}")]), edition)
        state, _ = registre.seal_roadworks(state, {"fetched_at": E2, "events": [{"event_id": "R-1"}]})
        without = json.loads(json.dumps(state))
        del without["evenements"]
        return state, without

    def test_inspect_judge_and_persist_see_the_same_chains(self):
        with_key, without = self.states()
        self.assertEqual(len(with_key["evenements"]["seals"]), 2)
        with tempfile.TemporaryDirectory() as tmp:
            a = state_sync.inspect_archive(self.archive(Path(tmp) / "a.tar.gz", with_key))
            b = state_sync.inspect_archive(self.archive(Path(tmp) / "b.tar.gz", without))
        for key in ("registre", "seals", "seal_count", "head_seq", "head_root",
                    "travaux", "travaux_seq", "travaux_root", "members"):
            self.assertEqual(a[key], b[key], key)
        witnesses = [state_sync.Witness(state_sync.EDITION, 2, with_key["seals"][-1]["root"], state_sync.ANCHORED),
                     state_sync.Witness(state_sync.TRAVAUX, 1, with_key["travaux"]["seals"][-1]["root"],
                                        state_sync.RECORDED)]
        self.assertEqual(state_sync.judge(a, witnesses).kind, "")
        baseline = {"seal_count": b["seal_count"], "head_seq": b["head_seq"], "head_root": b["head_root"],
                    "travaux_seq": b["travaux_seq"], "travaux_root": b["travaux_root"],
                    "durable_members": b["durable_members"], "durable_bytes": b["durable_bytes"]}
        self.assertEqual(state_sync.persist_refusals(baseline, a, allow_shrink=False), ([], []))
        # The sidecar records the two witnessed chains, and nothing about events.
        text = state_sync.sidecar_text("c" * 64, "", witnesses=state_sync.heads(a))
        self.assertNotIn("evenements", text)
        self.assertEqual([w.chain for w in state_sync.recorded_witnesses(text)],
                         [state_sync.EDITION, state_sync.TRAVAUX])

    def test_the_checkpoint_never_names_the_event_chain(self):
        with_key, without = self.states()
        self.assertEqual(registre.checkpoint_text(with_key), registre.checkpoint_text(without))
        self.assertNotIn("evenements", registre.checkpoint_text(with_key))


# --------------------------------------------------------------------------- #
# emit: fail-soft hook, public artefacts byte-identical
# --------------------------------------------------------------------------- #
def payload(edition_issue="d1") -> dict:
    return {"clustered_at": "2026-09-10T13:00:00+00:00",
            "chancellery_institutions": ["cbc", "le-soleil", "ville-quebec"],
            "change_ledger": {"has_previous": False, "new": [], "developed": [], "quiet": []},
            "issues": [{"issue_id": edition_issue, "question": "Sujet inventé", "item_count": 2,
                        "official_voice_count": 1, "sources": ["le-soleil", "ville-quebec"],
                        "silence": {"silent": [{"institution_id": "cbc", "institution_name": "CBC",
                                                "source_kind": "media"}]}}]}


def history(edition: str) -> dict:
    return {"method": "dossier-history-v1", "updated_at": edition}


PUBLIC = ("chain.json", "checkpoint.txt", "institutions.json", "travaux.json")


class EmitRunner:
    """registre.emit into a temporary root, output captured (a mixin for TestCase)."""

    def run_emit(self, root: Path, edition: str, *, events=None, events_path=None, quiet=True):
        kwargs = {"events": events}
        kwargs["events_path"] = events_path if events_path is not None else root / "no-such" / "latest_events.json"
        out = io.StringIO()
        with redirect_stdout(out):
            state = registre.emit(payload(), {"fetched_at": edition, "events": [{"event_id": "R-1"}]},
                                  history=history(edition), collection={},
                                  state_path=root / "state.json", out_dir=root / "registre",
                                  out_html=root / "registre.html", **kwargs)
        self.last_output = out.getvalue()
        return state

    def public_bytes(self, root: Path) -> dict[str, bytes]:
        files = {name: (root / "registre" / name).read_bytes() for name in PUBLIC}
        files["registre.html"] = (root / "registre.html").read_bytes()
        return files


class EmitHook(EmitRunner, unittest.TestCase):
    def test_events_are_sealed_after_the_edition_and_bound_to_its_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "events" / "latest_events.json"
            path.parent.mkdir()
            path.write_text(json.dumps(rich_view(E1)), encoding="utf-8")
            state = self.run_emit(root, E1, events_path=path)
            self.assertIn("registre: evenements appended", self.last_output)
            seal = state["evenements"]["seals"][0]
            self.assertEqual(seal["record"]["edition"], state["seals"][0]["edition"])
            self.assertEqual(seal["record"]["edition_root"], state["seals"][0]["root"])
            # re-render (the hourly roads lane): nothing minted, nothing moved
            before = (root / "state.json").read_bytes()
            state = self.run_emit(root, E1, events_path=path)
            self.assertIn("registre: evenements confirmed", self.last_output)
            self.assertEqual(len(state["evenements"]["seals"]), 1)
            self.assertEqual((root / "state.json").read_bytes(), before)
            # next edition
            path.write_text(json.dumps(view(E2, [event("next")])), encoding="utf-8")
            state = self.run_emit(root, E2, events_path=path)
            self.assertEqual([s["seq"] for s in state["evenements"]["seals"]], [1, 2])
            self.assertEqual(state["evenements"]["seals"][1]["record"]["edition_root"], state["seals"][1]["root"])
            on_disk = registre.load_state(root / "state.json")
            self.assertTrue(registre.verify_event_chain(on_disk["evenements"]["seals"])[0])

    def test_public_artefacts_are_byte_identical_with_or_without_events(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = Path(a), Path(b)
            for edition, doc in ((E1, rich_view(E1)), (E1, rich_view(E1)), (E2, view(E2, [event("x")]))):
                sa = self.run_emit(ra, edition)                      # no event view at all
                sb = self.run_emit(rb, edition, events=doc)
                self.assertEqual(self.public_bytes(ra), self.public_bytes(rb), edition)
                self.assertEqual(sa["seals"], sb["seals"])
                self.assertEqual(sa["voice"], sb["voice"])
                self.assertEqual(sa["travaux"], sb["travaux"])
            self.assertEqual(len(sb["evenements"]["seals"]), 2)
            self.assertEqual(sa["evenements"]["seals"], [])

    def test_absent_corrupt_hostile_or_foreign_views_mint_nothing(self):
        nested = "[" * 200000 + "]" * 200000
        cases = {
            "absent": None,
            "corrupt": "{not json",
            "not utf-8": b"\xff\xfe\x00{",
            "nested": nested,
            "a list": json.dumps([event("a")]),
            "foreign edition": json.dumps(view(E2, [event("a")])),
            "no clock": json.dumps({"events": [event("a")]}),
            "bad entry": json.dumps(view(E1, [event("a"), {"event_id": "<script>"}])),
            "surrogate": '{"edition": "%s", "events": [{"event_id": "\\ud800"}]}' % E1,
        }
        with tempfile.TemporaryDirectory() as ref_dir:
            ref = Path(ref_dir)
            reference = self.run_emit(ref, E1)
            ref_public = self.public_bytes(ref)
            for name, body in cases.items():
                with self.subTest(name), tempfile.TemporaryDirectory() as tmp:
                    root = Path(tmp)
                    path = root / "latest_events.json"
                    if isinstance(body, bytes):
                        path.write_bytes(body)
                    elif body is not None:
                        path.write_text(body, encoding="utf-8")
                    state = self.run_emit(root, E1, events_path=path)
                    self.assertEqual(state["evenements"]["seals"], [], name)
                    self.assertEqual(state["seals"], reference["seals"], name)
                    self.assertEqual(self.public_bytes(root), ref_public, name)
                    self.assertIn("registre: evenements", self.last_output)
                    self.assertNotIn("appended (shadow", self.last_output.split("registre: evenements")[-1])

    def test_oversized_view_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            path = root / "latest_events.json"
            path.write_text(json.dumps(rich_view(E1)), encoding="utf-8")
            with mock.patch.object(registre, "EVENTS_MAX_BYTES", 64):
                state = self.run_emit(root, E1, events_path=path)
            self.assertEqual(state["evenements"]["seals"], [])
            self.assertIn("oversized", self.last_output)

    def test_a_fault_inside_the_event_chain_leaves_everything_else_intact(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = Path(a), Path(b)
            first = self.run_emit(ra, E1, events=rich_view(E1))
            self.run_emit(rb, E1, events=rich_view(E1))
            with mock.patch.object(registre, "seal_events", side_effect=RuntimeError("boom")):
                state = self.run_emit(ra, E2, events=view(E2, [event("x")]))
            self.assertIn("fault (RuntimeError: boom)", self.last_output)
            self.assertEqual(state["evenements"], first["evenements"])
            self.run_emit(rb, E2)
            self.assertEqual(self.public_bytes(ra), self.public_bytes(rb))
            with mock.patch.object(registre, "event_entry", side_effect=MemoryError()):
                state = self.run_emit(ra, E2, events=view(E2, [event("x")]))
            self.assertEqual(state["evenements"], first["evenements"])

    def test_an_old_state_gains_the_chain_without_touching_its_seals(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old = legacy_state()
            (root / "state.json").write_text(json.dumps(old), encoding="utf-8")
            state = self.run_emit(root, E2, events=view(E2, [event("a")]))
            self.assertEqual(state["seals"][0], old["seals"][0])
            self.assertEqual(state["seals"][1]["prev"], old["seals"][0]["root"])
            self.assertEqual(len(state["evenements"]["seals"]), 1)
            self.assertEqual(state["evenements"]["seals"][0]["record"]["edition_root"], state["seals"][1]["root"])


# --------------------------------------------------------------------------- #
# Closed lists: a code must belong to its list, a shape is not enough
# --------------------------------------------------------------------------- #
# Invented slugs with the exact shape of a code: a person's name and headlines.
SLUG_PERSON = "gaston-invente-personne"
SLUG_HEADLINE = "un-incendie-majeur-invente-dans-un-immeuble"
SLUG_TITLE = "manchette-inventee-en-slug"


class ClosedLists(unittest.TestCase):
    def test_slug_shaped_person_names_and_headlines_are_never_sealed(self):
        named = event("slug-a", type=SLUG_PERSON, places=[SLUG_TITLE, "limoilou", SLUG_PERSON],
                      institutions=[SLUG_PERSON, "le-soleil"])
        derived = event("slug-b", type=SLUG_HEADLINE, places=["saint-roch"], institutions=None,
                        members=[member("slug-b1", institution=SLUG_PERSON), member("slug-b2")])
        dropped: dict = {}
        record, why = registre.events_record(view(E1, [named, derived]), E1, "", dropped=dropped)
        self.assertEqual(why, "")
        by_id = {e["event_id"]: e for e in record["events"]}
        a, b = by_id[ev_id("slug-a")], by_id[ev_id("slug-b")]
        self.assertEqual((a["type"], b["type"]), ("", ""))          # not established
        self.assertEqual(a["places"], ["limoilou"])
        self.assertEqual(b["places"], ["saint-roch"])
        self.assertEqual(a["institutions"], ["le-soleil"])
        self.assertEqual(b["institutions"], ["le-soleil"])
        sealed = registre.canonical(record).decode("utf-8")
        for needle in ("gaston", "personne", "incendie-majeur", "manchette", "invente"):
            self.assertNotIn(needle, sealed, needle)
        self.assertEqual(dropped, {"type": 2, "places": 2, "institutions": 2})

    def test_every_code_of_the_closed_lists_is_sealed(self):
        import vocabulaire
        types = vocabulaire.type_codes()
        places = vocabulaire.place_codes()
        self.assertIn("unclassified", types)
        events = [event(f"type-{code}", type=code) for code in types]
        events.append(event("all-places", places=list(places),
                            members=[member(f"inst-{i}", institution=i) for i in sorted(FIXTURE_INSTITUTIONS)]))
        dropped: dict = {}
        record, why = registre.events_record(view(E1, events), E1, "", dropped=dropped)
        self.assertEqual(why, "")
        self.assertEqual(dropped, {})
        by_id = {e["event_id"]: e for e in record["events"]}
        self.assertEqual([by_id[ev_id(f"type-{code}")]["type"] for code in types], types)
        self.assertEqual(by_id[ev_id("all-places")]["places"], places)     # order kept
        self.assertEqual(by_id[ev_id("all-places")]["institutions"], sorted(FIXTURE_INSTITUTIONS))

    def test_the_lists_are_the_vocabulary_and_every_registry_institution(self):
        import vocabulaire
        closed, why = registre.closed_codes()
        self.assertEqual(why, "")
        self.assertEqual(closed["type"], frozenset(vocabulaire.type_codes()))
        self.assertEqual(closed["places"], frozenset(vocabulaire.place_codes()))
        # a cut source (enabled: false) keeps its institution: sticky members stay sealable
        self.assertEqual(closed["institutions"], frozenset(FIXTURE_INSTITUTIONS))

    def test_without_the_closed_lists_nothing_is_sealed(self):
        import vocabulaire
        doc = view(E1, [event("a")])
        with mock.patch.object(vocabulaire, "type_codes", return_value=[]):
            self.assertEqual(registre.events_record(doc, E1, ""), (None, "no-vocabulary (empty tables)"))
        with mock.patch.object(vocabulaire, "place_codes", side_effect=OSError("gone")):
            self.assertEqual(registre.events_record(doc, E1, ""), (None, "no-vocabulary (OSError)"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            no_block = root / "no-block.yaml"
            no_block.write_text("autre: 1\n", encoding="utf-8")
            empty = root / "empty.yaml"
            empty.write_text("sources:\n", encoding="utf-8")
            for path, why in ((root / "missing.yaml", "no-institutions (FileNotFoundError)"),
                              (no_block, "no-institutions (SystemExit)"),
                              (empty, "no-institutions (empty registry)")):
                with mock.patch.object(registre, "SOURCES_PATH", path):
                    self.assertEqual(registre.events_record(doc, E1, ""), (None, why))
            with self.assertRaises(ValueError), mock.patch.object(registre, "SOURCES_PATH", empty):
                registre.event_entry(event("a"))
            # The SystemExit of load_sources never escapes the shadow chain.
            with mock.patch.object(registre, "SOURCES_PATH", no_block):
                state, action = registre.seal_events_soft(registre.empty_state(), E1, doc)
            self.assertEqual((state["evenements"]["seals"], action), ([], "no-institutions (SystemExit)"))
            # The render goes on: the edition is sealed, the event chain is left as it was.
            with mock.patch.object(registre, "SOURCES_PATH", root / "missing.yaml"):
                out = io.StringIO()
                with redirect_stdout(out):
                    state = registre.emit(payload(), {"fetched_at": E1, "events": []}, history=history(E1),
                                          collection={}, state_path=root / "state.json",
                                          out_dir=root / "registre", out_html=root / "registre.html",
                                          events=doc, events_path=root / "absent.json")
            self.assertEqual(len(state["seals"]), 1)
            self.assertEqual(state["evenements"]["seals"], [])
            self.assertIn("registre: evenements no-institutions (FileNotFoundError)", out.getvalue())


# --------------------------------------------------------------------------- #
# Anchors: the City's own ids, verbatim; every drop counted
# --------------------------------------------------------------------------- #
# Invented ids in the shapes the WZDX feed really uses (spaces, slashes,
# accents, parentheses, an embedded newline).
WZDX_IDS = ("XYZ-20990101-AB-Phase 2", "QRS-20990101-007-ABC / DEF", "Chantier inventé (Phase 3)",
            "LMN-20990101-XY-01\n_1", "Rue Inventée-de l'Exemple", "20990231406-1")
UNSAFE_REFS = ("https://exemple.test/chantier", "x" * 129, "", "   ", "a‮b", "a\x00b", "a\x85b",
               "a​b", "\ud800")


def anchor(kind: str, ref) -> dict:
    return {"type": kind, "ref": ref, "rule": "invented-rule", "status": "linked_by_rule"}


class Anchors(unittest.TestCase):
    def test_wzdx_style_ids_are_sealed_verbatim(self):
        ev = event("wzdx", anchors=[anchor("roadwork", ref) for ref in WZDX_IDS])
        dropped: dict = {}
        record, why = registre.events_record(view(E1, [ev]), E1, "", dropped=dropped)
        self.assertEqual(why, "")
        self.assertEqual(dropped, {})
        self.assertEqual(record["events"][0]["anchors"],
                         [{"type": "roadwork", "ref": ref} for ref in sorted(WZDX_IDS)])
        # the same strings the travaux chain seals from the same feed
        state, _ = registre.seal_roadworks(registre.empty_state(),
                                           {"fetched_at": E1, "events": [{"event_id": r} for r in WZDX_IDS]})
        self.assertEqual(state["travaux"]["latest_record"]["active"],
                         [a["ref"] for a in record["events"][0]["anchors"]])
        # and the record still seals and verifies
        state, action = registre.seal_events(registre.empty_state(), record, dropped)
        self.assertEqual(action, "appended")
        self.assertTrue(registre.verify_event_chain(state["evenements"]["seals"])[0])

    def test_unsafe_refs_are_dropped_and_counted(self):
        rows = [anchor("roadwork", ref) for ref in UNSAFE_REFS]
        rows += [anchor("consultation", "www.exemple.test"), anchor("consultation", 1011),
                 anchor("consultation", "767"), anchor("outage", "not-an-item-id"),
                 anchor("outage", item_id("panne")), anchor("edition_seal", 0), anchor("edition_seal", 58)]
        dropped: dict = {}
        record, _ = registre.events_record(view(E1, [event("unsafe", anchors=rows)]), E1, "", dropped=dropped)
        self.assertEqual(record["events"][0]["anchors"], [
            {"type": "consultation", "ref": "1011"}, {"type": "consultation", "ref": "767"},
            {"type": "edition_seal", "ref": "58"}, {"type": "outage", "ref": item_id("panne")}])
        self.assertEqual(dropped, {"anchors": len(UNSAFE_REFS) + 3})
        self.assertNotIn("exemple.test", registre.canonical(record).decode("utf-8"))

    def test_an_event_keeps_at_most_the_anchor_cap_official_pointers_first(self):
        rows = [anchor("edition_seal", n) for n in range(1, 101)]
        rows += [anchor("roadwork", ref) for ref in WZDX_IDS[:3]]
        rows += [anchor("official_item", item_id("officiel")), anchor("consultation", 767)]
        dropped: dict = {}
        record, _ = registre.events_record(view(E1, [event("cap", anchors=rows)]), E1, "", dropped=dropped)
        kept = record["events"][0]["anchors"]
        self.assertEqual(len(kept), registre.EVENT_ANCHORS_MAX)
        self.assertEqual(kept, sorted(kept, key=lambda a: (a["type"], a["ref"])))
        seals = sorted(int(a["ref"]) for a in kept if a["type"] == "edition_seal")
        self.assertEqual(seals, list(range(101 - (registre.EVENT_ANCHORS_MAX - 5), 101)))   # the newest
        self.assertEqual({a["type"] for a in kept if a["type"] != "edition_seal"},
                         {"roadwork", "official_item", "consultation"})
        self.assertEqual(dropped, {"anchors": 105 - registre.EVENT_ANCHORS_MAX})

    def test_a_runaway_anchor_list_is_bounded_not_sealed_whole(self):
        """The review's shape (50 events x thousands of distinct anchors), scaled down."""
        events = [event(f"runaway{n}", anchors=[anchor("roadwork", f"W{n}-{k}") for k in range(600)])
                  for n in range(50)]
        dropped: dict = {}
        record, _ = registre.events_record(view(E1, events), E1, "", dropped=dropped)
        self.assertTrue(all(len(e["anchors"]) == registre.EVENT_ANCHORS_MAX for e in record["events"]))
        self.assertEqual(dropped, {"anchors": 50 * (600 - registre.EVENT_ANCHORS_MAX)})
        self.assertLess(registre.record_stored_bytes(record), registre.EVENTS_RECORD_MAX)

    def test_drops_are_kept_in_the_seal_header_and_printed_on_every_render(self):
        doc = view(E1, [event("drop", type=SLUG_PERSON,
                              anchors=[anchor("roadwork", UNSAFE_REFS[0]), anchor("roadwork", "a‮b"),
                                       anchor("roadwork", WZDX_IDS[0])])])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            kwargs = dict(history=history(E1), collection={}, state_path=root / "state.json",
                          out_dir=root / "registre", out_html=root / "registre.html", events=doc,
                          events_path=root / "absent.json")
            out = io.StringIO()
            with redirect_stdout(out):
                state = registre.emit(payload(), {"fetched_at": E1, "events": []}, **kwargs)
            seal = state["evenements"]["seals"][0]
            self.assertEqual(seal["dropped"], {"anchors": 2, "type": 1})
            self.assertNotIn("dropped", seal["record"])                       # outside the leaf
            self.assertEqual(seal["leaf"], registre.leaf_of(seal["record"]))
            self.assertIn("registre: evenements appended; left out of the record: anchors 2, type 1",
                          out.getvalue())
            before = (root / "state.json").read_bytes()
            out = io.StringIO()
            with redirect_stdout(out):
                registre.emit(payload(), {"fetched_at": E1, "events": []}, **kwargs)
            self.assertIn("registre: evenements confirmed; left out of the record: anchors 2, type 1",
                          out.getvalue())
            self.assertEqual((root / "state.json").read_bytes(), before)
            self.assertTrue(registre.verify_event_chain(registre.load_state(root / "state.json")
                                                        ["evenements"]["seals"])[0])


class ViewStatus(unittest.TestCase):
    def test_a_view_its_builder_did_not_build_is_not_sealed(self):
        """events.py writes an empty view with a non-ok status when it refuses an
        edition or cannot read its store: sealing it would be a false 'no event'."""
        for status, shown in (("edition_refused", "edition_refused"), ("store_unreadable", "store_unreadable"),
                              (None, "unrecognised"), (5, "unrecognised"), ("<b>x</b>", "unrecognised")):
            doc = dict(view(E1, []), status=status)
            self.assertEqual(registre.events_record(doc, E1, ""),
                             (None, f"not-built (the view's status is {shown})"), status)
        record, why = registre.events_record(dict(view(E1, [event("ok")]), status="ok"), E1, "")
        self.assertEqual((why, len(record["events"])), ("", 1))
        record, why = registre.events_record(view(E1, []), E1, "")      # section 3: no status at all
        self.assertEqual((why, record["events"]), ("", []))


# --------------------------------------------------------------------------- #
# Size: the record can never grow, then collapse, the private state
# --------------------------------------------------------------------------- #
def roadwork_ref(n: int, k: int) -> str:
    """An invented WZDX-style id of about 100 characters."""
    return f"CHANTIER-INVENTE-{n:05d}-{k:02d} - Phase {k} - " + "x" * 60


def heavy_event(n: int) -> dict:
    return event(f"heavy{n}", anchors=[anchor("roadwork", roadwork_ref(n, k))
                                       for k in range(registre.EVENT_ANCHORS_MAX)])


def sized_view(edition: str, stored_bytes: int) -> dict:
    """An invented view whose record is close to `stored_bytes` stored bytes."""
    empty = registre.record_stored_bytes(registre.events_record(view(edition, []), edition, "")[0])
    one = registre.record_stored_bytes(registre.events_record(view(edition, [heavy_event(0)]), edition, "")[0])
    count = max(1, round((stored_bytes - empty) / (one - empty)))
    return view(edition, [heavy_event(k) for k in range(count)])


def edition_at(n: int) -> str:
    return f"2026-09-{1 + n // 4:02d}T{(n % 4) * 6:02d}:00:00+00:00"


class RecordSize(EmitRunner, unittest.TestCase):
    def test_stored_bytes_are_what_store_io_writes(self):
        record, _ = registre.events_record(rich_view(), E1, "ab" * 32)
        state, _ = registre.seal_events(registre.empty_state(), record, {"anchors": 1})
        bare = json.loads(json.dumps(state))
        del bare["evenements"]["seals"][0]["record"]
        with tempfile.TemporaryDirectory() as tmp:
            sizes = []
            for n, doc in enumerate((state, bare)):
                path = Path(tmp) / f"{n}.json"
                registre.store_io.write_json_atomic(path, doc)
                sizes.append(len(path.read_bytes().replace(b"\r\n", b"\n")))   # LF, as on the runner
        # the key `"record": `, its comma, newline and indent are the only bytes not in the record
        self.assertEqual(sizes[0] - sizes[1], registre.record_stored_bytes(record) + len(',\n') + 8 + len('"record": '))
        self.assertGreater(registre.record_stored_bytes(record), 2 * len(registre.canonical(record)))

    def test_an_oversized_record_is_never_minted(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            ra, rb = Path(a), Path(b)
            self.run_emit(ra, E1, events=rich_view(E1))
            self.run_emit(rb, E1, events=rich_view(E1))
            before = registre.load_state(ra / "state.json")["evenements"]
            # same length as the edition root the emit binds the record to
            size = registre.record_stored_bytes(registre.events_record(view(E2, [event("x")]), E2, "0" * 64)[0])
            with mock.patch.object(registre, "EVENTS_RECORD_MAX", size - 1):
                state = self.run_emit(ra, E2, events=view(E2, [event("x")]))
            self.assertIn(f"registre: evenements record-oversized ({size} stored bytes, cap {size - 1}); "
                          f"nothing minted", self.last_output)
            self.assertEqual(state["evenements"], before)
            self.assertEqual(len(state["seals"]), 2)                 # the edition seal is unaffected
            self.run_emit(rb, E2)
            self.assertEqual(self.public_bytes(ra), self.public_bytes(rb))

    def test_one_edition_releases_less_than_one_record(self):
        """Whatever the sizes, the records kept shrink by less than EVENTS_RECORD_MAX
        from one edition to the next, stay within the budget, and the newest seal
        always keeps its record."""
        rng = random.Random(20261006)
        cap = 6000
        with mock.patch.multiple(registre, EVENTS_RECORD_MAX=cap, EVENTS_RECORD_BYTES=4 * cap,
                                 EVENTS_RECORD_CAP=5):
            state = registre.empty_state()
            kept_before = 0
            minted = refused = 0
            for n in range(300):
                pad = rng.choice((0, 1, 5, rng.randrange(0, 150), rng.randrange(100, 140)))
                edition = (datetime(2027, 1, 1, tzinfo=timezone.utc) + timedelta(hours=n)).isoformat()
                record = {"method": registre.EVENTS_METHOD, "edition": edition, "edition_root": "",
                          "events": [{"event_id": f"ev-{n:016x}", "pad": ["x" * 30] * pad}]}
                state, action = registre.seal_events(state, record)
                seals = state["evenements"]["seals"]
                kept = [registre.record_stored_bytes(s["record"]) for s in seals if "record" in s]
                if action == "appended":
                    minted += 1
                    self.assertIn("record", seals[-1])
                    self.assertLess(kept_before - sum(kept), cap, n)
                else:
                    refused += 1
                    self.assertTrue(action.startswith("record-oversized"), action)
                self.assertLessEqual(sum(kept), 4 * cap)
                self.assertLessEqual(len(kept), 5)
                kept_before = sum(kept)
            self.assertGreater(minted, 100)
            self.assertGreater(refused, 10)
            self.assertTrue(registre.verify_event_chain(state["evenements"]["seals"])[0])

    def test_big_then_small_editions_never_trip_the_persist_guard(self):
        """The review's scenario: a runaway edition, then normal ones. Every
        persist of the sequence must pass state_sync.persist_refusals over a
        durable state of the documented floor (2 x EVENTS_RECORD_MAX)."""
        cap = registre.EVENTS_RECORD_MAX
        plan = [int(1.6 * cap)] + [int(0.95 * cap)] * 5 + [0] * 6
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(registre, "EVENTS_RECORD_CAP", 4):
            root = Path(tmp)
            filler = root / "data" / "normalized" / "latest_enriched.json"
            filler.parent.mkdir(parents=True)
            filler.write_bytes(b" " * (2 * cap))
            previous = None
            outcomes = []
            for n, size in enumerate(plan):
                edition = edition_at(n)
                doc = sized_view(edition, size) if size else view(edition, [event(f"small{n}")])
                out = io.StringIO()
                with redirect_stdout(out):
                    registre.emit(payload(), {"fetched_at": edition, "events": [{"event_id": "R-1"}]},
                                  history=history(edition), collection={},
                                  state_path=root / "data" / "registre" / "registre.json",
                                  out_dir=root / "public" / "registre", out_html=root / "public" / "r.html",
                                  events=doc, events_path=root / "absent.json")
                outcomes.append(out.getvalue().split("registre: evenements ")[-1].split(" ")[0])
                archive = root / f"state-{n}.tar.gz"
                with mock.patch.multiple(state_pack, ROOT=root, DATA=root / "data"):
                    state_pack.pack(archive)
                facts = state_sync.inspect_archive(archive)
                if previous is not None:
                    self.assertEqual(state_sync.persist_refusals(previous, facts, allow_shrink=False), ([], []),
                                     f"edition {n}: {previous['durable_bytes']} -> {facts['durable_bytes']}")
                previous = facts
            self.assertEqual(outcomes, ["record-oversized"] + ["appended"] * 11)
            seals = registre.load_state(root / "data" / "registre" / "registre.json")["evenements"]["seals"]
            self.assertEqual([("record" in s) for s in seals], [False] * 7 + [True] * 4)
            self.assertTrue(registre.verify_event_chain(seals)[0])


# --------------------------------------------------------------------------- #
# Verify from a JSON export
# --------------------------------------------------------------------------- #
class VerifyCli(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        state = registre.empty_state()
        for n, edition in enumerate((E1, E2, E3)):
            state, _ = seal_view(state, view(edition, [event(f"v{n}")]), edition)
        self.state = state
        self.state_path = self.root / "registre.json"
        registre.store_io.write_json_atomic(self.state_path, state)

    def cli(self, *argv: str) -> tuple[int, str]:
        out = io.StringIO()
        with redirect_stdout(out), mock.patch.object(registre, "STATE", self.state_path):
            code = registre.main(list(argv))
        return code, out.getvalue()

    def test_export_then_verify(self):
        export = self.root / "evenements.json"
        code, text = self.cli("--export-events", str(export))
        self.assertEqual(code, 0, text)
        doc = json.loads(export.read_text(encoding="utf-8"))
        self.assertEqual((doc["method"], doc["size"], doc["anchor_root"]), (registre.EVENTS_METHOD, 3, ""))
        self.assertEqual(doc["root"], self.state["evenements"]["seals"][-1]["root"])
        code, text = self.cli("--verify-events", str(export))
        self.assertEqual(code, 0, text)
        self.assertIn("OK 3 event seals verified", text)
        # a whole state file verifies too (its evenements key)
        code, text = self.cli("--verify-events", str(self.state_path))
        self.assertEqual(code, 0, text)

    def test_verify_cli_as_a_subprocess(self):
        export = self.root / "evenements.json"
        registre.store_io.write_json_atomic(export, registre.evenements_export(self.state))
        proc = subprocess.run([sys.executable, "-X", "utf8", str(harness.ROOT / "scripts" / "registre.py"),
                               "--verify-events", str(export)], capture_output=True, text=True, timeout=120)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("OK", proc.stdout)

    def test_tampered_or_foreign_exports_fail(self):
        doc = registre.evenements_export(self.state)
        tampered = json.loads(json.dumps(doc))
        tampered["seals"][1]["record"]["events"][0]["independent_count"] = 9
        wrong_root = dict(json.loads(json.dumps(doc)), root="0" * 64)
        main_chain = {"method": registre.METHOD, "seals": []}
        for name, body in (("tampered", tampered), ("wrong root", wrong_root), ("main chain", main_chain),
                           ("list", [1, 2])):
            path = self.root / f"{name}.json"
            path.write_text(json.dumps(body), encoding="utf-8")
            code, text = self.cli("--verify-events", str(path))
            self.assertEqual(code, 1, name)
            self.assertIn("FAIL", text, name)
        (self.root / "nested.json").write_text("[" * 100000, encoding="utf-8")
        code, text = self.cli("--verify-events", str(self.root / "nested.json"))
        self.assertEqual(code, 1)
        code, text = self.cli("--verify-events", str(self.root / "missing.json"))
        self.assertEqual(code, 1)

    def test_main_verify_is_unchanged_for_an_edition_chain(self):
        self.assertEqual(self.cli("--verify", str(self.state_path))[0], 0)


if __name__ == "__main__":
    unittest.main()
