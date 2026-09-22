"""Aggressive debug session 2026-09-22: regression locks for every fix.

Each test pins one finding from the full-repo audit (bugs, logic errors,
contract drift, copy errors) so it cannot silently regress. Stdlib unittest.
"""
from __future__ import annotations

import json
import tarfile
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import ambient_pulse
import affiche
import change_ledger
import cluster_issues
import compile_watchdog
import dossier_history
import feed_health
import ingest_civic
import ingest_rss
import method_site
import promesse
import recits
import refresh
import registre
import resident_brief as brief
import state_pack
import store_io
import substrate
import verify

NOW = datetime(2026, 9, 22, 12, 0, tzinfo=timezone.utc)


class RoadsSignalDeterminism(unittest.TestCase):
    def test_key_order_is_not_a_change(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.json"
            second = Path(tmp) / "b.json"
            first.write_text(json.dumps({"events": [
                {"event_id": "A", "restrictions": {"b": 1, "a": 2}},
                {"event_id": "B", "road_names": ["x"]},
            ]}), encoding="utf-8")
            # Same logical content: reversed event order, reversed key order.
            second.write_text(json.dumps({"events": [
                {"event_id": "B", "road_names": ["x"]},
                {"event_id": "A", "restrictions": {"a": 2, "b": 1}},
            ]}), encoding="utf-8")
            self.assertEqual(refresh.roads_signal(first), refresh.roads_signal(second))
            self.assertTrue(refresh.roads_signal(first))

    def test_real_change_still_detected(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            first = Path(tmp) / "a.json"
            second = Path(tmp) / "b.json"
            first.write_text(json.dumps({"events": [{"event_id": "A"}]}), encoding="utf-8")
            second.write_text(json.dumps({"events": [{"event_id": "A", "event_status": "active"}]}), encoding="utf-8")
            self.assertNotEqual(refresh.roads_signal(first), refresh.roads_signal(second))


class RegistreHostileState(unittest.TestCase):
    def test_voice_row_coerces_mixed_ids(self) -> None:
        row = registre.voice_row({
            "edition": "e",
            "dossiers": [{"spoke": ["a", 123, None], "silent": ["b"]}],
            "followed": ["a", "b", 123],
        })
        self.assertEqual(row["spoke"], ["123", "a"])
        self.assertEqual(row["silent"], ["b"])

    def test_institution_register_survives_mixed_ids(self) -> None:
        state = {
            "seals": [],
            "voice": [{
                "edition": "2026-01-01T00:00:00+00:00",
                "spoke": ["a", 123, None], "silent": ["b", 456],
                "established": True,
            }],
            "names": {},
            "travaux": {"seals": []},
        }
        register = registre.institution_register(state)
        ids = {r["institution_id"] for r in register}
        self.assertTrue({"a", "b", "123", "456"} <= ids)
        self.assertTrue(all(isinstance(r["institution_id"], str) for r in register))

    def test_verify_chain_never_tracebacks(self) -> None:
        for seals in ([None], [{"record": "x"}], [{"record": {}, "prev": 123}],
                      [{"record": {}, "seq": 1, "prev": "", "leaf": 1, "root": 2}]):
            ok, _msg = registre.verify_chain(seals, "")
            self.assertFalse(ok)


class SilenceGlanceSpokePriority(unittest.TestCase):
    def test_spoke_anywhere_is_not_silent(self) -> None:
        issues = [
            {"tensions": [{"institution_name": "Ville de Québec"}],
             "silence": {"silent": [{"institution_name": "Le Soleil"}]}},
            {"tensions": [{"institution_name": "Le Soleil"}],
             "silence": {"silent": [{"institution_name": "Ville de Québec"}]}},
        ]
        status = {"ok": 12, "total": 12, "partial": False, "at": NOW.isoformat(), "stale": False}
        html = brief.digest_html([{"geo": "quebec-city"}], status, None, None,
                                 issues, NOW, has_changes=False)
        self.assertNotIn("n’ont pas parlé", html)

    def test_genuinely_quiet_is_still_counted(self) -> None:
        issues = [
            {"tensions": [{"institution_name": "Ville de Québec"}],
             "silence": {"silent": [{"institution_name": "Le Soleil"}]}},
        ]
        status = {"ok": 12, "total": 12, "partial": False, "at": NOW.isoformat(), "stale": False}
        html = brief.digest_html([{"geo": "quebec-city"}], status, None, None,
                                 issues, NOW, has_changes=False)
        self.assertIn("<strong>1</strong> institution suivie n’a pas parlé", html)


class AttributedHeadlineAttribution(unittest.TestCase):
    def _issue(self) -> dict:
        return {
            "issue_id": "aa11bb22cc33dd44",
            "question": "Un titre d’éditeur repris tel quel",
            "label_kind": "attributed_headline",
            "label_source": {"source_id": "le-soleil", "source_name": "Le Soleil",
                             "url": "https://www.lesoleil.com/x"},
            "geo_focus": ["quebec-city"],
            "source_count": 2,
            "tensions": [],
            "silence": {"silent": []},
        }

    def test_dossier_card_names_the_owner(self) -> None:
        html = brief.dossier_html(self._issue(), {})
        self.assertIn("Titre d’un éditeur, cité tel quel", html)
        self.assertIn("Le Soleil", html)

    def test_dossier_card_without_owner_still_marked(self) -> None:
        issue = self._issue()
        del issue["label_source"]
        html = brief.dossier_html(issue, {})
        self.assertIn("Titre d’un éditeur, cité tel quel", html)

    def test_subject_label_cards_unchanged(self) -> None:
        issue = self._issue()
        issue["label_kind"] = "subject_label"
        html = brief.dossier_html(issue, {})
        self.assertNotIn("Titre d’un éditeur", html)

    def test_recits_index_marks_attributed_labels(self) -> None:
        html = recits.render_index([self._issue()], {})
        self.assertIn("titre d’un éditeur, cité tel quel", html)


class TitleCaps(unittest.TestCase):
    def test_recits_voice_title_within_cap(self) -> None:
        tension = {"institution_name": "X", "source_kind": "media", "items": [
            {"url": "https://a.b/c", "title": "t" * 500, "source_name": "S"}]}
        html = recits._voice_block(tension)
        import re
        title = re.search(r"<a[^>]*>(.*?)<span", html).group(1)
        self.assertLessEqual(len(title), brief.TITLE_CAP)
        self.assertTrue(title.endswith("…"))

    def test_substrate_plain_never_exceeds_cap(self) -> None:
        self.assertLessEqual(len(substrate._plain("t" * 500, 300)), 300)
        self.assertLessEqual(len(substrate._plain("t" * 500, 120)), 120)
        self.assertEqual(substrate._plain("short", 300), "short")

    def test_civic_cap_never_exceeds_limit(self) -> None:
        self.assertLessEqual(len(ingest_civic._cap("t" * 500, 300)), 300)


class CivicOfflineCharset(unittest.TestCase):
    def test_offline_reuse_honours_archived_charset(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            raw_dir = Path(tmp) / "raw"
            src_dir = raw_dir / "civic-test"
            src_dir.mkdir(parents=True)
            body = "Consultation — été à Québec".encode("windows-1252")
            snap = src_dir / "20260922T120000Z_ab12cd34ef56.html"
            snap.write_bytes(body)
            snap.with_suffix(".json").write_text(json.dumps({
                "fetched_at": "2026-09-22T12:00:00+00:00",
                "content_type": "text/html; charset=windows-1252",
            }), encoding="utf-8")
            self.assertEqual(
                ingest_civic.snapshot_content_type(snap),
                "text/html; charset=windows-1252")
            self.assertIn("été", ingest_civic.decode_html(
                body, ingest_civic.snapshot_content_type(snap)))


class CutSourcesVisible(unittest.TestCase):
    CHANCELLERY = """version: 1
sources:
  - id: live-feed
    name: Live
    institution: live
    type: rss
    url: https://a.b/rss
    enabled: true
  - id: cut-feed
    name: Cut Paper
    institution: cut-paper
    type: rss
    url: https://c.d/rss
    enabled: false
    cut_reason: retrait à la demande de l'éditeur
    cut_at: 2026-09-22
"""

    def test_loader_finds_cut_sources(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sources.yaml"
            path.write_text(self.CHANCELLERY, encoding="utf-8")
            cuts = ingest_rss.load_cut_sources(path)
            self.assertEqual([c["id"] for c in cuts], ["cut-feed"])
            self.assertIn("retrait", cuts[0]["cut_reason"])

    def test_sources_page_lists_the_cut(self) -> None:
        import unittest.mock as mock
        with mock.patch.object(method_site.ingest_rss, "load_enabled_by_type", return_value=[]), \
                mock.patch.object(method_site, "_deferred_sources", return_value=[]), \
                mock.patch.object(method_site, "_rules_scalars", return_value={}), \
                mock.patch.object(method_site.ingest_rss, "load_cut_sources",
                                  return_value=[{"id": "cut-feed", "name": "Cut Paper",
                                                 "cut_reason": "retrait à la demande",
                                                 "cut_at": "2026-09-22"}]):
            html = method_site._sources_html()
        self.assertIn("Cut Paper", html)
        self.assertIn("retrait à la demande", html)
        self.assertIn("2026-09-22", html)


class StatePackDeterminism(unittest.TestCase):
    def test_members_are_normalized(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out1 = Path(tmp) / "a.tar.gz"
            out2 = Path(tmp) / "b.tar.gz"
            self.assertEqual(state_pack.pack(out1), state_pack.pack(out2))
            for out in (out1, out2):
                with tarfile.open(out, "r:gz") as tar:
                    for info in tar.getmembers():
                        self.assertEqual(info.mtime, 0)
                        self.assertEqual(info.uid, 0)
                        self.assertEqual(info.gid, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())


class DedupVerifiesBytes(unittest.TestCase):
    def test_wrong_previous_yields_correct_copy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prev = Path(tmp) / "prev.xml"
            prev.write_bytes(b"<old/>")
            target = Path(tmp) / "new.xml"
            store_io.write_bytes_dedup(target, b"<new/>", prev)
            self.assertEqual(target.read_bytes(), b"<new/>")
            try:
                self.assertNotEqual(target.stat().st_ino, prev.stat().st_ino)
            except (OSError, AttributeError):
                pass

    def test_identical_previous_links(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            prev = Path(tmp) / "prev.xml"
            prev.write_bytes(b"<same/>")
            target = Path(tmp) / "new.xml"
            try:
                store_io.write_bytes_dedup(target, b"<same/>", prev)
            except OSError:
                self.skipTest("filesystem refuses hard links")
            self.assertEqual(target.read_bytes(), b"<same/>")


class WatchdogRoadsDeploy(unittest.TestCase):
    def test_roads_ok_counts_as_deploy(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "refresh.log"
            log.write_text(
                "2026-09-22T10:00:00+00:00 START pipeline\n"
                "2026-09-22T10:05:00+00:00 FAIL pipeline failed (code 1)\n"
                "2026-09-22T11:00:00+00:00 OK roads production updated\n",
                encoding="utf-8")
            facts = compile_watchdog.read_refresh_log(log)
            self.assertEqual(facts["fails_since_ok"], 0)
            self.assertEqual(facts["last_deploy"], "2026-09-22T11:00:00+00:00")

    def test_fail_after_roads_ok_still_counts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / "refresh.log"
            log.write_text(
                "2026-09-22T11:00:00+00:00 OK roads production updated\n"
                "2026-09-22T12:00:00+00:00 FAIL pipeline failed (code 1)\n",
                encoding="utf-8")
            facts = compile_watchdog.read_refresh_log(log)
            self.assertEqual(facts["fails_since_ok"], 1)


class VerifyGuardSplit(unittest.TestCase):
    def test_precheck_ignores_current_store(self) -> None:
        # guard_offline_rebuild must only judge snapshot freshness; an empty
        # current store is the post-run guard's job, never a pre-run refusal.
        import unittest.mock as mock
        newest = datetime.now(timezone.utc)
        with mock.patch.object(verify, "_newest_raw_run", return_value=newest), \
                mock.patch.object(verify, "_candidate_count", return_value=0):
            verify.guard_offline_rebuild()  # must not raise
            with self.assertRaises(RuntimeError):
                verify.guard_rebuilt_edition()


class DiskBytesInodes(unittest.TestCase):
    def test_hardlink_counted_once(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "a.xml").write_bytes(b"x" * 1000)
            try:
                import os
                os.link(root / "a.xml", root / "b.xml")
            except OSError:
                self.skipTest("filesystem refuses hard links")
            self.assertEqual(feed_health._disk_bytes(root), 1000)


class PromesseHtmlDate(unittest.TestCase):
    def test_line_html_renders_time_element(self) -> None:
        from datetime import timedelta
        start = NOW - timedelta(days=4)
        entries = []
        for i, official in enumerate((0, 0, 1, 1)):
            entries.append({"ts": (start + timedelta(days=i)).isoformat(),
                            "sources": 2, "items": 3, "official": official})
        issue = {"issue_id": "x", "tracking": {
            "first_seen": entries[0]["ts"], "last_seen": entries[-1]["ts"],
            "editions_seen": 4, "editions_missed": 0, "timeline": entries}}
        inner = promesse.line_html(issue)
        self.assertIn("<time", inner)
        self.assertIn(">2026-09-20 12:00 UTC</time>", inner)
        self.assertNotIn("depuis le 2026-09-20T12:00:00", inner)
        # The plain-text contract is unchanged (test-locked elsewhere).
        self.assertIn(entries[2]["ts"], promesse.line_of(issue))

    def test_unanswered_line_has_no_time(self) -> None:
        from datetime import timedelta
        start = NOW - timedelta(days=2)
        entries = [{"ts": (start + timedelta(days=i)).isoformat(),
                    "sources": 2, "items": 3, "official": 0} for i in range(2)]
        issue = {"issue_id": "x", "tracking": {
            "first_seen": entries[0]["ts"], "last_seen": entries[-1]["ts"],
            "editions_seen": 2, "editions_missed": 0, "timeline": entries}}
        inner = promesse.line_html(issue)
        self.assertIn("Aucun document officiel", inner)
        self.assertNotIn("<time", inner)


class AfficheFallbackTitle(unittest.TestCase):
    def test_nonlocal_fallback_is_named(self) -> None:
        ranked = [{
            "id": "c1", "title": " linked wire story ", "summary": "x",
            "url": "https://a.b/c", "published_at": NOW.isoformat(),
            "source_id": "s", "source_name": "S", "language": "fr",
            "enrich": {"geo": {"geo": "linked"}, "topics": []},
        }]
        html = affiche.render_affiche(ranked, [], {}, {"seals": []}, NOW.isoformat(), run={})
        self.assertIn("aucun article local daté", html)


class TypographyUnification(unittest.TestCase):
    def test_human_surfaces_use_typographic_apostrophes(self) -> None:
        for scar in ("maelyne-levis", "marchand-immigration", "airport"):
            question = cluster_issues.neutral_question([], 2, scar)
            self.assertNotIn("'", question)
        for _slug, _file, title, _eyebrow, intro in method_site.PAGES:
            self.assertNotIn("'", intro)
            self.assertNotIn("'", title)
        self.assertNotIn("'", ambient_pulse.TRUST_NOTE)

    def test_morning_page_is_fr_ca(self) -> None:
        digest = ambient_pulse.build_digest([], [], clustered_at="", ranked_at=None,
                                            built_at=NOW.isoformat())
        html = ambient_pulse.render_morning_html(digest)
        self.assertIn('<html lang="fr-CA">', html)


class NoRegressions(unittest.TestCase):
    def test_change_ledger_and_history_still_pure(self) -> None:
        ledger = change_ledger.diff_editions(None, None)
        self.assertEqual(ledger["current_count"], 0)
        history = dossier_history.update_history(
            dossier_history.empty_history(), [], "2026-01-01T00:00:00+00:00")
        self.assertEqual(history["edition_count"], 1)


if __name__ == "__main__":
    unittest.main()
