"""Stage 1: find annual reports via BSE's own API.

This used to scrape screener's per-company pages. BSE publishes the same thing
as structured JSON and it is strictly better: official, every listed scrip,
every year back to ~2010, a direct PDF URL per row, and no aggressive rate
limiting -- about 0.45s per company against screener's 1s-plus-backoff.

    https://api.bseindia.com/BseIndiaAPI/api/AnnualReport_New/w?scripcode=500325

Set AR_SILENT_ONLY=1 to restrict this to the companies screen.py flagged as
having no concalls and a market cap above the floor.
"""
import json, os, threading, time
from datetime import date
from concurrent.futures import ThreadPoolExecutor

import requests

import xref

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
SCRIPS = os.path.join(ROOT, "data", "scrips.json")
SCREEN = os.path.join(ROOT, "data", "annual_reports", "screen.jsonl")
NSE_ENGAGED = os.path.join(ROOT, "data", "annual_reports", "nse_engaged.json")
OUT = os.path.join(ROOT, "data", "annual_reports", "fy2026_reports.jsonl")

FY = os.environ.get("AR_FY", "2026")
WORKERS = int(os.environ.get("AR_WORKERS", "3"))
DELAY = float(os.environ.get("AR_DELAY", "0.2"))
MIN_MCAP = float(os.environ.get("AR_MIN_MCAP", "100"))
SILENT_ONLY = os.environ.get("AR_SILENT_ONLY", "") not in ("", "0", "false")

API = "https://api.bseindia.com/BseIndiaAPI/api/AnnualReport_New/w?scripcode={}"
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


