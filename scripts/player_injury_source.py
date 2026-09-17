# -*- coding: utf-8 -*-
"""P3 球员伤病数据源升级：官方伤病源接入 + 官方/推算区分。

问题背景（docs/基于预测报告发现的问题.txt §4.2 P1-14 + §9 P3 清单）：
  现有球员可用性（pa_availability / pa_missing_impact / 预计首发）全部为「纯历史推算」
  （source=inferred，由 player_availability_features 基于历史出场连续性推算），无官方
  赛前伤病/停赛源。本模块落地官方伤病数据模型 + 官方/推算区分，使官方确认的伤病能
  覆盖（或标注）推算结果，为报告「伤病确定度」与置信度扣分提供 source 证据。

官方源现状（探测结论 C-20260915）：
  - transfermarkt.com 伤病页 requests 直连返回 405（Cloudflare 反爬）
  - premierleague.com 伤病页返回 404（URL 已迁移）
  → 纯 requests 采集不可靠，需浏览器自动化（Playwright）或用户 Edge cookies 注入；
    本模块交付结构化数据模型 + 采集/导入框架 + 纯函数校正逻辑，采集细节由运行时提供。

数据模型：
  player_injuries 表（source ∈ {'official', 'inferred'}）
    official  = 官方发布会 / 球队社媒 / Transfermarkt 伤停名单（人工或浏览器采集）
    inferred  = 历史出场连续性推算（player_availability_features，落库作对照基线）

用法：
  python scripts/player_injury_source.py --ensure-table
  python scripts/player_injury_source.py --import-json data/injury.json

纯函数（可单测）：
  - apply_official_injuries(predicted_xi, official_out): 官方缺阵名单校正推算首发
  - normalize_injury_records(records): 清洗官方伤停 JSON → InjuryRecord 列表
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Set

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"

VALID_SOURCES = ("official", "inferred")


@dataclass
class InjuryRecord:
    """单条伤病/缺阵记录。"""
    team: str                 # 英文 fbref 队名（与 match_player_stats.team 一致）
    player_name: str          # 英文球员名（与 match_player_stats.player_name 一致）
    status: str = "out"       # out / doubtful / suspended / recovered
    expected_return: Optional[str] = None   # "2026-09-20" 或 None=未知
    source: str = "official"  # official / inferred
    source_url: Optional[str] = None

    def __post_init__(self):
        if self.source not in VALID_SOURCES:
            raise ValueError(f"非法 source: {self.source}（须为 {VALID_SOURCES}）")

    def is_out(self) -> bool:
        return self.status in ("out", "suspended")


# ==================== 纯函数（可单测） ====================

def apply_official_injuries(predicted_xi: List[str],
                            official_out: List[str]) -> Dict:
    """用官方确认缺阵名单校正「推算首发」。

    Args:
        predicted_xi: 推算首发 11 人（player_availability_features.predict_xi_players）
        official_out: 官方确认缺阵球员（source=official，status=out/suspended）

    Returns:
        - predicted_xi: 原推算首发
        - confirmed_out: 推算首发中官方确认缺阵的球员（推算「漏判」的缺阵）
        - adjusted_xi: 去掉官方缺阵后的推算首发（需替补补位）
        - n_confirmed_out: 官方命中推算首发的缺阵人数
        - coverage_of_official: 官方缺阵名单被推算首发「意识到」的比例（越高越好，0 表示推算完全没发现）
    """
    predicted = set(predicted_xi)
    official = set(official_out)
    confirmed_out = predicted & official
    return {
        "source": "official",
        "predicted_xi": sorted(predicted),
        "confirmed_out": sorted(confirmed_out),
        "n_confirmed_out": len(confirmed_out),
        "adjusted_xi": sorted(predicted - official),
        "coverage_of_official": (len(confirmed_out) / len(official)) if official else 0.0,
    }


def normalize_injury_records(records: List[Dict]) -> List[InjuryRecord]:
    """清洗外部伤病 JSON → InjuryRecord 列表，丢弃非法/缺失关键字段的记录。"""
    out = []
    for r in records:
        try:
            team = (r.get("team") or "").strip()
            player = (r.get("player_name") or "").strip()
            if not team or not player:
                continue
            rec = InjuryRecord(
                team=team,
                player_name=player,
                status=(r.get("status") or "out").strip(),
                expected_return=r.get("expected_return"),
                source=(r.get("source") or "official").strip(),
                source_url=r.get("source_url"),
            )
            out.append(rec)
        except (ValueError, AttributeError):
            continue
    return out


# ==================== 落库 ====================

def ensure_injury_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """CREATE TABLE IF NOT EXISTS player_injuries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team TEXT NOT NULL,
            player_name TEXT NOT NULL,
            status TEXT DEFAULT 'out',
            expected_return TEXT,
            source TEXT DEFAULT 'official',
            source_url TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(team, player_name, status, source)
        )"""
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_injury_team ON player_injuries(team)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_injury_source ON player_injuries(source)")
    conn.commit()


def upsert_injury_records(conn: sqlite3.Connection, records: List[InjuryRecord]) -> int:
    ensure_injury_table(conn)
    n = 0
    for rec in records:
        cur = conn.execute(
            """INSERT OR IGNORE INTO player_injuries
               (team, player_name, status, expected_return, source, source_url)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (rec.team, rec.player_name, rec.status, rec.expected_return, rec.source, rec.source_url),
        )
        n += cur.rowcount
    conn.commit()
    return n


def load_official_out(conn: sqlite3.Connection, team: str) -> Set[str]:
    """读取某队官方确认缺阵球员集合（source=official，status=out/suspended）。"""
    cur = conn.execute(
        "SELECT player_name FROM player_injuries "
        "WHERE team=? AND source='official' AND status IN ('out','suspended')",
        (team,),
    )
    return {r[0] for r in cur.fetchall()}


