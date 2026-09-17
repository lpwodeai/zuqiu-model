# -*- coding: utf-8 -*-
"""
500.com 数据分析采集器
========================

采集 500.com 的三类赛前/赛后分析页面数据，写入 odds.db：

  1. 投注分析 (touzhu)  —— 必发成交 / 冷热指数 / 庄家盈亏  (识别冷门核心信号)
  2. 百家欧指 (ouzhi)    —— 57 家公司欧赔共识 + 返还率 + 离散度
  3. 技术统计 (stat)     —— 危险进攻 / 进攻次数 等 SofaScore 缺失的特色指标

数据流:
  赛程枚举 (liansai.500.com getmatch 接口, stid + round)
    -> 得到每场 fid / 中英文队名 / 比赛时间
    -> 逐场抓取 touzhu / ouzhi / stat 三个静态 HTML 页面
    -> 解析后写入 odds.db (odds500_* 表)

用法示例:
  # 单场调试 (不写库，打印解析结果)
  python collection/final_500_collector.py --test 1202381

  # 指定联赛 + 赛季 + 轮次范围试采 (默认 0.5s 间隔)
  python collection/final_500_collector.py --season 21/22 --league 西甲 --rounds 1 --limit 3

  # 采集单赛季五大联赛全量
  python collection/final_500_collector.py --season 21/22

  # 采集近5季五大联赛全量 (21/22 ~ 25/26)
  python collection/final_500_collector.py --season all --league all

  # 刷新当前赛季未开赛场次的亚盘盘口（只走赛程接口，不逐场抓页面）
  python collection/final_500_collector.py --refresh-odds --season 26/27 --rounds 1-8
"""
import sys
import os
import re
import json
import time
import sqlite3
import argparse
from pathlib import Path
from datetime import datetime

import requests
try:
    from curl_cffi import requests as curl_requests
except Exception:  # curl_cffi 缺失时回退 requests
    curl_requests = None
from bs4 import BeautifulSoup

# 动态定位项目根目录，避免硬编码盘符
MODEL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = MODEL_PROJECT_ROOT / "data" / "odds.db"
LOG_DIR = MODEL_PROJECT_ROOT / "logs"

# 队名归一化：统一到 feature_utils.normalize_team_name 的规范中文名，避免「云达不莱梅/云达不来梅」等别名不一致
_SCRIPTS_DIR = MODEL_PROJECT_ROOT / "scripts"
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))
try:
    from feature_utils import normalize_team_name as _normalize_team_name
except Exception:  # 依赖缺失时回退原样，不阻断采集
    _normalize_team_name = lambda s: s

# 500.com 站点
SCHED_URL = "https://liansai.500.com/index.php?c=score&a=getmatch"
FENXI_URL = "https://odds.500.com/fenxi/{page}-{fid}.shtml"

# 手动 Cookie 文件（可选）：用户经 Edge 完成 500.com 人机验证后导出 Cookie，用于绕过腾讯云 EdgeOne JS 反爬。
# 默认路径 data/cookies_500.json；可通过 --cookies 指定。支持两种格式：
#   1) JSON 对象 {"cookie名": "值", ...}  —— 应用到 odds.500.com 与 liansai.500.com 两个域
#   2) JSON 数组 [{"name":"", "value":"", "domain":"", "path":""}, ...] —— 逐条设置
COOKIE_FILE = MODEL_PROJECT_ROOT / "data" / "cookies_500.json"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36 Edg/152.0.0.0")

# 五大联赛 stid 映射：赛季 -> 联赛 -> (stid, 轮数)
# 轮数约定：英超/西甲/意甲 38 轮（20 队）；德甲 34 轮（18 队）；
#           法甲 16/17 ~ 22/23 为 38 轮（20 队），23/24 起缩编 18 队 34 轮
SEASONS = {
    "16/17": {
        "英超": (9848, 38), "西甲": (10193, 38), "意甲": (10216, 38),
        "德甲": (10077, 34), "法甲": (9854, 38),
    },
    "17/18": {
        "英超": (11734, 38), "西甲": (11944, 38), "意甲": (11964, 38),
        "德甲": (11826, 34), "法甲": (11740, 38),
    },
    "18/19": {
        "英超": (13070, 38), "西甲": (13195, 38), "意甲": (13207, 38),
        "德甲": (13109, 34), "法甲": (13051, 38),
    },
    "19/20": {
        "英超": (14789, 38), "西甲": (14981, 38), "意甲": (15160, 38),
        "德甲": (14917, 34), "法甲": (14803, 38),
    },
    "20/21": {
        "英超": (16907, 38), "西甲": (16939, 38), "意甲": (16967, 38),
        "德甲": (16855, 34), "法甲": (16764, 38),
    },
    "21/22": {
        "英超": (17793, 38), "西甲": (17831, 38), "意甲": (17884, 38),
        "德甲": (17800, 34), "法甲": (17818, 38),
    },
    "22/23": {
        "英超": (18819, 38), "西甲": (18844, 38), "意甲": (18849, 38),
        "德甲": (18830, 34), "法甲": (18828, 38),
    },
    "23/24": {
        "英超": (19891, 38), "西甲": (19918, 38), "意甲": (19980, 38),
        "德甲": (19946, 34), "法甲": (19978, 34),
    },
    "24/25": {
        "英超": (21038, 38), "西甲": (21073, 38), "意甲": (21104, 38),
        "德甲": (21105, 34), "法甲": (21047, 34),
    },
    "25/26": {
        "英超": (22196, 38), "西甲": (22267, 38), "意甲": (22159, 38),
        "德甲": (22219, 34), "法甲": (22218, 34),
    },
    "26/27": {
        "英超": (27953, 38), "西甲": (28016, 38), "意甲": (27864, 38),
        "德甲": (28025, 34), "法甲": (27893, 34),
    },
}

LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]

# 历史升降班马补充映射（16/17~22/23 出现，assets/team_name_map.json 未覆盖；
# 英文名对齐 Understat 表实际拼写，用于 match_id 一致性）
_EXTRA_CN_TO_EN = {
    "沃特福德": "Watford",
    "诺维奇": "Norwich",
    "皇家奥维耶多": "Real Oviedo",
    "斯佩齐亚": "Spezia",
    "桑普多利亚": "Sampdoria",
    "基尔高士丁": "Holstein Kiel",
    "柏林赫塔": "Hertha Berlin",
    "比勒费尔德": "Arminia Bielefeld",
    "沙尔克04": "Schalke 04",
    "菲尔特": "Greuther Fuerth",
    "克莱蒙特": "Clermont Foot",
    "波尔多": "Bordeaux",
    "特鲁瓦": "Troyes",
    "阿雅克肖": "Ajaccio",
    "赫尔城": "Hull",
    "米德尔斯堡": "Middlesbrough",
    "斯托克城": "Stoke",
    "斯旺西": "Swansea",
    "西布罗姆维奇": "West Brom",
    "哈德斯菲尔德": "Huddersfield",
    "加的夫城": "Cardiff",
    "亚眠": "Amiens",
    "卡昂": "Caen",
    "南锡": "Nancy",
    "甘冈": "Guingamp",
    "第戎": "Dijon",
    "尼姆": "Nimes",
    "巴斯蒂亚": "Bastia",
    "切沃": "Chievo",
    "克罗托内": "Crotone",
    "佩斯卡拉": "Pescara",
    "巴勒莫": "Palermo",
    "贝内文托": "Benevento",
    "布雷西亚": "Brescia",
    "费拉拉SPAL": "Spal",
    "因戈尔施塔特": "Ingolstadt",
    "汉诺威96": "Hannover",
    "纽伦堡": "Nuernberg",
    "杜塞尔多夫": "Duesseldorf",
    "帕德博恩": "Paderborn",
    "埃瓦尔": "Eibar",
    "马拉加": "Malaga",
    "拉科鲁尼亚": "La Coruna",
    "希洪竞技": "Gijon",
    "韦斯卡": "Huesca",
}


def _load_cn_to_en():
    """中文队名 -> 英文标准名（加载 assets/team_name_map.json + 升降班马补充）。"""
    mapping = {}
    map_path = MODEL_PROJECT_ROOT / "assets" / "team_name_map.json"
    if map_path.exists():
        try:
            mapping.update(json.loads(map_path.read_text(encoding="utf-8")))
        except Exception:
            pass
    mapping.update(_EXTRA_CN_TO_EN)
    return mapping


CN_TO_EN = _load_cn_to_en()

# 技术统计指标 -> 字段前缀（中文表头 -> 列名前缀）
STAT_METRICS = {
    "进攻次数": "attack",
    "危险进攻": "danger",
    "射门次数": "shots",
    "射正次数": "shots_on",
    "任意球": "fk",
    "角球": "corners",
    "越位": "offsides",
    "犯规": "fouls",
    "黄牌": "yellow",
    "红牌": "red",
    "控球率": "possession",
}


class Client:
    """带浏览器特征请求头的 HTTP 客户端，规避 500.com 反爬。"""

    def __init__(self, delay=0.5, cookies=None):
        if curl_requests is not None:
            self.session = curl_requests.Session(impersonate="chrome")
        else:
            self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": UA,
            "Accept-Language": "zh-CN,zh;q=0.9",
            "X-Requested-With": "XMLHttpRequest",
        })
        self.delay = delay
        self._last_req = 0.0
        self._load_cookies(cookies)

    def _load_cookies(self, path):
        """从 JSON 文件加载手动导出的 Cookie（绕过腾讯云 EdgeOne 人机验证）。

        path 为空时用默认 data/cookies_500.json；不存在则跳过（不阻断无 cookie 场景）。
        支持两种格式：
          A) {"name": "value", ...}  —— 应用到 odds.500.com 与 liansai.500.com
          B) [{"name":..,"value":..,"domain":..,"path":..}, ...] —— 逐条设置
        """
        if not path:
            path = COOKIE_FILE
        if not path or not Path(path).exists():
            return
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[警告] Cookie 文件解析失败，已忽略: {e}")
            return
        domains = ("odds.500.com", "liansai.500.com", ".500.com")
        if isinstance(data, dict):
            ua_override = data.get("__user_agent")
            if ua_override:
                self.session.headers["User-Agent"] = ua_override
            for name, value in data.items():
                if name == "__user_agent":
                    continue
                for d in domains:
                    self.session.cookies.set(name, value, domain=d, path="/")
        elif isinstance(data, list):
            for c in data:
                self.session.cookies.set(
                    c.get("name"), c.get("value"),
                    domain=c.get("domain"), path=c.get("path", "/"),
                )
        print(f"[Cookie] 已加载 {len(data)} 条 Cookie")

    def _throttle(self):
        wait = self.delay - (time.time() - self._last_req)
        if wait > 0:
            time.sleep(wait)

    def get(self, url, referer="https://www.500.com/"):
        last_exc = None
        for attempt in range(3):
            self._throttle()
            headers = {"Referer": referer}
            try:
                r = self.session.get(url, headers=headers, timeout=20)
                r.encoding = "gb18030"
                return r
            except Exception as e:
                # 瞬时断连/超时（如切热点导致 IP 变更）自动重试，避免整个进程崩溃
                last_exc = e
                self._last_req = time.time()
                if attempt < 2:
                    time.sleep(2 * (attempt + 1))
                    continue
            finally:
                self._last_req = time.time()
        raise last_exc


