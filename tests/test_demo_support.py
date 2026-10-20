"""Unit tests for the demo helpers.  Run from the project root:  python -m unittest"""

import os
import sys
import unittest
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_support as ds  # noqa: E402
from scoring import parse_scoring_response  # noqa: E402

STATUSES = {
    "rejected": "Rejection Email Sent",
    "invited_no_reply": "Interview Invite Sent - No Reply",
    "scheduled": "Interview Scheduled",
    "hired": "Hired",
}
NOW = datetime(2026, 10, 20, 15, 0)
HEADER = ["status"] + [f"c{i}" for i in range(17)]


class Tags(unittest.TestCase):
    def test_tag_email(self):
        self.assertEqual(ds.tag_email("ariel.lin@example.com", "k3f9"), "ariel.lin+k3f9@example.com")
        self.assertEqual(ds.tag_email("not-an-email", "k3f9"), "not-an-email")
        self.assertEqual(ds.tag_email("a@b.com", ""), "a@b.com")

    def test_belongs_to_visitor(self):
        self.assertTrue(ds.belongs_to_visitor("ariel.lin+k3f9@example.com", "k3f9"))
        self.assertFalse(ds.belongs_to_visitor("ariel.lin+zzzz@example.com", "k3f9"))
        self.assertFalse(ds.belongs_to_visitor("ariel.lin@example.com", "k3f9"))
        self.assertFalse(ds.belongs_to_visitor("ariel.lin+k3f9@example.com", ""))

    def test_new_tag_is_valid(self):
        for _ in range(50):
            self.assertTrue(ds.is_valid_tag(ds.new_visitor_tag()))
        self.assertFalse(ds.is_valid_tag("a@b"))
        self.assertFalse(ds.is_valid_tag(None))

    def test_seed_email(self):
        self.assertTrue(ds.is_seed_email("x.y@seed.example.com"))
        self.assertFalse(ds.is_seed_email("x.y+ab12@example.com"))


def row(email, applied):
    r = [""] * 18
    r[3] = email
    r[7] = applied
    return r


class Housekeeping(unittest.TestCase):
    def fresh_seed(self):
        return row("a@seed.example.com", (NOW - timedelta(days=2)).strftime("%Y-%m-%d %H:%M"))

    def test_deletes_only_old_visitor_rows(self):
        old = row("v+ab12@example.com", (NOW - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M"))
        new = row("v+cd34@example.com", (NOW - timedelta(hours=2)).strftime("%Y-%m-%d %H:%M"))
        delete, reseed = ds.plan_housekeeping([HEADER, self.fresh_seed(), old, new], NOW)
        self.assertEqual(delete, [3])
        self.assertFalse(reseed)

    def test_seeds_when_missing(self):
        delete, reseed = ds.plan_housekeeping([HEADER], NOW)
        self.assertEqual((delete, reseed), ([], True))

    def test_replaces_stale_seed(self):
        stale = row("a@seed.example.com", (NOW - timedelta(days=9)).strftime("%Y-%m-%d %H:%M"))
        delete, reseed = ds.plan_housekeeping([HEADER, stale], NOW)
        self.assertEqual((delete, reseed), ([2], True))

    def test_force_reset_keeps_header_only(self):
        rows = [HEADER, self.fresh_seed(), row("v+ab12@example.com", NOW.strftime("%Y-%m-%d %H:%M"))]
        delete, reseed = ds.plan_housekeeping(rows, NOW, force_reset=True)
        self.assertEqual((delete, reseed), ([2, 3], True))

    def test_unreadable_dates_are_kept(self):
        delete, _ = ds.plan_housekeeping([HEADER, self.fresh_seed(), row("v+ab12@example.com", "garbage")], NOW)
        self.assertEqual(delete, [])

    def test_accepts_seconds_in_timestamps(self):
        old = row("v+ab12@example.com", (NOW - timedelta(hours=30)).strftime("%Y-%m-%d %H:%M:%S"))
        delete, _ = ds.plan_housekeeping([HEADER, self.fresh_seed(), old], NOW)
        self.assertEqual(delete, [3])

    def test_visitor_rows_only_matches_that_visitor(self):
        rows = [HEADER, self.fresh_seed(), row("a+k3f9@example.com", ""), row("b+zzzz@example.com", ""), row("c+k3f9@example.com", "")]
        self.assertEqual(ds.visitor_rows(rows, "k3f9"), [3, 5])
        self.assertEqual(ds.visitor_rows(rows, ""), [])

    def test_contiguous_ranges_delete_from_the_bottom(self):
        self.assertEqual(ds.contiguous_ranges([2, 3, 4, 9, 10, 20]), [(20, 20), (9, 10), (2, 4)])
        self.assertEqual(ds.contiguous_ranges([]), [])


class SeedData(unittest.TestCase):
    def setUp(self):
        self.rows = ds.build_seed_rows(NOW, STATUSES)

    def test_shape(self):
        self.assertEqual(len(self.rows), 40)
        for r in self.rows:
            self.assertEqual(len(r), 18)
            self.assertTrue(ds.is_seed_email(r[3]))
        self.assertEqual(len({r[3] for r in self.rows}), 40)

    def test_deterministic(self):
        self.assertEqual(self.rows, ds.build_seed_rows(NOW, STATUSES))

    def test_scores_match_their_arithmetic_and_status_rules(self):
        for r in self.rows:
            self.assertTrue(0 <= r[4] <= 100)
            self.assertTrue(r[17].startswith("70 baseline"))
            if r[4] < 60:
                self.assertEqual(r[0], STATUSES["rejected"])
                self.assertEqual(r[5], "")  # auto-rejected: no manager decision

    def test_calculation_roundtrip(self):
        for r in self.rows:
            # last number after '=' is the raw total; the score is that value clamped to 0-100
            raw = float(r[17].split("=")[1].split("(")[0].strip())
            self.assertEqual(r[4], int(round(max(0, min(100, raw)))))

    def test_dates_are_valid_and_not_in_the_future(self):
        for r in self.rows:
            applied = ds.parse_time(r[7])
            self.assertIsNotNone(applied)
            self.assertLess(applied, NOW)
            self.assertGreaterEqual(applied, NOW - timedelta(days=21))
            if r[9]:
                self.assertIsNotNone(ds.parse_time(r[9]))

    def test_only_final_or_invited_statuses(self):
        allowed = set(STATUSES.values())
        for r in self.rows:
            self.assertIn(r[0], allowed)

    def test_funnel_has_a_story(self):
        invited = [r for r in self.rows if r[0] != STATUSES["rejected"]]
        self.assertTrue(8 <= len(invited) <= 24, len(invited))
        self.assertTrue(1 <= sum(1 for r in self.rows if r[0] == STATUSES["hired"]) <= 3)
        junior = [r for r in self.rows if float(r[12]) <= 2]
        senior = [r for r in self.rows if float(r[12]) > 5]
        rate = lambda g: sum(1 for r in g if r[0] != STATUSES["rejected"]) / len(g)  # noqa: E731
        self.assertLess(rate(junior), rate(senior))
        self.assertTrue(any("budget" in r[6] for r in self.rows))  # salary-driven manager declines exist

    def test_manager_comments_are_only_on_manager_declines(self):
        for r in self.rows:
            if r[6]:
                self.assertEqual(r[5], "FALSE")


if __name__ == "__main__":
    unittest.main()
