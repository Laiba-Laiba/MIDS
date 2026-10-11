"""Transparent rule-based behavioural risk scoring for MIDS.

Risk score:
0.0 = lower automation suspicion
1.0 = higher automation suspicion

Browser telemetry is untrusted. Scores are indicators, not proof of automation.
Backend solver telemetry must not be treated as missing human browser activity.
"""

WEIGHTS = {
    "no_pointer": 0.10,
    "pasted": 0.25,
    "few_keys": 0.10,
    "high_correction_rate": 0.08,
    "regular_rhythm": 0.12,
    "regular_key_hold": 0.08,
    "instant_first_key": 0.12,
    "superhuman_speed": 0.12,
    "very_short_typing": 0.10,
    "low_interaction": 0.08,
    "pointer_anomaly": 0.08,
    "focus_anomaly": 0.05,
    "visibility_anomaly": 0.05,
    "fast_solve": 0.25,
    "many_attempts": 0.25,
    "many_new_sessions": 0.20,
    "many_challenges": 0.15,
    "automation_ua": 0.15,
}

BANDS = (
    (0.60, "high"),
    (0.30, "medium"),
)

AUTOMATION_TOKENS = (
    "headless",
    "python-requests",
    "curl/",
    "playwright",
    "selenium",
    "phantomjs",
    "aiohttp",
    "go-http-client",
    "httpclient",
)

MOBILE_TOKENS = (
    "mobile",
    "android",
    "iphone",
    "ipad",
)


def band(score):
    """Convert a numeric risk score into low/medium/high."""
    for limit, name in BANDS:
        if score >= limit:
            return name
    return "low"


def _number(value, default=0.0):
    """Safely convert telemetry values to a number."""
    try:
        if value is None:
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value, default=0):
    """Safely convert telemetry values to an integer."""
    try:
        if value is None:
            return default
        return int(value)
    except (TypeError, ValueError):
        return default


