# Deployment Guide

How to run both apps locally and on Streamlit Community Cloud. Plan about 30-45 minutes the first time, mostly for Google Cloud setup.

**Requirements:** Python 3.9+, a Google account, a Gmail account for sending mail.

```bash
pip install -r requirements.txt
```

## 1. Create the Google Sheet

1. Create a spreadsheet named **HR Applicant Tracker** (or set `SHEET_NAME` in secrets).
2. Paste the 18 header names (the last is `score_calculation`) from the "Google Sheet contract" table in the README into row 1 of the first tab, in order.
3. On column A, add a data-validation dropdown containing the status values listed in the README.

## 2. Service account (Sheets access)

1. In Google Cloud Console, create a project and enable the **Google Sheets API** and **Google Drive API**.
2. Create a **service account**, then create a JSON key for it.
3. Share the spreadsheet with the service account's email address (Editor).
4. Locally, save the key as `creds.json` (git-ignored). On the cloud, paste its contents into the `gcp_service_account` secret.

## 3. Calendar access (OAuth)

1. In the same project, enable the **Google Calendar API**.
2. Configure the OAuth consent screen and create an **OAuth client ID** of type *Desktop app*. Download it as `credentials.json` (see `credentials.json.example` for the shape; git-ignored).
3. Each interviewer must make their calendar's free/busy details visible to the account that authorizes the app, or be that account.
4. Run once locally to sign in:

```bash
python calendar_busytime.py
```

   A browser opens for consent and `token.json` is written. For cloud deployment, paste the contents of `token.json` into the `google_calendar_token` secret.

## 4. Gmail and Gemini

- **Gmail:** turn on 2-step verification, then create an *App password* (Google Account > Security). Use it as `GMAIL_APP_PASSWORD` and the Gmail address as `SENDER_EMAIL`.
- **Gemini:** create an API key in Google AI Studio and use it as `GEMINI_API_KEY`.

## 5. Resume upload proxy (optional)

1. Create a Drive folder for resumes and copy its ID from the URL.
2. At script.google.com create a project, paste `apps_script/Code.gs`, and set `FOLDER_ID`.
3. **Deploy > New deployment > Web app**, execute as *Me*, access *Anyone*. Copy the `/exec` URL into `APPS_SCRIPT_URL`.

The URL is effectively a write key to that folder, so keep it only in secrets. If it ever leaks, create a new deployment and update the secret.

## 6. Secrets

Copy `secrets.example.toml` to `.streamlit/secrets.toml` and fill it in. Required: `GEMINI_API_KEY`, `SENDER_EMAIL`, `GMAIL_APP_PASSWORD`, `BOOKING_APP_URL`, `INTERVIEWER_EMAILS`, `gcp_service_account`, `google_calendar_token`.

**Never commit real secrets.** `.gitignore` excludes `.streamlit/`, `creds.json`, `credentials.json`, `token.json` and `.env`. If a key is ever committed, revoke it immediately: deleting it in a later commit does not remove it from git history.

## 7. Run locally

```bash
streamlit run app.py                                 # HR Command Center
streamlit run streamlit_app.py --server.port 8502    # candidate booking page
```

Suggested demo: in the HR app's first tab, load `sample_jd/python_backend_engineer_jd.pdf`, let the AI draft rules, then upload the PDFs in `sample_resumes/`. Approve a shortlisted candidate in the review tab, send emails from the routing tab, then open the invite link and book a slot.

## 7b. Public demo (recommended for portfolio links)

Add `DEMO_MODE = true` to the secrets of any publicly shared deployment. Calendars are simulated, emails are previewed instead of sent, and Drive uploads are skipped, so strangers cannot use your Gmail, calendar or Drive. You then only need `GEMINI_API_KEY`, `BOOKING_APP_URL` and `gcp_service_account` (pointing at a throwaway demo sheet). Put a spending cap on the Gemini key.

## 8. Deploy to Streamlit Community Cloud

1. Push the repo to GitHub.
2. At share.streamlit.io create **two apps** from the same repo: main file `app.py` (HR) and main file `streamlit_app.py` (booking).
3. Paste the same secrets into each app's *Settings > Secrets*. Set `BOOKING_APP_URL` to the booking app's public URL.

## Resetting between test runs

- **Calendar:** `python clear_calendar_events.py` and type `yes` to delete all `[Interview]` events in the next 90 days.
- **Sheet:** delete rows 2 and below.
- **Local state:** remove `bookings.json` and `job_profiles.json` if present.

## Troubleshooting

- **`503 UNAVAILABLE` from Gemini:** transient overload. Calls retry three times automatically; if it persists, wait a minute or switch API keys in Secrets.
- **Sheet connection fails:** confirm the spreadsheet is shared with the service account and the name matches `SHEET_NAME`.
- **No open slots / calendar error:** confirm the OAuth token is valid, `INTERVIEWER_EMAILS` are readable by the authorizing account, and at least two are configured.
