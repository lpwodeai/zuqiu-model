"""
Understat xG 数据批量采集器
================================================
功能：
  从 Understat（https://understat.com）采集五大联赛的 xG（预期进球）体系数据，
  作为 FBref（传统统计）和 SofaScore（评分/传球）之外的第三数据源补充。

数据源与端点（已验证 2026-08-22）：
  - 比赛列表    GET https://understat.com/getLeagueData/{slug}/{season}
                返回 { teams, players, dates }，其中 dates 是赛季全部比赛
                （含 match_id、主客队、比分、xG、forecast 胜平负概率）
  - 单场详细    GET https://understat.com/getMatchData/{match_id}（需 Referer 头）
                返回 { rosters, shots, tmpl }
                - rosters : 球员 xG 体系（xG/xA/xGChain/xGBuildup 等 21 字段）
                - shots   : 射门级数据（坐标 X/Y、xG、射门方式/部位/结果 20 字段）

采集到的三层数据（写入 3 张新表）：
  1. understat_match_team_stats : 比赛级（主客队比分/xG/胜负概率 forecast）
  2. understat_player_xg        : 球员级（xG/xA/xGChain/xGBuildup/key_passes）
  3. understat_shots            : 射门级（坐标/部位/情况/结果/xG）

与现有数据源的关系：
  - FBref      : 传统事件统计（射门/犯规/越位，无 xG 模型）
  - SofaScore  : 评分/传球成功率/触球（xG 覆盖率仅 30.8%）
  - Understat  : 完整 xG 模型体系 + 射门级坐标数据（全新维度）

使用方法：
  # 试跑：西甲 26/27 前 5 场（不写库）
  python collection/final_understat_collector.py --leagues 西甲 --season 26/27 --limit 5 --dry-run

  # 正式采集：西甲 26/27 前 20 场
  python collection/final_understat_collector.py --leagues 西甲 --season 26/27 --limit 20

  # 全量五大联赛 26/27
  python collection/final_understat_collector.py --leagues all --season 26/27

联赛 slug 映射（Understat 命名）：
  英超=EPL  西甲=La_liga  意甲=Serie_A  德甲=Bundesliga  法甲=Ligue_1

赛季映射：Understat 用赛季起始年份，如 26/27 -> 2026，25/26 -> 2025
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import requests

# ============================================================
# 路径与配置
# ============================================================

MODEL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = MODEL_PROJECT_ROOT / "data" / "odds.db"
LOG_DIR = MODEL_PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

BUSY_TIMEOUT_MS = 30000  # SQLite 写锁等待超时（毫秒，双开时持锁 HTTP I/O 可达 10s+）
CONNECT_TIMEOUT = 30  # sqlite3.connect timeout 参数（秒，Python 层等待锁的兜底）

BASE_URL = "https://understat.com"

# Understat 联赛 slug（URL 用）
LEAGUE_SLUGS: Dict[str, str] = {
    "英超": "EPL",
    "西甲": "La_liga",
    "意甲": "Serie_A",
    "德甲": "Bundesliga",
    "法甲": "Ligue_1",
}

# 请求头（缺 Referer / X-Requested-With 会 404）
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
}

# ============================================================
# 建表 SQL（3 张新表）
# ============================================================

CREATE_TABLES_SQL = [
    # 1. 比赛级：每场一行，主客队比分/xG/胜平负概率
    """
    CREATE TABLE IF NOT EXISTS understat_match_team_stats (
        match_id     TEXT PRIMARY KEY,
        season       TEXT,
        league       TEXT,
        datetime     TEXT,
        home_team    TEXT,
        home_team_id TEXT,
        away_team    TEXT,
        away_team_id TEXT,
        home_goals   INTEGER,
        away_goals   INTEGER,
        home_xg      REAL,
        away_xg      REAL,
        forecast_w   REAL,
        forecast_d   REAL,
        forecast_l   REAL,
        is_result    INTEGER,
        collected_at TEXT
    )
    """,
    # 2. 球员级：xG 体系（xG/xA/xGChain/xGBuildup）
    """
    CREATE TABLE IF NOT EXISTS understat_player_xg (
        match_id     TEXT,
        player_id    TEXT,
        player_name  TEXT,
        team_id      TEXT,
        team_name    TEXT,
        position     TEXT,
        time         INTEGER,
        goals        INTEGER,
        shots        INTEGER,
        xg           REAL,
        assists      INTEGER,
        xa           REAL,
        key_passes   INTEGER,
        xg_chain     REAL,
        xg_buildup   REAL,
        yellow_card  INTEGER,
        red_card     INTEGER,
        season       TEXT,
        league       TEXT,
        PRIMARY KEY (match_id, player_id)
    )
    """,
    # 3. 射门级：每脚射门一行（坐标/部位/情况/结果）
    """
    CREATE TABLE IF NOT EXISTS understat_shots (
        shot_id         TEXT PRIMARY KEY,
        match_id        TEXT,
        minute          INTEGER,
        result          TEXT,
        x               REAL,
        y               REAL,
        xg              REAL,
        player_id       TEXT,
        player_name     TEXT,
        team_side       TEXT,
        team_name       TEXT,
        situation       TEXT,
        shot_type       TEXT,
        player_assisted TEXT,
        last_action     TEXT,
        season          TEXT,
        league          TEXT
    )
    """,
]


# ============================================================
# 工具函数
# ============================================================

def season_to_understat(season: str) -> str:
    """转换赛季标签：'26/27' -> '2026'"""
    start = season.split("/")[0].strip()
    return "20" + start


def _to_int(v: Any) -> Optional[int]:
    if v is None or v == "":
        return None
    try:
        return int(float(v))
    except (ValueError, TypeError):
        return None


def _to_float(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (ValueError, TypeError):
        return None


def setup_logger() -> logging.Logger:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"understat_collector_{ts}.log"

    logger = logging.getLogger("understat")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))

    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s", datefmt="%H:%M:%S"))

    logger.addHandler(fh)
    logger.addHandler(ch)
    logger.info("=" * 70)
    logger.info(f"Understat xG 采集器启动")
    logger.info(f"日志文件: {log_file}")
    logger.info(f"数据库  : {DB_PATH}")
    logger.info("=" * 70)
    return logger


def http_get(url: str, referer: str, logger: logging.Logger, retries: int = 3) -> Optional[requests.Response]:
    """带反爬头和重试的 GET 请求。"""
    headers = dict(HEADERS)
    headers["Referer"] = referer
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, headers=headers, timeout=30)
            if r.status_code == 200:
                return r
            # 404 表示数据不存在（如未开赛比赛无 rosters/shots），重试无意义
            if r.status_code == 404:
                logger.debug(f"404 无数据（跳过）: {url}")
                return None
            logger.warning(f"请求失败 {r.status_code}: {url} (第{attempt}次)")
        except Exception as e:
            logger.warning(f"请求异常: {url} ({e}) (第{attempt}次)")
        time.sleep(1.5 * attempt)
    return None


# ============================================================
# 采集器主类
# ============================================================

class UnderstatCollector:
    def __init__(self, logger: logging.Logger, dry_run: bool = False):
        self.logger = logger
        self.dry_run = dry_run
        self.db_conn: Optional[sqlite3.Connection] = None

    def connect_db(self) -> None:
        if self.dry_run:
            return
        self.db_conn = sqlite3.connect(str(DB_PATH), timeout=CONNECT_TIMEOUT)
        # 并发写入安全：WAL 模式 + 30 秒锁等待，与 sofascore_collector 双开时能互相等待
        self.db_conn.execute("PRAGMA journal_mode=WAL")
        self.db_conn.execute("PRAGMA synchronous=NORMAL")
        self.db_conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        for ddl in CREATE_TABLES_SQL:
            self.db_conn.execute(ddl)
        # 兼容旧表：若无 league 列则补充
        for tbl in ("understat_match_team_stats", "understat_player_xg", "understat_shots"):
            cols = [r[1] for r in self.db_conn.execute(f"PRAGMA table_info({tbl})").fetchall()]
            if "league" not in cols:
                self.db_conn.execute(f"ALTER TABLE {tbl} ADD COLUMN league TEXT")
                self.logger.info(f"已为 {tbl} 补充 league 列")
        self.db_conn.commit()
        self.logger.info(
            f"已连接数据库并确保 3 张 understat 表存在 | WAL ON "
            f"| busy_timeout={BUSY_TIMEOUT_MS}ms | connect_timeout={CONNECT_TIMEOUT}s"
        )

    def close_db(self) -> None:
        if self.db_conn:
            self.db_conn.close()

    # ---------- 第一步：获取赛季比赛列表 ----------
    def fetch_season_dates(self, league_cn: str, season: str) -> tuple[List[Dict], Dict[str, str]]:
        slug = LEAGUE_SLUGS[league_cn]
        s = season_to_understat(season)
        url = f"{BASE_URL}/getLeagueData/{slug}/{s}"
        referer = f"{BASE_URL}/league/{slug}/{s}"
        r = http_get(url, referer, self.logger)
        if r is None:
            self.logger.error(f"[{league_cn}] 无法获取赛季数据: {url}")
            return [], {}

        data = r.json()
        dates = data.get("dates", [])
        teams = data.get("teams", {})
        # 部分新赛季（如德甲 26/27 尚未开赛）teams 返回空 list，需容错
        if not isinstance(teams, dict):
            teams = {}
        # 构建 team_id -> title 映射
        team_names = {tid: t.get("title", "") for tid, t in teams.items() if isinstance(t, dict)}
        self.logger.info(f"[{league_cn}] 获取到 {len(dates)} 场比赛, {len(team_names)} 支球队")
        return dates, team_names

    # ---------- 第二步：写入比赛级数据 ----------
    def write_match(self, match: Dict, season: str, league: str) -> None:
        if self.dry_run or self.db_conn is None:
            return
        cur = self.db_conn.cursor()
        h = match.get("h", {})
        a = match.get("a", {})
        goals = match.get("goals", {})
        xg = match.get("xG", {})
        fc = match.get("forecast", {})
        cur.execute("""
            INSERT OR REPLACE INTO understat_match_team_stats
            (match_id, season, league, datetime, home_team, home_team_id, away_team, away_team_id,
             home_goals, away_goals, home_xg, away_xg,
             forecast_w, forecast_d, forecast_l, is_result, collected_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            str(match.get("id", "")), season, league, match.get("datetime"),
            h.get("title"), str(h.get("id", "")), a.get("title"), str(a.get("id", "")),
            _to_int(goals.get("h")), _to_int(goals.get("a")),
            _to_float(xg.get("h")), _to_float(xg.get("a")),
            _to_float(fc.get("w")), _to_float(fc.get("d")), _to_float(fc.get("l")),
            1 if match.get("isResult") else 0,
            datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        ))

    # ---------- 第三步：写入单场球员 + 射门数据 ----------
    def write_match_detail(self, match: Dict, season: str, league: str, team_names: Dict[str, str],
                          pre_fetched: Optional[Dict[str, Any]] = None) -> Dict[str, int]:
        """写入单场球员 + 射门数据，返回写入行数。

        Args:
            pre_fetched: 若提供则跳过 HTTP 直接用（调用方提前 fetch，避免持锁打外网）
                         结构: {"rosters": {...}, "shots": {...}} 或 None（表示 getMatchData 失败）
        """
        match_id = str(match.get("id", ""))
        h_team = match.get("h", {}).get("title", "")
        a_team = match.get("a", {}).get("title", "")
        referer = f"{BASE_URL}/match/{match_id}"

        if pre_fetched is None:
            # 兼容旧调用（未提前 fetch），但调用方应尽量传 pre_fetched 避免持锁 HTTP
            r = http_get(f"{BASE_URL}/getMatchData/{match_id}", referer, self.logger)
            if r is None:
                self.logger.warning(f"[{match_id}] 获取单场数据失败")
                return {"players": 0, "shots": 0}
            data = r.json()
        else:
            # 提前已 fetch；None 值表示上游获取失败
            data = pre_fetched
            if data is None:
                self.logger.warning(f"[{match_id}] 单场数据上游获取失败，跳过写入")
                return {"players": 0, "shots": 0}

        rosters = data.get("rosters", {})
        shots = data.get("shots", {})

        player_rows = 0
        shot_rows = 0

        if not self.dry_run and self.db_conn is not None:
            cur = self.db_conn.cursor()
            # rosters 标准结构: {h: {record_id: {...}}, a: {record_id: {...}}}
            # 但 API 偶发返回 []（空 list），需先归一为 dict，否则 .values() 会 AttributeError
            def _rosters(side_key: str) -> Dict:
                v = rosters.get(side_key, {})
                return v if isinstance(v, dict) else {}

            def _shots(side_key: str) -> List:
                v = shots.get(side_key, [])
                return v if isinstance(v, list) else []

            for side in ("h", "a"):
                side_team = h_team if side == "h" else a_team
                for record in _rosters(side).values():
                    cur.execute("""
                        INSERT OR REPLACE INTO understat_player_xg
                        (match_id, player_id, player_name, team_id, team_name, position,
                         time, goals, shots, xg, assists, xa, key_passes,
                         xg_chain, xg_buildup, yellow_card, red_card, season, league)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        match_id, str(record.get("player_id", "")), record.get("player"),
                        str(record.get("team_id", "")), side_team, record.get("position"),
                        _to_int(record.get("time")), _to_int(record.get("goals")),
                        _to_int(record.get("shots")), _to_float(record.get("xG")),
                        _to_int(record.get("assists")), _to_float(record.get("xA")),
                        _to_int(record.get("key_passes")), _to_float(record.get("xGChain")),
                        _to_float(record.get("xGBuildup")), _to_int(record.get("yellow_card")),
                        _to_int(record.get("red_card")), season, league,
                    ))
                    player_rows += 1

            # shots: {h: [shot,...], a: [shot,...]}
            for side in ("h", "a"):
                side_team = h_team if side == "h" else a_team
                for shot in _shots(side):
                    s = shot if isinstance(shot, dict) else {}
                    cur.execute("""
                        INSERT OR REPLACE INTO understat_shots
                        (shot_id, match_id, minute, result, x, y, xg,
                         player_id, player_name, team_side, team_name,
                         situation, shot_type, player_assisted, last_action, season, league)
                        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """, (
                        str(s.get("id", "")), match_id, _to_int(s.get("minute")),
                        s.get("result"), _to_float(s.get("X")), _to_float(s.get("Y")),
                        _to_float(s.get("xG")), str(s.get("player_id", "")),
                        s.get("player"), s.get("h_a"), side_team,
                        s.get("situation"), s.get("shotType"),
                        s.get("player_assisted"), s.get("lastAction"), season, league,
                    ))
                    shot_rows += 1

        return {"players": player_rows, "shots": shot_rows}

    # ---------- 主流程 ----------
    def run(self, league_cn: str, season: str, limit: Optional[int], resume: bool,
            include_fixtures: bool = False) -> None:
        self.logger.info(f"开始采集: {league_cn} | season={season} | limit={limit} | resume={resume}")

        dates, team_names = self.fetch_season_dates(league_cn, season)
        if not dates:
            self.logger.warning(f"[{league_cn}] 无比赛数据，跳过")
            return

        # 默认只采集已完赛的比赛（isResult=true）；未开赛比赛无 rosters/shots 数据
        total_matches = len(dates)
        n_result = sum(1 for m in dates if m.get("isResult"))
        if not include_fixtures:
            dates = [m for m in dates if m.get("isResult")]
            self.logger.info(f"[{league_cn}] 完赛 {n_result} 场，跳过 {total_matches - n_result} 场未开赛")

        # 按日期排序
        dates_sorted = sorted(dates, key=lambda m: m.get("datetime", ""))

        # resume：跳过已采集的比赛
        done_ids = set()
        if resume and not self.dry_run and self.db_conn is not None:
            try:
                done_ids = {r[0] for r in self.db_conn.execute(
                    "SELECT DISTINCT match_id FROM understat_shots").fetchall()}
                self.logger.info(f"[{league_cn}] resume: 已采集 {len(done_ids)} 场，将跳过")
            except Exception as e:
                self.logger.warning(f"resume 查询失败: {e}")

        # 过滤掉已采集 + limit 截断
        todo = [m for m in dates_sorted if str(m.get("id", "")) not in done_ids]
        if limit is not None:
            todo = todo[:limit]
        self.logger.info(f"[{league_cn}] 待采集 {len(todo)} 场（总 {len(dates_sorted)} 场）")

        total_players = 0
        total_shots = 0
        errors = 0

        for i, match in enumerate(todo, 1):
            match_id = str(match.get("id", ""))

            # ============================================================
            # 关键修复：先做完所有 HTTP，再开始写库
            # （避免"写锁已持 + 外网 I/O 卡 2-15s"把另一爬虫堵死 5s 后 SQLITE_BUSY）
            # ============================================================
            referer = f"{BASE_URL}/match/{match_id}"
            r = http_get(f"{BASE_URL}/getMatchData/{match_id}", referer, self.logger)
            if r is None:
                errors += 1
                self.logger.warning(f"[{match_id}] 获取单场数据失败，跳过")
                # 但比赛级基础数据还是可以先写（纯本地 INSERT，无需额外 HTTP）
            pre_fetched_data = r.json() if r is not None else None

            # ---------- 纯 DB 写入区：所有 HTTP 已完成，持锁时间只有几毫秒 ----------
            try:
                self.write_match(match, season, league_cn)
                detail = self.write_match_detail(
                    match, season, league_cn, team_names,
                    pre_fetched=pre_fetched_data,
                )
                total_players += detail["players"]
                total_shots += detail["shots"]
            except Exception as e:
                errors += 1
                self.logger.error(f"[{match_id}] 写入异常: {e}")

            if self.db_conn and not self.dry_run:
                self.db_conn.commit()

            if i % 5 == 0 or i == len(todo):
                self.logger.info(
                    f"[{league_cn}] 进度 {i}/{len(todo)} | 球员 {total_players} | 射门 {total_shots} | 错误 {errors}"
                )

        if self.db_conn and not self.dry_run:
            self.db_conn.commit()
        self.logger.info(f"[{league_cn}] 完成: {len(todo)} 场, 球员 {total_players} 行, 射门 {total_shots} 行, 错误 {errors}")


# ============================================================
# main
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Understat xG 数据采集器")
    parser.add_argument("--leagues", type=str, default="all",
                        help="联赛名，多个用逗号分隔，或 all（英超/西甲/意甲/德甲/法甲）")
    parser.add_argument("--season", type=str, default="26/27", help="赛季，如 26/27 25/26")
    parser.add_argument("--limit", type=int, default=None, help="每联赛最多采集场次")
    parser.add_argument("--resume", action="store_true", help="跳过已采集比赛（断点续传）")
    parser.add_argument("--include-fixtures", action="store_true",
                        help="包含未开赛比赛（默认只抓已完赛，未开赛无 xG 数据）")
    parser.add_argument("--dry-run", action="store_true", help="试跑模式，不写库")
    args = parser.parse_args()

    logger = setup_logger()

    # 解析联赛
    if args.leagues.strip().lower() == "all":
        leagues = list(LEAGUE_SLUGS.keys())
    else:
        leagues = [x.strip() for x in args.leagues.split(",") if x.strip()]

    collector = UnderstatCollector(logger, args.dry_run)
    collector.connect_db()

    t0 = time.time()
    try:
        for lg in leagues:
            if lg not in LEAGUE_SLUGS:
                logger.warning(f"未知联赛: {lg}，跳过")
                continue
            collector.run(lg, args.season, args.limit, args.resume, args.include_fixtures)
    finally:
        collector.close_db()

    logger.info("=" * 70)
    logger.info(f"全部完成，耗时 {time.time() - t0:.1f}s")
    logger.info("=" * 70)


if __name__ == "__main__":
    main()