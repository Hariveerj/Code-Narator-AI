"""
analyze_project.py
──────────────────
Automated sequential code review for an entire project directory.

Usage:
    python scripts/analyze_project.py [TARGET_DIR] [--backend URL] [--retries N]

Example:
    python scripts/analyze_project.py D:\\AI-new\\FNBLifepilot
    python scripts/analyze_project.py D:\\AI-new\\FNBLifepilot --retries 3

Progress output (per file):
    Completed: src/auth_service.py
    31 / 1271
    2.4%

What it does:
1. Pre-checks that Ollama is running and llama3.2:3b is available.
2. Walks TARGET_DIR and builds an ordered queue of all code files.
3. Sends each file individually to the Code Narrator AI backend (POST /api/upload
   → GET /api/stream/{job_id}) one at a time.
4. Run-Test-Fix: if a file causes an Ollama crash, waits 2 s and retries up to
   --retries times before marking the file as FAILED and continuing.
5. After each file prints: Completed/FAILED, counter, and percentage.
6. Writes a full report to scripts/analysis_report.txt.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

from typing import Any

import requests

# ── Code file extensions to process ─────────────────────────────────────────
CODE_EXTS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".java", ".cs", ".cpp", ".c",
    ".go", ".rb", ".php", ".swift", ".kt", ".rs", ".sh", ".bash",
    ".html", ".css", ".scss", ".vue", ".yaml", ".yml", ".json", ".md",
}

DEFAULT_BACKEND      = "http://127.0.0.1:8081"
DEFAULT_RETRIES      = 3
OLLAMA_BASE_URL      = "http://localhost:11434"
OLLAMA_MODEL         = "llama3.2:3b"
UPLOAD_TIMEOUT_SECS  = 30
STREAM_TIMEOUT_SECS  = 180
RETRY_WAIT_SECS      = 2
TIMEOUT_STEP_SECS    = 30


# ── Pre-check helpers ────────────────────────────────────────────────────────

def check_ollama() -> None:
    """Verify Ollama is reachable and the primary model is available.
    Prints a clear status line and exits with code 1 on hard failure.
    """
    print("Pre-check: verifying Ollama ...", flush=True)
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        resp.raise_for_status()
    except requests.exceptions.ConnectionError:
        print("  WARN: Ollama not reachable. Attempting: ollama run llama3.2:3b", flush=True)
        try:
            subprocess.run(
                ["ollama", "run", OLLAMA_MODEL, "health-check"],
                capture_output=True,
                text=True,
                timeout=20,
                check=False,
            )
            # Re-check after warm-up attempt.
            resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
            resp.raise_for_status()
        except Exception:
            print(
                f"  ERROR: Cannot reach Ollama at {OLLAMA_BASE_URL}.\n"
                "  Fix : run  ollama serve  in a separate terminal.",
                file=sys.stderr,
            )
            sys.exit(1)
    except Exception as exc:
        print(f"  ERROR: Ollama health check failed: {exc}", file=sys.stderr)
        sys.exit(1)

    models: list[str] = [m.get("name", "") for m in resp.json().get("models", [])]
    base_name = OLLAMA_MODEL.split(":")[0].lower()
    found = any(base_name in m.lower() for m in models)

    if found:
        print(f"  OK : Ollama running. Model '{OLLAMA_MODEL}' is available.\n", flush=True)
    else:
        available = ", ".join(models) or "(none)"
        print(
            f"  WARN: Model '{OLLAMA_MODEL}' not listed (available: {available}).\n"
            f"  Tip : run  ollama pull {OLLAMA_MODEL}  if analysis fails.\n",
            flush=True,
        )


# ── Helpers ──────────────────────────────────────────────────────────────────

def collect_files(root: Path) -> list[Path]:
    """Return all code files under root, sorted for deterministic ordering."""
    _SKIP_DIRS = {
        "node_modules", "__pycache__", ".git", "dist", "build",
        "venv", ".venv", ".mypy_cache", ".pytest_cache",
    }
    found: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in CODE_EXTS:
            continue
        if any(d.startswith(".") or d in _SKIP_DIRS for d in p.parts):
            continue
        found.append(p)
    return sorted(found)


def upload_file(backend: str, filepath: Path) -> str:
    """POST a single file to /api/upload; return job_id."""
    with filepath.open("rb") as fh:
        resp = requests.post(
            f"{backend}/api/upload",
            files={"files": (filepath.name, fh, "text/plain")},
            timeout=UPLOAD_TIMEOUT_SECS,
        )
    resp.raise_for_status()
    return resp.json()["job_id"]


def stream_result(backend: str, job_id: str, timeout_secs: int = STREAM_TIMEOUT_SECS) -> dict[str, Any]:
    """Consume SSE stream for job_id; return final result dict."""
    url = f"{backend}/api/stream/{job_id}"
    result: dict[str, Any] = {}
    deadline = time.time() + timeout_secs

    with requests.get(url, stream=True, timeout=timeout_secs) as resp:
        resp.raise_for_status()
        for raw_line in resp.iter_lines():
            if time.time() > deadline:
                raise TimeoutError("Stream timed out waiting for result.")
            if not raw_line:
                continue
            line = raw_line.decode("utf-8", errors="replace")
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                msg = json.loads(payload)
            except json.JSONDecodeError:
                continue

            if msg.get("type") == "error":
                raise RuntimeError(msg.get("message", "Unknown error from backend."))
            if msg.get("type") == "result":
                result = msg
    return result


def analyse_file(backend: str, filepath: Path, retries: int) -> tuple[bool, dict[str, Any] | str, str]:
    """Upload & stream one file with Run-Test-Fix retry loop.

    On any failure: wait RETRY_WAIT_SECS and re-attempt.
    Returns (ok, result_dict, traceback_text) or (False, error_string, traceback_text).
    """
    last_err = ""
    last_trace = ""
    total_attempts = retries + 1  # 1 initial + N retries

    for attempt in range(1, total_attempts + 1):
        timeout_secs = STREAM_TIMEOUT_SECS + ((attempt - 1) * TIMEOUT_STEP_SECS)
        try:
            job_id = upload_file(backend, filepath)
            result = stream_result(backend, job_id, timeout_secs=timeout_secs)
            return True, result, ""
        except Exception as exc:
            last_err = str(exc)
            last_trace = traceback.format_exc()
            if attempt < total_attempts:
                print(
                    f"    \u21b3 Attempt {attempt}/{total_attempts} failed: "
                    f"{last_err[:100]} \u2014 retrying in {RETRY_WAIT_SECS}s "
                    f"(stream timeout {timeout_secs}s)",
                    flush=True,
                )
                time.sleep(RETRY_WAIT_SECS)

    return False, last_err, last_trace


def print_completed(current: int, total: int, filename: str, ok: bool) -> None:
    """Print the post-file status block:

        Completed: src/auth_service.py
        31 / 1271
        2.4%
    """
    label = "Completed" if ok else "FAILED   "
    pct = 100.0 * current / total
    print(f"\n{label}: {filename}")
    print(f"{current} / {total}")
    print(f"{pct:.1f}%")


def _write_debug_entry(
    dbg: Any,
    rel: Path,
    outcome: dict[str, Any] | str,
    trace_text: str,
) -> None:
    """Append a single failure entry to the debug log."""
    dbg.write(f"[{datetime.now():%Y-%m-%d %H:%M:%S}] FAIL {rel}\n")
    dbg.write(f"  Error Type: {type(outcome).__name__}\n")
    dbg.write(f"  Error: {outcome}\n")
    if trace_text:
        dbg.write("  Traceback:\n")
        dbg.write(trace_text + "\n")
    dbg.write("-" * 70 + "\n")


def _write_summary(
    log: Any,
    dbg: Any,
    passed: list[str],
    failed: list[tuple[str, str]],
    total: int,
    started_at: datetime,
) -> None:
    """Build and write final summary to console, log, and debug file."""
    elapsed = int((datetime.now() - started_at).total_seconds())
    pct_pass = 100.0 * len(passed) / total

    summary_lines = [
        "",
        "=" * 60,
        "SUMMARY",
        f"  Passed  : {len(passed)} / {total} ({pct_pass:.1f}%)",
        f"  Failed  : {len(failed)} / {total}",
        f"  Elapsed : {elapsed}s",
    ]
    if failed:
        summary_lines.append("\nFailed files:")
        for path, err in failed:
            summary_lines.append(f"  • {path}")
            summary_lines.append(f"    {err[:120]}")

    summary = "\n".join(summary_lines) + "\n"
    print(summary)
    log.write(summary)
    dbg.write(summary)


def _check_backend(backend: str) -> None:
    """Verify the Code Narrator backend is reachable, exit on failure."""
    print("Pre-check: verifying Code Narrator backend ...", flush=True)
    try:
        hr = requests.get(f"{backend}/health", timeout=5)
        hr.raise_for_status()
        print(f"  OK : Backend running at {backend}.\n", flush=True)
    except Exception as exc:
        print(
            f"  ERROR: Backend unreachable at {backend}.\n"
            "  Fix : start the server with  python app.py\n"
            f"  Detail: {exc}",
            file=sys.stderr,
        )
        sys.exit(1)


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Automated sequential project analysis via Code Narrator AI"
    )
    parser.add_argument(
        "target",
        nargs="?",
        default=r"D:\AI-new\FNBLifepilot",
        help="Directory to analyse (default: D:\\AI-new\\FNBLifepilot)",
    )
    parser.add_argument("--backend", default=DEFAULT_BACKEND, help="Backend base URL")
    parser.add_argument(
        "--retries",
        type=int,
        default=DEFAULT_RETRIES,
        help=f"Retry count per file on Ollama failure (default: {DEFAULT_RETRIES})",
    )
    parser.add_argument(
        "--no-ollama-check",
        action="store_true",
        help="Skip the Ollama pre-check",
    )
    args = parser.parse_args()

    target = Path(args.target)
    if not target.is_dir():
        print(f"ERROR: '{target}' is not a valid directory.", file=sys.stderr)
        sys.exit(1)

    # ── 1. Pre-check: Ollama is running and model is available ───────────────
    if not args.no_ollama_check:
        check_ollama()

    # ── 2. Pre-check: CodeNarrator backend is reachable ──────────────────────
    _check_backend(args.backend)

    # ── 3. Build file queue ───────────────────────────────────────────────────
    files = collect_files(target)
    total = len(files)
    if total == 0:
        print("No code files found in the target directory.", file=sys.stderr)
        sys.exit(0)

    report_path = Path(__file__).parent / "analysis_report.txt"
    debug_log_path = Path(__file__).parent / "review_debug.log"
    started_at  = datetime.now()

    print("─" * 60)
    print("Code Narrator AI — Sequential Project Analysis")
    print(f"  Target  : {target}")
    print(f"  Model   : {OLLAMA_MODEL}")
    print(f"  Files   : {total}")
    print(f"  Backend : {args.backend}")
    print(f"  Retries : {args.retries} per file")
    print(f"  Report  : {report_path}")
    print(f"  Debug   : {debug_log_path}")
    print(f"  Started : {started_at:%Y-%m-%d %H:%M:%S}")
    print("─" * 60)

    passed: list[str] = []
    failed: list[tuple[str, str]] = []

    with report_path.open("w", encoding="utf-8") as log:
        dbg = debug_log_path.open("w", encoding="utf-8")
        log.write("Code Narrator AI — Analysis Report\n")
        log.write(f"Generated : {started_at:%Y-%m-%d %H:%M:%S}\n")
        log.write(f"Target    : {target}\n")
        log.write(f"Model     : {OLLAMA_MODEL}\n")
        log.write(f"Total     : {total} files\n")
        log.write("=" * 70 + "\n\n")

        # ── 4. Sequential for-loop: one file at a time ────────────────────────
        for idx, filepath in enumerate(files, start=1):
            rel = filepath.relative_to(target)
            print(f"\nAnalysing [{idx}/{total}]: {rel}", flush=True)

            ok, outcome, trace_text = analyse_file(args.backend, filepath, args.retries)

            # ── Required progress format ──────────────────────────────────────
            print_completed(idx, total, str(rel), ok)

            if ok and isinstance(outcome, dict):
                passed.append(str(rel))
                expl = str(outcome.get("explanation", ""))[:300]
                log.write(f"[PASS] {rel}\n")
                log.write(f"  Explanation: {expl}\n\n")
            else:
                failed.append((str(rel), str(outcome)))
                log.write(f"[FAIL] {rel}\n")
                log.write(f"  Error: {outcome}\n\n")
                _write_debug_entry(dbg, rel, outcome, trace_text)

        # ── 5. Final summary ──────────────────────────────────────────────────
        _write_summary(log, dbg, passed, failed, total, started_at)
        dbg.close()

    if failed:
        print(
            f"⚠  {len(failed)} file(s) could not be analysed. "
            f"See {report_path} for details."
        )
        sys.exit(2)
    else:
        print(
            f"✔  All {total} files analysed successfully. "
            f"Report saved to {report_path}"
        )
        sys.exit(0)


if __name__ == "__main__":
    main()
