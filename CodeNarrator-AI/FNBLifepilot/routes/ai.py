# ================================================================
# BNF LifePilot — AI Chat Engine
# ================================================================
# Intent-aware conversational assistant that routes natural
# language queries to live banking data and actions.
# ================================================================
import re
import uuid
import json
from flask import Blueprint, request, jsonify
from database.db import get_conn

ai_bp = Blueprint("ai", __name__)

# ── Intent patterns ────────────────────────────────────────────
INTENTS = [
    ("balance",       re.compile(r"\b(balance|how much|money|account|funds)\b", re.I)),
    ("shield",        re.compile(r"\b(shield|protect|debit.?order|bounce|overdraft|upcoming.?debit)\b", re.I)),
    ("ebucks",        re.compile(r"\b(ebucks|e-?bucks|points|reward|level|optimis|optimize)\b", re.I)),
    ("transfer",      re.compile(r"\b(transfer|send|move|pay|payment)\b", re.I)),
    ("spending",      re.compile(r"\b(spend|spending|transaction|history|categor|budget)\b", re.I)),
    ("fraud",         re.compile(r"\b(fraud|suspicious|scam|hack|unauthori[sz]ed|security)\b", re.I)),
    ("health",        re.compile(r"\b(health|score|financial.?health|wellness|overview)\b", re.I)),
    ("help",          re.compile(r"\b(help|what can you|how do|assist|support|hi|hello|hey)\b", re.I)),
]


def _detect_intent(text: str) -> str:
    for name, pattern in INTENTS:
        if pattern.search(text):
            return name
    return "general"


def _log(user_id, action, node, status, details):
    get_conn().execute(
        "INSERT INTO AgentActivityLog (id,user_id,action,node_name,status,details) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, node, status, details),
    )
    get_conn().commit()


# ── POST /api/ai/chat ─────────────────────────────────────────
@ai_bp.route("/chat", methods=["POST"])
def chat():
    data = request.get_json(force=True)
    user_id = data.get("userId")
    message = (data.get("message") or "").strip()
    history = data.get("history", [])

    if not user_id or not message:
        return jsonify(error="userId and message required"), 400

    conn = get_conn()
    intent = _detect_intent(message)
    _log(user_id, "ai_chat", "AIEngine", "info", f"Intent: {intent} | Message: {message[:100]}")

    try:
        reply, actions, data_cards = _generate_response(conn, user_id, intent, message, history)
        _log(user_id, "ai_response", "AIEngine", "success", f"Intent: {intent}")
    except Exception as exc:
        _log(user_id, "ai_error", "AIEngine", "error", str(exc))
        reply = "I'm sorry, I encountered an issue processing your request. Please try again or rephrase your question."
        actions, data_cards = [], []

    return jsonify(
        reply=reply,
        intent=intent,
        actions=actions,
        dataCards=data_cards,
        conversationId=data.get("conversationId", str(uuid.uuid4())),
    )


# ── GET /api/ai/suggestions/<user_id> ─────────────────────────
@ai_bp.route("/suggestions/<user_id>", methods=["GET"])
def suggestions(user_id: str):
    conn = get_conn()
    cheque = conn.execute(
        "SELECT balance_cents FROM UserAccounts WHERE user_id=? AND account_type='cheque'", (user_id,)
    ).fetchone()
    debits = conn.execute(
        "SELECT SUM(amount_cents) AS total FROM UpcomingDebitOrders "
        "WHERE user_id=? AND is_active=1 AND due_date BETWEEN date('now') AND date('now','+3 days')",
        (user_id,),
    ).fetchone()
    eb = conn.execute("SELECT current_level FROM eBucksProgress WHERE user_id=?", (user_id,)).fetchone()

    suggestions = [
        {"text": "What's my account balance?", "icon": "💰"},
        {"text": "Show my eBucks status", "icon": "⭐"},
    ]

    if cheque and debits and debits["total"] and cheque["balance_cents"] < debits["total"]:
        suggestions.insert(0, {"text": "Run Shield protection scan", "icon": "🛡️"})
    if eb and eb["current_level"] < 5:
        suggestions.append({"text": "How can I level up my eBucks?", "icon": "📈"})

    suggestions.extend([
        {"text": "Show my spending analysis", "icon": "📊"},
        {"text": "Are there any suspicious transactions?", "icon": "🔒"},
    ])
    return jsonify(suggestions=suggestions[:6])


