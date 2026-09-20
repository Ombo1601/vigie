"""Resident trust boundaries: fresh sources, safe links and explainable coverage."""
from __future__ import annotations

import copy
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import harness
import resident_brief as brief
import stage_public

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def story(ident="one", **values):
    item = {
        "id": ident, "url": f"https://actualites.example.com/{ident}",
        "title": "Travaux dans le quartier Saint-Roch à Québec",
        "summary": "La Ville annonce les prochaines étapes.",
        "published_at": (NOW - timedelta(hours=2)).isoformat(),
        "fetched_at": NOW.isoformat(), "source_name": "Source locale",
        "source_id": "local", "language": "fr", "rank_score": 0.8,
        "enrich": {"geo": {"geo": "quebec-city"}, "topics": [{"topic": "transport"}]},
    }
    item.update(values)
    return item


def collection(**values):
    run = {"fetched_at": NOW.isoformat(), "enabled_rss": ["local"],
           "results": [{"source_id": "local", "ok": True, "item_count": 1}]}
    run.update(values)
    return run


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class SafeSourceLinks(unittest.TestCase):
    def test_ordinary_http_and_https_links_remain_usable(self):
        for url in ("https://www.ville.quebec.qc.ca/actualites/?a=1&b=2", "http://example.com:80/news", "https://example.com:443/news"):
            with self.subTest(url=url):
                self.assertEqual(brief.safe_url(url), url)

    def test_unsafe_schemes_credentials_and_local_addresses_are_rejected(self):
        urls = (
            "javascript:alert(1)", "data:text/html,x", "file:///etc/passwd", "ftp://example.com/x",
            "//example.com/x", "https://user:password@example.com/x", "https://example.com:8080/x",
            "https://example.com/\nattack", "https://example.com/a b", "https://example.com\\@localhost/x",
            "http://127.0.0.1/", "http://10.0.0.1/", "http://169.254.169.254/", "http://[::1]/",
            "http://device.local/", "http://device.internal/", "http://localhost./",
            "http://127.1/", "http://0177.0.0.1/", "http://0x7f.0.0.1/",
        )
        for url in urls:
            with self.subTest(url=url):
                self.assertEqual(brief.safe_url(url), "")

    def test_html_payloads_cannot_create_elements_or_event_handlers(self):
        malicious = story(
            title='Actualité </a><script>alert("title")</script>',
            summary='Résumé &lt;img src=x onerror=alert(1)&gt;',
            source_name='Source " onclick="alert(1) <svg onload=alert(1)>',
            url='https://example.com/news?q=\"><svg/onload=alert(1)>',
        )
        page = brief.render_brief([malicious], NOW.isoformat(), [], collection())
        tags = Tags()
        tags.feed(page)
        scripts = [attrs for tag, attrs in tags.tags if tag == "script"]
        self.assertEqual(scripts, [{"src": "/assets/brief.js", "defer": None}])
        self.assertFalse(any(key.lower().startswith("on") for _, attrs in tags.tags for key in attrs))
        self.assertFalse(any(tag == "img" for tag, _ in tags.tags))
        article_links = [attrs["href"] for tag, attrs in tags.tags if tag == "a" and attrs.get("href", "").startswith("https://example.com/news")]
        self.assertEqual(article_links, [malicious["url"]])

    def test_bad_story_url_does_not_become_a_card(self):
        rows, excluded = brief.prepare_items([story(url="javascript:alert(1)")], NOW)
        self.assertEqual(rows, [])
        self.assertEqual(excluded, 1)


