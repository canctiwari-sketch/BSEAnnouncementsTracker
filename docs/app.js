let allAnnouncements = [];
let currentFiltered = [];
let currentSort = { col: "date", dir: "desc" };
let searchTimeout = null;
let currentPage = 1;
const PAGE_SIZE = 50;

// ─── Watchlist ───────────────────────────────────────────────────────────────
let watchlist = {};
const WL_KEY = "twc_watchlist";
window._pageItems = [];

// High-priority categories
const STARRED_CATEGORIES = new Set([
    "Open Offer", "Warrants", "Buyback", "Delisting", "Business Expansion",
]);
const STARRED_RE = /open.?offer|warrants?|buybacks?|buy.?backs?|delisting|delist|capex|capital expenditure|expansion/i;

document.addEventListener("DOMContentLoaded", () => {
    // Debounced search (300ms)
    document.getElementById("searchBox").addEventListener("input", () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(applyFilter, 300);
    });
    // Escape key closes watchlist modal
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { closeWatchlist(); closeLookup(); }
    });
    loadWatchlist();
    restoreFilters();
    fetchData();
});

async function fetchData() {
    setStatus("Loading announcements...", "loading");
    try {
        const repo = "canctiwari-sketch/BSEAnnouncementsTracker";
        const r = await fetch(`https://raw.githubusercontent.com/${repo}/main/data/announcements.json?${Date.now()}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        allAnnouncements = data.announcements || [];

        const updated = data.last_updated
            ? new Date(data.last_updated + "Z")
            : null;
        const updatedStr = updated ? updated.toLocaleString("en-IN") : "unknown";
        setStatus(`${allAnnouncements.length} announcements \u2014 Last updated: ${updatedStr}`);

        // Show "X minutes ago" refresh indicator
        if (updated) {
            updateRefreshInfo(updated);
            setInterval(() => updateRefreshInfo(updated), 60000);
        }

        populateCategoryFilter();
        applyFilter();
    } catch (e) {
        setStatus(`Error loading data: ${e.message}`, "error");
    }
}

function updateRefreshInfo(updatedDate) {
    const mins = Math.floor((Date.now() - updatedDate.getTime()) / 60000);
    const el = document.getElementById("refreshInfo");
    if (mins < 1) {
        el.textContent = "Updated just now";
    } else if (mins < 60) {
        el.textContent = `Updated ${mins}m ago`;
    } else {
        el.textContent = `Updated ${Math.floor(mins / 60)}h ${mins % 60}m ago`;
    }
    el.innerHTML += ' <a href="#" onclick="location.reload();return false" class="refresh-link">Refresh</a>';
}

function setStatus(msg, type = "") {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "status " + type;
}

function isStarred(a) {
    if (a.starred) return true;
    if (STARRED_CATEGORIES.has(a.category || "")) return true;
    const text = (a.subject || "") + " " + (a.ai_summary || "");
    if (STARRED_RE.test(text)) return true;
    return false;
}

function populateCategoryFilter() {
    const dropdown = document.getElementById("categoryDropdown");
    // Count announcements per category
    const catCounts = {};
    allAnnouncements.forEach(a => {
        const cat = a.category || "";
        if (cat) catCounts[cat] = (catCounts[cat] || 0) + 1;
    });
    const categories = Object.keys(catCounts).sort();
    dropdown.innerHTML = "";
    categories.forEach(cat => {
        const label = document.createElement("label");
        label.className = "check-item";
        label.innerHTML = `<input type="checkbox" value="${escapeAttr(cat)}" checked onchange="onCategoryChange()"> ${escapeHtml(cat)} <span class="cat-count">(${catCounts[cat]})</span>`;
        dropdown.appendChild(label);
    });
    updateMultiSelectText("categoryFilter", "categoryDropdown");
}

function onCategoryChange() {
    updateMultiSelectText("categoryFilter", "categoryDropdown");
    applyFilter();
}


function updateMultiSelectText(selectId, dropdownId) {
    const allChecks = document.querySelectorAll(`#${dropdownId} input`);
    const checks = document.querySelectorAll(`#${dropdownId} input:checked`);
    const text = document.querySelector(`#${selectId} .multi-select-text`);
    if (checks.length === 0 || checks.length === allChecks.length) {
        text.textContent = "All";
    } else if (checks.length <= 3) {
        text.textContent = [...checks].map(c => c.value).join(", ");
    } else {
        text.textContent = `${checks.length} selected`;
    }
}

function getSelectedValues(selectId) {
    const checks = document.querySelectorAll(`#${selectId} .check-item input:checked`);
    return [...checks].map(c => c.value);
}

function getMcapClass(value) {
    if (!value) return "na";
    const cr = value / 1e7;
    if (cr >= 20000) return "large-cap";
    if (cr >= 5000) return "mid-cap";
    return "small-cap";
}

function screenerLink(name) {
    const q = encodeURIComponent(name + " screener.in");
    return `https://www.google.com/search?q=${q}`;
}

// ─── Sorting ────────────────────────────────────────────────────────────────
function sortBy(col) {
    if (currentSort.col === col) {
        currentSort.dir = currentSort.dir === "asc" ? "desc" : "asc";
    } else {
        currentSort.col = col;
        currentSort.dir = col === "date" ? "desc" : "asc";
    }
    // Update arrow indicators
    document.querySelectorAll(".sort-arrow").forEach(el => {
        el.textContent = el.dataset.col === col
            ? (currentSort.dir === "asc" ? "\u25B2" : "\u25BC")
            : "";
    });
    applySort();
    currentPage = 1;
    renderPage();
}

function applySort() {
    const { col, dir } = currentSort;
    currentFiltered.sort((a, b) => {
        let va, vb;
        if (col === "date") {
            va = parseAnnDate(a.date);
            vb = parseAnnDate(b.date);
        } else if (col === "market_cap") {
            va = a.market_cap || 0;
            vb = b.market_cap || 0;
        } else if (col === "company") {
            va = (a.company || "").toLowerCase();
            vb = (b.company || "").toLowerCase();
            return dir === "asc" ? va.localeCompare(vb) : vb.localeCompare(va);
        }
        return dir === "asc" ? va - vb : vb - va;
    });
}

// ─── Filters ────────────────────────────────────────────────────────────────
function applyFilter() {
    const query = document.getElementById("searchBox").value.toLowerCase().trim();
    const catFilters = getSelectedValues("categoryFilter");
    const starOnly = document.getElementById("starFilter").checked;
    const dateFrom = document.getElementById("dateFrom").value;
    const dateTo = document.getElementById("dateTo").value;
    const mcapMinVal = document.getElementById("mcapMin").value;
    const mcapMaxVal = document.getElementById("mcapMax").value;
    const mcapMin = mcapMinVal ? parseFloat(mcapMinVal) * 1e7 : null;  // Convert Cr to raw
    const mcapMax = mcapMaxVal ? parseFloat(mcapMaxVal) * 1e7 : null;
    const includeNA = document.getElementById("includeNA").checked;

    let filtered = allAnnouncements;

    if (starOnly) {
        filtered = filtered.filter(a => isStarred(a));
    }

    if (dateFrom || dateTo) {
        const fromTs = dateFrom ? new Date(dateFrom + "T00:00:00").getTime() : 0;
        const toTs = dateTo ? new Date(dateTo + "T23:59:59").getTime() : Infinity;
        filtered = filtered.filter(a => {
            const ts = parseAnnDate(a.date);
            return ts >= fromTs && ts <= toTs;
        });
    }

    const allCatChecks = document.querySelectorAll("#categoryDropdown input").length;
    if (catFilters.length > 0 && catFilters.length < allCatChecks) {
        filtered = filtered.filter(a => catFilters.includes(a.category || ""));
    }

    // Market cap filter — exclude N/A unless checkbox is checked
    filtered = filtered.filter(a => {
        const val = a.market_cap;
        if (!val) return includeNA;  // N/A companies only shown if checkbox checked
        if (mcapMin !== null && val < mcapMin) return false;
        if (mcapMax !== null && val > mcapMax) return false;
        return true;
    });

    if (query) {
        filtered = filtered.filter(a => {
            const text = `${a.company} ${a.symbol} ${a.subject} ${a.detail} ${a.category} ${a.ai_summary || ""}`.toLowerCase();
            return text.includes(query);
        });
    }

    currentFiltered = filtered;
    applySort();
    currentPage = 1;

    // Update count
    const total = allAnnouncements.length;
    const shown = filtered.length;
    const statusEl = document.getElementById("status");
    const base = statusEl.textContent.split(" | Showing")[0];
    statusEl.textContent = shown < total ? `${base} | Showing ${shown} of ${total}` : base;

    renderPage();
    saveFilters();
}

function clearAllFilters() {
    document.getElementById("searchBox").value = "";
    document.getElementById("starFilter").checked = false;
    document.getElementById("dateFrom").value = "";
    document.getElementById("dateTo").value = "";
    document.getElementById("mcapMin").value = "";
    document.getElementById("mcapMax").value = "";
    document.getElementById("includeNA").checked = false;
    document.querySelectorAll("#categoryDropdown input").forEach(c => c.checked = true);
    updateMultiSelectText("categoryFilter", "categoryDropdown");
    localStorage.removeItem("twc_filters");
    applyFilter();
}

// ─── localStorage persistence ───────────────────────────────────────────────
function saveFilters() {
    const state = {
        starOnly: document.getElementById("starFilter").checked,
        dateFrom: document.getElementById("dateFrom").value,
        dateTo: document.getElementById("dateTo").value,
        search: document.getElementById("searchBox").value,
        mcapMin: document.getElementById("mcapMin").value,
        mcapMax: document.getElementById("mcapMax").value,
        includeNA: document.getElementById("includeNA").checked,
    };
    localStorage.setItem("twc_filters", JSON.stringify(state));
}

function restoreFilters() {
    try {
        const raw = localStorage.getItem("twc_filters");
        if (!raw) return;
        const state = JSON.parse(raw);
        if (state.starOnly) document.getElementById("starFilter").checked = true;
        if (state.dateFrom) document.getElementById("dateFrom").value = state.dateFrom;
        if (state.dateTo) document.getElementById("dateTo").value = state.dateTo;
        if (state.search) document.getElementById("searchBox").value = state.search;
        if (state.mcapMin) document.getElementById("mcapMin").value = state.mcapMin;
        if (state.mcapMax) document.getElementById("mcapMax").value = state.mcapMax;
        if (state.includeNA) document.getElementById("includeNA").checked = true;
    } catch {}
}

// ─── Export Excel ────────────────────────────────────────────────────────────
function exportXLSX() {
    if (!currentFiltered.length) return;
    const headers = ["Company", "Symbol", "Exchange", "Market Cap", "Category", "Subject", "AI Summary", "Date", "PDF"];
    const data = currentFiltered.map(a => ({
        "Company": a.company || "",
        "Symbol": a.symbol || "",
        "Exchange": a.exchange || "",
        "Market Cap": a.market_cap_fmt || "N/A",
        "Category": a.category || "",
        "Subject": a.subject || "",
        "AI Summary": a.ai_summary || "",
        "Date": a.date || "",
        "PDF": a.attachment || "",
    }));
    const ws = XLSX.utils.json_to_sheet(data, { header: headers });
    // Set column widths
    ws["!cols"] = [
        { wch: 25 },  // Company
        { wch: 12 },  // Symbol
        { wch: 8 },   // Exchange
        { wch: 14 },  // Market Cap
        { wch: 18 },  // Category
        { wch: 50 },  // Subject
        { wch: 80 },  // AI Summary — wide enough to read
        { wch: 20 },  // Date
        { wch: 40 },  // PDF
    ];
    // Enable text wrapping on AI Summary and Subject columns
    const range = XLSX.utils.decode_range(ws["!ref"]);
    for (let r = range.s.r; r <= range.e.r; r++) {
        for (const c of [5, 6]) { // Subject=5, AI Summary=6
            const addr = XLSX.utils.encode_cell({ r, c });
            if (ws[addr]) {
                ws[addr].s = { alignment: { wrapText: true, vertical: "top" } };
            }
        }
    }
    const wb = XLSX.utils.book_new();
    XLSX.utils.book_append_sheet(wb, ws, "Announcements");
    XLSX.writeFile(wb, `announcements_${new Date().toISOString().slice(0, 10)}.xlsx`);
}

// ─── Pagination & Rendering ─────────────────────────────────────────────────
function renderPage() {
    const total = currentFiltered.length;
    const totalPages = Math.max(1, Math.ceil(total / PAGE_SIZE));
    if (currentPage > totalPages) currentPage = totalPages;

    const start = (currentPage - 1) * PAGE_SIZE;
    const end = Math.min(start + PAGE_SIZE, total);
    const pageItems = currentFiltered.slice(start, end);
    window._pageItems = pageItems;

    renderTable(pageItems);
    updatePagination(totalPages, start, end, total);
}

function updatePagination(totalPages, start, end, total) {
    const el = document.getElementById("pagination");
    if (!el) return;
    if (total === 0) {
        el.style.display = "none";
        return;
    }
    el.style.display = "flex";
    document.getElementById("pageInfo").textContent = `${start + 1}–${end} of ${total}`;
    document.getElementById("prevBtn").disabled = currentPage <= 1;
    document.getElementById("nextBtn").disabled = currentPage >= totalPages;
    document.getElementById("pageNum").textContent = `Page ${currentPage} of ${totalPages}`;
}

function prevPage() {
    if (currentPage > 1) {
        currentPage--;
        renderPage();
        window.scrollTo({ top: 0, behavior: "smooth" });
    }
}

function nextPage() {
    const totalPages = Math.ceil(currentFiltered.length / PAGE_SIZE);
    if (currentPage < totalPages) {
        currentPage++;
        renderPage();
        window.scrollTo({ top: 0, behavior: "smooth" });
    }
}

function renderTable(announcements) {
    const tbody = document.getElementById("annBody");
    if (!announcements.length) {
        tbody.innerHTML = '<tr><td colspan="6" style="text-align:center;padding:20px;color:#484f58">No announcements found.</td></tr>';
        return;
    }
    tbody.innerHTML = announcements.map((a, i) => renderRow(a, i)).join("");
}

function renderRow(a, idx) {
    const name = a.company || "Unknown";
    const symbol = a.symbol || "";
    const exchange = a.exchange || "";
    const mcapFmt = a.market_cap_fmt || "N/A";
    const mcapClass = getMcapClass(a.market_cap);
    const category = a.category || "";
    const date = formatDisplayDate(a.date || "");
    const attachment = a.attachment || "";
    const starred = isStarred(a);
    const aiSummary = a.ai_summary || "";

    const starIcon = starred ? `<span class="star-icon" title="High Priority">&#9733;</span>` : "";
    const rowClass = starred ? "starred-row" : "";
    const inWL = isInWatchlist(a);
    const wlBtn = `<button class="wl-add-btn ${inWL ? 'wl-added' : ''}" onclick="event.stopPropagation();addToWatchlistByIdx(${idx})" title="${inWL ? 'In watchlist' : 'Add to watchlist'}">${inWL ? '&#10003;' : '+'}</button>`;
    const exchangeBadge = `<span class="exchange-badge ${exchange.toLowerCase()}">${exchange}</span>`;
    const attachmentLink = attachment
        ? `<a class="attachment-link" href="${escapeAttr(attachment)}" target="_blank" rel="noopener">PDF</a>`
        : "-";
    const categoryBadge = category ? `<span class="category-badge">${escapeHtml(category)}</span>` : "";

    const detail = a.detail || "";
    let detailHtml = "";
    if (detail) {
        const short = detail.length > 150 ? detail.slice(0, 147) + "..." : detail;
        detailHtml = `<div class="summary-text">${highlightSearch(escapeHtml(short))}</div>`;
    }

    const aiHtml = aiSummary
        ? `<span class="ai-summary">${highlightSearch(escapeHtml(aiSummary))}</span>`
        : `<span class="ai-pending">\u2014</span>`;

    return `<tr class="${rowClass}">
        <td class="company-cell">
            ${wlBtn}${starIcon}
            <a class="company-name" href="${screenerLink(name)}" target="_blank" rel="noopener">${highlightSearch(escapeHtml(name))}</a>
            <div class="scrip-code">${exchangeBadge} ${escapeHtml(symbol)}</div>
        </td>
        <td class="mcap-cell ${mcapClass}">${mcapFmt}</td>
        <td class="subject-cell">
            ${categoryBadge}
            ${detailHtml}
        </td>
        <td class="ai-cell">${aiHtml}</td>
        <td class="date-cell">${escapeHtml(date)}</td>
        <td>${attachmentLink}</td>
    </tr>`;
}

// ─── Search highlighting ────────────────────────────────────────────────────
function highlightSearch(html) {
    const query = document.getElementById("searchBox").value.trim();
    if (!query || query.length < 2) return html;
    try {
        const re = new RegExp(`(${query.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')})`, "gi");
        return html.replace(re, '<mark class="search-highlight">$1</mark>');
    } catch {
        return html;
    }
}

// ─── Date helpers ───────────────────────────────────────────────────────────
function parseAnnDate(dateStr) {
    if (!dateStr) return 0;
    let d = new Date(dateStr);
    if (!isNaN(d)) return d.getTime();
    const m = dateStr.match(/(\d{1,2})-(\w{3})-(\d{4})\s*(\d{2}:\d{2}:\d{2})?/);
    if (m) {
        const s = `${m[2]} ${m[1]}, ${m[3]}${m[4] ? " " + m[4] : ""}`;
        d = new Date(s);
        if (!isNaN(d)) return d.getTime();
    }
    return 0;
}

function formatDisplayDate(date) {
    if (!date) return "";
    try {
        const d = new Date(date);
        if (!isNaN(d)) {
            return d.toLocaleDateString("en-IN", { day: "2-digit", month: "short" }) +
                " " + d.toLocaleTimeString("en-IN", { hour: "2-digit", minute: "2-digit" });
        }
    } catch {}
    return date;
}

function escapeHtml(str) {
    const div = document.createElement("div");
    div.textContent = str || "";
    return div.innerHTML;
}

function escapeAttr(str) {
    return (str || "").replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

// ─── Watchlist Functions ─────────────────────────────────────────────────────
const GH_REPO = "canctiwari-sketch/BSEAnnouncementsTracker";
const GH_WL_PATH = "data/watchlist.json";
const GH_TOKEN_KEY = "twc_gh_token";
let wlSyncing = false;
let wlSha = null; // GitHub file SHA for updates

function loadWatchlist() {
    // Load from localStorage first (instant)
    try {
        const raw = localStorage.getItem(WL_KEY);
        watchlist = raw ? JSON.parse(raw) : {};
    } catch { watchlist = {}; }
    updateWatchlistCount();

    // Then fetch from GitHub and merge (async)
    fetchWatchlistFromGitHub();
}

async function fetchWatchlistFromGitHub() {
    try {
        const r = await fetch(`https://raw.githubusercontent.com/${GH_REPO}/main/${GH_WL_PATH}?${Date.now()}`);
        if (!r.ok) return; // File doesn't exist yet
        const remote = await r.json();
        if (typeof remote !== "object" || Array.isArray(remote)) return;

        // Merge remote into local (remote entries we don't have locally)
        let changed = false;
        for (const key in remote) {
            if (!watchlist[key]) {
                watchlist[key] = remote[key];
                changed = true;
            } else {
                // Merge notes
                const localDates = new Set(watchlist[key].notes.map(n => n.date + n.subject));
                for (const note of (remote[key].notes || [])) {
                    if (!localDates.has(note.date + note.subject)) {
                        watchlist[key].notes.push(note);
                        changed = true;
                    }
                }
                // Keep user_note from remote if local is empty
                if (!watchlist[key].user_note && remote[key].user_note) {
                    watchlist[key].user_note = remote[key].user_note;
                    changed = true;
                }
            }
        }
        if (changed) {
            localStorage.setItem(WL_KEY, JSON.stringify(watchlist));
            updateWatchlistCount();
            renderPage();
        }

        // Get SHA for future updates
        const token = localStorage.getItem(GH_TOKEN_KEY);
        if (token) {
            const shaR = await fetch(`https://api.github.com/repos/${GH_REPO}/contents/${GH_WL_PATH}`, {
                headers: { "Authorization": `token ${token}` }
            });
            if (shaR.ok) {
                const shaData = await shaR.json();
                wlSha = shaData.sha;
            }
        }
    } catch {}
}

function saveWatchlist() {
    localStorage.setItem(WL_KEY, JSON.stringify(watchlist));
    updateWatchlistCount();
    syncWatchlistToGitHub(); // Push to GitHub in background
}

async function syncWatchlistToGitHub() {
    const token = localStorage.getItem(GH_TOKEN_KEY);
    if (!token || wlSyncing) return;

    wlSyncing = true;
    try {
        const content = btoa(unescape(encodeURIComponent(JSON.stringify(watchlist, null, 2))));
        const body = {
            message: "Update watchlist",
            content: content,
            branch: "main",
        };
        if (wlSha) body.sha = wlSha;

        const r = await fetch(`https://api.github.com/repos/${GH_REPO}/contents/${GH_WL_PATH}`, {
            method: "PUT",
            headers: {
                "Authorization": `token ${token}`,
                "Content-Type": "application/json",
            },
            body: JSON.stringify(body),
        });

        if (r.ok) {
            const data = await r.json();
            wlSha = data.content.sha;
        } else if (r.status === 409) {
            // Conflict — refetch SHA and retry once
            const shaR = await fetch(`https://api.github.com/repos/${GH_REPO}/contents/${GH_WL_PATH}`, {
                headers: { "Authorization": `token ${token}` }
            });
            if (shaR.ok) {
                const shaData = await shaR.json();
                wlSha = shaData.sha;
                body.sha = wlSha;
                const r2 = await fetch(`https://api.github.com/repos/${GH_REPO}/contents/${GH_WL_PATH}`, {
                    method: "PUT",
                    headers: {
                        "Authorization": `token ${token}`,
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify(body),
                });
                if (r2.ok) {
                    const data2 = await r2.json();
                    wlSha = data2.content.sha;
                }
            }
        }
    } catch {}
    wlSyncing = false;
}

function setupGitHubToken() {
    const existing = localStorage.getItem(GH_TOKEN_KEY);
    const token = prompt(
        existing
            ? "GitHub token is set. Enter new token to update, or leave empty to keep current:"
            : "Enter your GitHub Personal Access Token (needs 'repo' or 'contents:write' permission).\nThis is stored only in your browser, never in the repo."
    );
    if (token === null) return; // Cancelled
    if (token.trim()) {
        localStorage.setItem(GH_TOKEN_KEY, token.trim());
        syncWatchlistToGitHub(); // Push current watchlist immediately
    }
    renderWatchlistModal();
}

function getWatchlistKey(a) {
    return (a.symbol || "") + "_" + (a.exchange || "");
}

function isInWatchlist(a) {
    return !!watchlist[getWatchlistKey(a)];
}

function addToWatchlistByIdx(idx) {
    const a = window._pageItems[idx];
    if (!a) return;
    // Show inline note popup
    showNotePopup(idx, a);
}

function showNotePopup(idx, a) {
    // Remove any existing popup
    const existing = document.getElementById("wlNotePopup");
    if (existing) existing.remove();

    // Find the "+" button to position near it
    const btn = document.querySelectorAll("#annBody .wl-add-btn")[idx];
    if (!btn) return;

    const popup = document.createElement("div");
    popup.id = "wlNotePopup";
    popup.className = "wl-note-popup";
    popup.innerHTML = `
        <div class="wl-note-popup-header">Add to Watchlist</div>
        <div class="wl-note-popup-company">${escapeHtml(a.company || "")}</div>
        <textarea id="wlNoteInput" class="wl-note-popup-input" placeholder="Add your note (optional)..." rows="3"></textarea>
        <div class="wl-note-popup-actions">
            <button class="wl-note-popup-cancel" onclick="closeNotePopup()">Cancel</button>
            <button class="wl-note-popup-save" onclick="confirmAddToWatchlist(${idx})">Save</button>
        </div>
    `;

    // Position popup near the button
    const rect = btn.getBoundingClientRect();
    popup.style.position = "fixed";
    popup.style.top = Math.min(rect.bottom + 5, window.innerHeight - 220) + "px";
    popup.style.left = Math.max(rect.left, 10) + "px";
    popup.style.zIndex = "1001";

    document.body.appendChild(popup);

    // Focus the textarea
    setTimeout(() => document.getElementById("wlNoteInput").focus(), 50);

    // Close on Escape
    const onKey = (e) => {
        if (e.key === "Escape") { closeNotePopup(); document.removeEventListener("keydown", onKey); }
        if (e.key === "Enter" && e.ctrlKey) { confirmAddToWatchlist(idx); document.removeEventListener("keydown", onKey); }
    };
    document.addEventListener("keydown", onKey);
}

function closeNotePopup() {
    const popup = document.getElementById("wlNotePopup");
    if (popup) popup.remove();
}

function confirmAddToWatchlist(idx) {
    const a = window._pageItems[idx];
    if (!a) return;
    const key = getWatchlistKey(a);
    const userNote = (document.getElementById("wlNoteInput")?.value || "").trim();

    closeNotePopup();

    const note = {
        subject: a.subject || "",
        category: a.category || "",
        ai_summary: a.ai_summary || "",
        date: a.date || "",
        attachment: a.attachment || "",
        added_on: new Date().toISOString(),
        user_note: userNote,
    };
    if (watchlist[key]) {
        const isDupe = watchlist[key].notes.some(n => n.date === note.date && n.subject === note.subject);
        if (!isDupe) {
            watchlist[key].notes.push(note);
        }
        watchlist[key].market_cap_fmt = a.market_cap_fmt || watchlist[key].market_cap_fmt;
        watchlist[key].market_cap = a.market_cap || watchlist[key].market_cap;
    } else {
        watchlist[key] = {
            company: a.company || "Unknown",
            symbol: a.symbol || "",
            exchange: a.exchange || "",
            market_cap: a.market_cap || null,
            market_cap_fmt: a.market_cap_fmt || "N/A",
            notes: [note],
        };
    }
    saveWatchlist();
    renderPage();
}

function removeFromWatchlist(key) {
    delete watchlist[key];
    saveWatchlist();
    renderWatchlistModal();
    renderPage(); // Update checkmarks in table
}

function removeNoteFromWatchlist(key, noteIdx) {
    if (!watchlist[key]) return;
    watchlist[key].notes.splice(noteIdx, 1);
    if (watchlist[key].notes.length === 0) {
        delete watchlist[key];
    }
    saveWatchlist();
    renderWatchlistModal();
    renderPage();
}

function updateWatchlistCount() {
    const el = document.getElementById("wlCount");
    if (el) el.textContent = `(${Object.keys(watchlist).length})`;
}

function openWatchlist() {
    document.getElementById("watchlistOverlay").style.display = "flex";
    renderWatchlistModal();
}

function closeWatchlist() {
    document.getElementById("watchlistOverlay").style.display = "none";
}

function renderWatchlistModal() {
    const body = document.getElementById("wlContent");
    const keys = Object.keys(watchlist).sort((a, b) => {
        return (watchlist[a].company || "").localeCompare(watchlist[b].company || "");
    });

    const hasToken = !!localStorage.getItem(GH_TOKEN_KEY);
    const syncStatus = hasToken
        ? '<span class="wl-sync-status wl-synced">&#9679; Cloud synced &mdash; <a href="#" onclick="event.preventDefault();setupGitHubToken()" style="color:#5a3d8a;font-size:0.78rem">Update token</a></span>'
        : '<span class="wl-sync-status wl-not-synced" onclick="setupGitHubToken()" title="Click to connect">&#9679; Not synced — <a href="#" onclick="event.preventDefault();setupGitHubToken()">Connect GitHub</a></span>';

    if (!keys.length) {
        body.innerHTML = `<div style="padding:8px 0 4px">${syncStatus}</div><div class="wl-empty">No companies in watchlist yet.<br>Click the <strong>+</strong> button on any announcement to add it.</div>`;
        return;
    }

    // Try to get latest market cap from loaded data
    const latestMcap = {};
    allAnnouncements.forEach(a => {
        const k = getWatchlistKey(a);
        if (a.market_cap_fmt && a.market_cap_fmt !== "N/A") {
            latestMcap[k] = a.market_cap_fmt;
        }
    });

    body.innerHTML = `<div style="padding:0 0 8px">${syncStatus}</div>` + keys.map(key => {
        const entry = watchlist[key];
        const mcap = latestMcap[key] || entry.market_cap_fmt || "N/A";
        const companyLink = `<a class="wl-entry-company" href="${screenerLink(entry.company)}" target="_blank" rel="noopener">${escapeHtml(entry.company)}</a>`;
        const exchangeBadge = `<span class="exchange-badge ${(entry.exchange || '').toLowerCase()}">${escapeHtml(entry.exchange || '')}</span>`;

        const notesHtml = entry.notes.map((n, ni) => {
            const catBadge = n.category ? `<span class="wl-note-category">${escapeHtml(n.category)}</span>` : "";
            const subject = n.subject ? escapeHtml(n.subject.length > 100 ? n.subject.slice(0, 97) + "..." : n.subject) : "";
            const ai = n.ai_summary ? `<div class="wl-note-ai">${escapeHtml(n.ai_summary.length > 200 ? n.ai_summary.slice(0, 197) + "..." : n.ai_summary)}</div>` : "";
            const pdfLink = n.attachment ? `<a class="wl-note-pdf" href="${escapeAttr(n.attachment)}" target="_blank">PDF</a>` : "";
            const dateStr = formatDisplayDate(n.date);

            const userNoteHtml = n.user_note ? `<div class="wl-user-note">${escapeHtml(n.user_note)}</div>` : "";

            return `<div class="wl-note">
                <div class="wl-note-body">
                    ${catBadge}<span class="wl-note-subject">${subject}</span>
                    ${userNoteHtml}
                    ${ai}
                </div>
                <div class="wl-note-meta">
                    <span class="wl-note-date">${escapeHtml(dateStr)}</span>
                    ${pdfLink}
                    <button class="wl-note-remove" onclick="removeNoteFromWatchlist('${escapeAttr(key)}',${ni})" title="Remove this note">&times;</button>
                </div>
            </div>`;
        }).join("");

        const userNote = entry.user_note || "";

        return `<div class="wl-entry">
            <div class="wl-entry-header">
                <div class="wl-entry-left">
                    ${companyLink} ${exchangeBadge}
                    <span class="wl-entry-mcap">${escapeHtml(mcap)}</span>
                </div>
                <button class="wl-remove-btn" onclick="removeFromWatchlist('${escapeAttr(key)}')">Remove All</button>
            </div>
            ${notesHtml}
            <div class="wl-user-note">
                <textarea placeholder="Add your personal note..." onchange="updateUserNote('${escapeAttr(key)}', this.value)" onblur="updateUserNote('${escapeAttr(key)}', this.value)">${escapeHtml(userNote)}</textarea>
            </div>
        </div>`;
    }).join("");
}

function updateUserNote(key, value) {
    if (!watchlist[key]) return;
    watchlist[key].user_note = value;
    saveWatchlist();
}

function exportWatchlist() {
    const data = JSON.stringify(watchlist, null, 2);
    const blob = new Blob([data], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `watchlist_${new Date().toISOString().slice(0, 10)}.json`;
    a.click();
    URL.revokeObjectURL(url);
}

function importWatchlist(event) {
    const file = event.target.files[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = function(e) {
        try {
            const imported = JSON.parse(e.target.result);
            if (typeof imported !== "object" || Array.isArray(imported)) {
                alert("Invalid watchlist file.");
                return;
            }
            // Merge: imported entries get added, existing entries keep their data
            for (const key in imported) {
                if (watchlist[key]) {
                    // Merge notes (avoid duplicates)
                    const existingDates = new Set(watchlist[key].notes.map(n => n.date + n.subject));
                    for (const note of (imported[key].notes || [])) {
                        if (!existingDates.has(note.date + note.subject)) {
                            watchlist[key].notes.push(note);
                        }
                    }
                    // Keep user_note if not already set
                    if (!watchlist[key].user_note && imported[key].user_note) {
                        watchlist[key].user_note = imported[key].user_note;
                    }
                } else {
                    watchlist[key] = imported[key];
                }
            }
            saveWatchlist();
            renderWatchlistModal();
            renderPage();
            alert(`Imported ${Object.keys(imported).length} companies.`);
        } catch {
            alert("Failed to parse watchlist file.");
        }
    };
    reader.readAsText(file);
    event.target.value = ""; // Reset file input
}

// ─── Company Lookup ───────────────────────────────────────────────────────────

const REPO = "canctiwari-sketch/BSEAnnouncementsTracker";
let lookupSelectedCompany = null;   // { name, scrip_code, nse_symbol }
let lookupPollTimer = null;
let researchPollTimer = null;
let lookupSuggestIdx = -1;
let scripsData = null;  // Full BSE company list — loaded once on first Lookup open

let scripsLoading = null;  // Promise while loading, so concurrent calls await the same fetch

async function loadScrips() {
    if (scripsData) return;
    if (scripsLoading) return scripsLoading;  // Already in-flight, wait for it
    scripsLoading = (async () => {
        try {
            const r = await fetch(`https://raw.githubusercontent.com/${REPO}/main/data/scrips.json?v=${Date.now()}`);
            if (r.ok) {
                const raw = await r.json();
                // Build BSE map keyed by ScripCode, merge NSE symbols from NSE-only entries
                const bseMap = new Map();
                const nseSymbolByName = new Map();
                // First pass: collect NSE symbols from NSE-only entries (no ScripCode)
                raw.forEach(s => {
                    if (!s.ScripCode && s.NSESymbol && s.ScripName) {
                        nseSymbolByName.set(s.ScripName.toLowerCase().trim(), s.NSESymbol);
                        nseSymbolByName.set((s.IssuerName || "").toLowerCase().trim(), s.NSESymbol);
                    }
                });
                // Second pass: build BSE entries, merging NSE symbol if missing
                raw.forEach(s => {
                    if (!s.ScripCode) return;
                    const name = s.ScripName || s.IssuerName || "";
                    const nse = s.NSESymbol ||
                        nseSymbolByName.get(name.toLowerCase().trim()) ||
                        nseSymbolByName.get((s.IssuerName || "").toLowerCase().trim()) || "";
                    bseMap.set(String(s.ScripCode), { name, scrip_code: String(s.ScripCode), nse_symbol: nse });
                });
                scripsData = [...bseMap.values()]
                    .filter(s => s.name)
                    .sort((a, b) => a.name.localeCompare(b.name));
            }
        } catch {}
        // Fallback: build from loaded announcements if fetch failed
        if (!scripsData || !scripsData.length) {
            const seen = new Map();
            (allAnnouncements || []).forEach(a => {
                const code = a.symbol || "";
                if (!code || a.exchange !== "BSE") return;
                if (!seen.has(code)) seen.set(code, { name: a.company || "", scrip_code: code, nse_symbol: "" });
            });
            scripsData = [...seen.values()].sort((a, b) => a.name.localeCompare(b.name));
        }
    })();
    return scripsLoading;
}

function buildCompanyList() {
    return scripsData || [];
}

async function openLookup() {
    document.getElementById("lookupOverlay").style.display = "flex";
    const input = document.getElementById("lookupInput");
    input.placeholder = "Loading company list...";
    input.disabled = true;
    // Await scrips load so autocomplete works immediately on first keystroke
    await loadScrips();
    input.placeholder = "Type company name...";
    input.disabled = false;
    input.focus();
    // Check if we already have cached results to show
    const cached = sessionStorage.getItem("lookup_result");
    if (cached) {
        try { renderLookupResults(JSON.parse(cached)); } catch {}
    }
}

function closeLookup() {
    document.getElementById("lookupOverlay").style.display = "none";
    hideLookupSuggest();
    clearInterval(lookupPollTimer);
}

function onLookupInput() {
    const q = document.getElementById("lookupInput").value.trim().toLowerCase();
    document.getElementById("lookupFetchBtn").disabled = true;
    document.getElementById("researchBtn").disabled = true;
    lookupSelectedCompany = null;
    lookupSuggestIdx = -1;

    if (q.length < 2) { hideLookupSuggest(); return; }

    const companies = buildCompanyList();
    const matches = companies.filter(c => c.name.toLowerCase().includes(q)).slice(0, 10);

    if (!matches.length) { hideLookupSuggest(); return; }

    const suggest = document.getElementById("lookupSuggest");
    suggest.innerHTML = matches.map((c, i) =>
        `<div class="lookup-suggest-item" data-idx="${i}"
              onmousedown="selectLookupCompany(${i})"
              onmouseover="lookupSuggestIdx=${i};highlightSuggest()">
            <span class="lookup-suggest-name">${escapeHtml(c.name)}</span>
            <span class="lookup-suggest-code">BSE ${escapeHtml(c.scrip_code)}</span>
         </div>`
    ).join("");
    suggest._matches = matches;
    suggest.style.display = "block";
}

function onLookupKeyDown(e) {
    const suggest = document.getElementById("lookupSuggest");
    if (suggest.style.display === "none") {
        if (e.key === "Enter" && lookupSelectedCompany) triggerLookup();
        return;
    }
    const items = suggest.querySelectorAll(".lookup-suggest-item");
    if (e.key === "ArrowDown") {
        e.preventDefault();
        lookupSuggestIdx = Math.min(lookupSuggestIdx + 1, items.length - 1);
        highlightSuggest();
    } else if (e.key === "ArrowUp") {
        e.preventDefault();
        lookupSuggestIdx = Math.max(lookupSuggestIdx - 1, 0);
        highlightSuggest();
    } else if (e.key === "Enter") {
        e.preventDefault();
        if (lookupSuggestIdx >= 0) selectLookupCompany(lookupSuggestIdx);
        else if (lookupSelectedCompany) triggerLookup();
    } else if (e.key === "Escape") {
        hideLookupSuggest();
    }
}

function highlightSuggest() {
    const items = document.querySelectorAll(".lookup-suggest-item");
    items.forEach((el, i) => el.classList.toggle("active", i === lookupSuggestIdx));
}

function selectLookupCompany(idx) {
    const suggest = document.getElementById("lookupSuggest");
    const matches = suggest._matches || [];
    if (idx < 0 || idx >= matches.length) return;
    lookupSelectedCompany = matches[idx];
    document.getElementById("lookupInput").value = lookupSelectedCompany.name;
    document.getElementById("lookupFetchBtn").disabled = false;
    document.getElementById("researchBtn").disabled = false;
    hideLookupSuggest();
}

function hideLookupSuggest() {
    document.getElementById("lookupSuggest").style.display = "none";
}

async function triggerLookup() {
    if (!lookupSelectedCompany) return;

    const token = localStorage.getItem(GH_TOKEN_KEY);
    if (!token) {
        setLookupStatus("⚠️ GitHub token required. Set it via the Watchlist sync setup.", "error");
        return;
    }

    // Clear any previous results
    document.getElementById("lookupResults").style.display = "none";
    sessionStorage.removeItem("lookup_result");

    setLookupStatus(`⟳ Dispatching workflow for ${lookupSelectedCompany.name}...`, "loading");

    try {
        const resp = await fetch(
            `https://api.github.com/repos/${REPO}/actions/workflows/company-lookup.yml/dispatches`,
            {
                method: "POST",
                headers: {
                    Authorization: `token ${token}`,
                    Accept: "application/vnd.github.v3+json",
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    ref: "main",
                    inputs: {
                        company_name: lookupSelectedCompany.name,
                        scrip_code: lookupSelectedCompany.scrip_code,
                    },
                }),
            }
        );

        if (resp.status === 422) {
            setLookupStatus("⚠️ Token needs 'workflow' scope. Please regenerate your GitHub PAT with workflow permission.", "error");
            return;
        }
        if (!resp.ok) {
            setLookupStatus(`⚠️ Failed to trigger workflow (HTTP ${resp.status}). Check your GitHub token.`, "error");
            return;
        }
    } catch (e) {
        setLookupStatus(`⚠️ Network error: ${e.message}`, "error");
        return;
    }

    // Poll for result file (appears after workflow completes in ~1-2 min)
    const scrip = lookupSelectedCompany.scrip_code;
    const name = lookupSelectedCompany.name;
    let elapsed = 0;
    clearInterval(lookupPollTimer);

    setLookupStatus(`⟳ Workflow running... fetching 3 years of ${name} announcements. This takes about 1-2 minutes.`, "loading");

    lookupPollTimer = setInterval(async () => {
        elapsed += 10;
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
        setLookupStatus(`⟳ Fetching ${name} history... (${timeStr} elapsed)`, "loading");

        const data = await pollLookupResult(scrip);
        if (data) {
            clearInterval(lookupPollTimer);
            sessionStorage.setItem("lookup_result", JSON.stringify(data));
            renderLookupResults(data);
        } else if (elapsed > 300) {
            clearInterval(lookupPollTimer);
            setLookupStatus("⚠️ Timed out after 5 minutes. Check GitHub Actions for errors.", "error");
        }
    }, 10000);
}

async function pollLookupResult(scripCode) {
    try {
        const url = `https://raw.githubusercontent.com/${REPO}/main/data/lookup/${scripCode}.json?v=${Date.now()}`;
        const r = await fetch(url);
        if (r.ok) return await r.json();
    } catch {}
    return null;
}

function renderLookupResults(data) {
    document.getElementById("lookupStatus").style.display = "none";

    const title = document.getElementById("lookupResultTitle");
    const fromDate = data.from_date ? data.from_date.slice(0, 10) : "";
    const toDate = data.to_date ? data.to_date.slice(0, 10) : "";
    title.textContent = `${data.company} — ${data.total} announcements (${fromDate} to ${toDate})`;

    const body = document.getElementById("lookupBody");
    if (!data.announcements || !data.announcements.length) {
        body.innerHTML = `<tr><td colspan="4" style="text-align:center;padding:20px;color:#999">No important announcements found for this period.</td></tr>`;
    } else {
        body.innerHTML = data.announcements.map(a => {
            const dateStr = formatDisplayDate(a.date);
            const cat = a.category || "Other";
            const catClass = `cat-badge cat-${cat.toLowerCase().replace(/[^a-z]+/g, '-')}`;
            const subjectShort = a.subject && a.subject.length > 120
                ? a.subject.slice(0, 117) + "..." : (a.subject || "");
            const pdfLink = a.attachment
                ? `<a href="${escapeAttr(a.attachment)}" target="_blank" class="lookup-pdf-link">PDF</a>` : "—";
            return `<tr>
                <td class="lookup-date">${escapeHtml(dateStr)}</td>
                <td><span class="${catClass}">${escapeHtml(cat)}</span></td>
                <td class="lookup-subject" title="${escapeAttr(a.subject || '')}">${escapeHtml(subjectShort)}</td>
                <td>${pdfLink}</td>
            </tr>`;
        }).join("");
    }

    document.getElementById("lookupResults").style.display = "block";
}

function setLookupStatus(msg, type = "") {
    const el = document.getElementById("lookupStatus");
    el.style.display = "block";
    el.textContent = msg;
    el.className = `lookup-status ${type}`;
}

async function clearLookup() {
    clearInterval(lookupPollTimer);
    sessionStorage.removeItem("lookup_result");
    document.getElementById("lookupResults").style.display = "none";
    document.getElementById("lookupStatus").style.display = "none";
    document.getElementById("lookupInput").value = "";
    document.getElementById("lookupFetchBtn").disabled = true;
    document.getElementById("researchBtn").disabled = true;
    lookupSelectedCompany = null;

    // Delete the file from GitHub if we have a token and a scrip code
    const token = localStorage.getItem(GH_TOKEN_KEY);
    if (!token || !lookupSelectedCompany) return;

    try {
        const scrip = lookupSelectedCompany?.scrip_code;
        if (!scrip) return;
        const infoResp = await fetch(
            `https://api.github.com/repos/${REPO}/contents/data/lookup/${scrip}.json`,
            { headers: { Authorization: `token ${token}`, Accept: "application/vnd.github.v3+json" } }
        );
        if (!infoResp.ok) return;
        const info = await infoResp.json();
        await fetch(
            `https://api.github.com/repos/${REPO}/contents/data/lookup/${scrip}.json`,
            {
                method: "DELETE",
                headers: { Authorization: `token ${token}`, Accept: "application/vnd.github.v3+json", "Content-Type": "application/json" },
                body: JSON.stringify({ message: `Remove lookup: ${scrip}`, sha: info.sha }),
            }
        );
    } catch {}
}

// ─── Stock Research ───────────────────────────────────────────────────────────

async function triggerResearch() {
    if (!lookupSelectedCompany) return;

    const token = localStorage.getItem(GH_TOKEN_KEY);
    if (!token) {
        setLookupStatus("⚠️ GitHub token required. Set it via the Watchlist sync setup.", "error");
        return;
    }

    setLookupStatus(`⟳ Dispatching research workflow for ${lookupSelectedCompany.name}... This will take ~5-10 minutes.`, "loading");

    // Use NSE symbol from scrips.json (already stored in lookupSelectedCompany)
    const nseSymbol = lookupSelectedCompany.nse_symbol || "";

    try {
        const resp = await fetch(
            `https://api.github.com/repos/${REPO}/actions/workflows/stock-research.yml/dispatches`,
            {
                method: "POST",
                headers: {
                    Authorization: `token ${token}`,
                    Accept: "application/vnd.github.v3+json",
                    "Content-Type": "application/json",
                },
                body: JSON.stringify({
                    ref: "main",
                    inputs: {
                        company_name: lookupSelectedCompany.name,
                        scrip_code: lookupSelectedCompany.scrip_code,
                        nse_symbol: nseSymbol,
                    },
                }),
            }
        );
        if (resp.status === 422) {
            setLookupStatus("⚠️ Token needs 'workflow' scope. Please regenerate your GitHub PAT.", "error");
            return;
        }
        if (!resp.ok) {
            setLookupStatus(`⚠️ Failed to trigger workflow (HTTP ${resp.status}).`, "error");
            return;
        }
    } catch (e) {
        setLookupStatus(`⚠️ Network error: ${e.message}`, "error");
        return;
    }

    // Poll for .docx file
    const scrip = lookupSelectedCompany.scrip_code;
    const name = lookupSelectedCompany.name;
    let elapsed = 0;
    clearInterval(researchPollTimer);

    researchPollTimer = setInterval(async () => {
        elapsed += 15;
        const mins = Math.floor(elapsed / 60);
        const secs = elapsed % 60;
        const timeStr = mins > 0 ? `${mins}m ${secs}s` : `${secs}s`;
        setLookupStatus(`⟳ Generating deep research report for ${name}... (${timeStr} elapsed)`, "loading");

        const found = await pollResearchResult(scrip, name);
        if (found) {
            clearInterval(researchPollTimer);
        } else if (elapsed > 900) {
            clearInterval(researchPollTimer);
            setLookupStatus("⚠️ Timed out after 15 minutes. Check GitHub Actions for errors.", "error");
        }
    }, 15000);
}

async function pollResearchResult(scripCode, companyName) {
    // Search for any .docx file in data/research/ matching this scrip
    try {
        const url = `https://api.github.com/repos/${REPO}/contents/data/research`;
        const token = localStorage.getItem(GH_TOKEN_KEY);
        const r = await fetch(url, {
            headers: token ? { Authorization: `token ${token}` } : {}
        });
        if (!r.ok) return false;
        const files = await r.json();
        const match = files.find(f => f.name.includes(scripCode) && f.name.endsWith(".docx"));
        if (match) {
            showResearchDownload(match.download_url, companyName, match.name);
            return true;
        }
    } catch {}
    return false;
}

function showResearchDownload(downloadUrl, companyName, fileName) {
    const statusEl = document.getElementById("lookupStatus");
    statusEl.style.display = "block";
    statusEl.className = "lookup-status research-ready";
    statusEl.innerHTML = `✅ Research report ready for <strong>${escapeHtml(companyName)}</strong> —
        <a href="${escapeAttr(downloadUrl)}" download="${escapeAttr(fileName)}" class="research-download-link">⬇ Download .docx</a>
        <button class="research-clear-btn" onclick="clearResearch('${escapeAttr(fileName)}')">Remove</button>`;
}

async function clearResearch(scripCode) {
    clearInterval(researchPollTimer);
    const token = localStorage.getItem(GH_TOKEN_KEY);
    if (!token) return;
    try {
        // Find and delete the file
        const url = `https://api.github.com/repos/${REPO}/contents/data/research`;
        const r = await fetch(url, { headers: { Authorization: `token ${token}` } });
        if (!r.ok) return;
        const files = await r.json();
        const match = files.find(f => f.name.includes(scripCode) && f.name.endsWith(".docx"));
        if (!match) return;
        await fetch(
            `https://api.github.com/repos/${REPO}/contents/data/research/${match.name}`,
            {
                method: "DELETE",
                headers: { Authorization: `token ${token}`, "Content-Type": "application/json" },
                body: JSON.stringify({ message: `Remove research: ${match.name}`, sha: match.sha }),
            }
        );
    } catch {}
    document.getElementById("lookupStatus").style.display = "none";
}
