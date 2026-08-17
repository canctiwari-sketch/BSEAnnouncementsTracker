"""
US momentum screener — thematic idea generation.

Purpose: US sectors often lead and Indian counterparts follow (e.g. US fibre-
optic names ran through 2024-25 before Sterlite Tech moved). This finds US
stocks that have already run hard, grouped by INDUSTRY, so a theme — not just
a single ticker — becomes visible and its Indian analogue can be sought.

Data sources (both free, both verified to work from datacenter IPs):
  - NASDAQ screener API: whole US universe in ONE call, with market cap,
    sector and industry. The industry field is what makes theme-spotting work.
  - Yahoo Finance chart API: 6 months of weekly closes per symbol. Weekly
    granularity keeps payloads small; 1M/3M/6M returns all come from the one
    call, so there is no extra request cost for the shorter windows.

Writes data/us_movers.json. The worker stores every symbol it can price and
lets the frontend apply the % threshold, so the cutoff is adjustable without
re-running the job.
"""
import os
import math
import json
import time
from datetime import datetime

import requests
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
OUT_FILE = os.path.join(DATA_DIR, "us_movers.json")

SCREENER_URL = ("https://api.nasdaq.com/api/screener/stocks"
                "?tableonly=true&limit=25&download=true")
CHART_URL = ("https://query1.finance.yahoo.com/v8/finance/chart/{sym}"
             "?range=6mo&interval=1wk&events=split")

MIN_MCAP_USD = 300e6   # floor: keeps the list meaningful, drops penny noise
MAX_WORKERS = 12
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36")

# Non-common-stock instruments that slip into the screener feed.
_BAD_NAME_BITS = ("warrant", "unit", "preferred", "depositary", "right")


def log(m):
    print(m, flush=True)


def fetch_universe(sess):
    """Whole US universe with mcap/sector/industry — one call."""
    r = sess.get(SCREENER_URL, timeout=60)
    r.raise_for_status()
    rows = r.json()["data"]["rows"]
    log(f"NASDAQ screener: {len(rows)} listings")

    out = []
    for x in rows:
        sym = (x.get("symbol") or "").strip()
        name = (x.get("name") or "").strip()
        if not sym or any(c in sym for c in "^/ "):
            continue  # warrants / preferred / units carry suffix characters
        if any(b in name.lower() for b in _BAD_NAME_BITS):
            continue
        try:
            mcap = float(x.get("marketCap") or 0)
        except ValueError:
            mcap = 0.0
        if mcap < MIN_MCAP_USD:
            continue
        out.append({
            "symbol": sym,
            "name": name,
            "mcap_usd": mcap,
            "sector": (x.get("sector") or "").strip() or "Unclassified",
            "industry": (x.get("industry") or "").strip() or "Unclassified",
        })
    log(f"universe after filters (mcap >= ${MIN_MCAP_USD/1e6:.0f}M): {len(out)}")
    return out


