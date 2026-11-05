from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
import threading
import uuid
from pathlib import Path

import yaml

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src import app_settings
from src import memory as memory_store
from src.profile_overrides import apply_profile_overrides
from src.profile_paths import resolve_profile_paths
from src.resume_parser import parse_resume
from webapp import hitl_queue
from webapp.runner import run_pipeline

# A rough, clearly-labeled-as-such estimate, not a measurement -- how long
# filling one application out by hand typically takes, used only for the
# "time saved" figure on the Applied tab. Real reply/outcome data (did the
# application actually lead anywhere) isn't something this tool has any
# way to know, so "successful" always reports what the tracker actually
# recorded (an "applied" status), never a guess.
MINUTES_SAVED_PER_APPLICATION = 10

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = ROOT / "webapp" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()

STATIC_DIR = Path(__file__).parent / "static"
app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

RUNS: dict[str, queue.Queue] = {}
STOP_EVENTS: dict[str, threading.Event] = {}

# Starting a new run never used to check whether a previous one for the
# SAME account was still going -- each click just spawned an independent
# thread, so overlapping runs piled up (confirmed live: this is why Ashby's
# slow multi-minute scoring pass kept never finishing -- each new run
# competed with the last one's still-running Ashby thread for the same
# cache file and CPU). Keyed by profile name (None = root account) so
# different people's resumes can still run fully concurrently -- only the
# SAME account's own overlapping run is blocked.
ACTIVE_PROFILES: set[str | None] = set()
ACTIVE_PROFILES_LOCK = threading.Lock()

# Lets a 409 "already in progress" response hand the client the OTHER run's
# id, so the UI can offer to cancel that stuck/forgotten run on the spot
# instead of leaving the account locked with no way to free it short of
# restarting the server (what used to happen whenever the browser lost
# track of a run_id -- a page reload, or any gap before the tab-persistence
# fix -- since ACTIVE_PROFILES only ever cleared via that one run's own
# finally block, never via anything reachable from a fresh page load).
ACTIVE_RUNS: dict[str | None, str] = {}


def _run_and_release(
    resume_path: Path,
    min_fit_score: float,
    events: queue.Queue,
    profile_name: str | None,
    run_id: str,
    stop_event: threading.Event,
) -> None:
    try:
        run_pipeline(resume_path, min_fit_score, events, profile_name, stop_event=stop_event)
    finally:
        with ACTIVE_PROFILES_LOCK:
            ACTIVE_PROFILES.discard(profile_name)
            if ACTIVE_RUNS.get(profile_name) == run_id:
                ACTIVE_RUNS.pop(profile_name, None)
        STOP_EVENTS.pop(run_id, None)


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/api/run")
async def start_run(
    resume: UploadFile = File(...),
    profile: str | None = Form(None),
    min_fit_score: float = Form(0.60),
) -> dict:
    profile = (profile or "").strip() or None
    min_fit_score = min(max(min_fit_score, 0.0), 1.0)

    with ACTIVE_PROFILES_LOCK:
        if profile in ACTIVE_PROFILES:
            label = profile or "your account"
            raise HTTPException(
                status_code=409,
                detail={
                    "message": f"A run is already in progress for {label}.",
                    "run_id": ACTIVE_RUNS.get(profile),
                },
            )
        ACTIVE_PROFILES.add(profile)

    run_id = uuid.uuid4().hex
    suffix = Path(resume.filename or "resume.pdf").suffix or ".pdf"
    resume_path = UPLOADS_DIR / f"{run_id}{suffix}"
    resume_path.write_bytes(await resume.read())

    events: queue.Queue = queue.Queue()
    RUNS[run_id] = events
    stop_event = threading.Event()
    STOP_EVENTS[run_id] = stop_event
    with ACTIVE_PROFILES_LOCK:
        ACTIVE_RUNS[profile] = run_id

    thread = threading.Thread(
        target=_run_and_release,
        args=(resume_path, min_fit_score, events, profile, run_id, stop_event),
        daemon=True,
    )
    thread.start()

    return {"run_id": run_id}


@app.post("/api/run/{run_id}/stop")
async def stop_run(run_id: str) -> dict:
    """Signals the running pipeline to stop as soon as it can -- the
    platform currently mid-LLM-call or mid-apply finishes that one step,
    then every platform thread breaks out of its job loop instead of
    moving on to the next posting."""
    stop_event = STOP_EVENTS.get(run_id)
    if stop_event is None:
        raise HTTPException(status_code=404, detail="Unknown or already-finished run_id.")
    stop_event.set()
    return {"ok": True}


