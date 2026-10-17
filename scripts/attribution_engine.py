# -*- coding: utf-8 -*-
"""
attribution_engine.py — 模块 A3：赛后归因引擎（8 维度纯规则，P0）

===============================================
背景（已归档：原模型改进实施方案 v1.0 §三/A3）：
  每场已完赛预测比赛输出带权重归因列表（权重和=100%），定位预测偏差根因。
  纯规则引擎实现（不用 LLM 判断，LLM 仅允许文字润色）。
  ⚠️ 平局归因方向修正：模型平局概率系统性高于市场（draw z=-2.86 显著负，
  7,291 笔平局价值投注 -4.47% 全线亏损）——维度 8 平局判断必须是
  「平局概率高于市场隐含且实际非平局 → 平局过度自信」，而非「平局召回不足」。

8 维度与数据来源：
  1 数据缺失归因   : sofascore_team_features 关键特征缺失率 >20% + understat xG 缺失
  2 赔率异动归因   : 竞彩 wdl_history 开盘→末条任一方向漂移 >5%（经 team_mapping 中英映射）；
                    C-20261014-085 起附 drift_indicative 标记（hit=漂移推向实际赢家 /
                    rev=漂移背离实际赢家 / neutral=无明确推向），反指场次文案改判
                    「市场临场信号反指，模型不跟随为正确行为」
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
import re
import sqlite3
import sys
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from team_name_mapping import normalize_team_name, TEAM_ALIASES  # noqa: E402

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
# ---- C-20261016-111：键错位回退命中计数器（只读仪表，零模型影响）----
# 用途：① 量化"英文键 review 还在不在持续产生"（P2/P4 紧迫性仪表）；
#       ② 未来"中文行也查不到"新死角的报警器；③ P2 生效后计数器归零 = 兜底层可安全移除的验收门。
FALLBACK_LOG = PROJECT_DIR / "reports" / "resolve_matches_fallback.jsonl"
_fallback_seen: set = set()


def _log_fallback(match_id: str, level: int, detail: str = "") -> None:
    """记录一次 resolve_matches_row 回退事件（进程内按 (level, match_id) 去重）。

    level: 1=中文名+日期回退命中（线索来自 review_row）
           2=中文名+日期回退命中（线索来自 event_id 的 fbref 映射）
           3=三层均无匹配（新死角告警）
    仅追加 JSONL，任何异常静默——仪表不得影响主链。
    """
    try:
        key = (level, match_id)
        if key in _fallback_seen:
            return
        _fallback_seen.add(key)
        FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
        rec = {
            "ts": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "day": datetime.now().strftime("%Y-%m-%d"),
            "match_id": match_id,
            "level": level,
            "detail": detail,
        }
        with open(FALLBACK_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:  # noqa: BLE001
        pass


def resolve_matches_row(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    event_id: Optional[str] = None,
    review_row: Optional[Dict[str, Any]] = None,
) -> Optional[Dict[str, Any]]:
    """按 match_id 取 matches 行；跨源键错位时回退查找（C-20261016-110b）。

    背景：matches 表键格式混存（实测 18321 行中中文键 12812 / 英文键 5509），
    post_match_review 由预测侧（500.com 英文键）写入，英文键 review 直查 matches 常 miss。
    此前调用点直接 return None 致下游整体跳过（DQ 假阴性 / 归因归零 / 让球口径静默降级）。
    回退链：
      ① match_id 精确查；
      ② 中文队名 + 日期（±1 天，兼容跨源日期口径错位）查 matches，线索优先取
         review_row，其次 event_id 对应的 fbref_match_mapping（英文名经 SOFA_TEAM_CN_MAP 转中文）；
      ③ 均无匹配返回 None（调用方自建兜底或跳过）。
    返回 matches 行 dict（SELECT *，字段名与列同名）。
    """
    row = conn.execute("SELECT * FROM matches WHERE match_id=?", (match_id,)).fetchone()
    if row is not None:
        return dict(row)

    home_cn = away_cn = date0 = None
    clue = None
    if review_row:
        home_cn = review_row.get("home_team")
        away_cn = review_row.get("away_team")
        date0 = review_row.get("match_date")
    if home_cn and away_cn:
        clue = "review"
    if not (home_cn and away_cn):
        rr = conn.execute(
            "SELECT home_team, away_team, match_date FROM post_match_review WHERE match_id=?",
            (match_id,),
        ).fetchone()
        if rr is not None:
            home_cn, away_cn, date0 = rr[0], rr[1], rr[2]
            clue = "review"
    if not (home_cn and away_cn) and event_id:
        try:
            mr = conn.execute(
                "SELECT home_team_cn, away_team_cn, match_date FROM fbref_match_mapping "
                "WHERE fbref_match_id=? LIMIT 1", (event_id,),
            ).fetchone()
            if mr is not None:
                try:
                    _feat = str(PROJECT_DIR / "features")
                    if _feat not in sys.path:
                        sys.path.insert(0, _feat)
                    from sofascore_pre_match_features import SOFA_TEAM_CN_MAP as _MAP
                except Exception:  # noqa: BLE001
                    _MAP = {}
                home_cn = _MAP.get(mr[0]) or mr[0]
                away_cn = _MAP.get(mr[1]) or mr[1]
                date0 = mr[2]
                clue = "event_id"
        except Exception:  # noqa: BLE001
            pass

    if home_cn and away_cn:
        try:
            _base = datetime.strptime(str(date0)[:10], "%Y-%m-%d")
            _dts = [(_base + timedelta(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)]
        except (ValueError, TypeError):
            _dts = [str(date0)[:10]] if date0 else []
        if _dts:
            _ph = ",".join("?" * len(_dts))
            row = conn.execute(
                f"SELECT * FROM matches WHERE home_team=? AND away_team=? AND match_date IN ({_ph}) LIMIT 1",
                (home_cn, away_cn, *_dts),
            ).fetchone()
            if row is not None:
                _log_fallback(match_id, 1 if clue == "review" else 2,
                              f"{home_cn}_{away_cn}@{date0}")
                return dict(row)
    _log_fallback(match_id, 3, "no matches row")
    return None


def get_match_basics(
    conn: sqlite3.Connection,
    match_id: str,
    *,
    event_id: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """比赛基础信息：matches（英文队名/handicap）为主，post_match_review（A2 权威赛果）补强。

    - home_team/away_team 保留英文（understat/阵容比对/竞彩匹配需要英文）
    - home_team_cn/away_team_cn 来自 review 中文名（复盘展示用）
    - actual_score/actual_wdl/league 优先 review（matches 表 09-06 场次常缺赛果）
    - handicap 仅 matches 有
    """
    cur = conn.cursor()
    cur.execute(
        "SELECT league, match_date, home_team, away_team, actual_score, actual_wdl "
        "FROM post_match_review WHERE match_id=?", (match_id,),
    )
    r = _row_to_dict(cur, cur.fetchone())
    m = resolve_matches_row(conn, match_id, event_id=event_id, review_row=r)
    if m is None:
        if r is None:
            return None
        # 彻底无 matches 行：以 review 行自建 basics（handicap 缺省 None），下游不整体跳过
        m = {
            "match_id": match_id,
            "match_date": r.get("match_date"),
            "league": r.get("league"),
            "home_team": r.get("home_team"),
            "away_team": r.get("away_team"),
            "actual_score": r.get("actual_score"),
            "actual_wdl": r.get("actual_wdl"),
            "handicap": None,
        }
    out = dict(m)
    out["match_id"] = match_id  # 回退路径下 m 可能来自中文键行，统一为请求侧 match_id
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
    """fbref_match_mapping: odds_match_id -> SofaScore event_id。

    精确 odds_match_id 失配时自动 fuzzy 回退（见 find_event_id_fuzzy）：
    model_predictions 用 UTC 日期+预测侧队名，而 mapping 用北京日期+SofaScore 队名，
    跨日凌晨场（如 2026-09-16_FC Barcelona_... vs 2026-09-17_FC Barcelona_...）会失配。
    """
    cur = conn.cursor()
    cur.execute("SELECT fbref_match_id FROM fbref_match_mapping WHERE odds_match_id=?", (match_id,))
    row = cur.fetchone()
    if row and row[0]:
        return str(row[0])
    return find_event_id_fuzzy(conn, match_id)


# ---- fuzzy 回退：日期±1 + 英文队名 token 匹配 ----
# 停用词（西/英/意冠词、介词）与俱乐部组织后缀，匹配前剔除
_TEAM_STOP_TOKENS = {"de", "a", "da", "la", "el", "the", "of", "and", "del"}
_TEAM_SUFFIX_TOKENS = {
    "fc", "cf", "afc", "sc", "ac", "ss", "cd", "ud", "us", "rc", "sd", "cp",
    "club", "calcio", "ssc", "cfc",
}


def _team_tokens(name: str) -> set:
    """英文队名 -> 去重音/小写/去停用词与后缀的 token 集合。

    例：'Deportivo de A Coruña' -> {'deportivo', 'coruna'}；'Málaga CF' -> {'malaga'}。
    """
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return {
        t for t in re.split(r"[^a-z]+", ascii_name)
        if t and t not in _TEAM_STOP_TOKENS and t not in _TEAM_SUFFIX_TOKENS
    }


def _team_token_match(t1: set, t2: set) -> bool:
    """两队名 token 集合是否同指：含共同独特 token，且满足包含或 Jaccard≥0.3。"""
    if not t1 or not t2:
        return False
    inter = t1 & t2
    if not inter:
        return False
    if inter == t1 or inter == t2:
        return True
    return len(inter) / len(t1 | t2) >= 0.3


def find_event_id_fuzzy(conn: sqlite3.Connection, match_id: str) -> Optional[str]:
    """精确 odds_match_id 失配时的 event_id 回退查找。

    口径：match_id 形如 ``{YYYY-MM-DD}_{home}_{away}``（队名不含下划线）。
    在 mapping 表日期 ±1 天窗口内，按主/客队 token 双匹配；
    命中必须唯一（多义时保守返回 None，避免错误关联）。
    """
    try:
        d = datetime.strptime(match_id[:10], "%Y-%m-%d").date()
    except ValueError:
        return None
    rest = match_id[11:]
    if "_" not in rest:
        return None
    home_name, away_name = rest.split("_", 1)
    home_tok, away_tok = _team_tokens(home_name), _team_tokens(away_name)
    if not home_tok or not away_tok:
        return None

    lo = (d - timedelta(days=1)).isoformat()
    hi = (d + timedelta(days=1)).isoformat()
    cur = conn.cursor()
    cur.execute(
        "SELECT DISTINCT fbref_match_id, home_team_cn, away_team_cn "
        "FROM fbref_match_mapping "
        "WHERE substr(odds_match_id, 1, 10) BETWEEN ? AND ?",
        (lo, hi),
    )
    hits = [
        str(r[0]) for r in cur.fetchall()
        if _team_token_match(home_tok, _team_tokens(r[1] or ""))
        and _team_token_match(away_tok, _team_tokens(r[2] or ""))
    ]
    return hits[0] if len(hits) == 1 else None


def get_understat_xg(conn: sqlite3.Connection, basics: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """维度4：understat xG（league + datetime + 英文队名匹配）。

    C-20260921-042: 修复中文队名匹配——先将 post_match_review 的中文队名
    转换为英文，再与 Understat 英文队名匹配。
    C-20260921-044: 修复时区日期偏移——Understat 存 UTC 日期，post_match_review
    存北京时间（UTC+8），跨天比赛日期会偏移 1 天。改为 ±1 天范围搜索。
    """
    if not basics:
        return None
    cur = conn.cursor()
    league = basics.get("league")
    home_raw = basics.get("home_team")
    away_raw = basics.get("away_team")
    date = basics.get("match_date")

    # C-20260921-044: ±1 天范围搜索（时区偏移兼容）
    from datetime import datetime as _dt, timedelta as _td
    try:
        d = _dt.strptime(date, "%Y-%m-%d")
        date_likes = [(d + _td(days=off)).strftime("%Y-%m-%d") + "%" for off in (-1, 0, 1)]
    except (ValueError, TypeError):
        date_likes = [f"{date}%"] if date else []

    # C-20260921-042: 中文→英文转换
    home = _cn_to_en_team(home_raw) or home_raw
    away = _cn_to_en_team(away_raw) or away_raw

    # 策略1：精确队名 + league（±1天）
    for dl in date_likes:
        cur.execute(
            "SELECT home_xg, away_xg, home_goals, away_goals FROM understat_match_team_stats "
            "WHERE league=? AND home_team=? AND away_team=? AND datetime LIKE ? LIMIT 1",
            (league, home, away, dl),
        )
        row = cur.fetchone()
        if row:
            break
    if row is None:
        # 策略2：精确队名，忽略 league（±1天）
        for dl in date_likes:
            cur.execute(
                "SELECT home_xg, away_xg, home_goals, away_goals FROM understat_match_team_stats "
                "WHERE home_team=? AND away_team=? AND datetime LIKE ? LIMIT 1",
                (home, away, dl),
            )
            row = cur.fetchone()
            if row:
                break
    if row is None:
        # 策略3：归一化队名匹配（处理 FC Augsburg vs Augsburg、
        # Bayer 04 Leverkusen vs Bayer Leverkusen 等英英变体）
        home_norm = _norm_en_team(home)
        away_norm = _norm_en_team(away)
        # C-20260921-042: 同时用 normalize_team_name 做双向归一化（处理
        # RB Leipzig vs RasenBallsport Leipzig、Hamburg vs Hamburger SV 等）
        home_std = normalize_team_name(home) or normalize_team_name(home_raw)
        away_std = normalize_team_name(away) or normalize_team_name(away_raw)
        if (home_norm and away_norm) or (home_std and away_std):
            for dl in date_likes:
                cur.execute(
                    "SELECT home_team, away_team, home_xg, away_xg, home_goals, away_goals "
                    "FROM understat_match_team_stats WHERE datetime LIKE ?",
                    (dl,),
                )
                for r in cur.fetchall():
                    # 路径A: _norm_en_team 英文归一化匹配
                    us_home_norm = _norm_en_team(r[0])
                    us_away_norm = _norm_en_team(r[1])
                    if (home_norm and away_norm and
                        home_norm == us_home_norm and away_norm == us_away_norm):
                        row = (r[2], r[3], r[4], r[5])
                        break
                    if (home_norm and away_norm and
                        home_norm == us_away_norm and away_norm == us_home_norm):
                        row = (r[3], r[2], r[5], r[4])
                        break
                    # 路径B: normalize_team_name 双向归一化匹配
                    us_home_std = normalize_team_name(r[0])
                    us_away_std = normalize_team_name(r[1])
                    if (home_std and away_std and
                        home_std == us_home_std and away_std == us_away_std):
                        row = (r[2], r[3], r[4], r[5])
                        break
                    if (home_std and away_std and
                        home_std == us_away_std and away_std == us_home_std):
                        row = (r[3], r[2], r[5], r[4])
                        break
                if row:
                    break
    if row is None:
        # C-20261016-110: Understat 无数据时先回退 SofaScore 赛后缓存（已挂链稳定、
        # stats.expectedGoals 比赛级 xG，无需实时请求），再回退 xgscore.io
        sofa = _sofascore_xg_fallback(conn, basics)
        if sofa:
            return sofa
        # C-20260922: Understat 无数据时 fallback 到 xgscore.io（try/except 全隔离）
        xg = _xgscore_fallback(conn, basics)
        if xg is not None:
            xg["xg_source"] = "xgscore"
        return xg
    return {"home_xg": float(row[0]), "away_xg": float(row[1]),
            "home_goals": int(row[2]), "away_goals": int(row[3]),
            "xg_source": "understat"}


def _sofascore_xg_fallback(conn: sqlite3.Connection, basics: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """C-20261016-110: xG 第二回退——sofascore_post_match_cache 的 stats.expectedGoals。

    Understat 断供（C-20261014-081 型）期间的保险层：sofascore 赛后采集器挂链稳定，
    缓存表已有 stats.home/away.expectedGoals 比赛级 xG。±1 天 + 英文归一队名匹配
    （与竞彩日期口径错位兼容），try/except 全隔离失败返回 None。
    """
    try:
        date = basics.get("match_date")
        home = basics.get("home_team")
        away = basics.get("away_team")
        if not (date and home and away):
            return None
        from datetime import datetime as _dt, timedelta as _td
        try:
            base = _dt.strptime(date, "%Y-%m-%d")
            dates = [(base + _td(days=k)).strftime("%Y-%m-%d") for k in (-1, 0, 1)]
        except ValueError:
            dates = [date]
        home_norm = _norm_en_team(_cn_to_en_team(home) or home)
        away_norm = _norm_en_team(_cn_to_en_team(away) or away)
        ph = ",".join("?" * len(dates))
        cur = conn.execute(
            f"SELECT match_id, payload_json FROM sofascore_post_match_cache "
            f"WHERE substr(match_id, 1, 10) IN ({ph})", dates)
        for mid, pj in cur.fetchall():
            parts = str(mid).split("_")
            if len(parts) < 3:
                continue
            mh, ma = _norm_en_team(parts[1]), _norm_en_team(parts[2])
            direct = (home_norm == mh and away_norm == ma)
            flipped = (home_norm == ma and away_norm == mh)
            if not (direct or flipped):
                continue
            d = json.loads(pj)
            st = d.get("stats") or {}
            hx = (st.get("home") or {}).get("expectedGoals")
            ax = (st.get("away") or {}).get("expectedGoals")
            if hx is None or ax is None:
                continue
            return {"home_xg": float(ax if flipped else hx),
                    "away_xg": float(hx if flipped else ax),
                    "home_goals": int(d.get("away_score") if flipped else d.get("home_score") or 0),
                    "away_goals": int(d.get("home_score") if flipped else d.get("away_score") or 0),
                    "xg_source": "sofascore"}
        return None
    except Exception:
        return None


def _xgscore_fallback(conn: sqlite3.Connection, basics: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """C-20260922: Understat 无数据时 fallback 到 xgscore.io 采集比赛级 xG。

    策略：
      1. 先查 xgscore_match_stats 缓存表（避免复盘时重复请求）
      2. 缓存未命中则实时抓取 xgscore.io 比赛页
    try/except 全隔离，失败返回 None（维度4 运气偏差归因跳过，不影响其他维度）。
    """
    try:
        import sys as _sys
        _col_dir = PROJECT_DIR / "collection"
        if str(_col_dir) not in _sys.path:
            _sys.path.insert(0, str(_col_dir))
        from xgscore_collector import get_cached_xg, fetch_match_xg  # noqa: E402
        league = basics.get("league")
        home = basics.get("home_team")
        away = basics.get("away_team")
        date = basics.get("match_date")
        if not (league and home and away and date):
            return None
        # 1. 查 xgscore_match_stats 缓存表
        cached = get_cached_xg(conn, league, home, away, date)
        if cached:
            logging.getLogger("attribution").debug(
                f"xgscore 缓存命中: {home} vs {away} xG {cached['home_xg']}-{cached['away_xg']}")
            return cached
        # 2. 实时抓取（不落库，避免复盘时副作用写入）
        result = fetch_match_xg(league, home, away, date)
        if result:
            logging.getLogger("attribution").info(
                f"xgscore fallback 命中: {home} vs {away} xG {result['home_xg']}-{result['away_xg']}")
        return result
    except Exception as e:
        logging.getLogger("attribution").debug(f"xgscore fallback 异常（已隔离）: {e}")
        return None


def _cn_to_en_team(cn_name: Optional[str]) -> Optional[str]:
    """C-20260921-042: 中文队名→英文队名（用于 Understat 匹配）。

    优先从 TEAM_ALIASES 查找第一个英文别名；
    若未命中，用 normalize_team_name 归一后查找；
    都未命中返回 None（调用方 fallback 到 _norm_en_team 模糊匹配）。
    """
    if not cn_name:
        return None
    # 检查是否已是英文
    if cn_name.isascii():
        return cn_name
    # 直接从 TEAM_ALIASES 查找
    std = normalize_team_name(cn_name)
    if std and std in TEAM_ALIASES:
        for alias in TEAM_ALIASES[std]:
            if alias.isascii():
                return alias
    return None


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


def _find_odds_match_id(conn: sqlite3.Connection, match_id: str,
                        home_en: str, away_en: str) -> Optional[str]:
    """竞彩 wdl_history match_id（中文格式）匹配。

    C-20260930-002：精确日期失败时按 d-1 / d / d+1 三日依次扫描（竞彩键日期
    可能按开售日落在前一天，如罗马场同时存在 09-19/09-20 两个镜像键）。
    命中顺序：精确日构造键 → 精确日模糊扫描 → 相邻日模糊扫描（先 d-1 后 d+1）。
    """
    date = match_id[:10]
    home_cn = _cn_team(conn, home_en)
    away_cn = _cn_team(conn, away_en)
    cand = f"{date}_{home_cn}_{away_cn}"
    cur = conn.cursor()
    cur.execute("SELECT 1 FROM wdl_history WHERE match_id=? LIMIT 1", (cand,))
    if cur.fetchone():
        return cand

    h_norm, a_norm = normalize_team_name(home_cn), normalize_team_name(away_cn)

    def _scan_day(day: str) -> Optional[str]:
        cur.execute(
            "SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE ?", (f"{day}%",),
        )
        for mid in cur.fetchall():
            parts = mid[0].split("_")
            if len(parts) != 3 or parts[0] != day:
                continue
            mh, ma = parts[1], parts[2]
            if (normalize_team_name(mh) == h_norm and normalize_team_name(ma) == a_norm) or \
               (normalize_team_name(mh) == a_norm and normalize_team_name(ma) == h_norm):
                return mid[0]
        return None

    # 精确日优先，再相邻日（d-1 → d+1）
    hit = _scan_day(date)
    if hit:
        return hit
    try:
        d0 = datetime.strptime(date, "%Y-%m-%d").date()
    except ValueError:
        return None
    for delta in (-1, 1):
        hit = _scan_day((d0 + timedelta(days=delta)).isoformat())
        if hit:
            return hit
    return None


def get_odds_timeline(conn: sqlite3.Connection, match_id: str,
                      basics: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """维度2/8：竞彩 wdl_history 开盘→末条。返回 {odds_id, open, close, drift_max}。"""
    if not basics:
        return None
    cid = _find_odds_match_id(conn, match_id, basics.get("home_team", ""), basics.get("away_team", ""))
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
    return {"odds_id": cid, "open": list(open_o), "close": list(close_o),
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


# C-20261014-085：漂移指示性标记值域
DRIFT_INDICATIVE_HIT = "hit"       # 漂移推向实际赢家（顺指）
DRIFT_INDICATIVE_REV = "rev"       # 漂移背离实际赢家（反指）
DRIFT_INDICATIVE_NEUTRAL = "neutral"  # 无明确漂移方向 / 赛果缺失

_WDL_LABELS = ("主胜", "平局", "客胜")  # wdl_history 列序 [win_a, draw, win_b]


def _norm_implied(odds: List[Any]) -> Optional[List[float]]:
    """[win_a, draw, win_b] 赔率 -> 去抽水隐含概率。"""
    try:
        inv = [1.0 / float(o) for o in odds if o and float(o) > 0]
    except (ValueError, TypeError):
        return None
    if len(inv) != 3 or sum(inv) <= 0:
        return None
    s = sum(inv)
    return [v / s for v in inv]


def get_drift_indicative(odds_hist: Optional[Dict[str, Any]],
                         actual_wdl: Optional[str]) -> Dict[str, Any]:
    """C-20261014-085 维度2：漂移方向 vs 实际赛果的对应关系（顺指/反指/中性）。

    判据：开盘/末条去抽水隐含概率差 argmax = 市场临场推向方向；
      推向 == 实际赛果 -> hit（顺指）；推向 != 实际赛果 -> rev（反指）；
      三向概率差均 <=0（无明确推向）或赛果缺失 -> neutral。
    返回 {"drift_indicative": hit/rev/neutral, "drift_toward": 主胜/平局/客胜/None,
          "drift_delta": 最大隐含概率增量}。
    """
    out = {"drift_indicative": DRIFT_INDICATIVE_NEUTRAL,
           "drift_toward": None, "drift_delta": None}
    if not odds_hist:
        return out
    open_p = _norm_implied(odds_hist.get("open"))
    close_p = _norm_implied(odds_hist.get("close"))
    if not open_p or not close_p:
        return out
    deltas = [close_p[i] - open_p[i] for i in range(3)]
    idx = max(range(3), key=lambda i: deltas[i])
    if deltas[idx] <= 0:
        return out
    toward = _WDL_LABELS[idx]
    out["drift_toward"] = toward
    out["drift_delta"] = round(deltas[idx], 4)
    norm_actual = {"主胜": "主胜", "平局": "平局", "客胜": "客胜",
                   "胜": "主胜", "平": "平局", "负": "客胜"}.get(actual_wdl or "")
    if norm_actual is None:
        return out
    out["drift_indicative"] = (DRIFT_INDICATIVE_HIT if toward == norm_actual
                               else DRIFT_INDICATIVE_REV)
    return out


# C-20261016-106: Platt 校准生效日（C-20261015-113 文件名 bug 修复）。
# 此前的预测为未校准原始输出；分桶命中率按此日期切 pre/post 双窗，
# 防止「未校准时代」旧样本污染新归因（每场复盘重复误报高估、人工翻老账）。
PLATT_LIVE_DATE = "2026-10-15"


def get_bucket_hit_rates(conn: sqlite3.Connection) -> Dict[str, Dict[str, float]]:
    """维度8：全库 WDL 概率分桶历史命中率（model_predictions + matches.actual_wdl）。

    分桶：40-50 / 50-60 / 60-70 / >=70；命中按 pred_wdl 与 actual_wdl 比对。
    返回双窗结构：
      {
        "all":    {bkey: rate},   # 原口径（n>=20），向后兼容
        "pre":    {bkey: rate},   # 校准前时代（预测时间 < PLATT_LIVE_DATE，n>=20）
        "post":   {bkey: rate},   # 校准后时代（>= PLATT_LIVE_DATE，n>=10 才输出）
        "pre_n":  {bkey: n},
        "post_n": {bkey: n},      # 含 n<10 的桶，供「样本不足」判定
      }
    """
    cur = conn.cursor()
    cur.execute(
        """
        SELECT mp.prediction_type, mp.probability,
               CASE mp.prediction_type
                    WHEN 'WDL_home' THEN '主胜'
                    WHEN 'WDL_draw' THEN '平局'
                    WHEN 'WDL_away' THEN '客胜' END AS pred_wdl,
               m.actual_wdl,
               substr(mp.timestamp, 1, 10) AS pred_date
        FROM model_predictions mp
        JOIN matches m ON mp.match_id = m.match_id
        WHERE mp.prediction_type IN ('WDL_home','WDL_draw','WDL_away')
          AND m.actual_wdl IS NOT NULL AND m.actual_wdl != ''
        """
    )
    buckets: Dict[str, Dict[str, int]] = {}
    for pt, prob, pred_wdl, actual_wdl, pred_date in cur.fetchall():
        try:
            p = float(prob)
        except (ValueError, TypeError):
            continue
        # matches.actual_wdl 值域：'主胜/平局/客胜'/'胜/平/负' 混合——归一
        hit = _is_wdl_hit(pred_wdl, actual_wdl)
        if hit is None:
            continue
        bucket = f"{pt}_{_bucket_key(p)}"
        era = "post" if (pred_date or "") >= PLATT_LIVE_DATE else "pre"
        for scope in ("all", era):
            k = f"{scope}|{bucket}"
            b = buckets.setdefault(k, {"n": 0, "hit": 0})
            b["n"] += 1
            b["hit"] += 1 if hit else 0
    out: Dict[str, Dict[str, Any]] = {"all": {}, "pre": {}, "post": {},
                                      "all_n": {}, "pre_n": {}, "post_n": {}}
    for key, b in buckets.items():
        scope, bkey = key.split("|", 1)
        out[f"{scope}_n"][bkey] = b["n"]
        min_n = 10 if scope == "post" else 20  # 校准后窗口样本少，阈值放宽但保留「样本不足」标记能力
        if b["n"] >= min_n:
            out[scope][bkey] = b["hit"] / b["n"]
    return out


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

        # 维度2数据源先取：维度1兜底判据也需要竞彩快照数
        odds_hist = get_odds_timeline(self.conn, match_id, basics)

        # ---- 维度1 数据缺失归因（C-20260930-002 Fix B：复合判据）----
        # 权威源=赛前快照 prematch_data_quality；历史场走库检兜底；
        # SofaScore 11 列缺失率作为子信号并入（不再是唯一口径）。
        gap = self._prematch_quality_signals(match_id, basics, odds_hist, event_id)
        if gap["data_gap"]:
            attrs.append({
                "type": "数据缺失归因", "weight": BASE_WEIGHTS["数据缺失归因"],
                "description": "赛前数据缺失（{}）：{}".format(gap["source"], gap["description"]),
                "evidence": {"source": gap["source"], "triggered": gap["triggered"],
                             **gap["signals"]},
            })

        # ---- 维度2 赔率异动归因（C-20261014-085：顺指/反指双文案）----
        if odds_hist and odds_hist["drift_max"] > ODDS_DRIFT_THRESHOLD:
            drift = get_drift_indicative(odds_hist, basics.get("actual_wdl"))
            ind = drift["drift_indicative"]
            if ind == DRIFT_INDICATIVE_REV:
                drift_note = "市场临场信号反指（漂移推向失败方），模型不跟随为正确行为"
            else:
                # hit / neutral 沿用原文案
                drift_note = "市场临场信息未反映在模型特征中"
            attrs.append({
                "type": "赔率异动归因", "weight": BASE_WEIGHTS["赔率异动归因"],
                "description": f"竞彩赔率赛前漂移最大 {odds_hist['drift_max']:.1%}（{odds_hist['drift_dir']} 方向），"
                               f"{drift_note}",
                "drift_indicative": ind,
                "evidence": {"odds_match_id": odds_hist["odds_id"],
                             "open": odds_hist["open"], "close": odds_hist["close"],
                             "drift_max": odds_hist["drift_max"], "snapshots": odds_hist["snapshots"],
                             "drift_indicative": ind,
                             "drift_toward": drift["drift_toward"],
                             "drift_delta": drift["drift_delta"]},
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
    # C-20260930-002 Fix B：维度1复合判据阈值
    PDQ_COMPLETENESS_THRESHOLD = 80   # 赛前报告完整度 <80% 计缺失
    _PDQ_SELECT = (
        "SELECT generated_at,completeness,wdl_snapshots,wdl_not_offered,"
        "sporttery_status,ouzhi_status,betting_status,betting_crawl_status,sofascore_status "
        "FROM prematch_data_quality WHERE match_id=?"
    )

    def _find_500_fid(self, match_id: str) -> Optional[int]:
        """odds500_match 英文 match_id -> fid（键格式与 build_log 一致）。"""
        cur = self.conn.cursor()
        cur.execute("SELECT fid FROM odds500_match WHERE match_id=? LIMIT 1", (match_id,))
        row = cur.fetchone()
        try:
            return int(row[0]) if row and row[0] is not None else None
        except (TypeError, ValueError):
            return None

    def _sporttery_other_offered(self, mdate: str, home_cn: str, away_cn: str) -> bool:
        """让球/大小球/比分历史表是否有该场（±1 日）。

        兜底路径识别「竞彩未开售 WDL」（业务缺位，非数据缺失）：WDL 表无行但
        其余竞彩盘口有行——口径与 generate_unified_report.find_any_sporttery_match_id 一致。
        """
        h_norm, a_norm = normalize_team_name(home_cn), normalize_team_name(away_cn)
        try:
            d0 = datetime.strptime(mdate, "%Y-%m-%d").date()
        except ValueError:
            return False
        days = [(d0 + timedelta(days=k)).isoformat() for k in (-1, 0, 1)]
        cur = self.conn.cursor()
        for table in ("handicap_history", "total_goals_history", "score_history"):
            for day in days:
                try:
                    cur.execute(
                        f"SELECT DISTINCT match_id FROM {table} WHERE match_id LIKE ?",
                        (f"{day}%",),
                    )
                except sqlite3.OperationalError:
                    continue
                for (mid,) in cur.fetchall():
                    parts = mid.split("_")
                    if len(parts) != 3 or parts[0] != day:
                        continue
                    mh, ma = parts[1], parts[2]
                    if (normalize_team_name(mh) == h_norm and normalize_team_name(ma) == a_norm) or \
                       (normalize_team_name(mh) == a_norm and normalize_team_name(ma) == h_norm):
                        return True
        return False

    def _prematch_quality_signals(self, match_id: str, basics: Dict[str, Any],
                                  odds_hist: Optional[Dict[str, Any]],
                                  event_id: Optional[str]) -> Dict[str, Any]:
        """维度1「赛前数据缺失」复合判据（C-20260930-002 Fix B）。

        ① 权威源 prematch_data_quality（报告渲染时落库的赛前快照，历史不覆盖）：
           completeness<80% 或任一通道 FAIL（竞彩「未开售WDL」是业务缺位，不触发；
           WARN 不触发）。取 kickoff 当日 23:59:59 前最后一条；无则取最新行兜底。
        ② 历史兜底（快照表无行，2026-09-30 前的比赛）：实时库检
           竞彩快照=0（1 条不算）/ 500 欧指空壳 / betting 缺失·非ok·空壳。
           ⚠️ 兜底局限：检的是库内*当前*状态——历史场赛后若已补齐数据会漏报，
           这正是 Fix A 赛前快照要消除的盲区。
        ③ SofaScore 11 列缺失率检测保留为子信号，两条路径都参与 OR。
        """
        mdate = match_id[:10]
        cur = self.conn.cursor()
        row = None
        try:
            cur.execute(
                self._PDQ_SELECT + " AND generated_at<=? ORDER BY generated_at DESC LIMIT 1",
                (match_id, f"{mdate} 23:59:59"),
            )
            row = cur.fetchone()
            if row is None:
                cur.execute(self._PDQ_SELECT + " ORDER BY id DESC LIMIT 1", (match_id,))
                row = cur.fetchone()
        except sqlite3.OperationalError:
            row = None  # 旧库无 prematch_data_quality 表 → 走兜底

        signals: Dict[str, Any] = {}
        triggered: List[str] = []
        descs: List[str] = []
        feature_rate = self._feature_missing_rate(event_id) if event_id else None
        signals["feature_missing_rate"] = round(feature_rate, 4) if feature_rate is not None else None

        if row is not None:
            source = "prematch_data_quality"
            (_gen, completeness, wdl_snaps, not_offered,
             st_sport, st_ouzhi, st_bet, crawl_bet, st_sofa) = row
            signals.update({
                "generated_at": _gen, "completeness": completeness,
                "wdl_snapshots": wdl_snaps, "wdl_not_offered": bool(not_offered),
                "sporttery_status": st_sport, "ouzhi_status": st_ouzhi,
                "betting_status": st_bet, "betting_crawl_status": crawl_bet,
                "sofascore_status": st_sofa,
            })
            if completeness is not None and completeness < self.PDQ_COMPLETENESS_THRESHOLD:
                triggered.append("completeness")
                descs.append(f"赛前报告数据完整度仅 {completeness}%（<80%）")
            channel_rules = (
                ("ouzhi", st_ouzhi, "500.com 百家欧指通道 FAIL（未采集/空壳）"),
                ("betting", st_bet, f"500.com 投注分析通道 FAIL（{crawl_bet or '状态未知'}）"),
                ("sofascore", st_sofa, "SofaScore 赛前特征通道 FAIL"),
            )
            for key, status, desc in channel_rules:
                if status == "FAIL":
                    triggered.append(key)
                    descs.append(desc)
            # 竞彩：未开售 WDL 属业务缺位（WARN），不计缺失；只有真实 FAIL 才触发
            if st_sport == "FAIL" and not not_offered:
                triggered.append("sporttery")
                descs.append("竞彩时序赔率通道 FAIL（无快照）")
        else:
            source = "fallback_db_check"
            # ① 竞彩快照数（维度2 已匹配的中文键；0 才触发，1 条不触发）
            snaps = int(odds_hist["snapshots"]) if odds_hist else 0
            signals["wdl_snapshots"] = snaps
            not_offered = False
            if snaps == 0:
                # 未开售 WDL（让球/大小/比分有行）是业务缺位，不计缺失
                _h_cn = basics.get("home_team_cn") or _cn_team(self.conn, basics.get("home_team", ""))
                _a_cn = basics.get("away_team_cn") or _cn_team(self.conn, basics.get("away_team", ""))
                not_offered = self._sporttery_other_offered(mdate, _h_cn, _a_cn)
                signals["wdl_not_offered"] = not_offered
                if not not_offered:
                    triggered.append("sporttery")
                    descs.append("竞彩赛前时序无快照（开盘→即时漂移不可观测）")
            # ② 500 欧指空壳：无 summary 行，或 company_count 空/0 且公司明细 0 行
            fid = self._find_500_fid(match_id)
            signals["fid"] = fid
            ouzhi_shell = None
            if fid is None:
                ouzhi_shell = True  # 无 500 比赛映射，三通道均视为不可用
                descs_ouzhi = "odds500_match 无该场映射"
            else:
                cur.execute(
                    "SELECT company_count FROM odds500_ouzhi_summary WHERE fid=? LIMIT 1", (fid,))
                sr = cur.fetchone()
                cur.execute("SELECT COUNT(*) FROM odds500_ouzhi_company WHERE fid=?", (fid,))
                company_n = int(cur.fetchone()[0])
                cc = sr[0] if sr else None
                try:
                    cc_n = int(float(cc)) if cc not in (None, "") else 0
                except (TypeError, ValueError):
                    cc_n = 0
                ouzhi_shell = (sr is None) or (cc_n == 0 and company_n == 0)
                descs_ouzhi = f"空壳（company_count={cc!r}，公司明细 {company_n} 行）"
            signals["ouzhi_shell"] = ouzhi_shell
            if ouzhi_shell:
                triggered.append("ouzhi")
                descs.append(f"500.com 百家欧指{descs_ouzhi}")
            # ③ betting：缺失 / crawl_status 非 ok / ok 但空壳
            bet_status = None
            bet_shell = None
            if fid is None:
                bet_shell = True
            else:
                cur.execute(
                    "SELECT crawl_status,home_bf_volume,draw_bf_volume,away_bf_volume "
                    "FROM odds500_betting WHERE fid=? LIMIT 1", (fid,),
                )
                br = cur.fetchone()
                if br is None:
                    bet_status, bet_shell = None, True
                else:
                    bet_status = br[0]
                    if bet_status != "ok":
                        bet_shell = True
                    else:
                        bet_shell = not any(
                            str(v or "").strip() not in ("", "0", "None")
                            for v in br[1:4]
                        )
            signals["betting_crawl_status"] = bet_status
            signals["betting_shell"] = bet_shell
            if bet_shell:
                triggered.append("betting")
                if bet_status is None:
                    descs.append("500.com 投注分析无采集记录")
                elif bet_status != "ok":
                    descs.append(f"500.com 投注分析采集状态 {bet_status}（反爬/解析失败）")
                else:
                    descs.append("500.com 投注分析 ok 但字段空壳（疑似反爬）")

        # ③ SofaScore 关键特征缺失率（原子信号，两路径通用）
        if feature_rate is not None and feature_rate > MISSING_FEATURE_THRESHOLD:
            triggered.append("features")
            descs.append(f"SofaScore 赛前关键特征缺失率 {feature_rate:.0%}（>20%）")

        return {
            "source": source,
            "data_gap": bool(triggered),
            "triggered": triggered,
            "signals": signals,
            "description": "；".join(descs),
        }

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

        # ① 概率档高估（C-20261016-106 双窗口径：优先校准后窗口；post 样本不足
        #    时只展示 pre 旧账、不做高估判定——防止未校准时代样本污染新归因）
        argmax_pt = max(wdl_probs, key=wdl_probs.get)
        p_arg = wdl_probs[argmax_pt]
        bkey = f"{argmax_pt}_{_bucket_key(p_arg)}"
        post_rates, pre_rates = hit_rates.get("post", {}), hit_rates.get("pre", {})
        post_n = hit_rates.get("post_n", {}).get(bkey, 0)
        pre_n = hit_rates.get("pre_n", {}).get(bkey, 0)
        evidences["bucket"] = bkey
        evidences["model_prob"] = round(p_arg, 4)
        if bkey in pre_rates:
            evidences["bucket_hit_rate_pre"] = round(pre_rates[bkey], 4)
            evidences["bucket_n_pre"] = pre_n
        if bkey in post_rates:
            # 校准后样本足够（n>=10）：用 post 窗判定，这才代表当前模型
            post_rate = post_rates[bkey]
            evidences["bucket_hit_rate_post"] = round(post_rate, 4)
            evidences["bucket_n_post"] = post_n
            if p_arg - post_rate > CALIB_GAP_THRESHOLD:
                evidences["bucket_hit_rate"] = round(post_rate, 4)
                descs.append(f"模型预测 {argmax_pt} 概率 {p_arg:.0%}，校准后该档命中率仅 "
                             f"{post_rate:.0%}（n={post_n}，高估 {p_arg - post_rate:.0%}pp）")
        elif bkey in pre_rates:
            # 校准后样本不足：仅当旧口径「本来会误报高估」时输出免责说明（防污染账本），
            # 否则保持沉默——不为每场 0.40 档比赛新增归因噪音
            pre_rate = pre_rates[bkey]
            if p_arg - pre_rate > CALIB_GAP_THRESHOLD:
                descs.append(f"模型预测 {argmax_pt} 概率 {p_arg:.0%}；校准后样本不足（n={post_n}<10），"
                             f"校准前时代同档命中率 {pre_rate:.0%}（n={pre_n}，未校准旧账，仅参考不判定）")

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
    """--collect：缓存优先读取 SofaScore 赛后明细（C-20261002-001 起 miss 时采集并落库）。"""
    from post_match_cache import get_post_data
    conn = _get_conn()
    try:
        data, _state = get_post_data(conn, match_id, logger, allow_fetch=True)
        return data
    finally:
        conn.close()


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
