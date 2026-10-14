"""
Image CAPTCHA generator.

Generates a simple visual object-selection challenge.
The user must click the requested shape.

Fixes vs. the old version:
  * Exactly ONE object has the target type (distractors never share it),
    so "click the circle" has a single correct answer.
  * Objects never overlap, so the target can never be hidden by another shape.
  * Clutter lines are drawn BEFORE the shapes, so they cannot cut the target.
"""

import math
import random
from dataclasses import asdict, dataclass
from io import BytesIO

from PIL import Image, ImageDraw

SIZE = (440, 260)

OBJECTS = {
    "circle": "circle",
    "square": "square",
    "triangle": "triangle",
    "star": "star",
}


@dataclass(frozen=True)
class ImageParams:
    difficulty: int = 0
    object_count: int = 5

    def __post_init__(self):
        object.__setattr__(
            self,
            "difficulty",
            max(0, min(10, int(self.difficulty))),
        )
        object.__setattr__(
            self,
            "object_count",
            max(3, min(10, int(self.object_count))),
        )

    def as_dict(self):
        return asdict(self)


def _random_color(rng):
    # Channels <= 190 keep every shape clearly different from the white
    # background and from the light-grey clutter lines.
    return (
        rng.randint(30, 190),
        rng.randint(30, 190),
        rng.randint(30, 190),
    )


def _draw_star(draw, cx, cy, radius, fill):
    points = []
    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5
        r = radius if i % 2 == 0 else radius * 0.45
        points.append((cx + r * math.cos(angle), cy + r * math.sin(angle)))
    draw.polygon(points, fill=fill)


def _draw_object(draw, object_type, box, fill):
    x1, y1, x2, y2 = box

    if object_type == "circle":
        draw.ellipse(box, fill=fill)

    elif object_type == "square":
        draw.rectangle(box, fill=fill)

    elif object_type == "triangle":
        cx = (x1 + x2) / 2
        draw.polygon([(cx, y1), (x2, y2), (x1, y2)], fill=fill)

    elif object_type == "star":
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        radius = min((x2 - x1) / 2, (y2 - y1) / 2)
        _draw_star(draw, cx, cy, radius, fill)


def _overlaps(a, b, margin=8):
    return not (
        a[2] + margin <= b[0]
        or b[2] + margin <= a[0]
        or a[3] + margin <= b[1]
        or b[3] + margin <= a[1]
    )


def _place_boxes(rng, count, padding=25):
    boxes = []
    for _ in range(count):
        for _attempt in range(300):
            size = rng.randint(35, 60)
            x = rng.randint(padding, SIZE[0] - padding - size)
            y = rng.randint(padding, SIZE[1] - padding - size)
            box = (x, y, x + size, y + size)
            if all(not _overlaps(box, other) for other in boxes):
                boxes.append(box)
                break
        else:
            break  # no room left; use what we have
    return boxes


def generate(params=None, seed=None):
    params = params or ImageParams()
    rng = random.Random(seed)

    img = Image.new("RGB", SIZE, "white")
    draw = ImageDraw.Draw(img)

    names = list(OBJECTS.keys())
    selected_target = rng.choice(names)
    distractor_types = [n for n in names if n != selected_target]

    boxes = _place_boxes(rng, params.object_count)
    target_index = rng.randrange(len(boxes))

    objects = []
    for i, box in enumerate(boxes):
        obj_type = selected_target if i == target_index else rng.choice(distractor_types)
        objects.append({"type": obj_type, "box": box})

    # Clutter first (under the shapes), light grey so it stays "background".
    for _ in range(params.difficulty * 2):
        draw.line(
            (
                rng.randint(0, SIZE[0]),
                rng.randint(0, SIZE[1]),
                rng.randint(0, SIZE[0]),
                rng.randint(0, SIZE[1]),
            ),
            fill=(220, 220, 220),
            width=1,
        )

    for obj in objects:
        _draw_object(draw, obj["type"], obj["box"], _random_color(rng))

    answer = {
        "target": selected_target,
        "target_box": objects[target_index]["box"],
    }
    return answer, img


def to_png_bytes(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
