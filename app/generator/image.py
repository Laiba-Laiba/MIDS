"""
Image CAPTCHA generator.

Generates a simple visual object-selection challenge.
The user must click the requested object.
"""

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
    return (
        rng.randint(30, 220),
        rng.randint(30, 220),
        rng.randint(30, 220),
    )


def _draw_star(draw, cx, cy, radius, fill):
    import math

    points = []

    for i in range(10):
        angle = -math.pi / 2 + i * math.pi / 5

        r = radius if i % 2 == 0 else radius * 0.45

        x = cx + r * math.cos(angle)
        y = cy + r * math.sin(angle)

        points.append((x, y))

    draw.polygon(points, fill=fill)


def _draw_object(draw, object_type, box, fill):
    x1, y1, x2, y2 = box

    if object_type == "circle":
        draw.ellipse(box, fill=fill)

    elif object_type == "square":
        draw.rectangle(box, fill=fill)

    elif object_type == "triangle":
        cx = (x1 + x2) / 2

        draw.polygon(
            [
                (cx, y1),
                (x2, y2),
                (x1, y2),
            ],
            fill=fill,
        )

    elif object_type == "star":
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        radius = min(
            (x2 - x1) / 2,
            (y2 - y1) / 2,
        )

        _draw_star(
            draw,
            cx,
            cy,
            radius,
            fill,
        )


def generate(params=None, seed=None):
    params = params or ImageParams()

    rng = random.Random(seed)

    img = Image.new(
        "RGB",
        SIZE,
        "white",
    )

    draw = ImageDraw.Draw(img)

    object_names = list(OBJECTS.keys())

    selected_target = rng.choice(object_names)

    objects = []

    padding = 25

    for index in range(params.object_count):
        object_type = rng.choice(object_names)

        width = rng.randint(35, 60)
        height = width

        x = rng.randint(
            padding,
            SIZE[0] - padding - width,
        )

        y = rng.randint(
            padding,
            SIZE[1] - padding - height,
        )

        box = (
            x,
            y,
            x + width,
            y + height,
        )

        objects.append(
            {
                "type": object_type,
                "box": box,
            }
        )

    # Guarantee target exists.
    target = rng.choice(objects)

    target["type"] = selected_target

    for obj in objects:
        _draw_object(
            draw,
            obj["type"],
            obj["box"],
            _random_color(rng),
        )

    # Slight difficulty-dependent visual clutter.
    if params.difficulty > 0:
        for _ in range(params.difficulty * 2):
            x1 = rng.randint(
                0,
                SIZE[0],
            )

            y1 = rng.randint(
                0,
                SIZE[1],
            )

            x2 = rng.randint(
                0,
                SIZE[0],
            )

            y2 = rng.randint(
                0,
                SIZE[1],
            )

            draw.line(
                (x1, y1, x2, y2),
                fill=(220, 220, 220),
                width=1,
            )

    target_box = target["box"]

    answer = {
        "target": selected_target,
        "target_box": target_box,
    }

    return answer, img


def to_png_bytes(img):
    buf = BytesIO()

    img.save(
        buf,
        format="PNG",
    )

    return buf.getvalue()
