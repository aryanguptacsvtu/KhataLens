"""KhataLens web server: serves the HTML/CSS/JS UI in ./static and a small JSON API
on top of the existing khatalens package (db, extract, schema, reminders).

Run:  python server.py            (opens http://127.0.0.1:8000)
      python server.py --port 9000 --no-browser

Everything runs on this machine. The server listens on 127.0.0.1 only, uses just the
Python standard library plus what khatalens already needs, and loads nothing from
the internet. No backend module is modified by this file.
"""
from __future__ import annotations

import argparse
import io
import json
import math
import mimetypes
import os
import random
import sys
import threading
import traceback
import webbrowser
from datetime import date
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

BASE = Path(__file__).resolve().parent
os.chdir(BASE)  # khatalens.db and data/ resolve exactly as they do when running app.py
sys.path.insert(0, str(BASE))

from PIL import Image, UnidentifiedImageError  # noqa: E402

from khatalens import db  # noqa: E402
from khatalens.extract import DEFAULT_MODEL, ExtractionError, extract_entries  # noqa: E402
from khatalens.reminders import TEMPLATES, reminder_text, whatsapp_link  # noqa: E402
from khatalens.schema import validate_entries  # noqa: E402

DB_PATH = Path("khatalens.db")
SAMPLE_DIR = Path("data/synth")
STATIC_DIR = BASE / "static"
DEFAULT_SHOP = "हमारी दुकान"
MAX_IMAGE_BYTES = 30 * 1024 * 1024
MAX_JSON_BYTES = 2 * 1024 * 1024
STATUSES = ("pending", "settled", "check")

CONTENT_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
    ".json": "application/json; charset=utf-8",
    ".svg": "image/svg+xml",
}


class HTTPError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status, self.msg = status, msg


# ------------------------------------------------------------------ helpers
def _year(value) -> int:
    try:
        y = int(value)
        if 2000 <= y <= 2100:
            return y
    except (TypeError, ValueError):
        pass
    return date.today().year


def _lang(value) -> str:
    return value if value in TEMPLATES else "hi"


def _name(value) -> str:
    name = " ".join(str(value or "").split())
    if not name:
        raise HTTPError(400, "Customer name is required.")
    return name


def _customer_exists(conn, name: str) -> bool:
    return bool(db.customer_history(conn, name))


# ------------------------------------------------------------------ API routes
def api_config(h, q):
    return {
        "default_model": DEFAULT_MODEL,
        "year": date.today().year,
        "langs": list(TEMPLATES),
        "default_shop": DEFAULT_SHOP,
        "sample": (SAMPLE_DIR / "labels.jsonl").exists(),
    }


def api_sample(h, q):
    labels = SAMPLE_DIR / "labels.jsonl"
    if not labels.exists():
        raise HTTPError(404, "No sample pages are installed.")
    with open(labels, encoding="utf-8") as fh:
        recs = [json.loads(line) for line in fh if line.strip()]
    rec = random.choice(recs)
    entries = validate_entries({"entries": rec["entries"]}, default_year=_year(q.get("year")))
    return {"entries": [e.to_dict() for e in entries], "image": "/sample/" + rec["image"]}


def api_extract(h, q):
    body = h.read_body(MAX_IMAGE_BYTES)
    try:
        image = Image.open(io.BytesIO(body))
        image.load()
    except (UnidentifiedImageError, OSError):
        raise HTTPError(400, "That file is not a readable JPG or PNG image.")
    entries, raw = extract_entries(image, model=(q.get("model") or "").strip() or DEFAULT_MODEL,
                                   default_year=_year(q.get("year")))
    return {"entries": [e.to_dict() for e in entries], "raw": raw}


def api_save_entries(h, q):
    data = h.read_json()
    rows = data.get("entries")
    if not isinstance(rows, list):
        raise HTTPError(400, "'entries' must be a list.")
    clean = validate_entries({"entries": rows}, default_year=_year(data.get("year")))
    with db.connect(DB_PATH) as conn:
        n = db.add_entries(conn, clean, source="scan")
    return {"saved": n}


def api_balances(h, q):
    status = q.get("status", "pending")
    if status not in STATUSES:
        raise HTTPError(400, "Unknown status.")
    days = None
    if status == "pending" and q.get("days"):
        try:
            days = int(q["days"])
        except ValueError:
            raise HTTPError(400, "'days' must be a whole number.")
    with db.connect(DB_PATH) as conn:
        return db.balances(conn, overdue_days=days, status=status)


def api_history(h, q):
    name = _name(q.get("name"))
    with db.connect(DB_PATH) as conn:
        return db.customer_history(conn, name)


def api_entry_history(h, q):
    """History with entry ids - used by the 'Needs a check' view."""
    name = _name(q.get("name"))
    with db.connect(DB_PATH) as conn:
        return db.customer_entries(conn, name)


def api_entry_delete(h, q):
    """Delete one entry. Allowed only for customers currently in 'Needs a check'
    (paid more than the ledger says they owe), so other views can never be affected."""
    data = h.read_json()
    name = _name(data.get("name"))
    try:
        entry_id = int(data.get("id"))
    except (TypeError, ValueError):
        raise HTTPError(400, "A valid entry id is required.")
    with db.connect(DB_PATH) as conn:
        flagged = {b["name"] for b in db.balances(conn, status="check")}
        if name not in flagged:
            raise HTTPError(403, "Entries can only be deleted for customers listed under Needs a check.")
        if not db.delete_entry(conn, entry_id, name):
            raise HTTPError(404, "That entry no longer exists.")
    return {"ok": True}


