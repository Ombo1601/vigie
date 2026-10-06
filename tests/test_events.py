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
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401  (puts scripts/ on sys.path; hermetic robots policy)
import event_match as em
import events
import facts
import normalize
import ownership
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
                self.assertTrue(set(e) - set(events.V1_KEYS) <= set(events.EXT_KEYS) | {"member_count", "in_edition", "silence"},
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
        self.assertEqual(events.independence(["x"], {"x": row("x", "solo", "solo")})["count"], 1)

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
             "summary": "Le 21 septembre, les pompiers sont revenus rue Zorblax.", "published_at": iso(20, 9)},
        ]
        rows = [(it["id"], it["institution"], json.loads(json.dumps(facts.extract_item(it)))) for it in items]
        self.assertEqual(events.slots_from_atoms(rows), facts.build_slots(items))
        self.assertTrue(any(r["divergent"] for r in events.slots_from_atoms(rows)), "2 vs 3 injured: side by side")

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
        self.assertEqual(g["anchors"], [{"type": "official_item", "ref": G1["id"], "rule": "membership",
                                         "status": "linked_by_rule"}])
        self.assertEqual(by_id(stores[0])[FIRE_ID]["anchors"], [], "no anchor is a measured absence")
        self.assertIn("absent", opss[0]["anchors"])

    def test_the_hook_calls_find_anchors_with_the_contract_and_validates_rows(self):
        calls = []

        def find_anchors(members, official_items, roadworks, consultations, outages, vocab_places):
            calls.append((len(members), len(official_items), len(roadworks), len(consultations), len(outages),
                          list(vocab_places)))
            return [{"type": "roadwork", "ref": "wzdx-1", "rule": "same-road", "status": "linked_by_rule"},
                    {"type": "rumour", "ref": "x", "rule": "y"}, "garbage",
                    {"type": "roadwork", "ref": "wzdx-1", "rule": "same-road"}]

        fake = types.ModuleType("anchors")
        fake.find_anchors = find_anchors
        with mock.patch.dict(sys.modules, {"anchors": fake}):
            events._ANCHORS_MODULE.clear()
            store, _, ops = events.build(None, E1, registry(), anchor_inputs={"roadworks": [{"event_id": "wzdx-1"}]})
        self.assertTrue(calls)
        self.assertEqual(by_id(store)[FIRE_ID]["anchors"],
                         [{"type": "roadwork", "ref": "wzdx-1", "rule": "same-road", "status": "linked_by_rule"}])
        self.assertEqual(ops["anchors"], "anchors.find_anchors")

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
                      "beta": {"items": 4, "feeds_ok": 1, "feeds_total": 1}}
        _, view, _ = events.build(None, E1, registry(), collection=collection)
        strike = by_id(view)[STRIKE_ID]
        states = {r["institution"]: r["state"] for r in strike["silence"]}
        self.assertNotIn("beta", states, "the strike's own outlet is not silent")
        self.assertEqual(states["delta"], "collection_gap", "our fetch failed: Vigie's gap, not theirs")
        self.assertEqual(states["alpha"], "no_linked_item")
        self.assertNotIn("cutmedia", states, "a cut source is not followed")
        for r in strike["silence"]:
            self.assertIn(r["state"], events.SILENCE_STATES)


# --------------------------------------------------------------------------- #
# R10
# --------------------------------------------------------------------------- #
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
        self.assertEqual(vfire["member_count"], len(fire["members"]))
        self.assertEqual(vfire["event_id"], FIRE_ID)
        for slot in vfire["facts"]:
            for v in slot["values"]:
                self.assertFalse(set(v["stated_by"]) & set(gone))
        # A later edition without the article: still withdrawn (by id or domain).
        store3, view3, _ = events.build(json.loads(json.dumps(store)), E3, registry(rules))
        self.assertEqual(by_id(store3)[FIRE_ID]["withdrawn"], sorted(gone))
        self.assertFalse({m["item_id"] for m in by_id(view3)[FIRE_ID]["members"]} & set(gone))
        return store

    def test_an_article_takedown(self):
        self._check_withdrawn(self._rules("url", takedown._canon(F2["url"])), [F2["id"]])

    def test_a_source_takedown(self):
        # The edited URL arrives after the request: it never joins anything.
        self._check_withdrawn(self._rules("source", "delta"), [F2["id"]])

    def test_a_domain_takedown_still_matches_after_the_url_is_gone(self):
        self._check_withdrawn(self._rules("host", "delta.example.org"), [F2["id"]])

    def test_with_the_takedown_file_through_the_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            files = Files(tmp)
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
            self.assertEqual(events.main(["--bogus-flag"]), 0)
        hostile = {"normalized_at": "2026-09-20T12:00:00+00:00",
                   "candidates": [F1, "not an item", {"id": "XYZ"}, {**F2, "published_at": "9999-12-31T23:59:59Z"}]}
        self.assertEqual(self._run(hostile), 0)
        ops = json.loads((self.files.data / "ops" / "events_shadow.json").read_text(encoding="utf-8"))
        self.assertEqual(ops["items"]["excluded"], {"malformed_candidate": 1, "malformed_id": 1})

    def test_a_fault_inside_the_builder_exits_zero(self):
        with mock.patch.object(events, "build", side_effect=RuntimeError("boom")), mock.patch("sys.stdout"):
            self.assertEqual(self.files.run(E1), 0)


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
        with mock.patch.object(pipeline, "run") as run, mock.patch("sys.argv", ["pipeline.py", "--render-only"]):
            pipeline.main()
        self.assertNotIn("events.py", [c.args[0] for c in run.call_args_list])
        with mock.patch.object(pipeline, "run") as run, mock.patch("sys.argv", ["pipeline.py", "--offline"]):
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
