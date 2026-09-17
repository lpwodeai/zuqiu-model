# -*- coding: utf-8 -*-
"""P2-01 战意量化修正系数（P2 特征集 + P3 λ 修正系数离线回测）。

问题背景（docs/基于预测报告发现的问题.txt §4.3）：
  原战意仅输出「中游/争欧战」文字标签，未转化为 λ、概率的量化修正系数。缺失
  周中欧战、赛程密集度、保级/争冠/欧战资格等关键变量。

本模块分三层：
  1. build_features()：从 matches 历史赛果构建**战意特征集**（时间安全，只用赛前数据）：
     - 休息天数 rest_days
     - 周中赛事标记 midweek（周二~周四）
     - 近 14 天赛程密集度 density_14d
     - 联赛积分排位 rank_pct / 场均积分 ppg
     - 动机阵营 incentive（title / europe / relegation / midtable）
  2. team_motivation_factors()：将战意特征转化为 λ 进攻/防守修正系数（纯函数）。
  3. run_ablation()：对历史比赛对比「开启战意系数」vs「关闭战意系数」的总进球
     预测误差（MAE/RMSE），量化战意系数的增量增益。

用法：
  python motivation_adjustment.py
  python motivation_adjustment.py --db data/odds.db

输出：
  - reports/motivation_adjustment_<TS>.md / .json
"""
import argparse
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"

# 战意 → λ 修正系数（攻/防两轴，乘法因子；defense_mult>1 表示防守变弱、对手 λ 放大）
# 这些是规则化启发式，可随复盘闭环迭代调参。
MOTIVATION_RULES = {
    "rest_low": {"desc": "休息<3天(疲劳)", "cond": lambda f: f["rest_days"] < 3, "attack": 0.95, "defense": 1.04},
    "rest_high": {"desc": "休息>=7天(充分)", "cond": lambda f: f["rest_days"] >= 7, "attack": 1.05, "defense": 0.97},
    "midweek": {"desc": "周中赛事(周二~四)", "cond": lambda f: f["midweek"] == 1, "attack": 0.97, "defense": 1.02},
    "dense_schedule": {"desc": "近14天>=4场(密集)", "cond": lambda f: f["density_14d"] >= 4, "attack": 0.93, "defense": 1.05},
    "title_chase": {"desc": "争冠(排名<=2)", "cond": lambda f: f["incentive"] == "title", "attack": 1.04, "defense": 0.98},
    "europe_chase": {"desc": "争欧战(排名3~6)", "cond": lambda f: f["incentive"] == "europe", "attack": 1.03, "defense": 0.99},
    "relegation_fight": {"desc": "保级(排名倒数2)", "cond": lambda f: f["incentive"] == "relegation", "attack": 1.05, "defense": 1.02},
}


def parse_score(score_str):
    if not score_str or ":" not in str(score_str):
        return None, None
    try:
        h, a = str(score_str).split(":")[:2]
        return int(h), int(a)
    except (ValueError, IndexError):
        return None, None


