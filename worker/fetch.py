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
    r"intimation.update under reg.*(?:insider|trading window|closure)",
    r"disclosure.*prohibition of insider trading",
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
    r"resignation of",
    r"resignation",
    r"appointment of",
    r"appointment.*director",
    r"appointment.*auditor",
    r"appointment.*statutory auditor",
    r"appointment.*internal auditor",
    r"appointment.*secretarial auditor",
    r"appointment of managing director",
    r"appointment.*ceo",
    r"appointment.*cfo",
    r"appointment.*company secretary",
    r"appointment.*compliance officer",
    r"appointment.*key managerial",
    r"re.?appointment of",
    r"^appointment$",
    r"change in director",
    r"change in directorate",
    r"change.*key managerial",
    r"change.*contact details.*kmp",
    r"change.*senior management",
    r"^cessation$",
    r"cessation",
    r"resignation.*company secretary",
    r"appointment.*independent director",
    r"performance.*independent director",
    r"review.*independent director",
    r"separate meeting.*independent director",
    r"evaluation.*board",
    r"evaluation.*director",
    r"leadership transition",
    r"call money notice",
    r"second call.*money",
    r"materiality of events",
    r"determine the materiality",
    r"regulation 30\(5\)",
    r"contact details.*authorized",
    r"^shareholders? meeting$",
    r"agm|annual general meeting",
    r"extra.?ordinary general meeting",
    r"esop|esos|esps",
    r"allotment of securities",
    r"allotment of shares",
    r"esg rating",
    r"board meeting.*to be held",
    r"board meeting.*scheduled",
    r"board meeting.*will be held",
    r"board meeting.*is scheduled",
    r"intimation of board meeting",
    r"prior intimation.*board meeting",
    r"advance intimation.*board meeting",
    r"notice of board meeting",
    r"date of board meeting",
    r"income tax",
    r"income.tax",
    r"tax demand",
    r"tax order",
    r"tax assessment",
    r"tax notice",
    r"tax filing",
    r"tax return",
    r"goods and services tax",
    r"gst demand",
    r"gst order",
    r"gst notice",
    r"gst filing",
    r"gst return",
    r"gst registration",
    r"^gst",
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
    r"income tax",
    r"tax demand",
    r"tax order",
    r"tax notice",
    r"gst.*order",
    r"gst.*demand",
    r"gst.*notice",
    r"cgst",
    r"sgst",
    r"igst",
    r"central goods and services tax",
    r"state goods and services tax",
    r"board composition",
    r"composition of.*board",
    r"composition of.*committee",
    r"reconstitution.*committee",
    r"constitution of.*committee",
    r"gst.*filing",
    r"advance tax",
    r"tax assessment",
    r"tax.?related",
    r"^committee meeting updates$",
    r"action\(s\) taken or orders passed",
    r"^fraud/default/arrest$",
    r"change of name",
    r"intimation of record date",
    r"disclosures under reg",
    r"duplicate share certificate",
    r"issue of duplicate",
    r"stock lending",
    r"code of conduct",
    r"code of fair disclosure",
    r"minutes of meeting",
    r"minutes of.*annual general",
    r"minutes of.*board meeting",
    r"familiarisation programme",
    r"familiarization programme",
    r"secretarial compliance report",
    r"corporate governance report",
    r"certificate.*non.?disqualification",
    r"reconciliation of share capital",
    r"statement of investor complaints",
    r"disclosure of events",
    r"regulation 39.*3",
    r"confirmation.*return of excess",
    r"updation of email",
    r"iepf",
    r"unclaimed dividend",
    r"large corporate",
    r"identified as a large corporate",
    r"initial disclosure.*large corporate",
    r"format of.*disclosure.*large corporate",
    # Physical shares / transfer / demat noise
    r"physical.*share",
    r"shares? held in physical",
    r"demat.*physical",
    r"physical.*demat",
    r"re.?mater",
    r"sub.?division.*physical",
    r"split.*physical",
    r"consolidation.*physical",
    r"transposition",
    r"transmission of physical",
    r"transfer.*physical",
    r"issue.*physical.*certificate",
    r"physical.*certificate",
    r"request.*physical",
    r"conversion.*physical",
    r"demateriali[sz]ation.*request",
    # SAST Reg 31(4) — annual "no encumbrance" promoter declarations (routine, not Reg 29)
    r"regulation 31\(4\)",
    r"regulation 31\s*\(4\)",
    r"no encumbrance",
    r"not made any encumbrance",
    r"have not created.*encumbrance",
    r"have not.*encumber",
    r"declaration.*encumbrance",
    r"disclosure.*encumbrance.*promoter",
    r"encumbrance.*promoter.*group",
    r"yearly disclosure.*encumbrance",
    r"annual disclosure.*encumbrance",
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
STARRED_CATEGORIES = {"Open Offer", "Warrants", "Buyback", "Delisting", "Business Expansion"}
STARRED_KEYWORDS = re.compile(
    r"open.?offer|warrants?|buybacks?|buy.?backs?|delisting|delist|capex|capital expenditure|expansion",
    re.IGNORECASE,
)

