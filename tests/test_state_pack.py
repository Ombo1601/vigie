"""Cross-edition state packing (state_pack) — the memory that travels to CI.

House law under test: only the non-regenerable inputs travel; the public repo
never carries publisher content, so the archive is private and plain-files-only;
an archive can never write outside the repo; the same inputs list the same
members.
"""
from __future__ import annotations

import io
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import state_pack


class CuratedMembers(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        data = self.root / "data"
        (data / "raw" / "sourceA").mkdir(parents=True)
        (data / "raw" / "sourceA" / "20260101T000000Z_aaaa.xml").write_bytes(b"old")
        (data / "raw" / "sourceA" / "20260101T000000Z_aaaa.json").write_text("{}", encoding="utf-8")
        (data / "raw" / "sourceA" / "20260102T000000Z_bbbb.xml").write_bytes(b"new")
        (data / "raw" / "sourceA" / "20260102T000000Z_bbbb.json").write_text("{}", encoding="utf-8")
        (data / "raw" / "_run_20260101T000000Z.json").write_text("{}", encoding="utf-8")
        (data / "raw" / "_run_20260102T000000Z.json").write_text("{}", encoding="utf-8")
        (data / "raw" / "_ua_policy.json").write_text("{}", encoding="utf-8")
        (data / "raw" / "_bodies").mkdir()
        (data / "raw" / "_bodies" / "abc.body").write_bytes(b"body")
        (data / "issues").mkdir()
        (data / "issues" / "latest_issues.json").write_text("{}", encoding="utf-8")
        (data / "issues" / "history.json").write_text("{}", encoding="utf-8")
        (data / "issues" / "20260101T000000Z_issues.json").write_text("{}", encoding="utf-8")
        (data / "roadworks").mkdir()
        (data / "roadworks" / "latest_roadworks.json").write_text("{}", encoding="utf-8")
        (data / "ops").mkdir()
        (data / "ops" / "feed_health.json").write_text("{}", encoding="utf-8")
        (data / "media" / "brief").mkdir(parents=True)
        (data / "media" / "brief" / ("a" * 20 + ".jpg")).write_bytes(b"img")
        (data / "media" / "brief_manifest.json").write_text("{}", encoding="utf-8")
        self.data_patch = mock.patch.multiple(state_pack, ROOT=self.root, DATA=self.root / "data")
        self.data_patch.start()
        self.addCleanup(self.data_patch.stop)

    def names(self) -> list[str]:
        return [p.relative_to(self.root).as_posix() for p in state_pack.members()]

    def test_keeps_newest_snapshot_and_drops_the_older_one(self) -> None:
        names = self.names()
        self.assertIn("data/raw/sourceA/20260102T000000Z_bbbb.xml", names)
        self.assertIn("data/raw/sourceA/20260102T000000Z_bbbb.json", names)
        self.assertNotIn("data/raw/sourceA/20260101T000000Z_aaaa.xml", names)
        self.assertNotIn("data/raw/sourceA/20260101T000000Z_aaaa.json", names)

    def test_keeps_memory_ledgers_media_and_cache(self) -> None:
        names = self.names()
        for needle in (
            "data/raw/_run_20260102T000000Z.json",
            "data/raw/_ua_policy.json",
            "data/raw/_bodies/abc.body",
            "data/issues/latest_issues.json",
            "data/issues/history.json",
            "data/roadworks/latest_roadworks.json",
            "data/ops/feed_health.json",
            "data/media/brief_manifest.json",
        ):
            self.assertIn(needle, names)
        self.assertNotIn("data/raw/_run_20260101T000000Z.json", names)
        self.assertNotIn("data/issues/20260101T000000Z_issues.json", names)

    def test_includes_the_latest_rendered_inputs_for_the_roads_lane(self) -> None:
        for name in (
            "normalized/latest_candidates.json",
            "normalized/latest_enriched.json",
            "normalized/latest_ranked.json",
        ):
            self.assertIn(name, state_pack.EXPLICIT)

    def test_round_trip_restores_the_same_bytes(self) -> None:
        archive = self.root / "state.tar.gz"
        self.assertGreater(state_pack.pack(archive), 0)
        original = (self.root / "data" / "issues" / "history.json").read_bytes()
        target = Path(tempfile.mkdtemp(prefix="vigie-state-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        with mock.patch.multiple(state_pack, ROOT=target, DATA=target / "data"):
            self.assertGreater(state_pack.unpack(archive), 0)
            restored = target / "data" / "issues" / "history.json"
            self.assertEqual(restored.read_bytes(), original)

    def test_pack_is_ordered_and_repeatable(self) -> None:
        self.assertEqual(self.names(), self.names())

    def test_unpack_refuses_traversal(self) -> None:
        evil = self.root / "evil.tar.gz"
        with tarfile.open(evil, "w:gz") as tar:
            payload = b"x"
            info = tarfile.TarInfo("../escaped.txt")
            info.size = len(payload)
            tar.addfile(info, io.BytesIO(payload))
        target = Path(tempfile.mkdtemp(prefix="vigie-state-"))
        self.addCleanup(lambda: __import__("shutil").rmtree(target, ignore_errors=True))
        with mock.patch.multiple(state_pack, ROOT=target, DATA=target / "data"):
            with self.assertRaises(ValueError):
                state_pack.unpack(evil)


if __name__ == "__main__":
    unittest.main()
