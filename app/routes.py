import base64
import json
import math
import time
import uuid

from flask import Blueprint, g, jsonify, render_template, request

from .analysis import risk_score
from .db import get_db
from .generator.text import Params, generate, to_png_bytes

bp = Blueprint("main", __name__)

CHALLENGES = {}  # id -> {answer, params, issued, sid}; abhi memory mein (baad mein Redis)
TTL_SECONDS = 120


def _purge_expired():
    now = time.time()
    for key in [k for k, v in CHALLENGES.items() if now - v["issued"] > TTL_SECONDS]:
        del CHALLENGES[key]


def _clean_behaviour(raw):
    """Client ka data kabhi trust na karein: numbers banayein aur limit lagayein."""
    raw = raw if isinstance(raw, dict) else {}

    def num(key, hi):
        try:
            v = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0
        return max(0.0, min(v, hi)) if math.isfinite(v) else 0.0

    first = raw.get("first_key_ms")
    return {
        "keys": int(num("keys", 500)),
        "moves": int(num("moves", 100000)),
        "pasted": bool(raw.get("pasted")),
        "first_key_ms": None if first is None else num("first_key_ms", 600000),
        "key_gap_mean": num("key_gap_mean", 60000),
        "key_gap_std": num("key_gap_std", 60000),
    }


@bp.get("/")
def index():
    return render_template("index.html")


@bp.post("/api/captcha/new")
def new_challenge():
    data = request.get_json(silent=True) or {}
    try:
        params = Params(data.get("distort", 0), data.get("noise", 0), data.get("rotate", 0))
    except (TypeError, ValueError):
        return jsonify(error="distort, noise and rotate must be numbers from 0 to 10."), 400
    _purge_expired()
    answer, img = generate(params)
    cid = uuid.uuid4().hex
    CHALLENGES[cid] = {
        "answer": answer,
        "params": params.as_dict(),
        "issued": time.time(),
        "sid": g.sid,
    }
    png = base64.b64encode(to_png_bytes(img)).decode()
    return jsonify(id=cid, image=f"data:image/png;base64,{png}", params=params.as_dict())


@bp.post("/api/captcha/verify")
def verify():
    data = request.get_json(silent=True) or {}
    challenge = CHALLENGES.get(data.get("id"))
    if challenge is None or challenge["sid"] != g.sid:  # doosre session ka challenge nahi chalega
        return jsonify(error="This challenge expired. Request a new one."), 404
    del CHALLENGES[data["id"]]  # single use

    correct = str(data.get("answer", "")).strip().upper() == challenge["answer"]
    now = time.time()
    solve_time = round(now - challenge["issued"], 2)
    beh = _clean_behaviour(data.get("behaviour"))

    conn = get_db()
    per_min = conn.execute(
        "SELECT COUNT(*) FROM attempts WHERE session_id = ? AND ts > ?",
        (g.sid, now - 60),
    ).fetchone()[0]
    ip_sessions = conn.execute(
        "SELECT COUNT(*) FROM visitor_sessions WHERE ip = ? AND created_at > ?",
        (request.remote_addr, now - 600),
    ).fetchone()[0]

    risk, reasons = risk_score(beh, solve_time, per_min, ip_sessions)
    beh["reasons"] = reasons
    conn.execute(
        "INSERT INTO attempts (session_id, params, correct, solve_time, behaviour, risk, ts) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (g.sid, json.dumps(challenge["params"]), int(correct), solve_time,
         json.dumps(beh), risk, now),
    )
    conn.commit()
    return jsonify(correct=correct, solve_time=solve_time)


@bp.get("/api/stats")
def stats():
    row = get_db().execute(
        "SELECT COUNT(*) AS total, COALESCE(SUM(correct), 0) AS wins FROM attempts"
    ).fetchone()
    total, wins = row["total"], row["wins"]
    return jsonify(total=total, successes=wins, success_rate=(wins / total if total else None))


@bp.get("/api/monitor")
def monitor():
    """
    Development monitoring view:
    har session ka summary + latest behavioural attempt.

    Production mein is endpoint ko admin-only karna chahiye.
    """

    conn = get_db()

    # Session-level summary
    rows = conn.execute(
        """
        SELECT
            v.id,
            v.ip,
            v.created_at,
            v.last_seen,
            COUNT(a.id) AS attempts,
            COALESCE(SUM(a.correct), 0) AS correct,
            ROUND(AVG(a.risk), 2) AS avg_risk
        FROM visitor_sessions v
        LEFT JOIN attempts a
            ON a.session_id = v.id
        GROUP BY v.id
        ORDER BY v.last_seen DESC
        LIMIT 20
        """
    ).fetchall()

    result = []

    for row in rows:
        session_data = dict(row)

        # Latest attempt for this session
        latest = conn.execute(
            """
            SELECT
                id,
                correct,
                solve_time,
                behaviour,
                risk,
                ts
            FROM attempts
            WHERE session_id = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()

        if latest:
            latest_data = dict(latest)

            # behaviour JSON ko normal Python dictionary mein convert karein
            try:
                behaviour = json.loads(
                    latest_data.get("behaviour") or "{}"
                )
            except (TypeError, json.JSONDecodeError):
                behaviour = {}

            session_data["latest_attempt"] = {
                "id": latest_data["id"],
                "correct": bool(latest_data["correct"]),
                "solve_time": latest_data["solve_time"],
                "timestamp": latest_data["ts"],
                "risk": latest_data["risk"],
                "behaviour": {
                    "keys": behaviour.get("keys", 0),
                    "moves": behaviour.get("moves", 0),
                    "pasted": behaviour.get("pasted", False),
                    "first_key_ms": behaviour.get("first_key_ms"),
                    "key_gap_mean": behaviour.get("key_gap_mean", 0),
                    "key_gap_std": behaviour.get("key_gap_std", 0),
                    "reasons": behaviour.get("reasons", []),
                },
            }
        else:
            session_data["latest_attempt"] = None

        result.append(session_data)

    return jsonify(result)

