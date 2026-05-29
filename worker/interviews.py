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

# Verified YouTube channel IDs of major Indian business / market news channels.
CHANNELS = [
    {"name": "CNBC-TV18", "lang": "English", "id": "UCmRbHAgG2k2vDUvb3xsEunQ"},
    {"name": "ET NOW", "lang": "English", "id": "UCI_mwTKUhicNzFrhm33MzBQ"},
    {"name": "NDTV Profit", "lang": "English", "id": "UCZFMm1mMw0F81Z37aaEzTUA"},
    {"name": "Moneycontrol", "lang": "English", "id": "UCnhUiJ_-DRTP6w51LCQgJRQ"},
    {"name": "Mint", "lang": "English", "id": "UCUI9vm69ZbAqRK3q3vKLWCQ"},
    {"name": "Business Today", "lang": "English", "id": "UCaPHWiExfUWaKsUtENLCv5w"},
    {"name": "Bloomberg Quint", "lang": "English", "id": "UC3uJIdRFTGgLWrUziaHbzrg"},
    {"name": "Inc42", "lang": "English", "id": "UCQpk4isGpLvfE_riTJc9jHA"},
    {"name": "CNBC Awaaz", "lang": "Hindi", "id": "UCQIycDaLsBpMKjOCeaKUYVg"},
    {"name": "ET NOW Swadesh", "lang": "Hindi", "id": "UCD3CdwT8lTCe5ZGHbUBxmWA"},
    {"name": "Zee Business", "lang": "Hindi", "id": "UCkXopQ3ubd-rnXnStZqCl2w"},
]

# Words to ignore when extracting distinctive company tokens from titles.
# Adds generic English words that produce false matches when they appear in
# news headlines (Enterprise AI, Education Minister, Global Crisis, etc.).
STOPWORDS = {
    "ltd", "limited", "ltd.", "private", "pvt", "industries", "india", "corp",
    "corporation", "co", "company", "the", "and", "of", "for", "to", "&",
    "group", "holdings", "enterprises", "services", "international", "global",
    "ceo", "cfo", "md", "chairman", "managing", "director", "founder",
    "interview", "speaks", "talks", "explains", "share", "shares",
    # Generic words that appear in many news headlines
    "enterprise", "education", "national", "premier", "elite", "general",
    "universal", "consolidated", "united", "modern", "central", "eastern",
    "western", "northern", "southern", "asian", "indian", "trade", "trades",
    "technologies", "technology", "tech", "solutions", "systems", "products",
    "power", "energy", "finance", "financial", "capital", "investments",
    "infrastructure", "infra", "developers", "ventures",
    "limited.", "co.", "inc", "plc", "the.",
}

