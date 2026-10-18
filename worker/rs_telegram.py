"""
Weekly Relative Strength note → Telegram.

Reads data/rel_strength.json (built every weekday evening by rel_strength.py)
and sends, for each of Mid / Small / Micro caps, the top 10 by 7-day RS and
the top 10 by 3-month RS vs Nifty 500. Large caps are deliberately left out.

Runs Saturday morning, so the data is Friday's close.

Environment (GitHub Actions secrets — NEVER hard-code these; the repo is public):
  TELEGRAM_BOT_TOKEN  – from @BotFather
  TELEGRAM_CHAT_ID    – the recipient's numeric chat id

  python worker/rs_telegram.py --dry-run   # print the messages, send nothing
"""
import os
import sys
import json
import html
import urllib.parse
import urllib.request
from datetime import date

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RS_FILE = os.path.join(ROOT, "data", "rel_strength.json")
SITE_URL = "https://canctiwari-sketch.github.io/BSEAnnouncementsTracker/"

TOP_N = 10
STALE_DAYS = 5          # warn in the note if the data is older than this
TG_LIMIT = 4000         # Telegram caps a message at 4096 chars

# Same ₹ Cr thresholds as rel_strength.cap_bucket.
BUCKETS = [
    ("mid",   "🟦 MID CAPS",   "₹5,000–20,000 Cr"),
    ("small", "🟩 SMALL CAPS", "₹500–5,000 Cr"),
    ("micro", "🟨 MICRO CAPS", "below ₹500 Cr"),
]


def log(m):
    print(m, flush=True)


def _pct(v):
    return "—" if v is None else f"{v:+.1f}%"


def _mcap(v):
    return "N/A" if v is None else f"₹{round(v):,} Cr"


def _fmt_date(s):
    try:
        return date.fromisoformat(s).strftime("%d %b %Y")
    except (TypeError, ValueError):
        return s or "?"


def _line(i, r, move_key, rs_key):
    name = html.escape(r.get("name") or r.get("symbol") or "?")
    # Same "Google → screener.in" link the website uses for company names.
    q = urllib.parse.quote(f"{r.get('name') or r.get('symbol')} screener.in")
    sme = " · SME" if r.get("sme") else ""
    return (f"{i}. <a href=\"https://www.google.com/search?q={q}\">{name}</a>{sme}\n"
            f"     {_pct(r.get(move_key))}  (RS {_pct(r.get(rs_key))}) · {_mcap(r.get('mcap_cr'))}")


def _top(rows, key):
    return sorted((r for r in rows if r.get(key) is not None), key=lambda r: -r[key])[:TOP_N]


def build_messages(data):
    rows = data.get("rows") or []
    bench = data.get("benchmark") or "Nifty 500"
    latest = data.get("latest_date")

    header = [
        "<b>📈 Weekly Relative Strength</b>",
        f"Data as of <b>{_fmt_date(latest)}</b> close",
        f"{html.escape(bench)}: 1 week {_pct(data.get('benchmark_pct'))} · "
        f"3 months {_pct(data.get('benchmark_pct_3m'))}",
        "RS = stock's move minus the index's move.",
    ]
    try:
        age = (date.today() - date.fromisoformat(latest)).days
        if age > STALE_DAYS:
            header.append(f"⚠️ Data is {age} days old — the daily RS job may have failed.")
    except (TypeError, ValueError):
        header.append("⚠️ Could not read the data date.")

    messages = []
    for key, title, band in BUCKETS:
        grp = [r for r in rows if r.get("bucket") == key]
        parts = [f"<b>{title}</b> ({band}) — {len(grp)} stocks", ""]
        parts.append("<b>Top 10 · 1 week</b>")
        top7 = _top(grp, "rs")
        parts += [_line(i, r, "pct_7d", "rs") for i, r in enumerate(top7, 1)] or ["(no data)"]
        parts += ["", "<b>Top 10 · 3 months</b>"]
        top3 = _top(grp, "rs_3m")
        parts += [_line(i, r, "pct_3m", "rs_3m") for i, r in enumerate(top3, 1)] or ["(no data)"]
        messages.append("\n".join(parts))

    messages[0] = "\n".join(header) + "\n\n" + messages[0]
    messages[-1] += f"\n\n<a href=\"{SITE_URL}\">Open the full Rel Strength tab</a>"
    return messages


def _split(msg):
    """Keep each chunk under Telegram's limit, breaking on blank lines."""
    if len(msg) <= TG_LIMIT:
        return [msg]
    out, cur = [], ""
    for block in msg.split("\n\n"):
        if cur and len(cur) + len(block) + 2 > TG_LIMIT:
            out.append(cur)
            cur = block
        else:
            cur = f"{cur}\n\n{block}" if cur else block
    if cur:
        out.append(cur)
    return out


def send(token, chat_id, text):
    body = urllib.parse.urlencode({
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": "true",
    }).encode()
    req = urllib.request.Request(f"https://api.telegram.org/bot{token}/sendMessage", data=body)
    with urllib.request.urlopen(req, timeout=30) as r:
        res = json.load(r)
    if not res.get("ok"):
        raise RuntimeError(f"Telegram refused the message: {res.get('description')}")


def main():
    dry = "--dry-run" in sys.argv
    with open(RS_FILE, encoding="utf-8") as f:
        data = json.load(f)
    chunks = [c for m in build_messages(data) for c in _split(m)]

    if dry:
        for c in chunks:
            print(c, "\n" + "─" * 60)
        log(f"dry run: {len(chunks)} message(s), not sent")
        return

    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        log("::error::TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID secrets are not set in GitHub.")
        sys.exit(1)
    for c in chunks:
        send(token, chat_id, c)
    log(f"sent {len(chunks)} message(s)")


if __name__ == "__main__":
    main()
