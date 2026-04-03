import json
import re
from typing import Any, Dict

MAX_CODE_CHARS = 120_000


def clamp_code_size(code: str, limit: int = MAX_CODE_CHARS) -> str:
    """Keep payload size bounded to reduce latency and memory use."""
    if len(code) <= limit:
        return code
    return code[:limit]


def build_analysis_prompt(code: str) -> str:
    return (
        "You are an expert code analysis assistant. Analyse the given code and return ONLY a valid JSON object "
        "— no markdown fences, no extra text before or after the JSON.\n\n"
        "Required JSON structure (all three keys must be present):\n"
        "{\n"
        '  "explanation": "2-3 sentence plain-English description of what the code does",\n'
        '  "steps": ["Step 1: ...", "Step 2: ...", ...],\n'
        '  "mermaid": "flowchart TD\\n  A[Start] --> B[Process]\\n  B --> C[End]"\n'
        "}\n\n"
        "Mermaid diagram rules — follow STRICTLY:\n"
        "  • First line of the mermaid value MUST be: flowchart TD\n"
        "  • Use A[Label] for regular nodes, X{Condition?} for decisions, S([Start/End]) for terminals\n"
        "  • Arrow syntax: A --> B  (space around arrows, NO semicolons between nodes)\n"
        "  • Node labels: maximum 5 words, no quotes, no parentheses, no special chars\n"
        "  • Include 5-14 nodes total\n"
        "  • Do NOT wrap the mermaid string in markdown fences (no ```mermaid)\n\n"
        "Code to analyse:\n"
        f"{code}"
    )


def _extract_json_blob(text: str) -> str:
    text = text.strip()

    # Prefer fenced JSON blocks if present.
    fenced = re.search(r"```(?:json)?\s*(\{[\s\S]*\})\s*```", text, flags=re.IGNORECASE)
    if fenced:
        return fenced.group(1)

    # Fall back to first object-like payload.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]

    raise ValueError("No JSON object found in model output.")


def parse_model_json(text: str) -> Dict[str, Any]:
    blob = _extract_json_blob(text)
    parsed = json.loads(blob)

    explanation = str(parsed.get("explanation", "")).strip()
    steps_raw = parsed.get("steps", [])
    mermaid = str(parsed.get("mermaid", "")).strip()

    if not explanation:
        explanation = "No explanation returned by model."

    if not isinstance(steps_raw, list):
        steps = [str(steps_raw).strip()] if str(steps_raw).strip() else []
    else:
        steps = [str(step).strip() for step in steps_raw if str(step).strip()]

    if not mermaid:
        mermaid = "flowchart TD; A[Start] --> B[No Mermaid output];"

    return {
        "explanation": explanation,
        "steps": steps,
        "mermaid": mermaid,
    }
