"""
Image puzzle CAPTCHA generator.

Creates a 2x2 image puzzle with shuffled tiles.
"""

import random
from dataclasses import asdict, dataclass
from io import BytesIO

from PIL import Image, ImageDraw

SIZE = (440, 260)

GRID_SIZE = 2


@dataclass(frozen=True)
class PuzzleParams:
    difficulty: int = 0

    def __post_init__(self):
        object.__setattr__(
            self,
            "difficulty",
            max(0, min(10, int(self.difficulty))),
        )

    def as_dict(self):
        return asdict(self)


def _make_source_image(rng):
    img = Image.new(
        "RGB",
        SIZE,
        (
            rng.randint(180, 240),
            rng.randint(180, 240),
            rng.randint(180, 240),
        ),
    )

    draw = ImageDraw.Draw(img)

    # Large visual areas so tile ordering is recognizable.
    for _ in range(12):
        x1 = rng.randint(0, SIZE[0])
        y1 = rng.randint(0, SIZE[1])

        x2 = rng.randint(0, SIZE[0])
        y2 = rng.randint(0, SIZE[1])

        draw.line(
            (x1, y1, x2, y2),
            fill=(
                rng.randint(20, 220),
                rng.randint(20, 220),
                rng.randint(20, 220),
            ),
            width=rng.randint(3, 10),
        )

    for _ in range(8):
        x = rng.randint(20, SIZE[0] - 20)
        y = rng.randint(20, SIZE[1] - 20)

        r = rng.randint(10, 35)

        draw.ellipse(
            (
                x - r,
                y - r,
                x + r,
                y + r,
            ),
            fill=(
                rng.randint(30, 230),
                rng.randint(30, 230),
                rng.randint(30, 230),
            ),
        )

    return img


def _split_image(img):
    width, height = img.size

    tile_w = width // GRID_SIZE
    tile_h = height // GRID_SIZE

    tiles = []

    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            box = (
                col * tile_w,
                row * tile_h,
                (col + 1) * tile_w,
                (row + 1) * tile_h,
            )

            tile = img.crop(box)

            tiles.append(tile)

    return tiles


def _make_preview(tiles):
    tile_w = SIZE[0] // GRID_SIZE
    tile_h = SIZE[1] // GRID_SIZE

    img = Image.new(
        "RGB",
        SIZE,
        "white",
    )

    for index, tile in enumerate(tiles):
        row = index // GRID_SIZE
        col = index % GRID_SIZE

        img.paste(
            tile,
            (
                col * tile_w,
                row * tile_h,
            ),
        )

    return img


def generate(params=None, seed=None):
    params = params or PuzzleParams()

    rng = random.Random(seed)

    source = _make_source_image(rng)

    tiles = _split_image(source)

    correct_order = list(range(len(tiles)))

    shuffled_order = correct_order.copy()

    # Make sure puzzle is actually shuffled.
    while shuffled_order == correct_order:
        rng.shuffle(shuffled_order)

    shuffled_tiles = [tiles[i] for i in shuffled_order]

    preview = _make_preview(shuffled_tiles)

    answer = {
        "correct_order": correct_order,
        "shuffled_order": shuffled_order,
        "rows": GRID_SIZE,
        "cols": GRID_SIZE,
    }

    return answer, preview


def to_png_bytes(img):
    buf = BytesIO()

    img.save(
        buf,
        format="PNG",
    )

    return buf.getvalue()