def api_payment(h, q):
    data = h.read_json()
    name = _name(data.get("name"))
    try:
        amount = float(data.get("amount"))
    except (TypeError, ValueError):
        raise HTTPError(400, "Amount must be a number.")
    if not math.isfinite(amount) or amount <= 0:
        raise HTTPError(400, "Amount must be greater than zero.")
    with db.connect(DB_PATH) as conn:
        if not _customer_exists(conn, name):
            raise HTTPError(404, "Unknown customer.")
        db.record_payment(conn, name, amount)
    return {"ok": True}


def api_reminders(h, q):
    shop = (q.get("shop") or "").strip() or DEFAULT_SHOP
    lang = _lang(q.get("lang"))
    with db.connect(DB_PATH) as conn:
        bal = db.balances(conn)
    out = []
    for b in bal:
        msg = reminder_text(b["name"], b["balance"], shop, lang)
        phone = b["phone"] or ""
        out.append({"name": b["name"], "balance": b["balance"], "phone": phone, "message": msg,
                    "link": whatsapp_link(phone, msg) if phone else None})
    return out


def api_phone(h, q):
    data = h.read_json()
    name = _name(data.get("name"))
    phone = str(data.get("phone") or "").strip()[:20]
    shop = str(data.get("shop") or "").strip() or DEFAULT_SHOP
    lang = _lang(data.get("lang"))
    with db.connect(DB_PATH) as conn:
        if not _customer_exists(conn, name):
            raise HTTPError(404, "Unknown customer.")
        db.set_phone(conn, name, phone)
        mine = next((b for b in db.balances(conn) if b["name"] == name), None)
    link = None
    if mine and phone:
        link = whatsapp_link(phone, reminder_text(name, mine["balance"], shop, lang))
    return {"link": link}


ROUTES = {
    ("GET", "/api/config"): api_config,
    ("GET", "/api/sample"): api_sample,
    ("POST", "/api/extract"): api_extract,
    ("POST", "/api/entries"): api_save_entries,
    ("GET", "/api/balances"): api_balances,
    ("GET", "/api/history"): api_history,
    ("GET", "/api/entry-history"): api_entry_history,
    ("POST", "/api/entry-delete"): api_entry_delete,
    ("POST", "/api/payment"): api_payment,
    ("GET", "/api/reminders"): api_reminders,
    ("POST", "/api/phone"): api_phone,
}


# ------------------------------------------------------------------ HTTP handler
class Handler(BaseHTTPRequestHandler):
    server_version = "KhataLens"

    # -- request body
    def read_body(self, limit: int) -> bytes:
        try:
            n = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            n = 0
        if n <= 0:
            raise HTTPError(400, "Empty request body.")
        if n > limit:
            raise HTTPError(413, "That file is too large.")
        return self.rfile.read(n)

    def read_json(self) -> dict:
        try:
            data = json.loads(self.read_body(MAX_JSON_BYTES))
        except ValueError:
            raise HTTPError(400, "Invalid JSON.")
        if not isinstance(data, dict):
            raise HTTPError(400, "Expected a JSON object.")
        return data

    # -- responses
    def send_bytes(self, status: int, body: bytes, ctype: str) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, obj, status: int = 200) -> None:
        self.send_bytes(status, json.dumps(obj, ensure_ascii=False).encode("utf-8"),
                        "application/json; charset=utf-8")

    def send_static(self, path: str) -> None:
        if path == "/":
            root, rel = STATIC_DIR, "index.html"
        elif path.startswith("/static/"):
            root, rel = STATIC_DIR, unquote(path[len("/static/"):])
        else:  # /sample/...
            root, rel = SAMPLE_DIR, unquote(path[len("/sample/"):])
        root = root.resolve()
        target = (root / rel).resolve()
        if not target.is_file() or root not in target.parents:
            raise HTTPError(404, "Not found.")
        ctype = CONTENT_TYPES.get(target.suffix.lower()) or mimetypes.guess_type(target.name)[0] \
            or "application/octet-stream"
        self.send_bytes(200, target.read_bytes(), ctype)

    # -- routing
    def do_GET(self):
        self.dispatch("GET")

    def do_POST(self):
        self.dispatch("POST")

    def dispatch(self, method: str) -> None:
        url = urlparse(self.path)
        query = {k: v[0] for k, v in parse_qs(url.query).items()}
        try:
            if method == "GET" and (url.path == "/" or url.path.startswith(("/static/", "/sample/"))):
                return self.send_static(url.path)
            route = ROUTES.get((method, url.path))
            if route is None:
                raise HTTPError(404, "Not found.")
            self.send_json(route(self, query))
        except HTTPError as exc:
            self.send_json({"error": exc.msg}, exc.status)
        except ExtractionError as exc:
            self.send_json({"error": str(exc)}, 502)
        except (BrokenPipeError, ConnectionResetError):
            pass  # browser went away mid-request
        except Exception as exc:  # noqa: BLE001 - never leak a traceback page to the UI
            traceback.print_exc()
            self.send_json({"error": f"Server error: {exc}"}, 500)


def main() -> None:
    ap = argparse.ArgumentParser(description="KhataLens local web app")
    ap.add_argument("--host", default="127.0.0.1", help="use 0.0.0.0 only if you trust your network")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()

    try:
        httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as exc:
        sys.exit(f"Could not start on {args.host}:{args.port} ({exc}). Try --port 8001.")
    httpd.daemon_threads = True

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '') else args.host}:{args.port}/"
    print(f"KhataLens is running at {url}   (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()