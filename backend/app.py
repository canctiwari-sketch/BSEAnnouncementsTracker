from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from bse_api import fetch_announcements, is_noise
from nse_api import fetch_market_caps
from categorizer import categorize, extract_summary
import os
import re
import time
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor

STATIC_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend", "public")

app = Flask(__name__)
CORS(app)


@app.route("/api/announcements")
def api_announcements():
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")
    page = request.args.get("page", 1, type=int)

    # Use BSE for announcements (reliable)
    try:
        announcements = fetch_announcements(from_date, to_date, page)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch announcements: {str(e)}"}), 500

    # Try NSE for market cap (best-effort, uses ISIN/name mapping)
    # BSE announcements have SCRIP_CD and SLONGNAME
    # We need to map BSE names to NSE symbols — for now use scrip codes with .BO
    # Actually, let's try to get market cap from NSE if possible
    scrip_codes = list(set(
        str(a.get("SCRIP_CD", "")).strip()
        for a in announcements
        if a.get("SCRIP_CD")
    ))

    mcap_data = {}
    if scrip_codes:
        try:
            mcap_data = fetch_market_caps(scrip_codes, source="bse")
        except Exception:
            pass

    # Enrich announcements with market cap, price, and category data
    for a in announcements:
        code = str(a.get("SCRIP_CD", "")).strip()
        mcap = mcap_data.get(code, {})
        a["market_cap"] = mcap.get("value")
        a["market_cap_fmt"] = mcap.get("formatted")
        a["ltp"] = mcap.get("price")
        a["pe"] = mcap.get("pe")
        a["eps"] = mcap.get("eps")
        a["category"] = categorize(a)
        a["summary"] = extract_summary(a)

    return jsonify({
        "announcements": announcements,
        "page": page,
        "count": len(announcements),
    })


@app.route("/api/nse-announcements")
def api_nse_announcements():
    """NSE announcements endpoint (alternate source)."""
    from nse_api import fetch_announcements as nse_fetch
    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    try:
        announcements = nse_fetch(from_date, to_date)
    except Exception as e:
        return jsonify({"error": f"Failed to fetch NSE announcements: {str(e)}"}), 500

    symbols = list(set(a.get("symbol", "").strip() for a in announcements if a.get("symbol")))

    mcap_data = {}
    if symbols:
        try:
            mcap_data = fetch_market_caps(symbols, source="nse")
        except Exception:
            pass

    for a in announcements:
        sym = a.get("symbol", "").strip()
        mcap = mcap_data.get(sym, {})
        a["market_cap"] = mcap.get("value")
        a["market_cap_fmt"] = mcap.get("formatted")
        a["ltp"] = mcap.get("price")
        a["category"] = categorize(a)
        a["summary"] = extract_summary(a)

    return jsonify({
        "announcements": announcements,
        "count": len(announcements),
    })


