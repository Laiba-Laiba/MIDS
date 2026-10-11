from app.analysis import band, risk_score

HUMAN = {
    "keys": 7,
    "moves": 120,
    "pasted": False,
    "first_key_ms": 1500,
    "key_gap_mean": 180,
    "key_gap_std": 60,
}

BOT = {
    "keys": 0,
    "moves": 0,
    "pasted": False,
    "first_key_ms": None,
    "key_gap_mean": 0,
    "key_gap_std": 0,
}


def test_human_like_session_scores_zero():
    score, reasons = risk_score(HUMAN, 6.0, 0, 1, user_agent="Mozilla/5.0 Chrome/126")
    assert score == 0 and reasons == []


def test_naive_bot_is_high_risk():
    score, reasons = risk_score(BOT, 0.2, 0, 1, user_agent="python-requests/2.31")
    assert band(score) == "high"
    assert any("solved in under 1 second" in reason for reason in reasons)


def test_touch_device_is_not_penalised_for_no_pointer():
    beh = dict(HUMAN, moves=0)
    _, reasons = risk_score(
        beh, 6.0, 0, 1, user_agent="Mozilla/5.0 (iPhone) Mobile Safari"
    )
    assert "no pointer movement" not in reasons


def test_instant_first_key_and_fast_typing_are_flagged():
    beh = dict(HUMAN, first_key_ms=80, key_gap_mean=15, key_gap_std=30)
    _, reasons = risk_score(beh, 6.0, 0, 1, user_agent="Mozilla/5.0")
    assert any("250 ms" in r for r in reasons) and any("40 ms" in r for r in reasons)


def test_missing_first_key_does_not_crash():
    score, _ = risk_score(
        dict(HUMAN, first_key_ms=None), 6.0, 0, 1, user_agent="Mozilla/5.0"
    )
    assert score == 0


def test_score_is_capped_at_one():
    score, _ = risk_score(
        dict(BOT, pasted=True), 0.1, 50, 50, issued_last_min=99, user_agent=""
    )
    assert score == 1.0


def test_bands():
    assert (band(0.1), band(0.3), band(0.6)) == ("low", "medium", "high")