# ----------------------------------------------------------------------
# 赛程枚举
# ----------------------------------------------------------------------
def fetch_round_matches(client, stid, round_no):
    """调用 getmatch 接口，返回某一轮的所有比赛 dict 列表。"""
    url = f"{SCHED_URL}&stid={stid}&round={round_no}"
    r = client.get(url, referer="https://liansai.500.com/")
    if r.status_code != 200:
        return None
    try:
        data = r.json()
    except Exception:
        return None
    if not isinstance(data, list):
        return None
    return data


def fetch_season_matches(client, stid, rounds):
    """枚举赛季指定轮次的全部比赛。"""
    matches = []
    for rnd in rounds:
        items = fetch_round_matches(client, stid, rnd)
        if items is None:
            print(f"  [warn] round {rnd} 获取失败")
            continue
        for it in items:
            it["_stid"] = stid
            it["_round"] = rnd
            matches.append(it)
    return matches


# ----------------------------------------------------------------------
# 页面解析
# ----------------------------------------------------------------------
def _norm_team(cn):
    cn = (cn or "").strip()
    return CN_TO_EN.get(cn, cn)


def _td_text(tr, idx):
    cells = tr.find_all(["td", "th"])
    if idx < len(cells):
        return cells[idx].get_text(strip=True)
    return ""


# 反爬拦截页特征关键字（腾讯云 EdgeOne 人机验证）
_ANTIBOT_MARKERS = (
    "Security Verification",
    "Protected by Tencent Cloud EdgeOne",
    "TEOCaptchaWidget",
    "captcha.eo.gtimg.com",
    "t.captcha.qq.com",
)


def _is_anti_bot_page(soup):
    """检测页面是否为 EdgeOne 反爬人机验证页。"""
    html = str(soup) if soup else ""
    return any(m in html for m in _ANTIBOT_MARKERS)


def parse_touzhu(soup):
    """解析投注分析页 -> 热度分析 (必发/冷热/盈亏)。

    返回 dict，新增特殊键 _crawl_status:
      ok                - 解析成功且检测到有效数据行
      anti_bot_blocked  - 被 500.com EdgeOne 反爬拦截（需刷新 Cookie 重采），
                          包括：直接返回验证页、或返回表格但数值全"-"的空壳降级页
      source_no_data    - 页面返回正常但无数据表格（源站未提供该场投注数据）
      crawl_parse_failed- 页面结构异常，解析失败（如 class 变更 / 网络错误）
    数据按表内固定顺序排列: 主胜 -> 平局 -> 客胜。
    """
    out = {"_crawl_status": "crawl_parse_failed"}
    # 先判定反爬验证页（整页只有 Security Verification，无业务内容）
    if _is_anti_bot_page(soup):
        out["_crawl_status"] = "anti_bot_blocked"
        out["tips"] = "500.com EdgeOne 反爬拦截，请刷新 Cookie 后重新采集"
        return out
    table = soup.select_one("table.bif-yab")
    if table is None:
        # 检查是否是正常页面但无表格（源站无数据）：如果页面有「投注分析」字样但无表格则为 source_no_data
        page_text = soup.get_text("", strip=True)
        if "投注分析" in page_text or "必发" in page_text:
            out["_crawl_status"] = "source_no_data"
        return out
    data_rows = []
    tips = []
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) == 11 and cells[0] != "":
            data_rows.append(cells)
        elif cells and "数据提点" in cells[0]:
            tips.append(" ".join(cells[1:]))
    roles = ["home", "draw", "away"]
    for i, cells in enumerate(data_rows[:3]):
        out[roles[i]] = {
            "name": cells[0], "odds": cells[1], "prob": cells[2],
            "bf_ratio": cells[4], "bf_price": cells[5],
            "bf_volume": cells[6], "bf_profit": cells[7],
            "bf_index": cells[8], "hot_index": cells[9],
            "profit_index": cells[10],
        }
    if data_rows:
        out["home_name"] = data_rows[0][0]
        out["draw_name"] = "平局"
        out["away_name"] = data_rows[2][0] if len(data_rows) >= 3 else ""
        # P2-05 修正: 空壳行判定 —— 表格存在但数值列（赔率/概率/必发成交等）
        # 全是 "-" 或空 → 判定为 anti_bot_blocked（EdgeOne 反爬降级页/空壳页），
        # 而非源站无数据。超深盘不会所有列全空（至少赔率/概率会有值）。
        def _row_has_real_value(cells):
            # index 0 是队名，1~10 是数值列（赔率/概率/盈亏/冷热等）
            return any(c.strip() not in ("", "-", "—") for c in cells[1:])
        if any(_row_has_real_value(r) for r in data_rows[:3]):
            out["_crawl_status"] = "ok"
        else:
            out["_crawl_status"] = "anti_bot_blocked"
            if not out.get("tips"):
                out["tips"] = "投注数据为空壳（疑似 500.com EdgeOne 反爬拦截，请刷新 Cookie 后重新采集）"
    else:
        # 有表格但无有效数据行 → 源站空表
        out["_crawl_status"] = "source_no_data"
    out["tips"] = " | ".join([t for t in tips if t])
    return out


