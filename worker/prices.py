"""End-of-day price fetcher for the Portfolio tool.

Fetches the latest (previous close / EOD) price for a set of stocks from
Yahoo Finance and writes them to docs/prices.json. The frontend portfolio
page reads that file and multiplies by the quantities held in the advisor's
browser (localStorage) to compute current value and P&L.

NOTE ON PRIVACY: This worker only ever touches stock *prices*, never client
holdings. Client names / quantities / buy prices live solely in the browser
and are never sent to GitHub. The list of symbols to price comes either from
the SYMBOLS env var (passed by the "Refresh prices" button) or from the
symbols already present in prices.json (so the daily cron keeps them fresh).

Yahoo symbol convention:
  - BSE listing  -> "<scrip_code>.BO"   e.g. 500325.BO
  - NSE listing  -> "<nse_symbol>.NS"   e.g. TCS.NS
"""

import json
import os
import re
import time
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PRICES_PATH = os.path.join(ROOT, "docs", "prices.json")

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"
}


def _load_existing():
    """Return (prices_dict) from the committed prices.json, or empty."""
    try:
        with open(PRICES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data.get("prices", {}) or {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _fetch_one(symbol):
    """Fetch latest price for a single Yahoo symbol via the chart endpoint.

    The v8 chart endpoint is crumb-free and reliable per-symbol. We read
    `meta.regularMarketPrice` (live during market hours, previous close after).
    Returns a dict {price, name, currency, ts} or None on failure.
    """
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
    params = {"range": "5d", "interval": "1d"}
    try:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=15)
        resp.raise_for_status()
        result = resp.json()["chart"]["result"][0]
        meta = result["meta"]
        # BSE (.BO) symbols frequently return null regularMarketPrice/previousClose
        # but still carry a usable close. Fall through several fields in order of
        # freshness so we always get the latest available (EOD is fine per spec).
        price = meta.get("regularMarketPrice")
        if price is None:
            try:
                closes = [c for c in result["indicators"]["quote"][0]["close"] if c is not None]
                if closes:
                    price = closes[-1]
            except (KeyError, IndexError, TypeError):
                pass
        if price is None:
            price = meta.get("previousClose") or meta.get("chartPreviousClose")
        if price is None:
            return None
        return {
            "price": round(float(price), 2),
            "name": meta.get("longName") or meta.get("shortName") or symbol,
            "currency": meta.get("currency") or "INR",
            "ts": meta.get("regularMarketTime"),
        }
    except Exception as e:  # network error, bad symbol, parse error
        print(f"  ! yahoo {symbol}: {e}")
        return None


def _fetch_screener(query):
    """Fallback price from screener.in for stocks Yahoo can't serve.

    `query` is the bare ticker/code (no .NS/.BO suffix). Screener's search
    accepts NSE symbols and BSE codes alike. We read the "Current Price"
    ratio from the company page. Returns {price, name, currency} or None.
    """
    try:
        s_url = f"https://www.screener.in/api/company/search/?q={requests.utils.quote(query)}"
        resp = requests.get(s_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        hits = resp.json()
        if not hits:
            return None
        company_url = hits[0].get("url", "")
        company_name = hits[0].get("name", query)
        if not company_url:
            return None

        page = requests.get(f"https://www.screener.in{company_url}", headers=HEADERS, timeout=15)
        page.raise_for_status()
        soup = BeautifulSoup(page.text, "html.parser")

        # Top-ratios list: <span class="name">Current Price</span> ... <span class="number">1,320</span>
        for span in soup.find_all("span", class_="name"):
            if "current price" in span.get_text(strip=True).lower():
                num = span.find_next("span", class_="number")
                if num:
                    val = num.get_text(strip=True).replace(",", "")
                    return {"price": round(float(val), 2), "name": company_name, "currency": "INR"}

        # Fallback: regex the page text for "Current Price ... ₹ <number>"
        m = re.search(r"Current Price[^0-9₹]*₹?\s*([\d,]+(?:\.\d+)?)", soup.get_text())
        if m:
            return {"price": round(float(m.group(1).replace(",", "")), 2),
                    "name": company_name, "currency": "INR"}
        return None
    except Exception as e:
        print(f"  ! screener {query}: {e}")
        return None


def main():
    existing = _load_existing()

    # Symbols to price: union of those passed in (SYMBOLS env) and those we
    # already track. This way the daily cron refreshes everything previously
    # added, and the "Refresh prices" button can introduce brand-new symbols.
    env_syms = os.environ.get("SYMBOLS", "")
    requested = {s.strip() for s in env_syms.split(",") if s.strip()}
    symbols = sorted(requested | set(existing.keys()))

    if not symbols:
        print("No symbols to price. Nothing to do.")
        return

    print(f"Pricing {len(symbols)} symbol(s): {', '.join(symbols)}")

    prices = dict(existing)  # keep any we fail to refresh this run
    ok = 0
    for sym in symbols:
        result = _fetch_one(sym)
        source = "yahoo"
        if not result:
            # Yahoo missed (common for some BSE-only codes) — try Screener.
            bare = sym.rsplit(".", 1)[0]
            result = _fetch_screener(bare)
            source = "screener"
        if result:
            prices[sym] = {
                "price": result["price"],
                "name": result["name"],
                "currency": result["currency"],
            }
            ok += 1
            print(f"  {sym}: {result['currency']} {result['price']} [{source}]")
        else:
            print(f"  {sym}: no price from any source")
        time.sleep(0.4)  # be gentle with the upstream APIs

    out = {
        "last_updated": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S"),
        "prices": prices,
    }
    os.makedirs(os.path.dirname(PRICES_PATH), exist_ok=True)
    with open(PRICES_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)

    print(f"Wrote {len(prices)} price(s) to {PRICES_PATH} ({ok} refreshed this run)")


if __name__ == "__main__":
    main()