@app.get("/api/memory")
async def list_memory(profile: str | None = None) -> list[dict]:
    """Facts/preferences the candidate has told their agent directly,
    shown as the Profile tab's flowgraph and woven into every scoring/
    tailoring prompt (see src/memory.py and webapp/runner.py)."""
    profile = (profile or "").strip() or None
    return memory_store.load_facts(profile)


@app.post("/api/memory")
async def create_memory(profile: str | None = Form(None), text: str = Form(...)) -> dict:
    profile = (profile or "").strip() or None
    text = text.strip()
    if not text:
        raise HTTPException(status_code=422, detail="Memory text can't be empty.")
    api_key = app_settings.resolve_api_key()
    tag = memory_store.suggest_tag(text, api_key) if api_key else text[:24]
    return memory_store.add_fact(profile, text, tag)


@app.delete("/api/memory/{fact_id}")
async def delete_memory(fact_id: str, profile: str | None = None) -> dict:
    profile = (profile or "").strip() or None
    ok = memory_store.delete_fact(profile, fact_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown memory entry.")
    return {"ok": True}


@app.post("/api/memory/sync-resume")
async def sync_memory_from_resume(resume: UploadFile = File(...), profile: str | None = Form(None)) -> dict:
    """Parses a resume (same parser the Agent tab's run uses) purely to
    seed the Profile tab's memory graph with headline/experience/skills
    facts -- no job search, no applying, nothing platform-related. Exists
    because the pipeline's own auto-seed (webapp/runner.py) only fires the
    first time an account's memory is completely empty, so an account that
    already had a manually-typed note (or ran before this feature existed)
    never got resume facts automatically; this lets the Profile tab ask for
    them on demand instead. Skips any fact whose exact text is already
    stored, so repeat use doesn't pile up duplicates."""
    profile = (profile or "").strip() or None
    api_key = app_settings.resolve_api_key()
    if not api_key:
        raise HTTPException(status_code=500, detail="No API key set -- add one in Settings, or set ANTHROPIC_API_KEY in .env")

    paths = resolve_profile_paths(profile)
    suffix = Path(resume.filename or "resume.pdf").suffix or ".pdf"
    tmp_path = UPLOADS_DIR / f"sync-{uuid.uuid4().hex}{suffix}"
    tmp_path.write_bytes(await resume.read())
    try:
        parsed = parse_resume(str(tmp_path), api_key, cache_dir=paths.cache_dir, model=app_settings.resolve_model())
        parsed = apply_profile_overrides(profile, parsed)
    finally:
        tmp_path.unlink(missing_ok=True)

    existing_texts = {f["text"] for f in memory_store.load_facts(profile)}
    added = []

    def _maybe_add(text: str, tag: str) -> None:
        if text and text not in existing_texts:
            added.append(memory_store.add_fact(profile, text, tag))
            existing_texts.add(text)

    if parsed.headline:
        _maybe_add(parsed.headline, parsed.headline[:28])
    if parsed.years_of_experience:
        _maybe_add(
            f"{parsed.years_of_experience} years of professional experience",
            f"{parsed.years_of_experience}+ yrs experience",
        )
    for skill in parsed.skills[:5]:
        _maybe_add(f"Skilled in {skill}", skill)

    return {"added": added, "facts": memory_store.load_facts(profile)}


@app.get("/api/settings")
async def get_settings() -> dict:
    """Everything the Settings tab shows: which platforms are enabled (the
    saved override if one exists, otherwise the root account's own
    search_config.yaml -- so the toggles reflect real current behavior on
    first load, not an arbitrary default), the API key's status (never the
    key itself), the active model, and the model menu to choose from."""
    saved_platforms = app_settings.platform_overrides()
    if saved_platforms is None:
        cfg_path = resolve_profile_paths(None).config_path
        try:
            with open(cfg_path) as f:
                cfg = yaml.safe_load(f) or {}
        except (FileNotFoundError, yaml.YAMLError):
            cfg = {}
        saved_platforms = {p: bool(cfg.get("platforms", {}).get(p, False)) for p in app_settings.ALL_PLATFORMS}
    return {
        "platforms": saved_platforms,
        "api_key": app_settings.api_key_status(),
        "model": app_settings.resolve_model(),
        "available_models": app_settings.AVAILABLE_MODELS,
        "max_applications_per_platform_per_day": app_settings.max_applications_override(),
    }


@app.post("/api/settings")
async def update_settings(
    api_key: str | None = Form(None),
    model: str | None = Form(None),
    platforms: str | None = Form(None),
    max_applications_per_platform_per_day: int | None = Form(None),
) -> dict:
    patch: dict = {}
    if api_key is not None:
        patch["api_key"] = api_key
    if model:
        if model not in app_settings.AVAILABLE_MODELS:
            raise HTTPException(status_code=422, detail=f"Unknown model '{model}'.")
        patch["model"] = model
    if platforms is not None:
        try:
            parsed_platforms = json.loads(platforms)
        except json.JSONDecodeError:
            raise HTTPException(status_code=422, detail="platforms must be a JSON object.")
        patch["platforms"] = parsed_platforms
    if max_applications_per_platform_per_day is not None:
        patch["max_applications_per_platform_per_day"] = max_applications_per_platform_per_day

    app_settings.save_settings(patch)
    return await get_settings()


@app.get("/api/stream/{run_id}")
async def stream(run_id: str) -> StreamingResponse:
    events = RUNS.get(run_id)
    if events is None:
        return StreamingResponse(iter(["data: {\"type\": \"error\", \"message\": \"unknown run_id\"}\n\n"]), media_type="text/event-stream")

    async def event_gen():
        loop = asyncio.get_event_loop()
        while True:
            item = await loop.run_in_executor(None, events.get)
            yield f"data: {json.dumps(item)}\n\n"
            if item.get("type") == "done":
                break
        RUNS.pop(run_id, None)

    return StreamingResponse(event_gen(), media_type="text/event-stream")


@app.get("/api/hitl")
async def list_hitl() -> list[dict]:
    """Every filled-and-waiting application across every platform/run --
    a human reviews, solves any captcha, and submits each one by hand."""
    return hitl_queue.list_entries()


@app.post("/api/hitl/{entry_id}/open")
async def open_hitl(entry_id: str) -> dict:
    """Kicks off a fresh, headed, one-job re-fill for this entry and
    returns right away -- the window taking shape is tracked via the
    entry's own status (opening -> opened/failed), polled through
    GET /api/hitl, not this response."""
    ok = hitl_queue.open_entry(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Unknown entry.")
    return {"ok": True}


@app.post("/api/hitl/{entry_id}/resolve")
async def resolve_hitl(entry_id: str) -> dict:
    """Human clicked "Mark done" -- removes it from the queue and closes
    that tab (the page itself, whatever its state, isn't ours to judge --
    submitted or abandoned, the human already decided)."""
    ok = hitl_queue.resolve(entry_id)
    if not ok:
        raise HTTPException(status_code=404, detail="Already resolved or unknown entry.")
    return {"ok": True}


def _db_connect(profile: str | None) -> sqlite3.Connection:
    db_path = resolve_profile_paths(profile).db_path
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True) if db_path.exists() else sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    return conn


@app.get("/api/stats")
async def get_stats(profile: str | None = None) -> dict:
    """The Applied tab's summary bar. "successful" only ever reflects what
    the tracker actually recorded (status='applied') -- there's no reply/
    outcome tracking, so this never reports more than that. "time_saved_
    minutes" is a labeled estimate (MINUTES_SAVED_PER_APPLICATION per
    attempt), not a measurement."""
    conn = _db_connect(profile)
    try:
        row = conn.execute(
            """SELECT
                 COUNT(*) AS submitted,
                 SUM(CASE WHEN status = 'applied' THEN 1 ELSE 0 END) AS successful,
                 SUM(CASE WHEN status IN ('needs_review', 'pending_review') THEN 1 ELSE 0 END) AS needs_you
               FROM applications"""
        ).fetchone()
    except sqlite3.OperationalError:
        row = None
    finally:
        conn.close()

    submitted = (row["submitted"] if row else 0) or 0
    successful = (row["successful"] if row else 0) or 0
    needs_you = (row["needs_you"] if row else 0) or 0
    return {
        "submitted": submitted,
        "successful": successful,
        "needs_you": needs_you,
        "time_saved_minutes": submitted * MINUTES_SAVED_PER_APPLICATION,
    }


@app.get("/api/applications")
async def list_applications(profile: str | None = None, status: str | None = None, q: str | None = None) -> list[dict]:
    """The Applied tab's own list -- every row the tracker holds, newest
    first, optionally narrowed by status or a free-text match on company/
    title."""
    conn = _db_connect(profile)
    try:
        sql = "SELECT platform, job_id, title, company, url, status, reason, applied_at FROM applications WHERE 1=1"
        params: list[str] = []
        if status:
            sql += " AND status = ?"
            params.append(status)
        if q:
            sql += " AND (company LIKE ? OR title LIKE ?)"
            params.extend([f"%{q}%", f"%{q}%"])
        sql += " ORDER BY applied_at DESC LIMIT 200"
        rows = conn.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        rows = []
    finally:
        conn.close()
    return [dict(r) for r in rows]
