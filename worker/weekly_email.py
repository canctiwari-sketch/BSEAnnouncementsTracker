"""
Weekly newspaper-style HTML email summary of BSE/NSE announcements.
Collects announcements from the past week (Sunday–Saturday) and emails them
to the configured recipient every Sunday afternoon.

Required environment variables:
  EMAIL_SENDER       – Gmail address to send from (default: canctiwari@gmail.com)
  EMAIL_APP_PASSWORD – Gmail App Password for the sender account
  EMAIL_RECIPIENT    – Recipient address (default: canctiwari@gmail.com)
"""

import json
import os
import smtplib
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

# ─── Paths ────────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CACHE_FILE = os.path.join(ROOT, "data", "announcements.json")

# ─── Config ───────────────────────────────────────────────────────────────────
EMAIL_SENDER    = os.environ.get("EMAIL_SENDER", "canctiwari@gmail.com")
EMAIL_PASSWORD  = os.environ.get("EMAIL_APP_PASSWORD", "")
EMAIL_RECIPIENT = os.environ.get("EMAIL_RECIPIENT", "canctiwari@gmail.com")

# Display order for category sections (most important first)
CATEGORY_ORDER = [
    "Open Offer",
    "Delisting",
    "Buyback",
    "Warrants",
    "Acquisition",
    "Merger/Demerger",
    "New Order",
    "Business Expansion",
    "Fund Raising",
    "Joint Venture",
    "Results",
    "Capital Structure",
    "Divestment",
    "Regulatory",
    "Press Release",
    "Subsidiary",
    "Board Meeting",
    "Credit Rating",
    "Allotment",
    "Clarification",
    "Other",
]

# Categories that get a ★ badge
STARRED_CATEGORIES = {"Open Offer", "Warrants", "Buyback", "Delisting", "Business Expansion"}

# ─── Date helpers ─────────────────────────────────────────────────────────────

def parse_date(date_str: str) -> datetime | None:
    """Parse announcement date string like '23-Mar-2026 16:11:55'."""
    for fmt in ("%d-%b-%Y %H:%M:%S", "%d-%b-%Y"):
        try:
            return datetime.strptime(date_str.strip(), fmt)
        except ValueError:
            continue
    return None


def get_week_range() -> tuple[datetime, datetime]:
    """
    Return (week_start, week_end) for the just-completed Sunday→Saturday week.
    When run on Sunday, this covers last Sunday 00:00 → last Saturday 23:59:59.
    """
    today = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0)
    # Go back to most recent Saturday (yesterday if today is Sunday)
    days_since_saturday = (today.weekday() + 2) % 7  # Mon=0 → 2, Sun=0 → 1
    week_end = today - timedelta(days=days_since_saturday)
    week_end = week_end.replace(hour=23, minute=59, second=59)
    week_start = (week_end - timedelta(days=6)).replace(hour=0, minute=0, second=0)
    return week_start, week_end


# ─── Data loading & filtering ─────────────────────────────────────────────────

def load_weekly_announcements() -> tuple[list[dict], datetime, datetime]:
    """Load and return announcements from the current week, plus date range."""
    with open(CACHE_FILE, encoding="utf-8") as f:
        raw = json.load(f)

    # Handle both {"announcements": [...]} and flat [...]
    if isinstance(raw, dict):
        all_announcements = raw.get("announcements", [])
    else:
        all_announcements = raw

    week_start, week_end = get_week_range()

    weekly = []
    for ann in all_announcements:
        dt = parse_date(ann.get("date", ""))
        if dt and week_start <= dt <= week_end:
            weekly.append(ann)

    # Sort by date descending (newest first within each category)
    weekly.sort(key=lambda a: parse_date(a.get("date", "")) or datetime.min, reverse=True)
    return weekly, week_start, week_end


