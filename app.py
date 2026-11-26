"""
Recruiting Automation System - HR Dashboard (Streamlit)

Four tabs:
  1. AI resume parsing: job profiles, JD upload, AI-drafted scoring rules, batch resume scoring
  2. Manager review: shortlisted candidates (score >= 60) approved / declined in the browser
  3. Email routing: status-machine that sends rejection and interview-invite emails
  4. Analytics: funnel, rejection reasons, score vs. salary, skill premium

Google Sheets is the single source of truth; every state change is written back to it.
"""

import base64
import hashlib
import hmac
import io
import json
import math
import os
import re
import threading
import time
from datetime import datetime, timedelta
from urllib.parse import urlencode

import gspread
import httpx
import pandas as pd
import pdfplumber
import plotly.graph_objects as go
import streamlit as st
import yagmail
from google import genai
from google.genai import types as genai_types
from google.oauth2.service_account import Credentials
from plotly.subplots import make_subplots

import config
import demo_support
import i18n
import insights
from i18n import t
from insights import categorize_reason
from scoring import analyze_rules as _analyze_rules
from scoring import parse_scoring_response as _parse_scoring_response
from config import (
    INVITED_STATUSES,
    BASELINE_SCORE,
    REJECTION_DELAY_HOURS,
    RULE_GUIDE,
    SCORE_THRESHOLD,
    STATUS_HIRED,
    STATUS_INVITED_NO_REPLY,
    STATUS_PENDING_REJECTION,
    STATUS_PENDING_REVIEW,
    STATUS_REJECTED,
    STATUS_SCHEDULED,
)

# ==================== Core setup and cached clients ====================
st.set_page_config(page_title=t("page_title"), layout="wide", page_icon="👔")

UI_THEME = {
    "TITLE_COLOR": "#154360",
    "BG_COLOR": "#FDFDFD",
    "PRIMARY": "#154360",
    "SECONDARY": "#2E86C1",
    "DANGER": "#ABD6F4",
    "WARNING": "#75BDEE",
    "PIE_COLORS": ["#154360", "#22628D", "#4B7998", "#ABD6F4", "#D6EAF8"],
    "GRID_STYLE": "#F2F2F2",
    "SKILL_BAR_COLOR": "#2874A6",
}

st.markdown(
    """
    <style>
    .stButton > button { font-weight: bold; }
    .stDataFrame { border-radius: 8px; }
    </style>
""",
    unsafe_allow_html=True,
)

# English by default; ?lang=zh in the URL switches to Traditional Chinese.
i18n.render_language_switch()
LANG = i18n.get_lang()

if config.DEMO_MODE:
    st.warning(t("demo_banner"))

try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    # Email credentials are only needed outside demo mode
    SENDER_EMAIL = config.get_setting("SENDER_EMAIL", "demo@example.com") if config.DEMO_MODE else st.secrets["SENDER_EMAIL"]
    GMAIL_APP_PASSWORD = config.get_setting("GMAIL_APP_PASSWORD", "") if config.DEMO_MODE else st.secrets["GMAIL_APP_PASSWORD"]
except Exception as e:
    st.error(t("missing_secret", error=e))
    st.stop()


@st.cache_resource
def init_google_sheets():
    try:
        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]
        if "gcp_service_account" in st.secrets:
            creds_info = json.loads(st.secrets["gcp_service_account"])
            creds_object = Credentials.from_service_account_info(creds_info, scopes=scope)
        else:
            creds_object = Credentials.from_service_account_file("creds.json", scopes=scope)

        gs_client = gspread.authorize(creds_object)
        return gs_client.open(config.SHEET_NAME).sheet1
    except Exception as e:
        st.error(t("sheets_failed", error=e))
        return None


@st.cache_resource
def init_ai_client():
    try:
        # Pin the API version: JSON mode (response_mime_type) only works on v1beta, so do not rely on
        # whatever default the installed SDK version happens to use.
        return genai.Client(api_key=GEMINI_API_KEY, http_options=genai_types.HttpOptions(api_version="v1beta"))
    except Exception as e:
        st.error(t("ai_init_failed", error=e))
        return None


sheet = init_google_sheets()
client = init_ai_client()


def get_visitor_tag():
    """Demo only: a short per-visitor code, kept in the URL (?v=...) so it survives a page refresh."""
    tag = st.session_state.get("visitor_tag")
    if tag:
        return tag
    try:
        candidate = st.query_params.get("v")
    except Exception:  # noqa: BLE001
        candidate = None
    tag = candidate if demo_support.is_valid_tag(candidate) else demo_support.new_visitor_tag()
    st.session_state.visitor_tag = tag
    try:
        st.query_params["v"] = tag
    except Exception:  # noqa: BLE001
        pass
    return tag


if config.DEMO_MODE:
    get_visitor_tag()


@st.cache_resource
def housekeeping_lock():
    return threading.Lock()


def demo_housekeeping(force_reset=False):
    """Demo only: delete visitor rows older than 24 hours, and (re)load fresh sample candidates when needed."""
    if sheet is None:
        return
    seed_statuses = {
        "rejected": STATUS_REJECTED,
        "invited_no_reply": STATUS_INVITED_NO_REPLY,
        "scheduled": STATUS_SCHEDULED,
        "hired": STATUS_HIRED,
    }
    with housekeeping_lock():
        rows = sheet.get_all_values()
        now = datetime.now()
        to_delete, reseed = demo_support.plan_housekeeping(rows, now, force_reset=force_reset)
        for first, last in demo_support.contiguous_ranges(to_delete):
            sheet.delete_rows(first, last)
        if reseed:
            seed_rows = demo_support.build_seed_rows(now, seed_statuses, BASELINE_SCORE, SCORE_THRESHOLD)
            sheet.append_rows(seed_rows, value_input_option="RAW")


def clear_my_candidates():
    """Demo only: delete just this visitor's rows (other visitors and the sample candidates are untouched)."""
    if sheet is None:
        return 0
    with housekeeping_lock():
        mine = demo_support.visitor_rows(sheet.get_all_values(), st.session_state.get("visitor_tag"))
        for first, last in demo_support.contiguous_ranges(mine):
            sheet.delete_rows(first, last)
    return len(mine)


def is_demo_admin():
    """Full reset is for the owner only: the URL must carry ?admin=<ADMIN_KEY> and ADMIN_KEY must be set in secrets."""
    key = config.get_setting("ADMIN_KEY")
    if not key:
        return False
    try:
        supplied = st.query_params.get("admin", "")
    except Exception:  # noqa: BLE001
        return False
    return hmac.compare_digest(str(supplied), str(key))


if config.DEMO_MODE and sheet is not None and not st.session_state.get("housekeeping_done"):
    try:
        demo_housekeeping()
    except Exception as housekeeping_error:  # noqa: BLE001 - never block the app on cleanup
        st.warning(t("cleanup_skipped", error=housekeeping_error))
    st.session_state.housekeeping_done = True


def visible_to_me(row):
    """Demo only: a visitor sees just their own candidates (rows tagged with their code)."""
    if not config.DEMO_MODE:
        return True
    return demo_support.belongs_to_visitor(row[3] if len(row) > 3 else "", st.session_state.get("visitor_tag"))

# Session state
if "custom_rules" not in st.session_state:
    st.session_state.custom_rules = ""
if "current_job_title" not in st.session_state:
    st.session_state.current_job_title = ""
if "df_dash" not in st.session_state:
    st.session_state.df_dash = pd.DataFrame()

# Internal option values; their labels come from i18n (format_func), so a language switch never breaks a widget.
ALL_JOBS = "All jobs"
NEW_JOB = "➕ Add a new job manually"


def option_label(value):
    if value == ALL_JOBS:
        return t("all_jobs")
    if value == NEW_JOB:
        return t("new_job")
    return value

# ==================== Job profile storage ====================
PROFILE_FILE = "job_profiles.json"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
# The sample job (JD + rules) and sample resumes follow the interface language.
SAMPLE_PROFILES = {
    "en": {
        "name": "Python Backend Engineer",
        "jd": "jd.txt",
        "rules": "sample_jd/python_backend_engineer_rules.txt",
    },
    "zh": {
        "name": "Python 後端開發工程師",
        "jd": "sample_jd/zh/jd.txt",
        "rules": "sample_jd/zh/python_backend_engineer_rules.txt",
    },
}
SAMPLE_PROFILE_NAME = SAMPLE_PROFILES[LANG]["name"]


def _read_text(relative_path):
    try:
        with open(os.path.join(APP_DIR, relative_path), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def sample_profiles(lang=None):
    """Ready-made job profile so demo visitors can start scoring in two clicks."""
    sample = SAMPLE_PROFILES[lang or LANG]
    return {
        sample["name"]: {
            "jd": _read_text(sample["jd"]),
            "rules": _read_text(sample["rules"]),
        }
    }


def load_job_profiles():
    """Demo mode: profiles live in this visitor's session only (nothing is shared or persisted).
    Normal mode: profiles are stored in a local JSON file."""
    if config.DEMO_MODE:
        if "job_profiles" not in st.session_state:
            st.session_state.job_profiles = sample_profiles()
            st.session_state.job_profiles_lang = LANG
        elif st.session_state.get("job_profiles_lang", "en") != LANG:
            # Language switched: swap the other language's sample job for this one (only if it was not edited).
            old = st.session_state.get("job_profiles_lang", "en")
            old_name = SAMPLE_PROFILES[old]["name"]
            if st.session_state.job_profiles.get(old_name) == sample_profiles(old).get(old_name):
                st.session_state.job_profiles.pop(old_name, None)
            for name, profile in sample_profiles().items():
                st.session_state.job_profiles.setdefault(name, profile)
            st.session_state.job_profiles_lang = LANG
        return st.session_state.job_profiles
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_job_profiles(profiles_dict):
    if config.DEMO_MODE:
        st.session_state.job_profiles = profiles_dict
        return
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles_dict, f, ensure_ascii=False, indent=4)


