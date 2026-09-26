import requests

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Referer": "https://understat.com/match/30773",
    "X-Requested-With": "XMLHttpRequest",
}

candidates = [
    "https://understat.com/getMatchData/30773",
    "https://understat.com/match/getMatchData/30773",
    "https://understat.com/main/getMatchData/30773",
]

for url in candidates:
    try:
        r = requests.get(url, headers=headers, timeout=20)
        ct = r.headers.get("content-type", "")
        print(f"{r.status_code}  {url}  len={len(r.text)}  ct={ct}")
        if r.status_code == 200:
            print("   OK! sample:", r.text[:200])
    except Exception as e:
        print(f"ERR {url}: {e}")