# ── Response generation by intent ──────────────────────────────
def _generate_response(conn, user_id, intent, message, history):
    if intent == "balance":
        return _handle_balance(conn, user_id)
    if intent == "shield":
        return _handle_shield(conn, user_id)
    if intent == "ebucks":
        return _handle_ebucks(conn, user_id, message)
    if intent == "transfer":
        return _handle_transfer(conn, user_id)
    if intent == "spending":
        return _handle_spending(conn, user_id)
    if intent == "fraud":
        return _handle_fraud(conn, user_id)
    if intent == "health":
        return _handle_health(conn, user_id)
    if intent == "help":
        return _handle_help()
    return _handle_general(conn, user_id)


def _handle_balance(conn, user_id):
    accounts = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND is_active=1", (user_id,)
    ).fetchall()
    if not accounts:
        return "I couldn't find any active accounts. Please contact your branch for assistance.", [], []

    lines = ["Here's a summary of your BNF accounts:\n"]
    cards = []
    for a in accounts:
        bal = a["balance_cents"] / 100
        lines.append(f"• **{a['account_type'].title()}** ({a['account_number']}): **R {bal:,.2f}**")
        cards.append({"label": f"{a['account_type'].title()} Account", "value": f"R {bal:,.2f}",
                       "sublabel": a["account_number"]})

    total = sum(a["balance_cents"] for a in accounts) / 100
    lines.append(f"\n**Total across all accounts: R {total:,.2f}**")

    return "\n".join(lines), [], cards


def _handle_shield(conn, user_id):
    cheque = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND account_type='cheque'", (user_id,)
    ).fetchone()
    savings = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND account_type='savings'", (user_id,)
    ).fetchone()
    upcoming = conn.execute(
        "SELECT * FROM UpcomingDebitOrders WHERE user_id=? AND is_active=1 "
        "AND due_date BETWEEN date('now') AND date('now','+3 days') ORDER BY due_date",
        (user_id,),
    ).fetchall()

    if not upcoming:
        return "You have no debit orders due in the next 3 days. Nothing to shield right now! ✅", [], []

    total_due = sum(d["amount_cents"] for d in upcoming)
    cheque_bal = cheque["balance_cents"] if cheque else 0
    savings_bal = savings["balance_cents"] if savings else 0

    lines = [f"**🛡️ Shield Analysis** — {len(upcoming)} debit orders due soon:\n"]
    for d in upcoming:
        lines.append(f"• **{d['creditor_name']}**: R {d['amount_cents']/100:,.2f} — due {d['due_date']}")

    lines.append(f"\n**Total due:** R {total_due/100:,.2f}")
    lines.append(f"**Cheque balance:** R {cheque_bal/100:,.2f}")

    shortfall = total_due - cheque_bal
    actions = []
    if shortfall > 0:
        lines.append(f"\n⚠️ **Shortfall of R {shortfall/100:,.2f} detected!**")
        if savings_bal >= shortfall:
            lines.append(f"✅ Your savings (R {savings_bal/100:,.2f}) can cover this. I can run Shield protection to auto-transfer the shortfall.")
            actions.append({"label": "🛡️ Run Shield Now", "action": "shield_scan"})
        else:
            lines.append(f"❌ Savings (R {savings_bal/100:,.2f}) insufficient to cover the full shortfall. Some debit orders may bounce.")
    else:
        lines.append("\n✅ Your cheque balance fully covers all upcoming debits. No action needed!")

    cards = [
        {"label": "Total Due", "value": f"R {total_due/100:,.2f}", "sublabel": f"{len(upcoming)} debit orders"},
        {"label": "Cheque Balance", "value": f"R {cheque_bal/100:,.2f}", "sublabel": "Available"},
        {"label": "Shortfall", "value": f"R {max(shortfall,0)/100:,.2f}", "sublabel": "needs shielding" if shortfall > 0 else "none"},
    ]
    return "\n".join(lines), actions, cards


