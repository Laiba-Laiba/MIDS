"""Rule-based behavioural risk score.

The score combines client-side behavioural telemetry with server-side
session signals.

0.0 = lower automation suspicion
1.0 = higher automation suspicion

This is intentionally transparent and rule-based for Mid-1. The weights and
thresholds can be tuned later after collecting human/bot experiment data.
"""

WEIGHTS = {
    # Client-side behaviour
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
    # Server-side behaviour
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
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value, default=0):
    """Safely convert telemetry values to an integer."""
    try:
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

    The function is deliberately defensive because telemetry comes from the
    browser and therefore cannot be trusted completely.
    """
    score = 0.0
    reasons = []

    def flag(key, why):
        nonlocal score
        score += WEIGHTS[key]
        reasons.append(why)

    # ------------------------------------------------------------------
    # Normalised telemetry
    # ------------------------------------------------------------------

    pointer_moves = _integer(beh.get("pointer_moves", 0))

    # Backward compatibility with the original telemetry format.
    if pointer_moves == 0:
        pointer_moves = _integer(beh.get("moves", 0))

    keys = _integer(beh.get("keys", 0))
    backspaces = _integer(beh.get("backspaces", 0))
    deletes = _integer(beh.get("deletes", 0))

    corrections = _integer(
        beh.get(
            "corrections",
            backspaces + deletes,
        )
    )

    pasted = bool(beh.get("pasted", False))

    first_key_ms = beh.get("first_key_ms")

    if first_key_ms is not None:
        first_key_ms = _number(first_key_ms, None)

    key_gap_mean = _number(beh.get("key_gap_mean", 0))

    key_gap_std = _number(beh.get("key_gap_std", 0))

    key_hold_std = _number(beh.get("key_hold_std", 0))

    typing_duration_ms = _number(beh.get("typing_duration_ms", 0))

    typing_speed_cpm = _number(beh.get("typing_speed_cpm", 0))

    pointer_distance = _number(beh.get("pointer_distance", 0))

    pointer_max_speed = _number(beh.get("pointer_max_speed", 0))

    pointer_direction_changes = _integer(beh.get("pointer_direction_changes", 0))

    blur_count = _integer(beh.get("blur_count", 0))

    refocus_count = _integer(beh.get("refocus_count", 0))

    visibility_changes = _integer(beh.get("visibility_changes", 0))

    hidden_time_ms = _number(beh.get("hidden_time_ms", 0))

    # Only use interaction telemetry if the browser actually sent it.
    interaction_events_present = "interaction_events" in beh
    interaction_events = _integer(beh.get("interaction_events", 0))

    final_input_length_present = "final_input_length" in beh
    final_input_length = _integer(beh.get("final_input_length", 0))

    input_events_present = "input_events" in beh
    input_events = _integer(beh.get("input_events", 0))

    paste_count = _integer(beh.get("paste_count", 0))

    # ------------------------------------------------------------------
    # User-agent information
    # ------------------------------------------------------------------

    ua = (user_agent or "").lower()

    touch_device = any(token in ua for token in MOBILE_TOKENS)

    # ------------------------------------------------------------------
    # 1. Pointer behaviour
    # ------------------------------------------------------------------

    if pointer_moves == 0 and not touch_device:
        flag(
            "no_pointer",
            "no pointer movement during the challenge",
        )

    if pointer_max_speed > 8:
        flag(
            "pointer_anomaly",
            "unusually high pointer movement speed",
        )

    if (
        pointer_distance > 1000
        and pointer_direction_changes <= 1
        and pointer_moves >= 20
    ):
        flag(
            "pointer_anomaly",
            "unusually long and mechanically straight pointer path",
        )

    # ------------------------------------------------------------------
    # 2. Clipboard behaviour
    # ------------------------------------------------------------------

    if pasted or paste_count > 0:
        flag(
            "pasted",
            "answer was pasted from the clipboard",
        )

    # ------------------------------------------------------------------
    # 3. Keyboard behaviour
    # ------------------------------------------------------------------

    if keys < answer_len:
        flag(
            "few_keys",
            "fewer keystrokes than expected answer characters",
        )

    if keys >= 4:
        correction_rate = corrections / keys

        if correction_rate >= 0.75:
            flag(
                "high_correction_rate",
                "very high keyboard correction rate",
            )

    if keys >= 4 and key_gap_std > 0 and key_gap_std < 5:
        flag(
            "regular_rhythm",
            "very regular inter-key timing",
        )

    if keys >= 4 and key_hold_std > 0 and key_hold_std < 5:
        flag(
            "regular_key_hold",
            "very regular key-hold timing",
        )

    if first_key_ms is not None and first_key_ms < 250:
        flag(
            "instant_first_key",
            "first keystroke occurred under 250 ms after challenge display",
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

    if keys >= 3 and 0 < typing_duration_ms < 250:
        flag(
            "very_short_typing",
            "typing duration is unusually short",
        )

    # ------------------------------------------------------------------
    # 4. Form / interaction behaviour
    # ------------------------------------------------------------------

    if interaction_events_present and interaction_events <= 2 and keys > 0:
        flag(
            "low_interaction",
            "very few interaction events were observed",
        )

    if refocus_count >= 3 or blur_count >= 4:
        flag(
            "focus_anomaly",
            "unusual focus and blur activity",
        )

    if (
        final_input_length_present
        and input_events_present
        and final_input_length >= answer_len
        and input_events <= 1
    ):
        flag(
            "low_interaction",
            "final answer appeared with very few input events",
        )

    # ------------------------------------------------------------------
    # 5. Page / visibility behaviour
    # ------------------------------------------------------------------

    if visibility_changes >= 4 or hidden_time_ms > 5000:
        flag(
            "visibility_anomaly",
            "unusual tab visibility changes during the challenge",
        )

    # ------------------------------------------------------------------
    # 6. Server-side timing
    # ------------------------------------------------------------------

    solve_time = _number(
        solve_time,
        0,
    )

    if solve_time < 1.0:
        flag(
            "fast_solve",
            "challenge solved in under 1 second",
        )

    # ------------------------------------------------------------------
    # 7. Session-level behaviour
    # ------------------------------------------------------------------

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

    # ------------------------------------------------------------------
    # 8. User-Agent
    # ------------------------------------------------------------------

    if not ua or any(token in ua for token in AUTOMATION_TOKENS):
        flag(
            "automation_ua",
            "User-Agent resembles an automation client or is empty",
        )

    # ------------------------------------------------------------------
    # Final score
    # ------------------------------------------------------------------

    score = round(
        min(
            max(score, 0.0),
            1.0,
        ),
        2,
    )

    return score, reasons
