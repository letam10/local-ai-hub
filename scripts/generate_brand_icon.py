"""Generate the deterministic Local AI Hub ICO from the canonical LA mark."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "distribution" / "assets" / "local-ai-hub.ico"
SIZES = (16, 24, 32, 48, 64, 128, 256)


def _render(size: int) -> Image.Image:
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    pixels = image.load()
    radius = max(2, int(size * 0.19))
    for y in range(size):
        for x in range(size):
            u = x / max(1, size - 1)
            v = y / max(1, size - 1)
            r = int(128 * (1 - u) + 77 * u)
            g = int(170 * (1 - u) + 125 * u)
            b = int(255 * (1 - u) + 255 * u)
            pixels[x, y] = (r, g, b, 255)
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=radius, fill=255)
    image.putalpha(mask)
    # Use fixed vector strokes instead of a host font so every build produces
    # the same bytes on machines with different installed fonts.
    draw = ImageDraw.Draw(image)
    stroke = max(1, int(size * 0.12))
    left = int(size * 0.25)
    top = int(size * 0.24)
    bottom = int(size * 0.76)
    mid = int(size * 0.58)
    draw.line((left, top, left, bottom), fill="#ffffff", width=stroke)
    draw.line((left, bottom, mid, bottom), fill="#ffffff", width=stroke)
    a_left = int(size * 0.56)
    a_right = int(size * 0.83)
    a_top = int(size * 0.24)
    draw.line((a_left, bottom, (a_left + a_right) // 2, a_top, a_right, bottom), fill="#ffffff", width=stroke, joint="curve")
    cross_y = int(size * 0.57)
    draw.line((a_left + stroke // 2, cross_y, a_right - stroke // 2, cross_y), fill="#ffffff", width=stroke)
    return image


def main() -> None:
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    images = [_render(size) for size in SIZES]
    images[-1].save(OUTPUT, format="ICO", sizes=[(size, size) for size in SIZES])
    print(OUTPUT)


if __name__ == "__main__":
    main()
