"""Stage 2: render the mined extracts into a single self-contained HTML report.

No external assets, no network calls -- one file you can open, search and mail
around. Every extract links back to the exact page of the source PDF.
"""
import html
import json
import os
import re
import sys
from collections import Counter
from urllib.parse import quote_plus
from datetime import datetime, date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from patterns import BOILERPLATE, dehyphenate

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
IN = os.path.join(ROOT, "data", "annual_reports", "fy2026_mined.jsonl")
SCREEN = os.path.join(ROOT, "data", "annual_reports", "screen.jsonl")
REPORTS = os.path.join(ROOT, "data", "annual_reports", "fy2026_reports.jsonl")
NEW_DAYS = int(os.environ.get("AR_NEW_DAYS", "7"))
OUT = os.path.join(ROOT, "data", "annual_reports", "fy2026_guidance.html")
# Compact feed for the Annual Reports tab in docs/. Trimmed to display
# fields only -- page counts, file sizes and error strings are dropped, so
# the tab downloads a fraction of what the mined JSONL weighs.
FEED = os.path.join(ROOT, "data", "annual_reports.json")

LABELS = {
    "future_plans": ("Future plans", "Capex, expansion, new capacity, new products, M&amp;A"),
    "kpis": ("KPIs", "Capacity, order book, margins, volumes, market share"),
    "guidance": ("Guidance", "Explicit forward statements with a number or a timeframe"),
}
ORDER = ["guidance", "future_plans", "kpis"]

# Market cap bands, in Rs crore. Small caps are the point of this tool -- the
# large names are widely covered even when they hold no calls -- so the default
# sort is smallest first.
BANDS = [
    ("u300", "Under 300cr", 0, 300),
    ("300-1k", "300cr - 1,000cr", 300, 1000),
    ("1k-5k", "1,000cr - 5,000cr", 1000, 5000),
    ("o5k", "Over 5,000cr", 5000, float("inf")),
]

HILITE = re.compile(
    r"(\d[\d,]*\.?\d*\s*(?:%|per\s?cent|crore|lakh|million|billion|bn|mn|GWh|MWh|kWh|MW|GW|MTPA|TPA|TCD)"
    r"|FY\s?\d{2,4}|Q[1-4]\s?FY?\s?\d{2,4}|20[2-4]\d)", re.I)


def mark(text):
    """Escape, then bold the figures and periods so numbers pop when skimming.

    Repairs column-break hyphenation on the way through, so extracts mined
    before that fix existed still render as "data-driven" rather than
    "data- driven".
    """
    return HILITE.sub(r"<b>\1</b>", html.escape(dehyphenate(text)))


