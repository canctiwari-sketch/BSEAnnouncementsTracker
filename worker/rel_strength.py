"""
7-day Relative Strength builder.

Downloads NSE daily bhavcopies (one CSV per trading day with EVERY listed
stock's close — mainboard + SME, no per-symbol API calls) from the archives
host, computes each stock's 7-calendar-day return and its relative strength
vs the Nifty 500 over the same window, and writes data/rel_strength.json for
the frontend's Rel Strength tab.

Design notes (hard-won from the raw data):
- Universe: mainboard symbols are filtered against NSE's official EQUITY_L.csv
  — ETFs trade in the EQ series and are indistinguishable in the bhavcopy
  (FinInstrmTp is "STK" for everything), but they're absent from EQUITY_L.
  SME series (SM/ST) are equities by definition and always included.
- Returns are CHAIN-LINKED per trading day (close/prev-close), not a raw
  close-vs-close 7 days apart, and any single-day factor outside sane circuit
  bounds is neutralized: NSE's PrvsClsgPric is NOT split/bonus-adjusted
  (a 1:10 split shows as a fake -90% "return"), and real single-day moves are
  capped by circuit limits (~20%), so factors beyond +/-35% are corporate
  actions, not price action.
- Market cap joined from NSE's official semi-annual mcap file (same source
  comm_profile.py uses — covers the full universe including SME).
"""
import io
import os
import csv
import json
import zipfile
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_FILE = os.path.join(DATA_DIR, "rel_strength.json")

BHAV_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
IDX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d}.csv"
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BENCHMARK = "Nifty 500"

SERIES = {"EQ", "BE", "SM", "ST"}
SME_SERIES = {"SM", "ST"}

LOOKBACK_CALENDAR_DAYS = 7   # "past 7 days"
SEARCH_WINDOW_DAYS = 16      # how far back to look for trading days

# Single-day close/prev-close factors outside these bounds are corporate
# actions (split/bonus ex-date), not price moves — circuit limits cap real
# daily moves at ~20%. Neutralize them instead of poisoning the 7d return.
FACTOR_MIN, FACTOR_MAX = 0.65, 1.50


def log(m):
    print(m, flush=True)


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/134.0.0.0 Safari/537.36"})
    return s


def fetch_bhavcopy(sess, day):
    """One trading day: {symbol: (close, prev_close, name, series)} or None
    if that date has no file (weekend/holiday)."""
    url = BHAV_URL.format(d=day.strftime("%Y%m%d"))
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            return None
        z = zipfile.ZipFile(io.BytesIO(r.content))
        text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
    except Exception as e:
        log(f"  bhavcopy {day:%Y-%m-%d} error: {e}")
        return None
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("SctySrs") or "").strip() not in SERIES:
            continue
        sym = (row.get("TckrSymb") or "").strip()
        try:
            close = float(row.get("ClsPric") or 0)
            prev = float(row.get("PrvsClsgPric") or 0)
        except ValueError:
            continue
        if sym and close > 0:
            out[sym] = (close, prev, (row.get("FinInstrmNm") or "").strip(),
                        (row.get("SctySrs") or "").strip())
    return out or None


def fetch_index_close(sess, day, index_name=BENCHMARK):
    url = IDX_URL.format(d=day.strftime("%d%m%Y"))
    try:
        r = sess.get(url, timeout=30)
        if r.status_code != 200:
            return None
        for row in csv.DictReader(io.StringIO(r.text)):
            if (row.get("Index Name") or "").strip() == index_name:
                v = (row.get("Closing Index Value") or "").replace(",", "").strip()
                return float(v) if v and v != "-" else None
    except Exception as e:
        log(f"  index file {day:%Y-%m-%d} error: {e}")
    return None


def fetch_equity_symbols(sess):
    """Official mainboard equity list — excludes ETFs (which share the EQ
    series in bhavcopies but aren't equities). Empty set on failure means
    'no filter' rather than 'drop everything'."""
    try:
        r = sess.get(EQUITY_LIST_URL, timeout=30)
        if r.status_code == 200:
            syms = {(row.get("SYMBOL") or "").strip()
                    for row in csv.DictReader(io.StringIO(r.text))}
            syms.discard("")
            return syms
    except Exception as e:
        log(f"EQUITY_L fetch error: {e}")
    return set()


def load_mcap_map():
    """symbol -> mcap Cr from NSE's official semi-annual mcap file."""
    try:
        import comm_profile
        by_sym, _ = comm_profile.nse_xlsx_mcap()
        return by_sym
    except Exception as e:
        log(f"mcap map unavailable ({e}) — rows will show N/A")
        return {}


