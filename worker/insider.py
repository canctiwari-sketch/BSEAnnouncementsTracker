"""
Worker: Fetches BSE + NSE insider trading disclosures (SEBI PIT Reg 2015).
Deduplicates across exchanges, maintains 1-year rolling window.
Run daily via GitHub Actions.
"""

import json
import os
import re
import time
import requests
import httpx
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUTPUT_FILE = os.path.join(DATA_DIR, "insider.json")

BSE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/134.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}


def log(msg):
    print(msg, flush=True)


def load_existing():
    if os.path.exists(OUTPUT_FILE):
        with open(OUTPUT_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        # Ensure mcap_cache key exists
        if "mcap_cache" not in d:
            d["mcap_cache"] = {}
        return d
    return {"trades": [], "last_updated": None, "seen_keys": [], "mcap_cache": {}}


def save_data(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, separators=(",", ":"))


def dedup_key(trade):
    company = re.sub(r"\s+", "", trade.get("company", "").lower())
    person = re.sub(r"\s+", "", trade.get("person", "").lower())
    date = trade.get("date", "")
    qty = abs(trade.get("qty", 0))
    txn = trade.get("txn_type", "").lower()[0] if trade.get("txn_type") else "?"
    return f"{company}_{person}_{date}_{qty}_{txn}"


def normalize_date(s):
    if not s:
        return ""
    s = str(s).strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d-%m-%Y", "%d %b %Y", "%d-%b-%Y", "%d-%B-%Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if "T" in s:
        return s[:10]
    return s[:10] if len(s) >= 10 else s


def normalize_category(cat):
    c = (cat or "").lower()
    if "promoter" in c:
        return "Promoter"
    if "director" in c:
        return "Director"
    if "kmp" in c or "key managerial" in c:
        return "KMP"
    return "Other"


def normalize_txn(txn):
    t = (txn or "").lower()
    if any(w in t for w in ("buy", "purchase", "acqui")):
        return "Buy"
    if any(w in t for w in ("sell", "sale", "offload", "dispose")):
        return "Sell"
    return "Other"


def safe_float(v):
    try:
        return float(str(v or 0).replace(",", "").strip() or 0)
    except Exception:
        return 0.0


def safe_int(v):
    try:
        return int(float(str(v or 0).replace(",", "").strip() or 0))
    except Exception:
        return 0


# ─── BSE ─────────────────────────────────────────────────────────────────────

def _get_bse_session():
    """Warm BSE session (get homepage cookies) like we do for NSE."""
    session = requests.Session()
    session.headers.update(BSE_HEADERS)
    try:
        r = session.get("https://www.bseindia.com/", timeout=15)
        log(f"  BSE session: {r.status_code}, cookies: {len(r.cookies)}")
    except Exception as e:
        log(f"  BSE session error: {e}")
    # Update to JSON-focused headers after cookie warmup
    session.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/corporates/insider_trading_new",
        "X-Requested-With": "XMLHttpRequest",
    })
    return session


