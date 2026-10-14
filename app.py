"""KhataLens - photograph a handwritten udhaar page, review it, save it, send reminders.

Run:  streamlit run app.py
Everything runs on this machine. No external fonts, scripts or APIs are loaded.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pandas as pd
import streamlit as st
from PIL import Image

from khatalens import db
from khatalens.extract import DEFAULT_MODEL, ExtractionError, extract_entries
from khatalens.reminders import TEMPLATES, reminder_text, whatsapp_link
from khatalens.schema import Entry, validate_entries

DB_PATH = Path("khatalens.db")
SAMPLE_DIR = Path("data/synth")

def _stretch() -> dict:
    """Full-width kwarg that works on both old and new Streamlit.
    Newer versions (>=1.50) use width="stretch"; older ones use use_container_width=True."""
    try:
        major, minor = (int(x) for x in st.__version__.split(".")[:2])
    except ValueError:
        major, minor = 1, 0
    return {"width": "stretch"} if (major, minor) >= (1, 50) else {"use_container_width": True}


STRETCH = _stretch()

st.set_page_config(page_title="KhataLens", page_icon="📒", layout="wide")

# Offline-safe styling: system fonts only, so it works with no internet.
st.markdown(
    """
    <style>
    html, body, [class*="css"] { font-family: "Noto Sans Devanagari", "Mangal", "Nirmala UI", system-ui, sans-serif; }
    h1 { letter-spacing: -0.01em; }
    .balance { font-size: 1.6rem; font-weight: 700; color: #b3261e; }
    .muted { color: #5f6368; font-size: 0.9rem; }
    </style>
    """,
    unsafe_allow_html=True,
)

# ---------------------------------------------------------------- sidebar
with st.sidebar:
    st.header("Settings")
    shop = st.text_input("Shop name", "हमारी दुकान")
    model = st.text_input("Vision model (Ollama)", DEFAULT_MODEL,
                          help="Any vision model Ollama can serve, e.g. your fine-tuned one.")
    year = st.number_input("Year of the page", 2000, 2100, 2026)
    lang = st.selectbox("Reminder language", list(TEMPLATES), format_func=lambda k: {
        "hi": "हिन्दी", "mr": "मराठी", "ta": "தமிழ்", "en": "English"}[k])
    st.caption("All processing is local. Your customers' names and amounts never leave this computer.")

st.title("KhataLens")
st.markdown('<p class="muted">Photo of your udhaar notebook in, clean ledger and reminders out.</p>',
            unsafe_allow_html=True)

tab_scan, tab_ledger, tab_remind = st.tabs(["Scan a page", "Who owes me", "Send reminders"])

# ---------------------------------------------------------------- scan
with tab_scan:
    left, right = st.columns([1, 1.25], gap="large")

    with left:
        src = st.radio("Photo source", ["Upload", "Camera"], horizontal=True, label_visibility="collapsed")
        upload = st.file_uploader("Page photo", type=["jpg", "jpeg", "png"]) if src == "Upload" \
            else st.camera_input("Take a photo of the page")

        # Demo path: load a synthetic page with its known entries, so the review/save/remind
        # flow can be tried without any model running.
        if (SAMPLE_DIR / "labels.jsonl").exists() and st.button("Try a sample page (no model needed)"):
            recs = [json.loads(l) for l in open(SAMPLE_DIR / "labels.jsonl", encoding="utf-8") if l.strip()]
            rec = random.choice(recs)
            entries = validate_entries({"entries": rec["entries"]}, default_year=int(year))
            st.session_state["sample_image"] = str(SAMPLE_DIR / rec["image"])
            st.session_state["df"] = pd.DataFrame([e.to_dict() for e in entries])
            st.session_state["raw"] = ""

        image = Image.open(upload) if upload is not None else (
            Image.open(st.session_state["sample_image"]) if "sample_image" in st.session_state else None)
        if image is not None:
            st.image(image, caption="Your page", **STRETCH)

    with right:
        if upload is not None and st.button("Read this page", type="primary"):
            with st.spinner("Reading handwriting on this computer..."):
                try:
                    entries, raw = extract_entries(image, model=model, default_year=int(year))
                    st.session_state["df"] = pd.DataFrame([e.to_dict() for e in entries])
                    st.session_state["raw"] = raw
                    st.session_state.pop("sample_image", None)
                except ExtractionError as exc:
                    st.error(str(exc))

        df = st.session_state.get("df")
        if df is not None:
            if df.empty:
                st.warning("No entries found. Try a sharper, well-lit photo of one page.")
            else:
                df = df.copy()
                df["check"] = df["flags"].apply(lambda f: ", ".join(f))
                n_flag = int((df["check"] != "").sum())
                if n_flag:
                    st.warning(f"{n_flag} line(s) need a look - see the check column.")
                else:
                    st.success("Every line looks consistent. Still glance at names and amounts.")

                edited = st.data_editor(
                    df[["date", "customer", "items", "amount", "type", "check"]],
                    num_rows="dynamic", **STRETCH, hide_index=True,
                    column_config={
                        "type": st.column_config.SelectboxColumn("type", options=["udhaar", "payment"]),
                        "amount": st.column_config.NumberColumn("amount (₹)", min_value=0, format="%.0f"),
                        "check": st.column_config.TextColumn("check", disabled=True),
                    },
                )
                if st.button("Save to ledger", type="primary"):
                    rows = edited.drop(columns=["check"]).fillna("").to_dict("records")
                    clean = validate_entries({"entries": rows}, default_year=int(year))
                    with db.connect(DB_PATH) as conn:
                        n = db.add_entries(conn, clean, source="scan")
                    st.session_state.pop("df", None)
                    st.session_state.pop("sample_image", None)
                    st.success(f"Saved {n} entries.")
                with st.expander("Raw model output (for debugging)"):
                    st.code(st.session_state.get("raw", "") or "(sample page, no model used)", language="json")

# ---------------------------------------------------------------- ledger
with tab_ledger:
    view = st.radio("Show", ["Pending", "Settled", "Needs a check"], horizontal=True,
                    label_visibility="collapsed")
    status = {"Pending": "pending", "Settled": "settled", "Needs a check": "check"}[view]

    days = None
    if status == "pending":
        age = st.radio("Last entry was", ["Any time", "30+ days ago", "60+ days ago", "90+ days ago"],
                       horizontal=True, help="Find customers you have not heard from for a while.")
        days = {"Any time": None, "30+ days ago": 30, "60+ days ago": 60, "90+ days ago": 90}[age]

    with db.connect(DB_PATH) as conn:
        bal = db.balances(conn, overdue_days=days, status=status)

    if not bal:
        st.info({
            "pending": (f"Nobody has been pending for {days}+ days." if days
                        else "Nothing pending yet. Scan a page and save it to see balances here."),
            "settled": "No settled customers yet. They appear here once their balance is exactly zero.",
            "check": "Nothing to check. Every customer's payments match their recorded udhaar.",
        }[status])
    else:
        if status == "pending":
            st.markdown(f'Total pending: <span class="balance">₹{sum(b["balance"] for b in bal):,.0f}</span>',
                        unsafe_allow_html=True)
            table = pd.DataFrame(bal)[["name", "balance", "days_since", "last_activity",
                                       "total_udhaar", "total_paid"]]
        elif status == "settled":
            table = pd.DataFrame(bal)[["name", "total_udhaar", "total_paid", "last_activity"]]
        else:
            st.warning("These customers have paid more than the ledger shows they owe. Common causes: "
                       "an older notebook page that is not scanned yet, the same person written with "
                       "two different names, or a line the model misread.")
            table = pd.DataFrame(bal)
            table["overpaid_by"] = -table["balance"]
            table = table[["name", "overpaid_by", "total_udhaar", "total_paid", "last_activity"]]
        st.dataframe(table.rename(columns={"days_since": "days since last entry"}),
                     **STRETCH, hide_index=True)

        pick = st.selectbox("Customer", [b["name"] for b in bal])
        row = next(b for b in bal if b["name"] == pick)
        with db.connect(DB_PATH) as conn:
            st.dataframe(pd.DataFrame(db.customer_history(conn, pick)), **STRETCH, hide_index=True)

        if status == "pending":
            owed = float(row["balance"])
            amt = st.number_input("Payment received (₹)", min_value=1.0, value=owed, step=10.0,
                                  key=f"pay_{pick}_{int(owed)}")
            full = amt >= owed
            if st.button("Mark as fully paid" if full else "Record partial payment", type="primary"):
                with db.connect(DB_PATH) as conn:
                    db.record_payment(conn, pick, amt)
                st.toast(f"{pick}: " + ("settled" if full else f"₹{owed - amt:,.0f} still pending"))
                st.rerun()

        st.download_button("Download as CSV", table.to_csv(index=False).encode("utf-8-sig"),
                           f"khata_{status}.csv", "text/csv")

# ---------------------------------------------------------------- reminders
with tab_remind:
    with db.connect(DB_PATH) as conn:
        bal = db.balances(conn)
    if not bal:
        st.info("No pending balances.")
    for b in bal:
        c1, c2, c3 = st.columns([2, 2, 2])
        c1.markdown(f"**{b['name']}**  \n₹{b['balance']:,.0f}")
        phone = c2.text_input("Phone", b["phone"] or "", key=f"ph_{b['name']}", label_visibility="collapsed",
                              placeholder="10-digit mobile")
        if phone != (b["phone"] or ""):
            with db.connect(DB_PATH) as conn:
                db.set_phone(conn, b["name"], phone)
        link = whatsapp_link(phone, reminder_text(b["name"], b["balance"], shop, lang)) if phone else None
        if link:
            c3.link_button("Open in WhatsApp", link)
        else:
            c3.caption("Add a phone number")