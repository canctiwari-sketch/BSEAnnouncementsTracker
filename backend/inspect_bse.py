import requests
from bs4 import BeautifulSoup
import re

url = "https://www.bseindia.com/corporates/ann.html"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/120.0.0.0 Safari/537.36",
    "Accept": "*/*",
}
r = requests.get(url, headers=headers)
print("BSE Ann Page Status:", r.status_code)
# Let's search for "api" or "json" in the page content
soup = BeautifulSoup(r.text, 'html.parser')
scripts = soup.find_all('script')
for s in scripts:
    if s.string and 'api.bseindia.com' in s.string:
        print("Found API in script:", s.string[:200])
