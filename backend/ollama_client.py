from __future__ import annotations

import hashlib
import os
import re
import time
from collections import OrderedDict
from copy import deepcopy
from typing import Any, Dict, cast

from urllib.parse import urlparse

import requests

import logging

try:
    from .utils import build_analysis_prompt, build_structured_analysis_prompt, clamp_code_size, parse_model_json, extract_code_structure
except ImportError:
    from utils import build_analysis_prompt, build_structured_analysis_prompt, clamp_code_size, parse_model_json, extract_code_structure

logger = logging.getLogger(__name__)

_RAW_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
_parsed = urlparse(_RAW_BASE_URL)
# Allow localhost, loopback, and Docker-internal hostnames (no dots = not routable)
_hostname = _parsed.hostname or ""
_is_local = _hostname in ("localhost", "127.0.0.1", "::1")
_is_docker_internal = "." not in _hostname and _hostname != ""
if not (_is_local or _is_docker_internal):
    raise RuntimeError(
        f"OLLAMA_BASE_URL must point to localhost or a Docker service name, got: {_hostname}. "
        "Set OLLAMA_BASE_URL=http://localhost:11434 to fix."
    )
OLLAMA_BASE_URL = _RAW_BASE_URL
OLLAMA_URL = os.getenv("OLLAMA_URL", f"{OLLAMA_BASE_URL}/api/generate")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.2:3b")
OLLAMA_FALLBACK_MODELS = [
    model.strip()
    for model in os.getenv(
        "OLLAMA_FALLBACK_MODELS",
        "qwen2.5:7b,llama3.2:3b,codellama,llama3",
    ).split(",")
    if model.strip()
]
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "600"))
OLLAMA_TEMPERATURE = float(os.getenv("OLLAMA_TEMPERATURE", "0.2"))
OLLAMA_TOP_P = float(os.getenv("OLLAMA_TOP_P", "0.8"))
OLLAMA_SEED = int(os.getenv("OLLAMA_SEED", "42"))

# Retry settings for LLM validation failures (empty/bad JSON)
LLM_VALIDATION_RETRIES = int(os.getenv("LLM_VALIDATION_RETRIES", "3"))

ANALYSIS_CACHE_TTL_SECONDS = int(os.getenv("ANALYSIS_CACHE_TTL_SECONDS", "1800"))
ANALYSIS_CACHE_MAX_ITEMS = int(os.getenv("ANALYSIS_CACHE_MAX_ITEMS", "256"))
CIRCUIT_BREAKER_FAILURE_THRESHOLD = int(os.getenv("CIRCUIT_BREAKER_FAILURE_THRESHOLD", "5"))
CIRCUIT_BREAKER_COOLDOWN_SECONDS = int(os.getenv("CIRCUIT_BREAKER_COOLDOWN_SECONDS", "30"))

# Retry settings for transient connection / timeout failures
_RETRY_ATTEMPTS = 3
_RETRY_WAIT_SECONDS = 2

_analysis_cache: OrderedDict[str, tuple[float, dict[str, object]]] = OrderedDict()
_consecutive_failures = 0
_circuit_open_until = 0.0


class OllamaClientError(Exception):
    """Raised when the Ollama request or response processing fails."""


def _cache_get(cache_key: str) -> dict[str, object] | None:
    now = time.time()
    hit = _analysis_cache.get(cache_key)
    if hit is None:
        return None
    ts, value = hit
    if now - ts > ANALYSIS_CACHE_TTL_SECONDS:
        _analysis_cache.pop(cache_key, None)
        return None
    _analysis_cache.move_to_end(cache_key)
    return deepcopy(value)


def _cache_set(cache_key: str, value: dict[str, object]) -> None:
    _analysis_cache[cache_key] = (time.time(), deepcopy(value))
    _analysis_cache.move_to_end(cache_key)
    while len(_analysis_cache) > ANALYSIS_CACHE_MAX_ITEMS:
        _analysis_cache.popitem(last=False)


def _record_failure() -> None:
    global _consecutive_failures, _circuit_open_until
    _consecutive_failures += 1
    if _consecutive_failures >= CIRCUIT_BREAKER_FAILURE_THRESHOLD:
        _circuit_open_until = time.time() + CIRCUIT_BREAKER_COOLDOWN_SECONDS


def _record_success() -> None:
    global _consecutive_failures
    _consecutive_failures = 0


def _ensure_circuit_closed() -> None:
    if time.time() < _circuit_open_until:
        raise OllamaClientError(
            "Ollama circuit breaker is open due to repeated failures. "
            "Please retry in a few seconds."
        )


def _post_generate(payload: dict[str, Any]) -> dict[str, Any]:
    response = requests.post(OLLAMA_URL, json=payload, timeout=OLLAMA_TIMEOUT_SECONDS)
    response.raise_for_status()
    data = response.json()
    return data


def _is_model_not_found_error(exc: requests.exceptions.HTTPError) -> bool:
    return exc.response is not None and exc.response.status_code == 404 and "model" in (exc.response.text or "").lower()


