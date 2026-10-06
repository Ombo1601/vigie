"""Anomaly beacon compile (anomaly-beacon-v1): fixed thresholds, honest claims.

House law under test: an anomaly is a measured collection fact, never a
prediction; the City's own revision direction is relayed literally; a first
collection never implies a comparison; the verdict is deterministic (no wall
clock) and fail-soft; the published catalogue in anomalies.md locks the code.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import harness  # noqa: F401 - puts scripts/ on sys.path

import compile_anomalies as ca

FETCHED = "2026-09-17T11:30:00+00:00"


def _event(eid, roads=("Boulevard Charest Est",), end="2026-10-01T03:59:59Z", **over) -> dict:
    ev = {
        "event_id": eid, "event_type": "work-zone", "event_status": "active",
        "vehicle_impact": "some-lanes-closed", "road_names": list(roads),
        "direction": "both-directions", "start_date": "2026-09-10T04:00:00Z",
        "end_date": end, "start_date_accuracy": None, "end_date_accuracy": None,
        "description": "Réfection", "update_date": None, "restrictions": [],
        "source_id": "wzdx-quebec", "active": True,
    }
    ev.update(over)
    return ev


def _store(events=None, diff=None, **over) -> dict:
    base = {
        "method": "wzdx-roadworks-v2", "status": "proposed", "source_id": "wzdx-quebec",
        "source_name": "Ville de Québec — Entraves (WZDX)", "institution_name": "Ville de Québec",
        "feed_url": "https://quebec.gewi.com/wzdx/pull", "dataset_url": "https://example.invalid",
        "license_note": "CC-BY 4.0", "fetched_at": FETCHED,
        "bbox": [-71.85, 46.50, -70.75, 47.25],
        "counts": {"features": 1, "parsed": 1, "active": 1},
        "events": [_event("EV-001")] if events is None else events,
        "diff": diff or {"has_previous": False, "new": [], "removed": [], "changed": [],
                         "new_count": 0, "removed_count": 0, "changed_count": 0},
    }
    base.update(over)
    return base


def _diff(new=(), changed=(), removed=()) -> dict:
    return {
        "has_previous": True,
        "new": [dict(e) for e in new], "changed": [dict(e) for e in changed],
        "removed": [dict(e) for e in removed],
        "new_count": len(new), "changed_count": len(changed), "removed_count": len(removed),
    }


def _rules(verdict) -> dict:
    return {a["rule_id"]: a for a in verdict["anomalies"]}


class PousseeDeclarations(unittest.TestCase):
    def test_fires_at_threshold(self):
        diff = _diff(new=[
            {"event_id": f"N-{n}", "road_names": ["Boulevard Laurier"]} for n in range(3)
        ])
        rules = _rules(ca.compile_verdict(_store(diff=diff)))
        self.assertIn("poussee-declarations", rules)
        row = rules["poussee-declarations"]
        self.assertEqual(row["count"], 3)
        self.assertEqual(row["street_display"], "Boulevard Laurier")
        self.assertIn("3 nouvelles entraves déclarées sur Boulevard Laurier", row["claim"])
        self.assertIn("cette même collecte", row["claim"])

    def test_below_threshold_is_silent(self):
        diff = _diff(new=[
            {"event_id": f"N-{n}", "road_names": ["Boulevard Laurier"]} for n in range(2)
        ])
        self.assertNotIn("poussee-declarations", _rules(ca.compile_verdict(_store(diff=diff))))

    def test_first_collection_never_implies_a_comparison(self):
        verdict = ca.compile_verdict(_store())
        self.assertEqual(verdict["anomalies"], [])

    def test_different_streets_do_not_pool(self):
        diff = _diff(new=[
            {"event_id": "N-1", "road_names": ["Boulevard Laurier"]},
            {"event_id": "N-2", "road_names": ["Boulevard Laurier"]},
            {"event_id": "N-3", "road_names": ["Rue St-Jean"]},
        ])
        self.assertNotIn("poussee-declarations", _rules(ca.compile_verdict(_store(diff=diff))))


class FinReportee(unittest.TestCase):
    def test_relays_the_city_own_revision(self):
        diff = _diff(changed=[
            {"event_id": "C-1", "road_names": ["Chemin Ste-Foy"], "fields": ["end_date"],
             "end_date_moved": "later"},
            {"event_id": "C-2", "road_names": ["Chemin Ste-Foy"], "fields": ["end_date"],
             "end_date_moved": "later"},
        ])
        rules = _rules(ca.compile_verdict(_store(diff=diff)))
        self.assertIn("fin-reportee", rules)
        self.assertIn("La Ville a reporté la fin déclarée de 2 entraves", rules["fin-reportee"]["claim"])

    def test_advanced_ends_are_not_postponements(self):
        diff = _diff(changed=[
            {"event_id": "C-1", "road_names": ["Chemin Ste-Foy"], "end_date_moved": "earlier"},
            {"event_id": "C-2", "road_names": ["Chemin Ste-Foy"], "end_date_moved": "earlier"},
        ])
        self.assertNotIn("fin-reportee", _rules(ca.compile_verdict(_store(diff=diff))))

    def test_directionless_changes_do_not_fire(self):
        diff = _diff(changed=[
            {"event_id": "C-1", "road_names": ["Chemin Ste-Foy"], "fields": ["description"]},
            {"event_id": "C-2", "road_names": ["Chemin Ste-Foy"], "fields": ["description"]},
        ])
        self.assertNotIn("fin-reportee", _rules(ca.compile_verdict(_store(diff=diff))))


class Concentration(unittest.TestCase):
    def _events(self, hot: int, cold_streets: int):
        events = [_event(f"H-{n:03d}", roads=("Boulevard René-Lévesque O",)) for n in range(hot)]
        events += [_event(f"C-{n:03d}", roads=(f"Rue Fictive {n}",)) for n in range(cold_streets)]
        return events

    def test_fires_above_count_and_ratio(self):
        verdict = ca.compile_verdict(_store(events=self._events(8, 20)))
        row = _rules(verdict)["concentration"]
        self.assertEqual(row["count"], 8)
        self.assertEqual(row["median"], 1)
        self.assertIn("médiane observée par rue sur le réseau : 1 (rapport 8)", row["claim"])

    def test_below_count_threshold_is_silent(self):
        verdict = ca.compile_verdict(_store(events=self._events(7, 20)))
        self.assertNotIn("concentration", _rules(verdict))

    def test_below_ratio_threshold_is_silent(self):
        # Median 2, hot street 8 → ratio 4.0 fires; median 3 → 8 < 12 silent.
        events = [_event(f"H-{n:03d}", roads=("Boulevard René-Lévesque O",)) for n in range(8)]
        events += [_event(f"A-{n:03d}", roads=("Rue Alpha",)) for n in range(3)]
        events += [_event(f"B-{n:03d}", roads=("Rue Beta",)) for n in range(3)]
        events += [_event(f"C-{n:03d}", roads=("Rue Gamma",)) for n in range(3)]
        self.assertNotIn("concentration", _rules(ca.compile_verdict(_store(events=events))))

    def test_french_decimal_comma(self):
        # 9 hot vs median 2 → ratio 4,5 with a comma, never a dot.
        events = [_event(f"H-{n:03d}", roads=("Boulevard René-Lévesque O",)) for n in range(9)]
        events += [_event(f"A-{n:03d}", roads=("Rue Alpha",)) for n in range(2)]
        events += [_event(f"B-{n:03d}", roads=("Rue Beta",)) for n in range(2)]
        row = _rules(ca.compile_verdict(_store(events=events)))["concentration"]
        self.assertIn("rapport 4,5", row["claim"])
        self.assertNotIn("4.5", row["claim"])


class FenetreDepassee(unittest.TestCase):
    def test_fires_on_declared_past_window(self):
        events = [
            _event(f"O-{n}", roads=("Rue St-Vallier O",), end="2026-09-01T00:00:00Z")
            for n in range(3)
        ]
        row = _rules(ca.compile_verdict(_store(events=events)))["fenetre-depassee"]
        self.assertEqual(row["count"], 3)
        self.assertIn("restent déclarées", row["claim"])
        self.assertIn("fenêtre officielle est dépassée", row["claim"])

    def test_below_threshold_is_silent(self):
        events = [
            _event(f"O-{n}", roads=("Rue St-Vallier O",), end="2026-09-01T00:00:00Z")
            for n in range(2)
        ]
        self.assertNotIn("fenetre-depassee", _rules(ca.compile_verdict(_store(events=events))))

    def test_future_and_unparseable_ends_never_fire(self):
        events = [
            _event("F-1", roads=("Rue X",), end="2027-01-01T00:00:00Z"),
            _event("F-2", roads=("Rue X",), end="garbage"),
            _event("F-3", roads=("Rue X",), end=None),
        ]
        self.assertNotIn("fenetre-depassee", _rules(ca.compile_verdict(_store(events=events))))

    def test_end_equal_to_collection_is_not_overdue(self):
        events = [_event(f"E-{n}", roads=("Rue X",), end=FETCHED) for n in range(3)]
        self.assertNotIn("fenetre-depassee", _rules(ca.compile_verdict(_store(events=events))))

    def test_unparseable_collection_stamp_skips_the_rule(self):
        events = [_event(f"O-{n}", roads=("Rue X",), end="2026-09-01T00:00:00Z") for n in range(3)]
        verdict = ca.compile_verdict(_store(events=events, fetched_at="garbage"))
        self.assertNotIn("fenetre-depassee", _rules(verdict))


class VerdictDiscipline(unittest.TestCase):
    def test_deterministic_bytes(self):
        store = _store(events=[_event(f"H-{n:03d}", roads=("Boulevard René-Lévesque O",)) for n in range(9)])
        a = json.dumps(ca.compile_verdict(store), ensure_ascii=False, indent=2, sort_keys=True)
        b = json.dumps(ca.compile_verdict(store), ensure_ascii=False, indent=2, sort_keys=True)
        self.assertEqual(a, b)

    def test_compiled_at_is_the_collection_stamp(self):
        self.assertEqual(ca.compile_verdict(_store())["compiled_at"], FETCHED)

    def test_sorted_by_rule_rank_then_count_then_key(self):
        diff = _diff(
            new=[{"event_id": f"N-{n}", "road_names": ["Boulevard Laurier"]} for n in range(3)],
            changed=[{"event_id": f"C-{n}", "road_names": ["Chemin Ste-Foy"],
                      "end_date_moved": "later"} for n in range(2)],
        )
        events = [_event(f"O-{n}", roads=("Rue St-Vallier O",), end="2026-09-01T00:00:00Z")
                  for n in range(3)]
        verdict = ca.compile_verdict(_store(events=events, diff=diff))
        order = [a["rule_id"] for a in verdict["anomalies"]]
        ranks = [ca.RULES[r]["rank"] for r in order]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(order[0], "poussee-declarations")

    def test_cap_keeps_count_exact(self):
        # 8 streets × overdue (rank 3) + concentration etc.; force > 6 rows.
        events = []
        for s in range(8):
            events += [_event(f"S{s}-{n}", roads=(f"Rue Test {s}",), end="2026-09-01T00:00:00Z")
                       for n in range(3)]
        verdict = ca.compile_verdict(_store(events=events))
        self.assertGreater(verdict["anomaly_count"], ca.ANOMALIES_CAP)
        self.assertEqual(len(verdict["anomalies"]), ca.ANOMALIES_CAP)

    def test_evidence_ids_sorted_and_capped(self):
        events = [_event(f"O-{n:02d}", roads=("Rue St-Vallier O",), end="2026-09-01T00:00:00Z")
                  for n in range(12)]
        row = _rules(ca.compile_verdict(_store(events=events)))["fenetre-depassee"]
        self.assertEqual(row["count"], 12)
        self.assertEqual(len(row["evidence_event_ids"]), ca.EVIDENCE_CAP)
        self.assertEqual(row["evidence_event_ids"], sorted(row["evidence_event_ids"]))

    def test_every_row_is_proposed_and_carries_its_rule(self):
        events = [_event(f"H-{n:03d}", roads=("Boulevard René-Lévesque O",)) for n in range(9)]
        for row in ca.compile_verdict(_store(events=events))["anomalies"]:
            self.assertEqual(row["status"], "proposed")
            self.assertIn(row["rule_id"], ca.RULES)
            self.assertEqual(row["rule_label"], ca.RULES[row["rule_id"]]["label"])

    def test_claims_never_conclude_a_resolution(self):
        diff = _diff(changed=[
            {"event_id": "C-1", "road_names": ["Chemin Ste-Foy"], "end_date_moved": "later"},
            {"event_id": "C-2", "road_names": ["Chemin Ste-Foy"], "end_date_moved": "later"},
        ])
        events = [_event(f"O-{n}", roads=("Rue St-Vallier O",), end="2026-09-01T00:00:00Z")
                  for n in range(3)]
        verdict = ca.compile_verdict(_store(events=events, diff=diff))
        for row in verdict["anomalies"]:
            for forbidden in ("résolu", "résolue", "terminé", "terminée", "fini", "prédit"):
                self.assertNotIn(forbidden, row["claim"].lower())

    def test_malformed_entries_are_skipped(self):
        diff = {"has_previous": True, "new": ["nope", {}, None], "changed": [], "removed": [],
                "new_count": 3, "changed_count": 0, "removed_count": 0}
        verdict = ca.compile_verdict(_store(events=["nope", {}], diff=diff))
        self.assertEqual(verdict["anomalies"], [])


class PublishedLawLockstep(unittest.TestCase):
    def test_code_catalogue_matches_anomalies_md(self):
        text = (harness.ROOT / "anomalies.md").read_text(encoding="utf-8")
        for rule_id, rule in ca.RULES.items():
            self.assertIn(rule_id, text)
            self.assertIn(rule["label"], text)
        self.assertIn(str(ca.RULES["poussee-declarations"]["min_count"]), text)
        self.assertIn(str(ca.RULES["fin-reportee"]["min_count"]), text)
        self.assertIn(str(ca.RULES["concentration"]["min_count"]), text)
        self.assertIn(str(ca.RULES["fenetre-depassee"]["min_count"]), text)
        self.assertIn("anomaly-beacon-v1", text)
        self.assertIn(str(ca.ANOMALIES_CAP), text)
        self.assertIn(str(ca.EVIDENCE_CAP), text)


class MainFailSoft(unittest.TestCase):
    def _run_main(self, root: Path) -> int:
        with patch.object(ca, "ROADWORKS", root / "data" / "roadworks" / "latest_roadworks.json"), \
                patch.object(ca, "OUT_PATH", root / "data" / "anomalies" / "latest_verdict.json"):
            return ca.main([])

    def test_missing_store_writes_empty_verdict_and_exits_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            self.assertEqual(self._run_main(root), 0)
            verdict = json.loads(
                (root / "data" / "anomalies" / "latest_verdict.json").read_text(encoding="utf-8"))
        self.assertEqual(verdict["method"], "anomaly-beacon-v1")
        self.assertFalse(verdict["has_store"])
        self.assertEqual(verdict["anomalies"], [])
        self.assertEqual(verdict["anomaly_count"], 0)

    def test_foreign_method_writes_empty_verdict(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store_dir = root / "data" / "roadworks"
            store_dir.mkdir(parents=True)
            (store_dir / "latest_roadworks.json").write_text(
                json.dumps({"method": "another-model", "events": []}), encoding="utf-8")
            self.assertEqual(self._run_main(root), 0)
            verdict = json.loads(
                (root / "data" / "anomalies" / "latest_verdict.json").read_text(encoding="utf-8"))
        self.assertFalse(verdict["has_store"])

    def test_full_compile_is_byte_identical_across_runs(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "data" / "roadworks").mkdir(parents=True)
            (root / "data" / "roadworks" / "latest_roadworks.json").write_text(
                json.dumps(_store(), ensure_ascii=False), encoding="utf-8")
            self.assertEqual(self._run_main(root), 0)
            first = (root / "data" / "anomalies" / "latest_verdict.json").read_bytes()
            self.assertEqual(self._run_main(root), 0)
            second = (root / "data" / "anomalies" / "latest_verdict.json").read_bytes()
        self.assertEqual(first, second)


if __name__ == "__main__":
    unittest.main()
