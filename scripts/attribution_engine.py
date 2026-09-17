# -*- coding: utf-8 -*-
"""
attribution_engine.py — 模块 A3：赛后归因引擎（8 维度纯规则，P0）

===============================================
背景（模型改进实施方案 v1.0 §三/A3）：
  每场已完赛预测比赛输出带权重归因列表（权重和=100%），定位预测偏差根因。
  纯规则引擎实现（不用 LLM 判断，LLM 仅允许文字润色）。
  ⚠️ 平局归因方向修正：模型平局概率系统性高于市场（draw z=-2.86 显著负，
  7,291 笔平局价值投注 -4.47% 全线亏损）——维度 8 平局判断必须是
  「平局概率高于市场隐含且实际非平局 → 平局过度自信」，而非「平局召回不足」。

8 维度与数据来源：
  1 数据缺失归因   : sofascore_team_features 关键特征缺失率 >20% + understat xG 缺失
  2 赔率异动归因   : 竞彩 wdl_history 开盘→末条任一方向漂移 >5%（经 team_mapping 中英映射）
  3 阵容异动归因   : match_lineups 基准首发 vs SofaScore 实际首发差异 >3 人
  4 运气偏差归因   : understat xG 与实际进球异常（xG>1.5 且进≤1 / xG<0.5 且进≥2）
  5 异常事件归因   : SofaScore incidents 有红牌/点球/乌龙
  6 战意归因       : 弱队爆冷胜 + 胜方跑动距离明显更高（SofaScore stats）
  7 模型局限性归因 : 1-6 全不命中且该类比赛历史偏差率高（league 命中率 <0.5）
  8 校准偏移归因   : 单场 argmax 概率 vs 该概率档历史命中率显著高估；
                    平局过度自信（模型 draw > 市场隐含且实际非平局）

输出 JSON（attribution_json 字段，对齐方案 §A3）：
  {"attributions":[{"type","weight","description","evidence"}],
   "primary_cause","confidence_level"}

用法：
  python scripts/attribution_engine.py --match-id <ID>                # 只输出 JSON
  python scripts/attribution_engine.py --match-id <ID> --collect      # 自动采集赛后明细（阵容/事件/统计）
  python scripts/attribution_engine.py --match-id <ID> --collect --write  # 写回 post_match_review
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"
TIMING_DB = PROJECT_DIR / "data" / "odds_timing.db"

sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from team_name_mapping import normalize_team_name  # noqa: E402

# ==================== 阈值与权重（对齐方案 §A3） ====================
MISSING_FEATURE_THRESHOLD = 0.20   # 维度1：关键特征缺失率
ODDS_DRIFT_THRESHOLD = 0.05        # 维度2：竞彩赔率漂移
LINEUP_DIFF_THRESHOLD = 3          # 维度3：首发差异人数
XG_HIGH_LOW_GOALS = 1              # 维度4：xG>1.5 且进球≤1
XG_HIGH_THRESHOLD = 1.5
XG_LOW_GOALS = 2                   # 维度4：xG<0.5 且进球≥2
XG_LOW_THRESHOLD = 0.5
DISTANCE_GAP_KM = 3.0              # 维度6：跑动差距
LEAGUE_HIT_RATE_THRESHOLD = 0.50   # 维度7：league 命中率阈值
CALIB_GAP_THRESHOLD = 0.15         # 维度8：概率档命中率 vs 单场概率差距

# 维度基础权重（命中后归一化到 100%）
BASE_WEIGHTS = {
    "数据缺失归因": 25,
    "赔率异动归因": 20,
    "阵容异动归因": 20,
    "运气偏差归因": 15,
    "异常事件归因": 15,
    "战意归因": 10,
    "模型局限性归因": 10,
    "校准偏移归因": 10,
}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _row_to_dict(cur: sqlite3.Cursor, row: tuple) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    return dict(zip([d[0] for d in cur.description], row))


# ==================== 数据获取（维度数据源） ====================
def get_match_basics(conn: sqlite3.Connection, match_id: str) -> Optional[Dict[str, Any]]:
    """比赛基础信息：matches（英文队名/handicap）为主，post_match_review（A2 权威赛果）补强。

    - home_team/away_team 保留英文（understat/阵容比对/竞彩匹配需要英文）
    - home_team_cn/away_team_cn 来自 review 中文名（复盘展示用）
    - actual_score/actual_wdl/league 优先 review（matches 表 09-06 场次常缺赛果）
    - handicap 仅 matches 有
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT match_id, match_date, league, home_team, away_team, actual_score, actual_wdl, handicap "
        "FROM matches WHERE match_id=?", (match_id,),
    )
    m = _row_to_dict(cur, cur.fetchone())
    if m is None:
        return None
    cur.execute(
        "SELECT league, match_date, home_team, away_team, actual_score, actual_wdl "
        "FROM post_match_review WHERE match_id=?", (match_id,),
    )
    r = _row_to_dict(cur, cur.fetchone())
    out = dict(m)
    if r:
        out["home_team_cn"] = r.get("home_team")
        out["away_team_cn"] = r.get("away_team")
        for k in ("actual_score", "actual_wdl", "league"):
            if r.get(k):
                out[k] = r[k]
    return out


