# app/attacker_solver.py

import base64
import io
import re
import time

from PIL import Image

_OCR_READER = None
_YOLO_MODEL = None

# Shapes produced by app/generator/image.py. YOLO (COCO) cannot see these,
# so they are solved with classical computer vision (OpenCV contours).
SHAPE_NAMES = ("circle", "square", "triangle", "star")

ALIASES = {
    "bike": "bicycle",
    "motorbike": "motorcycle",
    "sofa": "couch",
    "television": "tv",
    "aeroplane": "airplane",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_yolo_model():
    global _YOLO_MODEL
    if _YOLO_MODEL is None:
        from ultralytics import YOLO

        _YOLO_MODEL = YOLO("yolov8n.pt")
    return _YOLO_MODEL


def _get_ocr_reader():
    global _OCR_READER
    if _OCR_READER is None:
        import easyocr

        _OCR_READER = easyocr.Reader(["en"], gpu=False)
    return _OCR_READER


def _decode_image(image_data):
    """Accept a base64 data URL or raw base64 image."""
    if not image_data:
        raise ValueError("Missing challenge image")
    if image_data.startswith("data:") and "," in image_data:
        image_data = image_data.split(",", 1)[1]
    raw = base64.b64decode(image_data)
    return Image.open(io.BytesIO(raw)).convert("RGB")


# ---------------------------------------------------------------------------
# TEXT
# ---------------------------------------------------------------------------


def solve_text(image_data):
    """Return an OCR guess; this does not guarantee a correct answer."""
    import numpy as np

    image = _decode_image(image_data)
    reader = _get_ocr_reader()
    results = reader.readtext(np.array(image), detail=0, paragraph=False)

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


# ---------------------------------------------------------------------------
# IMAGE
# ---------------------------------------------------------------------------


def _extract_target(task_text):
    """Turn 'Click the circle in the image' into 'circle'."""
    text = str(task_text or "").strip().lower()

    # 1) Known shape word anywhere in the sentence.
    for name in SHAPE_NAMES:
        if re.search(rf"\b{name}s?\b", text):
            return name

    # 2) Otherwise strip the leading verb and use what is left (YOLO labels).
    text = re.sub(
        r"^(?:click|select|find|tap)\s+(?:on\s+)?(?:the\s+)?(?:an?\s+)?",
        "",
        text,
    ).strip(" .!?")
    return ALIASES.get(text, text)


def _classify_shape(contour, cv2):
    area = cv2.contourArea(contour)
    _, _, w, h = cv2.boundingRect(contour)
    if w == 0 or h == 0:
        return None

    hull_area = cv2.contourArea(cv2.convexHull(contour)) or 1.0
    solidity = area / hull_area  # star ~0.55, everything else ~1.0
    fill = area / float(w * h)   # square ~1.0, circle ~0.78, triangle ~0.5

    if solidity < 0.85:
        return "star"
    if fill > 0.90:
        return "square"
    if fill > 0.68:
        return "circle"
    return "triangle"


def _solve_shape(image, target):
    import cv2
    import numpy as np

    arr = np.asarray(image, dtype=np.uint8)

    # Foreground = anything clearly darker/more coloured than the white
    # background. Light-grey clutter lines (220) fall below this threshold.
    mask = (arr.min(axis=2) < 205).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((3, 3), np.uint8))

    contours, _ = cv2.findContours(mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)

    found = []
    matches = []
    for contour in contours:
        area = cv2.contourArea(contour)
        if area < 200:  # ignore specks
            continue
        label = _classify_shape(contour, cv2)
        if label is None:
            continue
        x, y, w, h = cv2.boundingRect(contour)
        found.append(label)
        if label == target:
            matches.append({"x": x + w / 2.0, "y": y + h / 2.0, "area": area})

    seen_text = ", ".join(sorted(found)) if found else "nothing"
    events = [
        f"Target requested: {target}",
        f"Segmented {len(found)} object(s): {seen_text}",
    ]

    if not matches:
        events.append("No matching shape detected")
        return {"guess": None, "events": events}

    best = max(matches, key=lambda m: m["area"])
    events.append(f"Found {len(matches)} matching shape(s); returned the largest center")
    return {
        "guess": {"x": round(best["x"]), "y": round(best["y"])},
        "events": events,
    }


