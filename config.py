"""
Shared configuration for the recruiting automation system.

Everything that used to be hardcoded (company name, interviewer emails, deployment URLs,
status strings) lives here so the rest of the code stays free of personal or
environment-specific details.

Settings are read from Streamlit secrets first (.streamlit/secrets.toml locally, the
"Secrets" panel on Streamlit Community Cloud), then from environment variables.
See secrets.example.toml for the full list.
"""

import os

import streamlit as st

# ---------------------------------------------------------------------------
# Settings helpers
# ---------------------------------------------------------------------------


def get_setting(name, default=None):
    """Read a setting from st.secrets, falling back to an environment variable.

    Accessing st.secrets raises when no secrets file exists, so every lookup is
    wrapped in try/except instead of using `name in st.secrets`.
    """
    try:
        return st.secrets[name]
    except Exception:
        return os.environ.get(name, default)


def _as_bool(value):
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in ("1", "true", "yes", "on")


# Demo mode: for public demos. No real Google Calendar reads or writes, no real emails and
# no Drive uploads. Calendar busy times are generated, and emails are previewed on screen.
DEMO_MODE = _as_bool(get_setting("DEMO_MODE", False))
DEMO_INTERVIEWER_EMAILS = ["interviewer.a@demo.invalid", "interviewer.b@demo.invalid"]


def get_interviewer_emails():
    """Return the list of interviewer calendars used to compute free slots.

    Configure `INTERVIEWER_EMAILS` as a TOML list in secrets, or as a comma-separated
    environment variable. In demo mode, placeholder interviewers are used instead.
    """
    raw = get_setting("INTERVIEWER_EMAILS")
    if DEMO_MODE and not raw:
        return list(DEMO_INTERVIEWER_EMAILS)
    if not raw:
        raise RuntimeError(
            "INTERVIEWER_EMAILS is not configured. Add it to .streamlit/secrets.toml "
            "(see secrets.example.toml)."
        )
    if isinstance(raw, str):
        return [e.strip() for e in raw.split(",") if e.strip()]
    return [str(e).strip() for e in raw if str(e).strip()]


# ---------------------------------------------------------------------------
# Branding (the demo company is fictional)
# ---------------------------------------------------------------------------

COMPANY_NAME = get_setting("COMPANY_NAME", "Acme Technology Ltd.")
CONTACT_PHONE = get_setting("CONTACT_PHONE", "555-0100")
SHEET_NAME = get_setting("SHEET_NAME", "HR Applicant Tracker")
APPS_SCRIPT_URL = get_setting("APPS_SCRIPT_URL")      # resume-upload proxy (see apps_script/)
BOOKING_APP_URL = get_setting("BOOKING_APP_URL")      # public URL of streamlit_app.py

# ---------------------------------------------------------------------------
# Scheduling constants (demo deployment is in Taipei time)
# ---------------------------------------------------------------------------

TIMEZONE_NAME = "Asia/Taipei"
TIMEZONE_OFFSET_HOURS = 8
WEEKDAYS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
INTERVIEW_TAG = "[Interview]"  # prefix of every calendar event this app creates

# ---------------------------------------------------------------------------
# Pipeline status values
#
# These strings are PERSISTED in column A of the Google Sheet and compared against
# on every run, so the sheet and the code must use exactly the same values. If you
# change one, update the Sheet's data-validation dropdown too.
# ---------------------------------------------------------------------------

BASELINE_SCORE = 70           # every candidate starts here; the job's rules add or subtract
SCORE_THRESHOLD = 60          # below -> auto rejection, at or above -> manager review
REJECTION_DELAY_HOURS = 48    # automatic rejection emails wait this long after applying

# Suggested point ranges, shown to recruiters and given to the rule-drafting model.
# Calibrated so that missing one or two REQUIRED items drops a candidate below the threshold.
RULE_GUIDE = {
    "required_miss": (10, 20),   # a hard requirement the JD lists as required
    "salary_over": (10, 15),     # expected salary clearly above the JD range
    "minor_gap": (2, 5),         # soft gaps, e.g. no proof of English level
    "bonus": (3, 8),             # nice-to-have skills or distinctive experience
}

STATUS_PENDING_REJECTION = "AI Scored - Pending Rejection Email"
STATUS_PENDING_REVIEW = "AI Scored - Pending Manager Review"
STATUS_REJECTED = "Rejection Email Sent"
STATUS_INVITED_NO_REPLY = "Interview Invite Sent - No Reply"
STATUS_INVITED = "Interview Invite Sent"
STATUS_SCHEDULED = "Interview Scheduled"
STATUS_HIRED = "Hired"
STATUS_OFFER_DECLINED = "Offer Declined"  # set manually by HR

# Statuses that count as "reached the interview stage" in the funnel metrics
INVITED_STATUSES = [
    STATUS_INVITED_NO_REPLY,
    STATUS_INVITED,
    STATUS_SCHEDULED,
    STATUS_HIRED,
]