def _handle_ebucks(conn, user_id, message):
    eb = conn.execute("SELECT * FROM eBucksProgress WHERE user_id=?", (user_id,)).fetchone()
    if not eb:
        return "I couldn't find your eBucks record. Please ensure your account is linked.", [], []

    level_names = {1: "Blue", 2: "Silver", 3: "Gold", 4: "Platinum", 5: "Prestige"}
    cur_name = level_names[eb["current_level"]]
    nxt_name = level_names[min(eb["current_level"] + 1, 5)]

    lines = [f"**⭐ eBucks Status**\n"]
    lines.append(f"• **Level:** {cur_name} (Level {eb['current_level']})")
    lines.append(f"• **Points:** {eb['points_balance']:,}")
    lines.append(f"• **Monthly Spend:** R {eb['monthly_spend_cents']/100:,.2f}")

    level_up = re.search(r"(level.?up|improv|reach|next|how.?(do|can|to))", message, re.I)
    actions = []
    if level_up or "optimi" in message.lower():
        lines.append(f"\n**📈 To reach {nxt_name}:**")
        reqs = {
            2: "Spend R1,000+/month, deposit salary, 1+ debit order",
            3: "Spend R3,500+/month, deposit salary, 2+ debit orders, use digital banking",
            4: "Spend R7,000+/month, deposit salary, 3+ debit orders, use all digital channels",
            5: "Spend R12,000+/month, deposit salary, 5+ debit orders, all digital channels active",
        }
        nxt_lvl = min(eb["current_level"] + 1, 5)
        lines.append(f"• {reqs.get(nxt_lvl, 'You are at the highest level!')}")
        if not eb["salary_deposit"]:
            lines.append("• 💡 **Quick win:** Set up your salary deposit into BNF")
        if eb["debit_orders_count"] < 3:
            lines.append(f"• 💡 **Quick win:** Add {3 - eb['debit_orders_count']} more debit order(s)")
        actions.append({"label": "📊 Run Full Optimisation", "action": "ebucks_optimize"})
    else:
        if eb["current_level"] < 5:
            actions.append({"label": f"📈 How to reach {nxt_name}?", "action": "ebucks_levelup"})

    cards = [
        {"label": "Level", "value": cur_name, "sublabel": f"Level {eb['current_level']} of 5"},
        {"label": "Points", "value": f"{eb['points_balance']:,}", "sublabel": "eBucks balance"},
        {"label": "Monthly Spend", "value": f"R {eb['monthly_spend_cents']/100:,.2f}", "sublabel": "this month"},
    ]
    return "\n".join(lines), actions, cards


def _handle_transfer(conn, user_id):
    accounts = conn.execute(
        "SELECT account_type, account_number, balance_cents FROM UserAccounts "
        "WHERE user_id=? AND is_active=1", (user_id,)
    ).fetchall()

    lines = ["**💸 Transfer Funds**\n"]
    lines.append("To make a transfer, I need:\n")
    lines.append("1. **Source account** — which account to transfer from")
    lines.append("2. **Destination account** — where to send the money")
    lines.append("3. **Amount** — how much to transfer\n")
    lines.append("**🔒 Security note:** Transfers over R5,000 require biometric re-authentication.\n")
    lines.append("**Your accounts:**")
    for a in accounts:
        lines.append(f"• {a['account_type'].title()} ({a['account_number']}): R {a['balance_cents']/100:,.2f}")

    actions = [{"label": "💸 Open Transfer Form", "action": "transfer_form"}]
    return "\n".join(lines), actions, []


def _handle_spending(conn, user_id):
    by_cat = conn.execute(
        "SELECT category, SUM(amount_cents) AS total, COUNT(*) AS count "
        "FROM Transactions WHERE user_id=? AND type='debit' "
        "AND timestamp >= datetime('now','-30 days') GROUP BY category ORDER BY total DESC",
        (user_id,),
    ).fetchall()

    if not by_cat:
        return "No spending transactions found in the last 30 days.", [], []

    total = sum(c["total"] for c in by_cat)
    lines = ["**📊 Spending Analysis** (last 30 days)\n"]
    cards = []
    for c in by_cat:
        pct = c["total"] / max(total, 1) * 100
        lines.append(f"• **{c['category'].title()}**: R {c['total']/100:,.2f} ({pct:.0f}%) — {c['count']} transactions")
        cards.append({"label": c["category"].title(), "value": f"R {c['total']/100:,.2f}",
                       "sublabel": f"{pct:.0f}% of total"})

    lines.append(f"\n**Total spending: R {total/100:,.2f}**")

    top = by_cat[0]["category"] if by_cat else ""
    if top:
        lines.append(f"\n💡 **Tip:** Your highest spend category is **{top}**. Consider using your BNF card at eBucks partner stores for bonus points.")

    return "\n".join(lines), [], cards


def _handle_fraud(conn, user_id):
    large = conn.execute(
        "SELECT * FROM Transactions WHERE user_id=? AND amount_cents > 500000 "
        "AND timestamp >= datetime('now','-24 hours') ORDER BY timestamp DESC", (user_id,)
    ).fetchall()

    lines = ["**🔒 Security Scan**\n"]
    if large:
        lines.append(f"⚠️ **{len(large)} large transaction(s)** detected in the last 24 hours:\n")
        for t in large:
            lines.append(f"• R {t['amount_cents']/100:,.2f} — {t['description'] or 'No description'} — {t['timestamp']}")
        lines.append("\nIf you don't recognise any of these, please contact BNF Fraud immediately on **087 575 9444**.")
    else:
        lines.append("✅ **No suspicious activity detected** in the last 24 hours.")
        lines.append("\nBNF monitors all your transactions with real-time fraud detection.")
        lines.append("All transfers over R5,000 require biometric verification.")

    lines.append("\n**Security tips:**")
    lines.append("• Never share your PIN, OTP, or password with anyone")
    lines.append("• BNF will never ask for your full card number via phone/SMS")
    lines.append("• Enable biometric login on the BNF App")

    return "\n".join(lines), [], []


