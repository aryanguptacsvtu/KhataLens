"""Light-touch image cleanup before the vision model sees the page.

Deliberately conservative: VLMs are trained on natural-looking photos, so heavy
binarisation can hurt more than it helps. We only fix size, lighting and tilt.
"""
from __future__ import annotations

import io

import cv2
import numpy as np
from PIL import Image

MAX_SIDE = 1600  # keeps VLM token cost (and RAM) predictable on a shop laptop


def _estimate_skew_degrees(gray: np.ndarray) -> float:
    """Estimate page tilt from ruled lines / text baselines with a Hough transform."""
    edges = cv2.Canny(gray, 50, 150)
    lines = cv2.HoughLinesP(edges, 1, np.pi / 720, threshold=120,
                            minLineLength=gray.shape[1] // 3, maxLineGap=20)
    if lines is None:
        return 0.0
    angles = []
    for x1, y1, x2, y2 in lines[:, 0]:
        a = np.degrees(np.arctan2(y2 - y1, x2 - x1))
        if abs(a) < 15:  # only near-horizontal lines
            angles.append(a)
    return float(np.median(angles)) if angles else 0.0


def preprocess(image: Image.Image, deskew: bool = True) -> Image.Image:
    img = image.convert("RGB")
    if max(img.size) > MAX_SIDE:
        scale = MAX_SIDE / max(img.size)
        img = img.resize((int(img.width * scale), int(img.height * scale)), Image.LANCZOS)

    arr = np.array(img)

    # Even out uneven lighting (phone shadows) with CLAHE on the L channel.
    lab = cv2.cvtColor(arr, cv2.COLOR_RGB2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8)).apply(l)
    arr = cv2.cvtColor(cv2.merge((l, a, b)), cv2.COLOR_LAB2RGB)

    if deskew:
        angle = _estimate_skew_degrees(cv2.cvtColor(arr, cv2.COLOR_RGB2GRAY))
        if 0.3 < abs(angle) < 10:
            h, w = arr.shape[:2]
            M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
            arr = cv2.warpAffine(arr, M, (w, h), flags=cv2.INTER_CUBIC,
                                 borderMode=cv2.BORDER_REPLICATE)
    return Image.fromarray(arr)


def to_png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return buf.getvalue()
