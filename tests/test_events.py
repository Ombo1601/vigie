"""scripts/events.py: the shadow event builder (docs/EVENTS.md, MIGRATION.md step 5).

Every source, outlet, headline, excerpt, byline and URL below is invented for
the test. Nothing here reads data/."""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import types
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401  (puts scripts/ on sys.path; hermetic robots policy)
import event_match as em
import events
import facts
import normalize
import takedown
import vocabulaire

ROOT = Path(__file__).resolve().parents[1]


# --------------------------------------------------------------------------- #
# Invented registry
# --------------------------------------------------------------------------- #
def src(sid, inst, name, cls, group, lang="fr", kind="media", geo="quebec-city", nest="primary", enabled=True):
    return {"id": sid, "name": name, "institution": inst, "institution_name": name,
            "ownership_class": cls, "owner_group": group,
            "ownership_ref": "https://ownership.example.org/registre", "ownership_asof": "2026-10-01",
            "language": lang, "geo": geo, "nest_role": nest, "source_kind": kind, "type": "rss",
            "url": f"https://feeds.example.org/{sid}.xml", "homepage": f"https://{sid}.example.org/",
            "enabled": enabled}


SOURCES = [
    src("alpha-qc", "alpha", "Alpha Québec", "quebecor", "quebecor"),
    src("beta-qc", "beta", "Beta Matin", "quebecor", "quebecor"),
    src("gamma-fr", "gamma", "Gamma Radio", "public_broadcaster", "cbc-radio-canada"),
    src("gamma-en", "gamma-en", "Gamma English", "public_broadcaster", "cbc-radio-canada",
        lang="en", geo="linked", nest="linked"),
    src("delta", "delta", "Delta Quotidien", "independent", "delta"),
    src("ville-x", "ville-x", "Ville Xénon", "government", "ville-x", kind="official"),
    src("cut-feed", "cutmedia", "Coupé Hebdo", "independent", "cutmedia", enabled=False),
]


def registry(rules=None):
    return events.Registry([dict(s) for s in SOURCES], rules)


def sources_yaml(records=SOURCES) -> str:
    lines = ["version: 1", "sources:"]
    for rec in records:
        first = True
        for key, value in rec.items():
            text = ("true" if value else "false") if isinstance(value, bool) else str(value)
            lines.append(("  - " if first else "    ") + f"{key}: {text}")
            first = False
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Invented coverage
# --------------------------------------------------------------------------- #
LANG = {s["id"]: s["language"] for s in SOURCES}
NAME_OF: dict[str, str] = {}
TEXTS: list[str] = []   # every publisher-like string the fixtures carry


def url_for(name, source):
    return f"https://{source}.example.org/nouvelles/2026/{name}-zq"


def item(name, source, title, published, summary="", author=None, url=None):
    url = url or url_for(name, source)
    iid = normalize.stable_id(url, source, title, None)
    NAME_OF[iid] = name
    for text in (title, summary, author):
        if text:
            TEXTS.append(text)
    return {"id": iid, "title": title, "summary": summary, "published_at": published, "source_id": source,
            "url": url, "author": author, "language": LANG.get(source, "fr")}


def iso(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc).isoformat()


F1 = item("f1", "alpha-qc", "Incendie majeur dans un entrepôt de Limoilou : 60 000 litres de mazout menacés",
          iso(20, 8), "Les pompiers municipaux combattent les flammes à l'entrepôt Zéphirin.",
          author="Odile Pétronille-Quartz")
F2 = item("f2", "delta", "Limoilou : l'incendie de l'entrepôt Zéphirin menace 60 000 litres de mazout",
          iso(20, 9), "Les pompiers de Québec combattent toujours les flammes à l'entrepôt Zéphirin.",
          author="Barnabé Quillon-Ruisseau")
F4 = item("f4", "gamma-en", "Fire at Limoilou warehouse threatens 60,000 litres of fuel oil",
          iso(20, 9, 30), "Quebec City firefighters battle the blaze at the Zéphirin warehouse.",
          author="Wilhelmina Ostrander-Pike")
S1 = item("s1", "beta-qc", "Grève des chauffeurs d'autobus du RTC à Charlesbourg", iso(20, 10),
          "Le syndicat des chauffeurs déclenche une grève de trois jours à Charlesbourg.")
G1 = item("g1", "ville-x", "Travaux de réfection de la rue Quirion-Tabarnouche à Beauport", iso(20, 7),
          "La Ville annonce des travaux de réfection de la chaussée pendant douze jours.")
X1 = item("x1", "delta", "Le Festival des Lanternes zorblaxiennes ouvre ses portes", iso(20, 11),
          "Des milliers de lanternes zorblaxiennes illuminent le site dès ce soir.")
CUT = item("c1", "cut-feed", "Incendie majeur à Limoilou : l'entrepôt Zéphirin en flammes", iso(20, 8, 30))

# Edition 2: a broadcaster joins the fire, Delta edits the URL of its article
# (a new item id), the strike develops.
F3 = item("f3", "gamma-fr", "Incendie de l'entrepôt Zéphirin à Limoilou : les 60 000 litres de mazout épargnés",
          iso(20, 15), "Les pompiers de Québec ont maîtrisé l'incendie de l'entrepôt Zéphirin.")
F2B = item("f2b", "delta", F2["title"], iso(20, 9), F2["summary"], author="Barnabé Quillon-Ruisseau",
           url=url_for("f2", "delta").replace("/2026/", "/2026/mise-a-jour/"))
S2 = item("s2", "alpha-qc", "Charlesbourg : la grève des chauffeurs d'autobus du RTC se poursuit", iso(21, 9),
          "Deuxième journée de grève des chauffeurs d'autobus du RTC à Charlesbourg.")

# Edition 3 (a day later): the founding item has left the feeds.
S3 = item("s3", "gamma-fr", "RTC : troisième jour de grève des chauffeurs d'autobus à Charlesbourg", iso(22, 8),
          "Les chauffeurs d'autobus du RTC poursuivent leur grève à Charlesbourg.")

# Edition 4 (nine days later): a later fire at the same warehouse.
F9 = item("f9", "alpha-qc", "Nouvel incendie à l'entrepôt Zéphirin de Limoilou", iso(29, 8),
          "Les pompiers de Québec retournent à l'entrepôt Zéphirin.")


def payload(clock, items, **extra):
    return {"normalized_at": clock, "candidates": [dict(i) for i in items], **extra}


E1 = payload("2026-09-20T12:00:00+00:00", [F1, F2, F4, S1, G1, X1, CUT])
E2 = payload("2026-09-20T18:00:00+00:00", [F1, F2, F2B, F3, F4, S1, S2, G1])
E3 = payload("2026-09-22T12:00:00+00:00", [F3, S2, S3])
E4 = payload("2026-09-29T12:00:00+00:00", [F9])
EDITIONS = [("E1", E1), ("E2", E2), ("E3", E3), ("E4", E4)]
FIRE_ID = em.event_id_for(F1["id"])      # F1 is the earliest fire article: it founds the event
STRIKE_ID = em.event_id_for(S1["id"])


def build_all(editions=EDITIONS, reg=None, **kw):
    """(stores, views, opss) after each edition, the store round-tripped
    through JSON between editions as the pipeline does."""
    reg = reg or registry()
    store = events.empty_store()
    stores, views, opss = [], [], []
    for _, p in editions:
        store, view, ops = events.build(store, p, reg, **kw)
        store = json.loads(json.dumps(store))
        stores.append(store)
        views.append(view)
        opss.append(ops)
    return stores, views, opss


def by_id(doc):
    return {e["event_id"]: e for e in doc["events"]}


def owner_map(store):
    return {m["item_id"]: e["event_id"] for e in store["events"] for m in e["members"]}


def _strike_row(doc):
    """The stored row of the strike's founding article (an event unrelated to the fire)."""
    return next(m for m in by_id(doc)[STRIKE_ID]["members"] if m["item_id"] == S1["id"])


# --------------------------------------------------------------------------- #
# A hand-written checker for event-v1 (docs/EVENTS.md section 14), stdlib only
# --------------------------------------------------------------------------- #
_V1_REQUIRED = ("event_id", "schema", "method", "type", "places", "label", "born_edition", "last_edition",
                "window_state", "activity", "members", "institutions", "languages", "independence", "facts",
                "anchors", "language_pairs", "lineage", "seals")
_MEMBER_REQUIRED = ("item_id", "institution", "source_id", "language", "published_at", "first_seen",
                    "origin_class", "ownership_class", "owner_group")
_MEMBER_ALLOWED = set(_MEMBER_REQUIRED) | {"origin_rule", "url", "date_suspect"}
_CODE = re.compile(r"^[a-z0-9-]{2,48}$")


def _datetime_ok(value) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).tzinfo is not None
    except ValueError:
        return False


def check_v1(ev: dict) -> list[str]:
    """Every violation of the event-v1 schema (required keys, no extra keys,
    enums, patterns, types). Empty means valid."""
    err: list[str] = []

    def need(cond, msg):
        if not cond:
            err.append(msg)

    if not isinstance(ev, dict):
        return ["not an object"]
    for k in _V1_REQUIRED:
        need(k in ev, f"missing {k}")
    need(set(ev) <= set(_V1_REQUIRED), f"extra keys {sorted(set(ev) - set(_V1_REQUIRED))}")
    if err:
        return err
    need(isinstance(ev["event_id"], str) and re.fullmatch(r"ev-[0-9a-f]{16}", ev["event_id"]), "event_id pattern")
    need(ev["schema"] == 1 and not isinstance(ev["schema"], bool), "schema const 1")
    need(isinstance(ev["method"], str), "method string")
    need(isinstance(ev["type"], str) and _CODE.match(ev["type"]), "type pattern")
    need(isinstance(ev["places"], list) and len(ev["places"]) >= 1
         and all(isinstance(p, str) and _CODE.match(p) for p in ev["places"]), "places")
    lab = ev["label"]
    need(isinstance(lab, dict) and set(lab) == {"fr", "en"}
         and all(isinstance(lab[k], str) and len(lab[k]) <= 120 for k in lab), "label")
    need(_datetime_ok(ev["born_edition"]) and _datetime_ok(ev["last_edition"]), "edition date-times")
    need(ev["window_state"] in ("in_window", "out_of_window"), "window_state enum")
    need(ev["activity"] in ("new", "developed", "quiet"), "activity enum")
    need(isinstance(ev["members"], list) and len(ev["members"]) >= 1, "members minItems 1")
    for m in ev["members"] if isinstance(ev["members"], list) else []:
        if not isinstance(m, dict):
            err.append("member not an object")
            continue
        for k in _MEMBER_REQUIRED:
            need(k in m, f"member missing {k}")
        need(set(m) <= _MEMBER_ALLOWED, f"member extra keys {sorted(set(m) - _MEMBER_ALLOWED)}")
        need(isinstance(m.get("item_id"), str) and re.fullmatch(r"[0-9a-f]{16,64}", m.get("item_id", "")), "item_id")
        need(isinstance(m.get("institution"), str) and isinstance(m.get("source_id"), str), "member ids")
        need(m.get("language") in ("fr", "en"), "member language enum")
        need(m.get("published_at") is None or _datetime_ok(m.get("published_at")), "published_at")
        need(_datetime_ok(m.get("first_seen")), "first_seen")
        need(m.get("origin_class") in ("official", "wire", "press_release", "own_reporting", "unknown"), "origin enum")
        need(m.get("ownership_class") in ("public_broadcaster", "quebecor", "cooperative", "independent",
                                          "government"), "ownership enum")
        need(isinstance(m.get("owner_group"), str), "owner_group")
        if "origin_rule" in m:
            need(isinstance(m["origin_rule"], str), "origin_rule")
        if "url" in m:
            need(isinstance(m["url"], str) and m["url"].startswith(("https://", "http://")), "url uri")
        if "date_suspect" in m:
            need(isinstance(m["date_suspect"], bool), "date_suspect bool")
    need(isinstance(ev["institutions"], list) and all(isinstance(i, str) for i in ev["institutions"]), "institutions")
    need(isinstance(ev["languages"], list) and all(x in ("fr", "en") for x in ev["languages"]), "languages")
    ind = ev["independence"]
    need(isinstance(ind, dict) and {"groups", "count"} <= set(ind), "independence keys")
    if isinstance(ind, dict):
        need(isinstance(ind.get("groups"), list)
             and all(isinstance(g, list) and all(isinstance(i, str) for i in g) for g in ind["groups"]), "groups")
        need(isinstance(ind.get("count"), int) and not isinstance(ind.get("count"), bool) and ind["count"] >= 1,
             "count >= 1")
    for f in ev["facts"] if isinstance(ev["facts"], list) else [None]:
        if not isinstance(f, dict):
            err.append("facts")
            continue
        for k in ("slot", "values", "divergent", "method", "status"):
            need(k in f, f"fact missing {k}")
        slot = f.get("slot") or {}
        need(isinstance(slot, dict) and {"kind", "unit", "subject"} <= set(slot)
             and slot.get("kind") in ("count", "amount", "date", "place", "entity")
             and isinstance(slot.get("unit"), str), "fact slot")
        need(isinstance(f.get("values"), list) and len(f["values"]) >= 1, "fact values minItems 1")
        for v in f.get("values") or []:
            need(isinstance(v, dict) and {"value", "stated_by", "institutions"} <= set(v), "fact value keys")
            need(isinstance(v.get("value"), (int, str)) and not isinstance(v.get("value"), (bool, float)),
                 "fact value integer|string")
            need(isinstance(v.get("stated_by"), list) and isinstance(v.get("institutions"), list), "attribution")
        need(isinstance(f.get("divergent"), bool) and f.get("status") == "proposed", "divergent/status")
    for a in ev["anchors"] if isinstance(ev["anchors"], list) else [None]:
        need(isinstance(a, dict) and set(a) == {"type", "ref", "rule", "status"}
             and a.get("type") in ("official_item", "roadwork", "consultation", "outage", "edition_seal")
             and isinstance(a.get("ref"), str) and isinstance(a.get("rule"), str)
             and a.get("status") == "linked_by_rule", "anchor")
    for p in ev["language_pairs"] if isinstance(ev["language_pairs"], list) else [None]:
        need(isinstance(p, dict) and {"fr", "en", "rule", "same_owner"} <= set(p)
             and isinstance(p.get("same_owner"), bool), "language pair")
    lin = ev["lineage"]
    need(isinstance(lin, dict) and isinstance(lin.get("merged_into", ""), str)
         and isinstance(lin.get("absorbed", []), list) and isinstance(lin.get("detached", []), list), "lineage")
    need(isinstance(ev["seals"], list) and all(isinstance(s, int) and s >= 1 for s in ev["seals"]), "seals")
    return err


