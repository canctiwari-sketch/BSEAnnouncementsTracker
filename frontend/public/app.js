let allAnnouncements = [];
let currentPage = 1;
let currentSource = "both";

document.addEventListener("DOMContentLoaded", () => {
    const today = new Date();
    const daysAgo = new Date(today);
    daysAgo.setDate(today.getDate() - 1);

    document.getElementById("toDate").value = formatDate(today);
    document.getElementById("fromDate").value = formatDate(daysAgo);
    document.getElementById("searchBox").addEventListener("input", applyFilter);
    document.getElementById("source").addEventListener("change", () => {
        currentPage = 1;
        fetchAnnouncements();
    });

    fetchAnnouncements();
});

function formatDate(d) {
    return d.toISOString().split("T")[0];
}

function setStatus(msg, type = "") {
    const el = document.getElementById("status");
    el.textContent = msg;
    el.className = "status " + type;
}

async function fetchAnnouncements() {
    const fromDate = document.getElementById("fromDate").value;
    const toDate = document.getElementById("toDate").value;
    currentSource = document.getElementById("source").value;

    if (!fromDate || !toDate) {
        setStatus("Please select both dates.", "error");
        return;
    }

    setStatus("Fetching announcements...", "loading");
    document.getElementById("fetchBtn").disabled = true;

    let endpoint;
    if (currentSource === "both") endpoint = "/api/all-announcements";
    else if (currentSource === "nse") endpoint = "/api/nse-announcements";
    else endpoint = "/api/announcements";

    try {
        const params = new URLSearchParams({ from_date: fromDate, to_date: toDate });
        if (currentSource === "bse") params.set("page", currentPage);

        const resp = await fetch(`${endpoint}?${params}`);
        if (!resp.ok) {
            const err = await resp.json();
            throw new Error(err.error || `HTTP ${resp.status}`);
        }

        const data = await resp.json();
        allAnnouncements = data.announcements || [];

        let statusMsg = `${data.count} announcements`;
        if (data.page) statusMsg += ` (page ${data.page})`;
        if (data.bse_count != null) statusMsg += ` — BSE: ${data.bse_count}, NSE: ${data.nse_count}`;
        if (data.errors) statusMsg += ` | ${data.errors.join("; ")}`;
        setStatus(statusMsg);

        populateCategoryFilter();
        applyFilter();
        updatePagination(data);
    } catch (e) {
        setStatus(`Error: ${e.message}`, "error");
    } finally {
        document.getElementById("fetchBtn").disabled = false;
    }
}

