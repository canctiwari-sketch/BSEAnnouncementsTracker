import requests
import time
import threading
from datetime import datetime, timedelta
from urllib.parse import quote

# Thread-local storage for sessions
_thread_local = threading.local()
SESSION_TTL = 120  # Refresh session every 2 minutes

# Market cap cache (thread-safe since GIL protects dict operations)
_mcap_cache = {}
MCAP_CACHE_TTL = 86400  # 24 hours — user said day-old data is fine

# Flag to track if NSE is rate-limited
_nse_blocked = False
_nse_blocked_until = 0


def _get_nse_session():
    """Get or create a thread-local NSE session with valid cookies."""
    global _nse_blocked, _nse_blocked_until
    now = time.time()

    if _nse_blocked and now < _nse_blocked_until:
        return None

    session = getattr(_thread_local, "nse_session", None)
    session_time = getattr(_thread_local, "nse_session_time", 0)

    if session and now - session_time < SESSION_TTL:
        return session

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
        "Sec-Ch-Ua": '"Chromium";v="134", "Not:A-Brand";v="24", "Google Chrome";v="134"',
        "Sec-Ch-Ua-Mobile": "?0",
        "Sec-Ch-Ua-Platform": '"Windows"',
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
        "Upgrade-Insecure-Requests": "1",
        "Connection": "keep-alive",
    })
    try:
        r = session.get("https://www.nseindia.com", timeout=15)
        if r.status_code == 403:
            _nse_blocked = True
            _nse_blocked_until = now + 300  # Wait 5 minutes
            return None
    except Exception:
        return None

    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    })
    _thread_local.nse_session = session
    _thread_local.nse_session_time = now
    _nse_blocked = False
    return session


def _get_bse_session():
    """Get or create a BSE API session."""
    session = getattr(_thread_local, "bse_session", None)
    if session:
        return session

    session = requests.Session()
    session.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    })
    _thread_local.bse_session = session
    return session


def fetch_announcements(from_date=None, to_date=None):
    """Fetch NSE corporate announcements for a date range."""
    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if from_date is None:
        from_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    nse_from = from_dt.strftime("%d-%m-%Y")
    nse_to = to_dt.strftime("%d-%m-%Y")

    session = _get_nse_session()
    if not session:
        # Force retry with a fresh session
        _thread_local.nse_session = None
        _thread_local.nse_session_time = 0
        session = _get_nse_session()
        if not session:
            raise Exception("NSE is temporarily unavailable (rate limited). Try again in a few minutes.")

    url = (
        f"https://www.nseindia.com/api/corporate-announcements"
        f"?index=equities&from_date={nse_from}&to_date={nse_to}"
    )

    response = session.get(url, timeout=15)
    if response.status_code in (401, 403):
        # Session expired, force refresh and retry once
        _thread_local.nse_session = None
        _thread_local.nse_session_time = 0
        session = _get_nse_session()
        if not session:
            raise Exception("NSE is temporarily unavailable.")
        response = session.get(url, timeout=15)

    response.raise_for_status()
    response.encoding = "utf-8"
    text = response.text
    if not text or not text.strip():
        return []
    import json
    return json.loads(text)


def _fetch_mcap_nse(symbol):
    """Fetch market cap from NSE for an NSE symbol."""
    session = _get_nse_session()
    if not session:
        return None

    try:
        encoded = quote(symbol, safe="")
        r = session.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={encoded}",
            timeout=10,
        )
        if r.status_code in (401, 403):
            global _nse_blocked, _nse_blocked_until
            _nse_blocked = True
            _nse_blocked_until = time.time() + 300
            _thread_local.nse_session_time = 0
            return None
        r.raise_for_status()
        d = r.json()
        price = d.get("priceInfo", {}).get("lastPrice", 0)
        issued = d.get("securityInfo", {}).get("issuedSize", 0)
        raw_mcap = price * issued if price and issued else None
        return {"value": raw_mcap, "formatted": _format_market_cap(raw_mcap), "price": price}
    except Exception:
        return None