def fetch_bse_insider(from_date, to_date):
    """
    Fetch BSE insider trades using the new getCorp_Regulation_ng endpoint.
    Date range must be ≤ ~2 weeks or the API times out — caller should chunk.
    """
    session = _get_bse_session()
    # New endpoint uses YYYY-MM-DD format
    url = "https://api.bseindia.com/BseIndiaAPI/api/getCorp_Regulation_ng/w"
    params = {
        "scripCode": "",
        "Regulation": "",
        "fromDT": from_date,   # YYYY-MM-DD
        "ToDate": to_date,     # YYYY-MM-DD
        "Isdefault": "0",
    }

    trades = []
    try:
        r = session.get(url, params=params, timeout=25)
        if r.status_code != 200:
            log(f"  BSE insider: HTTP {r.status_code}")
            return trades
        ct = r.headers.get("Content-Type", "")
        if "html" in ct or not r.text.strip().startswith("{"):
            log(f"  BSE insider: non-JSON ({ct[:40]})")
            return trades

        data = r.json()
        rows = data.get("Table", []) if isinstance(data, dict) else []

        for row in rows:
            try:
                # Fld_SecurityNo = shares in THIS transaction (traded qty)
                # Fld_SecurityNoPrior = holding before, Fld_SecurityNoPost = holding after
                qty_traded = safe_int(row.get("Fld_SecurityNo", 0))

                value_rs = safe_float(row.get("Fld_SecurityValue", 0))
                value_cr = round(value_rs / 1e7, 2) if value_rs else 0.0
                price = round(value_rs / qty_traded, 2) if qty_traded and value_rs else 0.0

                txn_raw = (row.get("ModeOfAquisation") or row.get("Fld_TransactionType") or "").strip()
                mode_raw = txn_raw  # e.g. "Market Sale", "Market Purchase", "Preferential Allotment"

                t = {
                    "date": normalize_date(row.get("Fld_FromDate", "") or row.get("Fld_StampDate", "")),
                    "company": (row.get("Companyname") or "").strip(),
                    "scrip_code": str(row.get("Fld_ScripCode", "") or ""),
                    "nse_symbol": (row.get("Fld_TradeExchange") or "").strip()
                                  if (row.get("Fld_TradeExchange") or "") not in ("BSE", "NSE", "") else "",
                    "exchange": "BSE",
                    "txn_type": normalize_txn(txn_raw),
                    "mode": mode_raw,
                    "person": (row.get("Fld_PromoterName") or "").strip(),
                    "category": normalize_category(row.get("Fld_PersonCatgName", "")),
                    "qty": qty_traded,
                    "price": price,
                    "value_cr": value_cr,
                    "security_type": (row.get("Fld_SecurityTypeName") or "Equity Shares").strip(),
                    "before_pct": safe_float(row.get("Fld_PercentofShareholdingPre", 0)),
                    "after_pct": safe_float(row.get("Fld_PercentofShareholdingPost", 0)),
                    "industry": "",
                }
                if t["company"] and t["date"]:
                    trades.append(t)
            except Exception as e:
                log(f"  BSE row error: {e}")

    except Exception as e:
        log(f"  BSE insider fetch error: {e}")

    log(f"  BSE chunk {from_date} to {to_date}: {len(trades)} trades")
    return trades


# ─── NSE ─────────────────────────────────────────────────────────────────────

def _get_nse_client():
    client = httpx.Client(http2=True, follow_redirects=True, timeout=20)
    client.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/134.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        r = client.get("https://www.nseindia.com")
        log(f"  NSE session: {r.status_code}, cookies: {len(r.cookies)}")
    except Exception as e:
        log(f"  NSE session error: {e}")
        client.close()
        return None
    client.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/",
    })
    return client


def fetch_nse_insider(from_date, to_date):
    client = _get_nse_client()
    if not client:
        return []

    from_nse = datetime.strptime(from_date, "%Y-%m-%d").strftime("%d-%m-%Y")
    to_nse = datetime.strptime(to_date, "%Y-%m-%d").strftime("%d-%m-%Y")
    url = f"https://www.nseindia.com/api/corporates-pit?index=equities&from_date={from_nse}&to_date={to_nse}"

    try:
        r = client.get(url)
        if r.status_code in (401, 403):
            client.close()
            client = _get_nse_client()
            if not client:
                return []
            r = client.get(url)
        r.raise_for_status()
        raw_data = r.json() if r.text.strip() else {}
    except Exception as e:
        log(f"  NSE insider fetch error: {e}")
        return []
    finally:
        try:
            client.close()
        except Exception:
            pass

    rows = raw_data.get("data", []) if isinstance(raw_data, dict) else []
    trades = []

    for row in (rows or []):
        try:
            qty = safe_int(row.get("secAcq", 0))
            val_raw = safe_float(row.get("secVal", 0))
            # NSE secVal is always in rupees
            value_cr = round(val_raw / 1e7, 2)
            price = round(val_raw / qty, 2) if qty and val_raw else 0.0

            date_raw = row.get("acqfromDt") or row.get("tdpDt") or ""

            t = {
                "date": normalize_date(date_raw),
                "company": (row.get("company") or "").strip(),
                "scrip_code": "",
                "nse_symbol": (row.get("symbol") or "").strip(),
                "exchange": "NSE",
                "txn_type": normalize_txn(row.get("tdpTransactionType", "")),
                "mode": (row.get("acqMode") or "").strip(),
                "person": (row.get("personName") or "").strip(),
                "category": normalize_category(row.get("personCategory", "")),
                "qty": qty,
                "price": price,
                "value_cr": value_cr,
                "security_type": (row.get("secType") or "Equity Shares").strip(),
                "before_pct": safe_float(row.get("befAcqSharesPer", 0)),
                "after_pct": safe_float(row.get("afterAcqSharesPer", 0)),
                "industry": "",
            }
            if t["company"] and t["date"]:
                trades.append(t)
        except Exception as e:
            log(f"  NSE row error: {e}")

    log(f"  NSE: {len(trades)} insider trades")
    return trades


