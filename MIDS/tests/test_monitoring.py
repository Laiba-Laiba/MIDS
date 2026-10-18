from app.routes import CHALLENGES


def _solve(client, correct=True):
    new = client.post("/api/captcha/new", json={}).get_json()
    answer = CHALLENGES[new["id"]]["answer"] if correct else "WRONG"
    return client.post("/api/captcha/verify", json={"id": new["id"], "answer": answer})


def test_attempt_is_recorded_per_session(client):
    assert _solve(client).get_json()["correct"] is True
    _solve(client, correct=False)
    sessions = client.get("/api/monitor").get_json()
    assert len(sessions) == 1
    assert sessions[0]["attempts"] == 2
    assert sessions[0]["correct"] == 1


def test_other_session_cannot_use_my_challenge(client):
    new = client.post("/api/captcha/new", json={}).get_json()
    other = client.application.test_client()  # nayi cookie = naya session
    r = other.post("/api/captcha/verify", json={"id": new["id"], "answer": "AAAAA"})
    assert r.status_code == 403  # NFR-U: Session mismatch ab 403 return karta hai
