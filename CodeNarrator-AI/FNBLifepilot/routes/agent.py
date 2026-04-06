# ================================================================
# BNF LifePilot — Agent Orchestrator & Biometric Auth
# ================================================================
import uuid
from datetime import datetime, timedelta, timezone
from flask import Blueprint, request, jsonify
from database.db import get_conn

agent_bp = Blueprint("agent", __name__)
MAX_RETRIES = 3


def _log(user_id, action, node, status, details):
    get_conn().execute(
        "INSERT INTO AgentActivityLog (id,user_id,action,node_name,status,details) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, node, status, details),
    )
    get_conn().commit()


# POST /api/agent/execute — agentic action with self-healing retry
@agent_bp.route("/execute", methods=["POST"])
def execute():
    data = request.get_json(force=True)
    user_id = data.get("userId")
    action = data.get("action")
    params = data.get("params", {})
    if not user_id or not action:
        return jsonify(error="userId and action required"), 400

    conn = get_conn()
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            result = _execute_action(conn, user_id, action, params)
            _log(user_id, action, action, "success", f"Completed on attempt {attempt}")
            return jsonify(success=True, attempt=attempt, result=result)
        except Exception as exc:
            last_error = str(exc)
            _log(user_id, action, action, "error", f"Attempt {attempt} failed: {last_error}")
            if attempt < MAX_RETRIES:
                healed = _self_heal(user_id, action, exc)
                if healed:
                    _log(user_id, "self_heal", "SelfHeal", "info", f"Applied fix: {healed}")

    _log(user_id, action, action, "error", f"Failed after {MAX_RETRIES} attempts. Escalating.")
    return jsonify(error="Action failed after retries — escalating to human agent",
                   lastError=last_error, attempts=MAX_RETRIES, escalation=True), 500


# POST /api/agent/biometric-challenge
@agent_bp.route("/biometric-challenge", methods=["POST"])
def biometric_challenge():
    data = request.get_json(force=True)
    user_id = data.get("userId")
    if not user_id:
        return jsonify(error="userId required"), 400

    cid = str(uuid.uuid4())
    amount = data.get("amountCents", 0)
    expires = (datetime.now(timezone.utc) + timedelta(minutes=5)).isoformat()

    get_conn().execute(
        "INSERT INTO BiometricChallenges (id,user_id,challenge_type,status,amount_cents,expires_at) "
        "VALUES (?,?,'transfer_auth','pending',?,?)",
        (cid, user_id, amount, expires),
    )
    get_conn().commit()
    _log(user_id, "biometric_challenge", "BiometricAuth", "info", f"Challenge for R{amount/100:.2f}")
    return jsonify(challengeId=cid, expiresAt=expires)


# POST /api/agent/biometric-verify
@agent_bp.route("/biometric-verify", methods=["POST"])
def biometric_verify():
    data = request.get_json(force=True)
    user_id = data.get("userId")
    cid = data.get("challengeId")
    payload = data.get("biometricPayload")
    if not user_id or not cid:
        return jsonify(error="Missing required fields"), 400

    conn = get_conn()
    ch = conn.execute(
        "SELECT * FROM BiometricChallenges WHERE id=? AND user_id=? AND status='pending'",
        (cid, user_id),
    ).fetchone()
    if not ch:
        return jsonify(error="Challenge not found or expired"), 404

    if ch["expires_at"] and datetime.fromisoformat(ch["expires_at"]) < datetime.now(timezone.utc):
        conn.execute("UPDATE BiometricChallenges SET status='expired' WHERE id=?", (cid,))
        conn.commit()
        return jsonify(error="Challenge expired"), 410

    if not payload:
        conn.execute("UPDATE BiometricChallenges SET status='failed' WHERE id=?", (cid,))
        conn.commit()
        return jsonify(error="Biometric verification failed"), 401

    conn.execute("UPDATE BiometricChallenges SET status='verified' WHERE id=?", (cid,))
    conn.commit()
    _log(user_id, "biometric_verified", "BiometricAuth", "success", "Identity confirmed")
    return jsonify(verified=True)


