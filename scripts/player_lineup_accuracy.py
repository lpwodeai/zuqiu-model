# -*- coding: utf-8 -*-
"""P2 球员推算首发准确率复盘（官方 vs 推算区分的量化基线）。

问题背景（docs/基于预测报告发现的问题.txt §4.2 P1-14 / P2 清单）：
  预计首发 / pa_availability / pa_missing_impact 均基于历史出场连续性的「纯推算」，
  无官方赛前伤病/首发源。需复盘「推算首发」vs「真实实际首发」的一致率，量化推算
  可靠性，沉淀哪些球队/联赛最容易翻车（作为 z3 官方伤病源升级前的基线）。

方法：
  推算首发 = PlayerAvailabilityFeatures.predict_xi_players(team, match_date)
             （match_date 之前历史出场 + 首发率/评分优先级，推算 11 人）
  实际首发 = match_player_stats 中 is_starter=1 的 11 人（赛后 SofaScore 口径）
  命中率   = |推算 ∩ 实际| / 11
  官方区分 = 当前全量 source='inferred'（推算）；z3 引入官方伤病源后标记 'official'

用法：
  python scripts/player_lineup_accuracy.py [--limit N] [--no-write] [--db data/odds.db]

输出：
  - reports/player_xi_accuracy_<TS>.md / .json
  - 落库 player_xi_accuracy_review（每场每队一行，source 字段预留 official）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import numpy as np

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from features.player_availability_features import PlayerAvailabilityFeatures  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "odds.db"
REPORT_DIR = PROJECT_ROOT / "reports"
XI_SIZE = 11


# ==================== 纯函数（可单测） ====================

def hit_rate(predicted: Set[str], actual: Set[str], xi_size: int = XI_SIZE) -> float:
    """推算首发命中率 = |推算 ∩ 实际| / xi_size；实际为空返回 NaN。"""
    if not actual:
        return float("nan")
    return float(len(predicted & actual)) / xi_size


def aggregate_by(rows: List[dict], key: str, min_n: int = 1) -> List[dict]:
    """按维度（league / team）聚合命中率统计。"""
    buckets = defaultdict(list)
    for r in rows:
        buckets[r[key]].append(r["hit_rate"])
    out = []
    for k, vals in buckets.items():
        if len(vals) < min_n:
            continue
        arr = np.array([v for v in vals if not np.isnan(v)])
        if arr.size == 0:
            continue
        out.append({
            key: k,
            "n": int(arr.size),
            "mean_hit_rate": float(arr.mean()),
            "median_hit_rate": float(np.median(arr)),
            "p90_hit_rate": float(np.percentile(arr, 90)),
        })
    out.sort(key=lambda x: -x["mean_hit_rate"])
    return out


# ==================== 数据加载 ====================

def load_actual_starters(conn: sqlite3.Connection) -> Dict[Tuple[str, str], Set[str]]:
    """实际首发：match_player_stats 中 is_starter=1，按 (fbref_match_id, team) 分组。"""
    out = defaultdict(set)
    cur = conn.execute(
        "SELECT fbref_match_id, team, player_name FROM match_player_stats WHERE is_starter=1"
    )
    for mid, team, pname in cur.fetchall():
        out[(mid, team)].add(pname)
    return {k: v for k, v in out.items()}


def load_matches(conn: sqlite3.Connection) -> List[dict]:
    """比赛列表（fbref 英文全称队名，与 match_player_stats.team 一致）。"""
    cur = conn.execute(
        "SELECT fbref_match_id, home_team_fbref, away_team_fbref, match_date, league "
        "FROM fbref_match_mapping WHERE fbref_match_id IS NOT NULL"
    )
    return [
        {"mid": r[0], "home": r[1], "away": r[2], "date": r[3], "league": r[4]}
        for r in cur.fetchall()
    ]


# ==================== 复盘主循环 ====================

def run_review(
    generator: PlayerAvailabilityFeatures,
    matches: List[dict],
    starters: Dict[Tuple[str, str], Set[str]],
    n_recent: int = 5,
    limit: Optional[int] = None,
) -> Tuple[List[dict], dict]:
    """逐场复盘推算首发命中率。返回 (rows, skip_stats)。"""
    rows: List[dict] = []
    skip_no_actual = 0
    skip_no_predict = 0
    target = matches[:limit] if limit else matches

    for i, m in enumerate(target):
        if (i + 1) % 2000 == 0:
            print(f"  进度 {i+1}/{len(target)}")
        for side, team in (("home", m["home"]), ("away", m["away"])):
            actual = starters.get((m["mid"], team))
            if not actual:
                skip_no_actual += 1
                continue
            predicted = generator.predict_xi_players(team, m["date"], n_recent=n_recent)
            if not predicted:
                skip_no_predict += 1
                continue
            hit = len(set(predicted) & actual)
            rows.append({
                "fbref_match_id": m["mid"],
                "team": team,
                "league": m["league"],
                "match_date": m["date"],
                "source": "inferred",
                "predicted_xi": list(predicted),
                "actual_xi": sorted(actual),
                "hit_cnt": hit,
                "actual_cnt": len(actual),
                "hit_rate": hit / XI_SIZE,
            })

    return rows, {
        "matches": len(target),
        "skipped_no_actual": skip_no_actual,
        "skipped_no_predict": skip_no_predict,
    }


def summarize(rows: List[dict]) -> dict:
    """整体 + 分联赛 + 分球队（翻车排行榜）统计。"""
    hrs = np.array([r["hit_rate"] for r in rows])
    by_league = aggregate_by(rows, "league", min_n=10)
    # 分球队：至少 5 场才有统计意义
    by_team_all = aggregate_by(rows, "team", min_n=1)
    by_team = [x for x in by_team_all if x["n"] >= 5]
    # 翻车排行榜 = 命中率最低的球队（含小样本，≥3 场即提示）
    flop_candidates = [x for x in by_team_all if x["n"] >= 3]
    flop = sorted(flop_candidates, key=lambda x: x["mean_hit_rate"])[:20]

    return {
        "n_reviewed": int(hrs.size),
        "overall": {
            "mean_hit_rate": float(hrs.mean()),
            "median_hit_rate": float(np.median(hrs)),
            "std_hit_rate": float(hrs.std()),
            "p25": float(np.percentile(hrs, 25)),
            "p75": float(np.percentile(hrs, 75)),
        },
        "by_league": by_league,
        "by_team": by_team,
        "flop_teams": flop,
    }


# ==================== 落库 ====================

def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS player_xi_accuracy_review (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fbref_match_id TEXT NOT NULL,
            team TEXT NOT NULL,
            league TEXT,
            match_date TEXT,
            source TEXT DEFAULT 'inferred',
            predicted_xi TEXT,
            actual_xi TEXT,
            hit_cnt INTEGER,
            actual_cnt INTEGER,
            hit_rate REAL,
            reviewed_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fbref_match_id, team)
        )"""
    )
    conn.commit()


