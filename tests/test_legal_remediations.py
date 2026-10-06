"""Legal remediations (LEGAL_RISK.md): attribution, identity, search index,
retention and the public legal page - locked as law.

R1: the author name the publisher's own feed gives is parsed, normalized and
    rendered as a byline; a bare e-mail address is never published.
R2: (caption rendering locked in test_brief_media / test_media_sentinel.)
R3: legal.md exists, is staged with the methods and linked from the footer.
R4: the DOM search index carries exactly what the reader sees - never the
    fuller internal summary.
R5: the honest reader identity is the only identity - no browser identity,
    no Referer; a host that cannot be read honestly is a recorded gap.
R9: a 4xx refusal ends the fetch (no retry, no alternate URL); article pages
    and images are fetched only when the host's robots.txt allows it (absent
    = allowed, unreadable = skipped and diagnosed).
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

import fetch_brief_media as fbm
import fetch_media
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
    """R5/R9: one honest identity, always; a refusal ends the fetch."""

    MIRROR = "https://mirror.example/feed"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.raw = Path(self._tmp.name) / "raw"
        self.raw.mkdir()
        for name, value in (("RAW_DIR", self.raw), ("RETRIES", 3),
                            ("URL_ALTERNATES", {URL: [self.MIRROR]})):
            patcher = mock.patch.object(ingest_rss, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)

    def fetch(self, opener, url=URL, stalled_hosts=None):
        with mock.patch.object(ingest_rss, "public_http_url", side_effect=lambda u, resolve=False: u), \
                mock.patch.object(ingest_rss.urllib.request, "build_opener", return_value=opener):
            return ingest_rss.fetch_bytes(url, stalled_hosts=stalled_hosts)

    def requests_of(self, opener):
        return [call.args[0] for call in opener.open.call_args_list]

    def assert_all_honest(self, opener) -> None:
        reqs = self.requests_of(opener)
        self.assertTrue(reqs)
        for req in reqs:
            self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)
            self.assertIsNone(req.get_header("Referer"))
        self.assertEqual(sorted(p.name for p in self.raw.glob("_ua_policy*")), [])

    def test_honest_identity_is_the_only_identity_and_discloses_the_legal_page(self) -> None:
        opener = mock.Mock()
        opener.open.return_value = response(RSS, {})
        self.fetch(opener)
        self.assert_all_honest(opener)
        self.assertIn("vigieqc.com/methode/legal.html", ingest_rss.USER_AGENT)
        self.assertIn("non-commercial", ingest_rss.USER_AGENT)
        self.assertNotIn("Mozilla", ingest_rss.USER_AGENT)
        for gone in ("FALLBACK_USER_AGENT", "UA_POLICY_PATH", "choose_user_agent",
                     "mark_browser_identity"):
            self.assertFalse(hasattr(ingest_rss, gone), gone)

    def test_no_collector_carries_a_browser_identity_or_a_referer(self) -> None:
        for path in sorted((brief.ROOT / "scripts").glob("*.py")):
            text = path.read_text(encoding="utf-8")
            with self.subTest(script=path.name):
                self.assertNotIn("Mozilla/", text)
                self.assertNotIn('"Referer"', text)

    def test_transport_stall_is_retried_under_the_same_honest_identity(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = [ConnectionResetError("connection reset"), response(RSS, {})]
        body, _ = self.fetch(opener)
        self.assertEqual(body, RSS)
        self.assertEqual(len(self.requests_of(opener)), 2)
        self.assert_all_honest(opener)

    def test_timeouts_never_switch_identity_and_end_as_a_recorded_gap(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError("timed out")
        stalled: dict = {}
        with self.assertRaises(TimeoutError):
            self.fetch(opener, stalled_hosts=stalled)
        # every attempt on the feed and on its transport-only alternate is honest
        reqs = self.requests_of(opener)
        self.assertEqual([r.full_url for r in reqs], [URL] * 3 + [self.MIRROR] * 3)
        self.assert_all_honest(opener)
        self.assertEqual(stalled, {"news.example": "TimeoutError", "mirror.example": "TimeoutError"})

    def test_a_host_that_never_answered_is_not_knocked_on_again_this_run(self) -> None:
        opener = mock.Mock()
        stalled = {"news.example": "TimeoutError", "mirror.example": "TimeoutError"}
        with self.assertRaisesRegex(ConnectionError, "no HTTP answer earlier in this run"):
            self.fetch(opener, stalled_hosts=stalled)
        opener.open.assert_not_called()

    def test_transport_failure_reaches_the_documented_alternate_honestly(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = [TimeoutError("t")] * 3 + [response(RSS, {})]
        body, _ = self.fetch(opener)
        self.assertEqual(body, RSS)
        self.assertEqual(self.requests_of(opener)[-1].full_url, self.MIRROR)
        self.assert_all_honest(opener)

    def test_local_dns_failure_never_switches_identity(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.URLError(
            socket.gaierror(-2, "Name or service not known"))
        with self.assertRaises(urllib.error.URLError):
            self.fetch(opener)
        self.assert_all_honest(opener)

    def test_http_refusal_is_final_no_retry_no_alternate_no_other_identity(self) -> None:
        for code in (403, 404, 406, 410, 429):
            with self.subTest(code=code):
                opener = mock.Mock()
                opener.open.side_effect = urllib.error.HTTPError(URL, code, "Refused", {}, None)
                with self.assertRaises(urllib.error.HTTPError):
                    self.fetch(opener)
                # one request, to the feed itself: the alternate is never tried
                self.assertEqual([r.full_url for r in self.requests_of(opener)], [URL])
                self.assert_all_honest(opener)

    def test_a_server_error_is_an_answer_not_a_reason_to_try_an_alternate(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError(URL, 503, "Unavailable", {}, None)
        with self.assertRaises(urllib.error.HTTPError):
            self.fetch(opener)
        self.assertEqual([r.full_url for r in self.requests_of(opener)], [URL] * 3)
        self.assert_all_honest(opener)

    def test_the_refusal_is_recorded_by_ingest(self) -> None:
        refusal = urllib.error.HTTPError(URL, 403, "Forbidden", {}, None)
        with mock.patch.object(ingest_rss, "fetch_bytes", side_effect=refusal):
            result = ingest_rss.ingest_one({"id": "news", "url": URL},
                                           datetime(2026, 10, 5, tzinfo=timezone.utc))
        self.assertFalse(result["ok"])
        self.assertEqual(result["refused"], 403)
        self.assertIn("403", result["error"])

    def test_conditional_refusal_is_final_too(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = urllib.error.HTTPError(URL, 403, "Forbidden", {}, None)
        cache = {URL: {"etag": "e1", "last_modified": None,
                       "content_type": "application/rss+xml", "updated_at": "x"}}
        with mock.patch.object(ingest_rss, "_load_http_cache", return_value=cache):
            with self.assertRaises(urllib.error.HTTPError):
                self.fetch(opener)
        self.assertEqual(len(self.requests_of(opener)), 1)

    def test_conditional_refusal_still_tries_one_plain_request(self) -> None:
        # 412 Precondition Failed is the validator's fault, not the origin
        # refusing the feed: fall through to the plain GET.
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

    def test_a_leftover_browser_marker_file_is_ignored(self) -> None:
        # data/raw/_ua_policy.json may survive in the private state store; it
        # must never bring the browser identity back.
        (self.raw / "_ua_policy.json").write_text(json.dumps({"news.example": {
            "identity": "browser", "reason": "TimeoutError",
            "marked_at": "2099-01-01T00:00:00+00:00"}}), encoding="utf-8")
        opener = mock.Mock()
        opener.open.return_value = response(RSS, {})
        self.fetch(opener)
        for req in self.requests_of(opener):
            self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)


ART = "https://www.journaldequebec.com/2026/10/05/article"
IMG = "https://cdn.example/photo.jpg"
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 100


class RobotsLaw(unittest.TestCase):
    """R9: robots.txt first for article pages and images, honest identity,
    once per host per run; 404 = allowed, any other failure = skipped."""

    def setUp(self) -> None:
        ingest_rss._ROBOTS_CACHE.clear()
        self.addCleanup(ingest_rss._ROBOTS_CACHE.clear)
        self.robots_calls: list[str] = []

    def serve_robots(self, answer):
        """answer: bytes (robots body) or an exception to raise."""
        def fetcher(url):
            self.robots_calls.append(url)
            if isinstance(answer, BaseException):
                raise answer
            return answer
        patcher = mock.patch.object(ingest_rss, "ROBOTS_FETCHER", fetcher)
        patcher.start()
        self.addCleanup(patcher.stop)

    def opener_for(self, body: bytes):
        resp = mock.MagicMock()
        resp.headers = {}
        resp.read.return_value = body
        resp.__enter__.return_value = resp
        opener = mock.Mock()
        opener.open.return_value = resp
        return opener

    def html(self, url=ART, opener=None, retries=1):
        diag: dict = {}
        opener = opener or self.opener_for(b"<html>ok</html>")
        with mock.patch.object(fetch_media, "public_http_url", side_effect=lambda u, resolve=False: u), \
                mock.patch.object(fetch_media, "public_opener", return_value=opener):
            got = fetch_media.fetch_html(url, retries=retries, diag=diag)
        return got, diag, opener

    def image(self, url=IMG, opener=None, retries=1):
        diag: dict = {}
        opener = opener or self.opener_for(JPEG)
        with mock.patch.object(fbm, "public_http_url", side_effect=lambda u, resolve=False: u), \
                mock.patch.object(fbm, "public_opener", return_value=opener):
            got = fbm.fetch_image(url, retries=retries, diag=diag)
        return got, diag, opener

    # -- the fetch paths --------------------------------------------------

    def test_article_page_carries_no_referer_and_the_honest_identity(self) -> None:
        self.serve_robots(urllib.error.HTTPError("u", 404, "Not Found", {}, None))
        got, diag, opener = self.html()
        self.assertEqual(got, "<html>ok</html>")
        req = opener.open.call_args.args[0]
        self.assertIsNone(req.get_header("Referer"))
        self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)
        self.assertEqual(self.robots_calls, ["https://www.journaldequebec.com/robots.txt"])

    def test_image_carries_no_referer_and_the_honest_identity(self) -> None:
        self.serve_robots(b"User-agent: *\nDisallow: /private/\n")
        got, _, opener = self.image()
        self.assertEqual(got, (JPEG, "jpg"))
        req = opener.open.call_args.args[0]
        self.assertIsNone(req.get_header("Referer"))
        self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)

    def test_disallowed_article_is_never_fetched_and_is_diagnosed(self) -> None:
        self.serve_robots(b"User-agent: *\nDisallow: /2026/\n")
        got, diag, opener = self.html()
        self.assertIsNone(got)
        opener.open.assert_not_called()
        self.assertEqual(diag["reason"], "robots_disallow")
        self.assertIn("robots.txt", diag["detail"])

    def test_disallowed_image_is_never_fetched_and_is_diagnosed(self) -> None:
        self.serve_robots(b"User-agent: Vigie\nDisallow: /\n")
        got, diag, opener = self.image()
        self.assertIsNone(got)
        opener.open.assert_not_called()
        self.assertEqual(diag["reason"], "robots_disallow")

    def test_absent_robots_file_allows(self) -> None:
        self.serve_robots(urllib.error.HTTPError("u", 404, "Not Found", {}, None))
        got, diag, _ = self.image()
        self.assertEqual(got, (JPEG, "jpg"))
        self.assertEqual(diag, {})

    def test_unreadable_robots_fails_closed(self) -> None:
        for answer in (urllib.error.HTTPError("u", 403, "Forbidden", {}, None),
                       urllib.error.HTTPError("u", 410, "Gone", {}, None),
                       urllib.error.HTTPError("u", 503, "Unavailable", {}, None),
                       TimeoutError("timed out"),
                       ConnectionResetError("reset"),
                       urllib.error.URLError(socket.gaierror(-2, "dns down"))):
            with self.subTest(answer=repr(answer)):
                ingest_rss._ROBOTS_CACHE.clear()
                self.serve_robots(answer)
                got, diag, opener = self.html()
                self.assertIsNone(got)
                opener.open.assert_not_called()
                self.assertEqual(diag["reason"], "robots_unreachable")
                got, diag, opener = self.image("https://www.journaldequebec.com/i.jpg")
                self.assertIsNone(got)
                opener.open.assert_not_called()
                self.assertEqual(diag["reason"], "robots_unreachable")

    def test_robots_is_read_once_per_host_per_run(self) -> None:
        self.serve_robots(b"User-agent: *\nDisallow: /private/\n")
        self.html(ART)
        self.html("https://www.journaldequebec.com/2026/10/05/other")
        self.image("https://www.journaldequebec.com/images/a.jpg")
        self.image(IMG)
        self.assertEqual(self.robots_calls, ["https://www.journaldequebec.com/robots.txt",
                                             "https://cdn.example/robots.txt"])

    def test_the_real_robots_fetch_uses_the_guard_and_the_honest_identity(self) -> None:
        opener = self.opener_for(b"User-agent: *\nDisallow:\n")
        with mock.patch.object(ingest_rss, "public_http_url") as guard, \
                mock.patch.object(ingest_rss, "public_opener", return_value=opener):
            body = ingest_rss.robots_fetch("https://news.example/robots.txt")
        guard.assert_called_once_with("https://news.example/robots.txt", resolve=True)
        req = opener.open.call_args.args[0]
        self.assertEqual(req.get_header("User-agent"), ingest_rss.USER_AGENT)
        self.assertIsNone(req.get_header("Referer"))
        self.assertEqual(body, b"User-agent: *\nDisallow:\n")
        opener.open.return_value.read.assert_called_once_with(ingest_rss.MAX_ROBOTS_BYTES)

    def test_a_refused_page_is_not_retried(self) -> None:
        for code in (403, 410, 429):
            with self.subTest(code=code):
                opener = mock.Mock()
                opener.open.side_effect = urllib.error.HTTPError(ART, code, "No", {}, None)
                got, _, _ = self.html(opener=opener, retries=3)
                self.assertIsNone(got)
                self.assertEqual(opener.open.call_count, 1)
                opener = mock.Mock()
                opener.open.side_effect = urllib.error.HTTPError(IMG, code, "No", {}, None)
                got, _, _ = self.image(opener=opener, retries=3)
                self.assertIsNone(got)
                self.assertEqual(opener.open.call_count, 1)

    def test_a_timeout_never_switches_identity(self) -> None:
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError("timed out")
        with mock.patch.object(fetch_media.time, "sleep"):
            got, diag, _ = self.html(opener=opener, retries=3)
        self.assertIsNone(got)
        self.assertEqual(diag["reason"], "article_timeout")
        self.assertEqual(opener.open.call_count, 3)
        for call in opener.open.call_args_list:
            self.assertEqual(call.args[0].get_header("User-agent"), ingest_rss.USER_AGENT)
        opener = mock.Mock()
        opener.open.side_effect = TimeoutError("timed out")
        with mock.patch.object(fbm.time, "sleep"):
            got, diag, _ = self.image(opener=opener, retries=3)
        self.assertEqual(diag["reason"], "image_timeout")
        for call in opener.open.call_args_list:
            self.assertEqual(call.args[0].get_header("User-agent"), ingest_rss.USER_AGENT)

    # -- the rules (RFC 9309) ---------------------------------------------

    def allowed(self, robots: str, url: str) -> bool:
        return ingest_rss.robots_allows(ingest_rss.robots_rules(robots), url)

    def test_our_own_group_wins_over_the_star_group(self) -> None:
        robots = "User-agent: *\nDisallow: /\n\nUser-agent: Vigie\nAllow: /\n"
        self.assertTrue(self.allowed(robots, "https://x.example/a"))
        robots = "User-agent: *\nAllow: /\n\nUser-agent: vigie/0.2\nDisallow: /news\n"
        self.assertFalse(self.allowed(robots, "https://x.example/news/a"))
        self.assertTrue(self.allowed(robots, "https://x.example/sports"))

    def test_star_group_binds_when_we_are_not_named(self) -> None:
        robots = "User-agent: Googlebot\nAllow: /\n\nUser-agent: *\nDisallow: /news\n"
        self.assertFalse(self.allowed(robots, "https://x.example/news/a"))
        self.assertTrue(self.allowed(robots, "https://x.example/"))

    def test_grouped_agents_share_their_rules(self) -> None:
        robots = "User-agent: Bingbot\nUser-agent: Vigie\nDisallow: /a\nCrawl-delay: 3\nDisallow: /b\n"
        self.assertFalse(self.allowed(robots, "https://x.example/a1"))
        self.assertFalse(self.allowed(robots, "https://x.example/b1"))

    def test_longest_match_wins_and_allow_wins_ties(self) -> None:
        robots = "User-agent: *\nAllow: /\nDisallow: /private\n"
        self.assertFalse(self.allowed(robots, "https://x.example/private/a"))
        robots = "User-agent: *\nDisallow: /\nAllow: /news/\n"
        self.assertTrue(self.allowed(robots, "https://x.example/news/a"))
        self.assertFalse(self.allowed(robots, "https://x.example/other"))
        robots = "User-agent: *\nDisallow: /page\nAllow: /page\n"
        self.assertTrue(self.allowed(robots, "https://x.example/page"))

    def test_wildcards_and_end_anchor(self) -> None:
        robots = "User-agent: *\nDisallow: /*.jpg$\nDisallow: /*?replytocom=\n"
        self.assertFalse(self.allowed(robots, "https://x.example/img/a.jpg"))
        self.assertTrue(self.allowed(robots, "https://x.example/img/a.jpg?w=1"))
        self.assertFalse(self.allowed(robots, "https://x.example/p?replytocom=4"))
        self.assertTrue(self.allowed(robots, "https://x.example/p?page=2"))

    def test_empty_disallow_comments_and_garbage_allow(self) -> None:
        self.assertTrue(self.allowed("User-agent: *\nDisallow:\n", "https://x.example/a"))
        self.assertTrue(self.allowed("# nothing\n\x00garbage\n", "https://x.example/a"))
        self.assertTrue(self.allowed("", "https://x.example/a"))
        self.assertFalse(self.allowed("﻿User-agent: * # all\nDisallow: / # all\n",
                                      "https://x.example/a"))

    def test_hostile_pattern_cannot_hang_matching(self) -> None:
        pattern = "/" + "*a" * 200 + "b"
        robots = f"User-agent: *\nDisallow: {pattern}\n"
        self.assertTrue(self.allowed(robots, "https://x.example/" + "a" * 5000))

    def test_robots_disallow_backs_off_a_day_and_is_never_permanent(self) -> None:
        now = datetime(2026, 10, 5, 12, tzinfo=timezone.utc)
        entry: dict = {}
        fbm._apply_policy(entry, "robots_disallow", 1, now)
        self.assertFalse(entry.get("permanent"))
        self.assertEqual(entry["retry_after"], "2026-10-06T12:00:00+00:00")
        entry = {}
        fbm._apply_policy(entry, "robots_unreachable", 1, now)
        self.assertNotIn("retry_after", entry)
        self.assertFalse(entry.get("permanent"))


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

    def test_legal_page_discloses_the_one_identity_robots_and_retention(self) -> None:
        text = (brief.ROOT / "legal.md").read_text(encoding="utf-8")
        self.assertIn(ingest_rss.USER_AGENT, text)
        # the browser identity is gone, so the page must not claim it either
        self.assertNotIn("Mozilla", text)
        self.assertNotIn("identité de navigateur standard", text)
        self.assertIn("robots.txt", text)
        self.assertIn("30", text)

    def test_internal_law_files_quote_the_canonical_identity(self) -> None:
        for name in ("LEGAL_RISK.md", "TECHNICAL_PROCESS.md"):
            text = (brief.ROOT / name).read_text(encoding="utf-8")
            with self.subTest(file=name):
                self.assertNotIn("vigieqc.com/legal.md)", text)
                self.assertNotIn("_ua_policy.json", text)
                self.assertIn(ingest_rss.USER_AGENT, text)


if __name__ == "__main__":
    unittest.main()
