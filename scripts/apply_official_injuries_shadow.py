# -*- coding: utf-8 -*-
"""shadow 模式评估日志器：官方伤停校正 pa_* 特征（零生产链路改动）。

⚠️ 本脚本产出仅为「窗口前评估日志」：
- jsonl 只喂评估/对拍统计，永不喂训练（禁止训练路径从 jsonl 读特征 = train/serve 双实现红线）
- 不改 model_predictions、不改特征表、不改报告展示
- 重训窗口时的正确姿势：把官方伤停校正接进生产特征生成器（player_availability_features.py）
  → 全量重建 → 重训，train/serve 同源同函数

背景：
  生产 pa_availability / pa_missing_impact 由「核心球员是否出现在最近一场出场名单」推算
  （features/player_availability_features.py L404-425），不消费官方伤停。
  官方伤停校正逻辑 scripts/player_injury_source.py 的 apply_official_injuries（L65-90）
  此前无生产调用点（仅 tests/test_p2p3_modules.py import）。本脚本把它接到 shadow
  评估器：每场对比「生产推算值」与「官方校正 shadow 值」，追加写
  reports/shadow/pa_official_shadow.jsonl（每场一行，按 match_id 幂等去重）。

数据源口径（全部复用现有代码，不另造）：
  - 场次：fbref_match_mapping（filter fbref_match_url LIKE '%sofascore%'，
    同 player_availability_features.generate_all L490-498 的生产取场口径）
  - 官方缺阵名单：match_missing_players
    WHERE fbref_match_id=? AND team=? AND reason!='转会'
    （同 generate_unified_report.py L1374-1377 的报告口径；team 列为 fbref 英文队名，
    已实测与 fbref_match_mapping.home/away_team_fbref 全量对齐 86493/86493）
  - 生产 pa_*：PlayerAvailabilityFeatures.compute_team_features（即线上值）
  - 官方校正：apply_official_injuries（player_injury_source.py L65-90）。
    该函数签名是 (predicted_xi, official_out) → XI 级校正字典（confirmed_out/adjusted_xi/
    coverage_of_official），不直接产出 pa_* 指标，故不适合直接算 pa；本脚本复用其核心逻辑
    （tracked 球员池 ∩ 官方缺阵名单，L80-82 的集合求交）作用于生产核心球员池，再按生产
    公式（player_availability_features.py L388-419）重算 shadow pa_availability /
    pa_missing_impact。官方名单中不在历史核心池的球员无 xG 基线、无法量化影响，保守不计入。

用法：
  python scripts/apply_official_injuries_shadow.py --date 2026-10-15
  python scripts/apply_official_injuries_shadow.py --date-range 2026-10-01 2026-10-15
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for _p in (str(PROJECT_ROOT), str(PROJECT_ROOT / "scripts"), str(PROJECT_ROOT / "features")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from player_availability_features import (  # noqa: E402
    NO_DATA,
    PlayerAvailabilityFeatures,
    _no_data_sentinel_enabled,
)
from player_injury_source import apply_official_injuries  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "odds.db"
OUT_PATH = PROJECT_ROOT / "reports" / "shadow" / "pa_official_shadow.jsonl"

LOG_KEYS = [
    "pa_availability_home",
    "pa_missing_impact_home",
    "pa_availability_away",
    "pa_missing_impact_away",
]


# ==================== 数据读取 ====================

def fetch_matches(conn: sqlite3.Connection, start: str, end: str) -> List[sqlite3.Row]:
    """取日期区间内所有生产口径场次（同 generate_all 的 sofascore filter）。"""
    return conn.execute(
        """
        SELECT fbref_match_id, match_date, home_team_fbref, away_team_fbref
        FROM fbref_match_mapping
        WHERE match_date BETWEEN ? AND ?
          AND fbref_match_url LIKE '%sofascore%'
        ORDER BY match_date, fbref_match_id
        """,
        (start, end),
    ).fetchall()


def fetch_official_out(conn: sqlite3.Connection, fbref_match_id: str, team: str) -> List[str]:
    """该场该队官方缺阵名单（口径同 generate_unified_report.py L1374-1377，加 DISTINCT 去重）。"""
    rows = conn.execute(
        """
        SELECT DISTINCT player_name FROM match_missing_players
        WHERE fbref_match_id=? AND team=? AND reason!='转会'
        ORDER BY player_name
        """,
        (str(fbref_match_id), team),
    ).fetchall()
    return [r[0] for r in rows]


def load_logged_match_ids(path: Path) -> Set[str]:
    """读取已落盘 jsonl 的 match_id 集合（幂等：已存在则跳过）。"""
    seen: Set[str] = set()
    if not path.exists():
        return seen
    with path.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            mid = rec.get("match_id")
            if mid:
                seen.add(str(mid))
    return seen


# ==================== shadow 计算 ====================

def compute_shadow(
    pa_gen: PlayerAvailabilityFeatures,
    team_name: str,
    match_date: str,
    official_out: List[str],
) -> Optional[Tuple[float, float]]:
    """官方校正后的 (pa_availability, pa_missing_impact)；无历史数据返回 None。

    逐行对齐生产公式 player_availability_features.py L388-419（核心球员池 → 最近一场
    出场名单 → 缺阵影响），唯一改动：缺阵集合 = 推算缺阵 ∪ 官方确认缺阵。
    官方确认缺阵由 apply_official_injuries（player_injury_source.py L65-90）直接算出——
    把核心球员池作为 tracked 名单传入，取其 confirmed_out（核心池 ∩ 官方缺阵）。
    """
    prepared = pa_gen._prepare_recent(team_name, match_date)
    if prepared is None:
        return None
    _, recent_matches, player_stats = prepared

    sentinel_on = _no_data_sentinel_enabled()
    nd = NO_DATA if sentinel_on else 0.0

    core_players = player_stats[
        (player_stats["is_starting_rate"] > 0.6)
        & (player_stats["avg_rating"] >= player_stats["avg_rating"].quantile(0.7))
    ]
    total_xg = player_stats["avg_xg"].sum() or 0.01

    if len(recent_matches) > 0:
        latest_match_players = recent_matches.iloc[0]
        core_names = set(core_players["player_name"])
        missing_core = core_names - latest_match_players
        # 官方校正：核心池中被官方确认缺阵的球员
        corr = apply_official_injuries(sorted(core_names), sorted(official_out))
        missing_shadow = missing_core | set(corr["confirmed_out"])
        missing_shadow_xg = core_players[
            core_players["player_name"].isin(missing_shadow)
        ]["avg_xg"].sum()

        if sentinel_on and not core_names:
            availability = NO_DATA
            missing_impact = NO_DATA
        else:
            availability = 1.0 - min(0.2, len(missing_shadow) / max(len(core_names), 1))
            missing_impact = missing_shadow_xg / total_xg
    else:
        availability = NO_DATA if sentinel_on else 1.0
        missing_impact = nd

    return round(float(availability), 4), round(float(missing_impact), 4)


# ==================== 主流程 ====================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="官方伤停校正 pa_* 的 shadow 评估日志器（零生产链路改动）"
    )
    grp = parser.add_mutually_exclusive_group(required=True)
    grp.add_argument("--date", type=str, help="单日 YYYY-MM-DD")
    grp.add_argument("--date-range", type=str, nargs=2, metavar=("START", "END"),
                     help="日期区间 YYYY-MM-DD YYYY-MM-DD（含端点）")
    parser.add_argument("--db", type=str, default=str(DB_PATH), help="odds.db 路径")
    parser.add_argument("--out", type=str, default=str(OUT_PATH), help="输出 jsonl 路径")
    args = parser.parse_args()

    if args.date:
        start = end = args.date
    else:
        start, end = args.date_range
    # 日期格式校验
    for d in (start, end):
        datetime.strptime(d, "%Y-%m-%d")
    if start > end:
        parser.error(f"日期区间起点晚于终点: {start} > {end}")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    seen = load_logged_match_ids(out_path)

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row
    pa_gen = PlayerAvailabilityFeatures(db_path=Path(args.db))

    n_written = 0
    n_skipped = 0
    n_with_official = 0
    impact_deltas: List[float] = []

    try:
        matches = fetch_matches(conn, start, end)
        for m in matches:
            match_id = str(m["fbref_match_id"])
            if match_id in seen:
                n_skipped += 1
                continue
            match_date = m["match_date"]
            home, away = m["home_team_fbref"], m["away_team_fbref"]

            # 单队懒载入（同报告口径 C-20261014-092，避免 preload 全表）
            pa_gen.ensure_team_stats(home)
            pa_gen.ensure_team_stats(away)

            # 1. 生产口径（线上值）
            prod_home = pa_gen.compute_team_features(home, match_date)
            prod_away = pa_gen.compute_team_features(away, match_date)

            # 2. 官方伤停名单
            off_home = fetch_official_out(conn, match_id, home)
            off_away = fetch_official_out(conn, match_id, away)

            # 3. shadow 校正（无历史数据时退化为生产值，delta=0）
            sh_home = compute_shadow(pa_gen, home, match_date, off_home) or (
                prod_home["pa_availability"], prod_home["pa_missing_impact"])
            sh_away = compute_shadow(pa_gen, away, match_date, off_away) or (
                prod_away["pa_availability"], prod_away["pa_missing_impact"])

            prod = {
                "pa_availability_home": prod_home["pa_availability"],
                "pa_missing_impact_home": prod_home["pa_missing_impact"],
                "pa_availability_away": prod_away["pa_availability"],
                "pa_missing_impact_away": prod_away["pa_missing_impact"],
            }
            shadow = {
                "pa_availability_home": sh_home[0],
                "pa_missing_impact_home": sh_home[1],
                "pa_availability_away": sh_away[0],
                "pa_missing_impact_away": sh_away[1],
            }
            delta = {k: round(shadow[k] - prod[k], 4) for k in LOG_KEYS}

            record = {
                "match_id": match_id,
                "match_date": match_date,
                "home": home,
                "away": away,
                "prod": prod,
                "shadow": shadow,
                "official_injuries": {"home": off_home, "away": off_away},
                "delta": delta,
                "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
            }
            with out_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
            seen.add(match_id)

            n_written += 1
            if off_home or off_away:
                n_with_official += 1
            impact_deltas.extend(
                [abs(delta["pa_missing_impact_home"]), abs(delta["pa_missing_impact_away"])]
            )
    finally:
        pa_gen.close()
        conn.close()

    # 当日摘要
    print(f"[shadow] 区间 {start} ~ {end}")
    print(f"[shadow] 新写入 {n_written} 场，跳过已存在 {n_skipped} 场 → {out_path}")
    print(f"[shadow] 有官方伤停的场次: {n_with_official}/{n_written}")
    if impact_deltas:
        mean_d = sum(impact_deltas) / len(impact_deltas)
        print(f"[shadow] |Δpa_missing_impact| 均值 {mean_d:.4f} / 最大 {max(impact_deltas):.4f}")
    else:
        print("[shadow] 无新场次，未产生 delta")


if __name__ == "__main__":
    main()
