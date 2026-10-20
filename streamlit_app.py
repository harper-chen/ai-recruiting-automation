"""
Interview Scheduling System - candidate-facing booking page (Streamlit)

Features: URL-prefilled candidate form (?name=...&email=...), live Google Calendar free/busy
lookup for the interviewers, automatic Google Meet creation, double-booking protection,
Google Sheets status sync, and a confirmation email via yagmail.
"""

import ast
import json
import os
from datetime import datetime, timedelta, timezone

import gspread
import streamlit as st
import yagmail
from google.oauth2.service_account import Credentials

import config
from calendar_busytime import fetch_and_save_calendar_data
from calendar_sync import create_interview_event
from config import COMPANY_NAME, STATUS_SCHEDULED, WEEKDAYS

# ========== Page setup ==========
st.set_page_config(page_title="Interview Scheduling", layout="wide")

st.markdown(
    """
    <style>
    body, p, div, button, label { font-size: 1.2rem !important; }
    .stButton > button { font-size: 1.2rem !important; padding: 10px !important; min-height: 2.5rem !important; }
    .stMarkdown h3 { font-size: 1.3rem !important; }
    </style>
""",
    unsafe_allow_html=True,
)

st.title("👔 Interview Scheduling")

if config.DEMO_MODE:
    st.warning(
        "🧪 **Demo mode.** The interviewers' calendars are simulated, no real Google Calendar event is created, "
        "and no email is sent. Use a candidate name and email that exist in the demo sheet."
    )

WORK_SESSIONS = [(9, 12), (13, 18)]  # working hours, 24h clock
BUFFER = timedelta(minutes=15)
INTERVIEW_LEN = timedelta(hours=1)


# ==========================================
# Live calendar lookup (cached for 5 minutes to stay under API limits)
# ==========================================
@st.cache_data(ttl=300)
def get_live_calendar_data():
    try:
        return fetch_and_save_calendar_data(config.get_interviewer_emails())
    except Exception as e:
        st.error(f"❌ Could not load the interviewers' calendars. Please try again later. ({e})")
        st.stop()


with st.spinner("🔄 Syncing the interviewers' latest calendars..."):
    data = get_live_calendar_data()

emails = list(data.keys())
if len(emails) < 2:
    st.error("❌ Not enough calendar data (at least 2 interviewers are required).")
    st.stop()

# Pool every interviewer's busy blocks so an interview only lands where all of them are free
busy_times_global = [(item["start"], item["end"]) for person in data.values() for item in person]

script_dir = os.path.dirname(os.path.abspath(__file__))
booking_file = os.path.join(script_dir, "bookings.json")


class DemoMailer:
    """Stand-in for yagmail.SMTP in demo mode: previews the email instead of sending it."""

    def send(self, to, subject, contents):
        st.session_state.demo_email_preview = (to, subject, contents)


