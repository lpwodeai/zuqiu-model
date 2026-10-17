# -*- coding: utf-8 -*-
"""
SofaScore 联赛积分榜采集器（C-20261016-120）
============================================

目的：matches 赛果库断更/覆盖不全导致 §十四 排名/战绩失真（勒芒「已赛2」vs 真实 6）。
本采集器每日抓取五大联赛 26/27 赛季 SofaScore 权威积分榜快照，落 odds.db
league_standings 表；报告侧优先读快照、缺失回退 matches 现算（展示层，零模型影响）；
并与 matches 现算结果对拍（自检证伪仪表：两队已赛/积分不一致即告警）。

接口（实测 2026-10-16）：
  GET /unique-tournament/{utid}/seasons                          -> 选 26/27 赛季
  GET /unique-tournament/{utid}/season/{sid}/standings/total     -> standings[0].rows
反爬：curl_cffi 403 常态化，SofaScoreClient 自动降级 CDP(用户 Edge) 后端，已验证可用。
用法：
  python collection/collect_sofascore_standings.py                # 全部 5 联赛
  python collection/collect_sofascore_standings.py --leagues 德甲,法甲
异常处置：单联赛失败不阻断其余联赛；任何异常 exit 0（与 20:30 链非阻断步同风格）。
"""
import sys
import json
import time
import sqlite3
import logging
import argparse
from pathlib import Path
from datetime import datetime

_MODEL_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_MODEL_ROOT / "collection"))
sys.path.insert(0, str(_MODEL_ROOT / "scripts"))
sys.path.insert(0, str(_MODEL_ROOT / "features"))

from final_sofascore_collector import SofaScoreClient  # noqa: E402
from feature_utils import normalize_team_name as _norm_cn  # noqa: E402
try:
    from sofascore_pre_match_features import SOFA_TEAM_CN_MAP  # SofaScore 英文名 -> 中文名
except Exception:
    SOFA_TEAM_CN_MAP = {}

DB_PATH = _MODEL_ROOT / "data" / "odds.db"

# SOFA_TEAM_CN_MAP 缺口补充（2026-10-16 首采实测 12 个未映射，同 final_500_collector._EXTRA_CN_TO_EN 模式）
_ALIAS_CN = {
    "TSG Hoffenheim": "霍芬海姆",
    "Borussia M'gladbach": "门兴格拉德巴赫",
    "RC Strasbourg": "斯特拉斯堡",
    "Brentford": "布伦特福德",
    "Everton": "埃弗顿",
    "Ipswich Town": "伊普斯维奇",
    "Coventry City": "考文垂",
    "Fulham": "富勒姆",
    "Celta Vigo": "塞尔塔",
    "Athletic Club": "毕尔巴鄂竞技",
    "Getafe": "赫塔费",
    "Elche": "埃尔切",
}