class SchemaChecker(unittest.TestCase):
    def test_the_checker_catches_violations(self):
        stores, _, _ = build_all(EDITIONS[:1])
        good = events.to_v1(stores[0]["events"][0])
        self.assertEqual(check_v1(good), [])
        for mutate in (
            lambda e: e.pop("lineage"),
            lambda e: e.update(extra=1),
            lambda e: e.update(event_id="ev-XYZ"),
            lambda e: e.update(window_state="ended"),
            lambda e: e.update(activity="resolved"),
            lambda e: e.update(places=[]),
            lambda e: e["members"][0].update(title="Un titre"),
            lambda e: e["members"][0].update(language="de"),
            lambda e: e["members"][0].update(origin_class="confirmed"),
            lambda e: e.update(independence={"groups": [], "count": 0}),
            lambda e: e.update(anchors=[{"type": "rumour", "ref": "x", "rule": "y", "status": "linked_by_rule"}]),
            lambda e: e.update(seals=[0]),
            lambda e: e.update(label={"fr": "x" * 121, "en": "y"}),
        ):
            bad = json.loads(json.dumps(good))
            mutate(bad)
            self.assertNotEqual(check_v1(bad), [], mutate)

    def test_every_store_and_view_event_is_event_v1(self):
        stores, views, _ = build_all()
        for doc in stores + views:
            for e in doc["events"]:
                self.assertEqual(check_v1(events.to_v1(e)), [], e["event_id"])
                self.assertTrue(set(e) - set(events.V1_KEYS) <= set(events.EXT_KEYS) | set(events.VIEW_KEYS),
                                sorted(set(e) - set(events.V1_KEYS)))


# --------------------------------------------------------------------------- #
# Identity and membership
# --------------------------------------------------------------------------- #
class Identity(unittest.TestCase):
    def test_id_is_minted_once_and_survives_outlets_joining_leaving_editing_and_opting_out(self):
        stores, _, _ = build_all()
        for n, store in enumerate(stores):
            self.assertIn(FIRE_ID, by_id(store), f"edition {n + 1}")
            self.assertEqual(owner_map(store)[F1["id"]], FIRE_ID)
        fire = by_id(stores[1])[FIRE_ID]
        self.assertEqual(fire["born_edition"], E1["normalized_at"])
        ids = {m["item_id"] for m in fire["members"]}
        self.assertIn(F3["id"], ids, "an outlet joining does not move the id")
        self.assertIn(F2B["id"], ids, "an edited URL is a new item that joins the same event")
        self.assertIn(F2["id"], ids, "the old URL keeps its membership")
        self.assertNotIn(F1, E3["candidates"])
        self.assertIn(FIRE_ID, by_id(stores[2]), "the founding item left the feeds: the id stands")
        # Opt-out (R10) of the founding outlet in a later run: still the same id.
        rules = takedown.Rules([{"id": "td-a", "kind": "source", "value": "alpha-qc", "requested_at": "2026-09-23",
                                 "by": "Alpha Québec", "status": "active"}])
        store, view, _ = events.build(stores[2], E3, registry(rules))
        self.assertIn(FIRE_ID, by_id(store))
        self.assertEqual(owner_map(store)[F1["id"]], FIRE_ID)

    def test_the_id_is_the_spec_formula_of_the_founding_item(self):
        self.assertEqual(FIRE_ID, "ev-" + hashlib.sha256(("event-v1|" + F1["id"]).encode()).hexdigest()[:16])

    def test_membership_is_sticky_across_editions(self):
        stores, _, _ = build_all()
        seen: dict[str, str] = {}
        for store in stores:
            for item_id, eid in owner_map(store).items():
                self.assertEqual(seen.setdefault(item_id, eid), eid, f"{NAME_OF.get(item_id)} moved")
        self.assertEqual(len(owner_map(stores[-1])), len(set(owner_map(stores[-1]))))

    def test_an_item_is_owned_once_and_rows_carry_collection_clocks(self):
        stores, _, _ = build_all()
        fire = by_id(stores[1])[FIRE_ID]
        rows = {m["item_id"]: m for m in fire["members"]}
        self.assertEqual(rows[F1["id"]]["first_seen"], E1["normalized_at"])
        self.assertEqual(rows[F3["id"]]["first_seen"], E2["normalized_at"], "first edition containing the item")
        self.assertEqual(rows[F4["id"]]["language"], "en")
        self.assertEqual(rows[F1["id"]]["ownership_class"], "quebecor")
        self.assertEqual(rows[F1["id"]]["url"], F1["url"])
        order = [(m["first_seen"], m["published_at"] or "", m["item_id"]) for m in fire["members"]]
        self.assertEqual(order, sorted(order), "timeline rows sorted by (first_seen, published_at, item_id)")

    def test_cut_source_items_found_nothing(self):
        _, _, opss = build_all(EDITIONS[:1])
        self.assertEqual(opss[0]["items"]["excluded"].get("source_not_enabled"), 1)


class _Table:
    """A scripted pair_tier: the merge rule tested exactly (as tests/test_event_match.py)."""

    def __init__(self, table):
        self.table = {tuple(sorted(k)): v for k, v in table.items()}

    def __call__(self, a, b, ctx):
        s = self.table.get(tuple(sorted((a["id"], b["id"]))), 0.0)
        return s, em.tier(s, True, ctx.thresholds)


class Merge(unittest.TestCase):
    def _items(self, kind):
        word = "Incendie" if kind == "fire" else "Grève"
        a1 = item("m-a1", "alpha-qc", "Incendie Zorblax à Limoilou", iso(20, 8))
        a2 = item("m-a2", "delta", "Incendie Zorblax à Limoilou, le bilan", iso(20, 9))
        b1 = item("m-b1-" + kind, "gamma-fr", f"{word} Zorblax à Limoilou : les secours", iso(20, 10))
        n1 = item("m-n1-" + kind, "beta-qc", f"{word} Zorblax à Limoilou : la suite", iso(20, 14))
        return a1, a2, b1, n1

    def _run(self, kind):
        a1, a2, b1, n1 = self._items(kind)
        reg = registry()
        first = {(a1["id"], a2["id"]): 0.95}
        later = {**first, (n1["id"], b1["id"]): 0.95, (n1["id"], a1["id"]): 0.5, (n1["id"], a2["id"]): 0.05,
                 (a1["id"], b1["id"]): 0.6, (a2["id"], b1["id"]): 0.5}
        with mock.patch.object(em, "pair_tier", _Table(first)):
            s1, _, _ = events.build(None, payload("2026-09-20T12:00:00+00:00", [a1, a2, b1]), reg)
        with mock.patch.object(em, "pair_tier", _Table(later)):
            s2, v2, o2 = events.build(json.loads(json.dumps(s1)),
                                      payload("2026-09-20T18:00:00+00:00", [a1, a2, b1, n1]), reg)
        return (a1, a2, b1, n1), s1, s2, v2, o2

    def test_two_strong_cross_pairs_and_an_average_link_merge_same_type_and_place(self):
        (a1, a2, b1, n1), s1, s2, v2, o2 = self._run("fire")
        self.assertEqual(len(s1["events"]), 2)
        ev_a, ev_b = em.event_id_for(a1["id"]), em.event_id_for(b1["id"])
        older, younger = sorted((ev_a, ev_b))   # both born in the same edition: smaller id is older
        store = by_id(s2)
        self.assertEqual(store[younger]["lineage"]["merged_into"], older)
        self.assertEqual(store[older]["lineage"]["absorbed"], [younger])
        self.assertEqual(o2["items"]["merges"], 1)
        self.assertEqual({m["item_id"] for m in store[ev_b]["members"]}, {b1["id"], n1["id"]}, "no member moves")
        self.assertEqual({m["item_id"] for m in store[ev_a]["members"]}, {a1["id"], a2["id"]})
        self.assertEqual(store[older]["independence"]["count"], 4 - 1, "alpha and beta are one Quebecor group")
        view = by_id(v2)
        self.assertEqual(list(view), [older], "the view shows the survivor only")
        self.assertEqual(view[older]["member_count"], 4)
        self.assertEqual(len(view[older]["members"]), 4)
        self.assertEqual(view[older]["tier"], "probable")

    def test_different_type_blocks_the_merge(self):
        _, _, s2, v2, o2 = self._run("strike")
        self.assertEqual(o2["items"]["merges"], 0)
        self.assertFalse(any(e["lineage"]["merged_into"] for e in s2["events"]))
        self.assertEqual(len(v2["events"]), 2)


# --------------------------------------------------------------------------- #
# Window, activity, retention
# --------------------------------------------------------------------------- #
class WindowAndActivity(unittest.TestCase):
    def test_activity_words_follow_the_change_ledger(self):
        stores, _, _ = build_all()
        self.assertEqual(by_id(stores[0])[FIRE_ID]["activity"], "new")
        self.assertEqual(by_id(stores[1])[FIRE_ID]["activity"], "developed")
        self.assertEqual(by_id(stores[1])[em.event_id_for(G1["id"])]["activity"], "quiet")
        self.assertEqual(by_id(stores[2])[FIRE_ID]["activity"], "quiet")
        self.assertEqual(by_id(stores[2])[STRIKE_ID]["activity"], "developed")

    def test_out_of_window_after_seven_days_and_a_later_item_starts_a_new_event(self):
        stores, views, _ = build_all()
        last = by_id(stores[3])
        self.assertEqual(last[FIRE_ID]["window_state"], "out_of_window")
        self.assertEqual(by_id(stores[2])[FIRE_ID]["window_state"], "in_window")
        self.assertEqual(owner_map(stores[3])[F9["id"]], em.event_id_for(F9["id"]), "a new event, not the old one")
        self.assertEqual(last[em.event_id_for(F9["id"])]["window_state"], "in_window")
        self.assertEqual([e["event_id"] for e in views[3]["events"]], [em.event_id_for(F9["id"])])

    def test_media_facts_reduce_to_counts_out_of_window_official_values_stay(self):
        stores, _, _ = build_all()
        live = by_id(stores[2])[FIRE_ID]
        self.assertEqual(live["facts_state"], "live")
        self.assertTrue(live["facts"], "the fire coverage states typed facts")
        out = by_id(stores[3])[FIRE_ID]
        self.assertEqual(out["facts_state"], "reduced")
        self.assertEqual(out["facts"], [], "media-only values are not kept out of window")
        self.assertTrue(out["facts_reduced"])
        for r in out["facts_reduced"]:
            self.assertIsInstance(r["values"], int)
            self.assertIsInstance(r["statements"], int)
        official = by_id(stores[3])[em.event_id_for(G1["id"])]
        self.assertEqual(official["window_state"], "out_of_window")
        self.assertTrue(official["facts"], "values stated by an official member are kept")
        for m in out["members"] + official["members"]:
            self.assertNotIn("facts", stores[3]["items"][m["item_id"]], "atoms dropped once out of window")

    def test_retention_keeps_the_newest_editions(self):
        with mock.patch.object(events, "RETENTION_EDITIONS", 2):
            stores, _, _ = build_all()
        last = stores[-1]
        self.assertEqual(last["editions"], [E3["normalized_at"], E4["normalized_at"]])
        self.assertEqual(set(by_id(last)), {STRIKE_ID, em.event_id_for(F9["id"])})
        self.assertEqual(set(last["items"]), {m["item_id"] for e in last["events"] for m in e["members"]})


