"""
Company Lookup worker — fetches 3 years of BSE announcements for a specific company.
Triggered by the company-lookup workflow via workflow_dispatch.

Inputs (env vars):
  COMPANY_NAME  — display name, e.g. "Ramky Infrastructure Limited"
  SCRIP_CODE    — BSE 6-digit code, e.g. "532952"
"""

import json
import os
import re
import sys
import time
import requests
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
LOOKUP_DIR = os.path.join(DATA_DIR, "lookup")

# ─── Gemini summarisation (self-contained; mirrors fetch.py) ──────────────────
GEMINI_KEYS = [k for k in (os.environ.get("GEMINI_API_KEY", ""),
                           os.environ.get("GEMINI_API_KEY_2", "")) if k]
GEMINI_MODEL_CHAIN = [
    "gemini-2.5-flash-lite", "gemini-2.5-flash",
    "gemini-2.0-flash", "gemini-2.0-flash-lite",
]


def extract_pdf_text(url, max_chars=3000):
    """Download a PDF and pull the first ~max_chars of text."""
    if not url:
        return ""
    try:
        r = requests.get(url, timeout=20, headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"})
        if r.status_code != 200:
            return ""
        import io
        try:
            import PyPDF2
            reader = PyPDF2.PdfReader(io.BytesIO(r.content))
        except ImportError:
            import pypdf
            reader = pypdf.PdfReader(io.BytesIO(r.content))
        text = ""
        for page in reader.pages[:5]:
            text += page.extract_text() or ""
            if len(text) >= max_chars:
                break
        return re.sub(r"\s+", " ", text).strip()[:max_chars]
    except Exception as e:
        print(f"    PDF extract error: {e}")
        return ""


def summarize_batch(batch, company_name):
    """Summarize a batch of announcements in one Gemini call (with PDF text).
    Returns list of summary strings (or None)."""
    if not GEMINI_KEYS or not batch:
        return [None] * len(batch)
    parts = []
    for i, a in enumerate(batch):
        entry = (f"[{i+1}]\nCompany: {company_name}\nCategory: {a.get('category','')}\n"
                 f"Subject: {a.get('subject','')}\nDetails: {a.get('detail','')}")
        if a.get("_pdf"):
            entry += f"\nPDF Content: {a['_pdf']}"
        parts.append(entry)
    prompt = f"""For each corporate announcement below, write a SUMMARY of 3-5 detailed sentences for an investor.
Rules:
- Extract exact numbers: rupee amounts, share counts, percentages, capacities, order values, dates, names.
- State clearly what happened and what it means. No vague filler.
- If the PDF content is missing or empty, summarise from the subject/details available.
Format each response EXACTLY as:
[N] <summary text>

{chr(10).join(parts)}"""
    payload = {"contents": [{"parts": [{"text": prompt}]}],
               "generationConfig": {"maxOutputTokens": 600 * len(batch), "temperature": 0.3}}
    for model in GEMINI_MODEL_CHAIN:
        for ki, key in enumerate(GEMINI_KEYS):
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={key}"
            try:
                r = requests.post(url, json=payload, timeout=90)
                if r.status_code == 429:
                    continue
                r.raise_for_status()
                txt = r.json()["candidates"][0]["content"]["parts"][0]["text"]
                return _parse_summaries(txt, len(batch))
            except Exception as e:
                print(f"    Gemini {model} key#{ki+1} error: {e}")
                continue
    return [None] * len(batch)


def _parse_summaries(text, n):
    out = [None] * n
    chunks = re.split(r"\[(\d+)\]", text)
    for i in range(1, len(chunks) - 1, 2):
        try:
            idx = int(chunks[i]) - 1
            body = chunks[i + 1].strip()
            if 0 <= idx < n and body:
                out[idx] = body
        except (ValueError, IndexError):
            continue
    return out


def summarize_announcements(announcements, company_name, batch_size=5):
    """Attach an ai_summary to each announcement (downloads PDFs + Gemini)."""
    if not GEMINI_KEYS:
        print("  No GEMINI key — skipping summaries")
        return
    print(f"  Summarising {len(announcements)} announcements...")
    for a in announcements:
        a["_pdf"] = extract_pdf_text(a.get("attachment", ""))
    done = 0
    for start in range(0, len(announcements), batch_size):
        batch = announcements[start:start + batch_size]
        summaries = summarize_batch(batch, company_name)
        for a, s in zip(batch, summaries):
            a["ai_summary"] = s
            if s:
                done += 1
        time.sleep(2)
    for a in announcements:
        a.pop("_pdf", None)
    print(f"  Summarised {done}/{len(announcements)}")

# ─── Noise: same core patterns as fetch.py + presentation/transcript/annual report ───
NOISE_PATTERNS = [
    r"prohibition of insider trading", r"insider trading", r"trading window",
    r"closure of trading window", r"change in registered office",
    r"registered office address", r"compliance officer",
    r"scrutinizer.s report", r"scrutiniser.s report",
    r"notice of postal.ballot", r"voting result.*postal ballot",
    r"outcome of postal.ballot", r"newspaper publication",
    r"newspaper advertisement", r"book closure", r"record date",
    r"cut.off date", r"loss of share certificate",
    r"dematerialisation of physical", r"transfer.*physical.*securit",
    r"compliance certificate", r"certificate under",
    r"notice of.*agm", r"notice of.*egm", r"related party transaction",
    r"listing fee", r"annual fee",
    r"as per the attachment", r"as per attachment", r"please refer to attachment",
    r"please find enclosed\s*$", r"please find the enclosed", r"as attached",
    r"movement in price", r"movement in volume", r"sought clarification",
    r"clarification.*price", r"clarification.*volume", r"spurt in volume",
    r"insolvency resolution", r"corporate insolvency resolution process",
    r"resignation", r"appointment of",
    r"appointment.*director", r"appointment.*auditor",
    r"appointment.*ceo", r"appointment.*cfo",
    r"appointment.*company secretary", r"appointment.*key managerial",
    r"re.?appointment of",
    r"change in director", r"change.*key managerial",
    r"cessation",
    r"evaluation.*board", r"evaluation.*director",
    r"board composition", r"composition of.*board", r"composition of.*committee",
    r"reconstitution.*committee", r"constitution of.*committee",
    r"board meeting.*to be held", r"board meeting.*scheduled",
    r"intimation of board meeting", r"prior intimation.*board meeting",
    r"notice of board meeting",
    r"income tax", r"tax demand", r"tax order", r"tax notice",
    r"goods and services tax", r"gst", r"cgst", r"sgst", r"igst",
    r"advance tax", r"tax assessment",
    r"^credit rating", r"credit rating",
    r"dividend", r"interim dividend", r"final dividend",
    r"allotment of esop", r"esop|esos|esps",
    r"agm|annual general meeting", r"extra.?ordinary general meeting",
    r"call money notice",
    r"code of conduct", r"code of fair disclosure",
    r"minutes of meeting", r"minutes of.*annual general",
    r"familiarisation programme", r"familiarization programme",
    r"secretarial compliance report", r"corporate governance report",
    r"reconciliation of share capital", r"statement of investor complaints",
    r"regulation 39.*3", r"iepf", r"unclaimed dividend",
    r"large corporate", r"regulation 31\(4\)", r"no encumbrance",
    r"not made any encumbrance",
    r"duplicate share certificate", r"issue of duplicate",
    r"pledge.*shares", r"release of pledged",
    r"updation of email",
    # Extra exclusions for lookup
    r"investor presentation", r"analyst presentation",
    r"investor day", r"analyst day",
    r"concall transcript", r"conference call transcript",
    r"earnings call transcript", r"transcript of.*conference",
    r"transcript of.*call", r"transcript of.*meet",
    r"^annual report$", r"integrated.*annual report",
    r"annual report.*\d{4}", r"annual return",
    r"investor meet.*intimation", r"schedule of analyst",
    r"analyst.*investor.*meet",
    # Share-transfer / relodgement routine filings (big offenders in lookup)
    r"relodgement", r"re.?lodgement", r"lodgement of transfer",
    r"transfer request", r"report on relodgement", r"relodgment",
    r"transmission of shares", r"transposition", r"sub.?division of share",
    r"issue of duplicate", r"split of share certificate",
    # Routine borrowing / clarification / housekeeping
    r"financial assistance from bank", r"sanction of.*financial assistance",
    r"sanction of.*loan", r"availment of.*loan|facility",
    r"clarification regarding", r"clarification on", r"clarification sought",
    r"clarification to", r"clarification w\.?r\.?t",
    r"necessary arrangements", r"production loss", r"stop production",
    r"temporary (?:shut|suspension)", r"plant shutdown.*maintenance",
    r"newspaper", r"public announcement", r"postal ballot",
    r"trading plan", r"disclosure under regulation 7\(2\)",
    r"regulation 30.*report on", r"submission of.*report on relodg",
    # Plain board-meeting outcome with no substantive event keyword
    r"^outcome of board meeting$", r"board meeting outcome for outcome",
]

_noise_re = re.compile("|".join(NOISE_PATTERNS), re.IGNORECASE)


def is_noise(text, subject=""):
    if _noise_re.search(text):
        return True
    if subject and _noise_re.search(subject):
        return True
    return False


def categorize(subject, headline=""):
    """Simple category assignment for lookup results."""
    text = f"{subject} {headline}".lower()
    rules = [
        ("Open Offer",        r"open offer|takeover|sast.*acqui"),
        ("Buyback",           r"buy.?back"),
        ("Warrants",          r"warrant"),
        ("Delisting",         r"delist"),
        ("Results",           r"financial result|quarterly result|annual result|q[1-4].*result|result.*q[1-4]|half.?year.*result"),
        ("Acquisition",       r"acqui[rs]|merger|amalgam|demerger"),
        ("New Order",         r"order|contract|letter of intent|loi|work order"),
        ("Fund Raising",      r"ncd|debenture|rights issue|qip|ipo|preferential|fund.?rais"),
        ("Business Expansion",r"capex|expansion|new plant|new facility|commission|greenfield|brownfield"),
        ("Joint Venture",     r"joint venture|jv\b|tie.up|partnership|collaboration|mou\b"),
        ("Subsidiary",        r"subsidiary|wholly owned"),
        ("Divestment",        r"divest|stake sale|sale of.*stake|sell.*stake"),
        ("Capital Structure", r"bonus|split|rights|capital"),
        ("Press Release",     r"press release"),
        ("Regulatory",        r"sebi|nclt|nclat|cci|enforcement"),
        ("Litigation",        r"court|tribunal|arbitrat|litigation|judgment|verdict"),
        ("SAST/Insider",      r"sast|substantial acqui|regulation 29"),
        ("Board Meeting",     r"board meeting|board of director"),
    ]
    for cat, pat in rules:
        if re.search(pat, text, re.IGNORECASE):
            return cat
    return "Other"


def fetch_company_announcements(scrip_code, from_date, to_date):
    """Fetch all BSE announcements for a scrip code over the date range (all pages)."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/134.0.0.0 Safari/537.36",
        "Accept": "application/json, text/plain, */*",
        "Referer": "https://www.bseindia.com/",
        "Origin": "https://www.bseindia.com",
    })

    str_from = from_date.strftime("%Y%m%d")
    str_to = to_date.strftime("%Y%m%d")

    results = []
    seen_ids = set()

    for page in range(1, 50):  # up to ~2500 raw announcements
        # AnnSubCategoryGetData works for company-specific scrip lookups (AnnGetData returns 0)
        url = (
            f"https://api.bseindia.com/BseIndiaAPI/api/AnnSubCategoryGetData/w"
            f"?strCat=-1&strPrevDate={str_from}&strScrip={scrip_code}"
            f"&strSearch=P&strToDate={str_to}&strType=C&pageno={page}"
        )
        try:
            r = session.get(url, timeout=15)
            r.raise_for_status()
            data = r.json()
            items = data.get("Table") or []
            if not items:
                break
            new_count = 0
            for item in items:
                nid = item.get("NEWSID")
                if nid and nid not in seen_ids:
                    seen_ids.add(nid)
                    sub = item.get("NEWSSUB") or ""
                    headline = item.get("HEADLINE") or ""
                    combined = f"{sub} {headline}"
                    if is_noise(combined, sub):
                        continue
                    att = item.get("ATTACHMENTNAME") or ""
                    results.append({
                        "date": item.get("NEWS_DT") or "",
                        "subject": sub,
                        "detail": headline,
                        "category": categorize(sub, headline),
                        "attachment": (
                            f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}"
                            if att else ""
                        ),
                    })
                    new_count += 1
            print(f"  Page {page}: {new_count} kept, {len(results)} total so far")
            time.sleep(0.2)
        except Exception as e:
            print(f"  Page {page} error: {e}")
            break

    return results


def cleanup_old_lookups():
    """Delete lookup files older than 48 hours."""
    if not os.path.exists(LOOKUP_DIR):
        return
    cutoff = datetime.utcnow() - timedelta(hours=48)
    for fname in os.listdir(LOOKUP_DIR):
        if not fname.endswith(".json"):
            continue
        fpath = os.path.join(LOOKUP_DIR, fname)
        mtime = datetime.utcfromtimestamp(os.path.getmtime(fpath))
        if mtime < cutoff:
            os.remove(fpath)
            print(f"Cleaned up old lookup: {fname}")


def main():
    company_name = os.environ.get("COMPANY_NAME", "").strip()
    scrip_code = os.environ.get("SCRIP_CODE", "").strip()

    if not company_name or not scrip_code:
        print("ERROR: COMPANY_NAME and SCRIP_CODE env vars are required")
        sys.exit(1)

    print(f"Company: {company_name}  |  BSE Scrip: {scrip_code}")

    cleanup_old_lookups()

    # IST today
    ist_now = datetime.utcnow() + timedelta(hours=5, minutes=30)
    to_date = ist_now
    from_date = ist_now - timedelta(days=365 * 3)

    print(f"Date range: {from_date.strftime('%d-%b-%Y')} → {to_date.strftime('%d-%b-%Y')}")

    announcements = fetch_company_announcements(scrip_code, from_date, to_date)
    announcements.sort(key=lambda a: a.get("date", ""), reverse=True)

    print(f"Total important announcements: {len(announcements)}")

    # Generate rich, PDF-based AI summaries for the kept announcements
    summarize_announcements(announcements, company_name)

    os.makedirs(LOOKUP_DIR, exist_ok=True)
    output_file = os.path.join(LOOKUP_DIR, f"{scrip_code}.json")

    result = {
        "company": company_name,
        "scrip_code": scrip_code,
        "from_date": from_date.strftime("%Y-%m-%d"),
        "to_date": to_date.strftime("%Y-%m-%d"),
        "fetched_at": datetime.utcnow().isoformat(),
        "total": len(announcements),
        "announcements": announcements,
    }

    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    print(f"Saved → {output_file}")


if __name__ == "__main__":
    main()
