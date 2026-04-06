# ================================================================
# BNF LifePilot — Stress Test Runner
# ================================================================
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database.seed import seed


def stress():
    seed()
    client = app.test_client()

    print(f"\n{'='*60}")
    print(f"  BNF LifePilot — Stress Test")
    print(f"{'='*60}")

    # ── 1. Shield scan ────────────────────────────────────────
    discover = client.get("/api/dashboard/discover").get_json()
    uid = discover["userId"]
    print(f"\n  User: {uid}")

    scan = client.post("/api/shield/scan",
                       data=json.dumps({"userId": uid}),
                       content_type="application/json").get_json()
    s = scan["summary"]
    print(f"\n  🛡️ Shield Scan")
    print(f"     Shielded: {s['totalShielded']}")
    print(f"     At Risk:  {s['totalAtRisk']}")
    print(f"     Cheque After: R{s['chequeAfter']}")
    print(f"     Savings After: R{s['savingsAfter']}")

    # ── 2. eBucks optimise ────────────────────────────────────
    opt = client.post(f"/api/ebucks/optimize/{uid}").get_json()
    print(f"\n  📊 eBucks Optimisation")
    print(f"     Level: {opt['currentLevelName']} → {opt['projectedLevelName']}")
    print(f"     Recommendations: {len(opt['recommendations'])}")
    for r in opt["recommendations"]:
        print(f"       • [{r['priority']}] {r['title']}")

    # ── 3. Self-healing agent ─────────────────────────────────
    agent_ok = client.post("/api/agent/execute",
                           data=json.dumps({"userId": uid, "action": "GetBalance"}),
                           content_type="application/json").get_json()
    print(f"\n  🤖 Agent self-test")
    print(f"     GetBalance: {'✅' if agent_ok.get('success') else '❌'}")

    agent_fail = client.post("/api/agent/execute",
                             data=json.dumps({"userId": uid, "action": "BadAction"}),
                             content_type="application/json").get_json()
    print(f"     BadAction (expect fail): {'✅ escalated' if agent_fail.get('escalation') else '❌'}")

    # ── 4. Evaluation ─────────────────────────────────────────
    st = client.post("/api/evaluation/stress-test").get_json()
    print(f"\n  🧪 Evaluation Stress Test")
    print(f"     All Passed: {'✅' if st['allPassed'] else '❌'}")
    for r in st["results"]:
        status = "✅" if r["passed"] else "❌"
        print(f"       {status} {r['test']}")

    # ── 5. Checklist ──────────────────────────────────────────
    cl = client.get("/api/evaluation/checklist").get_json()
    print(f"\n  📋 Compliance Checklist")
    for k, v in cl["checklist"].items():
        status = "✅" if v["passed"] else "❌"
        print(f"     {status} {v['label']}")

    print(f"\n{'='*60}")
    print(f"  Overall: {'ALL PASSED ✅' if cl['allPassed'] and st['allPassed'] else 'SOME FAILURES ❌'}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    stress()