def _handle_health(conn, user_id):
    from routes.dashboard import _health_score
    accounts = conn.execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND is_active=1", (user_id,)
    ).fetchall()
    debits = conn.execute(
        "SELECT * FROM UpcomingDebitOrders WHERE user_id=? AND is_active=1 "
        "AND due_date >= date('now')", (user_id,)
    ).fetchall()
    eb = conn.execute("SELECT * FROM eBucksProgress WHERE user_id=?", (user_id,)).fetchone()

    hs = _health_score(accounts, debits, eb)

    lines = [f"**🏥 Financial Health Score: {hs['score']}/100 — {hs['label']}**\n"]
    lines.append("**Breakdown:**")

    cheque = next((a for a in accounts if a["account_type"] == "cheque"), None)
    savings = next((a for a in accounts if a["account_type"] == "savings"), None)

    if cheque:
        lines.append(f"• Cheque balance: R {cheque['balance_cents']/100:,.2f}")
    if savings:
        lines.append(f"• Savings buffer: R {savings['balance_cents']/100:,.2f}")
    if eb:
        level_names = {1: "Blue", 2: "Silver", 3: "Gold", 4: "Platinum", 5: "Prestige"}
        lines.append(f"• eBucks level: {level_names[eb['current_level']]}")

    total_due = sum(d["amount_cents"] for d in debits)
    lines.append(f"• Upcoming debits: R {total_due/100:,.2f}")

    lines.append("\n**Recommendations:**")
    if hs["score"] < 40:
        lines.append("• ⚠️ Your account is at risk. Consider Shield protection for debit orders.")
        lines.append("• 💡 Move non-essential funds to cover upcoming debits.")
    elif hs["score"] < 60:
        lines.append("• 💡 Build an emergency savings buffer of at least 1 month's debit orders.")
        lines.append("• 📈 Increase eBucks level for better rewards.")
    elif hs["score"] < 80:
        lines.append("• 📈 Close to excellent! Focus on eBucks optimisation.")
        lines.append("• 💰 Consider growing your savings allocation.")
    else:
        lines.append("• ✅ Excellent financial health! Keep it up.")
        lines.append("• 💡 Consider investment options to grow your wealth.")

    cards = [
        {"label": "Health Score", "value": str(hs["score"]), "sublabel": hs["label"]},
    ]
    actions = [{"label": "🛡️ Run Shield Scan", "action": "shield_scan"},
               {"label": "📊 Optimise eBucks", "action": "ebucks_optimize"}]
    return "\n".join(lines), actions, cards


def _handle_help():
    lines = [
        "**👋 Hi! I'm your BNF LifePilot AI assistant.**\n",
        "I can help you with:\n",
        "• **💰 Account balances** — \"What's my balance?\"",
        "• **🛡️ Shield protection** — \"Protect my debit orders\"",
        "• **⭐ eBucks optimisation** — \"How can I level up my eBucks?\"",
        "• **💸 Transfers** — \"I want to transfer money\"",
        "• **📊 Spending analysis** — \"Show my spending\"",
        "• **🔒 Security** — \"Any suspicious activity?\"",
        "• **🏥 Financial health** — \"What's my health score?\"",
        "\nJust type your question naturally and I'll assist you!",
    ]
    return "\n".join(lines), [], []


def _handle_general(conn, user_id):
    lines = [
        "I'm not sure I understood that. Let me help you with what I can do:\n",
        "• Ask about your **account balance**",
        "• Check **Shield protection** for debit orders",
        "• Get **eBucks** optimisation tips",
        "• Review your **spending** patterns",
        "• Check for **suspicious activity**",
        "• See your **financial health** score",
        "\nCould you rephrase your question?",
    ]
    actions = [
        {"label": "💰 My Balance", "action": "ask_balance"},
        {"label": "🛡️ Shield Scan", "action": "shield_scan"},
        {"label": "⭐ eBucks Status", "action": "ask_ebucks"},
    ]
    return "\n".join(lines), actions, []
