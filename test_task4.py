"""Validate TASK 4: Explanation structure."""
from backend.utils import _build_structured_explanation

code = (
    "import json\n\n"
    "def load_config(path):\n"
    "    with open(path) as f:\n"
    "        return json.load(f)\n\n"
    "def main():\n"
    "    config = load_config('app.json')\n"
    "    print(config)\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)

files = [("app.py", code)]
result = _build_structured_explanation(files)
print(result)
print()

assert "**Overview:**" in result
assert "**Execution Flow:**" in result
assert "**Key Functions:**" in result
assert "**Dependencies:**" in result
assert "load_config" in result
assert "main" in result
print("PASS: Structured explanation has proper sections and real function names")
print()
print("ALL TASK 4 VALIDATIONS PASSED")