def delete_selected_profile():
    """Button callback: remove the profile chosen in the dropdown and reset the form."""
    selected = st.session_state.get("profile_selector")
    profiles = load_job_profiles()
    if selected in profiles:
        del profiles[selected]
        save_job_profiles(profiles)
    st.session_state.profile_selector = NEW_JOB
    st.session_state.current_job_title = ""
    st.session_state.current_jd = ""
    st.session_state.custom_rules = ""


# ==================== Core functions ====================
MODEL_NAME = "gemini-2.5-flash"
MAX_AI_ATTEMPTS = 3


PERMANENT_ERROR_MARKERS = ("400", "401", "403", "404", "INVALID_ARGUMENT", "PERMISSION_DENIED", "UNAUTHENTICATED", "NOT_FOUND")


def is_permanent_error(error):
    """True for client-side errors that will not go away on retry (unlike 500/503 server hiccups)."""
    code = getattr(error, "code", None)
    if isinstance(code, int):
        return 400 <= code < 500 and code != 429
    return any(marker in str(error) for marker in PERMANENT_ERROR_MARKERS)


def quota_used_up_message():
    """Shown when the free Gemini quota runs out, pointing visitors to what still works."""
    message = t("quota_used_up")
    if config.DEMO_MODE and demo_snapshot():
        message += t("quota_samples_ok")
    return message + t("quota_screenshots", url=config.REPO_URL)


def generate_with_retry(prompt, json_mode=False):
    """Call Gemini, retrying transient failures (e.g. 503 UNAVAILABLE) with backoff.

    json_mode asks the API for strictly valid JSON, which avoids stray text or broken syntax.
    """
    last_error = None
    kwargs = {}
    if json_mode:
        kwargs["config"] = {"response_mime_type": "application/json"}
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            return client.models.generate_content(model=MODEL_NAME, contents=prompt, **kwargs)
        except Exception as e:  # noqa: BLE001 - SDK raises several error types
            last_error = e
            if "RESOURCE_EXHAUSTED" in str(e) or "429" in str(e):
                # A quota limit will not clear within seconds, so retrying only wastes time.
                raise RuntimeError(quota_used_up_message()) from e
            if is_permanent_error(e):
                # A bad request (wrong setting, bad key, unknown model) fails the same way every time,
                # so retrying would only burn the daily free-tier quota.
                raise
            if attempt < MAX_AI_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
    raise last_error


RULES_LANGUAGE_ZH = """
    [LANGUAGE]: Write every rule in Traditional Chinese as used in Taiwan (繁體中文，台灣用語), for example
    "• 沒有 Python 網頁框架的使用經驗：-10 分。" Always put a full-width colon (：) or a space right before the signed
    number. Write the final system rule as: "• Email 擷取：請完全照履歷上的寫法擷取真實的電子郵件地址，不得更改拼字。"
    """


def build_rules_prompt(jd_text, lang="en"):
    language = RULES_LANGUAGE_ZH if lang == "zh" else ""
    return f"""
    You are a senior technical recruiter. Based on the [Job Description (JD)] below, write 7 to 10 of the
    most important "resume scoring rules" (point deductions and bonuses).

    [STRICT FORMAT]:
    1. Never use Markdown bold (**) or italics (*).
    2. Start every line with a plain bullet character (•) followed by one space.
    3. Output the list directly, with no introduction or closing remarks.

    [SCORING SCALE - the scoring model applies your rules on top of this]:
    - Every candidate starts at a baseline of {BASELINE_SCORE} points. The final score is capped between 0 and 100.
    - A final score below {SCORE_THRESHOLD} means automatic rejection; {SCORE_THRESHOLD} or above goes to a hiring manager.
    - So a candidate missing one or two REQUIRED items from the JD must end up below {SCORE_THRESHOLD}. Calibrate the point values accordingly:
      * missing a hard requirement the JD lists as required: {RULE_GUIDE['required_miss'][0]} to {RULE_GUIDE['required_miss'][1]} points each
      * clearly above the JD's salary range: {RULE_GUIDE['salary_over'][0]} to {RULE_GUIDE['salary_over'][1]} points
      * minor or soft gaps (e.g. no proof of English level): {RULE_GUIDE['minor_gap'][0]} to {RULE_GUIDE['minor_gap'][1]} points
      * nice-to-have skills or distinctive experience: +{RULE_GUIDE['bonus'][0]} to +{RULE_GUIDE['bonus'][1]} points each
    - State every point value explicitly as a number.

    [CONTENT REQUIREMENTS]:
    1. Include explicit point deductions for missing core requirements (specific tools, education, years of experience).
    2. Include explicit point bonuses for advanced skills or distinctive experience.
    3. If the JD states a salary range, add a deduction rule for expected salaries above it.
    4. Each rule must test exactly ONE condition. Never join conditions with AND / OR inside a rule; split them into separate rules.
    5. Keep this system rule as the final line: "• Email extraction: extract the real email address exactly as written; never alter the spelling."
    {language}
    [JOB DESCRIPTION (JD)]:
    {jd_text}
    """


def rules_cache_key(prompt):
    return "rules:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def generate_rules_from_jd(jd_text):
    prompt = build_rules_prompt(jd_text, LANG)
    cache = demo_ai_cache() if config.DEMO_MODE else None
    cache_key = rules_cache_key(prompt)
    if config.DEMO_MODE and cache_key in demo_snapshot():
        return demo_snapshot()[cache_key]
    if cache is not None and cache_key in cache:
        return cache[cache_key]["raw"]
    try:
        response = generate_with_retry(prompt)
        text = response.text.strip().replace("**", "")
        if cache is not None:
            cache[cache_key] = {"raw": text, "at": datetime.now()}
        return text
    except Exception as e:
        return t("rules_failed", error=e)


def generate_insights_report(summary):
    """Plain-language report on the analytics numbers. The statistics come from code (insights.py)."""
    prompt = insights.build_prompt(summary, LANG)
    cache = demo_ai_cache() if config.DEMO_MODE else None
    cache_key = "report:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if cache is not None and cache_key in cache:
        return cache[cache_key]["raw"]
    try:
        response = generate_with_retry(prompt, json_mode=True)
        text = response.text.strip()
        if insights.parse_report(text) is None:
            return t("report_unreadable")
        if cache is not None:
            cache[cache_key] = {"raw": text, "at": datetime.now()}
        return text
    except Exception as e:
        return t("report_failed", error=e)


def show_insights_report(report):
    """Render the report as cards. The metrics come from code; the wording comes from the AI."""
    esc = lambda text: str(text).replace("$", "\\$")  # noqa: E731 - keep NT$ amounts from rendering as math
    data = insights.parse_report(report["text"])
    if data is None:
        st.warning(esc(report["text"]))
        return
    stats = report["summary"]
    m1, m2, m3, m4 = st.columns(4)
    m1.metric(t("m_applicants"), stats["applicants"])
    m2.metric(t("m_reached_interview"), f"{stats['interview_rate_pct']:g}%", t("m_people", count=stats["reached_interview_stage"]), delta_color="off")
    m3.metric(t("m_hired"), stats["hired"], t("m_of_applicants", pct=f"{stats['hire_rate_pct']:g}"), delta_color="off")
    m4.metric(t("m_median_score"), stats["score_median"] if stats["score_median"] is not None else "-")

    if data["summary"]:
        st.info(t("report_summary", text=esc(data["summary"])))

    if data["findings"]:
        st.markdown(t("report_findings"))
        for column, finding in zip(st.columns(len(data["findings"])), data["findings"]):
            with column:
                with st.container(border=True):
                    st.markdown(f"**{esc(finding['title'])}**")
                    st.write(esc(finding["detail"]))

    if data["recommendations"]:
        st.markdown(t("report_recommendations"))
        for number, rec in enumerate(data["recommendations"], start=1):
            with st.container(border=True):
                st.markdown(f"**{number}. {esc(rec['title'])}**")
                st.write(esc(rec["detail"]))

    if data["caveat"]:
        st.caption(f"⚠️ {esc(data['caveat'])}")


UPLOAD_FAILED_URL = "https://drive.google.com/resume/upload_failed"


def upload_file_and_get_link(file_path, file_name):
    """Upload through a Google Apps Script web app (see apps_script/Code.gs).

    Service accounts have no Drive storage quota of their own, so the upload is proxied
    through a script that runs as a normal Google user and returns a shareable link.
    """
    if config.DEMO_MODE:
        # Never store strangers' uploads. The bundled sample resumes link to their copy on GitHub instead.
        relative = sample_resume_path(file_name)
        if relative:
            return f"{config.REPO_URL}/blob/main/sample_resumes/{relative}"
        return ""

    if not config.APPS_SCRIPT_URL:
        st.warning(t("no_apps_script"))
        return UPLOAD_FAILED_URL

    try:
        with open(file_path, "rb") as f:
            encoded_file = base64.b64encode(f.read()).decode("utf-8")

        payload = {"fileName": file_name, "mimeType": "application/pdf", "fileData": encoded_file}
        response = httpx.post(config.APPS_SCRIPT_URL, json=payload, timeout=60.0, follow_redirects=True)

        if response.status_code == 200 and "Error" not in response.text:
            return response.text.strip()
        st.error(t("proxy_error", error=response.text))
        return UPLOAD_FAILED_URL

    except Exception as e:
        st.error(t("proxy_failed", error=e))
        return UPLOAD_FAILED_URL


