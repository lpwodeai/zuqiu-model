# -*- coding: utf-8 -*-
"""临时脚本：把用户新导出的 500.com Cookie 写入 data/cookies_500.json"""
import json
from pathlib import Path

RAW = (
    "path=/; __tst_status=3235236854#; EO_Bot_Ssid=3861643264; "
    "__utmz=63332592.1789121763.1.1.utmcsr=(direct)|utmccn=(direct)|utmcmd=(none); "
    "ck_RegFromUrl=https%3A//odds.500.com/fenxi/ouzhi-1428496.shtml; "
    "sdc_session=1789274465678; "
    "Hm_lvt_4f816d475bb0b9ed640ae412d6b42cab=1789121761,1789265283,1789274466; "
    "HMACCOUNT=A1FF6D217E066680; _jzqc=1; _qzjc=1; __utmc=63332592; liansaihidetag=false; "
    "_jzqa=1.2146547062809550300.1789121761.1789568565.1789647018.9; "
    "_jzqx=1.1789219874.1789647018.4.jzqsr=odds%2E500%2Ecom|jzqct=/fenxi/ouzhi-1428496%2Eshtml.jzqsr=liansai%2E500%2Ecom|jzqct=/zuqiu-19947/; "
    "_jzqckmp=1; __utma=63332592.572373341.1789121763.1789568566.1789647019.9; __utmt=1; "
    "EO-Bot-Captcha-Token=t04GIL1SxsA5pHCAUkUOvuNu9JzHPr49KDYVbSWVXLaNsTzbK0B7CIMifBnX2iJHBOy4R3Rxc1eY1j7BZgXqFoyvBGewJDS648RYp_DBfuOyLvqc72EPx9Q-gLnRAiXkCSS04EejY6kUoZvtjl5SIi-VeAqOJBq1Xke0aKkyrSZVtUBDXKKxuFi1YsBdghGQrHDdkj7MIo81LfniK5zrTVfbzDTE3RPF-wYzmg07dr5l3W-WmApOkw4Goec4ReVktiO7hbOUpOoBA0XRGywhpU9i2-ht6qvYAdtCdvxJBFcFrpjj0yXwsejB5KjUJ38pGx7BY7bWheVPKJrpN5gN_2nHhUNQU9eZgOlHXY-Rutnaq_aAFOfwxoo_WzqujbXB4S_s-0kSKwc5_HUhyikQRGXHOVoiB9a236DJGAHZURL7wW2FmL_BUVJIdN-1QdD7Fsa; "
    "_qzja=1.212371937.1789133477102.1789568564804.1789647016683.1789647045602.1789647048723.0.0.0.33.8; "
    "_qzjb=1.1789647016683.3.0.0.0; _qzjto=3.1.0; _jzqb=1.5.10.1789647018.1; "
    "WT_FPC=id=undefined:lv=1789647048814:ss=1789647045610; sdc_userflag=1789647017904::1789647048817::5; "
    "Hm_lpvt_4f816d475bb0b9ed640ae412d6b42cab=1789647049; __utmb=63332592.5.10.1789647019; "
    "CLICKSTRN_ID=240e:359:c52:5c00:c891:5362:cf4d:5293-1789121763.4304545::11F08BCD432EA11AECB75D0E12F4E5F2; "
    "motion_id=1789647062503_0.8197333940481165"
)

cookies = {}
for seg in RAW.split(";"):
    seg = seg.strip()
    if not seg or seg == "path=/":
        continue
    if "=" not in seg:
        continue
    k, v = seg.split("=", 1)
    k, v = k.strip(), v.strip()
    if k and k != "path":
        cookies[k] = v

target = Path("data/cookies_500.json")
old = {}
if target.exists():
    try:
        old = json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        old = {}

# 保留旧 user_agent（新 cookie 不含 UA），其余整体替换为新 cookie
ua = old.get("__user_agent")
if ua:
    cookies["__user_agent"] = ua

target.write_text(json.dumps(cookies, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"已写入 {len(cookies)} 条 cookie 到 {target}")
print("keys:", ", ".join(sorted(cookies.keys())))