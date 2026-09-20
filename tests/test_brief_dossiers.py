"""Resident front door carries the lookout: cross-source dossiers, in French.

Locks the honesty guards (grouping is not contradiction; absence is not proven
editorial silence or bias; several media are not independent confirmations),
attribute escaping, unsafe-link rejection, determinism and the empty state.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import harness  # noqa: F401

import resident_brief as brief

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)


def issue(**over):
    base = {
        "issue_id": "dossier-1",
        "question": "Tramway de Québec : les positions rapportées",
        "geo_focus": ["quebec-city"],
        "source_count": 2,
        "sources": ["le-soleil", "radio-canada"],
        "official_voice_count": 0,
        "media_remix": True,
        "silence": {
            "spoke_count": 2,
            "silent_count": 7,
            "enabled_count": 9,
            "silent": [
                {"institution_name": "Ville de Québec", "source_id": "ville-quebec", "source_kind": "official"},
                {"institution_name": "Hydro-Québec", "source_id": "hydro-quebec", "source_kind": "official"},
                {"institution_name": "Le Devoir", "source_id": "le-devoir", "source_kind": "media"},
            ],
        },
        "tensions": [
            {
                "institution_name": "Le Soleil",
                "source_kind": "media",
                "items": [
                    {"candidate_id": "c1", "title": "Québec dit non au tramway", "url": "https://lesoleil.example/a", "source_name": "Le Soleil"},
                ],
            },
            {
                "institution_name": "Radio-Canada",
                "source_kind": "media",
                "items": [
                    {"candidate_id": "c2", "title": "Ottawa tranche pour le tramway", "url": "https://rc.example/b", "source_name": "Radio-Canada"},
                ],
            },
        ],
    }
    base.update(over)
    return base


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


def collection():
    return {
        "fetched_at": NOW.isoformat(), "enabled_rss": ["local"],
        "results": [{"source_id": "local", "ok": True, "item_count": 1}],
    }


class Tags(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class DossierRendering(unittest.TestCase):
    def test_section_carries_question_voices_and_official_silence(self):
        page = brief.render_brief([story()], NOW.isoformat(), [issue()], collection())
        self.assertIn('id="dossiers"', page)
        self.assertIn("REGARD CROISÉ", page)
        self.assertIn("Ce que les sources racontent ensemble.", page)
        self.assertIn("Tramway de Québec", page)
        self.assertIn("Le Soleil", page)
        self.assertIn("Radio-Canada", page)
        self.assertIn("Ville de Québec", page)
        self.assertIn("Hydro-Québec", page)
        self.assertIn("https://lesoleil.example/a", page)
        self.assertIn("https://rc.example/b", page)

    def test_section_sits_between_brief_and_services(self):
        page = brief.render_brief([story()], NOW.isoformat(), [issue()], collection())
        self.assertLess(page.index('id="essentiel"'), page.index('id="dossiers"'))
        self.assertLess(page.index('id="dossiers"'), page.index('id="agir"'))
        self.assertIn('<a href="#dossiers">Les dossiers</a>', page)

    def test_honesty_guards_are_present(self):
        page = brief.render_brief([story()], NOW.isoformat(), [issue()], collection())
        self.assertIn("n’est pas une contradiction", page)
        self.assertIn("pas plusieurs confirmations indépendantes", page)
        self.assertIn("pas un silence éditorial prouvé", page)
        self.assertIn("pas un indicateur de biais", page)

    def test_story_desk_shows_face_on_headlines_and_pourquoi(self):
        html = brief.dossier_html(issue(), {})
        self.assertIn('class="dossier-headlines"', html)
        self.assertIn('id="dossier-dossier-1"', html)
        self.assertIn("Pourquoi ici : rapprochement proposé", html)
        self.assertIn("pas un verdict", html)
        self.assertIn("Québec dit non au tramway", html)
        self.assertIn("Ottawa tranche pour le tramway", html)

    def test_story_desk_caps_three_institutions_official_first(self):
        extra = issue(tensions=[
            {"institution_name": "Le Soleil", "source_kind": "media", "items": [
                {"title": "Média A", "url": "https://a.example/a", "published_at": "2026-09-17T12:00:00+00:00"},
            ]},
            {"institution_name": "Radio-Canada", "source_kind": "media", "items": [
                {"title": "Média B", "url": "https://b.example/b", "published_at": "2026-09-17T11:00:00+00:00"},
            ]},
            {"institution_name": "Ville de Québec", "source_kind": "official", "items": [
                {"title": "Avis officiel", "url": "https://v.example/v", "published_at": "2026-09-17T10:00:00+00:00"},
            ]},
            {"institution_name": "Le Devoir", "source_kind": "media", "items": [
                {"title": "Média C", "url": "https://c.example/c", "published_at": "2026-09-17T13:00:00+00:00"},
            ]},
        ])
        html = brief.dossier_html(extra, {})
        face = html[html.index("dossier-headlines"): html.index("</ol>")]
        self.assertIn("Avis officiel", face)
        self.assertLess(face.index("Avis officiel"), face.index("Média"))
        self.assertIn("Toutes les sources (4)", html)
        self.assertEqual(face.count("<li "), 3)

    def test_shared_edition_sentence_is_on_the_front_door(self):
        page = brief.render_brief([story()], NOW.isoformat(), [issue()], collection())
        self.assertIn("Cette édition est la même pour chaque lecteur", page)
        self.assertIn('id="lens-title"', page)
        self.assertIn('id="dossier-find"', page)
        self.assertIn("coller une URL", page)
        self.assertIn("Pourquoi ici :", page)

    def test_media_remix_is_disclosed_and_cleared_when_official_speaks(self):
        remix = brief.render_brief([story()], NOW.isoformat(), [issue(media_remix=True)], collection())
        self.assertIn("Aucune source officielle sur ce dossier", remix)
        official = issue(media_remix=False, official_voice_count=1)
        cleared = brief.render_brief([story()], NOW.isoformat(), [official], collection())
        self.assertNotIn("Aucune source officielle sur ce dossier", cleared)

    def test_only_official_institutions_are_named_as_quiet(self):
        html = brief.dossier_html(issue(), {})
        silence = html[html.index("dossier-silence"):]
        silence = silence[: silence.index("</p>")]
        self.assertIn("Ville de Québec", silence)
        self.assertIn("Hydro-Québec", silence)
        self.assertNotIn("Le Devoir", silence)

    def test_empty_dossiers_state_is_honest_not_hidden(self):
        page = brief.render_brief([story()], NOW.isoformat(), [], collection())
        self.assertIn('id="dossiers"', page)
        self.assertIn("Aucun sujet n’a été rapproché", page)
        self.assertIn("Cela ne dit rien de la couverture ailleurs.", page)


class DossierTrust(unittest.TestCase):
    def test_malicious_dossier_text_cannot_create_elements_or_handlers(self):
        evil = issue(
            question='Tramway </h3><script>alert("q")</script>',
            tensions=[
                {
                    "institution_name": 'Radio <svg onload=alert(1)>',
                    "source_kind": "media",
                    "items": [
                        {"candidate_id": "c1", "title": 'Titre " onclick="alert(1)', "url": "https://rc.example/b", "source_name": "x"},
                    ],
                }
            ],
            silence={
                "silent_count": 1,
                "silent": [{"institution_name": 'Ville <img src=x onerror=alert(1)>', "source_id": "v", "source_kind": "official"}],
            },
        )
        page = brief.render_brief([story()], NOW.isoformat(), [evil], collection())
        tags = Tags()
        tags.feed(page)
        scripts = [attrs for tag, attrs in tags.tags if tag == "script"]
        self.assertEqual(scripts, [{"src": "/assets/brief.js", "defer": None}])
        self.assertFalse(any(key.lower().startswith("on") for _, attrs in tags.tags for key in attrs))
        # The brief uses inline <svg> icons but never <img>; an injected onload/onerror
        # would surface as an on* attribute above, so it was escaped to text.
        self.assertFalse(any(tag == "img" for tag, _ in tags.tags))
        # Positive proof the injections were neutralized to inert escaped text.
        self.assertIn("&lt;svg onload=alert(1)&gt;", page)
        self.assertIn("&lt;img src=x onerror=alert(1)&gt;", page)

    def test_unsafe_source_url_is_dropped_safe_one_kept(self):
        evil = issue(
            tensions=[
                {"institution_name": "Le Soleil", "source_kind": "media", "items": [
                    {"candidate_id": "c1", "title": "Lien sûr", "url": "https://lesoleil.example/a"},
                    {"candidate_id": "c2", "title": "Lien dangereux", "url": "javascript:alert(1)"},
                ]},
            ]
        )
        html = brief.dossier_html(evil, {})
        self.assertIn("https://lesoleil.example/a", html)
        self.assertNotIn("javascript:", html)
        self.assertNotIn("Lien dangereux", html)

    def test_units_surface_only_from_brief_eligible_items(self):
        eligible = {
            "c1": {"enrich": {"impacts": [{"units": [{"raw": "1 200 logements"}]}]}},
        }
        html = brief.dossier_html(issue(), eligible)
        self.assertIn("Repères à vérifier", html)
        self.assertIn("1 200 logements", html)
        self.assertNotIn("Repères à vérifier", brief.dossier_html(issue(), {}))

    def test_render_is_deterministic(self):
        args = ([story()], NOW.isoformat(), [issue()], collection())
        self.assertEqual(brief.render_brief(*args), brief.render_brief(*args))


if __name__ == "__main__":
    unittest.main()
