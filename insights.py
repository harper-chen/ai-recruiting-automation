"""
Numbers behind the Analytics tab's AI report (no Streamlit, no network).

The code computes every statistic here. The language model only receives this compact summary (no names, no
emails, no resume text) and is asked to explain it, so it cannot invent figures and no personal data leaves the app.
"""

import json
import re

import pandas as pd

# ---- Why candidates drop out: keyword classifier over free text (English, because the AI writes in English) ----
MANAGER_SALARY_WORDS = ["budget", "salary", "pay", "compensation", "expensive", "cost"]
AI_SALARY_PATTERNS = (
    r"over budget|above budget|exceed(?:s|ed|ing)? (?:the )?(?:budget|range|salary)|"
    r"above the (?:range|budget)|"
    r"too high|overpriced|salary expectation (?:is )?(?:too )?high|higher than"
)
AI_SKILL_PATTERNS = (
    r"\b(?:lack|lacks|lacking|no|not|limited|insufficient|missing|without|unfamiliar|"
    r"basic|junior|mismatch|gap|weak)\b"
)


def categorize_reason(row):
    """Why one candidate left the funnel. `row` is a dict-like with manager_comment and score_reason."""
    comment = str(row.get("manager_comment", "") or "").strip()
    if comment:
        if any(k in comment.lower() for k in MANAGER_SALARY_WORDS):
            return "Manager: salary/budget mismatch"
        return "Manager: other mismatch"
    reason = str(row.get("score_reason", "")).lower()
    if re.search(AI_SALARY_PATTERNS, reason):
        return "AI: salary expectation too high"
    if re.search(AI_SKILL_PATTERNS, reason):
        return "AI: insufficient skills/experience"
    return "AI: other mismatch"


def experience_band(years):
    if pd.isna(years):
        return "Unknown"
    if years <= 2:
        return "0-2 yrs"
    if years <= 5:
        return "3-5 yrs"
    return "5+ yrs"


# ---- Scoring arithmetic stored in the sheet: "70 baseline; -15 Rule A; +5 Rule B = 60" ----
_CALC_SUFFIX = re.compile(r"\s=\s-?[\d.]+(?:\s\(capped to \d+\))?\s*$")
_CALC_PART = re.compile(r"^([+\-]\d+(?:\.\d+)?)\s+(.+)$")


def parse_calculation(text):
    """Return [(points, rule_text), ...] from a score_calculation cell (empty list when unreadable)."""
    text = _CALC_SUFFIX.sub("", str(text or "").strip())
    parts = text.split("; ")[1:]  # the first part is the baseline
    rules = []
    for part in parts:
        match = _CALC_PART.match(part.strip())
        if match:
            rules.append((float(match.group(1)), match.group(2).strip()))
    return rules


def _num(value, digits=0):
    if value is None or pd.isna(value):
        return None
    return round(float(value), digits) if digits else int(round(float(value)))


def build_summary(df, invited_statuses, hired_status, threshold, is_demo=False):
    """Aggregate the (already filtered) analytics dataframe into a small, personal-data-free dict."""
    df = df.copy()
    n = len(df)
    for col in ("score", "expected_salary", "total_experience_years"):
        if col not in df:
            df[col] = pd.NA
        df[col] = pd.to_numeric(df[col], errors="coerce")
    for col in ("status", "manager_comment", "score_reason", "score_calculation", "skills_extracted"):
        if col not in df:
            df[col] = ""

    df["invited"] = df["status"].isin(invited_statuses)
    df["band"] = df["total_experience_years"].apply(experience_band)
    invited_n = int(df["invited"].sum())
    hired_n = int((df["status"] == hired_status).sum())

    summary = {
        "is_demo_sample_data": bool(is_demo),
        "applicants": n,
        "sample_size_note": "small sample, percentages are unstable" if n < 100 else "moderate sample",
        "reached_interview_stage": invited_n,
        "interview_rate_pct": round(invited_n / n * 100, 1) if n else 0,
        "hired": hired_n,
        "hire_rate_pct": round(hired_n / n * 100, 1) if n else 0,
        "score_median": _num(df["score"].median()),
        f"share_below_{threshold}_pct": round(float((df["score"] < threshold).mean() * 100), 1) if n else 0,
    }

    bands = {}
    for band in ("0-2 yrs", "3-5 yrs", "5+ yrs"):
        group = df[df["band"] == band]
        if len(group):
            bands[band] = {
                "applicants": int(len(group)),
                "interview_rate_pct": round(float(group["invited"].mean() * 100), 1),
                "median_expected_salary": _num(group["expected_salary"].median()),
            }
    summary["by_experience"] = bands

    summary["expected_salary"] = {
        "median_all": _num(df["expected_salary"].median()),
        "median_reached_interview": _num(df.loc[df["invited"], "expected_salary"].median()),
        "median_not_reached_interview": _num(df.loc[~df["invited"], "expected_salary"].median()),
    }

    dropped = df[~df["invited"]]
    if len(dropped):
        reasons = dropped.apply(categorize_reason, axis=1).value_counts()
        summary["why_candidates_dropped_out"] = {k: int(v) for k, v in reasons.items()}

    # Which scoring rules fire, and how candidates who trigger them fare
    hits = {}
    for _, row in df.iterrows():
        for points, rule in parse_calculation(row["score_calculation"]):
            entry = hits.setdefault((points, rule), {"hits": 0, "invited": 0})
            entry["hits"] += 1
            entry["invited"] += int(row["invited"])
    rule_rows = [
        {
            "rule": rule,
            "points": points,
            "candidates_hit": e["hits"],
            "share_of_applicants_pct": round(e["hits"] / n * 100, 1),
            "interview_rate_pct_among_those_hit": round(e["invited"] / e["hits"] * 100, 1),
        }
        for (points, rule), e in hits.items()
    ]
    deductions = sorted((r for r in rule_rows if r["points"] < 0), key=lambda r: -r["candidates_hit"])[:8]
    bonuses = sorted((r for r in rule_rows if r["points"] > 0), key=lambda r: -r["candidates_hit"])[:4]
    summary["most_triggered_deductions"] = deductions
    summary["most_triggered_bonuses"] = bonuses

    skills = df[["skills_extracted", "expected_salary"]].copy()
    skills["skill"] = skills["skills_extracted"].astype(str).str.split(",")
    skills = skills.explode("skill")
    skills["skill"] = skills["skill"].str.strip().str.upper()
    skills = skills[skills["skill"] != ""]
    if not skills.empty:
        agg = skills.groupby("skill").agg(candidates=("skill", "count"), median_expected_salary=("expected_salary", "median"))
        agg = agg[agg["candidates"] >= 3].sort_values("candidates", ascending=False).head(8)
        summary["common_skills"] = [
            {"skill": s, "candidates": int(r["candidates"]), "median_expected_salary": _num(r["median_expected_salary"])}
            for s, r in agg.iterrows()
        ]
    return summary


