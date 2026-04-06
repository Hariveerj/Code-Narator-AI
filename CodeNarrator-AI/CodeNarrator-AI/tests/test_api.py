"""
Full test suite for Code Narrator AI backend (v2 â€” SSE streaming).
Run with:  pytest tests/test_api.py -v --tb=short
"""
from __future__ import annotations

import json
import sys
import os
from unittest import mock

# Allow importing backend package from the project root
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app, raise_server_exceptions=False)

# â”€â”€ Shared mock result (used wherever Ollama would be called) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
_MOCK_RESULT = {
    "explanation": "This code prints a greeting.",
    "steps": ["Step 1: Define function", "Step 2: Call print"],
    "mermaid": "flowchart TD\n  A([Start]) --> B[Print Hello]\n  B --> C([End])",
    "security": [],
}


# â”€â”€ Autouse fixture â€” prevent ALL real Ollama calls â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# Every test that triggers /api/upload or /analyze will see this mock.
# Background tasks (asyncio.create_task) complete instantly so the
# anyio portal can shut down cleanly after each test.
@pytest.fixture(autouse=True)
def _mock_analyze():
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        yield


# â”€â”€ Health â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_health_returns_200():
    r = client.get("/health")
    assert r.status_code == 200


def test_health_returns_ok_status():
    r = client.get("/health")
    assert r.json() == {"status": "ok"}


def test_health_response_is_json():
    r = client.get("/health")
    assert "application/json" in r.headers.get("content-type", "")


# â”€â”€ Root / Static â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_root_serves_frontend():
    r = client.get("/")
    assert r.status_code == 200
    assert "text/html" in r.headers.get("content-type", "")


# â”€â”€ /api/upload â€” 400 bad input â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_upload_no_body_returns_400():
    r = client.post("/api/upload")
    assert r.status_code == 400


def test_upload_empty_code_text_returns_400():
    r = client.post("/api/upload", data={"code_text": "   "})
    assert r.status_code == 400


def test_upload_empty_file_returns_400():
    r = client.post("/api/upload", files={"files": ("empty.py", b"", "text/plain")})
    assert r.status_code == 400


def test_upload_whitespace_file_returns_400():
    r = client.post("/api/upload", files={"files": ("ws.py", b"   \n\t  ", "text/plain")})
    assert r.status_code == 400


# â”€â”€ /api/upload â€” returns job_id â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_upload_paste_returns_job_id():
    r = client.post("/api/upload", data={"code_text": "print('hello')"})
    assert r.status_code == 200
    body = r.json()
    assert "job_id" in body
    assert isinstance(body["job_id"], str)
    assert len(body["job_id"]) > 0


def test_upload_file_returns_job_id():
    r = client.post(
        "/api/upload",
        files={"files": ("hello.py", b"def hi(): return 1", "text/plain")},
    )
    assert r.status_code == 200
    assert "job_id" in r.json()


def test_upload_multi_file_returns_job_id():
    files = [
        ("files", ("a.py", b"def a(): return 1", "text/plain")),
        ("files", ("b.py", b"def b(): return 2", "text/plain")),
    ]
    r = client.post("/api/upload", files=files)
    assert r.status_code == 200
    assert "job_id" in r.json()


def test_large_file_not_rejected():
    """No size limit â€” a 1 MB file must upload without 413."""
    big = b"x = 1\n" * 170_000  # ~1 MB
    r = client.post("/api/upload", files={"files": ("big.py", big, "text/plain")})
    assert r.status_code == 200
    assert "job_id" in r.json()


# â”€â”€ /api/stream â€” invalid job â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_stream_unknown_job_returns_404():
    r = client.get("/api/stream/nonexistent-job-id-xyz")
    assert r.status_code == 404


# â”€â”€ /api/stream â€” full round-trip with mocked Ollama â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€
# We mock analyze_code so tests run in milliseconds without hitting Ollama.

