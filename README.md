# Huntwell

An AI agent that applies to jobs for you. Drop in a resume and it will:

1. **Read your resume** and turn it into a structured profile (skills, experience, headline).
2. **Search LinkedIn and Wellfound** for roles that match.
3. **Score every posting** for fit with AI, so it doesn't waste time on jobs you're clearly not a match for.
4. **Fill out and submit the application** — cover letters, screening questions, the works — using your resume data.
5. **Hand off anything it can't finish on its own** (a CAPTCHA, an unusual form) to a "Review & submit" queue so you just click once instead of filling the whole thing.

> **Ashby, Workable, and Rippling support is in the codebase but not exposed in the web app yet.** Their guest-apply forms hit CAPTCHAs often enough in practice that they're not reliable for a casual user — see **Known gaps** below. LinkedIn and Wellfound use a real logged-in account and have never hit one.

It has a **web app** (the easiest way to use it — a local site with live progress, a memory of your preferences, and a settings page) and a **command-line tool** (for running it unattended, on a schedule, or for multiple people's accounts at once).

> **Read "Before you use this" below before you point it at your real accounts.**

---

## Before you use this

- **This automates logins and form submission on sites whose Terms of Service may prohibit it.** LinkedIn in particular detects and penalizes scripted activity, up to and including account suspension. Use your own account, keep the daily application caps reasonable, and expect to clear a login/CAPTCHA/2FA prompt by hand the first time you use each platform.
- Nothing here bypasses CAPTCHAs for you — when a platform challenges the login or a form, you solve it yourself (in a visible browser window, or via the Review & submit queue); the session is then saved so you don't have to repeat it.
- Website layouts change. Selectors **will** occasionally break as LinkedIn/Ashby/etc. redesign their pages. Treat this as a tool you maintain, not a finished, bulletproof product.
- You need your own AI API key (OpenAI-compatible) — see **Getting an API key** below. Nothing here works without one, and token usage costs real money (a small amount per run; see your provider's pricing).

---

## What you need before you start

- **A Mac, Linux, or Windows computer** with [Python 3.10+](https://www.python.org/downloads/) installed. ([Node.js 18+](https://nodejs.org/) too, only if you plan to modify the web app's UI — see step 3.)
- **Git**, to download (clone) the project.
- *(Optional, but easiest)* **[Docker](https://www.docker.com/)**, if you'd rather skip installing Python/Node entirely — see **Deploying with Docker** below.
- **An API key** from [platform.openai.com](https://platform.openai.com/api-keys) (or any OpenAI-compatible provider) — this is what pays for the AI that reads your resume, scores jobs, and writes your answers.
- Your own LinkedIn and/or Wellfound login (a real account — there's no "guest apply" for either).

---

## 1. Clone the project

```bash
git clone https://github.com/prince1823/huntwell.git
cd huntwell
```

## 2. Install the backend (Python)

```bash
python3 -m venv .venv
source .venv/bin/activate          # Windows: .venv\Scripts\activate
pip install -r requirements.txt
playwright install chromium        # downloads the browser the agent drives
```

## 3. Install the web app (frontend)

The web app's interface is pre-built and already checked into the repo (`webapp/static/`), so **you can skip this step** unless you want to modify the UI yourself. If you do:

```bash
cd webapp/frontend
npm install
npm run build                      # rebuilds webapp/static/ from source
cd ../..
```

## 4. Add your API key

You can either add it to a `.env` file now, or skip this and add it later from inside the web app's Settings tab (see below) — your choice.

```bash
cp .env.example .env
```

Open `.env` and fill in `ANTHROPIC_API_KEY` with your API key (despite the name, it's an OpenAI-compatible key — see **Getting an API key** below).

---

## Getting an API key

1. Go to [platform.openai.com/api-keys](https://platform.openai.com/api-keys) and sign up / log in.
2. Create a new API key and copy it (it looks like `sk-...`).
3. Add a small amount of credit to your account (a full run against a few hundred postings typically costs well under a dollar with the default model).
4. Paste the key into `.env` (step 4 above) **or** into the web app's Settings tab after you start it — the Settings tab's key always wins if both are set, so you can bring your own key without ever touching `.env`.

---

## Running the web app

```bash
uvicorn webapp.server:app --reload
```

Then open **http://127.0.0.1:8000** in your browser. That's it — the page you land on is a full app, not a plain form:

| Tab | What it does |
|---|---|
| **Agent** | Drop in a resume (yours, or someone else's — see Accounts below), set a minimum fit score, and watch it search and apply live on LinkedIn and Wellfound. |
| **Review** | Mostly relevant to the in-development platforms (see **Known gaps**) — anything one of them filled but couldn't safely submit itself lands here for a one-click finish. LinkedIn/Wellfound apply directly and rarely need this. |
| **Applied** | Every application it's made, with stats (submitted, successful, needing you, time saved) and filters. |
| **Tracker** | A breakdown of outcomes by status and by platform. |
| **Profile** | Its "memory" of you — preferences and facts (e.g. *"only FinTech roles, fully remote"*) that it factors into every job it scores and every answer it writes. Type new ones in anytime, or click "Fill from resume" to seed it straight from your resume. |
| **Settings** | Turn LinkedIn/Wellfound on or off, set your API key (BYOK), choose the AI model, and set a daily application cap — all without editing any config files. |

**The first time you use a platform that needs a real login (LinkedIn, Wellfound):** the agent needs to see you log in once. Run the one-time CLI login for that platform (see **First-time login per platform** below) before using the web app — the web app itself always runs headless (no visible browser window), so it can't show you a login screen itself.

---

## Deploying with Docker (recommended for self-hosting)

If you'd rather not install Python/Node yourself, or you want to run this on a server/VPS instead of your laptop, Docker packages everything (including Playwright's browser) into one command:

```bash
cp .env.example .env    # can stay blank -- add your key from Settings instead if you prefer
docker compose up --build
```

Open **http://localhost:8000** (or `http://<your-server-ip>:8000` if it's running on a remote machine). The container stores everything (logins, application history, memory, settings) in the project folder on your host via a bind mount, so stopping/rebuilding the container never loses data, and a fresh `git pull` + `docker compose up --build` picks up updates without touching it.

**One real caveat:** the container always runs headless, so it can't show you the visible browser window the one-time LinkedIn/Wellfound login needs (see the next section). Do that one-time login *outside* Docker first — on your own machine, with a normal Python install — then start the container; it'll pick up the saved `auth_state/` automatically since it's the same project folder.

---

## First-time login per platform

LinkedIn and Wellfound need a real, logged-in account — there's no way around this (and no way to automate past their own bot-detection login screen). Do this once per platform, from the command line, with a visible browser window:

```bash
python main.py --resume /path/to/your_resume.pdf --no-headless
```

A real Chrome window opens. Log in by hand (clear any CAPTCHA/2FA yourself), and once you're in, the session is saved to `auth_state/<platform>.json`. From then on, both the CLI and the web app reuse that saved session — no more logins needed, until it eventually expires and you repeat this once more.

---

## How it actually works

1. **`src/resume_parser.py`** pulls text out of your resume (PDF/DOCX/TXT) and asks the AI to structure it into a `Profile` — contact info, skills, experience, education.
2. For each enabled platform, an adapter in **`src/platforms/`** searches for postings matching your target titles/locations (generated from your resume, or pinned in config).
3. For each new posting, **`src/llm.py`** scores how well it fits your profile (and anything you've told the Profile tab's memory agent) and skips it if it's below your minimum fit score.
4. If it's a good fit, the adapter fills out the real application form — cover letters, screening questions, the lot — using your resume data and memory, and submits it. Anything it can't confidently finish (an unrecognized form step, a CAPTCHA) goes to the **Review & submit** queue instead of guessing.
5. Every outcome (applied / needs review / skipped / failed) is recorded in a local SQLite database, so re-running never double-applies to the same job, and enforces your daily per-platform cap.

### The memory agent

The Profile tab's flowgraph isn't just decoration — every fact you add there (a note you type, or one pulled from your resume) is woven into the actual AI prompts used for scoring jobs and writing your answers. Tell it "I only want remote roles" or "target ₹25 LPA+" once, and it factors that into everything from then on.

### Settings & BYOK

The Settings tab's choices (which platforms are on, your API key, your model, your daily cap) are saved locally and layered on top of the underlying config — they don't rewrite or lose any of your hand-tuned `search_config.yaml` settings, and a Settings tab you never open changes nothing.

---

## Using the command line instead

Everything the web app does, the CLI can also do — useful for scripting, cron jobs, or if you just prefer a terminal:

```bash
python main.py --resume /path/to/resume.pdf --no-headless
```

Useful flags:

```bash
# Only run LinkedIn, skip the others
python main.py --resume resume.pdf --platforms linkedin

# Raise/lower how picky the fit-scoring is before it bothers applying (0-1)
python main.py --resume resume.pdf --min-fit-score 0.75

# Run fully in the background once your logins are saved
python main.py --resume resume.pdf --headless
```

Check what it's done so far:

```bash
sqlite3 applications.db "select platform, status, count(*) from applications group by 1,2;"
```

Your search criteria (target titles, locations, excluded keywords, daily caps, per-platform settings) live in `config/search_config.yaml` — open it and edit directly for full control; it's thoroughly commented.

---

## Running it for multiple people (profiles)

Each "profile" is a completely separate account — its own login sessions, its own application history, its own memory. Nothing is shared or copied between people.

```bash
mkdir -p profiles/alice
cp alice_resume.pdf profiles/alice/resume.pdf

# One-time manual login per platform, same idea as the single-account flow --
# a visible browser opens, Alice logs in herself, the session gets saved.
python main.py --profile alice --resume profiles/alice/resume.pdf --no-headless
```

That creates `profiles/alice/auth_state/` (her saved login), `profiles/alice/applications.db` (her own tracker), `profiles/alice/memory.json` (her own preferences), and `profiles/alice/cache/` (her own cached AI answers) — fully isolated from every other profile and from the default account. She can optionally add her own `profiles/alice/search_config.yaml` or `profiles/alice/.env` to override the shared defaults.

Once every account you want has done its one-time login, run them all at once, headless:

```bash
python run_profiles.py --concurrency 5
```

This finds every `profiles/<name>/` folder, skips any missing a saved login (printing the exact command to fix it, instead of hanging), and runs the rest in parallel. Per-profile output goes to `profiles/<name>/run.log`.

**This does not remove the one-time human login** — that's LinkedIn's/Wellfound's own bot detection, not a limitation of this tool. Concurrency only speeds up everything *after* that one-time login.

You can also upload a resume for a named profile straight from the web app's Agent tab, by typing the profile name into the "Account" field next to the file picker.

---

## Known gaps

- **Ashby, Workable, and Rippling are in development, not ready for general use.** The adapters exist (`src/platforms/ashby.py`, `workable.py`, `rippling.py`) and still work from the CLI (`python main.py --platforms ashby`), but their guest-apply forms hit CAPTCHAs often enough in practice that they're turned off in the web app by default — `RUN_PLATFORMS` in `webapp/runner.py` only includes LinkedIn and Wellfound right now. If you want to help get them reliable, that's the place to start.
- **`needs_review` results** mean the adapter hit a form step it didn't recognize (or a CAPTCHA) and stopped rather than guessing — these land in the Review & submit queue for a quick human click.
- **Postings that redirect to an external ATS** (occasional on LinkedIn/Wellfound) are marked `skipped`; there's no generic external-site filler.
- **No resume/cover-letter file upload is wired up** yet for forms that specifically require attaching a PDF (rather than text fields) — these will stall and land in `needs_review`.
- **LinkedIn's apply flow can still be flaky** since LinkedIn's UI changes frequently and has no public API to fall back on — expect it to need the Review queue more often than Wellfound does.

---

## Project structure, at a glance

```
main.py                 CLI entry point
run_profiles.py          Run multiple accounts concurrently
config/search_config.yaml   Your search criteria & per-platform settings
src/
  resume_parser.py       Resume -> structured Profile
  llm.py                 Scoring, cover letters, screening answers (the "Tailor")
  memory.py               The Profile tab's memory agent
  app_settings.py         BYOK API key / model / platform toggles
  platforms/              One adapter per job board
  tracker.py               SQLite application history
webapp/
  server.py               FastAPI backend behind the web app
  runner.py                Runs the pipeline for one web-app request
  frontend/                The React web app's source (builds into webapp/static/)
Dockerfile, docker-compose.yml   Self-hosted deployment -- see "Deploying with Docker" above
```
