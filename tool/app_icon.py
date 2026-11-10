"""Draws the Dey Alert launcher icon and writes every Android size.

A lime location pin with an alert mark, sending out signal waves, on the app's near-black.
Run from the repository root:  python tool/app_icon.py   (needs Pillow)
"""

import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter

LIME = (166, 239, 31)
INK = (10, 12, 10)
BACKGROUND_TOP = (24, 30, 22)
CANVAS = 1024
SUPERSAMPLE = 4

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "android" / "app" / "src" / "main" / "res"
DENSITIES = {"mdpi": 1, "hdpi": 1.5, "xhdpi": 2, "xxhdpi": 3, "xxxhdpi": 4}


def mark(size: int) -> Image.Image:
    """The pin and waves on a transparent square, filling most of it."""
    s = size * SUPERSAMPLE
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    u = s / 1000

    cx, cy, r = 500 * u, 400 * u, 190 * u
    tip = (cx, 860 * u)
    # The pin's sides are the tangents from its tip to the head.
    d = tip[1] - cy
    angle = math.asin(r / d)
    left = (cx - r * math.cos(angle), cy + r * math.sin(angle))
    right = (cx + r * math.cos(angle), cy + r * math.sin(angle))
    draw.ellipse((cx - r, cy - r, cx + r, cy + r), fill=LIME)
    draw.polygon([left, right, tip], fill=LIME)

    # Alert mark cut out of the head.
    bar_w = 58 * u
    draw.rounded_rectangle(
        (cx - bar_w / 2, cy - 120 * u, cx + bar_w / 2, cy + 40 * u), radius=bar_w / 2, fill=INK
    )
    dot = 34 * u
    draw.ellipse((cx - dot, cy + 72 * u, cx + dot, cy + 72 * u + 2 * dot), fill=INK)

    # Signal waves either side of the head, the outer one fainter.
    for radius, alpha in ((280 * u, 255), (375 * u, 130)):
        width = 44 * u
        colour = (*LIME, alpha)
        layer = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        wave = ImageDraw.Draw(layer)
        box = (cx - radius, cy - radius, cx + radius, cy + radius)
        for start, end in ((-38, 38), (142, 218)):
            wave.arc(box, start, end, fill=colour, width=int(width))
            for a in (start, end):
                # Round caps at both ends of each wave.
                mid = radius - width / 2
                x = cx + mid * math.cos(math.radians(a))
                y = cy + mid * math.sin(math.radians(a))
                wave.ellipse((x - width / 2, y - width / 2, x + width / 2, y + width / 2), fill=colour)
        image = Image.alpha_composite(image, layer)
    return image.resize((size, size), Image.LANCZOS)


def fit(art: Image.Image, size: int, share: float) -> Image.Image:
    """Centres the drawn part of art so its longest side is share of size."""
    box = art.getbbox()
    cropped = art.crop(box)
    scale = share * size / max(cropped.size)
    cropped = cropped.resize(
        (round(cropped.width * scale), round(cropped.height * scale)), Image.LANCZOS
    )
    out = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    out.alpha_composite(cropped, ((size - cropped.width) // 2, (size - cropped.height) // 2))
    return out


def background(size: int) -> Image.Image:
    """Near-black with a faint lime glow behind the pin."""
    base = Image.new("RGBA", (size, size), (*INK, 255))
    glow = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse(
        (size * 0.2, size * 0.15, size * 0.8, size * 0.75), fill=(*LIME, 46)
    )
    glow = glow.filter(ImageFilter.GaussianBlur(size * 0.12))
    top = Image.new("RGBA", (size, size), (*BACKGROUND_TOP, 255))
    fade = Image.linear_gradient("L").resize((size, size)).point(lambda v: 255 - v)
    base.paste(top, mask=fade.point(lambda v: v // 2))
    return Image.alpha_composite(base, glow)


def full_icon(size: int, art: Image.Image) -> Image.Image:
    return Image.alpha_composite(background(size), fit(art, size, 0.66))


def rounded(icon: Image.Image) -> Image.Image:
    mask = Image.new("L", icon.size, 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, icon.width - 1, icon.height - 1), radius=icon.width * 0.22, fill=255
    )
    out = Image.new("RGBA", icon.size, (0, 0, 0, 0))
    out.paste(icon, mask=mask)
    return out


def main() -> None:
    art = mark(CANVAS)
    master = full_icon(CANVAS, art)
    (ROOT / "design").mkdir(exist_ok=True)
    master.resize((512, 512), Image.LANCZOS).convert("RGB").save(ROOT / "design" / "app-icon-512.png")
    rounded(master).save(ROOT / "design" / "app-icon-preview.png")

    # Adaptive icons keep the mark inside the 66dp safe circle of a 108dp layer.
    foreground = fit(art, CANVAS, 0.5)
    back = background(CANVAS)
    for name, factor in DENSITIES.items():
        folder = RES / f"mipmap-{name}"
        folder.mkdir(exist_ok=True)
        legacy = round(48 * factor)
        layer = round(108 * factor)
        master.resize((legacy, legacy), Image.LANCZOS).save(folder / "ic_launcher.png")
        foreground.resize((layer, layer), Image.LANCZOS).save(folder / "ic_launcher_foreground.png")
        back.resize((layer, layer), Image.LANCZOS).save(folder / "ic_launcher_background.png")

    adaptive = RES / "mipmap-anydpi-v26"
    adaptive.mkdir(exist_ok=True)
    (adaptive / "ic_launcher.xml").write_text(
        '<?xml version="1.0" encoding="utf-8"?>\n'
        '<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">\n'
        '    <background android:drawable="@mipmap/ic_launcher_background" />\n'
        '    <foreground android:drawable="@mipmap/ic_launcher_foreground" />\n'
        '    <monochrome android:drawable="@mipmap/ic_launcher_foreground" />\n'
        "</adaptive-icon>\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