def group_by_category(announcements: list[dict]) -> dict[str, list[dict]]:
    """Group announcements by category, preserving CATEGORY_ORDER priority."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for ann in announcements:
        cat = ann.get("category", "Other") or "Other"
        groups[cat].append(ann)

    # Build ordered dict
    ordered: dict[str, list[dict]] = {}
    for cat in CATEGORY_ORDER:
        if cat in groups:
            ordered[cat] = groups[cat]
    # Append any unexpected categories at the end
    for cat, items in groups.items():
        if cat not in ordered:
            ordered[cat] = items

    return ordered


# ─── HTML generation ──────────────────────────────────────────────────────────

def fmt_date(dt: datetime) -> str:
    return dt.strftime("%-d %B %Y") if sys.platform != "win32" else dt.strftime("%#d %B %Y")


def announcement_card(ann: dict) -> str:
    company     = ann.get("company", "Unknown Company")
    ai_summary  = ann.get("ai_summary", "").strip()
    subject     = ann.get("subject", "").strip()
    exchange    = ann.get("exchange", "")
    market_cap  = ann.get("market_cap_fmt", "")
    date_str    = ann.get("date", "")
    is_starred  = ann.get("starred", False)
    category    = ann.get("category", "Other")

    # Fall back to subject if no AI summary
    body_text = ai_summary if ai_summary else subject

    # Exchange badge color
    exch_color = "#1565c0" if exchange == "BSE" else "#2e7d32"
    badge_bg   = "#e3f2fd" if exchange == "BSE" else "#e8f5e9"

    star_badge = (
        '<span style="display:inline-block;background:#fff8e1;color:#f57f17;'
        'font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;'
        'border:1px solid #ffe082;margin-left:6px;vertical-align:middle;">★ PRIORITY</span>'
        if is_starred else ""
    )

    meta_parts = []
    if exchange:
        meta_parts.append(
            f'<span style="display:inline-block;background:{badge_bg};color:{exch_color};'
            f'font-size:10px;font-weight:700;padding:2px 6px;border-radius:3px;'
            f'border:1px solid {exch_color}33;margin-right:4px;">{exchange}</span>'
        )
    if market_cap:
        meta_parts.append(
            f'<span style="color:#78909c;font-size:11px;">Mkt Cap: {market_cap}</span>'
        )
    if date_str:
        meta_parts.append(
            f'<span style="color:#90a4ae;font-size:11px;">{date_str[:11]}</span>'
        )

    meta_html = (
        f'<div style="margin-bottom:6px;">{"&nbsp;&nbsp;".join(meta_parts)}</div>'
        if meta_parts else ""
    )

    return f"""
    <div style="border-left:3px solid #b0bec5;margin-bottom:16px;padding:10px 14px;
                background:#fafafa;border-radius:0 4px 4px 0;">
      {meta_html}
      <div style="font-size:14px;font-weight:700;color:#1a237e;line-height:1.3;margin-bottom:5px;">
        {company}{star_badge}
      </div>
      <div style="font-size:13px;color:#37474f;line-height:1.6;">{body_text}</div>
    </div>"""


def category_section(category: str, announcements: list[dict]) -> str:
    is_priority = category in STARRED_CATEGORIES
    header_color = "#b71c1c" if is_priority else "#1a237e"
    border_color = "#ef9a9a" if is_priority else "#9fa8da"
    count = len(announcements)

    cards = "\n".join(announcement_card(a) for a in announcements)

    return f"""
  <div style="margin-bottom:32px;">
    <div style="border-bottom:2px solid {border_color};padding-bottom:6px;margin-bottom:14px;
                display:flex;align-items:baseline;gap:10px;">
      <h2 style="margin:0;font-size:18px;font-family:Georgia,serif;color:{header_color};
                 font-weight:700;letter-spacing:0.5px;">{category.upper()}</h2>
      <span style="font-size:12px;color:#90a4ae;font-style:italic;">{count} announcement{"s" if count != 1 else ""}</span>
    </div>
    {cards}
  </div>"""


def build_html(announcements: list[dict], week_start: datetime, week_end: datetime) -> str:
    groups = group_by_category(announcements)
    total  = len(announcements)

    sections_html = "\n".join(
        category_section(cat, items) for cat, items in groups.items()
    )

    date_range = f"{fmt_date(week_start)} – {fmt_date(week_end)}"
    issue_date = datetime.now().strftime("%A, %#d %B %Y") if sys.platform == "win32" \
        else datetime.now().strftime("%A, %-d %B %Y")

    if not announcements:
        body_content = """
        <div style="text-align:center;padding:60px 20px;color:#90a4ae;">
          <div style="font-size:48px;margin-bottom:16px;">📋</div>
          <div style="font-size:16px;">No announcements this week.</div>
        </div>"""
    else:
        body_content = f"""
        <div style="margin-bottom:20px;">
          <p style="margin:0;font-size:13px;color:#78909c;font-style:italic;text-align:center;">
            Covering {total} corporate announcement{"s" if total != 1 else ""} &nbsp;|&nbsp;
            {", ".join(f"{cat} ({len(items)})" for cat, items in list(groups.items())[:5])}
            {"&nbsp;and more…" if len(groups) > 5 else ""}
          </p>
        </div>
        <hr style="border:none;border-top:1px solid #e0e0e0;margin:0 0 28px;">
        {sections_html}"""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width,initial-scale=1.0">
  <title>BSE/NSE Weekly — {date_range}</title>
</head>
<body style="margin:0;padding:0;background:#f5f5f5;font-family:Arial,Helvetica,sans-serif;">

  <div style="max-width:680px;margin:24px auto;background:#ffffff;
              box-shadow:0 2px 8px rgba(0,0,0,0.12);border-radius:4px;overflow:hidden;">

    <!-- MASTHEAD -->
    <div style="background:#0d1b2a;padding:28px 32px 20px;text-align:center;">
      <div style="font-size:11px;letter-spacing:3px;color:#90a4ae;
                  text-transform:uppercase;margin-bottom:6px;">The</div>
      <div style="font-size:32px;font-family:Georgia,serif;color:#ffffff;
                  font-weight:700;letter-spacing:1px;line-height:1.1;">
        Market Dispatch
      </div>
      <div style="font-size:13px;color:#64b5f6;margin-top:4px;letter-spacing:1px;">
        BSE &amp; NSE Corporate Announcements
      </div>
      <div style="margin-top:16px;border-top:1px solid #263850;padding-top:14px;
                  display:flex;justify-content:space-between;align-items:center;">
        <span style="font-size:11px;color:#78909c;">Vol. 1 &nbsp;•&nbsp; Weekly Edition</span>
        <span style="font-size:11px;color:#b0bec5;font-weight:600;">{date_range}</span>
        <span style="font-size:11px;color:#78909c;">Issued {issue_date}</span>
      </div>
    </div>

    <!-- CONTENT -->
    <div style="padding:28px 32px;">
      {body_content}
    </div>

    <!-- FOOTER -->
    <div style="background:#f8f9fa;border-top:1px solid #e0e0e0;
                padding:16px 32px;text-align:center;">
      <p style="margin:0 0 4px;font-size:11px;color:#90a4ae;">
        <strong>The Market Dispatch</strong> &nbsp;·&nbsp; Automated Weekly Summary
      </p>
      <p style="margin:0;font-size:11px;color:#bdbdbd;">
        Data sourced from BSE India &amp; NSE India official feeds.
        AI summaries generated via Google Gemini.
      </p>
    </div>

  </div>
</body>
</html>"""


