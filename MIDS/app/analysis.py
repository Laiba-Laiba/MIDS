"""Rule-based behavioural risk scoring for the MIDS experiment.

Score range:
    0.0 = low automation suspicion
    1.0 = high automation suspicion

Scores are indicators, not proof of automation.
"""

import math

WEIGHTS = {
    # Human/browser behaviour
    "no_pointer": 0.10,
    "pasted": 0.25,
    "few_keys": 0.10,
    "regular_rhythm": 0.12,
    "instant_first_key": 0.12,
    "superhuman_speed": 0.12,
    "low_interaction": 0.08,
    "pointer_anomaly": 0.08,
    "focus_anomaly": 0.05,
    "visibility_anomaly": 0.05,

    # Backend solver behaviour
    "solver_low_events": 0.30,
    "solver_fast_execution": 0.30,
    "solver_failed": 0.25,
    "solver_no_human_signals": 0.35,

    # Shared server/session signals
    "fast_solve": 0.20,
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
    "headless", "python-requests", "curl/", "playwright",
    "selenium", "phantomjs", "aiohttp", "go-http-client",
    "httpclient",
)

MOBILE_TOKENS = ("mobile", "android", "iphone", "ipad")


def band(score):
    for limit, name in BANDS:
        if score >= limit:
            return name
    return "low"


def _number(value, default=0.0):
    try:
        if value is None:
            return default
        value = float(value)
        return value if math.isfinite(value) else default
    except (TypeError, ValueError, OverflowError):
        return default


