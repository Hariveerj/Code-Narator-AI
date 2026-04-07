from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
import uuid
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Annotated, Any, List, cast

import aiofiles
from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
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
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(100 * 1024 * 1024)))  # 100 MB
MAX_FILE_BYTES = int(os.getenv("MAX_FILE_BYTES", str(50 * 1024 * 1024)))       # 50 MB per file
MAX_FILES_PER_JOB = int(os.getenv("MAX_FILES_PER_JOB", "1500"))
UPLOAD_CHUNK_BYTES = int(os.getenv("UPLOAD_CHUNK_BYTES", str(1024 * 1024)))
FILE_SNIPPET_CHARS = int(os.getenv("FILE_SNIPPET_CHARS", "30000"))  # reduced per-file to avoid OOM
UPLOAD_TMP_DIR = Path(os.getenv("UPLOAD_TMP_DIR", str(Path(tempfile.gettempdir()) / "codenarrator_uploads")))
UPLOAD_TMP_DIR.mkdir(parents=True, exist_ok=True)

# Rate limiting state
_rate_limit_window = int(os.getenv("RATE_LIMIT_WINDOW_SECONDS", "60"))
_rate_limit_max = int(os.getenv("RATE_LIMIT_MAX_REQUESTS", "20"))
_rate_limits: dict[str, list[float]] = {}

# Allowed file extensions
_ALLOWED_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cs", ".cpp", ".c", ".h",
    ".hpp", ".go", ".rb", ".php", ".swift", ".kt", ".rs", ".txt", ".html",
    ".css", ".scss", ".json", ".yaml", ".yml", ".md", ".sh", ".bash", ".vue",
    ".xml", ".sql", ".r", ".scala", ".dart", ".lua", ".pl", ".pm",
}

app = FastAPI(title="Code Narrator AI", version="3.0.0")