def silent_universe():
    """Scrip codes for companies that talk to nobody, above the market cap floor.

    A company qualifies only when BOTH exchanges agree it filed no concall,
    transcript or investor presentation. BSE's per-scrip feed is incomplete for
    some companies -- Sundaram Finance shows zero engagement on BSE but 26
    concall notices on NSE -- so the NSE cross-check is what keeps genuinely
    talkative companies off the list.

    The join runs on ISIN *and* NSE symbol, because neither alone is enough.
    The two exchanges do not always agree on a company's ISIN: BSE carries
    Karur Vysya Bank as INE036D01028 while NSE has INE036D01010, so an
    ISIN-only join silently kept a bank with 39 concall filings on the silent
    list. Symbols cover the listed names; ISIN covers the rest.
    """
    if not os.path.exists(SCREEN):
        raise SystemExit(
            "AR_SILENT_ONLY is set but {} does not exist.\n"
            "Run screen.py first, or unset AR_SILENT_ONLY.".format(SCREEN))

    nse_isins, nse_symbols = set(), set()
    if os.path.exists(NSE_ENGAGED):
        with open(NSE_ENGAGED, encoding="utf-8") as fh:
            payload = json.load(fh)
        nse_isins = {k.strip() for k in payload.get("engaged", {}) if k}
        nse_symbols = {v.strip().upper()
                       for v in payload.get("symbols", {}).values() if v}
    else:
        print("WARNING: {} missing -- BSE-only screening, which is known to\n"
              "         mislabel some talkative companies as silent. Run\n"
              "         nse_engagement.py for an accurate list."
              .format(os.path.basename(NSE_ENGAGED)), flush=True)

    # BSE scrip code -> NSE symbol, for the second half of the join. Built by
    # xref.py from both exchanges' master lists; data/scrips.json carries both
    # identifiers for only 1,312 rows and misses MRF and Karur Vysya entirely.
    sym_by_code = {k: v.strip().upper() for k, v in xref.load().items() if v}
    if not sym_by_code:
        print("WARNING: no BSE/NSE cross-reference found -- run xref.py so the\n"
              "         NSE check can resolve companies by symbol as well as ISIN.",
              flush=True)

    keep, overruled = set(), 0
    with open(SCREEN, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") != "ok" or rec.get("concalls"):
                continue
            if (rec.get("market_cap_cr") or 0) < MIN_MCAP:
                continue
            code = str(rec.get("scrip_code") or "")
            if not code:
                continue
            if ((rec.get("isin") or "").strip() in nse_isins
                    or sym_by_code.get(code, "") in nse_symbols):
                overruled += 1          # BSE said silent, NSE knows better
                continue
            keep.add(code)
    if overruled:
        print("  NSE overruled {} companies BSE reported as silent".format(overruled),
              flush=True)
    return keep


def scan(scrip):
    code = str(scrip.get("ScripCode") or "").strip()
    rec = {
        "key": scrip.get("NSESymbol") or code,
        "name": scrip.get("ScripName"),
        "scrip_code": code,
        "nse_symbol": scrip.get("NSESymbol"),
    }
    try:
        r = session().get(API.format(code), timeout=30)
        if r.status_code != 200:
            rec["status"] = "http_{}".format(r.status_code)
            return rec
        payload = r.json()
    except Exception as exc:
        rec["status"] = "error"
        rec["error"] = "{}: {}".format(type(exc).__name__, exc)[:200]
        return rec

    # BSE answers "No Record Found!" as a bare JSON string, not an object.
    rows = payload.get("Table", []) if isinstance(payload, dict) else []
    hit = next((row for row in rows
                if str(row.get("Year")) == FY and row.get("PDFDownload")), None)
    if hit:
        rec["status"] = "found"
        rec["url"] = hit["PDFDownload"]
        rec["source"] = "bse"
        # BSE's own upload timestamp -- the real filing date, as opposed to
        # first_seen, which is merely when this script noticed it.
        rec["filed_on"] = (hit.get("Fld_AuthoriseDate") or "")[:10]
        rec["revised_on"] = (hit.get("revised_date_time") or "")[:10]
        rec["first_seen"] = date.today().isoformat()
    else:
        rec["status"] = "no_fy_report"
        rec["years_available"] = sorted({str(r.get("Year")) for r in rows}, reverse=True)[:5]
    return rec


def load_done():
    """Keys whose report we have already located.

    Only 'found' counts as settled. A company with no FY2026 report yet has
    simply not filed one, and filing season runs for months -- caching that as
    a resolved answer would mean a daily run never notices the report when it
    finally appears, which defeats the point of running this on a schedule.
    So 'no_fy_report' is deliberately re-checked on every run.
    """
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                if rec.get("status") == "found":
                    done.add(rec["key"])
    return done


def prune_unresolved():
    """Drop stale 'no_fy_report' rows so the file does not grow without bound.

    Every run re-checks those companies, so yesterday's misses are noise. The
    'found' rows are the durable record and are kept, earliest first_seen wins.
    """
    if not os.path.exists(OUT):
        return
    keep = {}
    with open(OUT, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") != "found":
                continue
            prev = keep.get(rec["key"])
            if prev and prev.get("first_seen", "") <= rec.get("first_seen", ""):
                continue
            keep[rec["key"]] = rec
    with open(OUT, "w", encoding="utf-8") as fh:
        for rec in keep.values():
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")


def main():
    if SILENT_ONLY:
        # Drive off the screen records, not data/scrips.json. The screen comes
        # from BSE's live master list and scrips.json is a stale subset of it --
        # filtering through it drops 54 of the silent companies outright.
        keep = silent_universe()
        sym_by_code = xref.load()
        scrips = []
        with open(SCREEN, encoding="utf-8") as fh:
            for line in fh:
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                code = str(rec.get("scrip_code") or "")
                if code in keep:
                    scrips.append({
                        "ScripCode": code,
                        "ScripName": rec.get("name"),
                        "NSESymbol": sym_by_code.get(code, ""),
                    })
        print("silent-only mode: {} companies with no concalls and mcap >= {:.0f} cr"
              .format(len(scrips), MIN_MCAP), flush=True)
    else:
        scrips = [s for s in json.load(open(SCRIPS, encoding="utf-8"))
                  if s.get("ScripCode")]

    prune_unresolved()
    done = load_done()
    todo = [s for s in scrips if (s.get("NSESymbol") or str(s["ScripCode"])) not in done]
    print("{} candidates | {} already resolved | {} to go".format(
        len(scrips), len(done), len(todo)), flush=True)

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    fh = open(OUT, "a", encoding="utf-8")
    counts = {"n": 0, "found": 0}
    started = time.time()

    def work(scrip):
        rec = scan(scrip)
        time.sleep(DELAY)
        with _lock:
            fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            counts["n"] += 1
            if rec["status"] == "found":
                counts["found"] += 1
            if counts["n"] % 100 == 0:
                fh.flush()
                rate = counts["n"] / max(time.time() - started, 1)
                eta = (len(todo) - counts["n"]) / max(rate, 0.001) / 60
                print("  {}/{} | {} FY{} reports | ETA {:.0f} min".format(
                    counts["n"], len(todo), counts["found"], FY, eta), flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, todo))
    fh.close()
    print("done: {} NEW FY{} reports found this run ({} still unfiled) -> {}".format(
        counts["found"], FY, len(todo) - counts["found"], OUT))


if __name__ == "__main__":
    main()
