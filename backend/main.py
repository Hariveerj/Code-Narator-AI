from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, List, cast

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import Response

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

JOB_STREAM_POLL_SECONDS = int(os.getenv("JOB_STREAM_POLL_SECONDS", "15"))
JOB_STREAM_IDLE_TIMEOUT_SECONDS = int(os.getenv("JOB_STREAM_IDLE_TIMEOUT_SECONDS", "1800"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(50 * 1024 * 1024)))
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(20 * 1024 * 1024)))
MAX_FILES_PER_JOB = int(os.getenv("MAX_FILES_PER_JOB", "1200"))
UPLOAD_CHUNK_BYTES = int(os.getenv("UPLOAD_CHUNK_BYTES", str(1024 * 1024)))
FILE_SNIPPET_CHARS = int(os.getenv("FILE_SNIPPET_CHARS", "40000"))
UPLOAD_TMP_DIR = Path(os.getenv("UPLOAD_TMP_DIR", str(Path(tempfile.gettempdir()) / "codenarrator_uploads")))
UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)

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


@app.middleware("http")
async def request_size_guard(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    content_length = request.headers.get("content-length")
    if content_length and content_length.isdigit() and int(content_length) > MAX_UPLOAD_BYTES:
        return JSONResponse(
            status_code=413,
            content={"detail": f"Payload too large. Max allowed is {MAX_UPLOAD_BYTES} bytes."},
        )
    return await call_next(request)


def _safe_name(name: str) -> str:
    return "".join(ch if (ch.isalnum() or ch in "._-") else "_" for ch in name)[:120] or "file"


def _cleanup_staged_files(file_refs: list[tuple[str, Path]]) -> None:
    for _, path in file_refs:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue


async def _stage_upload_files(job_id: str, files: List[UploadFile]) -> list[tuple[str, Path]]:
    if len(files) > MAX_FILES_PER_JOB:
        raise HTTPException(status_code=413, detail=f"Too many files. Max supported is {MAX_FILES_PER_JOB}.")

    staged: list[tuple[str, Path]] = []
    total_uploaded = 0

    try:
        for idx, upload in enumerate(files):
            filename = upload.filename or f"unknown_{idx}"
            safe_filename = _safe_name(filename)
            tmp_path = UPLOAD_TMP_DIR / f"{job_id}_{idx}_{safe_filename}"

            written = 0
            with tmp_path.open("wb") as out:
                while True:
                    chunk = await upload.read(UPLOAD_CHUNK_BYTES)
                    if not chunk:
                        break
                    written += len(chunk)
                    total_uploaded += len(chunk)
                    if written > MAX_FILE_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"File '{filename}' exceeds max file size ({MAX_FILE_BYTES} bytes).",
                        )
                    if total_uploaded > MAX_UPLOAD_BYTES:
                        raise HTTPException(
                            status_code=413,
                            detail=f"Total upload exceeds max payload ({MAX_UPLOAD_BYTES} bytes).",
                        )
                    out.write(chunk)

            await upload.close()
            staged.append((filename, tmp_path))

        return staged
    except Exception:
        _cleanup_staged_files(staged)
        raise


def _read_file_snippet(path: Path, max_chars: int) -> str:
    chars_left = max_chars
    parts: list[str] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        while chars_left > 0:
            chunk = handle.read(min(8192, chars_left))
            if not chunk:
                break
            parts.append(chunk)
            chars_left -= len(chunk)
    return "".join(parts).strip()


async def _read_upload_content(
    upload: UploadFile,
    total_uploaded: int,
) -> tuple[bytes, int]:
    chunks: list[bytes] = []
    written = 0
    while True:
        chunk = await upload.read(UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        written += len(chunk)
        total_uploaded += len(chunk)
        if written > MAX_FILE_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"File '{upload.filename or 'unknown'}' exceeds max file size ({MAX_FILE_BYTES} bytes).",
            )
        if total_uploaded > MAX_UPLOAD_BYTES:
            raise HTTPException(
                status_code=413,
                detail=f"Total upload exceeds max payload ({MAX_UPLOAD_BYTES} bytes).",
            )
        chunks.append(chunk)
    await upload.close()
    return b"".join(chunks), total_uploaded


