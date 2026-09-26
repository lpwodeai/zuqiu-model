# -*- coding: utf-8 -*-
"""Elo 评分引擎（C-20260922-055）。

基于 matches 表历史赛果迭代计算各球队 Elo 评分，并提供赛前快照查询。
用于 upset_factor_engine 的 rf（近期状态）维度，替代当前恒为 0.5 的缺失值。

设计原则（train/serve 同源）：
  - 纯计算、确定性：同一输入永远产出同一 Elo 序列
  - 赛前快照：查询某队截至 match_date 的 Elo，仅使用该日期之前的赛果
  - 无未来信息泄露：迭代严格按 match_date 升序

Elo 参数：
  - 初始 Elo = 1500
  - K 因子 = 30（足球常用）
  - 主场优势修正 = 100（计算期望胜率时主队 Elo + 100）
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "odds.db"

INITIAL_ELO = 1500.0
K_FACTOR = 30.0
HOME_ADVANTAGE = 100.0


def _expected(elo_a, elo_b):
    """Elo 期望胜率：E_a = 1 / (1 + 10^((R_b - R_a)/400))"""
    return 1.0 / (1.0 + 10 ** ((elo_b - elo_a) / 400.0))


def _result_score(actual_wdl):
    """actual_wdl（胜/平/负）→ 主队得分 S ∈ {1.0, 0.5, 0.0}"""
    if actual_wdl == "胜":
        return 1.0
    if actual_wdl == "平":
        return 0.5
    if actual_wdl == "负":
        return 0.0
    return None


def compute_elo_ratings(db_path=None):
    """从 matches 表迭代计算所有球队 Elo，返回 {team: elo} 当前字典。

    同时将每场赛前快照写入 team_elo_snapshot 表（CREATE IF NOT EXISTS + 清空重建）。
    快照语义：该场比赛开始前，双方的 Elo 值。
    """
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row

    conn.execute("""
        CREATE TABLE IF NOT EXISTS team_elo_snapshot (
            team_name TEXT NOT NULL,
            match_date TEXT NOT NULL,
            elo REAL NOT NULL,
            PRIMARY KEY (team_name, match_date)
        )
    """)
    conn.execute("DELETE FROM team_elo_snapshot")

    rows = conn.execute("""
        SELECT match_date, home_team, away_team, actual_wdl
        FROM matches
        WHERE actual_wdl IS NOT NULL
        ORDER BY match_date ASC
    """).fetchall()

    elos = {}  # team -> current elo
    snapshots = []  # (team, date, elo) before each match

    for r in rows:
        ht, at = r["home_team"], r["away_team"]
        date = r["match_date"]
        s = _result_score(r["actual_wdl"])
        if s is None:
            continue
        elo_h = elos.get(ht, INITIAL_ELO)
        elo_a = elos.get(at, INITIAL_ELO)

        # 赛前快照（用于该场及之后查询）
        snapshots.append((ht, date, elo_h))
        snapshots.append((at, date, elo_a))

        # 更新 Elo（主队含主场优势）
        exp_h = _expected(elo_h + HOME_ADVANTAGE, elo_a)
        elos[ht] = elo_h + K_FACTOR * (s - exp_h)
        elos[at] = elo_a + K_FACTOR * ((1 - s) - (1 - exp_h))

    conn.executemany(
        "INSERT INTO team_elo_snapshot (team_name, match_date, elo) VALUES (?, ?, ?)",
        snapshots,
    )
    conn.commit()
    conn.close()
    return elos


def get_team_elo(team_name, match_date, db_path=None):
    """获取某队截至 match_date（含）的最新 Elo。

    查询 team_elo_snapshot 中 match_date <= 指定日期的最大 elo。
    若该队无任何快照，返回初始 Elo 1500。
    """
    db_path = db_path or DB_PATH
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT elo FROM team_elo_snapshot WHERE team_name = ? AND match_date <= ? "
        "ORDER BY match_date DESC LIMIT 1",
        (team_name, match_date),
    ).fetchone()
    conn.close()
    if row:
        return row[0]
    return INITIAL_ELO


def get_elo_gap(home_team, away_team, match_date, db_path=None):
    """赛前 Elo 差距归一化到 [0, 1]：|elo_home - elo_away| / 400。

    差距越大 → 冷门风险越高（强队翻车风险）。
    """
    eh = get_team_elo(home_team, match_date, db_path)
    ea = get_team_elo(away_team, match_date, db_path)
    return min(abs(eh - ea) / 400.0, 1.0)


if __name__ == "__main__":
    print("正在计算 Elo 评分...")
    elos = compute_elo_ratings()
    print(f"完成，共 {len(elos)} 支球队有 Elo 记录")
    top = sorted(elos.items(), key=lambda x: x[1], reverse=True)[:10]
    print("Elo Top 10:")
    for t, e in top:
        print(f"  {t:25s} {e:.1f}")
