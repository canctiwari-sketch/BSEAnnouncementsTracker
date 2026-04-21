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
            return json.load(f)
    return {"trades": [], "last_updated": None, "seen_keys": []}


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

def fetch_bse_insider(from_date, to_date):
    session = requests.Session()
    session.headers.update(BSE_HEADERS)
    from_bse = datetime.strptime(from_date, "%Y-%m-%d").strftime("%d/%m/%Y")
    to_bse = datetime.strptime(to_date, "%Y-%m-%d").strftime("%d/%m/%Y")

    trades = []
    page = 1

    while True:
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/InsiderTrading1/w"
            f"?pageno={page}&strdate={from_bse}&enddate={to_bse}"
            f"&ddlindustry=&scripcode=&txntype=&ddlcategory="
        )
        try:
            r = session.get(url, timeout=20)
            if r.status_code != 200:
                log(f"BSE insider p{page}: {r.status_code}")
                break
            data = r.json()
            rows = data.get("Table", []) if isinstance(data, dict) else []
            if not rows:
                break

            for row in rows:
                try:
                    qty = safe_int(row.get("NoOfSecurities", 0))
                    price = safe_float(row.get("Price", 0))
                    value_rs = safe_float(row.get("Value", 0))
                    value_cr = round(value_rs / 1e7, 2) if value_rs else round(qty * price / 1e7, 2)

                    t = {
                        "date": normalize_date(row.get("DtBuySell", "")),
                        "company": (row.get("CompanyName") or "").strip(),
                        "scrip_code": str(row.get("ScripCode", "") or ""),
                        "nse_symbol": "",
                        "exchange": "BSE",
                        "txn_type": normalize_txn(row.get("TxnType", "")),
                        "mode": (row.get("AcqMode") or "").strip(),
                        "person": (row.get("PersonName") or "").strip(),
                        "category": normalize_category(row.get("Category", "")),
                        "qty": qty,
                        "price": price,
                        "value_cr": value_cr,
                        "security_type": (row.get("TypeofSecurity") or "Equity Shares").strip(),
                        "before_pct": safe_float(row.get("BeforeTransactionHoldingPct", 0)),
                        "after_pct": safe_float(row.get("AfterTransactionHoldingPct", 0)),
                        "industry": (row.get("Industry") or "").strip(),
                    }
                    if t["company"] and t["date"]:
                        trades.append(t)
                except Exception as e:
                    log(f"BSE row error: {e}")

            total_rows = data.get("Table1", [{}])
            total = safe_int(total_rows[0].get("TotalCount", 0)) if total_rows else 0
            if not total or page * 50 >= total or len(rows) < 50:
                break
            page += 1
            time.sleep(0.5)

        except Exception as e:
            log(f"BSE insider fetch error: {e}")
            break

    log(f"  BSE: {len(trades)} insider trades")
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
            # NSE secVal sometimes in rupees, sometimes in crores — heuristic
            value_cr = round(val_raw / 1e7, 2) if val_raw > 1e5 else round(val_raw, 2)
            price = round((value_cr * 1e7) / qty, 2) if qty and value_cr else 0.0

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

    if not existing_trades:
        log("No existing data — backfilling 1 year in monthly chunks...")
        all_new = []
        end_date = ist_now.date()
        for i in range(12):
            chunk_end = end_date - timedelta(days=30 * i)
            chunk_start = chunk_end - timedelta(days=30)
            from_str = chunk_start.strftime("%Y-%m-%d")
            to_str = chunk_end.strftime("%Y-%m-%d")
            log(f"Chunk {i+1}/12: {from_str} → {to_str}")
            bse = fetch_bse_insider(from_str, to_str)
            nse = fetch_nse_insider(from_str, to_str)
            new_t = merge_trades(bse, nse, seen_keys)
            all_new.extend(new_t)
            log(f"  +{len(new_t)} new (total so far: {len(all_new)})")
            time.sleep(2)
        existing_trades = all_new
    else:
        # Incremental: yesterday + today
        today = ist_now.date()
        yesterday = today - timedelta(days=1)
        from_str = yesterday.strftime("%Y-%m-%d")
        to_str = today.strftime("%Y-%m-%d")
        log(f"Incremental: {from_str} → {to_str}")
        bse = fetch_bse_insider(from_str, to_str)
        nse = fetch_nse_insider(from_str, to_str)
        new_t = merge_trades(bse, nse, seen_keys)
        log(f"New trades: {len(new_t)}")
        existing_trades = new_t + existing_trades

    # Trim to 1 year
    cutoff = (ist_now - timedelta(days=365)).strftime("%Y-%m-%d")
    before = len(existing_trades)
    existing_trades = [t for t in existing_trades if t.get("date", "") >= cutoff]
    log(f"Trimmed to 1yr: {before} → {len(existing_trades)}")

    # Sort newest first
    existing_trades.sort(key=lambda x: x.get("date", ""), reverse=True)

    out = {
        "trades": existing_trades,
        "last_updated": ist_now.strftime("%Y-%m-%dT%H:%M:%S"),
        "seen_keys": list(seen_keys)[:60000],
    }
    save_data(out)
    log(f"Done — {len(existing_trades)} trades saved.")


if __name__ == "__main__":
    main()
