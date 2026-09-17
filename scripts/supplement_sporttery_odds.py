# -*- coding: utf-8 -*-
"""
西甲赔率自动采集脚本（替代手动导入 TXT）
========================================
按日期+联赛从竞彩网自动拉取比赛与固定奖金(时序)赔率，写入 data/odds.db：
  - 队名经 TEAM_NAME_MAP / feature_utils.normalize_team_name() 归一化（对齐 sporttery_live_collector.py）
  - 写库使用 busy_timeout=5000 + INSERT OR IGNORE（配合各表 UNIQUE 约束），幂等可重复运行
  - 可回补最近 N 天，替代原先手动导入 TXT 的流程

复用（对齐 scripts/sporttery_live_collector.py）：
  - sporttery_live_collector：HEADERS / BASE_URL / http_get_json / fetch_match_bonus /
    parse_bonus_response / fetch_match_list（未开赛 Selling 比赛）
  - sporttery_collector：getUniformMatchResultV1.qry（按日期拉取已完赛比赛）/
    parse_match_list_response / build_odds_match_id / build_match_type / compute_actual_handicap
  - sporttery_sync_to_odds：TEAM_NAME_MAP（队名归一）/ get_season（赛季标签）
  - feature_utils.normalize_team_name：队名归一兜底（依赖缺失时自动回退）

鉴权说明：竞彩网两个接口实测无需 cookie（仅带常规请求头）；如需 cookie，
沿用 sporttery_live_collector.py 的方式，从环境变量 SPORTTERY_COOKIE 读取，无交互式登录。

使用：
  python scripts/supplement_sporttery_odds.py --league 西甲                 # 默认回补最近 1 天
  python scripts/supplement_sporttery_odds.py --league 英超 --date 2026-09-08
  python scripts/supplement_sporttery_odds.py --league 西甲 --days 3        # 回补最近 3 天
  python scripts/supplement_sporttery_odds.py --league 西甲 --dry-run       # 试跑：只打印不写库
"""

from __future__ import annotations

import argparse
import os
import sqlite3
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import requests

# 项目路径动态定位（禁止硬编码盘符）
SCRIPTS_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPTS_DIR.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"

sys.path.insert(0, str(SCRIPTS_DIR))

# 复用 sporttery_live_collector：请求头 / getMatchListV1(未开赛赛程) / getFixedBonusV1(固定奖金) / 解析
from sporttery_live_collector import (  # noqa: E402
    BASE_URL,
    HEADERS,
    http_get_json,
    fetch_match_bonus,
    parse_bonus_response,
    fetch_match_list as fetch_selling_matches,
)
# 复用 sporttery_collector：getUniformMatchResultV1(已完赛比赛列表) / 解析 / match_id / match_type
from sporttery_collector import (  # noqa: E402
    MATCH_LIST_API,
    parse_match_list_response,
    build_odds_match_id,
    build_match_type,
    compute_actual_handicap,
)
# 对齐 sporttery_live_collector 的 TEAM_NAME_MAP（竞彩网队名 → 项目标准中文名）
from sporttery_sync_to_odds import TEAM_NAME_MAP, get_season  # noqa: E402

# feature_utils.normalize_team_name 兜底（依赖 pandas/numpy/yaml，缺失时回退原样）
try:
    from feature_utils import normalize_team_name as _feature_normalize_team_name
except Exception:
    _feature_normalize_team_name = None

LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]

# 若环境变量 SPORTTERY_COOKIE 存在则附加到请求头（当前接口实测无需鉴权，仅为兼容预留）
HEADERS = dict(HEADERS)
if os.environ.get("SPORTTERY_COOKIE"):
    HEADERS["Cookie"] = os.environ["SPORTTERY_COOKIE"]


def normalize_team(name: str) -> str:
    """队名归一：先经 TEAM_NAME_MAP 归并别名（对齐 sporttery_live_collector），
    再经 feature_utils.normalize_team_name() 标准映射兜底"""
    name = (name or "").strip()
    name = TEAM_NAME_MAP.get(name, name)
    if _feature_normalize_team_name is not None:
        try:
            name = _feature_normalize_team_name(name)
        except Exception:
            pass
    return name


