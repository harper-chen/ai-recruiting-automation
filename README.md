# AI Recruiting Automation System

AI resume screening, hiring-manager review, candidate emails and self-service interview booking, with a recruiting analytics dashboard. Built with Python, Streamlit, Gemini and Google Sheets.

**Live demo:** [HR Command Center](https://YOUR-HR-APP-NAME.streamlit.app/) · [Interview Booking](https://harper-interview-booking.streamlit.app/)

> The demo apps sleep when idle. If you see a "gone to sleep" page, press the wake-up button and wait about 30 seconds.

<!--
Add screenshots here after uploading them to docs/images/, for example:
![Manager review board](docs/images/manager-review.png)
![Analytics dashboard](docs/images/dashboard.png)
-->

> Class project built with fictional data. "Acme Technology Ltd." is an invented company, and every sample resume in `sample_resumes/` describes an invented person.

## What it does

- **Scores resumes with AI.** Upload a job description, let the AI draft scoring rules, then score a batch of PDF resumes against them. Every candidate starts at 70 points; the AI lists which rules applied and by how many points, and the code does the arithmetic.
- **Lets you test the rules first.** A "Test these rules" dry run scores sample resumes without saving anything, and basic checks warn about rules with no point values or rules too lenient to ever reject anyone.
- **Routes by score.** 60 or above goes to a hiring manager. Below 60 is queued for an automatic rejection email after 48 hours.
- **Keeps a human in the loop.** Managers approve or decline shortlisted candidates, and can open the auto-rejected list, read the AI's rationale and point-by-point score arithmetic in the table, and move anyone back to review before the rejection email goes out.
- **Sends the emails.** Interview invitations, rejection notes and booking confirmations are driven by a status column in Google Sheets.
- **Books the interview.** Candidates pick from the interviewers' shared free time, a Google Meet room is created, and double booking is prevented.
- **Shows where candidates drop out.** A dashboard covers the funnel, conversion by experience, rejection reasons, score vs. salary expectation and skill premium.

## Why it exists

Hiring funnels are a core People Analytics subject: where candidates drop out, whether salary expectations match the budget, and which skills carry a price premium. This project puts salary budget and required skills into the AI scoring rubric, timestamps each stage, and turns the Google Sheet into a funnel dashboard so bottlenecks are visible (for example, strong candidates lost to a salary range set too low).

## Two apps, one workflow

| App | File | Audience | What it does |
|---|---|---|---|
| HR Command Center | `app.py` | Recruiters and hiring managers | AI resume parsing, manager review board, email routing state machine, analytics dashboard |
| Interview Booking | `streamlit_app.py` | Candidates | Reads the interviewers' Google Calendars, offers open 1-hour slots (15-minute buffer), prevents double booking, creates a Google Meet event, sends a confirmation email |

Google Sheets is the shared state store between them: each candidate is one row, and the `status` column drives the pipeline.

## How it works

```text
New resume (PDF) -> Gemini lists applied rules + points -> code computes 70 + sum -> score routing
  score >= 60 -> [AI Scored - Pending Manager Review] -> manager ticks the box
                    - approved   -> [Interview Invite Sent - No Reply] -> candidate books a slot
                                      -> [Interview Scheduled] -> HR sets [Hired] manually
                    - declined   -> [Rejection Email Sent]   (a comment explaining why is required)
  score <  60 -> [AI Scored - Pending Rejection Email] -> after 48 hours -> [Rejection Email Sent]
                    - manager moves the candidate back before then -> [AI Scored - Pending Manager Review]
```

If a hired candidate declines or takes another offer, HR can set `Offer Declined` by hand.

- **Status drives everything.** Routing and emails depend on the `status` value, not on the score, so a candidate the manager moves back from the auto-rejected list follows the manager's decision.
- **Score and status are computed by code, not by the model.** The model only reports which rules applied and their signed points (`adjustments`). `scoring.py` sums them onto the baseline, clamps to 0-100 and stores the arithmetic in `score_calculation`; `app.py` derives the status from the score. A model arithmetic slip or a malformed response cannot corrupt the state machine, and an unusable response is rejected instead of written.
- **Duplicate guard.** A (job, email) pair already in the sheet is not written twice.
- **Interview slots.** Free time is the complement of all interviewers' busy blocks over the next 21 days, on weekdays within 09:00-12:00 and 13:00-18:00, shown on the hour and half hour.
- **Scoring scale lives in one place.** The baseline, threshold, delay and suggested point ranges are constants in `config.py`, and both the UI and the AI prompts read them.

## Try the demo

1. Open the **HR Command Center**. A sample "Python Backend Engineer" job is already loaded.
2. In **AI Resume Parsing**, open *Test these rules on the sample resumes* to dry-run the rules (nothing is saved), then upload the PDFs from `sample_resumes/` and press *Start AI evaluation*.
3. In **Manager Review**, click a cell in the *AI Rationale* or *Score arithmetic* column to read it in full, approve a candidate, decline another with a comment, and open the auto-rejected list to move someone back.
4. In **Email Routing**, run the state machine. Emails are previewed on screen (never sent). Tick "skip the wait" to include the automatic rejections.
5. Click the booking link in the invitation preview, choose a slot and confirm.
6. In **Analytics Dashboard**, sync and explore the charts.

In demo mode no email is sent, no calendar event is created, and uploaded files are not stored. Saved job profiles live only in your own browser session. Candidates you upload are tagged with a private code (for example `ariel.lin+k3f9@example.com`, kept in the page URL), so many visitors can score the same sample resumes at the same time and each sees only their own in Manager Review and Email Routing. Visitor rows are deleted after 24 hours. The Analytics tab also shows about 40 fictional sample candidates, and a *Demo tools* panel lets a visitor clear their own candidates (the owner can reset the whole demo with an `ADMIN_KEY`). Identical resume + rules combinations reuse the earlier AI answer, which keeps the free Gemini quota for visitors who change something.

## Run it yourself

1. Follow [DEPLOYMENT_GUIDE.md](DEPLOYMENT_GUIDE.md) to create the Google Sheet, service account, OAuth token, Gmail app password and Gemini key. Set `DEMO_MODE = true` to skip the Gmail and Calendar parts.
2. Copy `secrets.example.toml` to `.streamlit/secrets.toml` and fill it in.
3. Install and run (two terminals):

```bash
pip install -r requirements.txt
streamlit run app.py                                  # HR Command Center, port 8501
streamlit run streamlit_app.py --server.port 8502     # candidate booking page
```

## Limitations

This is a class project, not a production hiring system.

- **AI scoring is a screening aid, not a decision.** Scores come from a language model reading resumes and have not been validated for accuracy or tested for bias. Managers can review and rescue auto-rejected candidates, but only until the rejection email is sent; a production system should add a mandatory human review and be audited for adverse impact.
- **Scores are not perfectly repeatable.** Scoring the same resume twice can differ by a few points, so candidates near the threshold may land on either side of it.
- **Rule quality matters.** The AI drafts the scoring rules, but a recruiter must read and tune them. Strict rules (such as requiring specific degree majors) can screen out capable candidates.
- **The public demo is shared infrastructure.** All visitors use one Google Sheet and one free-tier Gemini key (a small daily request limit), so the demo can show a "quota used up" message at busy times. Its analytics include other visitors' recent uploads.
- **Retries are minimal.** AI calls retry up to three times with backoff on errors such as `503 UNAVAILABLE`; there is no queueing or rate limiting.
- **Concurrency.** Google Sheets is the database and `bookings.json` is a local file, so two simultaneous bookings can race. Slot protection is best-effort, and the file is lost when a free cloud host restarts.
- **Positional columns.** Reading the sheet by column index is brittle if columns are reordered.
- **The rejection-reason chart uses keyword matching** on the AI's free-text rationale. Treat it as indicative.
- **Fixed schedule and timezone.** Business hours, 1-hour interviews and Taipei time (GMT+8) are constants in the code.
- **Light test coverage.** Unit tests cover the scoring arithmetic, rule checks and demo helpers (`python -m unittest`); the Streamlit UI, Sheets, Calendar and email paths are tested by hand only.

## Team

Built by a student team for a "Programming for Business" course.

| Name | Role |
|---|---|
| Te-Chi Chen (team lead) | HR recruiting command center web app design; team code integration |
| Qi Ding | Interview scheduling system web app design |
| Yi-Xuan Yang | Interviewer availability (free-slot) algorithm design |
| Rui-Yun Zhuang | Plotly chart design |

<details>
<summary>Google Sheet contract (for self-hosting)</summary>

The apps read and write columns by position, so the header row must be exactly:

| A | B | C | D | E | F | G | H | I | J | K | L | M | N | O | P | Q | R |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| status | job_applied | name | email | score | manager_approved | manager_comment | applied_at | action_at | interview_at | meeting_link | highest_education | total_experience_years | expected_salary | skills_extracted | score_reason | resume | score_calculation |

The `status` values are defined in `config.py` and must match the sheet exactly (use a data-validation dropdown on column A):

`AI Scored - Pending Manager Review`, `AI Scored - Pending Rejection Email`, `Rejection Email Sent`, `Interview Invite Sent - No Reply`, `Interview Invite Sent`, `Interview Scheduled`, `Hired`, `Offer Declined`

</details>

<details>
<summary>Project layout</summary>

```text
.
├── app.py                      # HR Command Center (4 tabs)
├── streamlit_app.py            # Candidate interview booking page
├── demo_support.py             # Demo-only helpers: visitor tags, sample candidates, cleanup plan
├── scoring.py                  # Pure score arithmetic + rule checks (unit tested)
├── tests/                      # Unit tests for scoring.py and demo_support.py
├── config.py                   # Settings, scoring scale and the status-machine constants
├── calendar_busytime.py        # Reads interviewers' busy blocks (or simulates them in demo mode)
├── calendar_sync.py            # Creates the Calendar event + Google Meet link
├── clear_calendar_events.py    # Test helper: bulk-delete "[Interview]" events
├── apps_script/Code.gs         # Drive upload proxy (deploy as an Apps Script web app)
├── tools/generate_samples.py   # Regenerates the sample JD / resume PDFs
├── sample_jd/                  # Example job descriptions and the sample scoring rules
├── sample_resumes/             # Six fictional candidate resumes
├── jd.txt                      # Plain-text JD, preloaded as the demo's sample job
├── secrets.example.toml        # Every secret / setting the apps read
├── credentials.json.example    # Placeholder for the OAuth client file
├── requirements.txt
└── DEPLOYMENT_GUIDE.md
```

</details>

## License

No license is specified yet; all rights reserved by the authors until one is added.
