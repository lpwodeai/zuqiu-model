# -*- coding: utf-8 -*-
"""
从 fbref_match_mapping 填充 matches 表
将缺失的 24/25 和 25/26 赛季数据写入 matches 表
"""
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
sys.path.insert(0, str(BASE_DIR / "scripts"))

from team_name_mapping import normalize_team_name  # noqa: E402


def _norm(name):
    n = normalize_team_name(name)
    return n if n else (name if name else "")

LEAGUE_SEASON_MAP = {
    ("英超", "23/24"): "英超2023-2024赛季",
    ("西甲", "23/24"): "西甲2023-2024赛季",
    ("意甲", "23/24"): "意甲2023-2024赛季",
    ("德甲", "23/24"): "德甲2023-2024赛季",
    ("法甲", "23/24"): "法甲2023-2024赛季",
    ("英超", "24/25"): "英超2024-2025赛季",
    ("西甲", "24/25"): "西甲2024-2025赛季",
    ("意甲", "24/25"): "意甲2024-2025赛季",
    ("德甲", "24/25"): "德甲2024-2025赛季",
    ("法甲", "24/25"): "法甲2024-2025赛季",
    ("英超", "25/26"): "英超2025-2026赛季",
    ("西甲", "25/26"): "西甲2025-2026赛季",
    ("意甲", "25/26"): "意甲2025-2026赛季",
    ("德甲", "25/26"): "德甲2025-2026赛季",
    ("法甲", "25/26"): "法甲2025-2026赛季",
    ("英超", "26/27"): "英超2026-2027赛季",
    ("西甲", "26/27"): "西甲2026-2027赛季",
    ("意甲", "26/27"): "意甲2026-2027赛季",
    ("德甲", "26/27"): "德甲2026-2027赛季",
    ("法甲", "26/27"): "法甲2026-2027赛季",
}


def parse_score(score_str):
    if not score_str:
        return None, None, None
    m = re.match(r"(\d+)\s*[-–:]\s*(\d+)", score_str)
    if not m:
        return None, None, None
    hg, ag = int(m.group(1)), int(m.group(2))
    if hg > ag:
        wdl = "胜"
    elif hg < ag:
        wdl = "负"
    else:
        wdl = "平"
    return f"{hg}-{ag}", wdl, hg + ag


def populate():
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    
    print("=" * 70)
    print("📥 从 fbref_match_mapping 填充 matches 表")
    print("=" * 70)
    
    # 获取现有 match_id + (date, 归一化队名) 双键去重
    existing_ids = set()
    existing_games = set()  # (match_date, norm_home, norm_away)
    for row in cur.execute("SELECT match_id, match_date, home_team, away_team FROM matches"):
        existing_ids.add(row[0])
        existing_games.add((row[1], _norm(row[2]), _norm(row[3])))
    print(f"\nmatches 表现有: {len(existing_ids)} 条")

    # 获取 fbref 数据
    fbref_rows = cur.execute("""
        SELECT odds_match_id, league, season, match_date,
               home_team_cn, away_team_cn, fbref_score
        FROM fbref_match_mapping
        ORDER BY match_date, league
    """).fetchall()
    print(f"fbref_match_mapping: {len(fbref_rows)} 条")

    inserted = 0
    skipped = 0
    no_score = 0

    for row in fbref_rows:
        odds_mid, league, season, match_date, home_cn, away_cn, score = row

        if not odds_mid:
            # fbref_match_mapping.odds_match_id 缺失时无法保证与采集器口径一致，跳过
            no_score += 1
            continue

        # C-20260926-090 修复: match_id 直接用 fbref_match_mapping.odds_match_id
        # （采集器 final_sofascore_collector 生成的 SofaScore 口径，与
        # match_player_stats/match_lineups 的 match_id 同源），避免 C-086 用
        # 归一化中文名生成 match_id 导致 matches 与 ps 跨源断裂（风险 R）。
        # home_team/away_team 同样写 fbref_match_mapping 原值（SofaScore 名），
        # 与 matches 表 EN 主流口径一致；归一化仅用于双键去重。
        match_id = odds_mid
        home_norm = _norm(home_cn)
        away_norm = _norm(away_cn)
        game_key = (match_date, home_norm, away_norm)

        # 双键去重：match_id 相同 或 (date, 归一化队名) 相同
        if match_id in existing_ids or game_key in existing_games:
            skipped += 1
            continue

        # 解析比分
        actual_score, actual_wdl, actual_total = parse_score(score)
        if actual_score is None:
            no_score += 1
            continue

        # 构建 match_type：优先查表，否则将 YY/YY 补全为 20YY-20YY+1
        match_type = LEAGUE_SEASON_MAP.get((league, season))
        if match_type is None:
            # 兼容 '22/23' → '2022-2023'、'2024-2025' → 原样
            if "/" in season and len(season) == 5:
                sy, ey = season.split("/")
                match_type = f"{league}20{sy}-20{ey}赛季"
            else:
                match_type = f"{league}{season.replace('/', '-')}赛季"

        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        try:
            cur.execute("""
                INSERT INTO matches
                    (match_id, home_team, away_team, match_date, match_type, league,
                     actual_score, actual_wdl, actual_total_goals, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (match_id, home_cn, away_cn, match_date, match_type, league,
                  actual_score, actual_wdl, actual_total, now, now))
            inserted += 1
            existing_ids.add(match_id)
            existing_games.add(game_key)
        except Exception as e:
            if inserted < 5:
                print(f"  ⚠️ 插入失败: {match_id} - {e}")
    
    conn.commit()
    
    print(f"\n📊 结果:")
    print(f"  fbref 源数据: {len(fbref_rows)} 条")
    print(f"  新增插入: {inserted} 条")
    print(f"  跳过 (已存在): {skipped} 条")
    print(f"  跳过 (无比分): {no_score} 条")
    
    # 验证
    total = cur.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
    print(f"\n📊 matches 表最终: {total} 条")
    
    # 赛季分布
    for season_start in ["2023", "2024", "2025"]:
        cnt = cur.execute(
            "SELECT COUNT(*) FROM matches WHERE match_date >= ? AND match_date < ?",
            (f"{season_start}-08-01", f"{int(season_start)+1}-08-01")
        ).fetchone()[0]
        print(f"  {season_start}-{int(season_start)+1}: {cnt} 场")
    
    conn.close()
    print("\n✅ 填充完成!")


if __name__ == "__main__":
    populate()