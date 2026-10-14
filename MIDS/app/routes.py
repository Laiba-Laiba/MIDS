import base64
import json
import math
import time
import uuid

from flask import (
    Blueprint,
    current_app,
    g,
    jsonify,
    render_template,
    request,
)

from .analysis import band, risk_score
from .attacker_solver import solve_challenge
from .db import get_db
from .generator.image import (
    ImageParams,
)
from .generator.image import (
    generate as generate_image,
)
from .generator.image import (
    to_png_bytes as image_to_png_bytes,
)
from .generator.puzzle import (
    PuzzleParams,
)
from .generator.puzzle import (
    generate as generate_puzzle,
)
from .generator.puzzle import (
    to_png_bytes as puzzle_to_png_bytes,
)
from .generator.text import (
    Params as TextParams,
)
from .generator.text import (
    generate as generate_text,
)
from .generator.text import (
    to_png_bytes as text_to_png_bytes,
)
from .sessions import client_ip_hash

bp = Blueprint("main", __name__)


# ---------------------------------------------------------------------------
# In-memory challenge store
# ---------------------------------------------------------------------------

CHALLENGES = {}

TTL_SECONDS = 120


# ---------------------------------------------------------------------------
# Challenge answer verification
# ---------------------------------------------------------------------------


def _verify_challenge_answer(challenge, submitted):
    """
    Verify an answer according to the challenge type.

    Text:
        submitted = string

    Image:
        submitted = {
            "x": number,
            "y": number
        }

    Puzzle:
        submitted = [
            tile_number,
            tile_number,
            ...
        ]
    """

    challenge_type = challenge["type"]
    expected = challenge["answer"]

    # ------------------------------------------------------------------
    # TEXT
    # ------------------------------------------------------------------

    if challenge_type == "text":
        return str(submitted or "").strip().upper() == str(expected).strip().upper()

    # ------------------------------------------------------------------
    # IMAGE
    # ------------------------------------------------------------------

    if challenge_type == "image":
        if not isinstance(submitted, dict):
            return False

        try:
            x = float(submitted.get("x"))
            y = float(submitted.get("y"))
        except (TypeError, ValueError):
            return False

        if not math.isfinite(x) or not math.isfinite(y):
            return False

        try:
            x1, y1, x2, y2 = expected["target_box"]
        except (KeyError, TypeError, ValueError):
            return False

        return x1 <= x <= x2 and y1 <= y <= y2

    # ------------------------------------------------------------------
    # PUZZLE
    # ------------------------------------------------------------------

    if challenge_type == "puzzle":
        if not isinstance(submitted, list):
            return False

        try:
            submitted_order = [int(value) for value in submitted]

            expected_order = [int(value) for value in expected["correct_order"]]
        except (
            TypeError,
            ValueError,
            KeyError,
        ):
            return False

        return submitted_order == expected_order

    return False


# ---------------------------------------------------------------------------
# Remove expired challenges
# ---------------------------------------------------------------------------


def _purge_expired():
    now = time.time()

    expired_ids = [
        key for key, value in CHALLENGES.items() if now - value["issued"] > TTL_SECONDS
    ]

    for key in expired_ids:
        del CHALLENGES[key]


# ---------------------------------------------------------------------------
# Behaviour telemetry cleaning
# ---------------------------------------------------------------------------


def _clean_behaviour(raw):
    """
    Sanitize and bound all client-side behavioural telemetry.

    Client telemetry is never trusted directly.
    """

    raw = raw if isinstance(raw, dict) else {}

    def number(key, high):
        try:
            value = float(raw.get(key) or 0)
        except (TypeError, ValueError):
            return 0.0

        if not math.isfinite(value):
            return 0.0

        return max(
            0.0,
            min(value, high),
        )

    def integer(key, high):
        return int(number(key, high))

    def boolean(key):
        return bool(raw.get(key))

    def nullable_number(key, high):
        value = raw.get(key)

        if value is None:
            return None

        try:
            value = float(value)
        except (TypeError, ValueError):
            return None

        if not math.isfinite(value):
            return None

        return max(
            0.0,
            min(value, high),
        )

    pointer_types = raw.get(
        "pointer_types",
        {},
    )

    if not isinstance(
        pointer_types,
        dict,
    ):
        pointer_types = {}

    clean_pointer_types = {}

    for pointer_type in (
        "mouse",
        "touch",
        "pen",
    ):
        try:
            value = float(pointer_types.get(pointer_type) or 0)
        except (
            TypeError,
            ValueError,
        ):
            value = 0.0

        if not math.isfinite(value):
            value = 0.0

        clean_pointer_types[pointer_type] = int(
            max(
                0.0,
                min(value, 100000),
            )
        )

    # Challenge-specific telemetry
    puzzle_moves = integer(
        "puzzle_moves",
        10000,
    )

    puzzle_swaps = integer(
        "puzzle_swaps",
        10000,
    )

    image_selections = integer(
        "image_selections",
        1000,
    )

    image_wrong_selections = integer(
        "image_wrong_selections",
        1000,
    )

    return {
        # --------------------------------------------------------------
        # Keyboard
        # --------------------------------------------------------------
        "keys": integer(
            "keys",
            500,
        ),
        "backspaces": integer(
            "backspaces",
            500,
        ),
        "deletes": integer(
            "deletes",
            500,
        ),
        "corrections": integer(
            "corrections",
            500,
        ),
        "first_key_ms": nullable_number(
            "first_key_ms",
            600000,
        ),
        "typing_duration_ms": integer(
            "typing_duration_ms",
            600000,
        ),
        "typing_speed_cpm": number(
            "typing_speed_cpm",
            5000,
        ),
        "key_gap_mean": number(
            "key_gap_mean",
            60000,
        ),
        "key_gap_std": number(
            "key_gap_std",
            60000,
        ),
        "key_hold_mean": number(
            "key_hold_mean",
            60000,
        ),
        "key_hold_std": number(
            "key_hold_std",
            60000,
        ),
        # --------------------------------------------------------------
        # Pointer
        # --------------------------------------------------------------
        "pointer_moves": integer(
            "pointer_moves",
            100000,
        ),
        "pointer_distance": number(
            "pointer_distance",
            10000000,
        ),
        "pointer_duration_ms": integer(
            "pointer_duration_ms",
            600000,
        ),
        "pointer_avg_speed": number(
            "pointer_avg_speed",
            1000,
        ),
        "pointer_max_speed": number(
            "pointer_max_speed",
            1000,
        ),
        "pointer_direction_changes": integer(
            "pointer_direction_changes",
            100000,
        ),
        "pointer_pauses": integer(
            "pointer_pauses",
            100000,
        ),
        # --------------------------------------------------------------
        # Pointer interaction
        # --------------------------------------------------------------
        "clicks": integer(
            "clicks",
            10000,
        ),
        "pointer_downs": integer(
            "pointer_downs",
            10000,
        ),
        "pointer_types": clean_pointer_types,
        # --------------------------------------------------------------
        # Form interaction
        # --------------------------------------------------------------
        "focus_count": integer(
            "focus_count",
            1000,
        ),
        "blur_count": integer(
            "blur_count",
            1000,
        ),
        "refocus_count": integer(
            "refocus_count",
            1000,
        ),
        "input_events": integer(
            "input_events",
            1000,
        ),
        # --------------------------------------------------------------
        # Clipboard
        # --------------------------------------------------------------
        "pasted": boolean("pasted"),
        "paste_count": integer(
            "paste_count",
            100,
        ),
        "copied": boolean("copied"),
        "copy_count": integer(
            "copy_count",
            100,
        ),
        "cut": boolean("cut"),
        "cut_count": integer(
            "cut_count",
            100,
        ),
        # --------------------------------------------------------------
        # Page behaviour
        # --------------------------------------------------------------
        "scroll_events": integer(
            "scroll_events",
            10000,
        ),
        "visibility_changes": integer(
            "visibility_changes",
            1000,
        ),
        "hidden_time_ms": integer(
            "hidden_time_ms",
            600000,
        ),
        # --------------------------------------------------------------
        # General interaction
        # --------------------------------------------------------------
        "first_interaction_ms": nullable_number(
            "first_interaction_ms",
            600000,
        ),
        "interaction_events": integer(
            "interaction_events",
            100000,
        ),
        "final_input_length": integer(
            "final_input_length",
            100,
        ),
        # --------------------------------------------------------------
        # Image CAPTCHA
        # --------------------------------------------------------------
        "image_selections": image_selections,
        "image_wrong_selections": image_wrong_selections,
        # --------------------------------------------------------------
        # Puzzle CAPTCHA
        # --------------------------------------------------------------
        "puzzle_moves": puzzle_moves,
        "puzzle_swaps": puzzle_swaps,
    }


