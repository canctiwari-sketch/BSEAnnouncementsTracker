"""
Fetch management interviews from financial-news YouTube channels (RSS),
match titles against listed-company names, save to data/interviews.json.

Free — uses public YouTube RSS feeds, no API key.
"""
import os
import json
import re
import time
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(ROOT, "data")
SCRIPS_FILE = os.path.join(DATA_DIR, "scrips.json")
OUT_FILE = os.path.join(DATA_DIR, "interviews.json")

# YouTube channel IDs of major Indian business / market news channels.
# (Mix of Hindi + English.)
CHANNELS = [
    {"name": "CNBC-TV18", "lang": "English", "id": "UCH4cz9KqVCDhTI98yMxAjpw"},
    {"name": "ET NOW", "lang": "English", "id": "UC-f7r46JhYv78q5pGrO6ivA"},
    {"name": "NDTV Profit", "lang": "English", "id": "UCu5dwfFmDPCQqHo_FFI4Vmw"},
    {"name": "Bloomberg Quint", "lang": "English", "id": "UCb2O5Uo4a26CdTE7_2QA-jA"},
    {"name": "Moneycontrol", "lang": "English", "id": "UCYU3WdYUmaThbpdr7zCqMrA"},
    {"name": "Mint", "lang": "English", "id": "UCpio0bX7TJDPHEoVfQfFNOg"},
    {"name": "Business Today", "lang": "English", "id": "UCwqusr8YDwM-3mEYTDeJHzw"},
    {"name": "CNBC Awaaz", "lang": "Hindi", "id": "UCywAQwoFOyL3M-Nco6FaLug"},
    {"name": "ET NOW Swadesh", "lang": "Hindi", "id": "UCxNyDIb8MIQUIIPaszKkJEg"},
    {"name": "Zee Business", "lang": "Hindi", "id": "UC8NcXMG3A3f2aFQyGTpSNww"},
    {"name": "BQ Prime", "lang": "English", "id": "UCfWQ3wfaJ3jsoa2W-Cwy_AA"},
    {"name": "Inc42", "lang": "English", "id": "UCgyJ4Pe-0HqxQRq2hkr_C6A"},
]

# Words to ignore when extracting company names from titles.
STOPWORDS = {
    "ltd", "limited", "ltd.", "private", "pvt", "industries", "india", "corp",
    "corporation", "co", "company", "the", "and", "of", "for", "to", "&",
    "group", "holdings", "enterprises", "services", "international", "global",
    "ceo", "cfo", "md", "chairman", "managing", "director", "founder",
    "interview", "speaks", "talks", "explains", "share", "shares",
}

ATOM_NS = {"a": "http://www.w3.org/2005/Atom",
           "media": "http://search.yahoo.com/mrss/",
           "yt": "http://www.youtube.com/xml/schemas/2015"}


def log(msg):
    print(msg, flush=True)


def normalize_company_name(name):
    """Strip suffixes, lowercase, remove punctuation."""
    n = name.lower()
    n = re.sub(r"[\-\$&,.]+", " ", n)
    n = re.sub(r"\s+", " ", n).strip()
    for suffix in [" ltd", " limited", " pvt", " private", " industries", " corp",
                   " corporation", " co", " company", " group", " holdings",
                   " enterprises", " international"]:
        if n.endswith(suffix):
            n = n[:-len(suffix)].strip()
    return n


def load_companies():
    with open(SCRIPS_FILE, "r", encoding="utf-8") as f:
        scrips = json.load(f)
    companies = []
    seen = set()
    for s in scrips:
        name = s.get("ScripName") or s.get("IssuerName") or ""
        sym = s.get("NSESymbol") or ""
        scrip_code = s.get("ScripCode") or ""
        norm = normalize_company_name(name)
        if not norm or len(norm) < 4:
            continue
        # Skip duplicates (BSE + NSE listings of same name)
        key = (norm, sym)
        if key in seen:
            continue
        seen.add(key)
        companies.append({
            "name": name.strip(),
            "norm": norm,
            "norm_compact": norm.replace(" ", ""),
            "symbol": sym,
            "scrip_code": scrip_code,
            "exchange": s.get("Exchange", "BSE"),
        })
    return companies


def fetch_channel_feed(channel_id):
    url = f"https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"
    try:
        r = requests.get(url, timeout=15, headers={
            "User-Agent": "Mozilla/5.0 (compatible; BSE-Tracker/1.0)"
        })
        if r.status_code != 200:
            return []
        return parse_atom_feed(r.text)
    except Exception as e:
        log(f"  Channel feed error: {e}")
        return []


