from __future__ import annotations

import asyncio
import json
import queue
import threading
import uuid
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, File, UploadFile
from fastapi.responses import FileResponse, StreamingResponse

from webapp.runner import run_pipeline

load_dotenv()

ROOT = Path(__file__).resolve().parent.parent
UPLOADS_DIR = ROOT / "webapp" / "uploads"
UPLOADS_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI()

RUNS: dict[str, queue.Queue] = {}


@app.get("/")
def index() -> FileResponse:
    return FileResponse(Path(__file__).parent / "static" / "index.html")


@app.post("/api/run")
async def start_run(resume: UploadFile = File(...)) -> dict:
    run_id = uuid.uuid4().hex
    suffix = Path(resume.filename or "resume.pdf").suffix or ".pdf"
    resume_path = UPLOADS_DIR / f"{run_id}{suffix}"
    resume_path.write_bytes(await resume.read())

    events: queue.Queue = queue.Queue()
    RUNS[run_id] = events

    thread = threading.Thread(
        target=run_pipeline, args=(resume_path, 0.0, events), daemon=True
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
