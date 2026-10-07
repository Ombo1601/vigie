"""i18n catalogue parity and the hand-written FR/EN formatters (docs/I18N.md)."""
from __future__ import annotations

import ast
import json
import re
import unittest
from pathlib import Path

import harness  # noqa: F401 - puts scripts/ on sys.path

import composants
import i18n
import ownership

NBSP = " "
CAT = harness.ROOT / "scripts" / "i18n"


class CataloguePair(unittest.TestCase):
    def test_same_keys_and_placeholders(self):
        self.assertEqual(i18n.validate(), [])

    def test_both_files_are_flat_sorted_utf8_lf(self):
        for lang in i18n.LANGS:
            raw = (CAT / f"{lang}.json").read_bytes()
            self.assertFalse(raw.startswith(b"\xef\xbb\xbf"), "no BOM")
            self.assertNotIn(b"\r", raw, "LF only")
            doc = json.loads(raw.decode("utf-8"))
            self.assertEqual(list(doc), sorted(doc), f"{lang}.json keys are sorted")
            self.assertTrue(all(isinstance(v, str) for v in doc.values()))

    def test_a_missing_key_never_falls_back_to_french(self):
        with self.assertRaises(KeyError):
            i18n.t("no.such.key", "en")
        with self.assertRaises(KeyError):
            i18n.t("read", "en")  # a placeholder ({o}) with no value is an error too
        with self.assertRaises(KeyError):
            i18n.catalogue("de")

    def test_validate_reports_a_gap(self):
        fr = dict(i18n.catalogue("fr"))
        en = dict(i18n.catalogue("en"))
        saved = dict(i18n._CACHE)
        try:
            en.pop("skip")
            fr["read"] = "Lire chez {x}"
            i18n._CACHE.update({"fr": fr, "en": en})
            problems = i18n.validate()
        finally:
            i18n._CACHE.clear()
            i18n._CACHE.update(saved)
        self.assertTrue(any("en.json lacks skip" in p for p in problems))
        self.assertTrue(any("placeholders differ for read" in p for p in problems))

    def test_plural_forms_follow_each_language(self):
        self.assertEqual([i18n.plural_form(n, "fr") for n in (0, 1, 2, 5)], ["one", "one", "other", "other"])
        self.assertEqual([i18n.plural_form(n, "en") for n in (0, 1, 2, 5)], ["other", "one", "other", "other"])
        self.assertEqual(i18n.tn("n.voices", 0, "fr"), "0 voix")
        self.assertEqual(i18n.tn("n.events", 1, "fr"), "1 événement")
        self.assertEqual(i18n.tn("n.events", 0, "en"), "0 events")
        self.assertEqual(i18n.tn("n.events", 1234, "fr"), f"1{NBSP}234 événements")

    def test_every_key_the_kit_names_exists(self):
        source = (harness.SCRIPTS / "composants.py").read_text(encoding="utf-8")
        literal = set(re.findall(r"""\bt\(\s*f?["']([a-z0-9_.-]+)["']""", source))
        plural = {k + s for k in re.findall(r"""\btn\(\s*["']([a-z0-9_.]+)["']""", source) for s in (".one", ".other")}
        families = {
            "tier": composants.TIERS, "act": composants.ACTIVITIES,
            # the class fallbacks of scripts/ownership.py `words` (never a word for `independent`)
            "own": (*ownership.CLASS_WORDED, "unworded", "unverified"),
            "origin": composants.ORIGINS, "anchor": composants.ANCHOR_TYPES, "sil": composants.SILENCE_STATES,
            "neigh.reason": composants.NEIGHBOUR_REASONS, "roster": composants.ROSTER_STATES[3:],
            "ed.title": composants.PERIODS, "ed.end": composants.PERIODS, "impact": composants.IMPACTS,
            "status": composants.RW_STATUSES, "dir": composants.RW_DIRECTIONS, "type": composants.RW_TYPES,
            "unit": composants.FACT_UNITS, "kind": composants.FACT_KINDS, "why.level": composants.TIERS,
            "lang.chip": ("fr", "en", "both"), "pledge": ("1", "2", "3"),
        }
        dynamic = {f"{p}.{c}" for p, codes in families.items() for c in codes}
        dynamic |= {f"rules.{i}.{x}" for i in (1, 2, 3) for x in ("h", "p")}
        for lang in i18n.LANGS:
            cat = i18n.catalogue(lang)
            for key in sorted(literal | plural | dynamic):
                if key.endswith(".") or key in ("n", "own", "sil", "lang.chip", "tier", "act", "origin"):
                    continue
                self.assertIn(key, cat, f"{lang}.json lacks {key}")

    def test_every_key_the_event_ranking_and_chip_name_exists(self):
        import ranking_events

        source = (harness.SCRIPTS / "ranking_events.py").read_text(encoding="utf-8")
        literal = set(re.findall(r"""["']((?:rank|chip)\.[a-z0-9_.-]+|tier\.possible|why\.single)["']""", source))
        dynamic = {f"rank.geo.{g}" for g in (*ranking_events.GEO_RANK, "unknown")}
        dynamic |= {f"rank.decided.{d}" for d in ("first", *ranking_events.CRITERIA, "id", "none")}
        dynamic |= {"rank.geo.quebec-city.anchor", "chip.auto.model", "chip.auto.unrecorded",
                    "chip.auto.unmeasured", "chip.probable", "chip.certain"}
        for lang in i18n.LANGS:
            cat = i18n.catalogue(lang)
            for key in sorted(literal | dynamic):
                if key.endswith("."):
                    continue
                self.assertIn(key, cat, f"{lang}.json lacks {key}")

    def test_script_strings_are_all_in_the_island(self):
        js = (harness.ROOT / "public" / "assets" / "evenements.js").read_text(encoding="utf-8")
        used = set(re.findall(r"""\b(?:text|count)\(\s*'([a-z_]+)'""", js))
        island = {k[3:].split(".")[0] for k in i18n.catalogue("fr") if k.startswith("js.")}
        self.assertTrue(used, "the script reads texts")
        self.assertLessEqual(used, island, f"script reads keys the island lacks: {sorted(used - island)}")