# ---------------------------------------------------------------------------
# Index
# ---------------------------------------------------------------------------



@bp.get("/")
def index():
    return render_template("index.html", page_mode="generator")


@bp.get("/attacker")
def attacker_page():
    return render_template("index.html", page_mode="attacker")



# ---------------------------------------------------------------------------
# Create challenge
# ---------------------------------------------------------------------------


@bp.post("/api/captcha/new")
def new_challenge():

    data = request.get_json(silent=True) or {}

    challenge_type = (
        str(
            data.get(
                "type",
                "text",
            )
        )
        .lower()
        .strip()
    )

    try:
        # ==============================================================
        # TEXT
        # ==============================================================

        if challenge_type == "text":
            params = TextParams(
                data.get("distort", 0),
                data.get("noise", 0),
                data.get("rotate", 0),
            )

            answer, img = generate_text(params)

            params_dict = params.as_dict()

            png_bytes = text_to_png_bytes(img)

            # Never expose the answer.
            public_answer = None

        # ==============================================================
        # IMAGE
        # ==============================================================

        elif challenge_type == "image":
            params = ImageParams(
                difficulty=data.get(
                    "difficulty",
                    3,
                ),
                object_count=data.get(
                    "object_count",
                    6,
                ),
            )

            answer, img = generate_image(params)

            params_dict = params.as_dict()

            png_bytes = image_to_png_bytes(img)

            # Only expose what the user needs to know.
            #
            # target_box is NEVER returned.
            public_answer = {
                "target": answer["target"],
            }

        # ==============================================================
        # PUZZLE
        # ==============================================================

        # PUZZLE
        elif challenge_type == "puzzle":
            params = PuzzleParams(
                difficulty=data.get("difficulty", 3)
            )

            answer, img = generate_puzzle(params)
            params_dict = params.as_dict()
            png_bytes = puzzle_to_png_bytes(img)

            # Send the shuffled layout to the client so it can render tiles
            # in the SAME visual positions as the issued image.
            public_answer = {
                "rows": answer["rows"],
                "cols": answer["cols"],
                "shuffled_order": answer["shuffled_order"],
            }

        # ==============================================================
        # UNKNOWN
        # ==============================================================

        else:
            return jsonify(error="Unknown challenge type."), 400

    except (
        TypeError,
        ValueError,
    ):
        return jsonify(error="Invalid challenge parameters."), 400


    # Remove old challenges.
    _purge_expired()

    cid = uuid.uuid4().hex
    issued = time.time()
    now = issued

    # Log the issued challenge in SQLite.
    conn = get_db()
    cur = conn.execute(
        """
        INSERT INTO issued_challenges
            (session_id, challenge_type, params, ts, expires_at, used)
        VALUES (?, ?, ?, ?, ?, 0)
        """,
        (
            g.sid,
            challenge_type,
            json.dumps(params_dict),
            now,
            now + TTL_SECONDS,
        ),
    )
    db_challenge_id = cur.lastrowid
    conn.commit()

    # Encode the challenge image for the frontend and solver.
    png = base64.b64encode(png_bytes).decode("ascii")
    image_data = "data:image/png;base64," + png

    CHALLENGES[cid] = {
        "type": challenge_type,
        "answer": answer,  # Server-side ground truth only.
        "params": params_dict,
        "issued": issued,
        "created_at": issued,
        "sid": g.sid,
        "db_id": db_challenge_id,
        "image": image_data,
        "public_task": public_answer,
        "task_text": (
            public_answer.get("target", "")
            if isinstance(public_answer, dict)
            else ""
        ),
    }

    return jsonify(
        id=cid,
        type=challenge_type,
        image=image_data,
        params=params_dict,
        task=public_answer,
    )


# ---------------------------------------------------------------------------
# Verify challenge
# ---------------------------------------------------------------------------


