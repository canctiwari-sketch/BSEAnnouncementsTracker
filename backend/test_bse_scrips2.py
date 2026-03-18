import requests
from bs4 import BeautifulSoup
import pandas as pd
import io

def fetch_bse_scrips():
    url = "https://www.bseindia.com/corporates/List_Scrips.html"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    }
    r = requests.get(url, headers=headers)
    print("Status:", r.status_code)
    # print out scripts to see if there is an API url
    soup = BeautifulSoup(r.text, 'html.parser')
    for s in soup.find_all('script'):
        if s.string and 'api.bseindia.com' in s.string:
            print("Found script with api:", s.string[:300])
        elif s.get('src'):
            print("Script src:", s.get('src'))

fetch_bse_scrips()
