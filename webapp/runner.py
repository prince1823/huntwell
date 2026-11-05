from __future__ import annotations

import queue
import threading
import traceback
from pathlib import Path

import yaml
from playwright.sync_api import sync_playwright

from src.app_settings import max_applications_override, platform_overrides, resolve_api_key, resolve_model
from src.llm import Tailor
from src.memory import add_fact, as_prompt_block, load_facts
from src.models import ApplicationResult, Profile
from src.platforms.ashby import AshbyAdapter
from src.platforms.indeed import IndeedAdapter
from src.platforms.linkedin import LinkedInAdapter
from src.platforms.rippling import RipplingAdapter
from src.platforms.wellfound import WellfoundAdapter
from src.platforms.workable import WorkableAdapter
from src.profile_overrides import apply_profile_overrides
from src.profile_paths import resolve_profile_paths
from src.resume_parser import parse_resume
from src.tracker import Tracker

# These are guest-apply/CAPTCHA-prone platforms where a successfully
# filled form stops short of Submit and goes to the human-in-the-loop
# queue instead (see src/platforms/base.py's _queue_for_review) --
# LinkedIn and Wellfound use a real account and have never hit a CAPTCHA,
# so they keep auto-submitting as before.
HITL_PLATFORMS = {"ashby", "workable", "rippling"}

ADAPTERS = {
    "linkedin": LinkedInAdapter,
    "wellfound": WellfoundAdapter,
    "ashby": AshbyAdapter,
    "indeed": IndeedAdapter,
    "workable": WorkableAdapter,
    "rippling": RipplingAdapter,
}

# Each platform runs in its own thread, and every one of them starts its own
# Playwright driver + browser subprocess. Confirmed live (2026-11-01): firing
# all of those subprocess spawns at once under uvicorn's uvloop event loop
# crashed with "RuntimeError: Racing with another loop to spawn a process"
# -- uvloop's own subprocess-exec path isn't safe to call concurrently from
# several threads at once, each running sync_playwright()'s own event loop.
# This only serializes the two moments that actually spawn a subprocess
# (starting the driver, launching the browser); every platform still
# searches/applies fully in parallel once its own browser is up.
_PLAYWRIGHT_LAUNCH_LOCK = threading.Lock()


def _emit(events: queue.Queue, event: dict) -> None:
    """Put an event on the SSE queue AND print it to the server's own
    stdout (captured in the uvicorn log). Every event here used to go
    ONLY to the browser -- real progress (platform_start, search result
    counts, each job's duplicate/skipped/applied outcome) was genuinely
    being produced the whole time, but was invisible outside the browser
    tab, which is why a silent-looking run gave no way to tell "it found
    nothing" apart from "something's actually wrong" after the fact."""
    events.put(event)
    etype = event.get("type", "")
    platform = event.get("platform", "")
    if etype == "platform_search":
        print(f"[{platform}] found {event.get('count')} candidate postings", flush=True)
    elif etype == "result":
        fit = event.get("fit")
        fit_str = f"{fit:.2f}" if isinstance(fit, (int, float)) else "n/a"
        print(f"[{platform}] {event.get('status')} (fit {fit_str}): {event.get('title')} @ {event.get('company')}"
              + (f" -- {event.get('reason')}" if event.get("reason") else ""), flush=True)
    elif etype in ("platform_start", "platform_error", "platform_skipped", "platform_done"):
        print(f"[{platform}] {etype}" + (f": {event.get('message')}" if event.get("message") else ""), flush=True)

RUN_PLATFORMS = ["linkedin", "wellfound", "ashby", "workable", "rippling"]  # indeed excluded -- see README


def _load_config(config_path: Path) -> dict:
    with open(config_path) as f:
        return yaml.safe_load(f)