# ─── Market Cap Enrichment ────────────────────────────────────────────────────

def _format_mcap(raw):
    cr = raw / 1e7
    if cr >= 100000: return f"{cr/100000:.2f}L Cr"
    if cr >= 1000:   return f"{cr/1000:.2f}K Cr"
    return f"{cr:.0f} Cr"

def fetch_mcap_bse(session, scrip_code):
    try:
        url = f"https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w?flag=&scripcode={scrip_code}"
        r = session.get(url, timeout=8)
        if r.status_code != 200: return None
        ct = r.headers.get("Content-Type", "")
        if "html" in ct or not r.text.strip().startswith("{"): return None
        d = r.json()
        mkt = str(d.get("MktCapFull") or d.get("MktCapFF") or "").replace(",", "").strip()
        val = float(mkt) if mkt else 0
        return {"value": val * 1e7, "formatted": _format_mcap(val * 1e7)} if val > 0 else None
    except Exception:
        return None

def fetch_mcap_nse(client, symbol):
    try:
        from urllib.parse import quote
        r = client.get(f"https://www.nseindia.com/api/quote-equity?symbol={quote(symbol, safe='')}")
        if r.status_code != 200: return None
        d = r.json()
        price  = d.get("priceInfo", {}).get("lastPrice", 0)
        issued = d.get("securityInfo", {}).get("issuedSize", 0)
        if price and issued:
            raw = price * issued
            return {"value": raw, "formatted": _format_mcap(raw)}
    except Exception:
        pass
    return None

def enrich_market_caps(trades, mcap_cache):
    """Fetch market cap for each unique company and add to trades.
    mcap_cache: persistent dict { 'BSE:scrip_code' | 'NSE:symbol' -> {value, formatted} }
    Fetches only symbols not already in cache. Max 300 new lookups per run.
    """
    log("Enriching market caps...")

    # Find symbols we still need to fetch (not in cache yet)
    need_bse = []
    need_nse = []
    for t in trades:
        if t.get("scrip_code") and f"BSE:{t['scrip_code']}" not in mcap_cache:
            if t["scrip_code"] not in need_bse:
                need_bse.append(t["scrip_code"])
        if t.get("nse_symbol") and f"NSE:{t['nse_symbol']}" not in mcap_cache:
            if t["nse_symbol"] not in need_nse:
                need_nse.append(t["nse_symbol"])

    log(f"  Cache size: {len(mcap_cache)}, need BSE: {len(need_bse)}, need NSE: {len(need_nse)}")

    # Cap at 300 new lookups per run to stay within timeout
    MAX_PER_RUN = 300
    need_bse = need_bse[:MAX_PER_RUN]
    need_nse = need_nse[:max(0, MAX_PER_RUN - len(need_bse))]

    bse_session = _get_bse_session()

    # Fetch BSE mcaps
    for code in need_bse:
        result = fetch_mcap_bse(bse_session, code)
        mcap_cache[f"BSE:{code}"] = result  # None = tried but failed
        time.sleep(0.05)

    # Fetch NSE mcaps — probe first to check if session works
    nse_client = None
    if need_nse:
        nse_client = _get_nse_client()
        # Quick probe: test one symbol before committing to all 300
        if nse_client and need_nse:
            probe = fetch_mcap_nse(nse_client, need_nse[0])
            if probe is None:
                # NSE API blocked today — skip, will retry next run
                log("  NSE mcap probe returned None — skipping NSE enrichment today")
                nse_client.close()
                nse_client = None

    if nse_client:
        for sym in need_nse:
            result = fetch_mcap_nse(nse_client, sym)
            mcap_cache[f"NSE:{sym}"] = result
            time.sleep(0.05)
        nse_client.close()

    # Apply cache to trades
    enriched = 0
    for t in trades:
        mcap = None
        if t.get("scrip_code"):
            mcap = mcap_cache.get(f"BSE:{t['scrip_code']}")
        if not mcap and t.get("nse_symbol"):
            mcap = mcap_cache.get(f"NSE:{t['nse_symbol']}")
        if mcap:
            t["market_cap"] = mcap["value"]
            t["market_cap_fmt"] = mcap["formatted"]
            enriched += 1
        else:
            t["market_cap"] = None
            t["market_cap_fmt"] = "N/A"
    log(f"  Enriched {enriched}/{len(trades)} trades with market cap")
    return trades


# ─── Merge + Dedup ────────────────────────────────────────────────────────────

