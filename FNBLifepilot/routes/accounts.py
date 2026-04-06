# ================================================================
# BNF LifePilot — Accounts Blueprint
# ================================================================
import uuid
from flask import Blueprint, request, jsonify
from database.db import get_conn

accounts_bp = Blueprint("accounts", __name__)


def _log(user_id: str, action: str, node: str, status: str, details: str) -> None:
    get_conn().execute(
        "INSERT INTO AgentActivityLog (id,user_id,action,node_name,status,details) VALUES (?,?,?,?,?,?)",
        (str(uuid.uuid4()), user_id, action, node, status, details),
    )
    get_conn().commit()


@accounts_bp.route("/<user_id>", methods=["GET"])
def list_accounts(user_id: str):
    rows = get_conn().execute(
        "SELECT * FROM UserAccounts WHERE user_id=? AND is_active=1", (user_id,)
    ).fetchall()
    accounts = [
        {**dict(r), "balance": f"{r['balance_cents']/100:.2f}"} for r in rows
    ]
    return jsonify(accounts=accounts)


@accounts_bp.route("/transfer", methods=["POST"])
def transfer():
    data = request.get_json(force=True)
    user_id = data.get("userId")
    src_id = data.get("sourceAccountId")
    tgt_id = data.get("targetAccountId")
    amount = data.get("amountCents")
    bio_token = data.get("biometricToken")

    if not all([user_id, src_id, tgt_id, amount]):
        return jsonify(error="Missing required fields"), 400

    conn = get_conn()

    # Biometric enforcement for > R5 000
    if amount > 500_000:
        if not bio_token:
            return jsonify(error="Biometric re-authentication required for transfers over R5,000",
                           requiresBiometric=True), 403
        challenge = conn.execute(
            "SELECT * FROM BiometricChallenges WHERE user_id=? AND status='verified' "
            "AND expires_at > datetime('now') ORDER BY created_at DESC LIMIT 1",
            (user_id,),
        ).fetchone()
        if not challenge:
            return jsonify(error="No valid biometric verification found",
                           requiresBiometric=True), 403

    source = conn.execute("SELECT * FROM UserAccounts WHERE id=? AND user_id=?", (src_id, user_id)).fetchone()
    target = conn.execute("SELECT * FROM UserAccounts WHERE id=? AND user_id=?", (tgt_id, user_id)).fetchone()

    if not source or not target:
        return jsonify(error="Account not found"), 404
    if source["balance_cents"] < amount:
        return jsonify(error="Insufficient funds"), 400

    tx_id = str(uuid.uuid4())
    conn.execute("UPDATE UserAccounts SET balance_cents=balance_cents-?, updated_at=datetime('now') WHERE id=?", (amount, src_id))
    conn.execute("UPDATE UserAccounts SET balance_cents=balance_cents+?, updated_at=datetime('now') WHERE id=?", (amount, tgt_id))
    conn.execute(
        "INSERT INTO Transactions (id,user_id,account_id,type,amount_cents,description,category) VALUES (?,?,?,'transfer',?,?,'transfer')",
        (tx_id, user_id, src_id, amount, f"Transfer to {target['account_type']} ({target['account_number']})"),
    )
    conn.commit()

    _log(user_id, "transfer", "TransferFunds", "success",
         f"Transferred R{amount/100:.2f} from {source['account_type']} to {target['account_type']}")

    return jsonify(success=True, transactionId=tx_id)
