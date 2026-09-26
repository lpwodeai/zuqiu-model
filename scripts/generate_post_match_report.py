# -*- coding: utf-8 -*-
"""
generate_post_match_report.py — 模块 A4：自动复盘报告生成器（P0）

===============================================
背景（已归档：原模型改进实施方案 v1.0 §三/A4 + AI复盘闭环落地指南 §2.2）：
  每场已完赛预测比赛自动生成 8 章节 markdown 复盘报告，人工只看结论。
  复用 generate_unified_report.py 报告框架（章节结构 + markdown 表格 + 中文队名归一）。

数据链路（对齐 A1/A2/A3）：
  - 赛果/归因/质量分 单一来源 = post_match_review（A1 schema，A2/A3 写入）
  - 预测数据 = model_predictions（WDL/Lambda/TG/HC/Score 行）
  - 比分 Top1/Top5 直接读 Score_top1/Score_top5 发布行；缺失标「无发布记录」
    （C-20260921-034：禁止由 λ 现场重推，保证复盘口径=发布口径）
  - 归因缺失时自动调用 AttributionEngine.run() 补算
  - 关键事件时间线：--collect 时实时采集 SofaScore incidents（可降级为提示）

报告 8 章节（对齐指南 §2.2）：
  一、比赛结果 + 关键事件时间线
  二、预测 vs 实际对比（WDL/比分Top1/Top5/让球/进球数Top1/Top3）
  三、预测质量评分（单场 RPS/LogLoss/概率排名/校准偏差）
  四、AI 自动归因分析（权重排序 + 证据）
  五、数据质量检查（data_quality_score + 覆盖率）
  六、经验教训与改进建议（短/中/长期）
  七、因子库更新建议（L1/L2/L3 + 可信度）
  八、审核签字区

输出路径：
  docs/post_match/{YYYYMMDD}/{联赛}_{主队}_vs_{客队}_复盘.md
  docs/post_match/{YYYYMMDD}/_summary.md   （当日命中/失败统计 + 数据完整性告警）

用法：
  python scripts/generate_post_match_report.py --date 2026-09-06
  python scripts/generate_post_match_report.py --match-id "2026-08-22_Arsenal_Coventry City"
  python scripts/generate_post_match_report.py --date 2026-09-06 --no-collect  # 跳过赛后明细采集
  python scripts/generate_post_match_report.py --date 2026-09-06 --write     # 回填 pred_*/质量分
  python scripts/generate_post_match_report.py --date 2026-09-06 --dry-run   # 只扫不写
"""

from __future__ import annotations

import argparse
import json
import logging
import math
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

sys.path.insert(0, str(PROJECT_DIR / "scripts"))
sys.path.insert(0, str(PROJECT_DIR / "collection"))

from post_match_schema import ensure_post_match_schema  # noqa: E402
from feature_utils import canonical_team_name  # noqa: E402
from attribution_engine import (  # noqa: E402
    AttributionEngine,
    get_bucket_hit_rates,
    get_match_basics,
    get_model_wdl,
    get_event_id,
    get_odds_timeline,
    get_understat_xg,
)

REPORT_VERSION = "v1.0"
ATTRIBUTION_VERSION = "attribution_engine v1.0"

WDL_CN = {"WDL_home": "主胜", "WDL_draw": "平局", "WDL_away": "客胜"}
ACTUAL_WDL_NORM = {"主胜": "主胜", "平局": "平局", "客胜": "客胜",
                   "胜": "主胜", "平": "平局", "负": "客胜"}


# ==================== 基础工具 ====================
def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.row_factory = sqlite3.Row
    return conn


def _resolve_actual_hcp(conn: sqlite3.Connection, review: sqlite3.Row) -> Optional[str]:
    """C-20260921-042: 让球实际结果 fallback——当 post_match_review.actual_hcp 为 NULL 时，
    从 matches 表的 handicap 盘口 + actual_score 比分自动推算。

    口径对齐 run_post_match_pipeline.hcp_from_handicap()：
      handicap 以主队为基准（负=主队让球），调整后主队得分 = home + handicap
      > 客队 → 上盘赢；== → 走水；< → 下盘赢
    也兼容 matches.actual_handicap 已是赛果文本的情况。
    """
    if review["actual_hcp"]:
        return review["actual_hcp"]

    score = review["actual_score"]
    if not score or ":" not in str(score):
        return None
    parts = str(score).split(":")
    if len(parts) != 2:
        return None
    try:
        home_goals, away_goals = int(parts[0]), int(parts[1])
    except ValueError:
        return None

    cur = conn.cursor()
    cur.execute(
        "SELECT handicap, actual_handicap FROM matches WHERE match_id=?",
        (review["match_id"],),
    )
    row = cur.fetchone()
    if not row:
        return None

    # 优先用 actual_handicap（sporttery_collector 已计算的赛果文本）
    actual_hcp_text = row[1] if row[1] else None
    if actual_hcp_text:
        # actual_handicap 可能是 "(-1)胜" 格式，映射到 上盘赢/走水/下盘赢
        ah = str(actual_hcp_text)
        if "胜" in ah and "负" not in ah:
            return "上盘赢"
        if "负" in ah and "胜" not in ah:
            return "下盘赢"
        if "平" in ah or "走" in ah:
            return "走水"

    # 从 handicap 盘口值 + 比分 计算
    hcp_line = row[0]
    if hcp_line is None:
        return None
    try:
        hcp_val = float(hcp_line)
    except (ValueError, TypeError):
        # 中文串如 "受让半球" 无法直接解析
        return None

    adjusted = home_goals + hcp_val
    if adjusted > away_goals:
        result = "上盘赢"
    elif adjusted == away_goals:
        result = "走水"
    else:
        result = "下盘赢"

    # 回写 DB（幂等 UPDATE）
    cur.execute(
        "UPDATE post_match_review SET actual_hcp=? WHERE match_id=?",
        (result, review["match_id"]),
    )
    conn.commit()
    return result


def pct(x: Any, nd: int = 1) -> str:
    if x is None:
        return "—"
    try:
        return f"{float(x) * 100:.{nd}f}%"
    except (TypeError, ValueError):
        return "—"


def fnum(x: Any, nd: int = 2) -> str:
    if x is None or x == "":
        return "—"
    try:
        return f"{float(x):.{nd}f}"
    except (TypeError, ValueError):
        return "—"


def _load_json(value: Optional[str]) -> Any:
    if not value:
        return None
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (ValueError, TypeError):
        return None


def _tg_label_to_goals(label: Optional[str]) -> Optional[int]:
    """C-20260921-035：进球数 label → 整数（"4球"→4，"7+球"→7）；解析失败返回 None。"""
    if not label:
        return None
    m = re.match(r"\s*(\d+)\s*\+?球?", str(label))
    return int(m.group(1)) if m else None


def single_rps(probs: List[float], actual_idx: int) -> float:
    """单场 RPS（probs=[主胜,平,客胜]，actual_idx 0/1/2；对齐 stratified_evaluation._rps）。"""
    if len(probs) != 3 or actual_idx not in (0, 1, 2):
        return float("nan")
    cum_p = [probs[0], probs[0] + probs[1]]
    cum_a = [1.0 if actual_idx == 0 else 0.0, 1.0 if actual_idx <= 1 else 0.0]
    return sum((pa - pp) ** 2 for pp, pa in zip(cum_p, cum_a)) / 2.0


def single_logloss(probs: List[float], actual_idx: int) -> float:
    if len(probs) != 3 or actual_idx not in (0, 1, 2):
        return float("nan")
    return -math.log(max(probs[actual_idx], 1e-9))


