# AI Recruiting Automation System and HR Command Center

An end-to-end recruiting workflow built as a team class project (Python, Streamlit, Gemini, Google Sheets and Calendar): AI resume screening, a hiring-manager review step, rule-based candidate emails, self-service interview booking with Google Meet, and a recruiting analytics dashboard.

> Class project built with fictional data. "Acme Technology Ltd." is an invented company, and every sample resume in `sample_resumes/` describes an invented person.

## Two apps, one workflow

| App | File | Audience | What it does |
|---|---|---|---|
| HR Command Center | `app.py` | Recruiters and hiring managers | AI resume parsing, manager review board, email routing state machine, analytics dashboard |
| Interview Booking | `streamlit_app.py` | Candidates | Reads the interviewers' Google Calendars, offers open 1-hour slots (15-minute buffer), prevents double booking, creates a Google Meet event, sends a confirmation email |

Google Sheets is the shared state store between them: each candidate is one row, and the `status` column drives the whole pipeline.

## Why it exists

Hiring funnels are a core People Analytics subject: where candidates drop out, whether salary expectations match the budget, and which skills carry a price premium. This project puts salary budget and required technical skills into the AI scoring rubric, timestamps each stage (`applied_at`, `action_at`), and turns the sheet into a funnel dashboard so bottlenecks are visible (for example, strong candidates lost to a salary range set too low).

## How it works

```text
New resume (PDF) -> Gemini scores it (baseline 70, rule-based +/-) -> score routing
  score >= 60 -> [AI Scored - Pending Manager Review] -> manager ticks the box
                    - approved   -> [Interview Invite Sent - No Reply] -> candidate books a slot
                                      -> [Interview Scheduled] -> HR sets [Hired] manually
                    - declined   -> [Rejection Email Sent]   (a comment explaining why is required)
  score <  60 -> [AI Scored - Pending Rejection Email] -> after 48 hours -> [Rejection Email Sent]
```

If a hired candidate declines or takes another offer, HR can set `Offer Declined` by hand.

Details:

- **Scoring.** The prompt starts every candidate at 70, applies the job's deduction/bonus rules, and must write out the arithmetic in `score_calculation` before giving the final score. A salary expectation above the JD budget is a mandatory deduction.
- **Rules from the JD.** One click drafts 7-10 scoring rules from the uploaded JD; the recruiter edits them and saves the JD plus rules as a reusable job profile.
- **Status is set by code, not by the model.** The model suggests a status, but `app.py` computes it from the score so the state machine cannot be corrupted by a malformed response.
- **Duplicate guard.** A (job, email) pair already in the sheet is not written twice.
- **Resume storage.** PDFs are uploaded to Drive through a small Apps Script proxy (`apps_script/Code.gs`), because service accounts have no Drive quota of their own.
- **Interview slots.** Free time is the complement of all interviewers' busy blocks over the next 21 days, on weekdays within 09:00-12:00 and 13:00-18:00, shown on the hour and half hour.
- **Action timestamps.** `action_at` is updated whenever the pipeline acts: AI scoring, an email sent, a candidate booking.

## Analytics dashboard

Filter by job and time window, then see: funnel KPIs (applicants, reached interview, hired), applicants and interview conversion by years of experience, why candidates drop out (manager comment first, otherwise a keyword classifier over the AI rationale), AI score vs. expected salary, and a skill-premium chart (how many candidates have each skill vs. their average expected salary).

## Project layout

