"""Legal remediations (LEGAL_RISK.md): attribution, identity, search index,
retention and the public legal page - locked as law.

R1: the author name the publisher's own feed gives is parsed, normalized and
    rendered as a byline; a bare e-mail address is never published.
R2: (caption rendering locked in test_brief_media / test_media_sentinel.)
R3: legal.md exists, is staged with the methods and linked from the footer.
R4: the DOM search index carries exactly what the reader sees - never the
    fuller internal summary.
R5: the honest reader identity is the default; only a transport-level stall
    (never an HTTP refusal, never a guard rejection) switches a host to the
    disclosed browser identity, and the switch is remembered.
R6: raw snapshots older than the retention window are pruned; the newest
    snapshot of every source always survives so offline rebuilds work.
"""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
import urllib.error
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import harness  # noqa: F401 - puts scripts/ on sys.path

import ingest_rss
import normalize
import resident_brief as brief
import stage_public

from test_resident_brief import NOW, collection, story

URL = "https://news.example/feed"
RSS = b'<?xml version="1.0"?><rss version="2.0"><channel><item><title>t</title><link>https://news.example/a</link></item></channel></rss>'


def response(body: bytes, headers: dict | None = None):
    resp = mock.MagicMock()
    resp.headers = headers or {}
    resp.read.return_value = body
    resp.__enter__.return_value = resp
    return resp


class AuthorAttribution(unittest.TestCase):
    """R1: s. 29.2 requires source AND author when the source gives one."""

    def test_rss_dc_creator_and_mrss_credit_are_captured(self) -> None:
        xml = b'''<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0" xmlns:dc="http://purl.org/dc/elements/1.1/"
     xmlns:media="http://search.yahoo.com/mrss/"><channel>
<item><title>a</title><link>https://news.example/a</link>
<dc:creator>Marie Plourde</dc:creator>
<media:credit>Simon S\xc3\xa9guin-Bertrand</media:credit></item>
</channel></rss>'''
        item = ingest_rss.parse_feed(xml, URL)[0]
        self.assertEqual(item["author"], "Marie Plourde")
        self.assertEqual(item["credit"], "Simon Séguin-Bertrand")

    def test_rss_author_email_form_yields_the_person_name(self) -> None:
        xml = b'''<rss version="2.0"><channel>
<item><title>b</title><link>https://news.example/b</link>
<author>jose@example.com (Jos\xc3\xa9 Dupont)</author></item>
</channel></rss>'''
        self.assertEqual(ingest_rss.parse_feed(xml, URL)[0]["author"], "José Dupont")

    def test_bare_email_is_never_published(self) -> None:
        xml = b'''<rss version="2.0"><channel>
<item><title>c</title><link>https://news.example/c</link>
<author>someone@example.com</author></item>
</channel></rss>'''
        self.assertIsNone(ingest_rss.parse_feed(xml, URL)[0]["author"])

    def test_atom_author_name_is_captured(self) -> None:
        xml = '''<feed xmlns="http://www.w3.org/2005/Atom"><entry>
<title>t</title><link rel="alternate" href="https://news.example/x"/>
<updated>2026-09-16T10:00:00Z</updated>
<author><name>François Roy</name><email>f@example.com</email></author>
</entry></feed>'''.encode("utf-8")
        self.assertEqual(ingest_rss.parse_feed(xml)[0]["author"], "François Roy")

    def test_person_fields_are_bounded(self) -> None:
        self.assertEqual(len(ingest_rss._clean_person("x" * 500)), 120)
        self.assertIsNone(ingest_rss._clean_person("   "))
        self.assertIsNone(ingest_rss._clean_person(None))

    def test_normalize_carries_author_and_photo_credit(self) -> None:
        item = normalize.normalize_item(
            {"title": "t", "author": "Marie Plourde", "credit": "Simon Séguin-Bertrand"},
            {"source_id": "a", "source_kind": "media", "institution": "publisher"})
        self.assertEqual(item["author"], "Marie Plourde")
        self.assertEqual(item["photo_credit"], "Simon Séguin-Bertrand")

    def test_byline_renders_the_author_when_the_feed_gives_one(self) -> None:
        rows, _ = brief.prepare_items([story(author="Marie Plourde")], NOW)
        html = brief.article_html(rows[0], 1, [])
        self.assertIn('Par Marie Plourde<span aria-hidden="true"> · </span>Source locale', html)

    def test_no_author_no_invented_byline(self) -> None:
        rows, _ = brief.prepare_items([story()], NOW)
        html = brief.article_html(rows[0], 1, [])
        self.assertNotIn("Par ", html)
        self.assertIn('<p class="byline">Source locale', html)

    def test_hostile_author_never_renders_markup(self) -> None:
        rows, _ = brief.prepare_items([story(author='<script>alert("x")</script>')], NOW)
        html = brief.article_html(rows[0], 1, [])
        self.assertNotIn("<script>", html)


