let allAnnouncements = [];

// High-priority categories
const STARRED_CATEGORIES = new Set([
    "Business Expansion", "Fund Raising", "Capital Structure", "Acquisition",
]);
const STARRED_RE = /capex|capital expenditure|expansion|warrant|raising.*capital|raise.*fund/i;

document.addEventListener("DOMContentLoaded", () => {
    document.getElementById("searchBox").addEventListener("input", applyFilter);
    fetchData();
});

async function fetchData() {
    setStatus("Loading announcements...", "loading");
    try {
        // Use raw GitHub URL to fetch data from the data/ folder
        const repo = "canctiwari-sketch/BSEAnnouncementsTracker";
        const r = await fetch(`https://raw.githubusercontent.com/${repo}/main/data/announcements.json?${Date.now()}`);
        if (!r.ok) throw new Error(`HTTP ${r.status}`);
        const data = await r.json();
        allAnnouncements = data.announcements || [];

        const updated = data.last_updated
            ? new Date(data.last_updated + "Z").toLocaleString("en-IN")
            : "unknown";
        setStatus(`${allAnnouncements.length} announcements \u2014 Last updated: ${updated}`);

        populateCategoryFilter();
        applyFilter();
    } catch (e) {
        setStatus(`Error loading data: ${e.message}`, "error");
    }
}

function setStatus(msg, type = "") {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "status " + type;
}

function isStarred(a) {
    if (a.starred) return true;
    if (STARRED_CATEGORIES.has(a.category || "")) return true;
    const text = a.subject || "";
    if (STARRED_RE.test(text)) return true;
    return false;
}

function populateCategoryFilter() {
    const dropdown = document.getElementById("categoryDropdown");
    const categories = [...new Set(allAnnouncements.map(a => a.category || "").filter(Boolean))].sort();
    dropdown.innerHTML = "";
    categories.forEach(cat => {
        const label = document.createElement("label");
        label.className = "check-item";
        label.innerHTML = `<input type="checkbox" value="${escapeAttr(cat)}" checked onchange="onCategoryChange()"> ${escapeHtml(cat)}`;
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

function applyFilter() {
    const query = document.getElementById("searchBox").value.toLowerCase().trim();
    const catFilters = getSelectedValues("categoryFilter");
    const mcapFilters = getSelectedValues("mcapFilter");
    const starOnly = document.getElementById("starFilter").checked;

    let filtered = allAnnouncements;

    if (starOnly) {
        filtered = filtered.filter(a => isStarred(a));
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

    // Update count
    const total = allAnnouncements.length;
    const shown = filtered.length;
    const statusEl = document.getElementById("status");
    const base = statusEl.textContent.split(" | Showing")[0];
    statusEl.textContent = shown < total ? `${base} | Showing ${shown} of ${total}` : base;

    renderTable(filtered);
}

function renderTable(announcements) {
    const tbody = document.getElementById("annBody");
    if (!announcements.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center;padding:20px;color:#484f58">No announcements found.</td></tr>';
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

    // Show AI summary if available, otherwise show detail text
    const detail = a.detail || "";
    let summaryHtml = "";
    if (aiSummary) {
        summaryHtml = `<div class="summary-text ai-summary">${escapeHtml(aiSummary)}</div>`;
    } else if (detail) {
        const short = detail.length > 150 ? detail.slice(0, 147) + "..." : detail;
        summaryHtml = `<div class="summary-text">${escapeHtml(short)}</div>`;
    }

    return `<tr class="${rowClass}">
        <td class="company-cell">
            ${starIcon}
            <a class="company-name" href="${screenerLink(name)}" target="_blank" rel="noopener">${escapeHtml(name)}</a>
            <div class="scrip-code">${exchangeBadge} ${escapeHtml(symbol)}</div>
        </td>
        <td class="mcap-cell ${mcapClass}">${mcapFmt}</td>
        <td class="subject-cell">
            ${categoryBadge}
            ${summaryHtml}
        </td>
        <td class="date-cell">${escapeHtml(date)}</td>
        <td>${attachmentLink}</td>
    </tr>`;
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
