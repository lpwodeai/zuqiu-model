# -*- coding: utf-8 -*-
"""P2-04 数据源冲突检测告警。

问题背景（docs/基于预测报告发现的问题.txt §4.6）：
  SofaScore 基本面（评分/xG）与 500 百家欧指市场信号（隐含赔率）可能方向相反
  （如基本面主队一般，但市场大幅下调主胜赔率）。模型直接融合特征输出，此前
  **未检测跨源信号冲突**，冲突发生时也不会扣置信度或生成风险标签。

本模块：
  1. 纯函数 detect_conflict()：输入基本面主胜概率 + 市场主胜概率，输出冲突判定
     （方向背离 / 概率差超阈值）+ 告警标签 + 冲突分数（0~1，供置信度扣分）。
  2. 批量诊断 run_diagnosis()：对历史比赛 join SofaScore 基本面与 500 市场信号，
     统计冲突率、分布，输出冲突样本清单。

数据源：
  - sofascore_team_features：sofa_rat_5g_home/away（近 5 场平均评分，基本面）
  - odds500_match：win/draw/lost + home_team_cn/away_team_cn（500.com 市场信号）

用法：
  python data_source_conflict_detector.py
  python data_source_conflict_detector.py --db data/odds.db --gap 0.15

输出：
  - reports/data_source_conflict_<TS>.md / .json
"""
import argparse
import json
import math
import os
import sqlite3
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feature_utils import normalize_team_name  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"

# 基本面评分 → 主胜概率的 logistic 参数。
# 由历史数据（sofa_rat_5g 评分差 → 500 市场去水主胜概率）拟合得到，保证基本面
# 信号相对市场**无偏**（均值差≈0），使「冲突」真正反映基本面与市场的分歧而非
# 系统性偏移。拟合口径：logit(P_home) = HOME_BASE_LOGIT + RATING_DIFF_COEF × (r_h − r_a)
#   HOME_BASE_LOGIT=−0.2438 → 评分差为 0 时 P_home≈0.439（与真实主胜率一致）
#   RATING_DIFF_COEF=+2.2119 → 评分差每 +0.25，P_home 提升约 5.5pp
HOME_BASE_LOGIT = -0.2438
RATING_DIFF_COEF = 2.2119

CONFLICT_TAG = "⚠️SofaScore基本面与百家欧指市场信号出现明显冲突"


@dataclass
class ConflictResult:
    """一场比赛的跨源冲突检测结果。"""
    fund_home_prob: float = 0.5       # 基本面主胜概率
    market_home_prob: float = 0.5     # 市场主胜概率（去水）
    prob_gap: float = 0.0             # |fund - market|
    fund_direction: str = "neutral"   # home / away / neutral
    market_direction: str = "neutral"
    direction_mismatch: bool = False
    is_conflict: bool = False
    conflict_score: float = 0.0       # 0~1，越高冲突越强（供置信度扣分）
    tag: str = ""
    reason: str = ""
    details: list = field(default_factory=list)


def fundamental_home_prob(rat_home, rat_away):
    """由近 5 场 SofaScore 评分差导出基本面主胜概率（市场无偏 logistic）。"""
    diff = (rat_home - rat_away)
    z = HOME_BASE_LOGIT + RATING_DIFF_COEF * diff
    return 1.0 / (1.0 + math.exp(-z))


def market_home_prob(odds_home, odds_draw, odds_away):
    """由三向赔率去水得到市场主胜概率。"""
    ih, id_, ia = 1.0 / odds_home, 1.0 / odds_draw, 1.0 / odds_away
    total = ih + id_ + ia
    return ih / total


def _direction(p, threshold):
    if p > 0.5 + threshold:
        return "home"
    if p < 0.5 - threshold:
        return "away"
    return "neutral"


