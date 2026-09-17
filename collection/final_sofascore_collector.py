"""
SofaScore 五大联赛批量数据采集器（最终生产版本）
================================================
功能：
  从 SofaScore 公共 JSON API 采集 2025-2026 赛季五大联赛（英超/西甲/意甲/德甲/法甲）
  的比赛阵容、球员详细统计、事件流（进球/红黄牌/换人）。

数据源：
  Base URL : https://api.sofascore.com/api/v1
  反爬机制 : Akamai Bot Manager（TLS 指纹），通过 curl_cffi impersonate="chrome" 绕过
  五大联赛 uniqueTournament IDs（已验证）:
    - 英超 Premier League : 17
    - 西甲 La Liga        : 8
    - 意甲 Serie A        : 23
    - 德甲 Bundesliga     : 35
    - 法甲 Ligue 1        : 34
  25/26 赛季 season IDs（已验证）:
    - 英超 : 76986   西甲 : 77559   意甲 : 76457   德甲 : 77333   法甲 : 77356

API 端点（每场比赛调用 4 个）:
  GET /event/{event_id}                       -> 赛事基础信息（球队/比分/球场/时间戳）
  GET /event/{event_id}/statistics            -> 球队级统计（控球率/xG/射门/跑动距离等 46 项）
  GET /event/{event_id}/lineups               -> 阵容+单场球员统计（首发/替补/阵型/位置/号码/37~45 项球员指标）
  GET /event/{event_id}/incidents             -> 事件流（进球/红黄牌/换人，含是否因伤换人）

写入数据库：
  {项目根目录}/data/odds.db
  复用现有 fbref schema 的 4 张表，通过 stats_source='sofascore' 区分数据来源：
    - fbref_match_mapping  : 存 sofascore event_id ↔ odds.db match_id 映射
    - match_lineups        : 单场每球员一行（首发/替补/阵型/换人时间）
    - match_player_stats   : 单场每球员一行（37~45 项单场指标 + stats_json 全量）
    - fbref_players        : 球员注册表（fbref_player_id 字段存 sofascore player id）

使用方法：
  # 全量采集五大联赛 25/26 赛季
  python final_sofascore_collector.py --leagues all --season 25/26

  # 仅采集英超前 5 轮（试跑）
  python final_sofascore_collector.py --leagues 英超 --season 25/26 --rounds 1-5 --limit 5

  # 断点续传（跳过已采集比赛）
  python final_sofascore_collector.py --leagues all --season 25/26 --resume

  # 仅采集不写库（验证数据可拉取）
  python final_sofascore_collector.py --leagues 西甲 --season 25/26 --dry-run --limit 3

输出：
  - logs/sofascore_collector_YYYYMMDD_HHMMSS.log  详细日志（每个关键节点都有打印）
  - logs/sofascore_progress_{season}.json         断点续传进度文件
  - logs/sofascore_collector_summary_*.json       运行汇总报告
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
import time
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

# ============================================================
# 路径与目录配置
# ============================================================

# 项目根目录（collection 的上一级，即 五大联赛专属模型/五大联赛专属模型）
MODEL_PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = MODEL_PROJECT_ROOT / "data" / "odds.db"
LOG_DIR = MODEL_PROJECT_ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
# 原始 JSON 落盘目录（用于排查数据缺失/解析错误，按联赛分目录）
RAW_JSON_DIR = MODEL_PROJECT_ROOT / "data" / "sofascore_raw"
RAW_JSON_DIR.mkdir(parents=True, exist_ok=True)

# ============================================================
# 五大联赛 + 赛季配置（API verified 2026-08-09）
# ============================================================

LEAGUES_CONFIG: Dict[str, Dict[str, Any]] = {
    "英超": {
        "cn_name": "英超",
        "uniqueTournamentId": 17,
        "seasons": {
            "26/27": 96668,
            "25/26": 76986,
            "24/25": 61627,
            "23/24": 52186,
            "22/23": 41886,
            "21/22": 37036,
            "20/21": 29415,
            "19/20": 23776,
            "18/19": 17359,
            "17/18": 13380,
            "16/17": 11733,
        },
        "league_abbr": "EPL",
    },
    "西甲": {
        "cn_name": "西甲",
        "uniqueTournamentId": 8,
        "seasons": {
            "26/27": 97268,
            "25/26": 77559,
            "24/25": 61643,
            "23/24": 52376,
            "22/23": 42409,
            "21/22": 37223,
            "20/21": 32501,
            "19/20": 24127,
            "18/19": 18020,
            "17/18": 13662,
            "16/17": 11906,
        },
        "league_abbr": "LaLiga",
    },
    "意甲": {
        "cn_name": "意甲",
        "uniqueTournamentId": 23,
        "seasons": {
            "26/27": 95836,
            "25/26": 76457,
            "24/25": 63515,
            "23/24": 52760,
            "22/23": 42415,
            "21/22": 37475,
            "20/21": 32523,
            "19/20": 24644,
            "18/19": 17932,
            "17/18": 13768,
            "16/17": 11966,
        },
        "league_abbr": "SerieA",
    },
    "德甲": {
        "cn_name": "德甲",
        "uniqueTournamentId": 35,
        "seasons": {
            "26/27": 97464,
            "25/26": 77333,
            "24/25": 63516,
            "23/24": 52608,
            "22/23": 42268,
            "21/22": 37166,
            "20/21": 28210,
            "19/20": 23538,
            "18/19": 17597,
            "17/18": 13477,
            "16/17": 11818,
        },
        "league_abbr": "Bundesliga",
    },
    "法甲": {
        "cn_name": "法甲",
        "uniqueTournamentId": 34,
        "seasons": {
            "26/27": 96127,
            "25/26": 77356,
            "24/25": 61736,
            "23/24": 52571,
            "22/23": 42273,
            "21/22": 37167,
            "20/21": 28222,
            "19/20": 23872,
            "18/19": 17279,
            "17/18": 13384,
            "16/17": 11648,
        },
        "league_abbr": "Ligue1",
    },
}

API_BASE = "https://api.sofascore.com/api/v1"
REQUEST_TIMEOUT = 15
MAX_RETRIES = 2
RETRY_BACKOFF = 0.8  # 指数退避基数（秒）
REQUEST_DELAY = 0.5  # 单线程请求间隔（秒，用户多 IP 不怕封禁，激进提速）
CONCURRENT_WORKERS = 4  # 场内接口并发数（4 接口并行，提速 4 倍）
CHALLENGE_COOLDOWN_SECONDS = 1800  # 触发 403 challenge 后建议冷却时长（秒，30分钟）
BUSY_TIMEOUT_MS = 30000  # SQLite 写锁等待超时（毫秒，两爬虫双开时持锁的 HTTP I/O 可达 10s+）
CONNECT_TIMEOUT = 30  # sqlite3.connect timeout 参数（秒，Python 层等待锁的兜底）


class SofaScoreChallengeError(RuntimeError):
    """Akamai 403 challenge 反爬封禁异常。

    当 SofaScoreClient 检测到 403 challenge 时抛出，由上层采集循环捕获后
    立即中止整轮采集，避免继续写入空壳数据或污染断点续传进度。
    """


# 时区：SofaScore 时间戳为 UTC，转换为东八区写入数据库
CN_TZ = timezone(timedelta(hours=8))


# ============================================================
# 日志配置（双输出：控制台 + 文件，详细分级）
# ============================================================

def setup_logging(season_tag: str) -> logging.Logger:
    """配置日志：控制台 INFO 级别 + 文件 DEBUG 级别。

    日志文件命名：sofascore_collector_YYYYMMDD_HHMMSS.log
    位于 logs/ 目录下，每次运行独立成文件，便于排查数据缺失/解析错误。
    """
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_file = LOG_DIR / f"sofascore_collector_{ts}.log"

    logger = logging.getLogger("sofascore")
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # 文件 handler：DEBUG 级别，记录所有细节
    fh = logging.FileHandler(str(log_file), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(threadName)-12s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    # 控制台 handler：INFO 级别，输出关键进度
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.INFO)
    ch.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%H:%M:%S",
    ))

    logger.addHandler(fh)
    logger.addHandler(ch)

    logger.info("=" * 70)
    logger.info(f"SofaScore 五大联赛采集器启动 | season={season_tag}")
    logger.info(f"日志文件: {log_file}")
    logger.info(f"数据库  : {DB_PATH}")
    logger.info(f"原始JSON: {RAW_JSON_DIR}")
    logger.info("=" * 70)
    return logger


# ============================================================
# HTTP 客户端（curl_cffi + Akamai TLS 绕过 + 重试）
# ============================================================

# 延迟导入 curl_cffi，避免未安装时启动失败
try:
    from curl_cffi import requests as cffi_requests
    _HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    _HAS_CURL_CFFI = False

# Fallback: 用标准 requests（不能绕 Akamai，仅 --dry-run 时可勉强用于已开放端点）
try:
    import requests as std_requests
    _HAS_STD_REQUESTS = True
except ImportError:
    std_requests = None
    _HAS_STD_REQUESTS = False


class SofaScoreClient:
    """SofaScore API 客户端：封装 curl_cffi 会话 + 重试 + 限速。

    每个端点失败会重试 MAX_RETRIES 次，使用指数退避。
    所有请求都会被 logger 记录（URL / 状态码 / 耗时 / 响应大小）。
    """

    def __init__(self, logger: logging.Logger):
        if not _HAS_CURL_CFFI:
            logger.warning("⚠️ curl_cffi 未安装！Akamai 反爬将无法绕过，可能导致 403。"
                           "请执行: pip install curl_cffi")
        self.logger = logger
        self.session = cffi_requests.Session(impersonate="chrome") if _HAS_CURL_CFFI else None
        self._last_request_ts = 0.0
        self.challenge_detected = False  # Akamai 403 challenge 触发标记

    def _respect_rate_limit(self) -> None:
        """简单限速：确保两次请求间隔 >= REQUEST_DELAY 秒。"""
        elapsed = time.time() - self._last_request_ts
        if elapsed < REQUEST_DELAY:
            time.sleep(REQUEST_DELAY - elapsed)
        self._last_request_ts = time.time()

    def get(self, endpoint: str, tag: str = "") -> Optional[Dict[str, Any]]:
        """GET 请求，返回 JSON dict。失败返回 None。

        Args:
            endpoint: API 路径（如 /event/14025013/lineups）
            tag: 日志标识（如 "lineups"），便于在日志中区分接口
        """
        url = f"{API_BASE}{endpoint}"
        log_prefix = f"[GET {tag}]" if tag else "[GET]"

        for attempt in range(1, MAX_RETRIES + 1):
            self._respect_rate_limit()
            t0 = time.time()
            try:
                if self.session is not None:
                    resp = self.session.get(url, timeout=REQUEST_TIMEOUT)
                elif _HAS_STD_REQUESTS:
                    resp = std_requests.get(url, timeout=REQUEST_TIMEOUT,
                                            headers={"User-Agent": "Mozilla/5.0"})
                else:
                    self.logger.error(f"{log_prefix} 无可用 HTTP 客户端")
                    return None

                elapsed_ms = int((time.time() - t0) * 1000)
                size_kb = len(resp.content) / 1024

                if resp.status_code == 200:
                    self.logger.debug(
                        f"{log_prefix} OK 200 | {elapsed_ms}ms | {size_kb:.1f}KB | {endpoint}"
                    )
                    try:
                        return resp.json()
                    except Exception as je:
                        self.logger.error(
                            f"{log_prefix} JSON 解析失败 | {je} | endpoint={endpoint} | "
                            f"body前200字符: {resp.text[:200]}"
                        )
                        return None

                if resp.status_code == 404:
                    self.logger.warning(
                        f"{log_prefix} 404 Not Found | {endpoint} | 该数据可能不存在"
                    )
                    return None  # 404 不重试

                # 403 challenge：Akamai 反爬封禁，立即中止（不重试，越重试封得越久）
                if resp.status_code == 403:
                    body_lower = (resp.text or "")[:300].lower()
                    if any(kw in body_lower for kw in ("challenge", "akamai", "blocked", "denied")):
                        self.challenge_detected = True
                        self.logger.error(
                            f"{log_prefix} 403 CHALLENGE (Akamai 反爬封禁) | {endpoint} | "
                            f"立即中止不再重试 · body前200字符: {resp.text[:200]}"
                        )
                        return None

                # 4xx/5xx：记录并重试
                self.logger.warning(
                    f"{log_prefix} {resp.status_code} | attempt {attempt}/{MAX_RETRIES} | "
                    f"{endpoint} | body前200字符: {resp.text[:200]}"
                )
            except Exception as e:
                self.logger.warning(
                    f"{log_prefix} 异常 | attempt {attempt}/{MAX_RETRIES} | "
                    f"{endpoint} | {type(e).__name__}: {e}"
                )

            # 指数退避
            if attempt < MAX_RETRIES:
                backoff = RETRY_BACKOFF ** attempt
                self.logger.debug(f"{log_prefix} 等待 {backoff:.1f}s 后重试...")
                time.sleep(backoff)

        self.logger.error(f"{log_prefix} 全部 {MAX_RETRIES} 次重试失败 | {endpoint}")
        return None

    def close(self) -> None:
        if self.session is not None:
            try:
                self.session.close()
            except Exception:
                pass


# ============================================================
# 进度持久化（断点续传）
# ============================================================

@dataclass
class ProgressTracker:
    """断点续传进度跟踪器。

    进度文件路径：logs/sofascore_progress_{season_tag}.json
    结构：
      {"英超_25/26": ["14025013", "14082854"], "西甲_25/26": [...]}
    每个 key 是 {联赛}_{season}，value 是已完成的 event_id 列表。
    """
    season_tag: str
    progress_file: Path = field(init=False)
    completed: Dict[str, List[str]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.progress_file = LOG_DIR / f"sofascore_progress_{self.season_tag.replace('/', '_')}.json"
        self._load()

    def _load(self) -> None:
        if self.progress_file.exists():
            try:
                with open(self.progress_file, "r", encoding="utf-8") as f:
                    self.completed = json.load(f)
            except Exception:
                self.completed = {}

    def _save(self) -> None:
        try:
            with open(self.progress_file, "w", encoding="utf-8") as f:
                json.dump(self.completed, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logging.getLogger("sofascore").warning(f"进度文件保存失败: {e}")

    def is_done(self, league: str, event_id: str) -> bool:
        return event_id in self.completed.get(f"{league}_{self.season_tag}", [])

    def mark_done(self, league: str, event_id: str) -> None:
        key = f"{league}_{self.season_tag}"
        self.completed.setdefault(key, [])
        if event_id not in self.completed[key]:
            self.completed[key].append(event_id)
        self._save()


# ============================================================
# API 端点封装（每场比赛 4 个接口）
# ============================================================

def fetch_all_rounds(client: SofaScoreClient, league: str, season: str,
                     logger: logging.Logger) -> List[Dict[str, Any]]:
    """获取指定联赛+赛季的所有轮次信息。

    API: /unique-tournament/{tid}/season/{sid}/rounds
    返回示例: {"rounds": [{"round": 1, "name": "Round 1"}, ...]}
    """
    cfg = LEAGUES_CONFIG[league]
    tid = cfg["uniqueTournamentId"]
    sid = cfg["seasons"][season]
    endpoint = f"/unique-tournament/{tid}/season/{sid}/rounds"

    logger.info(f"[{league}] 获取赛季轮次列表 | tid={tid} sid={sid}")
    data = client.get(endpoint, tag="rounds")
    if not data or "rounds" not in data:
        logger.error(f"[{league}] 轮次列表为空！可能赛季未开始或 API 失败")
        return []
    rounds = data["rounds"]
    logger.info(f"[{league}] 共 {len(rounds)} 轮 | "
                f"首末轮: {rounds[0].get('round')} ~ {rounds[-1].get('round')}")
    return rounds


def fetch_round_events(client: SofaScoreClient, league: str, season: str,
                       round_num: int, logger: logging.Logger) -> List[Dict[str, Any]]:
    """获取指定轮次的所有比赛事件。

    API: /unique-tournament/{tid}/season/{sid}/events/round/{round_num}
    返回示例: {"events": [{"id": 14025013, "homeTeam": {...}, ...}, ...]}
    """
    cfg = LEAGUES_CONFIG[league]
    tid = cfg["uniqueTournamentId"]
    sid = cfg["seasons"][season]
    endpoint = f"/unique-tournament/{tid}/season/{sid}/events/round/{round_num}"

    data = client.get(endpoint, tag="round-events")
    if not data or "events" not in data:
        logger.warning(f"[{league} R{round_num}] 该轮比赛列表为空（可能尚未开始）")
        return []
    events = data["events"]
    logger.info(f"[{league} R{round_num}] 该轮 {len(events)} 场比赛")
    return events


def fetch_event_detail(client: SofaScoreClient, event_id: str,
                       logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """获取单场比赛基础信息。"""
    return client.get(f"/event/{event_id}", tag="event")


def fetch_event_statistics(client: SofaScoreClient, event_id: str,
                           logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """获取单场比赛球队级统计。"""
    return client.get(f"/event/{event_id}/statistics", tag="statistics")


def fetch_event_lineups(client: SofaScoreClient, event_id: str,
                        logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """获取单场比赛阵容（含首发/替补/阵型/单场球员统计）。"""
    return client.get(f"/event/{event_id}/lineups", tag="lineups")


def fetch_event_incidents(client: SofaScoreClient, event_id: str,
                          logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """获取单场比赛事件流（进球/红黄牌/换人）。"""
    return client.get(f"/event/{event_id}/incidents", tag="incidents")


# ============================================================
# 数据提取与转换（关键节点详细日志）
# ============================================================

def parse_event_basics(event: Dict[str, Any], league: str, season: str,
                       logger: logging.Logger) -> Dict[str, Any]:
    """从 /event 或 /events/round 返回的单个 event 中提取基础字段。

    输出字段：event_id, league, season, match_date(YYYY-MM-DD), match_time(HH:MM:SS),
              home_team, away_team, home_score, away_score, venue, status, round
    """
    event_id = str(event.get("id", ""))
    if not event_id:
        logger.error(f"[parse_event_basics] 缺少 event.id | data={event}")
        return {}

    home_team = event.get("homeTeam", {}).get("name", "") or ""
    away_team = event.get("awayTeam", {}).get("name", "") or ""
    if not home_team or not away_team:
        logger.warning(f"[event {event_id}] 球队名缺失: home='{home_team}' away='{away_team}'")

    ts = event.get("startTimestamp")
    if ts:
        dt_cn = datetime.fromtimestamp(ts, tz=CN_TZ)
        match_date = dt_cn.strftime("%Y-%m-%d")
        match_time = dt_cn.strftime("%H:%M:%S")
    else:
        match_date = ""
        match_time = ""
        logger.warning(f"[event {event_id}] 缺少 startTimestamp")

    home_score_obj = event.get("homeScore", {}) or {}
    away_score_obj = event.get("awayScore", {}) or {}
    home_score = home_score_obj.get("current")
    away_score = away_score_obj.get("current")

    venue = (event.get("venue") or {}).get("name", "") if event.get("venue") else ""
    status_obj = event.get("status") or {}
    status = status_obj.get("type", "")
    round_num = event.get("roundInfo", {}).get("round") if event.get("roundInfo") else None

    parsed = {
        "event_id": event_id,
        "league": league,
        "season": season,
        "match_date": match_date,
        "match_time": match_time,
        "home_team": home_team,
        "away_team": away_team,
        "home_score": home_score,
        "away_score": away_score,
        "venue": venue,
        "status": status,
        "round": round_num,
    }
    logger.debug(f"[event {event_id}] 基础信息: {home_team} {home_score}-{away_score} {away_team} "
                 f"| {match_date} {match_time} | R{round_num} | venue={venue} | status={status}")
    return parsed


def build_odds_match_id(match_date: str, home_team: str, away_team: str) -> str:
    """构建 odds.db 格式的 match_id: {date}_{home}_{away}（与 sporttery_collector 一致）。"""
    return f"{match_date}_{home_team}_{away_team}"


def parse_team_statistics(stats_data: Dict[str, Any], event_id: str,
                          logger: logging.Logger) -> Dict[str, Dict[str, Any]]:
    """解析球队级统计（ALL period）。

    返回结构: {"home": {key: value, ...}, "away": {key: value, ...}}
    其中 key 为 SofaScore 的 statisticsItem.key（如 ballPossession/expectedGoals/...）
    """
    home_stats: Dict[str, Any] = {}
    away_stats: Dict[str, Any] = {}

    if not stats_data or "statistics" not in stats_data:
        logger.warning(f"[event {event_id}] statistics 接口返回为空或缺少 statistics 字段")
        return {"home": home_stats, "away": away_stats}

    found_all_period = False
    for period in stats_data["statistics"]:
        if period.get("period") != "ALL":
            continue
        found_all_period = True
        for grp in period.get("groups", []):
            gname = grp.get("groupName", "")
            for item in grp.get("statisticsItems", []):
                key = item.get("key")
                if not key:
                    continue
                home_stats[key] = item.get("homeValue", item.get("home"))
                away_stats[key] = item.get("awayValue", item.get("away"))

    if not found_all_period:
        logger.warning(f"[event {event_id}] statistics 中未找到 ALL period 数据")

    logger.debug(f"[event {event_id}] 球队级统计: home={len(home_stats)}项, "
                 f"away={len(away_stats)}项")
    if not home_stats:
        logger.warning(f"[event {event_id}] 球队级统计为空，可能比赛未完成统计")
    return {"home": home_stats, "away": away_stats}


# 伤停原因代码映射
INJURY_REASON_MAP = {
    1: "伤病",
    2: "停赛",
    3: "转会",
    4: "其他",
}

# 伤停类型代码映射
INJURY_TYPE_MAP = {
    1: "Injury",        # 伤病
    2: "Suspended",     # 停赛
    3: "Transfer",      # 转会
    5: "Muscle Injury", # 肌肉伤病（常见于 reason=1 的详情）
}


def parse_missing_players(side_data: Dict[str, Any], event_id: str,
                          side: str, logger: logging.Logger) -> List[Dict[str, Any]]:
    """解析 SofaScore lineups 接口的 missingPlayers 字段（伤停球员）。

    独立封装，便于其他联赛采集时复用。

    参数:
        side_data : lineups 接口返回的 home/away 子对象
        event_id  : SofaScore event_id
        side      : "home" 或 "away"
        logger    : 日志实例

    返回:
        parsed_missing : 伤停球员列表，每元素为 dict，字段对齐 match_missing_players 表

    字段说明:
        - reason_code: SofaScore 原始 reason 值（1=伤病,2=停赛,3=转会）
        - reason     : 映射后的中文描述
        - expected_end_date: 去除时间部分的日期（YYYY-MM-DD）
        - market_value_eur : 球员市场价值（欧元）
        - missing_type    : 缺阵类型（missing/suspended 等）
    """
    raw_missing = side_data.get("missingPlayers", []) or []
    parsed_missing: List[Dict[str, Any]] = []

    for mp in raw_missing:
        player_obj = mp.get("player", {}) or {}
        player_id = player_obj.get("id")
        player_name = player_obj.get("name", "")

        # 跳过没有 ID 或姓名的条目
        if not player_id or not player_name:
            logger.debug(f"[event {event_id} {side}] 伤停条目缺少 player.id 或 name，跳过: {mp}")
            continue

        reason_code = mp.get("reason", 0)
        reason_desc = INJURY_REASON_MAP.get(reason_code, "未知")
        expected_end = mp.get("expectedEndDate", "")
        # 提取日期部分（去除时间）
        if expected_end:
            expected_end = expected_end.split("T")[0]

        parsed_missing.append({
            "player_id": str(player_id),
            "player_name": player_name,
            "player_short_name": player_obj.get("shortName", ""),
            "position": player_obj.get("position", ""),
            "jersey_number": player_obj.get("jerseyNumber"),
            "missing_type": mp.get("type", "missing"),
            "reason_code": reason_code,
            "reason": reason_desc,
            "description": mp.get("description", ""),
            "external_type": mp.get("externalType"),
            "expected_end_date": expected_end,
            "country": (player_obj.get("country") or {}).get("name", ""),
            "market_value_eur": (player_obj.get("proposedMarketValueRaw") or {}).get("value"),
            "height": player_obj.get("height"),
            "sofascore_slug": player_obj.get("slug", ""),
        })

    if parsed_missing:
        logger.info(f"[event {event_id} {side}] 发现 {len(parsed_missing)} 名伤停球员")
        for mp in parsed_missing:
            logger.info(f"    {mp['player_name']} (#{mp['jersey_number']}) - "
                       f"{mp['reason']}: {mp['description']} "
                       f"(预计复出: {mp['expected_end_date']})")

    return parsed_missing


def parse_lineups(lineups_data: Dict[str, Any], event_id: str,
                  logger: logging.Logger) -> Dict[str, Any]:
    """解析阵容数据。

    返回结构:
      {
        "confirmed": bool,
        "home": {
            "formation": "4-2-3-1",
            "players": [...],
            "missing_players": [  # 新增：伤停球员
                {
                    "player_id": ..., "player_name": ...,
                    "position": ..., "jersey_number": ...,
                    "missing_type": "missing",
                    "reason_code": 1, "reason": "伤病",
                    "description": "Muscle Injury",
                    "expected_end_date": "2026-09-03",
                    "country": "...", "market_value": ...
                }, ...
            ]
        },
        "away": {...}
      }
    """
    if not lineups_data:
        logger.warning(f"[event {event_id}] lineups 接口返回为空")
        return {"confirmed": False, "home": {"formation": "", "players": [], "missing_players": []},
                "away": {"formation": "", "players": [], "missing_players": []}}

    confirmed = bool(lineups_data.get("confirmed", False))
    if not confirmed:
        logger.info(f"[event {event_id}] 阵容未确认（confirmed=False），"
                    f"数据可能是预测阵容而非实际首发")

    result: Dict[str, Any] = {"confirmed": confirmed, "home": {}, "away": {}}

    for side in ("home", "away"):
        side_data = lineups_data.get(side, {}) or {}
        formation = side_data.get("formation", "")
        raw_players = side_data.get("players", []) or []

        # 解析伤停球员（调用独立函数，便于复用）
        parsed_missing = parse_missing_players(side_data, event_id, side, logger)

        parsed_players: List[Dict[str, Any]] = []
        starter_count = 0
        sub_count = 0
        players_with_stats = 0

        for p in raw_players:
            player_obj = p.get("player", {}) or {}
            player_id = player_obj.get("id")
            player_name = player_obj.get("name", "")
            if not player_id or not player_name:
                logger.debug(f"[event {event_id} {side}] 球员信息缺失，跳过: {p}")
                continue

            is_starter = not p.get("substitute", False)
            if is_starter:
                starter_count += 1
            else:
                sub_count += 1

            stats = p.get("statistics") or {}
            if stats:
                players_with_stats += 1

            parsed_players.append({
                "player_id": str(player_id),
                "player_name": player_name,
                "player_short_name": player_obj.get("shortName", ""),
                "position": p.get("position") or player_obj.get("position", ""),
                "shirt_number": p.get("shirtNumber") or p.get("jerseyNumber")
                                or player_obj.get("jerseyNumber"),
                "is_starter": is_starter,
                "captain": bool(p.get("captain", False)),
                "statistics": stats,
                # 球员基础信息（内联在 lineup 中）
                "height": player_obj.get("height"),
                "nationality": (player_obj.get("country") or {}).get("name", ""),
                "date_of_birth_ts": player_obj.get("dateOfBirthTimestamp"),
                "market_value_eur": (player_obj.get("proposedMarketValueRaw") or {}).get("value"),
                "sofascore_slug": player_obj.get("slug", ""),
            })

        result[side] = {
            "formation": formation,
            "players": parsed_players,
            "missing_players": parsed_missing,
        }
        logger.debug(f"[event {event_id} {side}] 阵型={formation} | "
                     f"球员={len(parsed_players)}(首发{starter_count}/替补{sub_count}) | "
                     f"带统计球员={players_with_stats} | "
                     f"伤停={len(parsed_missing)}")

    # 校验：标准比赛首发应为 11 人
    for side in ("home", "away"):
        starters = sum(1 for p in result[side]["players"] if p["is_starter"])
        if confirmed and starters != 11:
            logger.warning(f"[event {event_id} {side}] 阵容已确认但首发仅 {starters} 人"
                           f"（应为 11），数据可能不完整")

    return result


def parse_incidents(incidents_data: Dict[str, Any], event_id: str,
                    logger: logging.Logger) -> Dict[str, List[Dict[str, Any]]]:
    """解析事件流（进球/红黄牌/换人）。

    SofaScore 的 incidentType 字段值（已验证）:
      - "goal"           : 进球
      - "substitution"   : 换人（含 injury 字段标记是否因伤换人）
      - "card"           : 红黄牌（cardType/subType 区分黄/红/两黄变红）
      - "periodStart"/"matchEnd"/"periodEnd" : 阶段起止（不提取）

    返回结构:
      {
        "goals": [{time, side, player, assist1, xg, subType}, ...],
        "substitutions": [{time, side, player_in, player_out, injury}, ...],
        "cards": [{time, side, player, card_type}, ...]
      }
    """
    goals: List[Dict[str, Any]] = []
    substitutions: List[Dict[str, Any]] = []
    cards: List[Dict[str, Any]] = []

    if not incidents_data or "incidents" not in incidents_data:
        logger.warning(f"[event {event_id}] incidents 接口返回为空或缺少 incidents 字段")
        return {"goals": goals, "substitutions": substitutions, "cards": cards}

    raw_list = incidents_data["incidents"] or []
    logger.debug(f"[event {event_id}] incidents 原始事件数: {len(raw_list)}")

    for ev in raw_list:
        itype = ev.get("incidentType") or ev.get("type")
        if not itype:
            continue

        time_min = ev.get("time")
        added = ev.get("addedTime")
        time_str = f"{time_min}'" if not added else f"{time_min}+{added}'"
        is_home = ev.get("isHome")
        side = "home" if is_home else "away" if is_home is False else "unknown"

        if itype == "goal":
            scorer = (ev.get("player") or {}).get("name", "")
            assist1 = (ev.get("assist1") or {}).get("name", "") if ev.get("assist1") else ""
            goals.append({
                "time": time_str,
                "minute": time_min,
                "side": side,
                "player": scorer,
                "assist": assist1,
                "xg": ev.get("xg"),
                "sub_type": ev.get("incidentClass") or ev.get("subType"),
                "home_score": ev.get("homeScore"),
                "away_score": ev.get("awayScore"),
            })
        elif itype == "substitution":
            player_in = (ev.get("playerIn") or {}).get("name", "") if ev.get("playerIn") else ""
            player_out = (ev.get("playerOut") or {}).get("name", "") if ev.get("playerOut") else ""
            injury = bool(ev.get("injury", False))
            substitutions.append({
                "time": time_str,
                "minute": time_min,
                "side": side,
                "player_in": player_in,
                "player_out": player_out,
                "injury": injury,
                "incident_class": ev.get("incidentClass"),
            })
        elif itype == "card":
            player = (ev.get("player") or {}).get("name", "")
            cards.append({
                "time": time_str,
                "minute": time_min,
                "side": side,
                "player": player,
                "card_type": ev.get("incidentClass") or ev.get("cardType"),
                "reason": ev.get("reason", ""),
            })

    logger.debug(f"[event {event_id}] incidents 解析: 进球={len(goals)} "
                 f"换人={len(substitutions)} 红黄牌={len(cards)}")
    return {"goals": goals, "substitutions": substitutions, "cards": cards}


# ============================================================
# 赛后采集（模块 A2：复盘闭环数据基础）
# ============================================================

# SofaScore 已完赛 status：type 为 finished/ended，或 code==100（FT）
FINISHED_STATUS_TYPES = ("finished", "ended")


def collect_post_match(client: SofaScoreClient, event_id: str,
                       logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """赛后数据采集（模块 A2 单场入口）。

    拉取并解析 4 个端点：
      - event detail   : 实际比分 / 半场比分 / 状态(须为已完赛) / 开赛时间
      - statistics     : 射门/射正/控球/xG/角球/红黄牌等球队级统计（parse_team_statistics）
      - lineups        : 实际首发/替补/阵型（供 A3 阵容异动归因对比 match_lineups 预计首发）
      - incidents      : 进球/红牌/换人时间线（供 A4 复盘报告关键事件时间线）

    返回 dict（不含联赛/规范队名，由调用方补充）；比赛未结束或 detail 关键字段缺失返回 None。
    """
    detail = fetch_event_detail(client, event_id, logger)
    if not detail or "event" not in detail:
        logger.warning(f"[event {event_id}] 赛后采集失败：event detail 接口无数据")
        return None

    event = detail.get("event", {}) or {}
    status_obj = event.get("status") or {}
    status_type = str(status_obj.get("type", "")).lower()
    status_code = status_obj.get("code")

    if status_type not in FINISHED_STATUS_TYPES and status_code != 100:
        logger.info(f"[event {event_id}] 比赛未结束（status={status_type}/{status_code}），跳过赛后采集")
        return None

    home_score = (event.get("homeScore") or {}).get("current")
    away_score = (event.get("awayScore") or {}).get("current")
    if home_score is None or away_score is None:
        logger.warning(f"[event {event_id}] 已完赛但缺少比分（home={home_score} away={away_score}），跳过")
        return None

    home_p1 = (event.get("homeScore") or {}).get("period1")
    away_p1 = (event.get("awayScore") or {}).get("period1")
    half_score = None
    if home_p1 is not None and away_p1 is not None:
        half_score = f"{home_p1}:{away_p1}"

    ts = event.get("startTimestamp")
    match_date = ""
    if ts:
        match_date = datetime.fromtimestamp(ts, tz=CN_TZ).strftime("%Y-%m-%d")

    stats_data = fetch_event_statistics(client, event_id, logger)
    lineups_data = fetch_event_lineups(client, event_id, logger)
    incidents_data = fetch_event_incidents(client, event_id, logger)

    result = {
        "event_id": event_id,
        "status_type": status_type,
        "status_code": status_code,
        "match_date": match_date,
        "home_team": (event.get("homeTeam") or {}).get("name", ""),
        "away_team": (event.get("awayTeam") or {}).get("name", ""),
        "home_score": home_score,
        "away_score": away_score,
        "score": f"{home_score}:{away_score}",
        "half_score": half_score,
        "stats": parse_team_statistics(stats_data or {}, event_id, logger),
        "lineups": parse_lineups(lineups_data or {}, event_id, logger),
        "incidents": parse_incidents(incidents_data or {}, event_id, logger),
    }
    logger.info(f"[event {event_id}] 赛后采集完成: {result['home_team']} {result['score']} "
                f"{result['away_team']} | 半场 {half_score} | "
                f"stats={len(result['stats'].get('home', {}))}项 lineups_confirmed={result['lineups'].get('confirmed')} "
                f"events={sum(len(v) for v in result['incidents'].values())}")
    return result


# ============================================================
# 数据库写入（事务 + 幂等 + 详细日志）
# ============================================================

def init_db_schema_if_needed(conn: sqlite3.Connection, logger: logging.Logger) -> None:
    """确保 odds.db 已创建 fbref 4 张表（如果不存在则创建）。

    复用 scripts/fbref_schema.py 的 DDL，避免依赖外部脚本。
    SofaScore 数据通过 stats_source='sofascore' 区分。
    """
    cursor = conn.cursor()
    ddl = [
        # fbref_match_mapping
        """CREATE TABLE IF NOT EXISTS fbref_match_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            odds_match_id TEXT NOT NULL,
            fbref_match_id TEXT NOT NULL,
            fbref_match_url TEXT NOT NULL,
            fbref_match_slug TEXT,
            league TEXT NOT NULL,
            season TEXT NOT NULL,
            match_date TEXT NOT NULL,
            home_team_fbref TEXT,
            away_team_fbref TEXT,
            home_team_cn TEXT,
            away_team_cn TEXT,
            fbref_week INTEGER,
            fbref_score TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(fbref_match_id),
            UNIQUE(odds_match_id, season)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fbref_map_league_season ON fbref_match_mapping(league, season)",
        "CREATE INDEX IF NOT EXISTS idx_fbref_map_date ON fbref_match_mapping(match_date)",
        # match_lineups
        """CREATE TABLE IF NOT EXISTS match_lineups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            fbref_match_id TEXT,
            team TEXT NOT NULL,
            team_fbref TEXT,
            formation TEXT,
            player_name TEXT NOT NULL,
            player_name_cn TEXT,
            fbref_player_id TEXT,
            jersey_number INTEGER,
            position TEXT,
            is_starter INTEGER DEFAULT 1,
            sub_in_time TEXT,
            sub_out_time TEXT,
            sub_in_for TEXT,
            sub_out_for TEXT,
            sub_reason TEXT,
            minutes_played INTEGER,
            captain INTEGER DEFAULT 0,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, team, player_name)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_lineup_match ON match_lineups(match_id)",
        "CREATE INDEX IF NOT EXISTS idx_lineup_team ON match_lineups(team, match_id)",
        "CREATE INDEX IF NOT EXISTS idx_lineup_player ON match_lineups(fbref_player_id)",
        # match_player_stats
        """CREATE TABLE IF NOT EXISTS match_player_stats (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            fbref_match_id TEXT,
            team TEXT NOT NULL,
            team_fbref TEXT,
            player_name TEXT NOT NULL,
            player_name_cn TEXT,
            fbref_player_id TEXT,
            jersey_number INTEGER,
            position TEXT,
            is_starter INTEGER DEFAULT 1,
            minutes_played INTEGER,
            goals INTEGER DEFAULT 0,
            assists INTEGER DEFAULT 0,
            penalties_made INTEGER DEFAULT 0,
            penalties_attempted INTEGER DEFAULT 0,
            shots INTEGER DEFAULT 0,
            shots_on_target INTEGER DEFAULT 0,
            yellow_cards INTEGER DEFAULT 0,
            red_cards INTEGER DEFAULT 0,
            fouls_committed INTEGER DEFAULT 0,
            fouls_drawn INTEGER DEFAULT 0,
            offsides INTEGER DEFAULT 0,
            crosses INTEGER DEFAULT 0,
            tackles_won INTEGER DEFAULT 0,
            interceptions INTEGER DEFAULT 0,
            own_goals INTEGER DEFAULT 0,
            passes_completed INTEGER,
            passes_attempted INTEGER,
            pass_completion_pct REAL,
            total_distance_passes REAL,
            progressive_distance_passes REAL,
            short_passes_completed INTEGER,
            short_passes_attempted INTEGER,
            medium_passes_completed INTEGER,
            medium_passes_attempted INTEGER,
            long_passes_completed INTEGER,
            long_passes_attempted INTEGER,
            key_passes INTEGER,
            passes_into_final_third INTEGER,
            passes_into_penalty_area INTEGER,
            crosses_into_penalty_area INTEGER,
            progressive_passes INTEGER,
            tackles INTEGER,
            tackles_won_def INTEGER,
            tackles_in_def_third INTEGER,
            tackles_in_mid_third INTEGER,
            tackles_in_att_third INTEGER,
            dribblers_tackled INTEGER,
            dribblers_challenged INTEGER,
            blocks INTEGER,
            blocked_shots INTEGER,
            blocked_passes INTEGER,
            clearances INTEGER,
            errors_leading_to_shot INTEGER,
            touches INTEGER,
            touches_def_pen_area INTEGER,
            touches_def_third INTEGER,
            touches_mid_third INTEGER,
            touches_att_third INTEGER,
            touches_att_pen_area INTEGER,
            dribbles_completed_pos INTEGER,
            dribbles_attempted_pos INTEGER,
            successful_dribble_pct REAL,
            players_beaten INTEGER,
            carries INTEGER,
            carry_distance REAL,
            progressive_carries INTEGER,
            carries_into_final_third INTEGER,
            carries_into_penalty_area INTEGER,
            miscontrols INTEGER,
            dispossessed INTEGER,
            passes_received INTEGER,
            progressive_passes_received INTEGER,
            corner_kicks INTEGER,
            penalties_won INTEGER,
            penalties_conceded INTEGER,
            ball_recoveries INTEGER,
            aerials_won INTEGER,
            aerials_lost INTEGER,
            aerial_win_pct REAL,
            gk_shots_on_target_against INTEGER,
            gk_goals_against INTEGER,
            gk_saves INTEGER,
            gk_save_pct REAL,
            gk_psa REAL,
            xg REAL,
            xg_npxg REAL,
            xa REAL,
            sca INTEGER,
            gca INTEGER,
            stats_json TEXT,
            stats_source TEXT DEFAULT 'fbref',
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, team, player_name)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_pstats_match ON match_player_stats(match_id)",
        "CREATE INDEX IF NOT EXISTS idx_pstats_team ON match_player_stats(team, match_id)",
        "CREATE INDEX IF NOT EXISTS idx_pstats_player ON match_player_stats(fbref_player_id)",
        # match_missing_players (伤停球员)
        """CREATE TABLE IF NOT EXISTS match_missing_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT NOT NULL,
            fbref_match_id TEXT NOT NULL,
            team TEXT NOT NULL,
            player_name TEXT NOT NULL,
            player_short_name TEXT,
            fbref_player_id TEXT,
            jersey_number INTEGER,
            position TEXT,
            missing_type TEXT DEFAULT 'missing',
            reason_code INTEGER DEFAULT 0,
            reason TEXT DEFAULT '',
            description TEXT,
            external_type INTEGER,
            expected_end_date TEXT,
            country TEXT,
            market_value_eur REAL,
            height INTEGER,
            sofascore_slug TEXT,
            collected_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(match_id, team, fbref_player_id)
        )""",
        "CREATE INDEX IF NOT EXISTS idx_missing_match ON match_missing_players(match_id)",
        "CREATE INDEX IF NOT EXISTS idx_missing_team ON match_missing_players(team, match_id)",
        "CREATE INDEX IF NOT EXISTS idx_missing_reason ON match_missing_players(reason)",
        "CREATE INDEX IF NOT EXISTS idx_missing_player ON match_missing_players(fbref_player_id)",
        # fbref_players
        """CREATE TABLE IF NOT EXISTS fbref_players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            fbref_player_id TEXT UNIQUE NOT NULL,
            fbref_player_url TEXT,
            player_name_en TEXT NOT NULL,
            player_name_cn TEXT,
            five_leagues_player_id INTEGER,
            primary_team_cn TEXT,
            primary_team_fbref TEXT,
            primary_position TEXT,
            nationality TEXT,
            first_seen_match TEXT,
            last_updated TEXT DEFAULT CURRENT_TIMESTAMP
        )""",
        "CREATE INDEX IF NOT EXISTS idx_fbref_players_team ON fbref_players(primary_team_cn)",
        "CREATE INDEX IF NOT EXISTS idx_fbref_players_name ON fbref_players(player_name_en)",
        # 扩展列：SofaScore 特有指标（rating, expectedGoals, metersCovered* 等）
        "ALTER TABLE match_player_stats ADD COLUMN rating REAL",
        "ALTER TABLE match_player_stats ADD COLUMN expected_goals REAL",
        "ALTER TABLE match_player_stats ADD COLUMN expected_assists REAL",
        "ALTER TABLE match_player_stats ADD COLUMN total_shots INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN big_chances_created INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN big_chances_missed INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN accurate_long_balls INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN total_long_balls INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN accurate_crosses INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN total_crosses INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN successful_dribbles INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN total_dribbles INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN total_tackles INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN duels_won INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN duels_total INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN aerials_won_total INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN aerials_total INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN ball_recoveries_sofa INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN possession_lost INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN total_pass_sofa INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN accurate_pass_sofa INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN key_passes_sofa INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN touches_sofa INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN meters_covered_walking_km REAL",
        "ALTER TABLE match_player_stats ADD COLUMN meters_covered_jogging_km REAL",
        "ALTER TABLE match_player_stats ADD COLUMN meters_covered_running_km REAL",
        "ALTER TABLE match_player_stats ADD COLUMN meters_covered_high_speed_running_km REAL",
        "ALTER TABLE match_player_stats ADD COLUMN meters_covered_sprinting_km REAL",
        "ALTER TABLE match_player_stats ADD COLUMN total_ball_carries_distance REAL",
        "ALTER TABLE match_player_stats ADD COLUMN ball_carries_count INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN total_progression REAL",
        "ALTER TABLE match_player_stats ADD COLUMN goals_prevented REAL",
        "ALTER TABLE match_player_stats ADD COLUMN gk_saves_sofa INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN saved_shots_from_inside_box INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN good_high_claim INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN punches INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN keeper_save_value REAL",
        "ALTER TABLE match_player_stats ADD COLUMN accurate_keeper_sweeper INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN total_keeper_sweeper INTEGER",
        # --- 2026-08-22 新增: 6 分项补全 ---
        "ALTER TABLE match_player_stats ADD COLUMN ground_duels_won INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN ground_duels_lost INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN ground_duels_total INTEGER",
        "ALTER TABLE match_player_stats ADD COLUMN shots_on_target INTEGER",
    ]
    for stmt in ddl:
        try:
            cursor.execute(stmt)
        except sqlite3.OperationalError as oe:
            # ALTER TABLE ADD COLUMN 在列已存在时会报错，忽略即可
            if "duplicate column name" not in str(oe).lower():
                logger.warning(f"DDL 执行警告: {oe} | SQL: {stmt[:80]}...")
    conn.commit()
    logger.debug("odds.db schema 检查/初始化完成（4 张表 + SofaScore 扩展列）")