# Common finance / English words that are SOMETIMES a company's only distinctive
# token. Block them from single-word matching so market-commentary videos don't
# get tagged to companies like "Global Capital Markets", "Wealth First", etc.
FINANCE_BLOCKLIST = {
    "markets", "market", "wealth", "growth", "insurance", "mutual", "equity",
    "equities", "sensex", "nifty", "banking", "broking", "trading", "trade",
    "money", "rupee", "economy", "economic", "stocks", "stock", "shares",
    "futures", "options", "commodity", "commodities", "bullion", "gold",
    "silver", "crude", "metals", "auto", "pharma", "realty", "infra",
    "digital", "online", "retail", "consumer", "telecom", "media", "power",
    "energy", "solar", "green", "smart", "value", "prime", "select", "first",
    "advanced", "superior", "supreme", "royal", "crown", "star", "sun", "moon",
    "future", "futures", "tomorrow", "today", "vision", "mission", "dream",
    "success", "victory", "winner", "leader", "pioneer", "express", "rapid",
    "quick", "speed", "fast", "global", "world", "earth", "nation", "country",
    "people", "public", "private", "popular", "famous", "great", "grand",
    "mega", "macro", "micro", "tata", "birla", "adani", "ambani",
    "health", "medical", "care", "life", "living", "home", "house",
    "general", "special", "regular", "standard", "classic", "modern",
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


# Title MUST contain at least one of these to qualify as management content.
# Tuned to catch real management interviews/commentary while excluding generic news.
INTERVIEW_MARKERS = re.compile(
    r"\b("
    r"interview|exclusive|in\s+conversation|q\s*&\s*a|q\s*and\s*a|"
    r"speaks?|talks?|comments?|discusses?|explains?|reveals?|answers?|"
    r"shares?\s+(?:views|outlook|plans|strategy|insights)|"
    r"guidance|outlook|plans|strategy|expansion\s+plans|"
    r"reaction|management\s+(?:meet|view|commentary)|"
    r"ceo|managing\s+director|md\b|cfo|cmd\b|chairman|chairwoman|founder|"
    r"earnings\s+call|concall|results?\s+(?:talk|chat|reaction|preview)|"
    r"q[1-4]\s*fy|fy2[0-9]|outlook\s+for|on\s+(?:results|guidance|strategy|expansion)"
    r")\b",
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
            # Single-word company: standalone token, >=6 chars, and NOT a
            # common finance/English word (those collide with market videos).
            w = distinctive[0]
            if len(w) >= 6 and w not in FINANCE_BLOCKLIST and w in title_tokens:
                matches.append((c, len(w) * 2, "single"))
        else:
            # Multi-word: require the full normalized name as a contiguous
            # substring (catches "Ashok Leyland", "Bajaj Auto", "Inox Wind"),
            # not just scattered tokens.
            if nm in t and len(nm) >= 8:
                matches.append((c, len(nm) * 3, "full"))
        # NOTE: symbol/ticker matching removed — tickers like MARCO, ATUL,
        # WEALTH collide with unrelated proper nouns / English words.

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


API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
RETENTION_DAYS = 60
DAILY_LOOKBACK_DAYS = 3   # how far back the daily run scans per channel


def fetch_channel_api(channel_id, cutoff_iso):
    """Page a channel's uploads playlist via YouTube Data API until older than
    cutoff. Far better coverage than RSS (which caps at 15 videos)."""
    uploads = "UU" + channel_id[2:]
    videos = []
    token = None
    pages = 0
    while True:
        params = {"key": API_KEY, "part": "snippet,contentDetails",
                  "playlistId": uploads, "maxResults": 50}
        if token:
            params["pageToken"] = token
        try:
            r = requests.get("https://www.googleapis.com/youtube/v3/playlistItems",
                             params=params, timeout=20)
            d = r.json()
        except Exception as e:
            log(f"    API error: {e}")
            break
        if "error" in d:
            log(f"    API error: {d['error'].get('message','?')}")
            break
        items = d.get("items", [])
        if not items:
            break
        stop = False
        for it in items:
            sn = it["snippet"]
            pub = it.get("contentDetails", {}).get("videoPublishedAt") or sn.get("publishedAt", "")
            if pub and pub < cutoff_iso:
                stop = True
                continue
            vid = sn.get("resourceId", {}).get("videoId", "")
            if not vid:
                continue
            videos.append({
                "video_id": vid, "title": sn.get("title", ""), "published": pub,
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={vid}",
            })
        pages += 1
        token = d.get("nextPageToken")
        if stop or not token or pages > 10:
            break
        time.sleep(0.1)
    return videos


def main():
    log(f"Interviews worker starting — {datetime.utcnow().isoformat()}")
    companies = load_companies()
    log(f"Loaded {len(companies)} companies from scrips.json")

    existing = load_existing()
    seen_ids = set(existing.get("seen_ids", []))
    all_interviews = existing.get("interviews", [])
    log(f"Existing interviews: {len(all_interviews)}, seen IDs: {len(seen_ids)}")

    from datetime import timezone
    api_cutoff = (datetime.now(timezone.utc) - timedelta(days=DAILY_LOOKBACK_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    use_api = bool(API_KEY)
    log(f"Mode: {'YouTube Data API' if use_api else 'RSS fallback'} (lookback {DAILY_LOOKBACK_DAYS}d)")

    new_count = 0
    for ch in CHANNELS:
        log(f"Fetching {ch['name']} ({ch['lang']})...")
        if use_api:
            videos = fetch_channel_api(ch["id"], api_cutoff)
        else:
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
        time.sleep(0.3)

    # Enforce 60-day retention
    from datetime import timezone
    retain_cutoff = (datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    before = len(all_interviews)
    all_interviews = [i for i in all_interviews if i.get("published", "") >= retain_cutoff]
    dropped = before - len(all_interviews)

    # Sort newest first
    all_interviews.sort(key=lambda x: x.get("published", ""), reverse=True)

    # Trim seen_ids to those still present (keep set from growing forever)
    live_ids = {i["video_id"] for i in all_interviews}
    seen_ids = {vid for vid in seen_ids if vid in live_ids} | live_ids

    existing["interviews"] = all_interviews
    existing["seen_ids"] = list(seen_ids)
    existing["last_updated"] = datetime.utcnow().isoformat()
    save(existing)
    log(f"Done. Added {new_count} new. Dropped {dropped} >60d. Total: {len(all_interviews)}")


if __name__ == "__main__":
    main()