def prob_rank(probs: List[float], actual_idx: int) -> Optional[int]:
    """实际结果概率在 3 向中的排名（1=最高）。"""
    if len(probs) != 3 or actual_idx not in (0, 1, 2):
        return None
    higher = sum(1 for i, p in enumerate(probs) if i != actual_idx and p > probs[actual_idx])
    return higher + 1


# ==================== 数据读取 ====================
def load_review_rows(conn: sqlite3.Connection, match_date: Optional[str],
                     match_id: Optional[str]) -> List[sqlite3.Row]:
    """扫 post_match_review 目标场次（--date 或 --match-id）。"""
    cur = conn.cursor()
    if match_id:
        cur.execute("SELECT * FROM post_match_review WHERE match_id=?", (match_id,))
    else:
        cur.execute(
            "SELECT * FROM post_match_review WHERE match_date=? ORDER BY match_id", (match_date,)
        )
    rows = cur.fetchall()
    # 去重：同一 match_id 可能因队名变体被重复写入，只保留首条
    seen: set = set()
    deduped: List[sqlite3.Row] = []
    for r in rows:
        key = r["match_id"]
        if key in seen:
            continue
        seen.add(key)
        deduped.append(r)
    return deduped


def load_predictions(conn: sqlite3.Connection, match_id: str) -> Dict[str, Any]:
    """从 model_predictions 读取 WDL/Lambda/TG/HC 预测（最新 model_name）。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT model_name FROM model_predictions WHERE match_id=? "
        "AND prediction_type='WDL_home' ORDER BY id DESC LIMIT 1", (match_id,),
    )
    row = cur.fetchone()
    model_name = row["model_name"] if row else None
    pred: Dict[str, Any] = {"model_name": model_name}

    if model_name:
        cur.execute(
            "SELECT prediction_type, prediction, probability FROM model_predictions "
            "WHERE match_id=? AND model_name=?",
            (match_id, model_name),
        )
        for pt, prediction, probability in cur.fetchall():
            pred[pt] = {"prediction": prediction, "probability": float(probability) if probability is not None else None}

    # WDL 三向
    wdl = {}
    for pt in ("WDL_home", "WDL_draw", "WDL_away"):
        if pt in pred:
            wdl[pt] = pred[pt]["probability"]
    if len(wdl) == 3:
        pred["wdl_probs"] = [wdl["WDL_home"], wdl["WDL_draw"], wdl["WDL_away"]]
        pred["pred_wdl"] = WDL_CN[max(wdl, key=wdl.get)]
        pred["pred_wdl_prob"] = max(wdl.values())

    # 比分（只读真实发布行；C-20260921-034：禁止由 λ 现场重推，保证复盘=发布口径）
    t1_row = pred.get("Score_top1")
    t5_row = pred.get("Score_top5")
    t5_items = _load_json(t5_row["prediction"]) if t5_row else None
    if t1_row and t1_row.get("prediction"):
        pred["pred_score_top1"] = t1_row["prediction"]
    if isinstance(t5_items, list) and t5_items:
        pred["pred_score_top5"] = [x.get("score") for x in t5_items]
        pred["score_probs"] = {
            x.get("score"): x.get("prob")
            for x in t5_items
            if x.get("score") is not None and x.get("prob") is not None
        }
    # Score_* 两发布行均缺失时：不造预测，报告端标「无发布记录」，命中统计按 None 剔除

    # 总进球（C-20260921-035：只读 TG_top1/TG_top3 真实发布行，禁止现场重推；
    # 旧 TG_over/under 底层概率仅作数据保留，不再生成大/小球结论）
    tg1_row = pred.get("TG_top1")
    tg3_row = pred.get("TG_top3")
    tg3_items = _load_json(tg3_row["prediction"]) if tg3_row else None
    if tg1_row and tg1_row.get("prediction"):
        pred["pred_tg_top1"] = tg1_row["prediction"]          # label，如 "4球"
        pred["pred_tg_top1_goals"] = _tg_label_to_goals(tg1_row["prediction"])
    if isinstance(tg3_items, list) and tg3_items:
        pred["pred_tg_top3"] = [x.get("label") for x in tg3_items if x.get("label")]
        pred["pred_tg_top3_goals"] = [x.get("goals") for x in tg3_items if x.get("goals") is not None]
        pred["tg_top3_probs"] = {
            x.get("label"): x.get("prob")
            for x in tg3_items
            if x.get("label") is not None and x.get("prob") is not None
        }
    # 两发布行均缺失时：不造预测，报告端标「无发布记录」，命中统计按 None 剔除
    po = pred.get("TG_over_2_5", {}).get("probability")
    pu = pred.get("TG_under_2_5", {}).get("probability")
    if po is not None and pu is not None:
        pred["tg_ou_probs"] = {"大球": po, "小球": pu}

    # 让球
    hc = {pt: pred[pt]["probability"] for pt in ("HC_upper", "HC_draw", "HC_lower") if pt in pred}
    if len(hc) == 3:
        pred["pred_hcp"] = {"HC_upper": "上盘赢", "HC_draw": "走水", "HC_lower": "下盘赢"}[max(hc, key=hc.get)]
        pred["hcp_probs"] = hc

    return pred


def compute_review_fields(conn: sqlite3.Connection, review: sqlite3.Row, pred: Dict[str, Any]) -> Dict[str, Any]:
    """计算 wdl_correct / score_top1_hit / score_top5_cover / hcp_correct
    / tg_top1_hit / tg_top3_cover（C-20260921-035）
    + 质量分（RPS/LogLoss/概率排名/校准偏差）。"""
    actual_wdl = ACTUAL_WDL_NORM.get(review["actual_wdl"]) if review["actual_wdl"] else None
    fields: Dict[str, Any] = {}

    # 方向
    pred_wdl = pred.get("pred_wdl")
    fields["wdl_correct"] = (1 if (pred_wdl and actual_wdl and pred_wdl == actual_wdl) else 0) if (pred_wdl and actual_wdl) else None

    # 比分
    actual_score = str(review["actual_score"]) if review["actual_score"] else None
    top1 = pred.get("pred_score_top1")
    top5 = pred.get("pred_score_top5") or []
    fields["score_top1_hit"] = (1 if (top1 and actual_score and top1 == actual_score) else 0) if (top1 and actual_score) else None
    fields["score_top5_cover"] = (1 if (actual_score and actual_score in top5) else 0) if (top5 and actual_score) else None

    # 让球（C-20260921-042: actual_hcp 为 NULL 时自动从 matches 表推算）
    pred_hcp = pred.get("pred_hcp")
    actual_hcp = _resolve_actual_hcp(conn, review) if not review["actual_hcp"] else review["actual_hcp"]
    fields["actual_hcp_resolved"] = actual_hcp  # 供报告渲染使用
    fields["hcp_correct"] = (1 if (pred_hcp and actual_hcp and pred_hcp == actual_hcp) else 0) if (pred_hcp and actual_hcp) else None

    # 总进球精确档位（C-20260921-035：Top1 命中 / Top3 覆盖；口径同 Score 模式）
    actual_tg = review["actual_tg"]
    tg1_goals = pred.get("pred_tg_top1_goals")
    tg3_goals = pred.get("pred_tg_top3_goals") or []
    if actual_tg is not None:
        actual_tg = int(actual_tg)
        fields["tg_top1_hit"] = (
            1 if (tg1_goals is not None and tg1_goals == actual_tg) else 0
        ) if tg1_goals is not None else None
        fields["tg_top3_cover"] = (
            1 if actual_tg in tg3_goals else 0
        ) if tg3_goals else None
    else:
        fields["tg_top1_hit"] = None
        fields["tg_top3_cover"] = None

    # 质量分
    wdl_probs = pred.get("wdl_probs")
    if wdl_probs and actual_wdl:
        idx = {"主胜": 0, "平局": 1, "客胜": 2}[actual_wdl]
        fields["single_rps"] = single_rps(wdl_probs, idx)
        fields["single_logloss"] = single_logloss(wdl_probs, idx)
        fields["prob_rank"] = prob_rank(wdl_probs, idx)
        # 校准偏差：实际结果概率 vs 该方向概率档历史命中率
        pt_actual = {"主胜": "WDL_home", "平局": "WDL_draw", "客胜": "WDL_away"}[actual_wdl]
        p_actual = wdl_probs[idx]
        fields["calibration_gap"] = _calibration_gap(conn, pt=pt_actual, p=p_actual)

    return fields


def _calibration_gap(conn: Optional[sqlite3.Connection], pt: str, p: float) -> Optional[Dict[str, Any]]:
    """实际结果概率 vs 概率档历史命中率（复用 A3 分桶口径）。"""
    if conn is None:
        return None
    hit_rates = get_bucket_hit_rates(conn)
    if p >= 0.70:
        bkey = "0.70"
    elif p >= 0.60:
        bkey = "0.60"
    elif p >= 0.50:
        bkey = "0.50"
    else:
        bkey = "0.40"
    key = f"{pt}_{bkey}"
    if key not in hit_rates:
        return {"note": "insufficient-history", "bucket": key, "model_prob": round(p, 4)}
    gap = p - hit_rates[key]
    return {"bucket": key, "model_prob": round(p, 4),
            "bucket_hit_rate": round(hit_rates[key], 4),
            "gap": round(gap, 4)}


def compute_data_quality(conn: sqlite3.Connection, review: sqlite3.Row, pred: Dict[str, Any],
                         post_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """数据质量评分（0-1）+ 覆盖率明细。"""
    score = 1.0
    issues: List[str] = []
    cover: List[Dict[str, str]] = []

    event_id = get_event_id(conn, review["match_id"])

    # SofaScore 赛前特征
    if event_id:
        cover.append({"type": "SofaScore 球员特征", "status": "✅", "note": f"event_id={event_id}"})
    else:
        score -= 0.2
        issues.append("无 fbref_match_mapping 映射，SofaScore 特征无法关联")
        cover.append({"type": "SofaScore 球员特征", "status": "⚠️", "note": "无映射"})

    # 竞彩时序赔率
    basics = get_match_basics(conn, review["match_id"])
    odds_hist = get_odds_timeline(conn, review["match_id"], basics) if basics else None
    if odds_hist:
        cover.append({"type": "竞彩时序赔率", "status": "✅", "note": f"{odds_hist['snapshots']}个时间点"})
        if odds_hist["snapshots"] < 2:
            score -= 0.1
            issues.append(f"竞彩快照仅 {odds_hist['snapshots']} 条（<2），缺开盘→即时漂移")
    else:
        score -= 0.2
        issues.append("竞彩时序赔率缺失（无 wdl_history 匹配）")
        cover.append({"type": "竞彩时序赔率", "status": "⚠️", "note": "无数据"})

    # understat xG
    xg = get_understat_xg(conn, basics) if basics else None
    if xg:
        cover.append({"type": "Understat xG", "status": "✅", "note": f"{xg['home_xg']:.2f}:{xg['away_xg']:.2f}"})
    else:
        score -= 0.1
        issues.append("Understat xG 缺失（26/27 未更新或队名不匹配）")
        cover.append({"type": "Understat xG", "status": "⚠️", "note": "无数据"})

    # 赛后明细（--collect 时）
    if post_data:
        cover.append({"type": "赛后技术统计/时间线", "status": "✅", "note": "已采集"})
    else:
        cover.append({"type": "赛后技术统计/时间线", "status": "—", "note": "未采集（--collect 补全）"})

    # 预测数据
    if pred.get("wdl_probs"):
        cover.append({"type": "模型 WDL 预测", "status": "✅", "note": pred.get("model_name") or ""})
    else:
        score -= 0.2
        issues.append("model_predictions 无 WDL 预测行")
        cover.append({"type": "模型 WDL 预测", "status": "⚠️", "note": "无"})

    return {
        "score": round(max(0.0, min(1.0, score)), 2),
        "coverage": cover,
        "issues": issues,
        "alert": score < 0.7,
    }


# ==================== 章节渲染 ====================
def _header(review: sqlite3.Row, fields: Dict[str, Any], confidence: Optional[int]) -> str:
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    half = review["actual_half_score"] or "—"
    return (
        f"# 赛后复盘报告 — {review['home_team']} vs {review['away_team']}\n\n"
        f"> 报告版本: {REPORT_VERSION}\n"
        f"> 生成时间: {now}\n"
        f"> 比赛: {review['league'] or '—'} {review['match_date'] or ''}\n"
        f"> 对阵: {review['home_team']} (主) vs {review['away_team']} (客)\n"
        f"> 实际比分: {review['actual_score'] or '—'}（半场 {half}）\n"
        f"> 可信度: {f'{confidence}级' if confidence else '—'}\n"
    )


def _section1_result(review: sqlite3.Row, post_data: Optional[Dict[str, Any]],
                     stat500: Optional[sqlite3.Row] = None) -> str:
    tg = review["actual_tg"] if review["actual_tg"] is not None else "—"
    hcp = review["actual_hcp"] or "—"
    wdl = review["actual_wdl"] or "—"
    half = review["actual_half_score"] or "—"
    s = (
        "\n---\n\n## 一、比赛结果\n\n"
        "| 维度 | 实际结果 |\n|------|---------|\n"
        f"| 全场比分 | {review['actual_score'] or '—'} |\n"
        f"| 半场比分 | {half} |\n"
        f"| 胜平负 | {wdl} |\n"
        f"| 让球结果 | {hcp} |\n"
        f"| 总进球 | {tg}球 |\n"
    )

    # 单场技术统计对比（C-20260923-063: odds500_stat 12 组指标，按 match_id 直查）
    s += "\n### 单场技术统计对比（500.com）\n\n"
    if stat500 is not None:
        s += "| 指标 | 主队 | 客队 |\n|------|:----:|:----:|\n"
        _has_any = False
        for label, key in (
            ("进攻", "attack"), ("危险进攻", "danger"), ("射门", "shots"),
            ("射正", "shots_on"), ("任意球", "fk"), ("角球", "corners"),
            ("越位", "offsides"), ("犯规", "fouls"), ("黄牌", "yellow"),
            ("红牌", "red"), ("控球率", "possession"),
        ):
            h, a = stat500["home_" + key], stat500["away_" + key]
            if (h is None or h == "") and (a is None or a == ""):
                continue
            _has_any = True
            s += f"| {label} | {h if h not in (None, '') else '—'} | {a if a not in (None, '') else '—'} |\n"
        if not _has_any:
            s += "> odds500_stat 记录存在但 12 组指标全空（采集不完整）\n"
    else:
        s += "> ⚠️ 500.com 赛后技术统计未回补（跑 500.com 赛后采集流程后重生成复盘报告可见）\n"

    # 关键事件时间线
    s += "\n### 关键事件时间线\n\n"
    inc = (post_data or {}).get("incidents") or {}
    goals = inc.get("goals") or []
    cards = inc.get("cards") or []
    if not goals and not cards:
        s += "> ⚠️ 赛后明细未采集到（SofaScore 无数据或 event_id 缺失）\n"
        return s
    s += "| 时间 | 事件 | 影响 |\n|------|------|------|\n"
    events: List[tuple] = []
    for g in goals:
        side = "主队" if g.get("side") == "home" else "客队"
        events.append((g.get("minute") or 0, f"{side}进球", f"{g.get('home_score')}:{g.get('away_score')}"))
    for c in cards:
        side = "主队" if c.get("side") == "home" else "客队"
        ct = str(c.get("card_type", "")).lower()
        card_cn = {"red": "红牌", "yellow": "黄牌", "yellowred": "两黄变红"}.get(ct, ct)
        events.append((c.get("minute") or 0, f"{side}{card_cn}", ""))
    for minute, event, score in sorted(events, key=lambda e: e[0]):
        s += f"| {minute}' | {event} | {score} |\n"
    return s


def _section2_vs_actual(review: sqlite3.Row, pred: Dict[str, Any], fields: Dict[str, Any]) -> str:
    s = "\n---\n\n## 二、预测 vs 实际对比\n\n"
    s += "| 维度 | 预测 | 实际 | 是否正确 | 偏差说明 |\n|------|------|------|:--------:|---------|\n"

    def _mark(v: Optional[int]) -> str:
        if v is None:
            return "—"
        return "✅" if v == 1 else "❌"

    pred_wdl = pred.get("pred_wdl")
    pred_wdl_prob = pred.get("pred_wdl_prob")
    wdl_row = f"{pred_wdl}({pct(pred_wdl_prob)})" if pred_wdl else "—"
    act_wdl = ACTUAL_WDL_NORM.get(review["actual_wdl"]) if review["actual_wdl"] else None
    wdl_dev = ""
    if pred_wdl and act_wdl and pred_wdl != act_wdl:
        wdl_dev = f"模型预测{pred_wdl}，实际{act_wdl}"
    s += f"| 胜平负 | {wdl_row} | {act_wdl or '—'} | {_mark(fields.get('wdl_correct'))} | {wdl_dev} |\n"

    top1 = pred.get("pred_score_top1")
    top1_prob = ""
    if pred.get("score_probs") and top1:
        top1_prob = f"({pct(pred['score_probs'].get(top1, 0))})"
    top1_display = f"{top1}{top1_prob}" if top1 else "无发布记录"
    s += f"| 比分 Top1 | {top1_display} | {review['actual_score'] or '—'} | " \
         f"{_mark(fields.get('score_top1_hit'))} | {'发布比分未入库，未参与复盘' if not top1 else '—'} |\n"

    top5 = pred.get("pred_score_top5") or []
    top5_str = "、".join(top5) if top5 else "无发布记录"
    s += f"| 比分 Top5 覆盖 | {top5_str} | {review['actual_score'] or '—'} | " \
         f"{_mark(fields.get('score_top5_cover'))} | {'发布比分未入库，未参与复盘' if not top5 else '—'} |\n"

    pred_hcp = pred.get("pred_hcp")
    act_hcp = review["actual_hcp"] or "—"
    hcp_dev = ""
    if pred_hcp and act_hcp != "—" and pred_hcp != act_hcp:
        hcp_dev = f"模型{pred_hcp}，实际{act_hcp}"
    s += f"| 让球 | {pred_hcp or '—'} | {act_hcp} | {_mark(fields.get('hcp_correct'))} | {hcp_dev} |\n"

    # C-20260921-035：总进球改精确档位 Top1 / Top3 覆盖（旧大小球结论停用）
    tg1 = pred.get("pred_tg_top1")
    tg1_prob = ""
    if pred.get("tg_top3_probs") and tg1:
        tg1_prob = f"({pct(pred['tg_top3_probs'].get(tg1, 0))})"
    tg1_display = f"{tg1}{tg1_prob}" if tg1 else "无发布记录"
    act_tg = review["actual_tg"]
    act_tg_str = f"{act_tg}球" if act_tg is not None else "—"
    tg1_dev = "—" if not tg1 or act_tg is None else (
        "—" if pred.get("pred_tg_top1_goals") == int(act_tg)
        else f"模型预测{tg1}，实际{act_tg}球"
    )
    s += f"| 进球数 Top1 | {tg1_display} | {act_tg_str} | " \
         f"{_mark(fields.get('tg_top1_hit'))} | {'发布进球数未入库，未参与复盘' if not tg1 else tg1_dev} |\n"

    tg3 = pred.get("pred_tg_top3") or []
    tg3_str = "、".join(tg3) if tg3 else "无发布记录"
    tg3_goals = pred.get("pred_tg_top3_goals") or []
    tg3_dev = "—" if not tg3 or act_tg is None else (
        "—" if int(act_tg) in tg3_goals
        else f"实际{act_tg}球不在Top3"
    )
    s += f"| 进球数 Top3 覆盖 | {tg3_str} | {act_tg_str} | " \
         f"{_mark(fields.get('tg_top3_cover'))} | {'发布进球数未入库，未参与复盘' if not tg3 else tg3_dev} |\n"

    # 预测质量评分
    s += "\n### 预测质量评分\n\n"
    s += "| 指标 | 数值 | 基准 | 评价 |\n|------|:----:|:----:|------|\n"
    rps = fields.get("single_rps")
    ll = fields.get("single_logloss")
    pr = fields.get("prob_rank")
    calib = fields.get("calibration_gap")
    s += f"| 单场 RPS | {fnum(rps)} | 0.1984 | {'⚠️ 偏高' if rps is not None and rps > 0.1984 else '✅ 正常'} |\n"
    s += f"| 单场 LogLoss | {fnum(ll)} | 1.056 | {'⚠️ 偏高' if ll is not None and ll > 1.056 else '✅ 正常'} |\n"
    if pr:
        s += f"| 概率排名 | 第{pr}名 | — | 实际结果概率排名 |\n"
    else:
        s += "| 概率排名 | — | — | 无 WDL 三向概率 |\n"
    if calib:
        if "note" in calib:
            s += f"| 校准偏差 | 样本不足 | — | {calib.get('bucket')} 桶历史命中率不足 20 样本 |\n"
        else:
            gap = calib["gap"]
            flag = "⚠️ 高估" if gap > 0.15 else "✅ 正常"
            s += f"| 校准偏差 | {pct(calib['gap'])} | — | 模型{calib['model_prob']:.0%} vs 历史命中率{pct(calib['bucket_hit_rate'])} → {flag} |\n"
    else:
        s += "| 校准偏差 | — | — | 无历史分桶数据 |\n"
    return s


def _lambda_chain_review(conn: sqlite3.Connection, review: sqlite3.Row,
                         pred: Dict[str, Any]) -> str:
    """C-20260923-063: λ 告警的赛后复核（赛前报告 §四 风险提示的闭环）。

    触发条件：赛前 Lambda_alert=1（λ主客差>1.2）。复核方法：模型 λ主/λ客
    对比赛后真实 xG（Understat，缺失时 xgscore.io fallback），|λ-xG|≤0.5
    视为链路合理，>0.5 指明高估/低估方向。
    """
    la = pred.get("Lambda_alert", {}).get("prediction")
    # Lambda_alert 发布行为文本消息（如「λ差值告警: λ主客差=4.511 > 1.2 ...」），
    # 非布尔标志；发布行存在且消息非空即视为触发
    alerted = la is not None and str(la).strip() != ""
    if not alerted:
        return ""
    lh = pred.get("Lambda_home", {}).get("prediction")
    la_ = pred.get("Lambda_away", {}).get("prediction")
    if lh is None or la_ is None:
        return "\n### λ 链路赛后复核\n\n> ⚠️ 触发了 λ 告警但 Lambda_home/away 无发布记录，无法复核\n"
    try:
        lh, la_ = float(lh), float(la_)
    except (TypeError, ValueError):
        return "\n### λ 链路赛后复核\n\n> ⚠️ Lambda 发布值非法，无法复核\n"
    basics = get_match_basics(conn, review["match_id"])
    xg = get_understat_xg(conn, basics) if basics else None
    s = "\n### λ 链路赛后复核\n\n"
    if not xg:
        s += "> ⚠️ 赛后真实 xG 不可得（Understat 缺失且 xgscore fallback 未命中），无法定量复核\n"
        return s
    dh, da = abs(lh - xg["home_xg"]), abs(la_ - xg["away_xg"])
    verdict_h = "✅ 链路合理" if dh <= 0.5 else f"⚠️ {'高估' if lh > xg['home_xg'] else '低估'}（偏差 {dh:.2f}）"
    verdict_a = "✅ 链路合理" if da <= 0.5 else f"⚠️ {'高估' if la_ > xg['away_xg'] else '低估'}（偏差 {da:.2f}）"
    s += "| 侧 | 模型 λ | 赛后实际 xG | 偏差 \\|λ-xG\\| | 判定 |\n|------|:----:|:----:|:----:|------|\n"
    s += f"| 主队 | {lh:.2f} | {xg['home_xg']:.2f} | {dh:.2f} | {verdict_h} |\n"
    s += f"| 客队 | {la_:.2f} | {xg['away_xg']:.2f} | {da:.2f} | {verdict_a} |\n"
    if dh <= 0.5 and da <= 0.5:
        s += "\n> 结论：λ 链路对两队进球期望的刻画与实际 xG 偏差可控（≤0.5），告警属「期望失衡但方向正确」型，无需修链路。\n"
    else:
        s += "\n> 结论：存在偏差侧，建议核查赔率隐含→λ 缩放链路（见赛前报告 §四 风险提示），并归档至 B4 修正规则候选。\n"
    return s


def _section3_attribution(review: sqlite3.Row, attribution: Optional[Dict[str, Any]],
                          conn: Optional[sqlite3.Connection] = None,
                          pred: Optional[Dict[str, Any]] = None) -> str:
    s = "\n---\n\n## 三、AI 自动归因分析\n"
    if not attribution or not attribution.get("attributions"):
        s += "\n> ⚠️ 无归因数据（先运行 A3 attribution_engine --collect --write 或本脚本自动补算）\n"
    else:
        attrs = sorted(attribution["attributions"], key=lambda a: a["weight"], reverse=True)
        s += "\n### 归因权重排序\n\n"
        s += "| 排名 | 归因类型 | 权重 | 核心说明 |\n|:----:|---------|:----:|---------|\n"
        for i, a in enumerate(attrs, 1):
            s += f"| {i} | {a['type']} | {a['weight']:.1f}% | {a.get('description','')} |\n"
        s += "\n### 详细归因分析\n"
        for i, a in enumerate(attrs, 1):
            s += f"\n#### {i}. {a['type']}（权重 {a['weight']:.1f}%）\n\n"
            s += f"**描述**：{a.get('description','')}\n\n"
            ev = a.get("evidence") or {}
            if ev:
                s += "**证据**：\n"
                for k, v in ev.items():
                    try:
                        v_str = json.dumps(v, ensure_ascii=False, default=str)
                    except (TypeError, ValueError):
                        v_str = str(v)
                    s += f"- {k}: {v_str}\n"
            s += "\n"
    if conn is not None and pred:
        s += _lambda_chain_review(conn, review, pred)
    return s


def _section4_quality(review: sqlite3.Row, dq: Dict[str, Any]) -> str:
    s = "\n---\n\n## 四、数据质量检查\n\n"
    s += "| 数据类型 | 状态 | 说明 |\n|---------|:----:|------|\n"
    for c in dq["coverage"]:
        s += f"| {c['type']} | {c['status']} | {c['note']} |\n"
    s += f"\n**数据质量评分**：{dq['score']} / 1.0\n"
    if dq["issues"]:
        s += "\n**数据问题说明**：\n"
        for iss in dq["issues"]:
            s += f"- {iss}\n"
    if dq["alert"]:
        s += "\n> 🚨 **数据完整性告警**：评分 <0.7，本场不纳入性能统计（对齐 A6 门禁）\n"
    return s


def _section5_lessons(review: sqlite3.Row, attribution: Optional[Dict[str, Any]],
                      fields: Dict[str, Any]) -> str:
    s = "\n---\n\n## 五、经验教训与改进建议\n"
    types = [a["type"] for a in (attribution or {}).get("attributions", [])] if attribution else []

    short: List[str] = []
    mid: List[str] = []
    long: List[str] = []

    if "数据缺失归因" in types:
        short.append("**补采赛前特征**：该场关键特征缺失率>20%，下一场先校验 sofascore_team_features 覆盖率再预测")
    if "赔率异动归因" in types:
        short.append("**赛前1小时重查竞彩赔率**：临场异动>5% 时人工复核方向判断，必要时更新时序特征")
    if "阵容异动归因" in types:
        short.append("**赛前1小时重新拉取首发**：实际首发与基准差异>3人时更新阵容可用性特征")
    if "平局过度自信" in str(attribution):
        short.append("**平局方向谨慎**：模型平局概率系统性高于市场，重赛前对平局概率>市场5pp 的场次降权")
    if not short:
        short.append("**保持现状**：无显著单场异常，维持既有预测流程")

    if "运气偏差归因" in types:
        mid.append("**运气偏差不可优化**：xG 与实际进球偏差记录即可，不反馈模型调参")
    if "异常事件归因" in types:
        mid.append("**事件影响评估**：红牌/点球对比分影响显著，复盘时纳入异常事件标签")
    if "战意归因" in types:
        mid.append("**战意特征增强**：爆冷场次胜方跑动显著更高，探索跑动/逼抢投入度特征")

    if "校准偏移归因" in types or any(k in types for k in ("模型局限性归因",)):
        long.append("**概率校准跟踪**：持续监控概率档命中率，系统性高估方向进入重训候选")
    if fields.get("wdl_correct") == 0:
        long.append("**方向误判样本累积**：定期回溯方向误判场次，识别共同特征")

    s += "\n### 短期改进（下一场即可应用）\n\n"
    for i, t in enumerate(short, 1):
        s += f"{i}. {t}\n"
    s += "\n### 中期改进（1-2 周内）\n\n"
    for i, t in enumerate(mid or ["**暂无**：无中等周期改进项"], 1):
        s += f"{i}. {t}\n"
    s += "\n### 长期改进（1 月以上）\n\n"
    for i, t in enumerate(long or ["**暂无**：无长期改进项"], 1):
        s += f"{i}. {t}\n"
    return s


def _section6_factors(review: sqlite3.Row, attribution: Optional[Dict[str, Any]],
                      confidence: Optional[int]) -> str:
    s = "\n---\n\n## 六、因子库更新建议\n\n"
    s += "| 建议条目 | 层级 | 联赛 | 可信度 | 状态 |\n|---------|:----:|------|:------:|------|\n"
    types = [a["type"] for a in (attribution or {}).get("attributions", [])] if attribution else []
    rows: List[tuple] = []

    if "赔率异动归因" in types:
        rows.append(("赛前1小时竞彩赔率异动>5% 对预测影响显著，需增强时序赔率特征权重", "L2 特征工程", review["league"], 3))
    if "阵容异动归因" in types:
        rows.append(("实际首发与基准差异>3人时预测偏差显著，需新增阵容差异度特征", "L2 特征工程", review["league"], 4))
    if "运气偏差归因" in types:
        rows.append(("xG 高但进球少场次属运气偏差，比分 Top1 命中率天然受限", "L1 统计先验", "全局", 2))
    if "平局过度自信" in str(attribution):
        rows.append(("模型平局概率系统性高于市场，重训时平局方向样本校准/降权", "L3 样本权重", review["league"], 3))
    if "战意归因" in types:
        rows.append(("爆冷胜场次胜方跑动显著更高，战意/投入度特征有增量", "L2 特征工程", "全局", 2))

    if not rows:
        s += "| — | — | — | — | 无高置信因子建议 |\n"
    else:
        for item, level, league, conf in rows:
            status = "☑ 建议写入" if conf >= 4 else "⚠ 待验证"
            s += f"| {item} | {level} | {league or '—'} | {conf} | {status} |\n"
    s += "\n**写入规则**：只有可信度 ≥4 级的条目才写入因子库；3 级及以下标记为“待验证”。\n"
    return s


def _section7_sign(review: sqlite3.Row, confidence: Optional[int],
                   fields: Optional[Dict[str, Any]] = None,
                   dq: Optional[Dict[str, Any]] = None) -> str:
    """C-20260921-042: 自动审核替代人工签字——基于数据驱动规则自动判定。
    C-20260922-049: 三态判定重构——区分数据不足 / 冷门误判 / 真异常。

    判定规则（C-049 三态）：
      ✅ 通过：RPS≤0.1984 且 LogLoss≤1.056 且 数据质量≥0.8 且 校准偏差≤15%
      ⏸️ 数据不足：数据质量<0.5（对齐 A6 门禁，不纳入统计，非模型问题）
      ❌ 异常：DQ≥0.5 且 (RPS≥0.50 或 LogLoss≥2.0)，极端误判需排查
      ⚠️ 待改进：DQ≥0.5 且 RPS/LogLoss 偏高但未达极端（含冷门误判，单场随机性大）
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    s = "\n---\n\n## 七、自动审核\n\n"
    s += "| 项目 | 内容 |\n|------|------|\n"
    s += f"| 复盘生成时间 | {now} |\n"
    s += f"| 归因引擎版本 | {ATTRIBUTION_VERSION} |\n"
    s += f"| 可信度评级 | {confidence if confidence else '—'} 级 |\n"

    # 收集审核指标
    checks: List[tuple] = []  # (指标名, 数值, 基准, 通过?)
    rps = fields.get("single_rps") if fields else None
    ll = fields.get("single_logloss") if fields else None
    calib = fields.get("calibration_gap") if fields else None
    dq_score = dq.get("score") if dq else None

    rps_pass = rps is not None and rps <= 0.1984
    checks.append(("单场 RPS", fnum(rps), "≤0.1984", rps_pass))

    ll_pass = ll is not None and ll <= 1.056
    checks.append(("单场 LogLoss", fnum(ll), "≤1.056", ll_pass))

    if dq_score is not None:
        dq_pass = dq_score >= 0.8
        checks.append(("数据质量分", f"{dq_score:.2f}", "≥0.80", dq_pass))
    else:
        checks.append(("数据质量分", "—", "≥0.80", False))

    if calib and "gap" in calib:
        calib_pass = calib["gap"] <= 0.15
        checks.append(("校准偏差", pct(calib["gap"]), "≤15%", calib_pass))
    else:
        checks.append(("校准偏差", "—", "≤15%", True))

    # 方向命中
    wdl_correct = fields.get("wdl_correct") if fields else None
    if wdl_correct is not None:
        checks.append(("方向预测", "✅命中" if wdl_correct == 1 else "❌未命中", "—", wdl_correct == 1))

    # C-20260922-049: 三态判定——区分数据不足 / 冷门误判 / 真异常
    # 数据不足：DQ<0.5，对齐 A6 门禁，不纳入统计，不算模型问题
    # 真异常：DQ≥0.5 且 (RPS≥0.50 或 LogLoss≥2.0)，极端误判需排查
    # 冷门误判：DQ≥0.5 且 RPS/LogLoss 偏高但未达极端，单场随机性大
    data_insufficient = dq_score is not None and dq_score < 0.5
    model_severe = (
        not data_insufficient
        and (
            (rps is not None and rps >= 0.50)
            or (ll is not None and ll >= 2.0)
        )
    )
    all_pass = all(c[3] for c in checks if c[0] != "方向预测")
    any_fail = any(not c[3] for c in checks if c[0] != "方向预测")

    if data_insufficient:
        verdict = "⏸️ 数据不足"
        verdict_note = (
            f"数据完整性不足（DQ={dq_score:.2f}<0.5），本场不纳入性能统计"
            f"（对齐 A6 门禁），请补采数据后复评，非模型问题"
        )
    elif model_severe:
        verdict = "❌ 异常"
        severe_items = []
        if rps is not None and rps >= 0.50:
            severe_items.append(f"RPS={rps:.3f}≥0.50")
        if ll is not None and ll >= 2.0:
            severe_items.append(f"LogLoss={ll:.3f}≥2.0")
        verdict_note = (
            f"极端误判（{'、'.join(severe_items)}），数据完整但模型方向严重错误，需排查"
        )
    elif all_pass:
        verdict = "✅ 通过"
        verdict_note = "所有核心指标达标"
    elif any_fail:
        verdict = "⚠️ 待改进"
        fails = [c[0] for c in checks if not c[3] and c[0] != "方向预测"]
        verdict_note = (
            f"未达标项: {', '.join(fails)}（含冷门误判，单场随机性大）"
        )
    else:
        verdict = "⚠️ 待改进"
        verdict_note = "部分指标缺失，建议补充数据后复评"

    s += f"| 自动审核结论 | **{verdict}** |\n"
    s += f"| 审核依据 | {verdict_note} |\n"

    s += "\n### 审核指标明细\n\n"
    s += "| 指标 | 数值 | 基准 | 判定 |\n|------|:----:|:----:|:----:|\n"
    for name, val, baseline, passed in checks:
        flag = "✅" if passed else "❌"
        s += f"| {name} | {val} | {baseline} | {flag} |\n"

    s += f"\n> 审核规则（C-049 三态）：RPS≤0.1984、LogLoss≤1.056、数据质量≥0.80、校准偏差≤15% "
    s += f"全部达标→✅通过；DQ<0.5→⏸️数据不足（不纳入统计）；"
    s += f"DQ≥0.5 且 RPS≥0.50 或 LogLoss≥2.0→❌异常（极端误判）；"
    s += f"其余未达标项→⚠️待改进（含冷门误判，单场随机性大）。"
    s += f"方向命中不参与通过/失败判定。\n"

    s += "\n---\n\n*报告由 generate_post_match_report.py 自动生成（C-20260921-042 自动审核）*\n"
    return s