def _by_id(soup, id_prefix):
    el = soup.find("td", id=re.compile(rf"^{id_prefix}\d*$"))
    return el.get_text(strip=True) if el else None


def parse_ouzhi(soup):
    """解析百家欧指页 -> 平均值/离散值/公司数 摘要 + 逐公司明细。"""
    out = {"summary": {}, "companies": []}

    # 公司数
    n = soup.find(id="nowcnum")
    if n:
        out["summary"]["company_count"] = n.get_text(strip=True)

    def av(key):
        return _by_id(soup, key)

    s = out["summary"]
    # 平均值
    s["avg_init"] = [av("avwinc"), av("avdrawc"), av("avlostc")]
    s["avg_live"] = [av("avwinj"), av("avdrawj"), av("avlostj")]
    s["avg_prob_init"] = [av("avwinlc"), av("avdrawlc"), av("avlostlc")]
    s["avg_prob_live"] = [av("avwinlj"), av("avdrawlj"), av("avlostlj")]
    s["avg_return_init"] = av("avpaylc")
    s["avg_return_live"] = av("avpaylj")
    s["avg_kelly_init"] = [av("avklwc"), av("avkldc"), av("avkllc")]
    s["avg_kelly_live"] = [av("avklwj"), av("avkldj"), av("avkllj")]
    # 离散值
    s["disp_init"] = [av("lswc"), av("lsdc"), av("lslc")]
    s["disp_live"] = [av("lswj"), av("lsdj"), av("lslj")]

    # 逐公司明细
    datatb = soup.select_one("table#datatb")
    if datatb is not None:
        for tr in datatb.find_all("tr", class_=re.compile(r"tr\d+")):
            direct_td = tr.find_all("td", recursive=False)
            if len(direct_td) < 3:
                continue
            seq = direct_td[0].get_text(strip=True)
            name_td = direct_td[1]
            company = name_td.get("title") or name_td.get_text(strip=True)
            nested = [td.find("table") for td in direct_td]

            def _rows(nested_table, ncols):
                if nested_table is None:
                    return [[None] * ncols, [None] * ncols]
                trs = nested_table.find_all("tr")
                res = []
                for tr_ in trs[:2]:
                    vals = [td.get_text(strip=True) for td in tr_.find_all("td")]
                    while len(vals) < ncols:
                        vals.append(None)
                    res.append(vals[:ncols])
                while len(res) < 2:
                    res.append([None] * ncols)
                return res

            odds = _rows(nested[2], 3) if len(nested) > 2 else [[None]*3, [None]*3]
            prob = _rows(nested[3], 3) if len(nested) > 3 else [[None]*3, [None]*3]
            retn = _rows(nested[4], 1) if len(nested) > 4 else [[None], [None]]
            kelly = _rows(nested[5], 3) if len(nested) > 5 else [[None]*3, [None]*3]

            out["companies"].append({
                "seq": seq, "company": company,
                "init": odds[0], "live": odds[1],
                "prob_init": prob[0], "prob_live": prob[1],
                "return_init": retn[0][0], "return_live": retn[1][0],
                "kelly_init": kelly[0], "kelly_live": kelly[1],
            })
    return out


def parse_stat(soup):
    """解析技术统计页 -> 危险进攻/进攻次数等指标。"""
    out = {}
    container = soup.select_one("div.team-statis")
    if container is None:
        return out
    table = container.find("table")
    if table is None:
        return out
    for tr in table.find_all("tr"):
        cells = [td.get_text(strip=True) for td in tr.find_all(["td", "th"])]
        if len(cells) < 5:
            continue
        metric = cells[2]
        if metric in STAT_METRICS:
            prefix = STAT_METRICS[metric]
            out[f"home_{prefix}"] = cells[1]
            out[f"away_{prefix}"] = cells[3]
    return out


