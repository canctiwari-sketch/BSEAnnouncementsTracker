"""Stage 0: find the companies whose annual report is the ONLY thing they say.

A company that runs quarterly concalls and publishes investor presentations
telegraphs its guidance four times a year -- the annual report adds little. The
interesting set is the opposite: real businesses above a market cap floor that
never hold a call and never put out a deck. For those the annual report is the
single forward-looking disclosure of the year, and almost nobody reads it.

Entirely BSE-native -- screener is not involved. Screener rate-limits hard
enough to block the IP at the network layer, so both signals come from BSE's
own APIs instead:

  market cap        ListofScripData    one request, every active equity scrip,
                                       with a Mktcap field in Rs crore
  concall history   AnnSubCategoryGetData  per scrip, paged; we look for the
                                       three ENGAGED subcategories below

Note it is AnnSubCategoryGetData, not AnnGetData -- the latter has long
returned "No Record Found!" for every query (see worker/lookup.py).
"""
import json, os, threading, time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data", "annual_reports", "screen.jsonl")

MIN_MCAP = float(os.environ.get("AR_MIN_MCAP", "100"))
LOOKBACK_DAYS = int(os.environ.get("AR_LOOKBACK_DAYS", "450"))
WORKERS = int(os.environ.get("AR_WORKERS", "3"))
DELAY = float(os.environ.get("AR_DELAY", "0.2"))
MAX_PAGES = int(os.environ.get("AR_MAX_PAGES", "12"))

SCRIP_LIST = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
              "?Group=&Scripcode=&industry=&segment=Equity&status=Active")
ANN = ("https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
       "?strCat=-1&strPrevDate={frm}&strScrip={scrip}&strSearch=P"
       "&strToDate={to}&strType=C&pageno={page}")

# BSE's own subcategory labels for investor engagement. Enumerated by sampling
# every distinct SUBCATNAME across the 120 largest scrips (94 values in total);
# these are the three that mean the company actually talks to the market.
# 'Earnings Call Transcript' matters most -- omitting it flagged Infosys as
# silent, because that is the only one of the three Infosys files under.
ENGAGED = {
    "Analyst / Investor Meet",
    "Investor Presentation",
    "Earnings Call Transcript",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": "https://www.bseindia.com/",
    "Origin": "https://www.bseindia.com",
}

_lock = threading.Lock()
_local = threading.local()


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(HEADERS)
    return _local.s


def fetch_universe():
    """Every active BSE equity scrip, with market cap in Rs crore."""
    r = requests.get(SCRIP_LIST, headers=HEADERS, timeout=90)
    r.raise_for_status()
    out = []
    for row in r.json():
        try:
            mcap = float(row.get("Mktcap") or 0)
        except (TypeError, ValueError):
            mcap = 0.0
        out.append({
            "scrip_code": str(row.get("SCRIP_CD")),
            "name": row.get("Scrip_Name"),
            "scrip_id": row.get("scrip_id"),
            "isin": (row.get("ISIN_NUMBER") or "").strip(),
            "market_cap_cr": mcap,
        })
    return out


def engagement(scrip_code, frm, to):
    """Count concall / presentation filings. Stops early once one is found."""
    found, scanned = [], 0
    for page in range(1, MAX_PAGES + 1):
        r = session().get(
            ANN.format(frm=frm, to=to, scrip=scrip_code, page=page), timeout=30)
        if r.status_code != 200:
            raise RuntimeError("http_{}".format(r.status_code))
        payload = r.json()
        rows = payload.get("Table", []) if isinstance(payload, dict) else []
        if not rows:
            break
        scanned += len(rows)
        for row in rows:
            sub = (row.get("SUBCATNAME") or "").strip()
            if sub in ENGAGED:
                found.append(sub)
        if found:          # one is enough to disqualify; stop paging
            break
        if len(rows) < 50:  # last page
            break
        time.sleep(DELAY)
    return found, scanned


def load_done():
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("status") == "ok":
                    done.add(rec["scrip_code"])
    return done


def main():
    to = datetime.now().strftime("%Y%m%d")
    frm = (datetime.now() - timedelta(days=LOOKBACK_DAYS)).strftime("%Y%m%d")

    universe = fetch_universe()
    above = [c for c in universe if c["market_cap_cr"] >= MIN_MCAP]
    print("{} active equity scrips | {} with mcap >= {:.0f} cr".format(
        len(universe), len(above), MIN_MCAP), flush=True)

    done = load_done()
    todo = [c for c in above if c["scrip_code"] not in done]
    print("{} already screened | {} to go | concall window {} to {}".format(
        len(done), len(todo), frm, to), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fh = open(OUT, "a", encoding="utf-8")
    counts = {"n": 0, "silent": 0, "err": 0}
    started = time.time()

    def work(company):
        rec = dict(company)
        try:
            found, scanned = engagement(company["scrip_code"], frm, to)
            rec["concalls"] = len(found)
            rec["engagement_types"] = sorted(set(found))
            rec["announcements_scanned"] = scanned
            rec["status"] = "ok"
        except Exception as exc:
            rec["status"] = "error"
            rec["error"] = "{}: {}".format(type(exc).__name__, exc)[:200]
        time.sleep(DELAY)
        with _lock:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            counts["n"] += 1
            if rec["status"] == "error":
                counts["err"] += 1
            elif rec["concalls"] == 0:
                counts["silent"] += 1
            if counts["n"] % 100 == 0:
                fh.flush()
                rate = counts["n"] / max(time.time() - started, 1)
                eta = (len(todo) - counts["n"]) / max(rate, 0.001) / 60
                print("  {}/{} | {} silent | {} errors | ETA {:.0f} min".format(
                    counts["n"], len(todo), counts["silent"], counts["err"],
                    eta), flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, todo))
    fh.close()
    print("done: {} silent companies (mcap >= {:.0f} cr, no concalls) -> {}".format(
        counts["silent"], MIN_MCAP, OUT))


if __name__ == "__main__":
    main()