function populateCategoryFilter() {
    const dropdown = document.getElementById("categoryDropdown");
    const categories = [...new Set(allAnnouncements.map(a => getCategory(a)).filter(Boolean))].sort();

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

// High-priority categories that get a star
const STARRED_CATEGORIES = new Set([
    "Business Expansion",
    "Fund Raising",
    "Capital Structure",
    "Acquisition",
]);

function isStarred(a) {
    const cat = a.category || "";
    if (STARRED_CATEGORIES.has(cat)) return true;
    // Also check keywords in subject for capex/warrant mentions in other categories
    const text = (a.subject || a.NEWSSUB || a.desc || "").toLowerCase();
    if (/capex|capital expenditure|expansion|warrant|raising.*capital|raise.*fund/.test(text)) return true;
    return false;
}

function getCategory(a) {
    return a.category || "";
}

function getMcapValue(a) {
    return a.market_cap || null;
}

function getMcapClass(value) {
    if (!value) return "na";
    const cr = value / 1e7;
    if (cr >= 20000) return "large-cap";
    if (cr >= 5000) return "mid-cap";
    return "small-cap";
}

function getMcapDisplay(a) {
    return a.market_cap_fmt || "N/A";
}

function screenerLink(companyName) {
    const q = encodeURIComponent(companyName + " screener.in");
    return `https://www.google.com/search?q=${q}`;
}

function applyFilter() {
    const query = document.getElementById("searchBox").value.toLowerCase().trim();
    const catFilters = getSelectedValues("categoryFilter");
    const mcapFilters = getSelectedValues("mcapFilter");
    const starOnly = document.getElementById("starFilter").checked;

    let filtered = allAnnouncements;

    // Star filter
    if (starOnly) {
        filtered = filtered.filter(a => isStarred(a));
    }

    // Category filter (multi-select) — all checked = no filter
    const allCatChecks = document.querySelectorAll("#categoryDropdown input").length;
    if (catFilters.length > 0 && catFilters.length < allCatChecks) {
        filtered = filtered.filter(a => catFilters.includes(getCategory(a)));
    }

    // Market cap filter (multi-select) — all checked = no filter
    const allMcapChecks = document.querySelectorAll("#mcapFilter .check-item input").length;
    if (mcapFilters.length > 0 && mcapFilters.length < allMcapChecks) {
        filtered = filtered.filter(a => {
            const val = getMcapValue(a);
            const cr = val ? val / 1e7 : 0;
            const bucket = !val ? "na" : cr >= 20000 ? "large" : cr >= 5000 ? "mid" : "small";
            return mcapFilters.includes(bucket);
        });
    }

    // Text search
    if (query) {
        filtered = filtered.filter(a => {
            const name = (a.company || a.SLONGNAME || a.sm_name || "").toLowerCase();
            const symbol = (a.symbol || String(a.SCRIP_CD || "")).toLowerCase();
            const subject = (a.subject || a.NEWSSUB || a.desc || "").toLowerCase();
            const detail = (a.detail || a.attchmntText || a.HEADLINE || "").toLowerCase();
            const category = (a.category || "").toLowerCase();
            const summary = (a.summary || "").toLowerCase();
            const exchange = (a.exchange || "").toLowerCase();
            return name.includes(query) || symbol.includes(query) ||
                   subject.includes(query) || detail.includes(query) ||
                   category.includes(query) || summary.includes(query) ||
                   exchange.includes(query);
        });
    }

    // Update visible count
    const total = allAnnouncements.length;
    const shown = filtered.length;
    const statusEl = document.getElementById("status");
    const existing = statusEl.textContent.split(" | Showing")[0];
    if (shown < total) {
        statusEl.textContent = `${existing} | Showing ${shown} of ${total}`;
    } else {
        statusEl.textContent = existing;
    }

    renderTable(filtered);
}

function renderTable(announcements) {
    const tbody = document.getElementById("annBody");

    if (!announcements.length) {
        tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:20px; color:#484f58">No announcements found.</td></tr>';
        return;
    }

    tbody.innerHTML = announcements.map(a => {
        if (currentSource === "both") return renderUnifiedRow(a);
        if (currentSource === "bse") return renderBseRow(a);
        return renderNseRow(a);
    }).join("");
}

function renderUnifiedRow(a) {
    const name = a.company || "Unknown";
    const symbol = a.symbol || "";
    const exchange = a.exchange || "";
    const mcapFmt = a.market_cap_fmt || "N/A";
    const mcapClass = getMcapClass(a.market_cap);
    const category = a.category || "";
    const summary = a.summary || "";
    const date = a.date || "";
    const attachment = a.attachment || "";

    const exchangeBadge = `<span class="exchange-badge ${exchange.toLowerCase()}">${exchange}</span>`;

    const attachmentLink = attachment
        ? `<a class="attachment-link" href="${escapeAttr(attachment)}" target="_blank" rel="noopener">PDF</a>`
        : "-";

    const displayDate = formatDisplayDate(date);

    const categoryBadge = category
        ? `<span class="category-badge">${escapeHtml(category)}</span>`
        : "";

    const summaryHtml = summary
        ? `<div class="summary-text">${escapeHtml(summary)}</div>`
        : "";

    const starred = isStarred(a);
    const starIcon = starred ? `<span class="star-icon" title="High Priority">&#9733;</span>` : "";
    const rowClass = starred ? "starred-row" : "";

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
        <td class="date-cell">${escapeHtml(displayDate)}</td>
        <td>${attachmentLink}</td>
    </tr>`;
}

function renderBseRow(a) {
    const name = a.SLONGNAME || "Unknown";
    const scrip = a.SCRIP_CD || "";
    const mcapFmt = a.market_cap_fmt || "N/A";
    const mcapClass = getMcapClass(a.market_cap);
    const category = a.category || "";
    const summary = a.summary || "";
    const date = a.NEWS_DT || "";
    const attachment = a.ATTACHMENTNAME || "";

    const attachmentLink = attachment
        ? `<a class="attachment-link" href="https://www.bseindia.com/xml-data/corpfiling/AttachLive/${escapeAttr(attachment)}" target="_blank" rel="noopener">PDF</a>`
        : "-";

    const displayDate = formatDisplayDate(date);

    const starred = isStarred(a);
    const starIcon = starred ? `<span class="star-icon" title="High Priority">&#9733;</span>` : "";
    const rowClass = starred ? "starred-row" : "";

    return `<tr class="${rowClass}">
        <td class="company-cell">
            ${starIcon}
            <a class="company-name" href="${screenerLink(name)}" target="_blank" rel="noopener">${escapeHtml(name)}</a>
            <div class="scrip-code"><span class="exchange-badge bse">BSE</span> ${escapeHtml(String(scrip))}</div>
        </td>
        <td class="mcap-cell ${mcapClass}">${mcapFmt}</td>
        <td class="subject-cell">
            ${category ? `<span class="category-badge">${escapeHtml(category)}</span>` : ""}
            ${summary ? `<div class="summary-text">${escapeHtml(summary)}</div>` : ""}
        </td>
        <td class="date-cell">${escapeHtml(displayDate)}</td>
        <td>${attachmentLink}</td>
    </tr>`;
}

function renderNseRow(a) {
    const name = a.sm_name || "Unknown";
    const symbol = a.symbol || "";
    const mcapFmt = a.market_cap_fmt || "N/A";
    const mcapClass = getMcapClass(a.market_cap);
    const category = a.category || "";
    const summary = a.summary || "";
    const dateStr = a.an_dt || "";
    const attachment = a.attchmntFile || "";

    const attachmentLink = attachment
        ? `<a class="attachment-link" href="${escapeAttr(attachment)}" target="_blank" rel="noopener">PDF</a>`
        : "-";

    const starred = isStarred(a);
    const starIcon = starred ? `<span class="star-icon" title="High Priority">&#9733;</span>` : "";
    const rowClass = starred ? "starred-row" : "";

    return `<tr class="${rowClass}">
        <td class="company-cell">
            ${starIcon}
            <a class="company-name" href="${screenerLink(name)}" target="_blank" rel="noopener">${escapeHtml(name)}</a>
            <div class="scrip-code"><span class="exchange-badge nse">NSE</span> ${escapeHtml(symbol)}</div>
        </td>
        <td class="mcap-cell ${mcapClass}">${mcapFmt}</td>
        <td class="subject-cell">
            ${category ? `<span class="category-badge">${escapeHtml(category)}</span>` : ""}
            ${summary ? `<div class="summary-text">${escapeHtml(summary)}</div>` : ""}
        </td>
        <td class="date-cell">${escapeHtml(dateStr)}</td>
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
    div.textContent = str;
    return div.innerHTML;
}

function escapeAttr(str) {
    return str.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/'/g, "&#39;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function updatePagination(data) {
    const paginationDiv = document.querySelector(".pagination");
    if (currentSource !== "bse") {
        paginationDiv.style.display = "none";
        return;
    }
    paginationDiv.style.display = "flex";
    document.getElementById("pageInfo").textContent = `Page ${data.page || 1}`;
    document.getElementById("prevBtn").disabled = (data.page || 1) <= 1;
    document.getElementById("nextBtn").disabled = (data.count || 0) < 50;
}

function changePage(delta) {
    currentPage = Math.max(1, currentPage + delta);
    fetchAnnouncements();
}
