# -*- coding: utf-8 -*-
"""
五大联赛统一数据存储层 (data_store)
=====================================
将四类数据源统一解析、校验、落库，并提供给模型预测系统的统一读取接口。

数据目录（canonical layout）：
  赛前数据   SofaScore 页面/API → odds.db           (fbref_match_mapping, match_lineups)
  赛后数据   SofaScore 页面/API → odds.db           (match_player_stats, fbref_players)
                                → five_leagues.db   (matches 球队级 xG/射门/控球统计)
  赔率时序   TXT (固定奖金)    → odds_timing.db     (matches, wdl_timing, handicap_timing,
                                                       total_goals_timing, score_timing)
  赛果      TXT (开奖结果)    → odds_timing.db     (match_results)

设计原则：
  1. 幂等：所有写入采用 INSERT OR REPLACE / INSERT OR IGNORE，重复调用安全
  2. 校验：数值字段失败自动跳过并收集错误，不中断批量
  3. 双库边界：TXT 来源只写 odds_timing.db（外文名口径）；SofaScore 来源写 odds.db
     + five_leagues.db（官方名口径），与既有数据口径约束一致
  4. 读取接口：prediction_core / feature_utils 可直接调用 load_* 系列函数

用法：
  python scripts/data_store.py --ingest-odds    data/西甲2026-2027赛季完整时序赔率.txt --league 西甲
  python scripts/data_store.py --ingest-results data/西甲2026-2027赛季比赛赛果记录.txt --league 西甲
  python scripts/data_store.py --list-odds --league 西甲 --season 2026-2027
"""
from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
COLLECTION_DIR = PROJECT_DIR / "collection"
DATA_DIR = PROJECT_DIR / "data"

ODDS_DB = DATA_DIR / "odds.db"
FL_DB = DATA_DIR / "five_leagues.db"
ODDS_TIMING_DB = DATA_DIR / "odds_timing.db"

logger = logging.getLogger("data_store")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

# 联赛名前缀（用于剔除球队行前缀，如 '西甲 Deportivo' -> 'Deportivo'）
LEAGUE_PREFIX_TOKENS = [
    "西甲", "英超", "意甲", "德甲", "法甲",
    "La Liga", "LaLiga", "Premier League", "Serie A",
    "Bundesliga", "Ligue 1", "Ligue1",
]


def _strip_league_prefix(name: str) -> str:
    """去除队名前导的联赛名（'西甲 Deportivo' -> 'Deportivo'）。"""
    name = name.strip()
    for kw in LEAGUE_PREFIX_TOKENS:
        if name.startswith(kw + " "):
            name = name[len(kw):].strip()
            break
    return name


# ============================================================
# 工具函数
# ============================================================
def build_match_id(match_date: str, home_team: str, away_team: str) -> str:
    """生成 odds_timing.db 的 match_id（外文名口径，与 import_data 一致）。"""
    clean = lambda s: re.sub(r"\s+", " ", str(s).strip())  # noqa: E731
    return f"{match_date}_{clean(home_team)}_{clean(away_team)}"