def _http_error_detail(exc: requests.exceptions.HTTPError) -> str:
    response_text = ""
    if exc.response is not None and exc.response.text:
        response_text = exc.response.text.strip()
    detail = f"Ollama returned HTTP error: {exc}"
    if response_text:
        detail = f"{detail}. Response: {response_text}"
    return detail


def _model_not_found_detail(model_name: str, exc: requests.exceptions.HTTPError) -> str:
    response_text = ""
    if exc.response is not None and exc.response.text:
        response_text = exc.response.text.strip()
    detail = f"Ollama model '{model_name}' was not found"
    if OLLAMA_FALLBACK_MODELS:
        detail = f"{detail}. Tried fallbacks: {', '.join(OLLAMA_FALLBACK_MODELS)}"
    if response_text:
        detail = f"{detail}. Response: {response_text}"
    return detail


def _try_with_fallback_models(payload: dict[str, Any], initial_model: str) -> dict[str, Any] | None:
    for fallback_model in OLLAMA_FALLBACK_MODELS:
        if fallback_model == initial_model:
            continue
        retry_payload = dict(payload)
        retry_payload["model"] = fallback_model
        try:
            return _post_generate(retry_payload)
        except requests.exceptions.RequestException:
            continue
    return None


def _raise_connection_exhausted(exc: Exception) -> None:
    """Raise OllamaClientError for connection/timeout after retries exhausted."""
    if isinstance(exc, requests.exceptions.ConnectionError):
        raise OllamaClientError(
            f"Could not connect to Ollama after {_RETRY_ATTEMPTS} retries. "
            "Make sure Ollama is running at http://localhost:11434 and the model is loaded."
        ) from exc
    raise OllamaClientError(
        f"Ollama request timed out after {_RETRY_ATTEMPTS} retries. "
        "Try a smaller file or increase OLLAMA_TIMEOUT_SECONDS."
    ) from exc


def _handle_http_error(exc: requests.exceptions.HTTPError, payload: dict[str, Any], model_name: str) -> dict[str, Any]:
    """Handle HTTPError: try fallbacks for model-not-found, else raise."""
    if _is_model_not_found_error(exc):
        fallback_data = _try_with_fallback_models(payload, model_name)
        if fallback_data is not None:
            return fallback_data
        raise OllamaClientError(_model_not_found_detail(model_name, exc)) from exc
    raise OllamaClientError(_http_error_detail(exc)) from exc


def _request_ollama(payload: dict[str, Any], model_name: str) -> dict[str, Any]:
    """Call Ollama with automatic retry on transient connection/timeout errors."""
    _ensure_circuit_closed()
    for attempt in range(1, _RETRY_ATTEMPTS + 2):  # 1 .. RETRY_ATTEMPTS+1 total attempts
        try:
            data = _post_generate(payload)
            _record_success()
            return data
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt <= _RETRY_ATTEMPTS:
                time.sleep(_RETRY_WAIT_SECONDS)
                continue
            _record_failure()
            _raise_connection_exhausted(exc)
        except requests.exceptions.HTTPError as exc:
            _record_failure()
            return _handle_http_error(exc, payload, model_name)
        except requests.exceptions.RequestException as exc:
            _record_failure()
            raise OllamaClientError(f"Ollama request failed: {exc}") from exc
        except ValueError as exc:
            _record_failure()
            raise OllamaClientError("Received invalid JSON from Ollama API.") from exc
    # Unreachable — all paths above either return or raise
    raise OllamaClientError("Ollama request failed after all retries.")


def precheck_ollama() -> tuple[bool, str]:
    """Verify Ollama is reachable and the primary model is downloaded.

    Returns (ok, message).
    """
    try:
        resp = requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        resp.raise_for_status()
        models: list[str] = [m.get("name", "") for m in resp.json().get("models", [])]
        # Match by base name (before ':') to handle :latest vs :3b differences
        base = OLLAMA_MODEL.split(":")[0].lower()
        found = any(base in m.lower() for m in models)
        if found:
            return True, f"Ollama is running. Model '{OLLAMA_MODEL}' is available."
        model_list = ", ".join(models) or "(none downloaded)"
        return False, (
            f"Ollama is running but model '{OLLAMA_MODEL}' was not found. "
            f"Available: {model_list}. Run: ollama pull {OLLAMA_MODEL}"
        )
    except requests.exceptions.ConnectionError:
        return False, "Cannot reach Ollama at http://localhost:11434. Run: ollama serve"
    except Exception as exc:  # noqa: BLE001
        return False, f"Ollama pre-check failed: {exc}"


