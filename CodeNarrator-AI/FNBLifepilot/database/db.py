# ================================================================
# BNF LifePilot — Database Layer (pure sqlite3)
# ================================================================
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "lifepilot.db")

_conn: sqlite3.Connection | None = None


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        _conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.execute("PRAGMA journal_mode=WAL")
        _conn.execute("PRAGMA foreign_keys=ON")
        _migrate(_conn)
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        -- Users & Authentication
        CREATE TABLE IF NOT EXISTS Users (
            id            TEXT PRIMARY KEY,
            id_number     TEXT NOT NULL UNIQUE,
            first_name    TEXT NOT NULL,
            last_name     TEXT NOT NULL,
            email         TEXT,
            phone         TEXT,
            biometric_hash TEXT,
            created_at    TEXT DEFAULT (datetime('now')),
            updated_at    TEXT DEFAULT (datetime('now'))
        );

        -- Bank Accounts
        CREATE TABLE IF NOT EXISTS UserAccounts (
            id             TEXT PRIMARY KEY,
            user_id        TEXT NOT NULL REFERENCES Users(id),
            account_type   TEXT NOT NULL CHECK(account_type IN ('cheque','savings','credit','investment')),
            account_number TEXT NOT NULL UNIQUE,
            balance_cents  INTEGER NOT NULL DEFAULT 0,
            currency       TEXT NOT NULL DEFAULT 'ZAR',
            is_active      INTEGER NOT NULL DEFAULT 1,
            created_at     TEXT DEFAULT (datetime('now')),
            updated_at     TEXT DEFAULT (datetime('now'))
        );

        -- eBucks Progress Tracking
        CREATE TABLE IF NOT EXISTS eBucksProgress (
            id                    TEXT PRIMARY KEY,
            user_id               TEXT NOT NULL REFERENCES Users(id),
            current_level         INTEGER NOT NULL DEFAULT 1 CHECK(current_level BETWEEN 1 AND 5),
            points_balance        INTEGER NOT NULL DEFAULT 0,
            monthly_spend_cents   INTEGER NOT NULL DEFAULT 0,
            salary_deposit        INTEGER NOT NULL DEFAULT 0,
            debit_orders_count    INTEGER NOT NULL DEFAULT 0,
            online_banking_active INTEGER NOT NULL DEFAULT 0,
            app_active            INTEGER NOT NULL DEFAULT 0,
            next_review_date      TEXT,
            updated_at            TEXT DEFAULT (datetime('now'))
        );

        -- Upcoming Debit Orders
        CREATE TABLE IF NOT EXISTS UpcomingDebitOrders (
            id            TEXT PRIMARY KEY,
            user_id       TEXT NOT NULL REFERENCES Users(id),
            account_id    TEXT NOT NULL REFERENCES UserAccounts(id),
            creditor_name TEXT NOT NULL,
            amount_cents  INTEGER NOT NULL,
            due_date      TEXT NOT NULL,
            recurrence    TEXT NOT NULL DEFAULT 'monthly' CHECK(recurrence IN ('weekly','monthly','yearly')),
            is_active     INTEGER NOT NULL DEFAULT 1,
            last_status   TEXT DEFAULT 'pending' CHECK(last_status IN ('pending','paid','failed','shielded')),
            created_at    TEXT DEFAULT (datetime('now'))
        );

        -- Transaction History
        CREATE TABLE IF NOT EXISTS Transactions (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL REFERENCES Users(id),
            account_id   TEXT NOT NULL REFERENCES UserAccounts(id),
            type         TEXT NOT NULL CHECK(type IN ('debit','credit','transfer','shield')),
            amount_cents INTEGER NOT NULL,
            description  TEXT,
            category     TEXT,
            timestamp    TEXT DEFAULT (datetime('now'))
        );

        -- Shield Events (auto-protection log)
        CREATE TABLE IF NOT EXISTS ShieldEvents (
            id             TEXT PRIMARY KEY,
            user_id        TEXT NOT NULL REFERENCES Users(id),
            debit_order_id TEXT NOT NULL REFERENCES UpcomingDebitOrders(id),
            source_account TEXT NOT NULL REFERENCES UserAccounts(id),
            target_account TEXT NOT NULL REFERENCES UserAccounts(id),
            amount_cents   INTEGER NOT NULL,
            status         TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','failed','rolled_back')),
            triggered_at   TEXT DEFAULT (datetime('now')),
            completed_at   TEXT
        );

        -- Agent Activity Log (Activity Map data)
        CREATE TABLE IF NOT EXISTS AgentActivityLog (
            id        TEXT PRIMARY KEY,
            user_id   TEXT,
            action    TEXT NOT NULL,
            node_name TEXT,
            status    TEXT NOT NULL CHECK(status IN ('success','error','warning','info')),
            details   TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        );

        -- Biometric Auth Challenges
        CREATE TABLE IF NOT EXISTS BiometricChallenges (
            id             TEXT PRIMARY KEY,
            user_id        TEXT NOT NULL REFERENCES Users(id),
            challenge_type TEXT NOT NULL DEFAULT 'transfer_auth',
            status         TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','verified','failed','expired')),
            amount_cents   INTEGER,
            created_at     TEXT DEFAULT (datetime('now')),
            expires_at     TEXT
        );

        -- Evaluation Results
        CREATE TABLE IF NOT EXISTS EvaluationResults (
            id            TEXT PRIMARY KEY,
            test_name     TEXT NOT NULL,
            category      TEXT,
            input         TEXT,
            expected      TEXT,
            actual        TEXT,
            groundedness  REAL,
            completeness  REAL,
            passed        INTEGER NOT NULL DEFAULT 0,
            run_at        TEXT DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
