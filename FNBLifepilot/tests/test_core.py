# ================================================================
# BNF LifePilot — Core Tests
# ================================================================
import os
import sys
import json
import unittest

# Ensure project root on path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app
from database.db import get_conn
from database.seed import seed


class LifePilotTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        seed()
        cls.client = app.test_client()
        user = get_conn().execute("SELECT id FROM Users LIMIT 1").fetchone()
        cls.user_id = user["id"]

    # ── Dashboard ──────────────────────────────────────────────
    def test_discover(self):
        r = self.client.get("/api/dashboard/discover")
        self.assertEqual(r.status_code, 200)
        self.assertIn("userId", r.get_json())

    def test_dashboard(self):
        r = self.client.get(f"/api/dashboard/{self.user_id}")
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("healthScore", d)
        self.assertIn("accounts", d)

    # ── Accounts ───────────────────────────────────────────────
    def test_accounts(self):
        r = self.client.get(f"/api/accounts/{self.user_id}")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(len(r.get_json()["accounts"]) >= 2)

    # ── Shield ─────────────────────────────────────────────────
    def test_shield_scan(self):
        r = self.client.post("/api/shield/scan",
                             data=json.dumps({"userId": self.user_id}),
                             content_type="application/json")
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("shielded", d)

    def test_shield_events(self):
        r = self.client.get(f"/api/shield/events/{self.user_id}")
        self.assertEqual(r.status_code, 200)

    # ── eBucks ─────────────────────────────────────────────────
    def test_ebucks_status(self):
        r = self.client.get(f"/api/ebucks/{self.user_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("currentLevelName", r.get_json())

    def test_ebucks_optimize(self):
        r = self.client.post(f"/api/ebucks/optimize/{self.user_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("recommendations", r.get_json())

    # ── Agent ──────────────────────────────────────────────────
    def test_agent_execute(self):
        r = self.client.post("/api/agent/execute",
                             data=json.dumps({"userId": self.user_id, "action": "GetBalance"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_json()["success"])

    def test_biometric_flow(self):
        r = self.client.post("/api/agent/biometric-challenge",
                             data=json.dumps({"userId": self.user_id, "amountCents": 600000}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        cid = r.get_json()["challengeId"]

        r2 = self.client.post("/api/agent/biometric-verify",
                              data=json.dumps({"userId": self.user_id, "challengeId": cid,
                                               "biometricPayload": "fingerprint_ok"}),
                              content_type="application/json")
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.get_json()["verified"])

    def test_activity_map(self):
        r = self.client.get(f"/api/agent/activity-map/{self.user_id}")
        self.assertEqual(r.status_code, 200)
        self.assertIn("activities", r.get_json())

    # ── Evaluation ─────────────────────────────────────────────
    def test_eval_single(self):
        r = self.client.post("/api/evaluation/run",
                             data=json.dumps({"testName": "ebucks_query",
                                              "input": "What is my eBucks level?",
                                              "expected": "Gold Level 3"}),
                             content_type="application/json")
        self.assertEqual(r.status_code, 200)
        self.assertIn("groundedness", r.get_json())

    def test_eval_stress(self):
        r = self.client.post("/api/evaluation/stress-test")
        d = r.get_json()
        self.assertEqual(r.status_code, 200)
        self.assertIn("results", d)

    def test_eval_checklist(self):
        r = self.client.get("/api/evaluation/checklist")
        self.assertEqual(r.status_code, 200)
        self.assertIn("checklist", r.get_json())

    # ── Static ─────────────────────────────────────────────────
    def test_static_index(self):
        r = self.client.get("/")
        self.assertEqual(r.status_code, 200)
        self.assertIn(b"BNF LifePilot", r.data)


if __name__ == "__main__":
    unittest.main()
