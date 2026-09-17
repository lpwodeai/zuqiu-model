import requests

url = "https://understat.com/match/30773"
headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

r = requests.get(url, headers=headers, timeout=30)
print("status:", r.status_code)
print("content length:", len(r.text))

# 找出 HTML 里内嵌的 script 变量名
import re
# understat 用 var xxx = JSON.parse('...') 形式
vars_found = re.findall(r"var\s+(\w+)\s*=\s*JSON\.parse", r.text)
print("\nJSON.parse 变量:", sorted(set(vars_found)))

# 其他关键变量
for key in ["var shotsData", "var rostersData", "var teamsData", "var match_info",
            "var home", "var away", "var home_xg", "var away_xg", "var date"]:
    if key in r.text:
        print("存在变量:", key)

# 保存 html 供后续解析
with open("data/understat_sample_30773.html", "w", encoding="utf-8") as f:
    f.write(r.text)
print("\n已保存 data/understat_sample_30773.html")