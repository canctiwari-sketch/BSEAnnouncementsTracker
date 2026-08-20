# FY2026 annual report guidance tracker

Extracts future plans, KPIs and guidance from annual reports, and renders them
into one searchable HTML file.

The target is deliberately narrow: **companies that hold no concalls and publish
no investor presentations, above a market cap floor.** A company running
quarterly calls telegraphs its guidance four times a year and the annual report
adds little. For a silent company the annual report is the only forward-looking
disclosure it makes all year — and almost nobody reads 250 pages to find it.

## Why it is built this way

A single annual report runs ~250 pages and ~200,000 tokens of text. Reading
5,000 of them with a language model means ~1 billion tokens — not affordable at
any scale that matters. But the forward-looking content lives in maybe 5-15
pages: the chairman's letter, MD&A, and the outlook section of the directors'
report. Everything else is financial statements, notes, auditor's report,
governance tables and BRSR/CSR annexures.

So stage 1 is pure pattern matching — no model, no API cost — and it throws away
about 99.7% of the text. On a 247-page report it keeps roughly 13 sentences.
That output is small enough to read directly, or to hand to a model for real
synthesis on the companies you actually care about.

## Where the data comes from

| Need | Source | Notes |
| --- | --- | --- |
| Annual report PDFs | **BSE API** `AnnualReport_New` | official, JSON, all years, direct PDF URLs |
| Market cap | screener company page | `#top-ratios` |
| Concall / presentation history | screener company page | `div.concalls` |

Annual reports originally came from scraping screener's per-company pages. BSE
publishes the same thing as structured JSON and it is strictly better — official,
every listed scrip, every year, a direct PDF URL per row, ~0.45s per company
against screener's 1s-plus-backoff, and no aggressive blocking. Spot-checking
confirms screener serves the identical BSE PDF URLs.

Screener is still needed for the market cap and concall signal, which BSE does
not expose. BSE's announcement feed (`AnnGetData`) would have given the concall
signal too, but **that endpoint currently returns `"No Record Found!"` for every
query, including historical date ranges** — see the caveat at the bottom.

## Running it

```bash
python worker/annual_reports/screen.py          # market cap + concalls (~2 hours)
AR_SILENT_ONLY=1 python worker/annual_reports/discover.py   # find their reports
python worker/annual_reports/mine.py            # download + extract
python worker/annual_reports/build_report.py    # render the HTML
```

Drop `AR_SILENT_ONLY=1` to collect the whole market instead of just the silent
companies. You can skip `screen.py` entirely in that case.

Output lands in `data/annual_reports/`:

| File | Contents |
| --- | --- |
| `screen.jsonl` | per scrip: market cap, concall count |
| `fy2026_reports.jsonl` | per scrip: found / no report, with the PDF URL |
| `fy2026_mined.jsonl` | the extracted sentences, per company |
| `fy2026_guidance.html` | the report — open it in a browser |

Every stage is **resume-safe**: stop with Ctrl-C and rerun, and it picks up where
it left off. Transient failures are not written to disk, so they get retried
rather than cached as misses.

## Rate limiting — important

Screener rate-limits aggressively. Four concurrent workers tripped HTTP 429
within a minute, and then the IP was blocked at the network layer — connections
timing out rather than returning 429 — for a good while afterwards.

`screen.py` therefore runs **single-threaded at ~1 request/second** with
exponential backoff. Do not raise the concurrency. If you get blocked anyway,
leave it an hour and rerun; nothing is lost.

`discover.py` (BSE) and `mine.py` (BSE/NSE PDF hosts) tolerate 2-3 workers.

### Tunables

| Env var | Default | Applies to |
| --- | --- | --- |
| `AR_FY` | `2026` | discover — which financial year |
| `AR_MIN_MCAP` | `100` | discover — market cap floor, in crore |
| `AR_SILENT_ONLY` | unset | discover — restrict to no-concall companies |
| `AR_DELAY` | `1.0` screen / `0.2` discover | seconds between requests |
| `AR_COOLDOWN` | `900` | screen — sleep after 3 consecutive blocks |
| `AR_WORKERS` | `3` | discover, mine — concurrency |
| `AR_MAX_MB` | `120` | mine — skip reports larger than this |
| `AR_MAX_HITS` | `40` | mine — cap extracts per company |

## Disk

PDFs are parsed in memory and never written to disk. A full sweep costs a few
hundred MB of JSON rather than the ~35 GB the PDFs themselves would take.

## Tuning the patterns

All the regexes live in `patterns.py`, separate from the download machinery.
Three layers do the filtering, and each exists because of a specific failure
seen in testing:

1. **`EXCLUDE_PAGE`** drops whole pages — financial statements, AGM notices,
   governance reports, BRSR/CSR annexures. It matches against the *first 800
   characters only*, i.e. the page heading. Matching full page text was tried
   and was much worse: a narrative page that merely mentions "corporate
   governance" in passing got discarded, which silently zeroed out several large
   companies.
2. **`BOILERPLATE`** drops individual sentences — actuarial and provident-fund
   language, CSR spend tables, ESG metrics, committee composition, director
   biographies. These match the guidance vocabulary closely enough to matter.
3. **`is_prose`** rejects anything shaped like a table. Tables shred into short
   capitalised fragments and numbers; management commentary has a high share of
   ordinary lowercase words and real sentence punctuation. The single most
   effective filter.

Sentence splitting matters as much as the patterns. Annual reports are typeset
in columns and callout boxes, so blank lines are *not* paragraph boundaries — a
page usually extracts as one long run of short lines. Splitting on blank lines
missed almost everything on design-heavy reports. `windows()` normalises each
page to a single stream and splits on sentence punctuation instead.

To iterate without re-downloading, cache the extracted page text once and rerun
the matcher against the cache.

## Accuracy and limits

Spot-checked across 10 reports. Precision is roughly 75-80% — good enough for a
screening tool where you read the quote and click through to the page, not good
enough to act on unread. Recall is the weaker side: a company that buries its
outlook in unusual phrasing gets missed.

**Expect a lower yield on exactly the companies this is aimed at.** Silent
small-caps tend to write thin, boilerplate-heavy annual reports. In testing,
ATV Projects (Rs 137 cr, no concalls) produced no extracts at all and Ambalal
Sarabhai (Rs 354 cr, no concalls) produced one, against 30 for Amara Raja and 20
for Blue Star. A company that says nothing on a call often says little in print
either. The ones that *do* say something are the find.

Every extract is a verbatim sentence with a page link to the source PDF. Nothing
is paraphrased, so there is nothing for a model to get wrong — but read the
source before acting on any of it.

## Caveat: BSE announcement feed

`api.bseindia.com/BseIndiaAPI/api/AnnGetData/w` currently returns the bare
string `"No Record Found!"` for every query — all date ranges including
historical ones, both bulk and per-scrip, with and without cookie priming. The
annual-report endpoint on the same host works fine, so this is not an IP block.

This is the endpoint `backend/bse_api.py` uses for the daily announcements
tracker, so that pipeline is likely affected too. Worth checking separately.