class PublicationFreshness(unittest.TestCase):
    def test_missing_invalid_and_timezone_free_dates_are_excluded(self):
        for date in (None, "", "not-a-date", "2026-09-17", "2026-09-17T11:00:00", 123):
            with self.subTest(date=date):
                rows, count = brief.prepare_items([story(published_at=date)], NOW)
                self.assertEqual((rows, count), ([], 1))

    def test_rfc_and_iso_dates_normalize_to_utc(self):
        for raw in ("Thu, 17 Sep 2026 08:00:00 -0400", "2026-09-17T08:00:00-04:00", "2026-09-17T12:00:00Z"):
            with self.subTest(raw=raw):
                self.assertEqual(brief.parse_date(raw), NOW)

    def test_old_publication_is_not_refreshed_by_a_new_fetch(self):
        rows, count = brief.prepare_items([story(published_at=(NOW - timedelta(days=30)).isoformat())], NOW)
        self.assertEqual((rows, count), ([], 1))

    def test_window_boundary_and_future_tolerance(self):
        for delta, expected in ((timedelta(days=-7), 0), (timedelta(days=-7, seconds=1), 1), (timedelta(minutes=5), 1), (timedelta(minutes=5, seconds=1), 0)):
            with self.subTest(delta=delta):
                rows, _ = brief.prepare_items([story(published_at=(NOW + delta).isoformat())], NOW)
                self.assertEqual(len(rows), expected)

    def test_rank_order_is_preserved_and_input_is_not_mutated(self):
        ranked = [story("z", rank_score=0.6), story("a", rank_score=0.6), story("b", rank_score=0.4)]
        original = copy.deepcopy(ranked)
        rows, _ = brief.prepare_items(ranked, NOW)
        self.assertEqual([row["id"] for row in rows], ["z", "a", "b"])
        self.assertEqual(ranked, original)

    def test_same_link_is_deduplicated_and_bookmark_survives_candidate_id_change(self):
        first = story("old-id")
        second = story("new-id", url=first["url"])
        rows, _ = brief.prepare_items([first, second], NOW)
        newer, _ = brief.prepare_items([second], NOW)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["uid"], newer[0]["uid"])

    def test_date_fallback_escapes_untrusted_text(self):
        self.assertEqual(brief.date_html(None, fallback='<b onclick="x">Absent</b>'), '&lt;b onclick=&quot;x&quot;&gt;Absent&lt;/b&gt;')


class CoverageHonesty(unittest.TestCase):
    def test_parse_error_and_missing_source_reduce_coverage(self):
        run = collection(enabled_rss=["local", "failed", "missing"], results=[
            {"source_id": "local", "ok": True}, {"source_id": "failed", "ok": True, "parse_error": "invalid XML"},
        ])
        status = brief.collection_status(run, NOW)
        self.assertEqual((status["ok"], status["total"], status["partial"]), (1, 3, True))
        self.assertEqual(set(status["failed"]), {"failed", "missing"})
        page = brief.render_brief([], NOW.isoformat(), [], run)
        self.assertIn("1 flux disponibles sur 3", page)
        self.assertIn("Collecte partielle", page)
        self.assertIn("missing", page, "An unavailable enabled source must remain visible in the coverage disclosure")

    def test_no_sources_cannot_look_like_a_healthy_collection(self):
        run = collection(enabled_rss=[], results=[])
        status = brief.collection_status(run, NOW)
        self.assertTrue(status["stale"] or status["partial"], "Unknown coverage must not produce a healthy green status")
        page = brief.render_brief([], NOW.isoformat(), [], run)
        self.assertIn("État des sources inconnu", page)
        self.assertNotIn('>Dernière collecte</p>', page)

    def test_all_failed_sources_are_explicit(self):
        run = collection(results=[{"source_id": "local", "ok": False}])
        page = brief.render_brief([], NOW.isoformat(), [], run)
        self.assertIn("0 flux disponibles sur 1", page)
        self.assertIn("Indisponible", page)
        self.assertNotIn('>Dernière collecte</p>', page)

    def test_missing_old_and_future_collection_dates_warn(self):
        for date in (None, "bad", (NOW - timedelta(hours=7)).isoformat(), (NOW + timedelta(minutes=6)).isoformat()):
            with self.subTest(date=date):
                status = brief.collection_status(collection(fetched_at=date), NOW)
                self.assertTrue(status["stale"])


class PlaceAndSourceContext(unittest.TestCase):
    def test_neighborhood_in_local_title_is_a_useful_hint(self):
        rows, _ = brief.prepare_items([story()], NOW)
        self.assertIn("cite", rows[0]["areas"])

    def test_person_named_vanier_outside_quebec_is_not_les_rivieres(self):
        item = story(title="Georgia Vanier est honorée à Ottawa", summary="Une cérémonie en Ontario.", enrich={"geo": {"geo": "linked"}})
        rows, _ = brief.prepare_items([item], NOW)
        self.assertNotIn("rivières", rows[0]["areas"])

    def test_old_and_unsafe_related_articles_never_reenter_current_coverage(self):
        ranked = [story("current"), story("peer", source_id="peer"),
                  story("old", published_at=(NOW - timedelta(days=8)).isoformat()),
                  story("unsafe", url="javascript:alert(1)")]
        rows, _ = brief.prepare_items(ranked, NOW)
        issue = {"tensions": [{"items": [{"candidate_id": c["id"]} for c in ranked]}]}
        peers = brief.related_sources(rows[0], [issue], {r["id"]: r for r in rows})
        self.assertEqual([p["id"] for p in peers], ["peer"])

    def test_unrelated_issue_cannot_create_a_comparison(self):
        rows, _ = brief.prepare_items([story("first"), story("second")], NOW)
        issue = {"tensions": [{"items": [{"candidate_id": "second"}]}]}
        self.assertEqual(brief.related_sources(rows[0], [issue], {r["id"]: r for r in rows}), [])


