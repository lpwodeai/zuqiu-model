# -*- coding: utf-8 -*-
"""
统一赛前预测报告生成器 (generate_unified_report.py)
====================================================================
整合三大赛前数据通道，调用 prediction_core 四维预测引擎，按
《赛前预测报告制式模板 v1.0》生成 18 章 markdown 报告。

数据通道：
  ① 竞彩网(Sporttery) 时序赔率 → odds.db 的 wdl_history / handicap_history /
     total_goals_history / score_history（喂 prediction_core）
  ② 500.com 投注分析 / 百家欧指 → odds.db 的 odds500_betting / odds500_ouzhi_*（对方）
  ③ SofaScore 赛前特征 → odds.db 的 sofascore_team_features（球队实力/状态）

比赛清单来源：odds500_match（status=1 未开赛，含中英文队名、盘口、赛程）

用法：
  python scripts/generate_unified_report.py [--date 2026-08-30] [--days 2] [--dry-run]
  python scripts/generate_unified_report.py --date 2026-08-30 --league 英超,意甲
"""
from __future__ import annotations

import argparse
import json
import sys
import sqlite3
import statistics
from pathlib import Path
from datetime import datetime, timedelta, timezone

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
sys.path.insert(0, str(BASE_DIR / "scripts"))
sys.path.insert(0, str(BASE_DIR / "collection"))

try:
    from feature_utils import normalize_team_name as _normalize_team_name
except Exception:  # 特征库/依赖缺失时回退原样，不阻断报告生成
    _normalize_team_name = lambda s: s

# wdl_history 口径归一化（C-20260918-052）：
# feature_utils.normalize_team_name 是纯精确映射，无法处理
# 500.com 全名 ↔ wdl_history 短名（如 纽卡斯尔联 ↔ 纽卡斯尔、托特纳姆热刺 ↔ 热刺）的跨表匹配，
# 导致 find_sporttery_match_id 在 sporttery 写入短名 + 500.com 用全名时 LIKE 失配，
# 5 场被跳过。改用 team_name_mapping.normalize_team_name 4 级模糊匹配
# （精确别名→子串→模糊→序列相似度），与 sporttery_live_collector.normalize_team 写入口径对齐。
from team_name_mapping import normalize_team_name as _normalize_team_wdl


def _normalize_team_for_wdl(name):
    """wdl_history 口径归一化：优先 team_name_mapping 4 级模糊匹配，
    失败时 fallback 到 feature_utils 精确映射，再 fallback 原名。"""
    if not name:
        return name
    n = _normalize_team_wdl(name)
    if n:
        return n
    n2 = _normalize_team_name(name)
    return n2 if n2 else name

try:
    import ev_engine as _ev_engine
    import upset_factor_engine as _upset_engine
except Exception:  # EV引擎缺失时降级为原有凯利分析，不阻断报告生成
    _ev_engine = None

try:
    from knowledge_base_schema import get_insights as _kb_get_insights
    from knowledge_base_schema import get_correction_rules as _kb_get_correction_rules
    from knowledge_base_schema import record_kb_hit as _kb_record_hit
except Exception:  # B1 知识库缺失时降级标注，不阻断报告生成
    _kb_get_insights = None
    _kb_get_correction_rules = None
    _kb_record_hit = None

try:
    from data_source_conflict_detector import (
        detect_conflict as _detect_conflict,
        fundamental_home_prob as _fundamental_home_prob,
        market_home_prob as _market_home_prob,
    )
    _CONFLICT_DETECTOR_AVAILABLE = True
except Exception:  # P2-04 冲突检测模块缺失时降级，不阻断报告生成
    _detect_conflict = None
    _fundamental_home_prob = None
    _market_home_prob = None
    _CONFLICT_DETECTOR_AVAILABLE = False

ODDS_DB = DATA_DIR / "odds.db"

# 报告版本 / 模型版本（与模板 §18、project_memory 口径一致）
REPORT_VERSION = "v3.0"
MODEL_VERSION = "v8.3 / v3.0"
MODEL_HEADER = "WDL(5模型Stacking: DC+XGB+LGB+Elo+Bayes) + T-005 v3(让球) + T-006 v4(比分) + 总进球"

# P0-06: 特征/配置版本（随特征管线或 config.yaml 变更时更新，用于历史预测可复现）
FEATURE_VERSION = "208"  # slim_odds + ts_odds + consensus_odds 三模组合（与 project_memory 口径一致）
CONFIG_VERSION = "—"  # config.yaml 尚无版本号字段，暂用占位；后续接入 D3 单一来源

# P0-03: 缺失值统一标记（采集端用 0/None 占位缺失时，报告不显示 0.00% 冒充数据）
MISSING = "—【数据未采集】—"

# 传球成功率下限门禁：职业队近5场加权传球成功率不可能低于 50%，低于此值视为采集端脏数据
# （如 accurate_pass/total_pass 字段错位产生的 0.01 占位），报告按「未采集」处理而非显示 1.0%。
PASS_SR_MIN = 0.5

CN_TZ = timezone(timedelta(hours=8))

# 亚洲盘中文 → 数值（"受"为客队让球，主队受让为负）
HCP_STR_MAP = {
    "平手": 0.0, "平手/半球": 0.25, "半球": 0.5, "半球/一球": 0.75,
    "一球": 1.0, "一球/球半": 1.25, "球半": 1.5, "球半/两球": 1.75,
    "两球": 2.0, "两球/两球半": 2.25, "两球半": 2.5, "两球半/三球": 2.75, "三球": 3.0,
    # C-20260919-021: 补全深盘（odds500_match 实测存在，原映射缺失致 parse_hcp_line 返回 None）
    "三球/三球半": 3.25, "三球半": 3.5, "三球半/四球": 3.75,
}


# ============================================================
# 基础工具
# ============================================================
def connect_db() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def pct(x, nd=1):
    """数值(0~1) → 百分比字符串"""
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.{nd}f}%"
    except (TypeError, ValueError):
        return "—"


def fnum(x, nd=2):
    if x is None or x == "":
        return MISSING
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return MISSING


def num(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def strip_pct(x):
    """去掉百分比字符串的 '%' 后缀转 float（"95.10%" → 95.1），失败返回 None。"""
    if x is None:
        return None
    try:
        return float(str(x).replace("%", "").strip())
    except (TypeError, ValueError):
        return None


def parse_hcp_line(hcp_str):
    """把 500.com 亚洲盘中文串转数值（主队视角，让球为负，受让为正——与竞彩 goalLine 一致）。

    符号约定: 负值为主队让球, 正值为主队受让（对齐 sporttery_collector 的 goalLine 处理）。
    例如: "球半/两球"(主队让1.75球) → -1.75, "受一球"(主队受让1球) → +1.0
    """
    if not hcp_str:
        return None
    s = str(hcp_str).strip()
    neg = s.startswith("受")
    if neg:
        s = s[1:]
    val = HCP_STR_MAP.get(s)
    if val is None:
        return None
    # 受让(neg=True) → 正, 让球(neg=False) → 负
    return val if neg else -val


def format_hcp_label(hcp_line):
    """C-20261013-068: 竞彩让球线（整数 goalLine，主队视角：负=让球，正=受让）→ 中文标签。

    全链路统一线源：§一摘要卡 / §三让球节 / §7.2 标题 / §十四盘口对比 共用此函数，
    杜绝亚盘 0.25 档中文串（来自 odds500_match.handicap）再次混入展示层。

    None → "竞彩未开让球盘"（第③级真·未开售，与 C-067 线源切换口径一致）。
    """
    if hcp_line is None:
        return "竞彩未开让球盘"
    try:
        v = float(hcp_line)
    except (TypeError, ValueError):
        return "竞彩未开让球盘"
    if v == 0:
        return "平手"
    n = int(abs(v)) if abs(v) == int(abs(v)) else abs(v)
    if v < 0:
        return "让{}球".format(n) if n != 1 else "让一球"
    return "受让{}球".format(n) if n != 1 else "受让一球"


def odds_to_implied(win, draw, lose):
    """赔率 → 去水隐含概率"""
    try:
        win, draw, lose = float(win), float(draw), float(lose)
    except (TypeError, ValueError):
        return None, None, None, None
    if win <= 1 or draw <= 1 or lose <= 1:
        return None, None, None, None
    total = 1 / win + 1 / draw + 1 / lose
    return (1 / win) / total, (1 / draw) / total, (1 / lose) / total, 1 / total


def _market_lambda_contrast(ih_h, ih_d, ih_a, total):
    """C-20261018-141: 市场 λ 对照（展示层只读，λ 失衡场自检；零模型/零落库影响）。

    总量锁死为模型 λ 和（λ主+λ客），市场去水 WDL 经 solve_lambda_core 二分反演
    市场分割（与 C-20261002-006 bisect 同构：锁总量 + 市场 WDL 二分），独立泊松
    网格取 Top1 格。对照靶子 = λ 差放大（族内形状虚高），非总量。市场数据不足返回 None。
    """
    try:
        if not all(isinstance(x, (int, float)) and x > 0 for x in (ih_h, ih_d, ih_a)):
            return None
        if not isinstance(total, (int, float)) or total <= 0.3:
            return None
        import math
        from t006_score_predictor_v5 import solve_lambda_core  # 函数内 import 防循环（C-20261002-006 同法）
        lh, la = solve_lambda_core(ih_h, ih_a, total)

        def _pp(k, lam):
            return math.exp(-lam) * lam ** k / math.factorial(k)

        best, best_p = "—", -1.0
        for h in range(8):
            for a in range(8):
                p = _pp(h, lh) * _pp(a, la)
                if p > best_p:
                    best_p, best = p, "{}:{}".format(h, a)
        return {"lh": lh, "la": la, "top1": best, "top1_prob": best_p}
    except Exception:
        return None


def kelly(model_prob, odds):
    """凯利指数 = (模型概率×赔率 - 1)/(赔率 - 1)"""
    if odds is None or model_prob is None or odds <= 1:
        return None
    return (model_prob * odds - 1) / (odds - 1)


def kelly_tag(k):
    if k is None:
        return "—"
    if k < 0:
        return "无价值"
    if k < 0.05:
        return "微价值"
    if k < 0.10:
        return "价值"
    return "强价值"


def value_tag(edge):
    if edge is None:
        return "—"
    if edge >= 0.08:
        return "VALUE"
    if edge >= 0.04:
        return "SLIGHT_VALUE"
    if edge >= 0.02:
        return "SLIGHT_VALUE"
    if edge <= -0.05:
        return "OVERPRICED"
    return "FAIR"


def recommendation_type(edge):
    if edge is None:
        return "NO_VALUE"
    if edge >= 0.08:
        return "STRONG_VALUE"
    if edge >= 0.04:
        return "VALUE"
    if edge >= 0.02:
        return "SLIGHT_VALUE"
    if edge <= -0.05:
        return "AVOID"
    return "NO_VALUE"


# ============================================================
# 比赛清单
# ============================================================
def discover_matches(conn, start_date, end_date, leagues=None, played_only=False):
    """从 odds500_match 取比赛清单，竞彩 matches 表兜底补全。

    主源：odds500_match（500.com，含中英文队名、盘口、赛程）。
    兜底：matches（竞彩网 sporttery）——500.com 对部分联赛/轮次覆盖不全时
          （如 2026-10 意甲/西甲赛程缺失），用竞彩在售场次补全。

    played_only=False：未开赛(status=1)，供日常赛前预测；
    played_only=True ：已赛(status IN (2,5))，供历史补生成回放（用已入库赔率+特征）。
    """
    if played_only:
        status_cond = (
            "status IN (2,5) OR EXISTS ("
            " SELECT 1 FROM matches m WHERE m.match_id = odds500_match.match_id"
            "  AND m.actual_score IS NOT NULL AND m.actual_score != ''"
            ")"
        )
    else:
        status_cond = "status = 1"
    sql = """
        SELECT fid, league, season, round, match_date, match_time,
               home_team_cn, away_team_cn, home_team_en, away_team_en,
               win, draw, lost, handicap, pan
        FROM odds500_match
        WHERE season = '26/27' AND ({status_cond})
          AND match_date >= ? AND match_date <= ?
    """.format(status_cond=status_cond)
    args = [start_date, end_date]
    if leagues:
        placeholders = ",".join("?" for _ in leagues)
        sql += " AND league IN (" + placeholders + ")"
        args.extend(leagues)
    sql += "\n        ORDER BY match_date, match_time, league\n    "
    rows = conn.execute(sql, args).fetchall()
    out = []
    seen_keys = set()  # (date, home_cn_norm, away_cn_norm) 去重
    from datetime import datetime as _dt
    from datetime import timedelta as _td
    _now = _dt.now() if not played_only else None
    for r in rows:
        d = dict(r)
        d["home_team_en"] = (d.get("home_team_en") or "").strip()
        d["away_team_en"] = (d.get("away_team_en") or "").strip()
        # 队名若为中文残留（部分升班马无英文），回退到中文名占位
        if not d["home_team_en"] or any("\u4e00" <= ch <= "\u9fff" for ch in d["home_team_en"]):
            d["home_team_en"] = d["home_team_cn"]
        if not d["away_team_en"] or any("\u4e00" <= ch <= "\u9fff" for ch in d["away_team_en"]):
            d["away_team_en"] = d["away_team_cn"]
        # C-20261011-047 开球时间守卫（C-20261018-140 改造：不再 skip 丢弃，改标 _frozen
        # 标记——冻结场在主循环走 FROZEN 分支：不重预测（防赛后数据泄漏，C-053 纪律 /
        # pred_ts_valid 门禁），summary 经 load_frozen_row 保留既有赛前预测行。
        # 解混：500 混搭口径 match_date=欧洲日期 + match_time=北京时刻，直接拼接会把
        # 次日凌晨场（如皇马 03:00）误判已开球永久跳过；match_time<12:00 视为次日凌晨 +1 天。
        if _now is not None:
            try:
                _ko_date = d['match_date']
                if d['match_time'] and d['match_time'] < "12:00":
                    _ko_date = (_dt.strptime(_ko_date, "%Y-%m-%d") + _td(days=1)).strftime("%Y-%m-%d")
                _ko = _dt.strptime(f"{_ko_date} {d['match_time']}", "%Y-%m-%d %H:%M")
                if _ko <= _now:
                    d["_frozen"] = True
                    d["_kickoff"] = _ko.strftime("%Y-%m-%d %H:%M")
            except (ValueError, TypeError):
                pass
        key = (d["match_date"], _normalize_team_for_wdl(d["home_team_cn"]), _normalize_team_for_wdl(d["away_team_cn"]))
        seen_keys.add(key)
        out.append(d)

    # 兜底：从竞彩 matches 表补全 500.com 缺失的场次
    if not played_only:
        league_filter = ""
        sp_args = [start_date, end_date]
        if leagues:
            placeholders = ",".join("?" for _ in leagues)
            league_filter = " AND substr(match_type, 1, 2) IN (" + placeholders + ")"
            sp_args.extend(leagues)
        sp_sql = f"""
            SELECT match_id, match_type, match_date, home_team, away_team, handicap
            FROM matches
            WHERE match_date >= ? AND match_date <= ?{league_filter}
              AND (actual_score IS NULL OR actual_score = '')
            ORDER BY match_date, match_type
        """
        sp_rows = conn.execute(sp_sql, sp_args).fetchall()
        from datetime import timedelta as _td
        for r in sp_rows:
            mid, mtype, mdate, home, away, hcp = r
            # 从 match_type 提取联赛简称
            lg = mtype[:2] if mtype else ""
            h_norm = _normalize_team_for_wdl(home)
            a_norm = _normalize_team_for_wdl(away)
            # 去重：500.com 用欧洲当地日期（深夜球赛存为前一天），竞彩用北京日期。
            # 同一场比赛在两表中 match_date 可能差 1 天，按 ±1 天 + 队名匹配去重。
            dup = False
            for (sd, sh, sa) in seen_keys:
                if sh == h_norm and sa == a_norm:
                    try:
                        if abs((_dt.strptime(sd, "%Y-%m-%d") - _dt.strptime(mdate, "%Y-%m-%d")).days) <= 1:
                            dup = True
                            break
                    except ValueError:
                        pass
            if dup:
                continue
            key = (mdate, h_norm, a_norm)
            seen_keys.add(key)
            # C-20261016-114: 兜底场次反查真实 500 fid —— 跨源日期口径不一（竞彩北京日
            # vs 500 欧洲当地日，实测同一物理场次差 1~2 天且方向不定），discover 日期窗
            # 可能漏掉主源；此前直接拿竞彩中文 match_id 当 fid，load_500_data(m["fid"])
            # 按 fid 查不到 → §八/§九 显示「无数据」（C-20261016-002 遗留）。
            # 按 队名归一 + 日期±2天 反查、取日期最接近者（同对阵队名双重归一已足够防误配），
            # 命中则挂真实 fid，未命中保留原 mid（不劣化既有行为）。
            _fid500 = None
            _kickoff500 = None
            _round500 = None
            for _c in conn.execute(
                "SELECT fid, home_team_cn, away_team_cn, match_date, match_time, round "
                "FROM odds500_match "
                "WHERE season='26/27' "
                "AND match_date BETWEEN date(?,'-2 day') AND date(?,'+2 day') "
                "ORDER BY ABS(julianday(match_date) - julianday(?))",
                (mdate, mdate, mdate),
            ).fetchall():
                if (_normalize_team_for_wdl(_c[1]) == h_norm
                        and _normalize_team_for_wdl(_c[2]) == a_norm):
                    _fid500 = _c[0]
                    # C-20261016-116: 命中真实 500 记录时，取该场 500 口径的真实开赛
                    # 日期+时刻，供表头展示（与主源/主分支报告口径一致）。500 日期与
                    # 竞彩北京日可能差 1~2 天，故单独存为展示字段 _kickoff500，
                    # 不改内部 match_date/match_time（零模型影响）。
                    if _c[3] and _c[4]:
                        _kickoff500 = f"{_c[3]} {_c[4]}"
                    # C-20261016-118: 同步取 500 轮次，修表头「第None轮」（兜底分支原硬编码 round=None）
                    _round500 = _c[5]
                    break
            out.append({
                "fid": _fid500 if _fid500 else mid,
                "_sporttery_fallback": True,
                "league": lg,
                "season": "26/27",
                "round": _round500,  # C-20261016-118: 500 轮次；未命中仍为 None（表头降级处理）
                "match_date": mdate,
                "match_time": "12:00",  # 竞彩表无开赛时间；表头展示改由 _kickoff500 承担（见下）
                "_kickoff500": _kickoff500,  # C-20261016-116: 500 真实开赛（日期+时刻），仅表头展示用
                # C-20261017-133: 兜底行显示名归一到 canonical 中文（复用报告链已有
                # feature_utils.normalize_team_name，单一源 team_names.CANONICAL_CN）。
                # matches 表 FBref 英文行（竞彩未售场）原值直落会产生英文名报告文件；
                # 仅归一报告展示层，matches 写入侧归一属 P2/P4 窗口项，此处不碰。
                "home_team_cn": _normalize_team_name(home),
                "away_team_cn": _normalize_team_name(away),
                "home_team_en": home,   # 无英文名，用中文占位
                "away_team_en": away,
                "win": 0, "draw": 0, "lost": 0,
                "handicap": hcp or 0,
                "pan": None,
                "_source": "sporttery",
            })
            # C-20261018-140: 兜底场冻结判据（已开球不重预测，主循环走 FROZEN 分支保 summary 行）。
            # 判据 = mdate（竞彩北京日期，口径纯）+ _kickoff500 时刻分量（500 match_time
            # 即北京时刻，mdate 已承担日期归一，无需再解混）；未命中 500 反查时保守判
            # mdate < 今天才冻结（今日场宁可不冻结，临场终版链仍生效）。
            _fb = out[-1]
            try:
                if _kickoff500:
                    _fko = _dt.strptime(
                        f"{mdate} {_kickoff500.split(' ')[1]}", "%Y-%m-%d %H:%M")
                    if _fko <= _dt.now():
                        _fb["_frozen"] = True
                        _fb["_kickoff"] = _fko.strftime("%Y-%m-%d %H:%M")
                elif mdate < _dt.now().strftime("%Y-%m-%d"):
                    _fb["_frozen"] = True
            except (ValueError, TypeError, IndexError):
                pass
            # C-20261018-144: 漂移行封版扩展（C-143 泄漏根修②守卫）——同一物理场次
            # （队名双重归一+日期±3天，0a-7 传播同口径）在 matches 中已有赛果时，
            # 本兜底行视同已赛冻结，禁止 live 重算子模型表（0a-7 漏网/传播未跑时
            # 的残余错目录写回通道，C-136 同族；±3天同主客对阵双赛现实中不存在）。
            if not _fb.get("_frozen"):
                try:
                    for _pr in conn.execute(
                        "SELECT home_team, away_team FROM matches "
                        "WHERE match_date BETWEEN date(?, '-3 day') AND date(?, '+3 day') "
                        "AND actual_score IS NOT NULL AND actual_score != ''",
                        (mdate, mdate),
                    ).fetchall():
                        if (_normalize_team_for_wdl(_pr[0]) == h_norm
                                and _normalize_team_for_wdl(_pr[1]) == a_norm):
                            _fb["_frozen"] = True
                            break
                except Exception:
                    pass
        if sp_rows:
            print(f"[兜底] 竞彩 matches 表补全 {len([r for r in out if r.get('_source')=='sporttery'])} 场（500.com 缺失）")

        # C-20261016-109: 跨源日期归一——500.com 深夜场按欧洲当地日期存前一天，
        # 报告目录/文件名归一到竞彩北京日期（±1 天 + 队名匹配），防同一物理场次
        # 在 <date> 与 <date+1> 两个目录重复渲染。仅影响展示层（report_date），
        # match_date 原始值保留供逻辑/落库使用。
        sp_date_map = {}
        for r in sp_rows:
            sp_date_map[(_normalize_team_for_wdl(r[3]), _normalize_team_for_wdl(r[4]))] = r[2]
        for d in out:
            if d.get("_sporttery_fallback"):
                continue
            bj = sp_date_map.get((_normalize_team_for_wdl(d["home_team_cn"]),
                                  _normalize_team_for_wdl(d["away_team_cn"])))
            if bj and bj != d["match_date"]:
                try:
                    if abs((_dt.strptime(bj, "%Y-%m-%d") - _dt.strptime(d["match_date"], "%Y-%m-%d")).days) <= 1:
                        d["report_date"] = bj
                except (ValueError, TypeError):
                    pass

    return out


def filter_ghost_matches(conn, matches, season='26/27', min_gap_days=2):
    """过滤幽灵赛程：某队在间隔 < min_gap_days 天内有两场不同 fid 的比赛 → 判为幽灵。

    背景：500.com 数据源偶有「错配对阵塞入错误轮次」的脏数据（如 round=6 里
    同时出现 09-15 Villarreal vs Betis 和 09-16 Betis vs Getafe / Malaga vs Villarreal，
    形成连续两天背靠背，职业足球不可能）。此函数基于"同队背靠背间隔"做物理合理性校验。

    参数:
        min_gap_days: 最小允许间隔天数（含）。职业联赛通常 ≥3 天，取 2 天留缓冲。
    返回:
        (filtered_matches, removed)  removed 为被过滤的场次列表，用于打印日志。
    """
    if not matches:
        return matches, []

    from datetime import datetime, timedelta

    # 1. 取出所有参与场次涉及的队 → 查同赛季该队全部赛程（建索引）
    teams = set()
    for m in matches:
        teams.add(_clean_team(m.get('home_team_en') or ''))
        teams.add(_clean_team(m.get('away_team_en') or ''))
    teams.discard('')

    # team_dates[team] = [(date_str, fid), ...]  同赛季该队所有已入库赛程
    team_dates = {t: [] for t in teams}
    if teams:
        placeholders = ','.join('?' * len(teams))
        sql = f"""
            SELECT home_team_en, away_team_en, match_date, fid
            FROM odds500_match
            WHERE season = ?
              AND (home_team_en IN ({placeholders}) OR away_team_en IN ({placeholders}))
        """
        rows = conn.execute(sql, [season] + list(teams) + list(teams)).fetchall()
        for r in rows:
            ht = _clean_team(r['home_team_en'] or '')
            at = _clean_team(r['away_team_en'] or '')
            md = r['match_date']
            fid = r['fid']
            if ht in team_dates:
                team_dates[ht].append((md, fid))
            if at in team_dates:
                team_dates[at].append((md, fid))

    # 2. 逐场校验：主/客任一队出现"另一场 fid 不同、间隔 < min_gap_days" → 过滤
    removed = []
    kept = []
    for m in matches:
        ht = _clean_team(m.get('home_team_en') or '')
        at = _clean_team(m.get('away_team_en') or '')
        md = m['match_date']
        fid = m['fid']
        is_ghost = False
        ghost_reason = None
        for team, label in ((ht, 'home'), (at, 'away')):
            if not team or team not in team_dates:
                continue
            try:
                cur = datetime.strptime(md, '%Y-%m-%d').date()
            except Exception:
                continue
            for other_date, other_fid in team_dates[team]:
                if other_fid == fid:
                    continue
                try:
                    od = datetime.strptime(other_date, '%Y-%m-%d').date()
                except Exception:
                    continue
                gap = abs((cur - od).days)
                if gap < min_gap_days:
                    is_ghost = True
                    ghost_reason = (f"{label}队 {team} 与 {other_date} fid={other_fid} "
                                    f"间隔 {gap} 天（<{min_gap_days} 天，背靠背不成立）")
                    break
            if is_ghost:
                break
        if is_ghost:
            m['_ghost_reason'] = ghost_reason
            removed.append(m)
        else:
            kept.append(m)

    return kept, removed



def _clean_team(s):
    return " ".join(str(s).strip().split())


def match_has_prediction(conn, m):
    """判断该场是否已在 model_predictions 有预测（兼容中/英文两种 match_id 历史格式）。"""
    date = m["match_date"]
    cands = {
        f"{date}_{_clean_team(m['home_team_en'])}_{_clean_team(m['away_team_en'])}",
        f"{date}_{_clean_team(m['home_team_cn'])}_{_clean_team(m['away_team_cn'])}",
    }
    for mid in cands:
        if conn.execute("SELECT 1 FROM model_predictions WHERE match_id = ? LIMIT 1", (mid,)).fetchone():
            return True
    return False


def load_frozen_row(conn, m):
    """C-20261018-140: 冻结场（已开球）从 model_predictions 加载既有赛前预测，构造
    summary 行——不重预测（防赛后数据泄漏，C-053 纪律 / pred_ts_valid 门禁）。
    无既有 WDL 预测时返回 None（主循环整场跳过）。
    """
    date = m["match_date"]
    cands = [
        f"{date}_{_clean_team(m['home_team_en'])}_{_clean_team(m['away_team_en'])}",
        f"{date}_{_clean_team(m['home_team_cn'])}_{_clean_team(m['away_team_cn'])}",
    ]
    mid = None
    for c in cands:
        if conn.execute(
            "SELECT 1 FROM model_predictions WHERE match_id=? AND prediction_type='WDL_home' LIMIT 1",
            (c,),
        ).fetchone():
            mid = c
            break
    if mid is None:
        return None
    probs = {}
    snap_json = None
    for pt, prob, sj in conn.execute(
        "SELECT prediction_type, probability, input_snapshot_json FROM model_predictions "
        "WHERE match_id=? AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')",
        (mid,),
    ):
        probs[pt] = prob or 0.0
        if sj and not snap_json:
            snap_json = sj
    hp = probs.get("WDL_home", 0.0)
    dp = probs.get("WDL_draw", 0.0)
    ap = probs.get("WDL_away", 0.0)
    pred = "主胜" if hp >= max(dp, ap) else ("平局" if dp >= ap else "客胜")
    # 比分口径 = generate_unified_report_v2.0 行（与报告 §一 most_likely 同源）；
    # t006 行（v5 shadow 网格 argmax，同名类型）仅作兜底——冒烟实证利兹联两行
    # 分叉（t006=1:1 / v2.0=2:1），summary 展示口径以 v2.0 为准
    top1 = conn.execute(
        "SELECT prediction FROM model_predictions WHERE match_id=? AND prediction_type='Score_top1' "
        "ORDER BY (model_name LIKE 'generate_unified_report%') DESC, id DESC LIMIT 1",
        (mid,),
    ).fetchone()
    snaps = 2  # 解析失败保守置 2：冻结行的告警应以赛前快照为准，缺失时不误报
    if snap_json:
        try:
            _ws = json.loads(snap_json).get("wdl_snapshots")
            snaps = len(_ws) if isinstance(_ws, list) else int(_ws)
        except Exception:
            snaps = 2
    _rd = m.get("report_date") or date
    fname = f"{m['league']}_{_rd}_{_normalize_team_name(m['home_team_cn'])}_vs_{_normalize_team_name(m['away_team_cn'])}.md".replace("/", "-")
    return {
        "match_id": mid, "league": m["league"],
        "home": m["home_team_cn"], "away": m["away_team_cn"],
        "match_date": date, "report_date": m.get("report_date"),
        "wdl": {"prediction": pred, "home_prob": hp, "draw_prob": dp, "away_prob": ap},
        "score": {"most_likely": top1[0] if top1 else "—"},
        "hc": {}, "tg": {}, "ev": {},  # 冻结行不携带让球/总进球/EV（summary 不消费；不回写 DB）
        "_file": fname, "_frozen": True,
        "_wdl_snapshots": snaps, "_wdl_not_offered": False, "_completeness": 100,
    }


# ============================================================
# C-20260922-053: --replay 落库赛前时间锚点
# ============================================================
def pick_replay_anchor(match_date, buildlog_generated_at, file_ctime):
    """纯逻辑：按证据等级选择回放补算的赛前时间锚点。

    证据优先级（只接受 ≤ 比赛日 的时间，晚于比赛日的证据不成立）：
      1. buildlog  — 旧 build_log.json 的 generated_at（精确到秒，但重渲会覆盖）
      2. file_ctime— 报告 md 文件创建时间（Windows 覆盖写保留创建时间）
      3. match_date 00:00:00 — 无任何赛前证据时的保守锚点
    返回 (anchor_timestamp_str, evidence)。
    """
    md_end = f"{match_date} 23:59:59"
    if buildlog_generated_at and str(buildlog_generated_at) <= md_end \
            and str(buildlog_generated_at)[:10] <= match_date:
        return str(buildlog_generated_at), "buildlog"
    if file_ctime is not None and file_ctime.strftime("%Y-%m-%d") <= match_date:
        return file_ctime.strftime("%Y-%m-%d %H:%M:%S"), "file_ctime"
    return f"{match_date} 00:00:00", "match_date"


def resolve_replay_anchor(match_date, report_path):
    """渲染前调用：从既有报告文件解析赛前锚点（render_report 会覆盖 build_log）。

    必须在 render_report() 之前执行，否则 build_log 的 generated_at 已被
    重渲时间覆盖、md 内容也已重写（文件 ctime 不受影响，仍可作二级证据）。
    """
    buildlog_ts = None
    file_ctime = None
    try:
        if report_path and Path(report_path).exists():
            file_ctime = datetime.fromtimestamp(Path(report_path).stat().st_ctime)
            log_path = Path(report_path).parent / (Path(report_path).stem + "_build_log.json")
            if log_path.exists():
                _bl = json.loads(log_path.read_text(encoding="utf-8"))
                buildlog_ts = _bl.get("generated_at")
    except Exception as _e:
        print(f"  ⚠️ 赛前锚点解析失败，退化比赛日锚点: {type(_e).__name__}: {_e}")
    return pick_replay_anchor(match_date, buildlog_ts, file_ctime)



# ============================================================
# 竞彩网(Sporttery) 时序赔率组装
# ============================================================
def find_sporttery_match_id(conn, home_cn, away_cn, match_date):
    """按中文队名 + 日期邻近(±3天) 定位竞彩网时序 match_id。

    竞彩场次日期常比实际开赛日期早一天（凌晨场按销售日归属），故做日期容差。

    归一化口径（C-20260918-052）：使用 _normalize_team_for_wdl（team_name_mapping
    4 级模糊匹配）替代 _normalize_team_name（feature_utils 精确映射）。前者与
    sporttery_live_collector.normalize_team 写入口径一致，能跨表匹配
    500.com 全名 ↔ wdl_history 短名（如 纽卡斯尔联 ↔ 纽卡斯尔、托特纳姆热刺 ↔ 热刺）。
    """
    home_cn = _normalize_team_for_wdl(home_cn)
    away_cn = _normalize_team_for_wdl(away_cn)
    like = f"%_{home_cn}_{away_cn}"
    rows = conn.execute(
        "SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE ?", (like,)
    ).fetchall()
    if not rows:
        return None
    base = datetime.strptime(match_date, "%Y-%m-%d")
    # C-20261016-117: 同一物理场次的竞彩时序会因销售日跨零点分裂到两个日期键
    # （实测 2026-10-16_法兰克福_科隆 与 2026-10-17_法兰克福_科隆 并存）。原先只取
    # 日期最近者，命中记录稀疏的错键（10-17 键 wdl=2 vs 10-16 键 wdl=5，且 10-16 键
    # 时间戳更新）→ §七 时序漂移被高估为「快照不足」。改为取四表记录总数最多者，
    # 同数再取日期最近，使报告读到该场真实/最完整的那条序列。
    best, best_score, best_diff = None, -1, 999
    for r in rows:
        mid = r["match_id"]
        try:
            dstr = mid.split("_", 1)[0]
            d = datetime.strptime(dstr, "%Y-%m-%d")
        except (ValueError, IndexError):
            continue
        diff = abs((d - base).days)
        if diff > 3:
            continue
        total = 0
        for tbl in ("wdl_history", "handicap_history", "total_goals_history", "score_history"):
            total += conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE match_id=?", (mid,)
            ).fetchone()[0]
        if total > best_score or (total == best_score and diff < best_diff):
            best, best_score, best_diff = mid, total, diff
    return best