def fetch_finished_matches(target_date: str, league: str) -> list:
    """按日期拉取指定联赛已完赛比赛（getUniformMatchResultV1.qry，分页）"""
    matches = []
    page_no, total_pages = 1, 1
    while page_no <= total_pages:
        url = (f"{MATCH_LIST_API}?matchBeginDate={target_date}&matchEndDate={target_date}"
               f"&leagueId=&pageSize=100&pageNo={page_no}&isFix=0&matchPage=1&pcOrWap=1")
        try:
            data = http_get_json(url)
        except requests.RequestException as e:
            print(f"  ⚠️ 比赛列表请求失败: {e}")
            break
        if not data or data.get("errorCode") != "0":
            print(f"  ⚠️ 比赛列表接口失败: {(data or {}).get('errorMessage')}")
            break
        value = data.get("value") or {}
        total_pages = value.get("pages", 1)
        for m in parse_match_list_response(data):
            if m.get("league_name_abbr") == league:
                matches.append(m)
        page_no += 1
        time.sleep(0.3)
    return matches


def fetch_selling_for_date(target_date: str, league: str) -> list:
    """拉取指定日期未开赛(Selling)比赛（getMatchListV1.qry，复用 sporttery_live_collector）。
    注意：接口按销售日(businessDate)分组，需再按实际比赛日(match_date)过滤"""
    try:
        raw = fetch_selling_matches(target_date)
    except requests.RequestException as e:
        print(f"  ⚠️ 未开赛赛程请求失败: {e}")
        return []
    return [m for m in raw if m.get("league") == league and m.get("match_date") == target_date]


# 本地补充映射：feature_utils 缺失的西甲英文队名 → 中文标准名（避免反查漏匹配导致双套 match_id）
_LOCAL_NAME_FALLBACK = {
    "Malaga": "马拉加",
    "Deportivo Alavés": "阿拉维斯", "Alavés": "阿拉维斯",
    "Osasuna": "奥萨苏纳", "Getafe": "赫塔费",
    "Real Sociedad": "皇家社会", "Sevilla": "塞维利亚",
    "Rayo Vallecano": "巴列卡诺", "Valencia": "巴伦西亚",
}


def _norm_cn(name: str) -> str:
    """英文队名 → 中文标准名（feature_utils 优先，未识别时走本地补充映射兜底）"""
    if _feature_normalize_team_name is not None:
        try:
            out = _feature_normalize_team_name(name)
            if out != name:
                return out
        except Exception:
            pass
    return _LOCAL_NAME_FALLBACK.get(name, name)


def _find_existing_match_id(conn, match_date, home, away):
    """查 matches 表是否已存在同场比赛，存在则复用其 match_id（兼容中文/英文命名，
    避免同一场比赛双套 match_id 导致赔率挂错/重复——D4 验证 09-07 曾出现中英双份）。
    英文命名通过 feature_utils.normalize_team_name(英文)→中文 反查对比（本地补充映射兜底）。
    返回既有 match_id 或 None"""
    try:
        rows = conn.execute(
            "SELECT match_id, home_team, away_team FROM matches WHERE match_date=?",
            (match_date,)).fetchall()
    except Exception:
        return None
    for r in rows:
        if r[1] == home and r[2] == away:
            return r[0]  # 同命名体系直接命中
        if _norm_cn(r[1]) == home and _norm_cn(r[2]) == away:
            return r[0]  # 英文命名命中（反查归一为中文候选）
    return None


