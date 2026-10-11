
# app/attacker_solver.py

import base64
import io
import random
import re
import time

from PIL import Image

_OCR_READER = None


def _decode_image(image_data):
    """Accept a base64 data URL or raw base64 image."""
    if not image_data:
        raise ValueError("Missing challenge image")

    if "," in image_data and image_data.startswith("data:"):
        image_data = image_data.split(",", 1)[1]

    raw = base64.b64decode(image_data)
    return Image.open(io.BytesIO(raw)).convert("RGB")


def _get_ocr_reader():
    global _OCR_READER

    if _OCR_READER is None:
        import easyocr
        _OCR_READER = easyocr.Reader(["en"], gpu=False)

    return _OCR_READER


def solve_text(image_data):
    """Return an OCR guess; this does not guarantee a correct answer."""
    image = _decode_image(image_data)
    reader = _get_ocr_reader()

    results = reader.readtext(
        __import__("numpy").array(image),
        detail=0,
        paragraph=False,
    )

    guess = "".join(str(part) for part in results).upper()
    guess = re.sub(r"[^A-Z0-9]", "", guess)

    return {
        "guess": guess,
        "events": [
            "Loading challenge image",
            "Running local EasyOCR",
            f"OCR returned {len(guess)} candidate characters",
        ],
    }


def solve_image(image_data, task_text=""):
    """
    Experimental visual baseline.
    Without a reliable object detector, choose a candidate coordinate
    rather than pretending the target was identified correctly.
    """
    image = _decode_image(image_data)
    width, height = image.size

    # Candidate points are guesses, not knowledge of the target box.
    candidates = [
        (width // 2, height // 2),
        (width // 3, height // 2),
        (2 * width // 3, height // 2),
        (width // 2, height // 3),
        (width // 2, 2 * height // 3),
    ]
    x, y = random.choice(candidates)

    return {
        "guess": {"x": x, "y": y},
        "events": [
            "Inspecting image dimensions",
            "Generating candidate target locations",
            "Selecting a visual heuristic guess",
        ],
    }


def solve_puzzle(task_data=None):
    """
    Baseline only. A reliable puzzle solver needs the original image,
    correct tile-source mapping, and a consistent shuffle representation.
    """
    order = [0, 1, 2, 3]
    random.shuffle(order)

    return {
        "guess": order,
        "events": [
            "Inspecting puzzle metadata",
            "Generating a candidate tile order",
            "Puzzle solver used a baseline guess",
        ],
    }


def solve_challenge(challenge_type, image_data, task_text="", task_data=None):
    started = time.perf_counter()

    if challenge_type == "text":
        result = solve_text(image_data)
    elif challenge_type == "image":
        result = solve_image(image_data, task_text)
    elif challenge_type == "puzzle":
        result = solve_puzzle(task_data)
    else:
        raise ValueError("Unsupported challenge type")

    result["elapsed_ms"] = round(
        (time.perf_counter() - started) * 1000, 2
    )
    result["solver"] = "local_ocr_vision_baseline"
    return result