def _fetch_mcap_bse(scrip_code):
    """Fetch price from BSE for a BSE scrip code.
    BSE doesn't provide shares outstanding directly, so we get LTP + PE/EPS."""
    session = _get_bse_session()
    try:
        # Get price
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/getScripHeaderData/w"
            f"?Ession_id=&newsession_id=&scripcode={scrip_code}&gession_id=&Type=EQ"
        )
        r = session.get(url, timeout=10)
        r.raise_for_status()
        d = r.json()

        ltp_str = d.get("CurrRate", {}).get("LTP", "0")
        ltp = float(ltp_str.replace(",", "")) if ltp_str else 0

        company = d.get("Cmpname", {}).get("FullN", "")

        # Get PE/EPS for additional context
        url2 = (
            f"https://api.bseindia.com/BseIndiaAPI/api/ComHeadernew/w"
            f"?Ession_id=&newsession_id=&scripcode={scrip_code}&gression_id=&Type=EQ"
        )
        r2 = session.get(url2, timeout=10)
        pe = None
        eps = None
        if r2.status_code == 200:
            try:
                d2 = r2.json()
                pe_str = d2.get("ConPE") or d2.get("PE") or ""
                eps_str = d2.get("ConEPS") or d2.get("EPS") or ""
                # BSE returns "-" for unavailable values
                pe_str = pe_str.strip().replace("-", "").replace(",", "")
                eps_str = eps_str.strip().replace("-", "").replace(",", "")
                pe = float(pe_str) if pe_str else None
                eps = float(eps_str) if eps_str else None
            except (ValueError, TypeError):
                pass

        return {
            "price": ltp,
            "pe": pe,
            "eps": eps,
            "company": company,
        }
    except Exception:
        return None


def fetch_market_caps(identifiers, source="nse"):
    """Fetch market caps for multiple symbols/scrip codes.

    Args:
        identifiers: List of NSE symbols or BSE scrip codes.
        source: "nse" for NSE symbols, "bse" for BSE scrip codes.

    Returns dict mapping identifier -> { value, formatted, price }.
    """
    unique_ids = list(set(str(s).strip() for s in identifiers if s))
    results = {}
    now = time.time()

    # Check cache first
    to_fetch = []
    for ident in unique_ids:
        cache_key = f"{source}:{ident}"
        if cache_key in _mcap_cache and now - _mcap_cache[cache_key]["timestamp"] < MCAP_CACHE_TTL:
            results[ident] = _mcap_cache[cache_key]
        else:
            to_fetch.append(ident)

    if not to_fetch:
        return results

    if source == "nse":
        for i, sym in enumerate(to_fetch):
            data = _fetch_mcap_nse(sym)
            if data:
                data["timestamp"] = now
                _mcap_cache[f"nse:{sym}"] = data
                results[sym] = data
            else:
                entry = {"value": None, "formatted": None, "price": None, "timestamp": now}
                # Don't cache failures for long
                entry["timestamp"] = now - MCAP_CACHE_TTL + 300
                _mcap_cache[f"nse:{sym}"] = entry
                results[sym] = entry
            if (i + 1) % 3 == 0 and i < len(to_fetch) - 1:
                time.sleep(0.2)

    elif source == "bse":
        # For BSE, first try NSE (if available) to get actual market cap
        # Otherwise fall back to BSE price data
        for i, scrip in enumerate(to_fetch):
            entry = {"value": None, "formatted": None, "price": None, "timestamp": now}

            # Try BSE for price data
            bse_data = _fetch_mcap_bse(scrip)
            if bse_data and bse_data.get("price"):
                entry["price"] = bse_data["price"]
                entry["pe"] = bse_data.get("pe")
                entry["eps"] = bse_data.get("eps")

            _mcap_cache[f"bse:{scrip}"] = entry
            results[scrip] = entry

            if (i + 1) % 5 == 0 and i < len(to_fetch) - 1:
                time.sleep(0.1)

    return results


def _format_market_cap(value):
    """Format market cap in Indian convention (Cr / K Cr / L Cr)."""
    if value is None:
        return None
    cr = value / 1e7
    if cr >= 100000:
        return f"{cr / 100000:.2f}L Cr"
    elif cr >= 1000:
        return f"{cr / 1000:.2f}K Cr"
    elif cr >= 1:
        return f"{cr:.0f} Cr"
    else:
        return f"{value:,.0f}"


if __name__ == "__main__":
    print("Testing BSE market cap fetch...")
    results = fetch_market_caps(["500325", "543278"], source="bse")
    for code, data in results.items():
        print(f"  BSE:{code} -> LTP:{data.get('price')} PE:{data.get('pe')} MCap:{data.get('formatted')}")
