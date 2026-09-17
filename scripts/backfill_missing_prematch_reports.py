# -*- coding: utf-8 -*-
"""为「缺失赛前预测报告」的场次补生成 prematch_reports/*.md。

与 generate_unified_report.py 的日常流程同源（同一批函数），但仅写单场报告：
  - 不重写 _summary.md（避免用仅补回的子集覆盖已有汇总）
  - 不回写 model_predictions（避免覆盖既有预测记录）

适用场景：某场已存在 model_predictions / post_match 复盘，但缺赛前预测报告 .md。

用法：
  python scripts/backfill_missing_prematch_reports.py --date 2026-08-30 --league 英超 --teams 切尔西,托特纳姆热刺
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from generate_unified_report import (  # noqa: E402
    connect_db, discover_matches, find_sporttery_match_id,
    build_sporttery_odds, parse_hcp_line, build_match_dict, render_report,
    load_500_data, load_sofascore_data,
)


def backfill(date: str, league: str, homes: list[str]) -> None:
    conn = connect_db()
    matches = discover_matches(conn, date, date, [league], played_only=False)
    homeset = {h.strip().replace(" ", "") for h in homes}
    matches = [m for m in matches if (m["home_team_cn"] or "").replace(" ", "") in homeset]
    if not matches:
        print(f"[未发现] {date} {league} 中指定主队 {homes}")
        conn.close()
        return

    from prediction_core import init_models, PredictionCore  # noqa: E402
    models = init_models()
    models["epl"] = None  # 与日常流程一致：英超独立模型仅参考，默认关闭避免卡顿
    core = PredictionCore(models)

    for m in matches:
        home_cn, away_cn = m["home_team_cn"], m["away_team_cn"]
        print(f"\n=== {m['match_date']} [{m['league']}] {home_cn} vs {away_cn} ===")

        sporttery_mid = find_sporttery_match_id(conn, home_cn, away_cn, m["match_date"])
        odds_data = build_sporttery_odds(conn, sporttery_mid, m.get("win"), m.get("draw"), m.get("lost"))
        hcp_line = parse_hcp_line(m.get("handicap"))
        odds_data["handicap_odds"]["line"] = hcp_line

        extra = {"500": load_500_data(conn, m["fid"]),
                 "sofascore": load_sofascore_data(conn, m["match_date"], home_cn)}
        match = build_match_dict(m, hcp_line)
        try:
            result = core.predict_unified(match, odds_data, is_mock=False)
        except Exception as e:
            print(f"  ❌ 预测失败: {type(e).__name__}: {e}")
            continue

        out_dir = PROJECT_DIR / "docs" / "prematch_reports" / m["match_date"].replace("-", "")
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{m['league']}_{m['match_date']}_{home_cn}_vs_{away_cn}.md".replace("/", "-")
        report_path = out_dir / fname
        completeness = render_report(m, odds_data, result, extra, report_path, conn)
        print(f"  ✅ 报告: {report_path.name} (完整度 {completeness}%)")

    conn.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="补生成缺失赛前预测报告（仅单场 .md，不写 summary/predictions）")
    parser.add_argument("--date", required=True, help="比赛日 YYYY-MM-DD")
    parser.add_argument("--league", required=True, help="联赛（英超/西甲/意甲/德甲/法甲）")
    parser.add_argument("--teams", required=True, help="主队中文名，逗号分隔")
    args = parser.parse_args()
    backfill(args.date, args.league, [t for t in args.teams.split(",") if t.strip()])


if __name__ == "__main__":
    main()