def build_report(review: sqlite3.Row, pred: Dict[str, Any], fields: Dict[str, Any],
                 attribution: Optional[Dict[str, Any]],
                 dq: Dict[str, Any],
                 post_data: Optional[Dict[str, Any]] = None,
                 conn: Optional[sqlite3.Connection] = None,
                 stat500: Optional[sqlite3.Row] = None) -> str:
    confidence = attribution.get("confidence_level") if attribution else None
    parts = [
        _header(review, fields, confidence),
        _section1_result(review, post_data, stat500),
        _section2_vs_actual(review, pred, fields),
        _section3_attribution(review, attribution, conn=conn, pred=pred),
        _section4_quality(review, dq),
        _section5_lessons(review, attribution, fields),
        _section6_factors(review, attribution, confidence),
        _section7_sign(review, confidence, fields=fields, dq=dq),
    ]
    return "\n".join(parts) + "\n"


# ==================== 归因补算 ====================
def ensure_attribution(conn: sqlite3.Connection, review: sqlite3.Row,
                       post_data: Optional[Dict[str, Any]] = None) -> Optional[Dict[str, Any]]:
    """attribution_json 缺失时自动调用 A3 归因引擎补算。"""
    if review["attribution_json"]:
        return _load_json(review["attribution_json"])
    engine = AttributionEngine(conn_odds=conn)
    try:
        return engine.run(review["match_id"], post_data=post_data)
    except Exception as e:  # noqa: BLE001
        logging.getLogger("a4").warning(f"归因补算失败: {e}")
        return None
    finally:
        engine.close()


