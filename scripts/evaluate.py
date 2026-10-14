#!/usr/bin/env python3
"""Field-level accuracy on a labelled set of ledger pages.

    # open model through Ollama (base or your fine-tuned one)
    python scripts/evaluate.py --labels data/test/labels.jsonl --backend ollama --model qwen2.5vl:7b

    # closed-API baseline for the comparison table (needs ANTHROPIC_API_KEY)
    python scripts/evaluate.py --labels data/test/labels.jsonl --backend anthropic --model <model-name>

    # pipeline self-test with no model at all
    python scripts/evaluate.py --labels data/synth/labels.jsonl --backend oracle

labels.jsonl: one {"image": "relative/path.jpg", "entries": [{date, customer, items, amount, type}]}
per line, paths relative to the labels file. Use the same format for your own handwritten pages.

Matching: rows are compared position by position (ledger order is stable). Missing or extra
rows count as wrong for every field, so dropping lines is penalised, not hidden.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from khatalens.extract import PROMPT, extract_entries  # noqa: E402
from khatalens.preprocess import preprocess  # noqa: E402
from khatalens.schema import (Entry, normalize_name, parse_amount, parse_date,  # noqa: E402
                              validate_entries)

FIELDS = ("customer", "amount", "date", "type")


def anthropic_backend(image: Image.Image, model: str, year: int):
    """Closed-API baseline. Same prompt, same preprocessing, same validation."""
    buf = io.BytesIO()
    preprocess(image).save(buf, format="PNG")
    body = {
        "model": model, "max_tokens": 4000,
        "messages": [{"role": "user", "content": [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                         "data": base64.b64encode(buf.getvalue()).decode()}},
            {"type": "text", "text": PROMPT + '\nRespond with JSON only: {"entries": [...]}'},
        ]}],
    }
    req = urllib.request.Request(
        "https://api.anthropic.com/v1/messages", data=json.dumps(body).encode(),
        headers={"content-type": "application/json", "anthropic-version": "2023-06-01",
                 "x-api-key": os.environ["ANTHROPIC_API_KEY"]})
    with urllib.request.urlopen(req, timeout=300) as r:
        text = "".join(b.get("text", "") for b in json.loads(r.read())["content"])
    text = text.strip().removeprefix("```json").removesuffix("```").strip()
    return validate_entries(text, default_year=year), text


def oracle_backend(truth_rows: list[dict], year: int):
    """Returns the ground truth. Exists only to test the scoring code."""
    return validate_entries({"entries": truth_rows}, default_year=year), ""


def score_page(pred: list[Entry], truth: list[Entry]) -> dict[str, tuple[int, int]]:
    """Return {field: (correct, total)} aligned by row position."""
    n = max(len(pred), len(truth))
    res = {f: [0, n] for f in FIELDS}
    for i in range(min(len(pred), len(truth))):
        p, t = pred[i], truth[i]
        res["customer"][0] += normalize_name(p.customer) == normalize_name(t.customer)
        res["amount"][0] += parse_amount(p.amount) == parse_amount(t.amount)
        res["date"][0] += p.date == t.date
        res["type"][0] += p.type == t.type
    return {k: tuple(v) for k, v in res.items()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--labels", required=True)
    ap.add_argument("--backend", choices=["ollama", "anthropic", "oracle"], default="ollama")
    ap.add_argument("--model", default=None)
    ap.add_argument("--year", type=int, default=2026)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--save", default=None, help="write per-page predictions to this jsonl")
    args = ap.parse_args()

    labels_path = Path(args.labels)
    records = [json.loads(l) for l in open(labels_path, encoding="utf-8") if l.strip()]
    if args.limit:
        records = records[: args.limit]

    totals = {f: [0, 0] for f in FIELDS}
    page_exact = 0
    t0 = time.time()
    saved = []
    for rec in records:
        truth = validate_entries({"entries": rec["entries"]}, default_year=args.year)
        if args.backend == "oracle":
            pred, raw = oracle_backend(rec["entries"], args.year)
        else:
            img = Image.open(labels_path.parent / rec["image"])
            if args.backend == "ollama":
                pred, raw = extract_entries(img, model=args.model, default_year=args.year)
            else:
                pred, raw = anthropic_backend(img, args.model, args.year)
        s = score_page(pred, truth)
        for f, (c, t) in s.items():
            totals[f][0] += c
            totals[f][1] += t
        page_exact += all(c == t for c, t in s.values())
        saved.append({"image": rec["image"], "raw": raw, "scores": s})

    secs = time.time() - t0
    print(f"\nBackend: {args.backend}  Model: {args.model or '(default)'}  Pages: {len(records)}")
    print("-" * 44)
    for f in FIELDS:
        c, t = totals[f]
        print(f"{f:<10} {c:>5}/{t:<5} {100 * c / max(t, 1):6.1f}%")
    print(f"{'whole page':<10} {page_exact:>5}/{len(records):<5} {100 * page_exact / max(len(records), 1):6.1f}%")
    print(f"time/page  {secs / max(len(records), 1):6.1f}s")
    if args.save:
        with open(args.save, "w", encoding="utf-8") as f:
            for r in saved:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    main()
