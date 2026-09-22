"""Primary documents PDCA — Ville / Gouv / Hydro / Soleil.

Falsifiable: official source_kind crowns correctly; Hydro cap holds;
a fight without official voice is flagged media_remix.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import harness

import cluster_issues
import enrich
import ingest_rss
import normalize


class SourceYamlPrimary(unittest.TestCase):
    def test_enabled_count_at_ceiling(self) -> None:
        srcs = ingest_rss.load_enabled_rss(harness.ROOT / "sources.yaml")
        ids = {s["id"] for s in srcs}
        self.assertEqual(len(srcs), 12)
        for need in ("ville-quebec", "gouv-quebec", "hydro-quebec", "le-soleil"):
            self.assertIn(need, ids)

    def test_official_kinds_and_caps(self) -> None:
        by_id = {s["id"]: s for s in ingest_rss.load_enabled_rss(harness.ROOT / "sources.yaml")}
        self.assertEqual(by_id["ville-quebec"]["source_kind"], "official")
        self.assertEqual(by_id["gouv-quebec"]["source_kind"], "official")
        self.assertEqual(by_id["hydro-quebec"]["source_kind"], "official")
        self.assertEqual(by_id["le-soleil"]["source_kind"], "media")
        self.assertEqual(by_id["hydro-quebec"]["max_items"], 40)
        self.assertLessEqual(by_id["hydro-quebec"]["max_items"], 40)


class OfficialGeo(unittest.TestCase):
    def test_ville_official_primary_crowns_city_without_token(self) -> None:
        c = {
            "title": "Travaux de réfection sur une artère locale",
            "summary": "Avis aux citoyens.",
            "url": "https://www.ville.quebec.qc.ca/x",
            "nest_role": "primary",
            "geo": "quebec-city",
            "source_kind": "official",
        }
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "quebec-city")
        self.assertIn("official", g["reason"])

    def test_hydro_official_province_parks_quebec(self) -> None:
        c = {
            "title": "Interruption planifiée dans le secteur Montmorency",
            "summary": "",
            "url": "https://nouvelles.hydroquebec.com/x",
            "nest_role": "province",
            "geo": "quebec",
            "source_kind": "official",
        }
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "quebec")
        self.assertIn("official", g["reason"])

    def test_official_world_fog_still_linked(self) -> None:
        c = {
            "title": "L'Iran et la Russie — note diplomatique",
            "summary": "",
            "nest_role": "province",
            "geo": "quebec",
            "source_kind": "official",
        }
        g = enrich.propose_geo(c, enrich.blob(c))
        self.assertEqual(g["geo"], "linked")


class MaxItemsCap(unittest.TestCase):
    def test_ingest_respects_max_items(self) -> None:
        xml = (
            "<?xml version='1.0'?><rss version='2.0'><channel><title>x</title>"
            + "".join(f"<item><title>t{i}</title><link>https://ex.test/{i}</link></item>" for i in range(100))
            + "</channel></rss>"
        ).encode("utf-8")

        class FakeResp:
            def __init__(self):
                self.headers = {"Content-Type": "application/rss+xml"}

            def read(self):
                return xml

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        src = {
            "id": "hydro-quebec",
            "name": "Hydro",
            "language": "fr",
            "geo": "quebec",
            "nest_role": "province",
            "source_kind": "official",
            "url": "https://example.test/rss",
            "max_items": 40,
        }
        with tempfile.TemporaryDirectory() as raw:
            dest = Path(raw)
            # point RAW_DIR temporarily
            old = ingest_rss.RAW_DIR
            ingest_rss.RAW_DIR = dest
            try:
                with mock.patch("ingest_rss.fetch_bytes", return_value=(xml, "application/rss+xml")):
                    from datetime import datetime, timezone

                    out = ingest_rss.ingest_one(src, datetime.now(timezone.utc))
                self.assertTrue(out["ok"])
                self.assertEqual(out["raw_item_count"], 100)
                self.assertEqual(out["item_count"], 40)
                self.assertTrue(out["capped"])
                self.assertEqual(out["source_kind"], "official")
                self.assertTrue(all(it["source_kind"] == "official" for it in out["items"]))
            finally:
                ingest_rss.RAW_DIR = old


class MediaRemixFlag(unittest.TestCase):
    def tearDown(self) -> None:
        cluster_issues.IN_PATH = harness.ROOT / "data" / "normalized" / "latest_enriched.json"
        cluster_issues.OUT_ISSUES = harness.ROOT / "data" / "issues" / "latest_issues.json"

    def test_fight_with_official_clears_remix(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp = d / "in.json"
            out = d / "out.json"

            def item(sid: str, title: str, geo: str, kind: str) -> dict:
                return {
                    "id": sid + title[:6],
                    "title": title,
                    "summary": "",
                    "url": f"https://example.test/{sid}",
                    "source_id": sid,
                    "source_name": sid,
                    "source_kind": kind,
                    "language": "fr",
                    "enrich": {"geo": {"geo": geo}, "topics": [{"topic": "trade"}]},
                }

            payload = {
                "candidates": [
                    item("gouv-quebec", "Le Canada privatisera ses aéroports", "quebec", "official"),
                    item("le-devoir", "Privatisation des aéroports: Québec réagit", "quebec", "media"),
                ]
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            written = json.loads(out.read_text(encoding="utf-8"))
            airport = next(i for i in written["issues"] if i["scar"] == "airport")
            self.assertEqual(airport["official_voice_count"], 1)
            self.assertFalse(airport["media_remix"])
            self.assertEqual(airport["tensions"][0]["source_kind"], "official")

    def test_fight_without_official_is_remix(self) -> None:
        with tempfile.TemporaryDirectory() as raw:
            d = Path(raw)
            inp = d / "in.json"
            out = d / "out.json"

            def item(sid: str, title: str) -> dict:
                return {
                    "id": sid,
                    "title": title,
                    "summary": "",
                    "url": f"https://example.test/{sid}",
                    "source_id": sid,
                    "source_name": sid,
                    "source_kind": "media",
                    "language": "fr",
                    "enrich": {
                        "geo": {"geo": "quebec-city"},
                        "topics": [{"topic": "other"}],
                    },
                }

            payload = {
                "candidates": [
                    item("journal-de-quebec", "Les priorités de Marchand"),
                    item("radio-canada-quebec", "Marchand dévoile sa liste"),
                ]
            }
            inp.write_text(json.dumps(payload), encoding="utf-8")
            cluster_issues.IN_PATH = inp
            cluster_issues.OUT_ISSUES = out
            cluster_issues.main()
            written = json.loads(out.read_text(encoding="utf-8"))
            marchand = next(i for i in written["issues"] if i["scar"] == "marchand")
            self.assertEqual(marchand["official_voice_count"], 0)
            self.assertTrue(marchand["media_remix"])


class NormalizeKind(unittest.TestCase):
    def test_normalize_passes_source_kind(self) -> None:
        cand = normalize.normalize_item(
            {
                "title": "Avis",
                "url": "https://www.ville.quebec.qc.ca/a",
                "body": "x",
                "source_kind": "official",
            },
            {
                "source_id": "ville-quebec",
                "source_name": "Ville",
                "language": "fr",
                "geo": "quebec-city",
                "nest_role": "primary",
                "source_kind": "official",
            },
        )
        self.assertEqual(cand["source_kind"], "official")


if __name__ == "__main__":
    unittest.main()
