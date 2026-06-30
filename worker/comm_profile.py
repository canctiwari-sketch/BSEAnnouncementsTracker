"""
Disclosure-style profiler.

For each company x calendar quarter over the last ~6 months, records whether the
company released an Investor PRESENTATION and/or held a CONFERENCE CALL /
released a TRANSCRIPT. Output powers the "Disclosure Style" tab — the user can
filter to quarters where a company released a presentation but did NOT do a
concall/transcript.

No Gemini. Pure subject-line classification from NSE corporate announcements
(covers ~all >500 Cr companies, which are nearly all NSE-listed). Market cap is
reused from announcements.json where possible, else fetched from screener.in.
A market-cap cutoff keeps the list to meaningful names.
"""
import os
import re
import json
import time
from datetime import datetime, timedelta

import httpx
import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
ANN_FILE = os.path.join(DATA_DIR, "announcements.json")
OUT_FILE = os.path.join(DATA_DIR, "comm_profile.json")

MONTHS_BACK = 6
MCAP_MIN_CR = 50.0         # lower bound (₹50 Cr)
MCAP_MAX_CR = 1e12         # no upper bound

PRES_RE = re.compile(
    r"investor presentation|analyst presentation|earnings presentation|"
    r"results presentation|investor\s*&?\s*analyst|investor ppt|"
    r"institutional investor|presentation to (?:investors|analysts)",
    re.IGNORECASE,
)
CALL_RE = re.compile(
    r"transcript|conference call|concall|earnings call|audio recording|"
    r"investor call|earnings conference|audio of",
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
    """Fetch NSE announcements for a date range (dd-mm-yyyy)."""
    url = (f"https://www.nseindia.com/api/corporate-announcements"
           f"?index=equities&from_date={frm}&to_date={to}")
    for attempt in range(3):
        try:
            r = client.get(url)
            if r.status_code in (401, 403):
                client.headers.update({})
                client.get("https://www.nseindia.com")
                continue
            return r.json() if r.text.strip() else []
        except Exception as e:
            log(f"  NSE window {frm}-{to} attempt {attempt+1}: {e}")
            time.sleep(3)
    return []


# ─── market cap ──────────────────────────────────────────────────────────────
_SCR_RE = re.compile(r'Market Cap.*?<span class="number">\s*([\d,]+)', re.S)

def load_known_mcap():
    """symbol/name -> mcap_cr from announcements.json (avoids re-fetching)."""
    by_sym, by_name = {}, {}
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
        log(f"known mcap load error: {e}")
    return by_sym, by_name


def screener_mcap_cr(sess, name):
    try:
        clean = re.sub(r"\b(the|limited|ltd|pvt|private|industries|enterprises)\b", " ", name, flags=re.I)
        clean = re.sub(r"\([^)]*\)", " ", clean)
        clean = re.sub(r"\s+", " ", clean).strip()
        j = sess.get(f"https://www.screener.in/api/company/search/?q={requests.utils.quote(clean)}",
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=12).json()
        if not j:
            return None
        p = sess.get("https://www.screener.in" + j[0]["url"],
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=12)
        m = _SCR_RE.search(p.text)
        if m:
            return float(m.group(1).replace(",", ""))
    except Exception:
        pass
    return None


def main():
    log(f"Comm-profile starting {datetime.utcnow().isoformat()}")
    today = datetime.utcnow() + timedelta(hours=5, minutes=30)

    # Only the Q4 (Jan-Mar) results season — its presentations/concalls are
    # filed Apr onwards. If we're past April, the most recent Q4 season is this
    # calendar year; else last year's.
    season_year = today.year if today.month >= 4 else today.year - 1
    start = datetime(season_year, 4, 1)
    SEASON_LABEL = f"Q4 FY{str(season_year)[2:]}"
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
        is_pres = bool(PRES_RE.search(subj))
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

    # market cap join + cutoff
    by_sym, by_name = load_known_mcap()
    mcap_map = {}          # key -> mcap_cr
    need = []
    for key, c in companies.items():
        m = by_sym.get(c["symbol"]) or by_name.get(key)
        if m is not None:
            mcap_map[key] = m
        else:
            need.append((key, c["name"]))
    log(f"mcap: {len(mcap_map)} from cache, fetching {len(need)} via screener (parallel)...")

    from concurrent.futures import ThreadPoolExecutor
    import threading
    _local = threading.local()
    def _sess():
        if not hasattr(_local, "s"):
            _local.s = requests.Session()
        return _local.s
    def _one(item):
        key, name = item
        return key, screener_mcap_cr(_sess(), name)
    done = 0
    with ThreadPoolExecutor(max_workers=10) as ex:
        for key, m in ex.map(_one, need):
            if m:
                mcap_map[key] = m
            done += 1
            if done % 100 == 0:
                log(f"  screener mcap {done}/{len(need)}")

    rows = []
    for key, c in companies.items():
        mcap = mcap_map.get(key)
        if not mcap or mcap < MCAP_MIN_CR or mcap > MCAP_MAX_CR:
            continue
        for q, qd in c["quarters"].items():
            rows.append({
                "company": c["name"],
                "symbol": c["symbol"],
                "mcap_cr": round(mcap, 0),
                "quarter": q,
                "presentation": qd["pres"],
                "concall": qd["call"],
                "date": qd.get("date", ""),
                "pres_date": qd.get("pres_date", ""),
                "call_date": qd.get("call_date", ""),
            })
    log(f"rows after cutoff: {len(rows)}")

    rows.sort(key=lambda r: (r["quarter"], -r["mcap_cr"]), reverse=True)
    quarters = sorted({r["quarter"] for r in rows}, reverse=True)

    out = {
        "generated_at": datetime.utcnow().isoformat(),
        "months_back": MONTHS_BACK,
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