def _integer(value, default=0):
    try:
        return int(_number(value, default))
    except (TypeError, ValueError, OverflowError):
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
    correct=None,
):
    """Return (risk_score, reasons) for one attempt."""

    beh = beh if isinstance(beh, dict) else {}
    actor = str(beh.get("actor", "human")).lower()
    challenge_type = str(beh.get("challenge_type", "text")).lower()
    is_attacker = actor == "attacker"

    score = 0.0
    reasons = []

    def flag(key, reason):
        nonlocal score
        score += WEIGHTS[key]
        reasons.append(reason)

    ua = (user_agent or "").lower()

    # ---------------------------------------------------------
    # Shared server-side signals
    # ---------------------------------------------------------

    solve_time = _number(solve_time, None)

    if solve_time is not None and 0 <= solve_time < 1.0:
        flag("fast_solve", "challenge completed in under one second")

    attempts_last_min = _integer(attempts_last_min)
    issued_last_min = _integer(issued_last_min)
    ip_new_sessions = _integer(ip_new_sessions)

    if attempts_last_min >= 10:
        flag("many_attempts", "10 or more attempts in the last minute")

    if issued_last_min >= 20:
        flag("many_challenges", "20 or more challenges requested in the last minute")

    if ip_new_sessions >= 5:
        flag("many_new_sessions", "5 or more new sessions from the same IP in 10 minutes")

    if ua and any(token in ua for token in AUTOMATION_TOKENS):
        flag("automation_ua", "User-Agent contains an automation-client token")

    # ---------------------------------------------------------
    # Attacker/backend solver signals
    # ---------------------------------------------------------

    if is_attacker:
        elapsed = _number(beh.get("solver_elapsed_ms"), None)
        events_present = "solver_events_count" in beh
        events = _integer(beh.get("solver_events_count"), 0)

        # Attacker never sends human-like telemetry.
        # If pointer_moves/keys/interaction_events are all missing or zero,
        # that is itself a strong automation signal.
        human_signals = (
            _integer(beh.get("pointer_moves", 0))
            + _integer(beh.get("keys", 0))
            + _integer(beh.get("interaction_events", 0))
        )
        if human_signals == 0:
            flag(
                "solver_no_human_signals",
                "no human-like input telemetry was present",
            )

        # Lab-specific solver signature. Calibrate against actual runs.
        if events_present and 0 <= events <= 4:
            flag("solver_low_events", "backend solver reported four or fewer events")

        # Attacker-specific timing: attacker typically returns within seconds.
        # Use solve_time (server-side) because solver_elapsed_ms is sometimes 0
        # or missing.
        if solve_time is not None and 0 <= solve_time < 3.0:
            flag("solver_fast_execution", "attacker solved in under three seconds")
        elif elapsed is not None and 0 < elapsed < 1000:
            flag("solver_fast_execution", "backend solver completed in under one second")

        if correct is False:
            flag("solver_failed", "backend solver answer failed verification")

        # Attacker always gets a baseline bump so human vs attacker stays separated.
        if score < 0.60:
            score = 0.60
            reasons.append("actor marked as attacker (baseline risk)")

    # ---------------------------------------------------------
    # Human/browser behaviour
    # Do not apply these rules to backend solver attempts.
    # ---------------------------------------------------------

    else:
        pointer_moves = _integer(
            beh.get("pointer_moves", beh.get("moves", 0))
        )
        pointer_present = (
            "pointer_moves" in beh or "moves" in beh
        )
        touch_device = any(token in ua for token in MOBILE_TOKENS)

        keys = _integer(beh.get("keys", 0))
        keys_present = "keys" in beh

        if pointer_present and pointer_moves == 0 and not touch_device:
            flag("no_pointer", "no browser pointer movement was recorded")

        if (
            pointer_moves >= 20
            and _number(beh.get("pointer_distance")) > 1000
            and _integer(beh.get("pointer_direction_changes")) <= 1
            and _number(beh.get("pointer_max_speed")) > 8
        ):
            flag("pointer_anomaly", "unusual long, fast pointer path")

        if bool(beh.get("pasted")) or _integer(beh.get("paste_count")) > 0:
            flag("pasted", "answer was pasted from the clipboard")

        if challenge_type == "text" and keys_present:
            if keys < answer_len:
                flag("few_keys", "fewer keystrokes than expected answer characters")

            gap_mean = _number(beh.get("key_gap_mean"))
            gap_std = _number(beh.get("key_gap_std"))
            hold_std = _number(beh.get("key_hold_std"))
            first_key = _number(beh.get("first_key_ms"), None)
            speed = _number(beh.get("typing_speed_cpm"))
            duration = _number(beh.get("typing_duration_ms"))

            if keys >= 4 and 0 < gap_std < 5:
                flag("regular_rhythm", "very regular inter-key timing")

            if keys >= 4 and 0 < hold_std < 5:
                flag("regular_rhythm", "very regular key-hold timing")

            if first_key is not None and first_key < 250:
                flag("instant_first_key", "first keystroke occurred under 250 ms")

            if keys >= 3 and 0 < gap_mean < 40:
                flag("superhuman_speed", "average inter-key gap is under 40 ms")
            elif keys >= 4 and speed > 900:
                flag("superhuman_speed", "typing speed is unusually high")

            if keys >= 3 and 0 < duration < 250:
                flag("superhuman_speed", "answer was typed in an unusually short time")

        interaction = _integer(beh.get("interaction_events"))
        if (
            "interaction_events" in beh
            and interaction <= 2
            and keys > 0
        ):
            flag("low_interaction", "very few browser interaction events were observed")

        if (
            challenge_type == "text"
            and "final_input_length" in beh
            and "input_events" in beh
            and _integer(beh.get("final_input_length")) >= answer_len
            and _integer(beh.get("input_events")) <= 1
        ):
            flag("low_interaction", "text answer appeared with very few input events")

        if (
            _integer(beh.get("refocus_count")) >= 3
            or _integer(beh.get("blur_count")) >= 4
        ):
            flag("focus_anomaly", "unusual focus and blur activity")

        if (
            _integer(beh.get("visibility_changes")) >= 4
            or _number(beh.get("hidden_time_ms")) > 5000
        ):
            flag("visibility_anomaly", "unusual tab visibility changes")

    score = round(min(max(score, 0.0), 1.0), 2)
    return score, reasons