def save_to_odds_db(m: dict, league: str) -> dict:
    """写入 odds.db：busy_timeout=5000，INSERT OR IGNORE 幂等（matches 主表 + 四张时序表）。
    返回各表新增行数统计"""
    home = normalize_team(m.get("home_team", ""))
    away = normalize_team(m.get("away_team", ""))
    match_date = m.get("match_date", "")
    match_type = build_match_type(league, get_season(match_date))

    # actual_wdl: 主胜→胜, 客胜→负, 平局→平（与 sporttery_collector 一致）
    wdl_map = {"主胜": "胜", "客胜": "负", "平局": "平"}
    actual_wdl = wdl_map.get(m.get("actual_wdl", ""), "")
    handicap = float(m.get("handicap") or 0)
    actual_handicap = compute_actual_handicap(handicap, m.get("actual_score", ""))

    timing = m.get("timing") or {}
    stats = {"match": 0, "wdl": 0, "hcp": 0, "ttg": 0, "score": 0}

    conn = sqlite3.connect(str(DB_PATH), timeout=5)
    conn.execute("PRAGMA busy_timeout=5000")
    cur = conn.cursor()
    try:
        # 0. 复用既有 match_id（英文数据流占位/历史中文记录均兼容），避免双套命名
        existing_mid = _find_existing_match_id(conn, match_date, home, away)
        mid = existing_mid or build_odds_match_id(match_date, home, away)

        # 1. matches 主表（match_id UNIQUE，INSERT OR IGNORE 幂等）
        cur.execute("""
            INSERT OR IGNORE INTO matches
            (match_id, home_team, away_team, match_date, match_type,
             handicap, handicap_source, actual_wdl, actual_handicap, actual_score, actual_total_goals, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, 'sporttery', ?, ?, ?, ?, datetime('now'))
        """, (mid, home, away, match_date, match_type, handicap, actual_wdl,
              actual_handicap, m.get("actual_score", ""), m.get("actual_total_goals")))
        stats["match"] += cur.rowcount

        # 1b. 复用既有记录时回填赛果（INSERT OR IGNORE 不会更新已存在行）
        if existing_mid and actual_wdl:
            cur.execute("""
                UPDATE matches SET handicap=?, handicap_source='sporttery', actual_wdl=?, actual_handicap=?,
                       actual_score=?, actual_total_goals=?, updated_at=datetime('now')
                WHERE match_id=?
            """, (handicap, actual_wdl, actual_handicap,
                  m.get("actual_score", ""), m.get("actual_total_goals"), mid))

        # 2. wdl_history
        for w in timing.get("wdl_timing", []):
            cur.execute(
                "INSERT OR IGNORE INTO wdl_history (match_id, timestamp, win_a, draw, win_b) VALUES (?,?,?,?,?)",
                (mid, w["timestamp"], w["win_a"], w["draw"], w["win_b"]))
            stats["wdl"] += cur.rowcount

        # 3. handicap_history
        for h in timing.get("handicap_timing", []):
            cur.execute(
                "INSERT OR IGNORE INTO handicap_history (match_id, timestamp, hcp_win, hcp_draw, hcp_lose) "
                "VALUES (?,?,?,?,?)",
                (mid, h["timestamp"], h["win"], h["draw"], h["lose"]))
            stats["hcp"] += cur.rowcount

        # 4. total_goals_history
        for g in timing.get("total_goals_timing", []):
            cur.execute(
                "INSERT OR IGNORE INTO total_goals_history "
                "(match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (mid, g["timestamp"], g["goals_0"], g["goals_1"], g["goals_2"], g["goals_3"],
                 g["goals_4"], g["goals_5"], g["goals_6"], g["goals_7_plus"]))
            stats["ttg"] += cur.rowcount

        # 5. score_history
        for sc in timing.get("score_timing", []):
            cur.execute(
                "INSERT OR IGNORE INTO score_history (match_id, timestamp, score, odds) VALUES (?,?,?,?)",
                (mid, sc["timestamp"], sc["score"], sc["odds"]))
            stats["score"] += cur.rowcount

        conn.commit()
    except Exception as e:
        conn.rollback()
        print(f"  ❌ 写库异常: {e}")
    finally:
        conn.close()
    return stats