def find_any_sporttery_match_id(conn, home_cn, away_cn, match_date):
    """探测竞彩是否开了任意玩法（让球/大小球/比分），用于区分「未开胜平负盘」与「整场无竞彩」。

    深盘强队（如来让球两球半）竞彩常只开让球/大小球/比分、不开胜平负正盘，
    此时 wdl_history 无记录但 handicap/total_goals/score_history 有记录。
    与 find_sporttery_match_id 一样做 ±3 天日期容差，避免命中历史赛季同名对阵。

    归一化口径同 find_sporttery_match_id（C-20260918-052）：用 _normalize_team_for_wdl。
    """
    home_cn = _normalize_team_for_wdl(home_cn)
    away_cn = _normalize_team_for_wdl(away_cn)
    like = f"%_{home_cn}_{away_cn}"
    base = datetime.strptime(match_date, "%Y-%m-%d")
    cands = set()
    for tbl in ("handicap_history", "total_goals_history", "score_history", "wdl_history"):
        for r in conn.execute(
            f"SELECT DISTINCT match_id FROM {tbl} WHERE match_id LIKE ?", (like,)
        ):
            cands.add(r["match_id"])
    # C-20261016-117: 与 find_sporttery_match_id 同口径——多候选键取四表记录总数
    # 最多者（同数取日期最近），避免跨零点分裂的稀疏错键遮蔽真实序列。
    best, best_score, best_diff = None, -1, 999
    for mid in cands:
        try:
            d = datetime.strptime(mid.split("_", 1)[0], "%Y-%m-%d")
        except (ValueError, IndexError):
            continue
        diff = abs((d - base).days)
        if diff > 3:
            continue
        total = 0
        for tbl in ("wdl_history", "handicap_history", "total_goals_history", "score_history"):
            total += conn.execute(
                f"SELECT COUNT(*) FROM {tbl} WHERE match_id=?", (mid,)
            ).fetchone()[0]
        if total > best_score or (total == best_score and diff < best_diff):
            best, best_score, best_diff = mid, total, diff
    return best


def build_sporttery_odds(conn, mid, fallback_win, fallback_draw, fallback_lose, other_mid=None):
    """从竞彩网四张时序表组装 odds_data（prediction_core 兼容）。

    C-20261002-004：mid 仅按 wdl_history 解析；深盘强队场竞彩常不开 WDL 但开
    让球/大小球/比分（如 2026-09-20 塞维利亚 vs 巴塞罗那：HAD 0 条、HHAD 8 条、
    TTG 5 条、CRS 4 时点）。other_mid 为另三表解析到的 match_id——WDL 仍走 500
    兜底，但让球/总进球/比分必须用 other_mid 继续取数（此前 mid=None 直接 return，
    三表数据全部被丢弃，报告误报「无总进球赔率记录」）。
    """
    odds_data = {
        "wdl_odds": {"records": [], "open": {}, "close": {}},
        "handicap_odds": {"line": None, "records": [], "open": {}, "close": {}},
        "score_odds": {"records": []},
        "tg_odds": {"records": []},
    }
    timing_mid = mid or other_mid
    if not mid:
        # 无竞彩 WDL 时序 → 用 500.com 初盘做单条兜底 WDL（不再提前 return，
        # 让球/总进球/比分若 other_mid 命中仍继续取）
        fw, fd, fl = num(fallback_win), num(fallback_draw), num(fallback_lose)
        if fw and fd and fl and fw > 1:
            rec = {"time": "初盘", "win": fw, "draw": fd, "lose": fl}
            odds_data["wdl_odds"] = {"records": [rec], "open": rec, "close": rec}
    else:
        # WDL
        rows = conn.execute(
            "SELECT timestamp, win_a, draw, win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp",
            (mid,),
        ).fetchall()
        if rows:
            recs = [{"time": r["timestamp"], "win": r["win_a"], "draw": r["draw"], "lose": r["win_b"]} for r in rows]
            odds_data["wdl_odds"] = {"records": recs, "open": recs[0], "close": recs[-1]}

    if not timing_mid:
        return odds_data

    # 让球
    rows = conn.execute(
        "SELECT timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE match_id=? ORDER BY timestamp",
        (timing_mid,),
    ).fetchall()
    if rows:
        recs = [{"time": r["timestamp"], "win": r["hcp_win"], "draw": r["hcp_draw"], "lose": r["hcp_lose"]} for r in rows]
        odds_data["handicap_odds"] = {"line": None, "records": recs, "open": recs[0], "close": recs[-1]}

    # 总进球
    rows = conn.execute(
        "SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus "
        "FROM total_goals_history WHERE match_id=? ORDER BY timestamp",
        (timing_mid,),
    ).fetchall()
    if rows:
        labels = ["0", "1", "2", "3", "4", "5", "6", "7+"]
        tg_recs = []
        for r in rows:
            goals = {labels[i]: r[i + 1] for i in range(8)}
            tg_recs.append({"pub_time": r["timestamp"], "goals": goals})
        odds_data["tg_odds"]["records"] = tg_recs

    # 比分（按时间戳分组，按比分主/平/客分类）
    rows = conn.execute(
        "SELECT timestamp, score, odds FROM score_history WHERE match_id=? ORDER BY timestamp",
        (timing_mid,),
    ).fetchall()
    if rows:
        by_ts = {}
        for r in rows:
            rec = by_ts.setdefault(r["timestamp"], {"pub_time": r["timestamp"], "win_odds": {}, "draw_odds": {}, "lose_odds": {}})
            sc = r["score"]
            od = num(r["odds"])
            if od is None or ":" not in sc:
                continue
            try:
                h, a = map(int, sc.split(":")[:2])
            except ValueError:
                continue
            key = f"{h}:{a}"
            if h > a:
                rec["win_odds"][key] = od
            elif h == a:
                rec["draw_odds"][key] = od
            else:
                rec["lose_odds"][key] = od
        odds_data["score_odds"]["records"] = list(by_ts.values())

    return odds_data


def append_shadow_v5(record):
    """C-20260920-029: 追加 v5 Shadow 预测到 reports/t006_shadow_v5.jsonl（按 match_id 去重覆盖）。

    Shadow 记录在赛前生成、不含实际比分，赛后由独立评测任务按 match_id JOIN
    matches.actual_score；重跑同场时覆盖旧行，不产生重复。
    """
    path = BASE_DIR / "reports" / "t006_shadow_v5.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(ln).get("match_id") != record["match_id"]:
                    kept.append(ln)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def append_tg_calib_shadow(record):
    """C-20260921-036: 追加 TG λ 校准 Shadow 记录到 reports/tg_calib_shadow.jsonl。

    只含 control（生产 factor=1.0）与 shadow（滚动校准因子）双份 TG 分布，
    不含赛果；赛后由 tg_calibration_shadow_eval.py 按 match_id JOIN
    post_match_review.actual_tg 做 RPS/Brier/ECE 评测。按 match_id 去重覆盖。
    """
    path = BASE_DIR / "reports" / "tg_calib_shadow.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(ln).get("match_id") != record["match_id"]:
                    kept.append(ln)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def append_lambda_split_shadow(record):
    """P1-1（C-20261002-006）: 追加 λ 拆分 Shadow 记录到 reports/lambda_split_shadow.jsonl。

    control=生产 legacy A-002 拆分，shadow=同总量市场去水 WDL 二分反演拆分，
    两份各自重跑 T-006 v4（WDL 区域重加权不变）。赛前生成、不含赛果，
    赛后按 match_id JOIN 实际比分做配对评测；按 match_id 去重覆盖。
    """
    path = BASE_DIR / "reports" / "lambda_split_shadow.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(ln).get("match_id") != record["match_id"]:
                    kept.append(ln)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


def append_tg_wodds_shadow(record):
    """C-20260921-040: 追加 TG 融合权重 Shadow 记录到 reports/tg_wodds_shadow.jsonl。

    只含 control（生产 w_odds=0.15）与 shadow（w_odds=0.30）双份 TG 分布，
    factor=1.0 隔离 λ 校准变量；按 match_id 去重覆盖。
    """
    path = BASE_DIR / "reports" / "tg_wodds_shadow.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(ln).get("match_id") != record["match_id"]:
                    kept.append(ln)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


# C-20261014-086: 平局高危旗标（实验性·待 fresh 验证）——定义冻结，禁止再扫网格
# （282 场窗口已扫过 15 组网格，任何再选择都是 EVAL-peek；取最初命名规则 48.0%/1.88x）
DRAW_FLAG_SPREAD_MAX = 0.07  # 融合(Stacking)三向概率 max−min 上限
DRAW_FLAG_P_DRAW_MIN = 0.30  # 融合平局概率下限

# C-20261014-086 文案逐字冻结（语义=不确定度旗标；禁止引入「子模型一致性/平局预测」措辞）
DRAW_FLAG_TEXT = (
    "平局高危旗标（实验性·待 fresh 验证）：融合三向概率接近、方向高度不明朗，"
    "且融合平局概率偏高；样本内同构 25 场平局率 48%（基线 25.5%），误报含大比分场次"
    "——语义为「结果高度不确定、平局概率约五成」，不是「要出平局」。"
)


def compute_draw_high_risk_flag(hp, dp, ap):
    """C-20261014-086: 平局高危旗标判定（§十六风险标签 + §一小注 + 影子 jsonl 三处共用）。

    入参必须与报告 §一 Stacking 融合行同一对象（render_report 内 hp/dp/ap
    ← result["wdl"] 的 home_prob/draw_prob/away_prob），禁止另算或从 DB 重读，
    禁用子模型分歧度 std 口径——防「回测口径 vs serving 口径」分叉。
    概率缺失时返回 None（该场不写影子，等效旧行为）。
    """
    if not all(isinstance(v, (int, float)) for v in (hp, dp, ap)):
        return None
    spread = max(hp, dp, ap) - min(hp, dp, ap)
    return {
        "flag": bool(spread <= DRAW_FLAG_SPREAD_MAX and dp >= DRAW_FLAG_P_DRAW_MIN),
        "spread": round(float(spread), 4),
        "p_draw": round(float(dp), 4),
    }


def draw_flag_enabled():
    """C-20261014-086: 读 config.yaml report_flags.draw_high_risk.enabled（默认 false；读取异常=关）。

    开关只管报告渲染，不管影子——影子 jsonl 恒开。
    """
    try:
        import yaml
        cfg = yaml.safe_load((BASE_DIR / "config.yaml").read_text(encoding="utf-8")) or {}
        return bool(((cfg.get("report_flags") or {}).get("draw_high_risk") or {}).get("enabled", False))
    except Exception:
        return False


def append_draw_flag_shadow(record):
    """C-20261014-086: 追加平局高危旗标 Shadow 到 reports/draw_flag_shadow.jsonl（按 match_id 去重覆盖）。

    恒开（不受 report_flags.draw_high_risk.enabled 影响），每场必写一行含 flag=false
    （覆盖率门禁分母）；赛前生成、不含实际比分，赛后按 match_id JOIN 实际比分做
    fresh 三门禁评估（①n_flag≥45 且命中率≥40% 且 CI 下限>25.5% 基线；②覆盖率≤12%；
    ③旗标场 ≥3 球差比分占比不高于非旗标场），全过由人工评审把 enabled 翻 true。
    """
    path = BASE_DIR / "reports" / "draw_flag_shadow.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    kept = []
    if path.exists():
        for ln in path.read_text(encoding="utf-8").splitlines():
            try:
                if json.loads(ln).get("match_id") != record["match_id"]:
                    kept.append(ln)
            except json.JSONDecodeError:
                continue
    kept.append(json.dumps(record, ensure_ascii=False))
    path.write_text("\n".join(kept) + "\n", encoding="utf-8")


# ============================================================
# 500.com / SofaScore 数据
# ============================================================
def _augment_ouzhi(row, companies):
    """P0-05: 修 500 百家欧指聚合 None。

    odds500_ouzhi_summary 聚合字段可能为 NULL（采集遗漏/空壳行），但 odds500_ouzhi_company
    明细已存在。此时从明细重算 company_count / 平均赔率 / 返还率 / 去水隐含概率，禁止在
    报告里返回 None 冒充「无数据」。离散值（disp）因 500.com 公式不明，宁可留空也不编造。
    """
    out = dict(row) if row is not None else {}
    # 已有非空聚合（company_count 有值）→ 直接信任现有摘要
    if out.get("company_count") not in (None, "", 0, "0"):
        return out

    if not companies:
        return out if out else None

    wins, draws, loses = [], [], []
    for c in companies:
        w, dr, l = num(c["live_win"]), num(c["live_draw"]), num(c["live_lose"])
        if w and dr and l and w > 1:
            wins.append(w)
            draws.append(dr)
            loses.append(l)
    if not wins:
        return out if out else None

    n = len(wins)
    avg_w, avg_d, avg_l = sum(wins) / n, sum(draws) / n, sum(loses) / n
    ph, pd, pa, ret = odds_to_implied(avg_w, avg_d, avg_l)

    out["company_count"] = n
    out["avg_live_win"] = f"{avg_w:.2f}"
    out["avg_live_draw"] = f"{avg_d:.2f}"
    out["avg_live_lose"] = f"{avg_l:.2f}"
    # 返还率存纯数值（不带 %），供 fnum 正常渲染
    out["avg_return_live"] = f"{ret * 100:.2f}" if ret else None
    out["avg_prob_live_win"] = f"{ph * 100:.2f}%" if ph else None
    out["avg_prob_live_draw"] = f"{pd * 100:.2f}%" if pd else None
    out["avg_prob_live_lose"] = f"{pa * 100:.2f}%" if pa else None
    return out


