"""Aggressive debug session 2026-09-21: regression locks for every fix.

Each test pins one real bug found by the audit so it cannot silently return:
content-signal stability, feed-index budgeting, price-context precision,
chain-seq floors, hostile-chain verification, dossier dedupe, orphan pruning,
timestamp handling, chronological road ordering and atomic byte stores.
"""
from __future__ import annotations

import json
import re
import tempfile
import time
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import compile_anomalies
import dossier_history
import enrich
import fetch_brief_media
import normalize
import recits
import refresh
import registre
import resident_brief as brief
import store_io


def _roads_store(events, **over):
    doc = {
        "method": "wzdx-roadworks-v2",
        "fetched_at": "2026-09-19T00:00:00+00:00",
        "events": events,
    }
    doc.update(over)
    path = Path(tempfile.mkdtemp(prefix="vigie-dbg-")) / "store.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


def _event(eid="a", roads=("Rue A", "Rue B"), restrictions=("x", "y"), **over):
    base = {
        "event_id": eid, "event_type": "work-zone", "event_status": "active",
        "vehicle_impact": "some-lanes-closed", "direction": "both-directions",
        "start_date": "2026-09-10T04:00:00Z", "end_date": "2026-10-01T03:59:59Z",
        "description": "Réfection", "road_names": list(roads),
        "restrictions": list(restrictions),
        "update_date": "2026-09-18T00:00:00Z",
    }
    base.update(over)
    return base


class RoadsSignalStability(unittest.TestCase):
    def test_reordered_lists_are_not_a_change(self) -> None:
        first = refresh.roads_signal(_roads_store([_event()]))
        reordered = refresh.roads_signal(_roads_store([
            _event(roads=("Rue B", "Rue A"), restrictions=("y", "x")),
        ]))
        self.assertNotEqual(first, "")
        self.assertEqual(first, reordered)

    def test_signal_method_is_v2(self) -> None:
        self.assertEqual(refresh.ROADS_SIGNAL_METHOD, "roads-signal-v2")


class FeedIndexBudget(unittest.TestCase):
    def test_duplicate_urls_do_not_consume_the_cap(self) -> None:
        raw_dir = Path(tempfile.mkdtemp(prefix="vigie-dbg-"))
        src = raw_dir / "src"
        src.mkdir()
        items = "".join(
            '<item><title>t</title><link>https://ex.example/a</link>'
            '<enclosure url="https://ex.example/i.jpg" type="image/jpeg"/></item>'
            for _ in range(10)
        )
        (src / "20260919T000000Z_abc.xml").write_bytes(
            f"<rss><channel>{items}</channel></rss>".encode())
        index = fetch_brief_media.build_feed_media_index(raw_dir)
        self.assertEqual(len(index), 1)


class PricePrecision(unittest.TestCase):
    def test_context_stems_are_word_bounded(self) -> None:
        self.assertIsNone(enrich.PRICE_CTX_RE.search("les loyersX montent"))
        self.assertIsNone(enrich.PRICE_CTX_RE.search("tarifsX et factureY"))
        self.assertIsNotNone(enrich.PRICE_CTX_RE.search("les loyers montent"))
        self.assertIsNotNone(enrich.PRICE_CTX_RE.search("tarifs et factures"))

    def test_narrow_nbsp_thousands_parse(self) -> None:
        self.assertEqual(enrich._parse_number("1\u202f234"), 1234.0)
        self.assertEqual(enrich._parse_number("1\u00a0234"), 1234.0)
        m = re.search(enrich.RE_NUM, "hausse de 1\u202f234 $")
        self.assertIsNotNone(m)


class ChainSeqFloor(unittest.TestCase):
    def _record(self, edition):
        return {
            "method": registre.METHOD, "edition": edition, "followed": [],
            "dossiers": [], "ledger": {"has_previous": False, "new": [], "developed": [], "quiet": []},
        }

    def test_corrupt_last_seq_never_mints_a_duplicate(self) -> None:
        state = registre.empty_state()
        state, action = registre.seal_edition(state, self._record("2026-09-19T00:00:00+00:00"))
        self.assertEqual(action, "appended")
        state["seals"][-1]["seq"] = 0  # corrupt on disk
        state, action = registre.seal_edition(state, self._record("2026-09-19T06:00:00+00:00"))
        self.assertEqual(action, "appended")
        seqs = [s["seq"] for s in state["seals"]]
        self.assertEqual(len(set(seqs)), len(seqs))
        self.assertEqual(seqs[-1], 2)

    def test_deeply_nested_record_is_a_failure_not_a_crash(self) -> None:
        nested: dict = {}
        cursor = nested
        for _ in range(2000):
            cursor["x"] = {}
            cursor = cursor["x"]
        ok, msg = registre.verify_chain([{"seq": 1, "prev": "", "record": nested}])
        self.assertFalse(ok)
        self.assertIn("seal 1", msg)


