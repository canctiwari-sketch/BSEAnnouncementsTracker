"""
Disclosure-style profiler.

For each company x calendar quarter over the last ~6 months, records whether the
company released an Investor PRESENTATION and/or held a CONFERENCE CALL /
released a TRANSCRIPT. Output powers the "Disclosure Style" tab — the user can
filter to quarters where a company released a presentation but did NOT do a
concall/transcript.

No Gemini. Pure subject-line classification from NSE corporate announcements
(covers ~all >500 Cr companies, which are nearly all NSE-listed). Market cap is
reused from announcements.json where possible, else Yahoo Finance (no screener).
A market-cap cutoff keeps the list to meaningful names.
"""
import os
import re
import io
import json
import time
from datetime import datetime, timedelta

import httpx
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
ANN_FILE = os.path.join(DATA_DIR, "announcements.json")
OUT_FILE = os.path.join(DATA_DIR, "comm_profile.json")

MCAP_MIN_CR = 50.0         # lower bound (₹50 Cr)
MCAP_MAX_CR = 1e12         # no upper bound
GRACE_DAYS = 10            # wait this long after a presentation before we
                           # confirm "no concall" (concall can come ~a week later)


def current_season(today):
    """Auto-detect the results season currently being reported, and the date
    from which its filings (incl. early board-meeting/concall intimations)
    start appearing. Rolls forward automatically each quarter.

    Each results season = the calendar quarter in which those results are
    filed. STRICT, non-overlapping boundaries (a filing's date alone decides
    its quarter — e.g. anything up to Jun 30 is Q4; Jul 1 onward is Q1):
      Apr 1 - Jun 30  -> Q4 (Jan-Mar) results
      Jul 1 - Sep 30  -> Q1 (Apr-Jun) results
      Oct 1 - Dec 31  -> Q2 (Jul-Sep) results
      Jan 1 - Mar 31  -> Q3 (Oct-Dec) results
    """
    y, m = today.year, today.month
    if m in (4, 5, 6):
        return f"Q4 FY{str(y)[2:]}", datetime(y, 4, 1)
    if m in (7, 8, 9):
        return f"Q1 FY{str(y + 1)[2:]}", datetime(y, 7, 1)
    if m in (10, 11, 12):
        return f"Q2 FY{str(y + 1)[2:]}", datetime(y, 10, 1)
    return f"Q3 FY{str(y)[2:]}", datetime(y, 1, 1)

PRES_RE = re.compile(
    r"investor presentation|analyst presentation|earnings presentation|"
    r"results presentation|investor\s*&?\s*analyst|investor ppt|"
    r"institutional investor|presentation to (?:investors|analysts)|"
    # generic "Presentation ... <results context>" (e.g. "Presentation For
    # Quarter And Financial Year Ended ..."), in either word order
    r"presentation.{0,45}(?:quarter|financial year|year ended|results|fy\s?2\d|q[1-4]\b)|"
    r"(?:quarter|financial year|year ended|results|fy\s?2\d|q[1-4])\b.{0,45}presentation",
    re.IGNORECASE,
)
# A presentation tick additionally requires an actual presentation word.
# NSE's generic category "Analysts/Institutional Investor Meet/Con. Call
# Updates" matches PRES_RE via "institutional investor", so bare
# meeting-schedule intimations ("Schedule of meet", "Outcome of Analyst
# Meeting") were wrongly ticked as presentations. A genuine presentation
# filing always says "presentation"/"ppt" somewhere in desc or text.
PRES_WORD_RE = re.compile(r"presentation|\bppt\b", re.IGNORECASE)
CALL_RE = re.compile(
    r"transcript|conference call|con\.?\s*call|earnings call|audio recording|"
    r"investor call|analyst call|earnings conference|audio of|"
    r"intimation.{0,30}call|q[1-4].{0,15}call|schedule.{0,20}call|"
    # NSE's meet/call category itself: bare meeting-schedule intimations
    # ("Schedule of meet", "Outcome of Analyst Meeting") count as a
    # meeting/concall signal — NOT as a presentation (see PRES_WORD_RE).
    r"institutional investor meet|investor meet intimation|analyst meet",
    re.IGNORECASE,
)


def log(m):
    print(m, flush=True)


