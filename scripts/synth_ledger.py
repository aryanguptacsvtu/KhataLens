#!/usr/bin/env python3
"""Generate synthetic handwritten-style khata pages + labels for VLM fine-tuning.

    python scripts/synth_ledger.py --n 2000 --out data/synth --seed 1

Outputs
  <out>/images/page_00001.jpg ...
  <out>/labels.jsonl      one {"image", "entries"} per page (ground truth)
  <out>/train_llamafactory.jsonl   chat-format records for LLaMA-Factory (mllm / sharegpt-style)

Note: synthetic pages teach the model the *layout and vocabulary* (Devanagari
digits, "जमा", item names, ruled paper). They do NOT replace real handwriting,
so always evaluate on real/own handwritten pages and keep them out of training.
"""
from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from khatalens.extract import PROMPT  # noqa: E402  (same prompt at train and inference time)

FONT_FILES = ["Kalam-Light.ttf", "Kalam-Regular.ttf", "Kalam-Bold.ttf"]

FIRST = ["रमेश", "सुरेश", "महेश", "दिनेश", "राजू", "मोहन", "गीता", "सीता", "सुनीता", "कमला",
         "अनिल", "विजय", "संजय", "प्रकाश", "लक्ष्मी", "राधा", "मनोज", "अशोक", "पूजा", "किरण",
         "सचिन", "अमित", "प्रियंका", "शारदा", "भगवान", "रामू", "हरि", "नंदू", "बबलू", "शंकर"]
LAST = ["वर्मा", "शर्मा", "यादव", "पटेल", "ठाकुर", "साहू", "चौधरी", "जैन", "गुप्ता", "कुशवाहा",
        "पाटील", "जाधव", "शिंदे", "देशमुख", "कांबळे", "पवार", "मिश्रा", "तिवारी", "सिंह", ""]
ITEMS_HI = ["चावल", "शक्कर", "चाय पत्ती", "आटा", "तेल", "साबुन", "दाल", "नमक", "बिस्किट",
            "दूध", "मसाला", "सरसों तेल", "मैगी", "जीरा", "हल्दी", "बीड़ी", "गुड़", "सोयाबीन तेल"]
ITEMS_MR = ["तांदूळ", "साखर", "चहा", "पीठ", "तेल", "साबण", "डाळ", "मीठ", "बिस्कीट", "दूध",
            "मसाला", "शेंगदाणे", "रवा", "पोहे"]
PAYMENT_WORD = ["जमा", "जमा", "जमा किया", "नगद जमा"]

DEV_DIGITS = str.maketrans("0123456789", "०१२३४५६७८९")


def rnd_amount(rng: random.Random) -> int:
    kind = rng.random()
    if kind < 0.5:
        return rng.choice([10, 15, 20, 25, 30, 40, 50, 60, 70, 80, 90, 100, 120, 150, 180, 200, 250])
    if kind < 0.9:
        return rng.randrange(100, 1000, 5)
    return rng.randrange(1000, 5000, 10)


def make_page_entries(rng: random.Random, year: int) -> list[dict]:
    names = []
    for _ in range(rng.randint(3, 6)):
        n = f"{rng.choice(FIRST)} {rng.choice(LAST)}".strip()
        names.append(n)
    marathi = rng.random() < 0.3
    items_pool = ITEMS_MR if marathi else ITEMS_HI
    n_rows = rng.randint(8, 14)
    day = rng.randint(1, 20)
    month = rng.randint(1, 12)
    rows = []
    for _ in range(n_rows):
        day = min(day + rng.choice([0, 0, 1, 1, 2]), 28)
        name = rng.choice(names)
        is_payment = rng.random() < 0.22
        if is_payment:
            rows.append({"date": f"{day:02d}/{month:02d}", "customer": name, "items": "",
                         "amount": rnd_amount(rng), "type": "payment", "_word": rng.choice(PAYMENT_WORD)})
        else:
            its = ", ".join(rng.sample(items_pool, rng.choice([1, 1, 2, 2, 3])))
            rows.append({"date": f"{day:02d}/{month:02d}", "customer": name, "items": its,
                         "amount": rnd_amount(rng), "type": "udhaar"})
    return rows


def paper_background(rng: random.Random, w: int, h: int, line_gap: int) -> Image.Image:
    base = np.array([rng.randint(232, 250), rng.randint(228, 246), rng.randint(205, 232)], dtype=np.float32)
    img = np.ones((h, w, 3), dtype=np.float32) * base
    img += np.random.default_rng(rng.randint(0, 10**6)).normal(0, 3, (h, w, 1))  # paper grain
    im = Image.fromarray(np.clip(img, 0, 255).astype(np.uint8))
    d = ImageDraw.Draw(im)
    top = 150
    for y in range(top, h - 60, line_gap):  # ruled lines
        d.line([(0, y), (w, y)], fill=(150, 170, 205), width=2)
    mx = rng.randint(95, 130)
    d.line([(mx, 0), (mx, h)], fill=(200, 90, 90), width=3)  # red margin
    d.line([(mx + 7, 0), (mx + 7, h)], fill=(200, 90, 90), width=1)
    return im


