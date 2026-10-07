"""Text CAPTCHA generator with three independent difficulty knobs (0-10).

POC had ONE knob (distortion_level) that changed rotation, jitter, noise and
blur together. Here each effect has its own parameter, matching the proposal's
parameter vector [p_distort, p_noise, p_rotate]. That is what lets the
RL policy tune them separately later.
"""
import math
import random
from dataclasses import asdict, dataclass
from io import BytesIO

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ALPHABET = "ACDEFGHJKMNPQRTUVWXYZ234679"  # no look-alikes (0/O, 1/I)
SIZE = (220, 90)


def _clamp(v):
    return max(0, min(10, int(v)))


@dataclass(frozen=True)
class Params:
    distort: int = 0  # wave warp + vertical jitter of characters
    noise: int = 0    # clutter lines, speckle, blur
    rotate: int = 0   # per-character rotation

    def __post_init__(self):
        for name in ("distort", "noise", "rotate"):
            object.__setattr__(self, name, _clamp(getattr(self, name)))

    def as_dict(self):
        return asdict(self)


def random_text(rng, length=5):
    return "".join(rng.choice(ALPHABET) for _ in range(length))


def _draw_chars(text, params, rng):
    font = ImageFont.load_default(size=42)
    img = Image.new("RGB", SIZE, "white")
    x = 15
    for ch in text:
        tile = Image.new("RGBA", (50, 70), (255, 255, 255, 0))
        ImageDraw.Draw(tile).text((10, 5), ch, font=font, fill=(20, 20, 20, 255))
        max_angle = params.rotate * 4
        tile = tile.rotate(rng.uniform(-max_angle, max_angle), expand=True)
        jitter = rng.randint(-params.distort, params.distort)
        img.paste(tile, (x, 10 + jitter), tile)
        x += 34
    return img


def _warp(img, amplitude, rng):
    """Shift every pixel column up/down along a sine wave."""
    if amplitude == 0:
        return img
    arr = np.array(img)
    phase = rng.uniform(0, 2 * math.pi)
    period = rng.uniform(40, 70)
    for xi in range(arr.shape[1]):
        shift = round(amplitude * math.sin(2 * math.pi * xi / period + phase))
        arr[:, xi] = np.roll(arr[:, xi], shift, axis=0)
    return Image.fromarray(arr)


def _add_noise(img, level, rng):
    if level == 0:
        return img
    w, h = img.size
    draw = ImageDraw.Draw(img)
    for _ in range(level):
        line = (rng.randint(0, w), rng.randint(0, h), rng.randint(0, w), rng.randint(0, h))
        draw.line(line, fill=(rng.randint(0, 150),) * 3, width=1)
    arr = np.array(img)
    gen = np.random.default_rng(rng.randint(0, 2**32 - 1))
    n = level * 300
    arr[gen.integers(0, h, n), gen.integers(0, w, n)] = gen.integers(0, 256, (n, 1))
    return Image.fromarray(arr).filter(ImageFilter.GaussianBlur(level * 0.1))


def generate(params=None, seed=None, length=5):
    """Return (answer_text, PIL image). Same seed + params -> same image."""
    params = params or Params()
    rng = random.Random(seed)
    text = random_text(rng, length)
    img = _draw_chars(text, params, rng)
    img = _warp(img, params.distort * 0.8, rng)
    img = _add_noise(img, params.noise, rng)
    return text, img


def to_png_bytes(img):
    buf = BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()
