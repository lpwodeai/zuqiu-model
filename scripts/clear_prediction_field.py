# -*- coding: utf-8 -*-
"""
清空 model_predictions 表的 prediction 字段
===============================================
背景：西甲第1轮 4 场比赛的 model_predictions 记录中，
      probability / confidence 已置为 NULL（无真实预测概率），
      但 prediction 字段仍是占位值（主胜/客胜等），导致数据不一致。

处理：
  1. prediction 列当前为 TEXT NOT NULL，无法置 NULL；先重建表将其改为可空
     （与 probability / confidence 的可空语义保持一致）。
  2. 对 probability IS NULL AND confidence IS NULL 的记录，
     将 prediction 同步置为 NULL。
  3. 打印处理前后的行内容并做残留校验。
"""

import sqlite3
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"


def _prediction_nullable(cur) -> bool:
    """判断 prediction 列是否可空。"""
    row = cur.execute("PRAGMA table_info(model_predictions)").fetchall()
    # table_info 列顺序: cid, name, type, notnull, dflt_value, pk
    for col in row:
        if col[1] == "prediction":
            return col[3] == 0  # notnull == 0 表示可空
    return False


def rebuild_prediction_nullable(conn):
    """重建 model_predictions 表，将 prediction 从 NOT NULL 改为可空，并保留数据/id。"""
    cur = conn.cursor()
    cur.execute("PRAGMA foreign_keys=OFF")
    cur.execute("BEGIN")
    cur.execute(
        """CREATE TABLE model_predictions_new (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              match_id TEXT NOT NULL,
              model_name TEXT NOT NULL,
              prediction_type TEXT NOT NULL,
              prediction TEXT,
              probability REAL,
              confidence REAL,
              timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
              FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE
           )"""
    )
    cur.execute(
        """INSERT INTO model_predictions_new
              (id, match_id, model_name, prediction_type, prediction, probability, confidence, timestamp)
           SELECT id, match_id, model_name, prediction_type, prediction, probability, confidence, timestamp
           FROM model_predictions"""
    )
    cur.execute("DROP TABLE model_predictions")
    cur.execute("ALTER TABLE model_predictions_new RENAME TO model_predictions")
    # 修复 AUTOINCREMENT 序列
    cur.execute("DELETE FROM sqlite_sequence WHERE name='model_predictions'")
    cur.execute(
        "INSERT INTO sqlite_sequence (name, seq) SELECT 'model_predictions', COALESCE(MAX(id),0) FROM model_predictions"
    )
    conn.commit()
    cur.execute("PRAGMA foreign_keys=ON")


def main():
    conn = sqlite3.connect(str(ODDS_DB))
    cur = conn.cursor()

    target_sql = "probability IS NULL AND confidence IS NULL"

    print("=" * 70)
    print("处理前（目标记录）")
    print("=" * 70)
    for r in cur.execute(
        "SELECT match_id, prediction, probability, confidence "
        f"FROM model_predictions WHERE {target_sql}"
    ):
        print("  ", r)

    if not _prediction_nullable(cur):
        print("\n[prediction 列为 NOT NULL] 重建表使其可空…")
        rebuild_prediction_nullable(conn)
        cur = conn.cursor()
        print("  重建完成。prediction 列已可空。\n")

    cur.execute(f"UPDATE model_predictions SET prediction = NULL WHERE {target_sql}")
    affected = cur.rowcount
    conn.commit()

    print("=" * 70)
    print("处理后（prediction 应均为 None）")
    print("=" * 70)
    for r in cur.execute(
        "SELECT match_id, prediction, probability, confidence "
        f"FROM model_predictions WHERE {target_sql}"
    ):
        print("  ", r)
    print(f"  更新影响 {affected} 条\n")

    residual = cur.execute(
        f"SELECT COUNT(*) FROM model_predictions WHERE {target_sql} AND prediction IS NOT NULL"
    ).fetchone()[0]
    total = cur.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0]
    print(f"残留（prob/conf 为空但仍带 prediction）: {residual} 条")
    print(f"model_predictions 当前总行数: {total}")
    conn.close()
    print("[完成] prediction 字段已清空，与 probability/confidence 保持一致。")


if __name__ == "__main__":
    main()