# SofaScore 球员统计 key -> match_player_stats 列名映射
# 仅映射能直接对应的字段，其他全部存入 stats_json
SOFA_TO_FBREF_FIELD_MAP = {
    "rating": "rating",
    "expectedGoals": "expected_goals",
    "expectedAssists": "expected_assists",
    "totalShots": "total_shots",
    "shotsOnTarget": "shots_on_target",
    "goals": "goals",
    "goalAssist": "assists",
    "bigChanceCreated": "big_chances_created",
    "bigChanceMissed": "big_chances_missed",
    "accurateLongBalls": "accurate_long_balls",
    "totalLongBalls": "total_long_balls",
    "accurateCross": "accurate_crosses",
    "totalCross": "total_crosses",
    "successfulDribbles": "successful_dribbles",
    "totalDribbles": "total_dribbles",
    "wonContest": "successful_dribbles",
    "totalContest": "total_dribbles",
    "totalTackle": "total_tackles",
    "tackles": "tackles",
    "interceptionWon": "interceptions",
    "duelWon": "duels_won",
    "duelTotal": "duels_total",
    "duelLost": "duels_lost_sofa",
    "aerialWon": "aerials_won_total",
    "aerialTotal": "aerials_total",
    "aerialLost": "aerials_lost_sofa",
    "ballRecovery": "ball_recoveries_sofa",
    "possessionLostCtrl": "possession_lost",
    "totalPass": "total_pass_sofa",
    "accuratePass": "accurate_pass_sofa",
    "keyPass": "key_passes_sofa",
    "touches": "touches_sofa",
    "totalClearance": "clearances",
    "minutesPlayed": "minutes_played",
    "metersCoveredWalkingKm": "meters_covered_walking_km",
    "metersCoveredJoggingKm": "meters_covered_jogging_km",
    "metersCoveredRunningKm": "meters_covered_running_km",
    "metersCoveredHighSpeedRunningKm": "meters_covered_high_speed_running_km",
    "metersCoveredSprintingKm": "meters_covered_sprinting_km",
    "totalBallCarriesDistance": "total_ball_carries_distance",
    "ballCarriesCount": "ball_carries_count",
    "totalProgression": "total_progression",
    "goalsPrevented": "goals_prevented",
    "saves": "gk_saves_sofa",
    "savedShotsFromInsideTheBox": "saved_shots_from_inside_box",
    "goodHighClaim": "good_high_claim",
    "punches": "punches",
    "keeperSaveValue": "keeper_save_value",
    "accurateKeeperSweeper": "accurate_keeper_sweeper",
    "totalKeeperSweeper": "total_keeper_sweeper",
    # --- 2026-08-22 新增: 6 分项补全 ---
    # 注意: ground_duels_won 不在此映射表中，由步骤 2b 计算得出 (duelWon - aerialWon)
}


