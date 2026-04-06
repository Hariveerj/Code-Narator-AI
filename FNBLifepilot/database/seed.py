# ================================================================
# BNF LifePilot — Seed Data
# ================================================================
import uuid, random
from datetime import datetime, timedelta
from database.db import get_conn


def seed() -> None:
    conn = get_conn()
    cur = conn.cursor()

    # Clear existing data (order matters for FK)
    for t in [
        "EvaluationResults", "BiometricChallenges", "AgentActivityLog",
        "ShieldEvents", "Transactions", "UpcomingDebitOrders",
        "eBucksProgress", "UserAccounts", "Users",
    ]:
        cur.execute(f"DELETE FROM {t}")

    # ── User ───────────────────────────────────────────────────
    user_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO Users (id, id_number, first_name, last_name, email, phone) VALUES (?,?,?,?,?,?)",
        (user_id, "9001015009087", "Thabo", "Mokoena", "thabo@example.co.za", "0821234567"),
    )

    # ── Accounts ───────────────────────────────────────────────
    cheque_id = str(uuid.uuid4())
    savings_id = str(uuid.uuid4())
    cur.execute(
        "INSERT INTO UserAccounts (id, user_id, account_type, account_number, balance_cents) VALUES (?,?,?,?,?)",
        (cheque_id, user_id, "cheque", "62012345678", 10_000),  # R100
    )
    cur.execute(
        "INSERT INTO UserAccounts (id, user_id, account_type, account_number, balance_cents) VALUES (?,?,?,?,?)",
        (savings_id, user_id, "savings", "78812345678", 200_000),  # R2 000
    )

    # ── eBucks Progress ────────────────────────────────────────
    cur.execute(
        """INSERT INTO eBucksProgress
           (id, user_id, current_level, points_balance, monthly_spend_cents,
            salary_deposit, debit_orders_count, online_banking_active, app_active, next_review_date)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        (str(uuid.uuid4()), user_id, 3, 14500, 850_000, 1, 4, 1, 1, "2026-04-01"),
    )

    # ── Upcoming Debit Orders ─────────────────────────────────
    tomorrow = (datetime.now() + timedelta(days=1)).strftime("%Y-%m-%d")
    debit_id = str(uuid.uuid4())
    for name, cents in [("MiWay Insurance", 50_000), ("Vodacom", 39_900), ("Netflix SA", 19_900)]:
        cur.execute(
            "INSERT INTO UpcomingDebitOrders (id, user_id, account_id, creditor_name, amount_cents, due_date) VALUES (?,?,?,?,?,?)",
            (debit_id if name == "MiWay Insurance" else str(uuid.uuid4()), user_id, cheque_id, name, cents, tomorrow),
        )

    # ── Transaction History (30 days) ─────────────────────────
    categories = ["groceries", "fuel", "entertainment", "utilities", "dining"]
    for i in range(30):
        ts = (datetime.now() - timedelta(days=i)).isoformat()
        cur.execute(
            "INSERT INTO Transactions (id, user_id, account_id, type, amount_cents, description, category, timestamp) VALUES (?,?,?,?,?,?,?,?)",
            (str(uuid.uuid4()), user_id, cheque_id, "debit",
             random.randint(5_000, 55_000), f"Purchase #{i+1}", categories[i % len(categories)], ts),
        )

    conn.commit()
    print("✅ Seed data inserted successfully.")
    print(f"   User ID:     {user_id}")
    print(f"   Cheque ID:   {cheque_id}  (R100.00)")
    print(f"   Savings ID:  {savings_id} (R2,000.00)")
    print(f"   Debit Order: {debit_id}   (R500.00 due {tomorrow})")


if __name__ == "__main__":
    seed()