# ==================== SofaScore match_missing_players 回填 ====================

# SofaScore reason → InjuryRecord.status 映射
# reason: 伤病/停赛/转会/其他/未知 → status: out/suspended/recovered/out/out
SOFASCORE_REASON_TO_STATUS: Dict[str, str] = {
    "伤病": "out",          # 缺阵 → out
    "停赛": "suspended",    # 停赛 → suspended
    "转会": "recovered",    # 已转会 → 视为"非伤病缺阵"，标记 recovered（不参与缺阵计算）
    "其他": "out",
    "未知": "out",
}

# 转会类 reason → 直接跳过（离队球员不记入球队伤病表）
SOFASCORE_SKIP_REASONS: Set[str] = {"转会"}


def map_sofascore_reason(reason: str) -> Optional[str]:
    """SofaScore reason → InjuryRecord.status；转会类返回 None（跳过）。"""
    r = (reason or "").strip()
    if r in SOFASCORE_SKIP_REASONS:
        return None
    return SOFASCORE_REASON_TO_STATUS.get(r, "out")


def sofascore_missing_to_injury(mp: Dict[str, Any]) -> Optional[InjuryRecord]:
    """把单条 match_missing_players 行转成 InjuryRecord。

    转会类（reason=转会）返回 None（跳过，不写入 player_injuries）。
    source 固定为 'official'（SofaScore 官方缺阵口径）。
    """
    reason = mp.get("reason") or ""
    status = map_sofascore_reason(reason)
    if status is None:
        return None
    team = (mp.get("team") or "").strip()
    player = (mp.get("player_name") or "").strip()
    if not team or not player:
        return None
    expected_end = mp.get("expected_end_date") or None
    if expected_end:
        expected_end = str(expected_end).strip() or None
    # 用 sofascore_slug 或 match_id 拼 source_url 作为溯源
    source_url = None
    slug = mp.get("sofascore_slug")
    if slug:
        source_url = f"https://www.sofascore.com/player/{slug}"
    return InjuryRecord(
        team=team,
        player_name=player,
        status=status,
        expected_return=expected_end,
        source="official",
        source_url=source_url,
    )


def sync_from_sofascore_missing(conn: sqlite3.Connection, limit: Optional[int] = None) -> Dict[str, int]:
    """从 match_missing_players（SofaScore 官方缺阵）回填到 player_injuries。

    返回统计：{'scanned': N, 'skipped': N, 'inserted': N, 'by_status': {status: N}}。

    注意：
      - player_injuries 的 UNIQUE(team, player_name, status, source) 用于幂等去重；
      - 取 DISTINCT team+player+reason+expected_end_date，避免同球员跨多场重复写；
      - 转会类（reason=转会）跳过，不写入。
    """
    ensure_injury_table(conn)
    cur = conn.cursor()
    sql = (
        "SELECT DISTINCT team, player_name, reason, expected_end_date, sofascore_slug "
        "FROM match_missing_players "
        "WHERE player_name IS NOT NULL AND team IS NOT NULL "
        "ORDER BY team, player_name "
    )
    if limit:
        sql += f" LIMIT {int(limit)}"
    rows = cur.execute(sql).fetchall()

    scanned = 0
    skipped = 0
    inserted = 0
    by_status: Dict[str, int] = {}
    records: List[InjuryRecord] = []
    for row in rows:
        scanned += 1
        mp = {
            "team": row[0],
            "player_name": row[1],
            "reason": row[2],
            "expected_end_date": row[3],
            "sofascore_slug": row[4],
        }
        rec = sofascore_missing_to_injury(mp)
        if rec is None:
            skipped += 1
            continue
        records.append(rec)
        by_status[rec.status] = by_status.get(rec.status, 0) + 1

    inserted = upsert_injury_records(conn, records)
    return {
        "scanned": scanned,
        "skipped": skipped,
        "inserted": inserted,
        "by_status": by_status,
    }


# ==================== CLI ====================

def main():
    parser = argparse.ArgumentParser(description="球员伤病数据源升级（官方/推算区分，P3）")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--ensure-table", action="store_true", help="仅建表")
    parser.add_argument("--import-json", type=str, default=None,
                        help="导入官方伤停 JSON（格式：[{team, player_name, status, ...}]）")
    parser.add_argument("--sync-sofascore", action="store_true",
                        help="从 match_missing_players（SofaScore 官方缺阵）回填 player_injuries")
    parser.add_argument("--limit", type=int, default=None,
                        help="--sync-sofascore 时的最大处理条数（调试用）")
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    if args.import_json:
        data = json.loads(Path(args.import_json).read_text(encoding="utf-8"))
        recs = normalize_injury_records(data)
        n = upsert_injury_records(conn, recs)
        print(f"导入官方伤停记录: {n} 条（源数据 {len(data)} 条，清洗后 {len(recs)} 条）")
    elif args.sync_sofascore:
        stats = sync_from_sofascore_missing(conn, limit=args.limit)
        print("SofaScore 官方缺阵回填 player_injuries 完成：")
        print(f"  扫描 {stats['scanned']} 条，跳过 {stats['skipped']} 条（转会等），"
              f"新写入 {stats['inserted']} 条")
        print(f"  按状态分布：{stats['by_status']}")
        total = conn.execute(
            "SELECT source, COUNT(*) FROM player_injuries GROUP BY source"
        ).fetchall()
        print(f"  player_injuries 当前总数（按 source）：{dict(total)}")
    else:
        ensure_injury_table(conn)
        print("player_injuries 表已就绪（source ∈ official/inferred）")
    conn.close()


if __name__ == "__main__":
    main()