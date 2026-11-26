"""
English / Traditional Chinese (Taiwan) text for both apps (HR command center and booking page).

English is always the default. Chinese is shown only when the URL has ?lang=zh. The choice is never stored
anywhere else, so a link without ?lang=zh always opens in English.

What changes with the language:
  * interface text, chart labels, email templates
  * the sample job (JD + rules) and the sample resumes offered in demo mode
  * the language the AI writes in (score reasons, drafted rules, analytics report)

What does not change: the status values stored in the Google Sheet (they are internal codes) and the
scoring arithmetic.
"""

import streamlit as st

LANGS = ("en", "zh")


# =========================================================
# Language selection
# =========================================================

def get_lang():
    try:
        value = st.query_params.get("lang", "en")
    except Exception:  # noqa: BLE001
        return "en"
    return "zh" if value == "zh" else "en"


def is_zh():
    return get_lang() == "zh"


def render_language_switch():
    """Language switch that keeps ?lang=zh in the URL in step with the UI (other URL parameters are kept)."""
    lang = get_lang()
    _, switch_col = st.columns([5, 1])
    with switch_col:
        choice = st.radio(
            "Language / 語言",
            list(LANGS),
            index=LANGS.index(lang),
            format_func=lambda code: "English" if code == "en" else "繁體中文",
            horizontal=True,
            label_visibility="collapsed",
        )
    if choice != lang:
        if choice == "zh":
            st.query_params["lang"] = "zh"
        else:
            try:
                del st.query_params["lang"]
            except KeyError:
                pass
        st.rerun()


def with_lang(url):
    """Add lang=zh to a link (booking page, HR app) when the interface is in Chinese."""
    if not url or not is_zh():
        return url
    separator = "&" if "?" in url else "?"
    return f"{url}{separator}lang=zh"


# =========================================================
# Lookup
# =========================================================

def t(key, **kwargs):
    table = TEXT[get_lang()]
    text = table.get(key)
    if text is None:
        text = TEXT["en"][key]
    return text.format(**kwargs) if kwargs else text


def tn(key, count, **kwargs):
    """English uses key_one / key_other; Chinese has no plural, so key_other is enough there."""
    table = TEXT[get_lang()]
    suffix = "_one" if count == 1 else "_other"
    text = table.get(key + suffix) or table.get(key + "_other")
    if text is None:
        text = TEXT["en"][key + suffix]
    return text.format(count=count, **kwargs)


# Labels for codes produced by insights.py / the analytics code (the codes stay English).
REASON_ZH = {
    "Manager: salary/budget mismatch": "主管：薪資與預算不符",
    "Manager: other mismatch": "主管：其他不符",
    "AI: salary expectation too high": "AI：期待薪資過高",
    "AI: insufficient skills/experience": "AI：技能或經驗不足",
    "AI: other mismatch": "AI：其他不符",
}

BAND_ZH = {"0-2 yrs": "0–2 年", "3-5 yrs": "3–5 年", "5+ yrs": "5 年以上", "Unknown": "未知"}


def reason_label(code):
    return REASON_ZH.get(code, code) if is_zh() else code


def band_label(code):
    return BAND_ZH.get(code, code) if is_zh() else code


# The status values stored in the sheet stay English; this is only how they are shown.
STATUS_ZH = {
    "AI Scored - Pending Rejection Email": "AI 已評分－待寄婉拒信",
    "AI Scored - Pending Manager Review": "AI 已評分－待主管審核",
    "Rejection Email Sent": "已寄出婉拒信",
    "Interview Invite Sent - No Reply": "已寄出面試邀請－尚未回覆",
    "Interview Invite Sent": "已寄出面試邀請",
    "Interview Scheduled": "已安排面試",
    "Hired": "已錄取",
    "Offer Declined": "婉拒錄取",
}


def status_label(code):
    return STATUS_ZH.get(code, code) if is_zh() else code


WEEKDAYS_ZH = ["週一", "週二", "週三", "週四", "週五", "週六", "週日"]


def weekday(index, english_names):
    return WEEKDAYS_ZH[index] if is_zh() else english_names[index]


# =========================================================
# Interface text
# =========================================================