def draw_token(page: Image.Image, text: str, xy: tuple[int, int], font: ImageFont.FreeTypeFont,
               ink: tuple[int, int, int], rng: random.Random) -> None:
    """Draw text on its own layer with a small random rotation (baseline wobble) and paste."""
    if not text:
        return
    bbox = font.getbbox(text)
    pad = 20
    layer = Image.new("RGBA", (bbox[2] + 2 * pad, bbox[3] + 2 * pad + 20), (0, 0, 0, 0))
    ImageDraw.Draw(layer).text((pad, pad), text, font=font, fill=ink + (rng.randint(200, 255),))
    layer = layer.rotate(rng.uniform(-1.8, 1.8), resample=Image.BICUBIC, expand=True)
    page.paste(layer, (xy[0] - pad, xy[1] - pad), layer)


def render_page(rows: list[dict], rng: random.Random) -> Image.Image:
    w, h = 1000, 1400
    line_gap = rng.randint(70, 86)
    page = paper_background(rng, w, h, line_gap)
    font_path = ROOT / "data" / "fonts" / rng.choice(FONT_FILES)
    size = rng.randint(34, 44)
    font = ImageFont.truetype(str(font_path), size)
    ink = rng.choice([(25, 35, 120), (20, 20, 20), (35, 60, 150), (15, 15, 80)])
    use_dev = rng.random() < 0.35
    x0 = rng.randint(140, 165)

    # Items column is ~270px wide. Drop trailing items until the text fits, mutating the
    # row so the ground-truth label always matches what is actually drawn on the page.
    for r in rows:
        while r["type"] == "udhaar" and font.getlength(r["items"]) > 270 and ", " in r["items"]:
            r["items"] = r["items"].rsplit(", ", 1)[0]

    for i, r in enumerate(rows):
        y = 150 + i * line_gap + rng.randint(4, 12)  # sits just above the rule
        if y > h - 100:
            del rows[i:]  # rows that don't fit on the page must not appear in the label
            break
        jitter = lambda: rng.randint(-6, 6)
        draw_token(page, r["date"], (x0 + jitter(), y), font, ink, rng)
        draw_token(page, r["customer"], (x0 + 150 + jitter(), y), font, ink, rng)
        middle = r["_word"] if r["type"] == "payment" else r["items"]
        draw_token(page, middle, (x0 + 420 + jitter(), y), font, ink, rng)
        amt = str(r["amount"])
        amt = amt.translate(DEV_DIGITS) if use_dev else amt
        draw_token(page, amt + rng.choice(["/-", "/-", "", " रु"]), (x0 + 700 + jitter(), y), font, ink, rng)
    return page


def augment(img: Image.Image, rng: random.Random) -> Image.Image:
    # Phone-camera effects: tilt, shadow gradient, blur, noise, JPEG.
    img = img.rotate(rng.uniform(-3, 3), resample=Image.BICUBIC, expand=True, fillcolor=(40, 40, 40))
    arr = np.array(img).astype(np.float32)
    h, w = arr.shape[:2]
    gx = np.linspace(rng.uniform(0.75, 1.0), rng.uniform(0.9, 1.05), w)[None, :, None]
    gy = np.linspace(rng.uniform(0.85, 1.0), rng.uniform(0.9, 1.05), h)[:, None, None]
    arr = arr * gx * gy
    arr += np.random.default_rng(rng.randint(0, 10**6)).normal(0, rng.uniform(2, 8), arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))
    if rng.random() < 0.6:
        img = img.filter(ImageFilter.GaussianBlur(rng.uniform(0.3, 1.2)))
    scale = rng.uniform(0.6, 1.0)  # simulate lower-res phone captures
    img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)
    return img


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--out", default="data/synth")
    ap.add_argument("--seed", type=int, default=1)
    ap.add_argument("--year", type=int, default=2026)
    args = ap.parse_args()

    out = Path(args.out)
    (out / "images").mkdir(parents=True, exist_ok=True)
    rng = random.Random(args.seed)

    with open(out / "labels.jsonl", "w", encoding="utf-8") as lab, \
         open(out / "train_llamafactory.jsonl", "w", encoding="utf-8") as train:
        for i in range(1, args.n + 1):
            rows = make_page_entries(rng, args.year)
            img = augment(render_page(rows, rng), rng)
            rel = f"images/page_{i:05d}.jpg"
            img.save(out / rel, quality=rng.randint(55, 90))
            entries = [{k: v for k, v in r.items() if not k.startswith("_")} for r in rows]
            lab.write(json.dumps({"image": rel, "entries": entries}, ensure_ascii=False) + "\n")
            target = json.dumps({"entries": entries}, ensure_ascii=False)
            train.write(json.dumps({
                "messages": [{"role": "user", "content": "<image>" + PROMPT},
                             {"role": "assistant", "content": target}],
                "images": [str((out / rel).resolve())],
            }, ensure_ascii=False) + "\n")
    print(f"Wrote {args.n} pages to {out}")


if __name__ == "__main__":
    main()
