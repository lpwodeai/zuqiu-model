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

try:
    import ev_engine as _ev_engine
except Exception:  # EV引擎缺失时降级为原有凯利分析，不阻断报告生成
    _ev_engine = None

try:
    from knowledge_base_schema import get_insights as _kb_get_insights
    from knowledge_base_schema import get_correction_rules as _kb_get_correction_rules
except Exception:  # B1 知识库缺失时降级标注，不阻断报告生成
    _kb_get_insights = None
    _kb_get_correction_rules = None

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
    """从 odds500_match 取比赛清单。

    played_only=False：未开赛(status=1)，供日常赛前预测；
    played_only=True ：已赛(status IN (2,5))，供历史补生成回放（用已入库赔率+特征）。
    """
    status_cond = "status IN (2,5)" if played_only else "status = 1"
    sql = """
        SELECT fid, league, season, round, match_date, match_time,
               home_team_cn, away_team_cn, home_team_en, away_team_en,
               win, draw, lost, handicap, pan
        FROM odds500_match
        WHERE season = '26/27' AND {status_cond}
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
    for r in rows:
        d = dict(r)
        d["home_team_en"] = (d.get("home_team_en") or "").strip()
        d["away_team_en"] = (d.get("away_team_en") or "").strip()
        # 队名若为中文残留（部分升班马无英文），回退到中文名占位
        if not d["home_team_en"] or any("\u4e00" <= ch <= "\u9fff" for ch in d["home_team_en"]):
            d["home_team_en"] = d["home_team_cn"]
        if not d["away_team_en"] or any("\u4e00" <= ch <= "\u9fff" for ch in d["away_team_en"]):
            d["away_team_en"] = d["away_team_cn"]
        out.append(d)
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


# ============================================================
# 竞彩网(Sporttery) 时序赔率组装
# ============================================================
def find_sporttery_match_id(conn, home_cn, away_cn, match_date):
    """按中文队名 + 日期邻近(±3天) 定位竞彩网时序 match_id。

    竞彩场次日期常比实际开赛日期早一天（凌晨场按销售日归属），故做日期容差。
    """
    home_cn = _normalize_team_name(home_cn)
    away_cn = _normalize_team_name(away_cn)
    like = f"%_{home_cn}_{away_cn}"
    rows = conn.execute(
        "SELECT DISTINCT match_id FROM wdl_history WHERE match_id LIKE ?", (like,)
    ).fetchall()
    if not rows:
        return None
    base = datetime.strptime(match_date, "%Y-%m-%d")
    best, best_diff = None, 999
    for r in rows:
        mid = r["match_id"]
        try:
            dstr = mid.split("_", 1)[0]
            d = datetime.strptime(dstr, "%Y-%m-%d")
        except (ValueError, IndexError):
            continue
        diff = abs((d - base).days)
        if diff < best_diff:
            best, best_diff = mid, diff
    if best is not None and best_diff <= 3:
        return best
    return None


def find_any_sporttery_match_id(conn, home_cn, away_cn, match_date):
    """探测竞彩是否开了任意玩法（让球/大小球/比分），用于区分「未开胜平负盘」与「整场无竞彩」。

    深盘强队（如来让球两球半）竞彩常只开让球/大小球/比分、不开胜平负正盘，
    此时 wdl_history 无记录但 handicap/total_goals/score_history 有记录。
    与 find_sporttery_match_id 一样做 ±3 天日期容差，避免命中历史赛季同名对阵。
    """
    home_cn = _normalize_team_name(home_cn)
    away_cn = _normalize_team_name(away_cn)
    like = f"%_{home_cn}_{away_cn}"
    base = datetime.strptime(match_date, "%Y-%m-%d")
    best, best_diff = None, 999
    for tbl in ("handicap_history", "total_goals_history", "score_history", "wdl_history"):
        rows = conn.execute(
            f"SELECT DISTINCT match_id FROM {tbl} WHERE match_id LIKE ?", (like,)
        ).fetchall()
        for r in rows:
            mid = r["match_id"]
            try:
                dstr = mid.split("_", 1)[0]
                d = datetime.strptime(dstr, "%Y-%m-%d")
            except (ValueError, IndexError):
                continue
            diff = abs((d - base).days)
            if diff < best_diff:
                best, best_diff = mid, diff
    if best is not None and best_diff <= 3:
        return best
    return None


def build_sporttery_odds(conn, mid, fallback_win, fallback_draw, fallback_lose):
    """从竞彩网四张时序表组装 odds_data（prediction_core 兼容）。"""
    odds_data = {
        "wdl_odds": {"records": [], "open": {}, "close": {}},
        "handicap_odds": {"line": None, "records": [], "open": {}, "close": {}},
        "score_odds": {"records": []},
        "tg_odds": {"records": []},
    }
    if not mid:
        # 无竞彩时序 → 用 500.com 初盘做单条兜底 WDL
        fw, fd, fl = num(fallback_win), num(fallback_draw), num(fallback_lose)
        if fw and fd and fl and fw > 1:
            rec = {"time": "初盘", "win": fw, "draw": fd, "lose": fl}
            odds_data["wdl_odds"] = {"records": [rec], "open": rec, "close": rec}
        return odds_data

    # WDL
    rows = conn.execute(
        "SELECT timestamp, win_a, draw, win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp",
        (mid,),
    ).fetchall()
    if rows:
        recs = [{"time": r["timestamp"], "win": r["win_a"], "draw": r["draw"], "lose": r["win_b"]} for r in rows]
        odds_data["wdl_odds"] = {"records": recs, "open": recs[0], "close": recs[-1]}

    # 让球
    rows = conn.execute(
        "SELECT timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE match_id=? ORDER BY timestamp",
        (mid,),
    ).fetchall()
    if rows:
        recs = [{"time": r["timestamp"], "win": r["hcp_win"], "draw": r["hcp_draw"], "lose": r["hcp_lose"]} for r in rows]
        odds_data["handicap_odds"] = {"line": None, "records": recs, "open": recs[0], "close": recs[-1]}

    # 总进球
    rows = conn.execute(
        "SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus "
        "FROM total_goals_history WHERE match_id=? ORDER BY timestamp",
        (mid,),
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
        (mid,),
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


def load_sofascore_data(conn, match_date, home_cn):
    """按日期 + 主队中文名匹配 SofaScore 特征（客队可能是英文混合名）。

    凌晨场（北京时间 02:30~03:00）在 SofaScore 数据源常按当地日期归为前一天，
    与 500.com/竞彩的北京时间日期存在 ±1 天错位，故做日期容差回退（与
    find_sporttery_match_id 的日期容差思路一致）。
    """
    # 队名归一化：避免「云达不莱梅/云达不来梅」这类别名导致匹配失败
    home_norm = _normalize_team_name(home_cn)
    base = datetime.strptime(match_date, "%Y-%m-%d")
    # 按 |diff| 递增生成候选日期（0, ±1, ±2, ±3 天），与 find_sporttery_match_id
    # 的 ±3 天口径对齐；同队一周内不会打两次，±3 天范围内只会命中一场。
    date_candidates = [match_date]
    for delta in range(1, 4):
        date_candidates.append((base + timedelta(days=-delta)).strftime("%Y-%m-%d"))
        date_candidates.append((base + timedelta(days=delta)).strftime("%Y-%m-%d"))

    for d in date_candidates:
        row = conn.execute(
            "SELECT * FROM sofascore_team_features WHERE match_date=? AND (home_team_cn=? OR home_team_cn=?)",
            (d, home_cn, home_norm),
        ).fetchone()
        if row is None:
            # 兜底：归一化后主队名模糊匹配
            row = conn.execute(
                "SELECT * FROM sofascore_team_features WHERE match_date=? AND home_team_cn LIKE ?",
                (d, f"%{home_norm}%"),
            ).fetchone()
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
    """
    if row is None:
        return None
    v = row[field] if field in row.keys() else None
    if v is None:
        return None
    try:
        fv = float(v)
    except (TypeError, ValueError):
        return None
    if fv == 0.0:  # 采集端缺失占位
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