```text
.
├── app.py                      # HR Command Center (4 tabs)
├── streamlit_app.py            # Candidate interview booking page
├── config.py                   # Settings, branding, and the status-machine constants
├── calendar_busytime.py        # Reads interviewers' busy blocks from Google Calendar
├── calendar_sync.py            # Creates the Calendar event + Google Meet link
├── clear_calendar_events.py    # Test helper: bulk-delete "[Interview]" events
├── apps_script/Code.gs         # Drive upload proxy (deploy as an Apps Script web app)
├── tools/generate_samples.py   # Regenerates the sample JD / resume PDFs
├── sample_jd/                  # Two example job descriptions
├── sample_resumes/             # Six fictional candidate resumes
├── jd.txt                      # Plain-text JD for quick testing
├── secrets.example.toml        # Every secret / setting the apps read
├── credentials.json.example    # Placeholder for the OAuth client file
├── requirements.txt
└── DEPLOYMENT_GUIDE.md
```

## Run it

1. Follow [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) to create the Google Sheet, service account, OAuth token, Gmail app password and Gemini key.
2. Copy `secrets.example.toml` to `.streamlit/secrets.toml` and fill it in.
3. Install and run (two terminals):

```bash
pip install -r requirements.txt
streamlit run app.py                                  # HR Command Center, port 8501
streamlit run streamlit_app.py --server.port 8502     # candidate booking page
```

Live demo: _add link here after deploying to Streamlit Community Cloud._

### Demo mode

For a public deployment, set `DEMO_MODE = true` in the app's secrets. In this mode:

- The interviewers' calendars are **simulated** (a fixed weekly pattern of meetings), so no real Google Calendar is read or written.
- **No emails are sent.** Rejection, invitation and confirmation emails are shown on screen as previews.
- Resumes are scored but **not uploaded to Drive**.
- The Google Sheet is still real, so use a dedicated demo sheet: visitors write to it. Use only a Gemini key you can afford to expose to public use, and set a usage cap.

To try the booking flow in the demo, score a sample resume, tick "Approve Interview" in the review tab, send emails from the routing tab, then open the booking page with that candidate's exact name and email (the invitation preview contains the link).

## Google Sheet contract

The apps read and write columns by position, so the header row must be exactly:

| A | B | C | D | E | F | G | H | I | J | K | L | M | N | O | P | Q |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status | job_applied | name | email | score | manager_approved | manager_comment | applied_at | action_at | interview_at | meeting_link | highest_education | total_experience_years | expected_salary | skills_extracted | score_reason | resume |

The `status` values are defined in `config.py` and must match the sheet exactly (use a data-validation dropdown on column A):

`AI Scored - Pending Manager Review`, `AI Scored - Pending Rejection Email`, `Rejection Email Sent`, `Interview Invite Sent - No Reply`, `Interview Invite Sent`, `Interview Scheduled`, `Hired`, `Offer Declined`

## Limitations

This is a class project, not a production hiring system.

- **AI scoring is a screening aid, not a decision.** Scores come from a language model reading resumes and have not been validated for accuracy or tested for bias. Candidates scoring below 60 are rejected automatically after 48 hours with no human review; a production system should add human review there too and be audited for adverse impact.
- **Only shortlisted candidates get a manager review** (score >= 60). The below-threshold path is fully automatic.
- **Retries are minimal.** AI calls retry up to three times with backoff on errors such as `503 UNAVAILABLE`; there is no queueing or rate limiting.
- **Concurrency.** Google Sheets is the database and `bookings.json` is a local file, so two simultaneous bookings can race. Slot protection is best-effort, and the file is lost when a free cloud host restarts.
- **Positional columns.** Reading the sheet by column index is brittle if columns are reordered.
- **The dashboard's rejection-reason classifier is keyword matching** on the AI's free-text rationale. Treat the chart as indicative.
- **Fixed schedule and timezone.** Business hours, 1-hour interviews and Taipei time (GMT+8) are constants in the code.
- **No automated tests.**

## Team

Built by a student team for a "Programming for Business" course.

| Name | Role |
|---|---|
| _Name_ | _e.g. AI scoring and prompt design_ |
| _Name_ | _e.g. Google Calendar and booking flow_ |
| _Name_ | _e.g. dashboard and analytics_ |

## License

No license is specified yet; all rights reserved by the authors until one is added.
