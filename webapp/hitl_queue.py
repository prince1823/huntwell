from __future__ import annotations

import os
import queue
import threading
import time
import traceback
import uuid

from playwright.sync_api import sync_playwright

from src.llm import Tailor
from src.models import JobPosting
from src.profile_overrides import apply_profile_overrides
from src.profile_paths import resolve_profile_paths
from src.resume_parser import parse_resume

# In-memory registry only -- a page here only means anything while this
# server process (and its one browser-worker thread, below) is still
# running; restarting the server loses every pending entry.
#
# This is deliberately NOT "keep the filled page open and bring it to
# front later". That was tried and explicitly ruled out live (2026-11-04):
# Playwright has no headless<->headed toggle on an existing page, so
# keeping something alive to reveal later either means running headed
# (visible, even if minimized/off-screen -- macOS doesn't fully honor
# off-screen positioning either, confirmed live) the whole time, which is
# the "so many windows" problem this replaces. Instead: the search/fill
# pass runs headless=True like every other platform, with no window at
# all. An entry here is just enough metadata (platform, the job itself,
# which resume/account) to RE-FILL that one job from scratch, live, the
# moment a human actually clicks "Open" -- not the literal original
# page/state, a fresh equivalent run.
#
# Every LLM call Tailor makes (answer_screening_question,
# classify_field, etc.) is cached on (platform, job_id, question,
# profile) -- confirmed live by reading src/llm.py, not assumed. The
# refill reconstructs the exact same job_id against the exact same
# resume, so it's a cache hit on every answer the first pass already
# worked out, not a second LLM pass for the same thing.
_lock = threading.Lock()
_entries: dict[str, dict] = {}

# One shared, persistent, headed browser for the whole server's lifetime,
# owned by exactly one dedicated worker thread -- never touched from any
# other thread. Confirmed live (2026-11-04): Playwright's sync API is
# strictly single-thread-affine (a browser touched from a thread other
# than the one that created it crashes with "Cannot switch to a different
# thread"), so every "open"/"close" request funnels through
# _command_queue instead of being handled directly on whatever thread
# FastAPI happened to handle that request on. "Open" on a second job
# while the browser is already up reuses it -- a new TAB, same window,
# same Chromium process -- not a second window: every current HITL
# platform (Ashby/Workable/Rippling) is guest-apply, so they all share
# one context (see _browser_worker_loop's own comment for why that's not
# just convenient but correct, given nothing currently needs its own
# login state kept apart).
_command_queue: queue.Queue = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


def _ensure_worker_started() -> None:
    global _worker_started
    with _worker_lock:
        if _worker_started:
            return
        _worker_started = True
        threading.Thread(target=_browser_worker_loop, daemon=True).start()


def add(job: JobPosting, platform: str, resume_path: str | None, profile_name: str | None) -> str:
    """Registers a filled-and-stopped-before-submit job for later,
    on-demand review. Nothing live is kept -- just enough to re-fill it."""
    entry_id = uuid.uuid4().hex
    with _lock:
        _entries[entry_id] = {
            "id": entry_id,
            "platform": platform,
            "title": job.title,
            "company": job.company,
            "url": job.url,
            "added_at": time.time(),
            "status": "pending",
            "job": job.model_dump(),
            "resume_path": resume_path,
            "profile_name": profile_name,
            "page": None,
        }
    return entry_id


def list_entries() -> list[dict]:
    """Queue contents for the UI -- internal bookkeeping (the job dict,
    the live page handle) is never serialized out."""
    with _lock:
        items = list(_entries.values())
    out = [
        {
            "id": e["id"],
            "platform": e["platform"],
            "title": e["title"],
            "company": e["company"],
            "url": e["url"],
            "added_at": e["added_at"],
            "status": e["status"],
        }
        for e in items
    ]
    out.sort(key=lambda e: e["added_at"])
    return out


def open_entry(entry_id: str) -> bool:
    """Queues a fresh re-fill of this one job as a new tab in the shared
    browser and returns immediately -- the actual work (resume parse,
    navigation, filling) takes real time, so the UI polls list_entries()
    for the status to move from "opening" to "opened" rather than this
    call blocking on it."""
    with _lock:
        entry = _entries.get(entry_id)
        if entry is None:
            return False
        if entry["status"] != "pending":
            # Already opening/opened (a second click) or already failed --
            # nothing new to start either way.
            return True
        entry["status"] = "opening"
    _ensure_worker_started()
    _command_queue.put(("open", entry_id))
    return True


