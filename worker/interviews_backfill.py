"""
One-time 2-month backfill of management interviews via YouTube Data API v3.
Pages through each channel's uploads playlist, stops at 60 days back, matches
titles against companies, merges into data/interviews.json.

Run:  YOUTUBE_API_KEY=xxx python worker/interviews_backfill.py
"""
import os
import sys
import time
import requests
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from interviews import (
    CHANNELS, load_companies, match_companies, load_existing, save,
)

API_KEY = os.environ.get("YOUTUBE_API_KEY", "")
DAYS_BACK = 60
PLAYLIST_URL = "https://www.googleapis.com/youtube/v3/playlistItems"


def log(m):
    print(m, flush=True)


def fetch_uploads(channel_id, cutoff_iso):
    """Page through a channel's uploads until older than cutoff. Returns list of videos."""
    uploads_playlist = "UU" + channel_id[2:]  # UCxxxx -> UUxxxx
    videos = []
    page_token = None
    pages = 0
    while True:
        params = {
            "key": API_KEY,
            "part": "snippet,contentDetails",
            "playlistId": uploads_playlist,
            "maxResults": 50,
        }
        if page_token:
            params["pageToken"] = page_token
        try:
            r = requests.get(PLAYLIST_URL, params=params, timeout=20)
            d = r.json()
        except Exception as e:
            log(f"    fetch error: {e}")
            break
        if "error" in d:
            log(f"    API error: {d['error']['message']}")
            break
        items = d.get("items", [])
        if not items:
            break
        stop = False
        for it in items:
            sn = it["snippet"]
            published = it.get("contentDetails", {}).get("videoPublishedAt") or sn.get("publishedAt", "")
            if published and published < cutoff_iso:
                stop = True
                continue
            vid = sn.get("resourceId", {}).get("videoId", "")
            if not vid:
                continue
            videos.append({
                "video_id": vid,
                "title": sn.get("title", ""),
                "published": published,
                "thumbnail": f"https://i.ytimg.com/vi/{vid}/mqdefault.jpg",
                "url": f"https://www.youtube.com/watch?v={vid}",
            })
        pages += 1
        page_token = d.get("nextPageToken")
        if stop or not page_token or pages > 70:
            break
        time.sleep(0.05)
    return videos


def main():
    if not API_KEY:
        log("ERROR: YOUTUBE_API_KEY not set")
        return
    companies = load_companies()
    log(f"Loaded {len(companies)} companies")

    existing = load_existing()
    seen_ids = set(existing.get("seen_ids", []))
    interviews = existing.get("interviews", [])
    log(f"Existing interviews: {len(interviews)}")

    cutoff = (datetime.now(timezone.utc) - timedelta(days=DAYS_BACK)).strftime("%Y-%m-%dT%H:%M:%SZ")
    log(f"Cutoff: {cutoff}")

    new_count = 0
    for ch in CHANNELS:
        log(f"Backfilling {ch['name']} ({ch['lang']})...")
        vids = fetch_uploads(ch["id"], cutoff)
        log(f"  {len(vids)} videos in last {DAYS_BACK}d")
        matched_n = 0
        for v in vids:
            if v["video_id"] in seen_ids:
                continue
            matched = match_companies(v["title"], companies)
            if not matched:
                continue
            for company in matched:
                interviews.append({
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
                matched_n += 1
            seen_ids.add(v["video_id"])
        log(f"  matched {matched_n} interviews")
        time.sleep(0.1)

    # Enforce 60-day retention
    interviews = [i for i in interviews if i.get("published", "") >= cutoff]
    interviews.sort(key=lambda x: x.get("published", ""), reverse=True)

    existing["interviews"] = interviews
    existing["seen_ids"] = list(seen_ids)
    existing["last_updated"] = datetime.utcnow().isoformat()
    save(existing)
    log(f"Done. Added {new_count} new. Total after 60d retention: {len(interviews)}")


if __name__ == "__main__":
    main()
