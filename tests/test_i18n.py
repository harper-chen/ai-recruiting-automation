"""English / Traditional Chinese support.  Run from the project root:  python -m unittest

Checks the text tables, the Chinese sample job and resumes, and the Chinese branches of the pure helpers
(scoring rules, drop-out reasons, analytics tables). English behaviour is covered by the other test files.
"""

import os
import string
import sys
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import insights  # noqa: E402
from scoring import RULE_POINTS, analyze_rules  # noqa: E402

try:
    import i18n  # noqa: E402  (imports streamlit)
except ImportError:  # pragma: no cover - streamlit not installed
    i18n = None

BASELINE = 70
THRESHOLD = 60


def placeholders(text):
    return {name for _, name, _, _ in string.Formatter().parse(text) if name}


def read(relative):
    with open(os.path.join(ROOT, relative), encoding="utf-8") as f:
        return f.read()


def rule_points(text):
    points = []
    for line in text.splitlines():
        match = RULE_POINTS.search(line)
        if match:
            points.append(match.group(1) + match.group(2))
    return points


@unittest.skipIf(i18n is None, "streamlit is not installed")
class TextTableTests(unittest.TestCase):
    def use_lang(self, lang):
        return mock.patch.object(i18n.st, "query_params", {"lang": lang} if lang else {})

    def test_every_key_exists_in_both_languages(self):
        en, zh = set(i18n.TEXT["en"]), set(i18n.TEXT["zh"])
        self.assertEqual(en - zh, set())
        self.assertEqual(zh - en, set())

    def test_chinese_only_uses_values_the_code_passes(self):
        for key, english in i18n.TEXT["en"].items():
            with self.subTest(key=key):
                self.assertLessEqual(placeholders(i18n.TEXT["zh"][key]), placeholders(english))

    def test_english_is_the_default(self):
        with self.use_lang(None):
            self.assertEqual(i18n.get_lang(), "en")
        for value in ("zh-TW", "ZH", "fr"):
            with self.use_lang(value):
                self.assertEqual(i18n.get_lang(), "en")
        with self.use_lang("zh"):
            self.assertEqual(i18n.t("tab_review"), "👨‍💼 主管審核")

    def test_links_carry_the_language_only_in_chinese(self):
        with self.use_lang(None):
            self.assertEqual(i18n.with_lang("https://book.example/"), "https://book.example/")
        with self.use_lang("zh"):
            self.assertEqual(i18n.with_lang("https://book.example/"), "https://book.example/?lang=zh")
            self.assertEqual(i18n.with_lang("https://book.example/?v=abcd"), "https://book.example/?v=abcd&lang=zh")

    def test_every_sheet_status_has_a_chinese_label(self):
        import config

        statuses = [value for name, value in vars(config).items() if name.startswith("STATUS_")]
        self.assertEqual(set(statuses) - set(i18n.STATUS_ZH), set())

    def test_every_drop_out_reason_has_a_chinese_label(self):
        rows = [
            {"manager_comment": "budget"},
            {"manager_comment": "other"},
            {"score_reason": "above budget"},
            {"score_reason": "lacks SQL"},
            {"score_reason": "fine"},
        ]
        codes = {insights.categorize_reason(row) for row in rows}
        self.assertEqual(codes - set(i18n.REASON_ZH), set())


class ChineseSampleJobTests(unittest.TestCase):
    def test_chinese_rules_have_the_same_points_as_english(self):
        english = read("sample_jd/python_backend_engineer_rules.txt")
        chinese = read("sample_jd/zh/python_backend_engineer_rules.txt")
        self.assertEqual(rule_points(chinese), rule_points(english))

    def test_chinese_rules_pass_the_sanity_checks(self):
        warnings, has_points = analyze_rules(read("sample_jd/zh/python_backend_engineer_rules.txt"), BASELINE, THRESHOLD)
        self.assertTrue(has_points)
        self.assertEqual(warnings, [])

    def test_points_after_chinese_text_need_a_separator(self):
        # The rule-drafting prompt asks for "：-15"; a sign glued to a Chinese character is not read as points.
        self.assertEqual(rule_points("• 沒有 Python 經驗：-15 分"), ["-15"])
        self.assertEqual(rule_points("• 具備雲端經驗 +5 分"), ["+5"])
        self.assertEqual(rule_points("• TOEIC 750+ 或同等證明"), [])

    def test_chinese_sample_resumes_are_readable(self):
        import pdfplumber

        folder = os.path.join(ROOT, "sample_resumes", "zh")
        names = sorted(name for name in os.listdir(folder) if name.endswith(".pdf"))
        self.assertEqual(len(names), 4)
        for name in names:
            with self.subTest(name=name), pdfplumber.open(os.path.join(folder, name)) as pdf:
                text = "".join(page.extract_text() or "" for page in pdf.pages)
                self.assertIn("@example.com", text)


class ChineseAnalyticsTests(unittest.TestCase):
    def test_chinese_reasons_are_classified(self):
        self.assertEqual(
            insights.categorize_reason({"manager_comment": "薪資要求超出預算"}), "Manager: salary/budget mismatch"
        )
        self.assertEqual(
            insights.categorize_reason({"score_reason": "期待薪資 72,000 元，超出預算上限。"}),
            "AI: salary expectation too high",
        )
        self.assertEqual(
            insights.categorize_reason({"score_reason": "缺乏 Python 網頁框架經驗。"}),
            "AI: insufficient skills/experience",
        )
        self.assertEqual(insights.categorize_reason({"score_reason": "學歷與經驗皆符合。"}), "AI: other mismatch")

    def test_prompt_asks_for_chinese_only_in_chinese(self):
        summary = {"applicants": 3}
        self.assertNotIn("繁體中文", insights.build_prompt(summary))
        self.assertIn("繁體中文", insights.build_prompt(summary, "zh"))

    def test_tables_are_labelled_in_chinese(self):
        summary = {
            "by_experience": {"0-2 yrs": {"applicants": 2, "interview_rate_pct": 50.0, "median_expected_salary": 50000}},
            "why_candidates_dropped_out": {"AI: other mismatch": 1},
        }
        labels = {"0-2 yrs": "0–2 年", "AI: other mismatch": "AI：其他不符"}
        tables = insights.summary_tables(summary, "zh", lambda code: labels.get(code, code))
        titles = [title for title, _ in tables]
        self.assertEqual(titles, ["各年資的轉換率", "候選人流失原因"])
        self.assertEqual(tables[0][1].iloc[0, 0], "0–2 年")
        self.assertEqual(tables[1][1].iloc[0, 0], "AI：其他不符")

    def test_english_tables_are_unchanged(self):
        summary = {"why_candidates_dropped_out": {"AI: other mismatch": 1}}
        title, table = insights.summary_tables(summary)[0]
        self.assertEqual(title, "Why candidates dropped out")
        self.assertEqual(list(table.columns), ["Reason", "Candidates"])


class TranslatedRuleWarningsTests(unittest.TestCase):
    def test_messages_can_be_replaced(self):
        messages = {"no_deductions": "沒有任何扣分規則。"}
        warnings, _ = analyze_rules("• 加分：+5", BASELINE, THRESHOLD, messages)
        self.assertEqual(warnings, ["沒有任何扣分規則。"])

    def test_never_rejected_message_gets_the_numbers(self):
        messages = {"never_rejected": "最低 {score} 分，門檻 {threshold}。"}
        warnings, _ = analyze_rules("• 扣分：-3", BASELINE, THRESHOLD, messages)
        self.assertEqual(warnings, ["最低 67 分，門檻 60。"])


if __name__ == "__main__":
    unittest.main()