def render_report(m, odds_data, result, extra, report_path, conn=None):
    """按 18 章模板渲染单场报告。m=比赛元数据, result=四维预测, extra={500,sofascore}。"""
    home_cn, away_cn = m["home_team_cn"], m["away_team_cn"]
    home_en, away_en = m["home_team_en"], m["away_team_en"]
    league = m["league"]
    match_date = m["match_date"]
    match_time = m["match_time"]
    round_no = m["round"]

    # 联赛积分榜 + 战意（赛前口径，截止 match_date 之前的完赛场次）
    standings = compute_league_standings(conn, league, match_date) if conn is not None else []
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

    # 竞彩 WDL 赔率（尾盘/初盘）
    wdl_close = wdl.get("close") or {}
    wdl_open = wdl.get("open") or {}
    oc_h = wdl_close.get("win") or wdl_open.get("win")
    oc_d = wdl_close.get("draw") or wdl_open.get("draw")
    oc_a = wdl_close.get("lose") or wdl_open.get("lose")

    ih_h, ih_d, ih_a, wdl_return = odds_to_implied(oc_h, oc_d, oc_a)

    # 让球
    hcp_line_raw = hcp.get("line") or m.get("handicap_line")
    hcp_line_disp = m.get("handicap") or "—"
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
    lambda_alert = result.get("lambda_alert")  # P1-13: λ 主客差值告警

    # 总进球
    tg_pred = tg.get("prediction", "—")
    tg_over = tg.get("over_25_prob")
    tg_under = (1 - tg_over) if tg_over is not None else None
    tg_goals = tg.get("goals") or {}
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
            # 近 5 场主客评分（若缺失回退全赛季平均）
            rh = sofa_row["sofa_rat_5g_home"] or sofa_row["sofa_rat_home"]
            ra = sofa_row["sofa_rat_5g_away"] or sofa_row["sofa_rat_away"]
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
        extra_penalty=_conflict_penalty)
    conf_score = _conf_breakdown["score"]

    # ===== P0-03 遗留: 数据质量清单（逐模块状态，报告可视化）=====
    # 每个模块：name / status (OK/WARN/FAIL) / detail
    _quality_items = []

    # 1. 竞彩时序赔率
    if result.get("_wdl_not_offered"):
        _quality_items.append(("竞彩时序赔率", "WARN", "未开售WDL（500兜底）"))
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
    _bet_crawl = _betting["crawl_status"] if _betting is not None else None
    if _bet_crawl == "ok" and betting_has_real_data(_betting):
        _quality_items.append(("500.com 投注分析", "OK", "必发/冷热数据已接入"))
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
        _keys = ["sofa_rat_5g_home", "sofa_rat_5g_away", "xg_5g_home", "xg_5g_away"]
        _filled = sum(1 for k in _keys if _sf(sofa, k) is not None)
        if _filled >= 3:
            _quality_items.append(("SofaScore 赛前特征", "OK", f"核心字段{_filled}/4"))
        else:
            _quality_items.append(("SofaScore 赛前特征", "WARN", f"核心字段{_filled}/4（可能为真实0或采集缺失）"))
    else:
        _quality_items.append(("SofaScore 赛前特征", "FAIL", "未匹配到"))

    # 5. 球员伤病/预计首发（SofaScore 官方源）
    if extra["sofascore"] is not None and _sf(extra["sofascore"], "injury_count_home") is not None:
        _quality_items.append(("SofaScore 球员伤病", "OK", "官方源已接入"))
    else:
        _quality_items.append(("SofaScore 球员伤病", "WARN", "无伤病数据（非关键，靠历史推算）"))

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
                ev_analysis = _ev_engine.analyze_match(
                    probs=ev_probs, odds=ev_odds,
                    ev_threshold=ev_thr, kelly_strategy=ev_kelly_strat, kelly_cap=ev_kelly_cap,
                    match_id=m.get("fid"), league=league,
                    home_team=home_cn, away_team=away_cn)
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
    A("> **比赛**: {} 第{}轮".format(league, round_no))
    A("> **时间**: {} {} (北京时间)".format(match_date, match_time))
    A("> **对阵**: {} (主) vs {} (客)".format(home_cn, away_cn))
    if m.get("fid"):
        A("> **500.com**: fid={}".format(m["fid"]))
    A("> **数据完整度**: {}%".format(completeness))
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
    A("| **让球胜平负** ({}) | **{}** | {} | — |".format(hcp_line_disp, hcp_pred,
          pct(max(hcp_upper or 0, hcp_draw or 0, hcp_lower or 0))))
    A("| **总进球** (2.5球) | **{}** | {} | — |".format(tg_pred, pct(max(tg_over or 0, tg_under or 0))))
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
    A("### 模型推荐")
    A("")
    A("```")
    A("┌─────────────────────────────────────────────────────────────┐")
    A("│  推荐类型: {}".format(rec_type))
    if rec_type in ("NO_VALUE", "AVOID"):
        # P0-02: 无价值/规避时不再输出误导性「推荐方向」，明示观望
        A("│  推荐方向: —（无投注价值，观望）")
        A("│  推荐赔率: —")
        A("│  模型概率: —")
        A("│  隐含概率: —")
        A("│  价值空间: —")
        A("│  凯利指数: —")
        A("│  建议仓位: 0%")
    else:
        A("│  推荐方向: {}".format(rec_dir))
        A("│  推荐赔率: {}".format(fnum(rec_odds)))
        A("│  模型概率: {}".format(pct(rec_mp)))
        A("│  隐含概率: {}".format(pct(rec_implied)))
        A("│  价值空间: {}{}%".format("+" if (rec_edge or 0) > 0 else "", fnum((rec_edge or 0) * 100)))
        A("│  凯利指数: {}".format(fnum(rec_k, 3)))
        A("│  建议仓位: {}%".format(fnum(suggested_stake * 100, 1)))
    A("└─────────────────────────────────────────────────────────────┘")
    A("```")
    A("")
    if rec_type in ("NO_VALUE", "AVOID"):
        A("> 本场在 EV 阈值（2%）下无足够价值空间（edge 不足或低于抽水），建议观望。")
        A("")
    A("### 一句话总结")
    A("")
    summary = "模型{}（概率{}），市场{}，{}。".format(
        wdl_pred, pct(max(hp or 0, dp or 0, ap or 0)),
        "一致" if (rec_edge is None or abs(rec_edge) < 0.02) else
        ("低估" if (rec_edge or 0) > 0 else "过热"),
        "存在价值空间" if (rec_edge or 0) >= 0.02 else "无明显价值，建议观望")
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
    A("| 让球盘口 | 让球主胜 | 让球平局 | 让球客胜 | 预测方向 | 概率 |")
    A("|---------|:--------:|:--------:|:--------:|---------|:----:|")
    A("| {} | {} | {} | {} | **{}** | {} |".format(
        hcp_line_disp, pct(hcp_upper), pct(hcp_draw), pct(hcp_lower), hcp_pred,
        pct(max(hcp_upper or 0, hcp_draw or 0, hcp_lower or 0))))
    A("")
    A("**走水检测**: 走水概率 {} | 方法: {}".format(pct(hcp_draw), hcp_method))
    A("")
    A("---")
    A("")

    # ================= 五、比分 =================
    A("## 四、比分预测 Top10 ({})".format(score_method))
    A("")
    A("| 排名 | 比分 | 概率 | 累计概率 | 胜平负 |")
    A("|:----:|------|:----:|:--------:|:------:|")
    cum = 0.0
    for i, s in enumerate(top_scores[:10] if len(top_scores) > 5 else top_scores, 1):
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
    A("---")
    A("")

    # ================= 六、总进球 =================
    A("## 五、总进球预测")
    A("")
    if tg_goals:
        A("| 总进球 | 0球 | 1球 | 2球 | 3球 | 4球 | 5球 | 6球 | 7+球 |")
        A("|:------:|:---:|:---:|:---:|:---:|:---:|:---:|:---:|:----:|")
        vals = []
        cum_tg = 0.0
        for k in ["0", "1", "2", "3", "4", "5", "6", "7+"]:
            v = tg_goals.get(k) or tg_goals.get(str(k)) or 0.0
            vals.append(pct(v))
        A("| 概率 | {} |".format(" | ".join(vals)))
    A("")
    A("**大小球(2.5)**: 大球 {} | 小球 {} | 预测: **{}** | 方法: {}".format(
        pct(tg_over), pct(tg_under), tg_pred, tg_method))
    A("")
    A("---")
    A("")

    # ================= 七、赔率分析 =================
    A("## 六、赔率分析与价值投注")
    A("")
    # P1-07: 两层预测架构提示块（概率预测层 vs EV 决策层）
    A("> 📌 **预测层级架构**")
    A("> - **第一层 — 概率预测**（§一~§五）：Stacking 融合 5 子模型，输出 WDL/让球/比分/总进球概率，回答「谁赢、几个球」。")
    A("> - **第二层 — EV 决策**（§六）：以第一层概率 × 市场赔率，扣除抽水后计算期望值，回答「是否值得下注、下注仓位」。")
    A("> - 概率预测正确 ≠ 有投注价值：抽水率（vig）会吞噬部分 edge，仅当 EV>0 且 edge≥EV 阈值（2%）才进入决策。")
    A("")

    # --- EV 期望值分析（ev_engine 决策层）---
    if ev_analysis is not None:
        A("### 6.1 期望值分析 (EV)")
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
        A("### 6.2 投注建议 (EV 决策)")
        A("")
        A("```")
        A("┌─────────────────────────────────────────────────────────────┐")
        if ev_analysis.overall_decision == "AVOID":
            A("│  本场决策: AVOID（无投注价值）                              │")
            A("│  最佳EV: {:.2f}%                                            │".format(ev_analysis.best_ev * 100))
            A("│  建议仓位: 0%（观望）                                       │")
        else:
            label = "VALUE（有投注价值）" if ev_analysis.overall_decision == "VALUE" else "MARGINAL（微弱价值）"
            if ev_analysis.best_direction == "home":
                bp, bo = ev_analysis.home_analysis.p_model, ev_analysis.home_analysis.odds
            elif ev_analysis.best_direction == "draw":
                bp, bo = ev_analysis.draw_analysis.p_model, ev_analysis.draw_analysis.odds
            else:
                bp, bo = ev_analysis.away_analysis.p_model, ev_analysis.away_analysis.odds
            A("│  本场决策: {}".format(label))
            A("│  推荐方向: {}".format(ev_analysis.best_direction_cn))
            A("│  模型概率: {:.1f}% | 建议赔率: {:.2f}".format(bp * 100, bo))
            A("│  价值空间: {:+.1f}%".format(ev_analysis.best_edge * 100))
            A("│  期望值: {:+.2f}%（每注1元）".format(ev_analysis.best_ev * 100))
            A("│  建议仓位: {:.1f}%（1/4凯利）".format(ev_analysis.recommended_stake_pct * 100))
        A("└─────────────────────────────────────────────────────────────┘")
        A("```")
        A("")
        A("### 6.3 抽水分析")
        A("")
        A("- 竞彩返还率: {} | 抽水率: {} | 三向隐含概率和: {}".format(
            pct(ev_analysis.payout_rate), pct(ev_analysis.vig), pct(ev_analysis.total_implied)))
        if ev_analysis.risk_warnings:
            A("- 风险提示: {}".format("；".join(ev_analysis.risk_warnings)))
        A("")
        A("> 注：EV 阈值 {}% | 凯利策略 {} | 单场上限 {}%。EV>0 且 edge>0 才有价值。".format(
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
    A("> 📝 **凯利概念辨析**（两套概念易混用，特此脚注）")
    A("> - **下注策略凯利**（本表与 §6.2 决策卡）：f* = (bp - q) / b，b=净赔率(赔率-1)，p=模型胜率，q=1-p。本表列为**全凯利原始值**，§6.2 实际仓位取 **1/4 分数凯利**（kelly_clipped×0.25，控方差）。")
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
    A("## 七、时序赔率异动分析")
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
        if result.get("_wdl_not_offered"):
            A("> ℹ️ 竞彩未开售胜平负盘（仅让球/大小球/比分），上表为 500.com 初盘参考（非时序缺失）。")
        else:
            A("> ⚠️ 仅 1 条竞彩 WDL 快照，缺少开盘→即时漂移，需回踩刷新（--no-skip-existing）。")
    else:
        A("(无时序赔率数据；需补采竞彩 WDL 时序)")
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
        A("> 预测首发/伤病缺阵为代理指标（由 player_availability_features 基于历史出场连续性推算，非官方伤病新闻）。")
        A("")
        A("| 维度 | 主队 | 客队 |")
        A("|------|:----:|:----:|")
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
    A("")
    A("## 十五、技术统计对比")
    A("")
    A("(未开赛比赛无单场技术统计；赛后由 500.com odds500_stat 回补)")
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
        A("> ⚠️  本场有 {} 个数据模块处于 WARN/FAIL 状态，已在置信度计算中自动扣除相应分数。".format(_warn_fail_count))
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
    elif result.get("_wdl_not_offered"):
        sporttery_status = "未开售WDL(500兜底)"  # 玩法缺位，非数据缺失
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
                "partial" if wdl_snap == 1 else (
                    "degraded" if result.get("_wdl_not_offered") else "missing")),
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

    return completeness


def pct_with_sign(edge):
    if edge is None:
        return "—"
    return "{}{}%".format("+" if edge > 0 else "", fnum(edge * 100, 1))


def compute_confidence(completeness, model_conf):
    model_c = (model_conf or 0) * 100
    return int(round(0.5 * completeness + 0.5 * model_c))


def compute_confidence_breakdown(completeness, model_conf, divergence_std=0.0, n_sub=0,
                                wdl_snap=0, total_sub=5, extra_penalty=0.0):
    """P1-01: 综合置信度拆解 = 基准分 + 加分项 - 扣分项。

    返回 dict: {score, base, components: [{name, type, value, reason}], explanation}
    - base  = 0.5*completeness + 0.5*model_c （即旧 compute_confidence 输出，保持向后兼容基线）
    - 扣分项: 子模型分歧度 / 时序赔率快照不足 / 子模型覆盖度不足 / extra_penalty（如跨源冲突）
    - score = clamp(base - sum(deductions), 0, 100)
    """
    model_c = (model_conf or 0) * 100
    base = int(round(0.5 * completeness + 0.5 * model_c))
    components = [
        {"name": "数据完整度", "type": "base", "value": int(round(0.5 * completeness)),
         "reason": "三通道（竞彩时序+500+SofaScore）各占 1/3，完整度 {}%".format(completeness)},
        {"name": "模型方向概率", "type": "base", "value": int(round(0.5 * model_c)),
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
    # 时序赔率快照不足
    if wdl_snap == 0:
        d = 5
        r = "无时序赔率快照，缺少开盘→即时漂移信息"
    elif wdl_snap == 1:
        d = 3
        r = "仅 1 条时序赔率快照，无法观测漂移"
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
        "round": f"第{m['round']}轮",
        "match_time": f"{m['match_date']} {m['match_time']}",
        "handicap_line": hcp_line,
    }


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
            print(f"  - {m['match_date']} {m['match_time']} [{m['league']}] {m['home_team_cn']} vs {m['away_team_cn']} (盘口 {m['handicap'] or '—'})")
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

    rows = []
    for m in matches:
        home_cn, away_cn = m["home_team_cn"], m["away_team_cn"]
        print(f"\n=== {m['match_date']} {m['match_time']} [{m['league']}] {home_cn} vs {away_cn} ===")

        # 回放模式：已有预测(model_predictions)则跳过，仅回写缺预测场次
        if args.replay and match_has_prediction(conn, m):
            print(f"  ⏭️ 已有预测，跳过: {home_cn} vs {away_cn}")
            continue

        # 1. 定位竞彩时序 match_id（仅 WDL 表）
        sporttery_mid = find_sporttery_match_id(conn, home_cn, away_cn, m["match_date"])
        # 竞彩未开胜平负盘：WDL 无记录，但让球/大小球/比分有记录（深盘强队常见，非采集缺失）
        wdl_not_offered = (sporttery_mid is None) and (
            find_any_sporttery_match_id(conn, home_cn, away_cn, m["match_date"]) is not None)
        # 2. 组装 odds_data（500 欧指兜底：优先 live 欧指，回退 init 欧指）
        #    m["win/draw/lost"] 字段自 2026-10 起长期为 None，真实 500 欧指在
        #    odds500_ouzhi_summary 表（avg_live_win/draw/lose 等），故提前取
        #    data500 并从中提取兜底赔率，确保竞彩 WDL 缺失时报告仍可生成。
        data500 = load_500_data(conn, m["fid"])
        ouzhi = (data500 or {}).get("ouzhi") or {}
        fb_w = ouzhi.get("avg_live_win") or ouzhi.get("avg_init_win")
        fb_d = ouzhi.get("avg_live_draw") or ouzhi.get("avg_init_draw")
        fb_l = ouzhi.get("avg_live_lose") or ouzhi.get("avg_init_lose")
        odds_data = build_sporttery_odds(conn, sporttery_mid, fb_w, fb_d, fb_l)
        wdl_snapshots = len(odds_data["wdl_odds"]["records"])
        timing_complete = wdl_snapshots >= 2  # 时序漂移分析需开盘/即时两条快照
        has_odds = wdl_snapshots >= 1
        # 让球线
        hcp_line = parse_hcp_line(m.get("handicap"))
        odds_data["handicap_odds"]["line"] = hcp_line

        if not has_odds:
            print("  ⚠️ 无胜平负赔率（竞彩 + 500 欧指均缺失），跳过预测")
            continue

        # 3. 500.com / SofaScore
        extra = {"500": data500, "sofascore": load_sofascore_data(conn, m["match_date"], home_cn)}

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

        # 5. 渲染报告（按比赛日 match_date 落目录）
        out_dir = BASE_DIR / "docs" / "prematch_reports" / m['match_date'].replace("-", "")
        out_dir.mkdir(parents=True, exist_ok=True)
        fname = f"{m['league']}_{m['match_date']}_{home_cn}_vs_{away_cn}.md".replace("/", "-")
        report_path = out_dir / fname
        completeness = render_report(m, odds_data, result, extra, report_path, conn)
        print(f"  ✅ 报告: {report_path.name} (完整度 {completeness}%)")

        # 6. 汇总行 + 回写数据
        wdl = result["wdl"]; hcp = result["hcp"]; score = result["score"]; tg = result["tg"]

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
            "league": m["league"],
            "home": home_cn, "away": away_cn,
            "home_en": m["home_team_en"], "away_en": m["away_team_en"],
            "round": f"第{m['round']}轮",
            "match_time": f"{m['match_date']} {m['match_time']}",
            "wdl": {"home_prob": wdl.get("home_prob"), "draw_prob": wdl.get("draw_prob"),
                    "away_prob": wdl.get("away_prob"), "prediction": wdl.get("prediction")},
            "hcp": {"line": hcp_line, "home_win_prob": hcp.get("home_win_prob"),
                    "draw_prob": hcp.get("draw_prob"), "away_win_prob": hcp.get("away_win_prob")},
            "score": {"lambda_home": score.get("lambda_home"), "lambda_away": score.get("lambda_away"),
                      "most_likely": score.get("most_likely")},
            "tg": {"over_25_prob": tg.get("over_25_prob")},
            "ev": ev_summary,
            "_input_snapshot_json": input_snapshot,
            "_feature_version": FEATURE_VERSION,
            "_config_version": CONFIG_VERSION,
            "_file": fname,
            "_wdl_snapshots": wdl_snapshots,
            "_wdl_not_offered": wdl_not_offered,
            "_completeness": completeness,
            "lambda_alert": result.get("lambda_alert"),  # P1-13: λ 主客差值告警
        })

    # 7. 汇总报告（按比赛日 match_date 分组）
    # 回放模式只补单场报告，不重写 _summary.md（避免用"仅回放子集"覆盖已有汇总）
    if args.replay:
        print("\n[回放模式] 跳过 _summary.md 汇总重写（避免覆盖已有汇总）")
    rows_by_date = {}
    for r in rows:
        rows_by_date.setdefault(r["match_date"], []).append(r)

    for md, day_rows in ([] if args.replay else rows_by_date.items()):
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

    # 8. 回写 model_predictions
    if rows and not args.no_write:
        try:
            ids = batch_save_predictions([serializable_to_pred(r) for r in rows])
            print(f"✅ 已回写 {len(ids)} 场到 model_predictions: {ids}")
        except Exception as e:
            print(f"⚠️ 回写 model_predictions 失败: {type(e).__name__}: {e}")

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
    args = parser.parse_args()
    run(args)