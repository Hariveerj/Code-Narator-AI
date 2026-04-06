from __future__ import annotations

import os
import time
from typing import Any, Dict

from urllib.parse import urlparse

import requests

try:
    from .utils import build_analysis_prompt, clamp_code_size, parse_model_json
except ImportError:
    from utils import build_analysis_prompt, clamp_code_size, parse_model_json

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
        "qwen2.5:7b,deepseek-r1:latest,codellama,llama3",
    ).split(",")
    if model.strip()
]
OLLAMA_TIMEOUT_SECONDS = int(os.getenv("OLLAMA_TIMEOUT_SECONDS", "60"))

# Retry settings for transient connection / timeout failures
_RETRY_ATTEMPTS = 3
_RETRY_WAIT_SECONDS = 2


class OllamaClientError(Exception):
    """Raised when the Ollama request or response processing fails."""


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
    for attempt in range(1, _RETRY_ATTEMPTS + 2):  # 1 .. RETRY_ATTEMPTS+1 total attempts
        try:
            return _post_generate(payload)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as exc:
            if attempt <= _RETRY_ATTEMPTS:
                time.sleep(_RETRY_WAIT_SECONDS)
                continue
            _raise_connection_exhausted(exc)
        except requests.exceptions.HTTPError as exc:
            return _handle_http_error(exc, payload, model_name)
        except requests.exceptions.RequestException as exc:
            raise OllamaClientError(f"Ollama request failed: {exc}") from exc
        except ValueError as exc:
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


def analyze_code(code: str) -> Dict[str, object]:
    safe_code = clamp_code_size(code)
    prompt = build_analysis_prompt(safe_code)

    payload: dict[str, Any] = {
        "model": OLLAMA_MODEL,
        "prompt": prompt,
        "stream": False,
    }

    data = _request_ollama(payload, OLLAMA_MODEL)

    model_output = str(data.get("response", "")).strip()
    if not model_output:
        raise OllamaClientError("Ollama returned an empty response.")

    try:
        return parse_model_json(model_output)
    except Exception as exc:  # noqa: BLE001 - include response context in error
        raise OllamaClientError(
            "Failed to parse model output into expected JSON structure. "
            "Try the request again or use a code-focused model like codellama."
        ) from exc