def _connect(db_path: Path) -> sqlite3.Connection:
    if not db_path.exists():
        raise FileNotFoundError(f"数据库不存在: {db_path}")
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ============================================================
# Schema（odds_timing.db 幂等建表，复用 timing_collector/init_timing_db.py 结构）
# ============================================================
def ensure_odds_timing_schema(conn: sqlite3.Connection) -> None:
    """幂等创建 odds_timing.db 八张表（若不存在）。"""
    cur = conn.cursor()
    cur.executescript(
        """
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE NOT NULL,
            home_team TEXT NOT NULL,
            away_team TEXT NOT NULL,
            match_date TEXT NOT NULL,
            match_time TEXT,
            league TEXT,
            league_code TEXT,
            status TEXT DEFAULT 'pending',
            source TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS wdl_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            win_a REAL NOT NULL,
            draw REAL NOT NULL,
            win_b REAL NOT NULL,
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,
            is_valid INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, timestamp, source)
        );
        CREATE TABLE IF NOT EXISTS handicap_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            handicap REAL NOT NULL,
            hcp_win REAL NOT NULL,
            hcp_draw REAL,
            hcp_lose REAL NOT NULL,
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,
            is_valid INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, timestamp, source)
        );
        CREATE TABLE IF NOT EXISTS total_goals_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            goals_0 REAL, goals_1 REAL, goals_2 REAL, goals_3 REAL,
            goals_4 REAL, goals_5 REAL, goals_6 REAL, goals_7_plus REAL,
            over_25 REAL, under_25 REAL,
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,
            is_valid INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, timestamp, source)
        );
        CREATE TABLE IF NOT EXISTS score_timing (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            timestamp TEXT NOT NULL,
            score TEXT NOT NULL,
            odds REAL NOT NULL,
            source TEXT NOT NULL,
            quality_score REAL DEFAULT 1.0,
            is_valid INTEGER DEFAULT 1,
            notes TEXT,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, timestamp, score, source)
        );
        CREATE TABLE IF NOT EXISTS match_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE NOT NULL,
            actual_score TEXT,
            actual_wdl TEXT,
            actual_handicap TEXT,
            actual_total_goals INTEGER,
            source TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS data_sources (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_code TEXT UNIQUE NOT NULL,
            source_name TEXT NOT NULL,
            description TEXT,
            enabled INTEGER DEFAULT 1,
            last_sync_time TEXT,
            total_records INTEGER DEFAULT 0,
            quality_rating REAL DEFAULT 0.0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS import_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            import_time TEXT DEFAULT CURRENT_TIMESTAMP,
            source TEXT NOT NULL,
            total_matches INTEGER DEFAULT 0,
            wdl_records INTEGER DEFAULT 0,
            hcp_records INTEGER DEFAULT 0,
            tg_records INTEGER DEFAULT 0,
            score_records INTEGER DEFAULT 0,
            status TEXT DEFAULT 'processing',
            error_message TEXT,
            duration REAL
        );
        """
    )
    conn.commit()


# ============================================================
# 解析：赛果 TXT（开奖结果）
# ============================================================
def _parse_teams(team_line: str) -> Tuple[Optional[str], Optional[str]]:
    """从球队行提取主/客队名（兼容 tab 分隔与 2+ 空格分隔）。"""
    line = team_line.strip()
    if not line:
        return None, None
    if "\t" in line:
        parts = [p.strip() for p in line.split("\t") if p.strip()]
        if len(parts) >= 3:
            return _strip_league_prefix(parts[-2]), parts[-1]
        if len(parts) == 2:
            return _strip_league_prefix(parts[0]), parts[1]
    parts = [p.strip() for p in re.split(r"\s{2,}", line) if p.strip()]
    if len(parts) >= 3:
        return _strip_league_prefix(parts[-2]), parts[-1]
    if len(parts) == 2:
        return _strip_league_prefix(parts[0]), parts[1]
    return None, None


