"""Analyze test suite for duplicates and redundancy."""
import re
from collections import Counter

tests = []
with open("tests/test_api.py", encoding="utf-8") as f:
    for line in f:
        m = re.match(r"def (test_\w+)", line)
        if m:
            tests.append(m.group(1))

print(f"Total tests: {len(tests)}")

# Duplicates
dupes = {k: v for k, v in Counter(tests).items() if v > 1}
if dupes:
    print(f"DUPLICATES: {dupes}")
else:
    print("No duplicate test names")

# Group
groups = {}
for t in tests:
    if "health" in t:
        groups.setdefault("health", []).append(t)
    elif "upload" in t:
        groups.setdefault("upload", []).append(t)
    elif "stream" in t:
        groups.setdefault("stream", []).append(t)
    elif "analyze" in t or "200_" in t or "502_" in t:
        groups.setdefault("legacy_analyze", []).append(t)
    elif "root" in t or "frontend" in t:
        groups.setdefault("frontend", []).append(t)
    else:
        groups.setdefault("other", []).append(t)

print()
for group, items in groups.items():
    print(f"  {group}: {len(items)} tests")
    for t in items:
        print(f"    - {t}")
