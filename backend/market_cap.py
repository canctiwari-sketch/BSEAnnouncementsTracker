import requests
import time

# In-memory cache: { scrip_code: { "value": int|None, "formatted": str|None, "timestamp": float } }
_cache = {}
CACHE_TTL = 3600  # 1 hour


def _format_market_cap(value):
    """Format market cap in Indian convention (Cr / L Cr)."""
    if value is None:
        return None
    cr = value / 1e7  # 1 Cr = 10 million
    if cr >= 100000:
        return f"{cr/100000:.2f}L Cr"
    elif cr >= 1000:
        return f"{cr/1000:.2f}K Cr"
    elif cr >= 1:
        return f"{cr:.0f} Cr"
    else:
        return f"{value:,.0f}"


def _fetch_yahoo_quotes(symbols):
    """Fetch quote data for multiple symbols using Yahoo Finance v8 API."""
    if not symbols:
        return {}

    url = "https://query1.finance.yahoo.com/v7/finance/quote"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
    }
    params = {
        "symbols": ",".join(symbols),
        "fields": "marketCap,shortName",
    }

    try:
        resp = requests.get(url, headers=headers, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        results = {}
        for quote in data.get("quoteResponse", {}).get("result", []):
            symbol = quote.get("symbol", "")
            mcap = quote.get("marketCap")
            results[symbol] = mcap
        return results
    except Exception:
        return {}


def get_market_caps_batch(scrip_codes):
    """Fetch market caps for multiple BSE scrip codes.

    Returns dict mapping scrip_code -> { value, formatted }.
    """
    unique_codes = list(set(str(s).strip() for s in scrip_codes if s))
    results = {}
    now = time.time()

    # Check cache first
    to_fetch = []
    for code in unique_codes:
        if code in _cache and now - _cache[code]["timestamp"] < CACHE_TTL:
            results[code] = _cache[code]
        else:
            to_fetch.append(code)

    if not to_fetch:
        return results

    # Build Yahoo symbols (BSE scrip codes use .BO suffix)
    symbols = [f"{code}.BO" for code in to_fetch]

    # Fetch in batches of 20
    for i in range(0, len(symbols), 20):
        batch_symbols = symbols[i:i+20]
        batch_codes = to_fetch[i:i+20]
        yahoo_data = _fetch_yahoo_quotes(batch_symbols)

        for code, symbol in zip(batch_codes, batch_symbols):
            raw = yahoo_data.get(symbol)
            entry = {
                "value": raw,
                "formatted": _format_market_cap(raw),
                "timestamp": now,
            }
            _cache[code] = entry
            results[code] = entry

    return results


if __name__ == "__main__":
    # Test with some known scrip codes
    result = get_market_caps_batch(["500325", "543278", "500180"])
    for code, data in result.items():
        print(f"BSE:{code} -> {data['formatted']}")
