"""Dossier history: durable multi-edition tracking, collection facts only.

An edition is one collection snapshot (normalized_at), not one pipeline run.
Absence counts as missed, never as resolved. History only moves forward.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import harness

import cluster_issues
import dossier_history
import resident_brief

TS1 = "2026-09-17T06:00:00+00:00"
TS2 = "2026-09-17T12:00:00+00:00"
TS3 = "2026-09-17T18:00:00+00:00"


def issue(iid: str, scar: str = "scar", sources: int = 2) -> dict:
    return {"issue_id": iid, "scar": scar, "source_count": sources}


def ts_series(n: int) -> list[str]:
    base = datetime(2026, 9, 1, tzinfo=timezone.utc)
    return [(base + timedelta(hours=6 * i)).isoformat() for i in range(n)]


class UpdateHistory(unittest.TestCase):
    def test_first_edition(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], TS1)
        rec = h["dossiers"]["a"]
        self.assertEqual(rec["first_seen"], TS1)
        self.assertEqual(rec["last_seen"], TS1)
        self.assertEqual(rec["editions_seen"], 1)
        self.assertEqual(rec["editions_missed"], 0)
        self.assertEqual(h["edition_count"], 1)
        self.assertEqual(h["updated_at"], TS1)

    def test_second_edition_keeps_first_seen(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], TS1)
        h = dossier_history.update_history(h, [issue("a")], TS2)
        rec = h["dossiers"]["a"]
        self.assertEqual(rec["first_seen"], TS1)
        self.assertEqual(rec["last_seen"], TS2)
        self.assertEqual(rec["editions_seen"], 2)
        self.assertEqual(rec["editions_missed"], 0)
        self.assertEqual(h["edition_count"], 2)

    def test_absence_counts_missed_not_resolved(self) -> None:
        h = dossier_history.update_history(
            dossier_history.empty_history(), [issue("a"), issue("b")], TS1
        )
        h = dossier_history.update_history(h, [issue("a")], TS2)
        rec = h["dossiers"]["b"]
        self.assertEqual(rec["editions_missed"], 1)
        self.assertEqual(rec["editions_seen"], 1)
        self.assertEqual(rec["last_seen"], TS1)
        self.assertNotIn("resolved", json.dumps(h))

    def test_return_after_missed_editions(self) -> None:
        h = dossier_history.update_history(
            dossier_history.empty_history(), [issue("a"), issue("b")], TS1
        )
        h = dossier_history.update_history(h, [issue("a")], TS2)
        h = dossier_history.update_history(h, [issue("a"), issue("b")], TS3)
        rec = h["dossiers"]["b"]
        self.assertEqual(rec["editions_seen"], 2)
        self.assertEqual(rec["editions_missed"], 1)
        self.assertEqual(rec["last_seen"], TS3)
        self.assertEqual(rec["first_seen"], TS1)

    def test_same_edition_is_idempotent(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], TS1)
        h2 = dossier_history.update_history(h, [issue("a")], TS1)
        self.assertEqual(h2["dossiers"]["a"]["editions_seen"], 1)
        self.assertEqual(h2["edition_count"], 1)

    def test_a_present_dossier_is_never_pruned_for_lifetime_absences(self) -> None:
        history = {
            "method": dossier_history.METHOD,
            "updated_at": TS1,
            "edition_count": 5,
            "dossiers": {
                "a": {"first_seen": TS1, "last_seen": TS1, "editions_seen": 1,
                      "editions_missed": dossier_history.MISSED_PRUNE + 5},
            },
        }
        h = dossier_history.update_history(history, [issue("a")], TS2)
        # A dossier present in this edition is active, not dormant: its lifetime
        # absence counter must not erase a dossier the reader can still see.
        self.assertIn("a", h["dossiers"])
        self.assertEqual(h["dossiers"]["a"]["editions_seen"], 2)

    def test_absent_streak_resets_on_presence_but_lifetime_misses_stay(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], TS1)
        h = dossier_history.update_history(h, [], TS2)
        self.assertEqual(h["dossiers"]["a"]["absent_streak"], 1)
        h = dossier_history.update_history(h, [issue("a")], TS3)
        self.assertEqual(h["dossiers"]["a"]["absent_streak"], 0)
        self.assertEqual(h["dossiers"]["a"]["editions_missed"], 1)

    def test_a_dormant_dossier_is_pruned_after_consecutive_absences(self) -> None:
        history = {
            "method": dossier_history.METHOD,
            "updated_at": TS1,
            "edition_count": 5,
            "dossiers": {
                "a": {"first_seen": TS1, "last_seen": TS1, "editions_seen": 1,
                      "editions_missed": dossier_history.MISSED_PRUNE,
                      "absent_streak": dossier_history.MISSED_PRUNE, "timeline": []},
            },
        }
        h = dossier_history.update_history(history, [], TS2)
        self.assertNotIn("a", h["dossiers"])

    def test_older_snapshot_ignored(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], TS2)
        h2 = dossier_history.update_history(h, [issue("a"), issue("b")], TS1)
        self.assertEqual(h2["dossiers"]["a"]["editions_seen"], 1)
        self.assertNotIn("b", h2["dossiers"])
        self.assertEqual(h2["edition_count"], 1)

    def test_empty_edition_ts_ignored(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], "")
        self.assertEqual(h["dossiers"], {})
        self.assertEqual(h["edition_count"], 0)

    def test_input_not_mutated(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], TS1)
        snapshot = json.dumps(h, sort_keys=True)
        dossier_history.update_history(h, [issue("a"), issue("b")], TS2)
        self.assertEqual(json.dumps(h, sort_keys=True), snapshot)

    def test_timeline_capped_and_latest(self) -> None:
        tss = ts_series(45)
        h = dossier_history.empty_history()
        for ts in tss:
            h = dossier_history.update_history(h, [issue("a")], ts)
        rec = h["dossiers"]["a"]
        self.assertEqual(rec["editions_seen"], 45)
        self.assertEqual(len(rec["timeline"]), dossier_history.TIMELINE_CAP)
        self.assertEqual(rec["timeline"][-1]["ts"], tss[-1])
        self.assertEqual(rec["timeline"][0]["ts"], tss[45 - dossier_history.TIMELINE_CAP])

    def test_dossier_cap_prunes_oldest(self) -> None:
        issues = [issue(f"a{i:03d}") for i in range(250)]
        h = dossier_history.update_history(dossier_history.empty_history(), issues, TS1)
        self.assertEqual(len(h["dossiers"]), dossier_history.DOSSIER_CAP)
        self.assertIn("a249", h["dossiers"])
        self.assertNotIn("a000", h["dossiers"])

    def test_long_missed_dossiers_pruned(self) -> None:
        tss = ts_series(dossier_history.MISSED_PRUNE + 1)
        h = dossier_history.update_history(
            dossier_history.empty_history(), [issue("gone"), issue("stay")], tss[0]
        )
        for ts in tss[1:]:
            h = dossier_history.update_history(h, [issue("stay")], ts)
        self.assertNotIn("gone", h["dossiers"])
        self.assertIn("stay", h["dossiers"])


class LoadHistory(unittest.TestCase):
    def test_missing_file_starts_empty(self) -> None:
        h = dossier_history.load_history(Path("/no/such/history.json"))
        self.assertEqual(h, dossier_history.empty_history())

    def test_corrupt_file_starts_empty(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "history.json"
            p.write_text("{not json", encoding="utf-8")
            self.assertEqual(dossier_history.load_history(p), dossier_history.empty_history())

    def test_foreign_method_never_mixed(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "history.json"
            p.write_text(
                json.dumps(
                    {
                        "method": "dossier-history-v0",
                        "updated_at": TS1,
                        "edition_count": 9,
                        "dossiers": {"a": {"editions_seen": 9}},
                    }
                ),
                encoding="utf-8",
            )
            h = dossier_history.load_history(p)
            self.assertEqual(h, dossier_history.empty_history())

    def test_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            p = Path(raw) / "history.json"
            h = dossier_history.update_history(
                dossier_history.empty_history(), [issue("a")], TS1
            )
            p.write_text(json.dumps(h, ensure_ascii=False), encoding="utf-8")
            loaded = dossier_history.load_history(p)
            self.assertEqual(loaded["dossiers"]["a"]["editions_seen"], 1)
            self.assertEqual(loaded["edition_count"], 1)
            self.assertEqual(loaded["updated_at"], TS1)


class TrackingOf(unittest.TestCase):
    def test_summary_fields(self) -> None:
        h = dossier_history.update_history(dossier_history.empty_history(), [issue("a")], TS1)
        h = dossier_history.update_history(h, [issue("b")], TS2)
        t = dossier_history.tracking_of(h, "a")
        self.assertEqual(t["status"], "proposed")
        self.assertEqual(t["method"], dossier_history.METHOD)
        self.assertEqual(t["first_seen"], TS1)
        self.assertEqual(t["last_seen"], TS1)
        self.assertEqual(t["editions_seen"], 1)
        self.assertEqual(t["editions_missed"], 1)

    def test_unknown_issue_returns_none(self) -> None:
        h = dossier_history.empty_history()
        self.assertIsNone(dossier_history.tracking_of(h, "nope"))


class ClusterHistoryMain(unittest.TestCase):
    def tearDown(self) -> None:
        cluster_issues.IN_PATH = harness.ROOT / "data" / "normalized" / "latest_enriched.json"
        cluster_issues.OUT_ISSUES = harness.ROOT / "data" / "issues" / "latest_issues.json"

    def _payload(self, normalized_at: str) -> dict:
        def item(sid: str, title: str) -> dict:
            return {
                "id": sid + title[:8],
                "title": title,
                "summary": "",
                "url": "https://example.test/" + sid,
                "source_id": sid,
                "source_name": sid,
                "language": "fr",
                "enrich": {"geo": {"geo": "quebec"}, "topics": [{"topic": "trade"}]},
            }

        return {
            "normalized_at": normalized_at,
            "candidates": [
                item("le-devoir", "Le Canada privatisera ses aéroports"),
                item("cbc-politics", "Canada to privatize airports"),
            ],
        }

    def _run(self, d: Path, normalized_at: str) -> dict:
        inp = d / "in.json"
        out = d / "out.json"
        inp.write_text(
            json.dumps(self._payload(normalized_at), ensure_ascii=False), encoding="utf-8"
        )
        cluster_issues.IN_PATH = inp
        cluster_issues.OUT_ISSUES = out
        cluster_issues.main()
        return json.loads(out.read_text(encoding="utf-8"))

    def test_two_editions_track_history(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            first = self._run(d, TS1)
            airport = next(i for i in first["issues"] if i["scar"] == "airport")
            self.assertEqual(airport["tracking"]["editions_seen"], 1)
            self.assertEqual(airport["tracking"]["status"], "proposed")
            self.assertEqual(first["dossier_history"]["edition_count"], 1)
            self.assertEqual(first["dossier_history"]["tracked_dossiers"], 1)
            history_path = d / "history.json"
            self.assertTrue(history_path.exists())

            second = self._run(d, TS2)
            airport2 = next(i for i in second["issues"] if i["scar"] == "airport")
            self.assertEqual(airport2["tracking"]["editions_seen"], 2)
            self.assertEqual(airport2["tracking"]["first_seen"], TS1)
            self.assertEqual(airport2["tracking"]["last_seen"], TS2)
            stored = json.loads(history_path.read_text(encoding="utf-8"))
            self.assertEqual(stored["edition_count"], 2)
            self.assertEqual(stored["method"], dossier_history.METHOD)

    def test_same_snapshot_rerun_does_not_inflate(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            self._run(d, TS1)
            again = self._run(d, TS1)
            airport = next(i for i in again["issues"] if i["scar"] == "airport")
            self.assertEqual(airport["tracking"]["editions_seen"], 1)
            self.assertEqual(again["dossier_history"]["edition_count"], 1)

    def test_history_lives_beside_out_issues(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            nested = d / "issues"
            nested.mkdir()
            self._run(nested, TS1)
            self.assertTrue((nested / "history.json").exists())


class RenderTracking(unittest.TestCase):
    def _issue(self, tracking: dict | None) -> dict:
        out = {
            "question": "Aéroports : le projet d’investissement privé",
            "geo_focus": ["quebec"],
            "source_count": 2,
            "tensions": [],
        }
        if tracking is not None:
            out["tracking"] = tracking
        return out

    def test_without_tracking_no_line(self) -> None:
        html = resident_brief.dossier_html(self._issue(None), {})
        self.assertNotIn("dossier-tracking", html)

    def test_first_edition_line(self) -> None:
        html = resident_brief.dossier_html(
            self._issue({"editions_seen": 1, "editions_missed": 0, "first_seen": TS1}), {}
        )
        self.assertIn("dossier-tracking", html)
        self.assertIn("Suivi depuis cette édition", html)

    def test_multi_edition_shows_count_and_first_seen(self) -> None:
        html = resident_brief.dossier_html(
            self._issue({"editions_seen": 3, "editions_missed": 0, "first_seen": TS1}), {}
        )
        self.assertIn("3 éditions collectées", html)
        self.assertIn("Suivi depuis", html)
        self.assertIn("<time", html)

    def test_missed_states_absence_not_resolution(self) -> None:
        html = resident_brief.dossier_html(
            self._issue({"editions_seen": 2, "editions_missed": 1, "first_seen": TS1}), {}
        )
        self.assertIn("Absent de 1 édition", html)
        self.assertIn("pas une résolution", html)

    def test_missed_plural(self) -> None:
        html = resident_brief.dossier_html(
            self._issue({"editions_seen": 2, "editions_missed": 3, "first_seen": TS1}), {}
        )
        self.assertIn("Absent de 3 éditions", html)

    def test_zero_seen_renders_nothing(self) -> None:
        html = resident_brief.dossier_html(self._issue({"editions_seen": 0}), {})
        self.assertNotIn("dossier-tracking", html)


class QuestionRevisions(unittest.TestCase):
    """Field-level revisions: the question is stored only when it changes."""

    def _issue(self, question: str) -> dict:
        return {"issue_id": "a", "scar": "scar", "source_count": 2, "question": question}

    def test_initial_question_then_only_real_changes(self) -> None:
        h = dossier_history.update_history(
            dossier_history.empty_history(), [self._issue("Q initiale")], TS1)
        self.assertEqual(h["dossiers"]["a"]["timeline"][-1]["question"], "Q initiale")
        h = dossier_history.update_history(h, [self._issue("Q initiale")], TS2)
        self.assertNotIn("question", h["dossiers"]["a"]["timeline"][-1])
        h = dossier_history.update_history(h, [self._issue("Q reformulée")], TS3)
        self.assertEqual(h["dossiers"]["a"]["timeline"][-1]["question"], "Q reformulée")

    def test_tracking_exposes_the_question(self) -> None:
        h = dossier_history.update_history(
            dossier_history.empty_history(), [self._issue("Q initiale")], TS1)
        timeline = dossier_history.tracking_of(h, "a")["timeline"]
        self.assertEqual(timeline[0]["question"], "Q initiale")


if __name__ == "__main__":
    unittest.main()
