"""
sporttery.cn 竞彩足球时序赔率采集器 (v2 - 直接写入 odds.db)
===========================================================
功能：从中国体育彩票官网采集历史赛季五大联赛的详细时序赔率数据
数据源：https://www.sporttery.cn/jc/zqsgkj/
写入目标：data/odds.db（与现有 2025-2026 赛季数据完全兼容）

兼容性保证：
  - match_id 格式: {date}_{home_team}_{away_team}（与现有数据一致）
  - match_type 格式: {联赛简称}{season}赛季（如 英超2024-2025赛季）
  - actual_wdl: 胜/平/负（非 主胜/客胜/平局）
  - handicap_history: 无 goal_line 列（与现有表结构一致）
  - 表名: wdl_history / handicap_history / total_goals_history / score_history
  - 采集时间范围: 5月~次年8月（覆盖整个赛季窗口）

使用方法：
  python scripts/sporttery_collector.py --league 英超 --season 2024-2025
  python scripts/sporttery_collector.py --league all --season 2024-2025
"""

import asyncio
import json
import os
import sqlite3
import sys
import argparse
from datetime import datetime, timedelta
from pathlib import Path

# 后台重定向到文件时强制行缓冲，避免日志块缓冲导致看不到实时进度
try:
    sys.stdout.reconfigure(line_buffering=True)
    sys.stderr.reconfigure(line_buffering=True)
except Exception:
    pass

# ============================================================
# 配置
# ============================================================

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DB_PATH = DATA_DIR / "odds.db"
COLLECTED_DATA_DIR = DATA_DIR / "sporttery_collected"
COLLECTED_DATA_DIR.mkdir(parents=True, exist_ok=True)

# 队名归一化：统一到 feature_utils 规范中文名，保证 match_id 与 500.com/SofaScore 一致
try:
    from feature_utils import normalize_team_name as _normalize_team_name
except Exception:  # 依赖缺失时回退原样，不阻断采集
    _normalize_team_name = lambda s: s

# 五大联赛在 sporttery.cn 的 leagueId
LEAGUE_IDS = {
    "英超": 25, "意甲": 40, "西甲": 62, "德甲": 37, "法甲": 32,
}

# 赛季 → 采集日期范围（8月~次年5月，覆盖实际比赛期）
# 对标 Understat / SofaScore 五大联赛 10 季（16/17 ~ 25/26）
# 20/21 因疫情延迟开赛（法甲 8/22，西甲/英超 9/12，意甲 9/19，德甲 9/18），start 取 8/1 已覆盖
# 法甲 16/17~22/23 为 20 队 38 轮（19/20 因疫情 4 月提前结束），23/24 起 18 队 34 轮
SEASON_RANGES = {
    "2016-2017": {"start": "2016-08-01", "end": "2017-05-31"},
    "2017-2018": {"start": "2017-08-01", "end": "2018-05-31"},
    "2018-2019": {"start": "2018-08-01", "end": "2019-05-31"},
    "2019-2020": {"start": "2019-08-01", "end": "2020-05-31"},
    "2020-2021": {"start": "2020-08-01", "end": "2021-05-31"},
    "2021-2022": {"start": "2021-08-01", "end": "2022-05-31"},
    "2022-2023": {"start": "2022-08-01", "end": "2023-05-31"},
    "2023-2024": {"start": "2023-08-01", "end": "2024-05-31"},
    "2024-2025": {"start": "2024-08-01", "end": "2025-05-31"},
    "2025-2026": {"start": "2025-08-01", "end": "2026-05-31"},
    "2026-2027": {"start": "2026-08-01", "end": "2027-05-31"},
}

# API URL
MATCH_LIST_API = "https://webapi.sporttery.cn/gateway/uniform/football/getUniformMatchResultV1.qry"
FIXED_BONUS_API = "https://webapi.sporttery.cn/gateway/uniform/football/getFixedBonusV1.qry"
SPORTTERY_HOME = "https://www.sporttery.cn/jc/zqsgkj/"

REQUEST_DELAY = 1.0
BATCH_DELAY = 3.0


# ============================================================
# 数据库操作（直接写入 odds.db）
# ============================================================

