"""Branch tests for enrich.propose_geo and related proposals."""
from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path

import harness

import enrich


def _cand(**kwargs) -> dict:
    base = {
        "id": "test",
        "title": "Titre",
        "summary": "",
        "url": "https://example.test/x",
        "geo": "quebec",
        "nest_role": "province",
    }
    base.update(kwargs)
    return base


class StrictCityTokens(unittest.TestCase):
    def test_bare_tramway_is_not_a_city_token(self) -> None:
        self.assertIsNone(enrich.STRICT_CITY.search("Le tramway sera voté demain"))
        self.assertIsNone(enrich.STRICT_CITY.search("tramway"))

    def test_tramway_de_quebec_is_a_city_token(self) -> None:
        self.assertIsNotNone(enrich.STRICT_CITY.search("Le tramway de Québec divise"))

    def test_borough_and_mayor_tokens(self) -> None:
        for text in (
            "Quebec City council",
            "Ville de Québec",
            "Capitale-Nationale",
            "le maire de Québec",
            "Bruno Marchand",
            "Sainte-Foy",
            "Limoilou",
            "FCVQ",
        ):
            with self.subTest(text=text):
                self.assertIsNotNone(enrich.STRICT_CITY.search(text), text)


class ProposeGeo(unittest.TestCase):
    def test_city_token_crowns_quebec_city(self) -> None:
        c = _cand(title="Incendie à Limoilou", nest_role="province")
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "quebec-city")
        self.assertEqual(g["status"], "proposed")

    def test_world_fog_without_city_parks_linked(self) -> None:
        c = _cand(
            title="L'Iran et la Russie s'entendent",
            nest_role="province",
            geo="quebec",
        )
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "linked")
        self.assertIn("world-fog", g["reason"])

    def test_world_fog_with_city_token_stays_city(self) -> None:
        c = _cand(title="Trump visitera Limoilou", nest_role="linked")
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "quebec-city")

    def test_primary_without_local_evidence_parks_linked(self) -> None:
        c = _cand(
            title="Un festival de cinéma à Toronto",
            nest_role="primary",
            geo="quebec-city",
        )
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "linked")
        self.assertIn("source geography is not article geography", g["reason"])

    def test_province_with_hint_stays_quebec(self) -> None:
        c = _cand(title="Legault à Montréal", nest_role="province")
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "quebec")

    def test_province_without_hint_parks_linked(self) -> None:
        c = _cand(title="Un sommet à Tokyo", nest_role="province", geo="quebec")
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "linked")
        self.assertIn("cloak", g["reason"])

    def test_linked_with_province_hint_promotes_quebec(self) -> None:
        c = _cand(title="Ottawa table un projet", nest_role="linked", geo="linked")
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "quebec")

    def test_linked_without_hint_stays_linked(self) -> None:
        c = _cand(title="Un musée ouvre à Lyon", nest_role="linked", geo="linked")
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "linked")

    def test_unknown_nest_falls_through_linked(self) -> None:
        c = _cand(title="Rien ici", nest_role="mystery", geo="unknown")
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "linked")


class TopicsImpactsBlob(unittest.TestCase):
    def test_blob_joins_nonempty(self) -> None:
        self.assertEqual(enrich.blob({"title": "A", "summary": None, "url": "U"}), "A")

    def test_topics_other_when_empty(self) -> None:
        self.assertEqual(
            enrich.propose_topics("zzzz"),
            [{"status": "proposed", "topic": "other"}],
        )

    def test_topics_housing_and_energy(self) -> None:
        text = "Le loyer explose; Hydro hausse le tarif d'électricité"
        labels = [t["topic"] for t in enrich.propose_topics(text)]
        self.assertIn("housing", labels)
        self.assertIn("energy/hydro", labels)

    def test_impacts_skip_other_and_dedupe(self) -> None:
        topics = [
            {"topic": "other"},
            {"topic": "housing"},
            {"topic": "housing"},
            {"topic": "economy"},
        ]
        impacts = enrich.propose_impacts(topics)
        labels = [i["label"] for i in impacts]
        self.assertEqual(labels, ["housing", "price"])
        self.assertTrue(all(i["status"] == "proposed" for i in impacts))
        self.assertTrue(all("units" in i for i in impacts))

    def test_enrich_one_never_writes_truth(self) -> None:
        out = enrich.enrich_one(_cand(title="Logement à Limoilou"))
        self.assertEqual(out["enrich_status"], "proposed")
        self.assertEqual(out["enrich"]["geo"]["status"], "proposed")
        self.assertEqual(out["enrich"]["claims"], [])
        self.assertTrue(out["enrich"]["method"].startswith("rules-"))
        self.assertIn("impact-units", out["enrich"]["method"])
        self.assertIn("claims", out["enrich"]["method"])


