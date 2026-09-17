# -*- coding: utf-8 -*-
"""
补充竞彩网时序赔率：读取已有 JSON 文件，调用 getFixedBonusV1.qry API 获取时序赔率
直接写入 odds.db，复用 sporttery_collector.py 的解析和写入逻辑
"""
import asyncio
import json
import os
import sys
import sqlite3
from pathlib import Path
from datetime import datetime

# 添加 scripts 路径
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from sporttery_collector import (
    parse_fixed_bonus_response, save_to_odds_db, build_odds_match_id,
    REQUEST_DELAY, BATCH_DELAY, FIXED_BONUS_API
)

BASE_DIR = Path(__file__).resolve().parent.parent  # 项目根目录（collection 的上一级）
JSON_DIR = BASE_DIR / "data" / "sporttery_collected"
DB_PATH = BASE_DIR / "data" / "odds.db"

# 联赛名映射 (JSON文件名前缀 → 中文联赛名)
LEAGUE_MAP = {
    "英超": "英超", "西甲": "西甲", "意甲": "意甲", "德甲": "德甲", "法甲": "法甲",
}


async def process_match(page, match: dict, league_name: str, season: str) -> bool:
    """处理单场比赛：获取时序赔率并写入DB"""
    sid = match["sporttery_match_id"]
    
    try:
        response = await page.evaluate(f"""
            async () => {{
                const resp = await fetch('{FIXED_BONUS_API}?clientCode=3001&matchId={sid}');
                return await resp.json();
            }}
        """)
    except Exception as e:
        print(f"    ❌ API请求失败: {e}")
        return False
    
    if not response or response.get("errorCode") != "0":
        print(f"    ❌ API返回错误")
        return False
    
    timing = parse_fixed_bonus_response(response)
    full_data = {**match, **timing}
    
    if save_to_odds_db(full_data, season, league_name):
        w = len(timing["wdl_timing"])
        h = len(timing["handicap_timing"])
        t = len(timing["total_goals_timing"])
        s = len(timing["score_timing"])
        return True
    return False


async def main():
    print("=" * 70)
    print("📈 竞彩网时序赔率补充采集")
    print("=" * 70)
    
    # 加载 JSON 文件
    json_files = sorted(JSON_DIR.glob("*.json"))
    print(f"JSON 文件: {len(json_files)} 个")
    
    # 统计总数
    all_matches = []
    for fpath in json_files:
        with open(fpath, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        # 从文件名提取联赛和赛季
        fname = fpath.stem
        parts = fname.split("_")
        league = parts[0]
        season = f"{parts[1]}-{parts[2]}"
        for m in data:
            m["_league"] = league
            m["_season"] = season
        all_matches.extend(data)
    
    print(f"总比赛数: {len(all_matches)}")
    
    # 检查已有赔率数据的比赛
    conn = sqlite3.connect(str(DB_PATH))
    existing = set()
    for row in conn.execute("SELECT DISTINCT match_id FROM wdl_history"):
        existing.add(row[0])
    conn.close()
    
    need_process = []
    for m in all_matches:
        mid = build_odds_match_id(m["match_date"], m["home_team"], m["away_team"])
        if mid not in existing:
            need_process.append(m)
    
    print(f"已有WDL赔率: {len(existing)} 场")
    print(f"需要补充: {len(need_process)} 场")
    
    if len(need_process) == 0:
        print("\n✅ 所有比赛已有赔率数据，无需补充!")
        return
    
    # 启动浏览器
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        print("❌ 请安装 Playwright: pip install playwright && playwright install chromium")
        return
    
    print(f"\n启动浏览器...")
    pw = await async_playwright().start()
    browser = await pw.chromium.launch(
        headless=True,
        args=['--disable-blink-features=AutomationControlled']
    )
    context = await browser.new_context(
        user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
    )
    page = await context.new_page()
    
    # 访问首页获取 cookies
    print("获取 cookies...")
    await page.goto("https://www.sporttery.cn/jc/zqsgkj/", wait_until="domcontentloaded", timeout=30000)
    await asyncio.sleep(2)
    
    # 处理
    success = 0
    fail = 0
    total = len(need_process)
    
    for i, match in enumerate(need_process):
        league = match["_league"]
        season = match["_season"]
        print(f"[{i+1}/{total}] {match['match_date']} {match['home_team']} vs {match['away_team']}", end=" ")
        
        ok = await process_match(page, match, league, season)
        if ok:
            print("✅")
            success += 1
        else:
            print("❌")
            fail += 1
        
        await asyncio.sleep(REQUEST_DELAY)
        
        if (i + 1) % 50 == 0:
            print(f"  ⏸️ 已完成 {i+1}/{total}，休息 {BATCH_DELAY} 秒...")
            await asyncio.sleep(BATCH_DELAY)
    
    print(f"\n{'='*70}")
    print(f"📊 完成: 成功 {success}, 失败 {fail}, 总计 {total}")
    print(f"{'='*70}")
    
    await browser.close()
    await pw.stop()


if __name__ == "__main__":
    asyncio.run(main())