TEXT = {
    "en": {
        # ---------- HR app: shell ----------
        "page_title": "HRIS Recruiting Command Center",
        "title": "👔 HRIS Recruiting Command Center",
        "demo_banner": (
            "🧪 **Demo mode.** No real emails are sent, no calendar events are created, and your uploads are not "
            "stored in Drive. Emails are shown on screen instead. Saved job profiles live only in your browser session. "
            "Candidates you upload are tagged with your private code (for example ariel.lin+k3f9@example.com), so other "
            "visitors do not see them, and they are deleted after 24 hours. The Analytics tab also includes about 40 "
            "fictional sample candidates."
        ),
        "missing_secret": "❌ Missing required secret: {error}. Check .streamlit/secrets.toml (see secrets.example.toml).",
        "sheets_failed": "❌ Could not connect to Google Sheets: {error}",
        "ai_init_failed": "❌ AI client initialization failed: {error}",
        "cleanup_skipped": "Demo cleanup skipped: {error}",
        "all_jobs": "All jobs",
        "new_job": "➕ Add a new job manually",
        "quota_used_up": (
            "Today's free AI quota for this demo is used up (the free Gemini tier allows only a small number of "
            "requests per day), so new AI requests will work again tomorrow."
        ),
        "quota_samples_ok": (
            " The built-in sample resumes with the default scoring rules still work at any time, because their "
            "results are saved."
        ),
        "quota_screenshots": " You can also see every step in the screenshots on GitHub: {url}#how-it-works",
        "rules_failed": "⚠️ Rule generation failed: {error}",
        "report_unreadable": "⚠️ The AI answer could not be read as a report. Please press the button again.",
        "report_failed": "⚠️ Could not generate the report: {error}",
        "m_applicants": "Applicants",
        "m_reached_interview": "Reached interview",
        "m_people": "{count} people",
        "m_hired": "Hired",
        "m_of_applicants": "{pct}% of applicants",
        "m_median_score": "Median score",
        "report_summary": "📌 **Summary**  \n{text}",
        "report_findings": "#### 🔍 Key findings",
        "report_recommendations": "#### 💡 Recommendations",
        "no_apps_script": "APPS_SCRIPT_URL is not configured, so the resume file will not be stored in Drive.",
        "proxy_error": "❌ Upload proxy returned an error: {error}",
        "proxy_failed": "❌ Upload via proxy failed: {error}",
        "sheet_not_connected": "🛑 Sheet is not connected.",
        "duplicate_blocked": "🛑 Duplicate blocked: {name} ({email}) already applied to this job; not written again.",
        "write_ok": "🚀 {name} written. Job: [{job}] / Status: [{status}]",
        "write_fatal": "❌ Fatal error while writing to the sheet: {error}",
        "resume_preview_alt": "First page of the sample resume",

        # ---------- demo tools ----------
        "see_candidate_side": "📅 See the candidate side: interview booking page",
        "demo_tools": "🛠️ Demo tools",
        "private_code": (
            "Your private code is **{tag}**. Only candidates tagged with it appear in "
            "Manager Review and Email Routing. Keep this page's URL (it contains the code) to come back to them."
        ),
        "clear_mine": "🧹 Clear my candidates",
        "cleared": "Removed {count} of your candidate(s). Other visitors' data is untouched.",
        "clear_failed": "Could not clear your candidates: {error}",
        "owner_reset_note": "Owner only: this reloads the whole demo sheet.",
        "confirm_reset": "I understand this deletes every candidate in the demo sheet, for all visitors",
        "reset_all": "♻️ Reset demo data (all visitors)",
        "reset_done": "Demo data reset: all candidates were removed and the sample candidates reloaded.",
        "reset_failed": "Reset failed: {error}",
        "owner_snapshot_note": (
            "Owner only: save the AI answers for the sample resumes (default job and rules), so the default demo "
            "keeps working when the free AI quota is used up."
        ),
        "not_saved_yet": "Not saved yet: {names}. Score them with the default rules first, then come back.",
        "export_snapshot": "💾 Export AI snapshot ({count} saved answer(s))",
        "upload_snapshot": "Then upload the file to the repo as `sample_jd/demo_ai_snapshot.json`.",

        # ---------- tabs ----------
        "tab_parse": "📥 AI Resume Parsing",
        "tab_review": "👨‍💼 Manager Review",
        "tab_email": "📬 Email Routing",
        "tab_analytics": "📊 Analytics Dashboard",
        "eval_review": "**{count}** candidate(s) scored {threshold}+ and are waiting for a manager's decision",
        "eval_rejected": (
            "**{count}** scored below {threshold} and are in the \"Auto-rejected candidates\" list, "
            "where the manager can read the AI's reasoning and move anyone back for review"
        ),
        "eval_problems": "{count} could not be processed",
        "eval_done": "✅ Resume parsing finished: {parts}.",
        "eval_sep": "; ",

        # ---------- tab 1 ----------
        "parse_header": "📥 AI Resume Parsing",
        "set_opening": "### 🎯 Set the current opening",
        "load_profile": "📂 Load a saved job profile:",
        "delete_profile": "🗑️ Delete this saved profile",
        "job_title_input": "Job title you are currently hiring for:",
        "edit_jd_rules": "📝 Edit job requirements (JD) and AI scoring rules",
        "jd_heading": "### 📄 1. Job Description (JD)",
        "jd_upload": "Upload a JD PDF to extract its text (optional)",
        "jd_read_failed": "❌ Could not read the JD file: {error}",
        "jd_edit": "Review or edit the JD text:",
        "draft_rules": "✨ Let AI draft 7-10 scoring rules from the JD",
        "drafting_rules": "AI is analyzing the JD's keywords and screening criteria...",
        "need_jd": "Upload or enter a JD first.",
        "rules_heading": "### ⚖️ 2. AI scoring and deduction rules",
        "how_scoring_works": (
            "**How scoring works**\n\n"
            "- Every candidate starts at **{baseline}** points. Your rules add or subtract from this; the final score is 0 to 100.\n"
            "- **Below {threshold}:** automatic rejection email (sent {delay} hours after applying). "
            "**{threshold} or above:** goes to the hiring manager.\n"
            "- **Tip:** missing a required item should cost {req_lo} to {req_hi} points; "
            "minor gaps {minor_lo} to {minor_hi}; "
            "nice-to-have skills +{bonus_lo} to +{bonus_hi}."
        ),
        "rules_tip": "Type your own rules, or use the button on the left to have AI draft them in a clean format.",
        "rules_input": "Enter your evaluation and deduction logic:",
        "rules_placeholder": "• Deduction: -15 if the candidate has no Python experience\n• Bonus: +5 for cloud deployment experience",
        "save_profile": "💾 Save this job profile (JD + scoring rules)",
        "need_job_title": "⚠️ Enter a job title above before saving.",
        "profile_updated": "Updated the existing profile",
        "profile_saved": "Saved the JD and scoring rules as the profile",
        "profile_saved_msg": "✅ {verb} \"{name}\". Load it from the dropdown next time.",
        "profile_session_only": "Demo mode: saved profiles are kept only in your browser session.",
        "resumes_heading": "### 📄 Resumes to evaluate",
        "resumes_label": "Resumes to evaluate",
        "use_samples": "🧪 Use sample resumes",
        "upload_own": "📥 Upload my own PDF resumes",
        "samples_caption": (
            "Fictional candidates with different fits for the sample Python Backend Engineer job, so you can see both "
            "outcomes: some go to manager review and some are auto-rejected."
        ),
        "sample_resumes": "Sample resumes",
        "preview": "👀 Preview: {label}",
        "open_pdf": "[📄 Open the original PDF]({url})",
        "upload_resumes": "📥 Upload candidate resumes (multiple files allowed)",
        "start_eval": "🚀 Start AI evaluation",
        "need_resume": "⚠️ Choose or upload at least one resume first.",
        "need_fields": "⚠️ Job title, JD and scoring rules must not be empty.",
        "need_points": "⚠️ The scoring rules contain no point values (such as -15 or +5), so scores cannot be computed. Please fix the rules first.",
        "ai_starting": "🤖 AI interviewer starting...",
        "processing": "Processing: {name}...",
        "using_saved": "📦 Using the saved AI result for {name} (sample resume with the default rules)",
        "analyzing": "🧠 AI is analyzing {name} in depth...",
        "unusable": "❌ The AI response for {name} was unusable and was NOT written: {error}",
        "raw_response": "🔍 View the raw response",
        "unusable_short": "{name}: the AI response was unusable and was NOT written",
        "eval_failed": "❌ Evaluation of {name} failed: {error}",
        "all_evaluated": "✅ All resumes evaluated!",
        "eval_with_problems": "⚠️ Evaluation finished with problems",
        "go_review": "👉 Go to Manager Review",
        "none_processed": "⚠️ No resume could be processed. See the messages above.",

        # ---------- rule warnings (scoring.analyze_rules) ----------
        "warn_no_points": (
            "No point values found. Write each rule with an explicit signed number such as -15 or +5, "
            "otherwise nobody can be scored."
        ),
        "warn_no_deductions": "There are no deduction rules, so nobody can be screened out.",
        "warn_never_rejected": (
            "Even a candidate who triggers every deduction would score {score}, which is still "
            "at or above the {threshold} threshold, so nobody can be auto-rejected."
        ),

        # ---------- tab 2 ----------
        "review_header": "👨‍💼 Manager Review Board",
        "not_processed": "⚠️ Resumes that could not be processed",
        "how_review_works": (
            "💡 **How review works**\n\nResumes the AI scored {threshold} or higher appear here for the hiring manager, "
            "plus any lower-scoring candidate you move back from the auto-rejected list below. "
            "Tick the box to invite a candidate to interview. If the candidate is not a fit, leave the box unticked and "
            "**always write the reason in the comment column**, so HR can analyze the funnel and adjust the hiring strategy."
        ),
        "no_data_yet": "🎉 There is no data in the sheet yet.",
        "choose_job_review": "🎯 Choose the job to review:",
        "reload": "🔄 Reload list",
        "col_name": "Name",
        "col_score": "Score",
        "col_invite": "✅ Invite",
        "col_invite_help": "Tick to invite this candidate to interview",
        "col_comment": "📝 Comment",
        "col_comment_help": "Required when you do not invite: the reason feeds the analytics",
        "col_resume": "Resume",
        "col_open": "📄 Open",
        "col_rationale": "AI rationale (click to expand)",
        "col_arithmetic": "Score arithmetic (click to expand)",
        "job_candidates": "#### 💼 {job} · {count} candidate(s)",
        "save_review": "💾 Save review decisions and sync to the cloud",
        "syncing_review": "Syncing the manager's decisions to Google Sheets...",
        "no_changes": "No changes to save.",
        "no_waiting": "👉 No resumes are waiting for manager review under \"{job}\".",
        "review_saved": (
            "✅ Saved {count} review decision(s). Next, send the interview invitations "
            "and rejection notes in Email Routing."
        ),
        "go_email": "👉 Go to Email Routing",
        "rescue_expander": "🔎 Auto-rejected candidates (score below {threshold}, rejection email not sent yet): {count}",
        "rescue_caption": (
            "The AI rejected these candidates. Their rejection email is sent {delay} hours after they "
            "applied, so until then you can read the AI's reasoning and move anyone back to the review board above. "
            "Once the email is sent, the decision is final."
        ),
        "col_move_back": "↩️ Move back",
        "col_move_back_help": "Tick to send this candidate to the review board above",
        "col_hours_left": "⏳ Hours left",
        "col_hours_left_help": "Hours until the rejection email is sent",
        "job_candidates_small": "**💼 {job} · {count} candidate(s)**",
        "move_back": "↩️ Move selected candidates back to manager review",
        "tick_one": "Tick at least one candidate first.",
        "updating_sheet": "Updating Google Sheets...",
        "moved_back": "✅ Moved {count} candidate(s) back to the review board.",
        "no_rejected": "No auto-rejected candidates are waiting right now.",
        "nothing_to_review": "🎉 No resumes need manager review right now.",

        # ---------- tab 3 ----------
        "email_header": "📬 Automated Email Routing",
        "email_previews": "📧 Demo email previews: {count} email(s), NOT actually sent",
        "preview_to": "**{n}. To:** {to}  \n**Subject:** {subject}",
        "clear_previews": "Clear previews",
        "choose_job_email": "🎯 Choose the job to view or send emails for:",
        "time_unparsed": " (could not parse application time)",
        "time_elapsed": " 🔴 ({hours} hours elapsed, will send immediately)",
        "time_days": " ⏳ ({days}d {hours}h until send)",
        "time_hours": " ⏳ ({hours}h until send)",
        "queued_line": "• {name} {info} [applied: {job}]",
        "queued_line_plain": "• {name} [applied: {job}]",
        "q_ai_reject": "Queued: AI rejection emails",
        "q_invite": "Queued: manager-approved interview invites",
        "q_mgr_reject": "Queued: manager rejection emails",
        "who_receives": "**📄 Candidates who will receive this email:**",
        "nobody_queued": "Nobody is queued",
        "skip_wait": "Demo only: skip the {hours}-hour wait so automatic rejection emails are sent now",
        "run_machine": "⚡ Run the email state machine (send emails for \"{job}\")",
        "no_credentials": "⚠️ Real email credentials are not configured, so no emails can be sent.",
        "no_booking_url": "⚠️ BOOKING_APP_URL is not configured. Interview invites need the public booking-page URL.",
        "engine_running": "📬 Email dispatch engine running...",
        "log_auto_reject": "⚡ [Auto rejection] Sending a thank-you email to {name}...",
        "log_invite": "🚀 [Manager approved] Sending an interview invitation to {name}...",
        "log_mgr_reject": "📝 [Manager declined] Sending a thank-you email to {name}...",
        "engine_done": "🎉 Done! {count} email(s) sent.",
        "engine_crashed": "❌ The email dispatch engine crashed: {error}",
        "no_email_data": "There is no data to send emails for yet.",

        # ---------- emails ----------
        "mail_reject_subject": "[{company}] Update on your application for \"{job}\"",
        "mail_auto_reject_body": (
            "Dear {name},\n\n"
            "Thank you for your interest in the \"{job}\" position at {company_short}. "
            "After an initial review, and given the large number of applicants, we found that your "
            "background is not a close enough match with the specific technical stack our team "
            "currently needs, so we are unable to move forward with an interview at this time. "
            "We wish you every success in your career and hope our paths cross again.\n\n"
            "Sincerely,\nThe {company} Recruiting Team"
        ),
        "mail_invite_subject": "[Interview Invitation] {company} - \"{job}\" team interview",
        "mail_invite_body": """Dear {name},<br><br>
Thank you for applying for the "{job}" position at {company_short}. After a careful review of your background, the hiring manager would like to invite you to the next stage: an online interview!<br><br>
To fit around the technical managers' schedules, we built a self-service booking page. It shows all of their open time slots for the next three weeks, synced in real time.<br><br>
👇 <b>Click the link below and choose one open slot to book your interview:</b><br>
<a href="{url}" style="font-size: 16px; font-weight: bold; color: #1a73e8;">Open the interview booking page</a><br><br>
<span style="color: gray; font-size: 12px;">
💡 Tip: popular slots fill up quickly, so we recommend booking within 24 hours of receiving this email. Once you book, a confirmation email with the meeting details will be sent to your inbox right away. We look forward to meeting you!<br><br>
Sincerely,<br>The {company} Recruiting Team
</span>
""",
        "mail_mgr_reject_body": (
            "Dear {name},\n\n"
            "Thank you for applying for the \"{job}\" position at {company_short}. "
            "Your résumé is strong; however, after review by the hiring manager, we felt your fit with "
            "our current technical stack or salary expectations is not quite aligned with this role, "
            "so we are unable to arrange an interview this time. Thank you again for applying, and we "
            "hope to work with you in the future!\n\n"
            "Sincerely,\nThe {company} Recruiting Team"
        ),

        # ---------- tab 4 ----------
        "analytics_header": "📊 Applicant Analytics and Hiring Decision Report",
        "analytics_demo": (
            "Demo data: about 40 fictional sample candidates plus the candidates visitors uploaded in the last 24 hours. "
            "Unlike the review and routing tabs, this report is not limited to your own candidates."
        ),
        "sync_analytics": "🔄 Sync cloud data and load analytics",
        "fetching": "Fetching the latest data from Google Sheets...",
        "synced": "✅ Data synced.",
        "no_recruiting_data": "There is no recruiting data in the sheet yet.",
        "all": "All",
        "filter_job": "🎯 Filter by job",
        "time_window": "⏰ Time window",
        "last_week": "Last week",
        "last_3_weeks": "Last 3 weeks",
        "k_total": "👥 Total applicants",
        "k_interview": "✉️ Reached interview stage",
        "k_conversion": "Overall conversion: {pct}%",
        "k_hired": "🎉 Hired",
        "k_hire_rate": "Hire rate: {pct}%",
        "c_applicants": "Applicants",
        "c_conversion_pct": "Interview conversion (%)",
        "c_exp_title": "<b>Applicants and interview conversion by experience</b>",
        "c_conversion_axis": "Conversion (%)",
        "c_reason_title": "<b>Why candidates drop out of the funnel</b>",
        "no_rejections": "No rejection data to analyze yet.",
        "c_hover": "<b>%{text}</b><br>AI score: %{x}<br>Expected salary: %{y:,.0f}<extra></extra>",
        "c_scatter_title": "<b>AI score vs. expected salary</b>",
        "c_score_axis": "Overall AI score (0-100)",
        "c_salary_axis": "Expected monthly salary (TWD)",
        "c_with_skill": "Candidates with skill",
        "c_avg_salary": "Avg. expected salary",
        "c_skill_title": "<b>Core skill price analysis</b>",
        "c_with_skill_axis": "Candidates with this skill",
        "c_avg_salary_axis": "Avg. expected salary of this group (TWD)",
        "insights_header": "🤖 AI hiring insights",
        "insights_caption": (
            "The app computes every number from the data above (filters included). The AI only explains them, and it "
            "never sees names, emails or resumes. Treat the result as a starting point for discussion, not a decision."
        ),
        "gen_insights": "✨ Generate insights for this view",
        "reading_numbers": "The AI is reading the numbers...",
        "report_scope": "job: {job} / window: {window}",
        "report_for": "Report for {scope}",
        "report_data": "📎 Data behind this report (calculated by the app, given to the AI)",
        "no_match_filters": "No applicants match the current filters.",
        "click_sync": "👈 Click \"Sync cloud data and load analytics\" at the top left to load the latest recruiting report.",

        # ---------- booking page ----------
        "b_page_title": "Interview Scheduling",
        "b_title": "👔 Interview Scheduling",
        "b_demo": (
            "🧪 **Demo mode.** The interviewers' calendars are simulated, no real Google Calendar event is created, "
            "and no email is sent. Pick an invited candidate below and book a slot as them."
        ),
        "b_hr_link": "This is the candidate side. The recruiter side is the [HR Command Center]({url}).",
        "b_calendar_failed": "❌ Could not load the interviewers' calendars. Please try again later. ({error})",
        "b_syncing": "🔄 Syncing the interviewers' latest calendars...",
        "b_not_enough": "❌ Not enough calendar data (at least 2 interviewers are required).",
        "b_sample_label": "{name} (sample candidate)",
        "b_own_label": "{name} (your candidate from the HR demo)",
        "b_booked": "✅ Your interview is booked!",
        "b_time": "📅 Time: {date} ({day}) {range}",
        "b_meet": "🔗 Google Meet room: {url}",
        "b_sent": "A confirmation email and calendar invitation have been sent to your inbox. To change the time, please contact the recruiting team.",
        "b_preview": "📧 Demo preview (not sent) - {subject}",
        "b_to": "To: {to}",
        "b_simulated": (
            "Demo: this was a sample candidate, so the booking was simulated and not saved. The candidate and "
            "the slot stay available for the next visitor."
        ),
        "b_again": "🔁 Try another booking",
        "b_details": "### 📝 Your details",
        "b_load_failed": "❌ Could not load the demo candidates. Please try again later. ({error})",
        "b_none_invited": "No invited candidates are available right now. Please try again in a moment.",
        "b_book_as": "📋 Book as this invited candidate:",
        "b_full_name": "Full name",
        "b_email": "Email address",
        "b_your_name": "Your full name (must match your résumé)",
        "b_name_ph": "e.g. Alex Taylor",
        "b_your_email": "Your email address (must match your résumé)",
        "b_email_ph": "e.g. name@example.com",
        "b_choose_slot": "### 📅 Choose an interview slot that works for you",
        "b_timezone": "All times are {tz} (GMT+{offset}).",
        "b_date_heading": "**📅 {date} ({day})**",
        "b_confirm": "Confirm booking",
        "b_need_details": "⚠️ Enter your name and email at the top of the page before confirming.",
        "b_verifying": "🔄 Verifying your applicant record...",
        "b_verify_failed": (
            "🛑 Verification failed. No application matches this name and email. Check for typos, "
            "and make sure you have passed the initial screening and received an interview invitation."
        ),
        "b_taken": "🛑 Booking failed: another candidate just took this slot. Please choose a different one.",
        "b_changed": "🛑 Booking failed: an interviewer's schedule just changed for this slot. Please choose a different one.",
        "b_creating": "🎉 Verified, and the slot is still free. Creating your Google Calendar event...",
        "b_sending": "✉️ Sending your confirmation email...",
        "b_mail_failed": "Your interview is booked, but the confirmation email failed to send: {error}",
        "b_unexpected": "Something unexpected happened. Please try again later.",
        "b_unexpected_detail": "Something unexpected happened. Please try again later: {error}",
        "b_full": (
            "The interview schedule is very full right now and every slot is taken. "
            "Please call the HR team to book an interview: {phone}"
        ),
        "b_mail_subject": "[Interview Confirmed] {company} - technical interview booked",
        "b_mail_body": """Dear {name},

You have successfully booked a technical interview with {company_short}. Your interview details are below. Please arrive on time.

📅 Date: {date}
⏰ Time: {start} - {end} ({tz}, GMT+{offset})
💻 Online meeting link (Google Meet): {url}

💡 Tips:
1. On the day, click the Google Meet link above to join. The hiring manager will meet you online.
2. We suggest testing your microphone, camera and network connection 5 minutes before the interview.

Good luck!

Sincerely,
The {company} Recruiting Team
""",
    },

    # =====================================================
    # 繁體中文（台灣）
    # =====================================================
    "zh": {
        # ---------- HR app: shell ----------
        "page_title": "HRIS 招募指揮中心",
        "title": "👔 HRIS 招募指揮中心",
        "demo_banner": (
            "🧪 **示範模式。** 系統不會真的寄出信件、不會建立行事曆活動，你上傳的檔案也不會存到 Drive，"
            "信件內容會直接顯示在畫面上。儲存的職缺設定只保留在你的瀏覽器工作階段中。"
            "你上傳的候選人會加上專屬代碼（例如 ariel.lin+k3f9@example.com），其他訪客看不到，24 小時後自動刪除。"
            "分析儀表板另外包含約 40 位虛構的範例候選人。"
        ),
        "missing_secret": "❌ 缺少必要的設定：{error}。請檢查 .streamlit/secrets.toml（參考 secrets.example.toml）。",
        "sheets_failed": "❌ 無法連線到 Google Sheets：{error}",
        "ai_init_failed": "❌ AI 服務初始化失敗：{error}",
        "cleanup_skipped": "略過示範資料清理：{error}",
        "all_jobs": "所有職缺",
        "new_job": "➕ 手動新增職缺",
        "quota_used_up": "今天這個示範的免費 AI 額度已經用完（Gemini 免費方案每天只能呼叫有限次數），明天就會恢復。",
        "quota_samples_ok": "內建範例履歷搭配預設評分規則仍然隨時可用，因為結果已事先儲存。",
        "quota_screenshots": "也可以到 GitHub 看每個步驟的截圖：{url}#how-it-works",
        "rules_failed": "⚠️ 規則產生失敗：{error}",
        "report_unreadable": "⚠️ 無法讀取 AI 產生的報告，請再按一次按鈕。",
        "report_failed": "⚠️ 無法產生報告：{error}",
        "m_applicants": "應徵人數",
        "m_reached_interview": "進入面試",
        "m_people": "{count} 人",
        "m_hired": "錄取",
        "m_of_applicants": "占應徵者 {pct}%",
        "m_median_score": "分數中位數",
        "report_summary": "📌 **摘要**  \n{text}",
        "report_findings": "#### 🔍 主要發現",
        "report_recommendations": "#### 💡 建議",
        "no_apps_script": "尚未設定 APPS_SCRIPT_URL，履歷檔案不會存到 Drive。",
        "proxy_error": "❌ 上傳代理回傳錯誤：{error}",
        "proxy_failed": "❌ 透過代理上傳失敗：{error}",
        "sheet_not_connected": "🛑 尚未連線到試算表。",
        "duplicate_blocked": "🛑 已擋下重複資料：{name}（{email}）已經應徵過這個職缺，不會重複寫入。",
        "write_ok": "🚀 已寫入 {name}。職缺：［{job}］／狀態：［{status}］",
        "write_fatal": "❌ 寫入試算表時發生嚴重錯誤：{error}",
        "resume_preview_alt": "範例履歷第一頁",

        # ---------- demo tools ----------
        "see_candidate_side": "📅 查看候選人端：面試預約頁面",
        "demo_tools": "🛠️ 示範工具",
        "private_code": (
            "你的專屬代碼是 **{tag}**。只有帶這個代碼的候選人會出現在「主管審核」和「信件寄送」。"
            "保留這個頁面的網址（裡面含有代碼），之後就能回來繼續操作。"
        ),
        "clear_mine": "🧹 清除我的候選人",
        "cleared": "已移除你的 {count} 位候選人，其他訪客的資料不受影響。",
        "clear_failed": "無法清除你的候選人：{error}",
        "owner_reset_note": "僅限擁有者：這會重新載入整份示範試算表。",
        "confirm_reset": "我了解這會刪除示範試算表中所有訪客的候選人",
        "reset_all": "♻️ 重設示範資料（所有訪客）",
        "reset_done": "示範資料已重設：所有候選人已移除，並重新載入範例候選人。",
        "reset_failed": "重設失敗：{error}",
        "owner_snapshot_note": "僅限擁有者：儲存範例履歷（預設職缺與規則）的 AI 結果，讓免費 AI 額度用完時，預設示範仍可正常運作。",
        "not_saved_yet": "尚未儲存：{names}。請先用預設規則評分這些履歷，再回到這裡。",
        "export_snapshot": "💾 匯出 AI 結果快照（已儲存 {count} 筆）",
        "upload_snapshot": "接著把檔案上傳到 repo，路徑為 `sample_jd/demo_ai_snapshot.json`。",

        # ---------- tabs ----------
        "tab_parse": "📥 AI 履歷解析",
        "tab_review": "👨‍💼 主管審核",
        "tab_email": "📬 信件寄送",
        "tab_analytics": "📊 數據分析",
        "eval_review": "**{count}** 位候選人分數達 {threshold} 分以上，正等待主管決定",
        "eval_rejected": "**{count}** 位低於 {threshold} 分，列在「AI 自動淘汰的候選人」清單中，主管可以查看 AI 的理由，並把任何人移回審核",
        "eval_problems": "{count} 份無法處理",
        "eval_done": "✅ 履歷解析完成：{parts}。",
        "eval_sep": "；",

        # ---------- tab 1 ----------
        "parse_header": "📥 AI 履歷解析",
        "set_opening": "### 🎯 設定目前的職缺",
        "load_profile": "📂 載入已儲存的職缺設定：",
        "delete_profile": "🗑️ 刪除這個職缺設定",
        "job_title_input": "目前招募的職缺名稱：",
        "edit_jd_rules": "📝 編輯職缺說明（JD）與 AI 評分規則",
        "jd_heading": "### 📄 1. 職缺說明（JD）",
        "jd_upload": "上傳 JD 的 PDF 檔以擷取文字（選填）",
        "jd_read_failed": "❌ 無法讀取 JD 檔案：{error}",
        "jd_edit": "檢查或修改 JD 內容：",
        "draft_rules": "✨ 讓 AI 根據 JD 草擬 7–10 條評分規則",
        "drafting_rules": "AI 正在分析 JD 的關鍵字與篩選條件……",
        "need_jd": "請先上傳或輸入 JD。",
        "rules_heading": "### ⚖️ 2. AI 評分與扣分規則",
        "how_scoring_works": (
            "**評分方式**\n\n"
            "- 每位候選人從 **{baseline}** 分起算，依你的規則加分或扣分，最終分數介於 0 到 100。\n"
            "- **低於 {threshold} 分：**自動寄出婉拒信（應徵 {delay} 小時後寄出）。"
            "**{threshold} 分以上：**交給用人主管審核。\n"
            "- **小提醒：**缺少必備條件建議扣 {req_lo}–{req_hi} 分；"
            "次要落差扣 {minor_lo}–{minor_hi} 分；"
            "加分條件 +{bonus_lo} 到 +{bonus_hi} 分。"
        ),
        "rules_tip": "可以自己輸入規則，或用左邊的按鈕讓 AI 草擬格式整齊的規則。",
        "rules_input": "輸入評分與扣分邏輯：",
        "rules_placeholder": "• 沒有 Python 經驗：-15 分\n• 具備雲端部署經驗：+5 分",
        "save_profile": "💾 儲存這個職缺設定（JD＋評分規則）",
        "need_job_title": "⚠️ 儲存前請先在上方輸入職缺名稱。",
        "profile_updated": "已更新現有的職缺設定",
        "profile_saved": "已將 JD 與評分規則儲存為職缺設定",
        "profile_saved_msg": "✅ {verb}「{name}」，下次可以從下拉選單載入。",
        "profile_session_only": "示範模式：儲存的職缺設定只保留在你的瀏覽器工作階段中。",
        "resumes_heading": "### 📄 要評估的履歷",
        "resumes_label": "要評估的履歷",
        "use_samples": "🧪 使用範例履歷",
        "upload_own": "📥 上傳自己的 PDF 履歷",
        "samples_caption": "以下是虛構的候選人，與範例職缺「Python 後端開發工程師」的符合程度各不相同，可以同時看到兩種結果：有的進入主管審核，有的被自動淘汰。",
        "sample_resumes": "範例履歷",
        "preview": "👀 預覽：{label}",
        "open_pdf": "[📄 開啟原始 PDF]({url})",
        "upload_resumes": "📥 上傳候選人履歷（可一次選多個檔案）",
        "start_eval": "🚀 開始 AI 評估",
        "need_resume": "⚠️ 請先選擇或上傳至少一份履歷。",
        "need_fields": "⚠️ 職缺名稱、JD 和評分規則都不能空白。",
        "need_points": "⚠️ 評分規則中沒有任何分數（例如 -15 或 +5），無法計算分數，請先修正規則。",
        "ai_starting": "🤖 AI 面試官啟動中……",
        "processing": "處理中：{name}……",
        "using_saved": "📦 使用 {name} 已儲存的 AI 結果（範例履歷搭配預設規則）",
        "analyzing": "🧠 AI 正在深入分析 {name}……",
        "unusable": "❌ {name} 的 AI 回應無法使用，因此「沒有」寫入：{error}",
        "raw_response": "🔍 查看原始回應",
        "unusable_short": "{name}：AI 回應無法使用，因此沒有寫入",
        "eval_failed": "❌ {name} 評估失敗：{error}",
        "all_evaluated": "✅ 所有履歷都評估完成！",
        "eval_with_problems": "⚠️ 評估完成，但有部分問題",
        "go_review": "👉 前往主管審核",
        "none_processed": "⚠️ 沒有任何履歷處理成功，請查看上方訊息。",

        # ---------- rule warnings ----------
        "warn_no_points": "找不到任何分數。每條規則都要寫出帶正負號的數字，例如 -15 或 +5，否則無法評分。",
        "warn_no_deductions": "沒有任何扣分規則，所以沒有人會被篩掉。",
        "warn_never_rejected": "即使候選人觸發所有扣分規則，分數仍有 {score} 分，不低於 {threshold} 分的門檻，因此沒有人會被自動淘汰。",

        # ---------- tab 2 ----------
        "review_header": "👨‍💼 主管審核",
        "not_processed": "⚠️ 無法處理的履歷",
        "how_review_works": (
            "💡 **審核方式**\n\nAI 評分達 {threshold} 分以上的履歷會出現在這裡，交給用人主管決定；"
            "你從下方「自動淘汰」清單移回的低分候選人也會出現在這裡。"
            "勾選即邀請候選人面試。如果不適合，請不要勾選，並**務必在備註欄寫下原因**，"
            "讓 HR 能分析招募漏斗並調整招募策略。"
        ),
        "no_data_yet": "🎉 試算表裡還沒有資料。",
        "choose_job_review": "🎯 選擇要審核的職缺：",
        "reload": "🔄 重新整理",
        "col_name": "姓名",
        "col_score": "分數",
        "col_invite": "✅ 邀請",
        "col_invite_help": "勾選即邀請這位候選人面試",
        "col_comment": "📝 備註",
        "col_comment_help": "不邀請時必填：原因會用於數據分析",
        "col_resume": "履歷",
        "col_open": "📄 開啟",
        "col_rationale": "AI 評分理由（點擊展開）",
        "col_arithmetic": "分數計算過程（點擊展開）",
        "job_candidates": "#### 💼 {job} · {count} 位候選人",
        "save_review": "💾 儲存審核結果並同步到雲端",
        "syncing_review": "正在將主管的決定同步到 Google Sheets……",
        "no_changes": "沒有需要儲存的變更。",
        "no_waiting": "👉「{job}」目前沒有等待主管審核的履歷。",
        "review_saved": "✅ 已儲存 {count} 筆審核結果。接下來請到「信件寄送」寄出面試邀請和婉拒信。",
        "go_email": "👉 前往信件寄送",
        "rescue_expander": "🔎 AI 自動淘汰的候選人（低於 {threshold} 分、婉拒信尚未寄出）：{count} 位",
        "rescue_caption": (
            "這些候選人被 AI 淘汰。婉拒信會在應徵後 {delay} 小時寄出，在那之前你可以查看 AI 的理由，"
            "並把任何人移回上方的審核清單。信件寄出後，決定就無法更改。"
        ),
        "col_move_back": "↩️ 移回審核",
        "col_move_back_help": "勾選即把這位候選人移回上方的審核清單",
        "col_hours_left": "⏳ 剩餘小時",
        "col_hours_left_help": "距離寄出婉拒信的小時數",
        "job_candidates_small": "**💼 {job} · {count} 位候選人**",
        "move_back": "↩️ 將勾選的候選人移回主管審核",
        "tick_one": "請先勾選至少一位候選人。",
        "updating_sheet": "正在更新 Google Sheets……",
        "moved_back": "✅ 已將 {count} 位候選人移回審核清單。",
        "no_rejected": "目前沒有等待寄出婉拒信的自動淘汰候選人。",
        "nothing_to_review": "🎉 目前沒有需要主管審核的履歷。",

        # ---------- tab 3 ----------
        "email_header": "📬 自動化信件寄送",
        "email_previews": "📧 示範信件預覽：共 {count} 封，並未實際寄出",
        "preview_to": "**{n}. 收件人：** {to}  \n**主旨：** {subject}",
        "clear_previews": "清除預覽",
        "choose_job_email": "🎯 選擇要查看或寄信的職缺：",
        "time_unparsed": "（無法讀取應徵時間）",
        "time_elapsed": " 🔴（已超過 {hours} 小時，將立即寄出）",
        "time_days": " ⏳（{days} 天 {hours} 小時後寄出）",
        "time_hours": " ⏳（{hours} 小時後寄出）",
        "queued_line": "• {name} {info}［應徵：{job}］",
        "queued_line_plain": "• {name}［應徵：{job}］",
        "q_ai_reject": "待寄：AI 婉拒信",
        "q_invite": "待寄：主管核准的面試邀請",
        "q_mgr_reject": "待寄：主管婉拒信",
        "who_receives": "**📄 將收到這封信的候選人：**",
        "nobody_queued": "目前沒有人",
        "skip_wait": "僅限示範：略過 {hours} 小時的等待，立即寄出自動婉拒信",
        "run_machine": "⚡ 執行信件寄送流程（寄送「{job}」的信件）",
        "no_credentials": "⚠️ 尚未設定真實的寄信帳號，無法寄出信件。",
        "no_booking_url": "⚠️ 尚未設定 BOOKING_APP_URL，面試邀請需要公開的預約頁面網址。",
        "engine_running": "📬 信件寄送流程執行中……",
        "log_auto_reject": "⚡［自動婉拒］正在寄送感謝信給 {name}……",
        "log_invite": "🚀［主管核准］正在寄送面試邀請給 {name}……",
        "log_mgr_reject": "📝［主管婉拒］正在寄送感謝信給 {name}……",
        "engine_done": "🎉 完成！共寄出 {count} 封信。",
        "engine_crashed": "❌ 信件寄送流程發生錯誤：{error}",
        "no_email_data": "目前還沒有需要寄信的資料。",

        # ---------- emails ----------
        "mail_reject_subject": "【{company}】「{job}」應徵結果通知",
        "mail_auto_reject_body": (
            "{name} 您好：\n\n"
            "感謝您應徵 {company_short}「{job}」一職。"
            "經過初步審閱，由於本次應徵人數眾多，我們評估您的背景與團隊目前所需的技術條件尚未完全相符，"
            "因此這次暫時無法安排面試。"
            "祝福您求職順利，也期待未來有機會合作。\n\n"
            "{company} 招募團隊 敬上"
        ),
        "mail_invite_subject": "【面試邀請】{company}「{job}」團隊面試",
        "mail_invite_body": """{name} 您好：<br><br>
感謝您應徵 {company_short}「{job}」一職。用人主管仔細看過您的背景後，誠摯邀請您參加下一階段的線上面試！<br><br>
為了配合技術主管的行程，我們提供自助預約頁面，會即時顯示主管們未來三週所有可預約的時段。<br><br>
👇 <b>請點擊下方連結，選擇一個時段完成面試預約：</b><br>
<a href="{url}" style="font-size: 16px; font-weight: bold; color: #1a73e8;">前往面試預約頁面</a><br><br>
<span style="color: gray; font-size: 12px;">
💡 小提醒：熱門時段很快就會額滿，建議您在收到信後 24 小時內完成預約。預約完成後，系統會立即寄送附有會議資訊的確認信到您的信箱。期待與您見面！<br><br>
{company} 招募團隊 敬上
</span>
""",
        "mail_mgr_reject_body": (
            "{name} 您好：\n\n"
            "感謝您應徵 {company_short}「{job}」一職。"
            "您的履歷很優秀，不過經過用人主管審閱後，我們評估您與目前的技術需求或薪資條件尚未完全契合，"
            "因此這次無法安排面試。再次感謝您的應徵，期待未來有合作的機會！\n\n"
            "{company} 招募團隊 敬上"
        ),

        # ---------- tab 4 ----------
        "analytics_header": "📊 應徵者分析與招募決策報告",
        "analytics_demo": "示範資料：約 40 位虛構的範例候選人，加上訪客在過去 24 小時內上傳的候選人。與「主管審核」和「信件寄送」不同，這份報告不只包含你自己的候選人。",
        "sync_analytics": "🔄 同步雲端資料並載入分析",
        "fetching": "正在從 Google Sheets 取得最新資料……",
        "synced": "✅ 資料已同步。",
        "no_recruiting_data": "試算表裡還沒有招募資料。",
        "all": "全部",
        "filter_job": "🎯 依職缺篩選",
        "time_window": "⏰ 時間範圍",
        "last_week": "最近一週",
        "last_3_weeks": "最近三週",
        "k_total": "👥 應徵總人數",
        "k_interview": "✉️ 進入面試階段",
        "k_conversion": "整體轉換率：{pct}%",
        "k_hired": "🎉 錄取",
        "k_hire_rate": "錄取率：{pct}%",
        "c_applicants": "應徵人數",
        "c_conversion_pct": "面試轉換率（%）",
        "c_exp_title": "<b>各年資的應徵人數與面試轉換率</b>",
        "c_conversion_axis": "轉換率（%）",
        "c_reason_title": "<b>候選人在招募漏斗中流失的原因</b>",
        "no_rejections": "目前還沒有可分析的淘汰資料。",
        "c_hover": "<b>%{text}</b><br>AI 分數：%{x}<br>期待薪資：%{y:,.0f}<extra></extra>",
        "c_scatter_title": "<b>AI 分數與期待薪資</b>",
        "c_score_axis": "AI 總分（0–100）",
        "c_salary_axis": "期待月薪（新台幣）",
        "c_with_skill": "具備此技能的人數",
        "c_avg_salary": "平均期待薪資",
        "c_skill_title": "<b>核心技能薪資分析</b>",
        "c_with_skill_axis": "具備此技能的候選人數",
        "c_avg_salary_axis": "這群人的平均期待薪資（新台幣）",
        "insights_header": "🤖 AI 招募洞察",
        "insights_caption": "所有數字都由系統根據上方資料（含篩選條件）計算，AI 只負責解讀，完全看不到姓名、Email 或履歷。請把結果當作討論的起點，而不是最終決策。",
        "gen_insights": "✨ 針對目前的畫面產生洞察",
        "reading_numbers": "AI 正在解讀數據……",
        "report_scope": "職缺：{job}／時間：{window}",
        "report_for": "報告範圍：{scope}",
        "report_data": "📎 這份報告背後的數據（由系統計算後提供給 AI）",
        "no_match_filters": "沒有符合目前篩選條件的應徵者。",
        "click_sync": "👈 點選左上方的「同步雲端資料並載入分析」，載入最新的招募報告。",

        # ---------- booking page ----------
        "b_page_title": "面試預約",
        "b_title": "👔 面試預約",
        "b_demo": "🧪 **示範模式。** 面試官的行事曆是模擬的，不會建立真的 Google 行事曆活動，也不會寄出信件。請在下方選擇一位已受邀的候選人，以他的身分預約時段。",
        "b_hr_link": "這裡是候選人端，招募人員端請見 [HR 招募指揮中心]({url})。",
        "b_calendar_failed": "❌ 無法載入面試官的行事曆，請稍後再試。（{error}）",
        "b_syncing": "🔄 正在同步面試官最新的行事曆……",
        "b_not_enough": "❌ 行事曆資料不足（至少需要 2 位面試官）。",
        "b_sample_label": "{name}（範例候選人）",
        "b_own_label": "{name}（你在 HR 示範中邀請的候選人）",
        "b_booked": "✅ 面試預約成功！",
        "b_time": "📅 時間：{date}（{day}）{range}",
        "b_meet": "🔗 Google Meet 會議室：{url}",
        "b_sent": "確認信與行事曆邀請已寄到你的信箱。如需更改時間，請聯絡招募團隊。",
        "b_preview": "📧 示範預覽（未寄出）- {subject}",
        "b_to": "收件人：{to}",
        "b_simulated": "示範：這是範例候選人，所以只是模擬預約、不會儲存，候選人和時段都會保留給下一位訪客。",
        "b_again": "🔁 再預約一次",
        "b_details": "### 📝 你的資料",
        "b_load_failed": "❌ 無法載入示範候選人，請稍後再試。（{error}）",
        "b_none_invited": "目前沒有已受邀的候選人，請稍後再試。",
        "b_book_as": "📋 以這位受邀候選人的身分預約：",
        "b_full_name": "姓名",
        "b_email": "電子郵件",
        "b_your_name": "你的姓名（須與履歷相同）",
        "b_name_ph": "例如：王小明",
        "b_your_email": "你的電子郵件（須與履歷相同）",
        "b_email_ph": "例如：name@example.com",
        "b_choose_slot": "### 📅 選擇方便的面試時段",
        "b_timezone": "以下皆為{tz}時間（GMT+{offset}）。",
        "b_date_heading": "**📅 {date}（{day}）**",
        "b_confirm": "確認預約",
        "b_need_details": "⚠️ 確認前請先在頁面上方填寫姓名與電子郵件。",
        "b_verifying": "🔄 正在核對你的應徵紀錄……",
        "b_verify_failed": "🛑 核對失敗：找不到符合這組姓名與電子郵件的應徵紀錄。請檢查是否有錯字，並確認你已通過初步篩選且收到面試邀請。",
        "b_taken": "🛑 預約失敗：這個時段剛被其他候選人預約，請選擇其他時段。",
        "b_changed": "🛑 預約失敗：面試官這個時段的行程剛有變動，請選擇其他時段。",
        "b_creating": "🎉 核對成功，時段仍可預約，正在建立 Google 行事曆活動……",
        "b_sending": "✉️ 正在寄送確認信……",
        "b_mail_failed": "面試已預約成功，但確認信寄送失敗：{error}",
        "b_unexpected": "發生預期外的錯誤，請稍後再試。",
        "b_unexpected_detail": "發生預期外的錯誤，請稍後再試：{error}",
        "b_full": "目前面試時段已全部額滿，請來電與 HR 團隊預約面試：{phone}",
        "b_mail_subject": "【面試確認】{company} 技術面試預約成功",
        "b_mail_body": """{name} 您好：

您已成功預約 {company_short} 的技術面試，面試資訊如下，請準時參加。

📅 日期：{date}
⏰ 時間：{start} - {end}（{tz}，GMT+{offset}）
💻 線上會議連結（Google Meet）：{url}

💡 小提醒：
1. 面試當天請點擊上方的 Google Meet 連結加入，用人主管會在線上與您見面。
2. 建議在面試前 5 分鐘先測試麥克風、鏡頭與網路連線。

祝您面試順利！

{company} 招募團隊 敬上
""",
    },
}
