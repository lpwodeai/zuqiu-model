# -*- coding: utf-8 -*-
"""T-006 v5 离线复测（v5 接入三步法第 1 步，C-20260920-029）

在同一「有生产 Stacking WDL + 已赛 + WDL/TG/比分赔率齐全」子集上，
严格对比现生产 v4 与拟接入 v5 的比分分布质量。冻结口径（C-20260920-028）：

  v4（忠实复用生产算法，不重写）：
    λ raw = 3.6 × 去水WDL概率 clamp[0.3,3.5]
    → A-002 两阶段缩放（Stage1 WDL 0.5+1.5P clamp0.7~1.8；Stage2 TG反推E[g] clamp0.85~1.4）
    → Poisson+DC(ρ=-0.30) 85% + MC500 15% + 比分赔率 α=0.30 融合
    → C-019 单轮 ratio 重加权（锚定 Stacking 生产 WDL）

  v5（新口径）：
    total = TG 八档赔率去水后隐含 E[g]（唯一先验，不做 recency）
    → 二分法解 λ 强度差使 Poisson 胜负差对齐 Stacking WDL（绕过 A-002）
    → 联赛 ρ 的 DC 网格（英超-0.08/西甲-012/意甲-0.15/德甲-0.05/法甲-0.10）
    → 与比分赔率 α=0.30 融合（保留市场信号，弃用 MC）
    → 嵌套一轮 IPF(30 迭代) 把 WDL 三边缘精确修回 Stacking 概率

评测：严格时序（match_date 升序，TimeSeriesSplit 5 折），逐折 + 总体输出
Top1/Top3/Top5 精确命中、1球内/2球内、WDL 方向一致率，并做逐场配对分析。

用法：
  python scripts/t006_v5_offline_retest.py            # 全量
  python scripts/t006_v5_offline_retest.py --limit 8  # 小样本试跑
产物：reports/t006_v5_offline_retest.json
"""
import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import numpy as np
from sklearn.model_selection import TimeSeriesSplit

from feature_utils import build_match_alignment
from prediction_core import (
    CalcEngine,
    T006_RHO,
    T006_MC_SIMULATIONS,
    T006_POISSON_WEIGHT,
    T006_MC_WEIGHT,
    T006_SCORE_ODDS_ALPHA,
)
from t006_score_predictor_v5 import (
    LEAGUE_RHO as V5_LEAGUE_RHO,
    predict_score_v5,
)
from t006_score_predictor import parse_score

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"


# ------------------------------------------------------------
# 数据加载
# ------------------------------------------------------------
def load_prod_wdl(conn):
    """matches match_id -> [home, draw, away] 生产 Stacking WDL（三向齐全）。"""
    rows = conn.execute(
        "SELECT match_id, prediction_type, probability FROM model_predictions "
        "WHERE prediction_type IN ('WDL_home','WDL_draw','WDL_away')"
    ).fetchall()
    tmp = {}
    for mid, ptype, prob in rows:
        if prob is None:
            continue
        tmp.setdefault(mid, {})[ptype] = float(prob)
    return {mid: [d["WDL_home"], d["WDL_draw"], d["WDL_away"]]
            for mid, d in tmp.items() if len(d) == 3}


def load_histories(conn, hkey):
    """按赔率历史键直查三张时序表（近期键为中文短名，matches 无同键行，不能走 load_odds_from_db）。"""
    wdl = conn.execute(
        "SELECT win_a,draw,win_b FROM wdl_history WHERE match_id=? "
        "ORDER BY timestamp DESC LIMIT 1", (hkey,)).fetchone()
    tg = conn.execute(
        "SELECT goals_0,goals_1,goals_2,goals_3,goals_4,goals_5,goals_6,goals_7_plus "
        "FROM total_goals_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1",
        (hkey,)).fetchone()
    # 比分赔率：取最新时间点快照
    last_ts = conn.execute(
        "SELECT MAX(timestamp) FROM score_history WHERE match_id=?", (hkey,)).fetchone()[0]
    score_rows = conn.execute(
        "SELECT score,odds FROM score_history WHERE match_id=? AND timestamp=?",
        (hkey, last_ts)).fetchall() if last_ts is not None else []
    return wdl, tg, score_rows


def build_odds_data(wdl, tg, score_rows):
    """组装 CalcEngine 所需最小 odds_data（结构与生产 load_odds_from_db 产物一致）。"""
    wrec = {"win": float(wdl[0]), "draw": float(wdl[1]), "lose": float(wdl[2])}
    goals = {lab: float(v) for lab, v in zip(
        ["0", "1", "2", "3", "4", "5", "6", "7+"], tg) if v is not None}
    win_odds, draw_odds, lose_odds = {}, {}, {}
    for sc, od in score_rows:
        if od is None or ":" not in str(sc):
            continue
        h, a = parse_score(sc)
        if h is None:
            continue
        bucket = win_odds if h > a else (draw_odds if h == a else lose_odds)
        bucket[sc] = float(od)
    return {
        "wdl_odds": {"records": [wrec], "close": wrec},
        "tg_odds": {"records": [{"goals": goals}]},
        "score_odds": {"records": [
            {"win_odds": win_odds, "draw_odds": draw_odds, "lose_odds": lose_odds}]},
    }


