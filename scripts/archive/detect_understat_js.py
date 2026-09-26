import requests, re

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
r = requests.get("https://understat.com/js/match.min.js", headers=headers, timeout=30)
print("status:", r.status_code, "len:", len(r.text))

# 搜索 AJAX endpoint 模式
patterns = ["getMatch", "getData", "getPlayers", "shots", "rosters", "url:", ".php", "/main/", "ajax", "JSON.parse"]
for p in patterns:
    idxs = [m.start() for m in re.finditer(re.escape(p), r.text)]
    if idxs:
        print(f"\n== {p} 出现 {len(idxs)} 次 ==")
        # 打印每处上下文
        for i in idxs[:5]:
            print("   ...", r.text[max(0,i-60):i+80].replace("\n"," ")[:140])

# 保存 JS
with open("data/understat_match.min.js", "w", encoding="utf-8") as f:
    f.write(r.text)
print("\n已保存 data/understat_match.min.js")