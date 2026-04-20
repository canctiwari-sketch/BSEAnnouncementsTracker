"""
Stock Research worker — generates a full deep-dive .docx report for a company.
Triggered by stock-research.yml workflow via workflow_dispatch.

Env vars:
  COMPANY_NAME  — e.g. "RSWM Limited"
  SCRIP_CODE    — BSE scrip, e.g. "500350"
  NSE_SYMBOL    — NSE symbol, e.g. "RSWM" (optional)
  GEMINI_API_KEY — Gemini API key
"""
import os, sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bse_summarizer

def main():
    company_name = os.environ.get("COMPANY_NAME", "").strip()
    scrip_code   = os.environ.get("SCRIP_CODE",   "").strip()
    nse_symbol   = os.environ.get("NSE_SYMBOL",   "").strip()

    if not company_name or (not scrip_code and not nse_symbol):
        print("ERROR: COMPANY_NAME and SCRIP_CODE/NSE_SYMBOL required")
        sys.exit(1)

    print(f"Research: {company_name}  BSE:{scrip_code}  NSE:{nse_symbol}")

    # Ensure output dir exists
    research_dir = os.path.join(ROOT, "data", "research")
    os.makedirs(research_dir, exist_ok=True)

    bse_summarizer.setup_directories()
    docx_path, doc_count = bse_summarizer.analyze_single_stock(
        company_name, scrip_code, deep_dive=True, nse_symbol=nse_symbol
    )

    if docx_path and os.path.exists(docx_path):
        print(f"Report saved: {docx_path}  ({doc_count} documents analysed)")
    else:
        print("ERROR: Report generation failed")
        sys.exit(1)

if __name__ == "__main__":
    main()