# ==================== 汇总页 ====================
def build_summary(reviews: List[Dict[str, Any]], output_dir: Path) -> str:
    n = len(reviews)
    ok_wdl = sum(1 for r in reviews if r["fields"].get("wdl_correct") == 1)
    ok_t1 = sum(1 for r in reviews if r["fields"].get("score_top1_hit") == 1)
    ok_t5 = sum(1 for r in reviews if r["fields"].get("score_top5_cover") == 1)
    ok_hcp = sum(1 for r in reviews if r["fields"].get("hcp_correct") == 1)
    ok_tg1 = sum(1 for r in reviews if r["fields"].get("tg_top1_hit") == 1)
    ok_tg3 = sum(1 for r in reviews if r["fields"].get("tg_top3_cover") == 1)
    alert_rows = [r for r in reviews if r.get("dq", {}).get("alert")]

    rps_vals = [r["fields"]["single_rps"] for r in reviews
                if r["fields"].get("single_rps") is not None and not math.isnan(r["fields"]["single_rps"])]
    ll_vals = [r["fields"]["single_logloss"] for r in reviews
               if r["fields"].get("single_logloss") is not None and not math.isnan(r["fields"]["single_logloss"])]

    date = output_dir.name
    s = f"# 赛后复盘汇总 — {date}\n\n"
    if alert_rows:
        # 对齐 A6 门禁：数据完整性不足时加「[缺数据]」红标
        s += (f"> 🚨 **[[缺数据]] 数据完整性告警**：{len(alert_rows)}/{n} 场评分 <0.7，已排除出性能统计，"
              f"请运营回踩补采（`--no-skip-existing` 重跑 A2 后重生成）\n")
    s += f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
    s += f"> 当日复盘场次: {n}\n"
    s += f"> 数据完整性告警: {len(alert_rows)} 场\n"
    s += "\n---\n\n## 当日命中/失败统计\n\n"
    s += "| 维度 | 命中 | 失败 | 未判定 | 命中率 |\n|------|:----:|:----:|:------:|:------:|\n"

    def _row(label, ok, total):
        miss = total - ok
        none_n = n - total
        rate = f"{pct(ok / total)}" if total else "—"
        return f"| {label} | {ok} | {miss} | {none_n} | {rate} |\n"

    s += _row("胜平负方向", ok_wdl, sum(1 for r in reviews if r["fields"].get("wdl_correct") is not None))
    s += _row("比分 Top1", ok_t1, sum(1 for r in reviews if r["fields"].get("score_top1_hit") is not None))
    s += _row("比分 Top5 覆盖", ok_t5, sum(1 for r in reviews if r["fields"].get("score_top5_cover") is not None))
    s += _row("让球", ok_hcp, sum(1 for r in reviews if r["fields"].get("hcp_correct") is not None))
    s += _row("进球数 Top1", ok_tg1, sum(1 for r in reviews if r["fields"].get("tg_top1_hit") is not None))
    s += _row("进球数 Top3 覆盖", ok_tg3, sum(1 for r in reviews if r["fields"].get("tg_top3_cover") is not None))

    if rps_vals:
        s += f"\n**平均单场 RPS**：{sum(rps_vals)/len(rps_vals):.4f}（n={len(rps_vals)}）\n"
    if ll_vals:
        s += f"**平均单场 LogLoss**：{sum(ll_vals)/len(ll_vals):.4f}（n={len(ll_vals)}）\n"

    # 归因主因分布（人工只看结论 → 当日偏差根因聚合）
    s += "\n---\n\n## 归因主因分布\n\n"
    cause_counts: Dict[str, int] = {}
    for r in reviews:
        attr = r.get("attribution") or {}
        cause = attr.get("primary_cause", "—") if attr else "—"
        cause_counts[cause] = cause_counts.get(cause, 0) + 1
    s += "| 归因主因 | 场次 | 占比 |\n|---------|:----:|:----:|\n"
    for cause, cnt in sorted(cause_counts.items(), key=lambda kv: kv[1], reverse=True):
        s += f"| {cause} | {cnt} | {pct(cnt / n) if n else '—'} |\n"

    # 可信度分布
    s += "\n## 可信度分布\n\n"
    conf_counts: Dict[str, int] = {}
    for r in reviews:
        attr = r.get("attribution") or {}
        conf = str(attr.get("confidence_level", "—")) if attr else "—"
        conf_counts[conf] = conf_counts.get(conf, 0) + 1
    s += "| 可信度 | 场次 |\n|:------:|:----:|\n"
    for conf in sorted(conf_counts, key=lambda c: (c == "—", c)):
        s += f"| {conf} 级 | {conf_counts[conf]} |\n"

    # 数据完整性告警明细（对齐 A6：<0.7 不纳入性能统计）
    if alert_rows:
        s += "\n## 数据完整性告警明细\n\n"
        s += "| 场次 | 数据质量分 | 告警原因 |\n|------|:------:|---------|\n"
        for r in alert_rows:
            dq = r.get("dq", {})
            issues = "；".join(dq.get("issues", [])[:3]) or "—"
            s += f"| {r['review']['match_id']} | {dq.get('score', '—')} | {issues} |\n"
        s += "\n> 🚨 上述场次已排除出命中率/质量分统计（对齐 A6 质量门禁）；数据缺失类问题只反馈采集器，不反馈模型调参。\n"

    s += "\n---\n\n## 场次列表\n\n"
    s += "| 场次 | 比分 | 方向 | 归因主因 | 可信度 | 数据质量 |\n|------|------|:----:|---------|:------:|:------:|\n"
    for r in reviews:
        mid = r["review"]["match_id"]
        fname = r.get("report_file", "")
        link = f"[{mid}]({fname})" if fname else mid
        attr = r.get("attribution") or {}
        primary = attr.get("primary_cause", "—") if attr else "—"
        conf = attr.get("confidence_level", "—") if attr else "—"
        wdl_ok = "✅" if r["fields"].get("wdl_correct") == 1 else ("❌" if r["fields"].get("wdl_correct") == 0 else "—")
        dq = r.get("dq", {})
        dq_str = f"{dq.get('score', '—')}" + ("🚨" if dq.get("alert") else "")
        s += f"| {link} | {r['review']['actual_score'] or '—'} | {wdl_ok} | {primary} | {conf} | {dq_str} |\n"
    return s


