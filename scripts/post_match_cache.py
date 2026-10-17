# -*- coding: utf-8 -*-
"""SofaScore 赛后明细（stats/lineups/incidents）持久化缓存。

C-20261002-001 根治改造：collect_post_match() 原产物仅存活于内存，
A4 复盘重生成若不带 --collect，关键事件时间线/阵容异动/跑动维度全部降级为空，
且 md 一旦重渲染，曾采集过的 incidents 永久丢失（235 份复盘时间线全缺的根因）。

本模块把整份 collect_post_match 结果（含 stats/lineups/incidents）按 match_id
落库到 sofascore_post_match_cache，三处入口（A2 主管道 / A3 归因 CLI / A4 复盘生成器）
统一走「缓存优先，允许采集时缓存 miss 才请求 SofaScore 并回填」。

表结构（整体 JSON 列，与 model_predictions.input_snapshot_json 等既有 JSON 列惯例一致；
三段 payload 均为解析后的纯 JSON 类型，下游按 dict 访问）：
    sofascore_post_match_cache(
        match_id     TEXT PRIMARY KEY,  -- odds.db 口径，如 2026-09-21_AC Milan_Lecce
        event_id     TEXT,              -- SofaScore event id
        payload_json TEXT NOT NULL,     -- collect_post_match 完整返回
        fetched_at   TEXT NOT NULL      -- ISO8601 本地时间
    )
"""
from __future__ import annotations

import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent

# get_post_data 状态码：
#   cache        命中本地缓存（未发 HTTP 请求）
#   fetched      缓存 miss，实时采集成功并已落库
#   no_cache     不允许采集且本地无缓存
#   no_mapping   fbref_match_mapping 无 event_id（fuzzy 回退也失败）
#   fetch_failed SofaScore 请求失败或比赛未结束（不落库，可重试）

_DDL = """
CREATE TABLE IF NOT EXISTS sofascore_post_match_cache (
    match_id     TEXT PRIMARY KEY,
    event_id     TEXT,
    payload_json TEXT NOT NULL,
    fetched_at   TEXT NOT NULL
)
"""


def init_cache_table(conn: sqlite3.Connection) -> None:
    """幂等建表（IF NOT EXISTS），各入口调用前确保表存在。"""
    conn.execute(_DDL)
    conn.commit()


def load_cached_post_data(conn: sqlite3.Connection,
                          match_id: str) -> Optional[Dict[str, Any]]:
    """读缓存；表/行不存在或 JSON 损坏返回 None。"""
    init_cache_table(conn)
    row = conn.execute(
        "SELECT payload_json FROM sofascore_post_match_cache WHERE match_id=?",
        (match_id,),
    ).fetchone()
    if not row or not row[0]:
        return None
    try:
        return json.loads(row[0])
    except json.JSONDecodeError:
        return None


def save_post_data(conn: sqlite3.Connection, match_id: str, event_id: Optional[str],
                   payload: Dict[str, Any]) -> None:
    """幂等 upsert 一份赛后明细（INSERT OR REPLACE，重采即覆盖）。"""
    init_cache_table(conn)
    conn.execute(
        "INSERT OR REPLACE INTO sofascore_post_match_cache"
        "(match_id, event_id, payload_json, fetched_at) VALUES (?,?,?,?)",
        (match_id, str(event_id) if event_id else None,
         json.dumps(payload, ensure_ascii=False),
         datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()


def get_post_data(conn: sqlite3.Connection, match_id: str, logger: logging.Logger,
                  *, allow_fetch: bool,
                  force_refresh: bool = False) -> Tuple[Optional[Dict[str, Any]], str]:
    """缓存优先获取赛后明细。

    参数:
        allow_fetch:  True（默认 --collect 语义）缓存 miss 时实时采集并落库；
                      False（--no-collect）只读缓存，绝不发 HTTP。
        force_refresh: True 时跳过缓存强制重采（手动修正用）。

    返回 (payload|None, state)；state 取值见模块头注释。
    """
    if not force_refresh:
        cached = load_cached_post_data(conn, match_id)
        if cached is not None:
            logger.info(f"[{match_id}] 赛后明细命中本地缓存（sofascore_post_match_cache）")
            return cached, "cache"

    if not allow_fetch:
        return None, "no_cache"

    # 延迟导入：collection 目录依赖仅在采集时需要
    sys.path.insert(0, str(PROJECT_DIR / "scripts"))
    from attribution_engine import get_event_id

    event_id = get_event_id(conn, match_id)
    if not event_id:
        logger.warning(f"[{match_id}] 无 fbref_match_mapping 映射，跳过赛后采集")
        return None, "no_mapping"

    sys.path.insert(0, str(PROJECT_DIR / "collection"))
    from final_sofascore_collector import SofaScoreClient, collect_post_match

    client = SofaScoreClient(logger=logger)
    try:
        try:
            payload = collect_post_match(client, event_id, logger)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{match_id}] 赛后采集失败: {e}")
            return None, "fetch_failed"
        if payload is None:
            logger.warning(f"[{match_id}] 赛后采集无数据（未完赛或 detail 缺失），不落库")
            return None, "fetch_failed"
        save_post_data(conn, match_id, event_id, payload)
        logger.info(f"[{match_id}] 赛后明细已采集并落库 sofascore_post_match_cache")
        return payload, "fetched"
    finally:
        client.close()
