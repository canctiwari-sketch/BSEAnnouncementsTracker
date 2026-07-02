# Time Wheel Capital — Project Handoff

A static **GitHub Pages** frontend (`docs/`) backed by **GitHub Actions** Python
workers (`worker/`) that fetch data into JSON files under `data/`. The frontend
reads those JSON files (via `raw.githubusercontent.com` or the GitHub contents
API) — there is **no backend server**.

Repo: `canctiwari-sketch/BSEAnnouncementsTracker` · Live: `https://canctiwari-sketch.github.io/BSEAnnouncementsTracker/`

---

## Tabs (frontend: `docs/index.html`, `docs/app.js`, `docs/style.css`)

1. **📢 Announcements** — hourly BSE + NSE (now incl. **NSE SME**) corporate
   announcements, AI-summarized (Gemini). Filters: category, mcap min/max,
   N/A toggle, starred, date range, search. Pagination (First/Prev/Go-to/Next/Last).
2. **👤 Insider Trades** — 1-year rolling BSE+NSE insider trades; All-Trades and
   Aggregate (Market Net + Preferential) views.
3. **🎙️ Interviews** — management interview videos from ~12 YouTube channels
   (Hindi+English), matched to companies. Card grid, 60-day retention.
4. **📊 Disclosure** — per-quarter: did a company release an investor
   **Presentation** but **no Concall/Transcript**. Auto-rolls Q4→Q1→Q2→Q3.
5. **🔬 Research** — on-demand: "Fetch 3 Yrs" (company history, AI-summarized)
   and "Deep Research" (full .docx report via `gemini-2.5-pro`).
6. **💼 Portfolios** — client portfolio tracker (`portfolio.html`), Yahoo prices.

Frontend cache-busting: bump `?v=` on `app.js`/`style.css` in `index.html` after edits.

---

## Workers & workflows (cadence)

| Worker | Workflow | Cadence | Purpose |
|---|---|---|---|
| `fetch.py` | `fetch.yml` | hourly | Announcements feed (BSE firehose + NSE equities+SME) → `announcements.json` |
| `insider.py` | `insider.yml` | daily | Insider trades → `insider.json` |
| `interviews.py` | `interviews.yml` | daily | YouTube interviews → `interviews.json` (needs `YOUTUBE_API_KEY`) |
| `comm_profile.py` | `comm-profile.yml` | weekly (Sun) | Disclosure profile → `comm_profile.json` |
| `lookup.py` | `company-lookup.yml` | on-demand | 3-yr company history → `data/lookup/<scrip>.json` |
| `research.py` + `bse_summarizer.py` | `stock-research.yml` | on-demand | Deep-dive .docx → `data/research/` |
| `prices.py` | `prices.yml` | weekdays | Portfolio prices → `docs/prices.json` |
| `weekly_email.py` | `weekly-email.yml` | weekly | Email digest |

**Secrets (GitHub → Settings → Secrets → Actions):** `GEMINI_API_KEY`,
`GEMINI_API_KEY_2` (failover), `YOUTUBE_API_KEY`, `EMAIL_SENDER`,
`EMAIL_APP_PASSWORD`. On-demand workflows are triggered from the frontend via a
user GitHub PAT (classic, scopes: `repo` + `workflow`) stored in browser localStorage.

---

## Key logic & gotchas (IMPORTANT — hard-won)

- **Gemini:** hourly summarizer uses `gemini-2.5-flash-lite` with a model
  cascade (flash-lite→flash→2.0-flash→2.0-flash-lite) × 2 keys on 429.
  Deep Research uses **`gemini-2.5-pro` only** (never downgrade — quality).
  PDF text cached per announcement, cleared once summarized.
- **BSE firehose (`AnnGetData`)** is flaky/throttles from datacenter IPs →
  returns "No Record Found!" strings. `fetch.py` retries per page and uses a
  split window (2-day hourly, 5-day reconciliation twice daily). BSE per-scrip
  (`AnnSubCategoryGetData`) is more complete but can't be run for all companies.
- **NSE announcements API** works from datacenter; **NSE quote-equity (mcap)
  API is 403-blocked** from datacenter/most IPs.
- **Market cap sources:** persistent cache → **Yahoo Finance** (by symbol,
  crumb flow, datacenter-friendly) → **BSE** (name→scrip→StockTrading).
  **screener.in is NOT used** in `comm_profile.py` (it IP-blocks the user's
  network). `fetch.py` still uses screener as one fallback (runs on GH IP, so
  it does not affect the user's home network) — leave `fetch.py` as-is.
- **NSE SME** companies: no free mcap source (Yahoo/BSE don't have them,
  screener does but is off-limits). They are shown with **"N/A SME"** in the
  Announcements + Disclosure tabs and are always visible (not hidden by the
  N/A filter). The daily feed now scans the SME board.
- **Disclosure seasons** (strict, by filing date): Apr–Jun=Q4, Jul–Sep=Q1,
  Oct–Dec=Q2, Jan–Mar=Q3. Grace = **10 days** after a presentation before
  "no concall" is confirmed (statuses: pending / pres_only / both / call_only).
  Worker merges — past quarters preserved, only current season rebuilt.
  Mcap band: ≥ ₹50 Cr, no upper bound. `FORCE_SEASON`/`FORCE_START`/`FORCE_END`
  and `SKIP_SCREENER=1` env vars exist for manual reprocessing.
- **Insider BSE** uses `getCorp_Regulation_ng` (25 records/response cap →
  daily-chunk fetching). Runs daily, 7-day incremental lookback.
- **Offline Deep Research tool** exists separately at
  `C:\Users\canct\Downloads\StockResearchTool` (Flask app, `Start Stock
  Research Tool.bat`) — older code (`gemini-1.5-flash`), NOT synced with website.

---

## Standing preferences (from the user)

- Bump `?v=` cache-buster on any `docs/*.js|css` edit (stale cache caused real
  data loss once).
- Prospective-only for AI summaries — don't retroactively re-summarize old data.
- Don't touch the announcement noise filter — it's tuned and working.
- Never use screener.in in a way that hits the user's home network.
- User is a wealth advisor (~10–15 clients, Indian stocks); values client-data privacy.

---

## Open / optional items

- ₹20,000 Cr upper cap on Disclosure (user wanted to review full list first).
- Nightly per-scrip announcement backstop for guaranteed coverage of key names.
- ~107 NSE-SME-only companies in Disclosure show N/A mcap (no free source).
- BSE-only companies are NOT in the Disclosure tab (NSE-based scan).

---

## How to work in the repo

- Commit/push only when asked; on `main`; end commit messages with the
  Co-Authored-By line. Use `git pull --rebase` before push (hourly bots also push).
- Verify Python syntax before committing: `python -c "import ast; ast.parse(open('worker/X.py').read())"`.
- The user is on Windows (Git Bash + PowerShell available).
