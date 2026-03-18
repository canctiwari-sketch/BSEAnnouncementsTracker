import requests
from bs4 import BeautifulSoup
import pandas as pd
import io

def fetch_bse_scrips():
    url = "https://www.bseindia.com/corporates/List_Scrips.aspx" # Sometimes it's .aspx
    url2 = "https://www.bseindia.com/corporates/List_Scrips.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    r = requests.get(url2, headers=headers)
    print("Status:", r.status_code)
    print("Length:", len(r.text))
    # Check if there's a direct download link for CSV in the page
    soup = BeautifulSoup(r.text, 'html.parser')
    links = soup.find_all('a')
    for a in links:
        if a.get('href') and ('csv' in a.get('href').lower() or 'excel' in a.get('href').lower()):
            print("Found download link:", a.get('href'))

fetch_bse_scrips()
