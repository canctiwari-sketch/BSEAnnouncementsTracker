"""
Worker script for GitHub Actions.
Fetches BSE + NSE announcements, filters, deduplicates,
summarizes NEW ones via Gemini Flash, saves to data/announcements.json.
"""

import json
import os
import re
import sys
import time
import hashlib
import requests
import httpx
from datetime import datetime, timedelta
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

# ─── Paths ───────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
CACHE_FILE = os.path.join(DATA_DIR, "announcements.json")
GEMINI_KEY = os.environ.get("GEMINI_API_KEY", "")

# ─── Noise Patterns (same as bse_api.py) ─────────────────────────────────────
NOISE_PATTERNS = [
    r"prohibition of insider trading",
    r"insider trading",
    r"closure of trading window",
    r"trading window",
    r"change in registered office",
    r"registered office address",
    r"appointment of company secretary",
    r"resignation of company secretary",
    r"compliance officer",
    r"change in management",
    r"change in senior management",
    r"resignation of director",
    r"resignation of chief financial",
    r"cessation.*director",
    r"appointment.*additional director",
    r"appointment.*independent director",
    r"appointment.*nominee director",
    r"scrutinizer.s report",
    r"scrutiniser.s report",
    r"notice of postal.ballot",
    r"voting result.*postal ballot",
    r"outcome of postal.ballot",
    r"newspaper publication",
    r"newspaper advertisement",
    r"notice of separate meeting of independent director",
    r"independent directors.?\s*meeting",
    r"allotment of esop",
    r"allotment of esps",
    r"analyst.*investor.*meet",
    r"investor.*meet.*intimation",
    r"schedule of analyst",
    r"book closure",
    r"record date",
    r"cut.off date",
    r"loss of share certificate",
    r"special window.*transfer",
    r"special window.*demateriali",
    r"dematerialisation of physical",
    r"transfer.*physical.*securit",
    r"compliance certificate",
    r"certificate under",
    r"reg\.?\s*74.*debenture",
    r"change in director.*address",
    r"notice of.*agm",
    r"notice of.*egm",
    r"annual report",
    r"related party transaction",
    r"listing fee",
    r"annual fee",
    r"as per the attachment",
    r"as per attachment",
    r"please refer to attachment",
    r"please find enclosed\s*$",
    r"please find the enclosed",
    r"as attached",
    r"submission of disclosure under regulation",
    r"intimation.update under reg",
    r"intimation under regulation 30",
    r"pursuant to regulation 30",
    r"pursuant to regulation 29",
    r"disclosure under regulation 30",
    r"disclosure pursuant to regulation",
    r"in.principal approval.*from bse",
    r"in.principal approval.*from nse",
    r"listing approval.*equity share",
    r"listing application",
    r"re.lodgement of transfer",
    r"investor presentation",
    r"transfer of shares",
    r"transmission of shares",
    r"movement in price",
    r"movement in volume",
    r"increase in volume",
    r"decrease in volume",
    r"sought clarification",
    r"reference to.*movement",
    r"clarification.*price",
    r"clarification.*volume",
    r"clarification on increase",
    r"clarification on decrease",
    r"spurt in volume",
    r"corporate insolvency resolution process",
    r"insolvency resolution",
    r"^resignation$",
    r"^resignation of independent director$",
    r"^appointment$",
    r"^change in director",
    r"^cessation$",
    r"cessation",
    r"appointment.*company secretary",
    r"resignation.*company secretary",
    r"appointment.*independent director",
    r"^shareholders? meeting$",
    r"agm|annual general meeting",
    r"extra.?ordinary general meeting",
    r"esop|esos|esps",
    r"allotment of securities",
    r"allotment of shares",
    r"esg rating",
    r"^credit rating",
    r"pendency of litigation",
    r"pending litigation",
    r"^address change$",
    r"^corrigendum$",
    r"^name change$",
    r"spurt in volume",
    r"post offer advertisement",
    r"submission of trust deed",
    r"release of pledged",
    r"pledge.*shares",
    r"comments.*(?:fine|penalty).*stock exchange",
    r"dividend",
    r"interim dividend",
    r"final dividend",
    r"^dividend$",
    r"credit rating",
    r"^committee meeting updates$",
    r"action\(s\) taken or orders passed",
    r"^fraud/default/arrest$",
    r"change of name",
    r"intimation of record date",
    r"disclosures under reg",
]

