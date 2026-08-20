"""Stage 1b: download each FY2026 annual report and mine forward-looking text.

For every report discovered by discover.py this fetches the PDF, extracts the
text, throws away the pages that are financial statements / notices / governance
tables, and keeps only paragraphs that read as future plans, KPIs or guidance.

The PDF itself is deleted straight after extraction, so a full run over the
whole market costs a few hundred MB of disk rather than tens of GB.
"""
import hashlib
import io
import json
import os
import re
import sys
import threading
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import fitz
import requests

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patterns import (BOILERPLATE, CATEGORIES, EXCLUDE_PAGE, SUBSTANCE,
                      is_prose, numeric_density, windows)

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IN = os.path.join(ROOT, "data", "annual_reports", "fy2026_reports.jsonl")
OUT = os.path.join(ROOT, "data", "annual_reports", "fy2026_mined.jsonl")

WORKERS = int(os.environ.get("AR_WORKERS", "3"))
MAX_MB = int(os.environ.get("AR_MAX_MB", "120"))
MAX_HITS = int(os.environ.get("AR_MAX_HITS", "40"))
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Referer": "https://www.bseindia.com/",
}

_lock = threading.Lock()
_local = threading.local()


def session():
    if not hasattr(_local, "s"):
        _local.s = requests.Session()
        _local.s.headers.update(HEADERS)
    return _local.s


def fetch_pdf_bytes(url):
    r = session().get(url, timeout=180)
    r.raise_for_status()
    blob = r.content
    if len(blob) > MAX_MB * 1024 * 1024:
        raise ValueError(f"oversized ({len(blob)//1024//1024} MB)")
    if blob[:2] == b"PK":  # some NSE archives ship the report zipped
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = [n for n in z.namelist() if n.lower().endswith(".pdf")]
            if not names:
                raise ValueError("zip contains no pdf")
            blob = z.read(max(names, key=lambda n: z.getinfo(n).file_size))
    if blob[:4] != b"%PDF":
        raise ValueError("not a pdf")
    return blob


def mine(blob):
    """Return (hits, stats) for one report."""
    doc = fitz.open(stream=blob, filetype="pdf")
    hits, seen = [], set()
    kept_pages = 0
    try:
        for idx, page in enumerate(doc):
            text = page.get_text()
            if not text.strip():
                continue
            if EXCLUDE_PAGE.search(text[:800]):
                continue
            if numeric_density(text) > 0.18:
                continue
            kept_pages += 1
            for _, sent, ctx in windows(text):
                if BOILERPLATE.search(ctx):
                    continue
                cats = [n for n, pat in CATEGORIES.items() if pat.search(sent)]
                if not cats:
                    continue
                if not SUBSTANCE.search(ctx):
                    continue
                if not is_prose(ctx):
                    continue
                fp = hashlib.md5(re.sub(r"\d", "", sent.lower()).encode()).hexdigest()
                if fp in seen:  # standalone/consolidated duplication
                    continue
                seen.add(fp)
                hits.append({"page": idx + 1, "categories": cats, "text": ctx})
        stats = {"pages": len(doc), "pages_scanned": kept_pages}
    finally:
        doc.close()
    hits.sort(key=lambda h: (-len(h["categories"]), h["page"]))
    return hits[:MAX_HITS], stats


def load_done():
    done = set()
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            for line in fh:
                try:
                    done.add(json.loads(line)["key"])
                except Exception:
                    pass
    return done


def main():
    reports = []
    with open(IN, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") == "found":
                reports.append(rec)

    done = load_done()
    todo = [r for r in reports if r["key"] not in done]
    print(f"{len(reports)} FY2026 reports | {len(done)} already mined | {len(todo)} to go", flush=True)

    fh = open(OUT, "a", encoding="utf-8")
    counts = {"n": 0, "ok": 0, "hits": 0}
    started = time.time()

    def work(rec):
        out = {k: rec.get(k) for k in ("key", "name", "scrip_code", "nse_symbol", "url", "source")}
        try:
            blob = fetch_pdf_bytes(rec["url"])
            out["size_mb"] = round(len(blob) / 1e6, 2)
            hits, stats = mine(blob)
            out.update(stats)
            out["hits"] = hits
            out["status"] = "ok"
        except Exception as exc:
            out["status"] = "error"
            out["error"] = f"{type(exc).__name__}: {exc}"[:200]
            out["hits"] = []
        with _lock:
            fh.write(json.dumps(out, ensure_ascii=False) + "\n")
            counts["n"] += 1
            if out["status"] == "ok":
                counts["ok"] += 1
                counts["hits"] += len(out["hits"])
            if counts["n"] % 25 == 0:
                fh.flush()
                rate = counts["n"] / max(time.time() - started, 1)
                eta = (len(todo) - counts["n"]) / max(rate, 0.001) / 60
                print(f"  {counts['n']}/{len(todo)} | {counts['ok']} ok | "
                      f"{counts['hits']} extracts | ETA {eta:.0f} min", flush=True)

    with ThreadPoolExecutor(max_workers=WORKERS) as pool:
        list(pool.map(work, todo))
    fh.close()
    print(f"done: {counts['ok']}/{counts['n']} mined, {counts['hits']} extracts -> {OUT}")


if __name__ == "__main__":
    main()
