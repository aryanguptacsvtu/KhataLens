"""Page image -> structured ledger entries, using a local open-weight VLM served by Ollama.

Nothing here talks to the internet: the default host is localhost.
Swap models with the KHATALENS_MODEL env var (e.g. your fine-tuned LoRA merged
and imported into Ollama) - that's the whole point of building on open weights.
"""
from __future__ import annotations

import base64
import json
import os
import urllib.request
from typing import Optional

from PIL import Image

from .preprocess import preprocess, to_png_bytes
from .schema import LEDGER_JSON_SCHEMA, Entry, validate_entries

OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
DEFAULT_MODEL = os.environ.get("KHATALENS_MODEL", "gemma3:4b")

PROMPT = """You are reading one page from a shopkeeper's handwritten credit ledger (khata).
The writing may be Hindi, Marathi, Tamil or English, with Devanagari or ASCII digits.

Extract every ledger line as an entry:
- customer: the person's name exactly as written (keep the original script)
- date: as written on the line or page heading (DD/MM or DD/MM/YYYY), else empty
- items: goods bought, as written, else empty
- amount: the number in rupees (convert Devanagari digits to ASCII)
- type: "udhaar" if the customer took goods on credit (उधार / नाम / बाकी),
        "payment" if the customer paid money back (जमा / जमा किया)

Rules: do not invent lines. Skip crossed-out lines. If a value is unreadable, leave it empty.
Return only JSON matching the schema."""


class ExtractionError(RuntimeError):
    pass


def _chat(payload: dict, timeout: int = 600) -> dict:
    req = urllib.request.Request(
        f"{OLLAMA_HOST}/api/chat",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read())
    except OSError as exc:  # connection refused, timeout, etc.
        raise ExtractionError(
            f"Could not reach Ollama at {OLLAMA_HOST}. Is it running (`ollama serve`) "
            f"and is the model pulled (`ollama pull {DEFAULT_MODEL}`)?"
        ) from exc


def extract_entries(
    image: Image.Image,
    model: Optional[str] = None,
    default_year: Optional[int] = None,
    do_preprocess: bool = True,
) -> tuple[list[Entry], str]:
    """Return (validated entries, raw model text). Raw text is kept for debugging/eval."""
    img = preprocess(image) if do_preprocess else image
    b64 = base64.b64encode(to_png_bytes(img)).decode()
    payload = {
        "model": model or DEFAULT_MODEL,
        "stream": False,
        "format": LEDGER_JSON_SCHEMA,  # structured output: model can only emit this shape
        "options": {"temperature": 0},
        "messages": [{"role": "user", "content": PROMPT, "images": [b64]}],
    }
    data = _chat(payload)
    raw = data.get("message", {}).get("content", "")
    return validate_entries(raw, default_year=default_year), raw