_noise_re = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)

IMPORTANT_PATTERNS = [
    r"order", r"contract", r"acquisition", r"merger", r"demerger",
    r"joint venture", r"tie.up", r"partnership", r"collaboration",
    r"expansion", r"new plant", r"new facility", r"commenced.*operation",
    r"commissioned", r"bonus", r"buyback", r"buy.back", r"split",
    r"rights issue", r"fund.?rais", r"ncd\s", r"debenture.*issue",
    r"qip", r"ipo", r"ofs|offer for sale", r"fpo",
    r"result", r"financial.result", r"quarterly.*result", r"annual.*result",
    r"upgrade", r"downgrade", r"clarification", r"press release",
    r"media release", r"divestment", r"disinvestment", r"stake.*sale",
    r"subsidiary", r"wholly owned", r"incorporation", r"delisting",
    r"allotment.*debenture", r"preferential", r"warrant",
    r"open offer", r"takeover", r"board meeting.*consider", r"raising.*fund",
]
_important_re = re.compile("|".join(IMPORTANT_PATTERNS), re.IGNORECASE)

# Starred categories
STARRED_CATEGORIES = {"Business Expansion", "Fund Raising", "Capital Structure", "Acquisition"}
STARRED_KEYWORDS = re.compile(
    r"capex|capital expenditure|expansion|warrant|raising.*capital|raise.*fund",
    re.IGNORECASE,
)

# ─── Category Rules ──────────────────────────────────────────────────────────
CATEGORY_RULES = [
    ("New Order", re.compile(
        r"order|contract.*award|letter of intent|LOI|work order|purchase order|"
        r"supply agreement|received.*order|bagged.*order|secured.*order|"
        r"award.*contract|empanelment", re.I)),
    ("Results", re.compile(
        r"financial result|quarterly result|annual result|half.yearly result|"
        r"un.?audited.*result|audited.*result|standalone.*result|consolidated.*result|"
        r"profit|loss.*quarter|revenue|turnover|earning", re.I)),
    ("Acquisition", re.compile(
        r"acqui(?:sition|red|ring)|takeover|open offer|bought|purchase.*stake|"
        r"purchase.*share|purchase.*business|buy.*stake", re.I)),
    ("Merger/Demerger", re.compile(
        r"merger|demerger|amalgamation|scheme of arrangement|composite scheme", re.I)),
    ("Fund Raising", re.compile(
        r"fund.?rais|qip|qualified institutional|rights issue|fpo|"
        r"preferential.*allot|preferential.*issue|warrant|convertible|"
        r"ncd|debenture.*issue|ipo|initial public|private placement", re.I)),
    ("Business Expansion", re.compile(
        r"expansion|new plant|new facility|new unit|capex|capital expenditure|"
        r"greenfield|brownfield|commissioned|commenced.*operation|"
        r"capacity.*addition|capacity.*expansion|production.*start|"
        r"new factory|new warehouse|inaugurat", re.I)),
    ("Joint Venture", re.compile(
        r"joint venture|jv|tie.up|partnership|collaboration|mou|"
        r"memorandum of understanding|strategic alliance|consortium", re.I)),
    ("Capital Structure", re.compile(
        r"bonus|stock split|sub.?division|buyback|buy.back|"
        r"reduction.*capital|alteration.*capital|reclassification", re.I)),
    ("Board Meeting", re.compile(
        r"board meeting|outcome of board|board.*consider|"
        r"meeting of board|resolution.*board", re.I)),
    ("Press Release", re.compile(
        r"press release|media release|press note|news release", re.I)),
    ("Subsidiary", re.compile(
        r"subsidiary|wholly owned|incorporation.*company|"
        r"new company|step.down subsidiary", re.I)),
    ("Divestment", re.compile(
        r"divestment|disinvestment|divest|disposal|"
        r"sale of.*stake|sale of.*business|sale of.*unit|"
        r"stake sale|sold.*stake", re.I)),
    ("Delisting", re.compile(r"delisting|delist", re.I)),
    ("Regulatory", re.compile(
        r"sebi|stock exchange|penalty|fine.*imposed|"
        r"show cause|adjudication|settlement|"
        r"clarification.*exchange|clarification.*sebi", re.I)),
    ("Allotment", re.compile(
        r"allotment.*share|allotment.*debenture|allotment.*securit|"
        r"allotment.*equity", re.I)),
    ("Clarification", re.compile(
        r"clarification|response to.*query|reply to.*exchange|"
        r"price movement|media report", re.I)),
]


