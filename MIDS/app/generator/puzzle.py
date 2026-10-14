"""
Image puzzle CAPTCHA generator.

Creates a 2x2 image puzzle with shuffled tiles.

ANSWER CONTRACT (important):
  The issued image is a grid of tiles. Each tile carries a "tile number"
  which is its ORIGINAL position in the source image (0..n-1, reading order).

  The user's job is to rearrange the tiles so that every tile number is back
  at its correct position:
      position p must contain tile number p.

  The correct submission is therefore [0, 1, 2, ..., n-1].

  Server stores:
      correct_order = [0, 1, 2, ..., n-1]
      shuffled_order = the order in which tiles were shuffled for display
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

    for _ in range(12):
        draw.line(
            (
                rng.randint(0, SIZE[0]),
                rng.randint(0, SIZE[1]),
                rng.randint(0, SIZE[0]),
                rng.randint(0, SIZE[1]),
            ),
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
            (x - r, y - r, x + r, y + r),
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
            tiles.append(img.crop(box))
    return tiles


def _make_preview(tiles):
    tile_w = SIZE[0] // GRID_SIZE
    tile_h = SIZE[1] // GRID_SIZE

    img = Image.new("RGB", SIZE, "white")
    for index, tile in enumerate(tiles):
        row = index // GRID_SIZE
        col = index % GRID_SIZE
        img.paste(tile, (col * tile_w, row * tile_h))
    return img


def generate(params=None, seed=None):
    params = params or PuzzleParams()
    rng = random.Random(seed)

    source = _make_source_image(rng)
    tiles = _split_image(source)
    n = len(tiles)

    # Each tile keeps its ORIGINAL index 0..n-1.
    # We shuffle positions for display.
    identity = list(range(n))
    shuffled_order = identity.copy()
    while shuffled_order == identity:
        rng.shuffle(shuffled_order)

    # Displayed tile at position p is tiles[shuffled_order[p]].
    # We keep track of the original tile number at each displayed position.
    displayed_tiles = [tiles[i] for i in shuffled_order]
    preview = _make_preview(displayed_tiles)

    # The correct final arrangement is: position p shows tile number p.
    correct_order = identity  # [0, 1, 2, ..., n-1]

    answer = {
        "correct_order": correct_order,
        "shuffled_order": shuffled_order,  # what was displayed where
        "rows": GRID_SIZE,
        "cols": GRID_SIZE,
    }

    return answer, preview


def to_png_bytes(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()