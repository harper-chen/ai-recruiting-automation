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
import json
import os
import re
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
from config import (
    COMPANY_NAME,
    INVITED_STATUSES,
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
        "🧪 **Demo mode.** No real emails are sent, no calendar events are created, and resumes are not "
        "stored in Drive. Emails are shown on screen instead. Data lives in a shared demo sheet that anyone can edit."
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


def load_job_profiles():
    if os.path.exists(PROFILE_FILE):
        try:
            with open(PROFILE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def save_job_profiles(profiles_dict):
    with open(PROFILE_FILE, "w", encoding="utf-8") as f:
        json.dump(profiles_dict, f, ensure_ascii=False, indent=4)


# ==================== Core functions ====================
MODEL_NAME = "gemini-2.5-flash"
MAX_AI_ATTEMPTS = 3


def generate_with_retry(prompt):
    """Call Gemini, retrying transient failures (e.g. 503 UNAVAILABLE) with backoff."""
    last_error = None
    for attempt in range(MAX_AI_ATTEMPTS):
        try:
            return client.models.generate_content(model=MODEL_NAME, contents=prompt)
        except Exception as e:  # noqa: BLE001 - SDK raises several error types
            last_error = e
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

    [CONTENT REQUIREMENTS]:
    1. Include explicit point deductions for missing core requirements (specific tools, education, years of experience).
    2. Include explicit point bonuses for advanced skills or distinctive experience.
    3. If the JD states a salary range, add a deduction rule for expected salaries above it.
    4. Keep this system rule as the final line: "• Email extraction: extract the real email address exactly as written; never alter the spelling."

    [JOB DESCRIPTION (JD)]:
    {jd_text}
    """
    try:
        response = generate_with_retry(prompt)
        return response.text.strip().replace("**", "")
    except Exception as e:
        return f"⚠️ Rule generation failed: {e}"


UPLOAD_FAILED_URL = "https://drive.google.com/resume/upload_failed"


def upload_file_and_get_link(file_path, file_name):
    """Upload through a Google Apps Script web app (see apps_script/Code.gs).

    Service accounts have no Drive storage quota of their own, so the upload is proxied
    through a script that runs as a normal Google user and returns a shareable link.
    """
    if config.DEMO_MODE:
        return "(not stored in demo mode)"

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


def score_resume(file_name, resume_text, jd_text, rules_text, target_job_title):
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
    1. The baseline score is 70 (not 100).
    2. Check the resume against every deduction and bonus rule above and do the real arithmetic.
    3. If the candidate's [expected salary] is clearly above the JD budget or an unreasonable range, you must
       apply a deduction. Never give a perfect score in that case.
    4. In the "score_calculation" field, write out the explicit add/subtract arithmetic first, and only then give
       the final "score" (maximum 100).

    Reply with the following JSON only, with no Markdown tags:
    {{
        "status": "decided by score: below {SCORE_THRESHOLD} use '{STATUS_PENDING_REJECTION}', {SCORE_THRESHOLD} or above use '{STATUS_PENDING_REVIEW}'",
        "job_applied": "{target_job_title}",
        "name": "candidate's full name",
        "email": "email address extracted from the resume",
        "score_calculation": "the arithmetic, e.g. baseline 70 + Python proficiency 10 - salary over budget 15 = 65",
        "score": numeric score,
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
    try:
        response = generate_with_retry(prompt)
        return response.text, True
    except Exception as e:
        return str(e), False


def write_to_google_sheet(data_dict, real_resume_url):
    if sheet is None:
        return "🛑 Sheet is not connected."
    try:
        target_job = data_dict.get("job_applied", "").strip()
        target_email = data_dict.get("email", "").strip()
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
        ]

        sheet.update(f"A{next_row}:E{next_row}", [front_part])
        sheet.update(f"H{next_row}:Q{next_row}", [back_part])
        return f"🚀 {candidate_name} written. Job: [{target_job}] / Status: [{final_status}]"

    except Exception as e:
        return f"❌ Fatal error while writing to the sheet: {e}"


class DemoMailer:
    """Stand-in for yagmail.SMTP in demo mode: previews the email instead of sending it."""

    def send(self, to, subject, contents):
        with st.expander(f"📧 Demo preview (not sent) - {subject}"):
            st.caption(f"To: {to}")
            st.markdown(contents.replace("\n", "  \n"), unsafe_allow_html=True)


def pad_row(row):
    """Sheets drops trailing empty cells; pad to the 17 columns this app expects."""
    return row + [""] * (17 - len(row))


# ==================== Streamlit UI ====================

st.title("👔 HRIS Recruiting Command Center")
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
            st.caption("Type your own rules, or use the button on the left to have AI draft them in a clean format.")
            st.text_area("Enter your evaluation and deduction logic:", key="custom_rules", height=415)

        st.write("")
        if st.button("💾 Save this job profile (JD + scoring rules)", type="secondary", use_container_width=True):
            job_name = st.session_state.current_job_title.strip()
            if not job_name:
                st.error("⚠️ Enter a job title above before saving.")
            else:
                job_profiles[job_name] = {
                    "jd": st.session_state.current_jd,
                    "rules": st.session_state.custom_rules,
                }
                save_job_profiles(job_profiles)
                st.success(f"✅ Saved the JD and scoring rules as the profile \"{job_name}\". Load it from the dropdown next time.")

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
                            match = re.search(r"\{[\s\S]*\}", result_json)
                            clean_json = match.group(0) if match else result_json

                            data_dict = json.loads(clean_json)
                            msg = write_to_google_sheet(data_dict, real_url)

                            if "❌" in msg or "🛑" in msg:
                                st.warning(msg)
                            else:
                                st.success(msg)

                        except json.JSONDecodeError:
                            st.error(f"❌ The AI response for {file.name} was malformed.")
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
        f"💡 **How review works**\n\nResumes the AI scored {SCORE_THRESHOLD} or higher are reviewed by the hiring manager. "
        "Tick the box to invite a candidate to interview. If the candidate is not a fit, leave the box unticked and "
        "**always write the reason in the comment column**, so HR can analyze the funnel and adjust the hiring strategy."
    )

    if sheet:
        all_rows = sheet.get_all_values()
        if len(all_rows) > 1:
            all_job_titles = sorted(
                {row[1].strip() for row in all_rows[1:] if len(row) > 1 and row[1].strip() != ""}
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
                                "✅ Approve Interview": row[5].strip().upper() == "TRUE",
                                "📝 Manager Comment": row[6],
                                "Resume": row[16],
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
                        },
                        disabled=["Job", "Name", "AI Score", "AI Rationale", "Resume"],
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
        else:
            st.success("🎉 No resumes need manager review right now.")

# ---------------------------------------------------------
# Tab 3: Email routing
# ---------------------------------------------------------
with tab3:
    st.header("📬 Automated Email Routing")

    if sheet:
        all_rows = sheet.get_all_values()

        if len(all_rows) > 1:
            all_job_titles_tab3 = sorted(
                {row[1].strip() for row in all_rows[1:] if len(row) > 1 and row[1].strip() != ""}
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
                status = row[0].strip()
                job_title = row[1].strip()
                name = row[2].strip()
                score = int(row[4]) if row[4].isdigit() else 0
                manager_approve_str = row[5].strip().upper()
                manager_comment = row[6].strip()
                applied_at_str = row[7].strip()

                if selected_job_tab3 != ALL_JOBS and job_title != selected_job_tab3:
                    continue

                if status == STATUS_PENDING_REJECTION and score < SCORE_THRESHOLD:
                    time_info = " (could not parse application time)"
                    try:
                        applied_time = datetime.strptime(applied_at_str, "%Y-%m-%d %H:%M")
                        target_send_time = applied_time + timedelta(days=2)
                        time_diff = target_send_time - current_time

                        if time_diff.total_seconds() <= 0:
                            time_info = " 🔴 (48 hours elapsed, will send immediately)"
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

                elif status == STATUS_PENDING_REVIEW and score >= SCORE_THRESHOLD and manager_approve_str == "TRUE":
                    names_pending_invite.append(f"• {name} [applied: {job_title}]")

                elif (
                    status == STATUS_PENDING_REVIEW
                    and score >= SCORE_THRESHOLD
                    and manager_approve_str == "FALSE"
                    and manager_comment != ""
                ):
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

                            for idx, row in enumerate(all_rows[1:], start=2):
                                row = pad_row(row)
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

                                if score < SCORE_THRESHOLD:
                                    try:
                                        applied_time = datetime.strptime(applied_at_str, "%Y-%m-%d %H:%M")
                                        is_past_two_days = current_time >= (applied_time + timedelta(days=2))
                                    except ValueError:
                                        is_past_two_days = False

                                    if is_past_two_days:
                                        st.write(f"⚡ [Auto rejection] Sending a thank-you email to {name}...")
                                        subject = f"[{COMPANY_NAME}] Update on your application for \"{job_title}\""
                                        body = (
                                            f"Dear {name},\n\n"
                                            f"Thank you for your interest in the \"{job_title}\" position at {COMPANY_NAME}. "
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

                                elif score >= SCORE_THRESHOLD and manager_approve_str == "TRUE":
                                    st.write(f"🚀 [Manager approved] Sending an interview invitation to {name}...")
                                    subject = f"[Interview Invitation] {COMPANY_NAME} - \"{job_title}\" team interview"
                                    booking_url = f"{config.BOOKING_APP_URL}?{urlencode({'name': name, 'email': email})}"
                                    body = f"""Dear {name},<br><br>
Thank you for applying for the "{job_title}" position at {COMPANY_NAME}. After a careful review of your background, the hiring manager would like to invite you to the next stage: an online interview!<br><br>
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

                                elif score >= SCORE_THRESHOLD and manager_approve_str == "FALSE" and manager_comment != "":
                                    st.write(f"📝 [Manager declined] Sending a thank-you email to {name}...")
                                    subject = f"[{COMPANY_NAME}] Update on your application for \"{job_title}\""
                                    body = (
                                        f"Dear {name},\n\n"
                                        f"Thank you for applying for the \"{job_title}\" position at {COMPANY_NAME}. "
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

    col_dash_load, col_dash_empty = st.columns([1, 3])
    with col_dash_load:
        if st.button("🔄 Sync cloud data and load analytics", type="primary"):
            with st.spinner("Fetching the latest data from Google Sheets..."):
                if sheet:
                    raw_vals = sheet.get_all_values()
                    if len(raw_vals) > 1:
                        st.session_state.df_dash = pd.DataFrame(raw_vals[1:], columns=raw_vals[0])
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
