# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "collection")
from final_500_collector import Client, SCHED_URL

c = Client(delay=0.3, cookies="data/cookies_500.json")
# 测试 liansai.500.com 的 getmatch 赛程接口（refresh-odds 走的通道）
url = f"{SCHED_URL}&stid=28025&round=3"
r = c.get(url, referer="https://liansai.500.com/")
print("status:", r.status_code, "len:", len(r.text))
txt = r.text[:2000]
print("has Security Verification:", "Security Verification" in r.text or "TEOCaptchaWidget" in r.text)
print(txt)