class EnrichMain(unittest.TestCase):
    def tearDown(self) -> None:
        enrich.IN_PATH = harness.ROOT / "data" / "normalized" / "latest_candidates.json"
        enrich.OUT_PATH = harness.ROOT / "data" / "normalized" / "latest_enriched.json"

    def test_main_missing_input_returns_1(self) -> None:
        enrich.IN_PATH = Path("/no/such/candidates.json")
        self.assertEqual(enrich.main(), 1)

    def test_main_writes_enriched(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp = d / "in.json"
            out = d / "out.json"
            payload = {
                "candidates": [
                    _cand(title="Logement à Limoilou"),
                    _cand(title="Un sommet à Tokyo", nest_role="province"),
                ]
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            enrich.IN_PATH = inp
            enrich.OUT_PATH = out
            self.assertEqual(enrich.main(), 0)
            written = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(written["candidate_count"], 2)
            geos = [c["enrich"]["geo"]["geo"] for c in written["candidates"]]
            self.assertEqual(geos[0], "quebec-city")
            self.assertEqual(geos[1], "linked")
            self.assertIn("word-boundary", written["method"])
            self.assertIn("claims", written["method"])
            self.assertIn("candidates_with_claims", written)


class LiveHelicopterGuard(unittest.TestCase):
    """Live store: the 2026-09-16 TV-helicopter URL must not be quebec-city."""

    def test_live_helicopter_url_not_city(self) -> None:
        path = harness.ROOT / "data" / "normalized" / "latest_candidates.json"
        if not path.is_file():
            self.skipTest("no live candidates")
        payload = json.loads(path.read_text(encoding="utf-8"))
        needle = "helicoptere-pres-de-los-angeles"
        hits = [
            c
            for c in payload.get("candidates") or []
            if needle in (c.get("url") or "")
        ]
        if not hits:
            self.skipTest("helicopter item not in this ingest")
        for c in hits:
            geo = enrich.propose_geo(c, enrich.blob(c))["geo"]
            self.assertNotEqual(geo, "quebec-city", c.get("title"))

    def test_live_tv_without_word_levis_not_city_unless_other_token(self) -> None:
        path = harness.ROOT / "data" / "normalized" / "latest_candidates.json"
        if not path.is_file():
            self.skipTest("no live candidates")
        payload = json.loads(path.read_text(encoding="utf-8"))
        place_levis = re.compile(r"\bl[ée]vis\b", re.I)
        tv = re.compile(r"t[ée]l[ée]vision", re.I)
        failures = []
        for c in payload.get("candidates") or []:
            text = enrich.blob(c)
            if not tv.search(text):
                continue
            if place_levis.search(text):
                continue
            geo = enrich.propose_geo(c, enrich.blob(c))["geo"]
            if geo == "quebec-city" and not (
                enrich.STRICT_CITY.search(place_levis.sub(" ", text))
                or enrich.CITY_LOCATION.search(c.get("title") or "")
            ):
                failures.append(c.get("title"))
        self.assertEqual(failures, [])


class TopicLexicon(unittest.TestCase):
    """Broadened topics: precision first, a wrong label is worse than `other`."""

    def _labels(self, text: str) -> list[str]:
        return [t["topic"] for t in enrich.propose_topics(text)]

    def test_common_stems_are_recognized(self) -> None:
        cases = {
            "environment": "Le plan climatique de Québec",
            "education": "L’Université Laval ouvre un programme",
            "security": "Un incendie majeur à Limoilou",
            "health": "Le CHSLD manque de personnel",
            "economy": "Les salaires stagnent dans la région",
            "law": "Les élections municipales approchent",
            "energy/hydro": "Hydro-Québec pose un pylône à Neufchâtel",
            "transport": "Un piéton heurté sur le boulevard",
            "death": "Les obsèques auront lieu mardi",
            "housing": "Les locataires réclament un répit",
            "trade": "L’aluminerie augmente ses exportations",
            "culture": "Le théâtre du Trident présente une pièce",
        }
        for topic, text in cases.items():
            with self.subTest(topic=topic):
                self.assertIn(topic, self._labels(text))

    def test_precision_scars_hold(self) -> None:
        # different != rent, villages != GES, importants != import, environs != environment
        self.assertEqual(self._labels("Un projet différent pour les villages"), ["other"])
        self.assertNotIn("trade", self._labels("Des changements importants"))
        self.assertNotIn("environment", self._labels("Les environs de la ville"))


if __name__ == "__main__":
    unittest.main()