def _norm(name):
    n = (name or "").lower()
    n = re.sub(r"[^\w\s&]", " ", n)
    n = re.sub(r"\b(the|limited|ltd|pvt|private|industries|enterprises|"
               r"corporation|corp|company|co|holdings)\b", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def _quarter(iso_date):
    """Calendar quarter label from an ISO/parseable date string."""
    s = str(iso_date)[:10]
    try:
        d = datetime.fromisoformat(s)
    except Exception:
        try:
            d = datetime.strptime(s, "%Y-%m-%d")
        except Exception:
            return None
    q = (d.month - 1) // 3 + 1
    return f"{d.year}-Q{q}"


def _nse_client():
    c = httpx.Client(http2=True, follow_redirects=True, timeout=25, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9"})
    try:
        c.get("https://www.nseindia.com")
    except Exception as e:
        log(f"NSE warm error: {e}")
    c.headers.update({"Accept": "application/json", "Referer": "https://www.nseindia.com/"})
    return c


def fetch_nse_window(client, frm, to):
    """Fetch NSE announcements for a date range (dd-mm-yyyy) across BOTH the
    main board (equities) and SME boards — many SME companies file investor
    presentations but skip concalls, so they must be included."""
    out = []
    for index in ("equities", "sme"):
        url = (f"https://www.nseindia.com/api/corporate-announcements"
               f"?index={index}&from_date={frm}&to_date={to}")
        for attempt in range(3):
            try:
                r = client.get(url)
                if r.status_code in (401, 403):
                    client.get("https://www.nseindia.com")
                    continue
                data = r.json() if r.text.strip() else []
                if isinstance(data, list):
                    out.extend(data)
                break
            except Exception as e:
                log(f"  NSE {index} {frm}-{to} attempt {attempt+1}: {e}")
                time.sleep(3)
    return out


# ─── market cap ──────────────────────────────────────────────────────────────
_SCR_RE = re.compile(r'Market Cap.*?<span class="number">\s*([\d,]+)', re.S)

def load_known_mcap():
    """symbol/name -> mcap_cr from prior data (persistent cache), so any mcap
    we've EVER resolved is reused and never re-fetched. Reads both the main
    announcements feed AND this tab's own prior output."""
    by_sym, by_name = {}, {}
    # 1) main announcements feed (market_cap is in rupees)
    try:
        d = json.load(open(ANN_FILE, encoding="utf-8"))
        for a in d.get("announcements", []):
            v = a.get("market_cap")
            if not v:
                continue
            cr = v / 1e7
            if a.get("symbol"):
                by_sym.setdefault(a["symbol"], cr)
            nm = _norm(a.get("company", ""))
            if nm:
                by_name.setdefault(nm, cr)
    except Exception as e:
        log(f"known mcap load error (announcements): {e}")
    # 2) this tab's own prior rows (mcap_cr already in Cr) — persistent cache
    try:
        p = json.load(open(OUT_FILE, encoding="utf-8"))
        for r in p.get("rows", []):
            cr = r.get("mcap_cr")
            if not cr:
                continue
            if r.get("symbol"):
                by_sym.setdefault(r["symbol"], cr)
            nm = _norm(r.get("company", ""))
            if nm:
                by_name.setdefault(nm, cr)
    except Exception:
        pass
    return by_sym, by_name


# ─── Yahoo Finance market cap (by symbol; datacenter-friendly, no screener) ──
_yahoo = {"sess": None, "crumb": None}

def _yahoo_session():
    if _yahoo["sess"] is None:
        s = requests.Session()
        s.headers.update({"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        try:
            s.get("https://fc.yahoo.com", timeout=12)
            _yahoo["crumb"] = s.get("https://query1.finance.yahoo.com/v1/test/getcrumb", timeout=12).text.strip()
        except Exception:
            _yahoo["crumb"] = None
        _yahoo["sess"] = s
    return _yahoo["sess"], _yahoo["crumb"]


def yahoo_mcap_cr(symbol):
    """Market cap (Cr) from Yahoo Finance by NSE symbol (.NS then .BO)."""
    if not symbol:
        return None
    s, crumb = _yahoo_session()
    if not crumb:
        return None
    for suf in (".NS", ".BO"):
        try:
            r = s.get(f"https://query1.finance.yahoo.com/v7/finance/quote?symbols={symbol}{suf}&crumb={crumb}", timeout=12)
            res = r.json().get("quoteResponse", {}).get("result", [])
            if res:
                mc = res[0].get("marketCap")
                if mc:
                    return mc / 1e7
        except Exception:
            pass
    return None


def _clean_name(name):
    n = re.sub(r"\([^)]*\)", " ", name or "")
    n = re.sub(r"\b(the|limited|ltd|pvt|private|industries|enterprises|corporation)\b", " ", n, flags=re.I)
    return re.sub(r"\s+", " ", n).strip()


_BSE_MC_RE = re.compile(r'"?MktCapFull"?\s*:\s*"?([\d,]+(?:\.\d+)?)', re.I)

def bse_mcap_cr(sess, name):
    """Fallback mcap via BSE: resolve scrip by name, then StockTrading API.
    Reliable from datacenter IPs when screener is flaky."""
    try:
        clean = _clean_name(name) or name
        r = sess.get(f"https://api.bseindia.com/BseIndiaAPI/api/PeerSmartSearch/w?Type=SS&text={requests.utils.quote(clean)}",
                     headers={"User-Agent": "Mozilla/5.0", "Referer": "https://www.bseindia.com/"}, timeout=12)
        m = re.search(r"liclick\('(\d{6})'", r.text)
        if not m:
            return None
        scrip = m.group(1)
        d = sess.get(f"https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w?flag=&scripcode={scrip}",
                     headers={"User-Agent": "Mozilla/5.0", "Accept": "application/json",
                              "Referer": "https://www.bseindia.com/"}, timeout=12).json()
        v = str(d.get("MktCapFull") or "").replace(",", "").strip()
        if v:
            cr = float(v)
            return cr if cr > 0 else None
    except Exception:
        pass
    return None


# NOTE: screener.in is intentionally NOT used in this worker — it IP-blocks
# under load and breaks the user's whole network. Market cap comes from the
# persistent cache -> Yahoo (by symbol) -> BSE (by name) -> NSE mcap file
# (below; covers NSE SME/Emerge names that Yahoo/BSE don't carry).

_NSE_MCAP_PAGE = "https://www.nseindia.com/static/regulations/listing-compliance/nse-market-capitalisation-all-companies"
_XLSX_LINK_RE = re.compile(r'href="([^"]+\.xlsx?)"', re.I)


def nse_xlsx_mcap():
    """NSE's official semi-annual 'Average Market Capitalisation' file (SEBI
    LODR eligibility list). Unlike Yahoo/BSE/screener, it covers the full
    listed universe including NSE SME/Emerge names. Values are a 6-month
    average, in Rs. lakhs. Returns (by_symbol, by_name) dicts of mcap in Cr.

    Uses its own plain-browser session rather than _nse_client() — that one
    sets Accept: application/json for the announcements API, which is the
    wrong content-type for an HTML page + xlsx download and more likely to
    get flagged by NSE's bot detection from a datacenter IP."""
    by_sym, by_name = {}, {}
    client = httpx.Client(http2=True, follow_redirects=True, timeout=30, headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9"})
    try:
        try:
            client.get("https://www.nseindia.com")
        except Exception as e:
            log(f"NSE mcap file warm error: {e}")
        page = client.get(_NSE_MCAP_PAGE)
        links = _XLSX_LINK_RE.findall(page.text)
        if not links:
            log(f"NSE mcap file: no xlsx link found on page (status {page.status_code}, {len(page.text)} bytes)")
            return by_sym, by_name
        xlsx_url = links[0]  # most recently published file listed first
        r = client.get(xlsx_url)
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(r.content), data_only=True)
        ws = wb.worksheets[0]
        for row in ws.iter_rows(min_row=2, values_only=True):
            if not row or len(row) < 4:
                continue
            _, symbol, name, mcap_lakhs = row[:4]
            if not isinstance(mcap_lakhs, (int, float)):
                continue  # e.g. "Not traded during the period"
            cr = mcap_lakhs / 100.0
            if symbol:
                by_sym.setdefault(str(symbol).strip(), cr)
            nm = _norm(name)
            if nm:
                by_name.setdefault(nm, cr)
        log(f"NSE mcap file: {xlsx_url.rsplit('/', 1)[-1]} -> {len(by_sym)} symbols")
    except Exception as e:
        log(f"NSE mcap file error: {e}")
    finally:
        client.close()
    return by_sym, by_name


def main():
    log(f"Comm-profile starting {datetime.utcnow().isoformat()}")
    today = datetime.utcnow() + timedelta(hours=5, minutes=30)
    real_today = today  # kept for grace-day status even if FORCE_END caps the fetch window below

    # Auto-detect the current results season (rolls forward each quarter).
    SEASON_LABEL, start = current_season(today)
    # Optional one-off override to (re)build a specific past season, e.g.
    # FORCE_SEASON="Q4 FY26" FORCE_START="2026-03-15" FORCE_END="2026-06-30"
    if os.environ.get("FORCE_SEASON"):
        SEASON_LABEL = os.environ["FORCE_SEASON"]
        start = datetime.fromisoformat(os.environ.get("FORCE_START", start.strftime("%Y-%m-%d")))
        if os.environ.get("FORCE_END"):
            today = datetime.fromisoformat(os.environ["FORCE_END"])
    log(f"Season: {SEASON_LABEL}  window {start:%d-%b-%Y} -> {today:%d-%b-%Y}")

    client = _nse_client()
    raw = []
    # monthly chunks
    cur = start.replace(day=1)
    while cur <= today:
        nxt = (cur.replace(day=28) + timedelta(days=8)).replace(day=1)
        frm = cur.strftime("%d-%m-%Y")
        to = min(nxt - timedelta(days=1), today).strftime("%d-%m-%Y")
        chunk = fetch_nse_window(client, frm, to)
        log(f"  {frm}–{to}: {len(chunk)} announcements")
        raw.extend(chunk)
        cur = nxt
        time.sleep(1)
    client.close()
    log(f"Total NSE announcements: {len(raw)}")

    # company -> {symbol, name, quarters: {q: {'pres':bool,'call':bool}}}
    companies = {}
    for a in raw:
        subj = (a.get("desc") or "") + " " + (a.get("attchmntText") or "")
        is_pres = bool(PRES_RE.search(subj)) and bool(PRES_WORD_RE.search(subj))
        is_call = bool(CALL_RE.search(subj))
        if not (is_pres or is_call):
            continue
        q = SEASON_LABEL  # single season
        sym = (a.get("symbol") or "").strip()
        name = (a.get("sm_name") or a.get("smName") or sym).strip()
        key = _norm(name) or sym
        if not key:
            continue
        c = companies.setdefault(key, {"symbol": sym, "name": name, "quarters": {}})
        if sym and not c["symbol"]:
            c["symbol"] = sym
        qd = c["quarters"].setdefault(q, {"pres": False, "call": False, "date": "",
                                          "pres_date": "", "call_date": ""})
        qd["pres"] = qd["pres"] or is_pres
        qd["call"] = qd["call"] or is_call
        fdate = (a.get("sort_date") or "")[:10]
        if fdate:
            qd["date"] = max(qd["date"], fdate)
            if is_pres:
                qd["pres_date"] = max(qd["pres_date"], fdate)
            if is_call:
                qd["call_date"] = max(qd["call_date"], fdate)
    log(f"Companies with pres/call activity: {len(companies)}")

    # Market cap: this worker now runs daily (presentation/concall detection
    # is cheap on NSE's API), but a full Yahoo->BSE->NSE-file refresh of every
    # active company is not — that's ~1,500+ live lookups, and doing it daily
    # would multiply Yahoo/BSE load 7x/week. So the full refresh only runs on
    # Sundays (matching the old weekly cadence); other days just fill in
    # companies that don't have a cached mcap yet, reusing the rest.
    by_sym, by_name = load_known_mcap()
    is_sunday_refresh = real_today.weekday() == 6  # Monday=0 ... Sunday=6
    mcap_map = {}
    if is_sunday_refresh:
        need = [(key, c["name"], c["symbol"]) for key, c in companies.items()]
        log(f"mcap: Sunday full refresh — {len(need)} active companies (Yahoo->BSE->NSE file->prior cache)...")
    else:
        need = []
        for key, c in companies.items():
            m = by_sym.get(c["symbol"]) or by_name.get(key)
            if m is not None:
                mcap_map[key] = m
            else:
                need.append((key, c["name"], c["symbol"]))
        log(f"mcap: {len(mcap_map)} from cache, fetching {len(need)} new (Yahoo->BSE->NSE file)...")

    nse_by_sym, nse_by_name = {}, {}
    if need:
        nse_by_sym, nse_by_name = nse_xlsx_mcap()

    from concurrent.futures import ThreadPoolExecutor
    import threading
    _local = threading.local()
    def _sess():
        if not hasattr(_local, "s"):
            _local.s = requests.Session()
        return _local.s
    def _one(item):
        key, name, symbol = item
        # Yahoo by symbol (datacenter-friendly) -> BSE by name (dual-listed) ->
        # NSE's own mcap file (covers SME/Emerge names the other two miss) ->
        # prior cached value (last resort, if all live sources failed).
        # screener.in is NOT used — it IP-blocks and breaks the user's network.
        m = yahoo_mcap_cr(symbol)
        if m is None:
            m = bse_mcap_cr(_sess(), name)
        if m is None:
            m = nse_by_sym.get(symbol) or nse_by_name.get(key)
        if m is None:
            m = by_sym.get(symbol) or by_name.get(key)
        return key, m
    done = 0
    with ThreadPoolExecutor(max_workers=8) as ex:
        for key, m in ex.map(_one, need):
            if m:
                mcap_map[key] = m
            done += 1
            if done % 100 == 0:
                log(f"  mcap {done}/{len(need)} ({len(mcap_map)} found)")

    today_d = real_today.date()
    def _status(pres, call, pres_date):
        # both -> both ; concall only -> call_only ; presentation only ->
        # pending until GRACE_DAYS after the presentation, then pres_only.
        if pres and call:
            return "both"
        if call and not pres:
            return "call_only"
        if pres and not call:
            try:
                pd = datetime.fromisoformat(pres_date[:10]).date()
                if (today_d - pd).days >= GRACE_DAYS:
                    return "pres_only"
            except Exception:
                return "pres_only"  # no date -> assume window closed
            return "pending"
        return "none"

    rows = []
    for key, c in companies.items():
        mcap = mcap_map.get(key)   # may be None if screener+BSE both failed
        # Only drop when we KNOW the mcap is outside the band. If mcap is
        # unknown (source throttled / SME not on BSE), keep the company as N/A
        # rather than silently losing it (this is why IPHL/Rama disappeared).
        if mcap is not None and (mcap < MCAP_MIN_CR or mcap > MCAP_MAX_CR):
            continue
        for q, qd in c["quarters"].items():
            rows.append({
                "company": c["name"],
                "symbol": c["symbol"],
                "mcap_cr": round(mcap, 0) if mcap else None,
                "quarter": q,
                "presentation": qd["pres"],
                "concall": qd["call"],
                "status": _status(qd["pres"], qd["call"], qd.get("pres_date", "")),
                "date": qd.get("date", ""),
                "pres_date": qd.get("pres_date", ""),
                "call_date": qd.get("call_date", ""),
            })
    log(f"rows (this season {SEASON_LABEL}): {len(rows)}")

    # Merge with previously-saved quarters (keep history; replace this season).
    prior_all = []
    if os.path.exists(OUT_FILE):
        try:
            prior_all = json.load(open(OUT_FILE, encoding="utf-8")).get("rows", [])
        except Exception:
            prior_all = []
    prior_this_season = [r for r in prior_all if r.get("quarter") == SEASON_LABEL]
    prior = [r for r in prior_all if r.get("quarter") != SEASON_LABEL]

    # Safety net: now that this runs daily, a single transient NSE throttle
    # (BSE/NSE are known to intermittently return empty/"No Record Found!")
    # would otherwise silently wipe the whole current season down to whatever
    # (possibly nothing) this run managed to fetch. If this run found far
    # fewer companies than we already had for this season, assume it's a bad
    # fetch and keep the prior data instead of overwriting it.
    # ALLOW_SHRINK=1 overrides for deliberate rebuilds (e.g. after tightening
    # the presentation/concall classifier, which legitimately drops rows).
    if (prior_this_season and len(rows) < len(prior_this_season) * 0.5
            and os.environ.get("ALLOW_SHRINK") != "1"):
        log(f"Suspiciously few rows this run ({len(rows)} vs {len(prior_this_season)} previously) "
            f"for {SEASON_LABEL} — likely a fetch issue, keeping prior data for this season. "
            f"Set ALLOW_SHRINK=1 if this shrink is intentional.")
        rows = prior_this_season
    rows = rows + prior
    log(f"total rows after merge: {len(rows)} (kept {len(prior)} from other quarters)")

    rows.sort(key=lambda r: (r["quarter"], -(r["mcap_cr"] or 0)), reverse=True)
    quarters = sorted({r["quarter"] for r in rows}, reverse=True)

    out = {
        "generated_at": datetime.utcnow().isoformat(),
        "grace_days": GRACE_DAYS,
        "mcap_min_cr": MCAP_MIN_CR,
        "mcap_max_cr": MCAP_MAX_CR,
        "quarters": quarters,
        "rows": rows,
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    json.dump(out, open(OUT_FILE, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log(f"Saved {OUT_FILE} — {len(rows)} rows, quarters={quarters}")


if __name__ == "__main__":
    main()