# ── Health ──────────────────────────────────────────────────────────────────
@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/health/ollama")
def ollama_health() -> dict[str, object]:
    ok, message = precheck_ollama()
    return {"status": "ok" if ok else "error", "ok": ok, "message": message}


# ── Upload endpoint (returns job_id) ────────────────────────────────────────
@app.post(
    "/api/upload",
    responses={
        400: {"description": "No file/code provided or all files were empty."},
        413: {"description": "Payload too large."},
    },
)
async def upload_for_stream(
    files: Annotated[List[UploadFile] | None, File()] = None,
    code_text: Annotated[str | None, Form()] = None,
) -> dict[str, str]:
    """Stream files to temp storage, launch background processing, return job_id."""
    job_id = str(uuid.uuid4())
    staged_files: list[tuple[str, Path]] = []

    if files and not (code_text and code_text.strip()):
        staged_files = await _stage_upload_files(job_id, files)

    has_file_content = bool(staged_files)
    has_text_content = bool(code_text and code_text.strip())
    if not has_file_content and not has_text_content:
        _cleanup_staged_files(staged_files)
        raise HTTPException(status_code=400, detail="Provide at least one file or paste code.")

    queue: asyncio.Queue[dict[str, Any] | None] = asyncio.Queue()
    _jobs[job_id] = queue

    task = asyncio.create_task(_process_job(job_id, staged_files, code_text))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"job_id": job_id}


def _decode_uploaded_files(file_data: list[tuple[str, Path]]) -> list[tuple[str, str]]:
    decoded_files: list[tuple[str, str]] = []
    for filename, path in file_data:
        text = _read_file_snippet(path, FILE_SNIPPET_CHARS)
        if text:
            decoded_files.append((filename, text))
    return decoded_files


def _build_file_batches(decoded_files: list[tuple[str, str]], batch_size: int) -> list[list[tuple[str, str]]]:
    return [decoded_files[i : i + batch_size] for i in range(0, len(decoded_files), batch_size)]


def _merge_batch_result(
    result: dict[str, object],
    batch_idx: int,
    filenames: list[str],
    all_explanations: list[str],
    all_steps: list[str],
    all_security: list[dict[str, str]],
    all_mermaid_nodes: list[str],
) -> None:
    explanation = str(result.get("explanation", "")).strip()
    if explanation:
        all_explanations.append(f"**Batch {batch_idx}** ({', '.join(filenames)}): {explanation}")

    steps = result.get("steps", [])
    if isinstance(steps, list):
        for step in cast(list[object], steps):
            step_text = str(step).strip()
            if step_text:
                all_steps.append(step_text)

    security = result.get("security", [])
    if isinstance(security, list):
        for finding in cast(list[object], security):
            if isinstance(finding, dict):
                finding_map = cast(dict[str, object], finding)
                all_security.append({
                    "severity": str(finding_map.get("severity", "INFO")),
                    "issue": str(finding_map.get("issue", "")),
                    "detail": str(finding_map.get("detail", "")),
                })

    mermaid = str(result.get("mermaid", "")).strip()
    if mermaid:
        all_mermaid_nodes.append(mermaid)