def extract_player_stats_fields(stats: Dict[str, Any],
                                goal_count: int = 0, assist_count: int = 0,
                                yellow: int = 0, red: int = 0,
                                logger: logging.Logger = None,
                                context: str = "") -> Dict[str, Any]:
    """从 SofaScore 球员 statistics dict 提取结构化字段。

    独立封装，便于其他联赛采集时复用。

    参数:
        stats        : SofaScore lineups 接口中球员的 statistics dict
        goal_count   : 从 incidents 索引获取的进球数（优先于 stats.goals）
        assist_count : 从 incidents 索引获取的助攻数
        yellow       : 黄牌数
        red          : 红牌数
        logger       : 日志实例（传入则输出详细调试日志）
        context      : 日志上下文标识（如 "event_16363633_player_Xavi"）

    返回:
        dynamic_fields: 可直接用于 match_player_stats INSERT 的 dict，
                        key 为列名，value 为值。已包含：
                        - SOFA_TO_FBREF_FIELD_MAP 映射的所有字段
                        - 自动补算的 duels_total / aerials_total
                        - 强制覆盖的 goals / assists / yellow_cards / red_cards
    """
    dynamic_fields: Dict[str, Any] = {}

    # 0. 类型保护：stats 必须是 dict，否则直接返回空
    if not isinstance(stats, dict):
        print(f"[DEBUG extract_player_stats_fields] stats type={type(stats).__name__} value={stats!r} context={context}")
        if logger:
            ctx = f"[{context}] " if context else ""
            logger.debug(f"{ctx}stats 非 dict (type={type(stats).__name__})，跳过字段提取")
        return dynamic_fields

    # 1. 从映射表提取
    mapped_count = 0
    missed_keys = []
    for sofa_key, col_name in SOFA_TO_FBREF_FIELD_MAP.items():
        if sofa_key in stats:
            dynamic_fields[col_name] = stats[sofa_key]
            mapped_count += 1
        else:
            missed_keys.append(sofa_key)

    # 2. 补算 SofaScore 未直接提供的 total 字段（won+lost=total）
    dw = dynamic_fields.get("duels_won")
    dl = dynamic_fields.get("duels_lost_sofa")
    if dw is not None or dl is not None:
        dynamic_fields["duels_total"] = (dw or 0) + (dl or 0)

    aw = dynamic_fields.get("aerials_won_total")
    al = dynamic_fields.get("aerials_lost_sofa")
    if aw is not None or al is not None:
        dynamic_fields["aerials_total"] = (aw or 0) + (al or 0)

    # 2b. 补算地面对抗（SofaScore 无 groundDuelWon 字段，需由总对抗-空中对抗得出）
    # 注意：aerialWon/aerialLost 缺失时视为 0（球员没有空中对抗记录）
    if dw is not None:
        dynamic_fields["ground_duels_won"] = max(0, (dw or 0) - (aw or 0))
    if dl is not None:
        dynamic_fields["ground_duels_lost"] = max(0, (dl or 0) - (al or 0))
    if dw is not None or dl is not None:
        gdw = dynamic_fields.get("ground_duels_won", 0)
        gdl = dynamic_fields.get("ground_duels_lost", 0)
        dynamic_fields["ground_duels_total"] = (gdw or 0) + (gdl or 0)

    # 3. 强制覆盖：goals/assists/yellow/red 用 incidents 索引值
    dynamic_fields["goals"] = goal_count
    dynamic_fields["assists"] = assist_count
    dynamic_fields["yellow_cards"] = yellow
    dynamic_fields["red_cards"] = red

    # 4. 日志输出
    if logger:
        ctx = f"[{context}] " if context else ""
        try:
            logger.debug(
                f"{ctx}字段提取: stats输入{len(stats)}key → 映射{len(mapped_count)}列 "
                f"(mapped={mapped_count}, missed={len(missed_keys)}, "
                f"duels_total={dynamic_fields.get('duels_total')}, "
                f"aerials_total={dynamic_fields.get('aerials_total')}, "
                f"goals={goal_count}, assists={assist_count}, "
                f"yellow={yellow}, red={red})"
            )
            if missed_keys:
                critical_missed = [k for k in missed_keys
                                   if k in ('bigChanceCreated', 'bigChanceMissed',
                                            'shotsOnTarget', 'progressivePass',
                                            'passReceived', 'shotOffTarget')]
                if critical_missed:
                    logger.debug(f"{ctx} 关键缺失key: {critical_missed}")
        except Exception:
            pass  # 日志异常不影响主流程

    return dynamic_fields