# --------------------------------------------------------------------------- #
# Independence, language pairs, places, type, facts
# --------------------------------------------------------------------------- #
def row(i, inst, group, cls="own_reporting", rule="own_reporting.named_byline", lang="fr"):
    return {"item_id": i, "institution": inst, "source_id": inst, "owner_group": group, "language": lang,
            "origin_class": cls, "origin_rule": rule}


class Independence(unittest.TestCase):
    def test_owner_groups_wire_agencies_and_relays(self):
        rows = {
            "a": row("a", "alpha", "quebecor"), "b": row("b", "beta", "quebecor"),
            "c": row("c", "gamma", "cbc-radio-canada"), "d": row("d", "gamma-en", "cbc-radio-canada", lang="en"),
            "w1": row("w1", "alpha", "quebecor", "wire", "wire.author.cp"),
            "w2": row("w2", "delta", "delta", "wire", "wire.text_tag.cp"),
            "w3": row("w3", "delta", "delta", "wire", "wire.author.afp"),
            "o": row("o", "delta", "delta"),
            "p1": row("p1", "alpha", "quebecor", "press_release", "press_release.dateline"),
            "p2": row("p2", "gamma", "cbc-radio-canada", "press_release", "press_release.in_a_news_release"),
            "g": row("g", "ville-x", "ville-x", "official", "official.source_kind"),
            "u": {**row("u", "solo", ""), "owner_group": ""},
        }
        out = events.independence(sorted(rows), rows)
        self.assertEqual(out["groups"], sorted([["a", "b"], ["c", "d"], ["w1", "w2"], ["w3"], ["o"], ["p1", "p2"],
                                                 ["g"], ["u"]]))
        self.assertEqual(out["count"], 8)
        self.assertEqual(out["declarations"], ["g"], "official voices are declarations")
        self.assertEqual(out["reporting_count"], 7, "a declaration never corroborates the reporting")
        self.assertEqual(events.independence(["x"], {"x": row("x", "solo", "solo")})["count"], 1)
        # A near-duplicate copy is one origin, whoever owns it.
        copied = events.independence(sorted(rows), rows, [["c", "o"], ["zz", "o"]])
        self.assertIn(["c", "d", "o"], copied["groups"])
        self.assertEqual(copied["count"], 7)

    def test_near_duplicate_copies_are_one_origin_and_stay_one(self):
        copy = item("cp1", "delta", F1["title"], iso(20, 8, 20), F1["summary"])
        other = item("cp2", "gamma-fr", "Limoilou : incendie d'un entrepôt, 60 000 litres de mazout en péril",
                     iso(20, 8, 40), "Des pompiers de Québec sur place depuis l'aube à l'entrepôt Zéphirin.")
        self.assertTrue(events.near_duplicate(F1, copy))
        self.assertFalse(events.near_duplicate(F1, other))
        self.assertFalse(events.near_duplicate({"title": "Court titre"}, {"title": "Court titre"}),
                         "too short to call a copy")
        first = payload("2026-09-20T12:00:00+00:00", [F1, copy, other])
        s1, _, _ = events.build(None, first, registry())
        fire = by_id(s1)[FIRE_ID]
        self.assertEqual({m["item_id"] for m in fire["members"]}, {F1["id"], copy["id"], other["id"]})
        self.assertEqual(fire["copies"], [sorted([F1["id"], copy["id"]])])
        self.assertEqual(fire["independence"]["count"], 2, "Alpha's text republished by Delta is one origin")
        s2, _, _ = events.build(json.loads(json.dumps(s1)), payload("2026-09-20T18:00:00+00:00", [S1]), registry())
        self.assertEqual(by_id(s2)[FIRE_ID]["copies"], fire["copies"], "kept when the texts are gone")
        self.assertEqual(by_id(s2)[FIRE_ID]["independence"], fire["independence"])

    def test_radio_canada_and_cbc_are_one_group_in_the_real_registry(self):
        reg = events.Registry.load(ROOT / "sources.yaml", ROOT / "takedowns.yaml")
        rows = {}
        for i, sid in (("r", "radio-canada-quebec"), ("c", "cbc-montreal"), ("j", "journal-de-quebec")):
            r = {"item_id": i, "source_id": sid, "institution": reg.institution_of(sid), "origin_class": "unknown",
                 "origin_rule": "unknown.no_signal"}
            events._refresh_ownership(r, reg)
            rows[i] = r
        self.assertEqual(rows["r"]["owner_group"], rows["c"]["owner_group"])
        self.assertEqual(events.independence(sorted(rows), rows)["groups"], [["c", "r"], ["j"]])


class LanguagePairs(unittest.TestCase):
    def test_fr_en_members_pair_and_same_owner_is_flagged(self):
        stores, views, opss = build_all(EDITIONS[:2])
        fire = by_id(stores[1])[FIRE_ID]
        self.assertEqual(fire["languages"], ["en", "fr"])
        pairs = {(p["fr"], p["en"]): p for p in fire["language_pairs"]}
        self.assertIn((F3["id"], F4["id"]), pairs)
        self.assertTrue(pairs[(F3["id"], F4["id"])]["same_owner"], "Gamma FR and Gamma EN share an owner")
        self.assertFalse(pairs[(F1["id"], F4["id"])]["same_owner"])
        self.assertEqual(sorted(pairs), sorted(pairs, key=lambda k: k), "sorted by (fr, en)")
        for p in fire["language_pairs"]:
            self.assertIn(p["rule"], (events.PAIR_RULE_FEATURES, events.PAIR_RULE_MATCHER))
        self.assertEqual(opss[1]["edition_view"]["bilingual"], 1)

    def test_pairs_are_kept_when_the_texts_leave(self):
        stores, _, _ = build_all(EDITIONS[:3])
        self.assertNotIn(F4, E3["candidates"])
        self.assertEqual(by_id(stores[2])[FIRE_ID]["language_pairs"], by_id(stores[1])[FIRE_ID]["language_pairs"])


class PlacesTypeLabel(unittest.TestCase):
    def test_item_geo_beats_a_scope_only_place_hit(self):
        items = {"i1": {"places": ["ottawa"], "geo": "quebec-city"}}
        places, basis = events.event_places(["i1"], items)
        self.assertEqual(places[0], "quebec-city")
        self.assertEqual(places, ["quebec-city", "ottawa"])
        self.assertEqual(basis, "geo")
        # a named specific place wins over every scope
        items["i2"] = {"places": ["limoilou", "ottawa"], "geo": "quebec"}
        self.assertEqual(events.event_places(["i1", "i2"], items)[0][0], "limoilou")
        # a linked item that names Ottawa only is about Ottawa
        self.assertEqual(events.event_places(["i3"], {"i3": {"places": ["ottawa"], "geo": "linked"}}),
                         (["ottawa"], "named"))
        # the province geo (mostly the source's nest) yields to a scope the headline names
        self.assertEqual(events.event_places(["i4"], {"i4": {"places": ["ottawa"], "geo": "quebec"}}),
                         (["ottawa", "province"], "named"))
        self.assertEqual(events.event_places(["i5"], {"i5": {"places": [], "geo": "quebec"}}),
                         (["province"], "geo"))

    def test_no_place_falls_back_to_a_scope_code_and_the_label_names_none(self):
        places, basis = events.event_places(["i"], {"i": {"places": [], "geo": "linked"}})
        self.assertEqual((places, basis), ([vocabulaire.FALLBACK_PLACE], "fallback"))
        label = events.event_label("fire-building", places, basis)
        self.assertEqual(label["fr"], vocabulaire.type_label("fire-building", "fr"))
        self.assertEqual(events.event_label("fire-building", ["limoilou"], "named")["fr"],
                         vocabulaire.label("fire-building", "limoilou", "fr"))

    def test_real_titles_quebec_city_item_mentioning_canada_is_not_labelled_ottawa(self):
        reg = registry()
        it = events.item_view(item("geo1", "alpha-qc", "Une famille de Québec reçoit une aide du Canada",
                                   iso(20, 8), "La Ville de Québec accueille la famille."), reg,
                              "2026-09-20T12:00:00+00:00")
        it["enrich"] = {"geo": {"geo": "quebec-city"}}
        derived = events.derive_item(it)
        places, _ = events.event_places(["geo1"], {"geo1": derived})
        self.assertNotEqual(places[0], "ottawa")

    def test_event_type_equals_classify_type_over_member_titles(self):
        titles = ["Incendie majeur dans un entrepôt", "L'incendie de l'entrepôt est maîtrisé",
                  "Grève des cols bleus", "Un titre sans aucun indice"]
        for chosen in (titles[:2], titles[2:3], titles[3:], titles):
            items = {f"t{k}": {"type_scores": events.item_type_scores({"title": t})} for k, t in enumerate(chosen)}
            self.assertEqual(events.event_type(sorted(items), items), vocabulaire.classify_type(chosen)[0], chosen)

    def test_slots_from_stored_atoms_equal_facts_build_slots(self):
        items = [
            {"id": "a" * 24, "institution": "alpha", "title": "Incendie : 2 blessés et 14 logements évacués",
             "summary": "Les dommages sont évalués à 1,5 million $. Le maire de Québec s'est rendu sur place.",
             "published_at": iso(20, 8)},
            {"id": "b" * 24, "institution": "delta", "title": "Incendie : 3 blessés, 14 logements évacués",
             "summary": "Le 21 septembre, les pompiers sont revenus rue Zorblax à Limoilou.", "published_at": iso(20, 9)},
        ]
        rows = [(it["id"], it["institution"], json.loads(json.dumps(facts.extract_item(it)))) for it in items]
        mine = events.slots_from_atoms(rows)
        theirs = facts.build_slots(items)
        # Same rows as facts.build_slots for every kind but place ...
        self.assertEqual([r for r in mine if r["slot"]["kind"] != "place"],
                         [r for r in theirs if r["slot"]["kind"] != "place"])
        self.assertTrue(any(r["divergent"] for r in mine), "2 vs 3 injured: side by side")
        # ... where an event carries vocabulary codes only: the area hint maps
        # onto its code, the road name "zorblax" (no corridor code) is ignored.
        self.assertIn("zorblax", [v["value"] for r in theirs if r["slot"]["kind"] == "place" for v in r["values"]])
        places = [v["value"] for r in mine if r["slot"]["kind"] == "place" for v in r["values"]]
        self.assertEqual(places, ["limoilou"])
        for code in places:
            self.assertIsNotNone(vocabulaire.place_kind(code))

    def test_a_stored_junk_place_atom_never_reaches_an_event(self):
        """Atoms stored before facts.py guarded its road tokens ("a", "est",
        a road name with no code) are ignored by the event, whatever the store holds."""
        junk = [{"kind": "place", "unit": "road", "subject": None, "value": v, "qualifiers": ["exact"]}
                for v in ("a", "est", "entre", "zorblax-quirion")]
        good = [{"kind": "place", "unit": "road", "subject": None, "value": "laporte", "qualifiers": ["exact"]},
                {"kind": "place", "unit": "area", "subject": None, "value": "duberger", "qualifiers": ["exact"]},
                {"kind": "place", "unit": "area", "subject": None, "value": "nowhere-zq", "qualifiers": ["exact"]}]
        out = events.slots_from_atoms([("c" * 24, "alpha", junk + good)])
        self.assertEqual(sorted(v["value"] for r in out for v in r["values"]),
                         ["duberger-les-saules", "pierre-laporte-bridge"])
        self.assertIsNone(events.place_atom_code({"kind": "place", "unit": "road", "value": "unplaced"}))
        self.assertEqual(events.place_atom_code({"kind": "place", "unit": "x", "value": "limoilou"}), "limoilou")
        # In a build: a junk atom planted in the stored item codes stays out of the view.
        s1, _, _ = events.build(None, E1, registry())
        s1["items"][F1["id"]]["facts"] = junk + s1["items"][F1["id"]]["facts"]
        _, view, _ = events.build(json.loads(json.dumps(s1)), E2, registry())
        values = [v["value"] for e in view["events"] for r in e["facts"] if r["slot"]["kind"] == "place"
                  for v in r["values"]]
        self.assertTrue(values)
        for value in values:
            self.assertIsNotNone(vocabulaire.place_kind(value), value)

    def test_published_dates_null_when_implausible_and_suspect_when_late(self):
        first = "2026-09-20T12:00:00+00:00"
        self.assertEqual(events.published_fields("", first), (None, False))
        self.assertEqual(events.published_fields("0001-01-01T00:00:00Z", first), (None, True))
        self.assertEqual(events.published_fields("2026-09-20T20:00:00Z", first), ("2026-09-20T20:00:00+00:00", True))
        self.assertEqual(events.published_fields("2026-09-20T17:00:00-04:00", first),
                         ("2026-09-20T21:00:00+00:00", True))
        self.assertEqual(events.published_fields("2026-09-20T15:00:00+00:00", first), ("2026-09-20T15:00:00+00:00", False))
        zero = item("z0", "delta", "Incendie Zorblax à Beauport : date inconnue", "0001-01-01T00:00:00Z")
        store, _, _ = events.build(None, payload(first, [zero]), registry())
        r = store["events"][0]["members"][0]
        self.assertIsNone(r["published_at"])
        self.assertTrue(r["date_suspect"])
        self.assertEqual(r["first_seen"], first)


