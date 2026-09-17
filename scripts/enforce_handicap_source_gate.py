# -*- coding: utf-8 -*-
"""
enforce_handicap_source_gate.py — matches.handicap 写入门禁（强制式）

===============================================
背景：
  matches.handicap 曾同时被「竞彩 goalLine（整数盘口，三分类含走水）」与
  「亚盘（500.com/bet365 半整数盘口，无走水）」两路写入，口径混乱导致复盘
  actual_hcp 误判。前序已完成亚盘写入修复 + 数据清洗。

本脚本是「强制式门禁」：在数据库层用 SQLite 触发器禁止任何非竞彩数据源
写入 matches.handicap。

机制：
  1. matches 表新增 handicap_source 列（TEXT，NULL 表示未标记来源）。
  2. 仅允许 handicap_source='sporttery' 的行写入非 NULL 的 handicap。
  3. 两条触发器（INSERT / UPDATE OF handicap）在写入时校验：
       handicap IS NOT NULL 且 handicap_source != 'sporttery' → RAISE(ABORT)。

竞彩写入器在写 handicap 时必须显式带上 handicap_source='sporttery'：
  - scripts/sporttery_collector.py
  - scripts/supplement_sporttery_odds.py
  - scripts/sporttery_sync_to_odds.py
  - scripts/prediction_db_writer.py（pred["handicap"] 为竞彩 goalLine）

用法：
  python scripts/enforce_handicap_source_gate.py            # 应用门禁（幂等）
  python scripts/enforce_handicap_source_gate.py --verify   # 只验证，不改库
"""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

COLUMN = "handicap_source"
TRIGGER_INSERT = "trg_guard_matches_handicap_ins"
TRIGGER_UPDATE = "trg_guard_matches_handicap_upd"

GUARD_SQL_INSERT = f"""
CREATE TRIGGER {TRIGGER_INSERT}
BEFORE INSERT ON matches
FOR EACH ROW
WHEN NEW.handicap IS NOT NULL
     AND (NEW.{COLUMN} IS NULL OR NEW.{COLUMN} <> 'sporttery')
BEGIN
    SELECT RAISE(ABORT, 'matches.handicap 仅允许竞彩(goalLine)数据源写入：请设置 handicap_source=sporttery');
END;
"""

GUARD_SQL_UPDATE = f"""
CREATE TRIGGER {TRIGGER_UPDATE}
BEFORE UPDATE OF handicap ON matches
FOR EACH ROW
WHEN NEW.handicap IS NOT NULL
     AND (NEW.{COLUMN} IS NULL OR NEW.{COLUMN} <> 'sporttery')
BEGIN
    SELECT RAISE(ABORT, 'matches.handicap 仅允许竞彩(goalLine)数据源写入：请设置 handicap_source=sporttery');
END;
"""


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _has_column(conn: sqlite3.Connection) -> bool:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(matches)").fetchall()]
    return COLUMN in cols


def apply_gate() -> None:
    conn = _connect()
    try:
        # 1. 新增列（幂等）
        if not _has_column(conn):
            conn.execute(f"ALTER TABLE matches ADD COLUMN {COLUMN} TEXT")
            print(f"  + 新增列 matches.{COLUMN}")
        else:
            print(f"  = 列 matches.{COLUMN} 已存在")

        # 2. 回填既有非空 handicap 行（值已对齐竞彩 goalLine）
        cur = conn.execute(
            f"UPDATE matches SET {COLUMN}='sporttery' "
            f"WHERE handicap IS NOT NULL AND ({COLUMN} IS NULL OR {COLUMN}='')"
        )
        print(f"  + 回填 handicap_source='sporttery' 共 {cur.rowcount} 行")

        # 3. 重建触发器（幂等）
        for trig in (TRIGGER_INSERT, TRIGGER_UPDATE):
            conn.execute(f"DROP TRIGGER IF EXISTS {trig}")
        conn.execute(GUARD_SQL_INSERT)
        conn.execute(GUARD_SQL_UPDATE)
        print(f"  + 已创建触发器 {TRIGGER_INSERT} / {TRIGGER_UPDATE}")

        conn.commit()
    finally:
        conn.close()
    print("✅ 门禁已应用")


def verify() -> bool:
    conn = _connect()
    try:
        has_col = _has_column(conn)
        trigs = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='matches'"
        ).fetchall()]
        bad = conn.execute(
            "SELECT COUNT(*) FROM matches WHERE handicap IS NOT NULL "
            "AND (handicap_source IS NULL OR handicap_source <> 'sporttery')"
        ).fetchone()[0]
        print(f"列 handicap_source 存在: {has_col}")
        print(f"matches 触发器: {trigs}")
        print(f"handicap 非空但未标记 sporttery 的行数: {bad}")
        ok = has_col and TRIGGER_INSERT in trigs and TRIGGER_UPDATE in trigs and bad == 0
        print("✅ 门禁就绪" if ok else "❌ 门禁未就绪")
        return ok
    finally:
        conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description="matches.handicap 非竞彩写入门禁")
    ap.add_argument("--verify", action="store_true", help="只验证，不改库")
    args = ap.parse_args()

    if args.verify:
        return 0 if verify() else 1
    apply_gate()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())