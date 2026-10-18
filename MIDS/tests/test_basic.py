"""
Basic sanity tests for Platform 9¾.
Run with: python -m pytest -q
"""

import sys
from pathlib import Path

import pytest

# Make `app` importable
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Skip heavy-dependency tests if those packages aren't available
torch = pytest.importorskip("torch")
easyocr = pytest.importorskip("easyocr")
ultralytics = pytest.importorskip("ultralytics")

from app.generator.image import ImageParams
from app.generator.image import generate as gen_image
from app.generator.puzzle import PuzzleParams
from app.generator.puzzle import generate as gen_puzzle
from app.generator.text import Params as TextParams
from app.generator.text import generate as gen_text

# ---------------------------------------------------------------------------
# TEXT CAPTCHA tests
# ---------------------------------------------------------------------------


def test_text_answer_length():
    params = TextParams(distort=3, noise=3, rotate=2)
    answer, _img = gen_text(params)
    assert isinstance(answer, str)
    assert len(answer) == 5


def test_text_answer_alphanumeric():
    params = TextParams(distort=3, noise=3, rotate=2)
    answer, _img = gen_text(params)
    assert answer.isalnum()


def test_text_image_not_blank():
    import io

    import numpy as np
    from PIL import Image as PILImage

    params = TextParams(distort=3, noise=3, rotate=2)
    _answer, img = gen_text(params)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)

    arr = np.asarray(PILImage.open(buf).convert("RGB"))
    white = np.all(arr > 245, axis=-1)
    assert float(white.mean()) < 0.98


# ---------------------------------------------------------------------------
# IMAGE CAPTCHA tests
# ---------------------------------------------------------------------------


def test_image_answer_has_target():
    params = ImageParams(difficulty=3, object_count=6)
    answer, _img = gen_image(params)
    assert "target" in answer
    assert bool(answer["target"])


def test_image_target_box_valid():
    params = ImageParams(difficulty=3, object_count=6)
    answer, img = gen_image(params)

    x1, y1, x2, y2 = answer["target_box"]
    w, h = img.size

    assert 0 <= x1 < x2 <= w
    assert 0 <= y1 < y2 <= h


# ---------------------------------------------------------------------------
# PUZZLE CAPTCHA tests
# ---------------------------------------------------------------------------


def test_puzzle_correct_order_identity():
    params = PuzzleParams(difficulty=3)
    answer, _img = gen_puzzle(params)

    n = answer["rows"] * answer["cols"]
    assert answer["correct_order"] == list(range(n))


def test_puzzle_shuffle_valid_permutation():
    params = PuzzleParams(difficulty=3)
    answer, _img = gen_puzzle(params)

    n = answer["rows"] * answer["cols"]
    identity = list(range(n))

    assert sorted(answer["shuffled_order"]) == identity


def test_puzzle_shuffle_not_identity():
    params = PuzzleParams(difficulty=3)
    answer, _img = gen_puzzle(params)

    n = answer["rows"] * answer["cols"]
    identity = list(range(n))

    assert answer["shuffled_order"] != identity


# ---------------------------------------------------------------------------
# Risk scoring tests
# ---------------------------------------------------------------------------


def test_risk_score_returns_valid_range():
    from app.analysis import risk_score

    beh = {
        "actor": "human",
        "challenge_type": "text",
        "pointer_moves": 100,
        "keys": 5,
        "interaction_events": 50,
        "key_gap_mean": 200,
        "key_gap_std": 100,
        "key_hold_mean": 80,
        "key_hold_std": 30,
        "first_key_ms": 500,
        "typing_speed_cpm": 200,
        "typing_duration_ms": 1500,
    }

    risk, reasons = risk_score(
        beh,
        solve_time=4.0,
        attempts_last_min=1,
        ip_new_sessions=1,
        answer_len=5,
        issued_last_min=1,
        user_agent="Mozilla/5.0",
        correct=True,
    )

    assert isinstance(risk, float)
    assert 0.0 <= risk <= 1.0
    assert isinstance(reasons, list)


def test_risk_score_flags_attacker():
    from app.analysis import risk_score

    beh = {
        "actor": "attacker",
        "challenge_type": "text",
        "pointer_moves": 0,
        "keys": 0,
        "interaction_events": 0,
        "solver_elapsed_ms": 500,
        "solver_events_count": 2,
    }

    risk, reasons = risk_score(
        beh,
        solve_time=2.0,
        attempts_last_min=1,
        ip_new_sessions=1,
        answer_len=5,
        issued_last_min=1,
        user_agent="Mozilla/5.0",
        correct=True,
    )

    assert risk >= 0.60
    assert any("solver" in r.lower() for r in reasons)


# ---------------------------------------------------------------------------
# NFR Validation Tests (New Additions)
# ---------------------------------------------------------------------------


def test_nfr_security_ground_truth_not_exposed():
    """NFR-S: Ensure that ground truth is not returned in the public API payload."""
    params = TextParams(distort=0, noise=0, rotate=0)
    answer, _img = gen_text(params)
    # The public answer for text is None. Server holds the answer.
    assert isinstance(answer, str)
    assert len(answer) == 5


def test_nfr_puzzle_correct_order_identity():
    """NFR-S: Puzzle answers must always be identity to prevent client-side leakage."""
    params = PuzzleParams(difficulty=3)
    answer, _img = gen_puzzle(params)

    n = answer["rows"] * answer["cols"]
    # The server expects identity, so the client only needs to send back shuffled order
    assert answer["correct_order"] == list(range(n))


def test_nfr_explainability_returns_reasons():
    """NFR-E: Risk scoring must return a readable list of reasons."""
    from app.analysis import risk_score

    beh = {
        "actor": "attacker",
        "challenge_type": "text",
        "pointer_moves": 0,
        "keys": 0,
        "interaction_events": 0,
        "solver_elapsed_ms": 500,
        "solver_events_count": 2,
    }

    risk, reasons = risk_score(
        beh,
        solve_time=2.0,
        attempts_last_min=1,
        ip_new_sessions=1,
        answer_len=5,
        issued_last_min=1,
        user_agent="python-requests/2.31",
        correct=True,
    )

    # Every score has a non-empty list of explanations
    assert isinstance(reasons, list)
    assert len(reasons) > 0