def get_model_wdl(conn: sqlite3.Connection, match_id: str) -> Optional[Dict[str, float]]:
    """model_predictions 该场最新 model_name 的 WDL 三向概率。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT model_name FROM model_predictions WHERE match_id=? "
        "AND prediction_type='WDL_home' ORDER BY id DESC LIMIT 1", (match_id,),
    )
    row = cur.fetchone()
    if row is None:
        return None
    model_name = row[0]
    probs: Dict[str, float] = {}
    cur.execute(
        "SELECT prediction_type, probability FROM model_predictions "
        "WHERE match_id=? AND model_name=? AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')",
        (match_id, model_name),
    )
    for pt, p in cur.fetchall():
        probs[pt] = float(p)
    if len(probs) != 3:
        return None
    return probs


def get_event_id(conn: sqlite3.Connection, match_id: str) -> Optional[str]:
    """fbref_match_mapping: odds_match_id -> SofaScore event_id。"""
    cur = conn.cursor()
    cur.execute("SELECT fbref_match_id FROM fbref_match_mapping WHERE odds_match_id=?", (match_id,))
    row = cur.fetchone()
    return str(row[0]) if row and row[0] else None


def get_understat_xg(conn: sqlite3.Connection, basics: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """维度4：understat xG（league + datetime + 英文队名匹配）。"""
    if not basics:
        return None
    cur = conn.cursor()
    league = basics.get("league")
    home = basics.get("home_team")
    away = basics.get("away_team")
    date = basics.get("match_date")
    date_like = f"{date}%"

    # 策略1：精确队名 + league
    cur.execute(
        "SELECT home_xg, away_xg, home_goals, away_goals FROM understat_match_team_stats "
        "WHERE league=? AND home_team=? AND away_team=? AND datetime LIKE ? LIMIT 1",
        (league, home, away, date_like),
    )
    row = cur.fetchone()
    if row is None:
        # 策略2：精确队名，忽略 league（值域差异 fallback）
        cur.execute(
            "SELECT home_xg, away_xg, home_goals, away_goals FROM understat_match_team_stats "
            "WHERE home_team=? AND away_team=? AND datetime LIKE ? LIMIT 1",
            (home, away, date_like),
        )
        row = cur.fetchone()
    if row is None:
        # 策略3：归一化队名匹配（处理 FC Augsburg vs Augsburg、
        # Bayer 04 Leverkusen vs Bayer Leverkusen 等英英变体）
        home_norm = _norm_en_team(home)
        away_norm = _norm_en_team(away)
        if home_norm and away_norm:
            cur.execute(
                "SELECT home_team, away_team, home_xg, away_xg, home_goals, away_goals "
                "FROM understat_match_team_stats WHERE datetime LIKE ?",
                (date_like,),
            )
            for r in cur.fetchall():
                us_home_norm = _norm_en_team(r[0])
                us_away_norm = _norm_en_team(r[1])
                if (home_norm == us_home_norm and away_norm == us_away_norm):
                    row = (r[2], r[3], r[4], r[5])
                    break
                # 主客互换兜底
                if (home_norm == us_away_norm and away_norm == us_home_norm):
                    row = (r[3], r[2], r[5], r[4])  # 主客互换
                    break
    if row is None:
        return None
    return {"home_xg": float(row[0]), "away_xg": float(row[1]),
            "home_goals": int(row[2]), "away_goals": int(row[3])}


def _norm_en_team(name: Optional[str]) -> str:
    """英文队名归一化：去 FC/04/前缀后缀、统一大小写和空格，用于模糊比对。"""
    if not name:
        return ""
    n = name.strip().lower()
    # 去常见前缀
    for prefix in ("fc ", "sc ", "tsg ", "1. ", "1. fc ", "rc ", "as ", "ss ", "us "):
        if n.startswith(prefix):
            n = n[len(prefix):]
            break
    # 去常见后缀
    for suffix in (" fc", " cf", " bc", " ac", " united", " city", " town", " athletic"):
        if n.endswith(suffix):
            n = n[:-len(suffix)]
            break
    # 去 "04"（Bayer 04 Leverkusen → bayer leverkusen）
    n = n.replace(" 04 ", " ")
    # 统一空格和标点
    n = n.replace("'", "").replace(".", " ").replace("-", " ")
    n = " ".join(n.split())
    return n.strip()


def _cn_team(conn: sqlite3.Connection, en_name: str) -> str:
    """英文队名 -> 中文：team_mapping 优先，fallback normalize_team_name，再 fallback 原值。"""
    cur = conn.cursor()
    cur.execute("SELECT standard_name FROM team_mapping WHERE original_name=?", (en_name,))
    row = cur.fetchone()
    if row:
        return row[0]
    norm = normalize_team_name(en_name)
    return norm if norm else en_name


def _find_cricket_match_id(conn: sqlite3.Connection, match_id: str,
                           home_en: str, away_en: str) -> Optional[str]:
    """竞彩 wdl_history match_id（中文格式）匹配。

    候选1：构造 {date}_{home_cn}_{away_cn}；候选2：遍历当日 DISTINCT match_id 双向归一化比对。
    """
    date = match_id[:10]
    home_cn = _cn_team(conn, home_en)
    away_cn = _cn_team(conn, away_en)
    cand = f"{date}_{home_cn}_{away_cn}"
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM wdl_history WHERE match_id=? LIMIT 1", (cand,))
    if cur.fetchone():
        return cand
    cur.execute(
        "SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE ?", (f"{date}%",),
    )
    h_norm, a_norm = normalize_team_name(home_cn), normalize_team_name(away_cn)
    for mid in cur.fetchall():
        parts = mid[0].split("_")
        if len(parts) != 3 or parts[0] != date:
            continue
        mh, ma = parts[1], parts[2]
        if (normalize_team_name(mh) == h_norm and normalize_team_name(ma) == a_norm) or \
           (normalize_team_name(mh) == a_norm and normalize_team_name(ma) == h_norm):
            return mid[0]
    return None


def get_odds_timeline(conn: sqlite3.Connection, match_id: str,
                      basics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """维度2/8：竞彩 wdl_history 开盘→末条。返回 {cricket_id, open, close, drift_max}。"""
    if not basics:
        return None
    cid = _find_cricket_match_id(conn, match_id, basics.get("home_team", ""), basics.get("away_team", ""))
    if cid is None:
        return None
    cur = conn.cursor()
    cur.execute(
        "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp", (cid,),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    open_o, close_o = rows[0], rows[-1]
    drift_max = 0.0
    drift_dir = None
    for i, label in enumerate(("win_a", "draw", "win_b")):
        if open_o[i] and open_o[i] > 0:
            d = abs(close_o[i] - open_o[i]) / open_o[i]
            if d > drift_max:
                drift_max = d
                drift_dir = label
    return {"cricket_id": cid, "open": list(open_o), "close": list(close_o),
            "drift_max": round(drift_max, 4), "drift_dir": drift_dir,
            "snapshots": len(rows)}


def get_market_draw_prob(odds_hist: Optional[Dict[str, Any]]) -> Optional[float]:
    """维度8：竞彩末条去抽水 draw 隐含概率（1/draw / sum(1/o)）。"""
    if not odds_hist:
        return None
    close = odds_hist["close"]
    try:
        inv = [1.0 / float(o) for o in close if o and float(o) > 0]
    except (ValueError, TypeError):
        return None
    if not inv or sum(inv) <= 0:
        return None
    return inv[1] / sum(inv)  # [win_a, draw, win_b]


def get_bucket_hit_rates(conn: sqlite3.Connection) -> Dict[str, float]:
    """维度8：全库 WDL 概率分桶历史命中率（model_predictions + matches.actual_wdl）。

    分桶：40-50 / 50-60 / 60-70 / >=70；命中按 pred_wdl 与 actual_wdl 比对。
    返回 {"WDL_home_0.60": 0.72, ...}，样本 <20 的桶不输出。
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT mp.prediction_type, mp.probability,
               CASE mp.prediction_type
                    WHEN 'WDL_home' THEN '主胜'
                    WHEN 'WDL_draw' THEN '平局'
                    WHEN 'WDL_away' THEN '客胜' END AS pred_wdl,
               m.actual_wdl
        FROM model_predictions mp
        JOIN matches m ON mp.match_id = m.match_id
        WHERE mp.prediction_type IN ('WDL_home','WDL_draw','WDL_away')
          AND m.actual_wdl IS NOT NULL AND m.actual_wdl != ''
        """
    )
    buckets: Dict[str, Dict[str, int]] = {}
    for pt, prob, pred_wdl, actual_wdl in cur.fetchall():
        try:
            p = float(prob)
        except (ValueError, TypeError):
            continue
        # matches.actual_wdl 值域：'主胜/平局/客胜'/'胜/平/负' 混合——归一
        hit = _is_wdl_hit(pred_wdl, actual_wdl)
        if hit is None:
            continue
        bucket = f"{pt}_{_bucket_key(p)}"
        b = buckets.setdefault(bucket, {"n": 0, "hit": 0})
        b["n"] += 1
        b["hit"] += 1 if hit else 0
    rates: Dict[str, float] = {}
    for key, b in buckets.items():
        if b["n"] >= 20:  # 最小样本
            rates[key] = b["hit"] / b["n"]
    return rates


def _bucket_key(p: float) -> str:
    if p >= 0.70:
        return "0.70"
    if p >= 0.60:
        return "0.60"
    if p >= 0.50:
        return "0.50"
    if p >= 0.40:
        return "0.40"
    return "0.40"


def _is_wdl_hit(pred_wdl: str, actual_wdl: str) -> Optional[bool]:
    """pred_wdl ∈ {主胜/平局/客胜}，actual_wdl ∈ {主胜/平局/客胜/胜/平/负}。"""
    if not actual_wdl:
        return None
    norm_actual = {"主胜": "主胜", "平局": "平局", "客胜": "客胜",
                   "胜": "主胜", "平": "平局", "负": "客胜"}.get(actual_wdl)
    if norm_actual is None:
        return None
    return pred_wdl == norm_actual


def get_league_hit_rate(conn: sqlite3.Connection, league: Optional[str]) -> Optional[float]:
    """维度7：该 league 模型 WDL 历史命中率（全库，>=50 样本才有意义）。"""
    if not league:
        return None
    cur = conn.cursor()
    cur.execute(
        """
        SELECT COUNT(*), SUM(CASE WHEN m.actual_wdl IN ('主胜','胜') AND mp.prediction_type='WDL_home'
                                  OR m.actual_wdl IN ('平局','平') AND mp.prediction_type='WDL_draw'
                                  OR m.actual_wdl IN ('客胜','负') AND mp.prediction_type='WDL_away'
                             THEN 1 ELSE 0 END)
        FROM model_predictions mp
        JOIN matches m ON mp.match_id = m.match_id
        WHERE m.league=? AND mp.prediction_type='WDL_home'
          AND m.actual_wdl IS NOT NULL AND m.actual_wdl != ''
        """,
        (league,),
    )
    row = cur.fetchone()
    if not row or row[0] < 50:
        return None
    return row[1] / row[0]


def get_lineup_benchmark(conn: sqlite3.Connection, event_id: Optional[str],
                         home_en: str, away_en: str) -> Optional[Dict[str, List[str]]]:
    """match_lineups 首发基准（fbref_match_id + is_starter=1）。

    match_lineups.team 为英文队名（如 'Arsenal'），按 home_en/away_en 归边。
    返回 {home:[姓], away:[姓]}；无该场首发数据返回 None。
    """
    if not event_id:
        return None
    cur = conn.cursor()
    cur.execute(
        "SELECT team, player_name FROM match_lineups "
        "WHERE fbref_match_id=? AND is_starter=1", (event_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return None
    h_key, a_key = _surname(home_en), _surname(away_en)
    bench: Dict[str, List[str]] = {"home": [], "away": []}
    for team, pname in rows:
        if not pname:
            continue
        t_key = _surname(str(team))
        side = "home" if t_key == h_key else ("away" if t_key == a_key else None)
        if side:
            bench[side].append(_surname(pname))
    return bench


def _surname(name: str) -> str:
    """取姓（最后一个词，小写去标点），用于阵容比对容错。"""
    import re
    parts = re.split(r"[^a-zA-Z\u4e00-\u9fff]+", name.strip().lower())
    return parts[-1] if parts else name.strip().lower()


def get_lineup_diff(conn: sqlite3.Connection, event_id: Optional[str],
                    post_data: Optional[Dict[str, Any]],
                    basics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """维度3：基准首发 vs SofaScore 实际首发差异。返回 {lineup_diff, missing_players} 或 None（数据不足）。"""
    bench = get_lineup_benchmark(conn, event_id,
                                 basics.get("home_team", ""), basics.get("away_team", ""))
    if not bench or not post_data:
        return None
    lineups = post_data.get("lineups") or {}
    if not lineups.get("confirmed"):
        return None
    actual: Dict[str, set] = {}
    for side in ("home", "away"):
        sd = lineups.get(side) or {}
        actual[side] = {
            _surname(p.get("player_name") or p.get("player_short_name") or "")
            for p in sd.get("players", []) if p.get("is_starter")
        }
    total_diff = 0
    missing: List[str] = []
    for side in ("home", "away"):
        b_set, a_set = set(bench.get(side, [])), actual.get(side, set())
        if not a_set or not b_set:
            return None
        diff = len(b_set - a_set) + len(a_set - b_set)
        total_diff += diff
        missing += list(b_set - a_set)
    if total_diff == 0:
        return None
    return {"lineup_diff": total_diff, "missing_players": missing[:6]}


def analyze_incidents(post_data: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """维度5：SofaScore incidents 异常事件（红牌/点球/乌龙）。"""
    if not post_data:
        return None
    inc = post_data.get("incidents") or {}
    result: Dict[str, List[str]] = {"red_cards": [], "penalties": [], "own_goals": []}
    for card in inc.get("cards", []):
        card_type = str(card.get("card_type", card.get("type", ""))).lower()
        if "red" in card_type or card_type == "红牌":
            result["red_cards"].append(card.get("player", ""))
    for g in inc.get("goals", []):
        desc = str(g.get("description", g.get("detail", g.get("type", "")))).lower()
        if "penalty" in desc or "点球" in desc:
            result["penalties"].append(g.get("player", ""))
        if "own" in desc or "乌龙" in desc:
            result["own_goals"].append(g.get("player", ""))
    has_event = any(v for v in result.values())
    return result if has_event else None


def analyze_motivation(post_data: Optional[Dict[str, Any]],
                       wdl_probs: Optional[Dict[str, float]]) -> Optional[Dict[str, Any]]:
    """维度6：弱队爆冷胜 + 胜方跑动明显更高（kilometersCovered）。"""
    if not post_data or not wdl_probs:
        return None
    stats = post_data.get("stats") or {}
    dh = stats.get("home", {}).get("kilometersCovered")
    da = stats.get("away", {}).get("kilometersCovered")
    if dh is None or da is None:
        return None
    home_win = wdl_probs.get("WDL_home", 0.0)
    away_win = wdl_probs.get("WDL_away", 0.0)
    gap = abs(float(dh) - float(da))
    evidence = {"kilometersCovered_home": dh, "kilometersCovered_away": da, "gap_km": round(gap, 2)}
    # 客队不被看好（away 概率 < home 概率）但客队赢且客队跑动更高 → 客队战意强/主队懈怠
    if away_win < home_win and float(da) > float(dh) + DISTANCE_GAP_KM:
        return {"type": "客队更强投入", **evidence}
    if home_win < away_win and float(dh) > float(da) + DISTANCE_GAP_KM:
        return {"type": "主队更强投入", **evidence}
    return None


# ==================== 归因引擎 ====================
class AttributionEngine:
    """8 维度纯规则归因引擎。

    用法：
      engine = AttributionEngine()
      result = engine.run(match_id, post_data=post_data)
    返回 attribution dict（attributions 权重和=100%）。
    """

    def __init__(self, conn_odds: Optional[sqlite3.Connection] = None,
                 conn_timing: Optional[sqlite3.Connection] = None) -> None:
        self.conn = conn_odds if conn_odds is not None else _get_conn()
        self._owns_conn = conn_odds is None
        self.conn_timing = conn_timing

    def close(self) -> None:
        if self._owns_conn:
            self.conn.close()

    def run(self, match_id: str,
            post_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        basics = get_match_basics(self.conn, match_id)
        if not basics:
            raise ValueError(f"matches 表无 {match_id} 记录")

        wdl_probs = get_model_wdl(self.conn, match_id)
        event_id = get_event_id(self.conn, match_id)
        attrs: List[Dict[str, Any]] = []

        # ---- 维度1 数据缺失归因 ----
        missing_rate = None
        if event_id:
            missing_rate = self._feature_missing_rate(event_id)
            if missing_rate is not None and missing_rate > MISSING_FEATURE_THRESHOLD:
                attrs.append({
                    "type": "数据缺失归因", "weight": BASE_WEIGHTS["数据缺失归因"],
                    "description": f"赛前关键特征缺失率 {missing_rate:.0%}（>20%），模型输入不完整",
                    "evidence": {"feature_missing_rate": round(missing_rate, 4), "event_id": event_id},
                })

        # ---- 维度2 赔率异动归因 ----
        odds_hist = get_odds_timeline(self.conn, match_id, basics)
        if odds_hist and odds_hist["drift_max"] > ODDS_DRIFT_THRESHOLD:
            attrs.append({
                "type": "赔率异动归因", "weight": BASE_WEIGHTS["赔率异动归因"],
                "description": f"竞彩赔率赛前漂移最大 {odds_hist['drift_max']:.1%}（{odds_hist['drift_dir']} 方向），"
                               f"市场临场信息未反映在模型特征中",
                "evidence": {"cricket_match_id": odds_hist["cricket_id"],
                             "open": odds_hist["open"], "close": odds_hist["close"],
                             "drift_max": odds_hist["drift_max"], "snapshots": odds_hist["snapshots"]},
            })

        # ---- 维度3 阵容异动归因 ----
        lineup = get_lineup_diff(self.conn, event_id, post_data, basics)
        if lineup and lineup["lineup_diff"] > LINEUP_DIFF_THRESHOLD:
            attrs.append({
                "type": "阵容异动归因", "weight": BASE_WEIGHTS["阵容异动归因"],
                "description": f"实际首发与基准首发差异 {lineup['lineup_diff']} 人（>3），阵容变化未被模型捕捉",
                "evidence": {"lineup_diff": lineup["lineup_diff"],
                             "missing_players": lineup["missing_players"]},
            })

        # ---- 维度4 运气偏差归因 ----
        xg = get_understat_xg(self.conn, basics)
        if xg:
            luck_ev = []
            if xg["home_xg"] > XG_HIGH_THRESHOLD and xg["home_goals"] <= XG_HIGH_LOW_GOALS:
                luck_ev.append(f"主队 xG={xg['home_xg']:.2f} 仅进 {xg['home_goals']} 球")
            if xg["away_xg"] > XG_HIGH_THRESHOLD and xg["away_goals"] <= XG_HIGH_LOW_GOALS:
                luck_ev.append(f"客队 xG={xg['away_xg']:.2f} 仅进 {xg['away_goals']} 球")
            if xg["home_xg"] < XG_LOW_THRESHOLD and xg["home_goals"] >= XG_LOW_GOALS:
                luck_ev.append(f"主队 xG={xg['home_xg']:.2f} 却进 {xg['home_goals']} 球（超预期）")
            if xg["away_xg"] < XG_LOW_THRESHOLD and xg["away_goals"] >= XG_LOW_GOALS:
                luck_ev.append(f"客队 xG={xg['away_xg']:.2f} 却进 {xg['away_goals']} 球（超预期）")
            if luck_ev:
                attrs.append({
                    "type": "运气偏差归因", "weight": BASE_WEIGHTS["运气偏差归因"],
                    "description": "；".join(luck_ev) + "，实际比分与创造机会质量不匹配",
                    "evidence": {"home_xg": xg["home_xg"], "away_xg": xg["away_xg"],
                                 "home_goals": xg["home_goals"], "away_goals": xg["away_goals"]},
                })

        # ---- 维度5 异常事件归因 ----
        incidents = analyze_incidents(post_data)
        if incidents:
            ev_summary = []
            if incidents["red_cards"]:
                ev_summary.append(f"红牌 {len(incidents['red_cards'])} 张")
            if incidents["penalties"]:
                ev_summary.append(f"点球 {len(incidents['penalties'])} 个")
            if incidents["own_goals"]:
                ev_summary.append(f"乌龙 {len(incidents['own_goals'])} 个")
            attrs.append({
                "type": "异常事件归因", "weight": BASE_WEIGHTS["异常事件归因"],
                "description": "比赛含" + "、".join(ev_summary) + "，比分受非常规事件影响",
                "evidence": {"red_cards": incidents["red_cards"],
                             "penalties": incidents["penalties"],
                             "own_goals": incidents["own_goals"]},
            })

        # ---- 维度6 战意归因 ----
        motivation = analyze_motivation(post_data, wdl_probs)
        if motivation:
            attrs.append({
                "type": "战意归因", "weight": BASE_WEIGHTS["战意归因"],
                "description": f"{motivation['type']}：胜方跑动距离明显更高（差距 {motivation['gap_km']:.1f}km），"
                               f"投入度差异未反映在模型特征中",
                "evidence": {"kilometersCovered_home": motivation["kilometersCovered_home"],
                             "kilometersCovered_away": motivation["kilometersCovered_away"]},
            })

        # ---- 维度8 校准偏移归因 ----
        calib = self._calibration_analysis(wdl_probs, odds_hist)
        if calib:
            attrs.append({
                "type": "校准偏移归因", "weight": BASE_WEIGHTS["校准偏移归因"],
                "description": calib["description"],
                "evidence": calib["evidence"],
            })

        # ---- 维度7 模型局限性归因（兜底） ----
        if not attrs:
            league_hit = get_league_hit_rate(self.conn, basics.get("league"))
            actual_wdl = basics.get("actual_wdl")
            # 预测 argmax 方向 vs 实际方向
            direction_miss = False
            if wdl_probs and actual_wdl:
                argmax_pt = max(wdl_probs, key=wdl_probs.get)
                argmax_wdl = {"WDL_home": "主胜", "WDL_draw": "平局", "WDL_away": "客胜"}.get(argmax_pt)
                norm_actual = {"主胜": "主胜", "平局": "平局", "客胜": "客胜",
                               "胜": "主胜", "平": "平局", "负": "客胜"}.get(actual_wdl)
                direction_miss = bool(argmax_wdl and norm_actual and argmax_wdl != norm_actual)
            big_margin = False
            score = basics.get("actual_score")
            if score and ":" in str(score):
                try:
                    hs, as_ = str(score).split(":")
                    big_margin = abs(int(hs) - int(as_)) >= 3
                except ValueError:
                    pass
            if league_hit is not None and league_hit < LEAGUE_HIT_RATE_THRESHOLD:
                attrs.append({
                    "type": "模型局限性归因", "weight": BASE_WEIGHTS["模型局限性归因"],
                    "description": f"无显著单场归因命中，但 {basics.get('league')} 联赛模型历史命中率 "
                                   f"{league_hit:.1%}（<50%），属模型系统性薄弱联赛",
                    "evidence": {"league": basics.get("league"), "league_hit_rate": round(league_hit, 4)},
                })
            elif direction_miss and big_margin:
                attrs.append({
                    "type": "模型局限性归因", "weight": BASE_WEIGHTS["模型局限性归因"],
                    "description": f"模型预测方向与实际结果相反且比分差≥3 球（{score}），"
                                   f"该类大比分偏差反映模型对强弱悬殊比赛把握不足",
                    "evidence": {"direction_miss": True, "actual_score": score,
                                 "league_hit_rate": league_hit,
                                 "note": "insufficient-history" if league_hit is None else None},
                })
            elif league_hit is None:
                attrs.append({
                    "type": "模型局限性归因", "weight": 100,
                    "description": "无显著归因维度命中；当前历史样本不足（league 命中率无法计算），"
                                   "偏差待数据积累后再判定是否系统性",
                    "evidence": {"note": "insufficient-history", "league_hit_rate": None},
                })
            else:
                attrs.append({
                    "type": "模型局限性归因", "weight": 100,
                    "description": "无显著归因维度命中，模型在常规比赛区间表现正常，偏差属随机波动",
                    "evidence": {"note": "no-attribution-hit", "league_hit_rate": league_hit},
                })

        # ---- 权重归一化到 100% ----
        total = sum(a["weight"] for a in attrs)
        for a in attrs:
            a["weight"] = round(a["weight"] * 100.0 / total, 1)

        primary = max(attrs, key=lambda a: a["weight"])
        confidence = self._confidence_level(attrs)

        return {
            "match_id": match_id,
            "attributions": attrs,
            "primary_cause": primary["type"],
            "confidence_level": confidence,
        }

    # ---- 内部辅助 ----
    def _feature_missing_rate(self, event_id: str) -> Optional[float]:
        """sofascore_team_features 关键特征缺失率。"""
        cur = self.conn.cursor()
        cur.execute("SELECT * FROM sofascore_team_features WHERE event_id=?", (event_id,))
        cols = [d[0] for d in cur.description] if cur.description else []
        row = cur.fetchone()
        if row is None:
            return None
        KEY_COLS = [
            "sofa_rat_5g_home", "sofa_xg_5g_home", "sofa_pass_sr_5g_home",
            "sofa_tackle_5g_home", "sofa_duel_sr_5g_home",
            "pa_xi_rating_home", "pa_availability_home", "pa_fatigue_7d_home",
            "sofa_rat_5g_away", "sofa_xg_5g_away", "pa_xi_rating_away",
        ]
        present = [c for c in KEY_COLS if c in cols]
        if not present:
            return None
        missing = 0
        idx = {c: i for i, c in enumerate(cols)}
        for c in present:
            v = row[idx[c]]
            if v is None or (isinstance(v, float) and v != v) or v == "":
                missing += 1
        return missing / len(present)

    def _calibration_analysis(self, wdl_probs: Optional[Dict[str, float]],
                              odds_hist: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        """维度8：①argmax 概率档历史命中率 vs 单场概率（高估检测）；
        ②平局过度自信（模型 draw > 市场隐含 draw 且实际非平局）。"""
        if not wdl_probs:
            return None
        hit_rates = get_bucket_hit_rates(self.conn)
        evidences: Dict[str, Any] = {}
        descs: List[str] = []

        # ① 概率档高估
        argmax_pt = max(wdl_probs, key=wdl_probs.get)
        p_arg = wdl_probs[argmax_pt]
        bkey = f"{argmax_pt}_{_bucket_key(p_arg)}"
        if bkey in hit_rates:
            hit_rate = hit_rates[bkey]
            if p_arg - hit_rate > CALIB_GAP_THRESHOLD:
                evidences["bucket"] = bkey
                evidences["model_prob"] = round(p_arg, 4)
                evidences["bucket_hit_rate"] = round(hit_rate, 4)
                descs.append(f"模型预测 {argmax_pt} 概率 {p_arg:.0%}，该概率档历史命中率仅 "
                             f"{hit_rate:.0%}（高估 {p_arg - hit_rate:.0%}pp）")

        # ② 平局过度自信（方向反转：模型平局系统性高于市场）
        market_draw = get_market_draw_prob(odds_hist)
        p_draw = wdl_probs.get("WDL_draw", 0.0)
        if market_draw is not None and p_draw > market_draw + 0.05:
            evidences["p_draw_model"] = round(p_draw, 4)
            evidences["p_draw_market"] = round(market_draw, 4)
            descs.append(f"模型平局概率 {p_draw:.0%} 高于市场隐含 "
                         f"{market_draw:.0%}（>5pp）——平局过度自信，模型系统性高估平局")

        if not descs:
            return None
        return {"description": "；".join(descs), "evidence": evidences}

    def _confidence_level(self, attrs: List[Dict[str, Any]]) -> int:
        """可信度 1-5：命中维度数 + 强证据（红牌/点球/大漂移）加分。"""
        n = len(attrs)
        if n >= 3:
            level = 4
        elif n == 2:
            level = 3
        else:
            level = 2
        # 强证据升级
        for a in attrs:
            ev = a.get("evidence", {})
            if ev.get("red_cards") or ev.get("penalties"):
                level = min(5, level + 1)
            if isinstance(ev.get("drift_max"), (int, float)) and ev["drift_max"] > 0.15:
                level = min(5, level + 1)
            if a["type"] == "模型局限性归因" and a["weight"] >= 99:
                level = 1  # 无明显归因
        return level


# ==================== 主入口 ====================
def _load_post_data(match_id: str, basics: Dict[str, Any], logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """--collect：自动采集 SofaScore 赛后明细（阵容/统计/事件）。"""
    sys.path.insert(0, str(PROJECT_DIR / "collection"))
    from final_sofascore_collector import SofaScoreClient, collect_post_match
    event_id = get_event_id(_get_conn(), match_id)
    if not event_id:
        logger.warning("无 fbref_match_mapping 映射，无法采集赛后明细")
        return None
    client = SofaScoreClient(logger=logger)
    try:
        return collect_post_match(client, event_id, logger)
    finally:
        client.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="模块 A3：赛后归因引擎（8 维度纯规则）")
    parser.add_argument("--match-id", required=True, help="odds.db 英文 match_id")
    parser.add_argument("--collect", action="store_true",
                        help="自动采集 SofaScore 赛后明细（阵容/统计/事件），否则仅用数据库数据")
    parser.add_argument("--write", action="store_true",
                        help="写回 post_match_review.attribution_json + confidence_level（须 review 行已存在）")
    args = parser.parse_args()

    logger = logging.getLogger("attribution_engine")
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(h)
    logger.setLevel(logging.INFO)

    conn = _get_conn()
    try:
        basics = get_match_basics(conn, args.match_id)
        if not basics:
            print(f"❌ matches 表无 {args.match_id} 记录")
            sys.exit(1)

        post_data = None
        if args.collect:
            print("采集 SofaScore 赛后明细 ...")
            post_data = _load_post_data(args.match_id, basics, logger)

        engine = AttributionEngine(conn_odds=conn)
        try:
            result = engine.run(args.match_id, post_data=post_data)
        finally:
            engine.close()

        print(json.dumps(result, ensure_ascii=False, indent=2))

        if args.write:
            cur = conn.cursor()
            cur.execute(
                "UPDATE post_match_review SET attribution_json=?, confidence_level=? WHERE match_id=?",
                (json.dumps(result, ensure_ascii=False),
                 result["confidence_level"], args.match_id),
            )
            conn.commit()
            print(f"\n✅ 已写回 post_match_review（更新 {cur.rowcount} 行）")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