def detect_conflict(fund_home_prob, market_home_prob,
                    prob_gap_threshold=0.15, direction_threshold=0.02):
    """检测基本面信号与市场信号的冲突。

    Args:
        fund_home_prob: 基本面主胜概率 [0,1]
        market_home_prob: 市场主胜概率 [0,1]
        prob_gap_threshold: 概率差阈值，超出即判冲突
        direction_threshold: 方向中性区（±该值内视为中性，不算背离）

    Returns:
        ConflictResult
    """
    fund_home_prob = float(fund_home_prob)
    market_home_prob = float(market_home_prob)

    gap = abs(fund_home_prob - market_home_prob)
    fd = _direction(fund_home_prob, direction_threshold)
    md = _direction(market_home_prob, direction_threshold)

    # 方向背离：强主 vs 强客（忽略中性方向）
    mismatch = (fd == "home" and md == "away") or (fd == "away" and md == "home")

    is_conflict = mismatch or gap > prob_gap_threshold

    # 冲突分数：方向背离给高分，概率差线性叠加（归一 0~1）
    base = 0.7 if mismatch else 0.0
    gap_part = min(1.0, gap / (2 * prob_gap_threshold)) * 0.3
    conflict_score = round(min(1.0, base + gap_part), 4)

    details = []
    if mismatch:
        details.append(
            f"方向背离：基本面{'主强' if fd == 'home' else '客强'} vs 市场{'主强' if md == 'home' else '客强'}")
    if gap > prob_gap_threshold:
        details.append(f"概率差 {gap:.1%} 超过阈值 {prob_gap_threshold:.1%}")

    tag = CONFLICT_TAG if is_conflict else ""
    reason = "；".join(details) if details else "无冲突"

    return ConflictResult(
        fund_home_prob=round(fund_home_prob, 4),
        market_home_prob=round(market_home_prob, 4),
        prob_gap=round(gap, 4),
        fund_direction=fd,
        market_direction=md,
        direction_mismatch=mismatch,
        is_conflict=is_conflict,
        conflict_score=conflict_score,
        tag=tag,
        reason=reason,
        details=details,
    )


# ---------------------------------------------------------------------------
# 批量诊断
# ---------------------------------------------------------------------------
def _key(match_date, home, away):
    d = (match_date or "")[:10]
    return (d, normalize_team_name(home), normalize_team_name(away))


def load_fundamentals(db_path):
    """加载 SofaScore 基本面，返回 {key: dict}。只保留评分非零的场次。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "SELECT match_date, home_team_cn, away_team_cn, sofa_rat_5g_home, sofa_rat_5g_away, "
        "sofa_xg_5g_home, sofa_xg_5g_away, league FROM sofascore_team_features"
    )
    out = {}
    for md, h, a, r_h, r_a, x_h, x_a, lg in cur.fetchall():
        if not r_h or not r_a:  # 评分缺失（老赛季为 0）
            continue
        k = _key(md, h, a)
        out[k] = {
            "league": lg,
            "rat_home": float(r_h),
            "rat_away": float(r_a),
            "xg_home": float(x_h or 0.0),
            "xg_away": float(x_a or 0.0),
        }
    conn.close()
    return out


def load_market_signals(db_path):
    """加载 500.com 市场信号，返回 {key: (win, draw, lost)}。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "SELECT match_date, home_team_cn, away_team_cn, win, draw, lost FROM odds500_match"
    )
    out = {}
    for md, h, a, w, d, l in cur.fetchall():
        try:
            wv, dv, lv = float(w), float(d), float(l)
        except (TypeError, ValueError):
            continue
        if wv <= 1.0 or dv <= 1.0 or lv <= 1.0:
            continue
        out[_key(md, h, a)] = (wv, dv, lv)
    conn.close()
    return out


