# -*- coding: utf-8 -*-
"""
post_match_schema.py — 模块 A1：赛后事实表 schema 与幂等写库（P0）

===============================================
背景（模型改进实施方案 v1.0 §三/A1）：
  赛后复盘闭环完全缺失：data/odds.db 无 post_match_review 表，
  model_predictions 无 actual_* 字段。本脚本建立复盘数据基础：
    1. 新建 post_match_review 赛后事实表（UNIQUE(match_id) 幂等）
    2. 赛果以 4 行 actual_*（actual_wdl/actual_score/actual_tg/actual_hcp）
       追加写入 model_predictions，使既有回测/报告链路无需改造即可读到赛果
       （对齐 EV_decision 七行先例，UNIQUE(match_id, model_name, prediction_type) 下
       INSERT OR IGNORE 幂等）

基础设施约定（§1.3）：
  - 数据库路径动态定位：Path(__file__).resolve().parent.parent / data / odds.db，禁硬编码盘符
  - odds.db 连接必须 PRAGMA busy_timeout = 5000
  - 队名入库前统一 normalize_team_name()（scripts/team_name_mapping.py）
  - 所有写库 INSERT OR IGNORE 幂等

用法：
  python scripts/post_match_schema.py                      # 仅建表（幂等可重跑）
  python scripts/post_match_schema.py --demo               # 插入一条示例赛果并打印核对
  python scripts/post_match_schema.py --clean              # 清理示例数据（按 match_id）
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# ==================== 路径常量 ====================
PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

try:
    from team_name_mapping import normalize_team_name
except Exception:  # pragma: no cover - 独立模块兜底
    normalize_team_name = None

# ==================== DDL（对齐方案文档 §A1） ====================
POST_MATCH_REVIEW_DDL = """
CREATE TABLE IF NOT EXISTS post_match_review (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    match_id        TEXT NOT NULL,
    league          TEXT,
    match_date      TEXT,
    home_team       TEXT,            -- 规范中文名（normalize_team_name）
    away_team       TEXT,
    actual_score    TEXT,            -- 如 "2:1"
    actual_half_score TEXT,
    actual_wdl      TEXT,            -- 主胜/平局/客胜
    actual_hcp      TEXT,            -- 上盘赢/走水/下盘赢
    actual_tg       INTEGER,
    pred_wdl        TEXT,
    pred_wdl_probs  TEXT,            -- JSON [ph,pd,pa]
    pred_score_top1 TEXT,
    pred_score_top5 TEXT,            -- JSON
    pred_hcp        TEXT,
    pred_tg         TEXT,
    wdl_correct     INTEGER,         -- 0/1
    score_top1_hit  INTEGER,
    score_top5_cover INTEGER,
    hcp_correct     INTEGER,
    tg_correct      INTEGER,
    single_rps      REAL,
    single_logloss  REAL,
    prob_rank       INTEGER,         -- 1/2/3
    attribution_json TEXT,           -- 归因引擎输出
    confidence_level INTEGER,        -- 1-5（3 级起才写因子库）
    data_quality_score REAL,         -- 0-1
    human_reviewed  INTEGER DEFAULT 0,
    human_notes     TEXT,
    created_at      TEXT DEFAULT (datetime('now','localtime')),
    reviewed_at     TEXT,
    UNIQUE(match_id)
)
"""

# post_match_review 可写白名单列（不含自增 id / created_at）
REVIEW_WRITABLE_COLS = [
    "match_id", "league", "match_date", "home_team", "away_team",
    "actual_score", "actual_half_score", "actual_wdl", "actual_hcp", "actual_tg",
    "pred_wdl", "pred_wdl_probs", "pred_score_top1", "pred_score_top5",
    "pred_hcp", "pred_tg",
    "wdl_correct", "score_top1_hit", "score_top5_cover", "hcp_correct", "tg_correct",
    "single_rps", "single_logloss", "prob_rank",
    "attribution_json", "confidence_level", "data_quality_score",
    "human_reviewed", "human_notes", "reviewed_at",
]

# model_predictions 实际结果四行（prediction_type -> 取值说明）
ACTUAL_TYPES = ["actual_wdl", "actual_score", "actual_tg", "actual_hcp"]

DEFAULT_MODEL_NAME = "generate_unified_report_v2.0"
DEMO_MATCH_ID = "2026-08-22_Arsenal_Coventry City"  # 现有 model_predictions 中真实存在的 match_id


# ==================== schema 管理 ====================
def ensure_post_match_schema(conn: sqlite3.Connection) -> None:
    """幂等建表：post_match_review（UNIQUE(match_id)）。

    可重复执行；同时校验 model_predictions 唯一索引存在
    （UNIQUE(match_id, model_name, prediction_type)，actual_* 四行幂等写入的前提）。
    """
    conn.execute(POST_MATCH_REVIEW_DDL)
    conn.commit()

    cur = conn.cursor()
    cur.execute(
        "SELECT 1 FROM sqlite_master WHERE type='index' "
        "AND name='idx_model_predictions_unique' AND tbl_name='model_predictions'"
    )
    if cur.fetchone() is None:
        raise RuntimeError(
            "model_predictions 缺少唯一索引 idx_model_predictions_unique "
            "(UNIQUE(match_id, model_name, prediction_type))，无法保证 actual_* 幂等写入，"
            "请先运行 scripts/add_model_predictions_unique.py"
        )


def _normalize(name: Optional[str]) -> Optional[str]:
    """队名归一化：成功返回规范中文名，失败保留原值（避免数据丢失）。"""
    if not name:
        return None
    if normalize_team_name is not None:
        try:
            norm = normalize_team_name(name)
            if norm:
                return norm
        except Exception:
            pass
    return str(name).strip()


def _json_str(value: Any) -> Optional[str]:
    """list/dict 序列化为 JSON 字符串；str/None 原样返回。"""
    if value is None:
        return None
    if isinstance(value, (list, dict)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


# ==================== 写库 ====================
def write_post_match_review(conn: sqlite3.Connection, review: Dict[str, Any]) -> bool:
    """单场赛后事实写入 post_match_review。

    UNIQUE(match_id) 下 INSERT OR IGNORE 幂等；返回 True=新插入 / False=已存在跳过。
    只取白名单列；pred_wdl_probs / attribution_json / pred_score_top5 自动 JSON 序列化。
    """
    row = {}
    for col in REVIEW_WRITABLE_COLS:
        if col in review and review[col] is not None:
            val = review[col]
            if col in ("match_id", "home_team", "away_team"):
                row[col] = _normalize(val) if col != "match_id" else str(val).strip()
            elif col in ("pred_wdl_probs", "attribution_json", "pred_score_top5"):
                row[col] = _json_str(val)
            elif col == "actual_tg":
                row[col] = int(val)
            else:
                row[col] = val

    if "match_id" not in row:
        raise ValueError("write_post_match_review 必须提供 match_id")

    cols = list(row.keys())
    placeholders = ",".join("?" for _ in cols)
    cur = conn.cursor()
    cur.execute(
        f"INSERT OR IGNORE INTO post_match_review ({','.join(cols)}) VALUES ({placeholders})",
        list(row.values()),
    )
    conn.commit()
    return cur.rowcount > 0


def write_actual_predictions(
    conn: sqlite3.Connection,
    match_id: str,
    model_name: str,
    actual_wdl: Optional[str] = None,      # 主胜/平局/客胜
    actual_score: Optional[str] = None,    # 如 "2:1"
    actual_tg: Optional[int] = None,       # 总进球
    actual_hcp: Optional[str] = None,      # 上盘赢/走水/下盘赢（对齐 HC_* 行）
) -> int:
    """赛果 4 行 actual_* 追加写入 model_predictions（INSERT OR IGNORE 幂等）。

    probability/confidence 固定 1.0（结果已确定）；timestamp 为当前 UTC ISO 时间，
    与既有 EV_direction 行格式（ISO+00:00）一致。返回实际写入行数。
    """
    ts = datetime.now(timezone.utc).isoformat()
    rows: List[tuple] = []
    if actual_wdl:
        rows.append((match_id, model_name, "actual_wdl", actual_wdl, 1.0, 1.0, ts))
    if actual_score:
        rows.append((match_id, model_name, "actual_score", str(actual_score), 1.0, 1.0, ts))
    if actual_tg is not None:
        rows.append((match_id, model_name, "actual_tg", str(int(actual_tg)), 1.0, 1.0, ts))
    if actual_hcp:
        rows.append((match_id, model_name, "actual_hcp", actual_hcp, 1.0, 1.0, ts))

    if not rows:
        return 0

    cur = conn.cursor()
    cur.executemany(
        "INSERT OR IGNORE INTO model_predictions "
        "(match_id, model_name, prediction_type, prediction, probability, confidence, timestamp) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    return cur.rowcount


def write_review_and_actuals(
    conn: sqlite3.Connection,
    review: Dict[str, Any],
    model_name: str = DEFAULT_MODEL_NAME,
) -> Dict[str, int]:
    """A2/A3/A4 统一入口：一次调用完成 post_match_review 写入 + 4 行 actual_* 写入。

    review 需含 match_id/actual_wdl/actual_score/actual_tg/actual_hcp。
    返回 {"review": 0/1, "actuals": 写入行数}。
    """
    review_inserted = 1 if write_post_match_review(conn, review) else 0
    actuals = write_actual_predictions(
        conn,
        review["match_id"],
        model_name,
        actual_wdl=review.get("actual_wdl"),
        actual_score=review.get("actual_score"),
        actual_tg=review.get("actual_tg"),
        actual_hcp=review.get("actual_hcp"),
    )
    return {"review": review_inserted, "actuals": actuals}


# ==================== 校验 / 清理 ====================
def verify_review(conn: sqlite3.Connection, match_id: str) -> Dict[str, Any]:
    """核对两表写入结果，供验证/报告使用。"""
    cur = conn.cursor()
    cur.execute("SELECT * FROM post_match_review WHERE match_id=?", (match_id,))
    cols = [d[0] for d in cur.description] if cur.description else []
    review_row = cur.fetchone()

    cur.execute(
        "SELECT prediction_type, prediction, probability FROM model_predictions "
        "WHERE match_id=? AND prediction_type IN ('actual_wdl','actual_score','actual_tg','actual_hcp') "
        "ORDER BY prediction_type",
        (match_id,),
    )
    actual_rows = cur.fetchall()
    return {
        "review_columns": cols,
        "review_row": dict(zip(cols, review_row)) if review_row else None,
        "actual_rows": [dict(zip(["prediction_type", "prediction", "probability"], r)) for r in actual_rows],
    }


def clean_review(conn: sqlite3.Connection, match_id: str) -> int:
    """按 match_id 清理示例数据：post_match_review 1 行 + model_predictions 4 行 actual_*。

    返回删除总行数。
    """
    cur = conn.cursor()
    d1 = cur.execute("DELETE FROM post_match_review WHERE match_id=?", (match_id,)).rowcount
    d2 = cur.execute(
        "DELETE FROM model_predictions WHERE match_id=? AND prediction_type IN "
        "('actual_wdl','actual_score','actual_tg','actual_hcp')",
        (match_id,),
    ).rowcount
    conn.commit()
    return d1 + d2


# ==================== 主入口 ====================
def _demo(conn: sqlite3.Connection, match_id: str) -> None:
    review = {
        "match_id": match_id,
        "league": "英超",
        "match_date": "2026-08-22",
        "home_team": "阿森纳",
        "away_team": "考文垂",
        "actual_score": "2:1",
        "actual_half_score": "1:0",
        "actual_wdl": "主胜",
        "actual_hcp": "上盘赢",
        "actual_tg": 3,
    }
    result = write_review_and_actuals(conn, review)
    print(f"写入结果: review={result['review']} actuals={result['actuals']}")
    verify = verify_review(conn, match_id)
    print(f"post_match_review 列数: {len(verify['review_columns'])}")
    print(f"post_match_review 行: {verify['review_row']}")
    print("model_predictions actual_* 行:")
    for r in verify["actual_rows"]:
        print("  ", r)


def main() -> None:
    parser = argparse.ArgumentParser(description="模块 A1：赛后事实表 schema 与幂等写库")
    parser.add_argument("--demo", nargs="?", const=DEMO_MATCH_ID, default=None, metavar="MATCH_ID",
                        help="插入一条示例赛果并打印核对（默认 match_id=%s）" % DEMO_MATCH_ID)
    parser.add_argument("--clean", nargs="?", const=DEMO_MATCH_ID, default=None, metavar="MATCH_ID",
                        help="按 match_id 清理示例数据（默认 match_id=%s）" % DEMO_MATCH_ID)
    args = parser.parse_args()

    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    try:
        ensure_post_match_schema(conn)
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) FROM post_match_review")
        print(f"数据库: {ODDS_DB}")
        print("post_match_review 表已就绪（幂等建表），当前行数:", cur.fetchone()[0])

        if args.clean:
            deleted = clean_review(conn, args.clean)
            print(f"已清理 {args.clean}: 删除 {deleted} 行")
        elif args.demo:
            _demo(conn, args.demo)
        else:
            cur.execute("SELECT COUNT(*) FROM model_predictions")
            print("model_predictions 当前行数:", cur.fetchone()[0])
    finally:
        conn.close()


if __name__ == "__main__":
    main()
