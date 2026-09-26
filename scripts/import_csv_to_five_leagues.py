# -*- coding: utf-8 -*-
"""
将 5 个 CSV（2025-26 赛季）全量导入 five_leagues.db
====================================================
替代直接删除 CSV 的数据损失：把 CSV 的比赛统计（射门/角球/犯规/黄牌/控球率）
合并进 five_leagues.db，使 DB 成为 SSOT，之后 3 个活跃脚本可改用 DB 读取，
CSV 即可删除。

软去重策略：(normalize_team_name(home), normalize_team_name(away), competition_name)
若 DB 已存在同场（队名+联赛）记录则跳过，避免日期±1天差异导致重复。

队名映射：CSV 英文队名 → normalize_team_name → 中文（与 DB teams.name 一致）。

关联变更：C-20260918-027
"""
import sqlite3
import sys
import numpy as np
import pandas as pd
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "five_leagues.db"

sys.path.insert(0, str(BASE_DIR / "scripts"))
from team_name_mapping import normalize_team_name

CSV_FILES = {
    'EPL': 'EPL_2025-26.csv',
    'BUNDESLIGA': 'BUNDESLIGA_2025-26.csv',
    'LALIGA': 'LALIGA_2025-26.csv',
    'SERIEA': 'SERIEA_2025-26.csv',
    'LIGUE1': 'LIGUE1_2025-26.csv',
}
LEAGUE_TO_COMP = {
    'EPL': 'Premier League',
    'BUNDESLIGA': 'Bundesliga',
    'LALIGA': 'La Liga',
    'SERIEA': 'Serie A',
    'LIGUE1': 'Ligue 1',
}


def _to_int(v):
    """安全转 int，无效返回 None。"""
    if v is None or (isinstance(v, float) and np.isnan(v)):
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _parse_date(date_str):
    """CSV 日期 dd/mm/YYYY → YYYY-MM-DD。"""
    if pd.isna(date_str):
        return None
    s = str(date_str).strip()
    try:
        return datetime.strptime(s, '%d/%m/%Y').strftime('%Y-%m-%d')
    except ValueError:
        try:
            return datetime.strptime(s, '%Y-%m-%d').strftime('%Y-%m-%d')
        except ValueError:
            return s


def normalize_row(df_row, league, is_zh):
    """把 CSV 一行归一化为 DB matches 字段 dict。"""
    if is_zh:
        return {
            'date': _parse_date(df_row['日期']),
            'home_team': df_row['主队'],
            'away_team': df_row['客队'],
            'homeGoals': _to_int(df_row.get('主队进球')),
            'awayGoals': _to_int(df_row.get('客队进球')),
            'homeShots': _to_int(df_row.get('主队射门')),
            'awayShots': _to_int(df_row.get('客队射门')),
            'homeShotsOnTarget': _to_int(df_row.get('主队射正')),
            'awayShotsOnTarget': _to_int(df_row.get('客队射正')),
            'homeCorners': _to_int(df_row.get('主队角球')),
            'awayCorners': _to_int(df_row.get('客队角球')),
            'homeYellowCards': _to_int(df_row.get('主队黄牌')),
            'awayYellowCards': _to_int(df_row.get('客队黄牌')),
            'homeFouls': _to_int(df_row.get('主队犯规')),
            'awayFouls': _to_int(df_row.get('客队犯规')),
            'homePossession': _to_int(df_row.get('主队控球率')),
        }
    return {
        'date': _parse_date(df_row['Date']),
        'home_team': df_row['HomeTeam'],
        'away_team': df_row['AwayTeam'],
        'homeGoals': _to_int(df_row.get('FTHG')),
        'awayGoals': _to_int(df_row.get('FTAG')),
        'homeShots': _to_int(df_row.get('HS')),
        'awayShots': _to_int(df_row.get('AS')),
        'homeShotsOnTarget': _to_int(df_row.get('HST')),
        'awayShotsOnTarget': _to_int(df_row.get('AST')),
        'homeCorners': _to_int(df_row.get('HC')),
        'awayCorners': _to_int(df_row.get('AC')),
        'homeYellowCards': _to_int(df_row.get('HY')),
        'awayYellowCards': _to_int(df_row.get('AY')),
        'homeFouls': _to_int(df_row.get('HF')),
        'awayFouls': _to_int(df_row.get('AF')),
        'homePossession': None,
    }


def get_or_create_team(cur, name_cn):
    """查 teams 表，不存在则插入，返回 id。"""
    cur.execute("SELECT id FROM teams WHERE name = ?", (name_cn,))
    row = cur.fetchone()
    if row:
        return row[0]
    cur.execute("INSERT INTO teams (name) VALUES (?)", (name_cn,))
    return cur.lastrowid


