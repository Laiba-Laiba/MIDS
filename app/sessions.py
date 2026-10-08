import hashlib
import os
import time
import uuid

from flask import current_app, g, request, session

from .db import get_db

TRACKED = {"main.index", "main.new_challenge", "main.verify"}  # not /stats or /monitor
LABELS = {"human", "naive_bot", "browser_bot", "humanized_bot"}


def client_ip_hash():
    """Store a salted hash, never the raw IP (privacy). Same IP -> same hash."""
    key = f"{current_app.config.get('SECRET_KEY')}|{request.remote_addr}"
    return hashlib.sha256(key.encode()).hexdigest()[:16]


def _experiment_label():
    """Ground truth for experiments only. Never used by the risk score itself."""
    if not current_app.config.get("EXPERIMENT_MODE"):
        return None
    raw = request.headers.get("X-Experiment-Label") or request.args.get("label")
    return raw if raw in LABELS else None


def init_app(app):
    app.config.setdefault("EXPERIMENT_MODE", os.environ.get("EXPERIMENT_MODE") == "1")

    @app.before_request
    def track_visitor():
        if request.endpoint not in TRACKED:
            return
        now = time.time()
        conn = get_db()
        label = _experiment_label()
        sid = session.get("sid")
        row = None
        if sid:
            row = conn.execute(
                "SELECT id, last_seen FROM visitor_sessions WHERE id = ?", (sid,)
            ).fetchone()
        if row is None:  # new visitor (or cleared/forged cookie)
            sid = uuid.uuid4().hex
            session["sid"] = sid
            session.permanent = True
            conn.execute(
                "INSERT INTO visitor_sessions (id, ip, user_agent, created_at, last_seen, label) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (sid, client_ip_hash(), request.headers.get("User-Agent", "")[:200], now, now, label),
            )
            conn.commit()
        else:
            if label:  # humans open /?label=human once; bots send the header
                conn.execute(
                    "UPDATE visitor_sessions SET label = ? WHERE id = ? AND label IS NULL",
                    (label, sid),
                )
            if now - row["last_seen"] > 30:  # avoid a DB write on every request
                conn.execute("UPDATE visitor_sessions SET last_seen = ? WHERE id = ?", (now, sid))
            conn.commit()
        g.sid = sid