def merge_trades(bse, nse, seen_keys_set):
    new_trades = []
    for t in bse + nse:
        key = dedup_key(t)
        if key and key not in seen_keys_set:
            seen_keys_set.add(key)
            new_trades.append(t)
    return new_trades


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    log(f"Insider worker starting — {datetime.utcnow().isoformat()}")
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)

    existing_data = load_existing()
    existing_trades = existing_data.get("trades", [])
    seen_keys = set(existing_data.get("seen_keys", []))

    # Allow forcing a full backfill via env var (used by workflow_dispatch)
    force_backfill = os.environ.get("INSIDER_BACKFILL", "").lower() in ("1", "true", "yes")

    if not existing_trades or force_backfill:
        log(f"Full backfill mode (force={force_backfill}) — fetching 1 year")
        # NSE: one call (no cap)
        nse_from = (ist_now - timedelta(days=365)).date().strftime("%Y-%m-%d")
        nse_to = ist_now.date().strftime("%Y-%m-%d")
        log(f"NSE: {nse_from} to {nse_to} (single call)")
        # NSE works in monthly chunks for safety
        all_new = []
        end_date = ist_now.date()
        for i in range(12):
            chunk_end = end_date - timedelta(days=30 * i)
            chunk_start = chunk_end - timedelta(days=30)
            nse_f = chunk_start.strftime("%Y-%m-%d")
            nse_t = chunk_end.strftime("%Y-%m-%d")
            nse = fetch_nse_insider(nse_f, nse_t)
            new_t = merge_trades([], nse, seen_keys)
            all_new.extend(new_t)
            time.sleep(2)
        log(f"NSE backfill: {len(all_new)} trades")

        # BSE: daily chunks (25-record cap per response)
        log("BSE: daily chunks (1 year = 366 calls)...")
        bse_count = 0
        capped_days = []
        for i in range(366):
            day = end_date - timedelta(days=i)
            d_str = day.strftime("%Y-%m-%d")
            bse = fetch_bse_insider(d_str, d_str)
            if len(bse) >= 25:
                capped_days.append(d_str)
            new_t = merge_trades(bse, [], seen_keys)
            all_new.extend(new_t)
            bse_count += len(new_t)
            if (i + 1) % 50 == 0:
                log(f"  BSE progress: {i+1}/366 days, +{bse_count} new BSE trades")
            time.sleep(0.4)
        log(f"BSE backfill: {bse_count} trades")
        if capped_days:
            log(f"  WARN: {len(capped_days)} days hit 25-cap")

        if force_backfill:
            # Replace existing data with fresh backfill
            existing_trades = all_new
        else:
            existing_trades = all_new
    else:
        # Incremental: yesterday + today as separate daily calls (BSE cap)
        today = ist_now.date()
        yesterday = today - timedelta(days=1)

        bse_all = []
        for d in (yesterday, today):
            bse_all.extend(fetch_bse_insider(d.strftime("%Y-%m-%d"), d.strftime("%Y-%m-%d")))

        # NSE handles range fine
        nse = fetch_nse_insider(yesterday.strftime("%Y-%m-%d"), today.strftime("%Y-%m-%d"))

        new_t = merge_trades(bse_all, nse, seen_keys)
        log(f"Incremental: BSE={len(bse_all)} NSE={len(nse)} | new={len(new_t)}")
        existing_trades = new_t + existing_trades

    # Trim to 1 year
    cutoff = (ist_now - timedelta(days=365)).strftime("%Y-%m-%d")
    before = len(existing_trades)
    existing_trades = [t for t in existing_trades if t.get("date", "") >= cutoff]
    log(f"Trimmed to 1yr: {before} → {len(existing_trades)}")

    # Sort newest first
    existing_trades.sort(key=lambda x: x.get("date", ""), reverse=True)

    # Enrich with market cap using persistent cache
    mcap_cache = existing_data.get("mcap_cache", {})
    trades_needing_mcap = [t for t in existing_trades if t.get("market_cap") is None]
    if trades_needing_mcap:
        enrich_market_caps(trades_needing_mcap, mcap_cache)
    else:
        log("All trades already have market cap data.")

    out = {
        "trades": existing_trades,
        "last_updated": ist_now.strftime("%Y-%m-%dT%H:%M:%S"),
        "seen_keys": list(seen_keys)[:60000],
        "mcap_cache": mcap_cache,
    }
    save_data(out)
    log(f"Done — {len(existing_trades)} trades saved.")


if __name__ == "__main__":
    main()
