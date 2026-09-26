# -*- coding: utf-8 -*-
import sys
sys.path.insert(0, "collection")
from final_500_collector import Client, FENXI_URL

c = Client(delay=0.3, cookies="data/cookies_500.json")
url = FENXI_URL.format(page="ouzhi", fid=1428493)
r = c.get(url, referer="https://odds.500.com/fenxi/")
print(r.text)