def _solve_yolo(image, target):
    import numpy as np

    model = _get_yolo_model()
    results = model.predict(source=np.array(image), conf=0.15, verbose=False)

    matches = []
    seen = set()
    for result in results:
        for box in result.boxes:
            label = str(model.names[int(box.cls[0].item())]).lower()
            seen.add(label)
            if label != target:
                continue
            x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
            matches.append(
                {
                    "x": round((x1 + x2) / 2),
                    "y": round((y1 + y2) / 2),
                    "confidence": float(box.conf[0].item()),
                }
            )

    seen_text = ", ".join(sorted(seen)) if seen else "nothing"
    if not matches:
        return {
            "guess": None,
            "events": [
                f"Target requested: {target}",
                f"Detector saw: {seen_text}",
                "No matching object detected",
            ],
        }

    best = max(matches, key=lambda m: m["confidence"])
    return {
        "guess": {"x": best["x"], "y": best["y"]},
        "events": [
            f"Target requested: {target}",
            f"Detected {len(matches)} matching object(s)",
            "Returned the highest-confidence object's center",
        ],
    }


def solve_image(image_data, task_text=""):
    """Find the requested object and return its center (natural pixel coords)."""
    image = _decode_image(image_data)
    target = _extract_target(task_text)

    if not target:
        return {"guess": None, "events": ["No target object was provided"]}

    if target in SHAPE_NAMES:
        return _solve_shape(image, target)

    return _solve_yolo(image, target)


# ---------------------------------------------------------------------------
# PUZZLE
# ---------------------------------------------------------------------------


def _edge_cost(ea, eb, tol=2, clip=120.0):
    """
    Distance between two touching 1-pixel edges (shape: length x 3).
    Each pixel may match a neighbour within +-tol pixels, because lines
    drawn without anti-aliasing are jagged where they cross a tile border.
    """
    import numpy as np

    length = len(ea)
    pa = np.pad(ea, ((tol, tol), (0, 0)), mode="edge")
    pb = np.pad(eb, ((tol, tol), (0, 0)), mode="edge")
    best_ab = np.full(length, np.inf)
    best_ba = np.full(length, np.inf)
    for s in range(-tol, tol + 1):
        best_ab = np.minimum(best_ab, np.abs(ea - pb[tol + s:tol + s + length]).sum(axis=1))
        best_ba = np.minimum(best_ba, np.abs(eb - pa[tol + s:tol + s + length]).sum(axis=1))
    return float(np.mean(np.minimum(best_ab, clip) + np.minimum(best_ba, clip)))