# ==================== 主流程 ====================
def run_report(match_date: Optional[str], match_id: Optional[str],
               do_collect: bool, do_write: bool, dry_run: bool,
               logger: logging.Logger) -> Dict[str, Any]:
    conn = _get_conn()
    try:
        ensure_post_match_schema(conn)
        rows = load_review_rows(conn, match_date, match_id)
        if not rows:
            logger.info(f"post_match_review 无目标场次（date={match_date} match_id={match_id}）")
            return {"total": 0}

        if match_date:
            out_dir = PROJECT_DIR / "docs" / "post_match" / match_date.replace("-", "")
        else:
            d = str(rows[0]["match_date"] or match_id[:10] or datetime.now().strftime("%Y-%m-%d"))
            out_dir = PROJECT_DIR / "docs" / "post_match" / d.replace("-", "")
        out_dir.mkdir(parents=True, exist_ok=True)

        summaries: List[Dict[str, Any]] = []
        for row in rows:
            mid = row["match_id"]
            logger.info(f"[{mid}] 生成复盘报告 ...")
            pred = load_predictions(conn, mid)
            fields = compute_review_fields(conn, row, pred)

            post_data = None
            if do_collect:
                post_data = _load_post_data(mid, logger)

            attribution = ensure_attribution(conn, row, post_data=post_data)
            dq = compute_data_quality(conn, row, pred, post_data=post_data)

            # C-20260921-042: 用 resolved actual_hcp 构造 mutable review dict
            review_dict = dict(row)
            if not review_dict.get("actual_hcp") and fields.get("actual_hcp_resolved"):
                review_dict["actual_hcp"] = fields["actual_hcp_resolved"]

            if dry_run:
                logger.info(f"[{mid}] [dry-run] 将写入: 方向={'✅' if fields.get('wdl_correct')==1 else '❌'} "
                            f"RPS={fnum(fields.get('single_rps'))}")
                summaries.append({"review": row, "fields": fields, "attribution": attribution, "dq": dq})
                continue

            # 汇总字段补全（写回用）
            if fields.get("single_rps") is not None or fields.get("wdl_correct") is not None:
                _fill_review_fields(conn, mid, fields, pred)

            report = build_report(review_dict, pred, fields, attribution, dq, post_data=post_data,
                                  conn=conn,
                                  stat500=conn.execute(
                                      "SELECT * FROM odds500_stat WHERE match_id=?", (mid,)
                                  ).fetchone())
            safe_home = _safe_name(canonical_team_name(mid, row["home_team"], is_home=True))
            safe_away = _safe_name(canonical_team_name(mid, row["away_team"], is_home=False))
            fname = f"{row['league'] or '未知'}_{safe_home}_vs_{safe_away}_复盘.md"
            fpath = out_dir / fname
            fpath.write_text(report, encoding="utf-8")
            logger.info(f"[{mid}] 已写出 {fpath.name}")
            summaries.append({"review": row, "fields": fields, "attribution": attribution,
                              "dq": dq, "report_file": fname})

        if not dry_run and summaries:
            summary_md = build_summary(summaries, out_dir)
            (out_dir / "_summary.md").write_text(summary_md, encoding="utf-8")
            logger.info(f"已写出 {out_dir / '_summary.md'}")

        return {"total": len(rows), "reports": len(summaries)}
    finally:
        conn.close()


