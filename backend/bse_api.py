import requests
import re
from datetime import datetime, timedelta


# --- Noise patterns to EXCLUDE ---
# These are routine/regulatory filings that clutter the feed
NOISE_PATTERNS = [
    # Insider trading routine (keep SAST)
    r"prohibition of insider trading",
    r"insider trading",
    r"closure of trading window",
    r"trading window",
    # Routine management changes (not CEO/MD level)
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
    # Postal ballot / AGM procedural
    r"scrutinizer.s report",
    r"scrutiniser.s report",
    r"notice of postal.ballot",
    r"voting result.*postal ballot",
    r"outcome of postal.ballot",
    r"newspaper publication",
    r"newspaper advertisement",
    r"notice of separate meeting of independent director",
    r"independent directors.?\s*meeting",
    # ESOP routine
    r"allotment of esop",
    r"allotment of esps",
    # Analyst meet intimations (just scheduling, no content)
    r"analyst.*investor.*meet",
    r"investor.*meet.*intimation",
    r"schedule of analyst",
    # Book closure / record date routine
    r"book closure",
    r"record date",
    r"cut.off date",
    # Loss of share certificates / demat
    r"loss of share certificate",
    r"special window.*transfer",
    r"special window.*demateriali",
    r"dematerialisation of physical",
    r"transfer.*physical.*securit",
    # Routine compliance
    r"compliance certificate",
    r"certificate under",
    r"reg\.?\s*74.*debenture",
    # KMP changes misc
    r"change in director.*address",
    # AGM/EGM procedural
    r"notice of.*agm",
    r"notice of.*egm",
    r"annual report",
    # Related party routine
    r"related party transaction",
    # Listing fees
    r"listing fee",
    r"annual fee",
    # Vague / no-content announcements
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
    # Listing approvals (routine)
    r"listing approval.*equity share",
    r"listing application",
    r"re.lodgement of transfer",
    # Investor presentations (not actionable news)
    r"investor presentation",
    # Share transfer / demat routine
    r"transfer of shares",
    r"transmission of shares",
    # Exchange-generated price/volume movement clarifications
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
    # Routine KMP / director changes
    r"^resignation$",
    r"^resignation of independent director$",
    r"^appointment$",
    r"^change in director",
    r"^cessation$",
    r"cessation",
    r"appointment.*company secretary",
    r"resignation.*company secretary",
    r"appointment.*independent director",
    # Shareholder meetings (procedural)
    r"^shareholders? meeting$",
    r"agm|annual general meeting",
    r"extra.?ordinary general meeting",
    # ESOP / routine allotments
    r"esop|esos|esps",
    r"allotment of securities",
    r"allotment of shares",
    # Credit rating (routine reaffirmations)
    r"esg rating",
    # Litigation / disputes (ongoing, not new events)
    r"pendency of litigation",
    r"pending litigation",
    # Routine address/name/corrigendum
    r"^address change$",
    r"^corrigendum$",
    r"^name change$",
    # Spurt in volume (exchange-generated, not company filing)
    r"spurt in volume",
    # Post offer advertisement (routine takeover compliance)
    r"post offer advertisement",
    # Trust deed / pledge routine
    r"submission of trust deed",
    r"release of pledged",
    r"pledge.*shares",
    # Comments on fine / penalty (routine)
    r"comments.*(?:fine|penalty).*stock exchange",
    # Dividend (routine, not actionable for research)
    r"dividend",
    r"interim dividend",
    r"final dividend",
    r"^dividend$",
    # Credit rating (routine, not actionable for research)
    r"credit rating",
    # Additional procedural noise
    r"^committee meeting updates$",
    r"action\(s\) taken or orders passed",
    r"^fraud/default/arrest$",
    r"change of name",
    r"intimation of record date",
]

# Compile patterns for performance
_noise_re = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)


# --- Important patterns to ALWAYS KEEP ---
IMPORTANT_PATTERNS = [
    r"order",
    r"contract",
    r"acquisition",
    r"merger",
    r"demerger",
    r"joint venture",
    r"tie.up",
    r"partnership",
    r"collaboration",
    r"expansion",
    r"new plant",
    r"new facility",
    r"commenced.*operation",
    r"commissioned",
    r"bonus",
    r"buyback",
    r"buy.back",
    r"split",
    r"rights issue",
    r"fund.?rais",
    r"ncd\s",
    r"debenture.*issue",
    r"qip",
    r"ipo",
    r"ofs|offer for sale",
    r"fpo",
    r"result",
    r"financial.result",
    r"quarterly.*result",
    r"annual.*result",
    r"upgrade",
    r"downgrade",
    r"clarification",
    r"press release",
    r"media release",
    r"divestment",
    r"disinvestment",
    r"stake.*sale",
    r"subsidiary",
    r"wholly owned",
    r"incorporation",
    r"delisting",
    r"allotment.*share",
    r"allotment.*debenture",
    r"preferential",
    r"warrant",
    r"open offer",
    r"takeover",
    r"board meeting.*consider",
    r"raising.*fund",
]