def categorize(subject, detail="", bse_category=""):
    combined = f"{subject} {detail}"
    for cat, pat in CATEGORY_RULES:
        if pat.search(combined):
            return cat
    if "Board Meeting" in bse_category:
        return "Board Meeting"
    if "Corp. Action" in bse_category:
        return "Capital Structure"
    return "Other"


def is_noise(text, subject=""):
    if _noise_re.search(text):
        return True
    if subject and _noise_re.search(subject):
        return True
    return False


def is_important_bse(sub, headline, category_name):
    combined = f"{sub} {headline}"
    if _noise_re.search(combined):
        return False
    if _important_re.search(combined):
        return True
    if category_name in ("AGM/EGM", "Credit Rating", "Dividend", "ESOP/ESOS/ESPS"):
        return False
    return True


def is_starred(category, subject=""):
    if category in STARRED_CATEGORIES:
        return True
    if STARRED_KEYWORDS.search(subject):
        return True
    return False


def _extract_nse_symbol(nsurl):
    """Extract short symbol from BSE NSURL field (often matches NSE symbol)."""
    if not nsurl:
        return ""
    # URL format: .../stock-share-price/company-name/SYMBOL/scripcode/
    parts = nsurl.rstrip("/").split("/")
    if len(parts) >= 2:
        return parts[-2].upper()
    return ""


# ─── BSE Fetching ────────────────────────────────────────────────────────────
def fetch_bse(from_date, to_date):
    """Fetch BSE announcements, filter noise, return normalized list."""
    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    str_from = from_dt.strftime("%Y%m%d")
    str_to = to_dt.strftime("%Y%m%d")

    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    })

    all_raw = []
    seen_ids = set()
    for page in range(1, 6):
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
            f"?strCat=-1&strPrevDate={str_from}&strScrip=&strSearch=P"
            f"&strToDate={str_to}&strType=C&pageno={page}"
        )
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            items = data.get("Table") or []
            if not items:
                break
            for item in items:
                nid = item.get("NEWSID")
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    all_raw.append(item)
        except Exception as e:
            print(f"BSE page {page} error: {e}")
            break

    # Filter
    results = []
    for a in all_raw:
        sub = a.get("NEWSSUB") or ""
        headline = a.get("HEADLINE") or ""
        cat_name = a.get("CATEGORYNAME") or ""
        if not is_important_bse(sub, headline, cat_name):
            continue

        att = a.get("ATTACHMENTNAME") or ""
        category = categorize(sub, headline, cat_name)
        nse_sym = _extract_nse_symbol(a.get("NSURL") or "")
        results.append({
            "company": a.get("SLONGNAME") or "Unknown",
            "symbol": str(a.get("SCRIP_CD") or ""),
            "exchange": "BSE",
            "subject": sub,
            "detail": headline,
            "date": a.get("NEWS_DT") or "",
            "attachment": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else "",
            "category": category,
            "starred": is_starred(category, sub),
            "_nse_symbol": nse_sym,
        })

    return results


# ─── NSE Fetching (HTTP/2 via httpx — bypasses cloud IP blocks) ──────────────
def _get_nse_client():
    """Create an httpx HTTP/2 client for NSE. Works from datacenter IPs."""
    client = httpx.Client(http2=True, follow_redirects=True, timeout=15)
    client.headers.update({
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate",
    })
    try:
        r = client.get("https://www.nseindia.com")
        # Even if homepage returns 403, cookies are set and API works
        log(f"NSE homepage: {r.status_code} (HTTP/2), cookies: {len(r.cookies)}")
    except Exception as e:
        print(f"NSE session error: {e}")
        client.close()
        return None

    client.headers.update({
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.nseindia.com/",
    })
    return client


