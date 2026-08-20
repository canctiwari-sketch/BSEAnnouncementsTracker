"""Stage 0b: which companies talk to investors, according to NSE.

BSE's per-scrip announcement feed is incomplete for some companies. Sundaram
Finance is the case that exposed it: BSE returns 29 announcements over 15
months with no concall filings at all, while NSE returns 138 for the same
window including 26 concall notices. Trusting BSE alone would have put a
company that runs quarterly calls on the "never speaks" list.

So a company counts as silent only when BOTH exchanges agree. This script
builds the NSE half: it walks the announcement feed a month at a time and
collects the ISIN of every company that filed a concall, transcript or
investor presentation. ISIN is the join key -- it is on every NSE row and in
BSE's scrip master, and unlike ticker symbols it is stable across both.

Roughly 17,000 rows and 12 MB per month, so about 15 requests for the window.
"""
import json, os, time
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data", "annual_reports", "nse_engaged.json")

LOOKBACK_DAYS = int(os.environ.get("AR_LOOKBACK_DAYS", "450"))
DELAY = float(os.environ.get("AR_DELAY", "1.0"))

LANDING = "https://www.nseindia.com/companies-listing/corporate-filings-announcements"
API = ("https://www.nseindia.com/api/corporate-announcements"
       "?index=equities&from_date={frm}&to_date={to}")

# NSE's own labels for investor engagement.
ENGAGED = {
    "Analysts/Institutional Investor Meet/Con. Call Updates",
    "Investor Presentation",
    "Earnings Call Transcript",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "*/*",
    "Accept-Language": "en-US,en;q=0.9",
}


def session():
    """NSE hands out the cookies its API requires only via a page visit."""
    s = requests.Session()
    s.headers.update(HEADERS)
    s.get(LANDING, timeout=40)
    s.headers.update({"Referer": LANDING})
    return s


def month_windows(days):
    """Month-sized (start, end) date pairs covering the lookback, newest last."""
    end = datetime.now()
    start = end - timedelta(days=days)
    out, cur = [], start
    while cur < end:
        nxt = min(cur + timedelta(days=30), end)
        out.append((cur, nxt))
        cur = nxt + timedelta(days=1)
    return out


def main():
    s = session()
    engaged, symbols = {}, {}
    rows_seen = 0

    windows = month_windows(LOOKBACK_DAYS)
    print("fetching NSE announcements in {} monthly chunks".format(len(windows)), flush=True)

    for i, (a, b) in enumerate(windows, 1):
        url = API.format(frm=a.strftime("%d-%m-%Y"), to=b.strftime("%d-%m-%Y"))
        try:
            data = s.get(url, timeout=120).json()
        except Exception as exc:
            print("  {} to {}: FAILED ({}) - retrying once".format(
                a.date(), b.date(), type(exc).__name__), flush=True)
            time.sleep(5)
            s = session()
            try:
                data = s.get(url, timeout=120).json()
            except Exception as exc2:
                print("  {} to {}: giving up ({})".format(
                    a.date(), b.date(), type(exc2).__name__), flush=True)
                continue

        if not isinstance(data, list):
            print("  {} to {}: unexpected payload".format(a.date(), b.date()), flush=True)
            continue

        rows_seen += len(data)
        hits = 0
        for row in data:
            if row.get("desc") not in ENGAGED:
                continue
            isin = (row.get("sm_isin") or "").strip()
            if not isin:
                continue
            engaged[isin] = engaged.get(isin, 0) + 1
            symbols.setdefault(isin, row.get("symbol"))
            hits += 1

        print("  {} to {}: {} rows, {} engagement, {} ISINs so far".format(
            a.date(), b.date(), len(data), hits, len(engaged)), flush=True)
        time.sleep(DELAY)

    payload = {
        "generated": datetime.now().isoformat(timespec="seconds"),
        "lookback_days": LOOKBACK_DAYS,
        "rows_scanned": rows_seen,
        "engaged": engaged,
        "symbols": symbols,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    print("done: {} companies engage with investors per NSE ({} rows scanned) -> {}"
          .format(len(engaged), rows_seen, OUT))


if __name__ == "__main__":
    main()
