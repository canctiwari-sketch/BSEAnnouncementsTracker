let allAnnouncements = [];
let currentFiltered = [];
let currentSort = { col: "date", dir: "desc" };
let searchTimeout = null;
let currentPage = 1;
const PAGE_SIZE = 50;

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

function onMcapChange() {
    const allChecks = document.querySelectorAll("#mcapFilter .check-item input");
    const checks = document.querySelectorAll("#mcapFilter .check-item input:checked");
    const text = document.querySelector("#mcapFilter .multi-select-text");
    if (checks.length === 0 || checks.length === allChecks.length) {
        text.textContent = "All";
    } else {
        text.textContent = [...checks].map(c => {
            if (c.value === "large") return "Large";
            if (c.value === "mid") return "Mid";
            if (c.value === "small") return "Small";
            return "N/A";
        }).join(", ");
    }
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
    const mcapFilters = getSelectedValues("mcapFilter");
    const starOnly = document.getElementById("starFilter").checked;
    const dateFrom = document.getElementById("dateFrom").value;
    const dateTo = document.getElementById("dateTo").value;

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

    const allMcapChecks = document.querySelectorAll("#mcapFilter .check-item input").length;
    if (mcapFilters.length > 0 && mcapFilters.length < allMcapChecks) {
        filtered = filtered.filter(a => {
            const val = a.market_cap;
            const cr = val ? val / 1e7 : 0;
            const bucket = !val ? "na" : cr >= 20000 ? "large" : cr >= 5000 ? "mid" : "small";
            return mcapFilters.includes(bucket);
        });
    }

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
    document.querySelectorAll("#categoryDropdown input").forEach(c => c.checked = true);
    document.querySelectorAll("#mcapFilter .check-item input").forEach(c => c.checked = true);
    updateMultiSelectText("categoryFilter", "categoryDropdown");
    document.querySelector("#mcapFilter .multi-select-text").textContent = "All";
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
    tbody.innerHTML = announcements.map(renderRow).join("");
}

function renderRow(a) {
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
            ${starIcon}
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