def write_review(conn: sqlite3.Connection, rows: List[dict]) -> None:
    ensure_table(conn)
    conn.executemany(
        """INSERT OR REPLACE INTO player_xi_accuracy_review
           (fbref_match_id, team, league, match_date, source, predicted_xi, actual_xi,
            hit_cnt, actual_cnt, hit_rate)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        [
            (r["fbref_match_id"], r["team"], r["league"], r["match_date"], r["source"],
             json.dumps(r["predicted_xi"], ensure_ascii=False),
             json.dumps(r["actual_xi"], ensure_ascii=False),
             r["hit_cnt"], r["actual_cnt"], r["hit_rate"])
            for r in rows
        ],
    )
    conn.commit()


# ==================== 报告 ====================

def generate_report(summary: dict, skip: dict, md_path: Path, json_path: Path) -> Tuple[Path, Path]:
    REPORT_DIR.mkdir(exist_ok=True)
    ov = summary["overall"]

    L = []
    L.append("# 球员推算首发准确率复盘报告（P2）\n")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append(f"**复盘样本**: {summary['n_reviewed']} 队次（{skip['matches']} 场 × 2 队，"
             f"跳过无实际首发 {skip['skipped_no_actual']}、无推算 {skip['skipped_no_predict']}）\n")
    L.append("> 命中率 = 推算首发 11 人 ∩ 实际首发 11 人 / 11；source 当前全为 `inferred`（纯推算）\n")
    L.append("> 本复盘为 z3「官方伤病数据源升级」前基线：引入官方首发/伤病源后可对比 `official` 表现\n")

    L.append("## 一、整体推算准确率\n")
    L.append("| 指标 | 数值 |")
    L.append("|---|---|")
    L.append(f"| 平均命中率 | {ov['mean_hit_rate']*100:.2f}% |")
    L.append(f"| 中位数命中率 | {ov['median_hit_rate']*100:.2f}% |")
    L.append(f"| 标准差 | {ov['std_hit_rate']*100:.2f}pp |")
    L.append(f"| P25 / P75 | {ov['p25']*100:.2f}% / {ov['p75']*100:.2f}% |")
    L.append("")

    L.append("## 二、分联赛推算准确率（n≥10）\n")
    L.append("| 联赛 | 队次 | 平均命中率 | 中位数 | P90 |")
    L.append("|---|---|---|---|---|")
    for x in summary["by_league"]:
        L.append(f"| {x['league']} | {x['n']} | {x['mean_hit_rate']*100:.2f}% | "
                 f"{x['median_hit_rate']*100:.2f}% | {x['p90_hit_rate']*100:.2f}% |")
    L.append("")

    L.append("## 三、推算准确率最稳定球队 TOP20（n≥5）\n")
    L.append("| 球队 | 队次 | 平均命中率 | 中位数 |")
    L.append("|---|---|---|---|")
    for x in summary["by_team"][:20]:
        L.append(f"| {x['team']} | {x['n']} | {x['mean_hit_rate']*100:.2f}% | "
                 f"{x['median_hit_rate']*100:.2f}% |")
    L.append("")

    L.append("## 四、推算最容易翻车球队 TOP20（n≥3，命中率最低）\n")
    L.append("| 球队 | 队次 | 平均命中率 | 中位数 |")
    L.append("|---|---|---|---|")
    for x in summary["flop_teams"]:
        L.append(f"| {x['team']} | {x['n']} | {x['mean_hit_rate']*100:.2f}% | "
                 f"{x['median_hit_rate']*100:.2f}% |")
    L.append("")
    L.append("> 翻车球队提示：这些球队历史推算首发与实际首发偏差大，赛前应降低其球员可用性/首发特征的置信度权重。\n")

    L.append("\n---\n*本报告由 player_lineup_accuracy.py 自动生成*\n")
    md_path.write_text("\n".join(L), encoding="utf-8")
    json_path.write_text(json.dumps({"summary": summary, "skip": skip}, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="球员推算首发准确率复盘（P2）")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--limit", type=int, default=None, help="限制复盘场次（调试用）")
    parser.add_argument("--n-recent", type=int, default=5)
    parser.add_argument("--no-write", action="store_true", help="不落库")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    print("加载实际首发 + 比赛列表...")
    starters = load_actual_starters(conn)
    matches = load_matches(conn)
    print(f"  比赛 {len(matches)} 场，有首发记录 {len(starters)} 队次")

    gen = PlayerAvailabilityFeatures(db_path=Path(args.db))
    try:
        print("推算首发并比对实际首发...")
        rows, skip = run_review(gen, matches, starters, n_recent=args.n_recent, limit=args.limit)
    finally:
        gen.close()

    summary = summarize(rows)
    ov = summary["overall"]
    print("\n" + "=" * 60)
    print(f"复盘完成: {summary['n_reviewed']} 队次")
    print(f"  平均命中率: {ov['mean_hit_rate']*100:.2f}% | 中位数 {ov['median_hit_rate']*100:.2f}%")
    print(f"  分联赛: {len(summary['by_league'])} 个 | 翻车球队收录 {len(summary['flop_teams'])} 队")
    print("=" * 60)

    if not args.no_write:
        write_review(conn, rows)
        print(f"已落库 player_xi_accuracy_review: {len(rows)} 行")
    conn.close()

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path, json_path = generate_report(
        summary, skip,
        REPORT_DIR / f"player_xi_accuracy_{ts}.md",
        REPORT_DIR / f"player_xi_accuracy_{ts}.json",
    )
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")


if __name__ == "__main__":
    main()