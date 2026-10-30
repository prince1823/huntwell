from __future__ import annotations

import os
import queue
import threading
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

from src.llm import Tailor
from src.models import ApplicationResult, Profile
from src.platforms.ashby import AshbyAdapter
from src.platforms.base import PlatformAdapter
from src.platforms.indeed import IndeedAdapter
from src.platforms.linkedin import LinkedInAdapter
from src.platforms.wellfound import WellfoundAdapter
from src.profile_overrides import apply_profile_overrides
from src.profile_paths import resolve_profile_paths
from src.resume_parser import parse_resume
from src.tracker import Tracker

ADAPTERS = {
    "linkedin": LinkedInAdapter,
    "wellfound": WellfoundAdapter,
    "ashby": AshbyAdapter,
    "indeed": IndeedAdapter,
}

RUN_PLATFORMS = ["linkedin", "wellfound", "ashby"]  # indeed excluded -- see README


def _load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def run_pipeline(
    resume_path: Path, min_fit_score: float, events: queue.Queue, profile_name: str | None = None
) -> None:
    """Runs the full pipeline for the UI: parse resume, then search+apply on
    all RUN_PLATFORMS in parallel threads, pushing progress events into the
    given queue.

    profile_name=None uses the original single-account layout (root-level
    auth_state/applications.db). A real name uses profiles/<name>/ instead,
    via the same resolve_profile_paths the CLI's --profile flag uses --
    this is what lets multiple real accounts each apply through their own
    identity in the same UI run, rather than all going through one account.
    Each named profile still needs its own prior one-time CLI login
    (--no-headless) before it can do anything here; nothing here can create
    or bypass that.
    """
    try:
        paths = resolve_profile_paths(profile_name)

        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            events.put({"type": "error", "message": "ANTHROPIC_API_KEY not set in .env"})
            events.put({"type": "done"})
            return

        events.put({"type": "step", "step": 1, "label": "Extracting information from resume"})
        profile = parse_resume(str(resume_path), api_key, cache_dir=paths.cache_dir)
        profile = apply_profile_overrides(profile_name, profile)
        events.put(
            {
                "type": "profile",
                "name": profile.full_name,
                "headline": profile.headline,
                "years_of_experience": profile.years_of_experience,
                "skills": profile.skills[:10],
            }
        )

        events.put({"type": "step", "step": 2, "label": "Scoring postings and tailoring answers with AI"})
        tailor = Tailor(api_key, cache_dir=paths.cache_dir)
        cfg = _load_config(paths.config_path)

        # Search titles come from THIS resume by default, not one static list
        # shared by every account -- so two different people's resumes
        # uploaded in the same run each get titles relevant to them. A
        # profile can still pin its own fixed list via
        # `titles_from_resume: false` in its search_config.yaml.
        if cfg.get("titles_from_resume", True):
            suggested_titles = tailor.suggest_search_titles(profile)
            if suggested_titles:
                cfg["titles"] = suggested_titles
                events.put({"type": "search_titles", "titles": suggested_titles})

        events.put({"type": "step", "step": 3, "label": "Applying on LinkedIn, Wellfound & Ashby"})
        threads = []
        for platform in RUN_PLATFORMS:
            if not cfg.get("platforms", {}).get(platform, False):
                events.put({"type": "platform_skipped", "platform": platform, "message": "disabled in config"})
                continue
            # Each thread opens its own Tracker/sqlite3 connection -- a
            # connection object can't be shared across threads (Python's
            # sqlite3 enforces this and raises ProgrammingError), which
            # silently killed every platform thread the first time this ran.
            t = threading.Thread(
                target=_run_one_platform,
                args=(platform, cfg, profile, tailor, min_fit_score, events, paths.state_dir, paths.db_path),
                daemon=True,
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()
    except Exception as e:  # noqa: BLE001 -- surface any crash to the UI instead of hanging it
        events.put({"type": "error", "message": str(e)})
    finally:
        events.put({"type": "done"})


def _run_one_platform(
    platform: str,
    config: dict,
    profile: Profile,
    tailor: Tailor,
    min_fit_score: float,
    events: queue.Queue,
    state_dir: Path,
    db_path: Path,
) -> None:
    adapter_cls = ADAPTERS[platform]
    state_file = state_dir / f"{platform}.json"
    daily_cap = config.get("max_applications_per_day", {}).get(
        platform, config.get("max_applications_per_platform_per_day", 20)
    )

    events.put({"type": "platform_start", "platform": platform})

    tracker = Tracker(str(db_path))
    already_today = tracker.count_applied_today(platform)
    if already_today >= daily_cap:
        events.put({"type": "platform_error", "platform": platform, "message": f"Daily cap ({daily_cap}) already reached"})
        events.put({"type": "platform_done", "platform": platform})
        tracker.close()
        return

    if not state_file.exists() and adapter_cls.requires_login:
        events.put(
            {
                "type": "platform_error",
                "platform": platform,
                "message": "No saved login session -- run the CLI once with --no-headless to log in first.",
            }
        )
        events.put({"type": "platform_done", "platform": platform})
        tracker.close()
        return

    try:
        with sync_playwright() as pw:
            browser = pw.chromium.launch(headless=True)
            context = browser.new_context(storage_state=str(state_file) if state_file.exists() else None)
            adapter: PlatformAdapter = adapter_cls(context, tailor, config)

            try:
                adapter.ensure_logged_in("", "", headless=True)
            except RuntimeError as e:
                events.put({"type": "platform_error", "platform": platform, "message": str(e)})
                browser.close()
                events.put({"type": "platform_done", "platform": platform})
                return
            if adapter_cls.requires_login:
                context.storage_state(path=str(state_file))

            postings = adapter.search_jobs(profile)
            events.put({"type": "platform_search", "platform": platform, "count": len(postings)})

            applied_count = already_today
            for job in postings:
                if applied_count >= daily_cap:
                    break
                if tracker.already_handled(job):
                    # Same visibility problem as the keyword-exclude case:
                    # re-running the same search finds mostly postings
                    # already recorded from earlier runs. Surface that
                    # distinctly (not lumped into generic "skipped") so it's
                    # clear *why* nothing new happened, instead of it
                    # looking like the pipeline silently did nothing.
                    events.put(
                        {
                            "type": "result",
                            "platform": platform,
                            "company": job.company,
                            "title": job.title,
                            "url": job.url,
                            "status": "duplicate",
                            "fit": None,
                            "jd": "",
                            "reason": "already processed in an earlier run",
                        }
                    )
                    continue
                excluded_kw = next(
                    (kw for kw in config.get("keywords_exclude", []) if kw.lower() in job.title.lower()),
                    None,
                )
                if excluded_kw:
                    # Emit a result even for keyword-filtered postings --
                    # silently `continue`-ing left the UI showing "found N,
                    # applying..." forever with no further events, which
                    # reads as stuck/broken even though the filtering is
                    # correct and intentional.
                    events.put(
                        {
                            "type": "result",
                            "platform": platform,
                            "company": job.company,
                            "title": job.title,
                            "url": job.url,
                            "status": "skipped",
                            "fit": None,
                            "jd": (job.description or "")[:400],
                            "reason": f"title matched excluded keyword '{excluded_kw}'",
                        }
                    )
                    continue

                fit = tailor.score_fit(profile, job)
                if fit < min_fit_score:
                    events.put(
                        {
                            "type": "result",
                            "platform": platform,
                            "company": job.company,
                            "title": job.title,
                            "url": job.url,
                            "status": "skipped",
                            "fit": fit,
                            "jd": (job.description or "")[:400],
                            "reason": "below fit threshold",
                        }
                    )
                    continue

                try:
                    result = adapter.apply(job, profile)
                except Exception as e:  # noqa: BLE001
                    result = ApplicationResult(job=job, status="failed", reason=str(e))

                tracker.record(result)
                if result.status == "applied":
                    applied_count += 1

                events.put(
                    {
                        "type": "result",
                        "platform": platform,
                        "company": job.company,
                        "title": job.title,
                        "url": job.url,
                        "status": result.status,
                        "fit": fit,
                        "jd": (job.description or "")[:400],
                        "reason": result.reason,
                    }
                )

            if adapter_cls.requires_login:
                context.storage_state(path=str(state_file))
            browser.close()
    except Exception as e:  # noqa: BLE001 -- one platform crashing shouldn't hang the UI
        events.put({"type": "platform_error", "platform": platform, "message": str(e)})
    finally:
        tracker.close()
        events.put({"type": "platform_done", "platform": platform})