def solve_puzzle(task_data=None, image_data=None, outer_weight=1.0):
    """
    Rebuild a rows x cols puzzle from the shuffled image.

    Two signals are combined:
      1. Seam continuity: touching edges of neighbouring tiles must continue
         each other (tolerant to +-2px jitter).
      2. Blank-border prior: flat/blank edges match *anything*, so seam cost
         alone is ambiguous. In these puzzles the outer border of the picture
         is almost empty, so edges that end up on the outside of the rebuilt
         picture should be low-activity. Busy edges are penalised there.

    Returns guess[p] = the ORIGINAL tile number that must sit at grid
    position p.  For a perfectly solved puzzle, this is [0, 1, 2, ..., n-1].
    """
    import numpy as np

    task_data = task_data or {}
    rows = int(task_data.get("rows", 2) or 2)
    cols = int(task_data.get("cols", 2) or 2)
    n = rows * cols

    if not image_data:
        return {
            "guess": list(range(n)),
            "events": ["Puzzle image unavailable; used baseline order"],
        }

    if n > 9:
        return {
            "guess": list(range(n)),
            "events": [f"{rows}x{cols} grid is too large; used baseline order"],
        }

    arr = np.asarray(_decode_image(image_data), dtype=np.float32)
    height, width = arr.shape[:2]
    th, tw = height // rows, width // cols

    tiles = [
        arr[r * th:(r + 1) * th, c * tw:(c + 1) * tw]
        for r in range(rows)
        for c in range(cols)
    ]

    background = np.median(arr.reshape(-1, 3), axis=0)

    def activity(edge):
        return float(np.mean(np.minimum(np.abs(edge - background).sum(axis=1), 120.0)))

    act = [
        {
            "L": activity(t[:, 0]),
            "R": activity(t[:, -1]),
            "T": activity(t[0]),
            "B": activity(t[-1]),
        }
        for t in tiles
    ]

    inf = float("inf")
    LR = [[_edge_cost(tiles[i][:, -1], tiles[j][:, 0]) if i != j else inf for j in range(n)] for i in range(n)]
    TB = [[_edge_cost(tiles[i][-1], tiles[j][0]) if i != j else inf for j in range(n)] for i in range(n)]

    best = {"cost": inf, "order": list(range(n))}

    def place(order, used, cost):
        if cost >= best["cost"]:
            return
        pos = len(order)
        if pos == n:
            best["cost"], best["order"] = cost, order[:]
            return
        r, c = divmod(pos, cols)
        for t in range(n):
            if t in used:
                continue
            add = 0.0
            if c > 0:
                add += LR[order[pos - 1]][t]
            if r > 0:
                add += TB[order[pos - cols]][t]
            if r == 0:
                add += outer_weight * act[t]["T"]
            if r == rows - 1:
                add += outer_weight * act[t]["B"]
            if c == 0:
                add += outer_weight * act[t]["L"]
            if c == cols - 1:
                add += outer_weight * act[t]["R"]
            order.append(t)
            used.add(t)
            place(order, used, cost + add)
            order.pop()
            used.discard(t)

    place([], set(), 0.0)

    # ------------------------------------------------------------------
    # Convert solver's arrangement to the server's answer contract.
    #
    # Solver gives:  best["order"][p] = issued tile number that should
    #                                   sit at grid position p.
    #
    # Server expects: guess[p] = ORIGINAL tile number that must sit at
    #                 position p (so [0, 1, 2, ..., n-1] = fully solved).
    #
    # Mapping:
    #   shuffled_order[t] = ORIGINAL tile number carried by issued tile t.
    #   Therefore:  guess[p] = shuffled_order[ best["order"][p] ]
    # ------------------------------------------------------------------

    shuffled_order = None
    if isinstance(task_data, dict):
        shuffled_order = task_data.get("shuffled_order")

    if (
        isinstance(shuffled_order, list)
        and len(shuffled_order) == n
    ):
        try:
            shuffled_order = [int(v) for v in shuffled_order]
            guess = [
                shuffled_order[best["order"][p]]
                for p in range(n)
            ]
        except (TypeError, ValueError, IndexError):
            guess = list(best["order"])
    else:
        guess = list(best["order"])

    return {
        "guess": guess,
        "events": [
            f"Loaded the puzzle image ({rows}x{cols} grid)",
            "Compared the touching edges of every tile pair",
            "Penalised busy edges on the outer border",
            f"Selected the lowest-cost arrangement ({best['cost']:.2f})",
            "Mapped arrangement to the server's answer contract",
        ],
    }

# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


def solve_challenge(challenge_type, image_data, task_text="", task_data=None):
    started = time.perf_counter()

    if challenge_type == "text":
        result = solve_text(image_data)
    elif challenge_type == "image":
        result = solve_image(image_data, task_text)
    elif challenge_type == "puzzle":
        result = solve_puzzle(task_data=task_data, image_data=image_data)
    else:
        raise ValueError("Unsupported challenge type")

    result["elapsed_ms"] = round((time.perf_counter() - started) * 1000, 2)
    result["solver"] = "local_ocr_vision_baseline"
    return result