def main():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA foreign_keys = ON")
    cur = conn.cursor()

    # 1. 载入 competitions name → id
    cur.execute("SELECT id, name FROM competitions")
    comp_map = {name: cid for cid, name in cur.fetchall()}
    print(f"competitions: {comp_map}")

    # 2. 载入 DB 现有 (norm_home, norm_away, comp_name) 集合，用于软去重
    cur.execute("""
        SELECT ht.name, at.name, c.name FROM matches m
        LEFT JOIN teams ht ON m.homeTeamId = ht.id
        LEFT JOIN teams at ON m.awayTeamId = at.id
        LEFT JOIN competitions c ON m.competitionId = c.id
        WHERE m.homeGoals IS NOT NULL
    """)
    existing = set()
    for h, a, comp in cur.fetchall():
        existing.add((normalize_team_name(h), normalize_team_name(a), comp))
    print(f"DB existing matches (soft keys): {len(existing)}")

    # 3. 预加载 teams name → id 缓存（减少查询）
    cur.execute("SELECT id, name FROM teams")
    team_cache = {name: tid for tid, name in cur.fetchall()}

    # 4. 遍历 CSV，软去重后插入
    inserted = 0
    skipped = 0
    # 统计列（不含 homeGoals/awayGoals，二者已在前面 6 个值中传入）
    stats_cols = [
        'homeShots', 'awayShots',
        'homeShotsOnTarget', 'awayShotsOnTarget',
        'homeCorners', 'awayCorners',
        'homeFouls', 'awayFouls',
        'homeYellowCards', 'awayYellowCards',
        'homePossession',
    ]
    for league, fname in CSV_FILES.items():
        fp = DATA_DIR / fname
        if not fp.exists():
            print(f"skip {fname}: not found")
            continue
        df = pd.read_csv(fp)
        is_zh = '日期' in df.columns
        comp_name = LEAGUE_TO_COMP[league]
        comp_id = comp_map.get(comp_name)
        if comp_id is None:
            print(f"!! competition {comp_name} not in DB, skip {league}")
            continue

        league_inserted = 0
        for _, row in df.iterrows():
            nr = normalize_row(row, league, is_zh)
            if nr['date'] is None or nr['homeGoals'] is None or nr['awayGoals'] is None:
                skipped += 1
                continue
            h_cn = normalize_team_name(nr['home_team']) or str(nr['home_team']).strip()
            a_cn = normalize_team_name(nr['away_team']) or str(nr['away_team']).strip()
            if not h_cn or not a_cn:
                # 队名为空，跳过并记录
                print(f"  !! skip {league} empty team: home={nr['home_team']!r} away={nr['away_team']!r}")
                skipped += 1
                continue
            soft_key = (h_cn, a_cn, comp_name)
            if soft_key in existing:
                skipped += 1
                continue

            # 查/建 teams
            h_id = team_cache.get(h_cn)
            if h_id is None:
                h_id = get_or_create_team(cur, h_cn)
                team_cache[h_cn] = h_id
            a_id = team_cache.get(a_cn)
            if a_id is None:
                a_id = get_or_create_team(cur, a_cn)
                team_cache[a_cn] = a_id

            match_hash = f"{nr['date']}_{h_cn}_{a_cn}"
            vals = [nr[c] for c in stats_cols]
            try:
                # 18 列对应 18 个占位符（date/hcp/goals + 11 统计 + matchHash）
                placeholders = ",".join(["?"] * 18)
                cur.execute(
                    f"""INSERT OR IGNORE INTO matches
                    (date, homeTeamId, awayTeamId, homeGoals, awayGoals, competitionId,
                     homeShots, awayShots, homeShotsOnTarget, awayShotsOnTarget,
                     homeCorners, awayCorners, homeFouls, awayFouls,
                     homeYellowCards, awayYellowCards, homePossession, matchHash)
                    VALUES ({placeholders})""",
                    [nr['date'], h_id, a_id, nr['homeGoals'], nr['awayGoals'], comp_id]
                    + vals + [match_hash],
                )
                if cur.rowcount > 0:
                    inserted += 1
                    league_inserted += 1
                    existing.add(soft_key)
                else:
                    skipped += 1
            except sqlite3.IntegrityError:
                skipped += 1
        print(f"  {league:12s} ({fname}): +{league_inserted} inserted")

    conn.commit()

    # 5. 验证
    cur.execute("SELECT COUNT(*) FROM matches WHERE homeGoals IS NOT NULL")
    total = cur.fetchone()[0]
    cur.execute("SELECT COUNT(DISTINCT matchHash) FROM matches")
    uniq = cur.fetchone()[0]
    conn.close()
    print(f"\n=== Result ===")
    print(f"inserted: {inserted}, skipped: {skipped}")
    print(f"DB total matches now: {total} (unique matchHash: {uniq})")


if __name__ == "__main__":
    main()