def risk_score(
    beh,
    solve_time,
    attempts_last_min,
    ip_new_sessions,
    answer_len=5,
    *,
    issued_last_min=0,
    user_agent="",
):
    """Return (score, reasons) for one CAPTCHA attempt.

    Optional telemetry fields are evaluated only when present.
    Set beh['actor'] to 'attacker' for backend solver attempts.
    """
    beh = beh if isinstance(beh, dict) else {}

    score = 0.0
    reasons = []

    challenge_type = str(
        beh.get("challenge_type", "text")
    ).lower()

    actor = str(beh.get("actor", "human")).lower()
    backend_solver = actor == "attacker"

    def flag(key, why):
        nonlocal score
        score += WEIGHTS[key]
        reasons.append(why)

    # -------------------- Normalised telemetry --------------------

    pointer_moves = _integer(
        beh.get("pointer_moves", beh.get("moves", 0))
    )
    pointer_moves_present = (
        "pointer_moves" in beh or "moves" in beh
    )

    keys = _integer(beh.get("keys", 0))
    keys_present = "keys" in beh

    backspaces = _integer(beh.get("backspaces", 0))
    deletes = _integer(beh.get("deletes", 0))
    corrections = _integer(
        beh.get("corrections", backspaces + deletes)
    )

    pasted = bool(beh.get("pasted", False))
    paste_count = _integer(beh.get("paste_count", 0))

    first_key_ms = (
        _number(beh.get("first_key_ms"), None)
        if beh.get("first_key_ms") is not None
        else None
    )

    key_gap_mean = _number(beh.get("key_gap_mean"))
    key_gap_std = _number(beh.get("key_gap_std"))
    key_hold_std = _number(beh.get("key_hold_std"))
    typing_duration_ms = _number(
        beh.get("typing_duration_ms")
    )
    typing_speed_cpm = _number(
        beh.get("typing_speed_cpm")
    )

    pointer_distance = _number(
        beh.get("pointer_distance")
    )
    pointer_max_speed = _number(
        beh.get("pointer_max_speed")
    )
    pointer_direction_changes = _integer(
        beh.get("pointer_direction_changes")
    )

    blur_count = _integer(beh.get("blur_count"))
    refocus_count = _integer(beh.get("refocus_count"))
    visibility_changes = _integer(
        beh.get("visibility_changes")
    )
    hidden_time_ms = _number(beh.get("hidden_time_ms"))

    interaction_events_present = (
        "interaction_events" in beh
    )
    interaction_events = _integer(
        beh.get("interaction_events")
    )

    final_length_present = "final_input_length" in beh
    final_input_length = _integer(
        beh.get("final_input_length")
    )

    input_events_present = "input_events" in beh
    input_events = _integer(beh.get("input_events"))

    ua = (user_agent or "").lower()
    touch_device = any(token in ua for token in MOBILE_TOKENS)

    # -------------------- Browser interaction --------------------
    # Backend solvers do not use the human browser UI. Their absent
    # mouse/keyboard events must not trigger human interaction rules.

    if not backend_solver:
        if (
            pointer_moves_present
            and pointer_moves == 0
            and not touch_device
        ):
            flag(
                "no_pointer",
                "no browser pointer movement was recorded",
            )

        # A single peak-speed value is noisy and browser-dependent.
        # Use it as a flag only when there is also a substantial path
        # with very few direction changes.
        if (
            pointer_moves >= 20
            and pointer_distance > 1000
            and pointer_direction_changes <= 1
            and pointer_max_speed > 8
        ):
            flag(
                "pointer_anomaly",
                "long pointer path with few direction changes and a high peak speed",
            )

        if pasted or paste_count > 0:
            flag(
                "pasted",
                "answer was pasted from the clipboard",
            )

        # Keyboard rules apply only to text challenges.
        if challenge_type == "text" and keys_present:
            if keys < answer_len:
                flag(
                    "few_keys",
                    "fewer keystrokes than expected text answer characters",
                )

            if keys >= 4:
                correction_rate = corrections / max(keys, 1)
                if correction_rate >= 0.75:
                    flag(
                        "high_correction_rate",
                        "very high keyboard correction rate",
                    )

            if (
                keys >= 4
                and key_gap_std > 0
                and key_gap_std < 5
            ):
                flag(
                    "regular_rhythm",
                    "very regular inter-key timing",
                )

            if (
                keys >= 4
                and key_hold_std > 0
                and key_hold_std < 5
            ):
                flag(
                    "regular_key_hold",
                    "very regular key-hold timing",
                )

            if first_key_ms is not None and first_key_ms < 250:
                flag(
                    "instant_first_key",
                    "first keystroke occurred under 250 ms after display",
                )

            if keys >= 3 and 0 < key_gap_mean < 40:
                flag(
                    "superhuman_speed",
                    "average inter-key gap is under 40 ms",
                )

            if keys >= 4 and typing_speed_cpm > 900:
                flag(
                    "superhuman_speed",
                    "typing speed is unusually high",
                )

            if (
                keys >= 3
                and 0 < typing_duration_ms < 250
            ):
                flag(
                    "very_short_typing",
                    "typing duration is unusually short",
                )

        # Interaction count is meaningful only when the field exists.
        if (
            interaction_events_present
            and interaction_events <= 2
            and keys > 0
        ):
            flag(
                "low_interaction",
                "very few browser interaction events were observed",
            )

        if (
            final_length_present
            and input_events_present
            and challenge_type == "text"
            and final_input_length >= answer_len
            and input_events <= 1
        ):
            flag(
                "low_interaction",
                "text answer appeared with very few input events",
            )

        if refocus_count >= 3 or blur_count >= 4:
            flag(
                "focus_anomaly",
                "unusual focus and blur activity",
            )

        if visibility_changes >= 4 or hidden_time_ms > 5000:
            flag(
                "visibility_anomaly",
                "unusual tab visibility changes during the challenge",
            )

    # -------------------- Server-side timing --------------------

    solve_time = _number(solve_time, None)

    # Missing or invalid timing is not the same as a fast solve.
    if solve_time is not None and 0 <= solve_time < 1.0:
        flag(
            "fast_solve",
            "challenge completed in under one second",
        )

    # -------------------- Session-level signals --------------------

    attempts_last_min = _integer(attempts_last_min)
    issued_last_min = _integer(issued_last_min)
    ip_new_sessions = _integer(ip_new_sessions)

    if attempts_last_min >= 10:
        flag(
            "many_attempts",
            "10 or more attempts in the last minute",
        )

    if issued_last_min >= 20:
        flag(
            "many_challenges",
            "20 or more challenges requested in the last minute",
        )

    if ip_new_sessions >= 5:
        flag(
            "many_new_sessions",
            "5 or more new sessions from the same IP in 10 minutes",
        )

    # An empty User-Agent alone is weak evidence. Automation tokens
    # are a signal, not proof, and can also be spoofed.
    if ua and any(token in ua for token in AUTOMATION_TOKENS):
        flag(
            "automation_ua",
            "User-Agent contains a known automation-client token",
        )

    score = round(min(max(score, 0.0), 1.0), 2)
    return score, reasons