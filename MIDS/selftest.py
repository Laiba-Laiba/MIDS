"""
Offline check: does the solver beat the generator?  (no browser, no Flask)

Run from the project root:
    python selftest.py
"""

import base64

from app.attacker_solver import solve_challenge
from app.generator import image as gen_image
from app.generator import puzzle as gen_puzzle

N = 200


def to_data_url(png_bytes):
    return "data:image/png;base64," + base64.b64encode(png_bytes).decode()


def check_image():
    ok = 0
    for seed in range(N):
        answer, img = gen_image.generate(gen_image.ImageParams(difficulty=3, object_count=6), seed=seed)
        res = solve_challenge("image", to_data_url(gen_image.to_png_bytes(img)), answer["target"])
        g = res["guess"]
        x1, y1, x2, y2 = answer["target_box"]
        if g and x1 <= g["x"] <= x2 and y1 <= g["y"] <= y2:
            ok += 1
    print(f"image : {ok}/{N} solved")


def check_puzzle():
    ok = 0
    for seed in range(N):
        answer, img = gen_puzzle.generate(gen_puzzle.PuzzleParams(difficulty=3), seed=seed)
        res = solve_challenge(
            "puzzle",
            to_data_url(gen_puzzle.to_png_bytes(img)),
            "",
            {"rows": answer["rows"], "cols": answer["cols"]},
        )
        if [int(v) for v in res["guess"]] == answer["correct_order"]:
            ok += 1
    print(f"puzzle: {ok}/{N} solved")


if __name__ == "__main__":
    check_image()
    check_puzzle()