def compute_actual_handicap(handicap: float, full_score: str) -> str:
    """根据让球值和比分计算让球赛果，如 (-1)胜"""
    if handicap == 0 or not full_score or ":" not in full_score:
        return ""
    try:
        h, a = map(int, full_score.split(":"))
        h_adj = h + handicap
        sign = "+" if handicap > 0 else ""
        hstr = f"({sign}{int(handicap)})"
        if h_adj > a:
            return f"{hstr}胜"
        elif h_adj == a:
            return f"{hstr}平"
        else:
            return f"{hstr}负"
    except:
        return ""


def build_odds_match_id(match_date: str, home_team: str, away_team: str) -> str:
    """构建 odds.db 格式的 match_id: {date}_{home}_{away}"""
    return f"{match_date}_{home_team}_{away_team}"


def build_match_type(league_abbr: str, season: str) -> str:
    """构建 match_type: {联赛简称}{season}赛季"""
    return f"{league_abbr}{season}赛季"


def save_to_odds_db(match_data: dict, season: str, league_abbr: str) -> bool:
    """保存比赛数据到 odds.db，格式与现有 2025-2026 数据完全兼容"""
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    home_team = _normalize_team_name(match_data.get("home_team", ""))
    away_team = _normalize_team_name(match_data.get("away_team", ""))
    odds_match_id = build_odds_match_id(
        match_data["match_date"],
        home_team,
        away_team
    )

    # 映射 actual_wdl: 主胜→胜, 客胜→负, 平局→平
    wdl_map = {"主胜": "胜", "客胜": "负", "平局": "平"}
    actual_wdl = wdl_map.get(match_data.get("actual_wdl", ""), "")

    # 计算 actual_handicap
    actual_handicap = compute_actual_handicap(
        match_data.get("handicap", 0),
        match_data.get("actual_score", "")
    )

    match_type = build_match_type(league_abbr, season)

    try:
        # 1. matches 表
        cursor.execute("""
            INSERT OR REPLACE INTO matches 
            (match_id, home_team, away_team, match_date, match_type, 
             handicap, handicap_source, actual_wdl, actual_handicap, actual_score, actual_total_goals, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'sporttery', ?, ?, ?, ?, datetime('now'))
        """, (
            odds_match_id,
            home_team,
            away_team,
            match_data.get("match_date", ""),
            match_type,
            match_data.get("handicap", 0),
            actual_wdl,
            actual_handicap,
            match_data.get("actual_score", ""),
            match_data.get("actual_total_goals"),
        ))

        # 2. wdl_history
        for wdl in match_data.get("wdl_timing", []):
            cursor.execute("""
                INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b)
                VALUES (?, ?, ?, ?, ?)
            """, (odds_match_id, wdl["timestamp"], wdl["win_a"], wdl["draw"], wdl["win_b"]))

        # 3. handicap_history（无 goal_line，与现有表兼容）
        for hcp in match_data.get("handicap_timing", []):
            cursor.execute("""
                INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose)
                VALUES (?, ?, ?, ?, ?)
            """, (odds_match_id, hcp["timestamp"], hcp["win"], hcp["draw"], hcp["lose"]))

        # 4. total_goals_history
        for tg in match_data.get("total_goals_timing", []):
            cursor.execute("""
                INSERT OR IGNORE INTO total_goals_history 
                (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (odds_match_id, tg["timestamp"],
                  tg["goals_0"], tg["goals_1"], tg["goals_2"], tg["goals_3"],
                  tg["goals_4"], tg["goals_5"], tg["goals_6"], tg["goals_7_plus"]))

        # 5. score_history
        for score in match_data.get("score_timing", []):
            cursor.execute("""
                INSERT OR IGNORE INTO score_history (match_id, timestamp, score, odds)
                VALUES (?, ?, ?, ?)
            """, (odds_match_id, score["timestamp"], score["score"], score["odds"]))

        conn.commit()
        return True

    except Exception as e:
        conn.rollback()
        print(f"  ❌ DB保存异常: {e}")
        return False
    finally:
        conn.close()


# ============================================================
# API 数据解析
# ============================================================

def parse_match_list_response(data: dict) -> list:
    """解析比赛列表API响应"""
    matches = []
    results = data.get("value", {}).get("matchResult", [])

    for m in results:
        if m.get("matchResultStatus") != "2":
            continue

        sporttery_id = str(m.get("matchId", ""))
        if not sporttery_id:
            continue

        full_score = m.get("sectionsNo999", "?:?")
        try:
            parts = full_score.split(":")
            total_goals = int(parts[0]) + int(parts[1])
        except:
            total_goals = None

        try:
            handicap = float(m.get("goalLine", "0").replace("+", ""))
        except:
            handicap = 0.0

        win_flag = m.get("winFlag", "")
        wdl_map = {"H": "主胜", "A": "客胜", "D": "平局"}
        actual_wdl = wdl_map.get(win_flag, "")

        matches.append({
            "sporttery_match_id": sporttery_id,
            "home_team": m.get("allHomeTeam", m.get("homeTeam", "")),
            "away_team": m.get("allAwayTeam", m.get("awayTeam", "")),
            "match_date": m.get("matchDate", ""),
            "league_name_abbr": m.get("leagueNameAbbr", ""),
            "handicap": handicap,
            "actual_wdl": actual_wdl,
            "actual_score": full_score,
            "actual_total_goals": total_goals,
        })

    return matches


def parse_fixed_bonus_response(data: dict) -> dict:
    """解析赔率详情API响应"""
    result = {"wdl_timing": [], "handicap_timing": [], "total_goals_timing": [], "score_timing": []}

    try:
        odds_history = data.get("value", {}).get("oddsHistory", {})
        if not odds_history:
            return result

        # WDL
        for item in odds_history.get("hadList", []):
            ts = f"{item.get('updateDate', '')} {item.get('updateTime', '')}"
            result["wdl_timing"].append({
                "timestamp": ts,
                "win_a": float(item.get("h") or 0),
                "draw": float(item.get("d") or 0),
                "win_b": float(item.get("a") or 0),
            })

        # 让球（无 goal_line，与 odds.db handicap_history 兼容）
        for item in odds_history.get("hhadList", []):
            ts = f"{item.get('updateDate', '')} {item.get('updateTime', '')}"
            result["handicap_timing"].append({
                "timestamp": ts,
                "win": float(item.get("h") or 0),
                "draw": float(item.get("d") or 0),
                "lose": float(item.get("a") or 0),
            })

        # 总进球
        for item in odds_history.get("ttgList", []):
            ts = f"{item.get('updateDate', '')} {item.get('updateTime', '')}"
            result["total_goals_timing"].append({
                "timestamp": ts,
                "goals_0": float(item.get("s0") or 0),
                "goals_1": float(item.get("s1") or 0),
                "goals_2": float(item.get("s2") or 0),
                "goals_3": float(item.get("s3") or 0),
                "goals_4": float(item.get("s4") or 0),
                "goals_5": float(item.get("s5") or 0),
                "goals_6": float(item.get("s6") or 0),
                "goals_7_plus": float(item.get("s7") or 0),
            })

        # 比分
        crs_fields = {
            "s01s00": "1:0", "s02s00": "2:0", "s02s01": "2:1",
            "s03s00": "3:0", "s03s01": "3:1", "s03s02": "3:2",
            "s04s00": "4:0", "s04s01": "4:1", "s04s02": "4:2",
            "s05s00": "5:0", "s05s01": "5:1", "s05s02": "5:2",
            "-1sa": "胜其它",
            "s00s00": "0:0", "s01s01": "1:1", "s02s02": "2:2", "s03s03": "3:3",
            "-1sd": "平其它",
            "s00s01": "0:1", "s00s02": "0:2", "s01s02": "1:2",
            "s00s03": "0:3", "s01s03": "1:3", "s02s03": "2:3",
            "s00s04": "0:4", "s01s04": "1:4", "s02s04": "2:4",
            "s00s05": "0:5", "s01s05": "1:5", "s02s05": "2:5",
            "-1sh": "负其它",
        }
        for item in odds_history.get("crsList", []):
            ts = f"{item.get('updateDate', '')} {item.get('updateTime', '')}"
            for field, score_name in crs_fields.items():
                odds_val = item.get(field, "0")
                if odds_val and odds_val != "0":
                    try:
                        result["score_timing"].append({
                            "timestamp": ts, "score": score_name, "odds": float(odds_val)
                        })
                    except ValueError:
                        pass

    except Exception as e:
        print(f"  ⚠️ 解析赔率异常: {e}")

    return result


# ============================================================
# 浏览器采集器
# ============================================================

class SportteryCollector:
    """竞彩网数据采集器"""

    def __init__(self, headless: bool = True):
        self.headless = headless
        self.browser = None
        self.page = None
        self.playwright = None

    async def start(self):
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            print("❌ 请先安装 Playwright: pip install playwright && playwright install chromium")
            sys.exit(1)

        self.playwright = await async_playwright().start()
        self.browser = await self.playwright.chromium.launch(
            headless=self.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        context = await self.browser.new_context(
            user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        )
        self.page = await context.new_page()
        print("✅ 浏览器已启动")

    async def stop(self):
        if self.browser:
            await self.browser.close()
        if self.playwright:
            await self.playwright.stop()
        print("✅ 浏览器已关闭")

    async def _get_cookies(self):
        await self.page.goto(SPORTTERY_HOME, wait_until="domcontentloaded", timeout=30000)
        await asyncio.sleep(2)

    async def _api_fetch(self, url: str) -> dict:
        """通过浏览器页面上下文调用 API"""
        try:
            response = await self.page.evaluate(f"""
                async () => {{
                    const resp = await fetch('{url}');
                    return await resp.json();
                }}
            """)
            return response
        except Exception as e:
            print(f"  ❌ API请求失败: {e}")
            return None

    async def fetch_match_list(self, begin_date: str, end_date: str,
                                page_no: int = 1, page_size: int = 100) -> dict:
        params = {
            "matchBeginDate": begin_date, "matchEndDate": end_date,
            "leagueId": "", "pageSize": page_size, "pageNo": page_no,
            "isFix": "0", "matchPage": "1", "pcOrWap": "1",
        }
        query = "&".join([f"{k}={v}" for k, v in params.items()])
        return await self._api_fetch(f"{MATCH_LIST_API}?{query}")

    async def fetch_fixed_bonus(self, sporttery_match_id: str) -> dict:
        url = f"{FIXED_BONUS_API}?clientCode=3001&matchId={sporttery_match_id}"
        return await self._api_fetch(url)

    async def collect_league_chunk(self, league_name: str, season: str,
                                    start_date: str, end_date: str,
                                    save_raw: bool = True) -> tuple:
        """采集指定联赛一个时间块的数据"""
        print(f"\n{'='*60}")
        print(f"🏆 {league_name} {season} | {start_date} ~ {end_date}")
        print(f"{'='*60}")

        # 第一步：获取比赛列表
        print("📋 获取比赛列表...")
        all_matches = []
        page_no = 1
        total_pages = 1

        while page_no <= total_pages:
            data = await self.fetch_match_list(start_date, end_date, page_no)
            if not data or data.get("errorCode") != "0":
                print(f"  ⚠️ 第{page_no}页请求失败: {data.get('errorMessage') if data else '无响应'}")
                break

            value = data.get("value", {})
            total_pages = value.get("pages", 1)
            matches = parse_match_list_response(data)
            league_matches = [m for m in matches if m["league_name_abbr"] == league_name]
            all_matches.extend(league_matches)
            print(f"  第{page_no}/{total_pages}页: {len(league_matches)}场 (累计{len(all_matches)})")

            page_no += 1
            await asyncio.sleep(REQUEST_DELAY)

        print(f"📊 共 {len(all_matches)} 场")

        if not all_matches:
            return 0, 0

        if save_raw:
            raw_file = COLLECTED_DATA_DIR / f"{league_name}_{season.replace('-','_')}_{start_date}_{end_date}.json"
            with open(raw_file, "w", encoding="utf-8") as f:
                json.dump(all_matches, f, ensure_ascii=False, indent=2)

        # 第二步：逐场获取赔率
        print(f"📈 获取时序赔率...")
        success = fail = 0

        for i, match in enumerate(all_matches):
            sid = match["sporttery_match_id"]
            print(f"  [{i+1}/{len(all_matches)}] {match['match_date']} {match['home_team']} vs {match['away_team']}", end=" ")

            bonus = await self.fetch_fixed_bonus(sid)
            if not bonus or bonus.get("errorCode") != "0":
                print("❌")
                fail += 1
                continue

            timing = parse_fixed_bonus_response(bonus)
            full_data = {**match, **timing}

            if save_to_odds_db(full_data, season, league_name):
                w = len(timing["wdl_timing"])
                h = len(timing["handicap_timing"])
                t = len(timing["total_goals_timing"])
                print(f"✅ WDL:{w} HCP:{h} TG:{t}")
                success += 1
            else:
                print("❌")
                fail += 1

            await asyncio.sleep(REQUEST_DELAY)
            if (i + 1) % 50 == 0:
                print(f"  ⏸️ 已完成{i+1}场，休息{BATCH_DELAY}秒...")
                await asyncio.sleep(BATCH_DELAY)

        print(f"📊 结果: 成功 {success}, 失败 {fail}")
        return success, fail


# ============================================================
# 日期工具
# ============================================================

def generate_monthly_chunks(start_date: str, end_date: str) -> list:
    """将日期范围拆分为月度块"""
    from dateutil.relativedelta import relativedelta

    start = datetime.strptime(start_date, "%Y-%m-%d")
    end = datetime.strptime(end_date, "%Y-%m-%d")

    chunks = []
    current = start
    while current <= end:
        month_end = current + relativedelta(day=31)
        if month_end > end:
            month_end = end
        chunks.append((current.strftime("%Y-%m-%d"), month_end.strftime("%Y-%m-%d")))
        current = month_end + timedelta(days=1)
    return chunks


# ============================================================
# 主程序
# ============================================================

async def main():
    parser = argparse.ArgumentParser(description="sporttery.cn 竞彩足球时序赔率采集器 v2")
    parser.add_argument("--league", type=str, default="all",
                       choices=["all", "英超", "意甲", "西甲", "德甲", "法甲"])
    parser.add_argument("--season", type=str, default="2024-2025",
                       choices=list(SEASON_RANGES.keys()),
                       help="赛季 (默认: 2024-2025)")
    parser.add_argument("--start-date", type=str, default=None,
                       help="覆盖开始日期 (YYYY-MM-DD)")
    parser.add_argument("--end-date", type=str, default=None,
                       help="覆盖结束日期 (YYYY-MM-DD)")
    parser.add_argument("--no-headless", action="store_true", help="显示浏览器窗口")
    parser.add_argument("--no-save-raw", action="store_true", help="不保存原始JSON")
    parser.add_argument("--dry-run", action="store_true", help="仅检查比赛数量，不实际采集")

    args = parser.parse_args()

    # 确定日期范围
    season_cfg = SEASON_RANGES.get(args.season, SEASON_RANGES["2024-2025"])
    start_date = args.start_date or season_cfg["start"]
    end_date = args.end_date or season_cfg["end"]

    # 确定联赛
    if args.league == "all":
        leagues = list(LEAGUE_IDS.keys())
    else:
        leagues = [args.league]

    # 拆分月度块
    chunks = generate_monthly_chunks(start_date, end_date)
    print(f"📅 赛季: {args.season} | 范围: {start_date} ~ {end_date}")
    print(f"📦 拆分为 {len(chunks)} 个月度块")
    print(f"🏆 联赛: {', '.join(leagues)}")
    print(f"💾 写入目标: {DB_PATH}")

    if args.dry_run:
        print("\n🔍 Dry-run 模式，仅统计比赛数量...")
        collector = SportteryCollector(headless=not args.no_headless)
        await collector.start()
        try:
            await collector._get_cookies()
            for league_name in leagues:
                total = 0
                for cs, ce in chunks:
                    data = await collector.fetch_match_list(cs, ce, page_no=1, page_size=100)
                    if data and data.get("errorCode") == "0":
                        matches = parse_match_list_response(data)
                        league_matches = [m for m in matches if m["league_name_abbr"] == league_name]
                        total += len(league_matches)
                print(f"  {league_name}: 预计 {total} 场")
        finally:
            await collector.stop()
        return

    # 启动采集
    collector = SportteryCollector(headless=not args.no_headless)
    await collector.start()

    try:
        await collector._get_cookies()
        total_success = total_fail = 0

        for league_name in leagues:
            league_success = league_fail = 0
            for cs, ce in chunks:
                s, f = await collector.collect_league_chunk(
                    league_name, args.season, cs, ce,
                    save_raw=not args.no_save_raw
                )
                league_success += s
                league_fail += f
                await asyncio.sleep(BATCH_DELAY)

            total_success += league_success
            total_fail += league_fail
            print(f"\n📊 {league_name} {args.season}: 成功 {league_success}, 失败 {league_fail}")

        print(f"\n{'='*60}")
        print(f"🎉 全部完成! 成功 {total_success}, 失败 {total_fail}")
        print(f"💾 数据已写入: {DB_PATH}")
        print(f"{'='*60}")

    finally:
        await collector.stop()


if __name__ == "__main__":
    asyncio.run(main())