def run_diagnosis(db_path, prob_gap_threshold=0.15):
    """批量检测：join 基本面与市场信号，统计冲突。"""
    fund = load_fundamentals(db_path)
    market = load_market_signals(db_path)

    conflicts = []
    n_matched = 0
    for k, f in fund.items():
        if k not in market:
            continue
        n_matched += 1
        win, draw, lost = market[k]
        fp = fundamental_home_prob(f["rat_home"], f["rat_away"])
        mp = market_home_prob(win, draw, lost)
        res = detect_conflict(fp, mp, prob_gap_threshold=prob_gap_threshold)
        if res.is_conflict:
            conflicts.append({
                "league": f["league"],
                "date": k[0],
                "home": k[1],
                "away": k[2],
                "fund_home_prob": res.fund_home_prob,
                "market_home_prob": res.market_home_prob,
                "prob_gap": res.prob_gap,
                "fund_direction": res.fund_direction,
                "market_direction": res.market_direction,
                "direction_mismatch": res.direction_mismatch,
                "conflict_score": res.conflict_score,
                "reason": res.reason,
            })

    # 冲突分分布（含非冲突）
    score_dist = defaultdict(int)
    for k, f in fund.items():
        if k not in market:
            continue
        win, draw, lost = market[k]
        fp = fundamental_home_prob(f["rat_home"], f["rat_away"])
        mp = market_home_prob(win, draw, lost)
        res = detect_conflict(fp, mp, prob_gap_threshold=prob_gap_threshold)
        bucket = floor_score(res.conflict_score)
        score_dist[bucket] += 1

    return {
        "matched": n_matched,
        "n_conflict": len(conflicts),
        "conflict_rate": len(conflicts) / n_matched if n_matched else 0.0,
        "score_distribution": dict(sorted(score_dist.items())),
        "conflicts": conflicts,
        "threshold": prob_gap_threshold,
    }


def floor_score(s):
    return f"{int(s * 10) / 10:.1f}"


def generate_report(rep, md_path=None, json_path=None):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    md_path = md_path or (REPORT_DIR / f"data_source_conflict_{ts}.md")
    json_path = json_path or (REPORT_DIR / f"data_source_conflict_{ts}.json")

    L = []
    L.append("# 数据源冲突检测诊断报告（P2-04）\n")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append(f"- 匹配样本（SofaScore 评分 × 500 赔率）: {rep['matched']} 场")
    L.append(f"- 冲突场次: {rep['n_conflict']}（{rep['conflict_rate']*100:.1f}%）")
    L.append(f"- 概率差阈值: {rep['threshold']}\n")

    L.append("## 一、冲突分数分布\n")
    L.append("| 冲突分数档 | 场次 |")
    L.append("|---|---|")
    for k, v in rep["score_distribution"].items():
        L.append(f"| {k} | {v} |")
    L.append("")

    L.append("## 二、冲突样本清单（前 100）\n")
    L.append("| 日期 | 联赛 | 对阵 | 基本面主胜 | 市场主胜 | 概率差 | 背离 | 冲突分数 | 说明 |")
    L.append("|---|---|---|---|---|---|---|---|---|")
    for c in rep["conflicts"][:100]:
        L.append(f"| {c['date']} | {c['league']} | {c['home']} vs {c['away']} | "
                 f"{c['fund_home_prob']*100:.1f}% | {c['market_home_prob']*100:.1f}% | "
                 f"{c['prob_gap']*100:.1f}pp | {'是' if c['direction_mismatch'] else '否'} | "
                 f"{c['conflict_score']:.2f} | {c['reason']} |")
    L.append("")
    L.append("---\n*本报告由 data_source_conflict_detector.py 自动生成*\n")

    md_path.write_text("\n".join(L), encoding="utf-8")
    json_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="数据源冲突检测告警（P2-04）")
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--gap", type=float, default=0.15, help="概率差阈值")
    args = parser.parse_args()

    print("检测数据源冲突...")
    rep = run_diagnosis(Path(args.db), prob_gap_threshold=args.gap)
    print(f"  匹配样本: {rep['matched']} | 冲突: {rep['n_conflict']} "
          f"({rep['conflict_rate']*100:.1f}%)")

    md_path, json_path = generate_report(rep)
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")


if __name__ == "__main__":
    main()