# --------------------------------------------------------------------------- #
# Anchors hook, silence roster
# --------------------------------------------------------------------------- #
class Anchors(unittest.TestCase):
    def setUp(self):
        events._ANCHORS_MODULE.clear()
        self.addCleanup(events._ANCHORS_MODULE.clear)

    def test_without_anchors_py_an_official_member_anchors_by_membership(self):
        with mock.patch.dict(sys.modules, {"anchors": None}):
            events._ANCHORS_MODULE.clear()
            self.assertIsNone(events.anchors_module())
            stores, _, opss = build_all(EDITIONS[:1])
        g = by_id(stores[0])[em.event_id_for(G1["id"])]
        self.assertEqual(g["anchors"], [{"type": "official_item", "ref": G1["id"],
                                         "rule": "membership-official-source", "status": "linked_by_rule"}])
        self.assertEqual(events.ANCHOR_RULE_MEMBERSHIP, "membership-official-source",
                         "the name scripts/anchors.py gives the same rule (RULE_OFFICIAL)")
        self.assertEqual(by_id(stores[0])[FIRE_ID]["anchors"], [], "no anchor is a measured absence")
        self.assertIn("absent", opss[0]["anchors"])

    def test_one_row_per_type_and_ref_a_fresh_row_replaces_a_stored_one(self):
        rows = [{"type": "official_item", "ref": "a" * 24, "rule": "membership-official-source"},
                {"type": "official_item", "ref": "a" * 24, "rule": "membership"},
                {"type": "roadwork", "ref": "wzdx-1", "rule": "roadwork-name-dates-v1"}]
        self.assertEqual(events.clean_anchors(rows),
                         [{"type": "official_item", "ref": "a" * 24, "rule": "membership", "status": "linked_by_rule"},
                          {"type": "roadwork", "ref": "wzdx-1", "rule": "roadwork-name-dates-v1",
                           "status": "linked_by_rule"}])
        merged = events.merge_anchors(rows[1:2], rows[:1] + [{"type": "consultation", "ref": "c-1", "rule": "r"}])
        self.assertEqual([(a["type"], a["ref"], a["rule"]) for a in merged],
                         [("consultation", "c-1", "r"), ("official_item", "a" * 24, "membership-official-source")])

    def _contract_module(self, calls):
        """A stand-in with the contract of the parallel scripts/anchors.py:
        keyword-only event_type, rows deduplicated by (type, ref), RULE_OFFICIAL,
        and a road rule that can only match when it is given the member texts."""
        fake = types.ModuleType("anchors")
        fake.RULE_OFFICIAL = "membership-official-source"

        def find_anchors(members, official_items=(), roadworks=None, consultations=None, outages=(),
                         vocab_places=None, *, event_type=None):
            calls.append({"event_type": event_type, "members": [dict(m) for m in members],
                          "official_items": list(official_items), "outages": [dict(o) for o in outages]})
            out = {}
            for m in members:
                if m.get("source_kind") == "official" or m.get("item_id") in official_items:
                    out[("official_item", m["item_id"])] = fake.RULE_OFFICIAL
            if event_type in ("roadworks", "road-closure"):
                text = " ".join(str(m.get("title") or "") for m in members).lower()
                for rw in roadworks or []:
                    if any(name.lower() in text for name in rw.get("road_names") or []):
                        out[("roadwork", rw["event_id"])] = "roadwork-name-dates-v1"
            return [{"type": t, "ref": r, "rule": rule, "status": "linked_by_rule"} for (t, r), rule in sorted(out.items())]

        fake.find_anchors = find_anchors
        return fake

    ROADWORKS = [{"event_id": "wzdx-77", "road_names": ["Rue Quirion-Tabarnouche"], "start_date": "2026-09-19T00:00:00Z",
                  "end_date": "2026-10-02T00:00:00Z", "vehicle_impact": "all-lanes-closed", "event_status": "active"}]

    def test_the_module_reads_member_texts_the_event_type_and_source_kinds_never_stored(self):
        calls = []
        with mock.patch.dict(sys.modules, {"anchors": self._contract_module(calls)}):
            events._ANCHORS_MODULE.clear()
            store, _, ops = events.build(None, E1, registry(), anchor_inputs={"roadworks": self.ROADWORKS})
        g = by_id(store)[em.event_id_for(G1["id"])]
        self.assertEqual(g["type"], "roadworks")
        self.assertEqual(g["anchors"], [
            {"type": "official_item", "ref": G1["id"], "rule": "membership-official-source", "status": "linked_by_rule"},
            {"type": "roadwork", "ref": "wzdx-77", "rule": "roadwork-name-dates-v1", "status": "linked_by_rule"}])
        call = next(c for c in calls if [m["item_id"] for m in c["members"]] == [G1["id"]])
        self.assertEqual(call["event_type"], "roadworks", "the event's own type, not a re-derivation")
        self.assertEqual((call["members"][0]["title"], call["members"][0]["source_kind"]), (G1["title"], "official"))
        self.assertEqual(call["official_items"], [G1["id"]], "the edition's official items, from sources.yaml")
        self.assertEqual(ops["anchors"], "anchors.find_anchors")
        blob = json.dumps(store, ensure_ascii=False)
        for text in (G1["title"], G1["summary"], F1["title"], F1["summary"]):
            self.assertNotIn(text, blob, "member texts reach the module in memory only")

    def test_no_duplicate_official_anchor_across_the_module_arriving(self):
        with mock.patch.dict(sys.modules, {"anchors": None}):
            events._ANCHORS_MODULE.clear()
            before, _, _ = events.build(None, E1, registry())
        with mock.patch.dict(sys.modules, {"anchors": self._contract_module([])}):
            events._ANCHORS_MODULE.clear()
            after, _, _ = events.build(json.loads(json.dumps(before)), E2, registry(),
                                       anchor_inputs={"roadworks": self.ROADWORKS})
        g = by_id(after)[em.event_id_for(G1["id"])]
        refs = [(a["type"], a["ref"]) for a in g["anchors"]]
        self.assertEqual(len(refs), len(set(refs)), "one row per (type, ref)")
        self.assertEqual([a for a in g["anchors"] if a["type"] == "official_item"],
                         [{"type": "official_item", "ref": G1["id"], "rule": "membership-official-source",
                           "status": "linked_by_rule"}])

    def test_without_anchor_inputs_as_in_a_replay_official_members_still_anchor(self):
        with mock.patch.dict(sys.modules, {"anchors": self._contract_module([])}):
            events._ANCHORS_MODULE.clear()
            store, _, _ = events.build(None, E1, registry())
        self.assertEqual([a["ref"] for a in by_id(store)[em.event_id_for(G1["id"])]["anchors"]], [G1["id"]])

    def test_the_real_anchors_module_when_it_is_merged(self):
        """Integration with scripts/anchors.py (built in parallel); skipped
        until it is on this branch."""
        events._ANCHORS_MODULE.clear()
        mod = events.anchors_module()
        if mod is None or not hasattr(mod, "RULE_OFFICIAL"):
            self.skipTest("scripts/anchors.py is not merged on this branch yet")
        stores = []
        store = None
        for _, p in EDITIONS[:2]:
            store, _, _ = events.build(store, p, registry(), anchor_inputs={"roadworks": self.ROADWORKS})
            store = json.loads(json.dumps(store))
            stores.append(store)
        g = by_id(stores[-1])[em.event_id_for(G1["id"])]
        refs = [(a["type"], a["ref"]) for a in g["anchors"]]
        self.assertEqual(len(refs), len(set(refs)))
        self.assertIn(("official_item", G1["id"]), refs)
        self.assertIn(("roadwork", "wzdx-77"), refs, "the road rule matched on the member's own text")
        self.assertEqual({a["rule"] for a in g["anchors"] if a["type"] == "official_item"}, {mod.RULE_OFFICIAL})
        for e in stores[-1]["events"]:
            self.assertEqual(check_v1(events.to_v1(e)), [], e["event_id"])

    def test_the_hook_calls_find_anchors_with_the_contract_and_validates_rows(self):
        calls = []

        def find_anchors(members, official_items, roadworks, consultations, outages, vocab_places):
            calls.append((len(members), len(official_items), len(roadworks), len(consultations), len(outages),
                          list(vocab_places)))
            return [{"type": "roadwork", "ref": "wzdx-1", "rule": "same-road", "status": "linked_by_rule"},
                    {"type": "rumour", "ref": "x", "rule": "y"}, "garbage",
                    {"type": "roadwork", "ref": "wzdx-1", "rule": "same-road"},
                    # a module bug: pointers to no record it was given never reach the store
                    {"type": "roadwork", "ref": F1["title"], "rule": "same-road"},
                    {"type": "official_item", "ref": F1["summary"], "rule": "membership-official-source"},
                    {"type": "edition_seal", "ref": "douze", "rule": "edition-presence"}]

        fake = types.ModuleType("anchors")
        fake.find_anchors = find_anchors
        with mock.patch.dict(sys.modules, {"anchors": fake}):
            events._ANCHORS_MODULE.clear()
            store, _, ops = events.build(None, E1, registry(), anchor_inputs={"roadworks": [{"event_id": "wzdx-1"}]})
        self.assertTrue(calls)
        self.assertEqual(by_id(store)[FIRE_ID]["anchors"],
                         [{"type": "roadwork", "ref": "wzdx-1", "rule": "same-road", "status": "linked_by_rule"}])
        self.assertEqual(ops["anchors"], "anchors.find_anchors")
        self.assertTrue(any("pointers to no record" in d for d in ops["diagnosis"]))
        self.assertNotIn(F1["title"], json.dumps(store, ensure_ascii=False))

    def test_a_faulty_anchors_module_falls_back_and_is_diagnosed(self):
        fake = types.ModuleType("anchors")
        fake.find_anchors = lambda *a: 1 / 0
        with mock.patch.dict(sys.modules, {"anchors": fake}):
            events._ANCHORS_MODULE.clear()
            store, _, ops = events.build(None, E1, registry())
        self.assertEqual(len(by_id(store)[em.event_id_for(G1["id"])]["anchors"]), 1)
        self.assertTrue(any("anchors.find_anchors failed" in d for d in ops["diagnosis"]))


class SilenceRoster(unittest.TestCase):
    def test_followed_institutions_without_a_member_with_measured_collection_state(self):
        collection = {"delta": {"items": 0, "feeds_ok": 0, "feeds_total": 1},
                      "beta": {"items": 4, "feeds_ok": 1, "feeds_total": 1},
                      "alpha": {"items": 3, "feeds_ok": 1, "feeds_total": 1},
                      "gamma": {"items": 0, "feeds_ok": 0, "feeds_total": 0}}
        _, view, _ = events.build(None, E1, registry(), collection=collection)
        strike = by_id(view)[STRIKE_ID]
        states = {r["institution"]: r["state"] for r in strike["silence"]}
        self.assertNotIn("beta", states, "the strike's own outlet is not silent")
        self.assertEqual(states["delta"], "collection_gap", "our fetch failed: Vigie's gap, not theirs")
        self.assertEqual(states["alpha"], "no_linked_item", "fetched fine, no linked article: the measured fact")
        self.assertEqual(states["gamma"], "not_established", "no feed measured: nothing can be said")
        self.assertEqual(states["gamma-en"], "not_established", "no collection fact at all for it")
        self.assertNotIn("cutmedia", states, "a cut source is not followed")
        for r in strike["silence"]:
            self.assertIn(r["state"], events.SILENCE_STATES)

    def test_not_established_only_where_no_fact_exists(self):
        self.assertEqual(events.collection_state("x", None), "not_established")
        self.assertEqual(events.collection_state("x", {}), "not_established")
        self.assertEqual(events.collection_state("x", {"x": "garbage"}), "not_established")
        self.assertEqual(events.collection_state("x", {"x": {"feeds_ok": 2, "feeds_total": 2}}), "no_linked_item")
        self.assertEqual(events.collection_state("x", {"x": {"feeds_ok": 1, "feeds_total": 2}}), "collection_gap")
        self.assertEqual(events.collection_state("x", {"x": {"feeds_ok": 0, "feeds_total": 0}}), "not_established")

    def test_a_voice_withdrawn_whole_is_listed_as_withdrawn_in_every_roster(self):
        rules = takedown.Rules([{"id": "td-g", "kind": "source", "value": "gamma-en", "requested_at": "2026-09-20",
                                 "by": "Gamma English", "status": "active"}])
        _, view, _ = events.build(None, E1, registry(rules), collection={})
        for e in view["events"]:
            rows = {r["institution"]: r for r in e["silence"]}
            self.assertEqual(rows["gamma-en"]["state"], "withdrawn", e["event_id"])
            self.assertEqual(rows["gamma-en"]["scope"], "institution")
            self.assertEqual(rows["gamma-en"]["institution_name"], "Gamma English")
            self.assertNotIn("gamma-en", e["institutions"])
            self.assertNotIn("en", e["languages"], "its English coverage is not counted either")
        self.assertIn("withdrawn", events.SILENCE_STATES)

    def test_the_in_memory_rule_equals_takedown_withdrawn_institutions(self):
        rules = takedown.Rules([{"id": "td-g", "kind": "source", "value": "gamma-en", "requested_at": "2026-09-20",
                                 "by": "Gamma English", "status": "active"},
                                {"id": "td-h", "kind": "host", "value": "delta.example.org",
                                 "requested_at": "2026-09-21", "by": "Delta Quotidien", "status": "active"}])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.yaml"
            path.write_text(sources_yaml(), encoding="utf-8", newline="\n")
            from_file = takedown.withdrawn_institutions(rules, path)
            loaded = events.Registry(events.ingest_rss.load_sources(path), rules, path).withdrawn_institutions()
        in_memory = events.Registry([dict(s) for s in SOURCES], rules).withdrawn_institutions()
        self.assertEqual(sorted(from_file), ["delta", "gamma-en"])
        self.assertEqual(loaded, from_file)
        self.assertEqual({k: v["institution_name"] for k, v in in_memory.items()},
                         {k: v["institution_name"] for k, v in from_file.items()})


