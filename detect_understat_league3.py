import requests, re

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# 重新下载 league.min.js 并分析 getLeagueData url 拼接
rj = requests.get("https://understat.com/js/league.min.js", headers=headers, timeout=30)
for m in re.finditer(r'.{120}getLeagueData.{120}', rj.text, re.DOTALL):
    print("CONTEXT:", m.group(0).replace("\n", " "), "\n")

# 测试候选端点
for url in [
    "https://understat.com/getLeagueData/La_liga/2026",
    "https://understat.com/main/getLeagueDataJson/La_liga/2026",
]:
    h = dict(headers)
    h["Referer"] = "https://understat.com/league/La_liga/2026"
    h["X-Requested-With"] = "XMLHttpRequest"
    try:
        r = requests.get(url, headers=h, timeout=20)
        print(f"{r.status_code}  {url}  len={len(r.text)}")
        if r.status_code == 200:
            print("   sample:", r.text[:300])
    except Exception as e:
        print(f"ERR {url}: {e}")