"""Unit tests for the analytics summary behind the AI report.  Run from the project root:  python -m unittest"""

import os
import sys
import unittest
from datetime import datetime

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import demo_support  # noqa: E402
import insights  # noqa: E402

STATUSES = {
    "rejected": "Rejection Email Sent",
    "invited_no_reply": "Interview Invite Sent - No Reply",
    "scheduled": "Interview Scheduled",
    "hired": "Hired",
}
INVITED = [STATUSES["invited_no_reply"], STATUSES["scheduled"], STATUSES["hired"]]
COLUMNS = [
    "status", "job_applied", "name", "email", "score", "manager_approved", "manager_comment", "applied_at",
    "action_at", "interview_at", "meeting_link", "highest_education", "total_experience_years",
    "expected_salary", "skills_extracted", "score_reason", "resume", "score_calculation",
]


def seed_frame():
    rows = demo_support.build_seed_rows(datetime(2026, 10, 20, 15, 0), STATUSES)
    return pd.DataFrame(rows, columns=COLUMNS)


class ParseCalculation(unittest.TestCase):
    def test_reads_rules_and_points(self):
        text = "70 baseline; -15 Missing degree; +5 Cloud experience = 60"
        self.assertEqual(insights.parse_calculation(text), [(-15.0, "Missing degree"), (5.0, "Cloud experience")])

    def test_handles_cap_suffix_and_baseline_only(self):
        self.assertEqual(insights.parse_calculation("70 baseline; -90 Everything = -20 (capped to 0)"), [(-90.0, "Everything")])
        self.assertEqual(insights.parse_calculation("70 baseline = 70"), [])
        self.assertEqual(insights.parse_calculation(""), [])
        self.assertEqual(insights.parse_calculation(None), [])


class Categorize(unittest.TestCase):
    def test_manager_and_ai_reasons(self):
        self.assertEqual(insights.categorize_reason({"manager_comment": "Over our budget"}), "Manager: salary/budget mismatch")
        self.assertEqual(insights.categorize_reason({"manager_comment": "Closer match elsewhere"}), "Manager: other mismatch")
        self.assertEqual(insights.categorize_reason({"score_reason": "Expected salary is above budget."}), "AI: salary expectation too high")
        self.assertEqual(insights.categorize_reason({"score_reason": "Lacks SQL experience."}), "AI: insufficient skills/experience")
        self.assertEqual(insights.categorize_reason({"score_reason": "Salary within budget."}), "AI: other mismatch")


class ParseReport(unittest.TestCase):
    GOOD = (
        '```json\n{"summary": "S", "findings": [{"title": "T1", "detail": "D1"}, "junk", {"detail": "D2"}],'
        ' "recommendations": [{"title": "R1", "detail": "RD1"}], "caveat": "C"}\n```'
    )

    def test_parses_fenced_json_and_skips_junk(self):
        report = insights.parse_report(self.GOOD)
        self.assertEqual(report["summary"], "S")
        self.assertEqual([f["detail"] for f in report["findings"]], ["D1", "D2"])
        self.assertEqual(report["recommendations"][0]["title"], "R1")
        self.assertEqual(report["caveat"], "C")

    def test_unusable_text_returns_none(self):
        for bad in ("not json", "", None, "{broken", '{"foo": 1}', "[1, 2]"):
            self.assertIsNone(insights.parse_report(bad))


class Summary(unittest.TestCase):
    def setUp(self):
        self.df = seed_frame()
        self.summary = insights.build_summary(self.df, INVITED, STATUSES["hired"], 60, is_demo=True)

    def test_headline_numbers_match_the_data(self):
        s = self.summary
        invited = int(self.df["status"].isin(INVITED).sum())
        self.assertEqual(s["applicants"], 40)
        self.assertEqual(s["reached_interview_stage"], invited)
        self.assertEqual(s["hired"], int((self.df["status"] == STATUSES["hired"]).sum()))
        self.assertEqual(s["interview_rate_pct"], round(invited / 40 * 100, 1))
        self.assertTrue(s["is_demo_sample_data"])
        self.assertIn("small sample", s["sample_size_note"])

    def test_experience_bands_cover_everyone(self):
        bands = self.summary["by_experience"]
        self.assertEqual(sum(b["applicants"] for b in bands.values()), 40)
        self.assertLess(bands["0-2 yrs"]["interview_rate_pct"], bands["3-5 yrs"]["interview_rate_pct"])

    def test_dropout_reasons_add_up(self):
        reasons = self.summary["why_candidates_dropped_out"]
        self.assertEqual(sum(reasons.values()), 40 - self.summary["reached_interview_stage"])

    def test_rule_hit_rates_are_consistent(self):
        deductions = self.summary["most_triggered_deductions"]
        self.assertTrue(deductions)
        for r in deductions:
            self.assertLess(r["points"], 0)
            self.assertEqual(r["share_of_applicants_pct"], round(r["candidates_hit"] / 40 * 100, 1))
        hits = [r["candidates_hit"] for r in deductions]
        self.assertEqual(hits, sorted(hits, reverse=True))
        self.assertTrue(all(r["points"] > 0 for r in self.summary["most_triggered_bonuses"]))

    def test_no_personal_data_in_the_summary_or_prompt(self):
        prompt = insights.build_prompt(self.summary)
        for name in self.df["name"].head(10):
            self.assertNotIn(name, prompt)
        self.assertNotIn("@", prompt.split("[FACTS]:")[1].split("[FORMAT")[0])

    def test_prompt_tells_the_model_to_use_only_the_facts(self):
        prompt = insights.build_prompt(self.summary)
        self.assertIn("ONLY the numbers", prompt)
        self.assertIn("fictional demo candidates", prompt)
        self.assertNotIn("fictional demo candidates", insights.build_prompt({**self.summary, "is_demo_sample_data": False}))

    def test_prompt_forbids_unsupported_benchmarks(self):
        self.assertIn("no benchmark is provided", insights.build_prompt(self.summary))

    def test_summary_tables_are_readable_frames(self):
        tables = insights.summary_tables(self.summary)
        titles = [title for title, _ in tables]
        self.assertIn("Conversion by experience", titles)
        self.assertIn("Most triggered deductions", titles)
        for _, frame in tables:
            self.assertFalse(frame.empty)

    def test_empty_and_missing_columns_do_not_crash(self):
        empty = pd.DataFrame(columns=COLUMNS)
        s = insights.build_summary(empty, INVITED, STATUSES["hired"], 60)
        self.assertEqual(s["applicants"], 0)
        minimal = pd.DataFrame({"status": ["Hired"], "score": ["80"], "expected_salary": ["60,000"]})
        s2 = insights.build_summary(minimal, INVITED, "Hired", 60)
        self.assertEqual(s2["applicants"], 1)


if __name__ == "__main__":
    unittest.main()
