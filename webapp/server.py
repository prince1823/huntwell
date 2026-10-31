from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from webapp.runner import run_pipeline

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = ROOT / "webapp" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()

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
) -> dict:
    profile = (profile or "").strip() or None

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
        target=_run_and_release, args=(resume_path, 0.30, events, profile), daemon=True
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