def fetch_nse_mcap(client, symbol):
    """Fetch market cap from NSE for a symbol."""
    try:
        encoded = quote(symbol, safe="")
        r = client.get(
            f"https://www.nseindia.com/api/quote-equity?symbol={encoded}",
        )
        if r.status_code != 200:
            return None
        d = r.json()
        price = d.get("priceInfo", {}).get("lastPrice", 0)
        issued = d.get("securityInfo", {}).get("issuedSize", 0)
        if price and issued:
            raw = price * issued
            cr = raw / 1e7
            if cr >= 100000:
                fmt = f"{cr / 100000:.2f}L Cr"
            elif cr >= 1000:
                fmt = f"{cr / 1000:.2f}K Cr"
            elif cr >= 1:
                fmt = f"{cr:.0f} Cr"
            else:
                fmt = f"{raw:,.0f}"
            return {"value": raw, "formatted": fmt}
    except Exception:
        pass
    return None


def fetch_nse(from_date, to_date):
    """Fetch NSE announcements, filter noise, return normalized list."""
    client = _get_nse_client()
    if not client:
        print("NSE session failed")
        return []

    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    nse_from = from_dt.strftime("%d-%m-%Y")
    nse_to = to_dt.strftime("%d-%m-%Y")

    url = (
        f"https://www.nseindia.com/api/corporate-announcements"
        f"?index=equities&from_date={nse_from}&to_date={nse_to}"
    )

    try:
        r = client.get(url)
        if r.status_code in (401, 403):
            # Retry with fresh client
            client.close()
            client = _get_nse_client()
            if not client:
                return []
            r = client.get(url)
        r.raise_for_status()
        raw = r.json() if r.text.strip() else []
    except Exception as e:
        print(f"NSE fetch error: {e}")
        client.close()
        return []

    log(f"NSE raw announcements: {len(raw)}")

    # Filter noise FIRST, then collect symbols for market cap
    filtered_raw = []
    for a in raw:
        subject = a.get("desc") or ""
        detail = a.get("attchmntText") or ""
        combined = f"{subject} {detail}"
        if not is_noise(combined, subject):
            filtered_raw.append(a)

    log(f"NSE after noise filter: {len(filtered_raw)}")

    # Fetch market caps only for filtered symbols (cap at 60)
    symbols = list(set(a.get("symbol", "").strip() for a in filtered_raw if a.get("symbol")))
    if len(symbols) > 60:
        symbols = symbols[:60]
    mcap_data = {}
    log(f"Fetching market cap for {len(symbols)} symbols...")
    for i, sym in enumerate(symbols):
        data = fetch_nse_mcap(client, sym)
        if data:
            mcap_data[sym] = data
        if (i + 1) % 5 == 0:
            log(f"  MCap progress: {i + 1}/{len(symbols)}")
            time.sleep(0.3)

    client.close()
    log(f"Got market cap for {len(mcap_data)} symbols")

    # Normalize
    results = []
    for a in filtered_raw:
        subject = a.get("desc") or ""
        detail = a.get("attchmntText") or ""
        sym = a.get("symbol", "").strip()
        mcap = mcap_data.get(sym, {})
        category = categorize(subject, detail)
        results.append({
            "company": a.get("sm_name") or "Unknown",
            "symbol": sym,
            "exchange": "NSE",
            "subject": subject,
            "detail": detail,
            "date": a.get("an_dt") or "",
            "attachment": a.get("attchmntFile") or "",
            "category": category,
            "starred": is_starred(category, subject),
            "market_cap": mcap.get("value"),
            "market_cap_fmt": mcap.get("formatted"),
        })

    return results


# ─── Normalization & Dedup ───────────────────────────────────────────────────
def _normalize_name(name):
    if not name:
        return ""
    name = name.lower().strip()
    for suffix in ["limited", "ltd.", "ltd", "pvt.", "pvt", "private", "inc.", "inc",
                    "corporation", "corp.", "corp", "industries", "ind."]:
        name = name.replace(suffix, "")
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _ann_key(a):
    """Unique key for an announcement (for cache dedup)."""
    att = a.get("attachment") or ""
    if att:
        return hashlib.md5(att.encode()).hexdigest()
    # Fallback: company + date + subject prefix
    key = f"{_normalize_name(a['company'])}::{a.get('date','')}::{a.get('subject','')[:60]}"
    return hashlib.md5(key.encode()).hexdigest()