class SearchIndexHonesty(unittest.TestCase):
    """R4: data-search carries the displayed excerpt, never the full summary."""

    def test_data_search_stops_at_the_displayed_excerpt(self) -> None:
        tail = "SENTINELBEYONDTHEEXCERPT"
        summary = ("mot " * 200) + tail
        rows, _ = brief.prepare_items([story(summary=summary)], NOW)
        html = brief.article_html(rows[0], 1, [])
        search = html.split('data-search="', 1)[1].split('"', 1)[0]
        # data-search is folded (lowercase, accents stripped) like the rest of
        # the search index; assert the literal, not a re-fold of the input.
        self.assertIn("travaux dans le quartier saint-roch a quebec", search)
        self.assertNotIn("sentinelbeyondtheexcerpt", search)
        self.assertNotIn(tail, html.split("<p class=\"excerpt\">", 1)[1].split("</p>", 1)[0])
        self.assertLessEqual(len(search), 400)

    def test_ligatures_and_curly_apostrophes_fold_like_the_client(self) -> None:
        self.assertEqual(brief.folded("L’œuvre théâtrale"), "l'oeuvre theatrale")
        rows, _ = brief.prepare_items([story(summary="Une œuvre attendue")], NOW)
        html = brief.article_html(rows[0], 1, [])
        search = html.split('data-search="', 1)[1].split('"', 1)[0]
        self.assertIn("oeuvre", search)


