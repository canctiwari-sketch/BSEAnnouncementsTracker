"""Build a BSE scrip code <-> NSE symbol cross-reference.

Needed because the NSE cross-check in discover.py has to find a company's NSE
identity starting from a BSE scrip code, and neither obvious key works alone:

  * ISIN is not reliable across the two exchanges. BSE carries Karur Vysya Bank
    as INE036D01028 while NSE has INE036D01010 for the same company.
  * data/scrips.json only has both identifiers for 1,312 of its 6,288 rows, so
    it covers barely a quarter of the screened universe.

So this joins the two exchanges' own master lists on ISIN first, then falls
back to a normalised company name for the rows ISIN misses.
"""
import csv
import io
import json
import os
import re

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
OUT = os.path.join(ROOT, "data", "annual_reports", "bse_nse_xref.json")

BSE_LIST = ("https://api.bseindia.com/BseIndiaAPI/api/ListofScripData/w"
            "?Group=&Scripcode=&industry=&segment=Equity&status=Active")
NSE_LIST = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/122.0 Safari/537.36",
    "Accept": "*/*",
}
BSE_HEADERS = dict(HEADERS, Referer="https://www.bseindia.com/",
                   Origin="https://www.bseindia.com")

# Suffixes and noise words that differ between the two registries.
_NOISE = re.compile(
    r"\b(limited|ltd|the|company|co|corporation|corp|industries|india|"
    r"pvt|private|and|of)\b", re.I)


def norm(name):
    """Normalise a company name enough to match across two registries."""
    if not name:
        return ""
    s = re.sub(r"[^A-Za-z0-9 ]", " ", name)
    s = _NOISE.sub(" ", s)
    return re.sub(r"\s+", "", s).lower()


def fetch_bse():
    r = requests.get(BSE_LIST, headers=BSE_HEADERS, timeout=90)
    r.raise_for_status()
    return [{
        "scrip_code": str(x.get("SCRIP_CD")),
        "name": (x.get("Scrip_Name") or "").strip(),
        "isin": (x.get("ISIN_NUMBER") or "").strip(),
    } for x in r.json()]


def fetch_nse():
    r = requests.get(NSE_LIST, headers=HEADERS, timeout=60)
    r.raise_for_status()
    out = []
    for row in csv.DictReader(io.StringIO(r.text)):
        clean = {k.strip(): (v or "").strip() for k, v in row.items() if k}
        out.append({
            "symbol": clean.get("SYMBOL", ""),
            "name": clean.get("NAME OF COMPANY", ""),
            "isin": clean.get("ISIN NUMBER", ""),
        })
    return out


def build():
    bse, nse = fetch_bse(), fetch_nse()
    by_isin = {n["isin"]: n["symbol"] for n in nse if n["isin"]}
    by_name = {}
    for n in nse:
        key = norm(n["name"])
        if key:
            by_name.setdefault(key, n["symbol"])

    xref, hits = {}, {"isin": 0, "name": 0}
    for b in bse:
        sym = by_isin.get(b["isin"])
        if sym:
            hits["isin"] += 1
        else:
            sym = by_name.get(norm(b["name"]))
            if sym:
                hits["name"] += 1
        if sym:
            xref[b["scrip_code"]] = sym

    payload = {
        "bse_scrips": len(bse),
        "nse_symbols": len(nse),
        "matched": len(xref),
        "matched_by_isin": hits["isin"],
        "matched_by_name": hits["name"],
        "xref": xref,
    }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return payload


def load():
    """scrip_code -> NSE symbol. Returns {} if the xref has not been built."""
    if not os.path.exists(OUT):
        return {}
    with open(OUT, encoding="utf-8") as fh:
        return json.load(fh).get("xref", {})


if __name__ == "__main__":
    p = build()
    print("BSE scrips {} | NSE symbols {} | matched {} "
          "({} by ISIN, {} by name) -> {}".format(
              p["bse_scrips"], p["nse_symbols"], p["matched"],
              p["matched_by_isin"], p["matched_by_name"], OUT))