def load_first_seen():
    """key -> date the report was first spotted, so new arrivals can be flagged."""
    out = {}
    if not os.path.exists(REPORTS):
        return out
    with open(REPORTS, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") == "found":
                out[rec["key"]] = {"first_seen": rec.get("first_seen", ""),
                                   "filed_on": rec.get("filed_on", "")}
    return out


def load_screen():
    """scrip_code -> {market_cap_cr, concalls}, if screen.py has been run."""
    out = {}
    if not os.path.exists(SCREEN):
        return out
    with open(SCREEN, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") == "ok" and rec.get("scrip_code"):
                out[str(rec["scrip_code"])] = rec
    return out


SCREEN_BY_CODE = {}
FIRST_SEEN = {}


def fmt_cr(v):
    if not v:
        return None
    return "Rs {:,.0f} cr".format(v) if v < 100000 else "Rs {:.2f} L cr".format(v / 100000)


def load():
    rows = []
    with open(IN, encoding="utf-8") as fh:
        for line in fh:
            try:
                rec = json.loads(line)
            except Exception:
                continue
            if rec.get("status") != "ok" or not rec.get("hits"):
                continue
            # Re-apply the sentence filter on read. Patterns get tightened over
            # time; this lets a fix reach data mined before it existed, instead
            # of forcing a re-download of every report.
            rec["hits"] = [h for h in rec["hits"] if not BOILERPLATE.search(h["text"])]
            if rec["hits"]:
                rows.append(rec)
    for r in rows:
        sc = SCREEN_BY_CODE.get(str(r.get("scrip_code") or ""))
        if sc:
            r["market_cap_cr"] = sc.get("market_cap_cr")
            r["concalls"] = sc.get("concalls")
    cutoff = (date.today() - timedelta(days=NEW_DAYS)).isoformat()
    for r in rows:
        meta = FIRST_SEEN.get(r["key"]) or {}
        r["first_seen"] = meta.get("first_seen", "")
        r["filed_on"] = meta.get("filed_on", "")
        r["is_new"] = bool(r["first_seen"] and r["first_seen"] >= cutoff)
    rows.sort(key=lambda r: (not r["is_new"], r.get("market_cap_cr") or 0))
    return rows


def render_company(rec):
    name = html.escape(rec.get("name") or rec["key"])
    url = html.escape(rec.get("url") or "")
    ticker = html.escape(rec.get("nse_symbol") or rec.get("scrip_code") or rec["key"])
    buckets = {c: [h for h in rec["hits"] if c in h["categories"]] for c in ORDER}
    cats_present = " ".join(c for c in ORDER if buckets[c])

    blocks = []
    for cat in ORDER:
        hits = buckets[cat]
        if not hits:
            continue
        items = "".join(
            '<li class="x">{}<a class="pg" href="{}#page={}" target="_blank" '
            'rel="noopener">p{}</a></li>'.format(mark(h["text"]), url, h["page"], h["page"])
            for h in hits)
        blocks.append(
            '<div class="bk" data-c="{c}"><h4 class="{c}">{label}<span>{n}</span></h4>'
            '<ul>{items}</ul></div>'.format(
                c=cat, label=LABELS[cat][0], n=len(hits), items=items))

    return (
        '<section class="co" data-cats="{cats}" data-name="{key}" '
        'data-mcap="{mcapval:.0f}" data-hits="{n}" '
        'data-code="{code}" data-company="{plain}" data-mcapraw="{mcapraw:.0f}" '
        'data-filed="{filed}">'
        '<header><h3><a class="gg" href="https://www.google.com/search?q={query}" '
        'target="_blank" rel="noopener">{name}</a>'
        '<button class="star" title="Add to watchlist" aria-pressed="false">&#9734;</button>'
        '<button class="cov" title="Mark as already covered" aria-pressed="false">&#10003;</button>'
        '</h3><div class="meta"><code>{ticker}</code>'
        '{mcap}{silent}{filedtxt}<span>{pages} pages</span><span>{n} extracts</span>'
        '<a href="{url}" target="_blank" rel="noopener">source PDF &#8599;</a>'
        '</div></header>{blocks}</section>'.format(
            cats=cats_present, key=(name + " " + ticker).lower(), name=name,
            mcapval=(rec.get("market_cap_cr") or 0),
            code=html.escape(str(rec.get("scrip_code") or "")),
            plain=html.escape(rec.get("name") or rec["key"], quote=True),
            query=quote_plus(rec.get("name") or rec["key"]),
            # watchlist.json stores market cap in raw rupees, not crore
            mcapraw=(rec.get("market_cap_cr") or 0) * 1e7,
            filed=html.escape(rec.get("filed_on") or ""),
            filedtxt=('<span class="filed">filed ' + html.escape(rec["filed_on"])
                      + '</span>') if rec.get("filed_on") else '',
            ticker=ticker, pages=rec.get("pages", "?"), n=len(rec["hits"]),
            url=url, blocks="".join(blocks),
            mcap=('<span class="mc">' + fmt_cr(rec.get("market_cap_cr")) + '</span>'
                  if fmt_cr(rec.get("market_cap_cr")) else ''),
            silent=('<span class="sil">no concalls</span>'
                    if rec.get("concalls") == 0 else '')
                   + ('<span class="new">new</span>' if rec.get("is_new") else '')))


CSS = """
:root{--bg:#fbfaf9;--card:#fff;--ink:#1c1a17;--dim:#6b665f;--line:#e6e2dc;
--g:#0f7b6c;--f:#1f6feb;--k:#a8570a;--mark:#fff3cd}
@media(prefers-color-scheme:dark){:root:not([data-theme=light]){--bg:#161513;--card:#1f1e1b;
--ink:#eceae6;--dim:#9c968d;--line:#33312d;--g:#4ec9b0;--f:#6ca8ff;--k:#e0a458;--mark:#3d3620}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:980px;margin:0 auto;padding:32px 20px 80px}
h1{font-size:26px;margin:0 0 6px;letter-spacing:-.02em}
.sub{color:var(--dim);margin:0 0 26px;font-size:14px}
.bar{position:sticky;top:0;z-index:10;background:var(--bg);padding:12px 0;
border-bottom:1px solid var(--line);margin-bottom:22px}
#q{width:100%;padding:10px 13px;border:1px solid var(--line);border-radius:9px;
background:var(--card);color:var(--ink);font-size:15px}
.chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.chip{display:flex;align-items:center;gap:7px;padding:5px 11px;border:1px solid var(--line);
border-radius:999px;background:var(--card);color:var(--dim);cursor:pointer;font-size:13px}
.chip[aria-pressed=true]{color:var(--ink);border-color:currentColor}
.chip span{font-variant-numeric:tabular-nums;opacity:.65}
.chip i{width:8px;height:8px;border-radius:50%;display:block}
.chips.size{align-items:center}
.lbl{font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;color:var(--dim);
font-weight:600}
.lbl.srt{margin-left:8px}
#sort{padding:5px 10px;border:1px solid var(--line);border-radius:999px;
background:var(--card);color:var(--ink);font-size:13px;cursor:pointer}
.chip.band[aria-pressed=true]{background:var(--ink);color:var(--bg);border-color:var(--ink)}
.chip.band[aria-pressed=true] span{opacity:.8}
i.guidance,h4.guidance,dt.guidance{background:var(--g)}
i.future_plans,h4.future_plans,dt.future_plans{background:var(--f)}
i.kpis,h4.kpis,dt.kpis{background:var(--k)}
dl.leg{display:grid;grid-template-columns:auto 1fr;gap:5px 12px;font-size:13px;
background:var(--card);border:1px solid var(--line);border-radius:11px;padding:15px 17px;margin:0 0 24px}
dl.leg dt{font-weight:600;color:#fff;padding:1px 9px;border-radius:5px;font-size:12px;
justify-self:start}
dl.leg dd{margin:0;color:var(--dim)}
.co{background:var(--card);border:1px solid var(--line);border-radius:13px;
padding:18px 20px;margin-bottom:15px}
.co h3{margin:0;font-size:17px;letter-spacing:-.01em;display:flex;
align-items:center;gap:9px}
a.gg{color:inherit;text-decoration:none;border-bottom:1px dotted var(--line)}
a.gg:hover{color:var(--f);border-bottom-color:currentColor}
.star{background:none;border:0;cursor:pointer;font-size:19px;line-height:1;
color:var(--dim);padding:0 2px}
.star:hover{color:#e0a458}
.star[aria-pressed=true]{color:#e0a458}
.cov{background:none;border:0;cursor:pointer;font-size:15px;line-height:1;
color:var(--line);padding:0 2px}
.cov:hover{color:var(--g)}
.cov[aria-pressed=true]{color:var(--g)}
.co.covered{opacity:.55}
.co.covered:hover{opacity:1}
.filed{font-variant-numeric:tabular-nums}
#wl{position:fixed;left:0;right:0;bottom:0;z-index:20;background:var(--card);
border-top:1px solid var(--line);padding:11px 20px;display:none;
box-shadow:0 -3px 14px rgba(0,0,0,.08)}
#wl.on{display:block}
#wl .in{max-width:980px;margin:0 auto;display:flex;align-items:center;
gap:11px;flex-wrap:wrap}
#wl b{font-size:14px}
#wl .names{color:var(--dim);font-size:13px;flex:1;min-width:200px;
overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
#wl button{padding:6px 13px;border:1px solid var(--line);border-radius:8px;
background:var(--bg);color:var(--ink);font-size:13px;cursor:pointer}
#wl button:hover{border-color:var(--f);color:var(--f)}
#wl button.warn:hover{border-color:#b45309;color:#b45309}
.meta{display:flex;gap:13px;flex-wrap:wrap;align-items:center;color:var(--dim);
font-size:12.5px;margin:5px 0 14px}
.meta code{background:var(--bg);padding:1px 7px;border-radius:5px;border:1px solid var(--line)}
.meta a{color:var(--f);text-decoration:none}
.mc{font-weight:600;color:var(--ink)}
.new{background:#b45309;color:#fff;padding:1px 8px;border-radius:5px;
font-size:11.5px;font-weight:600;letter-spacing:.03em}
.sil{background:var(--g);color:#fff;padding:1px 8px;border-radius:5px;
font-size:11.5px;font-weight:600}
h4{margin:14px 0 8px;font-size:11.5px;text-transform:uppercase;letter-spacing:.07em;
color:#fff;display:inline-flex;align-items:center;gap:7px;padding:3px 10px;border-radius:6px}
h4 span{opacity:.75;font-variant-numeric:tabular-nums}
.bk ul{margin:0;padding:0;list-style:none}
.x{position:relative;padding:9px 52px 9px 14px;margin-bottom:7px;
border-left:2px solid var(--line);font-size:14.2px}
.x b{background:var(--mark);padding:0 2px;border-radius:3px;font-weight:600}
.pg{position:absolute;right:10px;top:9px;font-size:11.5px;color:var(--dim);
text-decoration:none;font-variant-numeric:tabular-nums}
.pg:hover{color:var(--f)}
.hide{display:none!important}
.empty{color:var(--dim);text-align:center;padding:50px 0}
footer{margin-top:40px;color:var(--dim);font-size:12.5px;
border-top:1px solid var(--line);padding-top:16px}
"""

JS = """
const q=document.getElementById('q'),
      list=document.getElementById('list'),
      cos=[...document.querySelectorAll('.co')],
      none=document.getElementById('none'),
      sortSel=document.getElementById('sort'),
      on=new Set(),          // active category filters
      bands=new Set();       // active market-cap bands, as [lo,hi] pairs

// Category chips. Scoped away from the size chips (which carry data-lo/data-hi)
// and from #covhide (an action button, not a filter) -- all three share the
// .chip class for styling only. Selecting on data-f is the robust test.
document.querySelectorAll('.chip[data-f]').forEach(b=>b.onclick=()=>{
  const f=b.dataset.f; on.has(f)?on.delete(f):on.add(f);
  b.setAttribute('aria-pressed',on.has(f)); apply();});

document.querySelectorAll('.chip.band').forEach(b=>b.onclick=()=>{
  const key=b.dataset.lo+':'+b.dataset.hi;
  bands.has(key)?bands.delete(key):bands.add(key);
  b.setAttribute('aria-pressed',bands.has(key)); apply();});

sortSel.addEventListener('change',apply);
q.addEventListener('input',apply);

function inBands(mcap){
  if(!bands.size) return true;
  for(const k of bands){
    const [lo,hi]=k.split(':');
    if(mcap>=(+lo) && (hi===''||mcap<(+hi))) return true;
  }
  return false;
}

function apply(){
  const t=q.value.trim().toLowerCase(); let shown=0;
  for(const c of cos){
    let vis=!on.size||[...on].some(f=>c.dataset.cats.includes(f));
    if(vis&&wlOnly) vis=!!wl[wlKey(c)];
    if(vis&&hideCov) vis=!cov[c.dataset.code];
    if(vis) vis=inBands(+c.dataset.mcap);
    if(vis&&t) vis=c.dataset.name.includes(t)||c.textContent.toLowerCase().includes(t);
    c.classList.toggle('hide',!vis);
    if(vis){shown++;
      c.querySelectorAll('.bk').forEach(b=>
        b.classList.toggle('hide',on.size>0&&!on.has(b.dataset.c)));}
  }
  none.classList.toggle('hide',shown>0);
  resort();
}

/* ---------- watchlist ----------
   Keyed and shaped to match data/watchlist.json: "{scrip}_BSE" -> record with
   market_cap in raw rupees, so the exported JSON merges straight in.
   Persisted in localStorage, which survives the daily rebuild of this file
   because the path does not change. Falls back to memory if the browser
   blocks storage on file:// URLs -- the export buttons still work either way. */
const WLKEY='ar-watchlist-v1';
let wl={}, wlOnly=false, storageOK=true;
try{ wl=JSON.parse(localStorage.getItem(WLKEY)||'{}'); }
catch(e){ storageOK=false; wl={}; }

function wlSave(){
  if(!storageOK) return;
  try{ localStorage.setItem(WLKEY,JSON.stringify(wl)); }catch(e){ storageOK=false; }
}
function fmtCr(raw){
  const cr=raw/1e7;
  return cr>=100000?(cr/100000).toFixed(2)+'L Cr':(cr>=1000?(cr/1000).toFixed(2)+'K Cr':Math.round(cr)+' Cr');
}
function wlKey(c){ return c.dataset.code+'_BSE'; }

function wlToggle(c){
  const k=wlKey(c);
  if(wl[k]) delete wl[k];
  else wl[k]={company:c.dataset.company,symbol:c.dataset.code,exchange:'BSE',
              market_cap:+c.dataset.mcapraw,
              market_cap_fmt:fmtCr(+c.dataset.mcapraw),
              added:new Date().toISOString().slice(0,10),notes:[]};
  wlSave(); wlPaint(); if(wlOnly) apply();
}
function wlPaint(){
  const keys=Object.keys(wl);
  document.getElementById('wl').classList.toggle('on',keys.length>0);
  document.getElementById('wln').textContent=
    keys.length+' on watchlist'+(storageOK?'':' (not saved - browser blocked storage)');
  document.getElementById('wlnames').textContent=keys.map(k=>wl[k].company).join(', ');
  for(const c of cos) c.querySelector('.star').setAttribute('aria-pressed',!!wl[wlKey(c)]);
  document.getElementById('wlshow').textContent=wlOnly?'Show all':'Show only these';
}
document.querySelectorAll('.star').forEach(b=>b.onclick=e=>{
  e.preventDefault(); e.stopPropagation(); wlToggle(b.closest('.co'));});

/* ---------- "already covered" ----------
   Separate from the watchlist: the watchlist is what you want to look at, this
   is what you have already written about. Keyed by scrip code and kept in
   localStorage, so it survives the daily rebuild -- which is the point. When a
   new SME shows up months from now, anything you have already covered is
   dimmed and tickable out, so only genuinely new names demand attention. */
const COVKEY='ar-covered-v1';
let cov={}, hideCov=false;
try{ cov=JSON.parse(localStorage.getItem(COVKEY)||'{}'); }catch(e){ cov={}; }

function covSave(){ try{ localStorage.setItem(COVKEY,JSON.stringify(cov)); }catch(e){} }
function covToggle(c){
  const k=c.dataset.code;
  if(cov[k]) delete cov[k];
  else cov[k]={company:c.dataset.company,on:new Date().toISOString().slice(0,10)};
  covSave(); covPaint(); if(hideCov) apply();
}
function covPaint(){
  for(const c of cos){
    const isC=!!cov[c.dataset.code];
    c.classList.toggle('covered',isC);
    c.querySelector('.cov').setAttribute('aria-pressed',isC);
  }
  const n=Object.keys(cov).length;
  const btn=document.getElementById('covhide');
  btn.textContent=(hideCov?'Show covered':'Hide covered')+(n?' ('+n+')':'');
  btn.classList.toggle('hide',n===0);
}
document.querySelectorAll('.cov').forEach(b=>b.onclick=e=>{
  e.preventDefault(); e.stopPropagation(); covToggle(b.closest('.co'));});
document.getElementById('covhide').onclick=()=>{hideCov=!hideCov; covPaint(); apply();};

document.getElementById('wlshow').onclick=()=>{wlOnly=!wlOnly; wlPaint(); apply();};
document.getElementById('wlclear').onclick=()=>{
  if(!confirm('Remove all '+Object.keys(wl).length+' companies from the watchlist?'))return;
  wl={}; wlOnly=false; wlSave(); wlPaint(); apply();};
document.getElementById('wlcopy').onclick=async()=>{
  const txt=JSON.stringify(wl,null,1);
  try{ await navigator.clipboard.writeText(txt);
       document.getElementById('wlcopy').textContent='Copied';
       setTimeout(()=>document.getElementById('wlcopy').textContent='Copy JSON',1400);
  }catch(e){ window.prompt('Copy the JSON below:',txt); }};
document.getElementById('wldl').onclick=()=>{
  const blob=new Blob([JSON.stringify(wl,null,1)],{type:'application/json'});
  const a=document.createElement('a');
  a.href=URL.createObjectURL(blob); a.download='watchlist_additions.json';
  document.body.appendChild(a); a.click(); a.remove();
  setTimeout(()=>URL.revokeObjectURL(a.href),2000);};

function resort(){
  const how=sortSel.value;
  const cmp={
    small:(a,b)=>(+a.dataset.mcap)-(+b.dataset.mcap),
    large:(a,b)=>(+b.dataset.mcap)-(+a.dataset.mcap),
    filed:(a,b)=>(b.dataset.filed||'').localeCompare(a.dataset.filed||''),
    hits:(a,b)=>(+b.dataset.hits)-(+a.dataset.hits),
    name:(a,b)=>a.dataset.name.localeCompare(b.dataset.name),
  }[how];
  // Reordering detached avoids a reflow per node on a 277-card list.
  const frag=document.createDocumentFragment();
  [...cos].sort(cmp).forEach(c=>frag.appendChild(c));
  list.appendChild(frag);
}

wlPaint();
covPaint();
resort();
"""


def write_feed(rows):
    """Emit the JSON the docs/ Annual Reports tab consumes.

    Shaped so each record can be handed straight to the existing watchlist
    code: symbol + exchange form the same "{scrip}_BSE" key that
    getWatchlistKey() builds for announcements, so a company starred here and
    a company starred from an announcement land on the same entry.
    """
    out = []
    for r in rows:
        out.append({
            "company": r.get("name") or r["key"],
            "symbol": str(r.get("scrip_code") or ""),
            "exchange": "BSE",
            "market_cap": int((r.get("market_cap_cr") or 0) * 1e7),
            "market_cap_fmt": fmt_cr(r.get("market_cap_cr")) or "N/A",
            "filed_on": r.get("filed_on") or "",
            "first_seen": r.get("first_seen") or "",
            "is_new": bool(r.get("is_new")),
            "url": r.get("url") or "",
            "hits": [{"p": h["page"], "c": h["categories"],
                      "t": dehyphenate(h["text"])} for h in r["hits"]],
        })
    payload = {"generated": datetime.now().isoformat(timespec="seconds"),
               "count": len(out), "companies": out}
    with open(FEED, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
    return payload


def main():
    global SCREEN_BY_CODE, FIRST_SEEN
    SCREEN_BY_CODE = load_screen()
    FIRST_SEEN = load_first_seen()
    rows = load()
    tally = Counter()
    for r in rows:
        for h in r["hits"]:
            for c in h["categories"]:
                tally[c] += 1
    total_hits = sum(len(r["hits"]) for r in rows)
    stamp = datetime.now().strftime("%d %b %Y, %H:%M")

    chips = "".join(
        '<button class="chip" data-f="{c}"><i class="{c}"></i>{label}<span>{n}</span></button>'
        .format(c=c, label=LABELS[c][0], n=tally.get(c, 0)) for c in ORDER)
    counts_band = {b[0]: 0 for b in BANDS}
    for r in rows:
        mc = r.get("market_cap_cr") or 0
        for key, _, lo, hi in BANDS:
            if lo <= mc < hi:
                counts_band[key] += 1
                break
    bands = "".join(
        '<button class="chip band" data-lo="{lo}" data-hi="{hi}">{label}'
        '<span>{n}</span></button>'.format(
            lo=lo, hi=("" if hi == float("inf") else hi), label=label,
            n=counts_band[key]) for key, label, lo, hi in BANDS)

    legend = "".join(
        '<dt class="{c}">{label}</dt><dd>{desc}</dd>'.format(
            c=c, label=LABELS[c][0], desc=LABELS[c][1]) for c in ORDER)

    doc = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FY2026 Annual Report Guidance Tracker</title>
<style>{css}</style></head><body><div class="wrap">
<h1>FY2026 Annual Report Guidance Tracker</h1>
<p class="sub">{ncos} companies &middot; {nhits} extracts &middot; generated {stamp}</p>
<dl class="leg">{legend}</dl>
<div class="bar"><input id="q" type="search"
 placeholder="Search company, ticker or any phrase&hellip;">
<div class="chips">{chips}</div>
<div class="chips size">
  <span class="lbl">Size</span>{bands}
  <button id="covhide" class="chip hide">Hide covered</button>
  <span class="lbl srt">Sort</span>
  <select id="sort">
    <option value="small">Smallest first</option>
    <option value="large">Largest first</option>
    <option value="filed">Recently filed</option>
    <option value="hits">Most extracts</option>
    <option value="name">Company name</option>
  </select>
</div></div>
<div id="list">{body}</div>
<p class="empty hide" id="none">Nothing matches that filter.</p>
<div id="wl"><div class="in">
  <b id="wln">0 on watchlist</b>
  <span class="names" id="wlnames"></span>
  <button id="wlshow">Show only these</button>
  <button id="wlcopy">Copy JSON</button>
  <button id="wldl">Download</button>
  <button id="wlclear" class="warn">Clear</button>
</div></div>
<footer>Extracts are verbatim sentences located by pattern matching over the
management commentary sections of each annual report; financial statements,
notices, governance and BRSR/CSR annexures are excluded. Page links open the
source PDF. Machine-selected &mdash; read the source before acting on anything
here.</footer>
</div><script>{js}</script></body></html>""".format(
        css=CSS, js=JS, ncos=len(rows), nhits=total_hits, stamp=stamp,
        legend=legend, chips=chips, bands=bands,
        body="".join(render_company(r) for r in rows))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fh:
        fh.write(doc)
    print("{} companies, {} extracts -> {}".format(len(rows), total_hits, OUT))
    feed = write_feed(rows)
    print("feed: {} companies -> {} ({:.0f} KB)".format(
        feed["count"], FEED, os.path.getsize(FEED) / 1024))
    print("  by category: " + ", ".join(
        "{} {}".format(LABELS[c][0], tally.get(c, 0)) for c in ORDER))


if __name__ == "__main__":
    main()