def _validate_llm_output(parsed: dict[str, object]) -> list[str]:
    """Return a list of quality issues found in the parsed output."""
    issues: list[str] = []
    overview = str(parsed.get("overview", "")).strip()
    if not overview or len(overview.split()) < 20 or len(overview) < 80:
        issues.append("Overview is too short or empty (need ≥20 words / ≥80 chars).")
    flow_steps: object = parsed.get("flow_steps", [])
    if not isinstance(flow_steps, list) or len(cast(list[object], flow_steps)) < 3:
        issues.append("Flow steps list is missing or too short (need ≥3).")
    class_diagram = str(parsed.get("class_diagram", "")).strip()
    if not class_diagram or "flowchart" not in class_diagram.lower():
        issues.append("Class diagram is missing or invalid.")
    else:
        node_labels = re.findall(r"\[(.*?)\]|\{(.*?)\}|\(\[(.*?)\]\)", class_diagram)
        flat = [p for g in node_labels for p in g if p]
        if len(flat) < 4:
            issues.append(f"Class diagram has only {len(flat)} nodes (need ≥4).")
    classes: object = parsed.get("classes", [])
    if not isinstance(classes, list) or len(cast(list[object], classes)) < 1:
        issues.append("Classes list is missing or empty.")
    detailed_logic = str(parsed.get("detailed_logic", "")).strip()
    if not detailed_logic or len(detailed_logic.split()) < 15:
        issues.append("Detailed logic is too short or empty (need ≥15 words).")
    return issues


def _build_attempt_prompt(
    safe_code: str,
    code_structure: dict[str, Any] | None,
    attempt: int,
    last_issues: list[str],
) -> str:
    """Build the prompt for a given analysis attempt."""
    if attempt == 1 and code_structure:
        return build_structured_analysis_prompt(safe_code, code_structure)
    hint = ""
    if last_issues:
        hint = (
            f"RETRY {attempt}/{LLM_VALIDATION_RETRIES}. Previous issues: {'; '.join(last_issues)}. "
            "You MUST fix these issues. Return COMPLETE, DETAILED output."
        )
    return build_analysis_prompt(safe_code, retry_hint=hint)


def _fetch_llm_response(
    prompt: str,
    attempt: int,
) -> str | None:
    """Send prompt to Ollama and return raw model output, or None if empty."""
    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": OLLAMA_TEMPERATURE + (0.05 * (attempt - 1)),
            "top_p": OLLAMA_TOP_P,
            "seed": OLLAMA_SEED + (attempt - 1),
        },
    }
    logger.info("Attempt %d/%d: sending %d-char prompt to %s", attempt, LLM_VALIDATION_RETRIES, len(prompt), OLLAMA_MODEL)
    t0 = time.time()
    data = _request_ollama(payload, OLLAMA_MODEL)
    elapsed = time.time() - t0
    model_output = str(data.get("response", "")).strip()
    logger.info("Attempt %d/%d: got %d-char response in %.1fs", attempt, LLM_VALIDATION_RETRIES, len(model_output), elapsed)
    return model_output or None


def _process_attempt_output(
    model_output: str,
    safe_code: str,
    cache_key: str,
    attempt: int,
) -> tuple[Dict[str, object] | None, list[str]]:
    """Parse and validate model output. Returns (cached_result, issues)."""
    parsed = parse_model_json(model_output, safe_code)
    quality_issues = _validate_llm_output(parsed)

    if not quality_issues:
        _cache_set(cache_key, parsed)
        return parsed, []

    if attempt == LLM_VALIDATION_RETRIES:
        logger.warning("Accepting output with issues after %d attempts: %s", attempt, quality_issues)
        _cache_set(cache_key, parsed)
        return parsed, quality_issues

    return None, quality_issues


def analyze_code(code: str) -> Dict[str, object]:
    safe_code = clamp_code_size(code)
    cache_key = hashlib.sha256(f"{OLLAMA_MODEL}|{safe_code}".encode("utf-8")).hexdigest()

    cached = _cache_get(cache_key)
    if cached is not None:
        return cached

    code_structure = extract_code_structure(safe_code)
    last_issues: list[str] = []

    for attempt in range(1, LLM_VALIDATION_RETRIES + 1):
        prompt = _build_attempt_prompt(safe_code, code_structure, attempt, last_issues)

        try:
            model_output = _fetch_llm_response(prompt, attempt)
            if model_output is None:
                last_issues = ["Ollama returned an empty response."]
                logger.warning("Attempt %d/%d: empty response", attempt, LLM_VALIDATION_RETRIES)
                continue

            accepted, issues = _process_attempt_output(model_output, safe_code, cache_key, attempt)
            if accepted is not None:
                return accepted
            last_issues = issues
            logger.info("Attempt %d/%d quality issues: %s", attempt, LLM_VALIDATION_RETRIES, issues)

        except OllamaClientError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_issues = [str(exc)]
            logger.warning("Attempt %d/%d parse error: %s", attempt, LLM_VALIDATION_RETRIES, exc)
            if attempt == LLM_VALIDATION_RETRIES:
                raise OllamaClientError(
                    f"Failed to get valid output after all retries. Last error: {exc}"
                ) from exc

    raise OllamaClientError(
        f"Failed to get valid output after {LLM_VALIDATION_RETRIES} retries. "
        f"Last issues: {'; '.join(last_issues)}"
    )
