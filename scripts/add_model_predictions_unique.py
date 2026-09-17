# -*- coding: utf-8 -*-
"""
为 model_predictions 表添加唯一约束 UNIQUE(match_id, model_name, prediction_type)
并清理历史重复数据。
===============================================
背景：savePreMatchPrediction / batchSavePredictions 原先对 model_predictions 使用
      普通 INSERT（非 INSERT OR IGNORE），导致同一场比赛重复调用时会累积重复预测行。
      matches / fbref_match_mapping 已用 INSERT OR IGNORE，但 model_predictions 缺失
      唯一约束 + 幂等写入，造成训练回读时同一比赛被多次采样。

处理：
  1. 统计并清理重复 (match_id, model_name, prediction_type) 组，每组仅保留最小 id。
  2. 创建唯一索引 idx_model_predictions_unique，使 INSERT OR IGNORE 生效。
  3. 打印清理前后数据量，做幂等校验（脚本可重复执行）。
"""

import sqlite3
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

UNIQUE_INDEX_NAME = "idx_model_predictions_unique"
UNIQUE_COLUMNS = "(match_id, model_name, prediction_type)"


def _index_exists(cur, name: str) -> bool:
    row = cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' AND name=?",
        (name,),
    ).fetchone()
    return row is not None


def main():
    conn = sqlite3.connect(str(ODDS_DB))
    cur = conn.cursor()
    conn.execute("PRAGMA foreign_keys=ON")

    total_before = cur.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0]

    print("=" * 70)
    print(f"数据库: {ODDS_DB}")
    print(f"处理前 model_predictions 总行数: {total_before}")
    print("=" * 70)

    # 1. 统计重复组
    dup_rows = cur.execute(
        f"""
        SELECT match_id, model_name, prediction_type, COUNT(*) AS cnt
        FROM model_predictions
        GROUP BY match_id, model_name, prediction_type
        HAVING COUNT(*) > 1
        ORDER BY cnt DESC
        """
    ).fetchall()

    if dup_rows:
        print(f"发现 {len(dup_rows)} 组重复数据:")
        for match_id, model_name, ptype, cnt in dup_rows:
            print(f"  - {match_id} | {model_name} | {ptype} | {cnt} 行 (保留 1 行)")
    else:
        print("未发现重复数据。")

    # 2. 清理重复：每组仅保留最小 id
    if dup_rows:
        conn.execute("BEGIN")
        # 删除所有非最小 id 的重复行
        deleted = cur.execute(
            f"""
            DELETE FROM model_predictions
            WHERE id NOT IN (
                SELECT MIN(id)
                FROM model_predictions
                GROUP BY match_id, model_name, prediction_type
            )
            """
        ).rowcount
        conn.commit()
        print(f"已清理重复行: {deleted} 行")
    else:
        print("无需清理。")

    # 3. 创建唯一索引（幂等）
    if _index_exists(cur, UNIQUE_INDEX_NAME):
        print(f"唯一索引 {UNIQUE_INDEX_NAME} 已存在，跳过创建。")
    else:
        cur.execute(
            f"CREATE UNIQUE INDEX {UNIQUE_INDEX_NAME} "
            f"ON model_predictions {UNIQUE_COLUMNS}"
        )
        conn.commit()
        print(f"已创建唯一索引 {UNIQUE_INDEX_NAME} ON model_predictions {UNIQUE_COLUMNS}")

    # 4. 校验
    total_after = cur.execute("SELECT COUNT(*) FROM model_predictions").fetchone()[0]
    dup_after = cur.execute(
        f"""
        SELECT COUNT(*) FROM (
            SELECT match_id, model_name, prediction_type, COUNT(*) AS cnt
            FROM model_predictions
            GROUP BY match_id, model_name, prediction_type
            HAVING COUNT(*) > 1
        )
        """
    ).fetchone()[0]

    print("-" * 70)
    print(f"处理后 model_predictions 总行数: {total_after}")
    print(f"剩余重复组数: {dup_after}")
    if dup_after == 0:
        print("✅ 唯一约束已生效，数据无重复。")
    else:
        print("❌ 仍存在重复，请人工排查。")

    conn.close()


if __name__ == "__main__":
    main()