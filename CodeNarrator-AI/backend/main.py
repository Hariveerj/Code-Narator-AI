from __future__ import annotations

import asyncio
import json
import logging
import os
import uuid
from pathlib import Path
from typing import Annotated, Any, List

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles

try:
    from .ollama_client import OllamaClientError, analyze_code, precheck_ollama
except ImportError:
    from ollama_client import OllamaClientError, analyze_code, precheck_ollama

BASE_DIR     = Path(__file__).resolve().parent.parent
FRONTEND_DIR = BASE_DIR / "frontend"

_ALLOWED_ORIGINS = [
    o.strip()
    for o in os.getenv("CORS_ORIGINS", "http://localhost:8081,http://127.0.0.1:8081").split(",")
    if o.strip()
]

logger = logging.getLogger(__name__)

app = FastAPI(title="Code Narrator AI", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

# ── In-memory job store ─────────────────────────────────────────────────────
# Maps job_id -> asyncio.Queue of SSE event dicts (None = done sentinel)
_jobs: dict[str, asyncio.Queue[dict[str, Any] | None]] = {}

# Keep references to background tasks so they are not garbage-collected.
_background_tasks: set[asyncio.Task[None]] = set()


# ── Health ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/ollama")
def ollama_health() -> dict[str, object]:
    ok, message = precheck_ollama()
    return {"status": "ok" if ok else "error", "ok": ok, "message": message}


# ── Upload endpoint (returns job_id) ────────────────────────────────────────
@app.post("/api/upload", responses={400: {"description": "No file/code provided or all files were empty."}})
async def upload_for_stream(
    files: Annotated[List[UploadFile] | None, File()] = None,
    code_text: Annotated[str | None, Form()] = None,
) -> dict[str, str]:
    """Read all uploaded files into memory, launch background processing, return job_id."""
    # Read file data eagerly while still inside the request context
    file_data: list[tuple[str, bytes]] = []
    if files:
        for f in files:
            raw = await f.read()
            file_data.append((f.filename or "unknown", raw))

    # Early validation — reject obviously empty submissions immediately
    has_file_content = any(raw.strip() for _, raw in file_data)
    has_text_content = bool(code_text and code_text.strip())
    if not has_file_content and not has_text_content:
        raise HTTPException(status_code=400, detail="Provide at least one file or paste code.")

    job_id = str(uuid.uuid4())
    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _jobs[job_id] = queue

    task = asyncio.create_task(_process_job(job_id, file_data, code_text))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"job_id": job_id}


async def _process_job(
    job_id: str,
    file_data: list[tuple[str, bytes]],
    code_text: str | None,
) -> None:
    """Background task: walks files one-by-one, emits SSE progress, runs Ollama."""
    queue = _jobs.get(job_id)
    if not queue:
        return

    try:
        total = len(file_data)
        parts: list[str] = []

        for idx, (filename, raw) in enumerate(file_data, start=1):
            # Emit per-file progress event
            await queue.put({
                "type": "progress",
                "current": idx,
                "total": total,
                "filename": filename,
            })

            decoded = raw.decode("utf-8", errors="replace").strip()
            if decoded:
                header = f"# === File: {filename} ==="
                parts.append(f"{header}\n{decoded}")

            # Yield control so the event loop can flush SSE to the client
            await asyncio.sleep(0)

        # Pasted code overrides file content
        merged = "\n\n".join(parts)
        if code_text and code_text.strip():
            merged = code_text.strip()

        if not merged:
            await queue.put({"type": "error", "message": "No content provided."})
            return

        await queue.put({"type": "analyzing", "message": "Running AI analysis…"})

        # Run blocking Ollama call in a thread-pool so we don't block the loop
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, analyze_code, merged)

        await queue.put({"type": "result", **result})

    except OllamaClientError as exc:
        await queue.put({"type": "error", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        await queue.put({"type": "error", "message": f"Unexpected error: {exc}"})
    finally:
        await queue.put(None)  # sentinel → client closes stream


# ── SSE stream endpoint ──────────────────────────────────────────────────────
@app.get("/api/stream/{job_id}", responses={404: {"description": "Job not found or already consumed."}})
async def stream_job(job_id: str):
    """Server-Sent Events stream for a running job."""
    queue = _jobs.get(job_id)
    if not queue:
        raise HTTPException(status_code=404, detail="Job not found or already consumed.")

    async def event_gen():
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=180)
                except asyncio.TimeoutError:
                    yield f'data: {json.dumps({"type": "error", "message": "Job timed out."})}\n\n'
                    break

                if item is None:
                    yield "data: [DONE]\n\n"
                    break

                yield f"data: {json.dumps(item)}\n\n"
        finally:
            _jobs.pop(job_id, None)

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ── Legacy single-shot endpoints (kept for tests / back-compat) ─────────────
_ANALYZE_RESPONSES: dict[int | str, dict[str, Any]] = {
    400: {"description": "No file/code provided or all files were empty."},
    500: {"description": "Unexpected server error."},
    502: {"description": "Ollama service unavailable or failed."},
}


async def _build_code(files: List[UploadFile] | None, code_text: str | None) -> str:
    code = ""
    if files:
        parts: list[str] = []
        for f in files:
            content = await f.read()
            file_code = content.decode("utf-8", errors="replace").strip()
            if file_code:
                header = f"# === File: {f.filename} ===" if f.filename else "# === File ==="
                parts.append(f"{header}\n{file_code}")
        code = "\n\n".join(parts)
    if code_text and code_text.strip():
        code = code_text.strip()
    return code


@app.post("/analyze", responses=_ANALYZE_RESPONSES)
async def analyze(
    files: Annotated[List[UploadFile] | None, File()] = None,
    code_text: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    code = await _build_code(files, code_text)
    if not code:
        raise HTTPException(status_code=400, detail="Provide at least one file or paste code.")
    try:
        return analyze_code(code)
    except OllamaClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        logger.error("Unexpected server error during analysis", exc_info=exc)
        raise HTTPException(status_code=500, detail="Internal server error.") from exc


@app.post("/api/analyze", responses=_ANALYZE_RESPONSES)
async def analyze_api(
    files: Annotated[List[UploadFile] | None, File()] = None,
    code_text: Annotated[str | None, Form()] = None,
) -> dict[str, object]:
    return await analyze(files=files, code_text=code_text)


# ── Frontend SPA (must be last) ──────────────────────────────────────────────
if FRONTEND_DIR.exists():
    app.mount("/", StaticFiles(directory=str(FRONTEND_DIR), html=True), name="spa")

