# -*- coding: utf-8 -*-
"""积分榜引擎（C-20260922-055）。

基于 matches 表历史赛果，按联赛+赛季计算各球队积分与排名，并提供赛前快照查询。
用于 upset_factor_engine 的 mt（战意）维度，替代当前恒为 0.5 的缺失值。

设计原则（train/serve 同源）：
  - 纯计算、确定性
  - 赛前快照：仅使用 match_date 之前的赛果计算积分和排名
  - 赛季推断：北半球赛季 8 月开赛，次年 5 月结束 → match_date.year 的前一年 8 月起为该赛季
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "odds.db"

POINTS_WIN = 3
POINTS_DRAW = 1
POINTS_LOSS = 0


def _season_from_date(match_date):
    """从比赛日期推断赛季（如 2026-09-21 → '26/27'）。"""
    y = int(match_date[:4])
    m = int(match_date[5:7])
    if m >= 8:
        return f"{y % 100:02d}/{(y + 1) % 100:02d}"
    return f"{(y - 1) % 100:02d}/{y % 100:02d}"


def _result_points(actual_wdl):
    """actual_wdl → (主队积分, 客队积分)"""
    if actual_wdl == "胜":
        return POINTS_WIN, POINTS_LOSS
    if actual_wdl == "平":
        return POINTS_DRAW, POINTS_DRAW
    if actual_wdl == "负":
        return POINTS_LOSS, POINTS_WIN
    return None, None


def compute_standings(db_path=None):
    """从 matches 表迭代计算各球队积分排名，写入 league_standing 快照表。"""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS league_standing (
            team_name TEXT NOT NULL,
            match_date TEXT NOT NULL,
            league TEXT,
            points INTEGER,
            position INTEGER,
            PRIMARY KEY (team_name, match_date)
        )
    """)
    conn.execute("DELETE FROM league_standing")

    rows = conn.execute("""
        SELECT match_date, home_team, away_team, actual_wdl, league
        FROM matches
        WHERE actual_wdl IS NOT NULL
        ORDER BY match_date ASC
    """).fetchall()

    # points[(league, season, team)] = current points
    points = {}
    snapshots = []  # (team, date, league, points, position)

    for r in rows:
        ht, at = r["home_team"], r["away_team"]
        date = r["match_date"]
        league = r["league"] or "未知"
        season = _season_from_date(date)
        ph, pa = _result_points(r["actual_wdl"])
        if ph is None:
            continue

        key_h = (league, season, ht)
        key_a = (league, season, at)
        cur_h = points.get(key_h, 0)
        cur_a = points.get(key_a, 0)

        # 赛前快照：先记录当前积分和排名
        # 排名 = 同 (league, season) 中积分 > 当前的球队数 + 1
        def _pos(league, season, cur_pts):
            cnt = sum(1 for (lg, ss, _tm), pt in points.items()
                      if lg == league and ss == season and pt > cur_pts)
            return cnt + 1

        pos_h = _pos(league, season, cur_h)
        pos_a = _pos(league, season, cur_a)
        snapshots.append((ht, date, league, cur_h, pos_h))
        snapshots.append((at, date, league, cur_a, pos_a))

        # 更新积分
        points[key_h] = cur_h + ph
        points[key_a] = cur_a + pa

    conn.executemany(
        "INSERT INTO league_standing (team_name, match_date, league, points, position) VALUES (?, ?, ?, ?, ?)",
        snapshots,
    )
    conn.commit()
    conn.close()
    return points


def get_team_standing(team_name, match_date, db_path=None):
    """获取某队截至 match_date 的最新 (points, position, league)。"""
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT points, position, league FROM league_standing WHERE team_name = ? AND match_date <= ? "
        "ORDER BY match_date DESC LIMIT 1",
        (team_name, match_date),
    ).fetchone()
    conn.close()
    if row:
        return row[0], row[1], row[2]
    return 0, None, None


def get_motivation_signal(home_team, away_team, match_date, db_path=None):
    """战意信号：基于赛前积分排名计算。

    返回 (motivation_score, has_data)：
      - 赛季早期（round 推断 < 8）：+0.3
      - 保级区（position > 16，假设 20 队联赛）：+0.3
      - 争冠区（position ≤ 3）：+0.2
      - 段位差 boost：min(|pos_home - pos_away| / 15, 0.2)
    """
    ph, pos_h, lg_h = get_team_standing(home_team, match_date, db_path)
    pa, pos_a, lg_a = get_team_standing(away_team, match_date, db_path)

    if pos_h is None or pos_a is None:
        return 0.5, True  # 缺失兜底

    score = 0.0
    # 赛季早期：从 match_date 推断轮次（简化：8-10月为早期）
    m = int(match_date[5:7])
    if m in (8, 9, 10):
        score += 0.3
    # 保级区
    if pos_h > 16 or pos_a > 16:
        score += 0.3
    # 争冠区
    if pos_h <= 3 or pos_a <= 3:
        score += 0.2
    # 段位差
    score += min(abs(pos_h - pos_a) / 15.0, 0.2)

    return min(score, 1.0), False


if __name__ == "__main__":
    print("正在计算积分榜...")
    pts = compute_standings()
    print(f"完成，共 {len(pts)} 个 (联赛,赛季,球队) 组合有积分")
    # 抽查
    print("\n26/27 英超部分球队当前积分:")
    for (lg, ss, tm), p in sorted(pts.items()):
        if ss == "26/27" and lg == "英超":
            print(f"  {tm:20s} {p}")
            break  # 只打一个示例
    from collections import defaultdict
    epl = {tm: p for (lg, ss, tm), p in pts.items() if ss == "26/27" and lg == "英超"}
    for tm, p in sorted(epl.items(), key=lambda x: x[1], reverse=True)[:8]:
        print(f"  {tm:20s} {p}")
