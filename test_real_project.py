"""Test with real Java files from fnbsp_airtime."""
import requests
import json
import glob
import sys

BASE = "http://127.0.0.1:8081"

# Pick 2 small Java files
java_files = glob.glob(
    r"C:\Users\Administrator\Downloads\fnbsp_airtime 1\fnbsp_airtime\**\*.java",
    recursive=True,
)
# Sort by size, pick 2 smallest non-trivial ones (>100 bytes)
import os
java_files = [(f, os.path.getsize(f)) for f in java_files]
java_files = [f for f, s in sorted(java_files, key=lambda x: x[1]) if s > 100][:2]

if not java_files:
    print("No Java files found!")
    sys.exit(1)

print(f"Uploading {len(java_files)} Java files...")
for f in java_files:
    print(f"  {f.split(chr(92))[-1]}")

files_payload = []
for f in java_files:
    name = f.split("\\")[-1]
    files_payload.append(("files", (name, open(f, "rb"), "text/plain")))

resp = requests.post(f"{BASE}/api/upload", files=files_payload, timeout=30)
print(f"Upload status: {resp.status_code}")
data = resp.json()
job_id = data.get("job_id")
print(f"Job ID: {job_id}")

if not job_id:
    print(f"Error: {data}")
    sys.exit(1)

# Stream results
print("\nStreaming results...")
with requests.get(f"{BASE}/api/stream/{job_id}", stream=True, timeout=600) as r:
    for line in r.iter_lines(decode_unicode=True):
        if not line or not line.startswith("data: "):
            continue
        payload = line[6:]
        if payload == "[DONE]":
            print("\n=== DONE ===")
            break
        evt = json.loads(payload)
        etype = evt.get("type")
        if etype == "progress":
            cur = evt.get("current", "?")
            tot = evt.get("total", "?")
            print(f"  Progress: batch {cur}/{tot}")
        elif etype == "result":
            print(f"  Raw event keys: {list(evt.keys())}")
            r_data = evt.get("data", evt)  # result may be at top level or under 'data'
            expl = r_data.get("explanation", "")
            steps = r_data.get("steps", [])
            mermaid_code = r_data.get("mermaid", "")
            security = r_data.get("security", [])

            print("\n--- RESULT ---")
            print(f"Explanation ({len(expl)} chars): {expl[:300]}...")
            print(f"Steps: {len(steps)} items")
            if steps:
                for i, s in enumerate(steps[:3]):
                    print(f"  {i+1}. {s[:120]}")
            print(f"Mermaid ({len(mermaid_code)} chars):")
            print(mermaid_code[:500])
            print(f"Security findings: {len(security)}")
            if security:
                for sec in security[:3]:
                    if isinstance(sec, dict):
                        print(f"  - {sec.get('issue', sec)}")
                    else:
                        print(f"  - {str(sec)[:120]}")

            # Validate quality
            problems = []
            if len(expl) < 50:
                problems.append(f"Explanation too short ({len(expl)} chars)")
            if "no explanation" in expl.lower() or "not available" in expl.lower():
                problems.append("Generic/fallback explanation")
            if len(steps) < 2:
                problems.append(f"Too few steps ({len(steps)})")
            if "flowchart" not in mermaid_code.lower():
                problems.append("No flowchart in mermaid")
            if mermaid_code.count("-->") < 3:
                problems.append(f"Too few edges ({mermaid_code.count('-->')})")

            if problems:
                print(f"\n⚠ QUALITY ISSUES: {problems}")
            else:
                print("\n✓ Quality checks PASSED")

        elif etype == "error":
            print(f"ERROR: {evt.get('message')}")
