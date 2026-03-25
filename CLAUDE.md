# CLAUDE.md — BSEAnnouncementsTracker

This file provides context for AI assistants working on this codebase.

## Project Overview

BSEAnnouncementsTracker is a corporate announcements aggregator for Indian stock markets (BSE and NSE). It fetches, deduplicates, categorizes, and summarizes exchange filings using AI, then serves them through a Flask API and vanilla JavaScript frontend. A weekly email digest is generated every Sunday.

## Repository Structure

```
.
├── backend/               # Flask API server and core logic
│   ├── app.py             # Main Flask app (entry point, port 5000)
│   ├── bse_api.py         # BSE announcement scraper
│   ├── nse_api.py         # NSE announcement scraper + market cap
│   ├── categorizer.py     # Rule-based announcement categorization (17 categories)
│   ├── summarizer.py      # Google Gemini AI summarization
│   ├── market_cap.py      # Yahoo Finance market cap fallback
│   ├── requirements.txt   # Python dependencies
│   └── test_*.py          # Manual connectivity/smoke tests
├── frontend/
│   └── public/
│       ├── index.html     # Main dashboard UI
│       ├── app.js         # Client-side filtering, pagination, search
│       ├── style.css      # Styling
│       └── filtered.html  # View for noise-filtered announcements
├── worker/
│   ├── fetch.py           # GitHub Actions worker: hourly data fetch
│   └── weekly_email.py    # Sunday digest email generator
├── data/
│   ├── announcements.json # Persisted announcements (git-committed by CI)
│   └── weekly_email_preview.html
├── docs/                  # GitHub Pages documentation mirror
└── .github/workflows/
    ├── fetch.yml          # Hourly fetch (5 min past each hour IST)
    └── weekly-email.yml   # Sunday 14:00 IST digest
```

## Technology Stack

- **Backend**: Python 3.11, Flask, Flask-CORS
- **Frontend**: Vanilla JavaScript, HTML, CSS (no frameworks)
- **AI**: Google Gemini 2.0 Flash (`google-generativeai`)
- **HTTP**: `requests`, `httpx[http2]` (for NSE)
- **PDF parsing**: `pypdf`
- **CI/CD**: GitHub Actions
- **Storage**: Flat JSON file (`data/announcements.json`), no database

## Local Development

```bash
pip install -r backend/requirements.txt
python backend/app.py
# Serves at http://localhost:5000
```

Required environment variables (set in `.env` or GitHub Secrets):
- `GEMINI_API_KEY` — Google Gemini API key (for summarization)
- `EMAIL_SENDER` — Gmail address for digest
- `EMAIL_APP_PASSWORD` — Gmail App Password
- `EMAIL_RECIPIENT` — Digest recipient email

## Key API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/all-announcements` | Primary endpoint: BSE+NSE merged, deduplicated, enriched |
| GET | `/api/announcements` | BSE-only announcements |
| GET | `/api/nse-announcements` | NSE-only announcements |
| GET | `/api/filtered-out` | Announcements removed as noise (for auditing) |
| GET | `/` | Serves `frontend/public/index.html` |

Query params for announcement endpoints: `from_date` (DD-MM-YYYY), `to_date` (DD-MM-YYYY), `page`.

## Data Model

All announcements share a common normalized structure:

```json
{
  "company": "Company Name",
  "symbol": "SYMBOL",
  "exchange": "BSE|NSE",
  "subject": "Announcement subject",
  "detail": "Detailed text or empty string",
  "date": "DD-MMM-YYYY HH:MM:SS",
  "attachment": "PDF URL or empty string",
  "category": "Category name",
  "starred": false,
  "market_cap": 85166494880.0,
  "market_cap_fmt": "8.52K Cr",
  "ai_summary": "AI-generated summary"
}
```

**Raw BSE fields**: `NEWSID`, `SLONGNAME`, `SCRIP_CD`, `NEWSSUB`, `HEADLINE`, `NEWS_DT`, `ATTACHMENTNAME`, `CATEGORYNAME`
**Raw NSE fields**: `sm_name`, `symbol`, `desc`, `an_dt`, `attchmntText`, `attchmntFile`

## Core Business Logic

### Noise Filtering (`worker/fetch.py`)
- 180+ regex patterns strip routine filings (board meeting notices, certificate submissions, compliance filings, etc.)
- 35+ whitelist patterns preserve important announcements regardless of subject
- Filtered-out items are saved separately for auditing via `/api/filtered-out`

### Deduplication (`backend/app.py` — `deduplicate_announcements`)
1. **Pass 1 (Exact)**: Normalized company name + first 60 chars of subject
2. **Pass 2 (Fuzzy)**: Same company + same category + announcements within 60 minutes
- When deduplicating, the entry with more data (market cap, attachment, longer subject) is preferred