class IdentityLaw(unittest.TestCase):
    """R5/R9: honest identity by default; stalls switch, refusals never do."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raw = Path(self._tmp.name) / "raw"
        self.raw.mkdir()
        self.policy = self.raw / "_ua_policy.json"
        for name, value in (("RAW_DIR", self.raw), ("RETRIES", 3),
                            ("UA_POLICY_PATH", self.policy)):
            patcher = mock.patch.object(ingest_rss, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetch(self, opener, url=URL):
        with mock.patch.object(ingest_rss, "public_http_url", side_effect=lambda u, resolve=False: u), \
                mock.patch.object(ingest_rss.urllib.request, "build_opener", return_value=opener):
            return ingest_rss.fetch_bytes(url)

    def requests_of(self, opener):
        return [call.args[0] for call in opener.open.call_args_list]

    def test_honest_identity_is_the_default_and_discloses_the_legal_page(self) -> None:
        opener = mock.Mock()
        opener.open.return_value = response(RSS, {})
        self.fetch(opener)
        req = self.requests_of(opener)[0]
        self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)
        self.assertIn("vigieqc.com/methode/legal.html", ingest_rss.USER_AGENT)
        self.assertIn("non-commercial", ingest_rss.USER_AGENT)

    def test_transport_stall_marks_the_host_and_retries_under_browser_identity(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = [ConnectionResetError("connection reset"), response(RSS, {})]
        body, _ = self.fetch(opener)
        self.assertEqual(body, RSS)
        second = self.requests_of(opener)[1]
        self.assertEqual(second.get_header("User-agent"), ingest_rss.FALLBACK_USER_AGENT)
        policy = json.loads(self.policy.read_text(encoding="utf-8"))
        self.assertEqual(policy["news.example"]["identity"], "browser")
        self.assertEqual(policy["news.example"]["reason"], "ConnectionResetError")
        # the switch is remembered: the next run starts under the browser identity
        self.assertEqual(ingest_rss.choose_user_agent(URL), ingest_rss.FALLBACK_USER_AGENT)
        self.assertEqual(ingest_rss.choose_user_agent("https://other.example/feed"),
                         ingest_rss.USER_AGENT)

    def test_local_dns_failure_is_never_blamed_on_the_origin(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError(
            socket.gaierror(-2, "Name or service not known"))
        with self.assertRaises(urllib.error.URLError):
            self.fetch(opener)
        self.assertFalse(self.policy.exists())
        for req in self.requests_of(opener):
            self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)

    def test_a_stale_marker_is_reprobed_under_the_honest_identity(self) -> None:
        self.policy.write_text(json.dumps({"news.example": {
            "identity": "browser", "reason": "TimeoutError",
            "marked_at": "2025-01-01T00:00:00+00:00"}}), encoding="utf-8")
        self.assertEqual(ingest_rss.choose_user_agent(URL), ingest_rss.USER_AGENT)

    def test_http_refusal_is_respected_and_never_identity_switched(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError(URL, 403, "Forbidden", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            self.fetch(opener)
        self.assertFalse(self.policy.exists())
        for req in self.requests_of(opener):
            self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)
        # a refusal is final: it is not retried under the same identity either
        self.assertEqual(len(self.requests_of(opener)), 1)

    def test_conditional_refusal_still_tries_one_plain_request(self) -> None:
        # 412 (or a proxy that dislikes validators) is the validator's fault,
        # not the origin refusing the article: fall through to the plain GET.
        opener = mock.Mock()
        opener.open.side_effect = [
            urllib.error.HTTPError(URL, 412, "Precondition Failed", {}, None),
            response(RSS, {}),
        ]
        cache = {URL: {"etag": "e1", "last_modified": None,
                       "content_type": "application/rss+xml", "updated_at": "x"}}
        with mock.patch.object(ingest_rss, "_load_http_cache", return_value=cache):
            body, _ = self.fetch(opener)
        self.assertEqual(body, RSS)
        reqs = self.requests_of(opener)
        self.assertEqual(len(reqs), 2)
        self.assertEqual(reqs[0].get_header("If-none-match"), "e1")
        self.assertIsNone(reqs[1].get_header("If-none-match"))

    def test_guard_rejection_never_identity_switches(self) -> None:
        opener = mock.Mock()
        # Not via self.fetch: its pass-through public_http_url patch would
        # shadow the guard rejection under test.
        with mock.patch.object(ingest_rss, "public_http_url",
                               side_effect=ValueError("private address")), \
                mock.patch.object(ingest_rss.urllib.request, "build_opener",
                                  return_value=opener):
            with self.assertRaises(ValueError):
                ingest_rss.fetch_bytes(URL)
        opener.open.assert_not_called()
        self.assertFalse(self.policy.exists())

    def test_corrupt_policy_degrades_to_the_honest_identity(self) -> None:
        self.policy.write_text("garbage", encoding="utf-8")
        self.assertEqual(ingest_rss.choose_user_agent(URL), ingest_rss.USER_AGENT)


class Retention(unittest.TestCase):
    """R6: raw snapshots live at most RETENTION_DAYS; newest always survives."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raw = Path(self._tmp.name) / "raw"
        self.raw.mkdir()
        self.now = datetime(2026, 9, 19, 12, tzinfo=timezone.utc)

    def touch(self, rel: str) -> Path:
        p = self.raw / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("{}", encoding="utf-8")
        return p

    def test_old_snapshots_are_pruned_and_newest_survives(self) -> None:
        self.touch("local/20260101T000000Z.json")
        keep_src = self.touch("local/20260918T060000Z.json")
        self.touch("_run_20260101T000000Z.json")
        keep_run = self.touch("_run_20260918T060000Z.json")
        removed = ingest_rss.prune_raw_snapshots(self.raw, now=self.now)
        self.assertEqual(removed, 2)
        self.assertTrue(keep_src.exists())
        self.assertTrue(keep_run.exists())

    def test_the_newest_snapshot_survives_even_when_every_copy_is_old(self) -> None:
        only = self.touch("quiet/20260101T000000Z.json")
        self.assertEqual(ingest_rss.prune_raw_snapshots(self.raw, now=self.now), 0)
        self.assertTrue(only.exists())

    def test_an_old_snapshot_pair_is_pruned_only_when_a_newer_pair_exists(self) -> None:
        old_xml = self.touch("quiet/20260101T000000Z_abcd.xml")
        old_meta = self.touch("quiet/20260101T000000Z_abcd.json")
        new_xml = self.touch("quiet/20260918T060000Z_ef01.xml")
        new_meta = self.touch("quiet/20260918T060000Z_ef01.json")
        removed = ingest_rss.prune_raw_snapshots(self.raw, now=self.now)
        self.assertEqual(removed, 2)
        self.assertFalse(old_xml.exists())
        self.assertFalse(old_meta.exists())
        self.assertTrue(new_xml.exists())
        self.assertTrue(new_meta.exists())

    def test_the_newest_pair_survives_for_a_quiet_source(self) -> None:
        xml = self.touch("quiet/20260101T000000Z_abcd.xml")
        meta = self.touch("quiet/20260101T000000Z_abcd.json")
        self.assertEqual(ingest_rss.prune_raw_snapshots(self.raw, now=self.now), 0)
        self.assertTrue(xml.exists())
        self.assertTrue(meta.exists())

    def test_infrastructure_files_are_never_pruned(self) -> None:
        cache = self.touch("_http_cache.json")
        policy = self.touch("_ua_policy.json")
        body = self.touch("_bodies/ab" + "0" * 30 + ".body")
        self.touch("local/20260101T000000Z.json")
        self.touch("local/20260918T060000Z.json")
        ingest_rss.prune_raw_snapshots(self.raw, now=self.now)
        self.assertTrue(cache.exists())
        self.assertTrue(policy.exists())
        self.assertTrue(body.exists())

    def test_missing_directory_is_a_no_op(self) -> None:
        self.assertEqual(
            ingest_rss.prune_raw_snapshots(self.raw / "nope", now=self.now), 0)

    def test_retention_window_is_thirty_days(self) -> None:
        self.assertEqual(ingest_rss.RETENTION_DAYS, 30)


class PublicLegalPage(unittest.TestCase):
    """R3: the legal page exists, ships with the edition and is linked."""

    def test_legal_md_exists_and_is_staged_with_the_methods(self) -> None:
        self.assertIn("legal.md", stage_public.METHODS)
        self.assertTrue((brief.ROOT / "legal.md").is_file())

    def test_footer_links_the_legal_page(self) -> None:
        page = brief.render_brief([story()], NOW.isoformat(), [], collection(), media={})
        self.assertIn('<a class="legal-link" href="/methode/legal.html">', page)

    def test_legal_page_discloses_both_identities_and_retention(self) -> None:
        text = (brief.ROOT / "legal.md").read_text(encoding="utf-8")
        self.assertIn(ingest_rss.USER_AGENT, text)
        self.assertIn(ingest_rss.FALLBACK_USER_AGENT, text)
        self.assertIn("30", text)


if __name__ == "__main__":
    unittest.main()