SCORING_LANGUAGE = {
    "en": "Write all free-text fields in English, even if the resume is in another language.",
    "zh": (
        "Write all free-text fields (rule descriptions in adjustments, skills_extracted, score_reason) in Traditional "
        "Chinese as used in Taiwan (繁體中文，台灣用語), even if the resume is in another language. Keep the JSON keys, "
        "the candidate's name and email exactly as written, and use the English values listed for highest_education."
    ),
}


def _score_resume_uncached(file_name, resume_text, jd_text, rules_text, target_job_title):
    current_now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""
    You are an extremely strict HR hiring manager at a technology company. Using the [Job Description] and the
    [Candidate Resume] below, score the candidate objectively and extract structured data.
    {SCORING_LANGUAGE[LANG]}

    [TARGET JOB TITLE]: {target_job_title}

    [JOB DESCRIPTION]:
    {jd_text}

    [CANDIDATE RESUME]:
    {resume_text}

    [CUSTOM SCORING RULES]:
    {rules_text}

    [STRICT SCORING LOGIC - VERY IMPORTANT]:
    1. Do NOT compute a final score. The system starts from a baseline of {BASELINE_SCORE} and adds up your
       adjustments itself.
    2. Check the resume against every deduction and bonus rule above. For each rule that APPLIES to this
       candidate, add one entry to "adjustments" with a short description of the rule and the signed points
       exactly as written in that rule (for example -15 or 5). Leave out rules that do not apply.
    3. Only use point values that appear in the rules. Never invent adjustments, and never apply one rule twice.
    4. If the candidate's [expected salary] is clearly above the JD budget and no rule covers it, add one
       adjustment "Salary above budget" worth -{RULE_GUIDE['salary_over'][0]}.

    Reply with the following JSON only, with no Markdown tags:
    {{
        "job_applied": "{target_job_title}",
        "name": "candidate's full name",
        "email": "email address extracted from the resume",
        "adjustments": [{{"rule": "short description of a rule that applied", "points": -15}}],
        "applied_at": "{current_now_str}",
        "action_at": "{current_now_str}",
        "interview_at": "",
        "meeting_link": "",
        "highest_education": "one of: High school, Bachelor's, Master's, PhD",
        "total_experience_years": numeric total years of experience,
        "expected_salary": numeric expected monthly salary,
        "skills_extracted": "comma-separated skill tags",
        "score_reason": "short rationale (30-50 words) that includes whether the salary expectation exceeds the budget",
        "resume": "URL placeholder"
    }}
    """
    # The model occasionally returns malformed JSON, so a bad answer gets one fresh attempt.
    # API errors are not retried here: generate_with_retry already retries the transient ones.
    last_error = "No response"
    for _ in range(2):
        try:
            response = generate_with_retry(prompt, json_mode=True)
        except Exception as e:  # noqa: BLE001
            return str(e), False
        try:
            parse_scoring_response(response.text)  # validate only; callers parse again
            return response.text, True
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
    return last_error, False


@st.cache_resource
def demo_ai_cache():
    """Demo only: shared by all visitors, so the same resume + rules is scored by the AI once."""
    return {}


DEMO_CACHE_HOURS = 24
DEMO_CACHE_MAX_ENTRIES = 500
TIME_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")
DEMO_SNAPSHOT_FILE = os.path.join(APP_DIR, "sample_jd", "demo_ai_snapshot.json")


def demo_snapshot():
    """Demo only: saved AI answers for the bundled sample resumes scored with the sample job's default JD and rules
    (created with the owner's export button). Unlike the in-memory cache they survive restarts and never expire,
    so the default demo path keeps working when the free AI quota is used up."""
    try:
        modified = os.path.getmtime(DEMO_SNAPSHOT_FILE)
    except OSError:
        return {}
    # Keyed by the file's modification time: a snapshot added or replaced later (which does not restart the app,
    # because only .py changes do) is picked up on the next run instead of the old answer staying cached.
    return _load_demo_snapshot(modified)


@st.cache_resource
def _load_demo_snapshot(modified):
    try:
        with open(DEMO_SNAPSHOT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def scoring_cache_key(resume_text, jd_text, rules_text, target_job_title, lang=None):
    # The AI writes in the interface language, so Chinese answers get their own key. English keys are unchanged,
    # which keeps the saved English snapshot valid.
    parts = [MODEL_NAME, resume_text, jd_text, rules_text, target_job_title]
    if (lang or LANG) == "zh":
        parts.append("zh")
    return hashlib.sha256("\x1f".join(parts).encode("utf-8")).hexdigest()


def score_resume(file_name, resume_text, jd_text, rules_text, target_job_title):
    """Score one resume. In demo mode an identical resume + JD + rules reuses the earlier AI answer, which keeps
    the free-tier quota for visitors who change something and makes the sample results repeatable."""
    if not config.DEMO_MODE:
        return _score_resume_uncached(file_name, resume_text, jd_text, rules_text, target_job_title)

    key = scoring_cache_key(resume_text, jd_text, rules_text, target_job_title)
    cache = demo_ai_cache()
    now = datetime.now()
    saved = demo_snapshot().get(key)
    if saved:
        # Saved answer for a sample resume: no AI call needed. Keep the application time current.
        return TIME_STAMP.sub(now.strftime("%Y-%m-%d %H:%M"), saved), True
    hit = cache.get(key)
    if hit and (now - hit["at"]) < timedelta(hours=DEMO_CACHE_HOURS):
        # Keep the application time current: swap the stored timestamp for the present one
        return TIME_STAMP.sub(now.strftime("%Y-%m-%d %H:%M"), hit["raw"]), True

    raw, ok = _score_resume_uncached(file_name, resume_text, jd_text, rules_text, target_job_title)
    if ok:
        if len(cache) >= DEMO_CACHE_MAX_ENTRIES:
            cache.pop(next(iter(cache)))
        cache[key] = {"raw": raw, "at": now}
    return raw, ok


@st.cache_data
def pdf_text(path):
    with pdfplumber.open(path) as pdf:
        return "".join([p.extract_text() or "" for p in pdf.pages])


def build_demo_snapshot():
    """Owner only: gather the AI answers for the bundled sample resumes scored with the sample job's default JD and
    rules, plus the AI-drafted rules for that JD. Nothing else is exported, so no visitor upload can end up in it.
    Returns (snapshot, names of sample resumes that have no answer yet)."""
    sample_dir = os.path.join(APP_DIR, "sample_resumes")
    cache, saved = demo_ai_cache(), demo_snapshot()

    def answer(key):
        if key in saved:
            return saved[key]
        return cache[key]["raw"] if key in cache else None

    # Both languages go into the same file: the keys are content hashes, so they never collide.
    snapshot, missing = {}, []
    for lang, sample in SAMPLE_PROFILES.items():
        name = sample["name"]
        profile = sample_profiles(lang)[name]
        for relative in DEMO_SAMPLE_RESUMES[lang]:
            path = os.path.join(sample_dir, relative)
            if not os.path.exists(path):
                continue
            key = scoring_cache_key(pdf_text(path), profile["jd"], profile["rules"], name, lang)
            if answer(key):
                snapshot[key] = answer(key)
            else:
                missing.append(relative)
        rules_key = rules_cache_key(build_rules_prompt(profile["jd"], lang))
        if answer(rules_key):
            snapshot[rules_key] = answer(rules_key)
    return snapshot, missing


def parse_scoring_response(raw_text):
    """Score is computed by code (see scoring.py), using this app's baseline."""
    return _parse_scoring_response(raw_text, BASELINE_SCORE)


def analyze_rules(rules_text):
    """Sanity checks on the free-text rules (see scoring.py), using this app's baseline and threshold."""
    messages = {
        "no_points": t("warn_no_points"),
        "no_deductions": t("warn_no_deductions"),
        "never_rejected": t("warn_never_rejected", score="{score}", threshold="{threshold}"),
    }
    return _analyze_rules(rules_text, BASELINE_SCORE, SCORE_THRESHOLD, messages)


def write_to_google_sheet(data_dict, real_resume_url):
    if sheet is None:
        return t("sheet_not_connected")
    try:
        target_job = data_dict.get("job_applied", "").strip()
        target_email = data_dict.get("email", "").strip()
        if config.DEMO_MODE:
            # Same sample resume, different visitors: the private code makes each visitor's row unique
            target_email = demo_support.tag_email(target_email, st.session_state.get("visitor_tag"))
        candidate_name = data_dict.get("name", "")
        score = data_dict.get("score", 0)

        all_jobs = sheet.col_values(2)
        all_emails = sheet.col_values(4)
        existing_records = set(zip(all_jobs, all_emails))

        if (target_job, target_email) in existing_records:
            return t("duplicate_blocked", name=candidate_name, email=target_email)

        # The code, not the model, decides the status so the status machine stays consistent.
        final_status = STATUS_PENDING_REJECTION if score < SCORE_THRESHOLD else STATUS_PENDING_REVIEW
        next_row = len(all_jobs) + 1

        front_part = [final_status, target_job, candidate_name, target_email, score]
        back_part = [
            data_dict.get("applied_at", ""),
            data_dict.get("action_at", ""),
            "",
            "",
            data_dict.get("highest_education", ""),
            data_dict.get("total_experience_years", 0),
            data_dict.get("expected_salary", 0),
            data_dict.get("skills_extracted", ""),
            data_dict.get("score_reason", ""),
            real_resume_url,
            data_dict.get("score_calculation", ""),
        ]

        sheet.update(f"A{next_row}:E{next_row}", [front_part])
        sheet.update(f"H{next_row}:R{next_row}", [back_part])
        return t("write_ok", name=candidate_name, job=target_job, status=i18n.status_label(final_status))

    except Exception as e:
        return t("write_fatal", error=e)


# Demo: a few bundled fictional resumes with different fits for the sample job, so visitors can try the
# whole flow without downloading anything. Scores come from the AI and can vary slightly between runs.
# Paths are relative to sample_resumes/; the Chinese interface offers Chinese resumes.
DEMO_SAMPLE_RESUMES = {
    "en": {
        "ariel_lin.pdf": "Ariel Lin · 4 yrs Python backend, cloud deployment",
        "kevin_huang.pdf": "Kevin Huang · 2 yrs Python backend",
        "mei-ling_wu.pdf": "Mei-Ling Wu · data assistant moving into backend",
    },
    "zh": {
        "zh/lin_ya-ting.pdf": "林雅婷 · 3 年 Python 後端經驗，期待薪資高於預算",
        "zh/hung_chia-wei.pdf": "洪家瑋 · 1 年多網頁開發經驗",
        "zh/gao_zi-hao.pdf": "高子豪 · 資管系應屆畢業",
        "zh/chen_mei-ling.pdf": "陳美玲 · 精品零售，想轉職科技業",
    },
}


def sample_resume_path(file_name):
    """sample_resumes/-relative path of a bundled sample resume with this file name, or None."""
    for samples in DEMO_SAMPLE_RESUMES.values():
        for relative in samples:
            if os.path.basename(relative) == file_name and os.path.exists(os.path.join(APP_DIR, "sample_resumes", relative)):
                return relative
    sample_dir = os.path.join(APP_DIR, "sample_resumes")
    if os.path.isdir(sample_dir) and file_name in os.listdir(sample_dir):
        return file_name
    return None


class BundledResume:
    """A resume shipped with the app, with the two things the evaluation loop reads from an upload."""

    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)

    def getbuffer(self):
        with open(self.path, "rb") as f:
            return f.read()


