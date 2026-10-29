# Job Hunt Auto-Apply

Parses your resume into a structured profile, searches LinkedIn / Indeed / Wellfound
for matching openings, scores each posting's fit with Claude, and fills + submits
the application form automatically (LinkedIn Easy Apply, Indeed Apply, Wellfound Apply).

## Before you run this

- **This automates logins and form submission on sites whose Terms of Service
  prohibit it.** LinkedIn and Indeed in particular detect and penalize scripted
  activity, up to and including account suspension. Use your own accounts, keep
  `max_applications_per_platform_per_day` low (the config defaults to 20), and run
  with `--no-headless` the first time on each platform — you'll very likely need
  to clear a login checkpoint, CAPTCHA, or 2FA prompt by hand.
- Selectors for all three sites **will break** as those sites change their DOM.
  Treat this as a maintained-by-you starting point, not a finished product.
- Nothing here bypasses CAPTCHAs for you — if a platform challenges the login,
  solve it manually in the visible browser window; the session is then saved to
  `auth_state/<platform>.json` so you don't have to repeat it every run.

## Setup

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # then fill in your API key + site credentials
```

Edit `config/search_config.yaml` to set your target titles, locations, excluded
keywords, and per-platform daily caps.

## Usage

```bash
python main.py --resume /path/to/resume.pdf --no-headless
```

First run per platform: keep `--no-headless` so you can see and clear any login
challenge. Once `auth_state/<platform>.json` exists with a valid session, you can
switch to `--headless`.

Useful flags:

```bash
# Only run LinkedIn, skip the others
python main.py --resume resume.pdf --platforms linkedin

# Raise/lower how picky the fit-scoring is before it bothers applying (0-1)
python main.py --resume resume.pdf --min-fit-score 0.75
```

Every attempted posting (applied, skipped, failed, needs_review) is recorded in
`applications.db` (SQLite) so re-running never double-applies to the same job.
Check status breakdown:

```bash
sqlite3 applications.db "select platform, status, count(*) from applications group by 1,2;"
```

## How it works

1. `src/resume_parser.py` extracts text from your resume and asks Claude to
   structure it into a `Profile` (contact info, skills, experience, education).
2. `main.py` launches one Playwright browser context per platform, logs in
   (reusing a saved session when possible), and hands control to that
   platform's adapter in `src/platforms/`.
3. Each adapter searches for postings matching your configured titles/locations,
   then for each new one: Claude scores fit (`src/llm.py`), and if it clears
   `--min-fit-score`, the adapter fills the platform's apply form — short-answer
   screening questions and cover-letter boxes are answered by Claude from your
   resume data, falling back to `screening_defaults` in the config when the
   resume has no relevant info.
4. Every outcome is written to `src/tracker.py`'s SQLite database, which also
   enforces the daily per-platform application cap and de-dupes jobs across runs.

## Running multiple accounts (profiles)

Each account is a real person's own LinkedIn/Wellfound login — this does not
share or create logins across people. To onboard an account:

```bash
mkdir -p profiles/alice
cp alice_resume.pdf profiles/alice/resume.pdf

# One-time manual login per platform, same as the single-account flow --
# a visible browser opens, alice logs in herself, session gets saved.
python main.py --profile alice --resume profiles/alice/resume.pdf --no-headless
```

That creates `profiles/alice/auth_state/<platform>.json` (her saved session),
`profiles/alice/applications.db` (her own tracker), and
`profiles/alice/cache/` (her own cached LLM answers) — fully isolated from
every other profile and from the single-account layout. She can optionally
drop a `profiles/alice/search_config.yaml` (her own titles/locations) or
`profiles/alice/.env` (e.g. her own `ANTHROPIC_API_KEY`) to override the
shared defaults.

Once each account you want has done its one-time login, run them all
concurrently, headless, with `run_profiles.py`:

```bash
python run_profiles.py --concurrency 5
```

It discovers every `profiles/<name>/` directory, skips any that don't yet
have a saved session for a platform (printing the exact login command needed
instead of hanging on a login prompt nobody can see), and runs the rest in
parallel, each against both LinkedIn and Wellfound. Per-profile output goes
to `profiles/<name>/run.log`.

**This does not remove the one-time human login requirement per account** —
that's not a limitation of this tool specifically, it's LinkedIn's/Wellfound's
bot detection, which a real person clicking through their own login is what
gets past. Concurrency only applies to runs *after* that.

## Web UI

A simple upload-and-watch UI: pick a resume, and it live-streams the same
3-step pipeline the CLI runs (extracting resume info → scoring with AI →
applying on LinkedIn, Wellfound & Ashby in parallel), with each application
appearing in a results feed as it happens (company, role, fit score, status,
and the job description).

```bash
uvicorn webapp.server:app --reload
```

Then open `http://127.0.0.1:8000`. This is a **single-account UI** — it uses
the same root-level `auth_state/` and `applications.db` as running `main.py`
without `--profile`, so whichever account is already logged in there (via the
CLI's one-time `--no-headless` login) is the one the UI applies as. It does
not add multi-account support, a job queue, or auth of its own — upload
triggers a real run immediately, same caps and fit-scoring as the CLI.

## Known gaps / next steps

- `needs_review` results mean the adapter hit a form step it didn't recognize
  and bailed out rather than guessing — check these manually.
- Postings that redirect to an external ATS (common on Indeed, occasional on
  Wellfound) are marked `skipped`; a generic external-ATS filler isn't included.
- No resume/cover-letter file upload is wired up yet — forms that require
  attaching a PDF (rather than relying on your platform profile) will stall and
  land in `needs_review`.
