/* KhataLens front end. Plain JavaScript, no libraries, no external requests.
   Talks only to the local server (server.py) which wraps the Python backend. */
"use strict";

(() => {
    // ------------------------------------------------------------------ helpers
    const $ = (sel, root = document) => root.querySelector(sel);
    const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

    const LANG_LABELS = { hi: "हिन्दी", mr: "मराठी", ta: "தமிழ்", en: "English" };
    const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
    const rupee = (n) => "₹" + inr.format(Number(n) || 0);

    /** Tiny element builder. Text is always inserted as text, never as HTML,
        so names read from handwriting can never inject markup. */
    function h(tag, attrs, ...kids) {
        const el = document.createElement(tag);
        for (const [k, v] of Object.entries(attrs || {})) {
            if (v == null || v === false) continue;
            if (k === "class") el.className = v;
            else if (k === "value") el.value = v;
            else if (k.startsWith("on")) el.addEventListener(k.slice(2), v);
            else el.setAttribute(k, v === true ? "" : v);
        }
        const add = (kid) => {
            if (kid == null || kid === false) return;
            if (Array.isArray(kid)) kid.forEach(add);
            else el.append(kid instanceof Node ? kid : document.createTextNode(String(kid)));
        };
        kids.forEach(add);
        return el;
    }

    async function api(path, opts) {
        let res;
        try {
            res = await fetch(path, opts);
        } catch (e) {
            throw new Error("Could not reach the KhataLens server. Is it still running?");
        }
        let data = null;
        try { data = await res.json(); } catch (e) { /* non-JSON body */ }
        if (!res.ok) throw new Error((data && data.error) || `Request failed (${res.status})`);
        return data;
    }
    const postJSON = (path, body) => api(path, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body),
    });

    const banner = (kind, text) => h("div", { class: `banner ${kind}` }, text);

    let toastTimer = null;
    function toast(text) {
        const t = $("#toast");
        t.textContent = text;
        t.hidden = false;
        clearTimeout(toastTimer);
        toastTimer = setTimeout(() => { t.hidden = true; }, 3500);
    }

    // ------------------------------------------------------------------ theme + avatar
    const THEME_KEY = "khatalens.theme";
    function applyTheme(name) {
        if (!["clean", "ledger", "midnight"].includes(name)) name = "clean";
        document.documentElement.setAttribute("data-theme", name);
        $$("[data-theme-pick]").forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.themePick === name)));
        try { localStorage.setItem(THEME_KEY, name); } catch (e) { /* not persisted */ }
    }

    /** Round initial badge; hue is derived from the name so each customer keeps one colour. */
    function avatar(name, large) {
        const first = Array.from(String(name || "?").trim())[0] || "?";
        let hue = 0;
        for (const ch of String(name)) hue = (hue * 31 + ch.codePointAt(0)) % 360;
        return h("span", { class: "avatar" + (large ? " lg" : ""), style: `--h:${hue}`, "aria-hidden": "true" }, first.toUpperCase());
    }

    // ------------------------------------------------------------------ state + settings
    const SETTINGS_KEY = "khatalens.settings.v1";
    const settings = { shop: "हमारी दुकान", model: "", year: new Date().getFullYear(), lang: "hi" };
    let config = { langs: ["hi", "mr", "ta", "en"], sample: false, default_model: "", year: settings.year };

    const state = {
        file: null,        // Blob chosen/captured by the user
        previewUrl: null,  // object URL for that blob
        sampleImage: null, // server URL of a sample page
        rows: null,        // review rows (null = nothing to review)
        startedEmpty: false,
    };

    function saveSettings() {
        try {
            localStorage.setItem(SETTINGS_KEY, JSON.stringify({ shop: settings.shop, model: settings.model, lang: settings.lang }));
        } catch (e) { /* storage unavailable: settings just won't persist */ }
    }

    function initSettings() {
        let saved = {};
        try { saved = JSON.parse(localStorage.getItem(SETTINGS_KEY)) || {}; } catch (e) { saved = {}; }
        settings.shop = typeof saved.shop === "string" ? saved.shop : (config.default_shop || settings.shop);
        settings.model = saved.model || config.default_model;
        settings.year = config.year;
        settings.lang = config.langs.includes(saved.lang) ? saved.lang : "hi";

        const langSel = $("#set-lang");
        langSel.replaceChildren(...config.langs.map((k) => h("option", { value: k }, LANG_LABELS[k] || k)));
        $("#set-shop").value = settings.shop;
        $("#set-model").value = settings.model;
        $("#set-year").value = settings.year;
        langSel.value = settings.lang;

        $("#set-shop").addEventListener("input", (e) => { settings.shop = e.target.value; saveSettings(); remindersChanged(); });
        $("#set-model").addEventListener("input", (e) => { settings.model = e.target.value; saveSettings(); });
        $("#set-year").addEventListener("input", (e) => {
            const y = parseInt(e.target.value, 10);
            if (y >= 2000 && y <= 2100) settings.year = y;
        });
        langSel.addEventListener("change", (e) => { settings.lang = e.target.value; saveSettings(); remindersChanged(); });

        applyTheme(document.documentElement.getAttribute("data-theme"));
        $$("[data-theme-pick]").forEach((b) => b.addEventListener("click", () => applyTheme(b.dataset.themePick)));
    }

    function setDrawer(open) {
        $("#settings").hidden = !open;
        $("#scrim").hidden = !open;
        $("#settings-btn").setAttribute("aria-expanded", String(open));
        if (open) $("#set-shop").focus(); else $("#settings-btn").focus();
    }

    // ------------------------------------------------------------------ tabs
    let activeTab = "scan";
    function showTab(name) {
        activeTab = name;
        $$(".tab").forEach((b) => b.setAttribute("aria-selected", String(b.dataset.tab === name)));
        for (const p of ["scan", "ledger", "remind"]) $("#panel-" + p).hidden = p !== name;
        if (name !== "scan") stopCamera();
        else if (source === "camera" && !state.file) startCamera();
        if (name === "ledger") loadLedger();
        if (name === "remind") loadReminders();
    }

    // ------------------------------------------------------------------ scan: source + preview
    let source = "upload";
    let stream = null;
    let camCaptured = false;

    function updatePreview() {
        const img = $("#preview-img");
        const src = state.previewUrl || state.sampleImage;
        $("#preview").hidden = !src;
        if (src) img.src = src;
        $("#read-btn").hidden = !state.file;  // reading needs a real photo, not a sample
    }

    function setFile(blob) {
        if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
        state.file = blob;
        state.previewUrl = URL.createObjectURL(blob);
        state.sampleImage = null;
        setMsg(null);
        updatePreview();
    }

    function setSource(next) {
        source = next;
        $$("#src-seg button").forEach((b) => b.classList.toggle("on", b.dataset.src === next));
        $("#src-upload").hidden = next !== "upload";
        $("#src-camera").hidden = next !== "camera";
        if (next === "camera") { camCaptured = false; $("#cam-shot").textContent = "Take photo"; startCamera(); }
        else stopCamera();
    }

    async function startCamera() {
        stopCamera();
        const msg = $("#cam-msg"), shot = $("#cam-shot"), video = $("#cam");
        if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
            msg.textContent = "This browser cannot open the camera here. Use Upload instead.";
            shot.disabled = true;
            return;
        }
        try {
            stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: "environment" }, audio: false });
            video.srcObject = stream;
            video.hidden = false;
            msg.textContent = "";
            shot.disabled = false;
        } catch (e) {
            msg.textContent = "Camera not available (" + (e.name || "error") + "). Allow camera access, or use Upload instead.";
            shot.disabled = true;
            video.hidden = true;
        }
    }

    function stopCamera() {
        if (stream) stream.getTracks().forEach((t) => t.stop());
        stream = null;
        $("#cam").srcObject = null;
    }

    function takePhoto() {
        if (camCaptured) {            // "Retake"
            camCaptured = false;
            $("#cam-shot").textContent = "Take photo";
            startCamera();
            return;
        }
        const video = $("#cam");
        if (!video.videoWidth) return;
        const canvas = document.createElement("canvas");
        canvas.width = video.videoWidth;
        canvas.height = video.videoHeight;
        canvas.getContext("2d").drawImage(video, 0, 0);
        canvas.toBlob((blob) => {
            if (!blob) return;
            setFile(blob);
            stopCamera();
            $("#cam").hidden = true;
            camCaptured = true;
            $("#cam-shot").textContent = "Retake";
        }, "image/jpeg", 0.92);
    }

    function pickFile(file) {
        if (!file) return;
        if (!/^image\/(jpeg|png)$/.test(file.type)) {
            setMsg("err", "Please choose a JPG or PNG photo.");
            return;
        }
        setFile(file);
    }

    // ------------------------------------------------------------------ scan: messages, read, review, save
    function setMsg(kind, text) {
        const box = $("#msg");
        box.replaceChildren(kind ? banner(kind, text) : "");
    }

    function updateEmpty() {
        $("#review-empty").hidden = Boolean(state.rows) || !$("#busy").hidden;
    }

    function setBusy(on) {
        $("#busy").hidden = !on;
        $("#read-btn").disabled = on;
        updateEmpty();
    }

    async function readPage() {
        if (!state.file) return;
        setMsg(null);
        setBusy(true);
        try {
            const q = new URLSearchParams({ model: settings.model.trim(), year: String(settings.year) });
            const data = await api("/api/extract?" + q, {
                method: "POST",
                headers: { "Content-Type": state.file.type || "image/jpeg" },
                body: state.file,
            });
            loadRows(data.entries, data.raw);
        } catch (e) {
            setMsg("err", e.message);
        } finally {
            setBusy(false);
        }
    }

    async function trySample() {
        setMsg(null);
        try {
            const data = await api("/api/sample?" + new URLSearchParams({ year: String(settings.year) }));
            if (state.previewUrl) URL.revokeObjectURL(state.previewUrl);
            state.file = null;
            state.previewUrl = null;
            state.sampleImage = data.image;
            loadRows(data.entries, "");
            updatePreview();
        } catch (e) {
            setMsg("err", e.message);
        }
    }

    function loadRows(entries, raw) {
        state.rows = entries.map((e) => ({
            date: e.date || "", customer: e.customer || "", items: e.items || "",
            amount: e.amount ?? 0, type: e.type === "payment" ? "payment" : "udhaar",
            flags: e.flags || [],
        }));
        state.startedEmpty = state.rows.length === 0;
        $("#raw").textContent = raw || "(sample page, no model used)";
        renderReview();
    }

    function renderBanner() {
        const box = $("#review-banner");
        if (state.startedEmpty) {
            box.replaceChildren(banner("warn", "No entries found. Try a sharper, well-lit photo of one page."));
            return;
        }
        const nFlag = state.rows.filter((r) => r.flags.length).length;
        if (nFlag) box.replaceChildren(banner("warn", `${nFlag} line(s) need a look - see the check column.`));
        else if (state.rows.length) box.replaceChildren(banner("ok", "Every line looks consistent. Still glance at names and amounts."));
        else box.replaceChildren();
    }

    function renderReview() {
        const review = $("#review");
        updateEmpty();
        if (!state.rows) { review.hidden = true; return; }
        review.hidden = false;
        $("#review-editor").hidden = state.startedEmpty;
        renderBanner();
        renderRows();
    }

    function renderRows() {
        const tbody = $("#review-table tbody");
        tbody.replaceChildren(...state.rows.map((row, i) => {
            const bind = (field) => (e) => { row[field] = e.target.value; };
            return h("tr", { class: row.flags.length ? "flagged" : "" },
                h("td", {}, h("input", { type: "text", value: row.date, "aria-label": "date", placeholder: "YYYY-MM-DD", oninput: bind("date") })),
                h("td", {}, h("input", { type: "text", value: row.customer, "aria-label": "customer", oninput: bind("customer") })),
                h("td", {}, h("input", { type: "text", value: row.items, "aria-label": "items", oninput: bind("items") })),
                h("td", {}, h("input", { type: "number", min: "0", step: "any", value: row.amount, "aria-label": "amount in rupees", oninput: bind("amount") })),
                h("td", {}, (() => {
                    const sel = h("select", { "aria-label": "type", onchange: bind("type") },
                        h("option", { value: "udhaar" }, "udhaar"), h("option", { value: "payment" }, "payment"));
                    sel.value = row.type;
                    return sel;
                })()),
                h("td", { class: "check-cell" }, row.flags.join(", ")),
                h("td", {}, h("button", {
                    type: "button", class: "x-btn", title: "Remove this row", "aria-label": "Remove this row",
                    onclick: () => { state.rows.splice(i, 1); renderBanner(); renderRows(); },
                }, "✕")));
        }));
    }

    function addRow() {
        if (!state.rows) return;
        state.rows.push({ date: "", customer: "", items: "", amount: 0, type: "udhaar", flags: [] });
        renderBanner();
        renderRows();
        const inputs = $$("#review-table tbody tr:last-child input");
        if (inputs[1]) inputs[1].focus();
    }

    async function saveEntries() {
        const btn = $("#save-btn");
        btn.disabled = true;
        try {
            const rows = state.rows.map(({ date, customer, items, amount, type }) => ({ date, customer, items, amount, type }));
            const res = await postJSON("/api/entries", { entries: rows, year: settings.year });
            state.rows = null;
            state.sampleImage = null;
            renderReview();
            updatePreview();
            setMsg("ok", `Saved ${res.saved} entries.`);
            toast(`Saved ${res.saved} entries.`);
        } catch (e) {
            setMsg("err", e.message);
        } finally {
            btn.disabled = false;
        }
    }

    // ------------------------------------------------------------------ ledger
    const ledger = { status: "pending", days: null, selected: null, token: 0, data: [], histToken: 0 };

    const nameCell = (v) => h("span", { class: "who-cell" }, avatar(v), h("span", {}, v));

    const COLUMNS = {
        pending: [
            ["name", "name", nameCell], ["balance", "balance", rupee, true],
            ["days_since", "days since last entry", (v) => (v == null ? "—" : Math.max(0, v)), true],
            ["last_activity", "last activity"], ["total_udhaar", "total udhaar", rupee, true], ["total_paid", "total paid", rupee, true],
        ],
        settled: [
            ["name", "name", nameCell], ["total_udhaar", "total udhaar", rupee, true], ["total_paid", "total paid", rupee, true],
            ["last_activity", "last activity"],
        ],
        check: [
            ["name", "name", nameCell], ["overpaid_by", "overpaid by", rupee, true], ["total_udhaar", "total udhaar", rupee, true],
            ["total_paid", "total paid", rupee, true], ["last_activity", "last activity"],
        ],
    };

    const EMPTY_TEXT = {
        pending: () => (ledger.days ? `Nobody has been pending for ${ledger.days}+ days.`
            : "Nothing pending yet. Scan a page and save it to see balances here."),
        settled: () => "No settled customers yet. They appear here once their balance is exactly zero.",
        check: () => "Nothing to check. Every customer's payments match their recorded udhaar.",
    };

    async function loadLedger() {
        const my = ++ledger.token;
        const body = $("#ledger-body");
        const q = new URLSearchParams({ status: ledger.status });
        if (ledger.status === "pending" && ledger.days) q.set("days", String(ledger.days));
        try {
            const data = await api("/api/balances?" + q);
            if (my !== ledger.token) return;  // a newer request superseded this one
            ledger.data = data.map((b) => ({ ...b, overpaid_by: -b.balance }));
            renderLedger();
        } catch (e) {
            if (my === ledger.token) body.replaceChildren(banner("err", e.message));
        }
    }

    function toCSV(cols, rows) {
        const esc = (v) => {
            const s = v == null ? "" : String(v);
            return /[",\n\r]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
        };
        const lines = [cols.map((c) => esc(c[0])).join(",")];
        rows.forEach((r) => lines.push(cols.map((c) => esc(r[c[0]])).join(",")));
        return "\ufeff" + lines.join("\r\n");  // BOM so Excel reads Devanagari correctly
    }

    function downloadCSV(name, text) {
        const url = URL.createObjectURL(new Blob([text], { type: "text/csv;charset=utf-8" }));
        const a = h("a", { href: url, download: name });
        document.body.append(a);
        a.click();
        a.remove();
        setTimeout(() => URL.revokeObjectURL(url), 1000);
    }

    function renderLedger() {
        const body = $("#ledger-body");
        const status = ledger.status;
        $("#age-wrap").hidden = status !== "pending";
        const rows = ledger.data;

        if (!rows.length) {
            body.replaceChildren(banner("info", EMPTY_TEXT[status]()));
            return;
        }

        const cols = COLUMNS[status];
        const parts = [];

        if (status === "pending") {
            const total = rows.reduce((s, b) => s + b.balance, 0);
            const oldest = rows.reduce((m, r) => (r.days_since != null && (m == null || r.days_since > m.days_since) ? r : m), null);
            const stat = (label, value, sub) => h("div", { class: "stat" },
                h("div", { class: "label" }, label), h("div", { class: "value" }, value), sub ? h("div", { class: "sub" }, sub) : null);
            parts.push(h("div", { class: "stats" },
                stat("Total pending", h("span", { class: "balance" }, rupee(total))),
                stat("Customers owing", String(rows.length)),
                stat("Longest without an entry", oldest ? `${Math.max(0, oldest.days_since)} days` : "—", oldest ? oldest.name : "")));
        } else if (status === "check") {
            parts.push(banner("warn", "These customers have paid more than the ledger shows they owe. Common causes: " +
                "an older notebook page that is not scanned yet, the same person written with " +
                "two different names, or a line the model misread."));
        }

        const trs = rows.map((r) => {
            const tr = h("tr", { class: "click", tabindex: "0", "data-name": r.name },
                cols.map(([key, , fmt, num]) => h("td", { class: num ? "num" : "" }, fmt ? fmt(r[key]) : (r[key] ?? ""))));
            const choose = () => selectCustomer(r.name);
            tr.addEventListener("click", choose);
            tr.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); choose(); } });
            return tr;
        });
        parts.push(h("div", { class: "card flush" },
            h("div", { class: "toolbar" },
                h("h3", {}, "Customers"),
                h("button", {
                    type: "button", class: "btn small",
                    onclick: () => downloadCSV(`khata_${status}.csv`, toCSV(cols, rows)),
                }, "Download as CSV")),
            h("div", { class: "table-wrap" },
                h("table", { class: "grid" },
                    h("thead", {}, h("tr", {}, cols.map(([, label, , num]) => h("th", { class: num ? "num" : "" }, label)))),
                    h("tbody", {}, trs)))));

        parts.push(h("div", { class: "card", id: "detail" }));

        body.replaceChildren(...parts);

        if (!rows.some((r) => r.name === ledger.selected)) ledger.selected = rows[0].name;
        selectCustomer(ledger.selected);
    }

    async function selectCustomer(name) {
        ledger.selected = name;
        $$("#ledger-body tbody tr").forEach((tr) => tr.classList.toggle("sel", tr.dataset.name === name));
        const box = $("#detail");
        if (!box) return;
        const row = ledger.data.find((b) => b.name === name);
        if (!row) return;
        const my = ++ledger.histToken;
        const head = () => h("div", { class: "detail-head" }, avatar(name, true), h("h3", {}, name));
        box.replaceChildren(head(), h("p", { class: "muted" }, "Loading history..."));
        let hist;
        try {
            // "Needs a check" also gets entry ids so single lines can be deleted.
            hist = await api((ledger.status === "check" ? "/api/entry-history?" : "/api/history?") + new URLSearchParams({ name }));
        } catch (e) {
            if (my === ledger.histToken) box.replaceChildren(head(), banner("err", e.message));
            return;
        }
        if (my !== ledger.histToken) return;

        const canDelete = ledger.status === "check";
        const histTable = h("div", { class: "table-wrap" },
            h("table", { class: "grid" },
                h("thead", {}, h("tr", {}, ["date", "items", "amount", "type"].map((c) =>
                    h("th", { class: c === "amount" ? "num" : "" }, c)),
                    canDelete ? h("th", {}, h("span", { class: "sr-only" }, "Delete")) : null)),
                h("tbody", {}, hist.map((e) => h("tr", {},
                    h("td", {}, e.date || ""), h("td", {}, e.items || ""),
                    h("td", { class: "num" }, rupee(e.amount)),
                    h("td", {}, h("span", { class: `chip ${e.type}` }, e.type)),
                    canDelete ? h("td", { class: "num" }, h("button", {
                        type: "button", class: "btn small danger",
                        "aria-label": `Delete this ${e.type} entry of ${rupee(e.amount)}`,
                        onclick: () => deleteEntry(name, e),
                    }, "Delete")) : null)))));

        const parts = [head()];
        if (canDelete) {
            parts.push(banner("info", `${name} has paid ${rupee(row.total_paid)} but only ${rupee(row.total_udhaar)} of udhaar is recorded, ` +
                `so ${rupee(row.overpaid_by)} more was paid than the ledger shows. Look for a missing udhaar line, a duplicate payment, ` +
                `or a misread amount. Delete an entry only if it is wrong.`));
        }
        parts.push(histTable);
        if (ledger.status === "pending") parts.push(payForm(row));
        box.replaceChildren(...parts);
    }

    async function deleteEntry(name, e) {
        const what = `${e.type} of ${rupee(e.amount)}${e.date ? " on " + e.date : ""}${e.items ? " (" + e.items + ")" : ""}`;
        if (!window.confirm(`Delete this entry for ${name}?\n\n${what}\n\nThis cannot be undone.`)) return;
        try {
            await postJSON("/api/entry-delete", { id: e.id, name });
            toast("Entry deleted");
            await loadLedger();
        } catch (err) {
            toast(err.message);
        }
    }

    function payForm(row) {
        const owed = Math.round(row.balance * 100) / 100;
        const input = h("input", { type: "number", min: "1", step: "any", value: String(owed), id: "pay-amt" });
        const btn = h("button", { type: "button", class: "btn primary" }, "Mark as fully paid");
        const refresh = () => {
            const a = parseFloat(input.value);
            btn.textContent = a >= owed ? "Mark as fully paid" : "Record partial payment";
            btn.disabled = !(a >= 1);
        };
        input.addEventListener("input", refresh);
        btn.addEventListener("click", async () => {
            const amt = parseFloat(input.value);
            if (!(amt >= 1)) return;
            btn.disabled = true;
            try {
                await postJSON("/api/payment", { name: row.name, amount: amt });
                toast(`${row.name}: ` + (amt >= owed ? "settled" : `${rupee(owed - amt)} still pending`));
                await loadLedger();
            } catch (e) {
                toast(e.message);
                refresh();
            }
        });
        refresh();
        return h("div", { class: "pay" },
            h("label", { class: "field", for: "pay-amt" }, "Payment received (₹)", input), btn);
    }

    // ------------------------------------------------------------------ reminders
    let remindToken = 0;

    async function loadReminders() {
        const my = ++remindToken;
        const body = $("#remind-body");
        const q = new URLSearchParams({ shop: settings.shop, lang: settings.lang });
        let list;
        try {
            list = await api("/api/reminders?" + q);
        } catch (e) {
            if (my === remindToken) body.replaceChildren(banner("err", e.message));
            return;
        }
        if (my !== remindToken) return;
        if (!list.length) { body.replaceChildren(banner("info", "No pending balances.")); return; }
        body.replaceChildren(h("div", { class: "card flush" },
            h("div", { class: "intro" },
                h("h3", {}, "Pending reminders"),
                h("p", { class: "muted" }, "Add a phone number, then open WhatsApp to review and send the message yourself.")),
            list.map(reminderRow)));
    }

    function remindersChanged() {
        if (activeTab === "remind") loadReminders();
    }

    function reminderRow(b) {
        const action = h("div", { class: "action" });
        const setAction = (link) => {
            action.replaceChildren(link
                ? h("a", { class: "btn wa", href: link, target: "_blank", rel: "noopener noreferrer" }, "Open in WhatsApp")
                : h("span", { class: "muted" }, "Add a phone number"));
        };
        setAction(b.link);

        let last = b.phone || "";
        const phone = h("input", {
            type: "tel", inputmode: "numeric", value: last, placeholder: "10-digit mobile",
            "aria-label": `Phone number for ${b.name}`, autocomplete: "off",
        });
        phone.addEventListener("change", async () => {
            const value = phone.value.trim();
            if (value === last) return;
            try {
                const res = await postJSON("/api/phone", { name: b.name, phone: value, shop: settings.shop, lang: settings.lang });
                last = value;
                setAction(res.link);
            } catch (e) {
                toast(e.message);
            }
        });

        return h("div", { class: "rem" },
            h("div", { class: "who" }, avatar(b.name, true),
                h("div", {}, h("strong", {}, b.name), h("span", { class: "amt" }, rupee(b.balance)))),
            phone, action);
    }

    // ------------------------------------------------------------------ wire up
    function wire() {
        $$(".tab").forEach((b) => b.addEventListener("click", () => showTab(b.dataset.tab)));
        $("#settings-btn").addEventListener("click", () => setDrawer($("#settings").hidden));
        $("#settings-close").addEventListener("click", () => setDrawer(false));
        $("#scrim").addEventListener("click", () => setDrawer(false));
        document.addEventListener("keydown", (e) => { if (e.key === "Escape" && !$("#settings").hidden) setDrawer(false); });

        $$("#src-seg button").forEach((b) => b.addEventListener("click", () => setSource(b.dataset.src)));
        $("#file").addEventListener("change", (e) => pickFile(e.target.files[0]));
        const drop = $("#drop");
        ["dragenter", "dragover"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.add("over"); }));
        ["dragleave", "drop"].forEach((ev) => drop.addEventListener(ev, (e) => { e.preventDefault(); drop.classList.remove("over"); }));
        drop.addEventListener("drop", (e) => pickFile(e.dataTransfer.files[0]));

        $("#cam-shot").addEventListener("click", takePhoto);
        $("#sample-btn").addEventListener("click", trySample);
        $("#read-btn").addEventListener("click", readPage);
        $("#add-row").addEventListener("click", addRow);
        $("#save-btn").addEventListener("click", saveEntries);

        $$("#status-seg button").forEach((b) => b.addEventListener("click", () => {
            ledger.status = b.dataset.status;
            if (ledger.status !== "pending") ledger.days = null;
            $$("#status-seg button").forEach((x) => x.classList.toggle("on", x === b));
            $$("#age-seg button").forEach((x) => x.classList.toggle("on", x.dataset.days === ""));
            loadLedger();
        }));
        $$("#age-seg button").forEach((b) => b.addEventListener("click", () => {
            ledger.days = b.dataset.days ? parseInt(b.dataset.days, 10) : null;
            $$("#age-seg button").forEach((x) => x.classList.toggle("on", x === b));
            loadLedger();
        }));

        window.addEventListener("pagehide", stopCamera);
    }

    async function init() {
        try { config = Object.assign(config, await api("/api/config")); }
        catch (e) { setMsg("err", e.message); }
        initSettings();
        $("#sample-btn").hidden = !config.sample;
        wire();
    }

    init();
})();