// ─── Time Wheel Capital — Client Portfolio tool ─────────────────────────────
// Client holdings live ONLY in this browser (localStorage). Stock prices come
// from docs/prices.json, refreshed daily by the Portfolio Prices GitHub Action.

const REPO = "canctiwari-sketch/BSEAnnouncementsTracker";
const PF_KEY = "twc_portfolios";       // localStorage: array of holdings
const GH_TOKEN_KEY = "twc_gh_token";   // shared with the main site (Watchlist sync)

let holdings = [];      // [{id, client, name, scrip_code, nse_symbol, qty, buy, date}]
let prices = {};        // { "500325.BO": {price, name, currency} }
let pricesUpdated = null;
let scrips = [];        // [{name, scrip_code, nse_symbol}] for autocomplete

document.addEventListener("DOMContentLoaded", () => {
    holdings = loadHoldings();
    document.getElementById("pfForm").addEventListener("submit", onAddHolding);
    setupAutocomplete();
    loadPrices().then(render);
    loadScrips();
    render();  // immediate render from localStorage while prices load
});

// ─── Yahoo symbol for a holding ─────────────────────────────────────────────
// Prefer NSE (.NS): Yahoo serves reliable live/EOD prices for NSE tickers,
// whereas numeric BSE codes (.BO) are patchier. Fall back to .BO for BSE-only.
function ysymFor(h) {
    if (h.nse_symbol) return `${h.nse_symbol}.NS`;
    if (h.scrip_code) return `${h.scrip_code}.BO`;
    return null;
}

// ─── Persistence ─────────────────────────────────────────────────────────────
function loadHoldings() {
    try {
        const raw = localStorage.getItem(PF_KEY);
        return raw ? JSON.parse(raw) : [];
    } catch { return []; }
}
function saveHoldings() {
    localStorage.setItem(PF_KEY, JSON.stringify(holdings));
}

// ─── Prices ──────────────────────────────────────────────────────────────────
async function loadPrices() {
    try {
        const r = await fetch(`https://raw.githubusercontent.com/${REPO}/main/docs/prices.json?t=${Date.now()}`);
        if (!r.ok) return;
        const data = await r.json();
        prices = data.prices || {};
        pricesUpdated = data.last_updated || null;
    } catch { /* offline / first run — leave prices empty */ }
}

// ─── Scrips (for autocomplete) ───────────────────────────────────────────────
async function loadScrips() {
    try {
        const r = await fetch(`https://raw.githubusercontent.com/${REPO}/main/data/scrips.json?v=${Date.now()}`);
        if (!r.ok) return;
        const raw = await r.json();
        const seen = new Set();
        raw.forEach(s => {
            const name = s.ScripName || s.IssuerName || "";
            if (!name) return;
            const key = (s.ScripCode || "") + "|" + (s.NSESymbol || "") + "|" + name;
            if (seen.has(key)) return;
            seen.add(key);
            scrips.push({
                name,
                scrip_code: s.ScripCode ? String(s.ScripCode) : "",
                nse_symbol: s.NSESymbol || "",
            });
        });
    } catch { /* autocomplete just won't suggest; manual entry still works */ }
}

// ─── Autocomplete ────────────────────────────────────────────────────────────
let pfSelected = null;   // chosen scrip object
let suggIdx = -1;

function setupAutocomplete() {
    const input = document.getElementById("pfStock");
    const box = document.getElementById("pfSuggest");

    input.addEventListener("input", () => {
        pfSelected = null;
        const q = input.value.trim().toLowerCase();
        if (q.length < 2) { box.style.display = "none"; return; }
        const matches = scrips.filter(s =>
            s.name.toLowerCase().includes(q) ||
            s.nse_symbol.toLowerCase().includes(q) ||
            s.scrip_code.includes(q)
        ).slice(0, 12);
        if (!matches.length) { box.style.display = "none"; return; }
        suggIdx = -1;
        box.innerHTML = matches.map((s, i) =>
            `<div data-i="${i}">${escapeHtml(s.name)}
                <span class="sym">${s.scrip_code ? "BSE " + s.scrip_code : ""}${s.nse_symbol ? " · NSE " + s.nse_symbol : ""}</span>
            </div>`).join("");
        box._matches = matches;
        box.style.display = "block";
        box.querySelectorAll("div").forEach(d => {
            d.addEventListener("click", () => pickScrip(matches[+d.dataset.i]));
        });
    });

    input.addEventListener("keydown", (e) => {
        const items = box.querySelectorAll("div");
        if (box.style.display === "none" || !items.length) return;
        if (e.key === "ArrowDown") { e.preventDefault(); suggIdx = Math.min(suggIdx + 1, items.length - 1); }
        else if (e.key === "ArrowUp") { e.preventDefault(); suggIdx = Math.max(suggIdx - 1, 0); }
        else if (e.key === "Enter" && suggIdx >= 0) { e.preventDefault(); pickScrip(box._matches[suggIdx]); return; }
        else return;
        items.forEach((d, i) => d.classList.toggle("active", i === suggIdx));
    });

    document.addEventListener("click", (e) => {
        if (!e.target.closest("#pfStockWrap")) box.style.display = "none";
    });
}

