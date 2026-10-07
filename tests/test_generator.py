import random

from app.generator.text import ALPHABET, SIZE, Params, generate, random_text


def test_params_are_clamped():
    p = Params(distort=99, noise=-5, rotate=3)
    assert (p.distort, p.noise, p.rotate) == (10, 0, 3)


def test_text_uses_safe_alphabet_and_length():
    text = random_text(random.Random(1), 6)
    assert len(text) == 6 and set(text) <= set(ALPHABET)


def test_same_seed_is_reproducible():
    a_text, a_img = generate(Params(5, 5, 5), seed=42)
    b_text, b_img = generate(Params(5, 5, 5), seed=42)
    assert a_text == b_text and a_img.tobytes() == b_img.tobytes()


def test_image_size_is_fixed():
    _, img = generate(Params(10, 10, 10), seed=1)
    assert img.size == SIZE


def test_each_knob_changes_the_image_independently():
    base = generate(Params(0, 0, 0), seed=7)[1].tobytes()
    for knob in ("distort", "noise", "rotate"):
        changed = generate(Params(**{knob: 8}), seed=7)[1].tobytes()
        assert changed != base, f"{knob} had no visible effect"