async def _process_job(
    job_id: str,
    file_data: list[tuple[str, Path]],
    code_text: str | None,
) -> None:
    """Background task: walks files in batches, emits SSE progress, runs Ollama."""
    queue = _jobs.get(job_id)
    if not queue:
        return

    BATCH_SIZE = int(os.getenv("BATCH_SIZE", "10"))

    try:
        # Pasted code → single-shot analysis (no batching needed)
        if code_text and code_text.strip():
            await queue.put({"type": "analyzing", "message": "Running AI analysis…"})
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(None, analyze_code, code_text.strip())
            await queue.put({"type": "result", **result})
            return

        decoded_files = _decode_uploaded_files(file_data)

        if not decoded_files:
            await queue.put({"type": "error", "message": "No content provided."})
            return

        batches = _build_file_batches(decoded_files, BATCH_SIZE)

        total_batches = len(batches)
        all_explanations: list[str] = []
        all_steps: list[str] = []
        all_security: list[dict[str, str]] = []
        all_mermaid_nodes: list[str] = []

        for batch_idx, batch in enumerate(batches, start=1):
            filenames = [f for f, _ in batch]
            await queue.put({
                "type": "progress",
                "current": batch_idx,
                "total": total_batches,
                "batch_files": filenames,
                "message": f"Analyzing batch {batch_idx}/{total_batches} ({len(batch)} files)…",
            })

            # Build merged code for this batch
            parts = [f"# === File: {fn} ===\n{code}" for fn, code in batch]
            merged = "\n\n".join(parts)

            await queue.put({"type": "analyzing", "message": f"Batch {batch_idx}/{total_batches}: Running AI analysis…"})

            loop = asyncio.get_event_loop()
            try:
                result = await loop.run_in_executor(None, analyze_code, merged)
            except OllamaClientError as exc:
                await queue.put({
                    "type": "batch_error",
                    "batch": batch_idx,
                    "message": f"Batch {batch_idx} failed: {exc}",
                })
                continue

            _merge_batch_result(
                result,
                batch_idx,
                filenames,
                all_explanations,
                all_steps,
                all_security,
                all_mermaid_nodes,
            )

            await asyncio.sleep(0)

        # Merge all batch results into one final result
        merged_explanation = "\n\n".join(all_explanations) if all_explanations else "No explanation returned."
        merged_mermaid = all_mermaid_nodes[0] if all_mermaid_nodes else "flowchart TD\n  A[Start] --> B[No output]"

        await queue.put({
            "type": "result",
            "explanation": merged_explanation,
            "steps": all_steps,
            "mermaid": merged_mermaid,
            "security": all_security,
        })

    except OllamaClientError as exc:
        await queue.put({"type": "error", "message": str(exc)})
    except Exception as exc:  # noqa: BLE001
        await queue.put({"type": "error", "message": f"Unexpected error: {exc}"})
    finally:
        _cleanup_staged_files(file_data)
        await queue.put(None)  # sentinel → client closes stream


# ── SSE stream endpoint ──────────────────────────────────────────────────────
@app.get("/api/stream/{job_id}", responses={404: {"description": "Job not found or already consumed."}})
async def stream_job(job_id: str):
    """Server-Sent Events stream for a running job."""
    queue = _jobs.get(job_id)
    if not queue:
        raise HTTPException(status_code=404, detail="Job not found or already consumed.")

    async def event_gen():
        idle_seconds = 0
        try:
            while True:
                try:
                    item = await asyncio.wait_for(queue.get(), timeout=JOB_STREAM_POLL_SECONDS)
                    idle_seconds = 0
                except asyncio.TimeoutError:
                    idle_seconds += JOB_STREAM_POLL_SECONDS
                    if idle_seconds >= JOB_STREAM_IDLE_TIMEOUT_SECONDS:
                        yield f'data: {json.dumps({"type": "error", "message": "Job timed out."})}\n\n'
                        break
                    yield ": keepalive\n\n"
                    continue

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
    total_uploaded = 0
    if files:
        if len(files) > MAX_FILES_PER_JOB:
            raise HTTPException(status_code=413, detail=f"Too many files. Max supported is {MAX_FILES_PER_JOB}.")
        parts: list[str] = []
        for f in files:
            content, total_uploaded = await _read_upload_content(f, total_uploaded)
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

