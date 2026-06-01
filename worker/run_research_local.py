"""
OFFLINE Deep Research runner — generate a report on your own PC, no website.

Usage:
  1. Put your key in a file named  .env  at the project root:
         GEMINI_API_KEY=AIza....
     (optionally GEMINI_API_KEY_2=AIza.... for a backup key)
  2. Run:   python worker/run_research_local.py
     or double-click  run_research.bat  (Windows)

It asks for a company, finds its BSE/NSE codes from scrips.json, then
generates the same .docx report into data/research/ — identical to the
website's Deep Research button, just on your machine.
"""
import os
import sys
import json

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Load .env from project root so GEMINI_API_KEY is available
try:
    from dotenv import load_dotenv
    load_dotenv(os.path.join(ROOT, ".env"))
except Exception:
    pass

SCRIPS = os.path.join(ROOT, "data", "scrips.json")


def load_scrips():
    with open(SCRIPS, "r", encoding="utf-8") as f:
        return json.load(f)


def search(scrips, query):
    q = query.lower().strip()
    hits = []
    for s in scrips:
        name = (s.get("ScripName") or s.get("IssuerName") or "")
        sym = (s.get("NSESymbol") or "")
        if q in name.lower() or (sym and q == sym.lower()):
            hits.append(s)
    # exact symbol / name first
    hits.sort(key=lambda s: (q != (s.get("NSESymbol") or "").lower(),
                             not (s.get("ScripName") or "").lower().startswith(q),
                             len(s.get("ScripName") or "")))
    return hits[:15]


def main():
    if not os.environ.get("GEMINI_API_KEY"):
        print("\n[!] GEMINI_API_KEY not found.")
        print("    Create a file called  .env  in the project root with:")
        print("        GEMINI_API_KEY=your_key_here\n")
        return

    scrips = load_scrips()
    print(f"Loaded {len(scrips)} companies.\n")

    query = " ".join(sys.argv[1:]).strip()
    if not query:
        query = input("Company name or NSE symbol: ").strip()
    if not query:
        return

    hits = search(scrips, query)
    if not hits:
        print("No match found.")
        return

    if len(hits) == 1:
        chosen = hits[0]
    else:
        print("\nMatches:")
        for i, s in enumerate(hits, 1):
            print(f"  {i:2}. {s.get('ScripName','')}  "
                  f"[BSE:{s.get('ScripCode','')}  NSE:{s.get('NSESymbol','') or '-'}]")
        sel = input("\nPick a number (or Enter for 1): ").strip() or "1"
        try:
            chosen = hits[int(sel) - 1]
        except (ValueError, IndexError):
            print("Invalid selection.")
            return

    name = chosen.get("ScripName") or chosen.get("IssuerName")
    scrip = str(chosen.get("ScripCode") or "")
    nse = chosen.get("NSESymbol") or ""
    print(f"\nGenerating Deep Research report for: {name}  "
          f"(BSE:{scrip}  NSE:{nse})\nThis takes ~5-10 minutes...\n")

    import bse_summarizer
    bse_summarizer.setup_directories()
    docx_path, doc_count = bse_summarizer.analyze_single_stock(
        name, scrip, deep_dive=True, nse_symbol=nse
    )
    if docx_path and os.path.exists(docx_path):
        print(f"\n[DONE] Report saved:\n  {docx_path}\n  ({doc_count} documents analysed)")
        # Open the folder on Windows
        try:
            if os.name == "nt":
                os.startfile(os.path.dirname(docx_path))
        except Exception:
            pass
    else:
        print("\n[FAILED] Report generation did not complete.")


if __name__ == "__main__":
    main()