def parse_atom_feed(xml_text):
    """Parse YouTube atom feed → list of {video_id, title, published, thumbnail}."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []
    out = []
    for entry in root.findall("a:entry", ATOM_NS):
        vid_id_el = entry.find("yt:videoId", ATOM_NS)
        title_el = entry.find("a:title", ATOM_NS)
        pub_el = entry.find("a:published", ATOM_NS)
        if vid_id_el is None or title_el is None:
            continue
        vid_id = vid_id_el.text
        title = title_el.text or ""
        published = pub_el.text if pub_el is not None else ""
        thumb = f"https://i.ytimg.com/vi/{vid_id}/mqdefault.jpg"
        out.append({
            "video_id": vid_id,
            "title": title,
            "published": published,
            "thumbnail": thumb,
            "url": f"https://www.youtube.com/watch?v={vid_id}",
        })
    return out


# Title MUST contain at least one of these to qualify as a management interview.
# Pure news headlines don't usually contain these.
INTERVIEW_MARKERS = re.compile(
    r"\b(interview|exclusive|speaks?\s+(?:to|with|on)|in\s+conversation|"
    r"q&a|q\s*and\s*a|chat\s+with|panel\s+discussion|earnings\s+call|"
    r"results?\s+(?:talk|chat|reaction)|guidance|outlook|"
    r"ceo\s+speaks?|md\s+speaks?|cfo\s+speaks?|chairman\s+speaks?|"
    r"managing\s+director|founder\s+(?:speaks|interview)|"
    r"talks?\s+(?:to|with|about|on)\s+(?!the\s|a\s|an\s)|"
    r"explains|reveals|on\s+(?:q[1-4]|results|guidance|outlook|strategy|expansion))\b",
    re.IGNORECASE,
)


def match_companies(title, companies):
    """Return list of company dicts that match this video title.

    Tightened strategy to reduce false positives:
    - Title must contain interview/exec-speaks markers
    - For multi-word companies: require all distinctive words present
    - For single-word companies: require min 6 chars (avoid 'shah'/'atul' matches)
    - Prefer most-specific match (longest token count)
    """
    if not INTERVIEW_MARKERS.search(title):
        return []

    t = re.sub(r"[\-,.\$&|]+", " ", title.lower())
    t = re.sub(r"\s+", " ", t).strip()
    title_tokens = set(t.split())

    matches = []
    for c in companies:
        nm = c["norm"]
        sym = (c["symbol"] or "").lower()
        if len(nm) < 4:
            continue
        name_tokens = nm.split()
        # Strip generic stopwords from match consideration
        distinctive = [w for w in name_tokens if w not in STOPWORDS]
        if not distinctive:
            continue

        if len(distinctive) == 1:
            # Single-word company: must be standalone token AND at least 6 chars
            w = distinctive[0]
            if len(w) >= 6 and w in title_tokens:
                matches.append((c, len(w) * 2, "single"))
        else:
            # Multi-word: require all distinctive tokens present in title
            if all(w in title_tokens for w in distinctive):
                score = sum(len(w) for w in distinctive) * len(distinctive)
                matches.append((c, score, "multi"))
            # OR full normalized name appears verbatim (handles compound names)
            elif nm in t and len(nm) >= 8:
                matches.append((c, len(nm), "full"))

        # Symbol match: ticker must be a standalone token >= 4 chars
        if sym and len(sym) >= 4 and sym in title_tokens:
            matches.append((c, len(sym) * 2, "symbol"))

    if not matches:
        return []
    matches.sort(key=lambda x: -x[1])
    return [matches[0][0]]


def load_existing():
    if not os.path.exists(OUT_FILE):
        return {"interviews": [], "last_updated": None, "seen_ids": []}
    try:
        with open(OUT_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"interviews": [], "last_updated": None, "seen_ids": []}


def save(data):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def main():
    log(f"Interviews worker starting — {datetime.utcnow().isoformat()}")
    companies = load_companies()
    log(f"Loaded {len(companies)} companies from scrips.json")

    existing = load_existing()
    seen_ids = set(existing.get("seen_ids", []))
    all_interviews = existing.get("interviews", [])
    log(f"Existing interviews: {len(all_interviews)}, seen IDs: {len(seen_ids)}")

    new_count = 0
    for ch in CHANNELS:
        log(f"Fetching {ch['name']} ({ch['lang']})...")
        videos = fetch_channel_feed(ch["id"])
        log(f"  {len(videos)} recent videos")
        for v in videos:
            if v["video_id"] in seen_ids:
                continue
            matched = match_companies(v["title"], companies)
            if not matched:
                continue
            for company in matched:
                all_interviews.append({
                    "video_id": v["video_id"],
                    "title": v["title"],
                    "url": v["url"],
                    "thumbnail": v["thumbnail"],
                    "published": v["published"],
                    "channel": ch["name"],
                    "language": ch["lang"],
                    "company": company["name"],
                    "symbol": company["symbol"],
                    "scrip_code": company["scrip_code"],
                    "exchange": company["exchange"],
                })
                new_count += 1
            seen_ids.add(v["video_id"])
        time.sleep(1)  # be polite to YouTube

    # Sort newest first
    all_interviews.sort(key=lambda x: x.get("published", ""), reverse=True)

    existing["interviews"] = all_interviews
    existing["seen_ids"] = list(seen_ids)
    existing["last_updated"] = datetime.utcnow().isoformat()
    save(existing)
    log(f"Done. Added {new_count} new interviews. Total: {len(all_interviews)}")


if __name__ == "__main__":
    main()