def main():
    log(f"Rel-strength starting {datetime.utcnow().isoformat()}")
    sess = _session()
    today = datetime.utcnow() + timedelta(hours=5, minutes=30)  # IST

    # Latest trading day: walk back from today until a bhavcopy exists.
    latest_day, latest = None, None
    probe = today
    for _ in range(SEARCH_WINDOW_DAYS):
        data = fetch_bhavcopy(sess, probe)
        if data:
            latest_day, latest = probe, data
            break
        probe -= timedelta(days=1)
    if not latest:
        log("::error::No bhavcopy found in search window — NSE archives down?")
        raise SystemExit(1)
    log(f"Latest trading day: {latest_day:%Y-%m-%d} ({len(latest)} symbols)")

    # Base day: most recent trading day ON/BEFORE latest - 7 calendar days.
    base_day, base = None, None
    probe = latest_day - timedelta(days=LOOKBACK_CALENDAR_DAYS)
    for _ in range(SEARCH_WINDOW_DAYS):
        data = fetch_bhavcopy(sess, probe)
        if data:
            base_day, base = probe, data
            break
        probe -= timedelta(days=1)
    if not base:
        log("::error::No base-day bhavcopy found — cannot compute returns")
        raise SystemExit(1)
    log(f"Base trading day: {base_day:%Y-%m-%d} ({len(base)} symbols)")

    # All trading days AFTER base up to latest (for chain-linked returns).
    day_data = []
    probe = base_day + timedelta(days=1)
    while probe <= latest_day:
        if probe.date() == latest_day.date():
            day_data.append((probe, latest))
        else:
            d = fetch_bhavcopy(sess, probe)
            if d:
                day_data.append((probe, d))
        probe += timedelta(days=1)
    log(f"Trading days in window: {len(day_data)}")

    idx_latest = fetch_index_close(sess, latest_day)
    idx_base = fetch_index_close(sess, base_day)
    idx_pct = None
    if idx_latest and idx_base:
        idx_pct = (idx_latest - idx_base) / idx_base * 100
        log(f"{BENCHMARK}: {idx_base:.2f} -> {idx_latest:.2f} ({idx_pct:+.2f}%)")
    else:
        log(f"::warning::{BENCHMARK} close unavailable — RS column will be null")

    equity_syms = fetch_equity_symbols(sess)
    log(f"EQUITY_L symbols: {len(equity_syms)}")
    mcap_by_sym = load_mcap_map()
    log(f"mcap map: {len(mcap_by_sym)} symbols")

    # Chain-link daily factors per symbol, neutralizing corporate-action jumps.
    factors = {}       # symbol -> cumulative factor
    adjusted = set()   # symbols where a corp-action day was neutralized
    for _, data in day_data:
        for sym, (close, prev, _name, _series) in data.items():
            if prev <= 0:
                continue
            fct = close / prev
            if not (FACTOR_MIN <= fct <= FACTOR_MAX):
                adjusted.add(sym)
                fct = 1.0
            factors[sym] = factors.get(sym, 1.0) * fct
    if adjusted:
        log(f"Neutralized corporate-action jumps for {len(adjusted)} symbols")

    rows = []
    for sym, (close, _prev, name, series) in latest.items():
        if sym not in base:
            continue  # not traded at window start (new listing etc.)
        if series not in SME_SERIES and equity_syms and sym not in equity_syms:
            continue  # EQ-series non-equity (ETF etc.)
        f = factors.get(sym)
        if f is None:
            continue
        pct = (f - 1.0) * 100
        mc = mcap_by_sym.get(sym)
        rows.append({
            "symbol": sym,
            "name": name,
            "sme": series in SME_SERIES,
            "close": round(close, 2),
            "pct_7d": round(pct, 2),
            "rs": round(pct - idx_pct, 2) if idx_pct is not None else None,
            "mcap_cr": round(mc, 0) if mc else None,
            "adj": sym in adjusted or None,  # split/bonus in window
        })
    rows.sort(key=lambda r: r["rs"] if r["rs"] is not None else r["pct_7d"], reverse=True)
    log(f"rows: {len(rows)}")

    out = {
        "generated_at": datetime.utcnow().isoformat(),
        "latest_date": latest_day.strftime("%Y-%m-%d"),
        "base_date": base_day.strftime("%Y-%m-%d"),
        "benchmark": BENCHMARK,
        "benchmark_pct": round(idx_pct, 2) if idx_pct is not None else None,
        "rows": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(out, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"Saved {OUT_FILE}")


if __name__ == "__main__":
    main()
