import requests, re

headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

r = requests.get("https://understat.com/league/La_liga/2026", headers=headers, timeout=30)
# 找 JS 文件引用
scripts = re.findall(r'src="(js/[^"]+\.js[^"]*)"', r.text)
print("引用 JS:", sorted(set(scripts)))

# 下载 league 相关 JS
for s in sorted(set(scripts)):
    if "league" in s.lower() or "main" in s.lower():
        url = "https://understat.com/" + s
        rj = requests.get(url, headers=headers, timeout=30)
        print(f"\n=== {s} -> {rj.status_code} len={len(rj.text)} ===")
        # 找 AJAX endpoint
        for m in re.finditer(r'(url\s*:\s*"([^"]+)"|get(?:League|Season|Match)[A-Za-z]*\([^)]*\))', rj.text):
            print("  ", m.group(0)[:120])
        # 保存
        with open("data/understat_" + s.split("/")[-1], "w", encoding="utf-8") as f:
            f.write(rj.text)