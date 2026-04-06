# ================================================================
# BNF LifePilot — eBucks Optimisation Engine
# ================================================================
import uuid
from flask import Blueprint, request, jsonify
from database.db import get_conn

ebucks_bp = Blueprint("ebucks", __name__)

LEVEL_REQS = {
    1: dict(name="Blue",     minSpend=0,        salaryDeposit=False, debitOrders=0, digital=False),
    2: dict(name="Silver",   minSpend=100_000,   salaryDeposit=True,  debitOrders=1, digital=False),
    3: dict(name="Gold",     minSpend=350_000,   salaryDeposit=True,  debitOrders=2, digital=True),
    4: dict(name="Platinum", minSpend=700_000,   salaryDeposit=True,  debitOrders=3, digital=True),
    5: dict(name="Prestige", minSpend=1_200_000, salaryDeposit=True,  debitOrders=5, digital=True),
}


def _log(user_id, action, node, status, details):
    get_conn().execute(
        "INSERT INTO AgentActivityLog (id,user_id,action,node_name,status,details) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, node, status, details),
    )
    get_conn().commit()


@ebucks_bp.route("/<user_id>", methods=["GET"])
def status(user_id: str):
    eb = get_conn().execute("SELECT * FROM eBucksProgress WHERE user_id=?", (user_id,)).fetchone()
    if not eb:
        return jsonify(error="eBucks record not found"), 404
    cur = LEVEL_REQS[eb["current_level"]]
    nxt = LEVEL_REQS[min(eb["current_level"] + 1, 5)]
    return jsonify(**dict(eb), currentLevelName=cur["name"], nextLevelName=nxt["name"], requirements=nxt)


@ebucks_bp.route("/optimize/<user_id>", methods=["POST"])
def optimize(user_id: str):
    conn = get_conn()
    eb = conn.execute("SELECT * FROM eBucksProgress WHERE user_id=?", (user_id,)).fetchone()
    if not eb:
        return jsonify(error="eBucks record not found"), 404

    txns = conn.execute(
        "SELECT category, SUM(amount_cents) AS total, COUNT(*) AS count "
        "FROM Transactions WHERE user_id=? AND type='debit' "
        "AND timestamp >= datetime('now','-30 days') GROUP BY category ORDER BY total DESC",
        (user_id,),
    ).fetchall()

    total_spend = sum(t["total"] for t in txns)
    analysis = _analyze(txns, total_spend)
    recs = _recommendations(eb, analysis, total_spend)
    proj = _projected(eb, recs)

    _log(user_id, "ebucks_optimize", "eBucksOptimize", "success",
         f"Analysis complete: {len(recs)} recommendations")

    return jsonify(
        currentLevel=eb["current_level"],
        currentLevelName=LEVEL_REQS[eb["current_level"]]["name"],
        projectedLevel=proj["level"],
        projectedLevelName=LEVEL_REQS[proj["level"]]["name"],
        pointsBalance=eb["points_balance"],
        monthlySpend=f"{total_spend/100:.2f}",
        spendingAnalysis=analysis,
        recommendations=recs,
        estimatedPointsGain=proj["pointsGain"],
    )


@ebucks_bp.route("/spending-analysis/<user_id>", methods=["GET"])
def spending_analysis(user_id: str):
    conn = get_conn()
    daily = conn.execute(
        "SELECT date(timestamp) AS day, SUM(amount_cents) AS total FROM Transactions "
        "WHERE user_id=? AND type='debit' AND timestamp >= datetime('now','-30 days') "
        "GROUP BY date(timestamp) ORDER BY day", (user_id,)
    ).fetchall()

    by_cat = conn.execute(
        "SELECT category, SUM(amount_cents) AS total, COUNT(*) AS count, AVG(amount_cents) AS avg_amount "
        "FROM Transactions WHERE user_id=? AND type='debit' AND timestamp >= datetime('now','-30 days') "
        "GROUP BY category", (user_id,)
    ).fetchall()

    return jsonify(
        daily=[{"day": d["day"], "total": f"{d['total']/100:.2f}"} for d in daily],
        byCategory=[{
            "category": c["category"], "total": f"{c['total']/100:.2f}",
            "count": c["count"], "avgAmount": f"{c['avg_amount']/100:.2f}",
        } for c in by_cat],
    )


# ── Helpers ────────────────────────────────────────────────────
def _analyze(txns, total_spend):
    return [
        dict(category=t["category"], total=f"{t['total']/100:.2f}", count=t["count"],
             percentage=f"{t['total']/max(total_spend,1)*100:.1f}")
        for t in txns
    ]


def _recommendations(eb, analysis, total_spend):
    nxt_lvl = min(eb["current_level"] + 1, 5)
    req = LEVEL_REQS[nxt_lvl]
    recs = []

    if total_spend < req["minSpend"]:
        gap = req["minSpend"] - total_spend
        recs.append(dict(type="spend", priority="high",
                         title="Increase monthly card spend",
                         description=f"You need R{gap/100:.2f} more to reach {req['name']}. Use your BNF card for all daily purchases.",
                         impact=f"+{int(gap/100)} potential eBucks"))

    if req["salaryDeposit"] and not eb["salary_deposit"]:
        recs.append(dict(type="salary", priority="high",
                         title="Set up salary deposit",
                         description="Deposit your salary into your BNF cheque account.",
                         impact="+1 level requirement met"))

    if eb["debit_orders_count"] < req["debitOrders"]:
        needed = req["debitOrders"] - eb["debit_orders_count"]
        recs.append(dict(type="debit_orders", priority="medium",
                         title=f"Add {needed} more debit order(s)",
                         description=f"You need {req['debitOrders']} debit orders for {req['name']}.",
                         impact=f"+{needed} requirement(s) met"))

    if req["digital"] and (not eb["online_banking_active"] or not eb["app_active"]):
        recs.append(dict(type="digital", priority="medium",
                         title="Activate all digital channels",
                         description="Register for Online Banking and the BNF App.",
                         impact="+1 level requirement met"))

    grocery = next((a for a in analysis if a["category"] == "groceries"), None)
    if grocery and float(grocery["percentage"]) > 30:
        recs.append(dict(type="optimize", priority="low",
                         title="Use eBucks at Checkers/Pick n Pay",
                         description="You spend heavily on groceries. Use eBucks-linked card at partners for 2x points.",
                         impact="Up to 2x eBucks on grocery spend"))
    return recs


def _projected(eb, recs):
    high_met = not any(r["priority"] == "high" for r in recs)
    med_met = not any(r["priority"] == "medium" for r in recs)
    if high_met and med_met:
        return dict(level=min(eb["current_level"] + 1, 5), pointsGain=(eb["current_level"] + 1) * 2000)
    elif high_met:
        return dict(level=eb["current_level"], pointsGain=1000)
    return dict(level=eb["current_level"], pointsGain=0)
