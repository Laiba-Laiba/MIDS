import base64
import json
import math
import time
import uuid

from flask import Blueprint, g, jsonify, render_template, request

from .analysis import risk_score
from .db import get_db
from .generator.text import (
    Params as TextParams,
    generate as generate_text,
    to_png_bytes as text_to_png_bytes,
)
from .generator.image import (
    ImageParams,
    generate as generate_image,
    to_png_bytes as image_to_png_bytes,
)
from .generator.puzzle import (
    PuzzleParams,
    generate as generate_puzzle,
    to_png_bytes as puzzle_to_png_bytes,
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

        return (
            str(submitted or "").strip().upper()
            == str(expected).strip().upper()
        )

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

        return (
            x1 <= x <= x2
            and y1 <= y <= y2
        )

    # ------------------------------------------------------------------
    # PUZZLE
    # ------------------------------------------------------------------

    if challenge_type == "puzzle":

        if not isinstance(submitted, list):
            return False

        try:
            submitted_order = [
                int(value)
                for value in submitted
            ]

            expected_order = [
                int(value)
                for value in expected["correct_order"]
            ]
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
        key
        for key, value in CHALLENGES.items()
        if now - value["issued"] > TTL_SECONDS
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
        return int(
            number(key, high)
        )

    def boolean(key):
        return bool(
            raw.get(key)
        )

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
            value = float(
                pointer_types.get(pointer_type) or 0
            )
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

        "pasted": boolean(
            "pasted"
        ),

        "paste_count": integer(
            "paste_count",
            100,
        ),

        "copied": boolean(
            "copied"
        ),

        "copy_count": integer(
            "copy_count",
            100,
        ),

        "cut": boolean(
            "cut"
        ),

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

        "image_wrong_selections":
            image_wrong_selections,

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
    return render_template(
        "index.html"
    )


# ---------------------------------------------------------------------------
# Create challenge
# ---------------------------------------------------------------------------

@bp.post("/api/captcha/new")
def new_challenge():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    challenge_type = str(
        data.get(
            "type",
            "text",
        )
    ).lower().strip()

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

            answer, img = generate_text(
                params
            )

            params_dict = params.as_dict()

            png_bytes = text_to_png_bytes(
                img
            )

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

            answer, img = generate_image(
                params
            )

            params_dict = params.as_dict()

            png_bytes = image_to_png_bytes(
                img
            )

            # Only expose what the user needs to know.
            #
            # target_box is NEVER returned.
            public_answer = {
                "target": answer["target"],
            }

        # ==============================================================
        # PUZZLE
        # ==============================================================

        elif challenge_type == "puzzle":

            params = PuzzleParams(
                difficulty=data.get(
                    "difficulty",
                    3,
                )
            )

            answer, img = generate_puzzle(
                params
            )

            params_dict = params.as_dict()

            png_bytes = puzzle_to_png_bytes(
                img
            )

            # Do not expose correct_order.
            public_answer = {
                "rows": answer["rows"],
                "cols": answer["cols"],
            }

        # ==============================================================
        # UNKNOWN
        # ==============================================================

        else:

            return jsonify(
                error="Unknown challenge type."
            ), 400

    except (
        TypeError,
        ValueError,
    ):

        return jsonify(
            error="Invalid challenge parameters."
        ), 400

    # Remove old challenges.
    _purge_expired()

    cid = uuid.uuid4().hex

    issued = time.time()

    CHALLENGES[cid] = {

        "type": challenge_type,

        # Server-side ground truth.
        "answer": answer,

        "params": params_dict,

        "issued": issued,

        "sid": g.sid,
    }

    # --------------------------------------------------------------
    # Log issued challenge
    # --------------------------------------------------------------

    conn = get_db()

    conn.execute(
        """
        INSERT INTO issued_challenges
        (
            session_id,
            challenge_type,
            ts
        )
        VALUES (?, ?, ?)
        """,
        (
            g.sid,
            challenge_type,
            issued,
        ),
    )

    conn.commit()

    # --------------------------------------------------------------
    # Encode image
    # --------------------------------------------------------------

    png = base64.b64encode(
        png_bytes
    ).decode()

    return jsonify(

        id=cid,

        type=challenge_type,

        image=(
            "data:image/png;base64,"
            + png
        ),

        params=params_dict,

        task=public_answer,
    )


# ---------------------------------------------------------------------------
# Verify challenge
# ---------------------------------------------------------------------------

@bp.post("/api/captcha/verify")
def verify():

    data = (
        request.get_json(
            silent=True
        )
        or {}
    )

    challenge_id = data.get("id")

    challenge = CHALLENGES.get(
        challenge_id
    )

    expired = (
        challenge is not None
        and
        time.time() - challenge["issued"]
        > TTL_SECONDS
    )

    # --------------------------------------------------------------
    # Validate challenge
    # --------------------------------------------------------------

    if (
        challenge is None
        or expired
        or challenge["sid"] != g.sid
    ):

        return jsonify(
            error=(
                "This challenge expired. "
                "Request a new one."
            )
        ), 404

    # --------------------------------------------------------------
    # CAPTCHA is single-use
    # --------------------------------------------------------------

    del CHALLENGES[
        challenge_id
    ]

    # --------------------------------------------------------------
    # Verify answer according to type
    # --------------------------------------------------------------

    correct = _verify_challenge_answer(
        challenge,
        data.get("answer"),
    )

    now = time.time()

    solve_time = round(
        now - challenge["issued"],
        2,
    )

    # --------------------------------------------------------------
    # Clean telemetry
    # --------------------------------------------------------------

    beh = _clean_behaviour(
        data.get("behaviour")
    )

    # Store challenge type in behaviour too.
    beh["challenge_type"] = (
        challenge["type"]
    )

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
        user_agent=request.headers.get(
            "User-Agent",
            "",
        ),
    )

    beh["reasons"] = reasons

    # --------------------------------------------------------------
    # Save attempt
    # --------------------------------------------------------------

    conn.execute(
        """
        INSERT INTO attempts
        (
            session_id,
            challenge_type,
            params,
            correct,
            solve_time,
            behaviour,
            risk,
            ts
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            g.sid,

            challenge["type"],

            json.dumps(
                challenge["params"]
            ),

            int(correct),

            solve_time,

            json.dumps(
                beh
            ),

            risk,

            now,
        ),
    )

    conn.commit()

    return jsonify(
        correct=correct,
        solve_time=solve_time,
        challenge_type=challenge["type"],
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
            "success_rate": (
                type_wins / type_total
                if type_total
                else None
            ),
            "avg_risk": row["avg_risk"],
            "avg_solve_time": (
                row["avg_solve_time"]
            ),
        }

    return jsonify(

        total=total,

        successes=wins,

        success_rate=(
            wins / total
            if total
            else None
        ),

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
            (
                row["id"],
            ),
        ).fetchone()

        if latest:

            latest_data = dict(
                latest
            )

            try:

                behaviour = json.loads(
                    latest_data.get(
                        "behaviour"
                    )
                    or "{}"
                )

            except (
                TypeError,
                json.JSONDecodeError,
            ):

                behaviour = {}

            session_data[
                "latest_attempt"
            ] = {

                "id":
                    latest_data["id"],

                "type":
                    latest_data[
                        "challenge_type"
                    ],

                "correct":
                    bool(
                        latest_data[
                            "correct"
                        ]
                    ),

                "solve_time":
                    latest_data[
                        "solve_time"
                    ],

                "timestamp":
                    latest_data["ts"],

                "risk":
                    latest_data["risk"],

                "behaviour":
                    behaviour,
            }

        else:

            session_data[
                "latest_attempt"
            ] = None

        result.append(
            session_data
        )

    return jsonify(result)