# ----------------------------------------------------------------------
# 数据库写入
# ----------------------------------------------------------------------
def create_tables(conn):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS odds500_match (
        fid INTEGER PRIMARY KEY,
        match_id TEXT,
        league TEXT,
        home_team_cn TEXT, away_team_cn TEXT,
        home_team_en TEXT, away_team_en TEXT,
        match_date TEXT, match_time TEXT,
        season TEXT, round INTEGER, stid INTEGER,
        home_score INTEGER, away_score INTEGER,
        status INTEGER,
        win REAL, draw REAL, lost REAL,
        handicap TEXT, pan TEXT
    );

    CREATE TABLE IF NOT EXISTS odds500_betting (
        fid INTEGER PRIMARY KEY,
        match_id TEXT,
        home_name TEXT, draw_name TEXT, away_name TEXT,
        home_odds TEXT, home_prob TEXT, home_bf_ratio TEXT, home_bf_price TEXT,
        home_bf_volume TEXT, home_bf_profit TEXT, home_hot TEXT, home_profit_index TEXT,
        draw_odds TEXT, draw_prob TEXT, draw_bf_ratio TEXT, draw_bf_price TEXT,
        draw_bf_volume TEXT, draw_bf_profit TEXT, draw_hot TEXT, draw_profit_index TEXT,
        away_odds TEXT, away_prob TEXT, away_bf_ratio TEXT, away_bf_price TEXT,
        away_bf_volume TEXT, away_bf_profit TEXT, away_hot TEXT, away_profit_index TEXT,
        tips TEXT, crawl_status TEXT, fetched_at TEXT
    );

    CREATE TABLE IF NOT EXISTS odds500_ouzhi_summary (
        fid INTEGER PRIMARY KEY,
        match_id TEXT,
        company_count TEXT,
        avg_init_win TEXT, avg_init_draw TEXT, avg_init_lose TEXT,
        avg_live_win TEXT, avg_live_draw TEXT, avg_live_lose TEXT,
        avg_prob_init_win TEXT, avg_prob_init_draw TEXT, avg_prob_init_lose TEXT,
        avg_prob_live_win TEXT, avg_prob_live_draw TEXT, avg_prob_live_lose TEXT,
        avg_return_init TEXT, avg_return_live TEXT,
        avg_kelly_init_win TEXT, avg_kelly_init_draw TEXT, avg_kelly_init_lose TEXT,
        avg_kelly_live_win TEXT, avg_kelly_live_draw TEXT, avg_kelly_live_lose TEXT,
        disp_init_win TEXT, disp_init_draw TEXT, disp_init_lose TEXT,
        disp_live_win TEXT, disp_live_draw TEXT, disp_live_lose TEXT,
        fetched_at TEXT
    );

    CREATE TABLE IF NOT EXISTS odds500_ouzhi_company (
        fid INTEGER, match_id TEXT, seq INTEGER, company TEXT,
        init_win TEXT, init_draw TEXT, init_lose TEXT,
        live_win TEXT, live_draw TEXT, live_lose TEXT,
        prob_init_win TEXT, prob_init_draw TEXT, prob_init_lose TEXT,
        prob_live_win TEXT, prob_live_draw TEXT, prob_live_lose TEXT,
        return_init TEXT, return_live TEXT,
        kelly_init_win TEXT, kelly_init_draw TEXT, kelly_init_lose TEXT,
        kelly_live_win TEXT, kelly_live_draw TEXT, kelly_live_lose TEXT,
        PRIMARY KEY (fid, seq)
    );

    CREATE TABLE IF NOT EXISTS odds500_stat (
        fid INTEGER PRIMARY KEY,
        match_id TEXT,
        home_attack TEXT, away_attack TEXT,
        home_danger TEXT, away_danger TEXT,
        home_shots TEXT, away_shots TEXT,
        home_shots_on TEXT, away_shots_on TEXT,
        home_fk TEXT, away_fk TEXT,
        home_corners TEXT, away_corners TEXT,
        home_offsides TEXT, away_offsides TEXT,
        home_fouls TEXT, away_fouls TEXT,
        home_yellow TEXT, away_yellow TEXT,
        home_red TEXT, away_red TEXT,
        home_possession TEXT, away_possession TEXT,
        fetched_at TEXT
    );
    """)

    # 迁移：旧库 odds500_match 若缺 league 列则补加，并回填既有英超数据
    cols = [r[1] for r in conn.execute("PRAGMA table_info(odds500_match)")]
    if "league" not in cols:
        conn.execute("ALTER TABLE odds500_match ADD COLUMN league TEXT")
        conn.execute("UPDATE odds500_match SET league='英超' WHERE league IS NULL")
    conn.commit()


def _pick(d, key):
    return d.get(key)


def write_match(conn, match):
    conn.execute(
        """INSERT OR REPLACE INTO odds500_match
           (fid, match_id, league, home_team_cn, away_team_cn, home_team_en, away_team_en,
            match_date, match_time, season, round, stid, home_score, away_score,
            status, win, draw, lost, handicap, pan)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (match["fid"], match["match_id"], match["league"],
         match["home_cn"], match["away_cn"],
         match["home_en"], match["away_en"],
         match["date"], match["time"], match["season"], match["round"], match["stid"],
         match.get("hscore"), match.get("gscore"),
         match.get("status"), match.get("win"), match.get("draw"), match.get("lost"),
         match.get("handicap"), match.get("pan")),
    )


def write_betting(conn, fid, match_id, parsed):
    def role(side, sub):
        r = parsed.get(side, {})
        return r.get(sub) if isinstance(r, dict) else None

    # (db 列后缀, 解析 dict 键)
    sub_fields = [
        ("odds", "odds"), ("prob", "prob"),
        ("bf_ratio", "bf_ratio"), ("bf_price", "bf_price"),
        ("bf_volume", "bf_volume"), ("bf_profit", "bf_profit"),
        ("hot", "hot_index"), ("profit_index", "profit_index"),
    ]
    cols = ["fid", "match_id", "home_name", "draw_name", "away_name"]
    vals = [fid, match_id,
            parsed.get("home_name"), parsed.get("draw_name"), parsed.get("away_name")]
    for side in ("home", "draw", "away"):
        for col_suf, parse_key in sub_fields:
            cols.append(f"{side}_{col_suf}")
            vals.append(role(side, parse_key))
    # P1-17: 采集状态标记 ok / anti_bot_blocked / source_no_data / crawl_parse_failed
    cols += ["tips", "crawl_status", "fetched_at"]
    vals += [parsed.get("tips"), parsed.get("_crawl_status"), datetime.now().isoformat()]

    marks = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT OR REPLACE INTO odds500_betting ({', '.join(cols)}) VALUES ({marks})",
        vals,
    )


