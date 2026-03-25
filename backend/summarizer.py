import google.generativeai as genai
import requests
import os
import time
import io

# Cache summaries to avoid re-processing
_summary_cache = {}

# Configure Gemini
_model = None


def _get_model():
    global _model
    if _model:
        return _model

    api_key = os.environ.get("GEMINI_API_KEY", "")
    if not api_key:
        raise ValueError("GEMINI_API_KEY environment variable not set")

    genai.configure(api_key=api_key)
    _model = genai.GenerativeModel("gemini-2.0-flash")
    return _model


def _download_pdf(url, timeout=15):
    """Download a PDF from a URL and return the bytes."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        ),
    }
    r = requests.get(url, headers=headers, timeout=timeout)
    r.raise_for_status()
    return r.content


def summarize_announcement(pdf_url, announcement_text="", news_id=""):
    """Summarize a BSE/NSE announcement using Gemini.

    Args:
        pdf_url: URL to the PDF attachment.
        announcement_text: The headline/subject text from the announcement.
        news_id: Unique ID for caching.

    Returns:
        dict with 'summary' and 'category' keys.
    """
    cache_key = news_id or pdf_url
    if cache_key in _summary_cache:
        return _summary_cache[cache_key]

    model = _get_model()

    prompt = (
        "You are a financial analyst summarizing BSE/NSE corporate announcements for Indian stock investors.\n\n"
        "Analyze this announcement and provide:\n"
        "1. A concise 1-2 sentence summary. Always include specific numbers where present: "
        "share counts, prices, percentages, order values, loan amounts, stake sizes, etc. "
        "Do not drop quantitative details — they are the most important part for investors.\n"
        "2. A category from this list: "
        "New Order, Business Expansion, Acquisition, Merger/Demerger, Capital Structure, "
        "Press Release, Credit Rating, Dividend, Results, Fund Raising, Insider Trading, "
        "Regulatory, Joint Venture, Delisting, Resolution, Other\n\n"
        "Return ONLY a JSON object like:\n"
        '{"summary": "...", "category": "..."}\n\n'
        "No markdown, no code blocks, just the raw JSON."
    )

    try:
        parts = [prompt]

        # Try to include the PDF
        if pdf_url:
            try:
                pdf_bytes = _download_pdf(pdf_url)
                parts.append({
                    "mime_type": "application/pdf",
                    "data": pdf_bytes,
                })
            except Exception:
                # If PDF download fails, use the text
                if announcement_text:
                    parts.append(f"\nAnnouncement text: {announcement_text}")

        elif announcement_text:
            parts.append(f"\nAnnouncement text: {announcement_text}")

        response = model.generate_content(parts)
        text = response.text.strip()

        # Parse JSON response
        import json
        # Clean up any markdown wrapping
        if text.startswith("```"):
            text = text.split("\n", 1)[1].rsplit("```", 1)[0].strip()
        if text.startswith("json"):
            text = text[4:].strip()

        result = json.loads(text)
        result.setdefault("summary", "")
        result.setdefault("category", "Other")

    except Exception as e:
        result = {
            "summary": f"Could not summarize: {str(e)[:100]}",
            "category": "Other",
        }

    _summary_cache[cache_key] = result
    return result


def summarize_batch(announcements, max_items=10):
    """Summarize a batch of announcements.

    Args:
        announcements: List of announcement dicts (BSE format).
        max_items: Max number to summarize (Gemini has rate limits).

    Returns:
        Dict mapping NEWSID -> {summary, category}.
    """
    results = {}

    for i, a in enumerate(announcements[:max_items]):
        news_id = a.get("NEWSID", "")

        # Check cache first
        if news_id in _summary_cache:
            results[news_id] = _summary_cache[news_id]
            continue

        # Build PDF URL
        attachment = a.get("ATTACHMENTNAME", "")
        pdf_url = ""
        if attachment:
            pdf_url = f"https://www.bseindia.com/xml-data/corpfiling/AttachLive/{attachment}"

        # Fallback text
        text = f"{a.get('SLONGNAME', '')} - {a.get('NEWSSUB', '')} - {a.get('HEADLINE', '')}"

        results[news_id] = summarize_announcement(pdf_url, text, news_id)

        # Small delay to respect rate limits
        if i < len(announcements) - 1:
            time.sleep(0.5)

    return results


if __name__ == "__main__":
    # Quick test
    result = summarize_announcement(
        "",
        "BEL receives orders worth Rs.1011 Crore from various customers.",
        "test-1",
    )
    print(result)
