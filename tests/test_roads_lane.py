"""The hourly roads-only lane: real-time obstructions without a new edition.

House law under test: collection clocks and presence counters are not a change;
a changed declaration set re-renders and deploys; an unchanged one costs only
the conditional fetch. The lane never runs normalize/enrich/cluster, so the
edition, its diff and the durable history stay untouched.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import refresh


def _store(**over) -> dict:
    base = {
        "method": "wzdx-roadworks-v2",
        "fetched_at": "2026-09-19T00:00:00+00:00",
        "events": [
            {
                "event_id": "a", "event_type": "work-zone", "event_status": "active",
                "vehicle_impact": "some-lanes-closed", "direction": "both-directions",
                "start_date": "2026-09-10T04:00:00Z", "end_date": "2026-10-01T03:59:59Z",
                "description": "Réfection", "road_names": ["Rue A"], "restrictions": [],
                "update_date": "2026-09-18T00:00:00Z",
            }
        ],
    }
    base.update(over)
    return base


def _signal(doc: dict) -> str:
    path = Path(tempfile.mkdtemp(prefix="vigie-roads-")) / "store.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return refresh.roads_signal(path)


class RoadsSignal(unittest.TestCase):
    def test_collection_clock_and_history_are_not_a_change(self) -> None:
        first = _signal(_store())
        second = _signal(_store(
            fetched_at="2026-09-19T06:00:00+00:00",
            event_history={"method": "wzdx-event-history-v1", "events": {"a": {"collections_seen": 9}}},
        ))
        self.assertNotEqual(first, "")
        self.assertEqual(first, second)

    def test_a_relayed_field_change_changes_the_signal(self) -> None:
        before = _signal(_store())
        changed = _store()
        changed["events"][0]["description"] = "Fermeture complète"
        self.assertNotEqual(before, _signal(changed))

    def test_an_absent_or_corrupt_store_yields_no_signal(self) -> None:
        missing = Path(tempfile.mkdtemp(prefix="vigie-roads-")) / "nope.json"
        self.assertEqual(refresh.roads_signal(missing), "")


class RoadsOnlyLane(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        directory = Path(self._tmp.name)
        self._patches = [
            mock.patch.multiple(
                refresh,
                LOCK_PATH=directory / "refresh.lock",
                LOG_PATH=directory / "refresh.log",
                ROADS_SIGNAL_PATH=directory / "roads_signal.json",
            ),
            mock.patch.object(refresh, "acquire_lock", return_value=True),
        ]
        for patch in self._patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _run(self, before: str, after: str, vercel: str | None = "vercel"):
        calls: list[str] = []
        with mock.patch.object(refresh, "read_roads_signal", return_value=before), \
                mock.patch.object(refresh, "roads_signal", return_value=after), \
                mock.patch.object(refresh, "write_roads_signal") as written, \
                mock.patch.object(refresh, "run_step",
                                  side_effect=lambda name, *a, **k: calls.append(name)), \
                mock.patch.object(refresh.shutil, "which", return_value=vercel):
            code = refresh.main(["--roads-only"])
        return code, calls, written

    def test_unchanged_declarations_skip_render_and_deploy(self) -> None:
        code, calls, written = self._run("same", "same")
        self.assertEqual(code, 0)
        self.assertEqual(calls, ["wzdx"])
        written.assert_not_called()

    def test_changed_declarations_run_the_lane_and_deploy(self) -> None:
        code, calls, written = self._run("old", "new")
        self.assertEqual(code, 0)
        self.assertEqual(
            calls, ["wzdx", "anomalies", "edges", "render", "verify", "link", "deploy"])
        written.assert_called_once_with("new")

    def test_no_normalize_enrich_or_cluster_is_ever_run(self) -> None:
        _, calls, _ = self._run("old", "new")
        self.assertNotIn("pipeline", calls)
        self.assertNotIn("feed_health", calls)
        self.assertNotIn("cluster_issues", calls)

    def test_missing_vercel_fails_without_publishing(self) -> None:
        code, calls, written = self._run("old", "new", vercel=None)
        self.assertEqual(code, 1)
        self.assertIn("verify", calls)
        self.assertNotIn("deploy", calls)
        written.assert_not_called()


class IndexNowPing(unittest.TestCase):
    """Fast indexing is best-effort: never fatal, and the key is publicly served."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        patcher = mock.patch.object(refresh, "LOG_PATH", Path(self._tmp.name) / "refresh.log")
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_ping_posts_host_key_and_urls(self) -> None:
        captured: dict = {}

        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *args): return False

        def fake_urlopen(request, timeout=None):
            captured["url"] = request.full_url
            captured["data"] = json.loads(request.data.decode("utf-8"))
            return Response()

        with mock.patch.object(refresh.urllib.request, "urlopen", side_effect=fake_urlopen):
            refresh.ping_indexnow()
        self.assertEqual(captured["url"], refresh.INDEXNOW_ENDPOINT)
        self.assertEqual(captured["data"]["host"], "vigieqc.com")
        self.assertEqual(captured["data"]["key"], refresh.INDEXNOW_KEY)
        self.assertIn("https://vigieqc.com/", captured["data"]["urlList"])

    def test_ping_failure_is_not_fatal(self) -> None:
        with mock.patch.object(refresh.urllib.request, "urlopen", side_effect=OSError("down")):
            refresh.ping_indexnow()  # must not raise

    def test_key_file_is_published_from_the_public_tree(self) -> None:
        key_file = harness.ROOT / "public" / f"{refresh.INDEXNOW_KEY}.txt"
        self.assertEqual(key_file.read_text(encoding="utf-8").strip(), refresh.INDEXNOW_KEY)


if __name__ == "__main__":
    unittest.main()
