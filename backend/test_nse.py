import requests
import zipfile
import io

def test_nse_pr_zip():
    # 03 March 26
    url = "https://archives.nseindia.com/archives/equities/bhavcopy/pr/PR030326.zip"
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"
    }
    r = requests.get(url, headers=headers)
    print("NSE PR Zip status:", r.status_code)
    if r.status_code == 200:
        try:
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                print("Files in zip:", z.namelist())
                # If there's an mcap file
                for name in z.namelist():
                    if 'mcap' in name.lower():
                        print("Found mcap file:", name)
        except Exception as e:
            print("Zip error:", e)

test_nse_pr_zip()
