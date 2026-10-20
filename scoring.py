"""
Pure scoring helpers (no Streamlit, no network) so they can be unit tested.

The AI model only reports WHICH rules applied and their signed points. The score itself is
computed here, in code, so a model arithmetic slip can never change a candidate's outcome.
"""

import json
import re

# A signed number at the start of a number token: "-15", "+5", or a unicode minus. Not matched:
# "750+", "2+ years", "cross-system", "NT$70,000", "3-5 years".
RULE_POINTS = re.compile(r"(?<![\w.])([+\-−])\s*(\d+(?:\.\d+)?)")


def parse_scoring_response(raw_text, baseline):
    """Turn the model's JSON into a record whose score is computed by code.

    score = baseline + sum(points of the adjustments), clamped to 0-100. Raises ValueError
    (json.JSONDecodeError is one) when the response is unusable, so a bad answer is never written.
    """
    match = re.search(r"\{[\s\S]*\}", raw_text)
    data = json.loads(match.group(0) if match else raw_text)
    adjustments = data.get("adjustments")
    if not isinstance(adjustments, list):
        raise ValueError("The AI response has no 'adjustments' list.")

    total = 0.0
    parts = []
    for item in adjustments:
        if not isinstance(item, dict):
            continue
        try:
            points = float(str(item.get("points")).replace("−", "-"))  # tolerate a unicode minus
        except ValueError:
            continue
        total += points
        parts.append(f"{points:+g} {str(item.get('rule', 'rule')).strip()}")

    raw_score = baseline + total
    score = int(round(max(0, min(100, raw_score))))
    calc = f"{baseline} baseline" + "".join(f"; {p}" for p in parts) + f" = {raw_score:g}"
    if score != round(raw_score):
        calc += f" (capped to {score})"
    data["score"] = score
    data["score_calculation"] = calc
    return data


def analyze_rules(rules_text, baseline, threshold):
    """Cheap sanity checks on free-text rules. Returns (warnings, has_points)."""
    deductions = 0.0
    n_ded = n_bon = 0
    for line in rules_text.splitlines():
        m = RULE_POINTS.search(line)
        if not m:
            continue
        value = float(m.group(2))
        if m.group(1) == "+":
            n_bon += 1
        else:
            deductions += value
            n_ded += 1

    warnings = []
    if n_ded + n_bon == 0:
        warnings.append(
            "No point values found. Write each rule with an explicit signed number such as -15 or +5, "
            "otherwise nobody can be scored."
        )
        return warnings, False
    if n_ded == 0:
        warnings.append("There are no deduction rules, so nobody can be screened out.")
    elif baseline - deductions >= threshold:
        warnings.append(
            f"Even a candidate who triggers every deduction would score {baseline - deductions:g}, which is still "
            f"at or above the {threshold} threshold, so nobody can be auto-rejected."
        )
    return warnings, True