def _tri(d, key, i):
    v = d.get(key)
    if v is None or i >= len(v):
        return None
    return v[i]


def _insert(conn, table, cols, vals):
    marks = ", ".join("?" for _ in cols)
    conn.execute(
        f"INSERT OR REPLACE INTO {table} ({', '.join(cols)}) VALUES ({marks})",
        vals,
    )


def write_ouzhi(conn, fid, match_id, parsed):
    s = parsed.get("summary", {})
    cols = ["fid", "match_id", "company_count"]
    vals = [fid, match_id, s.get("company_count")]
    # 三元组 (欧赔/概率/凯利/离散) 各含 初盘 + 即时, 每项 win/draw/lose
    for dbp, pk in [("avg_init", "avg_init"), ("avg_live", "avg_live"),
                    ("avg_prob_init", "avg_prob_init"), ("avg_prob_live", "avg_prob_live"),
                    ("avg_kelly_init", "avg_kelly_init"), ("avg_kelly_live", "avg_kelly_live"),
                    ("disp_init", "disp_init"), ("disp_live", "disp_live")]:
        for j, suf in enumerate(("win", "draw", "lose")):
            cols.append(f"{dbp}_{suf}")
            vals.append(_tri(s, pk, j))
    cols += ["avg_return_init", "avg_return_live", "fetched_at"]
    vals += [s.get("avg_return_init"), s.get("avg_return_live"), datetime.now().isoformat()]
    _insert(conn, "odds500_ouzhi_summary", cols, vals)

    for c in parsed.get("companies", []):
        try:
            seq = int(c["seq"])
        except (ValueError, TypeError):
            continue
        cols = ["fid", "match_id", "seq", "company"]
        vals = [fid, match_id, seq, c["company"]]
        for dbp, key in [("init", "init"), ("live", "live"),
                         ("prob_init", "prob_init"), ("prob_live", "prob_live"),
                         ("kelly_init", "kelly_init"), ("kelly_live", "kelly_live")]:
            for j, suf in enumerate(("win", "draw", "lose")):
                cols.append(f"{dbp}_{suf}")
                vals.append(_tri(c, key, j))
        cols += ["return_init", "return_live"]
        vals += [c.get("return_init"), c.get("return_live")]
        _insert(conn, "odds500_ouzhi_company", cols, vals)


def write_stat(conn, fid, match_id, parsed):
    cols = ["fid", "match_id"]
    vals = [fid, match_id]
    for key in STAT_METRICS.values():
        cols.append(f"home_{key}")
        vals.append(parsed.get(f"home_{key}"))
        cols.append(f"away_{key}")
        vals.append(parsed.get(f"away_{key}"))
    cols.append("fetched_at")
    vals.append(datetime.now().isoformat())
    _insert(conn, "odds500_stat", cols, vals)


# ----------------------------------------------------------------------
# 单个 match 的采集
# ----------------------------------------------------------------------
def is_already_collected(conn, fid, match_id, is_finished, pages):
    """判断该场是否已完整采集，用于断点续采跳过（避免重跑全量）。"""
    if conn is None:
        return False
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM odds500_match WHERE fid=? LIMIT 1", (fid,))
    if not cur.fetchone():
        return False
    if "touzhu" in pages:
        cur.execute("SELECT 1 FROM odds500_betting WHERE match_id=? LIMIT 1", (match_id,))
        if not cur.fetchone():
            return False
    if "ouzhi" in pages:
        cur.execute("SELECT 1 FROM odds500_ouzhi_summary WHERE match_id=? LIMIT 1", (match_id,))
        if not cur.fetchone():
            return False
    if "stat" in pages and is_finished:
        cur.execute("SELECT 1 FROM odds500_stat WHERE match_id=? LIMIT 1", (match_id,))
        if not cur.fetchone():
            return False
    return True


def collect_match(client, conn, item, season, league, pages):
    fid = item["fid"]

    # 状态过滤：status==5 为已完赛（有真实比分），其余为未开赛/进行中。
    # 未开赛场次跳过赛后技术统计，且不写入 0:0 占位比分（改 NULL，避免污染库）。
    # 调试模式（--test）未传 status，按完赛处理以保留原有全量抓取行为。
    is_finished = (item.get("status") == 5) or (item.get("status") is None)

    home_cn = _normalize_team_name((item.get("hname") or "").strip())
    away_cn = _normalize_team_name((item.get("gname") or "").strip())
    home_en = _norm_team((item.get("hname") or "").strip())
    away_en = _norm_team((item.get("gname") or "").strip())
    stime = (item.get("stime") or " ").strip()
    date, _, time_ = stime.partition(" ")
    match_id = f"{date}_{home_en}_{away_en}"

    match = {
        "fid": fid, "match_id": match_id,
        "league": league,
        "home_cn": home_cn, "away_cn": away_cn,
        "home_en": home_en, "away_en": away_en,
        "date": date, "time": time_, "season": season,
        "round": item.get("_round"), "stid": item.get("_stid"),
        "hscore": item.get("hscore") if is_finished else None,
        "gscore": item.get("gscore") if is_finished else None,
        "status": item.get("status"),
        "win": item.get("win"), "draw": item.get("draw"), "lost": item.get("lost"),
        "handicap": item.get("handline"), "pan": item.get("pan"),
    }
    if conn is not None:
        write_match(conn, match)

    result = {"match": match, "betting": None, "ouzhi": None, "stat": None}

    if "touzhu" in pages:
        try:
            r = client.get(FENXI_URL.format(page="touzhu", fid=fid), referer="https://odds.500.com/fenxi/")
            if r.status_code == 200:
                parsed = parse_touzhu(BeautifulSoup(r.text, "html.parser"))
                result["betting"] = parsed
            else:
                result["betting"] = {"_crawl_status": "crawl_parse_failed", "_error": f"HTTP {r.status_code}"}
        except Exception as e:
            result["betting"] = {"_crawl_status": "crawl_parse_failed", "_error": str(e)}

    if "ouzhi" in pages:
        r = client.get(FENXI_URL.format(page="ouzhi", fid=fid), referer="https://odds.500.com/fenxi/")
        if r.status_code == 200:
            result["ouzhi"] = parse_ouzhi(BeautifulSoup(r.text, "html.parser"))

    if "stat" in pages and is_finished:
        r = client.get(FENXI_URL.format(page="stat", fid=fid), referer="https://odds.500.com/fenxi/")
        if r.status_code == 200:
            result["stat"] = parse_stat(BeautifulSoup(r.text, "html.parser"))

    if conn is not None:
        # P1-17: betting 有结果就写（含 crawl_status 标记，区分 ok/source_no_data/crawl_parse_failed）
        if result["betting"] is not None:
            write_betting(conn, fid, match_id, result["betting"])
        if result["ouzhi"]:
            write_ouzhi(conn, fid, match_id, result["ouzhi"])
        if result["stat"]:
            write_stat(conn, fid, match_id, result["stat"])

    return result