def dedup(all_anns):
    """Deduplicate by normalized name + category within 60 min."""
    # Pass 1: exact subject match
    seen = {}
    results = []
    for a in all_anns:
        norm = _normalize_name(a["company"])
        subj = a.get("subject", "").lower()[:60]
        key = f"{norm}::{subj}"
        if key in seen:
            existing = seen[key]
            if (a.get("market_cap") and not existing.get("market_cap")) or \
               len(a.get("subject", "")) > len(existing.get("subject", "")):
                idx = results.index(existing)
                results[idx] = a
                seen[key] = a
        else:
            seen[key] = a
            results.append(a)

    # Pass 2: fuzzy — same company + category within 60 min
    def parse_dt(d):
        for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y"):
            try:
                return datetime.strptime(d, fmt)
            except (ValueError, TypeError):
                continue
        return None

    final = []
    seen_fuzzy = {}
    for a in results:
        norm = _normalize_name(a["company"])
        cat = a.get("category", "")
        dt = parse_dt(a.get("date", ""))
        fuzzy_key = (norm, cat)
        if fuzzy_key in seen_fuzzy and dt:
            prev_dt, prev_idx = seen_fuzzy[fuzzy_key]
            if prev_dt and abs((dt - prev_dt).total_seconds()) < 3600:
                existing = final[prev_idx]
                if existing and (a.get("market_cap") and not existing.get("market_cap")):
                    final[prev_idx] = a
                    seen_fuzzy[fuzzy_key] = (dt, prev_idx)
                continue
        seen_fuzzy[fuzzy_key] = (dt, len(final))
        final.append(a)

    return final