def load_bookings():
    if os.path.exists(booking_file):
        try:
            with open(booking_file, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def save_bookings(bookings):
    with open(booking_file, "w", encoding="utf-8") as f:
        json.dump(bookings, f, indent=4, ensure_ascii=False)
        print("[system log] bookings.json updated.")


if "booked_global" not in st.session_state:
    st.session_state.booked_global = load_bookings()
if "confirmed" not in st.session_state:
    st.session_state.confirmed = False
if "confirmed_info" not in st.session_state:
    st.session_state.confirmed_info = None
if "selected_slot" not in st.session_state:
    st.session_state.selected_slot = None
if "meet_link" not in st.session_state:
    st.session_state.meet_link = None


def is_busy(check_start, check_end, busy_list):
    for busy_start_str, busy_end_str in busy_list:
        busy_start = datetime.fromisoformat(busy_start_str)
        busy_end = datetime.fromisoformat(busy_end_str)
        if not (check_end <= busy_start or check_start >= busy_end):
            return True
    return False


def is_slot_available(date_str, time_str, booked_dict):
    time_parts = time_str.split("-")
    check_start = datetime.strptime(f"{date_str} {time_parts[0]}", "%Y-%m-%d %H:%M")
    check_end = datetime.strptime(f"{date_str} {time_parts[1]}", "%Y-%m-%d %H:%M")
    check_start_with_buffer = check_start - BUFFER
    check_end_with_buffer = check_end + BUFFER

    for key in booked_dict.keys():
        # Keys are stored as str((date, time_range)); literal_eval parses them safely
        booked_date, booked_time = ast.literal_eval(key)
        if booked_date == date_str:
            booked_parts = booked_time.split("-")
            booked_start = datetime.strptime(f"{booked_date} {booked_parts[0]}", "%Y-%m-%d %H:%M")
            booked_end = datetime.strptime(f"{booked_date} {booked_parts[1]}", "%Y-%m-%d %H:%M")
            booked_start_with_buffer = booked_start - BUFFER
            booked_end_with_buffer = booked_end + BUFFER
            if not (check_end_with_buffer <= booked_start_with_buffer or check_start_with_buffer >= booked_end_with_buffer):
                return False
    return True


# ==========================================
# Generate open slots for the next 21 days, starting tomorrow
# ==========================================
local_tz = timezone(timedelta(hours=config.TIMEZONE_OFFSET_HOURS))
tomorrow = (datetime.now(local_tz) + timedelta(days=1)).date()
all_dates = [tomorrow + timedelta(days=i) for i in range(21)]

available = {}
for date in all_dates:
    if date.weekday() < 5:
        for work_start, work_end in WORK_SESSIONS:
            ws = datetime.combine(date, datetime.min.time().replace(hour=work_start))
            we = datetime.combine(date, datetime.min.time().replace(hour=work_end))
            scan_start = ws + BUFFER
            scan_end = we - INTERVIEW_LEN - BUFFER
            t = scan_start

            while t <= scan_end:
                check_start = t - BUFFER
                check_end = t + INTERVIEW_LEN + BUFFER
                if not is_busy(check_start, check_end, busy_times_global):
                    date_str = date.strftime("%Y-%m-%d")
                    time_str = t.strftime("%H:%M")
                    end_str = (t + INTERVIEW_LEN).strftime("%H:%M")
                    available[(date_str, f"{time_str}-{end_str}")] = True
                t += timedelta(minutes=15)

available_slots = []
for date_str, time_range in available.keys():
    time_str = time_range.split("-")[0]
    minute = int(time_str.split(":")[1])
    if minute % 30 == 0:  # offer slots on the hour and half hour only
        if time_range not in [str(v) for v in st.session_state.booked_global.values()] and is_slot_available(
            date_str, time_range, st.session_state.booked_global
        ):
            slot_key = str((date_str, time_range))
            if slot_key not in st.session_state.booked_global:
                date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
                day_name = WEEKDAYS[date_obj.weekday()]
                available_slots.append(
                    {"display": f"{date_str} ({day_name}) {time_range}", "date": date_str, "time": time_range}
                )

if st.session_state.confirmed:
    # ========== Booking confirmed ==========
    date_str, time_range = st.session_state.confirmed_info
    date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
    day_name = WEEKDAYS[date_obj.weekday()]

    st.success("✅ Your interview is booked!")
    st.info(f"📅 Time: {date_str} ({day_name}) {time_range}")
    if st.session_state.meet_link:
        st.info(f"🔗 Google Meet room: {st.session_state.meet_link}")
    st.info("A confirmation email and calendar invitation have been sent to your inbox. To change the time, please contact the recruiting team.")
    st.balloons()
    if config.DEMO_MODE and st.session_state.get("demo_email_preview"):
        to, subject, contents = st.session_state.demo_email_preview
        with st.expander(f"📧 Demo preview (not sent) - {subject}", expanded=True):
            st.caption(f"To: {to}")
            st.text(contents)

else:
    # ========== Slot selection ==========
    st.markdown("### 📝 Your details")

    default_name = st.query_params.get("name", "")
    default_email = st.query_params.get("email", "")

    col1, col2 = st.columns(2)
    with col1:
        applicant_name = st.text_input(
            "Your full name (must match your résumé)", value=default_name, placeholder="e.g. Alex Taylor"
        )
    with col2:
        applicant_email = st.text_input(
            "Your email address (must match your résumé)", value=default_email, placeholder="e.g. name@example.com"
        )

    st.divider()
    st.markdown("### 📅 Choose an interview slot that works for you")
    st.caption(f"All times are {config.TIMEZONE_NAME} (GMT+{config.TIMEZONE_OFFSET_HOURS}).")

    if available_slots:
        by_date = {}
        for slot in available_slots:
            by_date.setdefault(slot["date"], []).append(slot)

        for date_str in sorted(by_date.keys()):
            date_obj = datetime.strptime(date_str, "%Y-%m-%d").date()
            day_name = WEEKDAYS[date_obj.weekday()]
            st.markdown(f"**📅 {date_str} ({day_name})**")
            slots = by_date[date_str]
            cols = st.columns(4)

            for idx, slot in enumerate(slots):
                with cols[idx % 4]:
                    is_selected = st.session_state.selected_slot == (slot["date"], slot["time"])
                    if is_selected:
                        st.markdown(
                            f"""
                            <div style="background-color: #FFD700; padding: 12px; border-radius: 8px; text-align: center; font-weight: bold; color: #333;">
                                ✨ {slot['time']}
                            </div>
                        """,
                            unsafe_allow_html=True,
                        )
                    else:
                        if st.button(f"⏰ {slot['time']}", key=f"slot_{slot['date']}_{slot['time']}", use_container_width=True):
                            st.session_state.selected_slot = (slot["date"], slot["time"])
                            st.rerun()
            st.write("")

        # ========== Confirm button ==========
        if st.session_state.selected_slot:
            st.divider()
            if st.button("Confirm booking", use_container_width=True, key="confirm_final"):

                if not applicant_name.strip() or not applicant_email.strip():
                    st.warning("⚠️ Enter your name and email at the top of the page before confirming.")
                else:
                    date_str, time_range = st.session_state.selected_slot
                    start_time, end_time = time_range.split("-")

                    st.info("🔄 Verifying your applicant record...")

                    try:
                        print("[system log] Connecting to Google Sheets to verify the applicant...")
                        scope = ["https://www.googleapis.com/auth/spreadsheets", "https://www.googleapis.com/auth/drive"]

                        if "gcp_service_account" in st.secrets:
                            creds_info = json.loads(st.secrets["gcp_service_account"])
                            base_creds = Credentials.from_service_account_info(creds_info)
                        else:
                            base_creds = Credentials.from_service_account_file("creds.json")

                        scoped_creds = base_creds.with_scopes(scope)
                        gs_client = gspread.authorize(scoped_creds)
                        sheet = gs_client.open(config.SHEET_NAME).sheet1

                        all_rows = sheet.get_all_values()
                        target_row_index = None

                        for idx, row in enumerate(all_rows, start=1):
                            if len(row) >= 4 and row[2].strip() == applicant_name.strip() and row[3].strip() == applicant_email.strip():
                                target_row_index = idx
                                break

                        if not target_row_index:
                            st.error(
                                "🛑 Verification failed. No application matches this name and email. Check for typos, "
                                "and make sure you have passed the initial screening and received an interview invitation."
                            )
                        else:
                            check_start_final = datetime.strptime(f"{date_str} {start_time}", "%Y-%m-%d %H:%M") - BUFFER
                            check_end_final = datetime.strptime(f"{date_str} {end_time}", "%Y-%m-%d %H:%M") + BUFFER

                            if not is_slot_available(date_str, time_range, st.session_state.booked_global):
                                st.error("🛑 Booking failed: another candidate just took this slot. Please choose a different one.")
                            elif is_busy(check_start_final, check_end_final, busy_times_global):
                                st.error("🛑 Booking failed: an interviewer's schedule just changed for this slot. Please choose a different one.")
                            else:
                                st.success("🎉 Verified, and the slot is still free. Creating your Google Calendar event...")

                                event_id, google_meet_url = create_interview_event(
                                    applicant_name, applicant_email, date_str, start_time, end_time
                                )

                                if event_id:
                                    slot_key = str((date_str, time_range))
                                    st.session_state.booked_global[slot_key] = applicant_name
                                    save_bookings(st.session_state.booked_global)

                                    sheet.update_cell(target_row_index, 1, STATUS_SCHEDULED)
                                    current_time_str = datetime.now().strftime("%Y-%m-%d %H:%M")
                                    sheet.update_cell(target_row_index, 9, current_time_str)
                                    sheet.update_cell(target_row_index, 10, f"{date_str} {start_time}")
                                    sheet.update_cell(target_row_index, 11, google_meet_url)

                                    st.info("✉️ Sending your confirmation email...")

                                    try:
                                        if config.DEMO_MODE:
                                            yag = DemoMailer()
                                        else:
                                            sender_email = st.secrets["SENDER_EMAIL"]
                                            gmail_app_password = st.secrets["GMAIL_APP_PASSWORD"]
                                            yag = yagmail.SMTP(user=sender_email, password=gmail_app_password)

                                        subject = f"[Interview Confirmed] {COMPANY_NAME} - technical interview booked"
                                        body = f"""Dear {applicant_name},

You have successfully booked a technical interview with {COMPANY_NAME.rstrip('.')}. Your interview details are below. Please arrive on time.

📅 Date: {date_str}
⏰ Time: {start_time} - {end_time} ({config.TIMEZONE_NAME}, GMT+{config.TIMEZONE_OFFSET_HOURS})
💻 Online meeting link (Google Meet): {google_meet_url}

💡 Tips:
1. On the day, click the Google Meet link above to join. The hiring manager will meet you online.
2. We suggest testing your microphone, camera and network connection 5 minutes before the interview.

Good luck!

Sincerely,
The {COMPANY_NAME} Recruiting Team
"""
                                        yag.send(to=applicant_email, subject=subject, contents=body)
                                    except Exception as email_err:
                                        st.warning(f"Your interview is booked, but the confirmation email failed to send: {email_err}")

                                    st.session_state.confirmed = True
                                    st.session_state.confirmed_info = (date_str, time_range)
                                    st.session_state.meet_link = google_meet_url
                                    st.rerun()
                                else:
                                    st.error("Something unexpected happened. Please try again later.")
                    except Exception as e:
                        st.error(f"Something unexpected happened. Please try again later: {e}")

    else:
        st.info(
            "The interview schedule is very full right now and every slot is taken. "
            f"Please call the HR team to book an interview: {config.CONTACT_PHONE}"
        )
