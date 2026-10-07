"""scripts/ranking_events.py: the evidence-driven order of events and the tier chip.

docs/AUTONOMY.md is the contract: no hand-set weight, cut-offs are quantiles of the
current edition, every value is explained, nothing depends on a person or a clock.
Every event, id, institution and place below is INVENTED: no publisher text exists
in this file."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
import unittest
from datetime import datetime, timedelta, timezone

import harness  # noqa: F401  (puts scripts/ on sys.path)

import events_eval as ev  # noqa: E402
import i18n  # noqa: E402
import ranking_events as R  # noqa: E402

CLOCK = "2026-10-06T12:00:00+00:00"
NOW = datetime(2026, 10, 6, 12, 0, tzinfo=timezone.utc)


def iso(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).isoformat()


def iid(n: int) -> str:
    return f"{n:024x}"


_COUNTER = [0]


def member(owner: str, hours_ago: float, origin: str = "own_reporting", **extra) -> dict:
    _COUNTER[0] += 1
    m = {"item_id": iid(_COUNTER[0]), "institution": owner, "source_id": owner, "language": "fr",
         "published_at": iso(hours_ago), "first_seen": iso(hours_ago), "origin_class": origin,
         "ownership_class": "independent", "owner_group": owner, "date_suspect": False}
    m.update(extra)
    return m


def event(n: int, geo: str, members: list[dict], anchors: list[dict] | None = None, **extra) -> dict:
    """An event view with the builder's own independence (same owner group = one group,
    an official member is a declaration)."""
    groups: dict[str, list[str]] = {}
    for m in members:
        groups.setdefault("owner:" + m["owner_group"], []).append(m["item_id"])
    e = {"event_id": f"ev-{n:016x}", "geo": geo, "places": ["quebec-city"], "type": "unclassified",
         "members": members, "anchors": anchors or [],
         "independence": {"groups": sorted(sorted(g) for g in groups.values()), "count": len(groups),
                          "declarations": sorted(m["item_id"] for m in members if m["origin_class"] == "official"),
                          "reporting_count": 0}}
    e.update(extra)
    return e


def decl(rid: str, status="active", impact="some-lanes-closed", start="2026-09-01", end="2026-12-01") -> dict:
    return {"id": rid, "status": status, "impact": impact, "from": start, "to": end}


def anchor(ref: str, typ="roadwork") -> dict:
    return {"type": typ, "ref": ref, "rule": "roadwork-name-dates-v1", "status": "linked_by_rule"}


def ctx(rows=None, now=CLOCK, total=None, **extra) -> dict:
    view = {"rows": rows or [], "total": total if total is not None else len(rows or [])}
    c = {"edition_clock": CLOCK, "roadworks_view": view, "now": now}
    c.update(extra)
    return c


def by_id(rows: list[dict]) -> dict[str, dict]:
    return {r["event_id"]: r for r in rows}


def plain_edition(count: int = 30, owners=("a", "b", "c", "d")) -> list[dict]:
    """A quiet edition: single-voice events, Quebec City, spread over the last days."""
    return [event(100 + i, "quebec-city", [member(owners[i % len(owners)], 2 + i * 2)]) for i in range(count)]


class BandsAreQuantilesOfTheEdition(unittest.TestCase):
    def test_mid_rank_percentile_quartiles(self):
        pop = list(range(1, 9))
        self.assertEqual([R.band_of(v, pop)[0] for v in pop], [0, 0, 1, 1, 2, 2, 3, 3])
        self.assertEqual(R.band_of(1, pop)[1], 6)      # floor(100 * 0.5 / 8)
        self.assertEqual(R.band_of(8, pop)[1], 93)

    def test_ties_share_the_middle_of_their_rank_and_none_is_lowest(self):
        pop = [1] * 9 + [5]
        self.assertEqual(R.band_of(1, pop), (1, 45))    # all ties: the middle of the lower 90 %
        self.assertEqual(R.band_of(5, pop)[0], 3)
        self.assertEqual(R.band_of(None, [None, 1, 2, 3])[0], 0)
        self.assertEqual(R.band_of(3, []), (0, 0))

    def test_quiet_week_and_election_night_each_rank_on_their_own_distribution(self):
        # One event with two independent origins, in two very different editions.
        quiet = plain_edition(20)
        quiet.append(event(1, "quebec-city", [member("a", 5), member("b", 5)]))
        night = [event(200 + i, "quebec-city", [member(f"o{j}", 3 + i) for j in range(5 + i % 5)])
                 for i in range(20)]
        night.append(event(1, "quebec-city", [member("a", 5), member("b", 5)]))
        q = by_id(R.rank_events(quiet, ctx()))[f"ev-{1:016x}"]["criteria"]["origins"]
        n = by_id(R.rank_events(night, ctx()))[f"ev-{1:016x}"]["criteria"]["origins"]
        self.assertEqual(q["reporting"], n["reporting"])          # the same raw count ...
        self.assertEqual(q["band"], 3)                            # ... top quartile in a quiet week
        self.assertEqual(n["band"], 0)                            # ... bottom quartile on an election night
        self.assertGreater(q["percent_below"], n["percent_below"])

    def test_freshness_is_banded_by_quartiles_of_the_edition(self):
        evs = [event(i, "quebec-city", [member("a", h)]) for i, h in enumerate([1, 2, 3, 4, 5, 6, 7, 8], start=1)]
        rows = by_id(R.rank_events(evs, ctx()))
        self.assertEqual([rows[f"ev-{i:016x}"]["criteria"]["fresh"]["band"] for i in range(1, 9)],
                         [3, 3, 2, 2, 1, 1, 0, 0])
        # the same ages a week later are the same bands: only the distribution matters
        old = [event(i, "quebec-city", [member("a", h + 200)]) for i, h in enumerate([1, 2, 3, 4, 5, 6, 7, 8], start=1)]
        rows2 = by_id(R.rank_events(old, ctx()))
        self.assertEqual([rows2[f"ev-{i:016x}"]["criteria"]["fresh"]["band"] for i in range(1, 9)],
                         [3, 3, 2, 2, 1, 1, 0, 0])


class SuccessiveCriteria(unittest.TestCase):
    def test_now_impact_outranks_everything(self):
        big = event(1, "quebec-city", [member(f"o{j}", 1) for j in range(6)])           # freshest, most origins
        quiet = event(2, "linked", [member("z", 90)], anchors=[anchor("W1")])            # old, linked, 1 origin
        evs = plain_edition(10) + [big, quiet]
        rows = R.rank_events(evs, ctx([decl("W1")]))
        self.assertEqual(rows[0]["event_id"], quiet["event_id"])
        self.assertTrue(rows[0]["criteria"]["now"]["impact"])
        self.assertEqual(rows[0]["criteria"]["now"]["active"], 1)
        self.assertEqual(rows[0]["criteria"]["decided_by"], "first")
        self.assertEqual(rows[1]["event_id"], big["event_id"])
        self.assertEqual(rows[1]["criteria"]["decided_by"], "now")

    def test_only_a_real_obstruction_in_force_counts(self):
        e = lambda n, ref: event(n, "linked", [member("z", 50)], anchors=[anchor(ref)])
        declarations = [
            decl("OPEN", impact="all-lanes-open"),                    # declared, obstructs nothing
            decl("UNK", impact="unknown"),                            # impact not stated: not counted
            decl("DONE", end="2026-10-05"),                           # its end date has passed
            decl("LATER", status="planned", start="2026-10-09"),      # starts in 3 days
            decl("SOON", status="planned", start="2026-10-07"),       # starts within 24 h
            decl("TODAY", status="pending", start="2026-10-06"),      # starts today
            decl("LIVE", impact="all-lanes-closed"),
        ]
        evs = [e(1, "OPEN"), e(2, "UNK"), e(3, "DONE"), e(4, "LATER"), e(5, "SOON"), e(6, "TODAY"), e(7, "LIVE"),
               e(8, "MISSING")]
        rows = by_id(R.rank_events(evs, ctx(declarations)))
        impact = {n: rows[f"ev-{n:016x}"]["criteria"]["now"] for n in range(1, 9)}
        self.assertEqual([impact[n]["impact"] for n in range(1, 9)],
                         [False, False, False, False, True, True, True, False])
        self.assertEqual((impact[5]["soon"], impact[5]["active"]), (1, 0))
        self.assertEqual((impact[7]["soon"], impact[7]["active"]), (0, 1))
        self.assertEqual(impact[8]["unseen"], 1)       # an anchor the view does not carry is reported, never counted
        self.assertEqual(impact[1]["anchored"], 1)

    def test_without_a_clock_nothing_is_in_force_and_the_page_is_told(self):
        e = event(1, "linked", [member("z", 5)], anchors=[anchor("W1")])
        row = R.rank_events([e], {"edition_clock": "", "roadworks_view": {"rows": [decl("W1")]}, "now": None})[0]
        self.assertFalse(row["criteria"]["now"]["impact"])
        self.assertEqual(row["explain"][0]["key"], "rank.now.unknown")

    def test_a_declared_outage_counts_only_while_its_coverage_is_recent(self):
        fresh = event(1, "quebec-city", [member("a", 3)], anchors=[anchor("H1", "outage")])
        stale = event(2, "quebec-city", [member("a", 40)], anchors=[anchor("H2", "outage")])
        rows = by_id(R.rank_events([fresh, stale], ctx()))
        self.assertEqual(rows[fresh["event_id"]]["criteria"]["now"]["outage"], 1)
        self.assertFalse(rows[stale["event_id"]]["criteria"]["now"]["impact"])

    def test_an_official_item_anchor_is_not_now_impact(self):
        e = event(1, "quebec-city", [member("ville", 2, origin="official")], anchors=[anchor("x", "official_item")])
        self.assertFalse(R.rank_events([e], ctx())[0]["criteria"]["now"]["impact"])

    def test_geographic_scope_comes_before_origins_and_freshness(self):
        city = event(1, "quebec-city", [member("a", 80)])
        province = event(2, "quebec", [member("a", 1), member("b", 1), member("c", 1)])
        linked = event(3, "linked", [member("a", 1), member("b", 1), member("c", 1), member("d", 1)])
        rows = R.rank_events([linked, province, city], ctx())
        self.assertEqual([r["event_id"] for r in rows], [city["event_id"], province["event_id"], linked["event_id"]])
        self.assertEqual([r["criteria"]["geo"]["scope"] for r in rows], ["quebec-city", "quebec", "linked"])
        self.assertEqual(rows[1]["criteria"]["decided_by"], "geo")

    def test_a_declaration_of_the_city_is_place_evidence_for_the_scope(self):
        e = event(1, "linked", [member("a", 4)], anchors=[anchor("W1")])
        row = R.rank_events([e], ctx([decl("W1", impact="all-lanes-open")]))[0]
        self.assertEqual((row["criteria"]["geo"]["scope"], row["criteria"]["geo"]["basis"]), ("quebec-city", "anchor"))
        self.assertEqual(row["explain"][1]["key"], "rank.geo.quebec-city.anchor")

    def test_origins_then_freshness_then_instant_then_id(self):
        many = event(1, "quebec-city", [member(f"o{j}", 60) for j in range(4)])
        old1 = event(2, "quebec-city", [member("a", 30)])
        new1 = event(3, "quebec-city", [member("b", 20)])
        tie_a = event(4, "quebec-city", [member("c", 20)])
        # same instant as new1 and tie_a: only the id can separate them
        rows = R.rank_events(plain_edition(12) + [many, old1, new1, tie_a], ctx())
        order = [r["event_id"] for r in rows]
        self.assertLess(order.index(many["event_id"]), order.index(new1["event_id"]))
        self.assertLess(order.index(new1["event_id"]), order.index(old1["event_id"]))
        r = by_id(rows)
        self.assertEqual(r[many["event_id"]]["criteria"]["decided_by"] in ("origins", "geo", "first"), True)
        self.assertLess(order.index(new1["event_id"]), order.index(tie_a["event_id"]))   # smaller id first
        self.assertEqual(r[tie_a["event_id"]]["criteria"]["decided_by"], "id")

    def test_declarations_do_not_inflate_origins(self):
        official_only = event(1, "quebec-city", [member("ville", 2, origin="official"),
                                                 member("ville", 3, origin="official")])
        mixed = event(2, "quebec-city", [member("ville", 2, origin="official"), member("a", 3)])
        wire = event(3, "quebec-city", [member("a", 2), member("a", 3), member("b", 4)])   # one owner twice
        rows = by_id(R.rank_events([official_only, mixed, wire], ctx()))
        o = {n: rows[f"ev-{n:016x}"]["criteria"]["origins"] for n in (1, 2, 3)}
        self.assertEqual((o[1]["reporting"], o[1]["declarations"]), (0, 2))
        self.assertEqual((o[2]["reporting"], o[2]["declarations"]), (1, 1))
        self.assertEqual((o[3]["reporting"], o[3]["members"]), (2, 3))
        # and it moves the order: a declaration alone is below a single report
        self.assertEqual(rows[f"ev-{1:016x}"]["criteria"]["origins"]["band"], 0)

    def test_origins_are_recomputed_from_the_members_not_trusted_from_the_store(self):
        e = event(1, "quebec-city", [member("a", 2), member("b", 3)])
        e["independence"]["reporting_count"] = 99
        e["independence"]["count"] = 99
        self.assertEqual(R.rank_events([e], ctx())[0]["criteria"]["origins"]["reporting"], 2)
        bare = copy.deepcopy(e)
        del bare["independence"]
        self.assertEqual(R.rank_events([bare], ctx())[0]["criteria"]["origins"]["reporting"], 2)


class TheCut(unittest.TestCase):
    def test_at_most_twelve_and_only_at_or_above_the_median(self):
        rows = R.rank_events(plain_edition(40), ctx())
        shown = [r for r in rows if r["shown"]]
        self.assertEqual(len(rows), 40)
        self.assertEqual(len(shown), R.CAP)
        self.assertEqual([r["position"] for r in rows], list(range(1, 41)))
        self.assertEqual([r["position"] for r in shown], list(range(1, 13)))   # shown is a prefix
        s = R.summarize(rows)
        self.assertEqual((s["total"], s["shown"], s["not_shown"]), (40, 12, 28))
        self.assertEqual(sum(s["hidden"].values()), 28)
        self.assertEqual(s["hidden"][R.HIDDEN_OVER_CAP] + s["hidden"][R.HIDDEN_BELOW_MEDIAN], 28)
        self.assertGreater(s["hidden"][R.HIDDEN_BELOW_MEDIAN], 0)

    def test_a_small_edition_shows_at_most_the_upper_half_and_says_why_for_the_rest(self):
        evs = [event(i, "quebec-city", [member("a", 2 * i)]) for i in range(1, 7)]
        evs += [event(10 + i, "linked", [member("a", 2 * i)]) for i in range(1, 7)]
        rows = R.rank_events(evs, ctx())
        reasons = {r["event_id"]: r["hidden_reason"] for r in rows}
        shown_geos = {r["criteria"]["geo"]["scope"] for r in rows if r["shown"]}
        self.assertEqual(shown_geos, {"quebec-city"})
        self.assertTrue(all(reasons[r["event_id"]] == R.HIDDEN_BELOW_MEDIAN
                            for r in rows if r["criteria"]["geo"]["scope"] == "linked"))
        s = R.summarize(rows)
        self.assertEqual((s["shown"], s["hidden"][R.HIDDEN_BELOW_MEDIAN], s["hidden"][R.HIDDEN_OVER_CAP]), (6, 6, 0))

    def test_every_event_stays_in_the_list_with_its_reason(self):
        rows = R.rank_events(plain_edition(40), ctx())
        for r in rows:
            self.assertEqual(r["shown"], r["hidden_reason"] == "")
            keys = [x["key"] for x in r["explain"]]
            self.assertEqual(any(k.startswith("rank.hidden.") for k in keys), not r["shown"])
            self.assertEqual("rank.shown" in keys, r["shown"])

    def test_a_withdrawn_voice_is_never_counted_and_a_fully_withdrawn_event_is_not_shown(self):
        a, b = member("a", 3), member("b", 4)
        e1 = event(1, "quebec-city", [a, b])
        e2 = event(2, "quebec-city", [member("c", 2)])
        rows = by_id(R.rank_events([e1, e2], ctx(withdrawn_item_ids=[b["item_id"]])))
        self.assertEqual(rows[e1["event_id"]]["criteria"]["origins"]["reporting"], 1)
        self.assertEqual(rows[e1["event_id"]]["criteria"]["origins"]["members"], 1)
        again = by_id(R.rank_events([e1, e2], ctx(withdrawn_item_ids=[a["item_id"], b["item_id"]])))
        gone = again[e1["event_id"]]
        self.assertFalse(gone["shown"])
        self.assertEqual(gone["hidden_reason"], R.HIDDEN_NO_MEMBER)
        self.assertEqual(gone["position"], 2)                       # last, after the ranked one
        self.assertEqual(R.summarize(list(again.values()))["hidden"][R.HIDDEN_NO_MEMBER], 1)
        # the event's own `withdrawn` list is honoured the same way
        e3 = event(3, "quebec-city", [a], withdrawn=[a["item_id"]])
        self.assertEqual(R.rank_events([e3], ctx())[0]["hidden_reason"], R.HIDDEN_NO_MEMBER)
        # and a withdrawn voice no longer sets the freshness
        old, new = member("a", 90), member("a", 1)
        e4 = event(4, "quebec-city", [old, new])
        age = R.rank_events([e4], ctx(withdrawn_item_ids=[new["item_id"]]))[0]["criteria"]["fresh"]["age_hours"]
        self.assertEqual(age, 90)


class Explanation(unittest.TestCase):
    def rows(self):
        evs = plain_edition(30)
        evs.append(event(1, "linked", [member("z", 50)], anchors=[anchor("W1")]))
        evs.append(event(2, "quebec-city", [member("ville", 5, origin="official"), member("a", 6), member("b", 7)]))
        evs.append(event(3, "quebec-city", [{"item_id": iid(9999), "institution": "q", "source_id": "q",
                                              "language": "fr", "published_at": None, "first_seen": None,
                                              "origin_class": "unknown", "owner_group": "q"}]))
        return R.rank_events(evs, ctx([decl("W1")]))

    def test_every_criterion_carries_its_real_values(self):
        for r in self.rows():
            c = r["criteria"]
            self.assertEqual(set(c), {"now", "geo", "origins", "fresh", "decided_by", "bands"})
            self.assertEqual(set(c["now"]), {"impact", "active", "soon", "outage", "anchored", "unseen",
                                             "declarations_in_view", "clock"})
            self.assertEqual(set(c["geo"]), {"scope", "rank", "basis"})
            self.assertEqual(set(c["origins"]), {"reporting", "declarations", "groups", "members", "percent_below",
                                                 "band", "events_compared"})
            self.assertEqual(set(c["fresh"]), {"last_instant", "source", "age_hours", "percent_older", "band",
                                               "events_compared"})
            self.assertIn(c["decided_by"], ("first", "now", "geo", "origins", "fresh", "id"))
            self.assertEqual(c["now"]["clock"], "2026-10-06T12:00:00Z")

    def test_every_explain_key_prints_in_both_languages_with_its_values(self):
        seen = set()
        rows = self.rows()
        no_clock = {"edition_clock": "", "roadworks_view": None, "now": None}
        rows += R.rank_events(plain_edition(2) + [event(7, "linked", [member("a", 1)], withdrawn=[])], no_clock)
        e = event(8, "quebec-city", [member("a", 1)], withdrawn=[iid(0)])
        rows += R.rank_events([e], ctx(withdrawn_item_ids=[e["members"][0]["item_id"]]))
        rows += R.rank_events([event(9, "unknown-scope", [member("a", 1)], anchors=[anchor("x", "outage")])], ctx())
        for r in rows:
            self.assertTrue(r["explain"])
            for entry in r["explain"]:
                seen.add(entry["key"])
                for lang in ("fr", "en"):
                    text = i18n.t(entry["key"], lang, **entry["values"])
                    self.assertTrue(text.strip())
                    self.assertNotRegex(text, r"[{}]")
        s = R.summarize(rows)
        for entry in s["explain"]:
            for lang in ("fr", "en"):
                self.assertTrue(i18n.t(entry["key"], lang, **entry["values"]))
        self.assertTrue({"rank.now.active", "rank.now.none", "rank.geo.quebec-city", "rank.origins", "rank.fresh",
                         "rank.fresh.unknown", "rank.shown", "rank.hidden.over_cap", "rank.hidden.below_median",
                         "rank.hidden.no_member", "rank.now.unknown", "rank.geo.unknown",
                         "rank.geo.linked", "rank.geo.quebec-city.anchor", "rank.decided.first",
                         "rank.decided.now"} <= seen, sorted(seen))

    def test_each_row_explains_every_criterion_in_order(self):
        for r in self.rows():
            keys = [x["key"] for x in r["explain"]]
            kinds = []
            for k in keys:
                for name in ("now", "geo", "origins", "fresh", "decided", "shown", "hidden"):
                    if k.startswith(f"rank.{name}"):
                        kinds.append(name)
                        break
            order = ["now", "geo", "origins", "fresh", "decided"]
            positions = [kinds.index(x) for x in order]
            self.assertEqual(positions, sorted(positions), keys)
            self.assertIn(kinds[-1], ("shown", "hidden"))

    def test_an_event_without_any_instant_is_ranked_after_the_dated_ones_and_says_so(self):
        rows = self.rows()
        undated = [r for r in rows if r["criteria"]["fresh"]["source"] == "none"]
        self.assertEqual(len(undated), 1)
        self.assertIsNone(undated[0]["criteria"]["fresh"]["age_hours"])
        self.assertEqual(undated[0]["explain"][3]["key"], "rank.fresh.unknown")
        same_geo = [r for r in rows if r["criteria"]["geo"]["scope"] == "quebec-city"
                    and r["criteria"]["origins"]["band"] == undated[0]["criteria"]["origins"]["band"]]
        self.assertEqual(same_geo[-1]["event_id"], undated[0]["event_id"])

    def test_a_suspect_or_implausible_date_never_sets_freshness(self):
        m = member("a", 0.5, date_suspect=True, first_seen=iso(30))
        bad = member("b", 0, published_at="0001-01-01T00:00:00Z", first_seen=iso(20))
        row = R.rank_events([event(1, "quebec-city", [m, bad])], ctx())[0]["criteria"]["fresh"]
        self.assertEqual((row["age_hours"], row["source"]), (20, "first_seen"))


class Purity(unittest.TestCase):
    def evs(self):
        evs = plain_edition(25)
        evs.append(event(1, "linked", [member("z", 50)], anchors=[anchor("W1")]))
        evs.append(event(2, "quebec", [member("a", 5), member("b", 6)]))
        return evs

    def test_same_inputs_same_bytes_whatever_the_input_order(self):
        evs = self.evs()
        a = json.dumps(R.rank_events(evs, ctx([decl("W1")])), sort_keys=True)
        b = json.dumps(R.rank_events(list(reversed(evs)), ctx([decl("W1")])), sort_keys=True)
        c = json.dumps(R.rank_events(copy.deepcopy(evs), ctx([decl("W1")])), sort_keys=True)
        self.assertEqual(a, b)
        self.assertEqual(a, c)

    def test_inputs_are_never_mutated(self):
        evs, cx = self.evs(), ctx([decl("W1")])
        before = json.dumps([evs, cx], sort_keys=True)
        R.rank_events(evs, cx)
        R.tier_chip("certain", {"gates": {}})
        self.assertEqual(json.dumps([evs, cx], sort_keys=True), before)

    def test_independent_of_the_hash_seed(self):
        code = (
            "import sys, json, hashlib; sys.path[:0] = [%r, %r]\n"
            "import test_ranking_events as T, ranking_events as R\n"
            "rows = R.rank_events(T.PurityEvents(), T.ctx([T.decl('W1')]))\n"
            "print(hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest())\n"
        ) % (str(harness.ROOT / "tests"), str(harness.SCRIPTS))
        outs = set()
        for seed in ("0", "1", "4242"):
            env = {**os.environ, "PYTHONHASHSEED": seed}
            done = subprocess.run([sys.executable, "-X", "utf8", "-c", code], capture_output=True, text=True,
                                  env=env, timeout=120)
            self.assertEqual(done.returncode, 0, done.stderr)
            outs.add(done.stdout.strip())
        self.assertEqual(len(outs), 1, outs)

    def test_no_wall_clock_and_no_randomness_in_the_module(self):
        source = (harness.SCRIPTS / "ranking_events.py").read_text(encoding="utf-8")
        code = re.sub(r'""".*?"""', "", source, count=1, flags=re.S)
        for needle in ("datetime.now", "utcnow", "time.time", "import random", "import uuid", "import time",
                       "os.environ", "open(", "urllib", "socket"):
            self.assertNotIn(needle, code)
        self.assertNotIn("\r", source)

    def test_the_only_clock_is_the_one_passed_in(self):
        e = event(1, "linked", [member("z", 50)], anchors=[anchor("W1")])
        v = [decl("W1", status="planned", start="2026-10-07")]
        self.assertTrue(R.rank_events([e], ctx(v, now="2026-10-06T12:00:00+00:00"))[0]["criteria"]["now"]["impact"])
        self.assertFalse(R.rank_events([e], ctx(v, now="2026-10-04T12:00:00+00:00"))[0]["criteria"]["now"]["impact"])

    def test_the_roads_lane_clock_is_whatever_the_caller_computed(self):
        e = event(1, "quebec-city", [member("a", 1)])
        row = R.rank_events([e], {"edition_clock": CLOCK, "roadworks_view": None, "now": "2026-10-06T14:00:00Z"})[0]
        self.assertEqual(row["criteria"]["now"]["clock"], "2026-10-06T14:00:00Z")
        self.assertEqual(row["criteria"]["fresh"]["age_hours"], 3)
        row = R.rank_events([e], {"edition_clock": CLOCK, "roadworks_view": None, "now": None})[0]
        self.assertEqual(row["criteria"]["now"]["clock"], "2026-10-06T12:00:00Z")      # falls back to the edition's


def PurityEvents():
    _COUNTER[0] = 0
    return Purity().evs()


class FailSoft(unittest.TestCase):
    def test_malformed_events_never_crash_and_the_good_ones_still_rank(self):
        good = event(1, "quebec-city", [member("a", 3)])
        junk = [None, "text", 7, [], {}, {"event_id": ""}, {"event_id": 5},
                {"event_id": "ev-bad1", "members": "nope", "anchors": 3, "geo": 9, "independence": []},
                {"event_id": "ev-bad2", "members": [None, 3, {"item_id": 4}], "anchors": [None, {"type": 1}],
                 "independence": {"groups": "x"}, "withdrawn": 5},
                {"event_id": "ev-bad3", "members": [{"item_id": "m1", "published_at": {"x": 1}, "first_seen": [],
                                                     "origin_class": 3, "owner_group": None}],
                 "independence": {"groups": [[1, None]], "declarations": "x"}, "geo": None}]
        rows = R.rank_events([good] + junk + [good], ctx())
        ids = [r["event_id"] for r in rows]
        self.assertEqual(ids.count(good["event_id"]), 1)
        self.assertIn("ev-bad3", ids)
        self.assertEqual(rows[0]["event_id"], good["event_id"])
        for r in rows:
            for entry in r["explain"]:
                i18n.t(entry["key"], "fr", **entry["values"])

    def test_absent_or_garbage_context_and_events(self):
        for events in (None, "x", 5, {}, []):
            self.assertEqual(R.rank_events(events, ctx()), [])
        e = [event(1, "quebec-city", [member("a", 3)])]
        for bad in (None, "x", [], {"roadworks_view": "x", "now": 5, "edition_clock": object()},
                    {"roadworks_view": {"rows": "x", "events": [None, 1, {"event_id": []}]}, "now": "garbage"}):
            rows = R.rank_events(e, bad)
            self.assertEqual(len(rows), 1)
            self.assertFalse(rows[0]["criteria"]["now"]["impact"])

    def test_a_fault_is_a_diagnosis_not_a_crash(self):
        saved = R._rank
        R._rank = lambda *a, **k: 1 / 0
        try:
            import io
            from contextlib import redirect_stderr
            buf = io.StringIO()
            with redirect_stderr(buf):
                self.assertEqual(R.rank_events([], ctx()), [])
            self.assertIn("ZeroDivisionError", buf.getvalue())
        finally:
            R._rank = saved

    def _corrupt_docs(self):
        base = tier_doc(certain=(80, 0), human=True)
        docs = []
        d = copy.deepcopy(base); d["gates"]["gate1_tier_precision"].update(labelers=5, provenance=None, human_verified=False); docs.append(d)
        d = copy.deepcopy(base); d["gates"]["gate1_tier_precision"].update(labelers={"a": 1}, provenance=7, human_verified=False); docs.append(d)
        d = copy.deepcopy(base); d["gates"]["gate1_tier_precision"]["splits"]["test"]["tier_by_label"]["different"]["certain"] = -5; docs.append(d)
        d = copy.deepcopy(base); d["gates"]["gate1_tier_precision"]["splits"]["test"]["tier_by_label"]["same_event"]["certain"] = True; docs.append(d)
        d = copy.deepcopy(base); d["gates"]["gate1_tier_precision"]["splits"]["test"]["tier_by_label"]["same_event"]["certain"] = "80"; docs.append(d)
        d = copy.deepcopy(base); d["gates"]["gate1_tier_precision"]["splits"]["test"]["tier_by_label"]["same_event"]["certain"] = None; docs.append(d)
        d = copy.deepcopy(base); d["gates"]["gate1_tier_precision"]["splits"]["test"]["tier_by_label"] = {"same_event": 3, 4: {"certain": 1}}; docs.append(d)
        docs.append({"gates": {"gate1_tier_precision": {"status": "measured", "labelers": 5}}})
        docs.append({"gates": {"gate1_tier_precision": {"status": "measured", "splits": {"test": 5}}}})
        docs.append({"gates": {"gate1_tier_precision": {"status": "measured", "splits": [1], "labelers": [None, 3]}}})
        return docs

    def test_a_malformed_measured_gate_is_auto_unmeasured_not_a_crash(self):
        import io
        from contextlib import redirect_stderr
        for doc in self._corrupt_docs():
            for tier in ("certain", "probable"):
                with redirect_stderr(io.StringIO()):
                    chip = R.tier_chip(tier, doc)
                self.assertEqual(chip["kind"], "auto", doc)
                i18n.t(chip["key"], "fr", **chip["values"])
                i18n.t(chip["key"], "en", **chip["values"])
            with redirect_stderr(io.StringIO()):
                pub = R.quality_public(doc)
            self.assertEqual(pub["format"], "events-quality-public-v1")
            json.dumps(pub)

    def test_negative_counts_are_unmeasured_and_diagnosed(self):
        import io
        from contextlib import redirect_stderr
        doc = tier_doc(certain=(80, -5), human=True)
        buf = io.StringIO()
        with redirect_stderr(buf):
            chip = R.tier_chip("certain", doc)
        self.assertEqual((chip["kind"], chip["key"], chip["values"]["n"]), ("auto", "chip.auto.unmeasured", 0))
        self.assertIn("unreadable pair count", buf.getvalue())

    def test_a_crash_inside_the_readers_is_a_diagnosis(self):
        import io
        from contextlib import redirect_stderr
        saved = (R._tier_chip, R._quality_public)
        R._tier_chip = lambda *a, **k: 1 / 0
        R._quality_public = lambda *a, **k: 1 / 0
        try:
            buf = io.StringIO()
            with redirect_stderr(buf):
                chip = R.tier_chip("certain", tier_doc(certain=(80, 0), human=True))
                pub = R.quality_public(tier_doc(certain=(80, 0), human=True))
            self.assertEqual((chip["kind"], chip["key"]), ("auto", "chip.auto.unmeasured"))
            self.assertFalse(pub["measured"])
            self.assertEqual(buf.getvalue().count("fault while reading the quality document"), 2)
        finally:
            R._tier_chip, R._quality_public = saved

    def test_wilson_clamps_corrupt_counts(self):
        for k, n in ((10, 5), (-3, 5), (5, 5), (0, 5)):
            lo, hi = R.wilson(k, n)
            self.assertTrue(0.0 <= lo <= hi <= 1.0)

    def test_summary_of_nothing(self):
        s = R.summarize([])
        self.assertEqual((s["total"], s["shown"], s["not_shown"]), (0, 0, 0))
        self.assertEqual(R.summarize([None, 3])["total"], 0)


# --------------------------------------------------------------------------- #
# The chip
# --------------------------------------------------------------------------- #
def tier_doc(*, certain=(0, 0), probable=(0, 0), human=False, provenance="two language models, invented",
             status="measured") -> dict:
    """The shape scripts/events_eval.py writes (gate 1), counts invented.
    certain/probable are (same-event pairs, wrong pairs) at that tier on the test split."""
    def lab(tier):
        return {"same_event": {"certain": certain[0], "probable": probable[0], "possible": 3, "none": 40},
                "related": {"certain": 0, "probable": probable[1], "possible": 5, "none": 10},
                "different": {"certain": certain[1], "probable": 0, "possible": 2, "none": 200}}
    gate = {"status": status, "human_verified": human, "provenance": provenance, "labelers": ["labeller-a"],
            "splits": {"test": {"n": 400, "tier_by_label": lab(None), "bands": {}}}}
    return {"schema": 1, "gates": {"gate1_tier_precision": gate}}


class TierChip(unittest.TestCase):
    def test_no_evidence_is_automatic_and_never_certain(self):
        for doc in (None, {}, "x", {"gates": {}}, {"gates": {"gate1_tier_precision": {"status": "skipped"}}}):
            chip = R.tier_chip("certain", doc)
            self.assertEqual(chip["kind"], "auto")
            self.assertEqual(chip["key"], "chip.auto.unmeasured")
            self.assertEqual(R.tier_chip("probable", doc)["kind"], "auto")
            self.assertTrue(i18n.t(chip["key"], "fr", **chip["values"]))

    def test_model_labelled_evidence_stays_automatic_with_its_measured_figures(self):
        # 100 % on a hundred pairs, but a model labelled them: never "certain"
        doc = tier_doc(certain=(100, 0), probable=(30, 10), human=False)
        c = R.tier_chip("certain", doc)
        self.assertEqual(c["kind"], "auto")
        self.assertEqual(c["key"], "chip.auto.model")
        self.assertEqual((c["values"]["p"], c["values"]["n"], c["values"]["provenance"]), (100, 100, "model"))
        self.assertLess(c["values"]["lo"], 100)
        p = R.tier_chip("probable", doc)
        self.assertEqual((p["kind"], p["values"]["p"], p["values"]["n"]), ("auto", 75, 40))
        for lang in ("fr", "en"):
            text = i18n.t(c["key"], lang, **c["values"])
            self.assertIn("100", text)

    def test_an_unrecorded_provenance_is_automatic_too(self):
        doc = tier_doc(certain=(100, 0), provenance=ev.NO_PROVENANCE)
        c = R.tier_chip("certain", doc)
        self.assertEqual((c["kind"], c["key"], c["values"]["provenance"]), ("auto", "chip.auto.unrecorded", "unrecorded"))

    def test_certain_needs_human_checked_pairs_and_the_lower_bound_and_73_pairs(self):
        self.assertEqual(R.CERTAIN_MIN_PAIRS, ev.zero_error_pairs_needed())
        # human-checked, 73 pairs, no error: Wilson lower bound just reaches 0.95
        c = R.tier_chip("certain", tier_doc(certain=(73, 0), human=True, provenance="two people, invented"))
        self.assertEqual((c["kind"], c["key"]), ("certain", "chip.certain"))
        self.assertEqual(c["values"]["provenance"], "human")
        self.assertGreaterEqual(c["values"]["lo"], 95)
        # one pair short
        c = R.tier_chip("certain", tier_doc(certain=(72, 0), human=True))
        self.assertEqual(c["kind"], "probable")
        # enough pairs, one error among 200: the lower bound is under the bar
        c = R.tier_chip("certain", tier_doc(certain=(199, 1), human=True))
        self.assertEqual(c["kind"], "certain" if R.wilson(199, 200)[0] >= 0.95 else "probable")
        c = R.tier_chip("certain", tier_doc(certain=(120, 6), human=True))
        self.assertEqual(c["kind"], "probable")
        self.assertEqual((c["values"]["p"], c["values"]["n"]), (95, 126))
        # the same human evidence never promotes a probable-tier grouping
        c = R.tier_chip("probable", tier_doc(certain=(500, 0), probable=(300, 0), human=True))
        self.assertEqual(c["kind"], "probable")

    def test_transitions_follow_the_evidence(self):
        kinds = [R.tier_chip("certain", d)["kind"] for d in (
            None,
            tier_doc(certain=(80, 0), human=False),
            tier_doc(certain=(30, 0), human=True),
            tier_doc(certain=(80, 0), human=True),
        )]
        self.assertEqual(kinds, ["auto", "auto", "probable", "certain"])

    def test_possible_is_never_a_merge_and_a_single_article_has_no_chip_claim(self):
        doc = tier_doc(certain=(80, 0), human=True)
        self.assertEqual(R.tier_chip("possible", doc), {"kind": "possible", "key": "tier.possible", "values": {}})
        for t in (None, "", "weird", 3):
            self.assertEqual(R.tier_chip(t, doc), {"kind": "none", "key": "why.single", "values": {}})

    def test_figures_come_from_the_held_out_split_only(self):
        doc = tier_doc(certain=(10, 0))
        doc["gates"]["gate1_tier_precision"]["splits"]["dev"] = {"tier_by_label": {
            "same_event": {"certain": 1000}, "related": {"certain": 0}, "different": {"certain": 0}}}
        self.assertEqual(R.tier_chip("certain", doc)["values"]["n"], 10)
        del doc["gates"]["gate1_tier_precision"]["splits"]["test"]
        self.assertEqual(R.tier_chip("certain", doc)["key"], "chip.auto.unmeasured")

    def test_a_real_events_eval_document_is_readable(self):
        import test_events_eval as T
        items = {}
        for n in range(len(T.FAMILIES)):
            for it in T.family(n):
                items[it["id"]] = it
        gate = ev.gate_tiers(T.gold_for(6), items)
        doc = {"gates": {"gate1_tier_precision": gate}}
        for tier in ("certain", "probable"):
            chip = R.tier_chip(tier, doc)
            self.assertIn(chip["kind"], ("auto",))
            self.assertTrue(i18n.t(chip["key"], "fr", **chip["values"]))
        pub = R.quality_public(doc)
        self.assertTrue(pub["measured"])
        self.assertFalse(pub["chip"]["human_checked"])

    def test_keys_exist_in_both_catalogues(self):
        for doc in (None, tier_doc(certain=(80, 0), human=True), tier_doc(certain=(80, 0), human=False),
                    tier_doc(certain=(10, 0), human=True), tier_doc(provenance="?")):
            for tier in ("certain", "probable", "possible", None):
                chip = R.tier_chip(tier, doc)
                for lang in ("fr", "en"):
                    self.assertTrue(i18n.t(chip["key"], lang, **chip["values"]))

    def test_wilson_is_the_figure_events_eval_computes(self):
        for k, n in ((0, 0), (1, 1), (33, 34), (73, 73), (72, 72), (199, 200), (5, 90)):
            self.assertEqual(R.wilson(k, n), ev.wilson(k, n))


class QualityPublic(unittest.TestCase):
    def doc(self):
        d = tier_doc(certain=(40, 2), probable=(20, 8), human=False)
        g = d["gates"]["gate1_tier_precision"]
        g.update({"reason": "no gold set at C:\\Users\\someone\\vigie\\data\\eval\\gold_v0.json",
                  "pairs": 520, "resolved": 500, "verdict": "not_met", "gold_kappa": 0.89,
                  "certain_test": {"predicted": 42, "tp": 40, "precision": 0.9524, "precision_lower95": 0.84,
                                   "meets_lower_bound": False, "zero_error_pairs_needed": 73},
                  "splits": {"test": {"n": 400, "label_counts": {"same_event": 60, "related": 10, "different": 330},
                                      "tier_by_label": g["splits"]["test"]["tier_by_label"],
                                      "bands": {"certain": {"all": {"n": 400, "predicted": 42, "tp": 40,
                                                                    "precision_ci95": [0.84, 0.99]}}}}}})
        d["gates"]["gate2_publisher_text_leak"] = {
            "status": "measured", "verdict": "met", "reason": "see C:\\temp\\x", "files_scanned": 3,
            "categories": {"sealed": {"files_scanned": 3, "grams_matched": 0}}}
        d["gates"]["gate3_id_stability"] = {"status": "skipped", "verdict": "not_measurable",
                                            "reason": "no snapshot under /home/someone/data"}
        d["caveat"] = "free text with a Name"
        return d

    def test_counts_only(self):
        pub = R.quality_public(self.doc())
        text = json.dumps(pub, ensure_ascii=False, sort_keys=True)
        for forbidden in ("C:\\", "/home/", "someone", "language model", "labeller", "caveat", "reason", "Name",
                          "invented", "f0a"):
            self.assertNotIn(forbidden, text)
        allowed = re.compile(r"^[a-z0-9_.-]+$")
        values = []

        def walk(x):
            if isinstance(x, dict):
                for v in x.values():
                    walk(v)
            elif isinstance(x, list):
                for v in x:
                    walk(v)
            elif isinstance(x, str):
                values.append(x)
        walk(pub)
        self.assertTrue(all(allowed.match(v) for v in values), values)
        self.assertEqual(pub["gates"]["gate1_tier_precision"]["certain_test"]["tp"], 40)
        self.assertEqual(pub["gates"]["gate2_publisher_text_leak"]["verdict"], "met")
        self.assertEqual(pub["gates"]["gate3_id_stability"]["verdict"], "not_measurable")
        self.assertEqual(pub["chip"]["provenance"], "model")
        self.assertFalse(pub["chip"]["human_checked"])
        self.assertEqual((pub["chip"]["certain"]["pairs"], pub["chip"]["certain"]["same_event"]), (42, 40))
        self.assertEqual(pub["chip"]["certain_min_pairs"], 73)

    def test_absent_document_is_an_honest_not_measured(self):
        for doc in (None, {}, "x", {"gates": []}):
            pub = R.quality_public(doc)
            self.assertFalse(pub["measured"])
            self.assertEqual(pub["gates"], {})
            self.assertFalse(pub["chip"]["human_checked"])

    def test_deterministic_and_does_not_mutate(self):
        d = self.doc()
        before = json.dumps(d, sort_keys=True)
        a = json.dumps(R.quality_public(d), sort_keys=True)
        self.assertEqual(a, json.dumps(R.quality_public(copy.deepcopy(d)), sort_keys=True))
        self.assertEqual(json.dumps(d, sort_keys=True), before)

    def test_keys_that_could_carry_text_are_dropped(self):
        d = self.doc()
        d["gates"]["gate1_tier_precision"]["Some Publisher Title"] = 5
        d["gates"]["gate1_tier_precision"]["pairs_by_id"] = {"0123456789abcdef0123": {"a": 1}, "fr-en": {"a": 2}}
        text = json.dumps(R.quality_public(d))
        self.assertNotIn("Publisher", text)
        self.assertNotIn("0123456789abcdef0123", text)    # an id-shaped key never passes
        self.assertIn("fr-en", text)                      # a code-shaped one does


class NoPublisherTextAnywhere(unittest.TestCase):
    def test_rows_carry_codes_counts_and_instants_only(self):
        evs = plain_edition(5)
        evs[0]["members"][0]["url"] = "https://invented.example/an-article-slug-with-words"
        evs[0]["members"][0]["title"] = "An invented headline"
        text = json.dumps(R.rank_events(evs, ctx()))
        self.assertNotIn("invented", text)
        self.assertNotIn("headline", text)
        self.assertNotIn("https://", text)


if __name__ == "__main__":
    unittest.main()