class PageIntegration(unittest.TestCase):
    def test_deterministic_render_and_no_external_passive_requests(self):
        args = ([story()], NOW.isoformat(), [], collection())
        page = brief.render_brief(*args)
        self.assertEqual(page, brief.render_brief(*args))
        tags = Tags()
        tags.feed(page)
        # A link relation (canonical, alternate, license...) is metadata the
        # browser never fetches; subresources must stay same-origin.
        metadata_rels = {"canonical", "alternate", "license", "author", "me"}
        for tag, attrs in tags.tags:
            if tag not in {"script", "img", "iframe", "link"}:
                continue
            if tag == "link" and attrs.get("rel") in metadata_rels:
                continue
            self.assertFalse(attrs.get("src", attrs.get("href", "")).startswith(("http:", "https:", "//")))
        self.assertIn('<html lang="fr-CA">', page)
        self.assertIn("<noscript>", page)
        self.assertIn("https://actualites.example.com/one", page)

    def test_first_image_bearing_story_is_eager_for_lcp(self) -> None:
        # The first story may have no image; the first one that does is the LCP
        # candidate and must not be lazy-loaded.
        rows, _ = brief.prepare_items([story("a"), story("b")], NOW)
        media = {rows[1]["uid"]: {"file": rows[1]["uid"] + ".jpg", "credit": None}}
        page = brief.render_brief([story("a"), story("b")], NOW.isoformat(), [], collection(),
                                  media=media)
        self.assertEqual(page.count('loading="eager" fetchpriority="high"'), 1)
        self.assertEqual(page.count('loading="lazy"'), 0)

    def test_generated_brief_passes_release_local_navigation_validation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "index.html").write_text(brief.render_brief([story()], NOW.isoformat(), [], collection()), encoding="utf-8")
            (root / "assets").mkdir()
            for name in ("assets/brief.css", "assets/fonts.css", "assets/brief.js", "favicon.svg", "apple-touch-icon.png", "site.webmanifest", "explorer.html", "morning.html", "llms.txt", "index.html.md", *stage_public.OPTIONAL_PAGES, *stage_public.METHODS):
                (root / name).write_text("placeholder", encoding="utf-8")
            self.assertEqual(stage_public.validate_site(root), [])


class TopicFilters(unittest.TestCase):
    def test_filters_cover_the_topics_the_edition_actually_has(self) -> None:
        page = brief.render_brief([], NOW.isoformat(), [], collection())
        for topic in ("transport", "housing", "health", "law", "culture",
                      "energy/hydro", "security", "economy", "education", "environment"):
            self.assertIn(f'data-topic="{topic}"', page)

    def test_secondary_themes_are_behind_a_disclosure(self) -> None:
        page = brief.render_brief([], NOW.isoformat(), [], collection())
        self.assertIn('id="more-topics"', page)
        self.assertIn('aria-controls="more-topics-list"', page)
        self.assertIn('aria-expanded="false"', page)
        start = page.index('id="more-topics-list"')
        block = page[start: page.index("</div>", start)]
        self.assertIn("hidden", block)
        for topic in ("energy/hydro", "security", "economy", "education", "environment", "culture"):
            self.assertIn(f'data-topic="{topic}"', block)

    def test_trade_is_canonicalised_to_economy(self) -> None:
        item = story(enrich={"geo": {"geo": "quebec-city"}, "topics": [{"topic": "trade"}]})
        page = brief.render_brief([item], NOW.isoformat(), [], collection())
        self.assertIn('data-topics="economy"', page)
        self.assertNotIn('data-topics="trade"', page)

    def test_other_stays_reachable_under_tout(self) -> None:
        item = story(enrich={"geo": {"geo": "quebec-city"}, "topics": [{"topic": "other"}]})
        page = brief.render_brief([item], NOW.isoformat(), [], collection())
        self.assertIn('data-topics="other"', page)


if __name__ == "__main__":
    unittest.main()
