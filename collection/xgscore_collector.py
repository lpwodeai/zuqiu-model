# -*- coding: utf-8 -*-
"""
xgscore_collector.py — xgscore.io 赛后 xG 数据采集器（Understat fallback）
================================================================
背景（C-20260922）：
  Understat 在部分赛季/联赛覆盖不全（如 26/27 意甲 AC米兰 vs 莱切无数据），
  归因引擎维度4（运气偏差）依赖 xG，缺失会导致归因失效。
  xgscore.io 是 SSR 页面，xG 数据嵌入 HTML，无需认证，可作为 Understat 的
  fallback 数据源补全比赛级 xG。

数据源与端点（已验证 2026-09-22）：
  比赛页  GET https://xgscore.io/{league_slug}/{home_slug}-{away_slug}-{dd-mm-yy}/xg-statistics
          - league_slug: epl / la-liga / serie-a / bundesliga / league-1
          - home_slug/away_slug: 见 team_name_mapping.XGSCORE_TEAM_SLUGS
          - dd-mm-yy: UTC 日期（北京时间 -8h），例 北京 9/21 05:00 → 20-09-26
          - SSR 页面，xG 嵌入 HTML text-primary（主队）/text-secondary（客队）标签
  联赛页  GET https://xgscore.io/xg-statistics/{league_slug}
          - 含比赛列表与 logo URL（slug = logo 文件名）

提取规则（实测 sunderland-chelsea-24-05-26 验证）：
  - text-primary 第一个数值 = 主队 xG（2.05）
  - text-secondary 第一个数值 = 客队 xG（0.89）
  - 第二个数值对 = 比分（goals）
  与 xgscore 比赛列表显示的 xG 一致。

落库：写入 xgscore_match_stats 表（与 understat_match_team_stats 分离，不污染
Understat 数据）。归因引擎 get_understat_xg 在 Understat 无数据时 fallback
先查此表，再实时抓取。

用法：
  # 单场抓取（不落库）
  python collection/xgscore_collector.py --league 意甲 --home AC米兰 --away 莱切 --date 2026-09-21

  # 单场抓取并落库
  python collection/xgscore_collector.py --league 意甲 --home AC米兰 --away 莱切 --date 2026-09-21 --write

  # 作为模块被 attribution_engine 调用：
  from collection.xgscore_collector import fetch_match_xg
  result = fetch_match_xg("意甲", "AC米兰", "莱切", "2026-09-21")
"""

from __future__ import annotations

import argparse
import json
import logging
import re
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional

import requests

# ============================================================
# 路径与配置
# ============================================================

MODEL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = MODEL_PROJECT_ROOT / "data" / "odds.db"
LOG_DIR = MODEL_PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BUSY_TIMEOUT_MS = 30000
CONNECT_TIMEOUT = 30  # sqlite3.connect timeout（秒）

BASE_URL = "https://xgscore.io"

# 联赛 slug（中英文 → xgscore URL slug）
# 中文 key 与 team_name_mapping.XGSCORE_LEAGUE_SLUGS 一致，额外收录英文别名
LEAGUE_SLUGS: Dict[str, str] = {
    # 中文
    "英超": "epl", "西甲": "la-liga", "意甲": "serie-a",
    "德甲": "bundesliga", "法甲": "league-1",
    # 英文（Understat/matches.league 可能存英文）
    "EPL": "epl", "La_liga": "la-liga", "Serie_A": "serie-a",
    "Bundesliga": "bundesliga", "Ligue_1": "league-1",
    "Premier League": "epl", "La Liga": "la-liga", "Serie A": "serie-a",
    "Ligue 1": "league-1",
}

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/153.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# ============================================================
# 建表 SQL（与 understat_match_team_stats 分离，避免污染 Understat 数据）
# ============================================================

CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS xgscore_match_stats (
    match_id     TEXT PRIMARY KEY,
    league       TEXT,
    match_date   TEXT,
    home_team    TEXT,
    away_team    TEXT,
    home_xg      REAL,
    away_xg      REAL,
    home_goals   INTEGER,
    away_goals   INTEGER,
    xg_source    TEXT,
    collected_at TEXT
)
"""

# ============================================================
# 日志
# ============================================================


def setup_logger() -> logging.Logger:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"xgscore_collector_{ts}.log"
    logger = logging.getLogger("xgscore")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter("%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"))
    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info("=" * 70)
    logger.info("xgscore.io xG 采集器启动（Understat fallback）")
    logger.info(f"日志文件: {log_file}")
    logger.info(f"数据库  : {DB_PATH}")
    logger.info("=" * 70)
    return logger


# ============================================================
# 工具函数
# ============================================================

# 引入队名 slug 映射（team_name_mapping 在 scripts/ 目录）
sys.path.insert(0, str(MODEL_PROJECT_ROOT / "scripts"))
from team_name_mapping import to_xgscore_slug, normalize_team_name  # noqa: E402


def _resolve_league_slug(league: str) -> Optional[str]:
    """联赛名（中/英文）→ xgscore URL slug。"""
    if not league:
        return None
    if league in LEAGUE_SLUGS:
        return LEAGUE_SLUGS[league]
    # 模糊：去空格/下划线后比对
    key = league.strip().lower().replace("_", " ").replace("-", " ")
    for k, v in LEAGUE_SLUGS.items():
        if k.strip().lower().replace("_", " ").replace("-", " ") == key:
            return v
    return None


def _cn_to_en_team(cn_name: str) -> Optional[str]:
    """中文队名→英文（用于落库 home_team/away_team 字段统一英文口径）。
    复用 attribution_engine 的逻辑：从 TEAM_ALIASES 取首个英文别名。
    """
    if not cn_name:
        return None
    if cn_name.isascii():
        return cn_name
    from team_name_mapping import TEAM_ALIASES  # noqa: WPS433
    std = normalize_team_name(cn_name)
    if std and std in TEAM_ALIASES:
        for alias in TEAM_ALIASES[std]:
            if alias.isascii():
                return alias
    return cn_name


def _utc_date_candidates(beijing_date: str) -> list:
    """北京时间日期（YYYY-MM-DD）→ UTC 日期候选列表（dd-mm-yy 字符串）。

    北京时间 = UTC+8。比赛 date 只有日期无开球时间，无法精确算 UTC 日期：
    - 北京 9/21 晚场比赛，UTC 仍是 9/21
    - 北京 9/21 凌晨场（如 05:00），UTC 是 9/20
    因此生成 [date-1, date] 两个 UTC 候选，由调用方依次尝试 404 检测。
    """
    try:
        d = datetime.strptime(beijing_date[:10], "%Y-%m-%d")
    except (ValueError, TypeError):
        return []
    cands = []
    for offset in (-1, 0):
        utc_d = d + timedelta(days=offset)
        cands.append(utc_d.strftime("%d-%m-%y"))
    return cands


def _build_url(league_slug: str, home_slug: str, away_slug: str, dd_mm_yy: str) -> str:
    """构造 xgscore 比赛页 URL。"""
    return f"{BASE_URL}/{league_slug}/{home_slug}-{away_slug}-{dd_mm_yy}/xg-statistics"


def http_get(url: str, logger: logging.Logger, retries: int = 2) -> Optional[requests.Response]:
    """带重试的 GET 请求。"""
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code == 200:
                return r
            # 404 表示比赛不存在（slug/日期不对），重试无意义
            if r.status_code == 404:
                logger.debug(f"404 无数据: {url}")
                return None
            logger.warning(f"请求失败 {r.status_code}: {url} (第{attempt}次)")
        except Exception as e:
            logger.warning(f"请求异常: {url} ({e}) (第{attempt}次)")
        time.sleep(1.0 * attempt)
    return None


# xG/goals 正则：text-primary（主队）/text-secondary（客队）数值
_RE_PRIMARY = re.compile(r'text-primary">\s*([0-9]+\.?[0-9]*)')
_RE_SECONDARY = re.compile(r'text-secondary">\s*([0-9]+\.?[0-9]*)')
# 比分（meta description 或 og:title：Sunderland 2 - 1 Chelsea）
_RE_SCORE_META = re.compile(
    r'content="([^"]+?)\s+(\d+)\s*-\s*(\d+)\s+([^"]+?)"', re.IGNORECASE)


def _parse_xg(html: str) -> Optional[Dict[str, Any]]:
    """从 xgscore 比赛页 HTML 提取主客 xG 和比分。

    策略（实测 sunderland-chelsea-24-05-26 验证）：
      text-primary 第一个数值 = 主队 xG
      text-secondary 第一个数值 = 客队 xG
      第二个数值对 = 比分
    """
    p_vals = _RE_PRIMARY.findall(html)
    s_vals = _RE_SECONDARY.findall(html)
    if len(p_vals) < 2 or len(s_vals) < 2:
        return None
    try:
        home_xg = float(p_vals[0])
        away_xg = float(s_vals[0])
        home_goals = int(float(p_vals[1]))
        away_goals = int(float(s_vals[1]))
    except (ValueError, IndexError):
        return None
    # 健全性：xG 应在 [0, 15] 区间，比分应在 [0, 20]
    if not (0 <= home_xg <= 15 and 0 <= away_xg <= 15):
        return None
    if not (0 <= home_goals <= 20 and 0 <= away_goals <= 20):
        return None
    return {
        "home_xg": home_xg,
        "away_xg": away_xg,
        "home_goals": home_goals,
        "away_goals": away_goals,
    }


# ============================================================
# 核心采集函数
# ============================================================


def fetch_match_xg(
    league: str,
    home_name: str,
    away_name: str,
    date_str: str,
    logger: Optional[logging.Logger] = None,
    write_db: bool = False,
) -> Optional[Dict[str, Any]]:
    """采集单场比赛 xG（Understat fallback）。

    参数：
      league    联赛名（中文如"意甲"或英文如"Serie_A"）
      home_name 主队名（中文/英文/Understat 英文均可，经 to_xgscore_slug 归一化）
      away_name 客队名
      date_str  北京时间日期（YYYY-MM-DD 或 YYYY-MM-DD HH:MM:SS）
      logger    可选日志器
      write_db  是否落库到 xgscore_match_stats 表

    返回：{"home_xg","away_xg","home_goals","away_goals"} 或 None
    """
    if logger is None:
        logger = setup_logger()

    league_slug = _resolve_league_slug(league)
    home_slug = to_xgscore_slug(home_name)
    away_slug = to_xgscore_slug(away_name)
    if not league_slug:
        logger.warning(f"无法解析联赛 slug: {league}")
        return None
    if not home_slug or not away_slug:
        logger.warning(f"无法解析队名 slug: home={home_name}→{home_slug}, away={away_name}→{away_slug}")
        return None

    date_candidates = _utc_date_candidates(date_str)
    if not date_candidates:
        logger.warning(f"无法解析日期: {date_str}")
        return None

    # 依次尝试 UTC 日期候选（date-1, date）
    for dd_mm_yy in date_candidates:
        url = _build_url(league_slug, home_slug, away_slug, dd_mm_yy)
        logger.info(f"尝试抓取: {url}")
        r = http_get(url, logger)
        if r is None:
            continue
        result = _parse_xg(r.text)
        if result is None:
            logger.debug(f"解析失败（可能页面结构异常）: {url}")
            continue
        logger.info(
            f"✓ 命中 {home_slug} vs {away_slug} ({dd_mm_yy}): "
            f"xG {result['home_xg']}-{result['away_xg']}, 比分 {result['home_goals']}-{result['away_goals']}"
        )
        if write_db:
            _write_db(league, date_str[:10], home_name, away_name, result, logger)
        return result

    logger.warning(
        f"✗ 未命中: {league}/{home_slug}-{away_slug} date={date_str} "
        f"(尝试 {len(date_candidates)} 个 UTC 日期候选)"
    )
    return None


def _write_db(
    league: str,
    date_str: str,
    home_name: str,
    away_name: str,
    result: Dict[str, Any],
    logger: logging.Logger,
) -> None:
    """写入 xgscore_match_stats 表（UPSERT）。"""
    home_en = _cn_to_en_team(home_name) or home_name
    away_en = _cn_to_en_team(away_name) or away_name
    match_id = f"{date_str}_{home_en}_{away_en}"
    try:
        conn = sqlite3.connect(str(DB_PATH), timeout=CONNECT_TIMEOUT)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        conn.execute(CREATE_TABLE_SQL)
        conn.execute(
            "INSERT INTO xgscore_match_stats "
            "(match_id, league, match_date, home_team, away_team, home_xg, away_xg, "
            "home_goals, away_goals, xg_source, collected_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?) "
            "ON CONFLICT(match_id) DO UPDATE SET "
            "home_xg=excluded.home_xg, away_xg=excluded.away_xg, "
            "home_goals=excluded.home_goals, away_goals=excluded.away_goals, "
            "collected_at=excluded.collected_at",
            (
                match_id, league, date_str, home_en, away_en,
                result["home_xg"], result["away_xg"],
                result["home_goals"], result["away_goals"],
                "xgscore", datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        conn.commit()
        conn.close()
        logger.info(f"已落库 xgscore_match_stats: {match_id}")
    except Exception as e:
        logger.warning(f"落库失败（不影响返回）: {e}")


def get_cached_xg(
    conn: sqlite3.Connection,
    league: str,
    home_name: str,
    away_name: str,
    date_str: str,
) -> Optional[Dict[str, Any]]:
    """从 xgscore_match_stats 表查已缓存的 xG（供归因引擎 fallback 先查缓存）。

    匹配策略：date ±1 天 + 英文队名（落库时已转英文）双向归一化。
    """
    home_en = _cn_to_en_team(home_name) or home_name
    away_en = _cn_to_en_team(away_name) or away_name
    try:
        d = datetime.strptime(date_str[:10], "%Y-%m-%d")
        date_likes = [(d + timedelta(days=off)).strftime("%Y-%m-%d") for off in (-1, 0, 1)]
    except (ValueError, TypeError):
        date_likes = [date_str[:10]]
    cur = conn.cursor()
    # 确保表存在
    cur.execute(CREATE_TABLE_SQL)
    for dl in date_likes:
        # 精确英文队名
        cur.execute(
            "SELECT home_xg, away_xg, home_goals, away_goals FROM xgscore_match_stats "
            "WHERE match_date=? AND home_team=? AND away_team=? LIMIT 1",
            (dl, home_en, away_en),
        )
        row = cur.fetchone()
        if row:
            return {"home_xg": float(row[0]), "away_xg": float(row[1]),
                    "home_goals": int(row[2]), "away_goals": int(row[3])}
    # 归一化队名 fallback
    home_std = normalize_team_name(home_name)
    away_std = normalize_team_name(away_name)
    if home_std and away_std:
        for dl in date_likes:
            cur.execute(
                "SELECT home_team, away_team, home_xg, away_xg, home_goals, away_goals "
                "FROM xgscore_match_stats WHERE match_date=?", (dl,),
            )
            for r in cur.fetchall():
                if (home_std == normalize_team_name(r[0]) and
                        away_std == normalize_team_name(r[1])):
                    return {"home_xg": float(r[2]), "away_xg": float(r[3]),
                            "home_goals": int(r[4]), "away_goals": int(r[5])}
                if (home_std == normalize_team_name(r[1]) and
                        away_std == normalize_team_name(r[0])):
                    return {"home_xg": float(r[3]), "away_xg": float(r[2]),
                            "home_goals": int(r[5]), "away_goals": int(r[4])}
    return None


# ============================================================
# CLI
# ============================================================


def main():
    parser = argparse.ArgumentParser(description="xgscore.io xG 采集器（Understat fallback）")
    parser.add_argument("--league", required=True, help="联赛名（如 意甲/Serie_A）")
    parser.add_argument("--home", required=True, help="主队名（中文/英文）")
    parser.add_argument("--away", required=True, help="客队名（中文/英文）")
    parser.add_argument("--date", required=True, help="北京时间日期（YYYY-MM-DD）")
    parser.add_argument("--write", action="store_true", help="落库到 xgscore_match_stats 表")
    args = parser.parse_args()

    logger = setup_logger()
    result = fetch_match_xg(args.league, args.home, args.away, args.date, logger, write_db=args.write)
    if result:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        logger.warning("未获取到 xG 数据")
        sys.exit(1)


if __name__ == "__main__":
    main()