def parse_results_txt(path: Path) -> List[Dict[str, Any]]:
    """解析赛果 TXT（'开奖结果' 区块），返回结构化结果列表。"""
    lines = [l.rstrip("\n") for l in path.read_text(encoding="utf-8").splitlines()]
    results: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None

    header_re = re.compile(
        r"(\d{4}/\d{4})\s+Regular\s+Season\s+第(\d+)轮\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})"
    )

    for line in lines:
        s = line.strip()
        h = header_re.match(s)
        if h:
            if cur and cur.get("home_team"):
                results.append(cur)
            cur = {
                "season": h.group(1),
                "round": int(h.group(2)),
                "match_date": h.group(3),
                "match_time": h.group(4),
                "home_team": None,
                "away_team": None,
                "results": {},
            }
            continue
        if cur is None:
            continue
        # 球队行 = 跟随 header 的第一个非空、且不含已知标记的行
        if cur["home_team"] is None and s and "开奖结果" not in s and "游戏" not in s:
            home, away = _parse_teams(line)
            if home and away and not any(k in s for k in ("胜平负", "让球", "比分", "总进球", "半全场")):
                cur["home_team"] = home
                cur["away_team"] = away
            continue
        # 结果行（tab 分隔）：游戏\t开奖结果\t奖金
        if s.startswith(("胜平负", "让球胜平负", "比分", "总进球", "半全场")):
            parts = [p.strip() for p in s.split("\t") if p.strip()]
            if len(parts) >= 2:
                cur["results"][parts[0]] = parts[1]
            continue

    if cur and cur.get("home_team"):
        results.append(cur)
    return results


# ============================================================
# 解析：赔率时序 TXT（固定奖金）
# ============================================================
def parse_odds_timing_txt(path: Path) -> List[Dict[str, Any]]:
    """解析赔率时序 TXT（胜平负/让球/总进球/比分固定奖金），返回结构化比赛列表。"""
    lines = [l.rstrip("\n") for l in path.read_text(encoding="utf-8").splitlines()]
    matches: List[Dict[str, Any]] = []
    cur: Optional[Dict[str, Any]] = None
    section: Optional[str] = None
    score_ts: Optional[str] = None
    pending_labels: List[str] = []

    header_re = re.compile(
        r"(\d{4}/\d{4})\s+Regular\s+Season\s+第(\d+)轮\s+(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2})"
    )
    ts_re = re.compile(r"^\d{4}-\d{2}-\d{2}\s+\d{2}:\d{2}")

    def _end_match():
        nonlocal cur, section, score_ts, pending_labels
        if cur and cur.get("home_team"):
            matches.append(cur)
        cur = None
        section = None
        score_ts = None
        pending_labels = []

    for line in lines:
        s = line.strip()
        h = header_re.match(s)
        if h:
            _end_match()
            cur = {
                "season": h.group(1),
                "round": int(h.group(2)),
                "match_date": h.group(3),
                "match_time": h.group(4),
                "home_team": None,
                "away_team": None,
                "handicap_line": None,
                "wdl_timing": [],
                "handicap_timing": [],
                "total_goals_timing": [],
                "score_timing": [],
            }
            section = None
            continue
        if cur is None:
            continue
        # 球队行
        if cur["home_team"] is None and s and not s.startswith(("胜平负", "让球", "比分", "总进球", "发布时间")):
            home, away = _parse_teams(line)
            if home and away:
                cur["home_team"] = home
                cur["away_team"] = away
            continue
        # 区块标记
        if s.startswith("胜平负固定奖金"):
            section = "wdl"
            continue
        if s.startswith("让球胜平负固定奖金"):
            section = "hcp"
            continue
        if s.startswith("让球") and cur and section == "hcp" and cur["handicap_line"] is None:
            m = re.match(r"让球([+\-]?\d+)", s)
            if m:
                cur["handicap_line"] = int(m.group(1))
            continue
        if s.startswith("总进球固定奖金"):
            section = "tg"
            continue
        if s.startswith("比分固定奖金"):
            section = "score"
            continue
        # 比分固定奖金区块：'发布时间' + 标签行/赔率行成对出现
        if section == "score":
            if s.startswith("发布时间"):
                m = re.search(r"(\d{4}-\d{2}-\d{2})\s+(\d{2}:\d{2}:\d{2})", s)
                if m:
                    score_ts = f"{m.group(1)} {m.group(2)}"
                continue
            if ":" in s or "其它" in s:
                pending_labels = [x.strip() for x in s.split("\t") if x.strip()]
                continue
            if pending_labels and s:
                odds_vals = [x.strip() for x in s.split("\t") if x.strip()]
                if len(odds_vals) == len(pending_labels):
                    for lbl, odd in zip(pending_labels, odds_vals):
                        if "其它" in lbl:
                            continue
                        try:
                            cur["score_timing"].append({
                                "timestamp": score_ts or "",
                                "score": lbl.replace(" ", ""),
                                "odds": float(odd),
                            })
                        except ValueError:
                            continue
                pending_labels = []
            continue
        # 数据行
        if section == "wdl" and ts_re.match(s):
            parts = re.split(r"\s+", s)
            if len(parts) >= 5:
                try:
                    cur["wdl_timing"].append({
                        "timestamp": f"{parts[0]} {parts[1]}",
                        "win_a": float(parts[2]), "draw": float(parts[3]), "win_b": float(parts[4]),
                    })
                except ValueError:
                    continue
        elif section == "hcp" and ts_re.match(s):
            parts = re.split(r"\s+", s)
            if len(parts) >= 5:
                try:
                    cur["handicap_timing"].append({
                        "timestamp": f"{parts[0]} {parts[1]}",
                        "hcp_win": float(parts[2]), "hcp_draw": float(parts[3]), "hcp_lose": float(parts[4]),
                    })
                except ValueError:
                    continue
        elif section == "tg" and ts_re.match(s):
            parts = re.split(r"\s+", s)
            if len(parts) >= 10:
                try:
                    cur["total_goals_timing"].append({
                        "timestamp": f"{parts[0]} {parts[1]}",
                        "goals": [float(x) for x in parts[2:10]],
                    })
                except ValueError:
                    continue

    _end_match()
    return matches


