"""Create the Google Calendar event (with a Google Meet room) for a booked interview."""

import json
import os
import uuid

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import config


def get_calendar_service():
    """Prefer the cloud secret; fall back to a local token.json for development."""
    token_secret = config.get_setting("google_calendar_token")
    if token_secret:
        token_info = json.loads(token_secret) if isinstance(token_secret, str) else dict(token_secret)
        creds = Credentials.from_authorized_user_info(token_info)
    elif os.path.exists("token.json"):
        creds = Credentials.from_authorized_user_file("token.json")
    else:
        raise RuntimeError("No Google Calendar authorization token found (secret or token.json).")

    return build("calendar", "v3", credentials=creds)


def create_interview_event(applicant_name, applicant_email, date_str, start_time_str, end_time_str):
    """Insert the interview event and return (event_id, meet_link), or (None, None) on failure."""
    if config.DEMO_MODE:
        # No Google API call: return a clearly fake event id and meeting link
        fake_id = f"demo-{uuid.uuid4().hex[:8]}"
        return fake_id, f"https://meet.example.com/demo-{fake_id[5:]} (demo link, not a real meeting)"

    try:
        service = get_calendar_service()

        clean_date = date_str.strip()
        start_iso = f"{clean_date}T{start_time_str.strip()}:00"
        end_iso = f"{clean_date}T{end_time_str.strip()}:00"

        print(f"📡 Submitting event time to Google: {start_iso} to {end_iso}")

        attendees = [{"email": email} for email in config.get_interviewer_emails()]
        attendees.append({"email": applicant_email.strip()})

        event_body = {
            "summary": f"{config.INTERVIEW_TAG} {config.COMPANY_NAME} - {applicant_name.strip()}",
            "description": (
                f"{config.COMPANY_NAME} online technical interview.\n"
                f"Candidate: {applicant_name.strip()}\n"
                f"Email: {applicant_email.strip()}"
            ),
            "start": {"dateTime": start_iso, "timeZone": config.TIMEZONE_NAME},
            "end": {"dateTime": end_iso, "timeZone": config.TIMEZONE_NAME},
            "attendees": attendees,
            "conferenceData": {
                "createRequest": {
                    "requestId": f"meet-{uuid.uuid4().hex[:8]}",
                    "conferenceSolutionKey": {"type": "hangoutsMeet"},
                }
            },
            "reminders": {
                "useDefault": False,
                "overrides": [{"method": "popup", "minutes": 30}],
            },
        }

        event = service.events().insert(calendarId="primary", body=event_body, conferenceDataVersion=1).execute()

        meet_link = ""
        entry_points = event.get("conferenceData", {}).get("entryPoints", [])
        for ep in entry_points:
            if ep.get("entryPointType") == "video":
                meet_link = ep.get("uri", "")
                break

        if not meet_link and entry_points:
            meet_link = entry_points[0].get("uri", "")

        print(f"✅ Google Meet link created: {meet_link}")
        return event.get("id"), meet_link

    except Exception as e:
        print(f"❌ Calendar module error: {e}")
        return None, None
