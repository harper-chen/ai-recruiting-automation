"""
Helpers that keep the public demo usable by many visitors at once (no Streamlit, no network).

  * Visitor tag: each visitor's candidates get a tagged email (ariel.lin+k3f9@example.com), so the same sample
    resumes can be uploaded by everyone and each visitor only sees their own rows.
  * Seed data: about 40 fictional candidates so the analytics tab has something to show. Their emails end in
    SEED_EMAIL_DOMAIN, which is how they are told apart from visitor rows.
  * Housekeeping plan: which sheet rows to delete (visitor rows older than a day, stale seed rows) and whether
    to re-seed.

Everything here is fictional demo data.
"""

import json
import random
import re
import secrets
from datetime import datetime, timedelta

from scoring import parse_scoring_response

SEED_EMAIL_DOMAIN = "@seed.example.com"
TAG_PATTERN = re.compile(r"^[a-z0-9]{4,8}$")
TAG_ALPHABET = "abcdefghjkmnpqrstuvwxyz23456789"
SEED_JOB = "Python Backend Engineer"
SALARY_BUDGET = 70000

_TIME_FORMATS = ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S")


# ---------------------------------------------------------------------------
# Visitor tags
# ---------------------------------------------------------------------------


def new_visitor_tag():
    return "".join(secrets.choice(TAG_ALPHABET) for _ in range(5))


def is_valid_tag(tag):
    return bool(tag) and bool(TAG_PATTERN.match(str(tag)))


def tag_email(email, tag):
    """ariel.lin@example.com + k3f9 -> ariel.lin+k3f9@example.com (unchanged when it has no '@')."""
    email = (email or "").strip()
    if "@" not in email or not tag:
        return email
    local, domain = email.rsplit("@", 1)
    return f"{local}+{tag}@{domain}"


def belongs_to_visitor(email, tag):
    return bool(tag) and f"+{tag}@" in (email or "").strip().lower()


def is_seed_email(email):
    return (email or "").strip().lower().endswith(SEED_EMAIL_DOMAIN)


# ---------------------------------------------------------------------------
# Housekeeping plan
# ---------------------------------------------------------------------------


def parse_time(value):
    for fmt in _TIME_FORMATS:
        try:
            return datetime.strptime(str(value).strip(), fmt)
        except ValueError:
            continue
    return None


def plan_housekeeping(rows, now, force_reset=False, max_age_hours=24, seed_max_age_days=7):
    """Decide what to do with the demo sheet. `rows` is get_all_values() including the header row.

    Returns (sheet_rows_to_delete, reseed): 1-based sheet row numbers (header is row 1 and never deleted)
    and whether fresh seed rows should be appended afterwards.
    """
    data = [(i, r) for i, r in enumerate(rows[1:], start=2)]
    if force_reset:
        return [i for i, _ in data], True

    delete = []
    seed_times = []
    seed_rows = []
    for i, r in data:
        email = r[3] if len(r) > 3 else ""
        applied = parse_time(r[7]) if len(r) > 7 else None
        if is_seed_email(email):
            seed_rows.append(i)
            if applied:
                seed_times.append(applied)
        elif applied and (now - applied) > timedelta(hours=max_age_hours):
            delete.append(i)  # a visitor's row from more than a day ago

    reseed = (not seed_rows) or (not seed_times) or (now - max(seed_times) > timedelta(days=seed_max_age_days))
    if reseed:
        delete.extend(seed_rows)
    return sorted(set(delete)), reseed


def visitor_rows(rows, tag):
    """1-based sheet row numbers of the candidates that belong to one visitor."""
    return [i for i, r in enumerate(rows[1:], start=2) if belongs_to_visitor(r[3] if len(r) > 3 else "", tag)]


def contiguous_ranges(numbers):
    """[2,3,4,9,10] -> [(9,10),(2,4)]: inclusive ranges, last first, so deleting them never shifts later ones."""
    numbers = sorted(set(numbers))
    ranges = []
    for n in numbers:
        if ranges and n == ranges[-1][1] + 1:
            ranges[-1][1] = n
        else:
            ranges.append([n, n])
    return [(a, b) for a, b in reversed(ranges)]


# ---------------------------------------------------------------------------
# Seed data
# ---------------------------------------------------------------------------