def fetch_returns(sym):
    """(1m, 3m, 6m) % returns and last close from 6 months of weekly closes.

    Split handling — Yahoo is INCONSISTENT here, so we detect rather than
    assume. For older splits it has already back-adjusted the history
    (Booking's 25:1 from Apr-2026 shows a smooth series); for a split only
    days old it has not (Beyond Meat's 1:30 on 14-Aug-2026 still had raw
    pre-split prices, making a real -38% read as +1758%). Blindly adjusting
    breaks the first case, blindly trusting Yahoo breaks the second. So for
    each split we compare the observed price step across the split date
    against the split ratio, and only rescale when the step shows the series
    is genuinely unadjusted.
    """
    try:
        r = requests.get(CHART_URL.format(sym=sym), headers={"User-Agent": UA}, timeout=20)
        if r.status_code != 200:
            return None
        res = r.json()["chart"]["result"][0]
        stamps = res.get("timestamp") or []
        raw = res["indicators"]["quote"][0]["close"]
        if len(stamps) != len(raw):
            return None

        pairs = [(t, c) for t, c in zip(stamps, raw) if c]
        if len(pairs) < 6:
            return None

        # R = shares multiplier (25:1 forward -> 25, price divides by 25;
        # 1:30 reverse -> 1/30, price multiplies by 30).
        splits = sorted(
            ((s.get("date") or 0), float(s.get("numerator") or 1) / float(s.get("denominator") or 1))
            for s in (res.get("events", {}).get("splits") or {}).values()
        )
        needs_fix = []
        for sdate, R in splits:
            if R <= 0:
                continue
            before = [c for t, c in pairs if t < sdate]
            after = [c for t, c in pairs if t >= sdate]
            if not before or not after:
                continue
            obs = after[0] / before[-1]          # observed step across the split
            if obs <= 0:
                continue
            # Unadjusted series steps by 1/R here; adjusted series steps by ~1.
            if abs(math.log(obs * R)) < abs(math.log(obs)):
                needs_fix.append((sdate, R))

        def adj_factor(ts):
            """Rescale a pre-split price into today's share terms."""
            f = 1.0
            for sdate, R in needs_fix:
                if ts < sdate:
                    f /= R
            return f

        closes = [c * adj_factor(t) for t, c in pairs]
        if len(closes) < 6:
            return None  # too little history to judge (recent IPO etc.)
        last = closes[-1]

        def chg(weeks_back):
            if len(closes) <= weeks_back:
                return None
            base = closes[-1 - weeks_back]
            return (last / base - 1) * 100 if base else None

        return {
            "close": round(last, 2),
            "pct_1m": chg(4),
            "pct_3m": chg(13),
            "pct_6m": (last / closes[0] - 1) * 100,
            # Surfaced in the UI: any split in the window is worth an eyeball,
            # since the return depends on getting its adjustment right.
            "split": bool(splits),
        }
    except Exception:
        return None


def main():
    log(f"US movers starting {datetime.utcnow().isoformat()}")
    sess = requests.Session()
    sess.headers.update({"User-Agent": UA, "Accept": "application/json"})

    universe = fetch_universe(sess)
    if not universe:
        log("::error::Empty universe — NASDAQ screener unavailable?")
        raise SystemExit(1)

    t0 = time.time()
    done = 0
    rows = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        for u, ret in zip(universe, ex.map(lambda u: fetch_returns(u["symbol"]), universe)):
            done += 1
            if done % 500 == 0:
                log(f"  priced {done}/{len(universe)} ({len(rows)} ok, {time.time()-t0:.0f}s)")
            if not ret or ret["pct_6m"] is None:
                continue
            rows.append({
                "symbol": u["symbol"],
                "name": u["name"],
                "sector": u["sector"],
                "industry": u["industry"],
                "mcap_usd": round(u["mcap_usd"]),
                "close": ret["close"],
                "pct_1m": round(ret["pct_1m"], 1) if ret["pct_1m"] is not None else None,
                "pct_3m": round(ret["pct_3m"], 1) if ret["pct_3m"] is not None else None,
                "pct_6m": round(ret["pct_6m"], 1),
                "split": ret.get("split") or None,
            })
    log(f"priced {len(rows)}/{len(universe)} symbols in {time.time()-t0:.0f}s")

    # Industry roll-up at the default 50% threshold. This is the actual point
    # of the tool: one ticker up 60% is noise, but six names in the same
    # industry up 60% is a theme worth hunting an Indian analogue for.
    THEME_TH = 50.0
    by_ind = {}
    for r in rows:
        if r["pct_6m"] >= THEME_TH:
            by_ind.setdefault(r["industry"], []).append(r)
    themes = sorted(
        ({"industry": ind,
          "sector": grp[0]["sector"],
          "count": len(grp),
          "median_6m": round(sorted(x["pct_6m"] for x in grp)[len(grp) // 2], 1),
          "symbols": [x["symbol"] for x in sorted(grp, key=lambda x: -x["pct_6m"])[:6]]}
         for ind, grp in by_ind.items() if len(grp) >= 2),
        key=lambda t: (-t["count"], -t["median_6m"]))
    log(f"themes (>=2 names up {THEME_TH:.0f}%+): {len(themes)}")
    for t in themes[:8]:
        log(f"    {t['count']:3} × {t['industry'][:52]:54} median {t['median_6m']:+.0f}%")

    rows.sort(key=lambda r: -r["pct_6m"])
    out = {
        "generated_at": datetime.utcnow().isoformat(),
        "min_mcap_usd": MIN_MCAP_USD,
        "theme_threshold": THEME_TH,
        "universe": len(universe),
        "themes": themes,
        "rows": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(out, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    log(f"Saved {OUT_FILE} — {len(rows)} rows")


if __name__ == "__main__":
    main()