def season_from_date(date_str):
    """YYYY-MM-DD → 赛季标签（7 月起算新赛季）。"""
    if not date_str:
        return "unknown"
    try:
        y, m, _ = date_str[:10].split("-")
        year, month = int(y), int(m)
    except (ValueError, IndexError):
        return "unknown"
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def load_matches(db_path):
    """加载历史赛果（含可解析比分的场次）。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute("SELECT match_id, home_team, away_team, match_date, league, actual_score "
                "FROM matches WHERE actual_score IS NOT NULL AND actual_score != ''")
    rows = []
    for mid, h, a, md, lg, sc in cur.fetchall():
        hg, ag = parse_score(sc)
        if hg is None:
            continue
        rows.append({
            "match_id": mid,
            "league": lg or "Unknown",
            "season": season_from_date(md),
            "date": (md or "")[:10],
            "home": h,
            "away": a,
            "home_goals": hg,
            "away_goals": ag,
        })
    conn.close()
    return rows


def team_motivation_factors(feat):
    """战意特征 → (attack_mult, defense_mult)。

    defense_mult > 1 表示该队防守因疲劳/轮换而变弱，对手的 λ 需放大。
    """
    attack = 1.0
    defense = 1.0
    for rule in MOTIVATION_RULES.values():
        if rule["cond"](feat):
            attack *= rule["attack"]
            defense *= rule["defense"]
    return round(attack, 4), round(defense, 4)


def build_features(matches):
    """构建每场比赛主客两队的战意特征（时间安全：只用赛前数据）。"""
    groups = defaultdict(list)
    for m in matches:
        groups[(m["league"], m["season"])].append(m)

    out = []
    for (league, season), g in groups.items():
        g.sort(key=lambda x: (x["date"], x["match_id"]))
        points = defaultdict(int)        # 球队 -> 积分（截至本轮前）
        goal_diff = defaultdict(int)     # 球队 -> 净胜球（截至本轮前）
        team_dates = defaultdict(list)   # 球队 -> 已赛日期列表（升序）
        played = defaultdict(int)

        def _standings_snapshot(set_all_teams):
            teams = set(points) | set_all_teams
            scored = []
            for t in teams:
                scored.append((points[t], goal_diff[t], t))
            scored.sort(key=lambda x: (-x[0], -x[1], x[2]))
            rank_map = {}
            for i, (_, _, t) in enumerate(scored):
                rank_map[t] = i + 1
            return rank_map, len(scored)

        for m in g:
            home, away = m["home"], m["away"]
            rank_map, n_teams = _standings_snapshot({home, away})

            def _feat(team):
                # 休息天数
                ds = team_dates.get(team, [])
                if ds:
                    last = datetime.strptime(ds[-1], "%Y-%m-%d")
                    cur = datetime.strptime(m["date"], "%Y-%m-%d")
                    rest = max(0, (cur - last).days)
                else:
                    rest = 7.0
                # 赛程密集度（近 14 天，不含本场）
                cur_d = datetime.strptime(m["date"], "%Y-%m-%d")
                density = sum(
                    1 for d in ds
                    if (cur_d - datetime.strptime(d, "%Y-%m-%d")).days <= 14
                )
                # 周中赛事
                wd = cur_d.weekday()  # 0=周一 ... 6=周日
                midweek = 1 if wd in (1, 2, 3) else 0  # 周二/三/四
                # 排位 / 场均积分
                rank = rank_map.get(team, (n_teams + 1) // 2)
                rank_pct = rank / max(n_teams, 1)
                pl = played[team]
                ppg = points[team] / pl if pl > 0 else 0.0
                # 动机阵营
                if pl >= 8:
                    if rank <= 2:
                        incentive = "title"
                    elif rank <= 6:
                        incentive = "europe"
                    elif rank >= n_teams - 1:
                        incentive = "relegation"
                    else:
                        incentive = "midtable"
                else:
                    incentive = "neutral"
                return {
                    "rest_days": rest,
                    "midweek": midweek,
                    "density_14d": density,
                    "rank_pct": round(rank_pct, 4),
                    "ppg": round(ppg, 3),
                    "incentive": incentive,
                }

            hf = _feat(home)
            af = _feat(away)
            out.append({
                "match_id": m["match_id"],
                "league": league,
                "season": season,
                "date": m["date"],
                "home": home,
                "away": away,
                "home_goals": m["home_goals"],
                "away_goals": m["away_goals"],
                "total_goals": m["home_goals"] + m["away_goals"],
                "home_feat": hf,
                "away_feat": af,
            })

            # 赛后再更新积分（保证时间安全）
            hg, ag = m["home_goals"], m["away_goals"]
            if hg > ag:
                points[home] += 3
            elif hg < ag:
                points[away] += 3
            else:
                points[home] += 1
                points[away] += 1
            goal_diff[home] += hg - ag
            goal_diff[away] += ag - hg
            played[home] += 1
            played[away] += 1
            team_dates[home].append(m["date"])
            team_dates[away].append(m["date"])

    return out


def load_lambdas(db_path):
    """从 model_predictions 读取 T-006 v5 的 λ_home / λ_away。"""
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    cur.execute(
        "SELECT match_id, prediction_type, probability FROM model_predictions "
        "WHERE model_name='t006_score_predictor_v5' AND prediction_type IN ('Lambda_home','Lambda_away')"
    )
    lh, la = defaultdict(lambda: None), defaultdict(lambda: None)
    for mid, ptype, prob in cur.fetchall():
        if ptype == "Lambda_home":
            lh[mid] = float(prob)
        else:
            la[mid] = float(prob)
    conn.close()
    return {mid: (lh[mid], la[mid]) for mid in lh if lh[mid] is not None and la[mid] is not None}


def run_ablation(features, lambdas):
    """对比开启/关闭战意系数下的总进球预测误差。"""
    base_err, adj_err = [], []
    rows = []
    for f in features:
        lam = lambdas.get(f["match_id"])
        if lam is None:
            continue
        lh, la = lam
        base_total = lh + la

        # 战意修正系数
        h_atk, h_def = team_motivation_factors(f["home_feat"])
        a_atk, a_def = team_motivation_factors(f["away_feat"])
        adj_lh = lh * h_atk * a_def   # 主队 λ = 主队进攻 × 客队防守变弱放大
        adj_la = la * a_atk * h_def
        adj_total = adj_lh + adj_la

        actual = f["total_goals"]
        base_err.append(base_total - actual)
        adj_err.append(adj_total - actual)
        rows.append({
            "match_id": f["match_id"],
            "home": f["home"],
            "away": f["away"],
            "home_incentive": f["home_feat"]["incentive"],
            "away_incentive": f["away_feat"]["incentive"],
            "home_rest": f["home_feat"]["rest_days"],
            "away_rest": f["away_feat"]["rest_days"],
            "base_lambda_total": round(base_total, 4),
            "adj_lambda_total": round(adj_total, 4),
            "actual_total": actual,
        })

    def _metrics(errs):
        e = np.array(errs)
        return {
            "n": int(len(e)),
            "mae": float(np.mean(np.abs(e))) if len(e) else float("nan"),
            "rmse": float(np.sqrt(np.mean(e ** 2))) if len(e) else float("nan"),
            "bias": float(np.mean(e)) if len(e) else float("nan"),
        }

    return {
        "base": _metrics(base_err),
        "adjusted": _metrics(adj_err),
        "mae_delta": _metrics(adj_err)["mae"] - _metrics(base_err)["mae"],
        "rmse_delta": _metrics(adj_err)["rmse"] - _metrics(base_err)["rmse"],
        "samples": rows,
    }


def generate_report(rep, md_path=None, json_path=None):
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)
    md_path = md_path or (REPORT_DIR / f"motivation_adjustment_{ts}.md")
    json_path = json_path or (REPORT_DIR / f"motivation_adjustment_{ts}.json")

    b = rep["base"]
    a = rep["adjusted"]
    L = []
    L.append("# 战意量化修正系数消融报告（P2-01）\n")
    L.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    L.append("> 指标为「λ 总进球预测 − 实际总进球」误差；MAE/RMSE 越小越好。\n")
    L.append("## 一、λ 修正规则（战意特征 → 攻/防乘法系数）\n")
    L.append("| 规则 | 触发条件 | 进攻系数 | 防守系数 |")
    L.append("|---|---|---|---|")
    for name, r in MOTIVATION_RULES.items():
        L.append(f"| {name} | {r['cond'].__doc__ or ''} | ×{r['attack']:.2f} | ×{r['defense']:.2f} |")
    L.append("")
    L.append("## 二、消融结果（开启 vs 关闭战意系数）\n")
    L.append("| 指标 | 关闭（基线） | 开启（战意） | 差值 |")
    L.append("|---|---|---|---|")
    L.append(f"| 样本量 | {b['n']} | {a['n']} | — |")
    L.append(f"| MAE | {b['mae']:.4f} | {a['mae']:.4f} | {rep['mae_delta']:+.4f} |")
    L.append(f"| RMSE | {b['rmse']:.4f} | {a['rmse']:.4f} | {rep['rmse_delta']:+.4f} |")
    L.append(f"| 偏差(bias) | {b['bias']:+.4f} | {a['bias']:+.4f} | — |")
    L.append("")

    # 分动机阵营统计（开启后的误差改善）
    L.append("## 三、样例（前 50）\n")
    L.append("| match_id | 对阵 | 主队动机/休息 | 客队动机/休息 | 基λ总 | 战意λ总 | 实际总 |")
    L.append("|---|---|---|---|---|---|---|")
    for s in rep["samples"][:50]:
        L.append(f"| {s['match_id']} | {s['home']} vs {s['away']} | "
                 f"{s['home_incentive']}/{s['home_rest']}d | {s['away_incentive']}/{s['away_rest']}d | "
                 f"{s['base_lambda_total']} | {s['adj_lambda_total']} | {s['actual_total']} |")
    L.append("\n---\n*本报告由 motivation_adjustment.py 自动生成*\n")

    md_path.write_text("\n".join(L), encoding="utf-8")
    json_path.write_text(json.dumps(rep, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="战意量化修正系数（P2-01）")
    parser.add_argument("--db", default=str(DB_PATH))
    args = parser.parse_args()

    print("加载历史赛果...")
    matches = load_matches(Path(args.db))
    print(f"  可解析比分场次: {len(matches)}")

    print("构建战意特征集...")
    features = build_features(matches)
    print(f"  战意特征样本: {len(features)}")

    print("加载 T-006 λ...")
    lambdas = load_lambdas(Path(args.db))
    print(f"  有 λ 的场次: {len(lambdas)}")

    print("运行消融（开启 vs 关闭战意系数）...")
    rep = run_ablation(features, lambdas)
    print("\n" + "=" * 60)
    print(f"基线(关闭) MAE={rep['base']['mae']:.4f} RMSE={rep['base']['rmse']:.4f}")
    print(f"战意(开启) MAE={rep['adjusted']['mae']:.4f} RMSE={rep['adjusted']['rmse']:.4f}")
    print(f"MAE 增量 {rep['mae_delta']:+.4f} | RMSE 增量 {rep['rmse_delta']:+.4f}")
    print("=" * 60)

    md_path, json_path = generate_report(rep)
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")


if __name__ == "__main__":
    main()