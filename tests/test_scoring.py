"""Unit tests for the pure scoring helpers.  Run from the project root:  python -m unittest"""

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from scoring import analyze_rules, parse_scoring_response  # noqa: E402

BASELINE = 70
THRESHOLD = 60
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ParseScoringResponse(unittest.TestCase):
    def parse(self, raw):
        return parse_scoring_response(raw, BASELINE)

    def test_sums_adjustments_onto_baseline(self):
        raw = '```json\n{"name": "A", "adjustments": [{"rule": "Python", "points": -15}, {"rule": "cloud", "points": 5}]}\n```'
        result = self.parse(raw)
        self.assertEqual(result["score"], 60)
        self.assertEqual(result["score_calculation"], "70 baseline; -15 Python; +5 cloud = 60")

    def test_accepts_unicode_minus_and_string_numbers(self):
        result = self.parse('{"adjustments": [{"rule": "x", "points": "−20"}, {"rule": "y", "points": "+8"}]}')
        self.assertEqual(result["score"], 58)

    def test_score_is_capped_at_100(self):
        result = self.parse('{"adjustments": [{"rule": "big", "points": 50}]}')
        self.assertEqual(result["score"], 100)
        self.assertIn("capped to 100", result["score_calculation"])

    def test_score_floor_is_zero(self):
        self.assertEqual(self.parse('{"adjustments": [{"rule": "huge", "points": -200}]}')["score"], 0)

    def test_no_adjustments_means_baseline(self):
        self.assertEqual(self.parse('{"adjustments": []}')["score"], 70)

    def test_skips_unusable_entries(self):
        raw = '{"adjustments": [{"rule": "bad", "points": "n/a"}, "junk", {"rule": "ok", "points": -5}]}'
        self.assertEqual(self.parse(raw)["score"], 65)

    def test_model_supplied_score_is_ignored(self):
        with self.assertRaises(ValueError):
            self.parse('{"name": "A", "score": 99}')  # no adjustments list: never trust a bare score

    def test_rejects_non_json(self):
        with self.assertRaises(ValueError):
            self.parse("not json at all")

    def test_rejects_non_list_adjustments(self):
        with self.assertRaises(ValueError):
            self.parse('{"adjustments": "none"}')


class AnalyzeRules(unittest.TestCase):
    def analyze(self, text):
        return analyze_rules(text, BASELINE, THRESHOLD)

    def test_bundled_sample_rules_are_clean(self):
        path = os.path.join(ROOT, "sample_jd", "python_backend_engineer_rules.txt")
        with open(path, encoding="utf-8") as f:
            warnings, has_points = self.analyze(f.read())
        self.assertTrue(has_points)
        self.assertEqual(warnings, [])

    def test_rules_without_numbers_are_blocked(self):
        warnings, has_points = self.analyze("• Python is important\n• Be nice")
        self.assertFalse(has_points)
        self.assertEqual(len(warnings), 1)

    def test_warns_when_nobody_can_be_rejected(self):
        warnings, has_points = self.analyze("• Deduction: -3 for no SQL\n• Bonus: +5 cloud")
        self.assertTrue(has_points)
        self.assertIn("nobody can be auto-rejected", warnings[0])

    def test_warns_when_there_are_no_deductions(self):
        warnings, _ = self.analyze("• Bonus: +5 cloud")
        self.assertIn("no deduction", warnings[0])

    def test_ignores_numbers_that_are_not_point_values(self):
        text = "• TOEIC 750+ or equivalent: -5 points\n• Experience of 2+ years: -15\n• cross-system work: -10"
        warnings, has_points = self.analyze(text)
        self.assertTrue(has_points)
        self.assertEqual(warnings, [])


if __name__ == "__main__":
    unittest.main()