### Categorization (`backend/categorizer.py`)
Rule-based keyword matching, 17 categories:
- New Order, Results, Dividend, Acquisition, Merger/Demerger, Fund Raising, Business Expansion, Joint Venture, Credit Rating, Capital Structure, Board Meeting, Press Release, Subsidiary, Divestment, Delisting, Regulatory, Allotment, Clarification, Other

### Starred/High-Priority Items
Announcements are starred (`starred: true`) if they belong to high-priority categories (Open Offer, Warrants, Buyback, Delisting, Business Expansion, Fund Raising) or contain keywords: `capex`, `expansion`, `warrant`, `raising capital`.

### Market Cap Enrichment
- **NSE**: Fetched from `https://www.nseindia.com/api/quote-equity?symbol={symbol}`
- **BSE**: Uses BSE scrip code to get price, then calculates market cap
- **Filtering**: Micro-cap stocks (< 50 Cr) are excluded
- **Cache**: 24-hour TTL, thread-safe in-memory cache
- **Format**: Indian convention — Cr (10M), K Cr (10B), L Cr (100T)

### AI Summarization (`backend/summarizer.py`, `worker/fetch.py`)
- Model: `gemini-2.0-flash`
- Only new announcements are summarized (worker compares to existing `announcements.json`)
- Attempts to download and parse PDF attachments before summarizing
- Responses are cached by news ID

### NSE Session Management (`backend/nse_api.py`)
- Thread-local session storage, refreshed every 2 minutes
- 5-minute backoff on 403 errors
- Rate limiting between market cap batch requests

## GitHub Actions Workflows

### `fetch.yml` — Hourly Data Collection
- **Schedule**: `5 * * * *` (5 minutes past every hour)
- **Timeout**: 15 minutes
- **Steps**: Install deps → run `worker/fetch.py` → auto-commit if `data/announcements.json` changed
- **Secrets**: `GEMINI_API_KEY`

### `weekly-email.yml` — Sunday Digest
- **Schedule**: `30 8 * * 0` (08:30 UTC = 14:00 IST, Sundays)
- **Timeout**: 10 minutes
- **Steps**: Run `worker/weekly_email.py` → upload `data/weekly_email_preview.html` as artifact
- **Secrets**: `EMAIL_SENDER`, `EMAIL_APP_PASSWORD`, `EMAIL_RECIPIENT`
- **Email structure**: Past 7 days grouped by 20 priority-ordered categories

## Testing

Tests are manual smoke tests in `backend/test_*.py` (not integrated with a test runner):
- `test_bse.py` — BSE API connectivity
- `test_bse_scrips.py`, `test_bse_scrips2.py` — BSE scrip code retrieval
- `test_nse.py` — NSE corporate announcements endpoint
- `test_nse_dl.py` — NSE price data and market cap

Run individually: `python backend/test_bse.py`

## Conventions and Guidelines

### When modifying the data pipeline
- `worker/fetch.py` is the authoritative source for noise filtering patterns — add/remove patterns there
- After modifying filter patterns, manually test with `python backend/test_bse.py` or similar
- The `data/announcements.json` file is committed by CI; do not hand-edit it

### When adding new announcement fields
- Update the normalization in both `backend/bse_api.py` and `backend/nse_api.py`
- Update the deduplication logic in `backend/app.py` if the new field affects merging
- Update the frontend table in `frontend/public/index.html` and `app.js`

### When modifying categories
- Edit `backend/categorizer.py` (single source of truth)
- Update the category list in `frontend/public/app.js` (used for filter dropdowns)
- Update `worker/weekly_email.py` if the email grouping order should change

### Frontend conventions
- No build step — pure HTML/CSS/JS, changes take effect immediately
- The frontend fetches from `/api/all-announcements` on load and filters client-side
- Date formatting uses Indian conventions throughout (DD-MMM-YYYY)

### Python conventions
- Flask app uses `flask-cors` for all origins — keep this for local dev flexibility
- Thread-safety is required for anything touching the NSE session cache or market cap cache
- Graceful fallback: if market cap or AI summary fails, the announcement is still returned without it

### Secrets and credentials
- Never commit `.env` files (already in `.gitignore`)
- All secrets go through GitHub Actions Secrets
- Gemini key is only needed for summarization; the app runs without it (summaries will be empty)

## External API Dependencies

| API | URL | Usage |
|-----|-----|-------|
| BSE | `https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w` | Announcement list |
| NSE | `https://www.nseindia.com/api/corporate-announcements` | Announcement list |
| NSE Quote | `https://www.nseindia.com/api/quote-equity?symbol=X` | Market cap + price |
| Gemini | Google AI API | AI summarization |
| Yahoo Finance | `https://query1.finance.yahoo.com` | Market cap fallback |

BSE and NSE APIs are public but may require session cookies or specific headers — see `bse_api.py` and `nse_api.py` for current header configuration.
