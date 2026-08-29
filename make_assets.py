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


if __name__ == "__main__":
    main()
