"""
Fetch the interviewers' busy blocks from Google Calendar and normalize them to Taipei time (GMT+8).

Used live by streamlit_app.py, and runnable on its own for local development:

    python calendar_busytime.py     # first run opens a browser for Google OAuth consent
"""

import datetime
import json
import os.path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

# ================= Settings =================
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLIENT_SECRETS_FILE = "credentials.json"
TOKEN_PATH = "token.json"
OUTPUT_PATH = "calendar_output.json"
# ============================================


def get_calendar_service():
    creds = None
    token_secret = config.get_setting("google_calendar_token")

    # Prefer the cloud secret
    if token_secret:
        token_info = json.loads(token_secret) if isinstance(token_secret, str) else dict(token_secret)
        creds = Credentials.from_authorized_user_info(token_info, SCOPES)
    # Local fallback
    elif os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            # A browser sign-in is only possible in local development, not on the cloud host
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        # Locally, persist the refreshed or newly issued credentials
        if not token_secret:
            with open(TOKEN_PATH, "w") as token:
                token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def generate_demo_busy_times(emails, days_range=21):
    """Deterministic fake busy blocks so the booking page has a realistic, repeatable schedule.

    Each interviewer gets a recurring pattern (meetings, a standing lunch, a blocked afternoon)
    that differs by weekday and by interviewer, so the shared free slots are a believable subset.
    """
    local_tz = datetime.timezone(datetime.timedelta(hours=config.TIMEZONE_OFFSET_HOURS))
    today = datetime.datetime.now(local_tz).date()
    # (weekday -> list of (start_hour, start_min, end_hour, end_min)) per interviewer index
    patterns = [
        {0: [(10, 0, 11, 0)], 1: [(14, 0, 16, 0)], 2: [(9, 30, 10, 30)], 3: [(13, 0, 15, 0)], 4: [(16, 0, 17, 0)]},
        {0: [(14, 0, 15, 0)], 1: [(9, 0, 10, 0)], 2: [(13, 0, 14, 30)], 3: [(10, 0, 11, 30)], 4: [(14, 0, 18, 0)]},
    ]
    out = {}
    for i, email in enumerate(emails):
        pattern = patterns[i % len(patterns)]
        blocks = []
        for d in range(days_range + 1):
            day = today + datetime.timedelta(days=d)
            for sh, sm, eh, em in pattern.get(day.weekday(), []):
                start = datetime.datetime(day.year, day.month, day.day, sh, sm)
                end = datetime.datetime(day.year, day.month, day.day, eh, em)
                blocks.append(
                    {
                        "start": start.strftime("%Y-%m-%dT%H:%M:%S"),
                        "end": end.strftime("%Y-%m-%dT%H:%M:%S"),
                        "timezone": f"GMT+{config.TIMEZONE_OFFSET_HOURS}",
                    }
                )
        out[email] = blocks
    return out


def fetch_and_save_calendar_data(emails, days_range=21):
    if config.DEMO_MODE:
        return generate_demo_busy_times(emails, days_range)

    service = get_calendar_service()

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    future_utc = now_utc + datetime.timedelta(days=days_range)
    time_min = now_utc.isoformat().replace("+00:00", "Z")
    time_max = future_utc.isoformat().replace("+00:00", "Z")

    print(f"Requesting calendar events for {emails} from Google...")

    processed_data = {}
    local_tz = datetime.timezone(datetime.timedelta(hours=config.TIMEZONE_OFFSET_HOURS))

    for email in emails:
        try:
            events_result = (
                service.events()
                .list(calendarId=email, timeMin=time_min, timeMax=time_max, singleEvents=True, orderBy="startTime")
                .execute()
            )
            raw_events = events_result.get("items", [])
        except Exception as e:
            print(f"⚠️  Could not read the calendar for {email}: {e}")
            raw_events = []

        filtered_busy = []

        for event in raw_events:
            if event.get("status") == "cancelled":
                continue

            # Events marked "Free" do not block the interviewer
            if event.get("transparency") == "transparent":
                continue

            start_raw = event["start"].get("dateTime") or event["start"].get("date")
            end_raw = event["end"].get("dateTime") or event["end"].get("date")

            if not start_raw or not end_raw:
                continue

            try:
                if len(start_raw) == 10:  # all-day event
                    start_local = datetime.datetime.strptime(start_raw, "%Y-%m-%d").replace(tzinfo=local_tz)
                    end_local = datetime.datetime.strptime(end_raw, "%Y-%m-%d").replace(tzinfo=local_tz)
                else:
                    start_dt = datetime.datetime.fromisoformat(start_raw.replace("Z", "+00:00"))
                    end_dt = datetime.datetime.fromisoformat(end_raw.replace("Z", "+00:00"))
                    if start_dt.tzinfo is None:
                        start_dt = start_dt.replace(tzinfo=datetime.timezone.utc)
                    if end_dt.tzinfo is None:
                        end_dt = end_dt.replace(tzinfo=datetime.timezone.utc)
                    start_local = start_dt.astimezone(local_tz)
                    end_local = end_dt.astimezone(local_tz)
            except ValueError:
                continue

            filtered_busy.append(
                {
                    "start": start_local.strftime("%Y-%m-%dT%H:%M:%S"),
                    "end": end_local.strftime("%Y-%m-%dT%H:%M:%S"),
                    "timezone": f"GMT+{config.TIMEZONE_OFFSET_HOURS}",
                }
            )

        processed_data[email] = filtered_busy
        print(f"✓ {email}: found {len(filtered_busy)} busy block(s)")

    try:
        with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
            json.dump(processed_data, f, indent=4, ensure_ascii=False)
        print(f"✅ Done. Busy times converted to local time and saved to {OUTPUT_PATH}")
    except OSError as e:
        print(f"❌ Could not save the output file: {e}")

    return processed_data


if __name__ == "__main__":
    fetch_and_save_calendar_data(config.get_interviewer_emails())