# --------------------------------------------------------------------------- #
# R10
# --------------------------------------------------------------------------- #
def assert_never_credited(test: unittest.TestCase, ev: dict, gone: set[str]) -> None:
    """No field of a view event a renderer could read credits or counts a
    withdrawn member: it is listed under `withdrawn` and nowhere else."""
    present = {m["item_id"] for m in ev["members"]}
    test.assertFalse(present & gone)
    test.assertTrue(gone <= set(ev["withdrawn"]))
    test.assertEqual(ev["withdrawn_count"], len(ev["withdrawn"]))
    test.assertEqual(ev["member_count"], len(ev["members"]))
    test.assertEqual(ev["institutions"], sorted({m["institution"] for m in ev["members"]}))
    ind = ev["independence"]
    test.assertEqual(sorted(i for g in ind["groups"] for i in g), sorted(present))
    test.assertEqual(sorted(i for o in ind["origins"] for i in o["members"]), sorted(present))
    test.assertEqual(set(ind["keys"]), present)
    test.assertTrue(set(ind["declarations"]) <= present)
    test.assertEqual(ev["reporting_origin_count"], ind["reporting_count"])
    test.assertTrue(set(ev["declared_by"]) <= set(ev["institutions"]))
    for p in ev["copies"]:
        test.assertTrue(set(p) <= present)
    for p in ev["language_pairs"]:
        test.assertTrue({p["fr"], p["en"]} <= present)
    for f in ev["facts"]:
        for v in f["values"]:
            test.assertTrue(set(v["stated_by"]) <= present)
            test.assertTrue(set(v["institutions"]) <= set(ev["institutions"]))
    for a in ev["anchors"]:
        if a["type"] in events.ITEM_ANCHORS:
            test.assertNotIn(a["ref"], gone)
    test.assertFalse(set(ev["in_edition"]) & gone)
    roster = {r["institution"] for r in ev["silence"]}
    test.assertFalse(roster & set(ev["institutions"]), "a credited voice is never in its own silence roster")


class Takedowns(unittest.TestCase):
    def _rules(self, kind, value):
        return takedown.Rules([{"id": "td-x", "kind": kind, "value": value, "requested_at": "2026-09-20",
                                "by": "Delta Quotidien", "status": "active"}])

    def _check_withdrawn(self, rules, gone):
        stores, _, _ = build_all(EDITIONS[:1])
        store, view, ops = events.build(stores[0], E2, registry(rules))
        fire = by_id(store)[FIRE_ID]
        rows = {m["item_id"]: m for m in fire["members"]}
        for i in gone:
            self.assertIn(i, rows, "the member row stands: the event's counts stand")
            self.assertNotIn("url", rows[i], "R10: no URL, the same run")
        self.assertEqual(fire["withdrawn"], sorted(gone))
        vfire = by_id(view)[FIRE_ID]
        shown = {m["item_id"] for m in vfire["members"]}
        self.assertFalse(shown & set(gone), "R10: no display, the same run")
        self.assertEqual(vfire["member_count"], len(fire["members"]) - len(gone), "members present only")
        self.assertEqual((vfire["withdrawn"], vfire["withdrawn_count"]), (sorted(gone), len(gone)))
        self.assertEqual(vfire["event_id"], FIRE_ID)
        assert_never_credited(self, vfire, set(gone))
        # A later edition without the article: still withdrawn (by id or domain).
        store3, view3, _ = events.build(json.loads(json.dumps(store)), E3, registry(rules))
        self.assertEqual(by_id(store3)[FIRE_ID]["withdrawn"], sorted(gone))
        self.assertFalse({m["item_id"] for m in by_id(view3)[FIRE_ID]["members"]} & set(gone))
        assert_never_credited(self, by_id(view3)[FIRE_ID], set(gone))
        return store, view

    def test_an_article_takedown(self):
        _, view = self._check_withdrawn(self._rules("url", takedown._canon(F2["url"])), [F2["id"]])
        # Delta's edited URL (another article) is still present: the voice stays credited.
        self.assertIn("delta", by_id(view)[FIRE_ID]["institutions"])

    def test_a_source_takedown(self):
        # The edited URL arrives after the request: it never joins anything.
        _, view = self._check_withdrawn(self._rules("source", "delta"), [F2["id"]])
        fire = by_id(view)[FIRE_ID]
        self.assertNotIn("delta", fire["institutions"])
        row = next(r for r in fire["silence"] if r["institution"] == "delta")
        self.assertEqual((row["state"], row["scope"]), ("withdrawn", "institution"))

    def test_a_domain_takedown_still_matches_after_the_url_is_gone(self):
        self._check_withdrawn(self._rules("host", "delta.example.org"), [F2["id"]])

    def test_an_article_takedown_of_a_voice_with_no_other_member_says_withdrawn_not_silent(self):
        stores, _, _ = build_all(EDITIONS[:1])
        rules = self._rules("url", takedown._canon(F4["url"]))
        store, view, _ = events.build(stores[0], E2, registry(rules))
        fire = by_id(view)[FIRE_ID]
        self.assertNotIn("gamma-en", fire["institutions"])
        row = next(r for r in fire["silence"] if r["institution"] == "gamma-en")
        self.assertEqual((row["state"], row["scope"]), ("withdrawn", "article"),
                         "its article here was withdrawn: never silent, never 'no linked item'")
        self.assertEqual(fire["languages"], ["fr"])
        self.assertFalse([p for p in fire["language_pairs"] if p["en"] == F4["id"]])
        # The store keeps the sticky pair ids (a revoked request would bring them back).
        self.assertTrue([p for p in by_id(store)[FIRE_ID]["language_pairs"] if p["en"] == F4["id"]])
        assert_never_credited(self, fire, {F4["id"]})

    def test_with_the_takedown_file_through_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            files = Files(tmp)
            with mock.patch("sys.stdout"):
                files.run(E1)
                files.takedowns.write_text(
                    "version: 1\ntakedowns:\n  - id: td-2026-09-20-a\n    kind: url\n"
                    f"    value: {F2['url']}\n    requested_at: 2026-09-20\n    by: Delta Quotidien\n    status: active\n",
                    encoding="utf-8", newline="\n")
                files.run(E2)
            store = json.loads((tmp / "data" / "events" / "store.json").read_text(encoding="utf-8"))
            latest = (tmp / "data" / "events" / "latest_events.json").read_text(encoding="utf-8")
            fire = by_id(store)[FIRE_ID]
            self.assertEqual(fire["withdrawn"], [F2["id"]])
            self.assertNotIn(F2["url"], (tmp / "data" / "events" / "store.json").read_text(encoding="utf-8"))
            self.assertNotIn(F2["url"], latest)
            self.assertNotIn(F2["id"], [m["item_id"] for m in by_id(json.loads(latest))[FIRE_ID]["members"]])


# --------------------------------------------------------------------------- #
# Files, determinism, fail-soft
# --------------------------------------------------------------------------- #
class Files:
    """A throwaway repo layout: sources.yaml, takedowns.yaml, data/."""

    def __init__(self, root: Path, records=SOURCES):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)
        self.data = root / "data"
        self.sources = root / "sources.yaml"
        self.takedowns = root / "takedowns.yaml"
        self.enriched = self.data / "normalized" / "latest_enriched.json"
        self.sources.write_text(sources_yaml(records), encoding="utf-8", newline="\n")
        self.takedowns.write_text("version: 1\ntakedowns: []\n", encoding="utf-8", newline="\n")
        self.enriched.parent.mkdir(parents=True, exist_ok=True)

    def run(self, p) -> int:
        self.enriched.write_text(json.dumps(p, ensure_ascii=False), encoding="utf-8")
        return events.main(["--in", str(self.enriched), "--data-dir", str(self.data), "--sources", str(self.sources),
                            "--takedowns", str(self.takedowns)])

    def outputs(self) -> dict[str, bytes]:
        names = ("events/store.json", "events/latest_events.json", "ops/events_shadow.json")
        return {n: (self.data / n).read_bytes() for n in names if (self.data / n).exists()}


def stamp(clock: str) -> str:
    return datetime.fromisoformat(clock).strftime("%Y%m%dT%H%M%SZ")


