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
import re
import csv
import json
import zipfile
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_FILE = os.path.join(DATA_DIR, "rel_strength.json")

BHAV_URL = "https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{d}_F_0000.csv.zip"
BSE_BHAV_URL = "https://www.bseindia.com/download/BhavCopy/Equity/BhavCopy_BSE_CM_0_0_0_{d}_F_0000.CSV"
IDX_URL = "https://nsearchives.nseindia.com/content/indices/ind_close_all_{d}.csv"
EQUITY_LIST_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
BENCHMARK = "Nifty 500"

SERIES = {"EQ", "BE", "SM", "ST"}
SME_SERIES = {"SM", "ST"}

# BSE groups: A/B mainboard, T trade-to-trade, X/XT exclusively-BSE-listed,
# M/MT SME. Excluded: F (debt), G (govt), Z (suspended/defaulter).
BSE_SERIES = {"A", "B", "T", "X", "XT", "M", "MT"}
BSE_SME_SERIES = {"M", "MT"}
# BSE has no clean equity-only list like NSE's EQUITY_L; ETFs trade in group
# B and are identified by name.
_ETF_NAME_RE = re.compile(r"\bETF\b|BEES\b|MUTUAL FUND|FUND OF FUND", re.IGNORECASE)

LOOKBACK_CALENDAR_DAYS = 7    # short window: "past 7 days"
LOOKBACK_3M_CALENDAR_DAYS = 91  # long window: ~3 months (leadership)
SEARCH_WINDOW_DAYS = 16       # how far back to look for a trading day

# Single-day close/prev-close factors outside these bounds are corporate
# actions (split/bonus ex-date), not price moves — circuit limits cap real
# daily moves at ~20%. Neutralize them instead of poisoning the 7d return.
FACTOR_MIN, FACTOR_MAX = 0.65, 1.50

# Market-cap buckets (₹ Cr) — standard Indian advisory thresholds.
def cap_bucket(mcap_cr):
    if mcap_cr is None:
        return None
    if mcap_cr < 500:
        return "micro"
    if mcap_cr < 5000:
        return "small"
    if mcap_cr < 20000:
        return "mid"
    return "large"


def log(m):
    print(m, flush=True)


def _session():
    s = requests.Session()
    s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                     "AppleWebKit/537.36 (KHTML, like Gecko) "
                                     "Chrome/134.0.0.0 Safari/537.36"})
    return s


def fetch_bhavcopy(sess, day, exchange="NSE"):
    """One trading day: {symbol: (close, prev_close, name, series, isin)} or
    None if that date has no file (weekend/holiday). Same UDiFF format on
    both exchanges; NSE ships a zip, BSE a plain CSV."""
    try:
        if exchange == "NSE":
            r = sess.get(BHAV_URL.format(d=day.strftime("%Y%m%d")), timeout=30)
            if r.status_code != 200:
                return None
            z = zipfile.ZipFile(io.BytesIO(r.content))
            text = z.read(z.namelist()[0]).decode("utf-8", errors="replace")
        else:
            r = sess.get(BSE_BHAV_URL.format(d=day.strftime("%Y%m%d")), timeout=30,
                         headers={"Referer": "https://www.bseindia.com/"})
            if r.status_code != 200:
                return None
            text = r.text
    except Exception as e:
        log(f"  {exchange} bhavcopy {day:%Y-%m-%d} error: {e}")
        return None
    series_ok = SERIES if exchange == "NSE" else BSE_SERIES
    out = {}
    for row in csv.DictReader(io.StringIO(text)):
        if (row.get("SctySrs") or "").strip() not in series_ok:
            continue
        sym = (row.get("TckrSymb") or "").strip()
        name = (row.get("FinInstrmNm") or "").strip()
        if exchange == "BSE":
            # ETFs and closed-end MF schemes share BSE's equity groups and
            # there's no clean equity-only list to filter against: exclude by
            # name, plus digit-leading symbols (BSE's bond/MF code convention).
            if _ETF_NAME_RE.search(name) or sym[:1].isdigit():
                continue
        try:
            close = float(row.get("ClsPric") or 0)
            prev = float(row.get("PrvsClsgPric") or 0)
        except ValueError:
            continue
        if sym and close > 0:
            out[sym] = (close, prev, name,
                        (row.get("SctySrs") or "").strip(),
                        (row.get("ISIN") or "").strip())
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


def find_bhav_on_or_before(sess, target, exchange="NSE"):
    """Walk back from target until a trading day's bhavcopy exists.
    Returns (day, bhav_map) or (None, None)."""
    probe = target
    for _ in range(SEARCH_WINDOW_DAYS):
        d = fetch_bhavcopy(sess, probe, exchange)
        if d:
            return probe, d
        probe -= timedelta(days=1)
    return None, None