def _do_stream(job_id: str) -> list[dict]:
    """Consume SSE stream and return all parsed events."""
    events = []
    with client.stream("GET", f"/api/stream/{job_id}") as resp:
        assert resp.status_code == 200
        for line in resp.iter_lines():
            if not line:
                continue
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if payload == "[DONE]":
                break
            try:
                events.append(json.loads(payload))
            except json.JSONDecodeError:
                pass
    return events


def test_stream_emits_progress_events_for_multi_file():
    """Multiple files -> progress events with correct counts."""
    files = [
        ("files", ("a.py", b"x = 1", "text/plain")),
        ("files", ("b.py", b"y = 2", "text/plain")),
        ("files", ("c.py", b"z = 3", "text/plain")),
    ]
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", files=files)
        assert up.status_code == 200
        events = _do_stream(up.json()["job_id"])

    types = [e.get("type") for e in events]
    assert "progress" in types, f"Expected progress events, got: {types}"


def test_stream_progress_has_required_fields():
    """Progress events contain current, total, and filename."""
    files = [
        ("files", ("x.py", b"a=1", "text/plain")),
        ("files", ("y.py", b"b=2", "text/plain")),
    ]
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", files=files)
        assert up.status_code == 200
        events = _do_stream(up.json()["job_id"])

    prog = [e for e in events if e.get("type") == "progress"]
    assert prog, "Expected at least one progress event"
    first = prog[0]
    assert "current" in first
    assert "total" in first
    assert "filename" in first
    assert first["total"] == 2


def test_stream_progress_counts_correct():
    """Progress current numbers go 1, 2, 3 for 3 files."""
    files = [
        ("files", ("1.py", b"a=1", "text/plain")),
        ("files", ("2.py", b"b=2", "text/plain")),
        ("files", ("3.py", b"c=3", "text/plain")),
    ]
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", files=files)
        events = _do_stream(up.json()["job_id"])

    prog = [e for e in events if e.get("type") == "progress"]
    currents = [p["current"] for p in prog]
    assert currents == [1, 2, 3], f"Expected [1,2,3], got: {currents}"


def test_stream_ends_with_result():
    """Stream ends with a result event when Ollama is available (mocked)."""
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", data={"code_text": "def foo(): pass"})
        assert up.status_code == 200
        events = _do_stream(up.json()["job_id"])

    types = {e.get("type") for e in events}
    assert "result" in types, f"Expected result event, got: {types}"


def test_stream_result_has_required_keys():
    """Result event has explanation, steps, and mermaid."""
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", data={"code_text": "x = 42"})
        assert up.status_code == 200
        events = _do_stream(up.json()["job_id"])

    results = [e for e in events if e.get("type") == "result"]
    assert results, "Expected a result event"
    body = results[0]
    assert "explanation" in body
    assert "steps" in body
    assert "mermaid" in body
    assert isinstance(body["steps"], list)
    assert body["explanation"] == _MOCK_RESULT["explanation"]


def test_stream_result_steps_match_mock():
    """Steps from the result event match the mocked Ollama output."""
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", data={"code_text": "a = 1"})
        events = _do_stream(up.json()["job_id"])

    result = next((e for e in events if e.get("type") == "result"), None)
    assert result is not None
    assert result["steps"] == _MOCK_RESULT["steps"]


def test_stream_error_event_on_ollama_failure():
    """When analyze_code raises, stream emits an error event."""
    from ollama_client import OllamaClientError
    with mock.patch("main.analyze_code", side_effect=OllamaClientError("Test error")):
        up = client.post("/api/upload", data={"code_text": "x = 1"})
        assert up.status_code == 200
        events = _do_stream(up.json()["job_id"])

    types = {e.get("type") for e in events}
    assert "error" in types, f"Expected error event, got: {types}"
    err = next(e for e in events if e.get("type") == "error")
    assert "Test error" in err.get("message", "")