_NAMES = [
    "Aiden Brooks", "Bella Nguyen", "Carlos Mendez", "Dana Whitfield", "Ethan Park", "Fiona Gallagher",
    "Gavin Ortiz", "Hannah Kim", "Isaac Romero", "Julia Tanaka", "Kyle Becker", "Lena Hoffmann",
    "Marcus Reed", "Nora Abbasi", "Oliver Stein", "Priya Raman", "Quentin Lowe", "Rosa Delgado",
    "Samuel Okafor", "Tina Marchetti", "Umar Siddiqui", "Vera Lindqvist", "Wesley Chu", "Ximena Torres",
    "Yusuf Demir", "Zoe Fitzgerald", "Adrian Cole", "Brianna Hayes", "Caleb Ford", "Daphne Reyes",
    "Elliot Nash", "Freya Jensen", "Gabriel Santos", "Holly Winters", "Ivan Petrov", "Jasmine Lee",
    "Kenji Watanabe", "Layla Hassan", "Miles Carter", "Naomi Bell",
]

_FRAMEWORKS = ["FLASK", "FASTAPI", "DJANGO"]
_CLOUD = ["AWS", "GCP", "AZURE"]
_EXTRA = ["DOCKER", "KUBERNETES", "REDIS", "PYTEST", "TABLEAU", "POSTGRESQL", "MYSQL", "LINUX"]
_MANAGER_DECLINES = [
    "Weaker fit with our current tech stack",
    "Another candidate was a closer match",
    "Not enough hands-on API design experience",
]