class Determinism(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vigie-events-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _sequential(self, name) -> Files:
        files = Files(self.tmp / name)
        for _, p in EDITIONS:
            with mock.patch("sys.stdout"):
                self.assertEqual(files.run(p), 0)
        return files

    def test_byte_identical_rebuild(self):
        a, b = self._sequential("a").outputs(), self._sequential("b").outputs()
        self.assertEqual(len(a), 3)
        self.assertEqual(a, b)

    def test_rerunning_the_same_edition_changes_nothing(self):
        files = Files(self.tmp / "same")
        with mock.patch("sys.stdout"):
            files.run(E1)
            first = files.outputs()
            files.run(E1)
        self.assertEqual(files.outputs(), first)

    def test_replay_from_empty_reproduces_the_live_store(self):
        live = self._sequential("live")
        snaps = self.tmp / "snapshots"
        snaps.mkdir()
        for _, p in EDITIONS:
            (snaps / f"{stamp(p['normalized_at'])}_candidates.json").write_text(json.dumps(p), encoding="utf-8")
        (snaps / "latest_candidates.json").write_text(json.dumps(E1), encoding="utf-8")  # not a stamped snapshot
        out = Files(self.tmp / "replayed")
        with mock.patch("sys.stdout"):
            code = events.main(["--replay", str(snaps), "--data-dir", str(out.data), "--sources", str(out.sources),
                                "--takedowns", str(out.takedowns)])
        self.assertEqual(code, 0)
        self.assertEqual(out.outputs(), live.outputs())
        # A replay never replaces a store that already carries minted ids.
        partial = self.tmp / "partial"
        partial.mkdir()
        (partial / f"{stamp(E1['normalized_at'])}_candidates.json").write_text(json.dumps(E1), encoding="utf-8")
        before = out.outputs()
        with mock.patch("sys.stdout"):
            events.main(["--replay", str(partial), "--data-dir", str(out.data), "--sources", str(out.sources),
                         "--takedowns", str(out.takedowns)])
        self.assertEqual(out.outputs(), before)

    def test_identical_under_two_hash_seeds(self):
        files = Files(self.tmp / "seed")
        snaps = self.tmp / "seed-snaps"
        snaps.mkdir()
        for _, p in EDITIONS:
            (snaps / f"{stamp(p['normalized_at'])}_candidates.json").write_text(json.dumps(p), encoding="utf-8")
        digests = []
        for seed in ("0", "4242"):
            out = self.tmp / f"seed-{seed}"
            env = dict(os.environ, PYTHONHASHSEED=seed, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
            proc = subprocess.run([sys.executable, "-X", "utf8", str(ROOT / "scripts" / "events.py"), "--replay",
                                   str(snaps), "--data-dir", str(out), "--sources", str(files.sources),
                                   "--takedowns", str(files.takedowns)],
                                  capture_output=True, text=True, encoding="utf-8", env=env, timeout=180)
            self.assertEqual(proc.returncode, 0, proc.stderr)
            blob = b"".join((out / n).read_bytes() for n in ("events/store.json", "events/latest_events.json",
                                                             "ops/events_shadow.json"))
            digests.append(hashlib.sha256(blob).hexdigest())
        self.assertEqual(digests[0], digests[1])

    def test_no_wall_clock_and_nothing_under_public(self):
        source = (ROOT / "scripts" / "events.py").read_text(encoding="utf-8")
        self.assertIsNone(re.search(r"datetime\.now|time\.time\(|date\.today|utcnow", source))
        self.assertNotIn('"public"', source)
        self.assertNotIn("public/", source.split('"""', 2)[2], "no output path under public/")

    def test_outputs_are_sorted_json_without_floats(self):
        files = self._sequential("fmt")
        for name, blob in files.outputs().items():
            doc = json.loads(blob.decode("utf-8"))

            def walk(x, path=name):
                if isinstance(x, float):
                    self.fail(f"a float at {path}")
                if isinstance(x, dict):
                    self.assertEqual(list(x), sorted(x), path)
                    for k, v in x.items():
                        walk(v, f"{path}.{k}")
                if isinstance(x, list):
                    for v in x:
                        walk(v, path)
            walk(doc)


class NoPublisherText(unittest.TestCase):
    def test_store_view_and_ops_carry_no_title_excerpt_or_byline(self):
        with tempfile.TemporaryDirectory() as tmp:
            files = Files(Path(tmp))
            with mock.patch("sys.stdout"):
                for _, p in EDITIONS:
                    files.run(p)
            for name, blob in files.outputs().items():
                text = blob.decode("utf-8")
                for needle in TEXTS:
                    self.assertNotIn(needle, text, f"{name} carries publisher text")
                for word in ("Pétronille", "Quillon", "Ostrander", "zorblaxiennes", "Lanternes"):
                    self.assertNotIn(word, text, name)

                def keys(x):
                    if isinstance(x, dict):
                        for k, v in x.items():
                            yield k
                            yield from keys(v)
                    elif isinstance(x, list):
                        for v in x:
                            yield from keys(v)
                found = set(keys(json.loads(text)))
                self.assertFalse(found & {"title", "summary", "excerpt", "quote", "author", "claims", "evidence"},
                                 name)


class FailSoft(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="vigie-events-soft-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.files = Files(self.tmp)
        self.store = self.files.data / "events" / "store.json"

    def _run(self, p) -> int:
        with mock.patch("sys.stdout"):
            return self.files.run(p)

    def test_absent_store_is_a_first_run(self):
        self.assertEqual(self._run(E1), 0)
        self.assertTrue(self.store.exists())

    def test_corrupt_store_yields_no_events_and_is_left_for_repair(self):
        for garbage in ("{not json", json.dumps({"format": "something-else"}), json.dumps([1, 2]),
                        json.dumps({**events.empty_store(), "events": [{"event_id": "ev-bad"}]})):
            self.store.parent.mkdir(parents=True, exist_ok=True)
            self.store.write_text(garbage, encoding="utf-8")
            self.assertEqual(self._run(E1), 0)
            self.assertEqual(self.store.read_text(encoding="utf-8"), garbage, "the store is never overwritten")
            latest = json.loads((self.files.data / "events" / "latest_events.json").read_text(encoding="utf-8"))
            self.assertEqual((latest["status"], latest["events"]), ("store_unreadable", []))
            ops = json.loads((self.files.data / "ops" / "events_shadow.json").read_text(encoding="utf-8"))
            self.assertEqual(ops["status"], "store_unreadable")
            self.assertTrue(ops["diagnosis"])

    # Damage the builder would otherwise trip over mid-build (KeyError, ValueError,
    # AttributeError), leaving the previous edition's outputs in place.
    DAMAGE = {
        "member without institution": lambda d: _strike_row(d).pop("institution"),
        "member language None": lambda d: _strike_row(d).update(language=None),
        "member without language": lambda d: _strike_row(d).pop("language"),
        "member language de": lambda d: _strike_row(d).update(language="de"),
        "member without source_id": lambda d: _strike_row(d).update(source_id=""),
        "member first_seen garbage": lambda d: _strike_row(d).update(first_seen="hier"),
        "bare member row": lambda d: _strike_row(d).pop("origin_class"),
        "member url not a string": lambda d: _strike_row(d).update(url=["x"]),
        "lineage a string": lambda d: by_id(d)[STRIKE_ID].update(lineage="merged"),
        "lineage absorbed a string": lambda d: by_id(d)[STRIKE_ID].update(lineage={"absorbed": "ev-x"}),
        "item codes a string": lambda d: d["items"].update({S1["id"]: "codes"}),
        "item type scores a list": lambda d: d["items"][S1["id"]].update(type_scores=["strike"]),
        "item places an int": lambda d: d["items"][S1["id"]].update(places=3),
        "item join a list": lambda d: d["items"][S1["id"]].update(join=["minted"]),
        "item facts not atoms": lambda d: d["items"][S1["id"]].update(facts=[1]),
        "facts rows not objects": lambda d: by_id(d)[STRIKE_ID].update(facts=["x"]),
        "language pair a string": lambda d: by_id(d)[STRIKE_ID].update(language_pairs=["fr-en"]),
        "facts_state unknown": lambda d: by_id(d)[STRIKE_ID].update(facts_state="partial"),
    }

    def test_every_damage_the_builder_would_trip_on_is_an_unreadable_store(self):
        stores, _, _ = build_all(EDITIONS[:1])
        events.check_store(json.loads(json.dumps(stores[0])))
        for name, damage in self.DAMAGE.items():
            doc = json.loads(json.dumps(stores[0]))
            damage(doc)
            with self.assertRaises(events.StoreUnreadable, msg=name):
                events.check_store(doc)
            with self.assertRaises(events.StoreUnreadable, msg=name):
                events.build(doc, E2, registry())

    def _takedown(self, kind="url", value=None):
        value = value if value is not None else F2["url"]
        self.files.takedowns.write_text(
            "version: 1\ntakedowns:\n  - id: td-2026-09-20-b\n"
            f"    kind: {kind}\n    value: {value}\n    requested_at: 2026-09-20\n    by: Delta Quotidien\n"
            "    status: active\n", encoding="utf-8", newline="\n")

    def _outputs(self):
        latest = json.loads((self.files.data / "events" / "latest_events.json").read_text(encoding="utf-8"))
        ops = json.loads((self.files.data / "ops" / "events_shadow.json").read_text(encoding="utf-8"))
        return latest, ops

    def _assert_cleared(self, status, edition):
        latest, ops = self._outputs()
        self.assertEqual((latest["status"], latest["edition"], latest["events"]), (status, edition, []),
                         "the previous edition is never left in place marked ok")
        self.assertEqual((ops["status"], ops["edition"]), (status, edition))
        self.assertTrue(ops["diagnosis"])
        return ops

    def test_a_damaged_store_clears_the_view_and_still_withdraws_the_url(self):
        self.assertEqual(self._run(E1), 0)
        doc = json.loads(self.store.read_text(encoding="utf-8"))
        self.assertIn(F2["url"], self.store.read_text(encoding="utf-8"))
        _strike_row(doc).pop("institution")       # an unrelated row: check_store used to accept it
        _strike_row(doc)["language"] = None
        self.store.write_text(json.dumps(doc), encoding="utf-8")
        self._takedown()
        self.assertEqual(self._run(E2), 0)
        ops = self._assert_cleared("store_unreadable", E2["normalized_at"])
        self.assertEqual(ops["r10_store_scrub"]["urls_removed"], 1)
        after = json.loads(self.store.read_text(encoding="utf-8"))
        for e in doc["events"]:
            for m in e["members"]:
                if m["item_id"] == F2["id"]:
                    m.pop("url")
        self.assertEqual(after, doc, "only the withdrawn URL left; the rest is kept for repair")
        self.assertNotIn(F2["url"], self.store.read_text(encoding="utf-8"))
        self.assertIn(F1["url"], self.store.read_text(encoding="utf-8"))

    def test_a_fault_while_building_clears_the_view_and_still_withdraws_the_url(self):
        self.assertEqual(self._run(E1), 0)
        self._takedown()
        with mock.patch.object(events, "build", side_effect=KeyError("institution")):
            self.assertEqual(self._run(E2), 0)
        ops = self._assert_cleared("build_failed", E2["normalized_at"])
        self.assertIn("KeyError", ops["diagnosis"][0])
        text = self.store.read_text(encoding="utf-8")
        self.assertNotIn(F2["url"], text)
        self.assertIn(F1["url"], text)
        self.assertEqual(ops["r10_store_scrub"]["urls_removed"], 1)

    def test_an_unreadable_sources_file_clears_the_view_and_still_withdraws_by_source(self):
        self.assertEqual(self._run(E1), 0)
        self._takedown("source", "delta")
        self.files.sources.write_text("version: 1\n", encoding="utf-8")   # no sources: block -> SystemExit
        self.assertEqual(self._run(E2), 0)
        ops = self._assert_cleared("build_failed", E2["normalized_at"])
        self.assertIn("SystemExit", ops["diagnosis"][0])
        text = self.store.read_text(encoding="utf-8")
        self.assertNotIn(F2["url"], text)
        self.assertNotIn(X1["url"], text, "every Delta URL goes, by source id alone")
        self.assertIn(F1["url"], text)

    def test_a_refused_edition_still_withdraws_the_url(self):
        self.assertEqual(self._run(E2), 0)
        self._takedown()
        self.assertEqual(self._run(E1), 0)
        self._assert_cleared("edition_refused", E1["normalized_at"])
        self.assertNotIn(F2["url"], self.store.read_text(encoding="utf-8"))

    def test_no_readable_edition_clears_the_view(self):
        self.assertEqual(self._run(E1), 0)
        before = self.store.read_bytes()
        self.files.enriched.write_text("garbage", encoding="utf-8")
        with mock.patch("sys.stdout"):
            self.assertEqual(events.main(["--in", str(self.files.enriched), "--data-dir", str(self.files.data),
                                          "--sources", str(self.files.sources), "--takedowns",
                                          str(self.files.takedowns)]), 0)
        self._assert_cleared("no_edition", None)
        self.assertEqual(self.store.read_bytes(), before, "nothing withdrawn: the store is untouched")

    def test_a_store_that_does_not_parse_is_scrubbed_row_by_row(self):
        self.assertEqual(self._run(E1), 0)
        text = self.store.read_text(encoding="utf-8")
        row = next(m for e in json.loads(text)["events"] for m in e["members"] if m["item_id"] == F2["id"])
        flat = json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        self.assertIn(flat, text)
        broken = text[:-40] + ',"url":"' + F2["url"]            # truncated, a dangling URL at the end
        self.store.write_text(broken, encoding="utf-8")
        self._takedown()
        self.assertEqual(self._run(E2), 0)
        self._assert_cleared("store_unreadable", E2["normalized_at"])
        after = self.store.read_text(encoding="utf-8")
        self.assertNotIn(F2["url"], after)
        row.pop("url")
        expected = broken.replace(flat, json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        self.assertEqual(after, expected[:-len(F2["url"])], "nothing else in the damaged file changes")

    def test_an_item_owned_twice_is_a_corrupt_store(self):
        stores, _, _ = build_all(EDITIONS[:1])
        doc = json.loads(json.dumps(stores[0]))
        doc["events"][1]["members"].append(dict(doc["events"][0]["members"][0]))
        with self.assertRaises(events.StoreUnreadable):
            events.check_store(doc)

    def test_an_older_edition_is_refused_and_the_store_is_unchanged(self):
        self._run(E2)
        before = self.store.read_bytes()
        self.assertEqual(self._run(E1), 0)
        self.assertEqual(self.store.read_bytes(), before)
        latest = json.loads((self.files.data / "events" / "latest_events.json").read_text(encoding="utf-8"))
        self.assertEqual(latest["status"], "edition_refused")

    def test_unusable_inputs_exit_zero(self):
        self.assertEqual(self._run({"candidates": []}), 0, "no clock")
        self.assertFalse(self.store.exists())
        self.files.enriched.write_text("garbage", encoding="utf-8")
        with mock.patch("sys.stdout"):
            self.assertEqual(events.main(["--in", str(self.files.enriched), "--data-dir", str(self.files.data)]), 0)
            self.assertEqual(events.main(["--in", str(self.tmp / "absent.json"), "--data-dir", str(self.files.data)]), 0)
            self.assertEqual(events.main(["--sources", str(self.tmp / "absent.yaml"), "--in", str(self.files.enriched),
                                          "--data-dir", str(self.files.data)]), 0)
            with mock.patch("sys.stderr"):
                self.assertEqual(events.main(["--bogus-flag"]), 0)
        hostile = {"normalized_at": "2026-09-20T12:00:00+00:00",
                   "candidates": [F1, "not an item", {"id": "XYZ"}, {**F2, "published_at": "9999-12-31T23:59:59Z"}]}
        self.assertEqual(self._run(hostile), 0)
        ops = json.loads((self.files.data / "ops" / "events_shadow.json").read_text(encoding="utf-8"))
        self.assertEqual(ops["items"]["excluded"], {"malformed_candidate": 1, "malformed_id": 1})

    def test_a_classifier_fault_on_one_item_costs_only_its_codes(self):
        with mock.patch.object(facts, "extract_item", side_effect=ValueError("bad item")):
            store, _, ops = events.build(None, E1, registry())
        self.assertEqual(owner_map(store)[F1["id"]], FIRE_ID, "the edition's events are still built")
        self.assertEqual(store["items"][F1["id"]]["facts"], [])
        self.assertTrue(any("facts fault" in d for d in ops["diagnosis"]))

    def test_a_fault_inside_the_builder_exits_zero(self):
        with mock.patch.object(events, "build", side_effect=RuntimeError("boom")), mock.patch("sys.stdout"):
            self.assertEqual(self.files.run(E1), 0)
        self._assert_cleared("build_failed", E1["normalized_at"])
        self.assertFalse(self.store.exists(), "no store is invented")

    def test_a_failed_edition_in_a_replay_leaves_the_store_as_a_live_run_does(self):
        real_build = events.build

        def flaky(previous, payload, reg, **kw):
            if payload.get("normalized_at") == E2["normalized_at"]:
                raise ValueError("damaged edition")
            return real_build(previous, payload, reg, **kw)

        snaps = self.tmp / "snaps"
        snaps.mkdir()
        for _, p in EDITIONS[:3]:
            (snaps / f"{stamp(p['normalized_at'])}_candidates.json").write_text(json.dumps(p), encoding="utf-8")
        out = Files(self.tmp / "replayed")
        with mock.patch.object(events, "build", side_effect=flaky), mock.patch("sys.stdout"):
            for _, p in EDITIONS[:3]:
                self.files.run(p)
            events.main(["--replay", str(snaps), "--data-dir", str(out.data), "--sources", str(out.sources),
                         "--takedowns", str(out.takedowns)])
        self.assertEqual(out.outputs()["events/store.json"], self.files.outputs()["events/store.json"])
        self.assertNotIn(E2["normalized_at"], json.loads(self.store.read_text(encoding="utf-8"))["editions"])


# --------------------------------------------------------------------------- #
# Wiring
# --------------------------------------------------------------------------- #
class Wiring(unittest.TestCase):
    def test_events_run_right_after_the_dossiers_in_full_and_offline_never_render_only(self):
        import pipeline
        s = pipeline.SCRIPTS
        self.assertEqual(s.index("events.py"), s.index("cluster_issues.py") + 1)
        self.assertNotIn("events.py", pipeline.RENDER_ONLY)
        self.assertNotIn("events.py", pipeline.OFFLINE_SKIP)
        self.assertIn("events.py", pipeline.SHADOW)
        with mock.patch.object(pipeline, "run") as run, mock.patch("sys.argv", ["pipeline.py", "--render-only"]),                 mock.patch("sys.stdout"):
            pipeline.main()
        self.assertNotIn("events.py", [c.args[0] for c in run.call_args_list])
        with mock.patch.object(pipeline, "run") as run, mock.patch("sys.argv", ["pipeline.py", "--offline"]),                 mock.patch("sys.stdout"):
            pipeline.main()
        self.assertIn("events.py", [c.args[0] for c in run.call_args_list])

    def test_a_failing_shadow_step_never_stops_the_edition(self):
        import pipeline
        with mock.patch.object(pipeline.subprocess, "run", return_value=mock.Mock(returncode=3)), \
                mock.patch("sys.stdout"):
            pipeline.run("events.py")
            with self.assertRaises(SystemExit):
                pipeline.run("cluster_issues.py")

    def test_the_state_tarball_carries_the_event_store(self):
        import state_pack
        self.assertIn("events/store.json", state_pack.EXPLICIT)
        self.assertIn("events/latest_events.json", state_pack.EXPLICIT)


# --------------------------------------------------------------------------- #
# Lexicon fixes found by reading the replayed events. Every headline below is
# written from scratch for the test (invented places, people and events): only
# the lexicon terms under test are shared with the lexicon.
# --------------------------------------------------------------------------- #
class LexiconFixes(unittest.TestCase):
    def test_a_ceremony_for_firefighters_is_not_a_building_fire(self):
        ceremony = ["Un pompier de Zorblaxville décoré pour un sauvetage dans un incendie "
                    "lors d'une journée nationale de reconnaissance",
                    "Gala de reconnaissance des pompiers volontaires de la Zorblaxie : "
                    "trois caporaux décorés pour leur courage face aux flammes"]
        self.assertNotEqual(vocabulaire.classify_type(ceremony)[0], "fire-building")
        # The ceremony phrase is what blocks the type: without it, the same words are a fire.
        bare = [t.replace(" lors d'une journée nationale de reconnaissance", "").replace("de reconnaissance ", "")
                for t in ceremony]
        for title in bare:
            self.assertEqual(vocabulaire.classify_type([title])[0], "fire-building", title)
        self.assertEqual(vocabulaire.classify_type(["Un triplex de la rue Zorblax ravagé par un incendie"])[0],
                         "fire-building")

    def test_a_vehicle_ramming_pedestrians_at_a_festival_is_pedestrians_struck(self):
        titles = ["Zorblaxfest : une camionnette percute trois piétons près de la grande scène",
                  "Trois piétons percutés en marge du festival Zorblax",
                  "Festival Zorblax : les enquêteurs n'écartent pas l'hypothèse d'une voiture-bélier",
                  "Festival Zorblax : quatre spectateurs blessés, la soirée de clôture annulée",
                  "Une conductrice fonce dans la foule à la sortie du festival Zorblax",
                  "Message de solidarité des élus aux blessés du festival Zorblax"]
        self.assertEqual(vocabulaire.classify_type(titles)[0], "pedestrian-cyclist-struck")
        for harmed in titles[2:]:
            self.assertNotIn("festival-event", [r["type"] for r in vocabulaire.explain_type([harmed])], harmed)
        self.assertEqual(vocabulaire.classify_type(["Festival Zorblax : quatre spectateurs, la soirée de clôture annulée"])[0],
                         "festival-event", "the harm word is what blocks the festival type")
        self.assertEqual(vocabulaire.classify_type(["Police say a van rammed pedestrians near the Zorblax festival grounds"])[0],
                         "pedestrian-cyclist-struck")
        self.assertEqual(vocabulaire.classify_type(["Coup d'envoi du festival Zorblax ce soir au parc des Zorblaxiens"])[0],
                         "festival-event")

    def test_rape_is_an_assault_not_a_campus_story(self):
        titles = ["Plainte pour viol collectif : l'Université de Zorblax suspend deux étudiants",
                  "Zorblax : un étudiant accusé de viol sur le campus universitaire",
                  "Gang rape reported at a Zorblax University residence"]
        self.assertEqual(vocabulaire.classify_type(titles)[0], "assault")
        for word_boundary in ("Le conseil de Zorblaxville blâmé pour une violation de son code d'éthique",
                              "Hockey mineur à Zorblax : la violence dans les gradins inquiète les arbitres"):
            self.assertNotEqual(vocabulaire.classify_type([word_boundary])[0], "assault", word_boundary)
        self.assertEqual(vocabulaire.classify_type(["L'Université de Zorblax ouvre un nouveau programme de doctorat"])[0],
                         "higher-education")


# --------------------------------------------------------------------------- #
# Tranche C: places by plurality, the window, independence, R10 at render time
# (every headline, byline and outlet below is invented)
# --------------------------------------------------------------------------- #
class PlacesByPlurality(unittest.TestCase):
    def test_one_member_saying_quebec_city_cannot_relabel_an_event_placed_elsewhere(self):
        items = {"a": {"places": [], "geo": "linked", "geo_place": "elsewhere"},
                 "b": {"places": ["elsewhere"], "geo": "linked", "geo_place": ""},
                 "c": {"places": [], "geo": "linked", "geo_place": "elsewhere"},
                 "d": {"places": [], "geo": "quebec-city", "geo_place": "quebec-city"}}
        places, basis = events.event_places(sorted(items), items)
        self.assertEqual(places, ["elsewhere", "quebec-city"])
        self.assertEqual(basis, "named", "one of the winner's votes is named by a headline")
        self.assertEqual(events.place_votes(sorted(items), items), {"elsewhere": [3, 1], "quebec-city": [1, 0]})
        # Two members only, one each: a tie, broken by the named vote, then specificity.
        two = {"x": {"places": ["limoilou"], "geo": "linked"}, "y": {"places": [], "geo": "quebec-city"}}
        self.assertEqual(events.event_places(sorted(two), two), (["limoilou", "quebec-city"], "named"))
        geo_tie = {"x": {"places": [], "geo_place": "elsewhere"}, "y": {"places": [], "geo_place": "quebec-city"}}
        self.assertEqual(events.event_places(sorted(geo_tie), geo_tie)[0][0], "quebec-city",
                         "equal geo votes: the published tie-break (specificity, then table order)")
        specific = {"x": {"places": ["beauport"]}, "y": {"places": ["limoilou"]}}
        self.assertEqual(events.event_places(sorted(specific), specific)[0][0], "limoilou", "a quartier is more specific")

    def test_no_evidence_is_unplaced_never_the_city(self):
        for entry in ({"places": [], "geo": "linked"}, {"places": [], "geo": "unknown"},
                      {"places": [], "geo": "linked", "geo_place": ""}, {}):
            self.assertEqual(events.event_places(["i"], {"i": entry}), (["unplaced"], "fallback"), entry)
        self.assertEqual(vocabulaire.FALLBACK_PLACE, "unplaced")
        self.assertEqual(vocabulaire.place_label("unplaced", "fr"), "Lieu non établi")
        self.assertEqual(events.event_label("fire-building", ["unplaced"], "fallback")["fr"],
                         vocabulaire.type_label("fire-building", "fr"), "the label names no place")
        # Items stored before `geo_place` existed: the plain geo mapping, where linked supports nothing.
        self.assertEqual(events.event_places(["i"], {"i": {"places": [], "geo": "quebec"}}), (["province"], "geo"))
        self.assertEqual(events.event_places(["i"], {"i": {"places": [], "geo": "quebec-city"}}),
                         (["quebec-city"], "geo"))

    def test_geo_evidence_of_one_item(self):
        world = {"title": "La Lituanie ferme le festival Zorblax", "summary": ""}
        self.assertEqual(events.item_geo_place(world, "linked"), "elsewhere", "enrich's world-fog branch")
        local_but_untagged = {"title": "Nouveaux bancs publics sur la rue Zorblax", "summary": ""}
        self.assertEqual(events.item_geo_place(local_but_untagged, "linked"), "", "no evidence: an absence")
        self.assertEqual(events.item_geo_place(world, "quebec-city"), "quebec-city")
        self.assertEqual(events.item_geo_place(world, "quebec"), "province")
        self.assertEqual(events.event_geo(["a", "b", "c"], {"a": {"geo": "quebec"}, "b": {"geo": "quebec"},
                                                            "c": {"geo": "quebec-city"}}), "quebec")
        self.assertEqual(events.event_geo(["a", "b"], {"a": {"geo": "quebec"}, "b": {"geo": "quebec-city"}}),
                         "quebec-city", "a tie goes to the more local")
        self.assertEqual(events.event_geo(["a"], {"a": {"geo": "linked"}}), "linked")

    def test_a_built_event_placed_elsewhere_by_its_members(self):
        def foreign(name, source, title, hour):
            it = item(name, source, title, iso(20, hour))
            it["enrich"] = {"geo": {"geo": "linked"}}
            return it

        a = foreign("pl-a", "delta", "Trump inaugure le festival Zorblax", 8)
        b = foreign("pl-b", "alpha-qc", "Festival Zorblax : Trump salue la foule", 9)
        c = foreign("pl-c", "gamma-fr", "Le festival Zorblax et Trump font salle comble", 10)
        d = item("pl-d", "beta-qc", "Festival Zorblax : des gens de Québec sur place", iso(20, 11))
        d["enrich"] = {"geo": {"geo": "quebec-city"}}
        table = {(x["id"], y["id"]): 0.95 for x in (a, b, c, d) for y in (a, b, c, d) if x["id"] < y["id"]}
        with mock.patch.object(em, "pair_tier", _Table(table)):
            store, view, ops = events.build(None, payload("2026-09-20T12:00:00+00:00", [a, b, c, d]), registry())
        self.assertEqual(len(view["events"]), 1)
        ev = view["events"][0]
        self.assertEqual(ev["places"][0], "elsewhere")
        self.assertEqual(ev["place_votes"]["elsewhere"]["votes"], 3)
        self.assertEqual(ev["place_basis"], "geo")
        self.assertEqual(ev["label"]["fr"].split(" · ")[-1], "Hors Québec")
        self.assertEqual(ops["edition_view"]["first_place"], {"elsewhere": 1})


class SuspectDates(unittest.TestCase):
    """A publication date more than 6 h after the first collection is kept as
    given but never dates the member: the first collection does."""

    E1 = "2026-09-20T12:00:00+00:00"
    E2 = "2026-09-28T12:00:00+00:00"

    def test_a_future_date_never_holds_an_event_in_window_nor_its_facts_live(self):
        future = item("fut", "delta", "Incendie Zorblax à Beauport : 3 blessés", "2026-09-26T12:00:00+00:00",
                      "Trois personnes blessées dans l'incendie Zorblax.")
        later = item("fut-2", "alpha-qc", "Beauport : l'incendie Zorblax, le bilan", "2026-09-28T09:00:00+00:00")
        table = {tuple(sorted((future["id"], later["id"]))): 0.95}
        with mock.patch.object(em, "pair_tier", _Table(table)):
            s1, _, _ = events.build(None, payload(self.E1, [future]), registry())
            row = s1["events"][0]["members"][0]
            self.assertEqual((row["published_at"], row["date_suspect"]), ("2026-09-26T12:00:00+00:00", True),
                             "kept as given, flagged, never corrected")
            s2, view, _ = events.build(json.loads(json.dumps(s1)), payload(self.E2, [future, later]), registry())
        fut_event = by_id(s2)[em.event_id_for(future["id"])]
        self.assertEqual(fut_event["window_state"], "out_of_window", "dated by its first collection: 8 days ago")
        self.assertEqual(fut_event["facts_state"], "reduced")
        self.assertEqual(fut_event["members"][0]["published_at"], "2026-09-26T12:00:00+00:00", "the store keeps the date")
        # The matcher dates it the same way: the newcomer, two days after the
        # suspect date but eight after the first collection, founds its own event.
        self.assertEqual(owner_map(s2)[later["id"]], em.event_id_for(later["id"]))

    def test_member_instant(self):
        row = {"published_at": "2026-09-26T12:00:00+00:00", "first_seen": self.E1, "date_suspect": True}
        self.assertEqual(events._member_instant(row).isoformat(), self.E1)
        row["date_suspect"] = False
        self.assertEqual(events._member_instant(row).isoformat(), "2026-09-26T12:00:00+00:00")
        self.assertEqual(events._matcher_item({"published_at": "2026-09-26T12:00:00Z"}, self.E1)["published_at"], None)
        kept = {"published_at": "2026-09-20T10:00:00Z"}
        self.assertIs(events._matcher_item(kept, self.E1), kept)


def irow(i, inst, group, cls="own_reporting", rule="own_reporting.named_byline"):
    return {"item_id": i, "institution": inst, "source_id": inst, "owner_group": group, "language": "fr",
            "origin_class": cls, "origin_rule": rule}


class IndependenceRules(unittest.TestCase):
    def test_declarations_never_count_as_reporting_and_one_owner_declares_once(self):
        rows = {"a": irow("a", "alpha", "quebecor"), "d": irow("d", "delta", "delta"),
                "g": irow("g", "ville-x", "ville-x", "official", "official.source_kind"),
                "h": irow("h", "hydro-x", "etat-x", "official", "official.source_kind"),
                "k": irow("k", "gouv-x", "etat-x", "official", "official.source_kind")}
        out = events.independence(sorted(rows), rows)
        self.assertEqual(out["declarations"], ["g", "h", "k"])
        self.assertEqual(out["reporting_count"], 2)
        self.assertEqual(out["count"], 4)
        state = next(o for o in out["origins"] if o["members"] == ["h", "k"])
        self.assertEqual((state["kind"], state["reasons"]), ("declaration", [{"rule": "same-owner", "key": "etat-x"}]))
        # A media text that copies the communiqué joins the declaration: it adds no reporting origin.
        copied = events.independence(sorted(rows), rows, [["a", "g"]])
        self.assertEqual(copied["reporting_count"], 1)
        origin_ag = next(o for o in copied["origins"] if "a" in o["members"])
        self.assertEqual(origin_ag["kind"], "declaration")
        self.assertIn({"rule": "near-duplicate", "key": "shingles-3-of-5"}, origin_ag["reasons"])
        # A declaration never merges with media by owner: a medium of the same owner stays reporting.
        rows["m"] = irow("m", "media-x", "etat-x")
        mixed = events.independence(sorted(rows), rows)
        self.assertEqual(mixed["reporting_count"], 3)

    def test_a_credit_is_a_wire_only_when_seen_in_two_owner_groups(self):
        rows = {"q1": irow("q1", "alpha", "quebecor", "unknown", "unknown.newsroom_credit"),
                "q2": irow("q2", "beta", "quebecor", "unknown", "unknown.newsroom_credit"),
                "q3": irow("q3", "delta", "delta", "unknown", "unknown.newsroom_credit")}
        credits = {"q1": ["qmi"], "q2": ["qmi"], "q3": ["qmi"]}
        one_group = events.wire_credits([(rows["q1"], ["qmi"]), (rows["q2"], ["qmi"])])
        self.assertEqual(one_group["qmi"], {"items": 2, "owner_groups": 1, "wire": False, "basis": "measured"})
        two_groups = events.wire_credits([(rows[i], credits[i]) for i in sorted(rows)])
        self.assertEqual(two_groups["qmi"], {"items": 3, "owner_groups": 2, "wire": True, "basis": "measured"})
        inside = events.independence(sorted(rows), rows, credits=credits, wire=set())
        self.assertEqual(inside["groups"], [["q1", "q2"], ["q3"]], "inside one group it is that group's byline")
        across = events.independence(sorted(rows), rows, credits=credits, wire={"qmi"})
        self.assertEqual(across["groups"], [["q1", "q2", "q3"]])
        self.assertEqual(across["origins"][0]["reasons"],
                         [{"basis": "measured", "key": "qmi", "rule": "same-wire-credit"}])
        prior = events.wire_credits([(irow("w", "delta", "delta", "wire", "wire.author.cp"), ["cp"])])
        self.assertEqual(prior["cp"], {"items": 1, "owner_groups": 1, "wire": True, "basis": "prior"})
        self.assertFalse(hasattr(events, "AGENCE_QMI_IS_WIRE"), "no human flag decides it here")

    def test_credit_codes_read_the_byline_and_keep_codes_only(self):
        self.assertEqual(events.credit_codes({"author": "Agence QMI"}), ["qmi"])
        self.assertEqual(events.credit_codes({"author": "Odile Zorblax, La Presse Canadienne"}), ["cp"])
        self.assertEqual(events.credit_codes({"author": "Agence QMI et Agence France-Presse"}), ["afp", "qmi"])
        self.assertEqual(events.credit_codes({"author": "Odile Pétronille-Quartz"}), [])
        self.assertEqual(events.credit_codes({"author": None}), [])

    def test_a_measured_wire_in_a_build_merges_across_owners(self):
        a = item("qa", "alpha-qc", "Grève Zorblax : les chauffeurs débraient", iso(20, 8), author="Agence QMI")
        d = item("qd", "delta", "Grève Zorblax : débrayage des chauffeurs", iso(20, 9), author="Agence QMI")
        table = {tuple(sorted((a["id"], d["id"]))): 0.95}
        with mock.patch.object(em, "pair_tier", _Table(table)):
            store, view, ops = events.build(None, payload("2026-09-20T12:00:00+00:00", [a, d]), registry())
        ev = view["events"][0]
        self.assertEqual(ev["independence"]["groups"], [sorted([a["id"], d["id"]])])
        self.assertEqual(ev["reporting_origin_count"], 1)
        self.assertEqual(ops["credits"]["qmi"], {"items": 2, "owner_groups": 2, "wire": True, "basis": "measured"})
        self.assertNotIn("Agence QMI", json.dumps(store, ensure_ascii=False))
        self.assertEqual(store["items"][a["id"]]["credits"], ["qmi"])

    def test_every_merged_origin_says_why_and_the_view_publishes_its_bounds(self):
        _, views, _ = build_all(EDITIONS[:2])
        rules = set(events.ORIGIN_RULES.values())
        merged = 0
        for ev in views[1]["events"]:
            for o in ev["independence"]["origins"]:
                if len(o["members"]) > 1:
                    merged += 1
                    self.assertTrue(o["reasons"], o)
                    self.assertTrue({r["rule"] for r in o["reasons"]} <= rules)
                else:
                    self.assertEqual(o["reasons"], [])
            self.assertEqual(ev["reporting_origin_count"], ev["independence"]["reporting_count"])
        self.assertGreater(merged, 0)
        self.assertEqual(views[1]["rules"], events.INDEPENDENCE_RULES)
        self.assertEqual(views[1]["rules"]["near_duplicate"]["jaccard_at_least"], [3, 5])
        g = by_id(views[1]).get(em.event_id_for(G1["id"]))
        self.assertEqual((g["declared_by"], g["reporting_origin_count"]), (["ville-x"], 0))

    def test_hydro_quebec_and_the_government_declare_as_one_owner_in_the_real_registry(self):
        reg = events.Registry.load(ROOT / "sources.yaml", ROOT / "takedowns.yaml")
        rows = {}
        for i, sid, cls in (("h", "hydro-quebec", "official"), ("g", "gouv-quebec", "official"),
                            ("j", "journal-de-quebec", "own_reporting")):
            r = {"item_id": i, "source_id": sid, "institution": reg.institution_of(sid), "origin_class": cls,
                 "origin_rule": "x"}
            events._refresh_ownership(r, reg)
            rows[i] = r
        out = events.independence(sorted(rows), rows)
        self.assertEqual(out["groups"], [["g", "h"], ["j"]], "the sourced fact: the State is the sole shareholder")
        self.assertEqual(out["reporting_count"], 1, "and neither counts as reporting")


class ApplyTakedownsAtRenderTime(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.sources = Path(self.tmp.name) / "sources.yaml"
        self.sources.write_text(sources_yaml(), encoding="utf-8", newline="\n")
        self.stores, self.views, _ = build_all(EDITIONS[:2])

    def rules(self, kind, value):
        return takedown.Rules([{"id": "td-r", "kind": kind, "value": value, "requested_at": "2026-09-21",
                                "by": "Delta Quotidien", "status": "active"}])

    def test_a_stored_view_is_stripped_as_the_builder_strips_it(self):
        view = self.views[1]
        before = json.dumps(view, sort_keys=True)
        rules = self.rules("source", "delta")
        out = events.apply_takedowns(view, rules, sources_path=self.sources)
        self.assertEqual(json.dumps(view, sort_keys=True), before, "the stored view is never mutated")
        fire = by_id(out)[FIRE_ID]
        assert_never_credited(self, fire, {F2["id"], F2B["id"]})
        self.assertEqual(fire["withdrawn"], sorted([F2["id"], F2B["id"]]))
        row = next(r for r in fire["silence"] if r["institution"] == "delta")
        self.assertEqual((row["state"], row["scope"], row["institution_name"]),
                         ("withdrawn", "institution", "Delta Quotidien"))
        for ev in out["events"]:
            self.assertEqual(next(r for r in ev["silence"] if r["institution"] == "delta")["state"], "withdrawn")
        # The same R10 outcome as a rebuild of that edition under the rule.
        _, rebuilt, _ = events.build(self.stores[0], E2, registry(rules))
        again = by_id(rebuilt)[FIRE_ID]
        for key in ("institutions", "languages", "reporting_origin_count", "declared_by"):
            self.assertEqual(fire[key], again[key], key)
        self.assertEqual(fire["independence"]["count"], again["independence"]["count"])
        # Idempotent.
        self.assertEqual(events.apply_takedowns(out, rules, sources_path=self.sources), out)
        self.assertEqual(out["event_count"], len(out["events"]))

    def test_no_active_takedown_changes_nothing(self):
        view = self.views[1]
        self.assertEqual(events.apply_takedowns(view, takedown.Rules([]), sources_path=self.sources), view)

    def test_an_event_left_without_a_member_leaves_the_view(self):
        view = self.views[0]
        self.assertIn(STRIKE_ID, by_id(view))
        out = events.apply_takedowns(view, self.rules("source", "beta-qc"), sources_path=self.sources)
        self.assertNotIn(STRIKE_ID, by_id(out))
        self.assertEqual(out["event_count"], len(view["events"]) - 1)

    def test_a_domain_rule_on_a_member_without_url_uses_the_store_hosts(self):
        view = json.loads(json.dumps(self.views[1]))
        fire = by_id(view)[FIRE_ID]
        for m in fire["members"]:
            if m["item_id"] == F1["id"]:
                m.pop("url")
        rules = self.rules("host", "alpha-qc.example.org")
        blind = events.apply_takedowns(view, rules, sources_path=None)
        self.assertIn(F1["id"], [m["item_id"] for m in by_id(blind)[FIRE_ID]["members"]],
                      "without its URL or the store's host code the article cannot be recognised")
        hosts = {k: v["host"] for k, v in self.stores[1]["items"].items()}
        seen = events.apply_takedowns(view, rules, sources_path=None, hosts=hosts)
        assert_never_credited(self, by_id(seen)[FIRE_ID], {F1["id"]})

    def test_unbuilt_or_foreign_views_pass_through(self):
        rules = self.rules("source", "delta")
        for doc in ({"status": "store_unreadable", "events": []}, {"events": "x"}, "garbage", None):
            self.assertEqual(events.apply_takedowns(doc, rules, sources_path=self.sources), doc)