@st.cache_data
def sample_resume_preview(path, alt="First page of the sample resume"):
    """First page as an HTML image, so visitors can see what the AI is reading.

    Rendered at about twice the display width so it stays sharp on high-resolution (Retina) screens, and sent as
    lossless PNG through HTML because st.image shrinks and recompresses images, which blurred the small text.
    """
    with pdfplumber.open(path) as pdf:
        page_image = pdf.pages[0].to_image(resolution=200).original
    buffer = io.BytesIO()
    page_image.save(buffer, format="PNG", optimize=True)
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return (
        f'<img src="data:image/png;base64,{encoded}" alt="{alt}" '
        'style="width:100%; max-width:800px; border:1px solid #e0e0e0; border-radius:6px;">'
    )


class DemoMailer:
    """Stand-in for yagmail.SMTP in demo mode: previews the email instead of sending it."""

    def send(self, to, subject, contents):
        # Stored in session state so the preview survives the st.rerun() after dispatch
        st.session_state.setdefault("demo_emails", []).append({"to": to, "subject": subject, "body": contents})


def pad_row(row):
    """Sheets drops trailing empty cells; pad to the 18 columns this app expects."""
    return row + [""] * (18 - len(row))


def clean_resume_url(value):
    """Only real links are clickable; placeholders and failed uploads show as blank."""
    value = (value or "").strip()
    if value.startswith("http") and "upload_failed" not in value:
        return value
    return ""


def hours_until_rejection(applied_at_str):
    """Hours left before the automatic rejection email may be sent (None if the date is unreadable)."""
    try:
        applied = datetime.strptime(applied_at_str.strip(), "%Y-%m-%d %H:%M")
    except ValueError:
        return None
    remaining = (applied + timedelta(hours=REJECTION_DELAY_HOURS) - datetime.now()).total_seconds() / 3600
    # Whole hours, rounded up, so a candidate with 0.3 hours left shows 1 rather than 0
    return max(0, math.ceil(remaining))


# ==================== Streamlit UI ====================

st.title(t("title"))
if config.DEMO_MODE:
    if config.BOOKING_APP_URL:
        st.link_button(t("see_candidate_side"), i18n.with_lang(config.BOOKING_APP_URL))
    with st.expander(t("demo_tools")):
        st.caption(t("private_code", tag=st.session_state.get("visitor_tag")))
        if st.button(t("clear_mine"), key="clear_mine"):
            try:
                removed = clear_my_candidates()
                st.success(t("cleared", count=removed))
            except Exception as clear_error:  # noqa: BLE001
                st.error(t("clear_failed", error=clear_error))
        if is_demo_admin():
            st.divider()
            st.caption(t("owner_reset_note"))
            confirm_reset = st.checkbox(t("confirm_reset"), key="confirm_reset")
            if st.button(t("reset_all"), disabled=not confirm_reset):
                try:
                    demo_housekeeping(force_reset=True)
                    st.session_state.df_dash = pd.DataFrame()
                    st.success(t("reset_done"))
                except Exception as reset_error:  # noqa: BLE001
                    st.error(t("reset_failed", error=reset_error))
            st.divider()
            st.caption(t("owner_snapshot_note"))
            snapshot, missing = build_demo_snapshot()
            if missing:
                st.caption(t("not_saved_yet", names=", ".join(missing)))
            st.download_button(
                t("export_snapshot", count=len(snapshot)),
                data=json.dumps(snapshot, ensure_ascii=False, indent=2),
                file_name="demo_ai_snapshot.json",
                mime="application/json",
                disabled=not snapshot,
            )
            st.caption(t("upload_snapshot"))
TAB_LABELS = [t("tab_parse"), t("tab_review"), t("tab_email"), t("tab_analytics")]
TAB_REVIEW = TAB_LABELS[1]
TAB_EMAIL = TAB_LABELS[2]
if st.session_state.get("main_tab") not in (None, *TAB_LABELS):
    # The language changed: the remembered tab label belongs to the other language.
    st.session_state.pop("main_tab", None)


def go_to_tab(label):
    """Button callback: callbacks run before the tabs are drawn, so the active tab can be changed here."""
    st.session_state.main_tab = label


def go_to_review_tab():
    go_to_tab(TAB_REVIEW)


def go_to_email_tab():
    st.session_state.pop("review_saved", None)
    go_to_tab(TAB_EMAIL)


def evaluation_summary_text(summary):
    parts = []
    if summary["review"]:
        parts.append(t("eval_review", count=summary["review"], threshold=SCORE_THRESHOLD))
    if summary["rejected"]:
        parts.append(t("eval_rejected", count=summary["rejected"], threshold=SCORE_THRESHOLD))
    if summary["problems"]:
        parts.append(t("eval_problems", count=len(summary["problems"])))
    return t("eval_done", parts=t("eval_sep").join(parts))


tab1, tab2, tab3, tab4 = st.tabs(TAB_LABELS, key="main_tab", on_change="rerun")

