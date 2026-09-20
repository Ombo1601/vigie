"""La promesse (promesse-v1): what the record knows about the official voice.

Locks: no claim before two recorded editions; unanswered counts are measured
editions, never a verdict; an official document entering the dossier is stated
as presence, never as an "answer"; malformed entries are skipped; the line
appears on the brief, the récit and the departure screen; deterministic.
"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

import harness  # noqa: F401 - puts scripts/ on sys.path

import depart
import promesse
import recits
import resident_brief as brief

NOW = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)


def _timeline(officials, *, start=NOW - timedelta(days=4)):
    entries = []
    for i, official in enumerate(officials):
        entries.append({
            "ts": (start + timedelta(days=i)).isoformat(),
            "sources": 2, "items": 3 + i,
            **({"official": official} if official is not None else {}),
        })
    return {
        "status": "proposed", "method": "dossier-history-v1",
        "first_seen": entries[0]["ts"] if entries else None,
        "last_seen": entries[-1]["ts"] if entries else None,
        "editions_seen": len(entries), "editions_missed": 0,
        "timeline": entries,
    }


def issue(question="Où en est le tramway ?", officials=(0, 0, 0), **over):
    base = {
        "issue_id": "aa11bb22cc33dd44",
        "question": question,
        "geo_focus": ["quebec-city"],
        "source_count": 2,
        "official_voice_count": officials[-1] if officials else 0,
        "media_remix": not any(officials),
        "tracking": _timeline(list(officials)),
        "tensions": [],
        "silence": {},
    }
    base.update(over)
    return base


class Status(unittest.TestCase):
    def test_no_claim_before_two_recorded_editions(self):
        self.assertIsNone(promesse.status_of(issue(officials=(0,))))
        self.assertIsNone(promesse.status_of(issue(officials=())))
        self.assertIsNone(promesse.status_of({}))
        self.assertIsNone(promesse.status_of({"tracking": "nope"}))
        self.assertIsNone(promesse.line_of(issue(officials=(0,))))

    def test_unanswered_counts_measured_editions(self):
        status = promesse.status_of(issue(officials=(0, 0, 0, 0, 0)))
        self.assertEqual(status["status"], "unanswered")
        self.assertEqual(status["editions"], 5)
        self.assertIn("Aucun document officiel dans les 5 éditions suivies",
                      promesse.line_of(issue(officials=(0, 0, 0, 0, 0))))

    def test_present_from_the_first_followed_edition(self):
        line = promesse.line_of(issue(officials=(1, 1)))
        self.assertIn("dès la première édition suivie", line)

    def test_official_entered_mid_history_names_the_date(self):
        timeline = issue(officials=(0, 0, 1, 1))
        line = promesse.line_of(timeline)
        self.assertEqual(promesse.status_of(timeline)["answered_index"], 2)
        self.assertIn(timeline["tracking"]["timeline"][2]["ts"], line)
        self.assertIn("après 2 éditions sans document officiel", line)

    def test_official_entered_this_edition_is_stated_as_such(self):
        line = promesse.line_of(issue(officials=(0, 0, 1)))
        self.assertIn("cette édition", line)
        self.assertIn("après 2 éditions", line)

    def test_entries_without_the_field_are_never_counted(self):
        # One measured zero + unknown editions: fewer than two measured = no claim.
        self.assertIsNone(promesse.status_of(issue(officials=(None, None, 0))))
        status = promesse.status_of(issue(officials=(None, 0, 0)))
        self.assertEqual(status["editions"], 2)
        self.assertEqual(status["status"], "unanswered")

    def test_unrecorded_editions_do_not_extend_the_silence_claim(self):
        status = promesse.status_of(issue(officials=(None, None, 0, 0, 0)))
        self.assertEqual(status["editions"], 3)

    def test_hostile_text_never_breaks_the_html(self):
        evil = issue(officials=(0, 0), question="<script>alert(1)</script>")
        html = promesse.html_of(evil)
        self.assertNotIn("<script>", html)
        self.assertIn("dossier-promesse", html)


class Surfaces(unittest.TestCase):
    def _collection(self):
        return {"fetched_at": NOW.isoformat(), "enabled_rss": [], "results": []}

    def test_brief_dossier_carries_the_line(self):
        page = brief.render_brief([], NOW.isoformat(), [issue(officials=(0, 0, 0))], self._collection())
        self.assertIn("Aucun document officiel dans les 3 éditions suivies", page)
        self.assertIn("dossier-promesse", page)

    def test_brief_without_tracking_carries_no_line(self):
        page = brief.render_brief([], NOW.isoformat(), [{"issue_id": "x", "question": "Q ?"}], self._collection())
        self.assertNotIn("dossier-promesse", page)

    def test_recit_page_carries_the_line(self):
        page = recits.render_recit(issue(officials=(0, 0)), {}, {}, slug="aa11bb22cc33dd44",
                                   rw_ok=False, edge_streets={}, edge_issues={})
        self.assertIn("Aucun document officiel dans les 2 éditions suivies", page)

    def test_departure_screen_features_the_longest_silence(self):
        short = issue(issue_id="bb22cc33dd44ee55", question="Question courte", officials=(0, 0))
        long = issue(issue_id="cc33dd44ee55ff66", question="Question longue", officials=(0, 0, 0, 0))
        page = depart.render_depart({"events": [], "fetched_at": NOW.isoformat()}, [short, long], {}, {"seals": []}, NOW.isoformat())
        self.assertIn("La question", page)
        self.assertIn("Question longue", page)
        self.assertIn("Aucun document officiel dans les 4 éditions suivies", page)
        self.assertIn("/dossiers/cc33dd44ee55ff66.html", page)

    def test_departure_screen_without_dossiers_hides_the_block(self):
        page = depart.render_depart({"events": [], "fetched_at": NOW.isoformat()}, [], {}, {"seals": []}, NOW.isoformat())
        self.assertNotIn("La question", page)

    def test_is_deterministic(self):
        args = (issue(officials=(0, 0, 1)),)
        self.assertEqual(promesse.line_of(*args), promesse.line_of(*args))
        self.assertEqual(brief.render_brief([], NOW.isoformat(), [issue()], self._collection()),
                         brief.render_brief([], NOW.isoformat(), [issue()], self._collection()))


if __name__ == "__main__":
    unittest.main()