# ------------------------------------------------------------
# v4（生产路径复刻）
# ------------------------------------------------------------
def predict_v4(odds_data, stack_wdl):
    np.random.seed(42)
    raw_h, raw_a = CalcEngine.calc_lambda_from_odds(odds_data)
    lh, la, _ = CalcEngine.adjust_lambda_for_mid_score(raw_h, raw_a, odds_data)
    np.random.seed(42)
    poisson_probs = CalcEngine.poisson_score_predict(lh, la, max_goals=7, rho=T006_RHO)
    mc_probs = CalcEngine.monte_carlo_score_predict(
        lh, la, n_sim=T006_MC_SIMULATIONS, max_goals=7)
    score_dict = {}
    so = odds_data["score_odds"]["records"][-1]
    for d in (so["win_odds"], so["draw_odds"], so["lose_odds"]):
        score_dict.update(d)
    fused = CalcEngine.fuse_score_predictions(
        poisson_probs, mc_probs, score_dict,
        T006_POISSON_WEIGHT, T006_MC_WEIGHT, T006_SCORE_ODDS_ALPHA)

    # C-019 单轮 ratio 重加权（锚定 Stacking）
    ph, pd_, pa = stack_wdl
    marg = {"win": 0.0, "draw": 0.0, "lose": 0.0}
    for sc, pr in fused.items():
        h, a = parse_score(sc)
        key = "win" if h > a else ("draw" if h == a else "lose")
        marg[key] += pr
    eps = 1e-6
    ratios = {"win": ph / max(marg["win"], eps),
              "draw": pd_ / max(marg["draw"], eps),
              "lose": pa / max(marg["lose"], eps)}
    out = {}
    for sc, pr in fused.items():
        h, a = parse_score(sc)
        key = "win" if h > a else ("draw" if h == a else "lose")
        out[sc] = pr * ratios[key]
    s = sum(out.values())
    return {k: v / s for k, v in out.items()} if s > 0 else out


# ------------------------------------------------------------
# v5（薄封装：直接调用 serving 层，保证复测与生产 Shadow 同一份算法）
# ------------------------------------------------------------
def predict_v5(odds_data, stack_wdl, league):
    res = predict_score_v5(
        odds_data,
        {"win": stack_wdl[0], "draw": stack_wdl[1], "lose": stack_wdl[2]},
        league)
    return res["dist"], res["lambdas"]


# ------------------------------------------------------------
# 评测
# ------------------------------------------------------------
def evaluate(dist, ah, aa):
    ss = sorted(dist.items(), key=lambda x: x[1], reverse=True)
    actual_key = f"{ah}:{aa}"
    top1 = ss[0][0] == actual_key
    top3 = actual_key in {k for k, _ in ss[:3]}
    top5 = actual_key in {k for k, _ in ss[:5]}
    w1 = w2 = 0
    for k, _ in ss[:5]:
        h, a = parse_score(k)
        d = abs(h - ah) + abs(a - aa)
        w1 |= d <= 1
        w2 |= d <= 2
    marg_h = sum(pr for k, pr in dist.items() if parse_score(k)[0] > parse_score(k)[1])
    marg_a = sum(pr for k, pr in dist.items() if parse_score(k)[1] > parse_score(k)[0])
    pred_dir = 0 if marg_h >= marg_a else 2
    actual_dir = 0 if ah > aa else (2 if aa > ah else 1)
    return {"top1": top1, "top3": top3, "top5": top5,
            "w1": bool(w1), "w2": bool(w2),
            "wdl_dir": pred_dir, "actual_dir": actual_dir,
            "top1_prob": ss[0][1], "top5_mass": sum(p for _, p in ss[:5])}


def rate(evs, field):
    return round(100.0 * sum(e[field] for e in evs) / len(evs), 2) if evs else None


def summarize(evs):
    non_draw = [e for e in evs if e["actual_dir"] != 1]
    return {
        "n": len(evs),
        "top1": rate(evs, "top1"),
        "top3": rate(evs, "top3"),
        "top5": rate(evs, "top5"),
        "within1": rate(evs, "w1"),
        "within2": rate(evs, "w2"),
        "wdl_acc": round(100.0 * sum(e["wdl_dir"] == e["actual_dir"] for e in non_draw)
                         / len(non_draw), 2) if non_draw else None,
        "wdl_n": len(non_draw),
        "avg_top1_prob": round(float(np.mean([e["top1_prob"] for e in evs])), 4),
        "avg_top5_mass": round(float(np.mean([e["top5_mass"] for e in evs])), 4),
    }


