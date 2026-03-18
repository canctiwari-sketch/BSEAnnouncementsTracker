import requests
from bs4 import BeautifulSoup
import pandas as pd
import yfinance as yf
import json
import os

def fetch_bse_announcements():
    # Since BSE API gives 403 or HTML block for python requests, 
    # we'll fetch from the List_Scrips.html to see what we can do, or we might need to 
    # find another way. Let's try downloading the CSV from List_Scrips directly if possible,
    # or use another known BSE endpoint.
    
    # Actually, in our previous test `test_bse.py` the BSE API `AnnSubCategoryGetData` 
    # returned 200 with 0 announcements, but maybe we need the right parameters or date format?
    pass

def test_yfinance_mcap():
    # Test getting mcap for a known BSE stock.
    # BSE scrip codes usually have `.BO` extension in Yahoo Finance.
    # Example: Reliance is 500325
    ticker = yf.Ticker("500325.BO")
    info = ticker.info
    mcap = info.get('marketCap', 'N/A')
    name = info.get('shortName', 'N/A')
    print(f"BSE: 500325.BO, Name: {name}, Market Cap: {mcap}")
    
    # Try an NSE ticker just in case
    ticker_nse = yf.Ticker("RELIANCE.NS")
    info_nse = ticker_nse.info
    print(f"NSE: RELIANCE.NS, Name: {info_nse.get('shortName', 'N/A')}, Market Cap: {info_nse.get('marketCap', 'N/A')}")

if __name__ == "__main__":
    test_yfinance_mcap()