class Formatters(unittest.TestCase):
    def test_dates_fr_en(self):
        iso = "2026-10-06T18:05:00Z"
        self.assertEqual(i18n.fmt_date(iso, "fr"), "6 octobre 2026")
        self.assertEqual(i18n.fmt_date(iso, "en"), "October 6, 2026")
        self.assertEqual(i18n.fmt_date("2026-10-01T15:00:00Z", "fr"), "1er octobre 2026")
        self.assertEqual(i18n.fmt_date("2026-10-01T15:00:00Z", "fr", weekday=True), "jeudi 1er octobre 2026")
        self.assertEqual(i18n.fmt_date("2026-10-01T15:00:00Z", "en", weekday=True), "Thursday, October 1, 2026")
        self.assertEqual(i18n.fmt_day("2026-10-01T15:00:00Z", "fr"), "1er oct.")
        self.assertEqual(i18n.fmt_day("2026-10-01T15:00:00Z", "en"), "Oct 1")
        self.assertEqual(i18n.fmt_date(iso, "fr", short=True), "6 oct. 2026")
        self.assertEqual(i18n.fmt_ymd("2026-09-15", "fr"), "15 sept. 2026")
        self.assertEqual(i18n.fmt_ymd("2026-10-01", "en"), "Oct 1, 2026")
        self.assertEqual(i18n.fmt_ymd("2026-10-01", "fr", short=False), "1er octobre 2026")

    def test_times_fr_en(self):
        self.assertEqual(i18n.fmt_time("2026-10-06T18:05:00Z", "fr"), "14 h 05")
        self.assertEqual(i18n.fmt_time("2026-10-06T18:05:00Z", "en"), "2:05 p.m.")
        self.assertEqual(i18n.fmt_time("2026-10-06T18:00:00Z", "fr"), "14 h")
        self.assertEqual(i18n.fmt_time("2026-10-06T04:30:00Z", "en"), "12:30 a.m.")
        self.assertEqual(i18n.fmt_time("2026-10-06T16:00:00Z", "en"), "12:00 p.m.")
        self.assertEqual(i18n.fmt_datetime("2026-10-06T18:05:00Z", "fr"), "6 octobre 2026 à 14 h 05")
        self.assertEqual(i18n.fmt_datetime("2026-10-06T18:05:00Z", "en"), "October 6, 2026, 2:05 p.m.")

    def test_toronto_wall_clock_crosses_midnight_and_dst(self):
        # 03:30 UTC on 6 Oct is still the evening of the 5th in Quebec City.
        self.assertEqual(i18n.fmt_date("2026-10-06T03:30:00Z", "fr"), "5 octobre 2026")
        self.assertEqual(i18n.fmt_time("2026-10-06T03:30:00Z", "fr"), "23 h 30")
        # Spring forward: second Sunday of March 2026 is the 8th, 02:00 local = 07:00 UTC.
        self.assertEqual(i18n.fmt_time("2026-03-08T06:59:00Z", "fr"), "1 h 59")
        self.assertEqual(i18n.fmt_time("2026-03-08T07:00:00Z", "fr"), "3 h")
        # Fall back: first Sunday of November 2026 is the 1st, 02:00 EDT = 06:00 UTC.
        self.assertEqual(i18n.fmt_time("2026-11-01T05:59:00Z", "fr"), "1 h 59")
        self.assertEqual(i18n.fmt_time("2026-11-01T06:00:00Z", "fr"), "1 h")
        self.assertEqual(i18n.fmt_time("2026-01-15T18:05:00Z", "fr"), "13 h 05")  # EST
        self.assertEqual(i18n.fmt_time("2026-07-15T18:05:00Z", "fr"), "14 h 05")  # EDT
        self.assertEqual(i18n.toronto_offset_hours(i18n.parse_instant("2027-03-14T07:00:00Z")), -4)
        self.assertEqual(i18n.toronto_offset_hours(i18n.parse_instant("2027-03-14T06:59:00Z")), -5)

    def test_offsets_and_zoneless_stamps(self):
        self.assertEqual(i18n.fmt_time("2026-10-06T14:05:00-04:00", "fr"), "14 h 05")
        self.assertEqual(i18n.fmt_time("2026-10-06T18:05:00", "fr"), "14 h 05")  # zoneless = UTC
        self.assertEqual(i18n.fmt_time("2026-10-06T18:05:00+00:00", "en"), "2:05 p.m.")

    def test_bad_input_is_empty_never_a_guess(self):
        for bad in (None, "", "not a date", "2026-13-45T00:00:00Z", 12):
            self.assertEqual(i18n.fmt_time(bad, "fr"), "")
            self.assertEqual(i18n.fmt_date(bad, "en"), "")
        self.assertEqual(i18n.fmt_ymd("nope", "fr"), "")
        self.assertEqual(i18n.fmt_money("x", "fr"), "")
        self.assertEqual(i18n.fmt_int(None, "en"), "")
        self.assertIsNone(i18n.local_hour(""))

    def test_money_is_integer_cents(self):
        self.assertEqual(i18n.fmt_money(123450, "fr"), f"1{NBSP}234,50{NBSP}$")
        self.assertEqual(i18n.fmt_money(123450, "en"), "$1,234.50")
        self.assertEqual(i18n.fmt_money(5, "fr"), f"0,05{NBSP}$")
        self.assertEqual(i18n.fmt_money(100000000, "en"), "$1,000,000.00")
        self.assertEqual(i18n.fmt_money(-250, "en"), "−$2.50")
        self.assertEqual(i18n.fmt_money(-250, "fr"), f"−2,50{NBSP}$")

    def test_numbers_percentages_durations(self):
        self.assertEqual(i18n.fmt_int(1234567, "fr"), f"1{NBSP}234{NBSP}567")
        self.assertEqual(i18n.fmt_int(1234567, "en"), "1,234,567")
        self.assertEqual(i18n.fmt_int(-12, "en"), "−12")
        self.assertEqual(i18n.fmt_percent_bp(350, "fr"), f"3,5{NBSP}%")
        self.assertEqual(i18n.fmt_percent_bp(350, "en"), "3.5%")
        self.assertEqual(i18n.fmt_percent_bp(1205, "en"), "12.05%")
        self.assertEqual(i18n.fmt_percent_bp(400, "fr"), f"4{NBSP}%")
        self.assertEqual(i18n.fmt_duration(30, "fr"), "1 min")
        self.assertEqual(i18n.fmt_duration(45 * 60, "en"), "45 min")
        self.assertEqual(i18n.fmt_duration(125 * 60, "fr"), "2 h 05")
        self.assertEqual(i18n.fmt_duration(125 * 60, "en"), "2 hr 5 min")
        self.assertEqual(i18n.fmt_duration(3 * 3600, "fr"), "3 h")
        self.assertEqual(i18n.fmt_duration(72 * 3600, "en"), "72 hr")

    def test_a_sentence_that_ends_on_a_time_has_one_period(self):
        self.assertEqual(i18n.t("end.next", "en", tm="4:10 p.m."), "Next collection around 4:10 p.m.")
        self.assertEqual(i18n.t("end.next", "fr", tm="16 h 10"), "Prochaine collecte vers 16 h 10.")
        lede = i18n.t("ed.lede", "en", events="5 events", institutions="9 institutions followed", tm="10:12 a.m.")
        self.assertNotIn("..", lede)
        page = composants.demo_pages()["en/evenements.html"]
        self.assertNotIn("m..", page)

    def test_no_wall_clock_or_locale_in_the_formatters(self):
        for name in ("i18n.py", "composants.py"):
            tree = ast.parse((harness.SCRIPTS / name).read_text(encoding="utf-8"))
            imported = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    imported.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom):
                    imported.add((node.module or "").split(".")[0])
                elif isinstance(node, ast.Attribute):
                    self.assertNotIn(node.attr, {"now", "utcnow", "today", "monotonic", "perf_counter"}, f"{name}: wall clock")
            self.assertFalse(imported & {"locale", "zoneinfo", "time", "random", "uuid", "os"}, f"{name}: {imported}")


if __name__ == "__main__":
    unittest.main()
