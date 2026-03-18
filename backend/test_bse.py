import requests

url = "https://api.bseindia.com/BseIndiaAPI/api/AnnDataGetData/w?strCat=-1&pageno=1&strPrevDate=20260220&strToDate=20260225"
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)",
    "Accept": "*/*",
    "Referer": "https://www.bseindia.com/",
}
response = requests.get(url, headers=headers)
print("Status:", response.status_code)
try:
    data = response.json()
    print("Keys:", data.keys())
    print("Got", len(data.get("Table", [])), "announcements")
except Exception as e:
    print("Error:", e, response.text[:200])