class AnomalyGroupDisplay(unittest.TestCase):
    def test_empty_variants_render_a_placeholder(self) -> None:
        group = compile_anomalies._StreetGroup()
        self.assertEqual(group.display(), "voie non précisée")


class DossierDedupe(unittest.TestCase):
    def test_duplicate_issue_ids_yield_one_page_and_one_card(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="vigie-dbg-"))
        out_dir = tmp / "dossiers"
        out_index = tmp / "dossiers.html"
        issue = {
            "issue_id": "dup", "question": "Q ?", "label_kind": "subject_label",
            "geo_focus": ["quebec-city"], "source_count": 2, "item_count": 2,
            "official_voice_count": 0, "tensions": [], "silence": {"silent": []},
        }
        report = recits.emit([dict(issue), dict(issue)], [], {}, {}, {},
                             out_dir=out_dir, out_index=out_index)
        self.assertEqual(report["dossiers"], 1)
        self.assertEqual(report["pages"], 1)
        self.assertEqual(out_index.read_text(encoding="utf-8").count("recit-index-item"), 1)


class LatestTmpPrune(unittest.TestCase):
    def test_old_latest_tmps_are_pruned_fresh_ones_kept(self) -> None:
        out_dir = Path(tempfile.mkdtemp(prefix="vigie-dbg-"))
        old = out_dir / "latest_candidates.json.1.aaaaaaaa.tmp"
        fresh = out_dir / "latest_candidates.json.1.bbbbbbbb.tmp"
        old.write_text("x", encoding="utf-8")
        fresh.write_text("x", encoding="utf-8")
        ancient = time.time() - 2 * 24 * 3600
        import os

        os.utime(old, (ancient, ancient))
        removed = normalize.prune_candidate_history(out_dir)
        self.assertEqual(removed, 1)
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())


class TimestampHandling(unittest.TestCase):
    def test_zulu_and_offset_compare_chronologically(self) -> None:
        # 12:00-04:00 == 16:00Z, after 15:00Z despite sorting before as text.
        self.assertTrue(dossier_history._is_after(
            "2026-09-17T12:00:00-04:00", "2026-09-17T15:00:00Z"))
        h = dossier_history.empty_history()
        out = dossier_history.update_history(
            h, [{"issue_id": "a", "source_count": 2}], "2026-09-17T15:00:00Z")
        self.assertEqual(out["edition_count"], 1)


class RoadOrdering(unittest.TestCase):
    def test_mixed_offsets_sort_chronologically(self) -> None:
        early = {"event_id": "e", "update_date": "2026-09-17T15:00:00+00:00"}
        late = {"event_id": "l", "update_date": "2026-09-17T12:00:00-04:00"}
        self.assertGreater(brief._update_key(late), brief._update_key(early))
        broken = {"event_id": "b", "update_date": "not-a-date"}
        self.assertGreater(brief._update_key(early), brief._update_key(broken))


class AtomicByteStores(unittest.TestCase):
    def test_write_bytes_atomic_round_trips(self) -> None:
        path = Path(tempfile.mkdtemp(prefix="vigie-dbg-")) / "snap.bin"
        store_io.write_bytes_atomic(path, b"\x00\x01binary")
        self.assertEqual(path.read_bytes(), b"\x00\x01binary")
        self.assertEqual(list(path.parent.glob("*.tmp")), [])

    def test_write_bytes_dedup_creates_parent_and_links_or_copies(self) -> None:
        directory = Path(tempfile.mkdtemp(prefix="vigie-dbg-"))
        prev = directory / "a" / "prev.bin"
        prev.parent.mkdir(parents=True)
        prev.write_bytes(b"same")
        target = directory / "b" / "next.bin"
        store_io.write_bytes_dedup(target, b"same", prev)
        self.assertEqual(target.read_bytes(), b"same")


if __name__ == "__main__":
    unittest.main()