# GET /api/agent/activity-map/<user_id>
@agent_bp.route("/activity-map/<user_id>", methods=["GET"])
def activity_map(user_id: str):
    limit = min(int(request.args.get("limit", 50)), 200)
    rows = get_conn().execute(
        "SELECT * FROM AgentActivityLog WHERE user_id=? ORDER BY timestamp DESC LIMIT ?",
        (user_id, limit),
    ).fetchall()

    node_map: dict[str, dict] = {}
    for r in rows:
        n = r["node_name"] or "Unknown"
        if n not in node_map:
            node_map[n] = dict(name=n, runs=0, errors=0, lastStatus="success")
        node_map[n]["runs"] += 1
        if r["status"] == "error":
            node_map[n]["errors"] += 1
            node_map[n]["lastStatus"] = "error"

    return jsonify(
        activities=[dict(r) for r in rows],
        nodeMap=list(node_map.values()),
        totalActions=len(rows),
        errorCount=sum(1 for r in rows if r["status"] == "error"),
    )


# ── Action executor ────────────────────────────────────────────
def _execute_action(conn, user_id, action, params):
    if action == "GetBalance":
        rows = conn.execute(
            "SELECT account_type, balance_cents FROM UserAccounts WHERE user_id=? AND is_active=1", (user_id,)
        ).fetchall()
        return [dict(type=r["account_type"], balance=f"{r['balance_cents']/100:.2f}") for r in rows]

    if action == "CheckFraudStatus":
        rows = conn.execute(
            "SELECT * FROM Transactions WHERE user_id=? AND type='transfer' "
            "AND amount_cents > 5000000 AND timestamp >= datetime('now','-1 hour')", (user_id,)
        ).fetchall()
        return dict(flagged=len(rows) > 0, transactions=[dict(r) for r in rows])

    if action == "TransferFunds":
        src = params.get("sourceAccountId")
        tgt = params.get("targetAccountId")
        amt = params.get("amountCents")
        if not all([src, tgt, amt]):
            raise ValueError("Missing transfer parameters: sourceAccountId, targetAccountId, amountCents")
        source = conn.execute("SELECT * FROM UserAccounts WHERE id=? AND user_id=?", (src, user_id)).fetchone()
        if not source:
            raise ValueError("Source account not found")
        if source["balance_cents"] < amt:
            raise ValueError("Insufficient funds")
        conn.execute("UPDATE UserAccounts SET balance_cents=balance_cents-? WHERE id=?", (amt, src))
        conn.execute("UPDATE UserAccounts SET balance_cents=balance_cents+? WHERE id=?", (amt, tgt))
        conn.execute(
            "INSERT INTO Transactions (id,user_id,account_id,type,amount_cents,description) VALUES (?,?,?,'transfer',?,'Agent-initiated transfer')",
            (str(uuid.uuid4()), user_id, src, amt),
        )
        conn.commit()
        return dict(transferred=f"{amt/100:.2f}")

    if action == "RunShieldScan":
        cheque = conn.execute(
            "SELECT * FROM UserAccounts WHERE user_id=? AND account_type='cheque'", (user_id,)
        ).fetchone()
        if not cheque:
            raise ValueError("Cheque account not found")
        return dict(triggered=True, chequeBalance=f"{cheque['balance_cents']/100:.2f}")

    raise ValueError(f"Unknown action: {action}")


def _self_heal(user_id, action, error):
    msg = str(error)
    if "Insufficient funds" in msg:
        return "Flagged: insufficient funds — consider shield workflow"
    if "not found" in msg:
        return "Flagged: referenced entity missing — verify account IDs"
    if "Missing transfer" in msg:
        return "Flagged: incomplete request payload — check JSON schema"
    return None