# ============================================================
# 写入：赔率时序 → odds_timing.db
# ============================================================
def ingest_odds_timing_txt(txt_path: Path, league: str, source: str = "TXT_IMPORT",
                          league_code: str = "") -> Dict[str, int]:
    """解析赔率时序 TXT 并批量写入 odds_timing.db。返回各表写入行数统计。"""
    txt_path = Path(txt_path)
    if not txt_path.exists():
        raise FileNotFoundError(f"赔率 TXT 不存在: {txt_path}")
    matches = parse_odds_timing_txt(txt_path)
    if not matches:
        logger.warning(f"未解析出任何比赛: {txt_path}")
        return {}

    conn = _connect(ODDS_TIMING_DB)
    ensure_odds_timing_schema(conn)
    cur = conn.cursor()

    stats = {"matches": 0, "wdl": 0, "hcp": 0, "tg": 0, "score": 0}
    errors: List[str] = []

    for m in matches:
        mid = build_match_id(m["match_date"], m["home_team"], m["away_team"])
        cur.execute(
            """INSERT OR REPLACE INTO matches
               (match_id, home_team, away_team, match_date, match_time, league, league_code, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, m["home_team"], m["away_team"], m["match_date"], m["match_time"], league, league_code, source),
        )
        stats["matches"] += 1

        for rec in m["wdl_timing"]:
            cur.execute(
                """INSERT OR REPLACE INTO wdl_timing
                   (match_id, timestamp, win_a, draw, win_b, source) VALUES (?, ?, ?, ?, ?, ?)""",
                (mid, rec["timestamp"], rec["win_a"], rec["draw"], rec["win_b"], source),
            )
            stats["wdl"] += 1

        for rec in m["handicap_timing"]:
            cur.execute(
                """INSERT OR REPLACE INTO handicap_timing
                   (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (mid, rec["timestamp"], m.get("handicap_line") or 0, rec["hcp_win"], rec["hcp_draw"], rec["hcp_lose"], source),
            )
            stats["hcp"] += 1

        for rec in m["total_goals_timing"]:
            cur.execute(
                """INSERT OR REPLACE INTO total_goals_timing
                   (match_id, timestamp, goals_0, goals_1, goals_2, goals_3,
                    goals_4, goals_5, goals_6, goals_7_plus, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mid, rec["timestamp"], *rec["goals"], source),
            )
            stats["tg"] += 1

        for rec in m["score_timing"]:
            cur.execute(
                """INSERT OR REPLACE INTO score_timing
                   (match_id, timestamp, score, odds, source) VALUES (?, ?, ?, ?, ?)""",
                (mid, rec["timestamp"], rec["score"], rec["odds"], source),
            )
            stats["score"] += 1

    conn.commit()
    conn.close()
    if errors:
        logger.warning(f"部分记录跳过 {len(errors)} 条")
    logger.info(f"赔率时序落库完成: matches={stats['matches']} wdl={stats['wdl']} "
                f"hcp={stats['hcp']} tg={stats['tg']} score={stats['score']}")
    return stats


# ============================================================
# 写入：赛果 → odds_timing.db
# ============================================================
def ingest_results_txt(txt_path: Path, league: str, source: str = "TXT_RESULT") -> Dict[str, int]:
    """解析赛果 TXT 并写入 odds_timing.db.match_results。"""
    txt_path = Path(txt_path)
    if not txt_path.exists():
        raise FileNotFoundError(f"赛果 TXT 不存在: {txt_path}")
    results = parse_results_txt(txt_path)
    if not results:
        logger.warning(f"未解析出任何赛果: {txt_path}")
        return {}

    conn = _connect(ODDS_TIMING_DB)
    ensure_odds_timing_schema(conn)
    cur = conn.cursor()

    stats = {"matches": 0, "results": 0}
    for r in results:
        mid = build_match_id(r["match_date"], r["home_team"], r["away_team"])
        # 基础比赛记录（若 odds 时序已写入则保留）
        cur.execute(
            """INSERT OR IGNORE INTO matches
               (match_id, home_team, away_team, match_date, match_time, league, source)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (mid, r["home_team"], r["away_team"], r["match_date"], r["match_time"], league, source),
        )
        if cur.rowcount:
            stats["matches"] += 1

        res = r["results"]
        actual_score = res.get("比分")
        actual_wdl = res.get("胜平负")
        actual_handicap = res.get("让球胜平负")
        actual_total_goals = None
        if res.get("总进球"):
            try:
                actual_total_goals = int(str(res["总进球"]).replace("+", ""))
            except ValueError:
                actual_total_goals = None

        cur.execute(
            """INSERT OR REPLACE INTO match_results
               (match_id, actual_score, actual_wdl, actual_handicap, actual_total_goals, source, verified)
               VALUES (?, ?, ?, ?, ?, ?, 1)""",
            (mid, actual_score, actual_wdl, actual_handicap, actual_total_goals, source),
        )
        stats["results"] += 1

    conn.commit()
    conn.close()
    logger.info(f"赛果落库完成: matches={stats['matches']} results={stats['results']}")
    return stats


# ============================================================
# 读取接口（供 prediction_core / feature_utils 直接使用）
# ============================================================
def list_matches(league: Optional[str] = None, season: Optional[str] = None,
                 with_results: bool = False) -> List[Dict[str, Any]]:
    """读取 odds_timing.db 的比赛列表，可选附带赛果。"""
    conn = _connect(ODDS_TIMING_DB)
    cur = conn.cursor()
    sql = "SELECT match_id, home_team, away_team, match_date, match_time, league, source FROM matches WHERE 1=1"
    args: List[Any] = []
    if league:
        sql += " AND league = ?"
        args.append(league)
    if season:
        sql += " AND match_date LIKE ?"
        args.append(season.split("/")[0] + "%")
    rows = [dict(zip(["match_id", "home_team", "away_team", "match_date", "match_time", "league", "source"], r))
            for r in cur.execute(sql, args).fetchall()]
    if with_results:
        for row in rows:
            res = cur.execute(
                "SELECT actual_score, actual_wdl, actual_handicap, actual_total_goals, verified "
                "FROM match_results WHERE match_id = ?", (row["match_id"],),
            ).fetchone()
            row["result"] = dict(zip(["actual_score", "actual_wdl", "actual_handicap", "actual_total_goals", "verified"], res)) if res else None
    conn.close()
    return rows


def load_timing_odds(match_id: str, kind: str = "wdl", source: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取某场比赛的赔率时序。kind ∈ {wdl, handicap, total_goals, score}。"""
    table = {"wdl": "wdl_timing", "handicap": "handicap_timing",
             "total_goals": "total_goals_timing", "score": "score_timing"}[kind]
    conn = _connect(ODDS_TIMING_DB)
    cur = conn.cursor()
    sql = f"SELECT * FROM {table} WHERE match_id = ?"
    args: List[Any] = [match_id]
    if source:
        sql += " AND source = ?"
        args.append(source)
    sql += " ORDER BY timestamp"
    cols = [d[0] for d in cur.execute(f"SELECT * FROM {table} LIMIT 1").description]
    rows = [dict(zip(cols, r)) for r in cur.execute(sql, args).fetchall()]
    conn.close()
    return rows


def load_match_results(match_id: str) -> Optional[Dict[str, Any]]:
    """读取单场比赛赛果。"""
    conn = _connect(ODDS_TIMING_DB)
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM match_results WHERE match_id = ?", (match_id,)).fetchone()
    conn.close()
    if not row:
        return None
    cols = ["id", "match_id", "actual_score", "actual_wdl", "actual_handicap",
            "actual_total_goals", "source", "verified", "created_at", "updated_at"]
    return dict(zip(cols, row))


def load_odds_matches(league: Optional[str] = None) -> List[Dict[str, Any]]:
    """读取 odds.db 的 matches（模型训练主表，含 actual_* 与 handicap）。"""
    conn = _connect(ODDS_DB)
    cur = conn.cursor()
    sql = "SELECT match_id, home_team, away_team, match_date, match_type, handicap, " \
          "actual_wdl, actual_handicap, actual_score, actual_total_goals FROM matches WHERE 1=1"
    args: List[Any] = []
    if league:
        sql += " AND match_type LIKE ?"
        args.append(f"%{league}%")
    rows = [dict(zip(["match_id", "home_team", "away_team", "match_date", "match_type", "handicap",
                      "actual_wdl", "actual_handicap", "actual_score", "actual_total_goals"], r))
            for r in cur.execute(sql, args).fetchall()]
    conn.close()
    return rows


# ============================================================
# CLI
# ============================================================
def main() -> None:
    p = argparse.ArgumentParser(description="五大联赛统一数据存储层")
    p.add_argument("--ingest-odds", type=str, help="导入赔率时序 TXT")
    p.add_argument("--ingest-results", type=str, help="导入赛果 TXT")
    p.add_argument("--league", type=str, default="西甲", help="联赛中文名")
    p.add_argument("--league-code", type=str, default="", help="联赛代码（可选）")
    p.add_argument("--list-odds", action="store_true", help="列出赔率时序比赛")
    p.add_argument("--season", type=str, default="", help="赛季（如 2026/2027）")
    p.add_argument("--with-results", action="store_true", help="列出比赛时附带赛果")
    args = p.parse_args()

    if args.ingest_odds:
        print(ingest_odds_timing_txt(Path(args.ingest_odds), args.league, league_code=args.league_code))
    if args.ingest_results:
        print(ingest_results_txt(Path(args.ingest_results), args.league))
    if args.list_odds:
        rows = list_matches(league=args.league, season=args.season or None, with_results=args.with_results)
        print(f"共 {len(rows)} 场比赛")
        for r in rows:
            tail = f" | 赛果={r['result']}" if args.with_results else ""
            print(f"  {r['match_id']}  {r['match_date']} {r['home_team']} vs {r['away_team']}{tail}")


if __name__ == "__main__":
    main()