def build_prompt(summary):
    """Instructions plus the facts. The model must explain these numbers, not add its own."""
    facts = json.dumps(summary, indent=1, ensure_ascii=False)
    demo_line = (
        "- These are fictional demo candidates; mention that once, briefly, in the summary.\n"
        if summary.get("is_demo_sample_data")
        else ""
    )
    return f"""You are a People Analytics consultant advising a hiring manager. Below are statistics from a recruiting
funnel. Write a short report using ONLY the numbers in [FACTS]. Never invent or estimate a figure.

[FACTS]:
{facts}

Reply with this JSON only (plain text values: no Markdown, no emoji, no preamble):
{{
  "summary": "2 sentences on how the funnel is performing (interview rate, hire rate)",
  "findings": [{{"title": "short title, max 8 words", "detail": "1-2 sentences that cite numbers from the facts"}}],
  "recommendations": [{{"title": "short action, max 8 words", "detail": "1-2 sentences on what a recruiter could do and why"}}],
  "caveat": "one sentence about sample size and correlation not being causation"
}}

[RULES]:
- Exactly 3 findings and 2 or 3 recommendations. Each detail is at most 35 words.
- Findings should cover where candidates drop out, whether a salary expectation or a requirement looks out of line
  with what the data shows, and any scoring rule that fires for a very large or very small share of applicants.
- Do not compare with other companies, industries or "typical" rates: no benchmark is provided, so never call a
  rate high or low in absolute terms. Compare only groups inside the facts (for example one experience band with another).
- Use cautious wording ("may", "worth checking"); do not claim causes.
- Talk about the process and the job requirements, never about individual people or protected characteristics.
- Salaries are monthly, in TWD.
{demo_line}"""


def parse_report(raw_text):
    """Turn the model's JSON into a clean dict, or None when it is unusable (the caller then shows the raw text)."""
    match = re.search(r"\{[\s\S]*\}", str(raw_text or ""))
    if not match:
        return None
    try:
        data = json.loads(match.group(0))
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None

    def items(key):
        out = []
        for entry in data.get(key) or []:
            if isinstance(entry, dict) and (entry.get("title") or entry.get("detail")):
                out.append({"title": str(entry.get("title", "")).strip(), "detail": str(entry.get("detail", "")).strip()})
        return out

    report = {
        "summary": str(data.get("summary", "")).strip(),
        "findings": items("findings"),
        "recommendations": items("recommendations"),
        "caveat": str(data.get("caveat", "")).strip(),
    }
    if not report["summary"] and not report["findings"]:
        return None
    return report


def summary_tables(summary):
    """The same numbers the AI saw, as small readable tables: [(title, DataFrame), ...]."""
    tables = []
    if summary.get("by_experience"):
        df = pd.DataFrame(summary["by_experience"]).T.reset_index()
        df.columns = ["Experience", "Applicants", "Interview rate (%)", "Median expected salary (TWD)"]
        tables.append(("Conversion by experience", df))
    if summary.get("why_candidates_dropped_out"):
        df = pd.DataFrame(list(summary["why_candidates_dropped_out"].items()), columns=["Reason", "Candidates"])
        tables.append(("Why candidates dropped out", df))
    for key, title in (("most_triggered_deductions", "Most triggered deductions"), ("most_triggered_bonuses", "Most triggered bonuses")):
        if summary.get(key):
            df = pd.DataFrame(summary[key])
            df.columns = ["Rule", "Points", "Candidates", "Share of applicants (%)", "Interview rate among them (%)"]
            tables.append((title, df))
    if summary.get("common_skills"):
        df = pd.DataFrame(summary["common_skills"])
        df.columns = ["Skill", "Candidates", "Median expected salary (TWD)"]
        tables.append(("Common skills", df))
    return tables
