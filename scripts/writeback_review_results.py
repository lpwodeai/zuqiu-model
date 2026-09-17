# -*- coding: utf-8 -*-
"""
writeback_review_results.py — 复盘赛果回写训练主表（B3 执行，C-20260908-011）
=============================================================================
把 post_match_review 已复盘场次的赛果回写到 matches 表，使最新赛果进入
训练基线（load_match_data_odds 按 actual_score IS NOT NULL 过滤）。
B3 闭环前提：L3 样本权重按 match_id 命中，若复盘场次赛果未回写，
加权样本不在训练集内，权重无法生效。

格式约定（对齐 matches 既有行）：
  actual_score 用 post_match_review 的 'X:Y'（'X-Y' 亦可解析）
  home_goals/away_goals 由比分解析
  actual_wdl 用 '主胜/平局/客胜'（对齐阿森纳行先例）
  league 归一中文名（英超/西甲/意甲/德甲/法甲）

用法：
  python scripts/writeback_review_results.py            # dry-run 预览
  python scripts/writeback_review_results.py --apply    # 写库（幂等：已有赛果行跳过）
"""
import sqlite3
import sys
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent
DB = PROJECT / "data" / "odds.db"
DRY = "--apply" not in sys.argv  # 默认 dry-run，显式 --apply 才写库

conn = sqlite3.connect(str(DB))
conn.row_factory = sqlite3.Row
conn.execute("PRAGMA busy_timeout = 5000")

# 待回写：post_match_review 中 matches.actual_score 为空的行（幂等）
rows = conn.execute("""
    SELECT r.match_id, r.league, r.actual_score, r.actual_wdl
    FROM post_match_review r
    JOIN matches m ON m.match_id = r.match_id
    WHERE r.wdl_correct IS NOT NULL AND m.actual_score IS NULL
""").fetchall()
print("待回写行数:", len(rows))

updated = 0
for r in rows:
    score = r["actual_score"]
    if not score or ":" not in score and "-" not in score:
        print("SKIP 无法解析比分:", r["match_id"], repr(score))
        continue
    sep = ":" if ":" in score else "-"
    hg, ag = score.split(sep)
    try:
        hg_i, ag_i = int(hg), int(ag)
    except ValueError:
        print("SKIP 比分非数字:", r["match_id"], repr(score))
        continue
    wdl = r["actual_wdl"]
    league = r["league"]
    if DRY:
        print("DRY   {}: {} {} | {} | {}-{}".format(r["match_id"], score, wdl, league, hg_i, ag_i))
    else:
        conn.execute(
            "UPDATE matches SET actual_score=?, actual_wdl=?, home_goals=?, away_goals=?, league=? "
            "WHERE match_id=?",
            (score, wdl, hg_i, ag_i, league, r["match_id"]))
        updated += 1

if not DRY:
    conn.commit()
    print("已回写:", updated)
else:
    print("(dry-run 未写库；加 --apply 执行)")
conn.close()