# ─── Gemini Summarizer ───────────────────────────────────────────────────────
def summarize_with_gemini(announcement):
    """Summarize an announcement using Gemini Flash."""
    if not GEMINI_KEY:
        return None

    subject = announcement.get("subject", "")
    detail = announcement.get("detail", "")
    company = announcement.get("company", "")
    category = announcement.get("category", "")

    prompt = f"""Summarize this stock exchange announcement in 2-3 concise sentences for an investor.
Focus on: what happened, financial impact, and why it matters.

Company: {company}
Category: {category}
Subject: {subject}
Details: {detail}

Summary:"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 150, "temperature": 0.3},
    }

    # Retry up to 3 times on rate limit
    for attempt in range(3):
        try:
            r = requests.post(url, json=payload, timeout=30)
            if r.status_code == 429:
                wait = 15 * (attempt + 1)
                log(f"  Gemini 429 rate limited, waiting {wait}s (attempt {attempt+1}/3)...")
                time.sleep(wait)
                continue
            r.raise_for_status()
            data = r.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return text.strip()
        except Exception as e:
            log(f"  Gemini error: {e}")
            if attempt < 2:
                time.sleep(5)
                continue
            return None
    return None


# ─── Main Worker ─────────────────────────────────────────────────────────────
def load_cache():
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"announcements": [], "last_updated": None, "seen_keys": []}


def save_cache(cache):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(CACHE_FILE, "w", encoding="utf-8") as f:
        json.dump(cache, f, ensure_ascii=False, indent=2)


def log(msg):
    """Print with flush so GitHub Actions shows logs in real-time."""
    print(msg, flush=True)


def main():
    log(f"Worker starting at {datetime.utcnow().isoformat()}")

    # Load existing cache
    cache = load_cache()
    seen_keys = set(cache.get("seen_keys", []))
    existing = cache.get("announcements", [])

    # Date range: today and yesterday (to catch late filings)
    today = datetime.utcnow() + timedelta(hours=5, minutes=30)  # IST
    yesterday = today - timedelta(days=1)
    from_date = yesterday.strftime("%Y-%m-%d")
    to_date = today.strftime("%Y-%m-%d")

    log(f"Fetching {from_date} to {to_date}...")

    # Fetch from both exchanges in parallel
    bse_anns = []
    nse_anns = []

    def do_bse():
        nonlocal bse_anns
        bse_anns = fetch_bse(from_date, to_date)
        log(f"BSE: {len(bse_anns)} announcements after filtering")

    def do_nse():
        nonlocal nse_anns
        nse_anns = fetch_nse(from_date, to_date)
        log(f"NSE: {len(nse_anns)} announcements after filtering")

    with ThreadPoolExecutor(max_workers=2) as ex:
        ex.submit(do_bse)
        ex.submit(do_nse)

    # Fill BSE market cap from NSE data (cross-reference)
    nse_mcap_by_name = {}
    for a in nse_anns:
        if a.get("market_cap"):
            nse_mcap_by_name[_normalize_name(a["company"])] = {
                "market_cap": a["market_cap"],
                "market_cap_fmt": a["market_cap_fmt"],
            }

    # For BSE companies: first try cross-reference, then fetch from NSE directly
    bse_need_mcap = []
    for a in bse_anns:
        norm = _normalize_name(a["company"])
        if norm in nse_mcap_by_name:
            a["market_cap"] = nse_mcap_by_name[norm]["market_cap"]
            a["market_cap_fmt"] = nse_mcap_by_name[norm]["market_cap_fmt"]
        elif a.get("_nse_symbol"):
            bse_need_mcap.append(a)

    # Fetch market cap from NSE for remaining BSE companies (cap at 40)
    if bse_need_mcap:
        # Deduplicate symbols
        sym_to_anns = {}
        for a in bse_need_mcap:
            sym = a["_nse_symbol"]
            if sym not in sym_to_anns:
                sym_to_anns[sym] = []
            sym_to_anns[sym].append(a)

        symbols_to_fetch = list(sym_to_anns.keys())[:40]
        log(f"Fetching market cap for {len(symbols_to_fetch)} BSE companies via NSE...")
        client = _get_nse_client()
        if client:
            for i, sym in enumerate(symbols_to_fetch):
                data = fetch_nse_mcap(client, sym)
                if data:
                    for a in sym_to_anns[sym]:
                        a["market_cap"] = data["value"]
                        a["market_cap_fmt"] = data["formatted"]
                if (i + 1) % 5 == 0:
                    log(f"  BSE MCap progress: {i + 1}/{len(symbols_to_fetch)}")
                    time.sleep(0.3)
            client.close()
            filled = sum(1 for a in bse_anns if a.get("market_cap"))
            log(f"BSE market cap filled: {filled}/{len(bse_anns)}")

    # Clean internal _nse_symbol field
    for a in bse_anns:
        a.pop("_nse_symbol", None)

    # Combine and dedup
    all_anns = bse_anns + nse_anns
    all_anns.sort(key=lambda a: a.get("date", ""), reverse=True)
    deduped = dedup(all_anns)
    log(f"After dedup: {len(deduped)}")

    # Filter micro-caps (below 50 Cr)
    MIN_MCAP = 50 * 1e7
    filtered = []
    for a in deduped:
        mcap = a.get("market_cap")
        if mcap is None or mcap >= MIN_MCAP:
            filtered.append(a)
    log(f"After mcap filter: {len(filtered)}")

    # Find NEW announcements (not in cache)
    new_anns = []
    for a in filtered:
        key = _ann_key(a)
        a["_key"] = key
        if key not in seen_keys:
            new_anns.append(a)
            seen_keys.add(key)

    log(f"New announcements: {len(new_anns)}")

    # Summarize new announcements with Gemini (rate-limited to ~12 RPM)
    summarized = 0
    for i, a in enumerate(new_anns):
        if not GEMINI_KEY:
            a["ai_summary"] = None
            continue

        summary = summarize_with_gemini(a)
        if summary:
            a["ai_summary"] = summary
            summarized += 1
        else:
            a["ai_summary"] = None

        # Progress log every 10
        if (i + 1) % 10 == 0:
            log(f"  Summarized progress: {i + 1}/{len(new_anns)} ({summarized} successful)")

        # Wait 5 seconds between EVERY call to stay under 15 RPM
        time.sleep(5)

    log(f"Summarized {summarized} announcements with Gemini")

    # Merge new into existing (prepend new, keep last 7 days)
    cutoff = (today - timedelta(days=7)).isoformat()
    # Filter old announcements
    kept_existing = [a for a in existing if a.get("date", "") >= cutoff]

    # Merge: new first, then existing (avoid duplicates)
    existing_keys = set(a.get("_key", "") for a in kept_existing)
    merged = []
    for a in new_anns:
        if a["_key"] not in existing_keys:
            merged.append(a)
    merged.extend(kept_existing)

    # Sort by date
    merged.sort(key=lambda a: a.get("date", ""), reverse=True)

    # Clean internal keys before saving
    for a in merged:
        a.pop("_key", None)

    # Save
    cache = {
        "announcements": merged,
        "last_updated": datetime.utcnow().isoformat(),
        "total_count": len(merged),
        "seen_keys": list(seen_keys),
    }
    save_cache(cache)
    log(f"Saved {len(merged)} announcements to {CACHE_FILE}")
    log(f"Done at {datetime.utcnow().isoformat()}")


if __name__ == "__main__":
    main()