def _normalize_name(name):
    """Normalize company name for dedup matching."""
    if not name:
        return ""
    name = name.lower().strip()
    # Remove common suffixes
    for suffix in ["limited", "ltd.", "ltd", "pvt.", "pvt", "private", "inc.", "inc",
                   "corporation", "corp.", "corp", "industries", "ind."]:
        name = name.replace(suffix, "")
    # Remove punctuation and extra spaces
    name = re.sub(r"[^a-z0-9\s]", "", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


def _extract_bse_symbol(nsurl):
    """Extract short symbol from BSE NSURL field (often matches NSE symbol)."""
    if not nsurl:
        return ""
    # URL format: .../stock-share-price/company-name/SYMBOL/scripcode/
    parts = nsurl.rstrip("/").split("/")
    if len(parts) >= 2:
        return parts[-2].upper()
    return ""


def _normalize_to_common(ann, source):
    """Normalize BSE or NSE announcement to a common format."""
    if source == "bse":
        return {
            "company": ann.get("SLONGNAME") or "Unknown",
            "symbol": str(ann.get("SCRIP_CD") or ""),
            "exchange": "BSE",
            "subject": ann.get("NEWSSUB") or "",
            "detail": ann.get("HEADLINE") or "",
            "date": ann.get("NEWS_DT") or "",
            "attachment": (
                f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{ann['ATTACHMENTNAME']}"
                if ann.get("ATTACHMENTNAME") else ""
            ),
            "market_cap": ann.get("market_cap"),
            "market_cap_fmt": ann.get("market_cap_fmt"),
            "ltp": ann.get("ltp"),
            "pe": ann.get("pe"),
            "category": ann.get("category") or "",
            "summary": ann.get("summary") or "",
            "_norm_name": _normalize_name(ann.get("SLONGNAME")),
            "_bse_symbol": _extract_bse_symbol(ann.get("NSURL")),
        }
    else:
        date_str = ann.get("an_dt") or ""
        return {
            "company": ann.get("sm_name") or "Unknown",
            "symbol": ann.get("symbol") or "",
            "exchange": "NSE",
            "subject": ann.get("desc") or "",
            "detail": ann.get("attchmntText") or "",
            "date": date_str,
            "attachment": ann.get("attchmntFile") or "",
            "market_cap": ann.get("market_cap"),
            "market_cap_fmt": ann.get("market_cap_fmt"),
            "ltp": ann.get("ltp"),
            "pe": ann.get("pe") if source == "bse" else None,
            "category": ann.get("category") or "",
            "summary": ann.get("summary") or "",
            "_norm_name": _normalize_name(ann.get("sm_name")),
        }


def _dedup_announcements(all_anns):
    """Remove duplicate announcements across BSE and NSE.

    Two-pass dedup:
    1. Exact: normalized company name + first 60 chars of subject
    2. Fuzzy: same company + same category + within 30 min (cross-exchange only)
    Prefers the one with more data (market cap, attachment, longer subject).
    """
    seen = {}  # key -> announcement
    results = []

    def _score(a):
        """Higher score = better entry to keep."""
        s = 0
        if a.get("market_cap"):
            s += 10
        if a.get("attachment"):
            s += 5
        s += len(a.get("subject", ""))  # Prefer longer/more descriptive subject
        return s

    for a in all_anns:
        norm_name = a["_norm_name"]
        subject = a["subject"].lower()[:60]
        key = f"{norm_name}::{subject}"

        if key in seen:
            existing = seen[key]
            if _score(a) > _score(existing):
                seen[key] = a
                for i, r in enumerate(results):
                    if r is existing:
                        results[i] = a
                        break
        else:
            seen[key] = a
            results.append(a)

    # Pass 2: Fuzzy dedup — same company + same category + within 60 min (cross-exchange)
    def _parse_dt(d):
        for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y"):
            try:
                return datetime.strptime(d, fmt)
            except (ValueError, TypeError):
                continue
        return None

    final = []
    seen_fuzzy = {}  # (norm_name, category) -> (datetime, index_in_final)
    for a in results:
        norm_name = a["_norm_name"]
        cat = a.get("category", "")
        dt = _parse_dt(a.get("date", ""))

        fuzzy_key = (norm_name, cat)
        if fuzzy_key in seen_fuzzy and dt:
            prev_dt, prev_idx = seen_fuzzy[fuzzy_key]
            # Same company + category within 60 min = likely same announcement
            if prev_dt and abs((dt - prev_dt).total_seconds()) < 3600:
                # Keep the one with better score
                existing = final[prev_idx]
                if existing and _score(a) > _score(existing):
                    final[prev_idx] = a
                    seen_fuzzy[fuzzy_key] = (dt, prev_idx)
                continue

        seen_fuzzy[fuzzy_key] = (dt, len(final))
        final.append(a)

    # Remove internal field
    for a in final:
        a.pop("_norm_name", None)
        a.pop("_bse_symbol", None)

    return final


@app.route("/api/all-announcements")
def api_all_announcements():
    """Combined BSE + NSE announcements with deduplication."""
    from nse_api import fetch_announcements as nse_fetch

    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    bse_anns = []
    nse_anns = []
    errors = []

    # Fetch from both exchanges in parallel
    def fetch_bse():
        try:
            anns = fetch_announcements(from_date, to_date, page=1)
            # Enrich with BSE price data
            scrip_codes = list(set(
                str(a.get("SCRIP_CD", "")).strip()
                for a in anns if a.get("SCRIP_CD")
            ))
            mcap_data = {}
            if scrip_codes:
                try:
                    mcap_data = fetch_market_caps(scrip_codes, source="bse")
                except Exception:
                    pass
            for a in anns:
                code = str(a.get("SCRIP_CD", "")).strip()
                mcap = mcap_data.get(code, {})
                a["market_cap"] = mcap.get("value")
                a["market_cap_fmt"] = mcap.get("formatted")
                a["ltp"] = mcap.get("price")
                a["pe"] = mcap.get("pe")
                a["category"] = categorize(a)
                a["summary"] = extract_summary(a)
            return anns
        except Exception as e:
            errors.append(f"BSE: {e}")
            return []

    def fetch_nse():
        try:
            raw_anns = nse_fetch(from_date, to_date)
            # Filter NSE announcements through noise filter
            anns = []
            for a in raw_anns:
                subject = a.get("desc") or ""
                detail = a.get("attchmntText") or ""
                combined = f"{subject} {detail}"
                if not is_noise(combined, subject):
                    anns.append(a)
            symbols = list(set(a.get("symbol", "").strip() for a in anns if a.get("symbol")))
            mcap_data = {}
            if symbols:
                try:
                    mcap_data = fetch_market_caps(symbols, source="nse")
                except Exception:
                    pass
            for a in anns:
                sym = a.get("symbol", "").strip()
                mcap = mcap_data.get(sym, {})
                a["market_cap"] = mcap.get("value")
                a["market_cap_fmt"] = mcap.get("formatted")
                a["ltp"] = mcap.get("price")
                a["category"] = categorize(a)
                a["summary"] = extract_summary(a)
            return anns
        except Exception as e:
            errors.append(f"NSE: {e}")
            return []

    with ThreadPoolExecutor(max_workers=2) as executor:
        bse_future = executor.submit(fetch_bse)
        nse_future = executor.submit(fetch_nse)
        bse_anns = bse_future.result()
        nse_anns = nse_future.result()

    # Normalize to common format
    all_normalized = []
    for a in bse_anns:
        all_normalized.append(_normalize_to_common(a, "bse"))
    nse_normalized = []
    for a in nse_anns:
        nse_normalized.append(_normalize_to_common(a, "nse"))

    # Build market cap lookup from NSE data (NSE has actual market cap, BSE doesn't)
    nse_mcap_by_name = {}
    for a in nse_normalized:
        if a.get("market_cap"):
            nse_mcap_by_name[a["_norm_name"]] = {
                "market_cap": a["market_cap"],
                "market_cap_fmt": a["market_cap_fmt"],
            }

    # Fill in missing market cap for BSE entries using NSE data
    # Step 1: Match by normalized company name (from NSE announcements already fetched)
    for a in all_normalized:
        if not a.get("market_cap") and a["_norm_name"] in nse_mcap_by_name:
            a["market_cap"] = nse_mcap_by_name[a["_norm_name"]]["market_cap"]
            a["market_cap_fmt"] = nse_mcap_by_name[a["_norm_name"]]["market_cap_fmt"]

    # Step 2: For BSE entries STILL missing market cap, try fetching from NSE
    # using the BSE short symbol (extracted from NSURL) as NSE symbol
    from nse_api import _fetch_mcap_nse, _mcap_cache, MCAP_CACHE_TTL
    still_missing = []
    for a in all_normalized:
        if not a.get("market_cap") and a.get("_bse_symbol"):
            sym = a["_bse_symbol"]
            cache_key = f"nse:{sym}"
            now = time.time()
            if cache_key in _mcap_cache and now - _mcap_cache[cache_key]["timestamp"] < MCAP_CACHE_TTL:
                cached = _mcap_cache[cache_key]
                if cached.get("value"):
                    a["market_cap"] = cached["value"]
                    a["market_cap_fmt"] = cached["formatted"]
            else:
                still_missing.append(a)

    # Fetch from NSE in small batches (limit to avoid rate limiting)
    for i, a in enumerate(still_missing[:15]):
        sym = a["_bse_symbol"]
        data = _fetch_mcap_nse(sym)
        if data and data.get("value"):
            data["timestamp"] = time.time()
            _mcap_cache[f"nse:{sym}"] = data
            a["market_cap"] = data["value"]
            a["market_cap_fmt"] = data["formatted"]
        else:
            # Cache the miss so we don't retry next time
            _mcap_cache[f"nse:{sym}"] = {
                "value": None, "formatted": None, "price": None,
                "timestamp": time.time() - MCAP_CACHE_TTL + 300
            }
        if (i + 1) % 3 == 0 and i < len(still_missing[:15]) - 1:
            time.sleep(0.2)

    # Filter out noise from NSE announcements (BSE already filtered in bse_api)
    nse_filtered = []
    for a in nse_normalized:
        text = f"{a.get('subject', '')} {a.get('detail', '')}"
        if not is_noise(text):
            nse_filtered.append(a)

    all_normalized.extend(nse_filtered)

    # Sort by date (newest first)
    def parse_date(d):
        for fmt in ("%d-%b-%Y %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S", "%d-%b-%Y"):
            try:
                return datetime.strptime(d, fmt)
            except (ValueError, TypeError):
                continue
        return datetime.min

    all_normalized.sort(key=lambda a: parse_date(a["date"]), reverse=True)

    # Deduplicate
    deduped = _dedup_announcements(all_normalized)

    # Filter out micro-cap / shell companies (below 50 Cr market cap)
    # Keep entries with no market cap (N/A) — they might be unlisted on NSE
    MIN_MCAP = 50 * 1e7  # 50 Crores in absolute value
    filtered = []
    for a in deduped:
        mcap = a.get("market_cap")
        if mcap is None:
            filtered.append(a)  # N/A — keep (can't determine)
        elif mcap >= MIN_MCAP:
            filtered.append(a)  # Above threshold — keep
        # else: below 50 Cr — skip

    return jsonify({
        "announcements": filtered,
        "count": len(filtered),
        "bse_count": len(bse_anns),
        "nse_count": len(nse_anns),
        "errors": errors if errors else None,
    })


@app.route("/api/filtered-out")
def api_filtered_out():
    """Return announcements that were filtered out as noise — for review."""
    from nse_api import fetch_announcements as nse_fetch
    from bse_api import fetch_announcements as bse_fetch_raw

    from_date = request.args.get("from_date")
    to_date = request.args.get("to_date")

    kept = set()
    filtered_out = []

    # --- BSE: fetch unfiltered, compare with filtered ---
    try:
        bse_all = bse_fetch_raw(from_date, to_date, page=1, filter_important=False)
        bse_kept = bse_fetch_raw(from_date, to_date, page=1, filter_important=True)
        kept_ids = set(a.get("NEWSID") for a in bse_kept)
        for a in bse_all:
            if a.get("NEWSID") not in kept_ids:
                att = a.get("ATTACHMENTNAME", "")
                filtered_out.append({
                    "company": a.get("SLONGNAME") or "Unknown",
                    "symbol": str(a.get("SCRIP_CD") or ""),
                    "exchange": "BSE",
                    "subject": a.get("NEWSSUB") or "",
                    "date": a.get("NEWS_DT") or "",
                    "attachment": f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{att}" if att else "",
                    "reason": "BSE noise filter",
                })
    except Exception:
        pass

    # --- NSE: fetch all, filter, diff ---
    try:
        nse_all = nse_fetch(from_date, to_date)
        for a in nse_all:
            subject = a.get("desc") or ""
            detail = a.get("attchmntText") or ""
            combined = f"{subject} {detail}"
            if is_noise(combined, subject):
                filtered_out.append({
                    "company": a.get("sm_name") or "Unknown",
                    "symbol": a.get("symbol") or "",
                    "exchange": "NSE",
                    "subject": subject,
                    "date": a.get("an_dt") or "",
                    "attachment": a.get("attchmntFile") or "",
                    "reason": "NSE noise filter",
                })
    except Exception:
        pass

    # Sort by date
    filtered_out.sort(key=lambda a: a.get("date", ""), reverse=True)

    return jsonify({
        "filtered_out": filtered_out,
        "count": len(filtered_out),
    })


@app.route("/")
def index():
    return send_from_directory(STATIC_DIR, "index.html")


@app.route("/<path:filename>")
def static_files(filename):
    return send_from_directory(STATIC_DIR, filename)


if __name__ == "__main__":
    app.run(debug=True, port=5000)