_important_re = re.compile("|".join(IMPORTANT_PATTERNS), re.IGNORECASE)


def is_noise(text, subject=""):
    """Check if text matches noise patterns. Usable for both BSE and NSE.

    Args:
        text: Combined subject + detail text for broad pattern matching.
        subject: Subject alone for exact-match patterns (^...$).
    """
    if _noise_re.search(text):
        return True
    # Also check subject alone (for ^...$ anchored patterns)
    if subject and _noise_re.search(subject):
        return True
    return False


def _is_important(announcement):
    """Determine if an announcement is important enough to show.

    Returns True if the announcement should be kept.
    Logic: noise check runs FIRST (even if important patterns match),
    then important patterns, then default keep for non-AGM categories.
    """
    sub = announcement.get("NEWSSUB", "") or ""
    headline = announcement.get("HEADLINE", "") or ""
    combined = f"{sub} {headline}"

    # Filter out noise FIRST — these are never important
    if _noise_re.search(combined):
        return False

    # Keep if it matches important patterns
    if _important_re.search(combined):
        return True

    # Filter out entire BSE categories that are routine/procedural
    category = announcement.get("CATEGORYNAME", "") or ""
    if category in ("AGM/EGM", "Credit Rating",
                     "Dividend", "ESOP/ESOS/ESPS"):
        return False

    # Keep other Company Update / Board Meeting / Corp Action by default
    return True


def fetch_announcements(from_date=None, to_date=None, page=1, category="-1",
                        filter_important=True):
    """Fetch BSE announcements for a date range.

    Args:
        from_date: Start date as YYYY-MM-DD string. Defaults to 1 day ago.
        to_date: End date as YYYY-MM-DD string. Defaults to today.
        page: Page number (1-indexed).
        category: Announcement category (-1 for all).
        filter_important: If True, filter out noise and keep only important ones.

    Returns:
        List of announcement dicts from BSE API.
    """
    if to_date is None:
        to_date = datetime.now().strftime("%Y-%m-%d")
    if from_date is None:
        from_date = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    from_dt = datetime.strptime(from_date, "%Y-%m-%d")
    to_dt = datetime.strptime(to_date, "%Y-%m-%d")
    str_from = from_dt.strftime("%Y%m%d")
    str_to = to_dt.strftime("%Y%m%d")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    }

    session = requests.Session()
    session.headers.update(headers)

    if filter_important:
        # Fetch multiple pages to have enough after filtering
        all_announcements = []
        seen_ids = set()
        for p in range(1, 6):  # Up to 5 pages (250 raw announcements)
            url = (
                f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
                f"?strCat={category}&strPrevDate={str_from}&strScrip=&strSearch=P"
                f"&strToDate={str_to}&strType=C&pageno={p}"
            )
            response = session.get(url, timeout=15)
            response.raise_for_status()
            data = response.json()
            batch = data.get("Table", [])
            if not batch:
                break
            for a in batch:
                news_id = a.get("NEWSID", "")
                if news_id not in seen_ids:
                    seen_ids.add(news_id)
                    if _is_important(a):
                        all_announcements.append(a)

        # Paginate filtered results (50 per page)
        page_size = 50
        start = (page - 1) * page_size
        end = start + page_size
        return all_announcements[start:end]
    else:
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/AnnGetData/w"
            f"?strCat={category}&strPrevDate={str_from}&strScrip=&strSearch=P"
            f"&strToDate={str_to}&strType=C&pageno={page}"
        )
        response = session.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        return data.get("Table", [])


if __name__ == "__main__":
    print("=== ALL announcements ===")
    all_results = fetch_announcements(filter_important=False)
    print(f"Raw: {len(all_results)} announcements")

    print("\n=== FILTERED (important only) ===")
    filtered = fetch_announcements(filter_important=True)
    print(f"Important: {len(filtered)} announcements")
    for a in filtered[:10]:
        print(f"  [{a.get('CATEGORYNAME','')}] {a.get('SLONGNAME','')}: {a.get('HEADLINE','')[:80]}")