# 五大联赛 SofaScore unique-tournament id（固定）
UTID_MAP = {"英超": 17, "西甲": 8, "意甲": 23, "德甲": 35, "法甲": 34}

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS league_standings (
  snapshot_date TEXT NOT NULL,
  league TEXT NOT NULL,
  season TEXT NOT NULL,
  utid INTEGER,
  season_id INTEGER,
  team_en TEXT NOT NULL,
  team_cn TEXT,
  position INTEGER,
  points INTEGER,
  played INTEGER,
  wins INTEGER, draws INTEGER, losses INTEGER,
  gf INTEGER, ga INTEGER, gd INTEGER,
  fetched_at TEXT,
  PRIMARY KEY (snapshot_date, league, team_en)
)
"""


def _pick_season(client, logger, utid, want="26/27"):
    """从 /seasons 选目标赛季（默认 26/27，缺失取 id 最大者）。"""
    d = client.get(f"/unique-tournament/{utid}/seasons", tag="sofa_standings_seasons")
    seasons = (d or {}).get("seasons") or []
    if not seasons:
        return None, None
    for s in seasons:
        if want in (s.get("name") or "") or want in (s.get("year") or ""):
            return s.get("id"), s.get("name")
    latest = max(seasons, key=lambda s: s.get("id") or 0)
    return latest.get("id"), latest.get("name")


def _to_cn(name_en):
    """SofaScore 英文名 -> 规范中文名（别名表 + SOFA_TEAM_CN_MAP + feature_utils 归一兜底）。"""
    if not name_en:
        return None
    cn = _ALIAS_CN.get(name_en)
    if not cn:
        cn = SOFA_TEAM_CN_MAP.get(name_en)
    if not cn:
        # 轻量尝试：去 "FC"/"SC" 等后缀再查一次
        base = name_en.replace(" FC", "").replace(" SC", "").strip()
        cn = _ALIAS_CN.get(base) or SOFA_TEAM_CN_MAP.get(base)
    return _norm_cn(cn) if cn else None


def collect_league(client, logger, conn, league, want_season, snapshot_date):
    """抓取并入库单个联赛积分榜快照。返回 (ok, n_rows, season_name)。"""
    utid = UTID_MAP[league]
    sid, sname = _pick_season(client, logger, utid, want_season)
    if not sid:
        logger.warning(f"[{league}] seasons 接口无可用赛季，跳过")
        return False, 0, None
    d = client.get(f"/unique-tournament/{utid}/season/{sid}/standings/total",
                   tag="sofa_standings")
    st = ((d or {}).get("standings") or [{}])[0]
    rows = st.get("rows") or []
    if not rows:
        logger.warning(f"[{league}] standings/total 空返回，跳过")
        return False, 0, sname

    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    conn.execute("DELETE FROM league_standings WHERE snapshot_date=? AND league=?",
                 (snapshot_date, league))
    n = 0
    for r in rows:
        team = (r.get("team") or {})
        en = team.get("name") or ""
        if not en:
            continue
        gf, ga = r.get("scoresFor") or 0, r.get("scoresAgainst") or 0
        conn.execute(
            "INSERT OR REPLACE INTO league_standings VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (snapshot_date, league, sname, utid, sid, en, _to_cn(en),
             r.get("position"), r.get("points"), r.get("matches"),
             r.get("wins"), r.get("draws"), r.get("losses"),
             gf, ga, gf - ga, now))
        n += 1
    conn.commit()
    logger.info(f"[{league}] OK: {sname} {n}/{len(rows)} 队")
    return True, n, sname


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--leagues", default="all", help="逗号分隔中文联赛名或 all")
    ap.add_argument("--season", default="26/27")
    args = ap.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(message)s")
    logger = logging.getLogger("sofa_standings")
    leagues = list(UTID_MAP) if args.leagues in ("all", "全部") else [
        x.strip() for x in args.leagues.split(",") if x.strip() in UTID_MAP]

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute(CREATE_SQL)
    conn.commit()

    snapshot_date = datetime.now().strftime("%Y-%m-%d")
    client = SofaScoreClient(logger=logger)
    ok_n, results = 0, []
    try:
        for lg in leagues:
            try:
                ok, n, sn = collect_league(client, logger, conn, lg, args.season, snapshot_date)
                results.append((lg, ok, n, sn))
                ok_n += 1 if ok else 0
            except Exception as e:  # 单联赛失败不阻断
                logger.warning(f"[{lg}] FAIL: {type(e).__name__} {e}")
                results.append((lg, False, 0, None))
            time.sleep(1.0)
    finally:
        try:
            client.close()
        except Exception:
            pass

    print("\n=== league_standings 快照 {} ===".format(snapshot_date))
    for lg, ok, n, sn in results:
        print(f"  {lg}: {'OK' if ok else 'FAIL'} ({n} 队, {sn})")
    # 永远 exit 0（非阻断步风格）
    sys.exit(0)


if __name__ == "__main__":
    main()
