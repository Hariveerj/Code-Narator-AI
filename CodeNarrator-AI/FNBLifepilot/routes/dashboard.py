# ================================================================
# BNF LifePilot — Dashboard Blueprint
# ================================================================
from flask import Blueprint, jsonify
from database.db import get_conn

dashboard_bp = Blueprint("dashboard", __name__)

LEVEL_NAMES = {1: "Blue", 2: "Silver", 3: "Gold", 4: "Platinum", 5: "Prestige"}


@dashboard_bp.route("/discover", methods=["GET"])
def discover():
    user = get_conn().execute("SELECT id FROM Users LIMIT 1").fetchone()
    if not user:
        return jsonify(error="No users found"), 404
    return jsonify(userId=user["id"])


@dashboard_bp.route("/<user_id>", methods=["GET"])
def dashboard(user_id: str):
    conn = get_conn()

    user = conn.execute("SELECT * FROM Users WHERE id=?", (user_id,)).fetchone()
    if not user:
        return jsonify(error="User not found"), 404

    accounts = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND is_active=1", (user_id,)
    ).fetchall()

    ebucks = conn.execute("SELECT * FROM eBucksProgress WHERE user_id=?", (user_id,)).fetchone()

    upcoming = conn.execute(
        "SELECT * FROM UpcomingDebitOrders WHERE user_id=? AND is_active=1 "
        "AND due_date >= date('now') ORDER BY due_date ASC", (user_id,)
    ).fetchall()

    txns = conn.execute(
        "SELECT * FROM Transactions WHERE user_id=? ORDER BY timestamp DESC LIMIT 20", (user_id,)
    ).fetchall()

    shield_events = conn.execute(
        "SELECT * FROM ShieldEvents WHERE user_id=? ORDER BY triggered_at DESC LIMIT 10", (user_id,)
    ).fetchall()

    health = _health_score(accounts, upcoming, ebucks)

    return jsonify(
        user=dict(id=user["id"], firstName=user["first_name"], lastName=user["last_name"]),
        accounts=[{**dict(a), "balance": f"{a['balance_cents']/100:.2f}"} for a in accounts],
        ebucks=_fmt_ebucks(ebucks) if ebucks else None,
        upcomingDebits=[{**dict(d), "amount": f"{d['amount_cents']/100:.2f}"} for d in upcoming],
        recentTransactions=[{**dict(t), "amount": f"{t['amount_cents']/100:.2f}"} for t in txns],
        shieldEvents=[dict(s) for s in shield_events],
        healthScore=health,
    )


def _health_score(accounts, debits, ebucks):
    score = 50
    cheque = next((a for a in accounts if a["account_type"] == "cheque"), None)
    total_due = sum(d["amount_cents"] for d in debits)

    if cheque:
        ratio = cheque["balance_cents"] / max(total_due, 1)
        if ratio >= 2:
            score += 20
        elif ratio >= 1:
            score += 10
        else:
            score -= 20

    savings = next((a for a in accounts if a["account_type"] == "savings"), None)
    if savings and savings["balance_cents"] > 0:
        score += 10

    if ebucks:
        score += ebucks["current_level"] * 4

    score = max(0, min(100, score))
    if score >= 80:
        label, color = "Excellent", "#00a651"
    elif score >= 60:
        label, color = "Good", "#4fc3f7"
    elif score >= 40:
        label, color = "Fair", "#ff9800"
    else:
        label, color = "At Risk", "#e53935"
    return dict(score=score, label=label, color=color)


def _fmt_ebucks(e):
    return {
        **dict(e),
        "pointsDisplay": f"{e['points_balance']:,}",
        "levelName": LEVEL_NAMES.get(e["current_level"], "Blue"),
    }
