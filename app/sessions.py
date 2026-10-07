import time
import uuid

from flask import g, request, session

from .db import get_db


def init_app(app):
    @app.before_request
    def track_visitor():
        if request.endpoint == "static":
            return
        now = time.time()
        conn = get_db()
        sid = session.get("sid")
        row = None
        if sid:
            row = conn.execute(
                "SELECT id, last_seen FROM visitor_sessions WHERE id = ?", (sid,)
            ).fetchone()
        if row is None:  # naya visitor (ya cookie clear/jaali)
            sid = uuid.uuid4().hex
            session["sid"] = sid
            session.permanent = True
            conn.execute(
                "INSERT INTO visitor_sessions (id, ip, user_agent, created_at, last_seen) "
                "VALUES (?, ?, ?, ?, ?)",
                (sid, request.remote_addr, request.headers.get("User-Agent", "")[:200], now, now),
            )
            conn.commit()
        elif now - row["last_seen"] > 30:  # har request par DB write na ho
            conn.execute("UPDATE visitor_sessions SET last_seen = ? WHERE id = ?", (now, sid))
            conn.commit()
        g.sid = sid