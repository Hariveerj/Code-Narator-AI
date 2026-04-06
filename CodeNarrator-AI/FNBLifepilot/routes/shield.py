# ================================================================
# BNF LifePilot — Shield Workflow Engine
# ================================================================
# Predicts debit-order failures and autonomously moves funds
# from Savings → Cheque to prevent bounced debits.
# ================================================================
import uuid
from flask import Blueprint, request, jsonify
from database.db import get_conn

shield_bp = Blueprint("shield", __name__)


def _log(user_id, action, node, status, details):
    get_conn().execute(
        "INSERT INTO AgentActivityLog (id,user_id,action,node_name,status,details) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, node, status, details),
    )
    get_conn().commit()


# POST /api/shield/scan
@shield_bp.route("/scan", methods=["POST"])
def scan():
    data = request.get_json(force=True)
    user_id = data.get("userId")
    if not user_id:
        return jsonify(error="userId required"), 400
    return jsonify(_run_shield(get_conn(), user_id))


# GET /api/shield/events/<user_id>
@shield_bp.route("/events/<user_id>", methods=["GET"])
def events(user_id: str):
    rows = get_conn().execute(
        "SELECT se.*, udo.creditor_name, udo.amount_cents AS debit_amount "
        "FROM ShieldEvents se JOIN UpcomingDebitOrders udo ON se.debit_order_id=udo.id "
        "WHERE se.user_id=? ORDER BY se.triggered_at DESC", (user_id,)
    ).fetchall()
    return jsonify(events=[dict(r) for r in rows])


# POST /api/shield/simulate
@shield_bp.route("/simulate", methods=["POST"])
def simulate():
    data = request.get_json(force=True)
    user_id = data.get("userId")
    if not user_id:
        return jsonify(error="Missing fields"), 400

    conn = get_conn()
    cheque = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND account_type='cheque'", (user_id,)
    ).fetchone()
    savings = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND account_type='savings'", (user_id,)
    ).fetchone()
    if not cheque or not savings:
        return jsonify(error="Accounts not found"), 404

    orig_c, orig_s = cheque["balance_cents"], savings["balance_cents"]

    cb = data.get("chequeBalance")
    sb = data.get("savingsBalance")
    if cb is not None:
        conn.execute("UPDATE UserAccounts SET balance_cents=? WHERE id=?", (cb, cheque["id"]))
    if sb is not None:
        conn.execute("UPDATE UserAccounts SET balance_cents=? WHERE id=?", (sb, savings["id"]))
    conn.commit()

    result = _run_shield(conn, user_id)

    conn.execute("UPDATE UserAccounts SET balance_cents=? WHERE id=?", (orig_c, cheque["id"]))
    conn.execute("UPDATE UserAccounts SET balance_cents=? WHERE id=?", (orig_s, savings["id"]))
    conn.commit()

    return jsonify(simulation=True, **result)


# ── Core Shield Logic ─────────────────────────────────────────
def _run_shield(conn, user_id):
    cheque = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND account_type='cheque'", (user_id,)
    ).fetchone()
    savings = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND account_type='savings'", (user_id,)
    ).fetchone()

    if not cheque or not savings:
        _log(user_id, "shield_scan", "ShieldScan", "error", "Missing cheque or savings account")
        return dict(shielded=[], warnings=["Missing cheque or savings account"])

    upcoming = conn.execute(
        "SELECT * FROM UpcomingDebitOrders WHERE user_id=? AND is_active=1 "
        "AND due_date BETWEEN date('now') AND date('now','+3 days') "
        "ORDER BY due_date ASC, amount_cents DESC", (user_id,)
    ).fetchall()

    running = cheque["balance_cents"]
    avail_savings = savings["balance_cents"]
    shielded, at_risk, warnings = [], [], []

    for d in upcoming:
        if running >= d["amount_cents"]:
            running -= d["amount_cents"]
            _log(user_id, "shield_check", "DebitOrderCheck", "success",
                 f"{d['creditor_name']} R{d['amount_cents']/100:.2f} — covered")
        else:
            shortfall = d["amount_cents"] - running
            if avail_savings >= shortfall:
                eid = str(uuid.uuid4())
                conn.execute("UPDATE UserAccounts SET balance_cents=balance_cents-?, updated_at=datetime('now') WHERE id=?",
                             (shortfall, savings["id"]))
                conn.execute("UPDATE UserAccounts SET balance_cents=balance_cents+?, updated_at=datetime('now') WHERE id=?",
                             (shortfall, cheque["id"]))
                conn.execute(
                    "INSERT INTO ShieldEvents (id,user_id,debit_order_id,source_account,target_account,amount_cents,status,completed_at) "
                    "VALUES (?,?,?,?,?,?,'completed',datetime('now'))",
                    (eid, user_id, d["id"], savings["id"], cheque["id"], shortfall),
                )
                conn.execute("UPDATE UpcomingDebitOrders SET last_status='shielded' WHERE id=?", (d["id"],))
                conn.execute(
                    "INSERT INTO Transactions (id,user_id,account_id,type,amount_cents,description,category) "
                    "VALUES (?,?,?,'shield',?,?,'shield')",
                    (str(uuid.uuid4()), user_id, savings["id"], shortfall,
                     f"Shield: Moved R{shortfall/100:.2f} for {d['creditor_name']}"),
                )
                conn.commit()
                avail_savings -= shortfall
                running = 0
                shielded.append(dict(debitOrder=d["creditor_name"],
                                     amount=f"{d['amount_cents']/100:.2f}",
                                     shortfall=f"{shortfall/100:.2f}", eventId=eid))
                _log(user_id, "shield_transfer", "ShieldTransfer", "success",
                     f"🛡️ Shielded {d['creditor_name']}: moved R{shortfall/100:.2f} from savings")
            else:
                at_risk.append(dict(debitOrder=d["creditor_name"],
                                    amount=f"{d['amount_cents']/100:.2f}",
                                    shortfall=f"{shortfall/100:.2f}"))
                warnings.append(f"⚠️ Cannot shield {d['creditor_name']} — insufficient savings")
                _log(user_id, "shield_fail", "ShieldTransfer", "error",
                     f"Cannot shield {d['creditor_name']} — shortfall R{shortfall/100:.2f}")

    _log(user_id, "shield_scan_complete", "ShieldScan", "success",
         f"Scan complete: {len(shielded)} shielded, {len(at_risk)} at risk")

    return dict(
        shielded=shielded, atRisk=at_risk, warnings=warnings,
        summary=dict(
            chequeAfter=f"{running/100:.2f}",
            savingsAfter=f"{avail_savings/100:.2f}",
            totalShielded=len(shielded),
            totalAtRisk=len(at_risk),
        ),
    )