def resolve(entry_id: str) -> bool:
    """Human clicked "Mark done". If the job was actually opened, queues
    a close for that one tab (done on the browser-worker thread, same
    thread-affinity reason as everything else here). If it was never
    opened, there's nothing live to close; just drop it."""
    with _lock:
        entry = _entries.pop(entry_id, None)
        was_opened = entry is not None and entry["page"] is not None
    if entry is None:
        return False
    if was_opened:
        _command_queue.put(("close", entry_id))
    return True


def _browser_worker_loop() -> None:
    """The one thread allowed to touch the shared browser, for the rest
    of this server process's life. Lazily launches it on the first real
    command (no window pops up just because the server started) and
    processes "open"/"close" commands one at a time, forever."""
    pw_cm = None
    pw = None
    browser = None
    # Every current HITL platform (Ashby/Workable/Rippling) is guest-apply
    # -- no login, no session state to keep apart -- so one shared context
    # for all of them is correct, not just convenient: it's what makes
    # multiple "Open"s land as tabs in the same window instead of each
    # getting its own. If a future HITL platform ever does carry a login
    # session, it needs its own context (its cookies must not mix into
    # this one), which this no longer special-cases now that nothing
    # currently needs it.
    guest_context = None

    def _get_browser():
        nonlocal pw_cm, pw, browser
        if browser is None:
            pw_cm = sync_playwright()
            pw = pw_cm.__enter__()
            browser = pw.chromium.launch(headless=False)
        return browser

    def _get_context():
        nonlocal guest_context
        if guest_context is None:
            guest_context = _get_browser().new_context()
        return guest_context

    while True:
        action, entry_id = _command_queue.get()

        if action == "close":
            with _lock:
                entry = _entries.pop(entry_id, None)
            page = entry["page"] if entry else None
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass
            continue

        # action == "open"
        with _lock:
            entry = _entries.get(entry_id)
        if entry is None:
            continue
        job = JobPosting(**entry["job"])
        platform = entry["platform"]
        resume_path = entry["resume_path"]
        profile_name = entry["profile_name"]

        try:
            # Lazy import: avoids a real import cycle (webapp.runner
            # imports this module too) by only resolving this once a
            # command is actually being processed, long after both
            # modules have finished loading.
            from webapp.runner import ADAPTERS, _load_config

            api_key = os.environ.get("ANTHROPIC_API_KEY")
            if not api_key or not resume_path:
                raise RuntimeError("Missing API key or resume path -- can't refill this job")

            paths = resolve_profile_paths(profile_name)
            profile = parse_resume(resume_path, api_key, cache_dir=paths.cache_dir)
            profile = apply_profile_overrides(profile_name, profile)
            tailor = Tailor(api_key, cache_dir=paths.cache_dir)
            config = _load_config(paths.config_path)
            adapter_cls = ADAPTERS[platform]

            context = _get_context()
            adapter = adapter_cls(context, tailor, config, hitl=True, hitl_live=True)

            with _lock:
                if entry_id not in _entries:
                    # Resolved (dismissed) while this was still being set
                    # up -- the page it just opened is the only thing to
                    # clean up; the shared browser/context stay up for
                    # whatever else is queued or comes next.
                    adapter.page.close()
                    continue
                _entries[entry_id]["page"] = adapter.page

            try:
                adapter.ensure_logged_in("", "", headless=False)
            except RuntimeError:
                pass  # a visible window exists now; a human can log in directly in it

            # Fills and stops before Submit. On a failure here the tab
            # stays open rather than closing automatically -- the whole
            # point of this flow is a human looking at it, and closing it
            # the moment something breaks would defeat that for exactly
            # the cases where seeing it matters most.
            adapter.apply(job, profile)
            status = "opened"
        except Exception:
            traceback.print_exc()
            status = "failed"

        with _lock:
            e = _entries.get(entry_id)
            if e is not None:
                e["status"] = status