function pickScrip(s) {
    pfSelected = s;
    document.getElementById("pfStock").value = s.name;
    document.getElementById("pfSuggest").style.display = "none";
}

// ─── Add / delete holdings ───────────────────────────────────────────────────
function onAddHolding(e) {
    e.preventDefault();
    const client = document.getElementById("pfClient").value.trim();
    const qty = parseFloat(document.getElementById("pfQty").value);
    const buy = parseFloat(document.getElementById("pfBuy").value);
    const date = document.getElementById("pfDate").value || "";
    const stockText = document.getElementById("pfStock").value.trim();

    if (!client || !stockText || !(qty > 0) || !(buy >= 0)) {
        setStatus("Please fill client, stock, a positive quantity and a buy price.", true);
        return;
    }
    // Use the picked scrip; if user typed free-text without picking, store name only.
    const s = pfSelected || { name: stockText, scrip_code: "", nse_symbol: "" };
    if (!s.scrip_code && !s.nse_symbol) {
        setStatus("⚠️ Pick the stock from the dropdown so prices can be fetched. Added without auto-pricing.", true);
    }

    holdings.push({
        id: Date.now() + "_" + Math.random().toString(36).slice(2, 7),
        client, name: s.name, scrip_code: s.scrip_code, nse_symbol: s.nse_symbol,
        qty, buy, date,
    });
    saveHoldings();

    // Reset form (keep client name — advisors often add several for one client)
    document.getElementById("pfStock").value = "";
    document.getElementById("pfQty").value = "";
    document.getElementById("pfBuy").value = "";
    document.getElementById("pfDate").value = "";
    pfSelected = null;
    document.getElementById("pfStock").focus();

    // If we have a token and this symbol isn't priced yet, kick off a refresh.
    const ysym = ysymFor(holdings[holdings.length - 1]);
    if (ysym && !prices[ysym] && localStorage.getItem(GH_TOKEN_KEY)) {
        refreshPrices(true);
    }
    render();
}

function deleteHolding(id) {
    holdings = holdings.filter(h => h.id !== id);
    saveHoldings();
    render();
}