def load_500_data(conn, fid):
    d = {"betting": None, "ouzhi": None, "companies": []}
    if not fid:
        return d
    d["betting"] = conn.execute("SELECT * FROM odds500_betting WHERE fid=?", (fid,)).fetchone()
    ouzhi = conn.execute("SELECT * FROM odds500_ouzhi_summary WHERE fid=?", (fid,)).fetchone()
    companies = conn.execute(
        "SELECT company, live_win, live_draw, live_lose, init_win, init_draw, init_lose "
        "FROM odds500_ouzhi_company WHERE fid=? ORDER BY seq", (fid,)
    ).fetchall()
    d["companies"] = companies
    # P0-05：聚合 NULL 但明细存在时，从明细重算聚合
    d["ouzhi"] = _augment_ouzhi(ouzhi, companies)
    return d


# C-20261016-124: SofaScore 采集端自有译名与 team_names 规范名存在少量不一致
# （如 sofa 入库「弗洛西诺内」，规范名/竞彩名「弗罗西诺内」）。normalize_team_name
# 路径 1（ALIASES 反向→规范名）优先命中会遮蔽 feature_utils 本地 CN 别名映射，
# 故查询前显式换用 sofa 侧译名候选。不动物理表、不动规范名（score_history 口径）。
SOFA_NAME_OVERRIDES = {
    '弗罗西诺内': '弗洛西诺内',
}


def load_sofascore_data(conn, match_date, home_cn):
    """按日期 + 主队中文名匹配 SofaScore 特征（客队可能是英文混合名）。

    凌晨场（北京时间 02:30~03:00）在 SofaScore 数据源常按当地日期归为前一天，
    与 500.com/竞彩的北京时间日期存在 ±1 天错位，故做日期容差回退（与
    find_sporttery_match_id 的日期容差思路一致）。
    """
    # 队名归一化：避免「云达不莱梅/云达不来梅」这类别名导致匹配失败
    home_norm = _normalize_team_name(home_cn)
    # C-20261016-124: 候选名 = 竞彩原名 / 规范名 / sofa 侧译名，去重保序
    name_candidates = [home_cn, home_norm]
    _sofa_alt = SOFA_NAME_OVERRIDES.get(home_norm) or SOFA_NAME_OVERRIDES.get(home_cn)
    if _sofa_alt and _sofa_alt not in name_candidates:
        name_candidates.append(_sofa_alt)
    base = datetime.strptime(match_date, "%Y-%m-%d")
    # 按 |diff| 递增生成候选日期（0, ±1, ±2, ±3 天），与 find_sporttery_match_id
    # 的 ±3 天口径对齐；同队一周内不会打两次，±3 天范围内只会命中一场。
    date_candidates = [match_date]
    for delta in range(1, 4):
        date_candidates.append((base + timedelta(days=-delta)).strftime("%Y-%m-%d"))
        date_candidates.append((base + timedelta(days=delta)).strftime("%Y-%m-%d"))

    for d in date_candidates:
        placeholders = ",".join("?" for _ in name_candidates)
        row = conn.execute(
            f"SELECT * FROM sofascore_team_features WHERE match_date=? AND home_team_cn IN ({placeholders})",
            (d, *name_candidates),
        ).fetchone()
        if row is None:
            # 兜底：候选名逐个模糊匹配
            for cand in name_candidates:
                row = conn.execute(
                    "SELECT * FROM sofascore_team_features WHERE match_date=? AND home_team_cn LIKE ?",
                    (d, f"%{cand}%"),
                ).fetchone()
                if row is not None:
                    break
        if row is not None:
            return row
    return None


# ============================================================
# 报告渲染
# ============================================================
def _sf(row, field, nd=2):
    """读取 SofaScore 特征字段，缺失返回 None。

    P0-03: 采集端缺失时以 0 或 NULL 占位（如 pa_fatigue_7d、sofa_rat_5g、sofa_pass_sr_5g），
    这里统一把 0 归为缺失返回 None，避免报告显示 0.00% 冒充真实数据。

    C-20261014-090: 同时把 PA 无数据哨兵 -1.0 视为缺失（与 pa_features.no_data_sentinel 配套；
    §十一/§十二 经 _sf 渲染的字段均非负，-1.0 不可能是真值，故该判定为纯增量、无回归）。
    """
    if row is None:
        return None
    if field in row.keys():
        v = row[field]
    else:
        # C-20260923-056 防复发：字段名不在 schema 中可能 typo，记日志而非静默返 None
        print(f"[WARN] generate_unified_report._sf: 字段 '{field}' 不在 row.keys() 中，可能字段名 typo 或 schema 变更", file=sys.stderr)
        v = None
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if fv == 0.0 or fv == -1.0:  # 采集端缺失占位 / PA 无数据哨兵
        return None
    return round(fv, nd)


def _sf_pct(row, field, min_ratio=None):
    """SofaScore 比率字段 → 百分比文本；缺失或低于合理下限时返回统一未采集标记（避免 None*100 崩溃 / 0.00% / 脏数据 1.0%）。"""
    v = _sf(row, field, 2)
    if v is None:
        return MISSING
    if min_ratio is not None and v < min_ratio:
        return MISSING
    return f"{v * 100:.1f}%"


# C-20260923-058 全库防复发：集中式 sofascore schema 校验
# 背景：C-056/C-057 仅覆盖 §十七之三 _keys（4 字段）和 _sf 调用链；本层把
# generate_unified_report.py 里所有硬编码访问 sofascore 字段的地方（_sf/_sf_pct
# 调用、直接 sofa_row[...] 下标、_sofa_pre[...] 关联）统一纳入 schema 校验。
# audit_sofascore_schema(row) 返回不在 row.keys() 中的字段名列表；主循环每场
# 拿到 extra["sofascore"] 后调用一次，缺失即 print [WARN] 到 stderr（非阻断）。
# 该层为补充层，不替代 §十七之三 _missing_in_schema 与 _sf 的 warning。
SOFA_AUDIT_FIELDS = frozenset({
    # §十七之三 数据质量清单 _keys（L1170）
    "sofa_rat_5g_home", "sofa_rat_5g_away",
    "sofa_xg_5g_home", "sofa_xg_5g_away",
    # §十一 近期状态与攻防（L1781-1783）
    "sofa_pass_sr_5g_home", "sofa_pass_sr_5g_away",
    # §十二 球员与阵容分析（L1807-1821）
    "pa_xi_rating_home", "pa_xi_rating_away",
    "pa_xi_xg_home", "pa_xi_xg_away",
    "pa_core_xg_share_home", "pa_core_xg_share_away",
    "pa_availability_home", "pa_availability_away",
    "pa_missing_impact_home", "pa_missing_impact_away",
    "pa_fatigue_7d_home", "pa_fatigue_7d_away",
    "pa_rest_days_home", "pa_rest_days_away",
    "pa_squad_stability_home", "pa_squad_stability_away",
    # event_id 关联 fbref_match_mapping（L1098-1099）
    "event_id",
})


def audit_sofascore_schema(row):
    """返回 SOFA_AUDIT_FIELDS 中不在 row.keys() 的字段名列表（空列表表示 schema 对齐）。

    C-20260923-058：与 §十七之三 _missing_in_schema 同口径，但覆盖全库所有硬编码
    访问的 sofascore 字段。row 为 None 时返回空列表（调用方已对 None 做分支）。
    """
    if row is None:
        return []
    try:
        row_keys = set(row.keys())
    except AttributeError:
        # 非 sqlite3.Row/dict（无 keys()）：保守视为全缺失
        return sorted(SOFA_AUDIT_FIELDS)
    return sorted(f for f in SOFA_AUDIT_FIELDS if f not in row_keys)


# ============================================================
# 联赛积分榜 & 战意（从 matches 表现算，无需新增爬虫）
# ============================================================
def compute_league_standings(conn, league, as_of_date):
    """从 matches 表计算某联赛“当前赛季”的积分榜（标准中文队名，截止 as_of_date）。

    仅统计本赛季（欧洲五大联赛：上/下半年分界，赛季于 7-1 起算）内的完赛场次，
    避免把跨 10 季的历史赛果累计进来。返回按 (积分, 净胜球, 进球) 降序的列表。
    """
    d = datetime.strptime(as_of_date, "%Y-%m-%d")
    season_year = d.year if d.month > 6 else d.year - 1
    season_start = f"{season_year}-07-01"
    rows = conn.execute(
        "SELECT home_team, away_team, home_goals, away_goals FROM matches "
        "WHERE league=? AND match_date < ? AND match_date >= ? "
        "AND home_goals IS NOT NULL AND away_goals IS NOT NULL",
        (league, as_of_date, season_start),
    ).fetchall()

    stats = {}
    for r in rows:
        h = _normalize_team_name(r["home_team"]) or str(r["home_team"])
        a = _normalize_team_name(r["away_team"]) or str(r["away_team"])
        try:
            hg, ag = int(r["home_goals"]), int(r["away_goals"])
        except (TypeError, ValueError):
            continue
        for team, gf, ga in ((h, hg, ag), (a, ag, hg)):
            s = stats.setdefault(team, {"played": 0, "win": 0, "draw": 0, "loss": 0,
                                        "gf": 0, "ga": 0, "pts": 0})
            s["played"] += 1
            s["gf"] += gf
            s["ga"] += ga
            if gf > ga:
                s["win"] += 1
                s["pts"] += 3
            elif gf == ga:
                s["draw"] += 1
                s["pts"] += 1
            else:
                s["loss"] += 1

    table = []
    for team, s in stats.items():
        s["team"] = team
        s["gd"] = s["gf"] - s["ga"]
        table.append(s)

    table.sort(key=lambda x: (x["pts"], x["gd"], x["gf"]), reverse=True)
    for i, s in enumerate(table):
        s["rank"] = i + 1
    return table


def _find_standing(table, team_cn):
    """在积分榜中定位某队（先归一化，未命中则精确比对）。"""
    team_norm = _normalize_team_name(team_cn) or str(team_cn)
    for s in table:
        if s["team"] == team_norm:
            return s
    for s in table:
        if s["team"] == str(team_cn):
            return s
    return None


def load_sofa_standings(conn, league):
    """读 SofaScore 权威积分榜快照（league_standings 表最新 snapshot_date）。C-20261016-120。

    行结构与 compute_league_standings 输出对齐（team=归一中文），供 §十四 优先使用；
    表不存在/无快照返回 ([], None)，调用方回退 matches 现算。collector=
    collection/collect_sofascore_standings.py（每日 20:30 链刷新）。
    """
    if conn is None:
        return [], None
    try:
        row = conn.execute(
            "SELECT MAX(snapshot_date) FROM league_standings WHERE league=?", (league,)).fetchone()
        snap = row[0] if row else None
        if not snap:
            return [], None
        rows = conn.execute(
            "SELECT team_cn, position, points, played, wins, draws, losses, gf, ga, gd "
            "FROM league_standings WHERE league=? AND snapshot_date=? ORDER BY position",
            (league, snap)).fetchall()
        table = []
        for r in rows:
            if not r["team_cn"]:
                continue
            table.append({
                "team": r["team_cn"], "rank": r["position"] or 0,
                "pts": r["points"] or 0, "played": r["played"] or 0,
                "win": r["wins"] or 0, "draw": r["draws"] or 0, "loss": r["losses"] or 0,
                "gf": r["gf"] or 0, "ga": r["ga"] or 0, "gd": r["gd"] or 0,
            })
        return table, snap
    except sqlite3.OperationalError:  # 表不存在（旧库/未建）
        return [], None


# ============================================================
# P2-05 修正: 500.com 投注分析 - 空壳行检测
# ============================================================
def betting_has_real_data(betting):
    """判断 betting 行是否含真实投注数据，而非空壳（全 "-"）。

    依据：主/平/客任一方的赔率/概率/必发成交比/成交额/庄家盈亏任一字段
    为非 "-" 空值即视为有数据。空壳数据通常源于 500.com EdgeOne 反爬拦截
    （crawl_status=anti_bot_blocked 或 旧数据 ok+全空），而非源站无数据。
    兼容 dict 和 sqlite3.Row 对象。
    """
    if not betting:
        return False

    def _get(obj, key):
        if hasattr(obj, "get"):
            return obj.get(key)
        # sqlite3.Row
        try:
            return obj[key]
        except (KeyError, IndexError):
            return None

    sides = ("home", "draw", "away")
    fields = ("odds", "prob", "bf_ratio", "bf_volume", "bf_profit")
    for s in sides:
        for f in fields:
            key = f"{s}_{f}"
            v = str(_get(betting, key) or "").strip()
            if v and v not in ("-", "—", "0", "None"):
                # 概率字段含 "%" 才是真数据
                if f == "prob" and "%" in v and v.strip("%").strip() not in ("-", ""):
                    return True
                elif f != "prob" and v not in ("-", "—", "None"):
                    return True
    return False


def _betting_status_label(betting):
    """给 build_log.sections 用的投注数据状态标签。

    返回: ok / anti_bot / degraded / missing
    兼容 sqlite3.Row 和 dict。
    """
    if not betting:
        return "missing"
    if betting_has_real_data(betting):
        return "ok"
    # 读 crawl_status
    if hasattr(betting, "get"):
        s = betting.get("crawl_status")
    else:
        try:
            s = betting["crawl_status"]
        except (KeyError, IndexError):
            s = None
    if s == "anti_bot_blocked":
        return "anti_bot"
    if s == "source_no_data":
        return "degraded"
    if s == "crawl_parse_failed":
        return "degraded"
    # 旧数据 ok+空壳：也视为降级（实质是反爬拦截空壳）
    return "degraded"


# ============================================================
# P2-05: 历史交锋聚合（基于 matches 表 + 中文名归一化 key）
# ============================================================
def compute_head_to_head(conn, league, home_cn, away_cn, match_date, limit=10):
    """返回两队历史交锋列表（按日期倒序，不含本场），以及汇总统计 dict。

    返回: (rows, summary)  summary = {home_win, draw, away_win, total}
    rows 每行包含 date, home_team, away_team, home_goals, away_goals, is_home_side
    """
    if not home_cn or not away_cn or not conn:
        return [], {"home_win": 0, "draw": 0, "away_win": 0, "total": 0}
    home_key = _normalize_team_name(home_cn) or str(home_cn)
    away_key = _normalize_team_name(away_cn) or str(away_cn)
    if home_key == away_key:
        return [], {"home_win": 0, "draw": 0, "away_win": 0, "total": 0}

    rows = conn.execute(
        "SELECT match_date, home_team, away_team, home_goals, away_goals FROM matches "
        "WHERE league=? AND match_date < ? "
        "AND home_goals IS NOT NULL AND away_goals IS NOT NULL "
        "ORDER BY match_date DESC",
        (league, match_date),
    ).fetchall()

    h2h = []
    for r in rows:
        h = _normalize_team_name(r["home_team"]) or str(r["home_team"])
        a = _normalize_team_name(r["away_team"]) or str(r["away_team"])
        if (h == home_key and a == away_key) or (h == away_key and a == home_key):
            is_home_side = (h == home_key)
            h2h.append({
                "date": r["match_date"],
                "home_team": r["home_team"],
                "away_team": r["away_team"],
                "home_goals": int(r["home_goals"]),
                "away_goals": int(r["away_goals"]),
                "is_home_side": is_home_side,
            })
            if len(h2h) >= limit:
                break

    hw = d = aw = 0
    for r in h2h:
        if r["home_goals"] > r["away_goals"]:
            if r["is_home_side"]:
                hw += 1
            else:
                aw += 1
        elif r["home_goals"] < r["away_goals"]:
            if r["is_home_side"]:
                aw += 1
            else:
                hw += 1
        else:
            d += 1
    return h2h, {"home_win": hw, "draw": d, "away_win": aw, "total": len(h2h)}


def classify_zhan_yi(rank, total_teams):
    """把排名映射为战意标签（五大联赛欧冠/欧战名额近似启发式）。"""
    if rank is None or total_teams is None or total_teams <= 0:
        return "—"
    if rank == 1:
        return "争冠"
    if rank <= 3:
        return "争欧冠"
    if rank <= 6:
        return "争欧战"
    if rank <= total_teams - 3:
        return "中游"
    if rank <= total_teams - 1:
        return "保级"
    return "降级区"


# 五大联赛参赛队数（用于判断积分榜是否完整）
EXPECTED_TEAMS = {"英超": 20, "西甲": 20, "意甲": 20, "德甲": 18, "法甲": 18}


# C-20260930-002 Fix A：§十七之三 模块名 → prematch_data_quality 列名
_PDQUALITY_STATUS_COLS = {
    "竞彩时序赔率": "sporttery_status",
    "500.com 百家欧指": "ouzhi_status",
    "500.com 投注分析": "betting_status",
    "SofaScore 赛前特征": "sofascore_status",
}


def _persist_prematch_quality(conn, m, match_date, build_log, quality_items, wdl_snap,
                              report_path, wdl_not_offered=False, betting_crawl_status=None):
    """把三通道模块状态追加写入 prematch_data_quality（append 历史，不覆盖）。

    归因引擎权威赛前口径源；任何异常仅 stderr 告警，绝不阻断报告生成
    （与 build_log 落盘同等级隔离）。
    """
    if conn is None:
        return
    try:
        from post_match_schema import insert_prematch_data_quality  # 懒加载隔离
        statuses = {}
        for _name, _status, _detail in quality_items:
            _col = _PDQUALITY_STATUS_COLS.get(_name)
            if _col:
                statuses[_col] = _status
        try:
            fid = int(m.get("fid"))
        except (TypeError, ValueError):
            fid = None
        try:
            rel = report_path.resolve().relative_to(BASE_DIR).as_posix()
        except Exception:
            rel = str(report_path)
        insert_prematch_data_quality(conn, {
            "match_id": build_log["match_id"],
            "fid": fid,
            "match_date": match_date,
            "generated_at": build_log["generated_at"],
            "completeness": build_log["completeness"],
            "wdl_snapshots": int(wdl_snap or 0),
            "wdl_not_offered": 1 if wdl_not_offered else 0,
            "sporttery_status": statuses.get("sporttery_status"),
            "ouzhi_status": statuses.get("ouzhi_status"),
            "betting_status": statuses.get("betting_status"),
            "betting_crawl_status": betting_crawl_status,
            "sofascore_status": statuses.get("sofascore_status"),
            "modules_json": [{"name": n, "status": s, "detail": d}
                             for n, s, d in quality_items],
            "report_file": rel,
            "model_version": build_log.get("model_version"),
            "feature_version": build_log.get("feature_version"),
        })
    except Exception as _e:  # noqa: BLE001
        print(f"  ⚠️ 写入 prematch_data_quality 失败: {_e}", file=sys.stderr)