def download_span(sess, span_start, latest_day, latest_bhav, exchange="NSE"):
    """Every trading day's bhavcopy in (span_start-1, latest_day], ordered.
    Reuses the already-fetched latest_bhav for the final day."""
    out = []
    probe = span_start
    while probe <= latest_day:
        if probe.date() == latest_day.date():
            out.append((probe, latest_bhav))
        else:
            d = fetch_bhavcopy(sess, probe, exchange)
            if d:
                out.append((probe, d))
        probe += timedelta(days=1)
    return out


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

    # Two lookback windows: 7-day (recent momentum) and ~3-month (leadership).
    # Base day = most recent trading day ON/BEFORE latest - N calendar days.
    base7_day, base7 = find_bhav_on_or_before(sess, latest_day - timedelta(days=LOOKBACK_CALENDAR_DAYS))
    base3_day, base3 = find_bhav_on_or_before(sess, latest_day - timedelta(days=LOOKBACK_3M_CALENDAR_DAYS))
    if not base7:
        log("::error::No 7-day base bhavcopy found — cannot compute returns")
        raise SystemExit(1)
    log(f"7d base: {base7_day:%Y-%m-%d}" +
        (f"  ·  3m base: {base3_day:%Y-%m-%d}" if base3 else "  ·  3m base: UNAVAILABLE"))

    # Chain-link daily factors per symbol, neutralizing corporate-action jumps.
    def chain_factors(day_maps):
        """symbol -> (cumulative factor, corp-action-adjusted?)"""
        factors, adjusted = {}, set()
        for data in day_maps:
            for sym, (close, prev, _n, _s, _i) in data.items():
                if prev <= 0:
                    continue
                fct = close / prev
                if not (FACTOR_MIN <= fct <= FACTOR_MAX):
                    adjusted.add(sym)
                    fct = 1.0
                factors[sym] = factors.get(sym, 1.0) * fct
        return factors, adjusted

    # Download the full span once; chain over all of it (3-month return) and
    # over the 7-day sub-span. One download pass feeds both windows.
    span_start = (base3_day or base7_day) + timedelta(days=1)
    nse_days = download_span(sess, span_start, latest_day, latest)
    log(f"NSE trading days downloaded: {len(nse_days)}")
    factors3, adj3 = chain_factors([d for _, d in nse_days])
    factors7, adjusted = chain_factors([d for dt, d in nse_days if dt > base7_day])
    if adjusted:
        log(f"NSE: neutralized corporate-action jumps for {len(adjusted)} symbols (7d)")

    idx_latest = fetch_index_close(sess, latest_day)
    idx7 = fetch_index_close(sess, base7_day)
    idx3 = fetch_index_close(sess, base3_day) if base3_day else None
    idx_pct = (idx_latest - idx7) / idx7 * 100 if (idx_latest and idx7) else None
    idx_pct_3m = (idx_latest - idx3) / idx3 * 100 if (idx_latest and idx3) else None
    log(f"{BENCHMARK} 7d: {idx_pct:+.2f}%" if idx_pct is not None else f"{BENCHMARK} 7d: N/A")
    if idx_pct_3m is not None:
        log(f"{BENCHMARK} 3m: {idx_pct_3m:+.2f}%")

    equity_syms = fetch_equity_symbols(sess)
    log(f"EQUITY_L symbols: {len(equity_syms)}")
    mcap_by_sym = load_mcap_map()
    log(f"mcap map: {len(mcap_by_sym)} symbols")

    def make_row(sym, close, prev, name, series, pct7, pct3, adj, sme, bse, mcap):
        # 1-day change from the latest bhavcopy (close vs prev close). Same
        # corp-action guard as the chains: a split ex-date on the latest day
        # would otherwise read as a fake -90%.
        pct_1d = None
        if prev and prev > 0 and FACTOR_MIN <= close / prev <= FACTOR_MAX:
            pct_1d = round((close - prev) / prev * 100, 2)
        mc = round(mcap, 0) if mcap else None
        return {
            "symbol": sym,
            "name": name,
            "sme": sme,
            "bse": bse or None,   # True only for BSE-only listings
            "close": round(close, 2),
            "pct_1d": pct_1d,
            "pct_7d": round(pct7, 2),
            "rs": round(pct7 - idx_pct, 2) if idx_pct is not None else None,
            # pct3 is None when the stock has no full 3-month history (listed
            # mid-window) — rs_3m then null too.
            "pct_3m": round(pct3, 2) if pct3 is not None else None,
            "rs_3m": round(pct3 - idx_pct_3m, 2) if (pct3 is not None and idx_pct_3m is not None) else None,
            "mcap_cr": mc,
            "bucket": cap_bucket(mc),
            "adj": adj or None,   # split/bonus in window
        }

    def pct3_for(sym, base_map, factors_map):
        """3-month return %, or None if the stock lacks full 3m history."""
        if base_map and sym in base_map:
            f3 = factors_map.get(sym)
            if f3 is not None:
                return (f3 - 1.0) * 100
        return None

    rows = []
    for sym, (close, prev, name, series, _isin) in latest.items():
        if sym not in base7:
            continue  # not traded at 7-day window start (new listing etc.)
        if series not in SME_SERIES and equity_syms and sym not in equity_syms:
            continue  # EQ-series non-equity (ETF etc.)
        f7 = factors7.get(sym)
        if f7 is None:
            continue
        rows.append(make_row(sym, close, prev, name, series, (f7 - 1.0) * 100,
                             pct3_for(sym, base3, factors3),
                             sym in adjusted, series in SME_SERIES, False,
                             mcap_by_sym.get(sym)))
    log(f"NSE rows: {len(rows)}")

    # ── BSE pass: add BSE-only companies (ISINs not present on NSE) ──────────
    # BSE shares NSE's trading calendar, so base7_day/base3_day are valid BSE
    # trading days too.
    nse_isins = {t[4] for t in latest.values() if t[4]}
    bse_latest = fetch_bhavcopy(sess, latest_day, "BSE")
    bse_base7 = fetch_bhavcopy(sess, base7_day, "BSE") if bse_latest else None
    bse_base3 = fetch_bhavcopy(sess, base3_day, "BSE") if (bse_latest and base3_day) else None
    if bse_latest and bse_base7:
        bse_days = download_span(sess, span_start, latest_day, bse_latest, "BSE")
        log(f"BSE trading days downloaded: {len(bse_days)}")
        bfactors3, _ = chain_factors([d for _, d in bse_days])
        bfactors7, badjusted = chain_factors([d for dt, d in bse_days if dt > base7_day])
        if badjusted:
            log(f"BSE: neutralized corporate-action jumps for {len(badjusted)} symbols (7d)")
        added = 0
        for sym, (close, prev, name, series, isin) in bse_latest.items():
            if isin and isin in nse_isins:
                continue  # dual-listed — already covered via its NSE row
            if sym not in bse_base7:
                continue
            f7 = bfactors7.get(sym)
            if f7 is None:
                continue
            # No mcap join for BSE-only rows: the NSE mcap map is keyed by NSE
            # symbols, and a coincidental symbol collision would attach the
            # wrong company's mcap. They show N/A instead.
            rows.append(make_row(sym, close, prev, name, series, (f7 - 1.0) * 100,
                                 pct3_for(sym, bse_base3, bfactors3),
                                 sym in badjusted, series in BSE_SME_SERIES,
                                 True, None))
            added += 1
        log(f"BSE-only rows added: {added}")
    else:
        log("::warning::BSE bhavcopy unavailable for window — BSE-only companies skipped this run")

    # Fallback for NSE rows missing from the semi-annual mcap file — mostly
    # companies that listed on NSE after the file's Jul-Dec window (e.g.
    # Transpek Industry, listed on NSE 20-Apr-2026). Fetch live mcap from
    # Yahoo by NSE symbol; safe because these are genuine NSE symbols (unlike
    # BSE-only rows, which we don't Yahoo-join to avoid symbol collisions).
    missing = [r for r in rows if not r.get("bse") and r["mcap_cr"] is None]
    if missing:
        import comm_profile
        from concurrent.futures import ThreadPoolExecutor
        def _y(r):
            return r, comm_profile.yahoo_mcap_cr(r["symbol"])
        filled = 0
        with ThreadPoolExecutor(max_workers=8) as ex:
            for r, mc in ex.map(_y, missing):
                if mc:
                    r["mcap_cr"] = round(mc, 0)
                    r["bucket"] = cap_bucket(r["mcap_cr"])
                    filled += 1
        log(f"Yahoo mcap fallback: filled {filled}/{len(missing)} NSE symbols missing from file")

    rows.sort(key=lambda r: r["rs"] if r["rs"] is not None else r["pct_7d"], reverse=True)
    log(f"total rows: {len(rows)}")

    out = {
        "generated_at": datetime.utcnow().isoformat(),
        "latest_date": latest_day.strftime("%Y-%m-%d"),
        "base_date": base7_day.strftime("%Y-%m-%d"),
        "base_date_3m": base3_day.strftime("%Y-%m-%d") if base3_day else None,
        "benchmark": BENCHMARK,
        "benchmark_pct": round(idx_pct, 2) if idx_pct is not None else None,
        "benchmark_pct_3m": round(idx_pct_3m, 2) if idx_pct_3m is not None else None,
        "rows": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(out, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"Saved {OUT_FILE}")


if __name__ == "__main__":
    main()
