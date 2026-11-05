from __future__ import annotations

import asyncio
import json
import queue
import sqlite3
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from src.profile_paths import resolve_profile_paths
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


def _run_and_release(resume_path: Path, min_fit_score: float, events: queue.Queue, profile_name: str | None) -> None:
    try:
        run_pipeline(resume_path, min_fit_score, events, profile_name)
    finally:
        with ACTIVE_PROFILES_LOCK:
            ACTIVE_PROFILES.discard(profile_name)


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
                detail=f"A run is already in progress for {label}. Wait for it to finish before starting another.",
            )
        ACTIVE_PROFILES.add(profile)

    run_id = uuid.uuid4().hex
    suffix = Path(resume.filename or "resume.pdf").suffix or ".pdf"
    resume_path = UPLOADS_DIR / f"{run_id}{suffix}"
    resume_path.write_bytes(await resume.read())

    events: queue.Queue = queue.Queue()
    RUNS[run_id] = events

    thread = threading.Thread(
        target=_run_and_release, args=(resume_path, min_fit_score, events, profile), daemon=True
    )
    thread.start()

    return {"run_id": run_id}


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