def render_report(m, odds_data, result, extra, report_path, conn=None, is_replay=False):
    """按 18 章模板渲染单场报告。m=比赛元数据, result=四维预测, extra={500,sofascore}。"""
    home_cn, away_cn = m["home_team_cn"], m["away_team_cn"]
    home_en, away_en = m["home_team_en"], m["away_team_en"]
    league = m["league"]
    match_date = m["match_date"]
    match_time = m["match_time"]
    round_no = m["round"]

    # 联赛积分榜 + 战意（赛前口径，截止 match_date 之前的完赛场次）
    standings_derived = compute_league_standings(conn, league, match_date) if conn is not None else []
    # C-20261016-120: SofaScore 权威积分榜快照优先（matches 赛果库断更时排名/战绩失真，
    # 如勒芒「已赛2」vs 真实 6）；无快照回退 matches 现算。derived 保留供 §十四 对拍。
    standings, sofa_snap = load_sofa_standings(conn, league)
    if not standings:
        standings, sofa_snap = standings_derived, None
    home_std = _find_standing(standings, home_cn)
    away_std = _find_standing(standings, away_cn)
    total_teams = len(standings)
    # 积分榜是否完整：完赛记录覆盖了绝大多数参赛队（数据采集滞后时会显著偏少）
    standings_ok = total_teams >= (EXPECTED_TEAMS.get(league, 20) - 2)

    # P2-05: 历史交锋聚合（matches 表 + 中文名归一化）
    h2h_rows, h2h_summary = compute_head_to_head(
        conn, league, home_cn, away_cn, match_date) if conn is not None else ([], {})

    wdl = result.get("wdl") or {}
    hcp = result.get("hcp") or {}
    score = result.get("score") or {}
    tg = result.get("tg") or {}

    hp, dp, ap = wdl.get("home_prob"), wdl.get("draw_prob"), wdl.get("away_prob")
    wdl_pred = wdl.get("prediction", "—")
    wdl_method = wdl.get("method", "—")
    # P1-02: 5 子模型原始输出 + 分歧度
    sub_models = wdl.get("sub_models") or {}
    divergence_level, divergence_std, n_sub_models = _sub_model_divergence(sub_models)

    # C-20261014-086: 平局高危旗标（与 §一 Stacking 融合行同源 hp/dp/ap；开关只管渲染，影子恒开）
    draw_flag = compute_draw_high_risk_flag(hp, dp, ap)
    draw_flag_on = draw_flag_enabled()

    # 竞彩 WDL 赔率（尾盘/初盘）
    wdl_close = wdl.get("close") or {}
    wdl_open = wdl.get("open") or {}
    oc_h = wdl_close.get("win") or wdl_open.get("win")
    oc_d = wdl_close.get("draw") or wdl_open.get("draw")
    oc_a = wdl_close.get("lose") or wdl_open.get("lose")

    ih_h, ih_d, ih_a, wdl_return = odds_to_implied(oc_h, oc_d, oc_a)

    # 让球
    # C-20261013-068: hcp_line_disp 改用竞彩 SSOT（hcp["line"]，C-067 已切换），
    # 不再读 m["handicap"]（亚盘 0.25 档中文串，来自 odds500_match.handicap）。
    # 全链路统一线源：§一/§三/§7.2/§十四 共用 format_hcp_label。
    hcp_line_raw = hcp.get("line")
    hcp_line_disp = format_hcp_label(hcp_line_raw)
    hcp_pred = hcp.get("prediction", "—")
    hcp_upper = hcp.get("home_win_prob")
    hcp_draw = hcp.get("draw_prob")
    hcp_lower = hcp.get("away_win_prob")
    hcp_method = hcp.get("method", "—")

    # 比分
    top_scores = score.get("top5") or []
    lambda_h = score.get("lambda_home")
    lambda_a = score.get("lambda_away")
    score_method = score.get("method", "—")
    score_reorder_note = score.get("reorder_note")  # C-20261011-041: WDL 方向族重排注记
    lambda_alert = result.get("lambda_alert")  # P1-13: λ 主客差值告警

    # 总进球（C-20260921-035：结论=精确进球数 Top1/Top3，不再出大小球结论）
    tg_pred = tg.get("prediction", "—")
    tg_over = tg.get("over_25_prob")
    tg_under = (1 - tg_over) if tg_over is not None else None
    tg_goals = tg.get("goals") or {}
    tg_top3 = tg.get("top3") or []
    tg_method = tg.get("method", "—")

    # 数据完整度：三通道各 1/3；竞彩时序按快照数分档（0 / 1 / ≥2）
    wdl_snap = int(result.get("_wdl_snapshots") or 0)
    if wdl_snap >= 2:
        comp_odds = 1.0
    elif wdl_snap == 1:
        comp_odds = 0.5  # 仅单条快照，缺少开盘→即时漂移信息
    else:
        comp_odds = 0.0
    # 500.com 完整度须验证「存在行且含真实赔率数据」，而非仅判断行是否存在。
    # 空壳行（company_count=0 且无公司明细）应计为缺失，避免掩盖采集失败。
    _ouzhi = extra["500"]["ouzhi"]
    if _ouzhi is not None and not isinstance(_ouzhi, dict):
        _ouzhi = dict(_ouzhi)  # C-20261016-114: sqlite3.Row → dict，供 .get() 访问（§八 欧指展示）
    _has_real_500 = _ouzhi is not None and (
        int(_ouzhi["company_count"] or 0) > 0 or len(extra["500"]["companies"]) > 0
    )
    comp_500 = 1.0 if _has_real_500 else 0.0
    comp_sofa = 1.0 if extra["sofascore"] is not None else 0.0
    completeness = int(round((comp_odds + comp_500 + comp_sofa) / 3 * 100))

    # ===== P2-04: 跨数据源冲突检测（SofaScore 基本面 vs 500 市场信号） =====
    _conflict_result = None  # ConflictResult or None
    if _CONFLICT_DETECTOR_AVAILABLE and extra["sofascore"] is not None and _has_real_500:
        try:
            sofa_row = extra["sofascore"]
            # 近 5 场主客评分（C-20260923-058：删除死回退 sofa_rat_home/away，该字段已下线；缺失时 if rh and ra 不通过，显式跳过冲突检测）
            rh = sofa_row["sofa_rat_5g_home"]
            ra = sofa_row["sofa_rat_5g_away"]
            if rh and ra and _ouzhi["avg_live_win"] and _ouzhi["avg_live_draw"] and _ouzhi["avg_live_lose"]:
                fund_p = _fundamental_home_prob(float(rh), float(ra))
                mkt_p = _market_home_prob(
                    float(_ouzhi["avg_live_win"]),
                    float(_ouzhi["avg_live_draw"]),
                    float(_ouzhi["avg_live_lose"]))
                _conflict_result = _detect_conflict(fund_p, mkt_p)
        except Exception:
            _conflict_result = None  # 冲突检测异常不阻断报告

    # P0-01/P1-01: 综合置信度拆解（基准 + 加分 - 扣分项），供摘要卡与拆解表使用。
    # divergence_std/n_sub_models 已在上方 P1-02 处算出；wdl_snap 已在数据完整度处算出。
    # P2-04 新增：若检测到跨源冲突，按 conflict_score 额外扣置信度（最多 -8 分）。
    _conflict_penalty = 0.0
    if _conflict_result is not None and _conflict_result.is_conflict:
        _conflict_penalty = round(min(8.0, _conflict_result.conflict_score * 16), 1)
    _conf_breakdown = compute_confidence_breakdown(
        completeness, wdl.get("confidence"),
        divergence_std=divergence_std, n_sub=n_sub_models, wdl_snap=wdl_snap,
        extra_penalty=_conflict_penalty, wdl_source=result.get("_wdl_source", "jc"))
    conf_score = _conf_breakdown["score"]

    # 官方伤停名单（match_missing_players 赛前采集行，经 event_id 精确关联）。
    # 提前到质检清单构建之前查询，供 §十二/§十六/§十七之三 三处复用，避免文案自相矛盾。
    om_home: list = []
    om_away: list = []
    om_names_home: set = set()   # C-20261014-092: 官方缺阵原始球员名（英文），供校正推算首发
    om_names_away: set = set()
    _home_fbref = None
    _away_fbref = None
    _event_id = None
    _sofa_pre = extra["sofascore"]
    if _sofa_pre is not None and "event_id" in _sofa_pre.keys():
        _event_id = _sofa_pre["event_id"]
    if _event_id and conn is not None:
        try:
            _mrow = conn.execute(
                "SELECT home_team_cn, away_team_cn, home_team_fbref, away_team_fbref "
                "FROM fbref_match_mapping WHERE fbref_match_id=?",
                (str(_event_id),)).fetchone()
            if _mrow:
                _home_fbref = _mrow["home_team_fbref"]
                _away_fbref = _mrow["away_team_fbref"]
                for _team_en, _bucket, _names in ((_mrow["home_team_cn"], om_home, om_names_home),
                                                  (_mrow["away_team_cn"], om_away, om_names_away)):
                    if not _team_en:
                        continue
                    for _r in conn.execute(
                            "SELECT player_name, reason, expected_end_date FROM match_missing_players "
                            "WHERE fbref_match_id=? AND team=? AND reason!='转会' ORDER BY player_name",
                            (str(_event_id), _team_en)).fetchall():
                        _ret = (_r["expected_end_date"] or "").strip()
                        _bucket.append(f"{_r['player_name']}({_r['reason']}"
                                       + (f",复出{_ret[5:]}" if _ret else "") + ")")
                        _names.add(_r["player_name"])
        except Exception:
            pass  # 伤停展示失败不阻塞报告

    # C-20261014-092: 官方缺阵校正推算首发（仅报告展示层；不改 pa_* 模型特征值，防 train/serve 分叉）。
    # 校正口径：predict_xi_players（纯历史推算，数据严格早于 match_date）∩ match_missing_players
    # 官方缺阵（该场赛前采集）→ confirmed_out = 推算首发中被官方确认缺阵的球员。
    # 报告均为赛前生成（含 replay 复现赛前口径），天然满足「仅未赛场次」；已赛场次的 replay
    # 展示的仍是该场赛前快照，口径一致。模型特征/λ 不读 match_missing_players → 无重复计数。
    _official_corr: dict = {}
    if (om_names_home or om_names_away) and conn is not None:
        try:
            if str(BASE_DIR / "features") not in sys.path:
                sys.path.insert(0, str(BASE_DIR / "features"))
            from player_availability_features import PlayerAvailabilityFeatures
            from player_injury_source import apply_official_injuries
            _pa_gen = PlayerAvailabilityFeatures(db_path=ODDS_DB)
            try:
                for _side, _tfb, _names in (("home", _home_fbref, om_names_home),
                                            ("away", _away_fbref, om_names_away)):
                    if not _tfb or not _names:
                        continue
                    _pa_gen.ensure_team_stats(_tfb)
                    _xi = _pa_gen.predict_xi_players(_tfb, match_date)
                    if not _xi:
                        _official_corr[_side] = {"unavailable": True, "official_n": len(_names)}
                        continue
                    _res = apply_official_injuries(_xi, sorted(_names))
                    _res["official_n"] = len(_names)
                    _official_corr[_side] = _res
            finally:
                _pa_gen.close()
        except Exception:
            _official_corr = {}  # 校正失败不阻塞报告

    # ===== P0-03 遗留: 数据质量清单（逐模块状态，报告可视化）=====
    # 每个模块：name / status (OK/WARN/FAIL) / detail
    _quality_items = []

    # 1. 竞彩时序赔率（C-20261002-005：按 WDL 来源三态分档）
    _wdl_src = result.get("_wdl_source", "jc")
    if _wdl_src == "fallback_deep":
        _quality_items.append(("竞彩时序赔率", "WARN", "未开售WDL，让球/大小/比分已接入（500兜底）"))
    elif _wdl_src == "fallback_none":
        _quality_items.append(("竞彩时序赔率", "WARN", "未开售本场四玩法（500兜底，非采集缺失）"))
    elif wdl_snap >= 2:
        _quality_items.append(("竞彩时序赔率", "OK", f"≥{wdl_snap}条快照（含开盘/即时）"))
    elif wdl_snap == 1:
        _quality_items.append(("竞彩时序赔率", "WARN", "仅1条快照（缺漂移分析）"))
    else:
        _quality_items.append(("竞彩时序赔率", "FAIL", "无快照"))

    # 2. 500 百家欧指
    if _has_real_500:
        _cnt = int(_ouzhi["company_count"] or 0) or len(extra["500"]["companies"])
        _quality_items.append(("500.com 百家欧指", "OK", f"{_cnt}家公司"))
    else:
        _quality_items.append(("500.com 百家欧指", "FAIL", "未采集/空壳"))

    # 3. 500 投注分析（必发/冷热）
    _betting = extra["500"]["betting"]
    if _betting is not None and not isinstance(_betting, dict):
        _betting = dict(_betting)  # C-20261016-114: sqlite3.Row → dict，供 .get() 访问（§九 质检三态）
    _bet_crawl = _betting["crawl_status"] if _betting is not None else None
    if _bet_crawl == "ok" and betting_has_real_data(_betting):
        # C-20261014-100 三态修正：投注比例/欧指存在 ≠ 必发接入；
        # 必发缺失单独 WARN（此前误标 OK "必发/冷热数据已接入" → 清单失真、置信度虚高）
        _bet_has_bf = any(_betting.get(f"{s}_bf_volume") and _betting[f"{s}_bf_volume"] not in ("0", "", "None", "-")
                          for s in ("home", "draw", "away"))
        if _bet_has_bf:
            _quality_items.append(("500.com 投注分析", "OK", "必发/冷热数据已接入"))
        else:
            _quality_items.append(("500.com 投注分析", "WARN", "仅投注比例/欧指（必发/冷热缺失：源站未提供或解析缺口）"))
    elif _bet_crawl == "anti_bot_blocked":
        _quality_items.append(("500.com 投注分析", "FAIL", "受 500.com EdgeOne 反爬拦截（需刷新 Cookie 重采）"))
    elif _bet_crawl == "source_no_data":
        _quality_items.append(("500.com 投注分析", "WARN", "源站未提供该场投注数据"))
    elif _bet_crawl == "crawl_parse_failed":
        _quality_items.append(("500.com 投注分析", "FAIL", "采集/解析失败（需排查爬虫或反爬）"))
    elif _betting is None:
        _quality_items.append(("500.com 投注分析", "FAIL", "未采集"))
    elif _bet_crawl == "ok" and not betting_has_real_data(_betting):
        # 旧数据：crawl_status=ok 但全字段空壳 → 实质是反爬拦截
        _quality_items.append(("500.com 投注分析", "FAIL", "采集为空壳（疑似反爬拦截，需刷新 Cookie 重采）"))
    else:
        # 兼容旧数据（无 crawl_status 字段但有记录）
        _bet_has_data = any(_betting.get(f"{s}_bf_volume") and _betting[f"{s}_bf_volume"] not in ("0", "", "None")
                            for s in ("home", "draw", "away"))
        if _bet_has_data:
            _quality_items.append(("500.com 投注分析", "OK", "必发/冷热数据已接入（旧格式）"))
        else:
            _quality_items.append(("500.com 投注分析", "FAIL", "有记录但字段为空（疑似反爬拦截）"))

    # 4. SofaScore 赛前特征
    if extra["sofascore"] is not None:
        # 关键评分是否齐全
        sofa = extra["sofascore"]
        _keys = ["sofa_rat_5g_home", "sofa_rat_5g_away", "sofa_xg_5g_home", "sofa_xg_5g_away"]
        # C-20260923-056 防复发：schema 校验，_keys 字段名必须在 row.keys() 中
        _missing_in_schema = [k for k in _keys if k not in sofa.keys()]
        if _missing_in_schema:
            print(f"[WARN] generate_unified_report §十七之三: sofascore 数据质量检查字段名不在 schema 中 {_missing_in_schema}（可能 typo 或表结构变更）", file=sys.stderr)
        _filled = sum(1 for k in _keys if _sf(sofa, k) is not None)
        if _filled >= 3:
            _quality_items.append(("SofaScore 赛前特征", "OK", f"核心字段{_filled}/4"))
        else:
            _quality_items.append(("SofaScore 赛前特征", "WARN", f"核心字段{_filled}/4（可能为真实0或采集缺失）"))
    else:
        _quality_items.append(("SofaScore 赛前特征", "FAIL", "未匹配到"))

    # 5. 球员伤病/预计首发（官方缺阵名单 match_missing_players 赛前采集）
    if om_home or om_away:
        _n_om = len(om_home) + len(om_away)
        _quality_items.append(("SofaScore 球员伤病", "OK", f"官方缺阵名单已接入（{_n_om}人）"))
    else:
        _quality_items.append(("SofaScore 球员伤病", "WARN", "官方伤停未采集（靠历史推算）"))

    # 6. 跨数据源一致性
    if _conflict_result is None:
        _quality_items.append(("跨数据源一致性", "WARN", "数据不足，未检测"))
    elif _conflict_result.is_conflict:
        _quality_items.append(("跨数据源一致性", "WARN",
                               f"冲突分 {_conflict_result.conflict_score:.2f}（基本面 vs 市场分歧）"))
    else:
        _quality_items.append(("跨数据源一致性", "OK", "基本面与市场信号方向一致"))

    # 7. 联赛积分榜
    if standings_ok:
        _quality_items.append(("联赛积分榜", "OK", f"{total_teams}队"))
    else:
        _quality_items.append(("联赛积分榜", "WARN", f"仅{total_teams}队（数据滞后）"))

    # 凯利/价值
    kh = kelly(hp, oc_h)
    kd = kelly(dp, oc_d)
    ka = kelly(ap, oc_a)
    edge_h = (hp - ih_h) if (hp is not None and ih_h is not None) else None
    edge_d = (dp - ih_d) if (dp is not None and ih_d is not None) else None
    edge_a = (ap - ih_a) if (ap is not None and ih_a is not None) else None

    # 推荐：选价值空间最大的方向
    cands = [("主胜", hp, oc_h, edge_h, kh), ("平局", dp, oc_d, edge_d, kd), ("客胜", ap, oc_a, edge_a, ka)]
    cands = [c for c in cands if c[1] is not None]
    if cands:
        rec_dir, rec_mp, rec_odds, rec_edge, rec_k = max(
            cands, key=lambda c: (c[3] if c[3] is not None else -9))
    else:
        rec_dir, rec_mp, rec_odds, rec_edge, rec_k = "—", None, None, None, None
    rec_type = recommendation_type(rec_edge)
    # C-20261017-130: 子模型分歧闸（纯展示层，零模型改动，ev_engine 返回值不动）。
    # 5 子模型 argmax 计票，多数（≥3）方向与 meta（wdl_pred）不一致（4:1/3:1:1 等）时，
    # §一 推荐卡与 §6.2 EV 决策卡一律强制降级为「提示·分歧观望」，不出 VALUE 执行建议。
    # （C-20261018-138 起两卡均已退役，本闸转挂为「方向分歧提示」，变量仍用于分支标记。）
    # 依据 C-20261004-007：分歧信任规则已被证伪（n=77，方向规则 RPS 0.1912 输纯市场
    # 0.1831；δ*=0.06 信任规则仅噪声级微胜；w*=1.0），分歧场跟随模型无统计优势。
    # pass-through 语义（C-20261017-130 定案，7 场景单测覆盖）：无 ≥3 多数票（如 2:2:1）
    # 或可用子模型 <3 时不构成"meta 逆多数票"，一律不降级、按原推荐类型放行。
    _vote_cnt = {"主胜": 0, "平局": 0, "客胜": 0}
    for _vk, _vl in SUB_MODEL_LABELS:
        _vp = sub_models.get(_vk)
        if _vp and all(isinstance(_vp.get(k), (int, float)) for k in ("win", "draw", "lose")):
            _vd = "主胜" if _vp["win"] >= max(_vp["draw"], _vp["lose"]) else (
                "客胜" if _vp["lose"] >= _vp["draw"] else "平局")
            _vote_cnt[_vd] += 1
    _vote_total = sum(_vote_cnt.values())
    _maj_dir = max(_vote_cnt, key=lambda d: _vote_cnt[d]) if _vote_total >= 1 else None
    _maj_votes = _vote_cnt.get(_maj_dir, 0) if _maj_dir else 0
    _meta_votes = _vote_cnt.get(wdl_pred, 0)
    _meta_majority_disagree = bool(
        _vote_total >= 3 and _maj_votes >= 3 and _maj_dir != wdl_pred)
    if _meta_majority_disagree and rec_type not in ("NO_VALUE", "AVOID"):
        rec_type = "DISAGREE_WATCH"
    rec_implied = (1 / rec_odds) if rec_odds and rec_odds > 1 else None
    suggested_stake = max(0.0, (rec_k or 0) * 0.25) if rec_k is not None else 0.0

    # EV 期望值分析（第二层决策引擎 ev_engine；失败降级为上方简单凯利/价值分析）
    # P1-10: EV 阈值/凯利策略/仓位上限从 config.yaml 的 ev: 段集中读取（不再是硬编码）
    ev_analysis = None
    _ev_cfg = _ev_engine.load_ev_config() if _ev_engine is not None else {}
    ev_thr = _ev_cfg.get("ev_threshold_default", 0.02)
    ev_kelly_strat = _ev_cfg.get("kelly_default_strategy", "quarter")
    ev_kelly_cap = _ev_cfg.get("kelly_upper_cap", 0.25)
    if _ev_engine is not None and all(v is not None for v in (hp, dp, ap)) \
            and all(v is not None for v in (oc_h, oc_d, oc_a)):
        try:
            ev_probs = _ev_engine.ModelProbabilities(
                home=float(hp), draw=float(dp), away=float(ap), model_name=wdl_method)
            ev_odds = _ev_engine.OddsData(
                home=float(oc_h), draw=float(oc_d), away=float(oc_a), odds_type="close")
            if ev_probs.validate() and ev_odds.validate():
                # C-20260923: urs 投注质量过滤器（shadow mode，默认不实际过滤）
                _urs_val = None
                try:
                    _ur = _upset_engine.compute_upset_risk_score(
                        home_odds=float(oc_h), draw_odds=float(oc_d), away_odds=float(oc_a),
                        home_team=home_cn, away_team=away_cn, match_date=m["match_date"],
                    )
                    _urs_val = _ur.get("upset_risk_score")
                except Exception:
                    _urs_val = None
                # 漂移惩罚：投注方向的 signed drift (close-open)/open
                # 赔率源一致性：EV 用竞彩 wdl_close，漂移也用竞彩 wdl_open→close（同市场同口径）
                # 无竞彩时 drift_signed=None（不判定，不惩罚）
                _drift_signed = None
                # 先算 EV 分析（不带 drift），拿到 best_direction 后再算 drift
                ev_analysis = _ev_engine.analyze_match(
                    probs=ev_probs, odds=ev_odds,
                    ev_threshold=ev_thr, kelly_strategy=ev_kelly_strat, kelly_cap=ev_kelly_cap,
                    match_id=m.get("fid"), league=league,
                    home_team=home_cn, away_team=away_cn,
                    urs=_urs_val)
                # 有了 best_direction，算投注方向的 signed drift
                if ev_analysis.best_direction is not None and wdl_open.get("win") and wdl_close.get("win"):
                    _dir_map = {"home": ("win",), "draw": ("draw",), "away": ("lose",)}
                    _dir_key = _dir_map.get(ev_analysis.best_direction)
                    if _dir_key:
                        _ok = _dir_key[0]
                        _open_o = wdl_open.get(_ok)
                        _close_o = wdl_close.get(_ok)
                        if _open_o and _close_o and _open_o > 1:
                            _drift_signed = (_close_o - _open_o) / _open_o
                # 重新分析（带 drift_signed，shadow 记录用）
                if _drift_signed is not None:
                    ev_analysis = _ev_engine.analyze_match(
                        probs=ev_probs, odds=ev_odds,
                        ev_threshold=ev_thr, kelly_strategy=ev_kelly_strat, kelly_cap=ev_kelly_cap,
                        match_id=m.get("fid"), league=league,
                        home_team=home_cn, away_team=away_cn,
                        urs=_urs_val, drift_signed=_drift_signed)
        except Exception:
            ev_analysis = None
    result["ev_analysis"] = ev_analysis
    result["_ev_config"] = _ev_cfg

    # 赔率漂移
    drift_h = drift_d = drift_a = None
    if ih_h is not None and wdl_open.get("win") and wdl_close.get("win"):
        oi_h, oi_d, oi_a, _ = odds_to_implied(wdl_open["win"], wdl_open["draw"], wdl_open["lose"])
        drift_h = (ih_h - oi_h) * 100 if oi_h else None
        drift_d = (ih_d - oi_d) * 100 if oi_d else None
        drift_a = (ih_a - oi_a) * 100 if oi_a else None

    # 500.com
    ouzhi = extra["500"]["ouzhi"]
    betting = extra["500"]["betting"]
    companies = extra["500"]["companies"]

    L = []
    A = L.append

    # ================= 一、报告头信息 =================
    A("# 赛前预测报告 — {} vs {}".format(home_cn, away_cn))
    A("")
    A("> **报告版本**: {}".format(REPORT_VERSION))
    A("> **生成时间**: {}".format(datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S")))
    # C-20261016-118: 兜底场次反查 500 轮次失败时降级为「轮次未知」，不再显示「第None轮」
    A("> **比赛**: {} {}".format(league, f"第{round_no}轮" if round_no is not None else "轮次未知"))
    # C-20261016-116: 竞彩兜底场次无开赛时间（占位 12:00），命中真实 500 记录时
    # 用其开赛日期+时刻展示（与主源/主分支口径一致）；未命中仍回退 match_date+占位。
    A("> **时间**: {} (北京时间)".format(
        m.get("_kickoff500") or "{} {}".format(match_date, match_time)))
    A("> **对阵**: {} (主) vs {} (客)".format(home_cn, away_cn))
    if m.get("fid"):
        A("> **500.com**: fid={}".format(m["fid"]))
    A("> **数据完整度**: {}%".format(completeness))
    if is_replay:
        A("> ⚠️ **回放说明**: 本报告为赛后回放重生成（--replay），概率为当前模型重算值，"
          "非赛前发布锚点；赛前发布口径以 model_predictions 表为准")
    A("")
    A("---")
    A("")

    # ================= 二、核心预测结论 =================
    A("## 一、核心预测结论")
    A("")
    A("### 预测摘要卡")
    A("")
    A("| 预测维度 | 预测结果 | 概率 | 置信度 |")
    A("|---------|---------|:----:|:------:|")
    A("| **胜平负** | **{}** | {} | {} |".format(wdl_pred, pct(max(hp or 0, dp or 0, ap or 0)), "{}/100".format(conf_score)))
    # C-20261013-068: hcp_line=None（竞彩未开让球盘）摘要卡走占位，不显示概率
    if hcp_line_raw is None:
        A("| **让球胜平负** | 竞彩未开让球盘 | — | — |")
    else:
        A("| **让球胜平负** ({}) | **{}** | {} | — |".format(hcp_line_disp, hcp_pred,
              pct(max(hcp_upper or 0, hcp_draw or 0, hcp_lower or 0))))
    if tg_top3:
        A("| **总进球 Top1** | **{}** | {} | — |".format(tg_top3[0]["label"], pct(tg_top3[0]["prob"])))
    else:
        A("| **总进球 Top1** | **{}** | — | — |".format(tg_pred))
    if top_scores:
        A("| **最可能比分** | **{}** | {} | — |".format(top_scores[0]["score"], pct(top_scores[0]["prob"])))
    A("")
    # P1-01: 综合置信度拆解表（基准分 + 扣分项明示）
    A("### 置信度拆解")
    A("")
    A("> 综合 **{}/100** = {}".format(conf_score, _conf_breakdown["explanation"]))
    A("")
    A("| 维度 | 类型 | 分值 | 说明 |")
    A("|------|:----:|:----:|------|")
    for _c in _conf_breakdown["components"]:
        _v = _c["value"]
        _sign = "+" if _v > 0 else ""
        A("| {} | {} | {}{} | {} |".format(
            _c["name"], "基准" if _c["type"] == "base" else "扣分",
            _sign, _v, _c["reason"]))
    A("")
    # C-20261018-138: EV 展示层解耦——「模型推荐」下注建议卡整体退役（六层证伪 ROI 为负、
    # 勒芒场错方向 VALUE 事件；推荐/仓位语义不再面向读者渲染）。ev_engine 计算与 EV_* 落库
    # 不受影响（C1 P0-E 实盘通道内部继续使用）。C-20261017-130 分歧闸转挂为「方向分歧提示」：
    # 4:1/3:1:1 票型 vs meta 反向的提示对读者仍有独立价值，但不再携带任何执行/仓位语义。
    if rec_type == "DISAGREE_WATCH":
        A("### 方向分歧提示")
        A("")
        A("> ⚠️ **子模型方向分歧**：可用子模型 {} 个，**{} 票看{}、Stacking meta 反向给{}**（分歧度 std={:.4f}）。"
          "方向分歧显著时融合概率的不确定性升高，本场置信度已在「置信度拆解」中相应扣分；"
          "历史检验（C-20261004-007，n=77）显示分歧场跟随模型无统计优势，请对结论保持审慎。".format(
            _vote_total, _maj_votes, _maj_dir, wdl_pred, divergence_std))
        A("")
    A("### 一句话总结")
    A("")
    # C-20260923-064: 「低估/过热」的判定对象是推荐方向 rec_dir 的 edge（EV 层），而非概率方向 wdl_pred；
    # 旧措辞主语混用会生成「模型主胜，市场低估」，被误读为市场低估主胜，与 §6.1 主胜 OVERPRICED 直接矛盾
    if rec_edge is None or abs(rec_edge) < 0.02:
        _mkt_word = "与模型定价方向一致"
    elif (rec_edge or 0) > 0:
        _mkt_word = "低估{}方向（价值空间 {:+.1f}%）".format(rec_dir, (rec_edge or 0) * 100)
    else:
        _mkt_word = "对{}方向过热（价值空间 {:+.1f}%）".format(rec_dir, (rec_edge or 0) * 100)
    if rec_type == "DISAGREE_WATCH":
        _summary_tail = "子模型 {} 票看{}而 meta 反向给{}，方向分歧显著，结论需审慎".format(
            _maj_votes, _maj_dir, wdl_pred)
    else:
        _summary_tail = "市场对照详见第六节"
    summary = "模型主看{}（概率{}），市场{}，{}。".format(
        wdl_pred, pct(max(hp or 0, dp or 0, ap or 0)), _mkt_word, _summary_tail)
    A("> " + summary)
    A("")
    # P1-02: 5 子模型原始输出 + 分歧度
    A("### 子模型原始输出与分歧度")
    A("")
    A("> 5 个基础模型各自 WDL 概率，与 Stacking 融合值对比；分歧度 = 三向概率标准差均值，反映子模型间一致性。")
    A("")
    A("| 子模型 | 主胜 | 平局 | 客胜 | 预测方向 |")
    A("|--------|:----:|:----:|:----:|:--------:|")
    _sub_total = 0
    for _key, _lbl in SUB_MODEL_LABELS:
        _p = sub_models.get(_key)
        if not _p or not all(isinstance(_p.get(k), (int, float)) for k in ("win", "draw", "lose")):
            A("| {} | — | — | — | — |".format(_lbl))
            continue
        _sub_total += 1
        _sub_pred = "主胜" if _p["win"] >= max(_p["draw"], _p["lose"]) else (
            "客胜" if _p["lose"] >= _p["draw"] else "平局")
        A("| {} | {} | {} | {} | {} |".format(
            _lbl, pct(_p["win"]), pct(_p["draw"]), pct(_p["lose"]), _sub_pred))
    # Stacking 融合行
    A("| **Stacking 融合** | **{}** | **{}** | **{}** | **{}** |".format(
        pct(hp), pct(dp), pct(ap), wdl_pred))
    A("")
    A("> 分歧度等级: **{}**（std={:.4f}，可用子模型 {}/{}）".format(
        divergence_level, divergence_std, _sub_total or n_sub_models, len(SUB_MODEL_LABELS)))
    A("> 阈值: std<0.05 低分歧 / 0.05–0.10 中分歧 / ≥0.10 高分歧；高分歧意味着子模型对结果判断差异显著，建议结合 EV 与抽水分析谨慎决策。")
    # C-20261014-086: 平局高危旗标小注（开关默认关；与 §十六/影子共用同一判定，文案逐字冻结）
    if draw_flag_on and draw_flag and draw_flag["flag"]:
        A("> ⚠️ **{}**".format(DRAW_FLAG_TEXT))
    # C-20260919-022: XGB 缺失在报告内直接告警，杜绝"静默 —"（prediction_core 哨兵同步报错）
    _xgb_sp = sub_models.get("xgboost")
    if not _xgb_sp or not all(isinstance(_xgb_sp.get(k), (int, float)) for k in ("win", "draw", "lose")):
        A("> 🚨 **XGBoost 子模型本场未产出**（上表显示 —）：模型文件/scaler/特征提取或推理环节异常，已自动降级为 {} 模型融合；请查日志中『WDL模型·哨兵』定位原因后重生成。".format(_sub_total))
    A("")
    A("---")
    A("")

    # ================= 三、胜平负 =================
    A("## 二、胜平负概率分布与模型集成")
    A("")
    A("### 最终概率分布 ({})".format(wdl_method))
    A("")
    A("| 结果 | 模型概率 | 竞彩赔率 | 隐含概率 | 差值 | 判定 |")
    A("|------|:--------:|:--------:|:--------:|:----:|:----:|")
    A("| 主胜 | {} | {} | {} | {} | {} |".format(pct(hp), fnum(oc_h), pct(ih_h), pct_with_sign(edge_h), value_tag(edge_h)))
    A("| 平局 | {} | {} | {} | {} | {} |".format(pct(dp), fnum(oc_d), pct(ih_d), pct_with_sign(edge_d), value_tag(edge_d)))
    A("| 客胜 | {} | {} | {} | {} | {} |".format(pct(ap), fnum(oc_a), pct(ih_a), pct_with_sign(edge_a), value_tag(edge_a)))
    implied_sum = (ih_h + ih_d + ih_a) if ih_h is not None else None
    A("| **合计** | **100%** | — | **{}** | — | 返还率 {}".format(pct(implied_sum), pct(wdl_return)))
    A("")
    A("> 预测方法: {}".format(wdl_method))
    A("")
    A("---")
    A("")

    # ================= 四、让球 =================
    A("## 三、让球胜平负预测 (T-005 v3)")
    A("")
    # C-20261013-068: hcp_line=None（竞彩未开让球盘）整节占位，保持 19 章结构稳定
    if hcp_line_raw is None:
        A("> 竞彩未开让球盘（hcp_line=None，T-005 v3/D2 短路，无 HC 落库）")
        A("")
        A("---")
        A("")
    else:
        A("| 让球盘口 | 让球主胜 | 让球平局 | 让球客胜 | 预测方向 | 概率 |")
        A("|---------|:--------:|:--------:|:--------:|---------|:----:|")
        A("| {} | {} | {} | {} | **{}** | {} |".format(
            hcp_line_disp, pct(hcp_upper), pct(hcp_draw), pct(hcp_lower), hcp_pred,
            pct(max(hcp_upper or 0, hcp_draw or 0, hcp_lower or 0))))
        A("")
        A("**走水检测**: 走水概率 {} | 方法: {}".format(pct(hcp_draw), hcp_method))
        # C-20261016-119: §二/§三 对账自检——**仅 ±1 盘**（走水=恰赢1球）。
        # 深盘（±2 起）下盘=净胜≤1球（含主胜恰赢1球），恒等式口径不成立 → 不渲染，防假警报
        # （威尼斯场实测 -2 盘误显 -42.7pp）。两口径差 >5pp 提示走水估计与 WDL/市场分裂
        # （法兰克福场实测 让球下盘 51.7% vs WDL 隐含 44.0%，根因=isotonic 台阶低估走水）。
        # C-20261016-123: 恒等式按盘口方向分支——主让一球(line<0)：让球负=平+客；
        # 受让一球(line>0)：让球胜=主+平（让球负=客胜2球+，「平+客」不成立）。
        # 残差语义不变=「模型 WDL vs 市场让球盘」跨源残差（受让场模型 WDL 高估主队时残差仍可 >5pp，属真实分歧）。
        if (hcp_lower is not None and dp is not None and ap is not None
                and hcp_line_raw is not None and abs(hcp_line_raw) == 1):
            if hcp_line_raw < 0:
                _wdl_side, _hcp_side = dp + ap, hcp_lower
                _side, _p1, _p2 = "下盘", pct(dp), pct(ap)
            elif hcp_upper is not None and hp is not None:
                _wdl_side, _hcp_side = hp + dp, hcp_upper
                _side, _p1, _p2 = "上盘", pct(hp), pct(dp)
            else:
                _wdl_side = None
            if _wdl_side is not None:
                _diff_pp = (_hcp_side - _wdl_side) * 100.0
                _flag = "⚠️" if abs(_diff_pp) > 5 else "✓"
                A("")
                A("> {} **口径自检**: WDL 隐含{} = {} + {} = {}；让球{} {}（差 {:+.1f}pp）".format(
                    _flag, _side, _p1, _p2, pct(_wdl_side), _side, pct(_hcp_side), _diff_pp))
        A("")
        A("---")
        A("")

    # ================= 五、比分 =================
    A("## 四、比分预测 Top5 ({})".format(score_method))
    A("")
    A("| 排名 | 比分 | 概率 | 累计概率 | 胜平负 |")
    A("|:----:|------|:----:|:--------:|:------:|")
    cum = 0.0
    for i, s in enumerate(top_scores[:5], 1):
        cum += s["prob"]
        try:
            h, a = map(int, s["score"].split(":"))
            w = "主胜" if h > a else ("平" if h == a else "客胜")
        except Exception:
            w = "—"
        A("| {} | {} | {} | {} | {} |".format(i, s["score"], pct(s["prob"]), pct(cum), w))
    if not top_scores:
        A("| 1 | — | — | — | — |")
    A("")
    A("**比分分布特征**: λ主={} λ客={} | 方法: {}".format(fnum(lambda_h), fnum(lambda_a), score_method))
    A("")
    # P1-13: λ 主客差值告警展示
    if lambda_alert and lambda_alert.get("triggered"):
        A("> ⚠️ **λ 主客差值告警**: {}（阈值 {}，实测差值 {}）".format(
            lambda_alert.get("message", ""),
            lambda_alert.get("threshold", 1.2),
            lambda_alert.get("diff", "—")))
        A("> 赛后建议：拿真实 xG 数据复核 λ 链路（赔率隐含概率 → WDL/TG 缩放 → 调整后 λ），"
          "确认客场强队 λ 被压低是否合理。")
        A("")
    # C-20261018-141: 市场 λ 对照行（λ 失衡场自检；展示层只读，零模型/零落库影响）
    if lambda_alert and lambda_alert.get("triggered"):
        _tot = (lambda_h + lambda_a) if (
            isinstance(lambda_h, (int, float)) and isinstance(lambda_a, (int, float))) else None
        _mlc = _market_lambda_contrast(ih_h, ih_d, ih_a, _tot)
        if _mlc:
            _t1 = top_scores[0] if top_scores else {}
            A("> 📏 **市场 λ 对照（λ 失衡自检）**: 市场去水 WDL（主 {} / 平 {} / 客 {}）在**同总量**（{} 球）下"
              "二分反演 λ主={} λ客={}（差 {:.2f}，模型差 {:.2f}），泊松同构 Top1 = {}@{}"
              "（本报告 Top1 = {}@{}）——单格真实频率天花板 ~12%，Top1 概率幅度请对照市场口径阅读；"
              "λ 差放大根治归 Plan 3（C-20261018-141）".format(
                  pct(ih_h), pct(ih_d), pct(ih_a), fnum(_tot),
                  fnum(_mlc["lh"]), fnum(_mlc["la"]),
                  abs(_mlc["lh"] - _mlc["la"]), abs((lambda_h or 0) - (lambda_a or 0)),
                  _mlc["top1"], pct(_mlc["top1_prob"]),
                  _t1.get("score", "—"), pct(_t1.get("prob") or 0)))
            A("")
    # C-20261011-041: WDL 方向族重排注记（Top1=族内最大格；网格/边缘/Top5 集合不变）
    if score_reorder_note:
        A("> ℹ️ **WDL 方向族重排**: {}（C-20261011-041；λ 失衡场重加权后 Top1 概率仍可能虚高，仅供参考）".format(
            score_reorder_note))
        A("")
    A("---")
    A("")

    # ================= 六、总进球 =================
    A("## 五、总进球预测")
    A("")
    if tg_goals:
        A("| 总进球 | 0球 | 1球 | 2球 | 3球 | 4球 | 5球 | 6球 | 7+球 |")
        A("|:------:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:----:|")
        vals = []
        for k in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
            v = tg_goals.get(k) or tg_goals.get(str(k)) or 0.0
            vals.append(pct(v))
        A("| 概率 | {} |".format(" | ".join(vals)))
    A("")
    # C-20260921-035：结论=精确进球数 Top3（原大小球结论已移除）
    if tg_top3:
        A("**进球数 Top3 预测**")
        A("")
        A("| 排名 | 进球数 | 概率 | 累计概率 |")
        A("|:----:|:------:|:----:|:--------:|")
        cum_p = 0.0
        for i, item in enumerate(tg_top3, 1):
            cum_p += float(item.get("prob") or 0.0)
            A("| {} | **{}** | {} | {} |".format(i, item["label"], pct(item["prob"]), pct(cum_p)))
        A("")
        _lh = tg.get("lambda_home"); _la = tg.get("lambda_away")
        if _lh is not None and _la is not None:
            A("> 分析：总进球期望（λ主+λ客）= {:.2f} 球为概率均值，分布峰值（众数）为 **{}**；"
              "Top3 合计 {} 覆盖近半数可能，其余 5 档概率分散，单场仍以 {} 为首选。".format(
                  _lh + _la, tg_top3[0]["label"], pct(cum_p), tg_top3[0]["label"]))
        else:
            A("> 分析：Top3 合计 {}；该场为纯赔率隐含口径（λ 缺失）。".format(pct(cum_p)))
        A("")
        A("**方法**: {}（大小球底层概率仍入库：大球 {}，不作结论）".format(tg_method, pct(tg_over)))
    else:
        A("**进球数预测**: 数据不足（无总进球赔率记录）")
    A("")
    A("---")
    A("")

    # ================= 七、赔率分析 =================
    A("## 六、赔率分析与市场对照")
    A("")
    # P1-07: 两层预测架构提示块（概率预测层 vs 市场对照层）
    # C-20261018-138: 本节由「EV 决策」改为「市场对照诊断」——保留 edge/去水概率/抽水/凯利等
    # 诊断数字（项目是锚哲学的展示层），但不再输出任何下注建议/仓位结论（该层已经六层证伪）。
    A("> 📌 **预测层级架构**")
    A("> - **第一层 — 概率预测**（§一~§五）：Stacking 融合 5 子模型，输出 WDL/让球/比分/总进球概率，回答「谁赢、几个球」。")
    A("> - **第二层 — 市场对照**（§六）：以第一层概率 × 市场赔率，计算 edge/去水市场概率/抽水率等诊断指标，回答「模型定价与市场定价差多少、差在哪」。")
    A("> - 本节所有数字均为**诊断对照、非下注建议**（历史回测已证伪其下注指导价值，仅作市场罗盘参考）。")
    A("")

    # --- EV 期望值分析（ev_engine 决策层）---
    if ev_analysis is not None:
        A("### 6.1 期望值分析 (EV)")
        A("")
        # C-20261018-138: 本表为市场对照诊断（保留罗盘），非下注建议；原「6.2 投注建议卡」已整体退役
        A("> 下表为**诊断对照**（历史回测已证伪其下注指导价值，ROI 长期为负），仅用于观察模型定价与市场定价的差异。")
        A("")
        A("| 结果 | 竞彩赔率 | 模型概率 | 去水市场概率 | 价值空间 | EV | 凯利(1/4) | 决策 |")
        A("|------|:--------:|:--------:|:------------:|:--------:|:---:|:---------:|:----:|")
        for d in (ev_analysis.home_analysis, ev_analysis.draw_analysis, ev_analysis.away_analysis):
            A("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                d.direction_cn, fnum(d.odds), pct(d.p_model), pct(d.p_market),
                pct_with_sign(d.edge), "{:+.2f}%".format(d.ev * 100),
                "{:.1f}%".format(d.kelly_clipped * 100) if d.decision != "AVOID" else "0%",
                d.decision))
        A("")
        # C-20261018-138: 原「6.2 投注建议 (EV 决策)」卡及其附注块整体退役（展示层解耦；
        # ev_engine 对象、EV_* 落库、C1 P0-E 实盘通道不受影响）。6.3 抽水分析顺延为 6.2。
        A("### 6.2 抽水分析")
        A("")
        A("- 竞彩返还率: {} | 抽水率: {} | 三向隐含概率和: {}".format(
            pct(ev_analysis.payout_rate), pct(ev_analysis.vig), pct(ev_analysis.total_implied)))
        if ev_analysis.risk_warnings:
            A("- 风险提示: {}".format("；".join(ev_analysis.risk_warnings)))
        A("")
        A("> 注：EV 阈值 {}% | 凯利策略 {} | 单场上限 {}%（以上参数为 ev_engine 内部口径，供 C1 实盘通道使用；本节数字仅作诊断对照）。".format(
            ev_analysis.ev_threshold * 100, ev_analysis.kelly_strategy, ev_analysis.kelly_cap * 100))
        A("")
        # P1-06: edge 正向但 EV 负（或反向）矛盾解释
        _best = ev_analysis.best_edge or 0
        _best_ev = ev_analysis.best_ev or 0
        if (_best > 0 and _best_ev < 0) or (_best < 0 and _best_ev > 0):
            A("> ⚠️ **edge 与 EV 符号相反**：edge = 模型概率 - 去水市场概率（未考虑抽水比例）；"
              "EV = (模型概率 × 赔率 - 1) 实打实算，已扣除抽水后单位注金的期望净收益。")
            A("> 当抽水率 {:.1f}% 较高、edge 较小（<2%）时，可能出现 edge>0 但 EV<0 —— 价值空间不足以覆盖抽水，建议观望。".format(
                ev_analysis.vig * 100))
            A("")
        # P1-08: EV 赔率快照溯源行
        _snap_time = (wdl_close.get("time") if isinstance(wdl_close, dict) else None) or "—"
        A("> 📎 **EV 赔率快照溯源**：赔率来源=竞彩 WDL 尾盘（close） | 快照时间={} | 返还率={} | 抽水率={} | 三向隐含概率和={}".format(
            _snap_time, pct(ev_analysis.payout_rate), pct(ev_analysis.vig), pct(ev_analysis.total_implied)))
        A("")
        A("---")
        A("")

    A("### 凯利指数分析")
    A("")
    # P1-05: 两套凯利概念脚注（下注策略 vs 市场隐含），消除混用
    # C-20261018-138: 本表保留作市场对照，标注「历史证伪，仅作对照」；原 §6.2 决策卡已退役
    A("> ⚠️ **历史证伪，仅作对照**：凯利下注策略在本项目历史回测中未产生正期望，本表仅用于观察「模型 vs 市场」定价分歧，不构成任何仓位建议。")
    A("")
    A("> 📝 **凯利概念辨析**（两套概念易混用，特此脚注）")
    A("> - **下注策略凯利**（本表）：f* = (bp - q) / b，b=净赔率(赔率-1)，p=模型胜率，q=1-p。本表列为**全凯利原始值**；1/4 分数凯利（kelly_clipped×0.25）为 ev_engine 内部口径，供 C1 实盘通道使用。")
    A("> - **赔率隐含凯利**：以市场隐含概率作为「市场认为的胜率」反推 f*，反映庄家观点，与模型凯利差值衡量「市场 vs 模型」分歧，本表未展示。")
    A("")
    A("| 结果 | 竞彩赔率 | 模型概率 | 凯利指数 | 判定 |")
    A("|------|:--------:|:--------:|:--------:|:----:|")
    A("| 主胜 | {} | {} | {} | {} |".format(fnum(oc_h), pct(hp), fnum(kh, 3), kelly_tag(kh)))
    A("| 平局 | {} | {} | {} | {} |".format(fnum(oc_d), pct(dp), fnum(kd, 3), kelly_tag(kd)))
    A("| 客胜 | {} | {} | {} | {} |".format(fnum(oc_a), pct(ap), fnum(ka, 3), kelly_tag(ka)))
    A("")
    A("**返还率**: {}".format(pct(wdl_return)))
    A("")
    A("---")
    A("")

    # ================= 八、时序赔率异动 =================
    # C-20261011-037：§七 由 WDL 单玩法扩展为四玩法（WDL/让球=快照网格+异动标签，
    # 大小球=初盘→即时两端点，比分=变动 Top8 摘要），数据不足时逐玩法降级声明。
    A("## 七、时序赔率异动分析")
    A("")
    A("### 7.1 胜平负（WDL）时序")
    A("")
    wdl_records = wdl.get("records") or []
    if wdl_snap >= 2:
        # P1-03: 完整快照网格 + 相邻快照异动标签（≥0.05 赔率绝对变化标记方向）
        A("| 时间 | 主胜 | 平局 | 客胜 | 主胜隐含 | 平局隐含 | 客胜隐含 | 异动标签 |")
        A("|------|:----:|:----:|:----:|:--------:|:--------:|:--------:|:--------:|")
        _prev = None
        for r in wdl_records:
            ih, idr, ia, _ = odds_to_implied(r.get("win"), r.get("draw"), r.get("lose"))
            _delta = "—" if _prev is None else _odds_delta_label(_prev, r)
            A("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r.get("time", "—"), fnum(r.get("win")), fnum(r.get("draw")), fnum(r.get("lose")),
                pct(ih), pct(idr), pct(ia), _delta))
            _prev = r
        A("")
        A("```")
        A("主胜隐含漂移: {}% (开盘 → 即时)".format(fnum(drift_h)))
        A("平局隐含漂移: {}%".format(fnum(drift_d)))
        A("客胜隐含漂移: {}%".format(fnum(drift_a)))
        A("```")
        A("> 异动标签: 相邻快照赔率绝对变化 ≥0.05 标记方向（↓ 赔率下降/隐含概率上升，↑ 反之）；<0.05 标记「平稳」。漂移列仅展示首尾两端累计差。")
    elif wdl_snap == 1:
        r = wdl_records[0]
        ih, idr, ia, _ = odds_to_implied(r.get("win"), r.get("draw"), r.get("lose"))
        A("| 时间 | 主胜 | 平局 | 客胜 | 主胜隐含 | 平局隐含 | 客胜隐含 |")
        A("|------|:----:|:----:|:----:|:--------:|:--------:|:--------:|")
        A("| {} | {} | {} | {} | {} | {} | {} |".format(
            r.get("time", "—"), fnum(r.get("win")), fnum(r.get("draw")), fnum(r.get("lose")),
            pct(ih), pct(idr), pct(ia)))
        A("")
        if _wdl_src == "fallback_deep":
            A("> ℹ️ 竞彩未开售胜平负盘（已开让球/大小球/比分），上表为 500.com 初盘参考（非时序缺失）。")
        elif _wdl_src == "fallback_none":
            A("> ℹ️ 竞彩未开售本场（胜平负/让球/大小球/比分均无），上表为 500.com 初盘参考（非采集缺失，回踩无法补）。")
        else:
            A("> ⚠️ 仅 1 条竞彩 WDL 快照，缺少开盘→即时漂移，需回踩刷新（--no-skip-existing）。")
    else:
        A("(无时序赔率数据；需补采竞彩 WDL 时序)")
    A("")

    # --- 7.2 让球胜平负时序（C-20261011-037）---
    # C-20261013-068: 标题盘口串改用竞彩线标签（hcp_line_disp），None 时不显示盘口串
    A("### 7.2 让球胜平负时序{}".format(
        "（盘口 {}）".format(hcp_line_disp) if hcp_line_raw is not None else ""))
    A("")
    hcp_recs = (odds_data.get("handicap_odds") or {}).get("records") or []

    def _hcp_delta(prev, cur):
        if prev is None or cur is None:
            return "—"
        labels = []
        for key, cn in (("win", "上盘"), ("draw", "走水"), ("lose", "下盘")):
            d0 = num(cur.get(key))
            d1 = num(prev.get(key))
            if d0 is None or d1 is None:
                continue
            if d0 - d1 <= -0.05:
                labels.append("{}↓".format(cn))
            elif d0 - d1 >= 0.05:
                labels.append("{}↑".format(cn))
        return "、".join(labels) if labels else "平稳"

    if len(hcp_recs) >= 2:
        A("| 时间 | 主胜 | 走水 | 客胜 | 主胜隐含 | 走水隐含 | 客胜隐含 | 异动标签 |")
        A("|------|:----:|:----:|:----:|:--------:|:--------:|:--------:|:--------:|")
        _prev = None
        for r in hcp_recs:
            ih, idr, ia, _ = odds_to_implied(r.get("win"), r.get("draw"), r.get("lose"))
            _delta = _hcp_delta(_prev, r)
            A("| {} | {} | {} | {} | {} | {} | {} | {} |".format(
                r.get("time", "—"), fnum(r.get("win")), fnum(r.get("draw")), fnum(r.get("lose")),
                pct(ih), pct(idr), pct(ia), _delta))
            _prev = r
        A("")
        A("> 让球三向赔率是 T-005 的直接输入市场：盘口固定、赔率随资金流漂移；走水赔率下行=市场认为走水风险上升。异动阈值 0.05。")
    elif len(hcp_recs) == 1:
        r = hcp_recs[0]
        ih, idr, ia, _ = odds_to_implied(r.get("win"), r.get("draw"), r.get("lose"))
        A("| 时间 | 主胜 | 走水 | 客胜 | 主胜隐含 | 走水隐含 | 客胜隐含 |")
        A("|------|:----:|:----:|:----:|:--------:|:--------:|:--------:|")
        A("| {} | {} | {} | {} | {} | {} | {} |".format(
            r.get("time", "—"), fnum(r.get("win")), fnum(r.get("draw")), fnum(r.get("lose")),
            pct(ih), pct(idr), pct(ia)))
        A("")
        A("> ⚠️ 仅 1 条竞彩让球快照，无漂移可分析；今晚 0b 临场补采 + 23:30 兜底重生成可补齐。")
    else:
        A("(竞彩让球时序无快照：未开售或未采集)")
    A("")

    # --- 7.3 总进球数时序（初盘→即时摘要）---
    A("### 7.3 总进球数时序（初盘 → 即时）")
    A("")
    tg_recs = (odds_data.get("tg_odds") or {}).get("records") or []
    _tg_labels = ["0", "1", "2", "3", "4", "5", "6", "7+"]
    if tg_recs:
        _tg_show = [tg_recs[0], tg_recs[-1]] if len(tg_recs) > 1 else [tg_recs[0]]
        A("| 快照 | " + " | ".join(_tg_labels) + " |")
        A("|------|" + ":----:|" * len(_tg_labels))
        for r in _tg_show:
            _g = r.get("goals") or {}
            _tag = "初盘" if r is tg_recs[0] else ("即时" if len(tg_recs) > 1 else "唯一快照")
            A("| {}（{}） | {} |".format(
                _tag, (r.get("pub_time") or "—")[:16],
                " | ".join(fnum(_g.get(k)) for k in _tg_labels)))
        A("")
        if len(tg_recs) >= 2:
            A("> 共 {} 条竞彩总进球快照，仅展示首尾两端；某档赔率下降=资金流入该档位。".format(len(tg_recs)))
        else:
            A("> ⚠️ 仅 1 条竞彩总进球快照，无漂移可分析。")
    else:
        A("(竞彩总进球时序无快照：未开售或未采集)")
    A("")

    # --- 7.4 比分赔率时序（变动 Top8 摘要）---
    A("### 7.4 比分赔率时序（初盘 → 即时变动 Top8）")
    A("")
    sc_recs = (odds_data.get("score_odds") or {}).get("records") or []
    if len(sc_recs) >= 2:
        def _score_map(rec):
            mp = {}
            for k in ("win_odds", "draw_odds", "lose_odds"):
                for s, o in (rec.get(k) or {}).items():
                    mp[s] = num(o)
            return mp
        _sm_first, _sm_last = _score_map(sc_recs[0]), _score_map(sc_recs[-1])
        _deltas = []
        for s in set(_sm_first) | set(_sm_last):
            _io, _lo = _sm_first.get(s), _sm_last.get(s)
            if _io and _lo:
                # 相对变动排序（C-20261014-100）：绝对变动天然偏向高赔比分的机械波动；
                # 负值=赔率下降=资金流入
                _rel = (_lo - _io) / _io
                _deltas.append((abs(_rel), _rel, s, _io, _lo))
        _deltas.sort(reverse=True)
        if _deltas:
            A("| 比分 | 初盘赔率 | 即时赔率 | 相对变动 | 方向 |")
            A("|:----:|:--------:|:--------:|:--------:|:----:|")
            for _ar, _rel, s, _io, _lo in _deltas[:8]:
                _tag = "↓ 资金流入" if _rel < 0 else "↑ 资金流出"
                A("| {} | {} | {} | {:+.1%} | {} |".format(s, fnum(_io), fnum(_lo), _rel, _tag))
            A("")
            A("> 共 {} 个竞彩比分快照时刻（初盘 {} → 即时 {}）；按**相对变动**排序（绝对变动会偏向高赔比分），赔率下降=资金流入；|Δ|/赔率 ≥5% 视为显著，低于视为噪声。".format(
                len(sc_recs), (sc_recs[0].get("pub_time") or "—")[:16], (sc_recs[-1].get("pub_time") or "—")[:16]))
            # 自动结论行（C-20261014-100：显式点名资金流向，带 5% 噪声阈值）
            _REL_TH = 0.05
            _sig_in = [(s, _io, _lo, _rel) for _ar, _rel, s, _io, _lo in _deltas if _rel <= -_REL_TH]
            if _sig_in:
                A("")
                A("> 💰 **资金净流入比分**（相对下降 ≥5%）：{}".format(
                    "；".join("{}（{}→{}，{:+.1%}）".format(s, fnum(io), fnum(lo), rel)
                              for s, io, lo, rel in _sig_in[:3])))
            else:
                A("")
                A("> 💰 资金净流入比分：无（所有比分相对变动均 <5%，属噪声区间）")
        else:
            A("(初盘/即时快照无可对比的比分赔率)")
    elif len(sc_recs) == 1:
        A("> 仅 1 条竞彩比分快照（{}），无漂移可分析；31 列比分赔率不逐格展示。".format(
            (sc_recs[0].get("pub_time") or "—")[:16]))
    else:
        A("(竞彩比分时序无快照：未开售或未采集)")
    A("")
    A("---")
    A("")

    # ================= 九、百家欧指 =================
    A("## 八、百家欧指一致性分析 (500.com)")
    A("")
    if ouzhi and ouzhi.get("company_count"):
        A("| 公司数 | 平均主胜 | 平均平局 | 平均客胜 | 主胜离散 | 返还率 |")
        A("|:------:|:--------:|:--------:|:--------:|:--------:|:------:|")
        A("| {} | {} | {} | {} | {} | {}% |".format(
            ouzhi["company_count"], fnum(ouzhi["avg_live_win"]), fnum(ouzhi["avg_live_draw"]),
            fnum(ouzhi["avg_live_lose"]), fnum(ouzhi["disp_live_win"]), fnum(strip_pct(ouzhi["avg_return_live"]))))
        A("")
        A("**即时平均隐含**: 主胜 {} / 平局 {} / 客胜 {}".format(
            ouzhi["avg_prob_live_win"], ouzhi["avg_prob_live_draw"], ouzhi["avg_prob_live_lose"]))
    else:
        A("(无百家欧指数据)")
    A("")
    if companies:
        A("**主流公司赔率**:")
        A("")
        A("| 博彩公司 | 主胜 | 平局 | 客胜 |")
        A("|---------|:----:|:----:|:----:|")
        for c in companies[:8]:
            A("| {} | {} | {} | {} |".format(c["company"], fnum(c["live_win"]), fnum(c["live_draw"]), fnum(c["live_lose"])))
        A("")
    A("---")
    A("")

    # ================= 十、投注热度 =================
    A("## 九、投注热度与资金流向 (500.com)")
    A("")
    if betting and betting_has_real_data(betting):
        A("| 结果 | 投注比例 | 赔率 | 必发成交占比 | 成交额 | 庄家盈亏 |")
        A("|------|:--------:|:----:|:------------:|:------:|:--------:|")
        A("| 主胜 | {} | {} | {} | {} | {} |".format(
            betting["home_prob"], betting["home_odds"], betting["home_bf_ratio"],
            betting["home_bf_volume"], betting["home_bf_profit"]))
        A("| 平局 | {} | {} | {} | {} | {} |".format(
            betting["draw_prob"], betting["draw_odds"], betting["draw_bf_ratio"],
            betting["draw_bf_volume"], betting["draw_bf_profit"]))
        A("| 客胜 | {} | {} | {} | {} | {} |".format(
            betting["away_prob"], betting["away_odds"], betting["away_bf_ratio"],
            betting["away_bf_volume"], betting["away_bf_profit"]))
        A("")
        if betting["tips"]:
            A("> {}".format(betting["tips"]))
        A("")
    elif betting:
        def _bget(obj, key, default=None):
            if hasattr(obj, "get"):
                return obj.get(key, default)
            try:
                return obj[key]
            except (KeyError, IndexError):
                return default
        _bstat = _bget(betting, "crawl_status", "ok") or "ok"
        if _bstat == "anti_bot_blocked":
            A("> ❌ **投注热度数据采集受限**：本场被 500.com EdgeOne 反爬拦截，返回了空壳数据（非真实无数据）。")
            A("> 请刷新 `data/cookies_500.json` 中的 Cookie 并重新采集（同 IP、同浏览器完成人机验证后导出）。")
            if _bget(betting, "tips"):
                A(">")
                A("> 采集提示：{}".format(_bget(betting, "tips")))
        elif _bstat == "source_no_data":
            A("> 源站未提供该场投注热度/必发成交数据。")
            if _bget(betting, "tips"):
                A(">")
                A("> 源站提示：{}".format(_bget(betting, "tips")))
        else:
            # 旧数据 ok+空壳：实质也是反爬降级
            A("> ❌ **投注热度数据为空壳**（疑似 500.com EdgeOne 反爬拦截，需刷新 Cookie 重新采集）。")
            if _bget(betting, "tips"):
                A(">")
                A("> 采集提示：{}".format(_bget(betting, "tips")))
        A("")
    else:
        A("(无投注分析数据)")
    A("")
    A("---")
    A("")

    # ================= 十一、球队实力 =================
    A("## 十、球队实力对比 (SofaScore)")
    A("")
    sof = extra["sofascore"]
    if sof:
        h_rat = _sf(sof, "sofa_rat_5g_home")
        a_rat = _sf(sof, "sofa_rat_5g_away")
        h_xg = _sf(sof, "sofa_xg_5g_home", 3)
        a_xg = _sf(sof, "sofa_xg_5g_away", 3)
        A("| 指标 | 主队 | 客队 |")
        A("|------|:----:|:----:|")
        A("| SofaScore 近5场评分 | {} | {} |".format(fnum(h_rat), fnum(a_rat)))
        A("| 近5场 xG | {} | {} |".format(fnum(h_xg), fnum(a_xg)))
        A("| λ (进球期望) | {} | {} |".format(fnum(lambda_h), fnum(lambda_a)))
        A("")
    else:
        A("(无 SofaScore 特征数据)")
    A("")
    A("---")
    A("")

    # ================= 十二、近期状态 =================
    A("## 十一、近期状态与攻防数据")
    A("")
    if sof:
        A("| 球队 | 近5场评分 | 传球成功率 | 控球/对抗 |")
        A("|------|:---------:|:---------:|:---------:|")
        A("| 主队 | {} | {} | — |".format(
            fnum(_sf(sof, "sofa_rat_5g_home")), _sf_pct(sof, "sofa_pass_sr_5g_home", min_ratio=PASS_SR_MIN)))
        A("| 客队 | {} | {} | — |".format(
            fnum(_sf(sof, "sofa_rat_5g_away")), _sf_pct(sof, "sofa_pass_sr_5g_away", min_ratio=PASS_SR_MIN)))
        A("")
    else:
        A("(无近期状态数据)")
    A("")
    A("---")
    A("")

    # ================= 十三~十六 =================
    A("## 十二、球员与阵容分析")
    A("")
    if sof:
        if om_home or om_away:
            A("> 预测首发为历史连续性推算；**官方伤停**来自 SofaScore 赛前采集（prematch_sofascore_lineups，临场重采自动转官方确认），"
              "已用于校正推算首发判定（见下方「官方校正·推算首发」行）；可用性/缺阵影响等数值特征保持纯推算口径"
              "（与模型训练输入一致，官方名单不改写模型特征）。")
        else:
            A("> 预测首发/伤病缺阵为代理指标（由 player_availability_features 基于历史出场连续性推算，非官方伤病新闻）。"
              "官方伤停未采集：运行 collection/prematch_sofascore_lineups.py 后重算特征即可补齐。")
        A("")
        A("| 维度 | 主队 | 客队 |")
        A("|------|:----:|:----:|")
        A("| 官方伤停 | {} | {} |".format(
            "、".join(om_home) if om_home else "无记录", "、".join(om_away) if om_away else "无记录"))
        # C-20261014-092: 官方校正行（仅展示层）：推算首发中被官方确认缺阵的球员
        if _official_corr:
            def _corr_cell(_side):
                _c = _official_corr.get(_side)
                if _c is None:
                    return "—"
                if _c.get("unavailable"):
                    return "官方缺阵 {} 人；推算首发不可用，未校正".format(_c["official_n"])
                if _c["n_confirmed_out"] == 0:
                    return "推算首发未含官方缺阵（{}人）".format(_c["official_n"])
                return "{}（{}人确认缺阵，占官方名单{:.0%}）".format(
                    "、".join(_c["confirmed_out"]), _c["n_confirmed_out"], _c["coverage_of_official"])
            A("| 官方校正·推算首发 | {} | {} |".format(_corr_cell("home"), _corr_cell("away")))
        A("| 预计首发评分 | {} | {} |".format(
            fnum(_sf(sof, "pa_xi_rating_home")), fnum(_sf(sof, "pa_xi_rating_away"))))
        A("| 预计首发 xG | {} | {} |".format(
            fnum(_sf(sof, "pa_xi_xg_home", 3)), fnum(_sf(sof, "pa_xi_xg_away", 3))))
        A("| 核心球员 xG 占比 | {} | {} |".format(
            pct(_sf(sof, "pa_core_xg_share_home", 4), 1), pct(_sf(sof, "pa_core_xg_share_away", 4), 1)))
        A("| 球员可用性 | {} | {} |".format(
            fnum(_sf(sof, "pa_availability_home", 3)), fnum(_sf(sof, "pa_availability_away", 3))))
        A("| 缺阵影响 | {} | {} |".format(
            pct(_sf(sof, "pa_missing_impact_home", 4), 1), pct(_sf(sof, "pa_missing_impact_away", 4), 1)))
        A("| 近7天疲劳(分钟) | {} | {} |".format(
            fnum(_sf(sof, "pa_fatigue_7d_home", 1)), fnum(_sf(sof, "pa_fatigue_7d_away", 1))))
        A("| 距上场休息(天) | {} | {} |".format(
            fnum(_sf(sof, "pa_rest_days_home", 1)), fnum(_sf(sof, "pa_rest_days_away", 1))))
        A("| 阵容稳定性 | {} | {} |".format(
            fnum(_sf(sof, "pa_squad_stability_home", 3)), fnum(_sf(sof, "pa_squad_stability_away", 3))))
        A("")
    else:
        A("(无 SofaScore 球员特征数据)")
    A("")
    A("## 十三、历史交锋")
    A("")
    if h2h_summary.get("total", 0) > 0:
        hw = h2h_summary["home_win"]
        d = h2h_summary["draw"]
        aw = h2h_summary["away_win"]
        total = h2h_summary["total"]
        # 汇总进球统计
        home_goals_sum = sum(r["home_goals"] if r["is_home_side"] else r["away_goals"] for r in h2h_rows)
        away_goals_sum = sum(r["away_goals"] if r["is_home_side"] else r["home_goals"] for r in h2h_rows)
        all_goals = home_goals_sum + away_goals_sum
        A(f"近 {total} 场交锋：**{home_cn} {hw} 胜 {d} 平 {aw} 负**  {home_goals_sum}:{away_goals_sum}  "
          f"场均进球 {all_goals / total:.2f} 球")
        A("")
        A("| 日期 | 对阵 | 比分 |")
        A("|------|:----:|:----:|")
        for r in h2h_rows:
            ht = r["home_team"]
            at = r["away_team"]
            score = f"{r['home_goals']}:{r['away_goals']}"
            A(f"| {r['date']} | {ht} vs {at} | {score} |")
        A("")
        A("> 数据来源：本地 `matches` 历史赛果库（2025/26 赛季起）")
    else:
        A("> 现有赛果库（2025/26 起）内两队无历史交锋记录（升班马或长期未同联赛）。")
    A("")
    A("## 十四、情境因素与战意分析")
    A("")
    if not standings_ok:
        A(f"> ⚠️ 积分榜数据不完整：`matches` 表当前仅 {total_teams} 队有完赛记录（应为 {EXPECTED_TEAMS.get(league, 20)} 队），排名/战意暂不展示，待结果补采后刷新。")
        A("")
    A("| 维度 | 主队 | 客队 |")
    A("|------|:----:|:----:|")
    A("| 联赛排名 | {} | {} |".format(
        f"第{home_std['rank']}/{total_teams}名 ({home_std['pts']}分)" if home_std else "—",
        f"第{away_std['rank']}/{total_teams}名 ({away_std['pts']}分)" if away_std else "—"))
    A("| 已赛 | {} | {} |".format(
        home_std["played"] if home_std else "—", away_std["played"] if away_std else "—"))
    A("| 战绩 | {} | {} |".format(
        f"{home_std['win']}胜{home_std['draw']}平{home_std['loss']}负" if home_std else "—",
        f"{away_std['win']}胜{away_std['draw']}平{away_std['loss']}负" if away_std else "—"))
    A("| 战意 | {} | {} |".format(
        (classify_zhan_yi(home_std["rank"], total_teams) if home_std and standings_ok else "—"),
        (classify_zhan_yi(away_std["rank"], total_teams) if away_std and standings_ok else "—")))
    A("| 让球盘口 | {} | — |".format(hcp_line_disp))
    # C-20261016-120: 数据源标注 + 与 matches 赛果库对拍（自检证伪仪表——赛果库缺漏当场现形）
    if sofa_snap:
        _diffs = []
        for _s in standings:
            _dv = next((d for d in standings_derived if d["team"] == _s["team"]), None)
            if _dv is None:
                _diffs.append("{}(赛果库无完赛记录)".format(_s["team"]))
            elif _dv["played"] != _s["played"] or _dv["pts"] != _s["pts"]:
                _diffs.append("{}(库{}场{}分 vs 权威{}场{}分)".format(
                    _s["team"], _dv["played"], _dv["pts"], _s["played"], _s["pts"]))
        if _diffs:
            _tail = " 等 {} 队".format(len(_diffs)) if len(_diffs) > 3 else ""
            _tag = "⚠️ " + "；".join(_diffs[:3]) + _tail
        else:
            _tag = "✓ 全部一致"
        A("")
        A("> 📊 排名数据源: SofaScore 权威积分榜快照 {} | 与 matches 赛果库对拍: {}".format(sofa_snap, _tag))
    A("")
    A("## 十五、技术统计对比")
    A("")
    # C-20260923-063: 本报告为赛前信息快照，技术统计属赛后数据（时间语义隔离）
    # 统一占位指向复盘报告；odds500_stat 12 组指标的真正渲染在 generate_post_match_report.py
    A("(技术统计属赛后数据，本赛前报告不展示；完整对比见赛后复盘报告)")
    A("")
    A("---")
    A("")

    # ================= 十七、置信度与风险 =================
    A("## 十六、置信度评估与风险提示")
    A("")
    A("| 维度 | 评级 |")
    A("|------|:----:|")
    A("| 数据完整度 | {}% |".format(completeness))
    A("| 预测方向概率 | {} |".format(pct(wdl.get("confidence"))))
    A("| **综合置信度** | **{}/100** |".format(conf_score))
    A("")
    # P1-04: 本场个性化风险标签（自动收集风险事件，替换全局固定模板）
    risk_labels = []
    if lambda_alert and lambda_alert.get("triggered"):
        risk_labels.append("λ 进球期望差值过大：{}".format(
            lambda_alert.get("message") or "λ主客差>1.2，赛后需复核λ计算链路"))
    if om_home or om_away:
        risk_labels.append("预测首发仍为 SofaScore 模拟（临场约 1 小时才官方确认），存在突发轮换/赛前临时调整翻车风险。")
    else:
        risk_labels.append("球员预计首发、伤病全部为历史连续性推算，无官方赛前发布会确认，存在突发轮换翻车风险。")
    if divergence_std >= 0.10:
        risk_labels.append("多子模型输出高分歧（std={:.3f}），降低本场置信度。".format(divergence_std))
    _disp_live_win = num(_ouzhi.get("disp_live_win")) if _ouzhi else None
    if _disp_live_win is not None and _disp_live_win > 0.25:
        risk_labels.append("百家欧指主胜赔率离散度偏高（{:.3f}>0.25），市场观点撕裂，可靠性下降。".format(_disp_live_win))
    _prior = None
    _odds_volatile = False
    for _r in (wdl_records or []):
        if _prior is not None and _odds_delta_label(_prior, _r) not in ("平稳", "—"):
            _odds_volatile = True
            break
        _prior = _r
    if _odds_volatile:
        risk_labels.append("时序赔率检测到显著异动（相邻快照赔率变动≥0.05）。")
    if _conflict_result is not None and _conflict_result.is_conflict:
        risk_labels.append(
            "跨数据源冲突：SofaScore 基本面（主胜{:.0%}）与 500 市场信号（主胜{:.0%}）"
            "方向分歧（冲突分 {:.2f}），需警惕单边极端值误导。".format(
                _conflict_result.fund_home_prob,
                _conflict_result.market_home_prob,
                _conflict_result.conflict_score))
    # C-20261014-086: 平局高危旗标追加风险标签（开关默认关；与 §一小注/影子共用同一判定）
    if draw_flag_on and draw_flag and draw_flag["flag"]:
        risk_labels.append(DRAW_FLAG_TEXT)
    A("```")
    A("⚠️  本场专属风险标签")
    A("─────────────────────────────────────────────────")
    if risk_labels:
        for _i, _rl in enumerate(risk_labels, 1):
            A("{}. ⚠️ {}".format(_i, _rl))
    else:
        A("（本场无显著风险事件）")
    A("")
    A("通用免责声明：")
    A("1. 本报告由 AI 模型生成，仅供参考，不构成投注建议。")
    A("2. 足球比赛存在高度不确定性，模型概率 ≠ 比赛结果。")
    A("3. 综合置信度 {}/100；若数据完整度 <80%，请谨慎参考。".format(conf_score))
    A("─────────────────────────────────────────────────")
    A("```")
    A("")
    A("---")
    A("")

    # ================= 十八、知识库参考（B2：读同联赛 L2 经验） =================
    A("## 十七、知识库参考（经验沉淀）")
    A("")
    kb_rows = None
    if _kb_get_insights is not None:
        try:
            kb_rows = []
            for it in _kb_get_insights(league):
                src_n = len(it.get("source_matches") or [])
                kb_rows.append("| {} | {} | {} | {} |".format(
                    it.get("confidence"), it.get("content"), src_n,
                    (it.get("note") or "—").replace("|", "｜")))
                if _kb_record_hit is not None:
                    _kb_record_hit("feature_insights", it.get("_league"), it.get("id"),
                                   1, None, src_n, "报告L2读取(仅展示)")
        except Exception:
            kb_rows = None  # 读取异常 → 降级标注，不影响报告
    if kb_rows is None:
        A("(知识库读取失败，本场结论不受影响)")
    elif kb_rows:
        A("> 以下为 {} 联赛历史复盘沉淀的**有效特征经验**（confidence≥4，来源 A5 人工审核确认；仅供特征开发参考，不自动改参）。".format(league))
        A("")
        A("| 可信度 | 经验内容 | 来源场次 | 备注 |")
        A("|:------:|---------|:------:|------|")
        for r in kb_rows:
            A(r)
        A("")
    else:
        if _kb_record_hit is not None:
            _kb_record_hit("feature_insights", league, None, 0, None, 0, "报告L2未命中")
        A("> {} 联赛暂无已审核（≥4 级）经验条目，知识库处于早期积累阶段（随 A5 人工审核推进自动增长）。".format(league))
        A("")
    A("> **L1 统计先验**已在模型侧直接应用（联赛 ρ 校准，如 ρ=-0.30 低比分修正）；**L3 样本权重**将在重训时按复盘偏差加权（见 B3）。")
    A("")

    # ================= 十七之二、人工修正规则提示（B4-1 短期规则覆盖层） =================
    if _kb_get_correction_rules is not None:
        try:
            corr_rules = [r for r in _kb_get_correction_rules() if r.get("league") == league]
            if corr_rules:
                A("## 十七之二、人工修正规则提示（B4 短期规则覆盖层）")
                A("")
                A("> {} 联赛存在人工确认的归因修正规则（来源 A5 审核），提示后续复盘/预测时优先检查该类偏差。".format(league))
                A("")
                A("| 出现次数 | 修正方向 | 来源场次 | 修正原因示例 |")
                A("|:------:|---------|---------|-------------|")
                for r in corr_rules:
                    A("| {} | 原归因 → **{}** | {} | {} |".format(
                        r.get("count"), r.get("corrected_attribution"),
                        "、".join(r.get("sample_matches") or []),
                        (r.get("reasons") or ["—"])[0].replace("|", "｜")))
                A("")
                A("> 本提示仅作人工参考，不自动改概率；规则覆盖（改预测）需人工在预测侧确认。")
                A("")
        except Exception:
            pass  # 规则读取异常 → 静默降级，不影响报告

    A("---")
    A("")

    # ================= 十七之三、数据质量清单（逐模块状态） =================
    A("## 十七之三、数据质量清单（逐模块自检）")
    A("")
    A("> 本场赛前采集到的各数据模块状态：✅ OK / ⚠️ WARN / ❌ FAIL。WARN/FAIL 模块可能影响置信度。")
    A("")
    A("| 模块 | 状态 | 说明 |")
    A("|------|:----:|------|")
    _status_icon = {"OK": "✅ OK", "WARN": "⚠️ WARN", "FAIL": "❌ FAIL"}
    for _qname, _qstatus, _qdetail in _quality_items:
        A("| {} | {} | {} |".format(_qname, _status_icon.get(_qstatus, _qstatus), _qdetail))
    A("")
    _warn_fail_count = sum(1 for _, s, _ in _quality_items if s in ("WARN", "FAIL"))
    if _warn_fail_count == 0:
        A("> ✅ 本场所有数据模块采集完整，模型输入质量良好。")
    else:
        # C-20261011-039：原文案「已在置信度计算中自动扣除相应分数」以偏概全——
        # 仅三通道缺失降完整度基准、时序不足独立扣分（-3/-5），其余模块 WARN/FAIL 不影响置信度。
        A("> ⚠️  本场有 {} 个数据模块处于 WARN/FAIL 状态。模块状态不直接进置信度扣分项：".format(_warn_fail_count))
        A("> 竞彩时序 / 500 欧指 / SofaScore 赛前特征三通道缺失会降低完整度基准分；竞彩时序快照不足另有独立扣分（-3/-5）；")
        A("> 其余模块（投注分析/球员伤病/积分榜等）的 WARN/FAIL 仅作数据质量提示，不影响综合置信度。")
    A("")
    A("---")
    A("")

    # ================= 十九、模型元数据 =================
    A("## 十八、模型元数据与数据来源")
    A("")
    A("| 项目 | 值 |")
    A("|------|-----|")
    A("| 模型版本 | {} |".format(MODEL_VERSION))
    A("| 模型结构 | {} |".format(MODEL_HEADER))
    A("| WDL 方法 | {} |".format(wdl_method))
    A("| 让球方法 | {} |".format(hcp_method))
    A("| 比分方法 | {} |".format(score_method))
    A("| 总进球方法 | {} |".format(tg_method))
    A("")
    A("| 数据源 | 状态 |")
    A("|--------|:----:|")
    if wdl_snap >= 2:
        sporttery_status = "已接入(≥2快照)"
    elif _wdl_src == "fallback_deep":
        sporttery_status = "未开售WDL(让球等已接入,500兜底)"  # 玩法缺位，非数据缺失
    elif _wdl_src == "fallback_none":
        sporttery_status = "未开售本场(500兜底)"  # 四玩法全缺位，非采集缺失
    elif wdl_snap == 1:
        sporttery_status = "仅1快照(缺漂移)"
    else:
        sporttery_status = "缺失"
    A("| 竞彩网时序赔率 | {} |".format(sporttery_status))
    A("| 500.com 百家欧指/投注 | {} |".format("已接入" if extra["500"]["ouzhi"] else "缺失"))
    A("| SofaScore 赛前特征 | {} |".format("已接入" if extra["sofascore"] else "缺失"))
    A("")
    A("---")
    A("")
    A("> 报告由 {} 自动生成。".format(Path(__file__).name))

    # P0-07: 报告构建日志（分模块状态），供自动化审计与降级定位；随报告同目录写 _build_log.json
    build_log = {
        "match_id": "{}_{}_{}".format(match_date, home_en, away_en),
        "home_team": home_cn, "away_team": away_cn, "league": league,
        "generated_at": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        "report_version": REPORT_VERSION,
        "model_version": MODEL_VERSION,
        "feature_version": FEATURE_VERSION,
        "config_version": CONFIG_VERSION,
        "completeness": completeness,
        "sections": {
            "wdl": "ok" if hp is not None else "missing",
            "hcp": "ok" if hcp_upper is not None else "missing",
            "score": "ok" if top_scores else "missing",
            "tg": "ok" if tg_over is not None else "missing",
            "sporttery_timing": "ok" if wdl_snap >= 2 else (
                "partial" if (wdl_snap == 1 and _wdl_src == "jc") else (
                    "degraded" if wdl_snap == 1 else "missing")),
            "ev_engine": "ok" if ev_analysis is not None else "degraded",
            "ouzhi_500": "ok" if _has_real_500 else "missing",
            "betting_500": _betting_status_label(betting),
            "sofascore_team": "ok" if sof else "missing",
            "standings": "ok" if standings_ok else "incomplete",
            "knowledge_base": "ok" if _kb_get_insights is not None else "missing",
            # P1 报告增强项（阶段 2 新增）
            "p1_01_conf_breakdown": "ok" if _conf_breakdown else "missing",
            "p1_02_sub_models": "ok" if n_sub_models >= 1 else (
                "partial" if sub_models else "missing"),
            "p1_03_timing_grid": "ok" if wdl_snap >= 2 else (
                "partial" if wdl_snap == 1 else "missing"),
            "p1_05_kelly_footnote": "ok",
            "p1_06_edge_ev_conflict": "ok" if ev_analysis is not None else "degraded",
            "p1_07_layer_hint": "ok",
            "p1_08_ev_snapshot_trace": "ok" if ev_analysis is not None else "degraded",
        },
    }

    report_path.write_text("\n".join(L), encoding="utf-8")

    # P0-07: 报告构建日志落盘，供自动化审计与降级定位（分模块 ✅/⚠️/❌ + 完整度）
    try:
        log_path = report_path.parent / (report_path.stem + "_build_log.json")
        log_path.write_text(json.dumps(build_log, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as _log_err:
        print(f"  ⚠️ 写入 build_log 失败: {_log_err}")

    # C-20260930-002 Fix A：同口径快照追加落 prematch_data_quality（历史不覆盖；
    # 归因引擎维度1权威源）。异常已在 helper 内全隔离。
    _persist_prematch_quality(
        conn, m, match_date, build_log, _quality_items, wdl_snap, report_path,
        wdl_not_offered=bool(result.get("_wdl_not_offered")),
        betting_crawl_status=_bet_crawl,
    )

    return completeness


def pct_with_sign(edge):
    if edge is None:
        return "—"
    return "{}{}%".format("+" if edge > 0 else "", fnum(edge * 100, 1))


def compute_confidence(completeness, model_conf):
    model_c = (model_conf or 0) * 100
    return int(round(0.5 * completeness + 0.5 * model_c))


def compute_confidence_breakdown(completeness, model_conf, divergence_std=0.0, n_sub=0,
                                wdl_snap=0, total_sub=5, extra_penalty=0.0, wdl_source="jc"):
    """P1-01: 综合置信度拆解 = 基准分 + 加分项 - 扣分项。

    返回 dict: {score, base, components: [{name, type, value, reason}], explanation}
    - base  = 0.5*completeness + 0.5*model_c （即旧 compute_confidence 输出，保持向后兼容基线）
    - 扣分项: 子模型分歧度 / 时序赔率快照不足 / 子模型覆盖度不足 / extra_penalty（如跨源冲突）
    - score = clamp(base - sum(deductions), 0, 100)
    - C-20261002-005：wdl_source 三态（jc/fallback_deep/fallback_none）区分「真·竞彩单快照」
      与「竞彩未开盘、500 初盘兜底」，避免把兜底静态初盘误述为时序快照（分值不变，仅文案分档）。
    """
    model_c = (model_conf or 0) * 100
    # C-20261011-036：基准分 = 两个展示组件各自取整后求和，消除「逐项进位 vs 总和取整」
    # 的 ±1 口径冲突（旧实现 base=round(41.5+19.5)=61 而组件显示 42+20=62）。
    _c1 = int(round(0.5 * completeness))
    _c2 = int(round(0.5 * model_c))
    base = _c1 + _c2
    components = [
        {"name": "数据完整度", "type": "base", "value": _c1,
         "reason": "三通道（竞彩时序+500+SofaScore）各占 1/3，完整度 {}%".format(completeness)},
        {"name": "模型方向概率", "type": "base", "value": _c2,
         "reason": "WDL 主预测方向概率 {}%".format(int(round(model_c)))},
    ]
    deductions = []
    # 子模型分歧度
    if divergence_std >= 0.10:
        d = 5
        lvl = "高分歧"
    elif divergence_std >= 0.05:
        d = 2
        lvl = "中分歧"
    else:
        d = 0
        lvl = "低分歧"
    if d:
        deductions.append({"name": "子模型分歧度", "type": "deduct", "value": -d,
                           "reason": "{}（std={:.3f}，{}）".format(lvl, divergence_std, lvl)})
    # 时序赔率快照不足（C-20261002-005：按 WDL 来源分档，扣分口径不变）
    if wdl_snap == 0:
        d = 5
        r = "无时序赔率快照，缺少开盘→即时漂移信息"
    elif wdl_snap == 1 and wdl_source == "jc":
        d = 3
        r = "仅 1 条竞彩时序快照，无法观测漂移"
    elif wdl_snap == 1 and wdl_source == "fallback_deep":
        d = 3
        r = "竞彩未开售胜平负盘（已开让球/大小球/比分），WDL 为 500 欧指初盘兜底，无时序漂移"
    elif wdl_snap == 1:  # fallback_none：四玩法全未开售
        d = 3
        r = "竞彩未开售本场，WDL 为 500 欧指初盘兜底，无时序漂移"
    else:
        d = 0
        r = ""
    if d:
        deductions.append({"name": "时序赔率不足", "type": "deduct", "value": -d, "reason": r})
    # 子模型覆盖度不足
    missing = max(0, total_sub - (n_sub or 0))
    if missing > 0:
        d = min(5, missing * 1)
        deductions.append({"name": "子模型覆盖不足", "type": "deduct", "value": -d,
                           "reason": "5 子模型仅 {} 个可用，缺 {} 个".format(n_sub, missing)})
    # P2-04: 跨数据源冲突额外扣分（extra_penalty 由调用方传入，如基本面 vs 市场分歧）
    if extra_penalty and extra_penalty > 0:
        ep = round(min(10.0, float(extra_penalty)), 1)
        deductions.append({"name": "跨数据源冲突", "type": "deduct", "value": -ep,
                           "reason": "SofaScore 基本面与 500 市场信号分歧，冲突分加权扣分 {}".format(ep)})
    total_deduction = sum(abs(it["value"]) for it in deductions)
    score = max(0, min(100, base - total_deduction))
    explanation = "基准 {} - 扣分 {} = {}".format(base, total_deduction, score)
    return {
        "score": score, "base": base,
        "components": components + deductions,
        "explanation": explanation,
    }


# P1-02: 5 子模型展示顺序与中文名（对齐 MODEL_HEADER "DC+XGB+LGB+Elo+Bayes"，与 STACKING_META_MODELS 一致）
SUB_MODEL_LABELS = [
    ("dixonColes", "Dixon-Coles"),
    ("xgboost", "XGBoost"),
    ("lightgbm", "LightGBM"),
    ("elo", "Elo"),
    ("bayesian", "贝叶斯"),
]


def _sub_model_divergence(sub_models):
    """5 子模型概率分歧度 = 三向概率的标准差均值。返回 (等级, std, 可用模型数)。

    等级阈值：<0.05 低 / 0.05~0.10 中 / ≥0.10 高（std 越大说明子模型间分歧越大）。
    """
    avail = {}
    for name, _lbl in SUB_MODEL_LABELS:
        p = (sub_models or {}).get(name)
        if p and all(isinstance(p.get(k), (int, float)) for k in ("win", "draw", "lose")):
            avail[name] = [float(p["win"]), float(p["draw"]), float(p["lose"])]
    n = len(avail)
    if n < 2:
        return "低", 0.0, n
    stds = [statistics.pstdev([v[i] for v in avail.values()]) for i in range(3)]
    std = sum(stds) / 3.0
    lvl = "高" if std >= 0.10 else ("中" if std >= 0.05 else "低")
    return lvl, std, n


def _odds_delta_label(prev, cur):
    """相邻两快照赔率变化 → 异动标签；显著阈值 0.05，无变化返回 '平稳'。"""
    if prev is None or cur is None:
        return "—"
    labels = []
    for key, cn in (("win", "主胜"), ("draw", "平局"), ("lose", "客胜")):
        d = num(cur.get(key)) - num(prev.get(key))
        if d is None:
            continue
        if d <= -0.05:
            labels.append("{}↓".format(cn))
        elif d >= 0.05:
            labels.append("{}↑".format(cn))
    return "、".join(labels) if labels else "平稳"


# ============================================================
# 主流程
# ============================================================
def build_match_dict(m, hcp_line):
    return {
        "home_team": m["home_team_en"],
        "away_team": m["away_team_en"],
        "home_team_cn": m["home_team_cn"],
        "away_team_cn": m["away_team_cn"],
        "league": m["league"],
        # C-20261016-118: 兜底场次未命中 500 轮次时写 None，避免「第None轮」
        "round": (f"第{m['round']}轮" if m["round"] is not None else None),
        "match_date": m["match_date"],
        "match_time": f"{m['match_date']} {m['match_time']}",
        "handicap_line": hcp_line,
    }


def write_daily_summary(day_rows, md=None):
    """按比赛日写 docs/prematch_reports/<yyyymmdd>/_summary.md。

    C-20260919-022 从 run() 提取：回放批量重生成整天报告后可直接调用刷新汇总。
    """
    md = md or day_rows[0]["match_date"]
    out_dir = BASE_DIR / "docs" / "prematch_reports" / md.replace("-", "")
    out_dir.mkdir(parents=True, exist_ok=True)
    sum_path = out_dir / "_summary.md"
    sum_lines = [f"# 赛前预测汇总 — {md}", ""]
    sum_lines.append(f"> 生成时间: {datetime.now(CN_TZ).strftime('%Y-%m-%d %H:%M:%S')} | 共 {len(day_rows)} 场")
    sum_lines.append("")
    sum_lines.append("| # | 联赛 | 对阵 | WDL预测 | 概率(主/平/客) | 最可能比分 | 推荐 | 报告 |")
    sum_lines.append("|:--:|------|------|:--------:|:--------------:|:----------:|------|------|")
    for i, r in enumerate(day_rows, 1):
        w = r["wdl"]
        rec_dir = "主胜" if w.get("home_prob", 0) == max(w.get("home_prob", 0), w.get("draw_prob", 0), w.get("away_prob", 0)) else ("平局" if w.get("draw_prob", 0) == max(w.get("home_prob", 0), w.get("draw_prob", 0), w.get("away_prob", 0)) else "客胜")
        sum_lines.append("| {} | {} | {} vs {} | **{}** | {}/{}/{} | {} | {} | [链接]({}) |".format(
            i, r["league"], r["home"], r["away"], w.get("prediction", "—"),
            fnum((w.get("home_prob") or 0) * 100, 1), fnum((w.get("draw_prob") or 0) * 100, 1),
            fnum((w.get("away_prob") or 0) * 100, 1), r["score"].get("most_likely", "—"),
            rec_dir, r["_file"]))
    sum_lines.append("")

    # 数据完整性告警：WDL 快照<2 或完整度<80% 的场次单独列出
    alerts = [r for r in day_rows if r.get("_wdl_snapshots", 0) < 2 or r.get("_completeness", 100) < 80]
    if alerts:
        sum_lines.append("## ⚠️ 数据完整性告警")
        sum_lines.append("")
        sum_lines.append("| 联赛 | 对阵 | WDL快照 | 完整度 | 说明 |")
        sum_lines.append("|------|------|:-------:|:------:|------|")
        for r in alerts:
            snap = r.get("_wdl_snapshots", 0)
            if r.get("_wdl_not_offered"):
                why = "竞彩未开售胜平负盘（非缺失）"
            elif snap == 0:
                why = "无竞彩WDL时序"
            else:
                why = "仅1条快照缺漂移"
            sum_lines.append("| {} | {} vs {} | {} | {}% | {} |".format(
                r["league"], r["home"], r["away"], snap, r.get("_completeness", 100), why))
        sum_lines.append("")
    sum_lines.append("---")
    sum_lines.append("")
    sum_lines.append(f"> 本汇总由 generate_unified_report.py {REPORT_VERSION} 自动生成。")
    sum_path.write_text("\n".join(sum_lines), encoding="utf-8")
    print(f"\n✅ 汇总: {sum_path} ({len(day_rows)} 场)")


def run(args):
    conn = connect_db()
    start = args.date if args.date else datetime.now(CN_TZ).strftime("%Y-%m-%d")
    if args.days > 1:
        end = (datetime.strptime(start, "%Y-%m-%d") + timedelta(days=args.days - 1)).strftime("%Y-%m-%d")
    else:
        end = start
    leagues = [x.strip() for x in args.league.split(",")] if args.league else None

    matches = discover_matches(conn, start, end, leagues, played_only=args.replay)
    # 物理合理性校验：过滤掉"同队背靠背<2天"的幽灵赛程（500.com 数据源偶发错配）
    matches, ghost = filter_ghost_matches(conn, matches)
    if ghost:
        print(f"[幽灵赛程] 已过滤 {len(ghost)} 场（500.com 数据源错配/背靠背不成立）:")
        for g in ghost:
            print(f"  - {g['match_date']} {g['home_team_cn']} vs {g['away_team_cn']}"
                  f" (fid={g['fid']}) | 原因: {g.get('_ghost_reason','')}")
    if getattr(args, "teams", None):
        homes = {x.strip().replace(" ", "") for x in args.teams.split(",") if x.strip()}
        matches = [m for m in matches if (m["home_team_cn"] or "").replace(" ", "") in homes]
        print(f"[过滤] 仅保留指定主队: {sorted(x.strip() for x in args.teams.split(',') if x.strip())}")
    mode = "已赛(回放)" if args.replay else "未开赛"
    print(f"[发现] {start}~{end} {mode}比赛 {len(matches)} 场"
          + (f" (联赛过滤: {args.league})" if leagues else ""))

    if args.dry_run:
        for m in matches:
            # C-20261018-140: 冻结场 dry-run 标签（含既有预测可用性，冒烟断言用）
            _fz = ""
            if m.get("_frozen"):
                _fz = " ❄️FROZEN(既有预测✓)" if match_has_prediction(conn, m) else " ❄️FROZEN(无预测)"
            print(f"  - {m['match_date']} {m['match_time']} [{m['league']}] {m['home_team_cn']} vs {m['away_team_cn']} (盘口 {m['handicap'] or '—'}){_fz}")
        conn.close()
        return

    print("[加载] 预测模型 (prediction_core)...")
    from prediction_core import init_models, PredictionCore
    from prediction_db_writer import batch_save_predictions, serializable_to_pred
    models = init_models()
    if not args.with_epl:
        # 英超独立模型为"参考"输出，不参与 WDL Stacking；其 build_all_features 在全量
        # 数据(14000+场)上做特征构造，单场耗时数分钟，默认关闭以避免报告生成卡顿。
        models['epl'] = None
    core = PredictionCore(models)

    # C-20260919-018: 批量预测前注入待赛场（虚拟行），使 LGB/XGB 使用本场真实特征
    # 而非 fallback 模板行；wdl_match_id 用于赔率特征按竞彩键对齐
    core.wdl_predictor.prime_fixtures([
        {
            'home_team': m.get('home_team_en'), 'away_team': m.get('away_team_en'),
            'home_team_cn': m['home_team_cn'], 'away_team_cn': m['away_team_cn'],
            'league': m['league'], 'date': m['match_date'],
            'wdl_match_id': find_sporttery_match_id(conn, m['home_team_cn'], m['away_team_cn'], m['match_date']),
        }
        for m in matches
    ])

    rows = []
    _xgb_missing = []  # C-20260919-022: 记录本批 XGB 未产出的场次，结束时哨兵汇总
    for m in matches:
        home_cn, away_cn = m["home_team_cn"], m["away_team_cn"]
        print(f"\n=== {m['match_date']} {m['match_time']} [{m['league']}] {home_cn} vs {away_cn} ===")

        # 回放模式：已有预测(model_predictions)则跳过，仅回写缺预测场次
        # C-20260923-059: --force 绕过此检查以强制重生成报告（验证/修复用，建议配合 --no-write 避免污染 DB）
        if args.replay and not args.force and match_has_prediction(conn, m):
            print(f"  ⏭️ 已有预测，跳过: {home_cn} vs {away_cn}")
            continue

        # C-20261018-144: 跨日重生成封版扩展（C-143 泄漏根修②守卫）——match_date
        # 早于运行日的场次一律视同封版（补开球守卫漏判：时间解析失败等残余），
        # 禁止 live 重算子模型表；--replay/--teams 手工绕过通道不变。
        if not m.get("_frozen") and not args.replay and not getattr(args, "teams", None):
            from datetime import datetime as _dt144
            if m.get("match_date") and m["match_date"] < _dt144.now().strftime("%Y-%m-%d"):
                m["_frozen"] = True

        # C-20261018-140: 冻结场（已开球）封版分支——不重预测（防赛后数据泄漏，
        # C-053 纪律 / pred_ts_valid 门禁），summary 经 load_frozen_row 保留既有
        # 赛前预测行；--replay / --teams 为手工绕过通道（runbook 写死）。
        if m.get("_frozen") and not args.replay and not getattr(args, "teams", None):
            _fr = load_frozen_row(conn, m)
            if _fr is not None:
                rows.append(_fr)
                print(f"  ❄️ FROZEN 已开球封版: 沿用既有赛前预测（{_fr['match_id']}），不重预测")
            else:
                print(f"  ❄️ FROZEN 已开球封版: 无既有赛前预测，整场跳过（不重预测）")
            continue

        # 1. 定位竞彩时序 match_id（仅 WDL 表）
        sporttery_mid = find_sporttery_match_id(conn, home_cn, away_cn, m["match_date"])
        # 竞彩未开胜平负盘：WDL 无记录，但让球/大小球/比分有记录（深盘强队常见，非采集缺失）
        # C-20261002-004：any_mid 必须传入 build_sporttery_odds，否则三表数据被丢弃
        any_mid = (find_any_sporttery_match_id(conn, home_cn, away_cn, m["match_date"])
                   if sporttery_mid is None else None)
        wdl_not_offered = any_mid is not None
        # C-20261002-005：WDL 来源三态，驱动扣分/§七/质检/元数据四处文案分档
        #   jc=竞彩WDL有盘；fallback_deep=未开WDL但开了让球/大小/比分；fallback_none=四玩法全无
        wdl_source = "jc" if sporttery_mid is not None else (
            "fallback_deep" if any_mid is not None else "fallback_none")
        # 2. 组装 odds_data（500 欧指兜底：优先 live 欧指，回退 init 欧指）
        #    m["win/draw/lost"] 字段自 2026-10 起长期为 None，真实 500 欧指在
        #    odds500_ouzhi_summary 表（avg_live_win/draw/lose 等），故提前取
        #    data500 并从中提取兜底赔率，确保竞彩 WDL 缺失时报告仍可生成。
        data500 = load_500_data(conn, m["fid"])
        ouzhi = (data500 or {}).get("ouzhi") or {}
        fb_w = ouzhi.get("avg_live_win") or ouzhi.get("avg_init_win")
        fb_d = ouzhi.get("avg_live_draw") or ouzhi.get("avg_init_draw")
        fb_l = ouzhi.get("avg_live_lose") or ouzhi.get("avg_init_lose")
        odds_data = build_sporttery_odds(conn, sporttery_mid, fb_w, fb_d, fb_l, other_mid=any_mid)
        wdl_snapshots = len(odds_data["wdl_odds"]["records"])
        timing_complete = wdl_snapshots >= 2  # 时序漂移分析需开盘/即时两条快照
        has_odds = wdl_snapshots >= 1
        # C-20261013-067：让球线源切换——竞彩整数 goalLine（SSOT），亚盘兜底已禁用（C-066）
        # None → 跳过让球节（竞彩未开让球盘）
        # C-20261016-116：原实现按 `matches JOIN odds500_match ON match_id` 取线，而
        # 26/27 起 odds500_match.match_id 已统一为英文权威 mid（如 2026-10-16_Eintracht
        # Frankfurt_1. FC Köln），该行 handicap 为空/不存在 → 全部 24 场误报「竞彩未开
        # 让球盘」。真实让球线在竞彩中文名行（如 2026-10-17_法兰克福_科隆 = -1.0）。
        # 改为按归一化队名 + ±3 天日期邻近、handicap 非空的行取最近日期值（复用 C-114 模式）。
        _n_h, _n_a = _normalize_team_for_wdl(home_cn), _normalize_team_for_wdl(away_cn)
        _m_base = datetime.strptime(m["match_date"], "%Y-%m-%d")
        hcp_line = None
        _hcp_best_diff = 999
        for _r in conn.execute(
            "SELECT match_date, home_team, away_team, handicap FROM matches "
            "WHERE handicap IS NOT NULL "
            "AND match_date BETWEEN date(?,'-3 day') AND date(?,'+3 day')",
            (m["match_date"], m["match_date"]),
        ).fetchall():
            if (_normalize_team_for_wdl(_r[1]) != _n_h
                    or _normalize_team_for_wdl(_r[2]) != _n_a):
                continue
            _diff = abs((datetime.strptime(_r[0], "%Y-%m-%d") - _m_base).days)
            if _diff < _hcp_best_diff:
                hcp_line, _hcp_best_diff = float(_r[3]), _diff
        odds_data["handicap_odds"]["line"] = hcp_line

        if not has_odds:
            print("  ⚠️ 无胜平负赔率（竞彩 + 500 欧指均缺失），跳过预测")
            continue

        # 3. 500.com / SofaScore
        extra = {"500": data500, "sofascore": load_sofascore_data(conn, m["match_date"], home_cn)}

        # C-20260923-058 全库防复发：集中式 sofascore schema 校验（补充层，非阻断）
        _sofa_missing = audit_sofascore_schema(extra["sofascore"])
        if _sofa_missing:
            print(f"[WARN] generate_unified_report audit_sofascore_schema: 字段 {_sofa_missing} 不在 row.keys() 中（可能 typo 或 schema 变更）", file=sys.stderr)

        # 4. 预测
        match = build_match_dict(m, hcp_line)
        try:
            result = core.predict_unified(match, odds_data, is_mock=False)
        except Exception as e:
            print(f"  ❌ 预测失败: {type(e).__name__}: {e}")
            continue
        result["_has_odds"] = has_odds
        result["_wdl_snapshots"] = wdl_snapshots
        result["_timing_complete"] = timing_complete
        result["_wdl_not_offered"] = wdl_not_offered
        result["_wdl_source"] = wdl_source

        # 5. 渲染报告（按 report_date 北京归一日 落目录，C-20261016-109；缺省回退 match_date）
        _rd = m.get("report_date") or m['match_date']
        out_dir = BASE_DIR / "docs" / "prematch_reports" / _rd.replace("-", "")
        out_dir.mkdir(parents=True, exist_ok=True)
        # C-20261017-133: 文件名队名归一到 canonical 中文（同一函数 feature_utils.
        # normalize_team_name，单一源 team_names.CANONICAL_CN）——兜底防线，覆盖任何
        # 残留英文/别名路径（如 matches 表 FBref 英文行进主源的情况）。
        fname = f"{m['league']}_{_rd}_{_normalize_team_name(home_cn)}_vs_{_normalize_team_name(away_cn)}.md".replace("/", "-")
        report_path = out_dir / fname
        # C-20260922-053: 必须在 render_report 覆盖文件前解析赛前时间锚点
        replay_anchor, anchor_ev = (None, None)
        if args.replay:
            replay_anchor, anchor_ev = resolve_replay_anchor(m["match_date"], report_path)
        completeness = render_report(m, odds_data, result, extra, report_path, conn,
                                     is_replay=args.replay)
        print(f"  ✅ 报告: {report_path.name} (完整度 {completeness}%)")
        if args.replay:
            print(f"  ♻️ 回放落库锚点: {replay_anchor} (证据={anchor_ev})")

        # C-20260920-029: v5 Shadow 记录（仅 JSONL，不落库/不展示；键与 model_predictions 同口径）
        _shadow_v5 = result.get("_shadow_v5")
        if _shadow_v5:
            # C-20261006-005: dist 仅供持久化，pop 出避免写入 jsonl（保持原记录口径）
            _v5_dist = _shadow_v5.pop("dist", None) if isinstance(_shadow_v5, dict) else None
            _v5_mid = f"{m['match_date']}_{m['home_team_en']}_{m['away_team_en']}"
            append_shadow_v5({
                "match_id": _v5_mid,
                "gen_time": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "match_date": m["match_date"],
                "league": m["league"],
                "home": home_cn, "away": away_cn,
                "v4_top5": [{"score": s.get("score"), "prob": round(float(s.get("prob") or 0), 4)}
                            for s in (result["score"].get("top5") or [])],
                "v4_most_likely": result["score"].get("most_likely"),
                "v5": _shadow_v5,
            })
            # C-20261006-005: v5 全网格日常落库（修复 09-08 起 Score_grid 断供——此前仅
            # backfill 一次性覆盖 ≤09-07；异常就地隔离，绝不影响报告生成与 jsonl 记录）。
            if _v5_dist:
                try:
                    from score_prediction_module import save_score_prediction
                    save_score_prediction(conn, _v5_mid, {"score_probs": _v5_dist})
                except Exception as _e:
                    print(f"  ⚠️ v5 网格落库失败（已隔离，不影响报告）: {type(_e).__name__}: {_e}")

        # C-20260921-036: TG λ 校准 Shadow 记录（仅 JSONL；factor=1.0 的 fallback 场也记录，
        # 便于监控激活率；生产 result['tg'] / model_predictions 值不变）
        _shadow_tg = result.get("_shadow_tg_calib")
        if _shadow_tg and not _shadow_tg.get("error"):
            append_tg_calib_shadow({
                "match_id": f"{m['match_date']}_{m['home_team_en']}_{m['away_team_en']}",
                "gen_time": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "match_date": m["match_date"],
                "league": m["league"],
                "home": home_cn, "away": away_cn,
                "lambda_home": result["score"].get("lambda_home"),
                "lambda_away": result["score"].get("lambda_away"),
                "tg_calib": _shadow_tg,
            })

        # P1-1（C-20261002-006）: λ 拆分 bisect Shadow 记录（仅 JSONL，不落库/不展示；
        # control 即生产 legacy 结果，shadow 仅替换 λ 拆分且总量逐位相同）
        _shadow_ls = result.get("_shadow_lambda_split")
        if _shadow_ls and not _shadow_ls.get("error") and not _shadow_ls.get("skipped"):
            append_lambda_split_shadow({
                "match_id": f"{m['match_date']}_{m['home_team_en']}_{m['away_team_en']}",
                "gen_time": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "match_date": m["match_date"],
                "league": m["league"],
                "home": home_cn, "away": away_cn,
                "lambda_split": _shadow_ls,
            })

        # C-20260921-040: TG 融合权重 Shadow 记录（仅 JSONL；w_odds=0.30 vs 生产 0.15）
        _shadow_wodds = result.get("_shadow_tg_wodds")
        if _shadow_wodds and not _shadow_wodds.get("error"):
            append_tg_wodds_shadow({
                "match_id": f"{m['match_date']}_{m['home_team_en']}_{m['away_team_en']}",
                "gen_time": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                "match_date": m["match_date"],
                "league": m["league"],
                "home": home_cn, "away": away_cn,
                "lambda_home": result["score"].get("lambda_home"),
                "lambda_away": result["score"].get("lambda_away"),
                "tg_wodds": _shadow_wodds,
            })

        # C-20261013-072: class_weight 平局判别力 Shadow 记录（仅 JSONL;
        # 4 个候选 ratio vs 生产 baseline_2.5; 赛后 JOIN post_match_review 做四门禁）
        _shadow_cw = (result.get("hcp") or {}).get("_shadow_class_weight")
        if _shadow_cw and not _shadow_cw.get("error"):
            try:
                from class_weight_shadow_writer import append_class_weight_shadow
                append_class_weight_shadow({
                    "match_id": f"{m['match_date']}_{m['home_team_en']}_{m['away_team_en']}",
                    "gen_time": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "match_date": m["match_date"],
                    "league": m["league"],
                    "home": home_cn, "away": away_cn,
                    "control": _shadow_cw.get("control"),
                    "shadow": _shadow_cw.get("shadow"),
                    "join_key": f"{m['match_date']}_{m['home_team_en']}_{m['away_team_en']}",
                })
            except Exception as _e:
                print(f"  ⚠️ class_weight shadow jsonl 写入失败（已隔离）: {type(_e).__name__}: {_e}")

        # C-20261014-086: 平局高危旗标 Shadow（恒开不受开关影响；每场必写一行含 flag=false，
        # 覆盖率门禁分母；与 §一 Stacking 行同源 result["wdl"]，赛后 JOIN 实际比分做三门禁）
        try:
            _w = result.get("wdl") or {}
            _df = compute_draw_high_risk_flag(
                _w.get("home_prob"), _w.get("draw_prob"), _w.get("away_prob"))
            if _df is not None:
                append_draw_flag_shadow({
                    "match_id": f"{m['match_date']}_{m['home_team_en']}_{m['away_team_en']}",
                    "gen_time": datetime.now(CN_TZ).strftime("%Y-%m-%d %H:%M:%S"),
                    "match_date": m["match_date"],
                    "league": m["league"],
                    "home": home_cn, "away": away_cn,
                    "flag": _df["flag"],
                    "spread": _df["spread"],
                    "p_draw": _df["p_draw"],
                })
        except Exception as _e:
            print(f"  ⚠️ draw_flag shadow jsonl 写入失败（已隔离）: {type(_e).__name__}: {_e}")

        # 6. 汇总行 + 回写数据
        wdl = result["wdl"]; hcp = result["hcp"]; score = result["score"]; tg = result["tg"]

        # C-20260919-022: XGB 缺失登记（报告内已有告警，这里供批次结束时统一哨兵）
        _xp = (wdl.get("sub_models") or {}).get("xgboost")
        if not _xp or not all(isinstance(_xp.get(k), (int, float)) for k in ("win", "draw", "lose")):
            _xgb_missing.append(f"{home_cn} vs {away_cn}")

        # EV 决策摘要（供落库回测）
        ev = result.get("ev_analysis")
        ev_summary = None
        if ev is not None:
            ev_summary = {
                "decision": ev.overall_decision,
                "direction": ev.best_direction_cn if ev.overall_decision != "AVOID" else None,
                "best_ev_direction": ev.best_direction,  # P0-04: home/draw/away（AVOID 时为 None）
                "best_ev": ev.best_ev,
                "best_edge": ev.best_edge,
                "stake_pct": ev.recommended_stake_pct,
                "kelly_fraction": ev.best_kelly,  # P0-04: 建议仓位（1/4 凯利已裁剪）
                "vig": ev.vig,
                "payout_rate": ev.payout_rate,
                # P0-04: 每方向 EV / edge 完整落库（脱离报告可复现全场 EV）
                "ev_home": ev.home_analysis.ev if ev.home_analysis else None,
                "ev_draw": ev.draw_analysis.ev if ev.draw_analysis else None,
                "ev_away": ev.away_analysis.ev if ev.away_analysis else None,
                "edge_home": ev.home_analysis.edge if ev.home_analysis else None,
                "edge_draw": ev.draw_analysis.edge if ev.draw_analysis else None,
                "edge_away": ev.away_analysis.edge if ev.away_analysis else None,
                # P1-10/P1-11: 本场实际 EV 配置与分层标记随行落库
                "ev_threshold_used": ev.ev_threshold,
                "kelly_strategy": ev.kelly_strategy,
                "kelly_cap": ev.kelly_cap,
                "match_ev_category": ev.match_ev_category,
                "odds_used_for_ev": json.dumps({
                    "source": "竞彩 WDL 尾盘 (close)",
                    "close": odds_data.get("wdl_odds", {}).get("close"),
                }, ensure_ascii=False),
            }

        # P0-06: 输入快照（用于历史预测可复现，随行落库）
        input_snapshot = json.dumps({
            "match_date": m["match_date"],
            "feature_version": FEATURE_VERSION,
            "config_version": CONFIG_VERSION,
            "handicap": hcp_line,
            "wdl_snapshots": wdl_snapshots,
            "wdl_open": odds_data["wdl_odds"].get("open"),
            "wdl_close": odds_data["wdl_odds"].get("close"),
        }, ensure_ascii=False)

        rows.append({
            "match_date": m["match_date"],
            "report_date": m.get("report_date"),  # C-20261016-109: 北京归一日（仅分组/汇总用）
            "league": m["league"],
            "home": home_cn, "away": away_cn,
            "home_en": m["home_team_en"], "away_en": m["away_team_en"],
            # C-20261016-118: 兜底场次未命中 500 轮次时写 None，避免「第None轮」
            "round": (f"第{m['round']}轮" if m["round"] is not None else None),
            "match_time": f"{m['match_date']} {m['match_time']}",
            "wdl": {"home_prob": wdl.get("home_prob"), "draw_prob": wdl.get("draw_prob"),
                    "away_prob": wdl.get("away_prob"), "prediction": wdl.get("prediction")},
            "hcp": {"line": hcp_line, "home_win_prob": hcp.get("home_win_prob"),
                    "draw_prob": hcp.get("draw_prob"), "away_win_prob": hcp.get("away_win_prob")},
            "score": {"lambda_home": score.get("lambda_home"), "lambda_away": score.get("lambda_away"),
                      "most_likely": score.get("most_likely"),
                      # C-20260921-034: 真实发布 Top5 随落库（复盘只读不重推）
                      "top5": [{"score": s.get("score"), "prob": s.get("prob")}
                               for s in (score.get("top5") or [])][:5]},
            "tg": {"over_25_prob": tg.get("over_25_prob"),
                   # C-20260921-035: 真实发布精确进球 Top3 随落库
                   "top3": [{"goals": x.get("goals"), "label": x.get("label"), "prob": x.get("prob")}
                            for x in (tg.get("top3") or [])][:3]},
            "ev": ev_summary,
            "_input_snapshot_json": input_snapshot,
            "_feature_version": FEATURE_VERSION,
            "_config_version": CONFIG_VERSION,
            # C-20260922-053: 回放补算的赛前时间锚点（普通模式恒 False/None）
            "_is_replay": bool(args.replay),
            "_pred_timestamp": replay_anchor,
            "_file": fname,
            "_wdl_snapshots": wdl_snapshots,
            "_wdl_not_offered": wdl_not_offered,
            "_wdl_source": wdl_source,
            "_completeness": completeness,
            "lambda_alert": result.get("lambda_alert"),  # P1-13: λ 主客差值告警
            # C-20261011-049: 5 子模型 WDL 概率随批透传落库（C-046⑧/C-048 影子线数据地基）
            "sub_models": wdl.get("sub_models"),
        })

    # 6b. C-20260919-019: 方向分布哨兵——防止主客系统性反转再次悄无声息
    # （历史异常：32场29主、20场17客；正常窗口单侧占比一般 <85%）
    if len(rows) >= 8:
        from collections import Counter
        _dir_count = Counter(r["wdl"]["prediction"] for r in rows)
        _top_dir, _top_n = _dir_count.most_common(1)[0]
        if _top_n / len(rows) >= 0.85:
            print(f"\n🚨 [方向告警] {len(rows)} 场中 {_top_dir} 占 {_top_n} 场 "
                  f"({_top_n/len(rows):.0%})，分布极端异常（疑似主客反转/特征污染），"
                  f"请人工核对子模型日志后再采信本批报告！")

    # C-20260919-022: XGB 缺失批次哨兵——任一报告 XGB 为 — 即醒目汇总，杜绝再次遗漏
    if _xgb_missing:
        print(f"\n🚨 [XGB 哨兵] 本批 {len(_xgb_missing)}/{len(rows)} 场 XGBoost 未产出（报告显示 —）：")
        for _nm in _xgb_missing:
            print(f"   - {_nm}")
        print("   请检查 XGB 模型加载/scaler/特征提取日志（WDL模型·哨兵），修复后重新生成本批报告！")

    # 7. 汇总报告（按比赛日 match_date 分组）
    # 回放模式只补单场报告，不重写 _summary.md（避免覆盖"仅回放子集"覆盖已有汇总）
    if args.replay:
        print("\n[回放模式] 跳过 _summary.md 汇总重写（避免覆盖已有汇总）")
    rows_by_date = {}
    for r in rows:
        rows_by_date.setdefault(r.get("report_date") or r["match_date"], []).append(r)  # C-20261016-109 北京归一分组

    if not args.replay:
        for md, day_rows in rows_by_date.items():
            write_daily_summary(day_rows, md)

    # 8. 回写 model_predictions
    if rows and not args.no_write:
        try:
            # C-20261018-140: 冻结行（_frozen）不回写——既有赛前预测原样保留，
            # 重跑只恢复 summary 展示，绝不用赛后窗口数据覆盖 pred_ts_valid 门禁字段
            ids = batch_save_predictions([serializable_to_pred(r) for r in rows if not r.get("_frozen")])
            print(f"✅ 已回写 {len(ids)} 场到 model_predictions: {ids}")
        except Exception as e:
            print(f"⚠️ 回写 model_predictions 失败: {type(e).__name__}: {e}")

        # C-20261011-049: 5 子模型 WDL 概率落库（C-046⑧/C-048 影子线数据地基）。
        # 独立 try/except 守卫：失败只告警，不阻断主预测回写与报告产出。
        try:
            from prediction_db_writer import batch_save_submodel_probs
            _n_sub = batch_save_submodel_probs([r for r in rows if not r.get("_frozen")])  # C-20261018-140: 冻结行不回写
            print(f"✅ 已回写子模型概率 {_n_sub} 行（{len(rows)} 场 × 最多 15，Submodel_* 类型）")
        except Exception as e:
            print(f"⚠️ 子模型概率回写失败（不影响主链路）: {type(e).__name__}: {e}")

    conn.close()
    print(f"\n完成。报告目录: {BASE_DIR / 'docs' / 'prematch_reports'}（按比赛日 match_date 分目录）")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="统一赛前预测报告生成器")
    parser.add_argument("--date", help="开始日期 YYYY-MM-DD（默认今天）")
    parser.add_argument("--days", type=int, default=1, help="覆盖天数（默认1）")
    parser.add_argument("--league", help="联赛过滤，逗号分隔（英超,西甲,意甲,德甲,法甲）")
    parser.add_argument("--teams", help="只生成指定主队（中文名，逗号分隔）的比赛报告")
    parser.add_argument("--dry-run", action="store_true", help="只列比赛不预测")
    parser.add_argument("--no-write", action="store_true", help="不回写 model_predictions")
    parser.add_argument("--with-epl", action="store_true", help="启用英超独立参考模型（极慢，默认关闭）")
    parser.add_argument("--replay", action="store_true", help="回放模式：枚举已赛(status 2/5)场次，用已入库赔率+特征补生成赛前报告")
    parser.add_argument("--force", action="store_true", help="强制重生成报告（绕过 replay 的「已有预测跳过」检查，配合 --no-write 可只刷新报告 md 不污染 model_predictions）")
    args = parser.parse_args()
    run(args)