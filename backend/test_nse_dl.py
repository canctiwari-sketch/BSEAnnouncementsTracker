import requests
import zipfile
import io
import pandas as pd
from datetime import datetime, timedelta

def test_nse_bhavcopy_download():
    # NSE bhavcopy url often looks like:
    # https://archives.nseindia.com/content/historical/EQUITIES/2026/MAR/cm03MAR2026bhav.csv.zip
    date_str = "03MAR2026"
    url = f"https://archives.nseindia.com/content/historical/EQUITIES/2026/MAR/cm{date_str}bhav.csv.zip"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/"
    }
    print("Fetching:", url)
    session = requests.Session()
    session.headers.update(headers)
    
    # First visit nseindia to get cookies
    try:
        session.get("https://www.nseindia.com", timeout=10)
    except:
        pass
        
    response = session.get(url, timeout=10)
    print("Status:", response.status_code)
    if response.status_code == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(response.content)) as z:
                csv_filename = z.namelist()[0]
                print("Extracted:", csv_filename)
                with z.open(csv_filename) as f:
                    df = pd.read_csv(f)
                    print(df.head())
        except Exception as e:
            print("Zip extraction error:", e)
    else:
        print("Failed. Response preview:", response.text[:200])

if __name__ == "__main__":
    test_nse_bhavcopy_download()