# Gzip compression for responses
app.add_middleware(GZipMiddleware, minimum_size=500)

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
async def security_headers(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    return response



@app.middleware("http")
async def rate_limiter(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
    if request.url.path.startswith("/api/upload") or request.url.path.startswith("/api/analyze") or request.url.path == "/analyze":
        client_ip = request.client.host if request.client else "unknown"
        # Skip rate limiting for localhost/test clients
        if client_ip not in ("127.0.0.1", "::1", "localhost", "testclient", "unknown"):
            now = time.time()
            window_start = now - _rate_limit_window
            timestamps = _rate_limits.get(client_ip, [])
            timestamps = [t for t in timestamps if t > window_start]
            if len(timestamps) >= _rate_limit_max:
                return JSONResponse(
                    status_code=429,
                    content={"detail": f"Rate limit exceeded. Max {_rate_limit_max} requests per {_rate_limit_window}s."},
                )
            timestamps.append(now)
            _rate_limits[client_ip] = timestamps
    return await call_next(request)


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


def _is_allowed_file(filename: str) -> bool:
    ext = os.path.splitext(filename)[1].lower()
    return ext in _ALLOWED_EXTENSIONS


def _cleanup_staged_files(file_refs: list[tuple[str, Path]]) -> None:
    for _, path in file_refs:
        try:
            path.unlink(missing_ok=True)
        except Exception:
            continue


async def _write_upload_chunks(
    upload: UploadFile,
    tmp_path: Path,
    filename: str,
    total_uploaded_ref: list[int],
) -> int:
    """Stream upload chunks to disk, enforcing size limits. Returns bytes written."""
    written = 0
    async with aiofiles.open(tmp_path, "wb") as out:
        while True:
            chunk = await upload.read(UPLOAD_CHUNK_BYTES)
            if not chunk:
                break
            written += len(chunk)
            total_uploaded_ref[0] += len(chunk)
            if written > MAX_FILE_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"File '{filename}' exceeds max file size ({MAX_FILE_BYTES} bytes).",
                )
            if total_uploaded_ref[0] > MAX_UPLOAD_BYTES:
                raise HTTPException(
                    status_code=413,
                    detail=f"Total upload exceeds max payload ({MAX_UPLOAD_BYTES} bytes).",
                )
            await out.write(chunk)
    return written


def _is_empty_file(tmp_path: Path, written: int) -> bool:
    """Check if a staged file is empty or whitespace-only."""
    if written == 0:
        return True
    if written < 1024:
        text = tmp_path.read_text(encoding="utf-8", errors="replace").strip()
        return not text
    return False


async def _stage_upload_files(job_id: str, files: List[UploadFile]) -> list[tuple[str, Path]]:
    if len(files) > MAX_FILES_PER_JOB:
        raise HTTPException(status_code=413, detail=f"Too many files. Max supported is {MAX_FILES_PER_JOB}.")

    staged: list[tuple[str, Path]] = []
    total_uploaded_ref = [0]

    try:
        for idx, upload in enumerate(files):
            filename = upload.filename or f"unknown_{idx}"
            if not _is_allowed_file(filename):
                await upload.close()
                continue
            safe_filename = _safe_name(filename)
            tmp_path = UPLOAD_TMP_DIR / f"{job_id}_{idx}_{safe_filename}"

            written = await _write_upload_chunks(upload, tmp_path, filename, total_uploaded_ref)
            await upload.close()

            if _is_empty_file(tmp_path, written):
                tmp_path.unlink(missing_ok=True)
                continue
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


def _extract_security_findings(security_issues: object) -> list[dict[str, str]]:
    """Extract normalized security findings from a result."""
    findings: list[dict[str, str]] = []
    if not isinstance(security_issues, list):
        return findings
    for finding in cast(list[object], security_issues):
        if isinstance(finding, dict):
            finding_map = cast(dict[str, object], finding)
            findings.append({
                "severity": str(finding_map.get("severity", "INFO")),
                "issue": str(finding_map.get("issue", "")),
                "detail": str(finding_map.get("detail", "")),
            })
    return findings


def _extract_class_entries(classes: object) -> list[dict[str, Any]]:
    """Extract class entries from a result."""
    entries: list[dict[str, Any]] = []
    if not isinstance(classes, list):
        return entries
    for cls in cast(list[object], classes):
        if isinstance(cls, dict):
            entries.append(cast(dict[str, Any], cls))
    return entries


def _merge_batch_result(
    result: dict[str, object],
    batch_idx: int,
    filenames: list[str],
    all_overviews: list[str],
    all_flow_steps: list[str],
    all_security_issues: list[dict[str, str]],
    all_class_diagrams: list[str],
    all_classes: list[dict[str, Any]],
    all_detailed_logic: list[str],
) -> None:
    batch_label = f"**Batch {batch_idx}** ({', '.join(filenames)})"

    overview = str(result.get("overview", "")).strip()
    if overview:
        all_overviews.append(f"{batch_label}: {overview}")

    flow_steps = result.get("flow_steps", [])
    if isinstance(flow_steps, list):
        for step in cast(list[object], flow_steps):
            step_text = str(step).strip()
            if step_text:
                all_flow_steps.append(step_text)

    all_security_issues.extend(_extract_security_findings(result.get("security_issues", [])))

    class_diagram = str(result.get("class_diagram", "")).strip()
    if class_diagram:
        all_class_diagrams.append(class_diagram)

    all_classes.extend(_extract_class_entries(result.get("classes", [])))

    detailed_logic = str(result.get("detailed_logic", "")).strip()
    if detailed_logic:
        all_detailed_logic.append(f"{batch_label}: {detailed_logic}")


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
        all_overviews: list[str] = []
        all_flow_steps: list[str] = []
        all_security_issues: list[dict[str, str]] = []
        all_class_diagrams: list[str] = []
        all_classes: list[dict[str, Any]] = []
        all_detailed_logic: list[str] = []

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
                all_overviews,
                all_flow_steps,
                all_security_issues,
                all_class_diagrams,
                all_classes,
                all_detailed_logic,
            )

            await asyncio.sleep(0)

        # Merge all batch results into one final result
        merged_overview = "\n\n".join(all_overviews) if all_overviews else "No overview returned."
        merged_diagram = all_class_diagrams[0] if all_class_diagrams else "flowchart TD\n  A[Start] --> B[No output]"
        merged_logic = "\n\n".join(all_detailed_logic) if all_detailed_logic else "No detailed logic returned."

        await queue.put({
            "type": "result",
            "overview": merged_overview,
            "flow_steps": all_flow_steps,
            "class_diagram": merged_diagram,
            "classes": all_classes,
            "detailed_logic": merged_logic,
            "security_issues": all_security_issues,
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

