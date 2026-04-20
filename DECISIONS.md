# Architectural Decisions

One-liner per decision with rationale. Read alongside `ARCHITECTURE.md`.

---

## D1. GitHub Actions for all backend work (no server)
**Decision:** Every backend task (hourly fetch, on-demand Lookup, on-demand Research) runs as a GitHub Actions workflow on Ubuntu runners.
**Why:** Public-repo minutes are unlimited and free; runners come with 7 GB RAM + 2 CPU which is enough for 10–15 page research reports in 5–10 min. No server ops, no cost. The alternative (user's local machine, Render, Fly) introduced friction or bills.
**Trade-off:** Cold start ~30s per dispatch. Fine for on-demand UX where user expects minutes anyway.

## D2. On-demand triggers via `workflow_dispatch` + PAT
**Decision:** Lookup and Deep Research are triggered by the browser POSTing to GitHub's `workflow_dispatch` REST API using a user-supplied PAT stored in `localStorage`.
**Why:** Keeps the site fully static while allowing user-parameterised jobs. No backend proxy needed.
**Required PAT scopes:** `repo` + `workflow`. Without `workflow`, dispatch 404s silently.
**Trade-off:** User must create a PAT once. UX mitigated by "Update token" link in Watchlist modal.

## D3. Use `AnnSubCategoryGetData/w`, not `AnnGetData/w`, for scrip-specific queries
**Decision:** Lookup and Research workers hit `AnnSubCategoryGetData/w`.
**Why:** BSE's `AnnGetData/w` returns 0 rows when a `scrip` filter is passed — it's only for the general feed. Discovered when RSWM Ltd (scrip 500350) returned empty despite having 252 real announcements.

## D4. Direct Gemini REST API, not `google.generativeai` SDK
**Decision:** `bse_summarizer.py` calls `https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` directly via `requests`.
**Why:** `google-generativeai` is deprecated; fresh installs on Ubuntu runners fail or emit deprecation warnings. `google-genai` is the replacement but has a different API. Direct REST is stable, one function, zero SDK churn.
**Model:** `gemini-2.5-flash-lite` (30 RPM, 1500 RPD free tier — enough headroom).

## D5. Night mode for summary backlog
**Decision:** `fetch.py` detects IST 22:00–07:00 and switches to aggressive batching (batch=10, max=500, wait=1s) vs daytime (batch=5, max=100, wait=2s).
**Why:** User hit a 466-row backlog; Gemini free-tier quotas reset daily and Indian business hours don't overlap with most quota windows. Night runs drain the queue without risking midday rate limits.
**IST calc:** `datetime.utcnow() + timedelta(hours=5, minutes=30)` — not `hours=5` (earlier bug).

## D6. Opposite noise filters for Feed vs Research
**Decision:** The feed drops presentations / concall transcripts / annual reports / Reg 31(4) encumbrance declarations / trading-window notices. The Research engine's `is_important_document` KEEPS presentations / transcripts / annual reports.
**Why:** In the feed, these are high-volume low-signal noise. In a synthesised research report, they're the *primary* source material.
**Kept in feed:** Reg 29 (substantial acquisition) — actually informative.

## D7. Ephemeral Lookup files, retained Research files
**Decision:** `data/lookup/*.json` auto-deleted after 48h by `cleanup-lookup.yml`. `data/research/*.docx` kept indefinitely.
**Why:** Lookup data is cheap to regenerate and bloats the repo; research reports are expensive (5–10 min compute) and users may revisit them.

## D8. `scrips.json` loaded lazily with race-condition guard
**Decision:** `app.js` exposes a `scripsLoading` promise; `openLookup()` is `async` and awaits it before enabling the input (placeholder shows "Loading company list…").
**Why:** The 910 KB `scrips.json` loads async on page open. Users opened Lookup and typed before it was ready → autocomplete empty → company "missing".
**Also:** BSE entries with empty `NSESymbol` are merged with NSE-only entries by exact name match so companies like Sanghvi Movers (`SANGHVIMOV`) surface correctly.

## D9. 6288-company scrips.json, not the ~500 from live feed
**Decision:** Autocomplete backs on a static `data/scrips.json` imported from the StockResearchTool project, not the dynamically-populated current-announcements set.
**Why:** Only companies that filed something recently appear in the feed; Lookup must work for *any* listed company.

## D10. Watchlist is frontend-first with optional cloud sync
**Decision:** Primary storage is `localStorage['twc_watchlist']`. If a PAT is present, sync to `data/watchlist_{userid}.json` on write.
**Why:** Works instantly with no auth; users who want multi-device get it via the PAT they already supplied for Lookup/Research.

## D11. No `fpdf2` in Research pipeline
**Decision:** Removed `from fpdf import FPDF` from `bse_summarizer.py`.
**Why:** It was dead code (only `python-docx` is actually used for report generation) and caused `ModuleNotFoundError` on fresh runners.

## D12. Copy-and-adapt StockResearchTool, don't git-submodule it
**Decision:** `bse_summarizer.py` and `web_search_utils.py` were copied into `worker/` and edited in place (paths, SDK calls, endpoint).
**Why:** Submodules complicate Actions checkout and the two projects have diverged output-path and dependency requirements. Copy keeps this repo self-contained.

---

## Known live items (for next session)
- Test Deep Research end-to-end after the `fpdf` + SDK fixes (commit `395f459`).
- Regenerate the GitHub PAT — the previous one was shared in chat and is compromised.
- Optional: add AI summaries to Lookup top-20 most recent rows (user expressed interest, not started).