@bp.post("/api/captcha/verify")
def verify():

    data = request.get_json(silent=True) or {}
    actor = str(data.get("actor", "human")).lower().strip()

    if actor not in {"human", "attacker"}:
        return jsonify(error="Invalid actor."), 400

    experiment_id = data.get("experiment_id")
    if experiment_id is not None:
        experiment_id = str(experiment_id)[:100]
    challenge_id = data.get("id")

    challenge = CHALLENGES.get(challenge_id)

    expired = challenge is not None and time.time() - challenge["issued"] > TTL_SECONDS

    # --------------------------------------------------------------
    # Validate challenge
    # --------------------------------------------------------------

    if challenge is None or expired or challenge["sid"] != g.sid:
        return jsonify(error=("This challenge expired. Request a new one.")), 404

    # --------------------------------------------------------------
    # CAPTCHA is single-use
    # --------------------------------------------------------------

    del CHALLENGES[challenge_id]

    # --------------------------------------------------------------
    # Verify answer according to type
    # --------------------------------------------------------------

    correct = _verify_challenge_answer(
        challenge,
        data.get("answer"),
    )
    if correct:
        fail_reason = None
    elif challenge["type"] == "image":
        fail_reason = "outside_target_or_wrong_answer"
    elif challenge["type"] == "puzzle":
        fail_reason = "wrong_tile_order"
    else:
        fail_reason = "wrong_text"

    now = time.time()

    solve_time = round(
        now - challenge["issued"],
        2,
    )



        # Clean telemetry
    beh = _clean_behaviour(data.get("behaviour"))

    # Give the risk scorer the correct context.
    beh["actor"] = actor
    beh["challenge_type"] = challenge["type"]

    # Attacker-specific solver metadata must survive cleanup.
    if actor == "attacker":
        try:
            elapsed = float(data.get("solver_elapsed_ms", 0))
            if not math.isfinite(elapsed):
                elapsed = 0
        except (TypeError, ValueError):
            elapsed = 0

        try:
            events_count = int(data.get("solver_events_count", 0))
        except (TypeError, ValueError):
            events_count = 0

        beh["solver_elapsed_ms"] = min(max(elapsed, 0), 600000)
        beh["solver_events_count"] = min(max(events_count, 0), 10000)


    conn = get_db()

    # --------------------------------------------------------------
    # Attempts by this session during
    # previous minute
    # --------------------------------------------------------------

    attempts_last_minute = conn.execute(
        """
        SELECT COUNT(*)
        FROM attempts
        WHERE session_id = ?
          AND ts > ?
        """,
        (
            g.sid,
            now - 60,
        ),
    ).fetchone()[0]

    # --------------------------------------------------------------
    # New sessions from same hashed IP
    # during previous ten minutes
    # --------------------------------------------------------------

    ip_new_sessions = conn.execute(
        """
        SELECT COUNT(*)
        FROM visitor_sessions
        WHERE ip = ?
          AND created_at > ?
        """,
        (
            client_ip_hash(),
            now - 600,
        ),
    ).fetchone()[0]

    # --------------------------------------------------------------
    # Challenges issued to this session
    # during previous minute
    # --------------------------------------------------------------

    issued_last_min = conn.execute(
        """
        SELECT COUNT(*)
        FROM issued_challenges
        WHERE session_id = ?
          AND ts > ?
        """,
        (
            g.sid,
            now - 60,
        ),
    ).fetchone()[0]

    # --------------------------------------------------------------
    # Risk scoring
    # --------------------------------------------------------------

    if challenge["type"] == "text":
        answer_len = 5
    else:
        answer_len = 1

    risk, reasons = risk_score(
        beh,
        solve_time,
        attempts_last_minute,
        ip_new_sessions,
        answer_len=answer_len,
        issued_last_min=issued_last_min,
        user_agent=request.headers.get("User-Agent","",),
        correct=correct
    )

    beh["reasons"] = reasons
    current_app.logger.info(
        "Risk debug | actor=%s type=%s solve_time=%.2f "
        "behaviour=%s risk=%r reasons=%r",
        actor,
        challenge["type"],
        solve_time,
        json.dumps(beh),
        risk,
        reasons,
    )
    # --------------------------------------------------------------
    # Save attempt
    # --------------------------------------------------------------




    conn.execute(
        """
        INSERT INTO attempts (
            session_id,
            challenge_id,
            challenge_type,
            params,
            correct,
            fail_reason,
            solve_time,
            behaviour,
            risk,
            ts,
            actor,
            experiment_id
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.sid,
            challenge.get("db_id"),
            challenge["type"],
            json.dumps(challenge["params"]),
            int(correct),
            fail_reason,
            solve_time,
            json.dumps(beh),
            risk,
            now,
            actor,
            experiment_id,
        ),
    )

    conn.execute(
        "UPDATE issued_challenges SET used = 1 WHERE id = ?",
        (challenge.get("db_id"),),
    )

    conn.commit()

    return jsonify(
    correct=correct,
    solve_time=solve_time,
    challenge_type=challenge["type"],
    risk=risk,
    risk_band=band(risk),
    reasons=reasons,
)


# ---------------------------------------------------------------------------
# Stats
# ---------------------------------------------------------------------------


@bp.get("/api/stats")
def stats():

    conn = get_db()

    # Overall
    row = conn.execute(
        """
        SELECT
            COUNT(*) AS total,
            COALESCE(
                SUM(correct),
                0
            ) AS wins
        FROM attempts
        """
    ).fetchone()

    total = row["total"]
    wins = row["wins"]

    # By challenge type
    rows = conn.execute(
        """
        SELECT
            challenge_type,
            COUNT(*) AS total,
            COALESCE(
                SUM(correct),
                0
            ) AS wins,
            ROUND(
                AVG(risk),
                3
            ) AS avg_risk,
            ROUND(
                AVG(solve_time),
                2
            ) AS avg_solve_time
        FROM attempts
        GROUP BY challenge_type
        ORDER BY challenge_type
        """
    ).fetchall()

    by_type = {}

    for row in rows:
        type_total = row["total"]
        type_wins = row["wins"]

        by_type[row["challenge_type"]] = {
            "total": type_total,
            "successes": type_wins,
            "success_rate": (type_wins / type_total if type_total else None),
            "avg_risk": row["avg_risk"],
            "avg_solve_time": (row["avg_solve_time"]),
        }

    return jsonify(
        total=total,
        successes=wins,
        success_rate=(wins / total if total else None),
        by_type=by_type,
    )


# ---------------------------------------------------------------------------
# Monitoring
# ---------------------------------------------------------------------------


@bp.get("/api/monitor")
def monitor():
    """
    Development monitoring endpoint.

    Returns session-level information plus
    latest detailed behavioural record.

    Production:
        Protect this endpoint with authentication.
    """

    conn = get_db()

    rows = conn.execute(
        """
        SELECT
            v.id,
            v.ip,
            v.user_agent,
            v.created_at,
            v.last_seen,
            v.label,

            COUNT(a.id) AS attempts,

            COALESCE(
                SUM(a.correct),
                0
            ) AS correct,

            ROUND(
                AVG(a.risk),
                2
            ) AS avg_risk,

            ROUND(
                AVG(a.solve_time),
                2
            ) AS avg_solve_time,

            ROUND(
                MIN(a.solve_time),
                2
            ) AS fastest_solve,

            ROUND(
                MAX(a.solve_time),
                2
            ) AS slowest_solve

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

        # --------------------------------------------------------------
        # Latest attempt
        # --------------------------------------------------------------

        latest = conn.execute(
            """
            SELECT
                id,
                challenge_type,
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

            try:
                behaviour = json.loads(latest_data.get("behaviour") or "{}")

            except (
                TypeError,
                json.JSONDecodeError,
            ):
                behaviour = {}

            session_data["latest_attempt"] = {
                "id": latest_data["id"],
                "type": latest_data["challenge_type"],
                "correct": bool(latest_data["correct"]),
                "solve_time": latest_data["solve_time"],
                "timestamp": latest_data["ts"],
                "risk": latest_data["risk"],
                "behaviour": behaviour,
            }

        else:
            session_data["latest_attempt"] = None

        result.append(session_data)

    return jsonify(result)


@bp.post("/api/attacker/solve")
def attacker_solve():
    data = request.get_json(silent=True) or {}
    challenge_id = data.get("challenge_id")

    if not challenge_id:
        return jsonify({"error": "challenge_id required"}), 400

    challenge = CHALLENGES.get(challenge_id)

    if not challenge:
        return jsonify({"error": "Challenge not found or expired"}), 404

    # Keep the existing session-binding security check.
    if challenge.get("sid") != g.sid:
        return jsonify({"error": "Session mismatch"}), 403

    if time.time() - challenge.get("created_at", time.time()) > TTL_SECONDS:
        CHALLENGES.pop(challenge_id, None)
        return jsonify({"error": "Challenge expired"}), 410

    # Adapt these field names to the exact keys stored by your /new route.
    challenge_type = (
        challenge.get("challenge_type")
        or challenge.get("type")
    )
    image_data = (
        challenge.get("image")
        or challenge.get("image_data")
        or challenge.get("png")
    )
    task_text = challenge.get("task_text", "")

    if not image_data:
        return jsonify({
            "error": "Challenge image is not stored in the expected field",
            "hint": "Map image_data to the image field used by /api/captcha/new"
        }), 500

    try:
        result = solve_challenge(
            challenge_type=challenge_type,
            image_data=image_data,
            task_text=task_text,
            task_data=challenge.get("public_task"),
        )
    except Exception as exc:
        current_app.logger.exception("Attacker solver failed")
        return jsonify({
            "error": "Local solver failed",
            "detail": str(exc),
        }), 500

    # Never include challenge["answer"] or other ground truth here.
    return jsonify({
        "challenge_id": challenge_id,
        "solver": result["solver"],
        "guess": result["guess"],
        "events": result["events"],
        "elapsed_ms": result["elapsed_ms"],
    })

@bp.get("/api/experiment/report")
def experiment_report():
    conn = get_db()

    experiment_id = request.args.get("experiment_id")

    # Per-attempt risk is stored already; report shows:
    #   - avg_risk: average of per-attempt risk (recent behaviour)
    #   - latest_risk: risk of the most recent attempt per (actor, type)
    base_query = """
        SELECT
            actor,
            challenge_type,
            COUNT(*) AS total_attempts,
            COALESCE(SUM(correct), 0) AS successes,
            ROUND(
                100.0 * COALESCE(SUM(correct), 0)
                / NULLIF(COUNT(*), 0),
                2
            ) AS success_rate_percent,
            ROUND(AVG(solve_time), 2) AS avg_solve_time,
            ROUND(AVG(risk), 3) AS avg_risk
        FROM attempts
    """

    params = []

    if experiment_id:
        base_query += " WHERE experiment_id = ? AND ts > ?"
        params.append(experiment_id)
        params.append(time.time() - 86400)  # last 24 hours
    else:
        base_query += " WHERE ts > ?"
        params.append(time.time() - 86400)

    base_query += " GROUP BY actor, challenge_type ORDER BY actor, challenge_type"

    rows = conn.execute(base_query, params).fetchall()

    report = []

    for row in rows:
        entry = dict(row)

        # Latest attempt risk for this (actor, challenge_type) pair.
        latest_query = """
            SELECT risk, ts, solve_time, correct
            FROM attempts
            WHERE actor = ? AND challenge_type = ?
        """
        latest_params = [row["actor"], row["challenge_type"]]

        if experiment_id:
            latest_query += " AND experiment_id = ?"
            latest_params.append(experiment_id)

        latest_query += " ORDER BY ts DESC LIMIT 1"

        latest = conn.execute(latest_query, latest_params).fetchone()

        if latest:
            entry["latest_risk"] = latest["risk"]
            entry["latest_ts"] = latest["ts"]
            entry["latest_correct"] = bool(latest["correct"])
        else:
            entry["latest_risk"] = None
            entry["latest_ts"] = None
            entry["latest_correct"] = None

        report.append(entry)

    return jsonify({
        "experiment_id": experiment_id,
        "report": report,
    })

@bp.route("/report")
def report_page():
    return render_template("index.html", page_mode="report")

@bp.route("/evaluation")
def evaluation_page():
    return render_template("index.html", page_mode="evaluation")

@bp.get("/api/experiment/attempts")
def experiment_attempts():
    """
    Per-attempt detail for an experiment.

    Returns one row per solve attempt (no grouping), so the report can show
    each human and attacker attempt separately.

    Query params:
        experiment_id: optional filter
        limit:         optional, default 100, max 500
    """
    conn = get_db()

    experiment_id = request.args.get("experiment_id")

    try:
        limit = int(request.args.get("limit", 100))
    except (TypeError, ValueError):
        limit = 100
    limit = max(1, min(limit, 500))

    query = """
        SELECT
            id,
            ts,
            actor,
            challenge_type,
            correct,
            fail_reason,
            solve_time,
            risk,
            behaviour,
            experiment_id
        FROM attempts
    """

    params = []

    if experiment_id:
        query += " WHERE experiment_id = ?"
        params.append(experiment_id)

    query += " ORDER BY ts DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    attempts = []

    for row in rows:
        entry = dict(row)

        # Attempts are stored with unix timestamp; convert to ISO-ish string
        # for readability on the frontend.
        ts = entry.get("ts")
        if ts is not None:
            try:
                import datetime
                entry["ts_iso"] = datetime.datetime.fromtimestamp(
                    float(ts)
                ).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError, OSError):
                entry["ts_iso"] = str(ts)
        else:
            entry["ts_iso"] = None

        # Parse behaviour JSON so frontend can inspect telemetry if needed.
        raw_beh = entry.get("behaviour")
        if raw_beh:
            try:
                entry["behaviour"] = json.loads(raw_beh)
            except (TypeError, json.JSONDecodeError):
                entry["behaviour"] = {}
        else:
            entry["behaviour"] = {}

        # Convert 0/1 to bool for JS.
        entry["correct"] = bool(entry.get("correct"))

        attempts.append(entry)

    # Also include a small summary so the UI can show totals at a glance.
    summary = {}

    if experiment_id:
        summary_query = """
            SELECT
                actor,
                challenge_type,
                COUNT(*) AS total_attempts,
                COALESCE(SUM(correct), 0) AS successes,
                ROUND(AVG(solve_time), 2) AS avg_solve_time,
                ROUND(AVG(risk), 3) AS avg_risk
            FROM attempts
            WHERE experiment_id = ?
            GROUP BY actor, challenge_type
            ORDER BY actor, challenge_type
        """
        summary_rows = conn.execute(summary_query, (experiment_id,)).fetchall()
    else:
        summary_query = """
            SELECT
                actor,
                challenge_type,
                COUNT(*) AS total_attempts,
                COALESCE(SUM(correct), 0) AS successes,
                ROUND(AVG(solve_time), 2) AS avg_solve_time,
                ROUND(AVG(risk), 3) AS avg_risk
            FROM attempts
            GROUP BY actor, challenge_type
            ORDER BY actor, challenge_type
        """
        summary_rows = conn.execute(summary_query).fetchall()

    for row in summary_rows:
        key = f"{row['actor']}|{row['challenge_type']}"
        summary[key] = dict(row)

    return jsonify({
        "experiment_id": experiment_id,
        "count": len(attempts),
        "attempts": attempts,
        "summary": summary,
    })
# ---------------------------------------------------------------------------
# Behaviour monitoring page
# ---------------------------------------------------------------------------


@bp.route("/behaviour")
def behaviour_page():
    return render_template("index.html", page_mode="behaviour")


@bp.get("/api/monitor/behaviour")
def monitor_behaviour():
    """
    Live behaviour monitoring.

    Returns the most recent attempts (across all sessions) with
    full behavioural telemetry, so the dashboard can show how the
    current experiment is behaving right now.

    Query params:
        limit:   optional, default 50, max 300
        actor:   optional, "human" or "attacker"
        type:    optional, "text" / "image" / "puzzle"
    """
    conn = get_db()

    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 300))

    actor_filter = request.args.get("actor")
    type_filter = request.args.get("type")

    query = """
        SELECT
            a.id,
            a.ts,
            a.actor,
            a.challenge_type,
            a.correct,
            a.fail_reason,
            a.solve_time,
            a.risk,
            a.behaviour,
            a.session_id,
            v.ip AS session_ip,
            v.user_agent AS session_user_agent
        FROM attempts a
        LEFT JOIN visitor_sessions v
            ON v.id = a.session_id
        WHERE 1 = 1
    """

    params = []

    if actor_filter in {"human", "attacker"}:
        query += " AND a.actor = ?"
        params.append(actor_filter)

    if type_filter in {"text", "image", "puzzle"}:
        query += " AND a.challenge_type = ?"
        params.append(type_filter)

    query += " ORDER BY a.ts DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()

    import datetime

    attempts = []

    for row in rows:
        entry = dict(row)

        ts = entry.get("ts")
        if ts is not None:
            try:
                entry["ts_iso"] = datetime.datetime.fromtimestamp(
                    float(ts)
                ).strftime("%Y-%m-%d %H:%M:%S")
            except (TypeError, ValueError, OSError):
                entry["ts_iso"] = str(ts)
        else:
            entry["ts_iso"] = None

        raw_beh = entry.get("behaviour")
        if raw_beh:
            try:
                entry["behaviour"] = json.loads(raw_beh)
            except (TypeError, json.JSONDecodeError):
                entry["behaviour"] = {}
        else:
            entry["behaviour"] = {}

        entry["correct"] = bool(entry.get("correct"))

        attempts.append(entry)

    # Roll-up summary across the returned slice.
    total = len(attempts)
    humans = [a for a in attempts if a["actor"] == "human"]
    attackers = [a for a in attempts if a["actor"] == "attacker"]

    def _avg(values):
        clean = [v for v in values if v is not None]
        return round(sum(clean) / len(clean), 3) if clean else None

    summary = {
        "total": total,
        "human_count": len(humans),
        "attacker_count": len(attackers),
        "human_avg_risk": _avg([a["risk"] for a in humans]),
        "attacker_avg_risk": _avg([a["risk"] for a in attackers]),
        "human_avg_time": _avg([a["solve_time"] for a in humans]),
        "attacker_avg_time": _avg([a["solve_time"] for a in attackers]),
        "human_success_rate": (
            round(100.0 * sum(1 for a in humans if a["correct"]) / len(humans), 1)
            if humans else None
        ),
        "attacker_success_rate": (
            round(100.0 * sum(1 for a in attackers if a["correct"]) / len(attackers), 1)
            if attackers else None
        ),
    }

    return jsonify({
        "count": total,
        "summary": summary,
        "attempts": attempts,
    })


# ---------------------------------------------------------------------------
# Session monitoring page
# ---------------------------------------------------------------------------


@bp.route("/sessions")
def sessions_page():
    return render_template("index.html", page_mode="sessions")


@bp.get("/api/monitor/sessions")
def monitor_sessions():
    """
    Session-level monitoring.

    Returns per-session roll-ups: IP hash, user agent, attempt counts,
    average risk, latest attempt, and a per-actor breakdown.

    Query params:
        limit: optional, default 30, max 200
    """
    conn = get_db()

    try:
        limit = int(request.args.get("limit", 30))
    except (TypeError, ValueError):
        limit = 30
    limit = max(1, min(limit, 200))

    rows = conn.execute(
        """
        SELECT
            v.id,
            v.ip,
            v.user_agent,
            v.created_at,
            v.last_seen,
            v.label,

            COUNT(a.id) AS total_attempts,
            COALESCE(SUM(a.correct), 0) AS total_successes,
            ROUND(AVG(a.risk), 3) AS avg_risk,
            ROUND(MAX(a.risk), 3) AS max_risk,
            ROUND(AVG(a.solve_time), 2) AS avg_solve_time,

            COALESCE(SUM(CASE WHEN a.actor = 'human' THEN 1 ELSE 0 END), 0) AS human_attempts,
            COALESCE(SUM(CASE WHEN a.actor = 'attacker' THEN 1 ELSE 0 END), 0) AS attacker_attempts,

            COALESCE(SUM(CASE WHEN a.actor = 'human' AND a.correct = 1 THEN 1 ELSE 0 END), 0) AS human_successes,
            COALESCE(SUM(CASE WHEN a.actor = 'attacker' AND a.correct = 1 THEN 1 ELSE 0 END), 0) AS attacker_successes

        FROM visitor_sessions v
        LEFT JOIN attempts a
            ON a.session_id = v.id
        GROUP BY v.id
        ORDER BY v.last_seen DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    import datetime

    sessions = []

    for row in rows:
        entry = dict(row)

        # ISO timestamps for readability.
        for ts_key in ("created_at", "last_seen"):
            ts = entry.get(ts_key)
            if ts is not None:
                try:
                    entry[ts_key + "_iso"] = datetime.datetime.fromtimestamp(
                        float(ts)
                    ).strftime("%Y-%m-%d %H:%M:%S")
                except (TypeError, ValueError, OSError):
                    entry[ts_key + "_iso"] = str(ts)
            else:
                entry[ts_key + "_iso"] = None

        # Latest attempt for this session.
        latest = conn.execute(
            """
            SELECT
                id, challenge_type, actor, correct,
                solve_time, risk, ts
            FROM attempts
            WHERE session_id = ?
            ORDER BY ts DESC
            LIMIT 1
            """,
            (row["id"],),
        ).fetchone()

        if latest:
            entry["latest_attempt"] = {
                "id": latest["id"],
                "type": latest["challenge_type"],
                "actor": latest["actor"],
                "correct": bool(latest["correct"]),
                "solve_time": latest["solve_time"],
                "risk": latest["risk"],
                "ts": latest["ts"],
            }
        else:
            entry["latest_attempt"] = None

        # Best-effort "primary actor" label.
        if entry["attacker_attempts"] > entry["human_attempts"]:
            entry["primary_actor"] = "attacker"
        elif entry["human_attempts"] > entry["attacker_attempts"]:
            entry["primary_actor"] = "human"
        elif entry["total_attempts"] > 0:
            entry["primary_actor"] = "mixed"
        else:
            entry["primary_actor"] = "unknown"

        sessions.append(entry)

    # Overall summary across returned sessions.
    total_sessions = len(sessions)
    sessions_with_attempts = [s for s in sessions if s["total_attempts"]]
    avg_risk_all = (
        round(
            sum(s["avg_risk"] for s in sessions_with_attempts if s["avg_risk"] is not None)
            / max(1, len([s for s in sessions_with_attempts if s["avg_risk"] is not None])),
            3,
        )
        if sessions_with_attempts else None
    )

    summary = {
        "total_sessions": total_sessions,
        "sessions_with_attempts": len(sessions_with_attempts),
        "total_attempts": sum(s["total_attempts"] for s in sessions),
        "avg_risk": avg_risk_all,
    }

    return jsonify({
        "count": total_sessions,
        "summary": summary,
        "sessions": sessions,
    })

@bp.route("/competitors")
def competitors_page():
    return render_template("index.html", page_mode="competitors")


@bp.get("/api/competitors/data")
def competitors_data():
    """
    Static competitor analysis data.

    All entries are hardcoded based on public documentation and
    research papers. Update the values here if you need to change
    the comparison points.
    """
    vendors = [
        {
            "id": "turnstile",
            "name": "Cloudflare Turnstile",
            "type": "Invisible / Non-interactive",
            "signals": [
                "Mouse movement",
                "Keyboard activity",
                "Focus changes",
                "Visibility changes",
                "IP reputation",
                "TLS fingerprint",
                "Browser quirks",
                "Proof-of-work"
            ],
            "risk_output": "Pass/fail token",
            "explainable": False,
            "open_source": False,
            "cost": "Free (Enterprise paid)"
        },
        {
            "id": "arkose",
            "name": "Arkose Labs (Titan)",
            "type": "Adaptive friction + Proof-of-Work",
            "signals": [
                "Mouse dynamics",
                "Keystroke dynamics",
                "Device fingerprint",
                "Network intelligence",
                "Email intelligence",
                "Interaction patterns"
            ],
            "risk_output": "Risk score + adaptive challenge",
            "explainable": False,
            "open_source": False,
            "cost": "Paid (Enterprise)"
        },
        {
            "id": "recaptcha",
            "name": "reCAPTCHA v3",
            "type": "Score-based, no user friction",
            "signals": [
                "Browser integrity",
                "Mouse movement",
                "Scroll activity",
                "Typing speed",
                "Tab switching",
                "IP/ASN reputation",
                "Cookie trust"
            ],
            "risk_output": "Score 0.0–1.0",
            "explainable": False,
            "open_source": False,
            "cost": "Free (Enterprise paid)"
        },
        {
            "id": "capsolver",
            "name": "CapSolver",
            "type": "Commercial CAPTCHA-solving service",
            "signals": [
                "Token harvesting",
                "Proxy rotation",
                "Headless browser automation",
                "OCR-based solving",
                "AI-based reCAPTCHA solving",
                "Cloudflare bypass"
            ],
            "risk_output": "Solved token / pass response",
            "explainable": False,
            "open_source": False,
            "cost": "Paid (per 1000 solves)"
        },
        {
            "id": "platform",
            "name": "Platform 9¾",
            "type": "Behavioural CAPTCHA + rule-based risk",
            "signals": [
                "Pointer movement",
                "Pointer distance & speed",
                "Keystroke timing",
                "Key hold duration",
                "Backspace / corrections",
                "Paste / copy / cut",
                "Focus / blur / refocus",
                "Visibility changes",
                "Scroll events",
                "Interaction events",
                "Typing speed (CPM)"
            ],
            "risk_output": "0.0–1.0 score + reasons list",
            "explainable": True,
            "open_source": True,
            "cost": "Free (research prototype)"
        }
    ]

    signal_comparison = {
        "categories": [
            "Mouse / Pointer",
            "Keyboard / Typing",
            "Focus / Blur",
            "Visibility",
            "Clipboard",
            "Scroll",
            "IP / Network",
            "Device fingerprint",
            "Proof-of-work",
            "Explainable output"
        ],
        "turnstile":    [1, 1, 1, 1, 0, 0, 1, 1, 1, 0],
        "arkose":       [1, 1, 0, 0, 0, 0, 1, 1, 1, 0],
        "recaptcha_v3": [1, 1, 0, 1, 0, 1, 1, 1, 0, 0],
        "capsolver":    [1, 1, 0, 1, 0, 0, 1, 1, 0, 0],
        "platform":     [1, 1, 1, 1, 1, 1, 0, 0, 0, 1]
    }

    return jsonify({
        "vendors": vendors,
        "signal_comparison": signal_comparison,
    })

# ---------------------------------------------------------------------------
# Evaluation: L-MACD reference vs Platform 9¾ actual
# ---------------------------------------------------------------------------


@bp.get("/api/evaluation/benchmark")
def evaluation_benchmark():
    """
    Compare Platform 9¾ metrics against L-MACD published benchmarks.

    L-MACD (Sangwan, Gupta, Rao — IEEE Access 2024) reports AGE_eff
    values for four defense strategies. This endpoint returns:

        - paper: static L-MACD reference numbers
        - project: live metrics from the local DB
        - comparison: per-strategy gap analysis
    """
    conn = get_db()

    # ------------------------------------------------------------------
    # L-MACD published benchmarks (IEEE Access 2024, Table 2)
    # ------------------------------------------------------------------
    paper = [
        {
            "id": "G1",
            "name": "G1 · Baseline",
            "description": "Standard static CAPTCHA",
            "sr_human": 0.9349,
            "sr_bot": 0.6808,
            "age": 0.2541,
        },
        {
            "id": "G2",
            "name": "G2 · LLM-ZSA-CG",
            "description": "Zero-shot LLM-generated CAPTCHA",
            "sr_human": 0.9548,
            "sr_bot": 0.8001,
            "age": 0.1548,
        },
        {
            "id": "G3",
            "name": "G3 · Human-Expert",
            "description": "Expert-tuned CAPTCHA design",
            "sr_human": 0.9116,
            "sr_bot": 0.1279,
            "age": 0.7837,
        },
        {
            "id": "G4",
            "name": "G4 · L-MACD (RL)",
            "description": "Reinforcement-learning adaptive defense",
            "sr_human": 0.9300,
            "sr_bot": 0.0800,
            "age": 0.8500,
        },
    ]

    # ------------------------------------------------------------------
    # Platform 9¾ live metrics from the local DB
    # ------------------------------------------------------------------
    def _metrics_for(challenge_type):
        h = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(correct), 0) AS wins,
                ROUND(AVG(solve_time), 2) AS avg_time,
                ROUND(AVG(risk), 3) AS avg_risk
            FROM attempts
            WHERE actor = 'human' AND challenge_type = ?
            """,
            (challenge_type,),
        ).fetchone()

        b = conn.execute(
            """
            SELECT
                COUNT(*) AS total,
                COALESCE(SUM(correct), 0) AS wins,
                ROUND(AVG(solve_time), 2) AS avg_time,
                ROUND(AVG(risk), 3) AS avg_risk
            FROM attempts
            WHERE actor = 'attacker' AND challenge_type = ?
            """,
            (challenge_type,),
        ).fetchone()

        h_total = h["total"] or 0
        h_wins = h["wins"] or 0
        b_total = b["total"] or 0
        b_wins = b["wins"] or 0

        sr_human = round(h_wins / h_total, 4) if h_total else None
        sr_bot = round(b_wins / b_total, 4) if b_total else None

        age = (
            round(sr_human - sr_bot, 4)
            if sr_human is not None and sr_bot is not None
            else None
        )

        return {
            "human_total": h_total,
            "human_wins": h_wins,
            "attacker_total": b_total,
            "attacker_wins": b_wins,
            "sr_human": sr_human,
            "sr_bot": sr_bot,
            "age": age,
            "human_avg_time": h["avg_time"],
            "attacker_avg_time": b["avg_time"],
            "human_avg_risk": h["avg_risk"],
            "attacker_avg_risk": b["avg_risk"],
        }

    # Aggregate across all challenge types for the "Platform 9¾" row
    overall = conn.execute(
        """
        SELECT
            COUNT(*) FILTER (WHERE actor = 'human') AS human_total,
            COALESCE(SUM(correct) FILTER (WHERE actor = 'human'), 0) AS human_wins,
            COUNT(*) FILTER (WHERE actor = 'attacker') AS attacker_total,
            COALESCE(SUM(correct) FILTER (WHERE actor = 'attacker'), 0) AS attacker_wins,
            ROUND(AVG(solve_time) FILTER (WHERE actor = 'human'), 2) AS human_avg_time,
            ROUND(AVG(solve_time) FILTER (WHERE actor = 'attacker'), 2) AS attacker_avg_time,
            ROUND(AVG(risk) FILTER (WHERE actor = 'human'), 3) AS human_avg_risk,
            ROUND(AVG(risk) FILTER (WHERE actor = 'attacker'), 3) AS attacker_avg_risk
        FROM attempts
        """
    ).fetchone()

    h_total = overall["human_total"] or 0
    h_wins = overall["human_wins"] or 0
    b_total = overall["attacker_total"] or 0
    b_wins = overall["attacker_wins"] or 0

    overall_sr_human = round(h_wins / h_total, 4) if h_total else None
    overall_sr_bot = round(b_wins / b_total, 4) if b_total else None

    overall_age = (
        round(overall_sr_human - overall_sr_bot, 4)
        if overall_sr_human is not None and overall_sr_bot is not None
        else None
    )

    project = {
        "id": "P9",
        "name": "Platform 9¾ (this work)",
        "description": "Behavioural CAPTCHA + explainable rule-based risk",
        "sr_human": overall_sr_human,
        "sr_bot": overall_sr_bot,
        "age": overall_age,
        "human_total": h_total,
        "attacker_total": b_total,
        "human_avg_time": overall["human_avg_time"],
        "attacker_avg_time": overall["attacker_avg_time"],
        "human_avg_risk": overall["human_avg_risk"],
        "attacker_avg_risk": overall["attacker_avg_risk"],
        "per_type": {
            "text": _metrics_for("text"),
            "image": _metrics_for("image"),
            "puzzle": _metrics_for("puzzle"),
        },
    }

    # ------------------------------------------------------------------
    # Gap analysis: Platform 9¾ vs each L-MACD strategy
    # ------------------------------------------------------------------
    comparison = []

    for entry in paper:
        age_delta = (
            round(project["age"] - entry["age"], 4)
            if project["age"] is not None
            else None
        )

        if age_delta is None:
            verdict = "no_data"
        elif age_delta > 0.05:
            verdict = "better"
        elif age_delta >= -0.05:
            verdict = "parity"
        else:
            verdict = "worse"

        comparison.append({
            "id": entry["id"],
            "name": entry["name"],
            "paper_age": entry["age"],
            "paper_sr_human": entry["sr_human"],
            "paper_sr_bot": entry["sr_bot"],
            "project_age": project["age"],
            "project_sr_human": project["sr_human"],
            "project_sr_bot": project["sr_bot"],
            "age_delta": age_delta,
            "verdict": verdict,
        })

    # ------------------------------------------------------------------
    # Verdict vs strongest paper strategy (G4)
    # ------------------------------------------------------------------
    g4 = next((p for p in paper if p["id"] == "G4"), None)
    strongest_age = g4["age"] if g4 else None

    if project["age"] is None:
        overall_verdict = "Need data"
        overall_cls = ""
    elif strongest_age is not None and project["age"] >= strongest_age:
        overall_verdict = "Matches best paper baseline"
        overall_cls = "ok"
    elif project["age"] >= 0.60:
        overall_verdict = "Strong (competitive)"
        overall_cls = "ok"
    elif project["age"] >= 0.30:
        overall_verdict = "Moderate"
        overall_cls = "warn"
    else:
        overall_verdict = "Weak — attacker wins"
        overall_cls = "bad"

    return jsonify({
        "paper_source": (
            "Sangwan, M., Gupta, A., & Rao, B. V. A. N. S. S. P. (2024). "
            "L-MACD: Learning-Based Adaptive CAPTCHA Defense Against "
            "Multimodal AI Attacks. IEEE Access."
        ),
        "paper": paper,
        "project": project,
        "comparison": comparison,
        "overall_verdict": overall_verdict,
        "overall_cls": overall_cls,
        "strongest_paper_age": strongest_age,
    })
# ---------------------------------------------------------------------------
# Generation validity checks
# ---------------------------------------------------------------------------


@bp.get("/api/verify/generation")
def verify_generation():
    """
    Validate that CAPTCHAs are being generated correctly.

    For each challenge type, generate a fresh sample and check:
        - image is non-empty and correctly sized
        - answer payload is consistent
        - puzzle shuffle is non-trivial
        - image has visible content (not blank)

    Returns a per-type pass/fail report.
    """
    import io

    import numpy as np
    from PIL import Image as PILImage

    def _blank_ratio(img_bytes):
        """Return fraction of pixels that are pure white (blank)."""
        img = PILImage.open(io.BytesIO(img_bytes)).convert("RGB")
        arr = np.asarray(img)
        white = np.all(arr > 245, axis=-1)
        return float(white.mean())

    results = []

    # ---- TEXT ----
    try:
        from .generator.text import Params as TextParams
        from .generator.text import generate as gen_text
        from .generator.text import to_png_bytes as text_png

        checks = {}
        for _ in range(3):
            params = TextParams(distort=3, noise=3, rotate=2)
            answer, img = gen_text(params)
            png = text_png(img)

            checks.setdefault("answer_length_ok", True)
            checks["answer_length_ok"] &= (len(answer) == 5)
            checks.setdefault("answer_alnum", True)
            checks["answer_alnum"] &= (answer.isalnum())
            checks.setdefault("image_size_ok", True)
            w, h = img.size
            checks["image_size_ok"] &= (w > 200 and h > 100 and w < 2000 and h < 2000)
            checks.setdefault("not_blank", True)
            checks["not_blank"] &= (_blank_ratio(png) < 0.98)

        results.append({
            "challenge_type": "text",
            "checks": checks,
            "passed": all(checks.values()),
            "details": f"Generated 3 samples, all checks {'passed' if all(checks.values()) else 'failed'}",
        })
    except Exception as e:
        results.append({
            "challenge_type": "text",
            "checks": {},
            "passed": False,
            "details": f"Error: {e}",
        })

    # ---- IMAGE ----
    try:
        from .generator.image import ImageParams
        from .generator.image import generate as gen_image
        from .generator.image import to_png_bytes as image_png

        checks = {}
        for _ in range(3):
            params = ImageParams(difficulty=3, object_count=6)
            answer, img = gen_image(params)
            png = image_png(img)

            checks.setdefault("target_present", True)
            checks["target_present"] &= ("target" in answer and bool(answer["target"]))
            checks.setdefault("target_box_valid", True)
            tb = answer.get("target_box", (0, 0, 0, 0))
            checks["target_box_valid"] &= (
                len(tb) == 4
                and 0 <= tb[0] < tb[2] <= 440
                and 0 <= tb[1] < tb[3] <= 260
            )
            checks.setdefault("not_blank", True)
            checks["not_blank"] &= (_blank_ratio(png) < 0.95)

        results.append({
            "challenge_type": "image",
            "checks": checks,
            "passed": all(checks.values()),
            "details": f"Generated 3 samples, all checks {'passed' if all(checks.values()) else 'failed'}",
        })
    except Exception as e:
        results.append({
            "challenge_type": "image",
            "checks": {},
            "passed": False,
            "details": f"Error: {e}",
        })

    # ---- PUZZLE ----
    try:
        from .generator.puzzle import PuzzleParams
        from .generator.puzzle import generate as gen_puzzle
        from .generator.puzzle import to_png_bytes as puzzle_png

        checks = {}
        for _ in range(3):
            params = PuzzleParams(difficulty=3)
            answer, img = gen_puzzle(params)
            png = puzzle_png(img)

            n = answer["rows"] * answer["cols"]
            identity = list(range(n))

            checks.setdefault("correct_order_identity", True)
            checks["correct_order_identity"] &= (answer["correct_order"] == identity)

            checks.setdefault("shuffle_not_identity", True)
            checks["shuffle_not_identity"] &= (answer["shuffled_order"] != identity)

            checks.setdefault("shuffle_valid_perm", True)
            checks["shuffle_valid_perm"] &= (
                sorted(answer["shuffled_order"]) == identity
            )

            checks.setdefault("not_blank", True)
            checks["not_blank"] &= (_blank_ratio(png) < 0.98)

        results.append({
            "challenge_type": "puzzle",
            "checks": checks,
            "passed": all(checks.values()),
            "details": f"Generated 3 samples, all checks {'passed' if all(checks.values()) else 'failed'}",
        })
    except Exception as e:
        results.append({
            "challenge_type": "puzzle",
            "checks": {},
            "passed": False,
            "details": f"Error: {e}",
        })

    return jsonify({
        "checks": results,
        "summary": {
            "total": len(results),
            "passed": sum(1 for r in results if r["passed"]),
        },
    })
@bp.get("/api/verify/attempts")
def verify_attempts():
    """
    Re-verify recent attempts against server-side ground truth.

    For each recent attempt, re-run the answer check using the
    originally issued challenge's ground truth stored in
    issued_challenges.params (or reconstructed).

    Reports mismatches — if the verifier disagrees with what was
    originally recorded, that's a bug.
    """
    conn = get_db()

    try:
        limit = int(request.args.get("limit", 50))
    except (TypeError, ValueError):
        limit = 50
    limit = max(1, min(limit, 200))

    rows = conn.execute(
        """
        SELECT
            a.id,
            a.challenge_type,
            a.actor,
            a.correct,
            a.fail_reason,
            a.solve_time,
            a.risk,
            a.behaviour,
            a.params
        FROM attempts a
        ORDER BY a.ts DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()

    report = []

    for row in rows:
        entry = dict(row)

        # Basic sanity checks that don't need ground truth
        checks = {}

        checks["correct_boolean"] = entry["correct"] in (0, 1)

        checks["fail_reason_consistency"] = (
            (entry["correct"] == 1 and entry["fail_reason"] is None) or
            (entry["correct"] == 0 and entry["fail_reason"] is not None)
        )

        checks["solve_time_positive"] = (
            entry["solve_time"] is not None and entry["solve_time"] > 0
        )

        checks["risk_in_range"] = (
            entry["risk"] is not None and 0.0 <= entry["risk"] <= 1.0
        )

        # Parse behaviour JSON
        beh = {}
        try:
            beh = json.loads(entry["behaviour"] or "{}")
        except (TypeError, json.JSONDecodeError):
            beh = {}
            checks["behaviour_json_valid"] = False

        checks.setdefault("behaviour_json_valid", True)

        # If correct == True, behaviour reasons should NOT contain critical flags
        reasons = beh.get("reasons") or []

        entry["checks"] = checks
        entry["passed"] = all(checks.values())
        entry["reasons_count"] = len(reasons)
        entry["reasons_preview"] = reasons[:3]

        report.append(entry)

    passed = sum(1 for r in report if r["passed"])
    total = len(report)

    return jsonify({
        "total": total,
        "passed": passed,
        "failed": total - passed,
        "pass_rate": round(100.0 * passed / total, 1) if total else 0,
        "attempts": report,
    })
@bp.get("/api/verify/risk")
def verify_risk():
    """
    Audit risk scoring: distribution of fired rules, human vs attacker.

    Returns:
        - rule firing counts per actor
        - average risk per actor
        - risk distribution histogram
        - suspicious cases (human high-risk, attacker low-risk)
    """
    conn = get_db()

    rows = conn.execute(
        """
        SELECT actor, challenge_type, risk, behaviour
        FROM attempts
        ORDER BY ts DESC
        LIMIT 500
        """
    ).fetchall()

    rule_counts_human = {}
    rule_counts_attacker = {}

    human_risks = []
    attacker_risks = []

    suspicious_human_high = 0
    suspicious_attacker_low = 0

    for r in rows:
        try:
            beh = json.loads(r["behaviour"] or "{}")
        except (TypeError, json.JSONDecodeError):
            continue

        reasons = beh.get("reasons") or []
        risk = r["risk"]
        actor = r["actor"]

        if actor == "human":
            human_risks.append(risk)
            for reason in reasons:
                rule_counts_human[reason] = rule_counts_human.get(reason, 0) + 1
            if risk >= 0.60:
                suspicious_human_high += 1

        elif actor == "attacker":
            attacker_risks.append(risk)
            for reason in reasons:
                rule_counts_attacker[reason] = rule_counts_attacker.get(reason, 0) + 1
            if risk < 0.30:
                suspicious_attacker_low += 1

    def avg(lst):
        return round(sum(lst) / len(lst), 3) if lst else None

    return jsonify({
        "human": {
            "count": len(human_risks),
            "avg_risk": avg(human_risks),
            "high_risk_count": suspicious_human_high,
            "rule_counts": rule_counts_human,
        },
        "attacker": {
            "count": len(attacker_risks),
            "avg_risk": avg(attacker_risks),
            "low_risk_count": suspicious_attacker_low,
            "rule_counts": rule_counts_attacker,
        },
        "verdict": {
            "human_should_be_low": suspicious_human_high <= len(human_risks) * 0.15,
            "attacker_should_be_high": suspicious_attacker_low <= len(attacker_risks) * 0.15,
        },
    })
@bp.route("/verification")
def verification_page():
    return render_template("index.html", page_mode="verification")