# ─── Email sending ────────────────────────────────────────────────────────────

def send_email(html_body: str, week_start: datetime, week_end: datetime) -> None:
    if not EMAIL_PASSWORD:
        raise RuntimeError(
            "EMAIL_APP_PASSWORD environment variable is not set. "
            "Create a Gmail App Password and set it as a GitHub Actions secret."
        )

    date_range = f"{fmt_date(week_start)} – {fmt_date(week_end)}"
    subject    = f"The Market Dispatch | Weekly Summary | {date_range}"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = f"The Market Dispatch <{EMAIL_SENDER}>"
    msg["To"]      = EMAIL_RECIPIENT

    # Plain-text fallback
    plain = (
        f"The Market Dispatch – Weekly BSE/NSE Summary\n"
        f"{date_range}\n\n"
        "Please view this email in an HTML-capable client to see the full newspaper layout."
    )
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    print(f"Sending email to {EMAIL_RECIPIENT} via {EMAIL_SENDER} …")
    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(EMAIL_SENDER, EMAIL_PASSWORD)
        server.sendmail(EMAIL_SENDER, EMAIL_RECIPIENT, msg.as_string())
    print("Email sent successfully.")


# ─── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    print("=== Weekly Email Summary ===")
    announcements, week_start, week_end = load_weekly_announcements()
    print(
        f"Week: {week_start.date()} → {week_end.date()} | "
        f"Announcements found: {len(announcements)}"
    )

    html = build_html(announcements, week_start, week_end)

    # Write HTML preview for debugging / CI artifact
    preview_path = os.path.join(ROOT, "data", "weekly_email_preview.html")
    with open(preview_path, "w", encoding="utf-8") as f:
        f.write(html)
    print(f"HTML preview written to {preview_path}")

    send_email(html, week_start, week_end)


if __name__ == "__main__":
    main()