# ------------------------------------------------------------
# 主流程
# ------------------------------------------------------------
def run(limit=None):
    conn = sqlite3.connect(str(DB_PATH))
    prod_wdl = load_prod_wdl(conn)

    meta_rows = conn.execute(
        "SELECT match_id, match_date, home_team, away_team, actual_score, match_type "
        "FROM matches WHERE actual_score IS NOT NULL AND actual_score != ''").fetchall()

    print("[对齐] wdl_history → matches ...")
    align = build_match_alignment(conn, "wdl_history")
    rev = {}
    for hk, mk in align.items():
        rev.setdefault(mk, hk)  # 一对一时首个历史键

    records = []
    skipped = 0
    for mid, mdate, home, away, actual, mtype in meta_rows:
        if mid not in prod_wdl:
            continue
        hkey = rev.get(mid)
        if not hkey:
            skipped += 1
            continue
        wdl, tg, score_rows = load_histories(conn, hkey)
        if not (wdl and tg and score_rows):
            skipped += 1
            continue
        ah, aa = parse_score(actual)
        if ah is None:
            skipped += 1
            continue
        league = (mtype or "")[:2]
        if league not in V5_LEAGUE_RHO:
            skipped += 1
            continue
        records.append({
            "match_id": mid, "date": mdate, "league": league,
            "ah": ah, "aa": aa,
            "odds_data": build_odds_data(wdl, tg, score_rows),
            "stack_wdl": prod_wdl[mid],
        })
    conn.close()

    records.sort(key=lambda r: (r["date"] or "", r["match_id"]))
    if limit:
        records = records[:limit]
    print(f"[评估集] n={len(records)}（跳过 {skipped} 场）")

    v4_evs, v5_evs, per_match = [], [], []
    for r in records:
        d4 = predict_v4(r["odds_data"], r["stack_wdl"])
        d5, (lh5, la5) = predict_v5(r["odds_data"], r["stack_wdl"], r["league"])
        e4, e5 = evaluate(d4, r["ah"], r["aa"]), evaluate(d5, r["ah"], r["aa"])
        v4_evs.append(e4)
        v5_evs.append(e5)
        per_match.append({
            "match_id": r["match_id"], "date": r["date"], "league": r["league"],
            "actual": f"{r['ah']}:{r['aa']}",
            "v4_top5": e4["top5"], "v5_top5": e5["top5"],
            "v5_lambda": [round(lh5, 3), round(la5, 3)],
        })

    # 严格时序 5 折
    folds = []
    n = len(records)
    if n >= 6:
        tscv = TimeSeriesSplit(n_splits=min(5, n - 1))
        for i, (_, te_idx) in enumerate(tscv.split(range(n)), 1):
            folds.append({
                "fold": i,
                "n": len(te_idx),
                "v4": summarize([v4_evs[j] for j in te_idx]),
                "v5": summarize([v5_evs[j] for j in te_idx]),
            })

    # 逐场配对（Top5 精确命中口径）
    paired = {"both": 0, "v4_only": 0, "v5_only": 0, "neither": 0}
    for e4, e5 in zip(v4_evs, v5_evs):
        if e4["top5"] and e5["top5"]:
            paired["both"] += 1
        elif e4["top5"]:
            paired["v4_only"] += 1
        elif e5["top5"]:
            paired["v5_only"] += 1
        else:
            paired["neither"] += 1

    s4, s5 = summarize(v4_evs), summarize(v5_evs)
    result = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "n": n,
        "date_range": [records[0]["date"], records[-1]["date"]] if records else None,
        "overall": {"v4": s4, "v5": s5,
                    "delta": {k: round(s5[k] - s4[k], 2) for k in
                              ("top1", "top3", "top5", "within1", "within2", "wdl_acc")
                              if s5.get(k) is not None and s4.get(k) is not None}},
        "paired_top5": paired,
        "folds": folds,
        "config": {
            "league_rho": V5_LEAGUE_RHO,
            "score_odds_alpha": T006_SCORE_ODDS_ALPHA,
            "v4_poisson_weight": T006_POISSON_WEIGHT,
            "v4_mc_weight": T006_MC_WEIGHT,
            "v4_rho": T006_RHO,
            "v4_mc_sim": T006_MC_SIMULATIONS,
        },
        "per_match": per_match,
    }

    REPORT_DIR.mkdir(exist_ok=True)
    out_path = REPORT_DIR / "t006_v5_offline_retest.json"
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")

    print("\n=== T-006 v5 离线复测（同子集 v4 vs v5） ===")
    print("v4:", json.dumps(s4, ensure_ascii=False))
    print("v5:", json.dumps(s5, ensure_ascii=False))
    print("Δ :", json.dumps(result["overall"]["delta"], ensure_ascii=False))
    print("配对(Top5):", paired)
    print("保存:", out_path)
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="只取时序最早 N 场试跑")
    args = parser.parse_args()
    run(limit=args.limit)