def process_match(m: dict, league: str, dry_run: bool, index: int, total: int) -> dict | None:
    """拉取并解析单场固定奖金赔率；dry_run 只打印将写入的比赛与赔率，否则写库。
    返回新增行数统计，失败返回 None"""
    print(f"  [{index}/{total}] {m.get('match_date')} {m.get('home_team')} vs {m.get('away_team')} "
          f"({league} {m.get('match_time', '')})", end=" ")
    try:
        bonus = fetch_match_bonus(m["sporttery_match_id"])
    except requests.RequestException as e:
        print(f"❌ 赔率接口请求失败: {e}")
        return None
    if not bonus or bonus.get("errorCode") != "0":
        print("❌ 赔率接口返回错误")
        return None

    timing = parse_bonus_response(bonus)
    m["timing"] = timing
    n = {"wdl": len(timing["wdl_timing"]), "hcp": len(timing["handicap_timing"]),
         "ttg": len(timing["total_goals_timing"]), "score": len(timing["score_timing"])}

    if dry_run:
        print(f"✅ [dry] WDL:{n['wdl']} HCP:{n['hcp']} TTG:{n['ttg']} CRS:{n['score']}")
        if timing["wdl_timing"]:
            w0 = timing["wdl_timing"][0]
            print(f"      WDL样本: 胜{w0['win_a']} 平{w0['draw']} 负{w0['win_b']} @ {w0['timestamp']}")
        return {"match": 0, "wdl": 0, "hcp": 0, "ttg": 0, "score": 0}

    stats = save_to_odds_db(m, league)
    print(f"✅ 新增 比赛:{stats['match']} WDL:{stats['wdl']} HCP:{stats['hcp']} "
          f"TTG:{stats['ttg']} CRS:{stats['score']}")
    return stats


def run(date: str, league: str, dry_run: bool) -> None:
    """采集指定日期+联赛的比赛与赔率"""
    print(f"{'='*68}")
    print(f"竞彩网赔率自动采集 | 日期={date} | 联赛={league} | 模式={'试跑(不写库)' if dry_run else '写入'}")
    print(f"{'='*68}")

    # 已完赛 + 未开赛两类比赛合并（按 sporttery_match_id 去重，完赛优先）
    finished = fetch_finished_matches(date, league)
    selling = fetch_selling_for_date(date, league)
    merged = {m["sporttery_match_id"]: m for m in finished}
    for m in selling:
        merged.setdefault(m["sporttery_match_id"], m)
    matches = list(merged.values())

    print(f"📋 {date} {league} 比赛: 已完赛 {len(finished)} + 未开赛 {len(selling)} = {len(matches)} 场")
    if not matches:
        print("  无待采集比赛")
        return

    total = {"match": 0, "wdl": 0, "hcp": 0, "ttg": 0, "score": 0}
    for i, m in enumerate(matches, 1):
        try:
            stats = process_match(m, league, dry_run, i, len(matches))
        except Exception as e:
            print(f"❌ 单场处理异常: {e}")
            continue
        if stats:
            for k in total:
                total[k] += stats[k]
        time.sleep(0.5)

    print(f"{'='*68}")
    if dry_run:
        print("🔍 试跑完成（未写库）")
    else:
        print(f"🎉 完成: 新增 比赛 {total['match']} / WDL {total['wdl']} / HCP {total['hcp']} "
              f"/ TTG {total['ttg']} / CRS {total['score']} 行")
        print(f"💾 写入: {DB_PATH}")
    print(f"{'='*68}")


def main() -> None:
    parser = argparse.ArgumentParser(description="竞彩网五大联赛赔率自动采集（替代手动导入 TXT）")
    parser.add_argument("--league", type=str, default="西甲", choices=LEAGUES,
                        help="联赛（默认 西甲）")
    parser.add_argument("--date", type=str, default=None,
                        help="采集指定日期 YYYY-MM-DD（默认从今天起回补 --days 天）")
    parser.add_argument("--days", type=int, default=1,
                        help="回补最近 N 天（默认 1；指定 --date 时忽略）")
    parser.add_argument("--dry-run", action="store_true",
                        help="试跑：只打印将写入的比赛与赔率，不写库")
    args = parser.parse_args()

    if args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            print(f"⚠️ 日期格式错误: {args.date}（应为 YYYY-MM-DD）")
            sys.exit(1)
        dates = [args.date]
    else:
        n = max(1, args.days)
        today = datetime.now()
        dates = [(today - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(n - 1, -1, -1)]

    print(f"📅 待采集日期: {dates} | 联赛: {args.league}")
    for d in dates:
        run(d, args.league, args.dry_run)


if __name__ == "__main__":
    main()
