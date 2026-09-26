import requests, re, json

headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}

# 探测西甲 26/27 赛季联赛页
urls = [
    "https://understat.com/league/La_liga/2026",
    "https://understat.com/league/La_liga",
]
for url in urls:
    try:
        r = requests.get(url, headers=headers, timeout=30)
        print(f"\n{url} -> {r.status_code}  len={len(r.text)}")
        if r.status_code == 200:
            # 找 JSON.parse 变量
            vars_found = re.findall(r"var\s+(\w+)\s*=\s*JSON\.parse", r.text)
            print("  JSON.parse 变量:", sorted(set(vars_found)))
            # 找 match 链接
            matches = re.findall(r"/match/(\d+)", r.text)
            print("  match id 数量:", len(set(matches)), "样例:", sorted(set(matches))[:10])
            # 找 season 相关
            seasons = re.findall(r'data-season="(\w+)"|value="(\d{4})"', r.text)
            print("  season 引用:", seasons[:10])
    except Exception as e:
        print(f"\n{url} -> ERR {e}")