# ─── Category Rules ──────────────────────────────────────────────────────────
CATEGORY_RULES = [
    # Priority categories — matched first
    ("Open Offer", re.compile(r"open.?offer", re.I)),
    ("Warrants", re.compile(r"warrants?", re.I)),
    ("Buyback", re.compile(r"buybacks?|buy.?backs?", re.I)),
    ("New Order", re.compile(
        r"order|contract.*award|letter of intent|LOI|work order|purchase order|"
        r"supply agreement|received.*order|bagged.*order|secured.*order|"
        r"award.*contract|empanelment", re.I)),
    ("Results", re.compile(
        r"financial result|quarterly result|annual result|half.yearly result|"
        r"un.?audited.*result|audited.*result|standalone.*result|consolidated.*result|"
        r"profit|loss.*quarter|revenue|turnover|earning", re.I)),
    ("Acquisition", re.compile(
        r"acqui(?:sition|red|ring)|takeover|bought|purchase.*stake|"
        r"purchase.*share|purchase.*business|buy.*stake", re.I)),
    ("Merger/Demerger", re.compile(
        r"merger|demerger|amalgamation|scheme of arrangement|composite scheme", re.I)),
    ("Fund Raising", re.compile(
        r"fund.?rais|qip|qualified institutional|rights issue|fpo|"
        r"preferential.*allot|preferential.*issue|convertible|"
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
        r"bonus|stock split|sub.?division|"
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


def _format_mcap(raw):
    """Format raw market cap value into human-readable string."""
    cr = raw / 1e7
    if cr >= 100000:
        return f"{cr / 100000:.2f}L Cr"
    elif cr >= 1000:
        return f"{cr / 1000:.2f}K Cr"
    elif cr >= 1:
        return f"{cr:.0f} Cr"
    else:
        return f"{raw:,.0f}"


def fetch_bse_mcap(session, scrip_code):
    """Fetch market cap directly from BSE StockTrading API using scrip code."""
    try:
        url = f"https://api.bseindia.com/BseIndiaAPI/api/StockTrading/w?flag=&scripcode={scrip_code}"
        r = session.get(url, timeout=10)
        if r.status_code != 200:
            return None
        d = r.json()
        # BSE StockTrading returns MktCapFull in Cr like "12,481.90"
        mktcap_str = d.get("MktCapFull") or d.get("MktCapFF") or ""
        if not mktcap_str:
            return None
        mktcap_str = str(mktcap_str).replace(",", "").strip()
        try:
            cr_val = float(mktcap_str)
        except ValueError:
            return None
        if cr_val <= 0:
            return None
        raw = cr_val * 1e7  # Convert Cr to raw value
        return {"value": raw, "formatted": _format_mcap(raw)}
    except Exception:
        return None


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
            return {"value": raw, "formatted": _format_mcap(raw)}
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

    # Fetch market caps for all filtered symbols
    symbols = list(set(a.get("symbol", "").strip() for a in filtered_raw if a.get("symbol")))
    mcap_data = {}
    log(f"Fetching market cap for {len(symbols)} NSE symbols...")
    for i, sym in enumerate(symbols):
        data = fetch_nse_mcap(client, sym)
        if data:
            mcap_data[sym] = data
        if (i + 1) % 5 == 0:
            log(f"  MCap progress: {i + 1}/{len(symbols)}")
            time.sleep(0.5)

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
    name = name.replace("&", " and ")
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
    """Deduplicate by normalized name + category on same day."""

    def parse_dt(d):
        for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y"):
            try:
                return datetime.strptime(d, fmt)
            except (ValueError, TypeError):
                continue
        return None

    def get_day(date_str):
        dt = parse_dt(date_str)
        return dt.strftime("%Y-%m-%d") if dt else ""

    def score(a):
        """Higher score = better entry to keep."""
        s = 0
        if a.get("market_cap"): s += 10
        if a.get("ai_summary"): s += 5
        s += len(a.get("subject", ""))
        return s

    # Pass 1: exact subject match
    seen = {}
    seen_idx = {}
    results = []
    for a in all_anns:
        norm = _normalize_name(a["company"])
        subj = a.get("subject", "").lower()[:60]
        key = f"{norm}::{subj}"
        if key in seen:
            if score(a) > score(seen[key]):
                results[seen_idx[key]] = a
                seen[key] = a
        else:
            seen[key] = a
            seen_idx[key] = len(results)
            results.append(a)

    # Pass 2: same company + same category + same day = duplicate
    final = []
    seen_day_cat = {}  # (norm, day, cat) -> idx in final
    seen_company_day = {}  # (norm, day) -> [(idx, subject)]
    for a in results:
        norm = _normalize_name(a["company"])
        cat = a.get("category", "")
        day = get_day(a.get("date", ""))
        is_dup = False

        # 2a: same company + category + day
        day_cat_key = (norm, day, cat)
        if day_cat_key in seen_day_cat:
            prev_idx = seen_day_cat[day_cat_key]
            if score(a) > score(final[prev_idx]):
                final[prev_idx] = a
            is_dup = True

        # 2b: same company + same day + different exchange = cross-exchange dupe
        if not is_dup:
            day_key = (norm, day)
            if day_key in seen_company_day:
                for prev_idx, prev_subj, prev_exchange in seen_company_day[day_key]:
                    # Different exchange = almost certainly same announcement
                    if a.get("exchange") != prev_exchange:
                        if score(a) > score(final[prev_idx]):
                            final[prev_idx] = a
                        is_dup = True
                        break
                    # Same exchange but overlapping subject = dupe
                    subj_words = set(a.get("subject", "").lower().split())
                    prev_words = set(prev_subj.lower().split())
                    if prev_words and subj_words:
                        overlap = len(subj_words & prev_words) / max(1, min(len(subj_words), len(prev_words)))
                        if overlap > 0.4:
                            if score(a) > score(final[prev_idx]):
                                final[prev_idx] = a
                            is_dup = True
                            break

        if is_dup:
            continue

        idx = len(final)
        seen_day_cat[day_cat_key] = idx
        day_key = (norm, day)
        if day_key not in seen_company_day:
            seen_company_day[day_key] = []
        seen_company_day[day_key].append((idx, a.get("subject", ""), a.get("exchange", "")))
        final.append(a)

    return final


# ─── PDF Text Extraction ─────────────────────────────────────────────────────
def extract_pdf_text(url, max_chars=3000):
    """Download PDF and extract first ~max_chars of text using pdfplumber (table-aware)."""
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
        })
        if r.status_code != 200:
            return ""
        import io
        import pdfplumber
        text_parts = []
        with pdfplumber.open(io.BytesIO(r.content)) as pdf:
            for page in pdf.pages[:5]:  # First 5 pages
                # Extract tables first — pdfplumber parses them as structured rows
                for table in page.extract_tables():
                    for row in table:
                        cells = [str(c).strip() if c else "" for c in row]
                        row_text = " | ".join(c for c in cells if c)
                        if row_text:
                            text_parts.append(row_text)
                # Then regular text
                page_text = page.extract_text() or ""
                if page_text:
                    text_parts.append(page_text)
                if sum(len(p) for p in text_parts) >= max_chars:
                    break
        text = "\n".join(text_parts)
        text = re.sub(r'\s+', ' ', text).strip()
        return text[:max_chars]
    except Exception as e:
        log(f"  PDF extract error for {url[:60]}: {e}")
        return ""


# ─── Gemini Batch Summarizer ──────────────────────────────────────────────────
def summarize_batch(announcements_batch):
    """Summarize a batch of announcements in ONE Gemini call. Returns list of summaries."""
    if not GEMINI_KEY or not announcements_batch:
        return [None] * len(announcements_batch)

    # Extract PDF text for each announcement
    log(f"  Downloading {len(announcements_batch)} PDFs...")
    pdf_texts = []
    for a in announcements_batch:
        pdf_text = extract_pdf_text(a.get("attachment", ""))
        pdf_texts.append(pdf_text)

    # Build batch prompt with PDF content
    parts = []
    for i, (a, pdf_text) in enumerate(zip(announcements_batch, pdf_texts)):
        entry = f"""[{i+1}]
Company: {a.get('company', '')}
Category: {a.get('category', '')}
Subject: {a.get('subject', '')}
Details: {a.get('detail', '')}"""
        if pdf_text:
            entry += f"\nPDF Content: {pdf_text}"
        parts.append(entry)

    categories_list = "Open Offer, Warrants, Buyback, New Order, Results, Acquisition, Merger/Demerger, Fund Raising, Business Expansion, Joint Venture, Capital Structure, Board Meeting, Press Release, Subsidiary, Divestment, Delisting, Regulatory, Allotment, Clarification, Corporate Guarantee, Plant Visit, SAST/Insider, Litigation, General Update"

    prompt = f"""For each stock exchange announcement below, provide:
1. A CATEGORY from this list: {categories_list}
2. A SUMMARY for an investor, based STRICTLY on the actual content provided.

LENGTH RULE (very important):
- DEFAULT: write 5-6 detailed sentences extracting ALL specific facts, numbers, names, and dates from the PDF Content. This is the expected length for most filings.
- SHORT FALLBACK: only when the PDF Content is genuinely empty/missing OR contains nothing beyond "the board met" with zero details, write 1-2 sentences ending with "No material financial details disclosed in this filing." Do NOT use this fallback if the PDF has any numbers, names, or specifics — extract them instead.
- Quarterly result filings, order announcements, acquisitions, fund-raises, presentations — these ALWAYS have material content; produce 5-6 sentences with the actual numbers.

CONTENT RULES:
- Use ONLY facts explicitly present in the Subject, Details, or PDF Content. Do NOT invent.
- NEVER use bracketed placeholders like [Date of Meeting], [Amount], [TBD], [X]. If a fact is not present, omit it.
- NEVER write filler like "investors should review the detailed outcome" or "crucial for understanding the company's plans".
- For Results announcements: extract revenue, EBITDA, PAT, EPS, YoY/QoQ growth %, margin %, dividend (with exact numbers from the PDF tables).
- For Order/Contract announcements: extract order value (Rs / Cr / Lakhs), client name, execution timeline, scope.
- For share transactions: state ACQUIRED/SOLD/GIFTED/PLEDGED/ALLOTTED, exact share count, % of voting capital before AND after.
- Include specific NAMES (buyer/seller/promoter/counterparty/subsidiary) and DATES (board meeting, transaction, record, effective).
- Mention WHAT HAPPENS NEXT only if disclosed: pending approvals, EGM/AGM votes, NCLT hearings, SEBI filings.
- For orders: mention order value, client name, delivery timeline — only if disclosed.
- Do NOT use vague phrases like "potentially impacting growth" or "details are in the annexure".

Format each response EXACTLY as:
[N] Category: <category>
<summary text>

{chr(10).join(parts)}"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={GEMINI_KEY}"
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": 700 * len(announcements_batch), "temperature": 0.3},
    }

    # Single attempt — if rate limited, skip and let next hourly run handle it
    try:
        r = requests.post(url, json=payload, timeout=90)
        if r.status_code == 429:
            log("  Gemini 429 rate limited — skipping, will retry next hour")
            return "RATE_LIMITED"
        r.raise_for_status()
        data = r.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"]
        company_names = [a.get("company", "") for a in announcements_batch]
        return _parse_batch_response(text, len(announcements_batch), company_names)
    except Exception as e:
        log(f"  Gemini batch error: {e}")
        return [None] * len(announcements_batch)


VALID_CATEGORIES = {
    "Open Offer", "Warrants", "Buyback",
    "New Order", "Results", "Acquisition", "Merger/Demerger", "Fund Raising",
    "Business Expansion", "Joint Venture", "Capital Structure", "Board Meeting",
    "Press Release", "Subsidiary", "Divestment", "Delisting", "Regulatory",
    "Allotment", "Clarification", "Corporate Guarantee", "Plant Visit",
    "SAST/Insider", "Litigation", "General Update",
}

def _parse_batch_response(text, expected_count, company_names=None):
    """Parse numbered responses with category and summary from Gemini.
    company_names: list of company names to validate responses against."""
    results = [None] * expected_count
    import re as _re
    parts = _re.split(r'\[(\d+)\]', text)
    for i in range(1, len(parts) - 1, 2):
        try:
            idx = int(parts[i]) - 1
            content = parts[i + 1].strip().strip(':').strip()
            if 0 <= idx < expected_count and content:
                # Try "Category: X\n" format
                cat_match = _re.match(r'(?:\*\*)?Category:\s*(?:\*\*)?(.+?)(?:\*\*)?\n', content)
                if cat_match:
                    category = cat_match.group(1).strip().strip('*')
                    summary = content[cat_match.end():].strip()
                else:
                    # Try "CategoryName: summary..." format (first word before colon)
                    colon_match = _re.match(r'(?:\*\*)?([A-Za-z /]+?)(?:\*\*)?:\s*', content)
                    if colon_match and colon_match.group(1).strip().strip('*') in VALID_CATEGORIES:
                        category = colon_match.group(1).strip().strip('*')
                        summary = content[colon_match.end():].strip()
                    else:
                        category = None
                        summary = content

                # Validate: check if summary mentions the correct company
                if company_names and idx < len(company_names):
                    expected_name = company_names[idx].lower().split()[0]  # First word of company name
                    # If summary mentions a completely different company name from the batch,
                    # it's likely mismatched — skip it so it retries next run
                    if len(expected_name) > 3 and summary:
                        # Check if any OTHER company name from the batch appears in this summary
                        # but NOT the expected company name
                        summary_lower = summary.lower()
                        expected_words = set(w.lower() for w in company_names[idx].split() if len(w) > 3)
                        found_expected = any(w in summary_lower for w in expected_words)
                        if not found_expected and len(summary) > 50:
                            # Check if another company's name is in this summary
                            for j, other_name in enumerate(company_names):
                                if j != idx:
                                    other_words = set(w.lower() for w in other_name.split() if len(w) > 3)
                                    found_other = any(w in summary_lower for w in other_words)
                                    if found_other:
                                        log(f"  WARNING: Summary [{idx+1}] mentions '{other_name}' instead of '{company_names[idx]}' — skipping")
                                        category = None
                                        summary = None
                                        break

                # Reject summaries containing bracketed placeholders or known boilerplate
                if summary:
                    bad_placeholder = _re.search(
                        r'\[(?:date of meeting|amount|company name|tbd|x|\.\.\.|insert|placeholder|number|value|name|details)\]',
                        summary, _re.IGNORECASE
                    )
                    # Also catch generic [Capitalized Phrase] patterns (likely a placeholder)
                    generic_placeholder = _re.search(r'\[[A-Z][A-Za-z ]{2,40}\]', summary)
                    boilerplate_phrases = [
                        "investors should review the detailed outcome",
                        "this announcement is crucial for understanding",
                        "provides an update on the company's strategic decisions",
                    ]
                    has_boilerplate = any(p.lower() in summary.lower() for p in boilerplate_phrases)
                    if bad_placeholder or generic_placeholder or has_boilerplate:
                        log(f"  WARNING: Summary [{idx+1}] contains placeholder/boilerplate — skipping for retry")
                        summary = None

                if summary:
                    results[idx] = {"category": category, "summary": summary}
        except (ValueError, IndexError):
            continue
    return results


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

    # FALLBACK: Fetch remaining BSE market caps directly from BSE API
    still_need_mcap = [a for a in bse_anns if not a.get("market_cap")]
    if still_need_mcap:
        # Deduplicate by scrip code
        scrip_to_anns = {}
        for a in still_need_mcap:
            sc = a.get("symbol", "")
            if sc and sc not in scrip_to_anns:
                scrip_to_anns[sc] = []
            if sc:
                scrip_to_anns[sc].append(a)

        scrips_to_fetch = list(scrip_to_anns.keys())[:50]
        log(f"Fetching market cap for {len(scrips_to_fetch)} BSE companies via BSE API (fallback)...")
        bse_session = requests.Session()
        bse_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.bseindia.com/",
        })
        for i, sc in enumerate(scrips_to_fetch):
            data = fetch_bse_mcap(bse_session, sc)
            if data:
                for a in scrip_to_anns[sc]:
                    a["market_cap"] = data["value"]
                    a["market_cap_fmt"] = data["formatted"]
            if (i + 1) % 10 == 0:
                log(f"  BSE MCap fallback progress: {i + 1}/{len(scrips_to_fetch)}")
            time.sleep(0.15)  # Be gentle with BSE API
        filled = sum(1 for a in bse_anns if a.get("market_cap"))
        log(f"BSE market cap total filled: {filled}/{len(bse_anns)}")

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

    # Sort oldest first so yesterday's announcements get summarized before today's
    new_anns.sort(key=lambda a: a.get("date", ""))
    log(f"New announcements: {len(new_anns)}")

    # Detect nighttime (IST 22:00–07:00) — no new announcements arrive,
    # so we can use full Gemini quota for clearing the backlog
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    ist_hour = ist_now.hour
    is_night = ist_hour >= 22 or ist_hour < 7
    log(f"IST time: {ist_now.strftime('%H:%M')} (night={is_night})")
    if is_night:
        log("Night mode: aggressive summary processing enabled")

    # Summarize new announcements with Gemini in batches of 10
    BATCH_SIZE = 5
    summarized = 0
    if not GEMINI_KEY:
        for a in new_anns:
            a["ai_summary"] = None
    else:
        for batch_start in range(0, len(new_anns), BATCH_SIZE):
            batch = new_anns[batch_start:batch_start + BATCH_SIZE]
            summaries = summarize_batch(batch)
            if summaries == "RATE_LIMITED":
                # Stop trying — unsummarized ones will be picked up next hour
                for a in new_anns[batch_start:]:
                    a["ai_summary"] = None
                log("  Stopping Gemini — rate limited. Unsummarized will retry next run.")
                break
            for a, result in zip(batch, summaries):
                if result and isinstance(result, dict):
                    a["ai_summary"] = result["summary"]
                    if result.get("category"):
                        a["category"] = result["category"]
                    summarized += 1
                else:
                    a["ai_summary"] = None
            log(f"  Batch {batch_start // BATCH_SIZE + 1}: {batch_start + len(batch)}/{len(new_anns)} done ({summarized} successful)")
            # Wait between batches to respect rate limits
            if batch_start + BATCH_SIZE < len(new_anns):
                time.sleep(2)

    log(f"Summarized {summarized} announcements with Gemini")

    # Retry summaries for existing announcements missing ai_summary OR containing placeholder/boilerplate
    if GEMINI_KEY:  # always retry unsummarized announcements
        def _is_bad_summary(s):
            if not s:
                return True
            if re.search(r'\[(?:date of meeting|amount|company name|tbd|insert|placeholder)\]', s, re.IGNORECASE):
                return True
            if re.search(r'\[[A-Z][A-Za-z ]{2,40}\]', s):
                return True
            for phrase in ("investors should review the detailed outcome",
                           "this announcement is crucial for understanding",
                           "provides an update on the company's strategic decisions"):
                if phrase.lower() in s.lower():
                    return True
            # Too-short summary that doesn't acknowledge missing details
            # (genuine "no material details" summaries say so explicitly)
            if len(s) < 120 and "no material financial details disclosed" not in s.lower():
                return True
            return False

        # Mark bad existing summaries as None so they enter the retry queue
        for a in existing:
            if _is_bad_summary(a.get("ai_summary")) and a.get("ai_summary") is not None:
                a["ai_summary"] = None

        need_retry = [a for a in existing if a.get("ai_summary") is None]
        # Sort oldest first — yesterday's announcements get summarized before today's
        need_retry.sort(key=lambda a: a.get("date", ""))
        # Night mode: aggressive processing — no new announcements to compete with
        RETRY_BATCH = 10 if is_night else 5
        MAX_RETRY = 500 if is_night else 100  # Night: clear entire backlog; Day: limit to avoid timeout
        RETRY_WAIT = 1 if is_night else 2
        need_retry = need_retry[:MAX_RETRY]
        if need_retry:
            log(f"Retrying {len(need_retry)} existing announcements missing summaries (batch={RETRY_BATCH}, night={is_night})...")
            retry_ok = 0
            for batch_start in range(0, len(need_retry), RETRY_BATCH):
                batch = need_retry[batch_start:batch_start + RETRY_BATCH]
                summaries = summarize_batch(batch)
                if summaries == "RATE_LIMITED":
                    log("  Rate limited during retry — stopping")
                    break
                for a, result in zip(batch, summaries):
                    if result and isinstance(result, dict):
                        a["ai_summary"] = result["summary"]
                        if result.get("category"):
                            a["category"] = result["category"]
                        retry_ok += 1
                log(f"  Retry batch {batch_start // RETRY_BATCH + 1}: {min(batch_start + RETRY_BATCH, len(need_retry))}/{len(need_retry)} done ({retry_ok} successful)")
                if batch_start + RETRY_BATCH < len(need_retry):
                    time.sleep(RETRY_WAIT)

    # Drop empty Board Meeting announcements — those where the PDF had no material content.
    # After the new prompt, these get "No material financial details disclosed" summaries.
    # We keep Board Meetings that mention results, dividend, order, acquisition, etc.
    _board_keep_re = re.compile(
        r"result|revenue|profit|loss|turnover|dividend|order|contract|acquisition|"
        r"merger|demerger|preferential|warrant|buyback|buy.?back|expansion|capex|"
        r"joint venture|fund.?rais|qip|rights|ipo|delisting|allotment|subsidiary|"
        r"divestment|open.?offer|bonus|split|ncd|debenture",
        re.IGNORECASE,
    )
    before_empty_bm = len(new_anns)
    new_anns = [
        a for a in new_anns
        if not (
            a.get("category") == "Board Meeting"
            and a.get("ai_summary")
            and "no material financial details disclosed" in a["ai_summary"].lower()
            and not _board_keep_re.search(
                f"{a.get('subject','')} {a.get('detail','')} {a.get('ai_summary','')}"
            )
        )
    ]
    if before_empty_bm != len(new_anns):
        log(f"Dropped {before_empty_bm - len(new_anns)} empty Board Meeting announcements")

    # Same pass for existing cached announcements
    before_empty_bm_ex = len(existing)
    existing = [
        a for a in existing
        if not (
            a.get("category") == "Board Meeting"
            and a.get("ai_summary")
            and "no material financial details disclosed" in a["ai_summary"].lower()
            and not _board_keep_re.search(
                f"{a.get('subject','')} {a.get('detail','')} {a.get('ai_summary','')}"
            )
        )
    ]
    if before_empty_bm_ex != len(existing):
        log(f"Dropped {before_empty_bm_ex - len(existing)} empty Board Meeting entries from cache")

    # Backfill market cap for existing cached NSE announcements still missing it
    nse_missing_mcap = [a for a in existing if a.get("exchange") == "NSE" and not a.get("market_cap") and a.get("symbol")]
    if nse_missing_mcap:
        sym_to_cached = {}
        for a in nse_missing_mcap:
            sym = a["symbol"]
            if sym not in sym_to_cached:
                sym_to_cached[sym] = []
            sym_to_cached[sym].append(a)
        sym_list = list(sym_to_cached.keys())[:40]
        log(f"Backfilling market cap for {len(sym_list)} cached NSE companies...")
        nse_client = _get_nse_client()
        if nse_client:
            backfilled = 0
            for i, sym in enumerate(sym_list):
                data = fetch_nse_mcap(nse_client, sym)
                if data:
                    for a in sym_to_cached[sym]:
                        a["market_cap"] = data["value"]
                        a["market_cap_fmt"] = data["formatted"]
                    backfilled += 1
                if (i + 1) % 5 == 0:
                    time.sleep(0.5)
            nse_client.close()
            log(f"Backfilled market cap for {backfilled}/{len(sym_list)} cached NSE companies")

    # Backfill market cap for existing cached BSE announcements still missing it
    bse_missing_mcap = [a for a in existing if a.get("exchange") == "BSE" and not a.get("market_cap") and a.get("symbol")]
    if bse_missing_mcap:
        scrip_to_cached = {}
        for a in bse_missing_mcap:
            sc = a["symbol"]
            if sc not in scrip_to_cached:
                scrip_to_cached[sc] = []
            scrip_to_cached[sc].append(a)
        scrips_list = list(scrip_to_cached.keys())[:100]
        log(f"Backfilling market cap for {len(scrips_list)} cached BSE companies...")
        bse_session = requests.Session()
        bse_session.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            "Accept": "application/json",
            "Referer": "https://www.bseindia.com/",
        })
        backfilled = 0
        for i, sc in enumerate(scrips_list):
            data = fetch_bse_mcap(bse_session, sc)
            if data:
                for a in scrip_to_cached[sc]:
                    a["market_cap"] = data["value"]
                    a["market_cap_fmt"] = data["formatted"]
                backfilled += 1
            time.sleep(0.15)
        log(f"Backfilled market cap for {backfilled}/{len(scrips_list)} cached BSE companies")

    # Merge new into existing (keep ALL history)
    # Merge: new first, then existing (avoid duplicates)
    kept_existing = existing
    existing_keys = set(a.get("_key", "") for a in kept_existing)
    merged = []
    for a in new_anns:
        if a["_key"] not in existing_keys:
            merged.append(a)
    merged.extend(kept_existing)

    # Re-dedup entire dataset (catches cached duplicates from before dedup improvements)
    before_dedup = len(merged)
    merged = dedup(merged)
    if before_dedup != len(merged):
        log(f"Removed {before_dedup - len(merged)} duplicate announcements from cache")

    # Remove cached announcements matching noise patterns (cleanup for old data)
    # Check subject, detail, AND AI summary for noise keywords
    before_noise = len(merged)
    merged = [a for a in merged if not is_noise(
        f"{a.get('subject', '')} {a.get('detail', '')} {a.get('ai_summary', '')}",
        a.get('subject', '')
    )]
    if before_noise != len(merged):
        log(f"Removed {before_noise - len(merged)} cached announcements matching noise filters")

    # Remove micro-caps (below 50 Cr) from entire dataset including cached
    MIN_MCAP_CLEANUP = 50 * 1e7
    before_cleanup = len(merged)
    merged = [a for a in merged if a.get("market_cap") is None or a.get("market_cap") >= MIN_MCAP_CLEANUP]
    if before_cleanup != len(merged):
        log(f"Removed {before_cleanup - len(merged)} cached micro-cap announcements (<50 Cr)")

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