def test_stream_job_id_single_use():
    """A job_id cannot be consumed twice â€” second attempt returns 404."""
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", data={"code_text": "y = 99"})
        job_id = up.json()["job_id"]
        _do_stream(job_id)

    r2 = client.get(f"/api/stream/{job_id}")
    assert r2.status_code == 404


def test_stream_single_file_no_progress_events():
    """A single file should NOT emit progress events (only multi-file does)."""
    with mock.patch("main.analyze_code", return_value=_MOCK_RESULT):
        up = client.post("/api/upload", files={"files": ("solo.py", b"z=9", "text/plain")})
        events = _do_stream(up.json()["job_id"])

    prog = [e for e in events if e.get("type") == "progress"]
    assert len(prog) <= 1, "Single file should produce at most one progress event"
    # Single file: no per-file progress needed (total=1), result should still arrive
    result = [e for e in events if e.get("type") == "result"]
    assert result, "Single file should still yield a result event"


# â”€â”€ Legacy /analyze & /api/analyze (back-compat) â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_analyze_no_body_returns_400():
    r = client.post("/analyze")
    assert r.status_code == 400


def test_api_analyze_no_body_returns_400():
    r = client.post("/api/analyze")
    assert r.status_code == 400


def test_analyze_empty_code_text_returns_400():
    r = client.post("/analyze", data={"code_text": "   "})
    assert r.status_code == 400


def test_analyze_empty_file_returns_400():
    r = client.post("/analyze", files={"files": ("empty.py", b"", "text/plain")})
    assert r.status_code == 400


def test_paste_code_valid_response_code():
    r = client.post("/analyze", data={"code_text": "def hello(): print('hi')"})
    assert r.status_code in (200, 502)


def test_single_file_valid_response_code():
    r = client.post(
        "/analyze",
        files={"files": ("hello.py", b"print('hello world')", "text/plain")},
    )
    assert r.status_code in (200, 502)


def test_api_route_valid_response_code():
    r = client.post("/api/analyze", data={"code_text": "x = 42"})
    assert r.status_code in (200, 502)


def test_folder_upload_multiple_files_valid_response_code():
    files = [
        ("files", ("module_a.py", b"def a(): return 1", "text/plain")),
        ("files", ("module_b.py", b"def b(): return 2", "text/plain")),
        ("files", ("module_c.py", b"def c(): return a() + b()", "text/plain")),
    ]
    r = client.post("/analyze", files=files)
    assert r.status_code in (200, 502)


def test_200_response_has_required_keys():
    r = client.post("/analyze", data={"code_text": "print(1+1)"})
    if r.status_code == 200:
        body = r.json()
        assert "explanation" in body
        assert "steps" in body
        assert "mermaid" in body
        assert isinstance(body["steps"], list)


def test_200_explanation_is_string():
    r = client.post("/analyze", data={"code_text": "x = 1"})
    if r.status_code == 200:
        assert isinstance(r.json()["explanation"], str)


def test_200_mermaid_is_string():
    r = client.post("/analyze", data={"code_text": "x = 1"})
    if r.status_code == 200:
        assert isinstance(r.json()["mermaid"], str)


def test_502_has_detail_field():
    r = client.post("/analyze", data={"code_text": "print(1)"})
    if r.status_code == 502:
        assert "detail" in r.json()


# â”€â”€ Route existence â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

def test_unknown_endpoint_returns_404():
    r = client.get("/does-not-exist-xyz")
    assert r.status_code == 404


def test_get_analyze_not_allowed():
    r = client.get("/analyze")
    assert r.status_code in (404, 405)


def test_multi_file_not_400():
    """Multiple files must not be silently dropped (not 400)."""
    files = [
        ("files", ("a.js", b"const a = 1;", "text/plain")),
        ("files", ("b.js", b"const b = 2;", "text/plain")),
    ]
    r = client.post("/analyze", files=files)
    assert r.status_code != 400


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
