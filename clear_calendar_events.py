"""
Google Calendar cleanup tool (for testing only)

Searches for, and bulk-deletes, every event on the primary calendar whose title contains
the interview tag ("[Interview]"). Handy after repeated demo runs: no need to delete
test events one by one.
"""

import os
from datetime import datetime, timedelta, timezone

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

import config

# ========== Google API settings ==========
SCOPES = ["https://www.googleapis.com/auth/calendar"]
CLIENT_SECRETS_FILE = "credentials.json"  # keep next to this script
TOKEN_PATH = "token.json"


def get_calendar_service():
    creds = None

    if os.path.exists(TOKEN_PATH):
        creds = Credentials.from_authorized_user_file(TOKEN_PATH, SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(CLIENT_SECRETS_FILE, SCOPES)
            creds = flow.run_local_server(port=0)

        with open(TOKEN_PATH, "w") as token:
            token.write(creds.to_json())

    return build("calendar", "v3", credentials=creds)


def clear_interview_events():
    try:
        service = get_calendar_service()

        # Step 1: time window (next 90 days)
        now = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
        future = (datetime.now(timezone.utc) + timedelta(days=90)).isoformat().replace("+00:00", "Z")

        print("=" * 70)
        print("🔍 Google Calendar interview-event cleanup tool")
        print("=" * 70)
        print("\nSearching for interview events in the next 90 days...")

        # Step 2: query. `q` matches free text in the event, so we also filter on the title below.
        events_result = (
            service.events()
            .list(
                calendarId="primary",
                timeMin=now,
                timeMax=future,
                singleEvents=True,
                q=config.INTERVIEW_TAG,
                orderBy="startTime",
            )
            .execute()
        )

        events = [e for e in events_result.get("items", []) if config.INTERVIEW_TAG in e.get("summary", "")]

        # Step 3: show results
        if not events:
            print("✅ No interview events found. Your calendar is clean!\n")
            return

        print(f"✅ Found {len(events)} matching interview event(s)\n")

        # Step 4: list what will be deleted
        print("=" * 70)
        print("Events to delete:")
        print("=" * 70)

        for i, event in enumerate(events, 1):
            start = event["start"].get("dateTime", event["start"].get("date"))
            print(f"{i}. {event['summary']}")
            print(f"   Time: {start}")
            if "description" in event:
                print(f"   Description: {event['description'][:50]}...")
            print()

        # Step 5: confirm
        print("=" * 70)
        confirm = input("Delete all of the events above? (type 'yes' to confirm, anything else cancels): ").strip().lower()

        if confirm != "yes":
            print("\n❌ Cancelled. No calendar events were changed.\n")
            return

        # Step 6: delete one by one
        print("\n🗑️  Deleting from the cloud...\n")
        deleted_count = 0

        for event in events:
            try:
                service.events().delete(calendarId="primary", eventId=event["id"]).execute()
                deleted_count += 1
                print(f"   ✓ Deleted: {event['summary']}")
            except Exception as e:
                print(f"   ❌ Could not delete: {event['summary']} - reason: {e}")

        # Step 7: summary
        print(f"\n🎉 Done! Removed {deleted_count}/{len(events)} test interview event(s).")
        print("=" * 70)

    except Exception as e:
        print(f"❌ Error: {e}")


if __name__ == "__main__":
    clear_interview_events()
