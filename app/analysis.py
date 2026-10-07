def risk_score(beh, solve_time, attempts_last_min, ip_new_sessions, answer_len=5):
    """Return (score 0..1, reasons). 0 = insaan jaisa, 1 = bot jaisa.

    Rule-based v1. Weights andaze hain; real data aane ke baad tune karein
    ya ML model (Logistic Regression / Isolation Forest) se replace karein.
    """
    score, reasons = 0.0, []

    def flag(weight, why):
        nonlocal score
        score += weight
        reasons.append(why)

    # client-side signals (spoof ho sakte hain)
    if beh["moves"] == 0:
        flag(0.15, "no pointer movement")
    if beh["pasted"]:
        flag(0.25, "answer pasted")
    if beh["keys"] < answer_len:
        flag(0.10, "fewer keystrokes than characters")
    if beh["keys"] >= 3 and beh["key_gap_std"] < 5:
        flag(0.15, "machine-regular typing rhythm")
    # server-side signals (spoof nahi hote)
    if solve_time < 1.0:
        flag(0.25, "solved in under 1 second")
    if attempts_last_min >= 10:
        flag(0.25, "10+ attempts in the last minute")
    if ip_new_sessions >= 5:
        flag(0.20, "5+ new sessions from this IP in 10 minutes")
    return min(score, 1.0), reasons