def write_missing_players_to_db(cursor: sqlite3.Cursor, odds_match_id: str,
                                event_id: str, team_name: str,
                                missing_players: List[Dict[str, Any]],
                                logger: logging.Logger) -> int:
    """将伤停球员列表写入 match_missing_players 表（幂等）。

    独立封装，便于其他联赛采集时复用。

    参数:
        cursor          : 数据库游标（调用方管理事务）
        odds_match_id   : odds.db 的 match_id（如 2026-08-22_Arsenal_Coventry City）
        event_id        : SofaScore 的 event_id
        team_name       : 球队名（与 match_lineups 的 team 字段一致）
        missing_players : parse_missing_players() 返回的列表
        logger          : 日志实例

    返回:
        写入条数（INSERT OR REPLACE 的 rowcount 之和）

    说明:
        - 使用 INSERT OR REPLACE 实现幂等：可反复重跑刷新数据
        - 18 列全字段写入
        - 不调用 commit()：由调用方统一管理事务
    """
    written = 0
    for mp in missing_players:
        try:
            cursor.execute(
                """INSERT OR REPLACE INTO match_missing_players
                   (match_id, fbref_match_id, team, player_name, player_short_name,
                    fbref_player_id, jersey_number, position, missing_type,
                    reason_code, reason, description, external_type,
                    expected_end_date, country, market_value_eur, height,
                    sofascore_slug)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (odds_match_id, event_id, team_name,
                 mp.get("player_name", ""), mp.get("player_short_name", ""),
                 mp.get("player_id"), mp.get("jersey_number"),
                 mp.get("position", ""), mp.get("missing_type", "missing"),
                 mp.get("reason_code", 0), mp.get("reason", ""),
                 mp.get("description", ""), mp.get("external_type"),
                 mp.get("expected_end_date", ""), mp.get("country", ""),
                 mp.get("market_value_eur"), mp.get("height"),
                 mp.get("sofascore_slug", "")),
            )
            written += 1
        except sqlite3.OperationalError as oe:
            logger.warning(f"[event {event_id}] missing_players 写入失败: "
                           f"{mp.get('player_name', '')} | {oe}")
    return written


def write_match_to_db(conn: sqlite3.Connection, parsed_event: Dict[str, Any],
                      team_stats: Dict[str, Dict[str, Any]],
                      lineups: Dict[str, Any],
                      incidents: Dict[str, List[Dict[str, Any]]],
                      logger: logging.Logger) -> Dict[str, int]:
    """将单场比赛全部数据写入 odds.db（事务 + 幂等）。

    写入 5 张表：
      1. fbref_match_mapping   : event_id ↔ odds_match_id 映射
      2. match_lineups         : 每球员一行（首发/替补/阵型/换人时间）
      3. match_player_stats    : 每球员一行（37~45 项单场指标 + stats_json 全量）
      4. match_missing_players : 伤停球员（伤病/停赛/转会缺阵）
      5. fbref_players         : 球员注册（首次见到时 upsert）

    返回写入统计: {"mapping": N, "lineups": N, "player_stats": N,
                  "missing_players": N, "players": N}
    """
    counts = {"mapping": 0, "lineups": 0, "player_stats": 0,
              "missing_players": 0, "players": 0}
    event_id = parsed_event["event_id"]
    odds_match_id = build_odds_match_id(
        parsed_event["match_date"],
        parsed_event["home_team"],
        parsed_event["away_team"],
    )
    league = parsed_event["league"]
    season = parsed_event["season"]

    cursor = conn.cursor()
    try:
        # ---------- 1. fbref_match_mapping ----------
        # 注意: UNIQUE(odds_match_id, season) 与 UNIQUE(fbref_match_id) 双约束
        # 使用 INSERT OR IGNORE 实现幂等
        fbref_score = (f"{parsed_event['home_score']}:{parsed_event['away_score']}"
                       if parsed_event["home_score"] is not None else "")
        cursor.execute(
            """INSERT OR IGNORE INTO fbref_match_mapping
               (odds_match_id, fbref_match_id, fbref_match_url, fbref_match_slug,
                league, season, match_date, home_team_fbref, away_team_fbref,
                home_team_cn, away_team_cn, fbref_week, fbref_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (odds_match_id, event_id,
             f"https://www.sofascore.com/api/v1/event/{event_id}",
             f"sofascore-event-{event_id}",
             league, season, parsed_event["match_date"],
             parsed_event["home_team"], parsed_event["away_team"],
             parsed_event["home_team"], parsed_event["away_team"],
             parsed_event["round"], fbref_score),
        )
        counts["mapping"] = cursor.rowcount if cursor.rowcount > 0 else 0
        logger.debug(f"[event {event_id}] 写入 fbref_match_mapping: rowcount={counts['mapping']}")

        # ---------- 2/3. match_lineups + match_player_stats ----------
        # 先按 incidents 的 substitutions 建索引: (side, player_name) -> (sub_in_time, sub_out_time, injury)
        sub_index: Dict[Tuple[str, str], Dict[str, Any]] = {}
        for sub in incidents.get("substitutions", []):
            side = sub["side"]
            # 换入球员：sub_in_time
            if sub["player_in"]:
                key = (side, sub["player_in"])
                sub_index.setdefault(key, {}).update({
                    "sub_in_time": sub["time"],
                    "sub_reason": "injury" if sub["injury"] else sub.get("incident_class", ""),
                })
            # 换出球员：sub_out_time
            if sub["player_out"]:
                key = (side, sub["player_out"])
                sub_index.setdefault(key, {}).update({
                    "sub_out_time": sub["time"],
                    "sub_reason": "injury" if sub["injury"] else sub.get("incident_class", ""),
                })

        # 按 (side, player_name) 索引进球/红黄牌，便于反查填充
        goal_index: Dict[Tuple[str, str], int] = {}
        for g in incidents.get("goals", []):
            if g["player"]:
                key = (g["side"], g["player"])
                goal_index[key] = goal_index.get(key, 0) + 1

        card_index: Dict[Tuple[str, str], Tuple[int, int]] = {}
        for c in incidents.get("cards", []):
            if c["player"]:
                key = (c["side"], c["player"])
                y, r = card_index.get(key, (0, 0))
                ct = (c.get("card_type") or "").lower()
                if "yellow" in ct:
                    y += 1
                elif "red" in ct:
                    r += 1
                card_index[key] = (y, r)

        for side in ("home", "away"):
            side_data = lineups.get(side, {})
            formation = side_data.get("formation", "")
            players = side_data.get("players", [])
            team_name = parsed_event["home_team"] if side == "home" else parsed_event["away_team"]

            for p in players:
                player_name = p["player_name"]
                sofa_player_id = p["player_id"]
                shirt_num = p["shirt_number"]
                position = p["position"]
                is_starter = 1 if p["is_starter"] else 0
                captain_val = 1 if p.get("captain") else 0
                raw_stats = p["statistics"]
                stats = raw_stats if isinstance(raw_stats, dict) else {}
                minutes_played = stats.get("minutesPlayed")

                # 从 incidents 索引中查换人时间
                sub_info = sub_index.get((side, player_name), {})
                sub_in_time = sub_info.get("sub_in_time")
                sub_out_time = sub_info.get("sub_out_time")
                sub_reason = sub_info.get("sub_reason")

                # ---------- 2. match_lineups ----------
                try:
                    cursor.execute(
                        """INSERT OR REPLACE INTO match_lineups
                           (match_id, fbref_match_id, team, team_fbref, formation,
                            player_name, fbref_player_id, jersey_number, position,
                            is_starter, sub_in_time, sub_out_time, sub_in_for,
                            sub_out_for, sub_reason, minutes_played, captain)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                        (odds_match_id, event_id, team_name, team_name, formation,
                         player_name, sofa_player_id, shirt_num, position,
                         is_starter, sub_in_time, sub_out_time, None, None,
                         sub_reason, minutes_played, captain_val),
                    )
                    counts["lineups"] += 1
                except sqlite3.IntegrityError as ie:
                    logger.warning(f"[event {event_id} {side}] lineups 写入冲突: "
                                   f"{player_name} | {ie}")

                # ---------- 3. match_player_stats ----------
                # 从 incidents 索引补全 goals/yellow/red
                goals_count = goal_index.get((side, player_name), 0)
                yellow, red = card_index.get((side, player_name), (0, 0))
                # 优先用 incidents 的进球数（更准确），fallback 用 stats.goals
                if goals_count == 0:
                    goals_count = int(stats.get("goals", 0) or 0)
                assists_count = int(stats.get("goalAssist", 0) or 0)

                # 构造动态字段映射（调用独立函数，便于复用）
                dynamic_fields = extract_player_stats_fields(
                    stats, goals_count, assists_count, yellow, red,
                    logger=logger,
                    context=f"event_{event_id}_{side}_{player_name}",
                )

                # stats_json 存全量原始数据，便于后续排查
                stats_json_str = json.dumps(stats, ensure_ascii=False)

                # 构造 INSERT 语句（动态字段）
                all_cols = (["match_id", "fbref_match_id", "team", "team_fbref",
                             "player_name", "fbref_player_id", "jersey_number",
                             "position", "is_starter", "stats_json", "stats_source",
                             "quality_flag"]
                            + list(dynamic_fields.keys()))
                # P1-15: 数据质量标记，区分「未登场全0」与「真实0数据」
                mp = int(dynamic_fields.get("minutes_played") or 0)
                if mp > 0:
                    qflag = "played_minutes"
                elif is_starter:
                    qflag = "subbed_zero"  # 首发但 0 分钟（红牌/极早伤退）
                else:
                    qflag = "did_not_play"  # 未替补登场，全 0 是真实的"未参与"
                all_vals = ([odds_match_id, event_id, team_name, team_name,
                             player_name, sofa_player_id, shirt_num, position,
                             is_starter, stats_json_str, "sofascore", qflag]
                            + list(dynamic_fields.values()))
                placeholders = ",".join(["?"] * len(all_cols))
                col_list = ",".join(all_cols)

                try:
                    cursor.execute(
                        f"""INSERT OR REPLACE INTO match_player_stats ({col_list})
                            VALUES ({placeholders})""",
                        all_vals,
                    )
                    counts["player_stats"] += 1
                except sqlite3.OperationalError as oe:
                    logger.error(f"[event {event_id} {side}] player_stats 写入失败: "
                                 f"{player_name} | {oe}")

                # ---------- 4. fbref_players (upsert) ----------
                try:
                    cursor.execute(
                        """INSERT OR IGNORE INTO fbref_players
                           (fbref_player_id, fbref_player_url, player_name_en,
                            primary_team_cn, primary_team_fbref, primary_position,
                            nationality, first_seen_match)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                        (sofa_player_id,
                         f"https://www.sofascore.com/api/v1/player/{sofa_player_id}",
                         player_name, team_name, team_name, position,
                         p.get("nationality", ""),
                         odds_match_id),
                    )
                    if cursor.rowcount > 0:
                        counts["players"] += 1
                except sqlite3.IntegrityError:
                    pass  # 球员已注册，正常情况

            # ---------- 4. match_missing_players (伤停球员) ----------
            # 调用独立函数（便于其他联赛采集复用）
            missing_players = side_data.get("missing_players", []) or []
            counts["missing_players"] += write_missing_players_to_db(
                cursor, odds_match_id, event_id, team_name,
                missing_players, logger
            )

        conn.commit()
        logger.debug(f"[event {event_id}] DB 写入完成: lineups={counts['lineups']} "
                     f"player_stats={counts['player_stats']} "
                     f"missing_players={counts['missing_players']} "
                     f"players_new={counts['players']}")

    except Exception as e:
        conn.rollback()
        logger.error(f"[event {event_id}] DB 写入异常，已回滚: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())
    return counts


# ============================================================
# 原始 JSON 落盘（用于排查数据缺失/解析错误）
# ============================================================

def save_raw_json(event_id: str, league: str,
                  event_detail: Optional[Dict[str, Any]],
                  stats: Optional[Dict[str, Any]],
                  lineups: Optional[Dict[str, Any]],
                  incidents: Optional[Dict[str, Any]],
                  logger: logging.Logger) -> bool:
    """将单场比赛 4 个接口的原始 JSON 落盘。

    目录: data/sofascore_raw/{league}/{event_id}/
    文件: event.json, statistics.json, lineups.json, incidents.json

    返回: True 成功，False 失败（失败仅记日志，绝不抛出 —— 冷存储失败不应影响DB热写入）
    """
    # SAFETY: 原始 JSON 是二级冷存储，任何存储介质异常（WinError 433 设备不存在 /
    # 路径写保护 / 磁盘满等）都不能中断主采集流程。因此本函数使用最外层宽泛 catch。
    try:
        out_dir = RAW_JSON_DIR / league / event_id
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            # 路径写保护 / 磁盘脱机 / 文件夹权限 / WinError 433 等
            logger.warning(
                f"[event {event_id}] 原始JSON目录创建失败 (OSError {e.errno})，"
                f"已跳过冷存储保存，不影响DB热写入: {e}"
            )
            return False
        except Exception as e:
            logger.warning(
                f"[event {event_id}] 原始JSON目录创建异常，已跳过冷存储保存，不影响DB写入: "
                f"{type(e).__name__}: {e}"
            )
            return False

        for fname, data in [("event.json", event_detail),
                            ("statistics.json", stats),
                            ("lineups.json", lineups),
                            ("incidents.json", incidents)]:
            if data is not None:
                try:
                    with open(out_dir / fname, "w", encoding="utf-8") as f:
                        json.dump(data, f, ensure_ascii=False, indent=2)
                except Exception as e:
                    logger.debug(
                        f"[event {event_id}] 保存 {fname} 失败 (已忽略，不影响DB写入): {e}"
                    )
        return True
    except Exception as e:
        # 任何意外：兜底捕获，保证调用方不中断
        logger.warning(
            f"[event {event_id}] 原始JSON保存总流程异常（已忽略，不影响DB写入）: "
            f"{type(e).__name__}: {e}"
        )
        return False


# ============================================================
# 单场比赛完整采集流程
# ============================================================

def collect_single_event(client: SofaScoreClient, event: Dict[str, Any],
                         league: str, season: str, conn: sqlite3.Connection,
                         logger: logging.Logger, dry_run: bool = False) -> Dict[str, Any]:
    """采集单场比赛的完整数据（4 个接口 + 解析 + 写库）。

    返回采集结果摘要: {event_id, status, counts, errors}
    """
    event_id = str(event.get("id", ""))
    parsed = parse_event_basics(event, league, season, logger)
    if not event_id:
        return {"event_id": "", "status": "failed", "counts": {}, "errors": ["missing event.id"]}

    home_team = parsed["home_team"]
    away_team = parsed["away_team"]
    match_date = parsed["match_date"]
    logger.info(f"[event {event_id}] 开始采集 | {league} | {home_team} vs {away_team} | {match_date}")

    # 4 个接口并发拉取（同一场内并行，提升效率）
    results: Dict[str, Optional[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=CONCURRENT_WORKERS, thread_name_prefix=f"evt{event_id}") as ex:
        futures = {
            ex.submit(fetch_event_detail, client, event_id, logger): "detail",
            ex.submit(fetch_event_statistics, client, event_id, logger): "statistics",
            ex.submit(fetch_event_lineups, client, event_id, logger): "lineups",
            ex.submit(fetch_event_incidents, client, event_id, logger): "incidents",
        }
        for fut in as_completed(futures):
            tag = futures[fut]
            try:
                results[tag] = fut.result()
            except Exception as e:
                logger.error(f"[event {event_id}] 接口 {tag} 异常: {type(e).__name__}: {e}")
                results[tag] = None

    detail = results.get("detail")
    stats = results.get("statistics")
    lineups_data = results.get("lineups")
    incidents_data = results.get("incidents")

    # 接口成功率统计
    ok_count = sum(1 for v in results.values() if v is not None)
    logger.info(f"[event {event_id}] 4 接口拉取完成 | 成功 {ok_count}/4 | "
                f"detail={'Y' if detail else 'N'} "
                f"stats={'Y' if stats else 'N'} "
                f"lineups={'Y' if lineups_data else 'N'} "
                f"incidents={'Y' if incidents_data else 'N'}")

    # 403 challenge 检测：反爬封禁已触发，跳过写库并中止整轮采集
    if getattr(client, "challenge_detected", False):
        logger.error(f"[event {event_id}] 检测到 Akamai 403 challenge，跳过写库并中止本轮采集")
        raise SofaScoreChallengeError(
            f"event {event_id} 触发 403 challenge（建议冷却 {CHALLENGE_COOLDOWN_SECONDS}s 后 --resume）"
        )

    # 原始 JSON 落盘（冷存储：失败仅WARNING，不中断DB热写入，不抛异常）
    try:
        save_raw_json(event_id, league, detail, stats, lineups_data, incidents_data, logger)
    except Exception as e:
        logger.warning(
            f"[event {event_id}] 原始JSON保存失败（调用方兜底捕获，已忽略，不影响DB写入）: "
            f"{type(e).__name__}: {e}"
        )

    # 解析
    team_stats = parse_team_statistics(stats or {}, event_id, logger)
    lineups_parsed = parse_lineups(lineups_data or {}, event_id, logger)
    incidents_parsed = parse_incidents(incidents_data or {}, event_id, logger)

    # 数据完整性校验日志
    lineup_home_count = len(lineups_parsed.get("home", {}).get("players", []))
    lineup_away_count = len(lineups_parsed.get("away", {}).get("players", []))
    missing_home = len(lineups_parsed.get("home", {}).get("missing_players", []))
    missing_away = len(lineups_parsed.get("away", {}).get("missing_players", []))
    logger.info(f"[event {event_id}] 解析完成 | 阵容确认={lineups_parsed.get('confirmed')} | "
                f"home球员={lineup_home_count}(伤停{missing_home}) "
                f"away球员={lineup_away_count}(伤停{missing_away}) | "
                f"球队级统计 home={len(team_stats.get('home', {}))}项 | "
                f"事件: 进球{len(incidents_parsed['goals'])} "
                f"换人{len(incidents_parsed['substitutions'])} "
                f"红黄牌{len(incidents_parsed['cards'])}")

    counts: Dict[str, int] = {}
    errors: List[str] = []

    if ok_count < 4:
        missing = [tag for tag, v in results.items() if v is None]
        errors.append(f"接口失败: {','.join(missing)}")

    # 写库
    if not dry_run:
        try:
            counts = write_match_to_db(conn, parsed, team_stats, lineups_parsed,
                                       incidents_parsed, logger)
            logger.info(f"[event {event_id}] 写库完成 | "
                        f"lineups={counts.get('lineups', 0)} "
                        f"player_stats={counts.get('player_stats', 0)} "
                        f"missing={counts.get('missing_players', 0)} "
                        f"players_new={counts.get('players', 0)} "
                        f"mapping={'新增' if counts.get('mapping') else '已存在'}")
        except Exception as e:
            errors.append(f"DB 写入异常: {e}")
            logger.error(f"[event {event_id}] DB 写入异常: {e}")
    else:
        logger.info(f"[event {event_id}] --dry-run 模式，跳过写库")

    return {
        "event_id": event_id,
        "league": league,
        "match": f"{home_team} vs {away_team}",
        "date": match_date,
        "status": "ok" if not errors else "partial",
        "counts": counts,
        "errors": errors,
        "api_success": ok_count,
    }


# ============================================================
# 主流程：单联赛采集 + 全联赛调度
# ============================================================

def parse_rounds_range(rounds_arg: str) -> Optional[Tuple[int, int]]:
    """解析 --rounds 参数，如 '1-5' 返回 (1, 5)，'3' 返回 (3, 3)。"""
    if not rounds_arg:
        return None
    try:
        if "-" in rounds_arg:
            start, end = map(int, rounds_arg.split("-"))
            return start, end
        return int(rounds_arg), int(rounds_arg)
    except ValueError:
        return None


def collect_league(client: SofaScoreClient, league: str, season: str,
                   rounds_range: Optional[Tuple[int, int]],
                   limit: Optional[int], resume: bool, dry_run: bool,
                   progress: ProgressTracker, conn: sqlite3.Connection,
                   logger: logging.Logger) -> List[Dict[str, Any]]:
    """采集单联赛所有轮次的所有比赛。"""
    logger.info("=" * 60)
    logger.info(f"开始采集联赛: {league} | season={season} | rounds={rounds_range} | "
                f"limit={limit} | resume={resume} | dry_run={dry_run}")
    logger.info("=" * 60)

    # 1. 获取所有轮次
    all_rounds = fetch_all_rounds(client, league, season, logger)
    if not all_rounds:
        logger.warning(f"[{league}] 无轮次数据，跳过")
        return []

    # 2. 过滤轮次
    if rounds_range:
        start, end = rounds_range
        all_rounds = [r for r in all_rounds if start <= r.get("round", 0) <= end]
        logger.info(f"[{league}] 过滤后保留轮次: {start}~{end}, 共 {len(all_rounds)} 轮")

    # 3. 拉取每轮的比赛列表
    all_events: List[Dict[str, Any]] = []
    for r in all_rounds:
        round_num = r.get("round")
        events = fetch_round_events(client, league, season, round_num, logger)
        all_events.extend(events)

    total = len(all_events)
    logger.info(f"[{league}] 共扫描到 {total} 场比赛")

    if limit:
        all_events = all_events[:limit]
        logger.info(f"[{league}] --limit={limit} 截断，实际处理 {len(all_events)} 场")

    # 4. 断点续传过滤
    if resume:
        before = len(all_events)
        all_events = [e for e in all_events
                      if not progress.is_done(league, str(e.get("id", "")))]
        skipped = before - len(all_events)
        logger.info(f"[{league}] --resume 模式: 跳过已采集 {skipped} 场，剩余 {len(all_events)} 场")

    if not all_events:
        logger.info(f"[{league}] 无待采集比赛")
        return []

    # 5. 串行采集每场比赛（场内 4 接口并发，场间串行避免限流）
    results: List[Dict[str, Any]] = []
    success_count = 0
    fail_count = 0
    partial_count = 0

    for idx, event in enumerate(all_events, 1):
        event_id = str(event.get("id", ""))
        logger.info(f"[{league}] 进度 {idx}/{len(all_events)} ({idx*100//len(all_events)}%) | "
                    f"event_id={event_id}")
        try:
            r = collect_single_event(client, event, league, season, conn, logger, dry_run)
            results.append(r)
            if r["status"] == "ok":
                success_count += 1
            elif r["status"] == "partial":
                partial_count += 1
            else:
                fail_count += 1
            # 标记进度（即使部分接口失败也标记，避免反复重试）
            if not dry_run:
                progress.mark_done(league, event_id)
        except SofaScoreChallengeError as ce:
            logger.error(f"[{league}] Akamai 反爬封禁，立即中止整轮采集: {ce}")
            logger.error(f"[{league}] 当前场未写入进度，请冷却 {CHALLENGE_COOLDOWN_SECONDS}s 后 --resume 续传")
            progress._save()
            break
        except KeyboardInterrupt:
            logger.warning(f"[{league}] 用户中断 (Ctrl+C)，已保存进度，可 --resume 续传")
            progress._save()
            break
        except Exception as e:
            logger.error(f"[{league}] event {event_id} 采集异常: {type(e).__name__}: {e}")
            logger.error(traceback.format_exc())
            fail_count += 1
            results.append({"event_id": event_id, "league": league,
                            "status": "failed", "errors": [str(e)], "counts": {}})

    logger.info(f"[{league}] 联赛采集完成 | 总 {len(results)} 场 | "
                f"成功 {success_count} 部分 {partial_count} 失败 {fail_count}")
    return results


def run_main(leagues: List[str], season: str, rounds_range: Optional[Tuple[int, int]],
             limit: Optional[int], resume: bool, dry_run: bool) -> None:
    """主入口。"""
    logger = setup_logging(season)
    progress = ProgressTracker(season)
    client = SofaScoreClient(logger)

    # DB 连接
    conn: Optional[sqlite3.Connection] = None
    if not dry_run:
        if not DB_PATH.exists():
            logger.error(f"数据库不存在: {DB_PATH} | 请先创建 odds.db")
            return
        conn = sqlite3.connect(str(DB_PATH), check_same_thread=False, timeout=CONNECT_TIMEOUT)
        # 并发写入安全：WAL 模式 + 30 秒锁等待（另一爬虫持锁做 HTTP 时可等它释放再写）
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute(f"PRAGMA busy_timeout={BUSY_TIMEOUT_MS}")
        init_db_schema_if_needed(conn, logger)
        logger.info(f"已连接数据库: {DB_PATH} | WAL ON | busy_timeout={BUSY_TIMEOUT_MS}ms | connect_timeout={CONNECT_TIMEOUT}s")
    else:
        logger.info("--dry-run 模式：不连接数据库，仅采集并落盘 JSON")

    # 汇总报告
    summary: Dict[str, Any] = {
        "run_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "season": season,
        "leagues": leagues,
        "rounds_range": list(rounds_range) if rounds_range else None,
        "limit": limit,
        "resume": resume,
        "dry_run": dry_run,
        "league_results": {},
        "totals": {"events": 0, "ok": 0, "partial": 0, "failed": 0,
                   "lineups": 0, "player_stats": 0, "players_new": 0},
    }

    t_start = time.time()
    try:
        for league in leagues:
            if league not in LEAGUES_CONFIG:
                logger.error(f"未知联赛: {league}，跳过")
                continue
            if season not in LEAGUES_CONFIG[league]["seasons"]:
                logger.error(f"联赛 {league} 不支持赛季 {season}，跳过")
                continue

            league_results = collect_league(
                client, league, season, rounds_range, limit, resume, dry_run,
                progress, conn, logger,
            )
            summary["league_results"][league] = league_results
            for r in league_results:
                summary["totals"]["events"] += 1
                summary["totals"][r["status"]] = summary["totals"].get(r["status"], 0) + 1
                counts = r.get("counts", {}) or {}
                summary["totals"]["lineups"] += counts.get("lineups", 0)
                summary["totals"]["player_stats"] += counts.get("player_stats", 0)
                summary["totals"]["players_new"] += counts.get("players", 0)
    finally:
        client.close()
        if conn:
            conn.close()
        elapsed = int(time.time() - t_start)
        summary["elapsed_seconds"] = elapsed

        # 保存汇总报告
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        summary_file = LOG_DIR / f"sofascore_collector_summary_{ts}.json"
        try:
            with open(summary_file, "w", encoding="utf-8") as f:
                json.dump(summary, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.error(f"汇总报告保存失败: {e}")

        logger.info("=" * 70)
        logger.info("采集流程结束")
        logger.info(f"总耗时: {elapsed}s")
        logger.info(f"采集场次: {summary['totals']['events']} "
                    f"(成功 {summary['totals']['ok']} / "
                    f"部分 {summary['totals']['partial']} / "
                    f"失败 {summary['totals']['failed']})")
        if not dry_run:
            logger.info(f"写入 lineups: {summary['totals']['lineups']} 行")
            logger.info(f"写入 player_stats: {summary['totals']['player_stats']} 行")
            logger.info(f"新增球员注册: {summary['totals']['players_new']} 人")
        logger.info(f"汇总报告: {summary_file}")
        logger.info("=" * 70)


# ============================================================
# CLI 入口
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(
        description="SofaScore 五大联赛批量数据采集器",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 全量采集五大联赛 25/26 赛季
  python final_sofascore_collector.py --leagues all --season 25/26

  # 仅采集英超前 5 轮（试跑）
  python final_sofascore_collector.py --leagues 英超 --season 25/26 --rounds 1-5 --limit 5

  # 断点续传
  python final_sofascore_collector.py --leagues all --season 25/26 --resume

  # 仅采集不写库（验证数据可拉取）
  python final_sofascore_collector.py --leagues 西甲 --season 25/26 --dry-run --limit 3
        """,
    )
    parser.add_argument("--leagues", type=str, default="all",
                        help="联赛列表，逗号分隔（英超,西甲,意甲,德甲,法甲）或 all")
    parser.add_argument("--season", type=str, default="25/26",
                        help="赛季（如 25/26, 24/25, 23/24）")
    parser.add_argument("--rounds", type=str, default=None,
                        help="轮次范围（如 1-5 或 3）")
    parser.add_argument("--limit", type=int, default=None,
                        help="每联赛最多采集场次（用于试跑）")
    parser.add_argument("--resume", action="store_true",
                        help="断点续传：跳过已采集比赛")
    parser.add_argument("--dry-run", action="store_true",
                        help="仅采集不写库，原始 JSON 仍会落盘")
    parser.add_argument("--post-match", metavar="EVENT_ID", default=None,
                        help="赛后采集调试：单场 event 拉取+解析（detail/statistics/lineups/incidents），不写库")

    args = parser.parse_args()

    logger = setup_logging("post-match")

    # 赛后采集调试模式：单场拉取+解析，不写库（批量触发走 scripts/run_post_match_pipeline.py）
    if args.post_match:
        client = SofaScoreClient(logger=logger)
        try:
            data = collect_post_match(client, args.post_match, logger)
            if data is None:
                print(f"\n❌ event {args.post_match} 赛后采集失败（未结束或数据缺失），详见日志")
                sys.exit(1)
            print(f"\n✅ 赛后采集成功 event={args.post_match}")
            print(f"   比赛: {data['home_team']} {data['score']} {data['away_team']} (半场 {data['half_score']})")
            print(f"   日期: {data['match_date']} | 状态: {data['status_type']}/{data['status_code']}")
            stats = data["stats"]
            print(f"   球队统计: home={len(stats.get('home', {}))}项 away={len(stats.get('away', {}))}项")
            print(f"     射正: home={stats.get('home', {}).get('shotsOnTarget')} "
                  f"away={stats.get('away', {}).get('shotsOnTarget')}")
            print(f"     控球: home={stats.get('home', {}).get('ballPossession')} "
                  f"away={stats.get('away', {}).get('ballPossession')}")
            print(f"     xG:   home={stats.get('home', {}).get('expectedGoals')} "
                  f"away={stats.get('away', {}).get('expectedGoals')}")
            lineups = data["lineups"]
            print(f"   阵容: confirmed={lineups.get('confirmed')}")
            for side in ("home", "away"):
                sd = lineups.get(side, {})
                starters = sum(1 for p in sd.get("players", []) if p.get("is_starter"))
                print(f"     {side}: 阵型={sd.get('formation')} 首发={starters} 伤停={len(sd.get('missing_players', []))}")
            inc = data["incidents"]
            print(f"   事件: 进球={len(inc['goals'])} 换人={len(inc['substitutions'])} 红黄牌={len(inc['cards'])}")
        finally:
            client.close()
        return

    # 解析联赛
    if args.leagues.lower() == "all":
        leagues = list(LEAGUES_CONFIG.keys())
    else:
        leagues = [s.strip() for s in args.leagues.split(",") if s.strip()]

    # 解析轮次
    rounds_range = parse_rounds_range(args.rounds)

    # 校验 curl_cffi
    if not _HAS_CURL_CFFI:
        print("⚠️  警告: curl_cffi 未安装，无法绕过 Akamai 反爬，可能全部 403。")
        print("   建议先执行: pip install curl_cffi")
        resp = input("是否继续? (y/N): ").strip().lower()
        if resp != "y":
            sys.exit(0)

    run_main(leagues, args.season, rounds_range, args.limit, args.resume, args.dry_run)


if __name__ == "__main__":
    main()
