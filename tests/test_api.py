from app.routes import CHALLENGES


def _new(client, **params):
    return client.post("/api/captcha/new", json=params)


def test_index_page_loads(client):
    r = client.get("/")
    assert r.status_code == 200


def test_new_challenge_returns_image_and_id(client):
    r = _new(client, distort=3, noise=2, rotate=1)
    assert r.status_code == 200
    data = r.get_json()
    assert data["image"].startswith("data:image/png;base64,")
    assert data["params"] == {"distort": 3, "noise": 2, "rotate": 1}
    assert data["id"] in CHALLENGES


def test_new_challenge_rejects_non_numbers(client):
    r = _new(client, distort="abc")
    assert r.status_code == 400


def test_correct_answer_is_accepted(client):
    new = _new(client).get_json()
    answer = CHALLENGES[new["id"]]["answer"]
    r = client.post("/api/captcha/verify", json={"id": new["id"], "answer": answer.lower()})
    assert r.get_json()["correct"] is True


def test_wrong_answer_is_rejected(client):
    new = _new(client).get_json()
    r = client.post("/api/captcha/verify", json={"id": new["id"], "answer": "WRONG"})
    assert r.get_json()["correct"] is False


def test_challenge_is_single_use(client):
    new = _new(client).get_json()
    client.post("/api/captcha/verify", json={"id": new["id"], "answer": "X"})
    r = client.post("/api/captcha/verify", json={"id": new["id"], "answer": "X"})
    assert r.status_code == 404


def test_stats_counts_attempts(client):
    for ok in (True, False):
        new = _new(client).get_json()
        answer = CHALLENGES[new["id"]]["answer"] if ok else "WRONG"
        client.post("/api/captcha/verify", json={"id": new["id"], "answer": answer})
    stats = client.get("/api/stats").get_json()
    assert stats["total"] == 2
    assert stats["successes"] == 1
    assert stats["success_rate"] == 0.5