def _fill_review_fields(conn: sqlite3.Connection, match_id: str, fields: Dict[str, Any],
                        pred: Optional[Dict[str, Any]] = None) -> None:
    """回填 pred_*/质量分到 post_match_review（幂等 UPDATE）。

    C-20260921-034：pred 非空时同步修正 pred_score_top1/pred_score_top5
    （旧实现把 λ 重推的伪预测写入了这两列）。
    C-20260921-035：总进球改 pred_tg_top1/pred_tg_top3 + tg_top1_hit/tg_top3_cover
    （旧 pred_tg/tg_correct 大小球口径停用）。
    """
    sets, params = [], []
    for col in ("wdl_correct", "score_top1_hit", "score_top5_cover", "hcp_correct",
                "tg_top1_hit", "tg_top3_cover"):
        if fields.get(col) is not None:
            sets.append(f"{col}=?")
            params.append(int(fields[col]))
    for col in ("single_rps", "single_logloss"):
        v = fields.get(col)
        if v is not None and not math.isnan(v):
            sets.append(f"{col}=?")
            params.append(round(float(v), 6))
    if fields.get("prob_rank") is not None:
        sets.append("prob_rank=?")
        params.append(int(fields["prob_rank"]))
    if pred is not None:
        if pred.get("pred_score_top1"):
            sets.append("pred_score_top1=?")
            params.append(pred["pred_score_top1"])
        t5 = pred.get("pred_score_top5")
        if t5:
            sets.append("pred_score_top5=?")
            params.append(json.dumps(t5, ensure_ascii=False))
        # C-20260921-035：总进球精确档位（只写真实发布行对应的预测）
        if pred.get("pred_tg_top1"):
            sets.append("pred_tg_top1=?")
            params.append(pred["pred_tg_top1"])
        tg3 = pred.get("pred_tg_top3")
        if tg3:
            sets.append("pred_tg_top3=?")
            params.append(json.dumps(tg3, ensure_ascii=False))
    if not sets:
        return
    params.append(match_id)
    cur = conn.cursor()
    cur.execute(f"UPDATE post_match_review SET {', '.join(sets)} WHERE match_id=?", params)
    conn.commit()


