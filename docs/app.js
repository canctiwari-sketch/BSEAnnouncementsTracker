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
    document.getElementById("searchBox").addEventListener("input", () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(applyFilter, 300);
    });
    document.getElementById("insiderSearch").addEventListener("input", () => {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(applyInsiderFilter, 300);
    });
    document.addEventListener("keydown", (e) => {
        if (e.key === "Escape") { closeWatchlist(); hideLookupSuggest(); }
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
    const categoryBadge = category ? `<span class="category-badge" data-cat="${escapeAttr(category)}">${escapeHtml(category)}</span>` : "";

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
                const nseOnlyList = [];
                raw.forEach(s => {
                    const name = s.ScripName || s.IssuerName || "";
                    if (!s.ScripCode) {
                        // NSE-only / SME entries — include them directly
                        if (s.NSESymbol && name) {
                            nseOnlyList.push({ name, scrip_code: "", nse_symbol: s.NSESymbol });
                        }
                        return;
                    }
                    const nse = s.NSESymbol ||
                        nseSymbolByName.get(name.toLowerCase().trim()) ||
                        nseSymbolByName.get((s.IssuerName || "").toLowerCase().trim()) || "";
                    bseMap.set(String(s.ScripCode), { name, scrip_code: String(s.ScripCode), nse_symbol: nse });
                });
                scripsData = [...bseMap.values(), ...nseOnlyList]
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
    const list = scripsData ? [...scripsData] : [];
    // Also include NSE-listed companies from insider trades (covers NSE-only + SME)
    const seen = new Set(list.map(s => s.name.toLowerCase().trim()));
    (allInsiderTrades || []).forEach(t => {
        if (!t.nse_symbol || !t.company) return;
        const key = t.company.toLowerCase().trim();
        if (!seen.has(key)) {
            list.push({ name: t.company, scrip_code: "", nse_symbol: t.nse_symbol });
            seen.add(key);
        }
    });
    list.sort((a, b) => a.name.localeCompare(b.name));
    return list;
}

async function openLookup() {
    showTab('research');
}

function closeLookup() {
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
    suggest.innerHTML = matches.map((c, i) => {
        const codeLabel = c.scrip_code ? `BSE ${c.scrip_code}` : (c.nse_symbol ? `NSE ${c.nse_symbol}` : "");
        return `<div class="lookup-suggest-item" data-idx="${i}"
              onmousedown="selectLookupCompany(${i})"
              onmouseover="lookupSuggestIdx=${i};highlightSuggest()">
            <span class="lookup-suggest-name">${escapeHtml(c.name)}</span>
            <span class="lookup-suggest-code">${escapeHtml(codeLabel)}</span>
         </div>`;
    }).join("");
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

    if (!lookupSelectedCompany.scrip_code) {
        setLookupStatus("⚠️ This company has no BSE listing — history lookup requires a BSE scrip code. You can still use 🔬 Deep Research.", "error");
        return;
    }

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

    // Immediate first check (in case file already exists from prior run)
    const foundImmediate = await pollResearchResult(scrip, name);
    if (foundImmediate) return;

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
    // Strategy 1: direct HEAD check on expected filename (no caching/rate-limit issues)
    const expectedFilename = `${companyName.replace(/ /g, '_')}_${scripCode}_Analysis_Report.docx`;
    const rawUrl = `https://raw.githubusercontent.com/${REPO}/main/data/research/${encodeURIComponent(expectedFilename)}`;
    try {
        const r = await fetch(`${rawUrl}?t=${Date.now()}`, { method: 'HEAD', cache: 'no-store' });
        if (r.ok) {
            showResearchDownload(rawUrl, companyName, expectedFilename);
            return true;
        }
    } catch {}

    // Strategy 2: fall back to directory listing with cache-bust
    try {
        const url = `https://api.github.com/repos/${REPO}/contents/data/research?t=${Date.now()}`;
        const token = localStorage.getItem(GH_TOKEN_KEY);
        const headers = { 'Cache-Control': 'no-cache', 'Pragma': 'no-cache' };
        if (token) headers['Authorization'] = `token ${token}`;
        const r = await fetch(url, { headers, cache: 'no-store' });
        if (!r.ok) return false;
        const files = await r.json();
        if (!Array.isArray(files)) return false;
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
        const url = `https://api.github.com/repos/${REPO}/contents/data/research?t=${Date.now()}`;
        const r = await fetch(url, { headers: { Authorization: `token ${token}`, 'Cache-Control': 'no-cache' } });
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

// ═══════════════════════════════════════════════════════════════════════════
// INSIDER TRADES
// ═══════════════════════════════════════════════════════════════════════════

let allInsiderTrades = [];
let insiderFiltered = [];
let insiderSort = { col: "date", dir: "desc" };
let insiderPage = 1;
let aggSort = { col: "total_value", dir: "desc" };
const INSIDER_PAGE_SIZE = 100;
let insiderLoaded = false;

// ─── Tab ─────────────────────────────────────────────────────────────────────
function showTab(tab) {
    const TABS = { ann: "annTab", insider: "insiderTab", research: "researchTab" };
    const BTNS = { ann: "tabAnn", insider: "tabInsider", research: "tabResearch" };
    Object.keys(TABS).forEach(t => {
        document.getElementById(TABS[t]).style.display = t === tab ? "" : "none";
        document.getElementById(BTNS[t]).classList.toggle("tab-active", t === tab);
    });
    if (tab === "insider" && !insiderLoaded) fetchInsiderData();
    if (tab === "research") {
        loadScrips();
        setTimeout(() => {
            const inp = document.getElementById("lookupInput");
            if (inp && !inp.disabled) inp.focus();
        }, 80);
        const cached = sessionStorage.getItem("lookup_result");
        if (cached) { try { renderLookupResults(JSON.parse(cached)); } catch {} }
    }
}

// ─── Load ─────────────────────────────────────────────────────────────────────
async function fetchInsiderData() {
    setInsiderStatus("Loading insider trades...", "loading");
    try {
        const r = await fetch("https://raw.githubusercontent.com/canctiwari-sketch/BSEAnnouncementsTracker/main/data/insider.json?t=" + Date.now());
        if (!r.ok) throw new Error("HTTP " + r.status);
        const data = await r.json();
        allInsiderTrades = data.trades || [];
        insiderLoaded = true;
        const updated = data.last_updated ? new Date(data.last_updated + "Z") : null;
        const updStr = updated ? updated.toLocaleString("en-IN") : "unknown";
        setInsiderStatus(allInsiderTrades.length.toLocaleString() + " trades \u2014 Last updated: " + updStr);
        populateInsiderPeriods();
        populateInsiderModes();
        // Default: last 30 days via custom range
        const today = new Date();
        const d30 = new Date(today - 30 * 86400000);
        document.getElementById("insiderFrom").value = d30.toISOString().slice(0, 10);
        document.getElementById("insiderTo").value = today.toISOString().slice(0, 10);
        applyInsiderFilter();
    } catch (e) {
        setInsiderStatus("Error loading insider data: " + e.message, "error");
    }
}

function populateInsiderPeriods() {
    const sel = document.getElementById("insiderPeriod");
    sel.innerHTML = "<option value='custom'>Custom Range</option>";
    const months = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
    const today = new Date();
    // Add last 18 months
    for (let i = 0; i < 18; i++) {
        const d = new Date(today.getFullYear(), today.getMonth() - i, 1);
        const val = d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0");
        const label = months[d.getMonth()] + " " + d.getFullYear();
        sel.innerHTML += "<option value='month:" + val + "'>" + label + "</option>";
    }
    // Add last 3 years
    for (let y = today.getFullYear(); y >= today.getFullYear() - 2; y--) {
        sel.innerHTML += "<option value='year:" + y + "'>Year " + y + "</option>";
    }
}

function onInsiderPeriodChange() {
    const val = document.getElementById("insiderPeriod").value;
    const customRange = document.getElementById("insiderCustomRange");
    if (val === "custom") {
        customRange.style.display = "";
    } else {
        customRange.style.display = "none";
        // Auto-switch to Aggregate when month/year selected
        document.getElementById("insiderView").value = "aggregate";
    }
    applyInsiderFilter();
}

function populateInsiderModes() {
    const modes = [...new Set(allInsiderTrades.map(t => t.mode).filter(Boolean))].sort();
    const sel = document.getElementById("insiderMode");
    sel.innerHTML = "<option value=''>All</option>";
    modes.forEach(m => {
        const o = document.createElement("option");
        o.value = m; o.textContent = m; sel.appendChild(o);
    });
}

function setInsiderStatus(msg, type) {
    const el = document.getElementById("insiderStatus");
    el.textContent = msg;
    el.className = "status" + (type ? " status-" + type : "");
}

// ─── Filter ───────────────────────────────────────────────────────────────────
function applyInsiderFilter() {
    const period  = document.getElementById("insiderPeriod").value;
    let from, to;
    if (period === "custom") {
        from = document.getElementById("insiderFrom").value;
        to   = document.getElementById("insiderTo").value;
    } else if (period.startsWith("month:")) {
        const ym = period.slice(6); // "YYYY-MM"
        const [y, m] = ym.split("-").map(Number);
        from = ym + "-01";
        const lastDay = new Date(y, m, 0).getDate();
        to   = ym + "-" + String(lastDay).padStart(2, "0");
    } else if (period.startsWith("year:")) {
        const y = period.slice(5);
        from = y + "-01-01";
        to   = y + "-12-31";
    }

    const txn    = document.getElementById("insiderTxn").value;
    const cat    = document.getElementById("insiderCategory").value;
    const mode   = document.getElementById("insiderMode").value;
    const minVal = parseFloat(document.getElementById("insiderMinVal").value) || 0;
    const mcapMin = parseFloat(document.getElementById("insiderMcapMin").value) || 0;
    const mcapMax = parseFloat(document.getElementById("insiderMcapMax").value) || 0;
    const exch   = document.getElementById("insiderExchange").value;
    const q      = document.getElementById("insiderSearch").value.trim().toLowerCase();
    const view   = document.getElementById("insiderView").value;

    insiderFiltered = allInsiderTrades.filter(t => {
        if (from && t.date < from) return false;
        if (to   && t.date > to)   return false;
        if (txn  && t.txn_type !== txn) return false;
        if (cat  && t.category !== cat) return false;
        if (mode && t.mode !== mode) return false;
        if (minVal && (t.value_cr || 0) < minVal) return false;
        if (exch && t.exchange !== exch) return false;
        if (mcapMin || mcapMax) {
            const mc = t.market_cap ? t.market_cap / 1e7 : null;
            if (mc === null) return false; // exclude N/A when mcap filter is active
            if (mcapMin && mc < mcapMin) return false;
            if (mcapMax && mc > mcapMax) return false;
        }
        if (q) {
            const hay = ((t.company || "") + " " + (t.person || "") + " " + (t.nse_symbol || "") + " " + (t.scrip_code || "")).toLowerCase();
            if (!hay.includes(q)) return false;
        }
        return true;
    });

    if (view === "aggregate") {
        document.getElementById("insiderResults").style.display = "none";
        document.getElementById("insiderPagination").style.display = "none";
        document.getElementById("insiderAggResults").style.display = "";
        renderAggregateTable();
        setInsiderStatus(insiderFiltered.length.toLocaleString() + " trades aggregated");
    } else {
        document.getElementById("insiderAggResults").style.display = "none";
        document.getElementById("insiderResults").style.display = "";
        insiderFiltered.sort((a, b) => {
            let av = a[insiderSort.col] != null ? a[insiderSort.col] : "";
            let bv = b[insiderSort.col] != null ? b[insiderSort.col] : "";
            if (typeof av === "string") av = av.toLowerCase();
            if (typeof bv === "string") bv = bv.toLowerCase();
            if (av < bv) return insiderSort.dir === "asc" ? -1 : 1;
            if (av > bv) return insiderSort.dir === "asc" ? 1 : -1;
            return 0;
        });
        insiderPage = 1;
        renderInsiderTable();
        setInsiderStatus(insiderFiltered.length.toLocaleString() + " trades shown");
    }
}

function clearInsiderFilters() {
    document.getElementById("insiderPeriod").value = "custom";
    document.getElementById("insiderCustomRange").style.display = "";
    document.getElementById("insiderView").value = "trades";
    const today = new Date();
    const d30 = new Date(today - 30 * 86400000);
    document.getElementById("insiderFrom").value = d30.toISOString().slice(0, 10);
    document.getElementById("insiderTo").value = today.toISOString().slice(0, 10);
    ["insiderTxn","insiderCategory","insiderMode","insiderExchange"].forEach(id => document.getElementById(id).value = "");
    ["insiderMinVal","insiderMcapMin","insiderMcapMax","insiderSearch"].forEach(id => document.getElementById(id).value = "");
    applyInsiderFilter();
}

// ─── Sort (Trades) ────────────────────────────────────────────────────────────
function sortInsiderBy(col) {
    insiderSort.dir = insiderSort.col === col && insiderSort.dir === "desc" ? "asc" : "desc";
    insiderSort.col = col;
    document.querySelectorAll(".sort-arrow[data-icol]").forEach(el => {
        el.textContent = el.dataset.icol === col ? (insiderSort.dir === "asc" ? " \u25b2" : " \u25bc") : "";
    });
    insiderPage = 1;
    applyInsiderFilter();
}

// ─── Sort (Aggregate) ─────────────────────────────────────────────────────────
function sortAggBy(col) {
    aggSort.dir = aggSort.col === col && aggSort.dir === "desc" ? "asc" : "desc";
    aggSort.col = col;
    renderAggregateTable();
}

// ─── Render Trades ────────────────────────────────────────────────────────────
function renderInsiderTable() {
    const tbody = document.getElementById("insiderBody");
    const total = insiderFiltered.length;
    const totalPages = Math.max(1, Math.ceil(total / INSIDER_PAGE_SIZE));
    insiderPage = Math.min(insiderPage, totalPages);
    const start = (insiderPage - 1) * INSIDER_PAGE_SIZE;
    const items = insiderFiltered.slice(start, start + INSIDER_PAGE_SIZE);

    if (!items.length) {
        tbody.innerHTML = "<tr><td colspan='12' style='text-align:center;padding:32px;color:#888'>No trades found</td></tr>";
        document.getElementById("insiderPagination").style.display = "none";
        return;
    }

    tbody.innerHTML = items.map(t => {
        const txnCls = t.txn_type === "Buy" ? "it-buy" : t.txn_type === "Sell" ? "it-sell" : "it-other";
        const catCls = {Promoter:"it-promoter",Director:"it-director",KMP:"it-kmp"}[t.category] || "it-other-cat";
        const qty   = t.qty   ? t.qty.toLocaleString("en-IN") : "\u2014";
        const price = t.price ? "\u20b9" + t.price.toLocaleString("en-IN", {maximumFractionDigits:2}) : "\u2014";
        const val   = t.value_cr ? t.value_cr.toLocaleString("en-IN", {maximumFractionDigits:2}) : "\u2014";
        const bpct  = t.before_pct ? t.before_pct + "%" : "\u2014";
        const apct  = t.after_pct  ? t.after_pct  + "%" : "\u2014";
        const sym   = t.nse_symbol ? "<span class='ann-exch nse'>" + escapeHtml(t.nse_symbol) + "</span>"
                    : t.scrip_code ? "<span class='ann-exch bse'>" + escapeHtml(t.scrip_code) + "</span>" : "";
        const exBadge = "<span class='ann-exch " + t.exchange.toLowerCase() + "'>" + t.exchange + "</span>";
        const mcapCr = t.market_cap ? t.market_cap / 1e7 : null;
        const mcapCls = !mcapCr ? "mcap-na" : mcapCr >= 20000 ? "mcap-large" : mcapCr >= 5000 ? "mcap-mid" : "mcap-small";
        const mcapTxt = t.market_cap_fmt || "N/A";
        return "<tr>"
            + "<td style='white-space:nowrap'>" + escapeHtml(t.date) + "</td>"
            + "<td><a href='" + screenerLink(t.company) + "' target='_blank' class='company-link'><strong>" + escapeHtml(t.company) + "</strong></a><br><small>" + sym + exBadge + "</small></td>"
            + "<td class='" + mcapCls + "' style='text-align:right;font-weight:500'>" + mcapTxt + "</td>"
            + "<td>" + escapeHtml(t.person) + "</td>"
            + "<td><span class='it-badge " + catCls + "'>" + escapeHtml(t.category) + "</span></td>"
            + "<td><span class='it-badge " + txnCls + "'>" + escapeHtml(t.txn_type) + "</span></td>"
            + "<td><small>" + escapeHtml(t.mode || "\u2014") + "</small></td>"
            + "<td style='text-align:right'>" + qty + "</td>"
            + "<td style='text-align:right'>" + price + "</td>"
            + "<td style='text-align:right;font-weight:600'>" + val + "</td>"
            + "<td style='text-align:right;color:#888'>" + bpct + "</td>"
            + "<td style='text-align:right;color:#888'>" + apct + "</td>"
            + "</tr>";
    }).join("");

    const pg = document.getElementById("insiderPagination");
    if (totalPages > 1) {
        pg.style.display = "flex";
        document.getElementById("insiderPageNum").textContent = "Page " + insiderPage + " of " + totalPages;
        document.getElementById("insiderPrevBtn").disabled = insiderPage <= 1;
        document.getElementById("insiderNextBtn").disabled = insiderPage >= totalPages;
        document.getElementById("insiderPageInfo").textContent =
            (start + 1) + "\u2013" + Math.min(start + INSIDER_PAGE_SIZE, total) + " of " + total.toLocaleString();
    } else {
        pg.style.display = "none";
    }
}

// ─── Aggregate helpers ────────────────────────────────────────────────────────
function _aggSort(rows) {
    const col = aggSort.col;
    const dir = aggSort.dir;
    return rows.sort((a, b) => {
        let av = a[col] != null ? a[col] : (typeof a[col] === "string" ? "" : 0);
        let bv = b[col] != null ? b[col] : (typeof b[col] === "string" ? "" : 0);
        if (typeof av === "string") { av = av.toLowerCase(); bv = (bv + "").toLowerCase(); }
        if (av < bv) return dir === "asc" ? -1 : 1;
        if (av > bv) return dir === "asc" ? 1 : -1;
        return 0;
    });
}

function _aggTh(col, label, align) {
    const arrow = aggSort.col === col ? (aggSort.dir === "asc" ? " \u25b2" : " \u25bc") : "";
    const st = align ? ` style="text-align:${align}"` : "";
    return `<th class="sortable"${st} onclick="sortAggBy('${col}')">${label}${arrow}</th>`;
}

function _aggCompanyCell(r) {
    const sym = r.nse_symbol ? `<span class='ann-exch nse'>${escapeHtml(r.nse_symbol)}</span>`
              : r.scrip_code ? `<span class='ann-exch bse'>${escapeHtml(r.scrip_code)}</span>` : "";
    return `<td><a href='${screenerLink(r.company)}' target='_blank' class='company-link'><strong>${escapeHtml(r.company)}</strong></a><br><small>${sym}</small></td>`;
}

function _aggMcapCell(r) {
    const cr = r.market_cap ? r.market_cap / 1e7 : null;
    const cls = !cr ? "mcap-na" : cr >= 20000 ? "mcap-large" : cr >= 5000 ? "mcap-mid" : "mcap-small";
    return `<td class="${cls}" style="text-align:right;font-weight:500">${r.market_cap_fmt || "N/A"}</td>`;
}

// ─── Render Aggregate ─────────────────────────────────────────────────────────
function _normCompany(name) {
    // Group "Limited"/"Ltd"/"Pvt" etc as same company across exchanges
    return (name || "")
        .toLowerCase()
        .replace(/\s+/g, " ")
        .replace(/[.,]/g, "")
        .replace(/\b(limited|ltd|pvt|private|corporation|corp|inc|co)\b/g, "")
        .replace(/\s+/g, " ")
        .trim();
}

function renderAggregateTable() {
    const isMarket = t => /^market/i.test(t.mode || "");
    const isPref   = t => /preferential|allotment/i.test(t.mode || "");

    // ── Section 1: Market Net (buy - sell per company) ──
    const mMap = {};
    insiderFiltered.filter(isMarket).forEach(t => {
        const k = _normCompany(t.company);
        if (!mMap[k]) mMap[k] = {
            company: t.company, market_cap: null, market_cap_fmt: "N/A",
            nse_symbol: t.nse_symbol, scrip_code: t.scrip_code,
            buy_qty: 0, sell_qty: 0, buy_value: 0, sell_value: 0,
            buy_rs: 0, buy_trades: 0, sell_trades: 0,
        };
        const r = mMap[k];
        // Prefer the version with market cap (NSE often has it, BSE sometimes doesn't)
        if (t.market_cap && !r.market_cap) {
            r.market_cap = t.market_cap;
            r.market_cap_fmt = t.market_cap_fmt;
            r.company = t.company;  // also use the better-named version
            r.nse_symbol = t.nse_symbol || r.nse_symbol;
            r.scrip_code = t.scrip_code || r.scrip_code;
        }
        if ((t.txn_type || "").toLowerCase() === "sell") {
            r.sell_qty += t.qty || 0; r.sell_value += t.value_cr || 0; r.sell_trades++;
        } else {
            r.buy_qty += t.qty || 0; r.buy_value += t.value_cr || 0;
            r.buy_rs += (t.price || 0) * (t.qty || 0); r.buy_trades++;
        }
    });
    let mRows = _aggSort(Object.values(mMap).map(r => ({
        ...r,
        net_qty:   r.buy_qty - r.sell_qty,
        net_value: +((r.buy_value - r.sell_value).toFixed(2)),
        avg_price: r.buy_qty > 0 ? Math.round(r.buy_rs / r.buy_qty) : 0,
        trades:    r.buy_trades + r.sell_trades,
        // expose for sort keys used by agg
        total_value: +((r.buy_value - r.sell_value).toFixed(2)),
        total_qty:   r.buy_qty - r.sell_qty,
        market_cap:  r.market_cap || 0,
    })));

    // ── Section 2: Preferential per company ──
    const pMap = {};
    insiderFiltered.filter(isPref).forEach(t => {
        const k = _normCompany(t.company);
        if (!pMap[k]) pMap[k] = {
            company: t.company, market_cap: null, market_cap_fmt: "N/A",
            nse_symbol: t.nse_symbol, scrip_code: t.scrip_code,
            trades: 0, total_qty: 0, total_value: 0, total_rs: 0,
        };
        const r = pMap[k];
        if (t.market_cap && !r.market_cap) {
            r.market_cap = t.market_cap;
            r.market_cap_fmt = t.market_cap_fmt;
            r.company = t.company;
            r.nse_symbol = t.nse_symbol || r.nse_symbol;
            r.scrip_code = t.scrip_code || r.scrip_code;
        }
        r.trades++; r.total_qty += t.qty || 0; r.total_value += t.value_cr || 0;
        r.total_rs += (t.price || 0) * (t.qty || 0);
    });
    let pRows = _aggSort(Object.values(pMap).map(r => ({
        ...r,
        avg_price: r.total_qty > 0 ? Math.round(r.total_rs / r.total_qty) : 0,
        market_cap: r.market_cap || 0,
    })));

    const container = document.getElementById("insiderAggResults");
    container.innerHTML = _buildMarketSection(mRows) + _buildPrefSection(pRows);
}

function _buildMarketSection(rows) {
    let html = `<div class="agg-section">
        <div class="agg-section-title">📈 Market Transactions &mdash; Net Position</div>
        <table class="agg-table"><thead><tr>
            ${_aggTh("company","Company")}
            ${_aggTh("market_cap","MCap","right")}
            <th style="text-align:right">Buy Qty</th>
            <th style="text-align:right">Sell Qty</th>
            ${_aggTh("net_qty","Net Qty","right")}
            ${_aggTh("avg_price","Avg Buy Price","right")}
            ${_aggTh("net_value","Net Value (Cr)","right")}
        </tr></thead><tbody>`;

    if (!rows.length) {
        html += `<tr><td colspan="7" style="text-align:center;padding:24px;color:#aaa">No market transactions in this period</td></tr>`;
    } else {
        rows.forEach(r => {
            const nqCls = r.net_qty >= 0 ? "agg-pos" : "agg-neg";
            const nvCls = r.net_value >= 0 ? "agg-pos" : "agg-neg";
            const nqStr = (r.net_qty >= 0 ? "+" : "") + r.net_qty.toLocaleString("en-IN");
            const nvStr = (r.net_value >= 0 ? "+" : "") + r.net_value.toLocaleString("en-IN", {maximumFractionDigits:2});
            const avgStr = r.avg_price > 0 ? "\u20b9" + r.avg_price.toLocaleString("en-IN") : "\u2014";
            html += "<tr>"
                + _aggCompanyCell(r) + _aggMcapCell(r)
                + `<td style="text-align:right;color:#555">${r.buy_qty.toLocaleString("en-IN")}</td>`
                + `<td style="text-align:right;color:#555">${r.sell_qty.toLocaleString("en-IN")}</td>`
                + `<td class="${nqCls}" style="text-align:right;font-weight:600">${nqStr}</td>`
                + `<td style="text-align:right">${avgStr}</td>`
                + `<td class="${nvCls}" style="text-align:right;font-weight:700">${nvStr}</td>`
                + "</tr>";
        });
    }
    return html + "</tbody></table></div>";
}

function _buildPrefSection(rows) {
    let html = `<div class="agg-section">
        <div class="agg-section-title">🏦 Preferential Allotments</div>
        <table class="agg-table"><thead><tr>
            ${_aggTh("company","Company")}
            ${_aggTh("market_cap","MCap","right")}
            <th style="text-align:right">Trades</th>
            ${_aggTh("total_qty","Total Qty","right")}
            ${_aggTh("avg_price","Avg Price","right")}
            ${_aggTh("total_value","Total Value (Cr)","right")}
        </tr></thead><tbody>`;

    if (!rows.length) {
        html += `<tr><td colspan="6" style="text-align:center;padding:24px;color:#aaa">No preferential allotments in this period</td></tr>`;
    } else {
        rows.forEach(r => {
            const avgStr = r.avg_price > 0 ? "\u20b9" + r.avg_price.toLocaleString("en-IN") : "\u2014";
            html += "<tr>"
                + _aggCompanyCell(r) + _aggMcapCell(r)
                + `<td style="text-align:right">${r.trades}</td>`
                + `<td style="text-align:right">${r.total_qty.toLocaleString("en-IN")}</td>`
                + `<td style="text-align:right">${avgStr}</td>`
                + `<td style="text-align:right;font-weight:700">${r.total_value.toLocaleString("en-IN", {maximumFractionDigits:2})}</td>`
                + "</tr>";
        });
    }
    return html + "</tbody></table></div>";
}

function insiderPrevPage() { if (insiderPage > 1) { insiderPage--; renderInsiderTable(); window.scrollTo(0,0); } }
function insiderNextPage() { insiderPage++; renderInsiderTable(); window.scrollTo(0,0); }

// ─── Export ───────────────────────────────────────────────────────────────────
function exportInsiderXLSX() {
    const view = document.getElementById("insiderView").value;
    if (view === "aggregate") {
        const isMarket = t => /^market/i.test(t.mode || "");
        const isPref   = t => /preferential|allotment/i.test(t.mode || "");

        // Market Net sheet
        const mMap = {};
        insiderFiltered.filter(isMarket).forEach(t => {
            const k = t.company || "";
            if (!mMap[k]) mMap[k] = { Company: t.company, MCap: t.market_cap_fmt||"N/A", "Buy Qty":0, "Sell Qty":0, "Net Qty":0, "Avg Buy Price":0, _buy_rs:0, "Net Value (Cr)":0 };
            const r = mMap[k];
            if ((t.txn_type||"").toLowerCase()==="sell") { r["Sell Qty"]+=t.qty||0; r["Net Value (Cr)"]-=t.value_cr||0; }
            else { r["Buy Qty"]+=t.qty||0; r["Net Value (Cr)"]+=t.value_cr||0; r._buy_rs+=(t.price||0)*(t.qty||0); }
        });
        const mRows = Object.values(mMap).map(r => {
            r["Net Qty"] = r["Buy Qty"] - r["Sell Qty"];
            r["Avg Buy Price"] = r["Buy Qty"] > 0 ? Math.round(r._buy_rs / r["Buy Qty"]) : 0;
            r["Net Value (Cr)"] = Math.round(r["Net Value (Cr)"] * 100) / 100;
            delete r._buy_rs; return r;
        });

        // Preferential sheet
        const pMap = {};
        insiderFiltered.filter(isPref).forEach(t => {
            const k = t.company || "";
            if (!pMap[k]) pMap[k] = { Company: t.company, MCap: t.market_cap_fmt||"N/A", Trades:0, "Total Qty":0, "Avg Price":0, _rs:0, "Total Value (Cr)":0 };
            const r = pMap[k]; r.Trades++; r["Total Qty"]+=t.qty||0; r["Total Value (Cr)"]+=t.value_cr||0; r._rs+=(t.price||0)*(t.qty||0);
        });
        const pRows = Object.values(pMap).map(r => {
            r["Avg Price"] = r["Total Qty"]>0 ? Math.round(r._rs/r["Total Qty"]) : 0;
            r["Total Value (Cr)"] = Math.round(r["Total Value (Cr)"]*100)/100;
            delete r._rs; return r;
        });

        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(mRows), "Market Net");
        XLSX.utils.book_append_sheet(wb, XLSX.utils.json_to_sheet(pRows), "Preferential");
        XLSX.writeFile(wb, "insider_aggregate_" + new Date().toISOString().slice(0,10) + ".xlsx");
    } else {
        if (!insiderFiltered.length) return;
        const rows = insiderFiltered.map(t => ({
            Date: t.date, Company: t.company, MCap: t.market_cap_fmt, "Scrip/Symbol": t.nse_symbol || t.scrip_code,
            Exchange: t.exchange, Person: t.person, Category: t.category,
            "Txn Type": t.txn_type, Mode: t.mode, Quantity: t.qty,
            "Avg Price": t.price, "Value (Cr)": t.value_cr,
            "Before %": t.before_pct, "After %": t.after_pct,
        }));
        const ws = XLSX.utils.json_to_sheet(rows);
        const wb = XLSX.utils.book_new();
        XLSX.utils.book_append_sheet(wb, ws, "Insider Trades");
        XLSX.writeFile(wb, "insider_trades_" + new Date().toISOString().slice(0,10) + ".xlsx");
    }
}