# ---------------------------------------------------------
# Tab 1: AI resume parsing
# ---------------------------------------------------------
with tab1:
    st.header(t("parse_header"))

    if "current_job_title" not in st.session_state:
        st.session_state.current_job_title = ""
    if "current_jd" not in st.session_state:
        st.session_state.current_jd = ""
    if "custom_rules" not in st.session_state:
        st.session_state.custom_rules = ""

    job_profiles = load_job_profiles()
    job_profile_names = list(job_profiles.keys())

    # Demo: start with the sample job already loaded so visitors can score resumes immediately.
    # After a language switch, swap in the other language's sample job if the visitor was still on the sample.
    loaded = st.session_state.get("demo_profile_loaded")
    previous_sample = SAMPLE_PROFILES.get(loaded if loaded in SAMPLE_PROFILES else "en", {}).get("name")
    still_on_sample = st.session_state.get("current_job_title") == previous_sample
    if config.DEMO_MODE and (not loaded or (loaded != LANG and still_on_sample)):
        sample = job_profiles.get(SAMPLE_PROFILE_NAME)
        if sample:
            st.session_state.profile_selector = SAMPLE_PROFILE_NAME
            st.session_state.current_job_title = SAMPLE_PROFILE_NAME
            st.session_state.current_jd = sample["jd"]
            st.session_state.custom_rules = sample["rules"]
    if config.DEMO_MODE:
        st.session_state.demo_profile_loaded = LANG
    if st.session_state.get("profile_selector") not in (None, NEW_JOB, *job_profile_names):
        st.session_state.profile_selector = NEW_JOB

    def on_profile_change():
        selected = st.session_state.profile_selector
        if selected == NEW_JOB:
            st.session_state.current_job_title = ""
            st.session_state.current_jd = ""
            st.session_state.custom_rules = ""
        else:
            st.session_state.current_job_title = selected
            st.session_state.current_jd = job_profiles[selected].get("jd", "")
            st.session_state.custom_rules = job_profiles[selected].get("rules", "")

    st.markdown(t("set_opening"))
    st.selectbox(
        t("load_profile"),
        [NEW_JOB] + job_profile_names,
        key="profile_selector",
        on_change=on_profile_change,
        format_func=option_label,
    )
    if st.session_state.get("profile_selector", NEW_JOB) != NEW_JOB:
        st.button(t("delete_profile"), on_click=delete_selected_profile)
    st.text_input(t("job_title_input"), key="current_job_title")

    with st.expander(t("edit_jd_rules"), expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown(t("jd_heading"))
            jd_file = st.file_uploader(t("jd_upload"), type=["pdf"], key="jd_upload")

            if jd_file:
                try:
                    with pdfplumber.open(jd_file) as pdf:
                        extracted = "".join([p.extract_text() or "" for p in pdf.pages])
                        if extracted != st.session_state.current_jd:
                            st.session_state.current_jd = extracted
                            st.rerun()
                except Exception as e:
                    st.error(t("jd_read_failed", error=e))

            st.text_area(t("jd_edit"), key="current_jd", height=350)

            if st.button(t("draft_rules"), use_container_width=True):
                if st.session_state.current_jd.strip():
                    with st.spinner(t("drafting_rules")):
                        new_rules = generate_rules_from_jd(st.session_state.current_jd)
                        st.session_state.custom_rules = new_rules
                        st.rerun()
                else:
                    st.warning(t("need_jd"))

        with col2:
            st.markdown(t("rules_heading"))
            st.info(
                t(
                    "how_scoring_works",
                    baseline=BASELINE_SCORE,
                    threshold=SCORE_THRESHOLD,
                    delay=REJECTION_DELAY_HOURS,
                    req_lo=RULE_GUIDE["required_miss"][0],
                    req_hi=RULE_GUIDE["required_miss"][1],
                    minor_lo=RULE_GUIDE["minor_gap"][0],
                    minor_hi=RULE_GUIDE["minor_gap"][1],
                    bonus_lo=RULE_GUIDE["bonus"][0],
                    bonus_hi=RULE_GUIDE["bonus"][1],
                )
            )
            st.caption(t("rules_tip"))
            st.text_area(
                t("rules_input"),
                key="custom_rules",
                height=415,
                placeholder=t("rules_placeholder"),
            )
            rule_warnings, rules_have_points = analyze_rules(st.session_state.custom_rules) if st.session_state.custom_rules.strip() else ([], True)
            for warning_text in rule_warnings:
                st.warning(warning_text)

        st.write("")
        if st.button(t("save_profile"), type="secondary", use_container_width=True):
            job_name = st.session_state.current_job_title.strip()
            if not job_name:
                st.error(t("need_job_title"))
            else:
                existed = job_name in job_profiles
                job_profiles[job_name] = {
                    "jd": st.session_state.current_jd,
                    "rules": st.session_state.custom_rules,
                }
                save_job_profiles(job_profiles)
                verb = t("profile_updated") if existed else t("profile_saved")
                st.success(t("profile_saved_msg", verb=verb, name=job_name))
                if config.DEMO_MODE:
                    st.caption(t("profile_session_only"))

    st.divider()
    sample_dir = os.path.join(APP_DIR, "sample_resumes")
    sample_choices = {
        name: label for name, label in DEMO_SAMPLE_RESUMES[LANG].items() if os.path.exists(os.path.join(sample_dir, name))
    }

    # Internal values stay English; the labels follow the language.
    SAMPLE_SOURCE = "🧪 Use sample resumes"
    UPLOAD_SOURCE = "📥 Upload my own PDF resumes"
    SOURCE_LABELS = {SAMPLE_SOURCE: "use_samples", UPLOAD_SOURCE: "upload_own"}
    resume_source = UPLOAD_SOURCE
    if config.DEMO_MODE and sample_choices:
        st.markdown(t("resumes_heading"))
        resume_source = st.radio(
            t("resumes_label"), [SAMPLE_SOURCE, UPLOAD_SOURCE], horizontal=True, key="resume_source",
            label_visibility="collapsed", format_func=lambda value: t(SOURCE_LABELS[value]),
        )

    if resume_source == SAMPLE_SOURCE:
        st.caption(t("samples_caption"))
        chosen_samples = st.pills(
            t("sample_resumes"),
            list(sample_choices),
            selection_mode="multi",
            default=list(sample_choices),
            format_func=lambda name: sample_choices[name],
            # One key per language: the two languages offer different files.
            key="chosen_samples" if LANG == "en" else f"chosen_samples_{LANG}",
            label_visibility="collapsed",
        ) or []
        for name in chosen_samples:
            with st.expander(t("preview", label=sample_choices[name])):
                st.markdown(
                    sample_resume_preview(os.path.join(sample_dir, name), t("resume_preview_alt")), unsafe_allow_html=True
                )
                st.markdown(t("open_pdf", url=f"{config.REPO_URL}/blob/main/sample_resumes/{name}"))
        uploaded_files = [BundledResume(os.path.join(sample_dir, name)) for name in chosen_samples]
    else:
        uploaded_files = st.file_uploader(
            t("upload_resumes"), type=["pdf"], accept_multiple_files=True
        )

    if st.button(t("start_eval"), type="primary", use_container_width=True):
        if not uploaded_files:
            st.warning(t("need_resume"))
        elif (
            not st.session_state.current_jd.strip()
            or not st.session_state.custom_rules.strip()
            or not st.session_state.current_job_title.strip()
        ):
            st.warning(t("need_fields"))
        elif not analyze_rules(st.session_state.custom_rules)[1]:
            st.warning(t("need_points"))
        else:
            st.session_state.pop("evaluation_summary", None)
            outcome = {"review": 0, "rejected": 0, "problems": []}
            with st.status(t("ai_starting"), expanded=True) as status:
                for file in uploaded_files:
                    st.write(t("processing", name=file.name))

                    temp_path = f"temp_{file.name}"
                    with open(temp_path, "wb") as f:
                        f.write(file.getbuffer())

                    real_url = upload_file_and_get_link(temp_path, file.name)

                    with pdfplumber.open(temp_path) as pdf:
                        full_text = "".join([p.extract_text() or "" for p in pdf.pages])

                    saved_answer = config.DEMO_MODE and scoring_cache_key(
                        full_text,
                        st.session_state.current_jd,
                        st.session_state.custom_rules,
                        st.session_state.current_job_title,
                    ) in demo_snapshot()
                    if saved_answer:
                        st.write(t("using_saved", name=file.name))
                    else:
                        st.write(t("analyzing", name=file.name))

                    result_json, is_ok = score_resume(
                        file.name,
                        full_text,
                        st.session_state.current_jd,
                        st.session_state.custom_rules,
                        st.session_state.current_job_title,
                    )

                    if is_ok:
                        try:
                            data_dict = parse_scoring_response(result_json)
                            msg = write_to_google_sheet(data_dict, real_url)

                            if "❌" in msg or "🛑" in msg:
                                st.warning(msg)
                                outcome["problems"].append(msg)
                            else:
                                st.success(msg)
                                outcome["review" if data_dict.get("score", 0) >= SCORE_THRESHOLD else "rejected"] += 1

                        except ValueError as parse_error:
                            st.error(t("unusable", name=file.name, error=parse_error))
                            with st.expander(t("raw_response")):
                                st.code(result_json)
                            outcome["problems"].append(t("unusable_short", name=file.name))
                    else:
                        st.error(t("eval_failed", name=file.name, error=result_json))
                        outcome["problems"].append(f"{file.name}: {result_json}")

                    if os.path.exists(temp_path):
                        os.remove(temp_path)

                # Keep the log open when something failed, so the error messages stay visible
                status.update(
                    label=t("all_evaluated") if not outcome["problems"] else t("eval_with_problems"),
                    state="complete" if not outcome["problems"] else "error",
                    expanded=bool(outcome["problems"]),
                )
            st.session_state.evaluation_summary = outcome
            if outcome["review"] or outcome["rejected"]:
                st.balloons()

    # Stays visible after the run, so the user decides when to move on to the review board
    summary = st.session_state.get("evaluation_summary")
    if summary:
        if summary["review"] or summary["rejected"]:
            st.success(evaluation_summary_text(summary))
            st.button(t("go_review"), type="primary", on_click=go_to_review_tab, key="go_to_review")
        else:
            st.warning(t("none_processed"))

# ---------------------------------------------------------
# Tab 2: Manager review
# ---------------------------------------------------------
with tab2:
    st.header(t("review_header"))
    summary = st.session_state.get("evaluation_summary")
    if summary and (summary["review"] or summary["rejected"]):
        st.success(evaluation_summary_text(summary))
        if summary["problems"]:
            with st.expander(t("not_processed")):
                for problem in summary["problems"]:
                    st.warning(problem)
    st.info(t("how_review_works", threshold=SCORE_THRESHOLD))

    if sheet:
        all_rows = sheet.get_all_values()
        if len(all_rows) > 1:
            all_job_titles = sorted(
                {row[1].strip() for row in all_rows[1:] if len(row) > 1 and row[1].strip() != "" and visible_to_me(row)}
            )

            if not all_job_titles:
                st.success(t("no_data_yet"))
            else:
                col_filter, col_btn = st.columns([3, 1])
                with col_filter:
                    if st.session_state.get("filter_tab2") not in (None, ALL_JOBS, *all_job_titles):
                        st.session_state.pop("filter_tab2", None)
                    selected_job_tab2 = st.selectbox(
                        t("choose_job_review"), [ALL_JOBS] + all_job_titles, key="filter_tab2", format_func=option_label
                    )
                with col_btn:
                    st.write("")
                    st.write("")
                    if st.button(t("reload"), key="reload_sheet_tab2"):
                        st.rerun()

                # One table per job (the job is shown as a heading, not repeated in every row).
                review_by_job = {}
                for idx, row in enumerate(all_rows[1:], start=2):
                    row = pad_row(row)
                    if not visible_to_me(row):
                        continue
                    job_title_in_sheet = row[1].strip()

                    is_status_match = row[0].strip() == STATUS_PENDING_REVIEW
                    is_job_match = selected_job_tab2 == ALL_JOBS or selected_job_tab2 == job_title_in_sheet

                    if is_status_match and is_job_match:
                        review_by_job.setdefault(job_title_in_sheet, []).append(
                            {
                                "Sheet_Row": idx,
                                "Name": row[2],
                                "AI Score": int(row[4]) if row[4].isdigit() else 0,
                                "✅ Approve Interview": row[5].strip().upper() == "TRUE",
                                "📝 Manager Comment": row[6],
                                "Resume": clean_resume_url(row[16]),
                                "AI Rationale": row[15],
                                "Score arithmetic": i18n.calc_label(row[17]),
                            }
                        )

                if review_by_job:
                    # Decision columns come right after the name and score so they are visible without scrolling;
                    # the long AI text columns go last and expand on click.
                    review_columns = {
                        "Sheet_Row": None,
                        "Name": st.column_config.TextColumn(t("col_name"), width="medium"),
                        "AI Score": st.column_config.NumberColumn(t("col_score"), width="small"),
                        "✅ Approve Interview": st.column_config.CheckboxColumn(
                            t("col_invite"), default=False, width="small", help=t("col_invite_help")
                        ),
                        "📝 Manager Comment": st.column_config.TextColumn(
                            t("col_comment"), width="medium", help=t("col_comment_help")
                        ),
                        "Resume": st.column_config.LinkColumn(t("col_resume"), display_text=t("col_open"), width="small"),
                        "AI Rationale": st.column_config.TextColumn(t("col_rationale"), width="medium"),
                        "Score arithmetic": st.column_config.TextColumn(t("col_arithmetic"), width="medium"),
                    }
                    review_tables = []
                    for job_title_in_sheet in sorted(review_by_job):
                        rows_for_job = review_by_job[job_title_in_sheet]
                        st.markdown(t("job_candidates", job=job_title_in_sheet, count=len(rows_for_job)))
                        df = pd.DataFrame(rows_for_job)
                        edited_df = st.data_editor(
                            df,
                            key=f"review_{job_title_in_sheet}",
                            column_config=review_columns,
                            disabled=["Name", "AI Score", "AI Rationale", "Score arithmetic", "Resume"],
                            hide_index=True,
                            use_container_width=True,
                        )
                        review_tables.append((df, edited_df))

                    if st.button(t("save_review"), type="primary"):
                        with st.spinner(t("syncing_review")):
                            update_count = 0
                            for df, edited_df in review_tables:
                                for i in range(len(df)):
                                    original_approved = df.iloc[i]["✅ Approve Interview"]
                                    edited_approved = edited_df.iloc[i]["✅ Approve Interview"]
                                    original_comment = df.iloc[i]["📝 Manager Comment"]
                                    edited_comment = edited_df.iloc[i]["📝 Manager Comment"]

                                    if original_approved != edited_approved or original_comment != edited_comment:
                                        row_idx = int(edited_df.iloc[i]["Sheet_Row"])
                                        sheet.update_cell(row_idx, 6, str(bool(edited_approved)).upper())
                                        sheet.update_cell(row_idx, 7, edited_comment)
                                        update_count += 1

                            if update_count > 0:
                                st.session_state.pop("evaluation_summary", None)
                                # Shown after the reload below, with a button to the next step
                                st.session_state.review_saved = update_count
                                st.rerun()
                            else:
                                st.info(t("no_changes"))
                else:
                    st.info(t("no_waiting", job=option_label(selected_job_tab2)))

                if st.session_state.get("review_saved"):
                    st.success(t("review_saved", count=st.session_state.review_saved))
                    st.button(t("go_email"), type="primary", on_click=go_to_email_tab, key="go_to_email")

                # ---- Safety net: let the manager look at AI-rejected candidates and move them back ----
                st.divider()
                # Same layout as the review board: one table per job, action column first, long AI text last.
                rescue_by_job = {}
                for idx, row in enumerate(all_rows[1:], start=2):
                    row = pad_row(row)
                    if not visible_to_me(row):
                        continue
                    if row[0].strip() != STATUS_PENDING_REJECTION:
                        continue
                    if selected_job_tab2 != ALL_JOBS and row[1].strip() != selected_job_tab2:
                        continue
                    rescue_by_job.setdefault(row[1].strip(), []).append(
                        {
                            "Sheet_Row": idx,
                            "Name": row[2],
                            "AI Score": int(row[4]) if row[4].isdigit() else 0,
                            "↩️ Move back to review": False,
                            "Resume": clean_resume_url(row[16]),
                            "AI Rationale": row[15],
                            "Score arithmetic": i18n.calc_label(row[17]),
                            "Hours left": hours_until_rejection(row[7]),
                        }
                    )
                rescue_count = sum(len(rows_for_job) for rows_for_job in rescue_by_job.values())

                with st.expander(
                    t("rescue_expander", threshold=SCORE_THRESHOLD, count=rescue_count),
                    # Open it right after parsing produced new rejections, so they are not missed
                    expanded=bool(summary and summary["rejected"]),
                ):
                    st.caption(t("rescue_caption", delay=REJECTION_DELAY_HOURS))
                    if rescue_by_job:
                        rescue_columns = {
                            "Sheet_Row": None,
                            "Name": st.column_config.TextColumn(t("col_name"), width="medium"),
                            "AI Score": st.column_config.NumberColumn(t("col_score"), width="small"),
                            "↩️ Move back to review": st.column_config.CheckboxColumn(
                                t("col_move_back"), default=False, width="small", help=t("col_move_back_help")
                            ),
                            "Resume": st.column_config.LinkColumn(t("col_resume"), display_text=t("col_open"), width="small"),
                            "AI Rationale": st.column_config.TextColumn(t("col_rationale"), width="medium"),
                            "Score arithmetic": st.column_config.TextColumn(t("col_arithmetic"), width="medium"),
                            "Hours left": st.column_config.NumberColumn(
                                t("col_hours_left"), format="%d", width="small", help=t("col_hours_left_help")
                            ),
                        }
                        rescue_tables = []
                        for job_title_in_sheet in sorted(rescue_by_job):
                            rows_for_job = rescue_by_job[job_title_in_sheet]
                            st.markdown(t("job_candidates_small", job=job_title_in_sheet, count=len(rows_for_job)))
                            rescue_tables.append(
                                st.data_editor(
                                    pd.DataFrame(rows_for_job),
                                    key=f"rescue_{job_title_in_sheet}",
                                    column_config=rescue_columns,
                                    disabled=["Name", "AI Score", "Hours left", "Resume", "AI Rationale", "Score arithmetic"],
                                    hide_index=True,
                                    use_container_width=True,
                                )
                            )
                        if st.button(t("move_back"), key="rescue_btn"):
                            chosen = [
                                row_idx
                                for redited in rescue_tables
                                for row_idx in redited.loc[redited["↩️ Move back to review"], "Sheet_Row"].tolist()
                            ]
                            if not chosen:
                                st.info(t("tick_one"))
                            else:
                                with st.spinner(t("updating_sheet")):
                                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                                    for row_idx in chosen:
                                        sheet.update_cell(int(row_idx), 1, STATUS_PENDING_REVIEW)
                                        sheet.update_cell(int(row_idx), 9, now_str)
                                st.success(t("moved_back", count=len(chosen)))
                                st.session_state.pop("evaluation_summary", None)
                                for job_title_in_sheet in rescue_by_job:
                                    # drop stale cell edits before the rows change
                                    st.session_state.pop(f"rescue_{job_title_in_sheet}", None)
                                time.sleep(1)
                                st.rerun()
                    else:
                        st.caption(t("no_rejected"))
        else:
            st.success(t("nothing_to_review"))

# ---------------------------------------------------------
# Tab 3: Email routing
# ---------------------------------------------------------
with tab3:
    st.header(t("email_header"))

    if config.DEMO_MODE and st.session_state.get("demo_emails"):
        previews = st.session_state.demo_emails
        with st.expander(t("email_previews", count=len(previews)), expanded=True):
            for i, mail in enumerate(previews, start=1):
                st.markdown(t("preview_to", n=i, to=mail["to"], subject=mail["subject"]))
                if "<br>" in mail["body"]:
                    st.markdown(mail["body"], unsafe_allow_html=True)
                else:
                    st.text(mail["body"])
                st.divider()
            if st.button(t("clear_previews"), key="clear_demo_emails"):
                st.session_state.demo_emails = []
                st.rerun()

    if sheet:
        all_rows = sheet.get_all_values()

        if len(all_rows) > 1:
            all_job_titles_tab3 = sorted(
                {row[1].strip() for row in all_rows[1:] if len(row) > 1 and row[1].strip() != "" and visible_to_me(row)}
            )

            col_filter3, col_empty = st.columns([3, 1])
            with col_filter3:
                if st.session_state.get("filter_tab3") not in (None, ALL_JOBS, *all_job_titles_tab3):
                    st.session_state.pop("filter_tab3", None)
                selected_job_tab3 = st.selectbox(
                    t("choose_job_email"), [ALL_JOBS] + all_job_titles_tab3, key="filter_tab3", format_func=option_label
                )

            current_time = datetime.now()
            names_reject = []
            names_pending_invite = []
            names_manager_reject = []

            for row in all_rows[1:]:
                row = pad_row(row)
                if not visible_to_me(row):
                    continue
                status = row[0].strip()
                job_title = row[1].strip()
                name = row[2].strip()
                score = int(row[4]) if row[4].isdigit() else 0
                manager_approve_str = row[5].strip().upper()
                manager_comment = row[6].strip()
                applied_at_str = row[7].strip()

                if selected_job_tab3 != ALL_JOBS and job_title != selected_job_tab3:
                    continue

                if status == STATUS_PENDING_REJECTION:
                    time_info = t("time_unparsed")
                    try:
                        applied_time = datetime.strptime(applied_at_str, "%Y-%m-%d %H:%M")
                        target_send_time = applied_time + timedelta(hours=REJECTION_DELAY_HOURS)
                        time_diff = target_send_time - current_time

                        if time_diff.total_seconds() <= 0:
                            time_info = t("time_elapsed", hours=REJECTION_DELAY_HOURS)
                        else:
                            days = time_diff.days
                            hours = time_diff.seconds // 3600
                            if days > 0:
                                time_info = t("time_days", days=days, hours=hours)
                            else:
                                time_info = t("time_hours", hours=hours)
                    except ValueError:
                        pass
                    names_reject.append(t("queued_line", name=name, info=time_info, job=job_title))

                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "TRUE":
                    names_pending_invite.append(t("queued_line_plain", name=name, job=job_title))

                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "FALSE" and manager_comment != "":
                    names_manager_reject.append(t("queued_line_plain", name=name, job=job_title))

            col1, col2, col3 = st.columns(3)
            col1.metric(t("q_ai_reject"), len(names_reject))
            col2.metric(t("q_invite"), len(names_pending_invite))
            col3.metric(t("q_mgr_reject"), len(names_manager_reject))

            st.write("")
            col1_list, col2_list, col3_list = st.columns(3)
            for column, names in ((col1_list, names_reject), (col2_list, names_pending_invite), (col3_list, names_manager_reject)):
                with column:
                    st.markdown(t("who_receives"))
                    if names:
                        for n in names:
                            st.write(n)
                    else:
                        st.caption(t("nobody_queued"))

            st.divider()

            skip_wait = False
            if config.DEMO_MODE:
                skip_wait = st.checkbox(
                    t("skip_wait", hours=REJECTION_DELAY_HOURS),
                    key="demo_skip_wait",
                )

            if st.button(
                t("run_machine", job=option_label(selected_job_tab3)),
                type="primary",
                use_container_width=True,
            ):
                if not config.DEMO_MODE and (not SENDER_EMAIL or GMAIL_APP_PASSWORD == "YOUR_16_DIGIT_PASSWORD"):
                    st.error(t("no_credentials"))
                elif not config.BOOKING_APP_URL:
                    st.error(t("no_booking_url"))
                else:
                    with st.status(t("engine_running"), expanded=True) as log_status:
                        try:
                            yag = DemoMailer() if config.DEMO_MODE else yagmail.SMTP(user=SENDER_EMAIL, password=GMAIL_APP_PASSWORD)
                            company = config.company_name(LANG)
                            trigger_count = 0
                            delay_hours = 0 if skip_wait else REJECTION_DELAY_HOURS

                            for idx, row in enumerate(all_rows[1:], start=2):
                                row = pad_row(row)
                                if not visible_to_me(row):
                                    continue
                                status = row[0].strip()
                                job_title = row[1].strip()
                                name = row[2].strip()
                                email = row[3].strip()
                                score = int(row[4]) if row[4].isdigit() else 0
                                manager_approve_str = row[5].strip().upper()
                                manager_comment = row[6].strip()
                                applied_at_str = row[7].strip()

                                if selected_job_tab3 != ALL_JOBS and job_title != selected_job_tab3:
                                    continue

                                if status not in [STATUS_PENDING_REVIEW, STATUS_PENDING_REJECTION]:
                                    continue

                                # Routing is driven by STATUS, not score: a candidate the manager moved back
                                # to review keeps a low score but must follow the manager's decision.
                                if status == STATUS_PENDING_REJECTION:
                                    try:
                                        applied_time = datetime.strptime(applied_at_str, "%Y-%m-%d %H:%M")
                                        is_past_delay = current_time >= (applied_time + timedelta(hours=delay_hours))
                                    except ValueError:
                                        is_past_delay = False

                                    if is_past_delay:
                                        st.write(t("log_auto_reject", name=name))
                                        subject = t("mail_reject_subject", company=company, job=job_title)
                                        body = t(
                                            "mail_auto_reject_body",
                                            name=name,
                                            job=job_title,
                                            company=company,
                                            company_short=company.rstrip("."),
                                        )
                                        yag.send(to=email, subject=subject, contents=body)

                                        sheet.update_cell(idx, 1, STATUS_REJECTED)
                                        sheet.update_cell(idx, 9, current_time.strftime("%Y-%m-%d %H:%M"))
                                        trigger_count += 1
                                        time.sleep(1)

                                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "TRUE":
                                    st.write(t("log_invite", name=name))
                                    subject = t("mail_invite_subject", company=company, job=job_title)
                                    booking_params = {"name": name, "email": email}
                                    if config.DEMO_MODE:
                                        # Lets the demo booking page list this visitor's own invited candidates
                                        booking_params["v"] = st.session_state.get("visitor_tag", "")
                                    if LANG == "zh":
                                        # The candidate gets the booking page in the language of the invitation
                                        booking_params["lang"] = "zh"
                                    booking_url = f"{config.BOOKING_APP_URL}?{urlencode(booking_params)}"
                                    body = t(
                                        "mail_invite_body",
                                        name=name,
                                        job=job_title,
                                        company=company,
                                        company_short=company.rstrip("."),
                                        url=booking_url,
                                    )
                                    yag.send(to=email, subject=subject, contents=body)
                                    sheet.update_cell(idx, 1, STATUS_INVITED_NO_REPLY)
                                    sheet.update_cell(idx, 9, current_time.strftime("%Y-%m-%d %H:%M"))
                                    trigger_count += 1
                                    time.sleep(1)

                                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "FALSE" and manager_comment != "":
                                    st.write(t("log_mgr_reject", name=name))
                                    subject = t("mail_reject_subject", company=company, job=job_title)
                                    body = t(
                                        "mail_mgr_reject_body",
                                        name=name,
                                        job=job_title,
                                        company=company,
                                        company_short=company.rstrip("."),
                                    )
                                    yag.send(to=email, subject=subject, contents=body)

                                    sheet.update_cell(idx, 1, STATUS_REJECTED)
                                    sheet.update_cell(idx, 9, current_time.strftime("%Y-%m-%d %H:%M"))
                                    trigger_count += 1
                                    time.sleep(1)

                            log_status.update(
                                label=t("engine_done", count=trigger_count), state="complete", expanded=True
                            )
                            time.sleep(1)
                            st.rerun()
                        except Exception as e:
                            st.error(t("engine_crashed", error=e))
        else:
            st.info(t("no_email_data"))

# ---------------------------------------------------------
# Tab 4: Analytics dashboard
# ---------------------------------------------------------
with tab4:
    st.header(t("analytics_header"))
    if config.DEMO_MODE:
        st.caption(t("analytics_demo"))

    col_dash_load, col_dash_empty = st.columns([1, 3])
    with col_dash_load:
        if st.button(t("sync_analytics"), type="primary"):
            with st.spinner(t("fetching")):
                if sheet:
                    raw_vals = sheet.get_all_values()
                    if len(raw_vals) > 1:
                        width = max(len(r) for r in raw_vals)
                        header = raw_vals[0] + [f"extra_{i}" for i in range(len(raw_vals[0]), width)]
                        body = [r + [""] * (width - len(r)) for r in raw_vals[1:]]
                        st.session_state.df_dash = pd.DataFrame(body, columns=header)
                        st.success(t("synced"))
                    else:
                        st.warning(t("no_recruiting_data"))
                else:
                    st.error(t("sheet_not_connected"))

    if not st.session_state.df_dash.empty:
        df_dash = st.session_state.df_dash.copy()

        # Data cleaning
        df_dash = df_dash[df_dash["name"].astype(str).str.strip() != ""]

        if not df_dash.empty:
            df_dash["expected_salary"] = df_dash["expected_salary"].astype(str).str.replace(r"[^\d.]", "", regex=True)
            df_dash["expected_salary"] = pd.to_numeric(df_dash["expected_salary"], errors="coerce")
            df_dash["total_experience_years"] = (
                df_dash["total_experience_years"].astype(str).str.extract(r"(\d+\.?\d*)")[0].astype(float)
            )
            df_dash["score"] = pd.to_numeric(df_dash["score"], errors="coerce")
            df_dash["applied_at"] = pd.to_datetime(df_dash["applied_at"], errors="coerce")

            st.divider()

            ALL = "All"
            TIME_LABELS = {ALL: "all", "Last week": "last_week", "Last 3 weeks": "last_3_weeks"}
            c_f1, c_f2 = st.columns(2)
            with c_f1:
                dash_job_list = [ALL] + list(df_dash["job_applied"].dropna().unique())
                sel_dash_job = st.selectbox(
                    t("filter_job"), dash_job_list, format_func=lambda value: t("all") if value == ALL else value
                )
            with c_f2:
                sel_dash_time = st.radio(
                    t("time_window"), list(TIME_LABELS), horizontal=True, format_func=lambda value: t(TIME_LABELS[value])
                )

            if sel_dash_job != ALL:
                df_dash = df_dash[df_dash["job_applied"] == sel_dash_job]

            now_t = pd.Timestamp.now()
            if sel_dash_time == "Last week":
                df_dash = df_dash[df_dash["applied_at"] >= (now_t - pd.Timedelta(days=7))]
            elif sel_dash_time == "Last 3 weeks":
                df_dash = df_dash[df_dash["applied_at"] >= (now_t - pd.Timedelta(days=21))]

            if not df_dash.empty:
                # Core KPI funnel
                t_app = len(df_dash)
                t_inv = len(df_dash[df_dash["status"].isin(INVITED_STATUSES)])
                t_hire = len(df_dash[df_dash["status"] == STATUS_HIRED])

                ck1, ck2, ck3 = st.columns(3)
                ck1.metric(t("k_total"), f"{t_app}")
                ck2.metric(
                    t("k_interview"),
                    f"{t_inv}",
                    t("k_conversion", pct=int(t_inv / t_app * 100) if t_app > 0 else 0),
                )
                ck3.metric(t("k_hired"), f"{t_hire}", t("k_hire_rate", pct=int(t_hire / t_app * 100) if t_app > 0 else 0))

                st.divider()

                # ================= Charts 1 & 2 =================
                col_c1, col_c2 = st.columns(2)

                with col_c1:
                    # Interview conversion by experience band (dual-axis)
                    def seg_exp(y):
                        if pd.isna(y):
                            return "Unknown"
                        if y <= 2:
                            return "0-2 yrs"
                        elif y <= 5:
                            return "3-5 yrs"
                        return "5+ yrs"

                    df_dash["experience_band"] = df_dash["total_experience_years"].apply(seg_exp)
                    df_dash["is_invited"] = df_dash["status"].isin(INVITED_STATUSES)

                    exp_groups = ["0-2 yrs", "3-5 yrs", "5+ yrs"]
                    exp_df = df_dash.groupby("experience_band")["is_invited"].agg(["count", "sum"]).reindex(exp_groups).fillna(0)
                    exp_df["rate"] = (exp_df["sum"] / exp_df["count"] * 100).fillna(0).round(1)

                    fig_exp = make_subplots(specs=[[{"secondary_y": True}]])
                    band_names = [i18n.band_label(band) for band in exp_df.index]
                    fig_exp.add_trace(
                        go.Bar(x=band_names, y=exp_df["count"], name=t("c_applicants"), marker_color=UI_THEME["PRIMARY"]),
                        secondary_y=False,
                    )
                    fig_exp.add_trace(
                        go.Scatter(
                            x=band_names,
                            y=exp_df["rate"],
                            name=t("c_conversion_pct"),
                            mode="lines+markers",
                            marker=dict(size=10, color=UI_THEME["WARNING"]),
                            line=dict(width=3),
                        ),
                        secondary_y=True,
                    )

                    fig_exp.update_layout(
                        title=t("c_exp_title"),
                        plot_bgcolor="rgba(0,0,0,0)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
                    fig_exp.update_yaxes(title_text=t("c_applicants"), secondary_y=False)
                    fig_exp.update_yaxes(
                        title_text=t("c_conversion_axis"), secondary_y=True, range=[0, max(100, exp_df["rate"].max() + 10)]
                    )
                    st.plotly_chart(fig_exp, use_container_width=True)

                with col_c2:
                    # Rejection reasons (donut)
                    df_rej = df_dash[~df_dash["status"].isin(INVITED_STATUSES)].copy()

                    if not df_rej.empty:
                        df_rej["fail_reason"] = df_rej.apply(categorize_reason, axis=1)
                        reason_counts = df_rej["fail_reason"].value_counts()

                        fig_reason = go.Figure(
                            data=[
                                go.Pie(
                                    labels=[i18n.reason_label(reason) for reason in reason_counts.index],
                                    values=reason_counts.values,
                                    hole=0.5,
                                    marker=dict(
                                        colors=[UI_THEME["DANGER"], UI_THEME["SECONDARY"], UI_THEME["WARNING"], UI_THEME["PRIMARY"]]
                                    ),
                                    textinfo="label+percent",
                                    textposition="outside",
                                )
                            ]
                        )
                        fig_reason.update_layout(title=t("c_reason_title"), showlegend=False)
                        st.plotly_chart(fig_reason, use_container_width=True)
                    else:
                        st.info(t("no_rejections"))

                st.divider()

                # ================= Charts 3 & 4 =================
                col_c3, col_c4 = st.columns(2)

                with col_c3:
                    # Score vs. expected salary
                    fig_scatter = go.Figure()
                    fig_scatter.add_trace(
                        go.Scatter(
                            x=df_dash["score"],
                            y=df_dash["expected_salary"],
                            mode="markers",
                            text=df_dash["name"],
                            marker=dict(size=12, color=UI_THEME["SECONDARY"], opacity=0.7, line=dict(width=1, color="white")),
                            hovertemplate=t("c_hover"),
                        )
                    )
                    fig_scatter.update_layout(
                        title=t("c_scatter_title"),
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis_title=t("c_score_axis"),
                        yaxis_title=t("c_salary_axis"),
                    )
                    st.plotly_chart(fig_scatter, use_container_width=True)

                with col_c4:
                    # Skill premium (dual-axis)
                    df_s = df_dash[["name", "expected_salary", "skills_extracted"]].copy()
                    df_s["skills_extracted"] = df_s["skills_extracted"].astype(str).str.split(",")
                    df_exp = df_s.explode("skills_extracted")
                    df_exp["skills_extracted"] = df_exp["skills_extracted"].str.strip().str.upper()
                    df_exp = df_exp[df_exp["skills_extracted"] != ""]

                    skill_agg = (
                        df_exp.groupby("skills_extracted")
                        .agg(Count=("name", "count"), AvgSalary=("expected_salary", "mean"))
                        .reset_index()
                    )

                    top_skills = skill_agg.sort_values("Count", ascending=False).head(10)

                    fig_skills = make_subplots(specs=[[{"secondary_y": True}]])
                    fig_skills.add_trace(
                        go.Bar(
                            x=top_skills["skills_extracted"],
                            y=top_skills["Count"],
                            name=t("c_with_skill"),
                            marker_color=UI_THEME["SKILL_BAR_COLOR"],
                        ),
                        secondary_y=False,
                    )
                    fig_skills.add_trace(
                        go.Scatter(
                            x=top_skills["skills_extracted"],
                            y=top_skills["AvgSalary"],
                            name=t("c_avg_salary"),
                            mode="lines+markers",
                            marker=dict(size=10, color=UI_THEME["WARNING"]),
                            line=dict(width=3),
                        ),
                        secondary_y=True,
                    )

                    fig_skills.update_layout(
                        title=t("c_skill_title"),
                        plot_bgcolor="rgba(0,0,0,0)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
                    fig_skills.update_yaxes(title_text=t("c_with_skill_axis"), secondary_y=False)
                    fig_skills.update_yaxes(title_text=t("c_avg_salary_axis"), secondary_y=True)
                    st.plotly_chart(fig_skills, use_container_width=True)

                # ================= AI report =================
                st.divider()
                st.subheader(t("insights_header"))
                st.caption(t("insights_caption"))
                if st.button(t("gen_insights"), key="gen_insights"):
                    insight_summary = insights.build_summary(
                        df_dash, INVITED_STATUSES, STATUS_HIRED, SCORE_THRESHOLD, is_demo=config.DEMO_MODE
                    )
                    with st.spinner(t("reading_numbers")):
                        st.session_state.analytics_report = {
                            "text": generate_insights_report(insight_summary),
                            "summary": insight_summary,
                            "scope": t(
                                "report_scope",
                                job=t("all") if sel_dash_job == ALL else sel_dash_job,
                                window=t(TIME_LABELS[sel_dash_time]),
                            ),
                        }
                report = st.session_state.get("analytics_report")
                if report:
                    st.caption(t("report_for", scope=report["scope"]))
                    show_insights_report(report)
                    with st.expander(t("report_data")):
                        table_label = lambda code: i18n.band_label(i18n.reason_label(code))  # noqa: E731
                        for table_title, table in insights.summary_tables(report["summary"], LANG, table_label):
                            st.markdown(f"**{table_title}**")
                            st.dataframe(table, hide_index=True, use_container_width=True)

            else:
                st.warning(t("no_match_filters"))
    else:
        st.info(t("click_sync"))
