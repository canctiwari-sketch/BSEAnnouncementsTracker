"""Rule-based announcement categorizer and summary extractor.

Instant, free, no API limits — uses keyword matching to categorize
BSE/NSE corporate announcements and extract key info from headlines.
"""

import re

# Category rules: (category_name, compiled_regex)
# Order matters — first match wins
CATEGORY_RULES = [
    # Orders & Contracts
    ("New Order", re.compile(
        r"order|contract.*award|letter of intent|LOI|work order|purchase order|"
        r"supply agreement|received.*order|bagged.*order|secured.*order|"
        r"award.*contract|empanelment", re.I)),

    # Financial Results
    ("Results", re.compile(
        r"financial result|quarterly result|annual result|half.yearly result|"
        r"un.?audited.*result|audited.*result|standalone.*result|consolidated.*result|"
        r"profit|loss.*quarter|revenue|turnover|earning", re.I)),

    # Dividend
    ("Dividend", re.compile(
        r"dividend|interim dividend|final dividend|special dividend", re.I)),

    # Acquisition / Takeover
    ("Acquisition", re.compile(
        r"acqui(?:sition|red|ring)|takeover|open offer|bought|purchase.*stake|"
        r"purchase.*share|purchase.*business|buy.*stake", re.I)),

    # Merger / Demerger / Amalgamation
    ("Merger/Demerger", re.compile(
        r"merger|demerger|amalgamation|scheme of arrangement|composite scheme", re.I)),

    # Fund Raising
    ("Fund Raising", re.compile(
        r"fund.?rais|qip|qualified institutional|rights issue|fpo|"
        r"preferential.*allot|preferential.*issue|warrant|convertible|"
        r"ncd|debenture.*issue|ipo|initial public|"
        r"private placement", re.I)),

    # Business Expansion
    ("Business Expansion", re.compile(
        r"expansion|new plant|new facility|new unit|capex|capital expenditure|"
        r"greenfield|brownfield|commissioned|commenced.*operation|"
        r"capacity.*addition|capacity.*expansion|production.*start|"
        r"new factory|new warehouse|inaugurat", re.I)),

    # Joint Venture / Partnership
    ("Joint Venture", re.compile(
        r"joint venture|jv|tie.up|partnership|collaboration|mou|"
        r"memorandum of understanding|strategic alliance|consortium", re.I)),

    # Credit Rating
    ("Credit Rating", re.compile(
        r"credit rating|rating.*assign|rating.*affirm|rating.*reaffirm|"
        r"rating.*upgrade|rating.*downgrade|crisil|icra|care rating|"
        r"india rating|brickwork|acuite", re.I)),

    # Capital Structure (Bonus/Split/Buyback)
    ("Capital Structure", re.compile(
        r"bonus|stock split|sub.?division|buyback|buy.back|"
        r"reduction.*capital|alteration.*capital|reclassification", re.I)),

    # Board Meeting
    ("Board Meeting", re.compile(
        r"board meeting|outcome of board|board.*consider|"
        r"meeting of board|resolution.*board", re.I)),

    # Press Release / Media
    ("Press Release", re.compile(
        r"press release|media release|press note|news release", re.I)),

    # Subsidiary / Incorporation
    ("Subsidiary", re.compile(
        r"subsidiary|wholly owned|incorporation.*company|"
        r"new company|step.down subsidiary", re.I)),

    # Divestment
    ("Divestment", re.compile(
        r"divestment|disinvestment|divest|disposal|"
        r"sale of.*stake|sale of.*business|sale of.*unit|"
        r"stake sale|sold.*stake", re.I)),

    # Delisting
    ("Delisting", re.compile(
        r"delisting|delist", re.I)),

    # Regulatory / SEBI / Exchange
    ("Regulatory", re.compile(
        r"sebi|stock exchange|penalty|fine.*imposed|"
        r"show cause|adjudication|settlement|"
        r"clarification.*exchange|clarification.*sebi", re.I)),

    # Allotment
    ("Allotment", re.compile(
        r"allotment.*share|allotment.*debenture|allotment.*securit|"
        r"allotment.*equity", re.I)),

    # Clarification
    ("Clarification", re.compile(
        r"clarification|response to.*query|reply to.*exchange|"
        r"price movement|media report", re.I)),
]


def categorize(announcement):
    """Categorize a single announcement based on headline/subject keywords.

    Args:
        announcement: Dict with BSE fields (NEWSSUB, HEADLINE, CATEGORYNAME)
                      or NSE fields (desc, attchmntText).

    Returns:
        str: Category name.
    """
    # Build combined text from available fields
    sub = announcement.get("NEWSSUB") or announcement.get("desc") or ""
    headline = announcement.get("HEADLINE") or announcement.get("attchmntText") or ""
    combined = f"{sub} {headline}"

    for category, pattern in CATEGORY_RULES:
        if pattern.search(combined):
            return category

    # Fallback based on BSE category field
    bse_cat = announcement.get("CATEGORYNAME") or ""
    if "Board Meeting" in bse_cat:
        return "Board Meeting"
    if "Corp. Action" in bse_cat:
        return "Capital Structure"

    return "Other"


def extract_summary(announcement):
    """Extract a concise summary from announcement fields.

    Instead of AI, we pull the most informative text from
    the headline/subject and clean it up.

    Args:
        announcement: Dict with BSE or NSE fields.

    Returns:
        str: Cleaned summary text.
    """
    # BSE fields
    sub = announcement.get("NEWSSUB") or announcement.get("desc") or ""
    headline = announcement.get("HEADLINE") or announcement.get("attchmntText") or ""
    company = announcement.get("SLONGNAME") or announcement.get("sm_name") or ""
    scrip = str(announcement.get("SCRIP_CD") or announcement.get("symbol") or "")

    # The headline is usually more informative than the subject
    text = headline if headline and headline != sub else sub

    # Clean up: remove company name and scrip code prefix (BSE format)
    # BSE often prepends "Company Ltd - 500123 - Announcement under Regulation 30 (LODR)-Category_Name"
    # We want to strip that boilerplate prefix
    prefix_pattern = re.compile(
        r"^.*?(?:Announcement under|Regulation 30|LODR\))-?\s*\w+[\w_]*\s*",
        re.I
    )
    cleaned = prefix_pattern.sub("", text).strip()

    # If cleaning removed everything, fall back to original
    if not cleaned or len(cleaned) < 10:
        cleaned = text

    # Remove company name prefix if present
    if company and cleaned.lower().startswith(company.lower()):
        cleaned = cleaned[len(company):].lstrip(" -–—:")

    # Remove scrip code prefix
    if scrip and cleaned.startswith(scrip):
        cleaned = cleaned[len(scrip):].lstrip(" -–—:")

    # Trim to reasonable length
    if len(cleaned) > 200:
        cleaned = cleaned[:197] + "..."

    return cleaned.strip() or text[:200]


def categorize_batch(announcements):
    """Categorize and summarize a batch of announcements.

    Args:
        announcements: List of announcement dicts.

    Returns:
        Dict mapping NEWSID (or index) -> {category, summary}.
    """
    results = {}
    for i, a in enumerate(announcements):
        news_id = a.get("NEWSID") or str(i)
        results[news_id] = {
            "category": categorize(a),
            "summary": extract_summary(a),
        }
    return results
