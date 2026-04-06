# ================================================================
# BNF LifePilot — Batch Evaluation Runner
# ================================================================
import csv
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database.seed import seed


def run_csv(path: str | None = None):
    seed()
    path = path or os.path.join(os.path.dirname(__file__), "evaluation-test-cases.csv")
    with open(path, newline="", encoding="utf-8") as f:
        cases = list(csv.DictReader(f))

    client = app.test_client()
    r = client.post(
        "/api/evaluation/batch",
        data=json.dumps({"testCases": cases}),
        content_type="application/json",
    )
    data = r.get_json()
    print(f"\n{'='*60}")
    print(f"  BNF LifePilot — Evaluation Results")
    print(f"{'='*60}")
    print(f"  Total: {data['total']}  Passed: {data['passed']}  Failed: {data['failed']}")
    print(f"  Pass Rate: {data['passRate']}")
    print(f"{'='*60}")
    for res in data["results"]:
        status = "✅" if res["passed"] else "❌"
        print(f"  {status} {res['testName']:30} G={res['groundedness']:.2f}  C={res['completeness']:.2f}")
    print()
    return data


if __name__ == "__main__":
    run_csv()