// ─── Refresh prices via the GitHub Action ────────────────────────────────────
async function refreshPrices(silent) {
    const ysyms = [...new Set(holdings.map(ysymFor).filter(Boolean))];
    if (!ysyms.length) { if (!silent) setStatus("Add a holding first.", true); return; }

    let token = localStorage.getItem(GH_TOKEN_KEY);
    if (!token) {
        token = prompt(
            "To refresh prices, paste a GitHub Personal Access Token with 'workflow' scope.\n" +
            "(Same token used by the main site's Watchlist sync. It's stored only in this browser.)"
        );
        if (!token) return;
        localStorage.setItem(GH_TOKEN_KEY, token.trim());
    }

    if (!silent) setStatus("⟳ Requesting fresh prices… (this takes ~30–60s, then prices auto-update)");
    try {
        const resp = await fetch(
            `https://api.github.com/repos/${REPO}/actions/workflows/prices.yml/dispatches`,
            {
                method: "POST",
                headers: {
                    Authorization: `token ${token}`,
                    Accept: "application/vnd.github.v3+json",
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({ ref: "main", inputs: { symbols: ysyms.join(",") } }),
            }
        );
        if (!resp.ok) {
            const txt = await resp.text();
            setStatus(`Could not start price refresh (HTTP ${resp.status}). ${txt.slice(0, 120)}`, true);
            return;
        }
        if (!silent) setStatus("✅ Price refresh started. Reloading prices in 60s…");
        // Poll prices.json a few times so the page updates without a manual reload.
        let tries = 0;
        const before = pricesUpdated;
        const poll = setInterval(async () => {
            tries++;
            await loadPrices();
            if (pricesUpdated !== before) { clearInterval(poll); setStatus("✅ Prices updated."); render(); }
            else if (tries >= 12) { clearInterval(poll); setStatus("Prices will update shortly — click Refresh again if needed."); }
            else render();
        }, 10000);
    } catch (err) {
        setStatus(`Network error starting refresh: ${err.message}`, true);
    }
}

// ─── Export / Import ─────────────────────────────────────────────────────────
function exportHoldings() {
    const blob = new Blob([JSON.stringify(holdings, null, 2)], { type: "application/json" });
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `portfolios_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(a.href);
}
function importHoldings(ev) {
    const file = ev.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => {
        try {
            const data = JSON.parse(reader.result);
            if (!Array.isArray(data)) throw new Error("Not a holdings file");
            holdings = data;
            saveHoldings();
            setStatus(`Imported ${holdings.length} holding(s).`);
            loadPrices().then(render);
        } catch (e) { setStatus(`Import failed: ${e.message}`, true); }
    };
    reader.readAsText(file);
    ev.target.value = "";
}

// ─── Compute metrics for one holding ─────────────────────────────────────────
function metrics(h) {
    const ysym = ysymFor(h);
    const p = ysym && prices[ysym] ? prices[ysym].price : null;
    const invested = h.qty * h.buy;
    const current = p != null ? h.qty * p : null;
    const pl = current != null ? current - invested : null;
    const plPct = (pl != null && invested > 0) ? (pl / invested) * 100 : null;
    return { price: p, invested, current, pl, plPct, priced: p != null };
}

// ─── Rendering ───────────────────────────────────────────────────────────────
function render() {
    renderFreshness();
    renderCards();
    renderClients();
    renderRollup();
}

function renderFreshness() {
    const el = document.getElementById("pfFreshness");
    if (!pricesUpdated) { el.textContent = "Prices: not fetched yet — click Refresh prices."; return; }
    const d = new Date(pricesUpdated + "Z");
    el.textContent = `Prices as of ${d.toLocaleString("en-IN")}`;
}

function renderCards() {
    const el = document.getElementById("pfCards");
    if (!holdings.length) { el.innerHTML = ""; return; }
    let inv = 0, cur = 0, anyPriced = false;
    const clients = new Set();
    holdings.forEach(h => {
        const m = metrics(h);
        inv += m.invested;
        if (m.current != null) { cur += m.current; anyPriced = true; }
        else { cur += m.invested; }  // fall back to cost if unpriced, so totals stay sensible
        clients.add(h.client);
    });
    const pl = cur - inv;
    const plPct = inv > 0 ? (pl / inv) * 100 : 0;
    const cls = pl >= 0 ? "gain" : "loss";
    el.innerHTML = `
        <div class="pf-card"><div class="lbl">Clients</div><div class="val">${clients.size}</div>
            <div class="sub muted">${holdings.length} holding(s)</div></div>
        <div class="pf-card"><div class="lbl">Total invested</div><div class="val">${fmtINR(inv)}</div></div>
        <div class="pf-card"><div class="lbl">Current value</div><div class="val">${fmtINR(cur)}</div>
            <div class="sub muted">${anyPriced ? "" : "prices pending"}</div></div>
        <div class="pf-card"><div class="lbl">Overall P&amp;L</div>
            <div class="val ${cls}">${pl >= 0 ? "+" : ""}${fmtINR(pl)}</div>
            <div class="sub ${cls}">${pl >= 0 ? "+" : ""}${plPct.toFixed(2)}%</div></div>`;
}

function renderClients() {
    const el = document.getElementById("pfClients");
    if (!holdings.length) {
        el.innerHTML = `<div class="pf-empty">No holdings yet. Add your first client's stock above ☝️</div>`;
        return;
    }
    // Group by client
    const byClient = {};
    holdings.forEach(h => { (byClient[h.client] = byClient[h.client] || []).push(h); });

    el.innerHTML = Object.keys(byClient).sort((a, b) => a.localeCompare(b)).map(client => {
        const rows = byClient[client];
        let cInv = 0, cCur = 0, cHasPrice = false;
        const body = rows.map(h => {
            const m = metrics(h);
            cInv += m.invested;
            if (m.current != null) { cCur += m.current; cHasPrice = true; }
            const plCls = m.pl == null ? "muted" : m.pl >= 0 ? "gain" : "loss";
            return `<tr>
                <td class="l">${escapeHtml(h.name)}</td>
                <td>${fmtNum(h.qty)}</td>
                <td>${fmtINR(h.buy)}</td>
                <td>${m.priced ? fmtINR(m.price) : "<span class='muted'>—</span>"}</td>
                <td>${fmtINR(m.invested)}</td>
                <td>${m.current != null ? fmtINR(m.current) : "<span class='muted'>—</span>"}</td>
                <td class="${plCls}">${m.pl == null ? "—" : (m.pl >= 0 ? "+" : "") + fmtINR(m.pl)}</td>
                <td class="${plCls}">${m.plPct == null ? "—" : (m.plPct >= 0 ? "+" : "") + m.plPct.toFixed(1) + "%"}</td>
                <td><button class="pf-btn danger" onclick="deleteHolding('${h.id}')">✕</button></td>
            </tr>`;
        }).join("");

        const cPl = cHasPrice ? cCur - cInv : null;
        const cPlPct = (cPl != null && cInv > 0) ? (cPl / cInv) * 100 : null;
        const cCls = cPl == null ? "muted" : cPl >= 0 ? "gain" : "loss";

        return `<div class="pf-client">
            <div class="pf-client-head">
                <h3>${escapeHtml(client)}</h3>
                <span class="meta">${rows.length} holding(s) · invested ${fmtINR(cInv)}</span>
                <span class="pl ${cCls}">${cPl == null ? "" :
                    (cPl >= 0 ? "+" : "") + fmtINR(cPl) + " (" + (cPlPct >= 0 ? "+" : "") + cPlPct.toFixed(1) + "%)"}</span>
            </div>
            <table class="pf-table">
                <thead><tr>
                    <th class="l">Stock</th><th>Qty</th><th>Buy ₹</th><th>LTP ₹</th>
                    <th>Invested</th><th>Current</th><th>P&amp;L</th><th>%</th><th></th>
                </tr></thead>
                <tbody>${body}</tbody>
                <tfoot><tr>
                    <td class="l">Total</td><td></td><td></td><td></td>
                    <td>${fmtINR(cInv)}</td>
                    <td>${cHasPrice ? fmtINR(cCur) : "—"}</td>
                    <td class="${cCls}">${cPl == null ? "—" : (cPl >= 0 ? "+" : "") + fmtINR(cPl)}</td>
                    <td class="${cCls}">${cPlPct == null ? "—" : (cPlPct >= 0 ? "+" : "") + cPlPct.toFixed(1) + "%"}</td>
                    <td></td>
                </tr></tfoot>
            </table>
        </div>`;
    }).join("");
}

