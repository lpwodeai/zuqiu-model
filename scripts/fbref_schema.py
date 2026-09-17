"""
fbref.com 数据采集 - 数据库表结构定义
=====================================
功能：在 odds.db 中新建 4 张表用于存储 fbref 阵容和球员统计数据
表：
  1. fbref_match_mapping  - fbref 比赛 URL ↔ odds.db match_id 映射
  2. match_lineups        - 单场比赛阵容（首发/替补/阵型/换人）
  3. match_player_stats   - 单场球员统计（60 typed 列 + JSON 存全部 280+ 指标）
  4. fbref_players        - 球员注册表（fbref_player_id ↔ five_leagues.players.id）

使用方法：
  python scripts/fbref_schema.py            # 初始化表结构
  python scripts/fbref_schema.py --drop     # 删除并重建（谨慎使用）
"""

import sqlite3
import argparse
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"


# ============================================================
# DDL 定义
# ============================================================

DDL_STATEMENTS = [
    # 1. fbref 比赛映射表
    """
    CREATE TABLE IF NOT EXISTS fbref_match_mapping (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        odds_match_id TEXT NOT NULL,
        fbref_match_id TEXT NOT NULL,
        fbref_match_url TEXT NOT NULL,
        fbref_match_slug TEXT,
        league TEXT NOT NULL,
        season TEXT NOT NULL,
        match_date TEXT NOT NULL,
        home_team_fbref TEXT,
        away_team_fbref TEXT,
        home_team_cn TEXT,
        away_team_cn TEXT,
        fbref_week INTEGER,
        fbref_score TEXT,
        collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(fbref_match_id),
        UNIQUE(odds_match_id, season)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fbref_map_league_season ON fbref_match_mapping(league, season)",
    "CREATE INDEX IF NOT EXISTS idx_fbref_map_date ON fbref_match_mapping(match_date)",

    # 2. 比赛阵容表（每球员每场一行）
    """
    CREATE TABLE IF NOT EXISTS match_lineups (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL,
        fbref_match_id TEXT,
        team TEXT NOT NULL,
        team_fbref TEXT,
        formation TEXT,
        player_name TEXT NOT NULL,
        player_name_cn TEXT,
        fbref_player_id TEXT,
        jersey_number INTEGER,
        position TEXT,
        is_starter INTEGER DEFAULT 1,
        sub_in_time TEXT,
        sub_out_time TEXT,
        sub_in_for TEXT,
        sub_out_for TEXT,
        sub_reason TEXT,
        minutes_played INTEGER,
        captain INTEGER DEFAULT 0,
        collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(match_id, team, player_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_lineup_match ON match_lineups(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_lineup_team ON match_lineups(team, match_id)",
    "CREATE INDEX IF NOT EXISTS idx_lineup_player ON match_lineups(fbref_player_id)",

    # 3. 比赛球员统计表（60 typed 列 + JSON）
    """
    CREATE TABLE IF NOT EXISTS match_player_stats (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id TEXT NOT NULL,
        fbref_match_id TEXT,
        team TEXT NOT NULL,
        team_fbref TEXT,
        player_name TEXT NOT NULL,
        player_name_cn TEXT,
        fbref_player_id TEXT,
        jersey_number INTEGER,
        position TEXT,
        is_starter INTEGER DEFAULT 1,
        minutes_played INTEGER,
        -- === Summary 表关键字段 ===
        goals INTEGER DEFAULT 0,
        assists INTEGER DEFAULT 0,
        penalties_made INTEGER DEFAULT 0,
        penalties_attempted INTEGER DEFAULT 0,
        shots INTEGER DEFAULT 0,
        shots_on_target INTEGER DEFAULT 0,
        yellow_cards INTEGER DEFAULT 0,
        red_cards INTEGER DEFAULT 0,
        fouls_committed INTEGER DEFAULT 0,
        fouls_drawn INTEGER DEFAULT 0,
        offsides INTEGER DEFAULT 0,
        crosses INTEGER DEFAULT 0,
        tackles_won INTEGER DEFAULT 0,
        interceptions INTEGER DEFAULT 0,
        own_goals INTEGER DEFAULT 0,
        -- === Passing 表关键字段 ===
        passes_completed INTEGER,
        passes_attempted INTEGER,
        pass_completion_pct REAL,
        total_distance_passes REAL,
        progressive_distance_passes REAL,
        short_passes_completed INTEGER,
        short_passes_attempted INTEGER,
        medium_passes_completed INTEGER,
        medium_passes_attempted INTEGER,
        long_passes_completed INTEGER,
        long_passes_attempted INTEGER,
        key_passes INTEGER,
        passes_into_final_third INTEGER,
        passes_into_penalty_area INTEGER,
        crosses_into_penalty_area INTEGER,
        progressive_passes INTEGER,
        -- === Defense 表关键字段 ===
        tackles INTEGER,
        tackles_won_def INTEGER,
        tackles_in_def_third INTEGER,
        tackles_in_mid_third INTEGER,
        tackles_in_att_third INTEGER,
        dribblers_tackled INTEGER,
        dribblers_challenged INTEGER,
        blocks INTEGER,
        blocked_shots INTEGER,
        blocked_passes INTEGER,
        clearances INTEGER,
        errors_leading_to_shot INTEGER,
        -- === Possession 表关键字段 ===
        touches INTEGER,
        touches_def_pen_area INTEGER,
        touches_def_third INTEGER,
        touches_mid_third INTEGER,
        touches_att_third INTEGER,
        touches_att_pen_area INTEGER,
        dribbles_completed_pos INTEGER,
        dribbles_attempted_pos INTEGER,
        successful_dribble_pct REAL,
        players_beaten INTEGER,
        carries INTEGER,
        carry_distance REAL,
        progressive_carries INTEGER,
        carries_into_final_third INTEGER,
        carries_into_penalty_area INTEGER,
        miscontrols INTEGER,
        dispossessed INTEGER,
        passes_received INTEGER,
        progressive_passes_received INTEGER,
        -- === Miscellaneous 表关键字段 ===
        corner_kicks INTEGER,
        penalties_won INTEGER,
        penalties_conceded INTEGER,
        ball_recoveries INTEGER,
        aerials_won INTEGER,
        aerials_lost INTEGER,
        aerial_win_pct REAL,
        -- === Goalkeeper 专属 ===
        gk_shots_on_target_against INTEGER,
        gk_goals_against INTEGER,
        gk_saves INTEGER,
        gk_save_pct REAL,
        gk_psa REAL,
        -- === 高级指标 ===
        xg REAL,
        xg_npxg REAL,
        xa REAL,
        sca INTEGER,
        gca INTEGER,
        -- === 完整数据 JSON（全部 280+ 指标） ===
        stats_json TEXT,
        -- === 元数据 ===
        stats_source TEXT DEFAULT 'fbref',
        collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(match_id, team, player_name)
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_pstats_match ON match_player_stats(match_id)",
    "CREATE INDEX IF NOT EXISTS idx_pstats_team ON match_player_stats(team, match_id)",
    "CREATE INDEX IF NOT EXISTS idx_pstats_player ON match_player_stats(fbref_player_id)",

    # 4. fbref 球员注册表
    """
    CREATE TABLE IF NOT EXISTS fbref_players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        fbref_player_id TEXT UNIQUE NOT NULL,
        fbref_player_url TEXT,
        player_name_en TEXT NOT NULL,
        player_name_cn TEXT,
        five_leagues_player_id INTEGER,
        primary_team_cn TEXT,
        primary_team_fbref TEXT,
        primary_position TEXT,
        nationality TEXT,
        first_seen_match TEXT,
        last_updated TEXT DEFAULT CURRENT_TIMESTAMP
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_fbref_players_team ON fbref_players(primary_team_cn)",
    "CREATE INDEX IF NOT EXISTS idx_fbref_players_name ON fbref_players(player_name_en)",
]

DROP_STATEMENTS = [
    "DROP TABLE IF EXISTS match_player_stats",
    "DROP TABLE IF EXISTS match_lineups",
    "DROP TABLE IF EXISTS fbref_match_mapping",
    "DROP TABLE IF EXISTS fbref_players",
]


def init_fbref_schema(drop_first: bool = False):
    """初始化 fbref 表结构到 odds.db

    Args:
        drop_first: 是否先删除已有表（谨慎使用）
    """
    conn = sqlite3.connect(str(DB_PATH))
    cursor = conn.cursor()

    if drop_first:
        print("⚠️  正在删除已有 fbref 表...")
        for stmt in DROP_STATEMENTS:
            cursor.execute(stmt)
        conn.commit()
        print("✅ 已删除")

    print(f"📋 正在 odds.db 创建 fbref 表结构...")
    for stmt in DDL_STATEMENTS:
        cursor.execute(stmt)
    conn.commit()
    conn.close()

    print(f"✅ fbref 表结构初始化完成: {DB_PATH}")
    print("   新建表: fbref_match_mapping / match_lineups / match_player_stats / fbref_players")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="初始化 fbref 表结构")
    parser.add_argument("--drop", action="store_true", help="先删除已有表（谨慎使用）")
    args = parser.parse_args()

    init_fbref_schema(drop_first=args.drop)