def _load_post_data(match_id: str, logger: logging.Logger) -> Optional[Dict[str, Any]]:
    """--collect：采集 SofaScore 赛后明细（时间线/统计/阵容）。"""
    conn = _get_conn()
    try:
        event_id = get_event_id(conn, match_id)
        if not event_id:
            logger.warning(f"[{match_id}] 无 fbref_match_mapping 映射，跳过赛后采集")
            return None
    finally:
        conn.close()
    sys.path.insert(0, str(PROJECT_DIR / "collection"))
    from final_sofascore_collector import SofaScoreClient, collect_post_match
    client = SofaScoreClient(logger=logger)
    try:
        return collect_post_match(client, event_id, logger)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{match_id}] 赛后采集失败: {e}")
        return None
    finally:
        client.close()


def _safe_name(name: Optional[str]) -> str:
    if not name:
        return "未知"
    return "".join(c for c in str(name) if c not in r'\/:*?"<>|')


def main() -> None:
    parser = argparse.ArgumentParser(description="模块 A4：自动复盘报告生成器（8 章节 markdown）")
    parser.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                        help="批量生成当日复盘报告（扫 post_match_review.match_date）")
    parser.add_argument("--match-id", type=str, default=None, metavar="MATCH_ID",
                        help="单场生成（优先级高于 --date）")
    parser.add_argument("--no-collect", action="store_true",
                        help="跳过采集 SofaScore 赛后明细（默认自动采集）")
    parser.add_argument("--write", action="store_true",
                        help="回填 pred_*/质量分到 post_match_review（默认回填，兼容参数）")
    parser.add_argument("--dry-run", action="store_true", help="只扫不写文件")
    args = parser.parse_args()

    if not args.date and not args.match_id:
        parser.error("必须提供 --date 或 --match-id 之一")

    logger = logging.getLogger("generate_post_match_report")
    if not logger.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter("%(levelname)s %(message)s"))
        logger.addHandler(h)
    logger.setLevel(logging.INFO)

    # C-20260921-042: --collect 改为默认行为（用 --no-collect 跳过）
    do_collect = not args.no_collect
    stats = run_report(args.date, args.match_id, do_collect, args.write, args.dry_run, logger)
    print("=" * 70)
    print(f"复盘报告生成汇总")
    print(f"  目标场次      : {stats['total']}")
    print(f"  生成报告      : {stats['reports']}")
    print("=" * 70)


if __name__ == "__main__":
    main()
