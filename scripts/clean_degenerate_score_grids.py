# -*- coding: utf-8 -*-
"""
clean_degenerate_score_grids.py — 清理 Score_grid 退化网格（C-20260924-072）
============================================================================
背景：可靠性门禁（P0-B）发现 444 场 Score_grid（model=t006_score_predictor_v5）退化为
      单一高分比分格（如 5:5=0.874），造成平局高置信桶 +81pp 的假性高估。
      根因：历史批量回填（2016-2023，wdl_history 赔率为空）时用更早版本算法写入，
      当前 serving 代码（predict_score_distribution_v5）已无法复现。

处理：删除退化网格的 36 格 Score_grid 行（有备份，可回滚）。
      这些场次无赔率，当前代码本应产出「无赔率泊松兜底」，但旧代码写出了退化网格；
      因无真实赔率可锚定，删除比「注入 444 条同质占位网格」更诚实。

安全：默认 --dry-run 只报告不删除；--apply 才真正删除，删除前自动备份到
      data/backup/degenerate_score_grids_{HHMMSS}.json（含完整行，可重建）。

用法：
  python scripts/clean_degenerate_score_grids.py                    # 仅扫描报告（dry-run）
  python scripts/clean_degenerate_score_grids.py --apply            # 备份后删除
  python scripts/clean_degenerate_score_grids.py --model X          # 指定模型（默认 t006_score_predictor_v5）
  python scripts/clean_degenerate_score_grids.py --diag 0.6 --cell 0.6
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_DB = PROJECT_DIR / "data" / "odds.db"
BACKUP_DIR = PROJECT_DIR / "data" / "backup"

DEFAULT_MODEL = "t006_score_predictor_v5"

# 退化判定阈值（与 t006_score_predictor_v5.predict_score_distribution_v5 哨兵一致）
DEFAULT_DIAG_THRESH = 0.6
DEFAULT_CELL_THRESH = 0.6


def load_grids(conn: sqlite3.Connection, model_name: str) -> dict:
    """读 Score_grid 36 格 → {match_id: {(h,a): prob}}。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT match_id, prediction_type, probability FROM model_predictions "
        "WHERE model_name = ? AND prediction_type GLOB 'Score_grid_*'",
        (model_name,),
    )
    grids: dict = defaultdict(dict)
    for mid, pt, p in cur.fetchall():
        # prediction_type 形如 'Score_grid_{h}_{a}'，h/a 为 0..5 单字符
        body = pt.split("Score_grid_", 1)[-1]
        parts = body.split("_")
        if len(parts) != 2:
            continue
        try:
            h, a = int(parts[0]), int(parts[1])
        except ValueError:
            continue
        if p is not None:
            grids[mid][(h, a)] = float(p)
    return grids


def find_degenerate(grids: dict, diag_thresh: float, cell_thresh: float) -> list:
    """返回退化 match_id 列表（对角线或单格概率超阈值）。"""
    degen = []
    for mid, g in grids.items():
        diag = sum(v for (h, a), v in g.items() if h == a)
        max_cell = max(g.values()) if g else 0.0
        if diag > diag_thresh or max_cell > cell_thresh:
            degen.append((mid, round(diag, 4), round(max_cell, 4)))
    return degen


def main() -> int:
    ap = argparse.ArgumentParser(description="清理 Score_grid 退化网格（备份后可回滚）")
    ap.add_argument("--apply", action="store_true", help="真正删除（默认 dry-run 只报告）")
    ap.add_argument("--model", default=DEFAULT_MODEL, help=f"模型名（默认 {DEFAULT_MODEL}）")
    ap.add_argument("--db", default=str(DEFAULT_DB), help=f"DB 路径（默认 {DEFAULT_DB}）")
    ap.add_argument("--diag", type=float, default=DEFAULT_DIAG_THRESH, help=f"对角线退化阈值（默认 {DEFAULT_DIAG_THRESH}）")
    ap.add_argument("--cell", type=float, default=DEFAULT_CELL_THRESH, help=f"单格退化阈值（默认 {DEFAULT_CELL_THRESH}）")
    args = ap.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    grids = load_grids(conn, args.model)
    degen = find_degenerate(grids, args.diag, args.cell)
    print(f"[scan] 模型 {args.model}: 共 {len(grids)} 场 Score_grid，退化 {len(degen)} 场")
    if degen:
        print("[scan] 退化场次样例（match_id / 对角线 / 单格峰值）:")
        for mid, d, c_ in degen[:20]:
            print(f"         {mid}  diag={d}  max_cell={c_}")

    if not degen:
        print("[scan] 无退化网格，无需处理。")
        conn.close()
        return 0

    if not args.apply:
        print("\n[dry-run] 未删除任何数据。确认后加 --apply 执行。")
        conn.close()
        return 0

    # 备份 + 删除
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%H%M%S")
    backup_path = BACKUP_DIR / f"degenerate_score_grids_{ts}.json"

    mid_set = {m for m, _, _ in degen}
    cur = conn.cursor()
    placeholders = ",".join("?" for _ in mid_set)
    rows = cur.execute(
        f"SELECT match_id, model_name, prediction_type, probability, timestamp, is_replay "
        f"FROM model_predictions WHERE model_name = ? AND prediction_type GLOB 'Score_grid_*' "
        f"AND match_id IN ({placeholders})",
        (args.model, *mid_set),
    ).fetchall()

    backup_records = [dict(r) for r in rows]
    with open(backup_path, "w", encoding="utf-8") as f:
        json.dump({
            "created": datetime.now().isoformat(),
            "model": args.model,
            "diag_thresh": args.diag,
            "cell_thresh": args.cell,
            "n_matches": len(mid_set),
            "n_rows": len(backup_records),
            "rows": backup_records,
        }, f, ensure_ascii=False, indent=2)

    del_cur = conn.cursor()
    del_cur.execute(
        f"DELETE FROM model_predictions WHERE model_name = ? AND prediction_type GLOB 'Score_grid_*' "
        f"AND match_id IN ({placeholders})",
        (args.model, *mid_set),
    )
    conn.commit()

    print(f"\n[apply] 已删除 {del_cur.rowcount} 行（{len(mid_set)} 场退化网格）。")
    print(f"[apply] 备份文件: {backup_path}（{len(backup_records)} 行，用于回滚）")

    # 复核
    grids2 = load_grids(conn, args.model)
    degen2 = find_degenerate(grids2, args.diag, args.cell)
    print(f"[verify] 删除后剩余 {len(grids2)} 场，退化 {len(degen2)} 场。")
    conn.close()
    return 0 if not degen2 else 1


if __name__ == "__main__":
    raise SystemExit(main())