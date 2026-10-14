import sqlite3

from flask import current_app, g

# Behaviour JSON ka format badalne par yeh number barhayein.
#   1 = purana format (keys, moves, pasted, first_key_ms, key_gap_*)
#   2 = full telemetry (pointer_*, key_hold_*, clipboard, page behaviour ...)
BEHAVIOUR_SCHEMA_VERSION = 2


# ---------------------------------------------------------------------------
# Schema (fresh database ke liye)
# ---------------------------------------------------------------------------
#
# Table order important hai: attempts, issued_challenges ko reference karta hai.

SCHEMA = """
CREATE TABLE IF NOT EXISTS visitor_sessions (
    id          TEXT PRIMARY KEY,
    ip          TEXT,                 -- hashed IP (sessions.client_ip_hash)
    user_agent  TEXT,
    created_at  REAL NOT NULL,
    last_seen   REAL NOT NULL,
    label       TEXT
);

CREATE TABLE IF NOT EXISTS issued_challenges (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL REFERENCES visitor_sessions(id),
    challenge_type TEXT NOT NULL DEFAULT 'text',
    params         TEXT,              -- JSON: difficulty, object_count, ...
    ts             REAL NOT NULL,     -- issue time (server)
    expires_at     REAL,
    used           INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS attempts (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id     TEXT NOT NULL REFERENCES visitor_sessions(id),
    challenge_id   INTEGER REFERENCES issued_challenges(id),
    challenge_type TEXT NOT NULL DEFAULT 'text',
    params         TEXT NOT NULL,
    correct        INTEGER NOT NULL,
    fail_reason    TEXT,              -- NULL agar correct, warna wrong_answer / outside_target / ...
    solve_time     REAL NOT NULL,     -- server-side: verify_time - issue_time
    behaviour      TEXT NOT NULL,     -- JSON telemetry
    schema_version INTEGER NOT NULL DEFAULT 2,
    risk           REAL NOT NULL,
    ts             REAL NOT NULL
);
"""

INDEXES = """
CREATE INDEX IF NOT EXISTS idx_vs_ip      ON visitor_sessions (ip, created_at);
CREATE INDEX IF NOT EXISTS idx_att_session ON attempts (session_id, ts);
CREATE INDEX IF NOT EXISTS idx_att_type    ON attempts (challenge_type, ts);
CREATE INDEX IF NOT EXISTS idx_att_chal    ON attempts (challenge_id);
CREATE INDEX IF NOT EXISTS idx_iss_session ON issued_challenges (session_id, ts);
CREATE INDEX IF NOT EXISTS idx_iss_type    ON issued_challenges (challenge_type, ts);
"""


# ---------------------------------------------------------------------------
# Connection
# ---------------------------------------------------------------------------

def get_db():
    if "db" not in g:
        conn = sqlite3.connect(
            current_app.config["DATABASE"],
            timeout=10,
        )
        conn.row_factory = sqlite3.Row

        # SQLite mein FOREIGN KEY default OFF hoti hai.
        conn.execute("PRAGMA foreign_keys = ON")

        g.db = conn

    return g.db


def close_db(_exc=None):
    conn = g.pop("db", None)

    if conn is not None:
        conn.close()


# ---------------------------------------------------------------------------
# Migration helpers
# ---------------------------------------------------------------------------

def _columns(conn, table):
    return {
        row["name"]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def _ensure_column(conn, table, column, decl):
    """
    CREATE TABLE IF NOT EXISTS purani table ko modify nahi karta,
    isliye missing columns yahan add hote hain.

    Return: True agar column abhi add hua (backfill ke kaam aata hai).
    """

    if column in _columns(conn, table):
        return False

    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
    return True


# ---------------------------------------------------------------------------
# Init
# ---------------------------------------------------------------------------

def init_app(app):
    app.teardown_appcontext(close_db)

    with app.app_context():
        conn = get_db()

        # Concurrent reads/writes ke liye (Flask dev server / threads).
        conn.execute("PRAGMA journal_mode = WAL")

        # 1. Tables (fresh DB).
        conn.executescript(SCHEMA)

        # 2. Purani databases ko upgrade karo.
        _ensure_column(conn, "visitor_sessions", "label", "TEXT")

        _ensure_column(
            conn, "attempts", "challenge_type",
            "TEXT NOT NULL DEFAULT 'text'",
        )
        _ensure_column(
            conn, "attempts", "challenge_id",
            "INTEGER REFERENCES issued_challenges(id)",
        )
        _ensure_column(conn, "attempts", "fail_reason", "TEXT")

        added_version = _ensure_column(
            conn, "attempts", "schema_version",
            "INTEGER NOT NULL DEFAULT 2",
        )

        _ensure_column(
            conn, "issued_challenges", "challenge_type",
            "TEXT NOT NULL DEFAULT 'text'",
        )
        _ensure_column(conn, "issued_challenges", "params", "TEXT")
        _ensure_column(conn, "issued_challenges", "expires_at", "REAL")
        _ensure_column(
            conn, "issued_challenges", "used",
            "INTEGER NOT NULL DEFAULT 0",
        )

        # 3. Purani rows ko version 1 mark karo (sirf pehli baar).
        #    Purane format mein 'pointer_moves' key nahi hoti.
        if added_version:
            try:
                conn.execute(
                    """
                    UPDATE attempts
                    SET schema_version = 1
                    WHERE json_extract(behaviour, '$.pointer_moves') IS NULL
                    """
                )
            except sqlite3.OperationalError:
                # SQLite bina JSON1 ke build hui ho.
                pass

        # 4. Purana, galat 'mode' column hata do (hamesha 'text' tha).
        if "mode" in _columns(conn, "attempts"):
            try:
                conn.execute("ALTER TABLE attempts DROP COLUMN mode")
            except sqlite3.OperationalError:
                # SQLite < 3.35: DROP COLUMN support nahi. Column rehne do,
                # naya code usay use hi nahi karta.
                pass

        # 5. Indexes (columns ab pakka maujood hain).
        conn.executescript(INDEXES)


# 6. Human-vs-Attacker experiment columns.
        _ensure_column(
            conn, "attempts", "actor",
            "TEXT NOT NULL DEFAULT 'human'",
        )
        _ensure_column(
            conn, "attempts", "experiment_id",
            "TEXT",
        )

        conn.commit()
