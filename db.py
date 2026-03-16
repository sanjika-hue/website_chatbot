import sqlite3
from config import DB_PATH


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()

    conn.execute("""
        CREATE TABLE IF NOT EXISTS events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT    NOT NULL,
            event      TEXT    NOT NULL,
            page       TEXT    NOT NULL,
            target     TEXT,
            data       TEXT,
            referrer   TEXT,
            locale     TEXT,
            device     TEXT,
            screen     TEXT,
            timestamp  INTEGER NOT NULL,
            created_at REAL    NOT NULL DEFAULT (unixepoch('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS recommendations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL,
            message    TEXT NOT NULL,
            page       TEXT,
            cta        TEXT,
            created_at REAL NOT NULL DEFAULT (unixepoch('now'))
        )
    """)

    conn.execute("""
        CREATE TABLE IF NOT EXISTS insights (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id      TEXT NOT NULL,
            page            TEXT NOT NULL,
            highlighted_text TEXT NOT NULL,
            product_slug    TEXT NOT NULL,
            locale          TEXT DEFAULT 'en',
            message         TEXT,
            questions       TEXT,
            created_at      REAL NOT NULL DEFAULT (unixepoch('now'))
        )
    """)

    conn.execute("CREATE INDEX IF NOT EXISTS idx_session ON events(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_event ON events(event)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON events(timestamp)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_rec_session ON recommendations(session_id)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_insight_session ON insights(session_id)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            name          TEXT NOT NULL,
            email         TEXT NOT NULL UNIQUE,
            phone         TEXT,
            company       TEXT,
            password_hash TEXT NOT NULL,
            created_at    REAL NOT NULL DEFAULT (unixepoch('now'))
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_user_email ON users(email)")

    conn.execute("""
        CREATE TABLE IF NOT EXISTS subscriptions (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id             INTEGER NOT NULL REFERENCES users(id),
            product             TEXT NOT NULL,
            plan                TEXT NOT NULL,
            amount              INTEGER NOT NULL,
            razorpay_payment_id TEXT,
            status              TEXT NOT NULL DEFAULT 'active',
            created_at          REAL NOT NULL DEFAULT (unixepoch('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_sub_user ON subscriptions(user_id)")

    try:
        conn.execute("ALTER TABLE users ADD COLUMN is_verified INTEGER NOT NULL DEFAULT 0")
    except Exception:
        pass

    conn.execute("""
        CREATE TABLE IF NOT EXISTS otp_codes (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id    INTEGER NOT NULL REFERENCES users(id),
            code       TEXT NOT NULL,
            expires_at REAL NOT NULL,
            created_at REAL NOT NULL DEFAULT (unixepoch('now'))
        )
    """)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_otp_user ON otp_codes(user_id)")

    conn.commit()
    conn.close()