function renderRollup() {
    const el = document.getElementById("pfRollup");
    if (holdings.length < 2) { el.innerHTML = ""; return; }
    // Aggregate identical stocks across all clients
    const byStock = {};
    holdings.forEach(h => {
        const key = ysymFor(h) || h.name;
        const m = metrics(h);
        const e = byStock[key] || (byStock[key] = { name: h.name, qty: 0, invested: 0, current: 0, priced: false, clients: new Set() });
        e.qty += h.qty;
        e.invested += m.invested;
        if (m.current != null) { e.current += m.current; e.priced = true; }
        e.clients.add(h.client);
    });
    const list = Object.values(byStock).sort((a, b) => (b.current || b.invested) - (a.current || a.invested));
    const body = list.map(e => {
        const pl = e.priced ? e.current - e.invested : null;
        const plPct = (pl != null && e.invested > 0) ? (pl / e.invested) * 100 : null;
        const cls = pl == null ? "muted" : pl >= 0 ? "gain" : "loss";
        return `<tr>
            <td class="l">${escapeHtml(e.name)}</td>
            <td>${e.clients.size}</td>
            <td>${fmtNum(e.qty)}</td>
            <td>${fmtINR(e.invested)}</td>
            <td>${e.priced ? fmtINR(e.current) : "—"}</td>
            <td class="${cls}">${pl == null ? "—" : (pl >= 0 ? "+" : "") + fmtINR(pl)}</td>
            <td class="${cls}">${plPct == null ? "—" : (plPct >= 0 ? "+" : "") + plPct.toFixed(1) + "%"}</td>
        </tr>`;
    }).join("");
    el.innerHTML = `<div class="pf-section-title">📊 Across all clients — by stock</div>
        <div class="pf-client"><table class="pf-table">
            <thead><tr><th class="l">Stock</th><th>Clients</th><th>Total Qty</th>
                <th>Invested</th><th>Current</th><th>P&amp;L</th><th>%</th></tr></thead>
            <tbody>${body}</tbody>
        </table></div>`;
}

// ─── Helpers ─────────────────────────────────────────────────────────────────
function fmtINR(x) {
    if (x == null || isNaN(x)) return "—";
    return "₹" + new Intl.NumberFormat("en-IN", { maximumFractionDigits: x < 100 ? 2 : 0 }).format(x);
}
function fmtNum(x) {
    return new Intl.NumberFormat("en-IN", { maximumFractionDigits: 4 }).format(x);
}
function escapeHtml(s) {
    return String(s).replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
function setStatus(msg, isErr) {
    const el = document.getElementById("pfStatus");
    el.textContent = msg;
    el.style.color = isErr ? "#dc2626" : "#6b7280";
}
