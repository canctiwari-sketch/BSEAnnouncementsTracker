# Architecture

BSE/NSE corporate announcements tracker with AI summaries, on-demand company history lookup, and deep-dive research reports. Fully serverless — static frontend on GitHub Pages, workers on GitHub Actions.

## Repo layout

```
BSEAnnouncementsTracker/
├── docs/                          # GitHub Pages site root
│   ├── index.html                 # Main feed + Watchlist modal + Lookup modal
│   ├── app.js                     # Frontend logic (~1500 lines)
│   ├── style.css                  # All styling
│   ├── logo.png
│   └── announcements.json         # Generated feed (committed by worker)
├── worker/
│   ├── fetch.py                   # Main hourly feed fetcher + AI summariser
│   ├── lookup.py                  # On-demand 3-year company history
│   ├── research.py                # Thin wrapper that calls bse_summarizer
│   ├── bse_summarizer.py          # Deep Research engine (ported from StockResearchTool)
│   └── web_search_utils.py        # DuckDuckGo helpers for research
├── data/
│   ├── scrips.json                # 6288 BSE+NSE companies (for autocomplete)
│   ├── lookup/{scrip}.json        # Ephemeral, 48h cleanup
│   └── research/{company}.docx    # Generated reports (retained)
├── .github/workflows/
│   ├── fetch.yml                  # Cron hourly — main feed
│   ├── company-lookup.yml         # workflow_dispatch — Lookup
│   ├── stock-research.yml         # workflow_dispatch — Deep Research
│   └── cleanup-lookup.yml         # Cron daily — purge old lookup files
├── ARCHITECTURE.md                # (this file)
└── DECISIONS.md
```

## Components

### 1. Main announcements feed (hourly)
- **Trigger:** `fetch.yml` cron (every hour)
- **Flow:** `fetch.py` →
  1. Pull last ~24h from BSE `AnnGetData/w` (general feed)
  2. Pull NSE equities announcements via httpx HTTP/2 (bypasses datacenter IP blocks)
  3. Noise filter (regex) — drops encumbrance Reg 31(4), trading window, duplicate listings etc. (Reg 29 substantial acquisition KEPT)
  4. Market cap enrichment from screener.in
  5. Gemini 2.5 Flash Lite summaries with night mode (IST 22:00–07:00 → batch=10, max=500, wait=1s; day → batch=5, max=100, wait=2s)
  6. Write `docs/announcements.json`, commit, push
- **Consumer:** `docs/app.js` fetches announcements.json on page load, renders filterable/sortable table.

### 2. Company History Lookup (on-demand)
- **Trigger:** User clicks "🔍 Lookup" in UI → types company → "Fetch 3 Yrs"
- **Frontend:** POSTs to GitHub `workflow_dispatch` API with `company_name`, `scrip_code`, then polls `data/lookup/{scrip}.json`
- **Backend:** `lookup.py` hits BSE `AnnSubCategoryGetData/w` (scrip-specific endpoint — `AnnGetData` returns 0 for specific scrips), filters presentations/transcripts/annual-reports (opposite of research filter), writes JSON, commits.
- **Cleanup:** `cleanup-lookup.yml` deletes files older than 48h.

### 3. Deep Research (on-demand)
- **Trigger:** User clicks "🔬 Deep Research" after selecting a company
- **Frontend:** Same PAT-dispatch pattern → polls `data/research/{company}.docx`
- **Backend:** `research.py` → `bse_summarizer.analyze_single_stock(deep_dive=True)`:
  1. Fetch 3y of important BSE docs (presentations/transcripts/annual reports INCLUDED here — essential for synthesis)
  2. Download PDFs into GitHub Actions runner tmpfs
  3. PyPDF2 extraction
  4. Screener.in scrape for financials
  5. DuckDuckGo news/web search
  6. Gemini 2.5 Flash Lite via **direct REST** (`generativelanguage.googleapis.com/v1beta/...:generateContent`) — NOT via deprecated `google.generativeai` SDK
  7. python-docx assembly → 10–15 page .docx
  8. Commit to `data/research/`
- **Runtime:** ~5–10 min on GitHub's Ubuntu runner (7GB RAM, 2 CPU — all free for public repos).

### 4. Watchlist (pure frontend)
- localStorage key `twc_watchlist`; optional cloud sync via user's GitHub PAT (writes `data/watchlist_{userid}.json`).
- "+" button per row → saves company + triggering announcement as note. Same company accumulates multiple notes.
- Modal with export/import JSON.

## Data sources
| Source | Purpose | Endpoint/Method |
|---|---|---|
| BSE | Feed | `AnnGetData/w` |
| BSE | Scrip-specific history | `AnnSubCategoryGetData/w` |
| NSE | Feed | `www.nseindia.com/api/corporate-announcements` via httpx HTTP/2 |
| Gemini 2.5 Flash Lite | Summaries + research synthesis | Direct REST (`v1beta`) |
| screener.in | Market cap + financial tables | HTML scrape |
| DuckDuckGo | News + web context for research | `duckduckgo-search` pkg |

## Authentication model
- **GitHub Actions secrets:** `GEMINI_API_KEY`
- **User PAT (classic):** stored in browser localStorage `twc_gh_token`. Needed scopes: `repo` (watchlist cloud sync) + `workflow` (dispatch for Lookup/Research). Without `workflow` scope, dispatch returns 404.

## Hosting model
- **Frontend:** GitHub Pages serves `docs/` at `https://canctiwari-sketch.github.io/BSEAnnouncementsTracker/`
- **Compute:** GitHub Actions — Ubuntu latest, unlimited minutes for public repo
- **Storage:** Git itself (announcements.json ~1–3 MB, .docx reports ~100–300 KB each)
- **No database, no server, no cost.**