# ----------------------------------------------------------------------
# 赛前亚盘/赔率刷新
# ----------------------------------------------------------------------
def refresh_upcoming_odds(client, conn, season, league, stid, rounds):
    """重枚举赛程，补齐/更新亚盘盘口（handline）与相关赔率。

    背景：整季赛程首次入库时，多数未来场次盘口尚未开出（getmatch 的 handline 为空），
    之后 `is_already_collected` 因行已存在而跳过，导致盘口开出后一直不刷新。

    本函数只走 getmatch 赛程接口（不逐场抓 touzhu/ouzhi/stat），规则：
      - 盘口未开出（handline 为空）→ 跳过
      - 未开赛/进行中（status != 5）→ 仅当库里盘口为空时补齐 handicap/pan/win/draw/lost/status
      - 已完赛（status == 5）→ 用结算值直接覆盖 handicap/pan/win/draw/lost + 比分 + status
        （完赛场次的亚盘是结算盘口，最有价值，必须补全而非跳过；同时纠正 stale 的 status）

    注：500.com 的 handline 在赔率开出后对未开赛场次同样返回（不限于完赛），
    故本函数对未开赛场次也能在盘口开出后补齐，无需逐场抓页面。
    """
    items = fetch_season_matches(client, stid, rounds)
    updated = 0
    skipped = 0
    for it in items:
        fid = it.get("fid")
        handline = (it.get("handline") or "").strip() or None
        if not fid or not handline:  # 盘口未开出则跳过
            skipped += 1
            continue
        pan = (it.get("pan") or "").strip() or None
        status = it.get("status")
        if status == 5:  # 已完赛：结算盘口完整覆盖（含比分、状态迁移）
            cur = conn.execute(
                "UPDATE odds500_match SET handicap=?, pan=?, win=?, draw=?, lost=?, "
                "status=?, home_score=?, away_score=? WHERE fid=?",
                (handline, pan, it.get("win"), it.get("draw"), it.get("lost"),
                 status, it.get("hscore"), it.get("gscore"), fid),
            )
        else:  # 未开赛/进行中：仅填库里仍为空的盘口，不覆盖已采集值
            cur = conn.execute(
                "UPDATE odds500_match SET handicap=?, pan=?, win=?, draw=?, lost=?, status=? "
                "WHERE fid=? AND (handicap IS NULL OR handicap='')",
                (handline, pan, it.get("win"), it.get("draw"), it.get("lost"),
                 status, fid),
            )
        updated += cur.rowcount
    print(f"  [亚盘刷新] {season} {league}: 更新 {updated} 场（跳过 {skipped} 场未开盘）")
    return updated


# ----------------------------------------------------------------------
# 主流程
# ----------------------------------------------------------------------
def _print_result(result):
    m = result["match"]
    print(f"\n=== fid={m['fid']}  {m['home_cn']} vs {m['away_cn']}  ({m['date']} {m['time']}) ===")
    print(f"  match_id = {m['match_id']}")

    b = result["betting"]
    if b:
        print("  [投注分析]")
        for role, label in [("home", m["home_cn"]), ("draw", "平局"), ("away", m["away_cn"])]:
            if role in b:
                r = b[role]
                print(f"    {label}: 指数={r['odds']} 概率={r['prob']} 必发比例={r['bf_ratio']} "
                      f"成交价={r['bf_price']} 成交量={r['bf_volume']} 盈亏={r['bf_profit']} "
                      f"冷热={r['hot_index']} 盈亏指数={r['profit_index']}")
        if b.get("tips"):
            print(f"    数据提点: {b['tips']}")

    o = result["ouzhi"]
    if o:
        s = o["summary"]
        print(f"  [百家欧指] 公司数={s.get('company_count')}")
        print(f"    平均即时: {s.get('avg_live')}  平均初盘: {s.get('avg_init')}")
        print(f"    即时概率: {s.get('avg_prob_live')}  返还率: {s.get('avg_return_init')}->{s.get('avg_return_live')}")
        print(f"    离散(初): {s.get('disp_init')}  离散(即): {s.get('disp_live')}")
        print(f"    公司明细: {len(o['companies'])} 家")

    st = result["stat"]
    if st:
        print("  [技术统计]")
        for label, key in STAT_METRICS.items():
            print(f"    {label}: {st.get('home_'+key)} vs {st.get('away_'+key)}")


