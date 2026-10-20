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
import json
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
from google.oauth2.service_account import Credentials
from plotly.subplots import make_subplots

import config
import demo_support
from scoring import analyze_rules as _analyze_rules
from scoring import parse_scoring_response as _parse_scoring_response
from config import (
    COMPANY_NAME,
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
st.set_page_config(page_title="HRIS Recruiting Command Center", layout="wide", page_icon="👔")

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

if config.DEMO_MODE:
    st.warning(
        "🧪 **Demo mode.** No real emails are sent, no calendar events are created, and your uploads are not "
        "stored in Drive. Emails are shown on screen instead. Saved job profiles live only in your browser session. "
        "Candidates you upload are tagged with your private code (for example ariel.lin+k3f9@example.com), so other "
        "visitors do not see them, and they are deleted after 24 hours. The Analytics tab also includes about 40 "
        "fictional sample candidates."
    )

try:
    GEMINI_API_KEY = st.secrets["GEMINI_API_KEY"]
    # Email credentials are only needed outside demo mode
    SENDER_EMAIL = config.get_setting("SENDER_EMAIL", "demo@example.com") if config.DEMO_MODE else st.secrets["SENDER_EMAIL"]
    GMAIL_APP_PASSWORD = config.get_setting("GMAIL_APP_PASSWORD", "") if config.DEMO_MODE else st.secrets["GMAIL_APP_PASSWORD"]
except Exception as e:
    st.error(f"❌ Missing required secret: {e}. Check .streamlit/secrets.toml (see secrets.example.toml).")
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
        st.error(f"❌ Could not connect to Google Sheets: {e}")
        return None


@st.cache_resource
def init_ai_client():
    try:
        return genai.Client(api_key=GEMINI_API_KEY)
    except Exception as e:
        st.error(f"❌ AI client initialization failed: {e}")
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


if config.DEMO_MODE and sheet is not None and not st.session_state.get("housekeeping_done"):
    try:
        demo_housekeeping()
    except Exception as housekeeping_error:  # noqa: BLE001 - never block the app on cleanup
        st.warning(f"Demo cleanup skipped: {housekeeping_error}")
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

ALL_JOBS = "All jobs"
NEW_JOB = "➕ Add a new job manually"

# ==================== Job profile storage ====================
PROFILE_FILE = "job_profiles.json"
APP_DIR = os.path.dirname(os.path.abspath(__file__))
SAMPLE_PROFILE_NAME = "Python Backend Engineer"


def _read_text(relative_path):
    try:
        with open(os.path.join(APP_DIR, relative_path), "r", encoding="utf-8") as f:
            return f.read().strip()
    except OSError:
        return ""


def sample_profiles():
    """Ready-made job profile so demo visitors can start scoring in two clicks."""
    return {
        SAMPLE_PROFILE_NAME: {
            "jd": _read_text("jd.txt"),
            "rules": _read_text("sample_jd/python_backend_engineer_rules.txt"),
        }
    }


def load_job_profiles():
    """Demo mode: profiles live in this visitor's session only (nothing is shared or persisted).
    Normal mode: profiles are stored in a local JSON file."""
    if config.DEMO_MODE:
        if "job_profiles" not in st.session_state:
            st.session_state.job_profiles = sample_profiles()
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
                raise RuntimeError(
                    "The AI quota for this demo is used up (the free Gemini tier allows only a small number of "
                    "requests per day). Please try again later."
                ) from e
            if attempt < MAX_AI_ATTEMPTS - 1:
                time.sleep(2 ** attempt)
    raise last_error


def generate_rules_from_jd(jd_text):
    prompt = f"""
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

    [JOB DESCRIPTION (JD)]:
    {jd_text}
    """
    cache = demo_ai_cache() if config.DEMO_MODE else None
    cache_key = "rules:" + hashlib.sha256(prompt.encode("utf-8")).hexdigest()
    if cache is not None and cache_key in cache:
        return cache[cache_key]["raw"]
    try:
        response = generate_with_retry(prompt)
        text = response.text.strip().replace("**", "")
        if cache is not None:
            cache[cache_key] = {"raw": text, "at": datetime.now()}
        return text
    except Exception as e:
        return f"⚠️ Rule generation failed: {e}"


UPLOAD_FAILED_URL = "https://drive.google.com/resume/upload_failed"


def upload_file_and_get_link(file_path, file_name):
    """Upload through a Google Apps Script web app (see apps_script/Code.gs).

    Service accounts have no Drive storage quota of their own, so the upload is proxied
    through a script that runs as a normal Google user and returns a shareable link.
    """
    if config.DEMO_MODE:
        # Never store strangers' uploads. The bundled sample resumes link to their copy on GitHub instead.
        sample_dir = os.path.join(APP_DIR, "sample_resumes")
        if os.path.isdir(sample_dir) and file_name in os.listdir(sample_dir):
            return f"{config.REPO_URL}/blob/main/sample_resumes/{file_name}"
        return ""

    if not config.APPS_SCRIPT_URL:
        st.warning("APPS_SCRIPT_URL is not configured, so the resume file will not be stored in Drive.")
        return UPLOAD_FAILED_URL

    try:
        with open(file_path, "rb") as f:
            encoded_file = base64.b64encode(f.read()).decode("utf-8")

        payload = {"fileName": file_name, "mimeType": "application/pdf", "fileData": encoded_file}
        response = httpx.post(config.APPS_SCRIPT_URL, json=payload, timeout=60.0, follow_redirects=True)

        if response.status_code == 200 and "Error" not in response.text:
            return response.text.strip()
        st.error(f"❌ Upload proxy returned an error: {response.text}")
        return UPLOAD_FAILED_URL

    except Exception as e:
        st.error(f"❌ Upload via proxy failed: {e}")
        return UPLOAD_FAILED_URL


def _score_resume_uncached(file_name, resume_text, jd_text, rules_text, target_job_title):
    current_now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
    prompt = f"""
    You are an extremely strict HR hiring manager at a technology company. Using the [Job Description] and the
    [Candidate Resume] below, score the candidate objectively and extract structured data.
    Write all free-text fields in English, even if the resume is in another language.

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
    last_error = "No response"
    for _ in range(2):
        try:
            response = generate_with_retry(prompt, json_mode=True)
            parse_scoring_response(response.text)  # validate only; callers parse again
            return response.text, True
        except Exception as e:  # noqa: BLE001
            last_error = str(e)
            if "quota" in last_error:
                break
    return last_error, False


@st.cache_resource
def demo_ai_cache():
    """Demo only: shared by all visitors, so the same resume + rules is scored by the AI once."""
    return {}


DEMO_CACHE_HOURS = 24
DEMO_CACHE_MAX_ENTRIES = 500
TIME_STAMP = re.compile(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}")


def score_resume(file_name, resume_text, jd_text, rules_text, target_job_title):
    """Score one resume. In demo mode an identical resume + JD + rules reuses the earlier AI answer, which keeps
    the free-tier quota for visitors who change something and makes the sample results repeatable."""
    if not config.DEMO_MODE:
        return _score_resume_uncached(file_name, resume_text, jd_text, rules_text, target_job_title)

    key = hashlib.sha256("\x1f".join([MODEL_NAME, resume_text, jd_text, rules_text, target_job_title]).encode("utf-8")).hexdigest()
    cache = demo_ai_cache()
    now = datetime.now()
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


def parse_scoring_response(raw_text):
    """Score is computed by code (see scoring.py), using this app's baseline."""
    return _parse_scoring_response(raw_text, BASELINE_SCORE)


def analyze_rules(rules_text):
    """Sanity checks on the free-text rules (see scoring.py), using this app's baseline and threshold."""
    return _analyze_rules(rules_text, BASELINE_SCORE, SCORE_THRESHOLD)


def write_to_google_sheet(data_dict, real_resume_url):
    if sheet is None:
        return "🛑 Sheet is not connected."
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
            return f"🛑 Duplicate blocked: {candidate_name} ({target_email}) already applied to this job; not written again."

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
        return f"🚀 {candidate_name} written. Job: [{target_job}] / Status: [{final_status}]"

    except Exception as e:
        return f"❌ Fatal error while writing to the sheet: {e}"


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
    return max(0.0, round(remaining, 1))


# ==================== Streamlit UI ====================

st.title("👔 HRIS Recruiting Command Center")
if config.DEMO_MODE:
    with st.expander("🛠️ Demo tools"):
        st.caption(
            f"Your private code is **{st.session_state.get('visitor_tag')}**. Only candidates tagged with it appear in "
            "Manager Review and Email Routing. Keep this page's URL (it contains the code) to come back to them."
        )
        confirm_reset = st.checkbox("I understand this deletes every candidate in the demo sheet, for all visitors", key="confirm_reset")
        if st.button("♻️ Reset demo data", disabled=not confirm_reset):
            try:
                demo_housekeeping(force_reset=True)
                st.session_state.df_dash = pd.DataFrame()
                st.success("Demo data reset: all candidates were removed and the sample candidates reloaded.")
            except Exception as reset_error:  # noqa: BLE001
                st.error(f"Reset failed: {reset_error}")
tab1, tab2, tab3, tab4 = st.tabs(
    ["📥 AI Resume Parsing", "👨‍💼 Manager Review", "📬 Email Routing", "📊 Analytics Dashboard"]
)

# ---------------------------------------------------------
# Tab 1: AI resume parsing
# ---------------------------------------------------------
with tab1:
    st.header("📥 AI Resume Parsing")

    if "current_job_title" not in st.session_state:
        st.session_state.current_job_title = ""
    if "current_jd" not in st.session_state:
        st.session_state.current_jd = ""
    if "custom_rules" not in st.session_state:
        st.session_state.custom_rules = ""

    job_profiles = load_job_profiles()
    job_profile_names = list(job_profiles.keys())

    # Demo: start with the sample job already loaded so visitors can score resumes immediately
    if config.DEMO_MODE and not st.session_state.get("demo_profile_loaded"):
        sample = job_profiles.get(SAMPLE_PROFILE_NAME)
        if sample:
            st.session_state.profile_selector = SAMPLE_PROFILE_NAME
            st.session_state.current_job_title = SAMPLE_PROFILE_NAME
            st.session_state.current_jd = sample["jd"]
            st.session_state.custom_rules = sample["rules"]
        st.session_state.demo_profile_loaded = True

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

    st.markdown("### 🎯 Set the current opening")
    st.selectbox(
        "📂 Load a saved job profile:",
        [NEW_JOB] + job_profile_names,
        key="profile_selector",
        on_change=on_profile_change,
    )
    if st.session_state.get("profile_selector", NEW_JOB) != NEW_JOB:
        st.button("🗑️ Delete this saved profile", on_click=delete_selected_profile)
    st.text_input("Job title you are currently hiring for:", key="current_job_title")

    with st.expander("📝 Edit job requirements (JD) and AI scoring rules", expanded=True):
        col1, col2 = st.columns(2)

        with col1:
            st.markdown("### 📄 1. Job Description (JD)")
            jd_file = st.file_uploader("Upload a JD PDF to extract its text (optional)", type=["pdf"], key="jd_upload")

            if jd_file:
                try:
                    with pdfplumber.open(jd_file) as pdf:
                        extracted = "".join([p.extract_text() or "" for p in pdf.pages])
                        if extracted != st.session_state.current_jd:
                            st.session_state.current_jd = extracted
                            st.rerun()
                except Exception as e:
                    st.error(f"❌ Could not read the JD file: {e}")

            st.text_area("Review or edit the JD text:", key="current_jd", height=350)

            if st.button("✨ Let AI draft 7-10 scoring rules from the JD", use_container_width=True):
                if st.session_state.current_jd.strip():
                    with st.spinner("AI is analyzing the JD's keywords and screening criteria..."):
                        new_rules = generate_rules_from_jd(st.session_state.current_jd)
                        st.session_state.custom_rules = new_rules
                        st.rerun()
                else:
                    st.warning("Upload or enter a JD first.")

        with col2:
            st.markdown("### ⚖️ 2. AI scoring and deduction rules")
            st.info(
                "**How scoring works**\n\n"
                f"- Every candidate starts at **{BASELINE_SCORE}** points. Your rules add or subtract from this; the final score is 0 to 100.\n"
                f"- **Below {SCORE_THRESHOLD}:** automatic rejection email (sent {REJECTION_DELAY_HOURS} hours after applying). "
                f"**{SCORE_THRESHOLD} or above:** goes to the hiring manager.\n"
                f"- **Tip:** missing a required item should cost {RULE_GUIDE['required_miss'][0]} to {RULE_GUIDE['required_miss'][1]} points; "
                f"minor gaps {RULE_GUIDE['minor_gap'][0]} to {RULE_GUIDE['minor_gap'][1]}; "
                f"nice-to-have skills +{RULE_GUIDE['bonus'][0]} to +{RULE_GUIDE['bonus'][1]}."
            )
            st.caption("Type your own rules, or use the button on the left to have AI draft them in a clean format.")
            st.text_area(
                "Enter your evaluation and deduction logic:",
                key="custom_rules",
                height=415,
                placeholder="• Deduction: -15 if the candidate has no Python experience\n• Bonus: +5 for cloud deployment experience",
            )
            rule_warnings, rules_have_points = analyze_rules(st.session_state.custom_rules) if st.session_state.custom_rules.strip() else ([], True)
            for warning_text in rule_warnings:
                st.warning(warning_text)

        st.write("")
        if st.button("💾 Save this job profile (JD + scoring rules)", type="secondary", use_container_width=True):
            job_name = st.session_state.current_job_title.strip()
            if not job_name:
                st.error("⚠️ Enter a job title above before saving.")
            else:
                existed = job_name in job_profiles
                job_profiles[job_name] = {
                    "jd": st.session_state.current_jd,
                    "rules": st.session_state.custom_rules,
                }
                save_job_profiles(job_profiles)
                verb = "Updated the existing profile" if existed else "Saved the JD and scoring rules as the profile"
                st.success(f"✅ {verb} \"{job_name}\". Load it from the dropdown next time.")
                if config.DEMO_MODE:
                    st.caption("Demo mode: saved profiles are kept only in your browser session.")

    sample_dir = os.path.join(APP_DIR, "sample_resumes")
    sample_files = sorted(f for f in os.listdir(sample_dir) if f.lower().endswith(".pdf")) if os.path.isdir(sample_dir) else []
    with st.expander("🧪 Test these rules on the sample resumes (nothing is saved)"):
        st.caption(
            "Dry run: scores the chosen sample resumes with the JD and rules above, so you can see what a rule change does "
            "before you use it. Nothing is written to the sheet and no emails are queued. Each resume is one AI call, "
            "and results can vary by a few points between runs."
        )
        if not sample_files:
            st.info("No sample resumes found.")
        else:
            test_choice = st.multiselect("Sample resumes to test with:", sample_files, default=sample_files, key="test_files")
            if st.button("▶️ Run the test", key="run_rule_test"):
                if not (st.session_state.current_jd.strip() and st.session_state.custom_rules.strip()):
                    st.warning("⚠️ Fill in the JD and the scoring rules first.")
                elif not analyze_rules(st.session_state.custom_rules)[1]:
                    st.warning("⚠️ The scoring rules contain no point values (such as -15 or +5).")
                elif not test_choice:
                    st.info("Choose at least one sample resume.")
                else:
                    results = []
                    progress = st.progress(0.0, text="Scoring sample resumes...")
                    for i, sample_name in enumerate(test_choice):
                        try:
                            with pdfplumber.open(os.path.join(sample_dir, sample_name)) as pdf:
                                sample_text = "".join([p.extract_text() or "" for p in pdf.pages])
                            raw, ok = score_resume(
                                sample_name,
                                sample_text,
                                st.session_state.current_jd,
                                st.session_state.custom_rules,
                                st.session_state.current_job_title or "Test job",
                            )
                            if not ok:
                                raise ValueError(raw)
                            scored = parse_scoring_response(raw)
                            results.append({"file": sample_name, "ok": True, "data": scored})
                        except Exception as test_error:  # noqa: BLE001 - show any failure per resume
                            results.append({"file": sample_name, "ok": False, "error": str(test_error)})
                        progress.progress((i + 1) / len(test_choice), text=f"Scored {i + 1} of {len(test_choice)}")
                    progress.empty()
                    st.session_state.rule_test_results = results

            results = st.session_state.get("rule_test_results")
            if results:
                good = [r for r in results if r["ok"]]
                st.markdown(
                    f"**Result:** {sum(1 for r in good if r['data']['score'] >= SCORE_THRESHOLD)} of {len(good)} scored resumes "
                    f"would reach the hiring manager ({SCORE_THRESHOLD}+), the rest would be auto-rejected."
                )
                table = [
                    {
                        "Resume": r["file"],
                        "Candidate": r["data"].get("name", ""),
                        "Score": r["data"]["score"],
                        "Outcome": "→ Manager review" if r["data"]["score"] >= SCORE_THRESHOLD else "→ Auto-rejection",
                    }
                    for r in good
                ]
                if table:
                    st.dataframe(pd.DataFrame(table), hide_index=True, use_container_width=True)
                    for r in good:
                        st.markdown(f"- **{r['data'].get('name', r['file'])}**: {r['data']['score_calculation']}".replace("$", "\\$"))
                for r in results:
                    if not r["ok"]:
                        st.error(f"{r['file']}: {r['error']}")

    st.divider()
    uploaded_files = st.file_uploader("📥 Upload candidate resumes (multiple files allowed)", type=["pdf"], accept_multiple_files=True)

    if st.button("🚀 Start AI evaluation", type="primary", use_container_width=True):
        if not uploaded_files:
            st.warning("⚠️ Upload at least one resume first.")
        elif (
            not st.session_state.current_jd.strip()
            or not st.session_state.custom_rules.strip()
            or not st.session_state.current_job_title.strip()
        ):
            st.warning("⚠️ Job title, JD and scoring rules must not be empty.")
        elif not analyze_rules(st.session_state.custom_rules)[1]:
            st.warning("⚠️ The scoring rules contain no point values (such as -15 or +5), so scores cannot be computed. Please fix the rules first.")
        else:
            with st.status("🤖 AI interviewer starting...", expanded=True) as status:
                for file in uploaded_files:
                    st.write(f"Processing: {file.name}...")

                    temp_path = f"temp_{file.name}"
                    with open(temp_path, "wb") as f:
                        f.write(file.getbuffer())

                    real_url = upload_file_and_get_link(temp_path, file.name)

                    with pdfplumber.open(temp_path) as pdf:
                        full_text = "".join([p.extract_text() or "" for p in pdf.pages])

                    st.write(f"🧠 AI is analyzing {file.name} in depth...")

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
                            else:
                                st.success(msg)

                        except ValueError as parse_error:
                            st.error(f"❌ The AI response for {file.name} was unusable and was NOT written: {parse_error}")
                            with st.expander("🔍 View the raw response"):
                                st.code(result_json)
                    else:
                        st.error(f"❌ Evaluation of {file.name} failed: {result_json}")

                    if os.path.exists(temp_path):
                        os.remove(temp_path)

                status.update(label="✅ All resumes evaluated!", state="complete", expanded=False)
            st.balloons()

# ---------------------------------------------------------
# Tab 2: Manager review
# ---------------------------------------------------------
with tab2:
    st.header("👨‍💼 Manager Review Board")
    st.info(
        f"💡 **How review works**\n\nResumes the AI scored {SCORE_THRESHOLD} or higher appear here for the hiring manager, "
        "plus any lower-scoring candidate you move back from the auto-rejected list below. "
        "Tick the box to invite a candidate to interview. If the candidate is not a fit, leave the box unticked and "
        "**always write the reason in the comment column**, so HR can analyze the funnel and adjust the hiring strategy."
    )

    if sheet:
        all_rows = sheet.get_all_values()
        if len(all_rows) > 1:
            all_job_titles = sorted(
                {row[1].strip() for row in all_rows[1:] if len(row) > 1 and row[1].strip() != "" and visible_to_me(row)}
            )

            if not all_job_titles:
                st.success("🎉 There is no data in the sheet yet.")
            else:
                col_filter, col_btn = st.columns([3, 1])
                with col_filter:
                    selected_job_tab2 = st.selectbox(
                        "🎯 Choose the job to review:", [ALL_JOBS] + all_job_titles, key="filter_tab2"
                    )
                with col_btn:
                    st.write("")
                    st.write("")
                    if st.button("🔄 Reload list", key="reload_sheet_tab2"):
                        st.rerun()

                review_data = []
                for idx, row in enumerate(all_rows[1:], start=2):
                    row = pad_row(row)
                    if not visible_to_me(row):
                        continue
                    job_title_in_sheet = row[1].strip()

                    is_status_match = row[0].strip() == STATUS_PENDING_REVIEW
                    is_job_match = selected_job_tab2 == ALL_JOBS or selected_job_tab2 == job_title_in_sheet

                    if is_status_match and is_job_match:
                        review_data.append(
                            {
                                "Sheet_Row": idx,
                                "Job": job_title_in_sheet,
                                "Name": row[2],
                                "AI Score": int(row[4]) if row[4].isdigit() else 0,
                                "AI Rationale": row[15],
                                "Score arithmetic": row[17],
                                "✅ Approve Interview": row[5].strip().upper() == "TRUE",
                                "📝 Manager Comment": row[6],
                                "Resume": clean_resume_url(row[16]),
                            }
                        )

                if review_data:
                    df = pd.DataFrame(review_data)

                    edited_df = st.data_editor(
                        df,
                        column_config={
                            "Sheet_Row": None,
                            "Resume": st.column_config.LinkColumn("Resume", display_text="Open resume"),
                            "✅ Approve Interview": st.column_config.CheckboxColumn("✅ Approve Interview", default=False),
                            "AI Rationale": st.column_config.TextColumn("AI Rationale (click to expand)", width="large"),
                            "Score arithmetic": st.column_config.TextColumn("Score arithmetic (click to expand)", width="large"),
                        },
                        disabled=["Job", "Name", "AI Score", "AI Rationale", "Score arithmetic", "Resume"],
                        hide_index=True,
                        use_container_width=True,
                    )

                    if st.button("💾 Save review decisions and sync to the cloud", type="primary"):
                        with st.spinner("Syncing the manager's decisions to Google Sheets..."):
                            update_count = 0
                            for i in range(len(df)):
                                original_approved = df.iloc[i]["✅ Approve Interview"]
                                edited_approved = edited_df.iloc[i]["✅ Approve Interview"]
                                original_comment = df.iloc[i]["📝 Manager Comment"]
                                edited_comment = edited_df.iloc[i]["📝 Manager Comment"]

                                if original_approved != edited_approved or original_comment != edited_comment:
                                    row_idx = edited_df.iloc[i]["Sheet_Row"]
                                    sheet.update_cell(row_idx, 6, str(edited_approved).upper())
                                    sheet.update_cell(row_idx, 7, edited_comment)
                                    update_count += 1

                            if update_count > 0:
                                st.success(f"✅ Synced {update_count} review decision(s). Go to the \"Email Routing\" tab to send notifications.")
                                time.sleep(1)
                                st.rerun()
                            else:
                                st.info("No changes to save.")
                else:
                    st.info(f"👉 No resumes are waiting for manager review under \"{selected_job_tab2}\".")

                # ---- Safety net: let the manager look at AI-rejected candidates and move them back ----
                st.divider()
                rescue_data = []
                for idx, row in enumerate(all_rows[1:], start=2):
                    row = pad_row(row)
                    if not visible_to_me(row):
                        continue
                    if row[0].strip() != STATUS_PENDING_REJECTION:
                        continue
                    if selected_job_tab2 != ALL_JOBS and row[1].strip() != selected_job_tab2:
                        continue
                    rescue_data.append(
                        {
                            "Sheet_Row": idx,
                            "Job": row[1].strip(),
                            "Name": row[2],
                            "AI Score": int(row[4]) if row[4].isdigit() else 0,
                            "Hours left": hours_until_rejection(row[7]),
                            "↩️ Move back to review": False,
                            "AI Rationale": row[15],
                            "Resume": clean_resume_url(row[16]),
                            "Score arithmetic": row[17],
                        }
                    )

                with st.expander(
                    f"🔎 Auto-rejected candidates (score below {SCORE_THRESHOLD}, rejection email not sent yet): {len(rescue_data)}"
                ):
                    st.caption(
                        f"The AI rejected these candidates. Their rejection email is sent {REJECTION_DELAY_HOURS} hours after they "
                        "applied, so until then you can read the AI's reasoning and move anyone back to the review board above. "
                        "Once the email is sent, the decision is final."
                    )
                    if rescue_data:
                        rdf = pd.DataFrame(rescue_data)
                        redited = st.data_editor(
                            rdf,
                            key="rescue_editor",
                            column_config={
                                "Sheet_Row": None,
                                "Resume": st.column_config.LinkColumn("Resume", display_text="Open resume"),
                                "↩️ Move back to review": st.column_config.CheckboxColumn("↩️ Move back to review", default=False),
                                "AI Rationale": st.column_config.TextColumn("AI Rationale (click to expand)", width="large"),
                                "Score arithmetic": st.column_config.TextColumn("Score arithmetic (click to expand)", width="large"),
                                "Hours left": st.column_config.NumberColumn("Hours until email", format="%.1f"),
                            },
                            disabled=["Job", "Name", "AI Score", "Hours left", "AI Rationale", "Resume", "Score arithmetic"],
                            hide_index=True,
                            use_container_width=True,
                        )
                        if st.button("↩️ Move selected candidates back to manager review", key="rescue_btn"):
                            chosen = redited.loc[redited["↩️ Move back to review"], "Sheet_Row"].tolist()
                            if not chosen:
                                st.info("Tick at least one candidate first.")
                            else:
                                with st.spinner("Updating Google Sheets..."):
                                    now_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                                    for row_idx in chosen:
                                        sheet.update_cell(int(row_idx), 1, STATUS_PENDING_REVIEW)
                                        sheet.update_cell(int(row_idx), 9, now_str)
                                st.success(f"✅ Moved {len(chosen)} candidate(s) back to the review board.")
                                st.session_state.pop("rescue_editor", None)  # drop stale cell edits before the rows change
                                time.sleep(1)
                                st.rerun()
                    else:
                        st.caption("No auto-rejected candidates are waiting right now.")
        else:
            st.success("🎉 No resumes need manager review right now.")

# ---------------------------------------------------------
# Tab 3: Email routing
# ---------------------------------------------------------
with tab3:
    st.header("📬 Automated Email Routing")

    if config.DEMO_MODE and st.session_state.get("demo_emails"):
        previews = st.session_state.demo_emails
        with st.expander(f"📧 Demo email previews: {len(previews)} email(s), NOT actually sent", expanded=True):
            for i, mail in enumerate(previews, start=1):
                st.markdown(f"**{i}. To:** {mail['to']}  \n**Subject:** {mail['subject']}")
                if "<br>" in mail["body"]:
                    st.markdown(mail["body"], unsafe_allow_html=True)
                else:
                    st.text(mail["body"])
                st.divider()
            if st.button("Clear previews", key="clear_demo_emails"):
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
                selected_job_tab3 = st.selectbox(
                    "🎯 Choose the job to view or send emails for:", [ALL_JOBS] + all_job_titles_tab3, key="filter_tab3"
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
                    time_info = " (could not parse application time)"
                    try:
                        applied_time = datetime.strptime(applied_at_str, "%Y-%m-%d %H:%M")
                        target_send_time = applied_time + timedelta(hours=REJECTION_DELAY_HOURS)
                        time_diff = target_send_time - current_time

                        if time_diff.total_seconds() <= 0:
                            time_info = f" 🔴 ({REJECTION_DELAY_HOURS} hours elapsed, will send immediately)"
                        else:
                            days = time_diff.days
                            hours = time_diff.seconds // 3600
                            if days > 0:
                                time_info = f" ⏳ ({days}d {hours}h until send)"
                            else:
                                time_info = f" ⏳ ({hours}h until send)"
                    except ValueError:
                        pass
                    names_reject.append(f"• {name} {time_info} [applied: {job_title}]")

                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "TRUE":
                    names_pending_invite.append(f"• {name} [applied: {job_title}]")

                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "FALSE" and manager_comment != "":
                    names_manager_reject.append(f"• {name} [applied: {job_title}]")

            col1, col2, col3 = st.columns(3)
            col1.metric("Queued: AI rejection emails", len(names_reject))
            col2.metric("Queued: manager-approved interview invites", len(names_pending_invite))
            col3.metric("Queued: manager rejection emails", len(names_manager_reject))

            st.write("")
            col1_list, col2_list, col3_list = st.columns(3)
            for column, names in ((col1_list, names_reject), (col2_list, names_pending_invite), (col3_list, names_manager_reject)):
                with column:
                    st.markdown("**📄 Candidates who will receive this email:**")
                    if names:
                        for n in names:
                            st.write(n)
                    else:
                        st.caption("Nobody is queued")

            st.divider()

            skip_wait = False
            if config.DEMO_MODE:
                skip_wait = st.checkbox(
                    f"Demo only: skip the {REJECTION_DELAY_HOURS}-hour wait so automatic rejection emails are sent now",
                    key="demo_skip_wait",
                )

            if st.button(
                f"⚡ Run the email state machine (send emails for \"{selected_job_tab3}\")",
                type="primary",
                use_container_width=True,
            ):
                if not config.DEMO_MODE and (not SENDER_EMAIL or GMAIL_APP_PASSWORD == "YOUR_16_DIGIT_PASSWORD"):
                    st.error("⚠️ Real email credentials are not configured, so no emails can be sent.")
                elif not config.BOOKING_APP_URL:
                    st.error("⚠️ BOOKING_APP_URL is not configured. Interview invites need the public booking-page URL.")
                else:
                    with st.status("📬 Email dispatch engine running...", expanded=True) as log_status:
                        try:
                            yag = DemoMailer() if config.DEMO_MODE else yagmail.SMTP(user=SENDER_EMAIL, password=GMAIL_APP_PASSWORD)
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
                                        st.write(f"⚡ [Auto rejection] Sending a thank-you email to {name}...")
                                        subject = f"[{COMPANY_NAME}] Update on your application for \"{job_title}\""
                                        body = (
                                            f"Dear {name},\n\n"
                                            f"Thank you for your interest in the \"{job_title}\" position at {COMPANY_NAME.rstrip('.')}. "
                                            "After an initial review, and given the large number of applicants, we found that your "
                                            "background is not a close enough match with the specific technical stack our team "
                                            "currently needs, so we are unable to move forward with an interview at this time. "
                                            "We wish you every success in your career and hope our paths cross again.\n\n"
                                            f"Sincerely,\nThe {COMPANY_NAME} Recruiting Team"
                                        )
                                        yag.send(to=email, subject=subject, contents=body)

                                        sheet.update_cell(idx, 1, STATUS_REJECTED)
                                        sheet.update_cell(idx, 9, current_time.strftime("%Y-%m-%d %H:%M"))
                                        trigger_count += 1
                                        time.sleep(1)

                                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "TRUE":
                                    st.write(f"🚀 [Manager approved] Sending an interview invitation to {name}...")
                                    subject = f"[Interview Invitation] {COMPANY_NAME} - \"{job_title}\" team interview"
                                    booking_url = f"{config.BOOKING_APP_URL}?{urlencode({'name': name, 'email': email})}"
                                    body = f"""Dear {name},<br><br>
Thank you for applying for the "{job_title}" position at {COMPANY_NAME.rstrip('.')}. After a careful review of your background, the hiring manager would like to invite you to the next stage: an online interview!<br><br>
To fit around the technical managers' schedules, we built a self-service booking page. It shows all of their open time slots for the next three weeks, synced in real time.<br><br>
👇 <b>Click the link below and choose one open slot to book your interview:</b><br>
<a href="{booking_url}" style="font-size: 16px; font-weight: bold; color: #1a73e8;">Open the interview booking page</a><br><br>
<span style="color: gray; font-size: 12px;">
💡 Tip: popular slots fill up quickly, so we recommend booking within 24 hours of receiving this email. Once you book, a confirmation email with the meeting details will be sent to your inbox right away. We look forward to meeting you!<br><br>
Sincerely,<br>The {COMPANY_NAME} Recruiting Team
</span>
"""
                                    yag.send(to=email, subject=subject, contents=body)
                                    sheet.update_cell(idx, 1, STATUS_INVITED_NO_REPLY)
                                    sheet.update_cell(idx, 9, current_time.strftime("%Y-%m-%d %H:%M"))
                                    trigger_count += 1
                                    time.sleep(1)

                                elif status == STATUS_PENDING_REVIEW and manager_approve_str == "FALSE" and manager_comment != "":
                                    st.write(f"📝 [Manager declined] Sending a thank-you email to {name}...")
                                    subject = f"[{COMPANY_NAME}] Update on your application for \"{job_title}\""
                                    body = (
                                        f"Dear {name},\n\n"
                                        f"Thank you for applying for the \"{job_title}\" position at {COMPANY_NAME.rstrip('.')}. "
                                        "Your résumé is strong; however, after review by the hiring manager, we felt your fit with "
                                        "our current technical stack or salary expectations is not quite aligned with this role, "
                                        "so we are unable to arrange an interview this time. Thank you again for applying, and we "
                                        "hope to work with you in the future!\n\n"
                                        f"Sincerely,\nThe {COMPANY_NAME} Recruiting Team"
                                    )
                                    yag.send(to=email, subject=subject, contents=body)

                                    sheet.update_cell(idx, 1, STATUS_REJECTED)
                                    sheet.update_cell(idx, 9, current_time.strftime("%Y-%m-%d %H:%M"))
                                    trigger_count += 1
                                    time.sleep(1)

                            log_status.update(
                                label=f"🎉 Done! {trigger_count} email(s) sent.", state="complete", expanded=True
                            )
                            time.sleep(1)
                            st.rerun()
                        except Exception as e:
                            st.error(f"❌ The email dispatch engine crashed: {e}")
        else:
            st.info("There is no data to send emails for yet.")

# ---------------------------------------------------------
# Tab 4: Analytics dashboard
# ---------------------------------------------------------
with tab4:
    st.header("📊 Applicant Analytics and Hiring Decision Report")
    if config.DEMO_MODE:
        st.caption(
            "Demo data: about 40 fictional sample candidates plus the candidates visitors uploaded in the last 24 hours. "
            "Unlike the review and routing tabs, this report is not limited to your own candidates."
        )

    col_dash_load, col_dash_empty = st.columns([1, 3])
    with col_dash_load:
        if st.button("🔄 Sync cloud data and load analytics", type="primary"):
            with st.spinner("Fetching the latest data from Google Sheets..."):
                if sheet:
                    raw_vals = sheet.get_all_values()
                    if len(raw_vals) > 1:
                        width = max(len(r) for r in raw_vals)
                        header = raw_vals[0] + [f"extra_{i}" for i in range(len(raw_vals[0]), width)]
                        body = [r + [""] * (width - len(r)) for r in raw_vals[1:]]
                        st.session_state.df_dash = pd.DataFrame(body, columns=header)
                        st.success("✅ Data synced.")
                    else:
                        st.warning("There is no recruiting data in the sheet yet.")
                else:
                    st.error("🛑 Sheet is not connected.")

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
            c_f1, c_f2 = st.columns(2)
            with c_f1:
                dash_job_list = [ALL] + list(df_dash["job_applied"].dropna().unique())
                sel_dash_job = st.selectbox("🎯 Filter by job", dash_job_list)
            with c_f2:
                sel_dash_time = st.radio("⏰ Time window", [ALL, "Last week", "Last 3 weeks"], horizontal=True)

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
                ck1.metric("👥 Total applicants", f"{t_app}")
                ck2.metric(
                    "✉️ Reached interview stage",
                    f"{t_inv}",
                    f"Overall conversion: {int(t_inv / t_app * 100) if t_app > 0 else 0}%",
                )
                ck3.metric("🎉 Hired", f"{t_hire}", f"Hire rate: {int(t_hire / t_app * 100) if t_app > 0 else 0}%")

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
                    fig_exp.add_trace(
                        go.Bar(x=exp_df.index, y=exp_df["count"], name="Applicants", marker_color=UI_THEME["PRIMARY"]),
                        secondary_y=False,
                    )
                    fig_exp.add_trace(
                        go.Scatter(
                            x=exp_df.index,
                            y=exp_df["rate"],
                            name="Interview conversion (%)",
                            mode="lines+markers",
                            marker=dict(size=10, color=UI_THEME["WARNING"]),
                            line=dict(width=3),
                        ),
                        secondary_y=True,
                    )

                    fig_exp.update_layout(
                        title="<b>Applicants and interview conversion by experience</b>",
                        plot_bgcolor="rgba(0,0,0,0)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
                    fig_exp.update_yaxes(title_text="Applicants", secondary_y=False)
                    fig_exp.update_yaxes(
                        title_text="Conversion (%)", secondary_y=True, range=[0, max(100, exp_df["rate"].max() + 10)]
                    )
                    st.plotly_chart(fig_exp, use_container_width=True)

                with col_c2:
                    # Rejection reasons (donut)
                    df_rej = df_dash[~df_dash["status"].isin(INVITED_STATUSES)].copy()

                    # Keyword classifier over free text. English keywords, because the AI is told to
                    # write score_reason in English. Salary phrases are specific ("over budget") on purpose:
                    # a bare "salary" would also match "salary within budget".
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

                    if not df_rej.empty:
                        df_rej["fail_reason"] = df_rej.apply(categorize_reason, axis=1)
                        reason_counts = df_rej["fail_reason"].value_counts()

                        fig_reason = go.Figure(
                            data=[
                                go.Pie(
                                    labels=reason_counts.index,
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
                        fig_reason.update_layout(title="<b>Why candidates drop out of the funnel</b>", showlegend=False)
                        st.plotly_chart(fig_reason, use_container_width=True)
                    else:
                        st.info("No rejection data to analyze yet.")

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
                            hovertemplate="<b>%{text}</b><br>AI score: %{x}<br>Expected salary: %{y:,.0f}<extra></extra>",
                        )
                    )
                    fig_scatter.update_layout(
                        title="<b>AI score vs. expected salary</b>",
                        plot_bgcolor="rgba(0,0,0,0)",
                        xaxis_title="Overall AI score (0-100)",
                        yaxis_title="Expected monthly salary (TWD)",
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
                            name="Candidates with skill",
                            marker_color=UI_THEME["SKILL_BAR_COLOR"],
                        ),
                        secondary_y=False,
                    )
                    fig_skills.add_trace(
                        go.Scatter(
                            x=top_skills["skills_extracted"],
                            y=top_skills["AvgSalary"],
                            name="Avg. expected salary",
                            mode="lines+markers",
                            marker=dict(size=10, color=UI_THEME["WARNING"]),
                            line=dict(width=3),
                        ),
                        secondary_y=True,
                    )

                    fig_skills.update_layout(
                        title="<b>Core skill price analysis</b>",
                        plot_bgcolor="rgba(0,0,0,0)",
                        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
                    )
                    fig_skills.update_yaxes(title_text="Candidates with this skill", secondary_y=False)
                    fig_skills.update_yaxes(title_text="Avg. expected salary of this group (TWD)", secondary_y=True)
                    st.plotly_chart(fig_skills, use_container_width=True)

            else:
                st.warning("No applicants match the current filters.")
    else:
        st.info("👈 Click \"Sync cloud data and load analytics\" at the top left to load the latest recruiting report.")