def build_seed_rows(now, statuses, baseline=70, threshold=60, seed=2026):
    """Return fictional candidate rows (18 columns, header order of the Google Sheet contract).

    `statuses` maps names to the status strings used by the app: rejected, invited_no_reply, scheduled, hired.
    The story the charts tell: little experience converts poorly, senior candidates are often priced above
    the NT$70,000 budget, and cloud skills carry a salary premium.
    """
    rng = random.Random(seed)
    names = list(_NAMES)
    rng.shuffle(names)

    bands = [("junior", 14), ("mid", 14), ("senior", 12)]
    people = []
    for band, count in bands:
        for _ in range(count):
            people.append(band)

    candidates = []
    for band, name in zip(people, names):
        if band == "junior":
            years = rng.choice([0, 0.5, 1, 1, 1.5, 2])
        elif band == "mid":
            years = rng.choice([3, 3, 4, 4, 5])
        else:
            years = rng.choice([6, 7, 8, 9, 10])

        skills = {"PYTHON"}
        if rng.random() < (0.55 if band == "junior" else 0.9):
            skills.add("SQL")
        if rng.random() < (0.6 if band == "junior" else 0.92):
            skills.add("GIT")
        if rng.random() < (0.6 if band == "junior" else 0.9):
            skills.add(rng.choice(_FRAMEWORKS))
        extra_count = {"junior": 1, "mid": 2, "senior": 4}[band]
        skills.update(rng.sample(_EXTRA, extra_count))
        if rng.random() < {"junior": 0.1, "mid": 0.45, "senior": 0.75}[band]:
            skills.add(rng.choice(_CLOUD))

        has_cloud = bool(skills & set(_CLOUD))
        salary = 36000 + years * 3800 + (6000 if has_cloud else 0) + (3000 if "KUBERNETES" in skills else 0)
        salary += rng.gauss(0, 3000)
        salary = int(round(max(34000, min(98000, salary)) / 1000.0) * 1000)

        relevant_degree = rng.random() < 0.86
        python_weak = band == "junior" and rng.random() < 0.3
        english_proof = rng.random() > 0.2
        candidates.append(
            dict(
                name=name, band=band, years=years, skills=skills, salary=salary,
                relevant_degree=relevant_degree, python_weak=python_weak, english_proof=english_proof,
                education=("Bachelor's" if rng.random() < 0.65 else "Master's") if relevant_degree
                else rng.choice(["Bachelor's", "High school"]),
            )
        )

    rows = []
    approved_positions = []  # indexes into `rows` of candidates the manager approved
    for number, c in enumerate(candidates):
        adjustments = []
        gaps = []  # plain-language gaps for the AI rationale
        if not c["relevant_degree"]:
            adjustments.append({"rule": "No bachelor's degree in a related field", "points": -15})
            gaps.append("no degree in a related field")
        if c["years"] < 2:
            adjustments.append({"rule": "Less than 2 years of software experience", "points": -15})
            gaps.append("limited professional experience")
        if c["python_weak"]:
            adjustments.append({"rule": "Python skills not shown as strong", "points": -15})
            gaps.append("basic Python skills")
        if not (c["skills"] & set(_FRAMEWORKS)):
            adjustments.append({"rule": "No Python web framework experience", "points": -10})
            gaps.append("no web framework experience")
        if not (c["skills"] & {"SQL", "MYSQL", "POSTGRESQL"}):
            adjustments.append({"rule": "No proven SQL or MySQL/PostgreSQL experience", "points": -15})
            gaps.append("no SQL experience")
        if "GIT" not in c["skills"]:
            adjustments.append({"rule": "No team Git experience", "points": -10})
            gaps.append("no Git collaboration experience")
        over_budget = c["salary"] > SALARY_BUDGET
        if over_budget:
            adjustments.append({"rule": "Expected salary above NT$70,000", "points": -10})
        if not c["english_proof"]:
            adjustments.append({"rule": "No proof of English reading ability", "points": -5})
        if "TABLEAU" in c["skills"]:
            adjustments.append({"rule": "Data visualization tools", "points": 5})
        if c["skills"] & set(_CLOUD):
            adjustments.append({"rule": "Cloud deployment experience", "points": 5})
        if "PYTEST" in c["skills"]:
            adjustments.append({"rule": "Unit testing experience", "points": 3})

        scored = parse_scoring_response(json.dumps({"adjustments": adjustments}), baseline)
        score = scored["score"]

        days_ago = rng.randint(2, 20)
        applied = (now - timedelta(days=days_ago)).replace(
            hour=rng.randint(9, 17), minute=rng.choice([0, 10, 20, 30, 40, 50]), second=0, microsecond=0
        )
        fmt = "%Y-%m-%d %H:%M"
        action = applied
        approve, comment, interview_at, meeting = "", "", "", ""

        if score < threshold:
            status = statuses["rejected"]
            action = applied + timedelta(hours=48 + rng.randint(0, 5))
            top = ", ".join(gaps[:2]) if gaps else "a weaker overall profile"
            reason = (
                f"Falls short of the core requirements: {top}. "
                f"{'Expected salary is above budget. ' if over_budget else 'Expected salary is within budget. '}"
                "Recommended for automatic rejection."
            )
        else:
            skills_text = ", ".join(sorted(c["skills"])[:3])
            reason = (
                f"Meets the core requirements with {c['years']:g} years of experience and solid {skills_text}. "
                f"{'Expected salary is above the budget.' if over_budget else 'Expected salary is within the budget.'}"
            )
            action = applied + timedelta(days=rng.randint(1, 3), hours=rng.randint(0, 6))
            decline = (over_budget and rng.random() < 0.75) or (not over_budget and rng.random() < 0.12)
            if decline:
                status = statuses["rejected"]
                approve = "FALSE"
                comment = "Salary expectation above our budget" if over_budget else rng.choice(_MANAGER_DECLINES)
            else:
                approve = "TRUE"
                approved_positions.append(number)
                roll = rng.random()
                if roll < 0.7:
                    status = statuses["scheduled"]
                else:
                    status = statuses["invited_no_reply"]
                if status == statuses["scheduled"]:
                    when = applied + timedelta(days=rng.randint(4, 9))
                    when = when.replace(hour=rng.choice([10, 11, 14, 15, 16]), minute=rng.choice([0, 30]))
                    interview_at = when.strftime(fmt)
                    meeting = f"https://meet.example.com/seed-{number:02d} (demo link, not a real meeting)"

        email = f"{c['name'].lower().replace(' ', '.')}{SEED_EMAIL_DOMAIN}"
        rows.append(
            [
                status, SEED_JOB, c["name"], email, score, approve, comment,
                applied.strftime(fmt), action.strftime(fmt), interview_at, meeting,
                c["education"], c["years"], c["salary"], ", ".join(sorted(c["skills"])),
                reason, "", scored["score_calculation"],
            ]
        )

    # A few of the best candidates whose interview is already in the past were hired.
    finished = [
        n for n in approved_positions
        if rows[n][9] and parse_time(rows[n][9]) and parse_time(rows[n][9]) < now
    ]
    finished.sort(key=lambda n: rows[n][4], reverse=True)
    for n in finished[:3]:
        rows[n][0] = statuses["hired"]

    rows.sort(key=lambda r: r[7])
    return rows