def main():
    ap = argparse.ArgumentParser(description="500.com 数据分析采集器")
    ap.add_argument("--season", default="all", help="16/17 ~ 25/26 / all")
    ap.add_argument("--league", default="all", help="英超/西甲/意甲/德甲/法甲 / all")
    ap.add_argument("--rounds", default="all", help="如 1-38 或 1,2,3 或 all")
    ap.add_argument("--limit", type=int, default=0, help="最多采集场次数 (0=不限)")
    ap.add_argument("--pages", default="touzhu,ouzhi,stat")
    ap.add_argument("--test", type=int, default=0, help="单场调试: 传入 fid (只打印，不写库)")
    ap.add_argument("--delay", type=float, default=0.5)
    ap.add_argument("--cookies", default=str(COOKIE_FILE),
                    help="手动导出的 Cookie JSON 路径（默认 data/cookies_500.json）")
    ap.add_argument("--skip-existing", dest="skip_existing", action="store_true", default=True,
                    help="跳过已采集场次（默认开启，断点续采）")
    ap.add_argument("--no-skip-existing", dest="skip_existing", action="store_false",
                    help="强制全量重采（不跳过已采集场次）")
    ap.add_argument("--refresh-odds", action="store_true",
                    help="仅刷新未开赛场次的亚盘/欧赔（只走赛程接口，不抓 touzhu/ouzhi/stat）")
    args = ap.parse_args()

    pages = [p.strip() for p in args.pages.split(",") if p.strip()]
    client = Client(delay=args.delay, cookies=args.cookies)

    # 单场调试模式
    if args.test:
        item = {
            "fid": args.test,
            "hname": "", "gname": "",
            "stime": "", "hscore": None, "gscore": None, "status": None,
            "win": None, "draw": None, "lost": None, "handline": None, "pan": None,
            "_round": None, "_stid": None,
        }
        result = collect_match(client, None, item, "test", "英超", pages)
        _print_result(result)
        return

    if args.season == "all":
        season_names = list(SEASONS.keys())
    else:
        season_names = [args.season]

    def parse_rounds(spec, max_round):
        if spec == "all":
            return list(range(1, max_round + 1))
        out = []
        for part in spec.split(","):
            part = part.strip()
            if "-" in part:
                a, b = part.split("-")
                out.extend(range(int(a), int(b) + 1))
            elif part:
                out.append(int(part))
        return out

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA busy_timeout=30000")
    create_tables(conn)

    if args.refresh_odds:
        for season in season_names:
            league_configs = SEASONS[season]
            if args.league != "all":
                league_configs = {args.league: league_configs[args.league]}
            for league, (stid, max_round) in league_configs.items():
                rounds = parse_rounds(args.rounds, max_round)
                refresh_upcoming_odds(client, conn, season, league, stid, rounds)
        conn.commit()
        conn.close()
        print(f"\n亚盘/赔率刷新完成，数据已写入 {DB_PATH}")
        return

    total = 0
    for season in season_names:
        league_configs = SEASONS[season]
        if args.league != "all":
            league_configs = {args.league: league_configs[args.league]}
        for league, (stid, max_round) in league_configs.items():
            rounds = parse_rounds(args.rounds, max_round)
            print(f"\n>>> 采集赛季 {season} {league} (stid={stid}) 轮次 {rounds[0]}-{rounds[-1]}")
            matches = fetch_season_matches(client, stid, rounds)
            print(f"    枚举到 {len(matches)} 场比赛")
            collected = 0
            finished = 0
            for item in matches:
                if args.limit and total >= args.limit:
                    break
                # 断点续采：跳过已完整入库的场次（四表齐全）
                if args.skip_existing:
                    _home_cn = (item.get("hname") or "").strip()
                    _away_cn = (item.get("gname") or "").strip()
                    _stime = (item.get("stime") or " ").strip()
                    _date = _stime.partition(" ")[0]
                    _mid = f"{_date}_{_norm_team(_home_cn)}_{_norm_team(_away_cn)}"
                    _finished = (item.get("status") == 5)
                    if is_already_collected(conn, item["fid"], _mid, _finished, pages):
                        print(f"  [{item['fid']}] {_home_cn} vs {_away_cn} skip（已采集）")
                        continue
                result = collect_match(client, conn, item, season, league, pages)
                m = result["match"]
                bets = "1" if result["betting"] else "0"
                ouz = "1" if result["ouzhi"] else "0"
                sta = "1" if result["stat"] else "0"
                played = "已赛" if m["status"] == 5 else "未赛"
                if m["status"] == 5:
                    finished += 1
                print(f"  [{m['fid']}] {m['home_cn']} vs {m['away_cn']} {played} "
                      f"投注={bets} 欧指={ouz} 技术={sta}")
                collected += 1
                total += 1
            conn.commit()
            print(f"    {league} {season} 采集 {collected} 场（已赛 {finished} / 未赛 {collected - finished}）")

    conn.close()
    print(f"\n完成，共采集 {total} 场，数据已写入 {DB_PATH}")


if __name__ == "__main__":
    main()