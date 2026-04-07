"""Validate TASK 5: AST Parsing is working."""
from backend.utils import extract_code_structure

code = (
    "# === File: api.py ===\n"
    "import requests\n"
    "from flask import Flask, jsonify\n\n"
    "app = Flask(__name__)\n\n"
    "class UserService:\n"
    "    def __init__(self, db):\n"
    "        self.db = db\n\n"
    "    def get_user(self, user_id):\n"
    "        return self.db.query(user_id)\n\n"
    "    async def create_user(self, data):\n"
    "        if not data.get('name'):\n"
    "            raise ValueError('Name required')\n"
    "        return self.db.insert(data)\n\n"
    "def validate_input(data):\n"
    "    if 'email' not in data:\n"
    "        return False\n"
    "    return True\n\n"
    "def main():\n"
    "    svc = UserService(db=None)\n"
    "    if validate_input({'email': 'a@b.com'}):\n"
    "        svc.create_user({'name': 'Test'})\n\n"
    "if __name__ == '__main__':\n"
    "    main()\n"
)

structure = extract_code_structure(code)
assert structure is not None, "AST extraction returned None"

# Check files parsed
files = structure["files"]
assert len(files) == 1
f = files[0]
assert f["language"] == "python"

# Check classes
classes = f["classes"]
assert len(classes) >= 1
assert classes[0]["name"] == "UserService"
methods = [m["name"] for m in classes[0]["methods"]]
assert "get_user" in methods
assert "create_user" in methods
print(f"PASS: Found class UserService with methods: {methods}")

# Check functions
funcs = [fn["name"] for fn in f["functions"]]
assert "validate_input" in funcs
assert "main" in funcs
print(f"PASS: Found top-level functions: {funcs}")

# Check call graph
call_graph = f["call_graph"]
assert "main" in call_graph
assert "validate_input" in call_graph["main"]
print(f"PASS: Call graph: {call_graph}")

# Check condition detection
validate_fn = next(fn for fn in f["functions"] if fn["name"] == "validate_input")
assert validate_fn["has_condition"] is True
print("PASS: Condition detection works")

# Check imports
imports = f["imports"]
assert any("Flask" in i for i in imports)
print(f"PASS: Imports extracted: {imports}")

# Check entry point
assert f["entry_point"] == "main"
print("PASS: Entry point detected as 'main'")

# Check summary
summary = structure["summary"]
assert summary["total_functions"] >= 2
assert summary["total_classes"] >= 1
print(f"PASS: Summary: {summary['total_functions']} functions, {summary['total_classes']} classes")

print()
print("ALL TASK 5 VALIDATIONS PASSED")