def run_pipeline(
    resume_path: Path,
    min_fit_score: float,
    events: queue.Queue,
    profile_name: str | None = None,
    stop_event: threading.Event | None = None,
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

        # A key saved via the Settings tab (BYOK) always wins over .env --
        # see src/app_settings.py.
        api_key = resolve_api_key()
        if not api_key:
            _emit(events, {"type": "error", "message": "No API key set -- add one in Settings, or set ANTHROPIC_API_KEY in .env"})
            _emit(events, {"type": "done"})
            return
        model = resolve_model()

        _emit(events, {"type": "step", "step": 1, "label": "Extracting information from resume"})
        profile = parse_resume(str(resume_path), api_key, cache_dir=paths.cache_dir, model=model)
        profile = apply_profile_overrides(profile_name, profile)
        _emit(events,
            {
                "type": "profile",
                "name": profile.full_name,
                "headline": profile.headline,
                "years_of_experience": profile.years_of_experience,
                "skills": profile.skills[:10],
            }
        )

        # First-ever run for this account: seed the Profile tab's memory
        # agent from the resume itself, so the flowgraph shows real
        # (resume-derived, not invented) context right away instead of
        # starting empty. Only when memory is still empty -- never
        # overwrites facts the candidate has since added or edited there.
        if not load_facts(profile_name):
            if profile.headline:
                add_fact(profile_name, profile.headline, profile.headline[:28])
            if profile.years_of_experience:
                add_fact(
                    profile_name,
                    f"{profile.years_of_experience} years of professional experience",
                    f"{profile.years_of_experience}+ yrs experience",
                )
            for skill in profile.skills[:5]:
                add_fact(profile_name, f"Skilled in {skill}", skill)

        if stop_event and stop_event.is_set():
            _emit(events, {"type": "stopped"})
            return

        _emit(events, {"type": "step", "step": 2, "label": "Scoring postings and tailoring answers with AI"})
        # Facts the candidate typed into the Profile tab's memory agent --
        # see src/memory.py -- woven into every scoring/tailoring prompt
        # Tailor builds, so a stated preference ("FinTech only", a target
        # comp number, "won't relocate") actually shapes fit scores and
        # generated answers, not just the Profile tab's own display.
        memory_text = as_prompt_block(load_facts(profile_name))
        tailor = Tailor(api_key, cache_dir=paths.cache_dir, memory_text=memory_text, model=model)
        cfg = _load_config(paths.config_path)

        # Settings-tab overrides (src/app_settings.py) layer on top of
        # search_config.yaml rather than rewriting it -- so the Platforms
        # tab works without ever touching (and stripping the comments
        # from) the YAML file, and a Settings tab nobody has opened yet
        # changes nothing about existing behavior.
        settings_platforms = platform_overrides()
        if settings_platforms:
            cfg["platforms"] = {**cfg.get("platforms", {}), **settings_platforms}
        settings_max_cap = max_applications_override()
        if settings_max_cap:
            cfg["max_applications_per_platform_per_day"] = settings_max_cap

        # Search titles come from THIS resume by default, not one static list
        # shared by every account -- so two different people's resumes
        # uploaded in the same run each get titles relevant to them. A
        # profile can still pin its own fixed list via
        # `titles_from_resume: false` in its search_config.yaml.
        if cfg.get("titles_from_resume", True):
            suggested_titles = tailor.suggest_search_titles(profile)
            if suggested_titles:
                cfg["titles"] = suggested_titles
                _emit(events, {"type": "search_titles", "titles": suggested_titles})

        if stop_event and stop_event.is_set():
            _emit(events, {"type": "stopped"})
            return

        _emit(events, {"type": "step", "step": 3, "label": "Applying on LinkedIn, Wellfound & Ashby"})
        threads = []
        for platform in RUN_PLATFORMS:
            if stop_event and stop_event.is_set():
                break
            if not cfg.get("platforms", {}).get(platform, False):
                _emit(events, {"type": "platform_skipped", "platform": platform, "message": "disabled in config"})
                continue
            # Each thread opens its own Tracker/sqlite3 connection -- a
            # connection object can't be shared across threads (Python's
            # sqlite3 enforces this and raises ProgrammingError), which
            # silently killed every platform thread the first time this ran.
            t = threading.Thread(
                target=_run_one_platform,
                args=(platform, cfg, profile, tailor, min_fit_score, events, paths.state_dir, paths.db_path, resume_path, profile_name, stop_event),
                daemon=True,
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        if stop_event and stop_event.is_set():
            _emit(events, {"type": "stopped"})
    except Exception as e:  # noqa: BLE001 -- surface any crash to the UI instead of hanging it
        print("[run_pipeline] CRASHED:")
        traceback.print_exc()
        _emit(events, {"type": "error", "message": str(e)})
    finally:
        _emit(events, {"type": "done"})


def _run_one_platform(
    platform: str,
    config: dict,
    profile: Profile,
    tailor: Tailor,
    min_fit_score: float,
    events: queue.Queue,
    state_dir: Path,
    db_path: Path,
    resume_path: Path,
    profile_name: str | None,
    stop_event: threading.Event | None = None,
) -> None:
    if stop_event and stop_event.is_set():
        return

    adapter_cls = ADAPTERS[platform]
    state_file = state_dir / f"{platform}.json"
    daily_cap = config.get("max_applications_per_day", {}).get(
        platform, config.get("max_applications_per_platform_per_day", 20)
    )

    _emit(events, {"type": "platform_start", "platform": platform})

    tracker = Tracker(str(db_path))
    already_today = tracker.count_applied_today(platform)
    if already_today >= daily_cap:
        _emit(events, {"type": "platform_error", "platform": platform, "message": f"Daily cap ({daily_cap}) already reached"})
        _emit(events, {"type": "platform_done", "platform": platform})
        tracker.close()
        return

    if not state_file.exists() and adapter_cls.requires_login:
        _emit(events,
            {
                "type": "platform_error",
                "platform": platform,
                "message": "No saved login session -- run the CLI once with --no-headless to log in first.",
            }
        )
        _emit(events, {"type": "platform_done", "platform": platform})
        tracker.close()
        return

    hitl = platform in HITL_PLATFORMS
    # Same headless run as every other platform, no exceptions -- a HITL
    # platform (Ashby/Workable/Rippling) just stops short of clicking
    # Submit once a form is filled, instead of a visible window of any
    # kind popping up during this pass. See src/platforms/base.py's
    # __init__ docstring for why a real headed browser here (even
    # minimized/off-screen) was tried and explicitly ruled out live
    # (2026-11-04) in favor of refill-on-demand -- webapp/hitl_queue.py's
    # browser-worker thread is what launches a headed browser later, only
    # once, only for the one job a human actually clicks "Open" on.
    with _PLAYWRIGHT_LAUNCH_LOCK:
        pw_cm = sync_playwright()
        pw = pw_cm.__enter__()
        browser = pw.chromium.launch(headless=True)

    try:
        context = browser.new_context(storage_state=str(state_file) if state_file.exists() else None)
        adapter = adapter_cls(context, tailor, config, hitl=hitl)
        if hitl:
            # Needed by _queue_for_review to record which resume/account a
            # later refill-on-demand ("Open" in the HITL queue) should use
            # -- see src/platforms/base.py.
            adapter.resume_path = str(resume_path)
            adapter.profile_name = profile_name

        try:
            adapter.ensure_logged_in("", "", headless=True)
        except RuntimeError as e:
            _emit(events, {"type": "platform_error", "platform": platform, "message": str(e)})
            browser.close()
            pw_cm.__exit__(None, None, None)
            return
        if adapter_cls.requires_login:
            context.storage_state(path=str(state_file))

        postings = adapter.search_jobs(profile)
        _emit(events, {"type": "platform_search", "platform": platform, "count": len(postings)})

        applied_count = already_today
        for job in postings:
            if applied_count >= daily_cap:
                break
            if stop_event and stop_event.is_set():
                break
            if tracker.already_handled(job):
                # Same visibility problem as the keyword-exclude case:
                # re-running the same search finds mostly postings
                # already recorded from earlier runs. Surface that
                # distinctly (not lumped into generic "skipped") so it's
                # clear *why* nothing new happened, instead of it
                # looking like the pipeline silently did nothing.
                _emit(events,
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
                _emit(events,
                    {
                        "type": "result",
                        "platform": platform,
                        "company": job.company,
                        "title": job.title,
                        "url": job.url,
                        "status": "skipped",
                        "fit": None,
                        "jd": job.description or "",
                        "reason": f"title matched excluded keyword '{excluded_kw}'",
                    }
                )
                continue

            fit = tailor.score_fit(profile, job)
            if fit < min_fit_score:
                _emit(events,
                    {
                        "type": "result",
                        "platform": platform,
                        "company": job.company,
                        "title": job.title,
                        "url": job.url,
                        "status": "skipped",
                        "fit": fit,
                        "jd": job.description or "",
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

            _emit(events,
                {
                    "type": "result",
                    "platform": platform,
                    "company": job.company,
                    "title": job.title,
                    "url": job.url,
                    "status": result.status,
                    "fit": fit,
                    "jd": job.description or "",
                    "reason": result.reason,
                }
            )

            # Same real gap between separate applications as the CLI
            # (main.py) -- see its comment for why. 0/0 (the default)
            # means no delay.
            delay_cfg = config.get("apply_delay_seconds", {})
            # Optional per-platform override -- see main.py's matching
            # call site for why (the burst-sensitivity this delay exists
            # for was confirmed live on Ashby/Workable specifically, not
            # LinkedIn/Wellfound's real-account flow).
            platform_delay_cfg = delay_cfg.get(platform) if isinstance(delay_cfg.get(platform), dict) else delay_cfg
            lo, hi = platform_delay_cfg.get("min", 0), platform_delay_cfg.get("max", 0)
            if hi > 0:
                import random
                import time

                time.sleep(random.uniform(lo, hi))

        if adapter_cls.requires_login:
            context.storage_state(path=str(state_file))
    except Exception as e:  # noqa: BLE001 -- one platform crashing shouldn't hang the UI
        # Only the short message reaches the UI (SSE event, nothing
        # persisted) -- this was the actual silent-failure gap: a real
        # crash here (e.g. Wellfound/Ashby producing zero results with no
        # visible error) left no trace anywhere to diagnose from
        # afterward. Print the full traceback to the server's own stdout
        # (captured in the uvicorn log) so a repeat is actually diagnosable
        # instead of invisible.
        print(f"[{platform}] CRASHED:", flush=True)
        traceback.print_exc()
        _emit(events, {"type": "platform_error", "platform": platform, "message": str(e)})
        browser.close()
        pw_cm.__exit__(None, None, None)
    finally:
        tracker.close()
        _emit(events, {"type": "platform_done", "platform": platform})
