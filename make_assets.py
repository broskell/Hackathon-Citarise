"""Generate the raw PHOTO artifact for Scenario C: a damaged shipping label with
a scrawled courier note. The agent OCRs this with extract_from_document (Gemini
vision) to recover the shipment id before it can act.

Run once:  python make_assets.py
Produces:  assets/label_SHP-3003.png
"""

import os
import random

from PIL import Image, ImageDraw, ImageFont

OUT_DIR = "assets"
OUT_PATH = os.path.join(OUT_DIR, "label_SHP-3003.png")


def _font(size, bold=False):
    for name in (("arialbd.ttf", "arial.ttf") if not bold else ("arialbd.ttf",)):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def main():
    os.makedirs(OUT_DIR, exist_ok=True)
    random.seed(3003)  # deterministic "damage"

    W, H = 760, 520
    img = Image.new("RGB", (W, H), (238, 236, 230))
    d = ImageDraw.Draw(img)

    # Label card
    card = (40, 40, W - 40, H - 40)
    d.rectangle(card, fill=(252, 252, 250), outline=(20, 20, 20), width=4)

    d.text((70, 60), "PRIORITY SHIPPING LABEL", font=_font(30, bold=True), fill=(15, 15, 15))
    d.line((70, 104, W - 70, 104), fill=(15, 15, 15), width=2)

    # Fake barcode
    x = 70
    while x < W - 90:
        w = random.choice([2, 2, 3, 5])
        if random.random() > 0.35:
            d.rectangle((x, 130, x + w, 210), fill=(10, 10, 10))
        x += w + random.choice([2, 3])

    d.text((70, 225), "TRACKING #:  SHP-3003", font=_font(28, bold=True), fill=(10, 10, 10))
    d.text((70, 265), "ORDER:  ORD-3003", font=_font(24), fill=(30, 30, 30))
    d.text((70, 300), "CARRIER:  SwiftCargo Freight", font=_font(22), fill=(30, 30, 30))
    d.text((70, 332), "TO:  500 S Grand Ave, Los Angeles, CA 90071", font=_font(20), fill=(40, 40, 40))

    # Handwritten-style courier exception note (red, slight angle via a sub-image)
    note = Image.new("RGBA", (560, 120), (0, 0, 0, 0))
    nd = ImageDraw.Draw(note)
    nd.text((6, 4), "SwiftCargo system DOWN -", font=_font(30, bold=True), fill=(190, 25, 25, 255))
    nd.text((6, 44), "cannot scan / cannot deliver.", font=_font(30, bold=True), fill=(190, 25, 25, 255))
    nd.text((6, 84), "returning to LAX hub  - drv #4471", font=_font(24), fill=(190, 25, 25, 255))
    note = note.rotate(-7, expand=True, resample=Image.BICUBIC)
    img.paste(note, (150, 360), note)

    # "Damage": a torn corner + a scuff line
    d.polygon([(W - 40, H - 40), (W - 40, H - 130), (W - 130, H - 40)], fill=(238, 236, 230))
    d.line((60, 470, 360, 430), fill=(120, 120, 120), width=3)

    # Slight rotation so it reads as a photo, not a clean render.
    img = img.rotate(1.5, expand=True, fillcolor=(210, 208, 202), resample=Image.BICUBIC)
    img.save(OUT_PATH)
    print(f"wrote {OUT_PATH} ({img.size[0]}x{img.size[1]})")


def make_favicons():
    """Generate PNG + ICO favicons (the LODESTAR four-point star) into web/.
    Rendered at 4x then downscaled for clean anti-aliasing."""
    import math

    web = "web"
    os.makedirs(web, exist_ok=True)
    TILE = (20, 18, 16, 255)      # dark tile
    STAR = (224, 85, 43, 255)     # brand orange

    def draw(size):
        ss = 4
        s = size * ss
        img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([0, 0, s - 1, s - 1], radius=int(s * 0.22), fill=TILE)
        c, R, r = s / 2, s * 0.44, s * 0.44 * 0.36
        pts = []
        for i in range(8):
            ang = math.radians(i * 45 - 90)
            rad = R if i % 2 == 0 else r
            pts.append((c + rad * math.cos(ang), c + rad * math.sin(ang)))
        d.polygon(pts, fill=STAR)
        dot = s * 0.045
        d.ellipse([c - dot, c - dot, c + dot, c + dot], fill=TILE)
        return img.resize((size, size), Image.LANCZOS)

    draw(32).save(os.path.join(web, "favicon-32.png"))
    draw(180).save(os.path.join(web, "apple-touch-icon.png"))
    draw(64).save(os.path.join(web, "favicon.ico"), sizes=[(16, 16), (32, 32), (48, 48)])
    print("wrote web/favicon-32.png, apple-touch-icon.png, favicon.ico")


if __name__ == "__main__":
    main()
    make_favicons()
