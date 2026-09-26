"""TG 校准离线 A/B 评测脚本（C-20260921-036）

在历史配对样本上对比 A（未校准）vs B（λ_total 滚动缩放校准）的预测质量。
严格时间切片：校准因子 / 气候基线只使用目标场比赛之前的数据（禁止未来泄露）。

口径保真原则（唯一变量 = TotalGoalsPredictor 内部校准逻辑）：
  - λ 读 model_predictions 发布行（禁止现场重推，对齐 C-20260921-034）
  - Poisson 轨复刻 prediction_core.TotalGoalsPredictor.predict()：
    USE_UNIFIED_ENGINE=True 时走 DixonColesGenerator（默认 ρ=-0.30，与生产一致），
    否则 legacy CalcEngine；g==7 档合并边界与生产逐行一致
  - TG 赔率经生产同款 fuzzy 桥接 find_sporttery_match_id（wdl_history ±3 天）
  - 无 TG 赔率时生产输出 over25=0.0 占位 → 评测按脏样本剔除（计划 §4.2），
    同时单独复算含脏样本的 Brier，用于对齐发布口径 0.269 并做根因分解

CLI:
    python scripts/tg_calibration_shadow_eval.py [--league 英超] [--limit N] [--no-pooled]
"""
from __future__ import annotations

import argparse
import json
import math
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from tg_lambda_calibrator import (  # noqa: E402
    DEFAULT_CFG,
    apply_factor,
    get_calib_factor_hierarchical,
)
from prediction_core import CalcEngine, USE_UNIFIED_ENGINE, get_unified_engine  # noqa: E402
from generate_unified_report import find_sporttery_match_id  # noqa: E402

DB_PATH = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"

# 生产 predict() 中的固定融合权重（不可在此调整，否则违反单一变量原则）
W_POISSON = 0.85
W_ODDS = 0.15


# ============================================================
# 数据加载
# ============================================================
def load_replay_samples(conn: sqlite3.Connection, league: Optional[str] = None) -> List[Dict]:
    """枚举所有可回放样本：Lambda_home/away + actual_tg + 发布 over25 三有。"""
    league_cond = "AND pmr.league = ?" if league else ""
    params: list = []
    if league:
        params.append(league)

    sql = f"""
        SELECT lh.match_id, pmr.match_date, pmr.league,
               pmr.home_team, pmr.away_team,
               CAST(lh.prediction AS REAL) AS lambda_home,
               CAST(la.prediction AS REAL) AS lambda_away,
               pmr.actual_tg,
               CAST(pub.probability AS REAL) AS published_over25
        FROM model_predictions lh
        JOIN model_predictions la
          ON lh.match_id = la.match_id
         AND lh.model_name = la.model_name
         AND la.prediction_type = 'Lambda_away'
        JOIN post_match_review pmr ON pmr.match_id = lh.match_id
        LEFT JOIN model_predictions pub
          ON pub.match_id = lh.match_id
         AND pub.model_name = lh.model_name
         AND pub.prediction_type = 'TG_over_2_5'
        WHERE lh.prediction_type = 'Lambda_home'
          AND lh.model_name = 'generate_unified_report_v2.0'
          AND pmr.actual_tg IS NOT NULL
          AND CAST(lh.prediction AS REAL) > 0
          AND CAST(la.prediction AS REAL) > 0
          {league_cond}
        ORDER BY pmr.match_date
    """
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def load_latest_tg_goals(conn: sqlite3.Connection,
                         home_cn: str, away_cn: str, match_date: str) -> Optional[Dict[str, float]]:
    """复刻生产赔率解析：fuzzy 定位竞彩 mid → 取最新一条 TG 快照。

    生产只在 find_sporttery_match_id（wdl_history）命中时才加载 TG 行；
    竞彩未开胜平负盘（find_any 命中但 wdl 无）时 TG records 为空。
    """
    mid = find_sporttery_match_id(conn, home_cn, away_cn, match_date)
    if not mid:
        return None
    row = conn.execute(
        "SELECT goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus "
        "FROM total_goals_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1",
        (mid,),
    ).fetchone()
    if not row:
        return None
    labels = ["0", "1", "2", "3", "4", "5", "6", "7+"]
    goals: Dict[str, float] = {}
    for i, k in enumerate(labels):
        v = row[i]
        if v is not None:
            goals[k] = float(v)
    return goals if goals else None


# ============================================================
# 重放（逐行复刻 TotalGoalsPredictor.predict() 主路径）
# ============================================================
def _poisson_total_goals(lambda_home: float, lambda_away: float) -> Dict[int, float]:
    """返回 8 档 Poisson/DC 总进球分布（0..7+），分支与生产 predict() 完全一致。"""
    dist: Dict[int, float] = {}
    if USE_UNIFIED_ENGINE:
        tg = get_unified_engine().generate(lambda_home, lambda_away, verbose=False)["total_goals"]
        for g in range(7):
            dist[g] = float(tg.get(g, 0.0))
        dist[7] = sum(float(tg.get(gg, 0.0)) for gg in range(7, 15))
    else:
        d = CalcEngine.calc_total_goals_from_lambda(lambda_home, lambda_away)["distribution"]
        for g in range(7):
            dist[g] = float(d.get(g, 0.0))
        # 生产 legacy 分支 g==7 只合并 7/8 两档（历史口径，保持一致）
        dist[7] = sum(float(d.get(gg, 0.0)) for gg in range(7, 9))
    return dist


def _fuse(tg_lambda_dist: Dict[int, float], goals: Dict[str, float]) -> Dict[int, float]:
    """0.85/0.15 融合 + 再归一化（生产 L1853-1871 逐行复刻）。"""
    odds_probs: Dict[int, float] = {}
    total_inv = sum(1.0 / max(v, 1e-10) for v in goals.values())
    for k, v in goals.items():
        key = int(k.replace("+", "")) if k.replace("+", "").isdigit() else 7
        odds_probs[key] = (1.0 / max(v, 1e-10)) / total_inv

    fused: Dict[int, float] = {}
    for g in range(8):
        p_poisson = tg_lambda_dist.get(g, 0.0)
        p_odds = odds_probs.get(g, 0.0)
        if g == 7 and not USE_UNIFIED_ENGINE:
            p_poisson = sum(tg_lambda_dist.get(gg, 0.0) for gg in range(7, 9))
        fused[g] = p_poisson * W_POISSON + p_odds * W_ODDS

    ft = sum(fused.values())
    if ft > 0:
        for g in fused:
            fused[g] /= ft
    return fused


def replay_one(sample: Dict, conn: sqlite3.Connection, cfg: Dict[str, Any]) -> Dict[str, Dict]:
    """单场双跑，返回 {"A": arm, "B": arm}。

    status:
      ok          — 有 λ 且有 TG 赔率，融合产出 8 档分布
      insufficient— 生产降级（无 TG 赔率），over25=0.0 占位脏样本
    """
    lh, la = sample["lambda_home"], sample["lambda_away"]
    date, league = sample["match_date"][:10], sample.get("league")

    factor, trace = get_calib_factor_hierarchical(date, league=league, cfg=cfg, conn=conn)
    factor = min(1.0, factor * cfg.get("factor_multiplier", 1.0))
    trace["factor_after_multiplier"] = round(factor, 4)
    goals = load_latest_tg_goals(conn, sample["home_team"], sample["away_team"], date)

    def arm(fl: float, fa: float, tag: str) -> Dict:
        base = {
            "match_id": sample["match_id"],
            "match_date": date,
            "league": league,
            "actual_tg": int(sample["actual_tg"]),
            "published_over25": sample["published_over25"],
            "lambda_home_raw": round(lh, 4),
            "lambda_away_raw": round(la, 4),
            "lambda_total_raw": round(lh + la, 4),
            "lambda_total_eff": round(fl + fa, 4),
        }
        if goals is None:
            # 生产 L1892-1900 / L1898-1900：无 TG 赔率 → 数据不足，over_25_prob=0.0
            return {**base, "status": "insufficient", "over25_prob": 0.0,
                    "dist_8": None, "factor": round(fa / la if la else 1.0, 6),
                    "factor_source": trace.get("source") if tag == "B" else None,
                    "trace": trace if tag == "B" else None}
        dist = _fuse(_poisson_total_goals(fl, fa), goals)
        return {**base, "status": "ok",
                "dist_8": {str(g): round(dist.get(g, 0.0), 6) for g in range(8)},
                "over25_prob": round(sum(dist.get(g, 0.0) for g in range(3, 8)), 6),
                "factor": round(fl / lh if lh else 1.0, 6),
                "factor_source": trace.get("source") if tag == "B" else None,
                "trace": trace if tag == "B" else None}

    clh, cla = apply_factor(lh, la, factor)
    return {"A": arm(lh, la, "A"), "B": arm(clh, cla, "B")}


# ============================================================
# 指标
# ============================================================
def rps_8bucket(dist: Dict[str, float], actual_tg: int) -> float:
    """Ranked Probability Score（8 档有序，归一化到 [0,1]，越低越好）。

    RPS = 1/(K-1) * Σ_{i=0..K-2} (CDF_pred(i) − CDF_actual(i))²
    CDF_actual 是阶跃：i < actual_idx 时为 0，i ≥ actual_idx 时为 1。
    """
    K = 8
    buckets = [dist.get(str(g), 0.0) for g in range(K)]
    actual_idx = min(actual_tg, 7)
    cp = 0.0
    rps = 0.0
    for i in range(K - 1):
        cp += buckets[i]
        ca = 1.0 if i >= actual_idx else 0.0
        rps += (cp - ca) ** 2
    return rps / (K - 1)


def brier_over25(p: float, actual_tg: int) -> float:
    return (p - (1.0 if actual_tg >= 3 else 0.0)) ** 2


def logloss_8bucket(dist: Dict[str, float], actual_tg: int) -> float:
    p = max(dist.get(str(min(actual_tg, 7)), 1e-10), 1e-10)
    return -math.log(p)


def ece_over25(rows: List[Dict], n_bins: int = 6) -> Tuple[float, List[Dict]]:
    bins_out = []
    for i in range(n_bins):
        lo = i / n_bins
        hi = (i + 1) / n_bins + (1e-9 if i == n_bins - 1 else 0)
        members = [m for m in rows if lo <= m["over25_prob"] < hi]
        if not members:
            continue
        avg_pred = sum(m["over25_prob"] for m in members) / len(members)
        rate = sum(1 for m in members if m["actual_tg"] >= 3) / len(members)
        bins_out.append({"range": f"[{lo:.2f},{hi:.2f})", "n": len(members),
                         "avg_pred": round(avg_pred, 3), "actual_rate": round(rate, 3),
                         "gap": round(avg_pred - rate, 3)})
    tot = sum(b["n"] for b in bins_out)
    ece = sum(abs(b["gap"]) * b["n"] for b in bins_out) / tot if tot else 0.0
    return ece, bins_out


def paired_t_test(deltas: List[float]) -> Tuple[float, float, float]:
    """配对 t 检验，返回 (t, df, p_two_sided)。优先 scipy，缺失时用正态近似。"""
    n = len(deltas)
    if n < 2:
        return 0.0, float(n - 1), 1.0
    mean = sum(deltas) / n
    var = sum((x - mean) ** 2 for x in deltas) / (n - 1)
    sd = math.sqrt(var)
    if sd == 0:
        return 0.0, float(n - 1), 1.0
    t = mean / (sd / math.sqrt(n))
    df = n - 1
    try:
        from scipy import stats  # type: ignore
        p = 2.0 * stats.t.sf(abs(t), df)
    except Exception:
        # 大样本正态近似（n>100 时与 t 分布差异 <0.002）
        p = 2.0 * (1.0 - 0.5 * (1.0 + math.erf(abs(t) / math.sqrt(2.0))))
    return t, float(df), p


def aggregate(rows: List[Dict], climatology: Optional[Dict[str, Dict[str, float]]] = None) -> Dict:
    ok = [m for m in rows if m["status"] == "ok"]
    n = len(ok)
    if n == 0:
        return {"n": 0}

    rps_v = [rps_8bucket(m["dist_8"], m["actual_tg"]) for m in ok]
    bri_v = [brier_over25(m["over25_prob"], m["actual_tg"]) for m in ok]
    ll_v = [logloss_8bucket(m["dist_8"], m["actual_tg"]) for m in ok]

    # RPSS：逐场气候基线 RPS
    rps_clim = []
    if climatology:
        for m in ok:
            clim = climatology.get(m["match_id"])
            if clim:
                rps_clim.append(rps_8bucket(clim, m["actual_tg"]))
    rpss = (1.0 - (sum(rps_v) / sum(rps_clim))) if rps_clim and sum(rps_clim) > 0 else None

    top1 = top3 = over_correct = 0
    for m in ok:
        items = sorted(m["dist_8"].items(), key=lambda x: (-x[1], int(x[0])))
        actual = min(m["actual_tg"], 7)
        if int(items[0][0]) == actual:
            top1 += 1
        if actual in [int(x[0]) for x in items[:3]]:
            top3 += 1
        if (m["over25_prob"] >= 0.5) == (m["actual_tg"] >= 3):
            over_correct += 1

    ece, bins = ece_over25(ok)
    # 高预测档（>=0.8）系统性 gap —— 计划门禁的核心观察桶
    hi = [m for m in ok if m["over25_prob"] >= 0.8]
    hi_gap = round((sum(m["over25_prob"] for m in hi) / len(hi)
                    - sum(1 for m in hi if m["actual_tg"] >= 3) / len(hi)), 4) if hi else None

    return {
        "n": n,
        "n_insufficient": len(rows) - n,
        "rps_mean": round(sum(rps_v) / n, 4),
        "rps_sum": round(sum(rps_v), 4),
        "rps_clim_mean": round(sum(rps_clim) / len(rps_clim), 4) if rps_clim else None,
        "rpss": round(rpss, 4) if rpss is not None else None,
        "brier_mean": round(sum(bri_v) / n, 4),
        "brier_const050": 0.25,
        "logloss_mean": round(sum(ll_v) / n, 4),
        "ece": round(ece, 4),
        "bucket_08_gap": hi_gap,
        "over25_acc": round(over_correct / n, 4),
        "top1_acc": round(top1 / n, 4),
        "top3_acc": round(top3 / n, 4),
        "ece_bins": bins,
        "_rps_per_match": rps_v,
        "_brier_per_match": bri_v,
        "_rows": ok,
    }


def build_climatology(samples: List[Dict]) -> Dict[str, Dict[str, float]]:
    """逐场时间严格气候基线：同赛季、日期严格更早样本的实际 8 档经验分布。

    样本池包含 insufficient 场次（实际进球有效，气候基线只看赛果）。
    无历史样本时退化为均匀分布。
    """
    ordered = sorted(samples, key=lambda s: s["match_date"][:10])
    counts: Dict[str, List[int]] = defaultdict(lambda: [0] * 8)
    totals: Dict[str, int] = defaultdict(int)
    by_date: Dict[str, List[int]] = defaultdict(list)
    for i, s in enumerate(ordered):
        by_date[s["match_date"][:10]].append(i)

    clim: Dict[str, Dict[str, float]] = {}
    # 每个比赛日之前，累计所有联赛的赛果
    for d in sorted(by_date):
        for i in by_date[d]:
            s = ordered[i]
            dist = ({str(g): counts["all"][g] / totals["all"] for g in range(8)}
                    if totals["all"] > 0 else {str(g): 1 / 8 for g in range(8)})
            clim[s["match_id"]] = dist
        for i in by_date[d]:
            b = min(int(ordered[i]["actual_tg"]), 7)
            counts["all"][b] += 1
            totals["all"] += 1
    return clim


# ============================================================
# 报告
# ============================================================
def _metrics_row(label: str, a: float, b: float, lower_better: bool) -> str:
    delta = b - a
    if lower_better:
        tag = "↓好" if delta < -1e-9 else ("↑差" if delta > 1e-9 else "持平")
    else:
        tag = "↑好" if delta > 1e-9 else ("↓差" if delta < -1e-9 else "持平")
    return f"| {label} | {a} | {b} | {delta:+.4f} {tag} |\n"


def write_report(met_a: Dict, met_b: Dict, pairs: List[Dict],
                 fidelity: Dict, gates: List[Dict], ts: str,
                 cfg: Dict[str, Any],
                 common: Optional[Tuple[Dict, Dict, int, int]] = None,
                 treated_tests: Optional[Tuple[List[float], List[float]]] = None) -> Tuple[str, str]:
    json_path = REPORT_DIR / f"tg_calib_shadow_{ts}.json"
    md_path = REPORT_DIR / f"tg_calib_shadow_{ts}.md"

    per_match = []
    for pr in pairs:
        a, b = pr["A"], pr["B"]
        per_match.append({
            "match_id": a["match_id"], "match_date": a["match_date"], "league": a["league"],
            "actual_tg": a["actual_tg"], "status": a["status"],
            "published_over25": a["published_over25"],
            "control_over25": a["over25_prob"], "treatment_over25": b["over25_prob"],
            "lambda_total_raw": a["lambda_total_raw"],
            "factor": b["factor"], "factor_source": b["factor_source"],
            "trace": b["trace"],
            "control_dist_8": a["dist_8"], "treatment_dist_8": b["dist_8"],
        })
    json_path.write_text(json.dumps({
        "timestamp": ts, "cfg": {k: v for k, v in cfg.items()},
        "use_unified_engine": USE_UNIFIED_ENGINE,
        "fidelity": fidelity,
        "control": {k: v for k, v in met_a.items() if not k.startswith("_")},
        "treatment": {k: v for k, v in met_b.items() if not k.startswith("_")},
        "per_match": per_match,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    s = "# TG 校准 Shadow A/B 评测报告\n\n"
    s += f"> 生成时间: {ts} | 引擎: unified_engine={USE_UNIFIED_ENGINE}\n"
    s += f"> 配置: window={cfg['window_days']}d, min_samples={cfg['min_samples']}, "
    s += f"clamp=[{cfg['factor_min']},{cfg['factor_max']}], pooled_fallback={cfg.get('pooled_fallback', True)}, "
    s += f"factor_multiplier={cfg.get('factor_multiplier', 1.0)}\n\n"

    # 1 样本概况
    n_tot = met_a["n"] + met_a["n_insufficient"]
    # 数据可得性分组（赔率库在报告生成后存在补采，发布快照与当前库不完全一致）
    common_pairs = [p for p in pairs if p["A"]["status"] == "ok"
                    and (p["A"]["published_over25"] or 0.0) > 0.01]
    recovered = [p for p in pairs if p["A"]["status"] == "ok"
                 and (p["A"]["published_over25"] or 0.0) <= 0.01]
    unreplayable = [p for p in pairs if p["A"]["status"] == "insufficient"
                    and (p["A"]["published_over25"] or 0.0) > 0.01]
    s += "## 1. 样本概况\n\n"
    s += f"- 配对样本总数：**{n_tot}**（发布 Lambda + actual_tg）\n"
    s += f"- 共同干净样本（重放有赔率 & 发布行非 0.0）：**{len(common_pairs)}** 场\n"
    s += f"- 补采恢复样本（发布时 0.0 占位，当前赔率库已有赔率）：**{len(recovered)}** 场\n"
    s += f"- 不可回放（发布时有值，当前库无赔率）：**{len(unreplayable)}** 场，剔除\n"
    s += f"- 发布即脏且当前仍无赔率：**{met_a['n_insufficient'] - len(unreplayable)}** 场，剔除\n"
    s += f"- 进入主评分样本：**{met_a['n']}** 场（共同干净 + 补采恢复，A/B 同集）\n"
    src_count = defaultdict(int)
    for pr in pairs:
        if pr["B"]["status"] == "ok":
            src_count[pr["B"]["factor_source"]] += 1
    s += f"- B 组因子来源分布：{dict(src_count)}\n\n"

    # 2 口径保真
    s += "## 2. A 臂口径保真校验（control 重放 vs 发布行）\n\n"
    s += "| 检查项 | 数值 | 判定 |\n|---|---:|:---:|\n"
    for k, v in fidelity.items():
        s += f"| {k} | {v['value']} | {v['judge']} |\n"
    s += "\n"

    # 3 全集指标
    s += "## 3. 全集指标对比（干净样本）\n\n"
    s += "| 指标 | A 组（未校准） | B 组（λ 缩放） | 变化 |\n|---|:---:|:---:|:---:|\n"
    s += _metrics_row("RPS（主指标，↓好）", met_a["rps_mean"], met_b["rps_mean"], True)
    s += f"| RPSS（vs 时间严格气候基线） | {met_a['rpss']} | {met_b['rpss']} | — |\n"
    s += f"| 气候基线 RPS | {met_a['rps_clim_mean']} | {met_b['rps_clim_mean']} | — |\n"
    s += _metrics_row("Brier（↓好，常量基准 0.250）", met_a["brier_mean"], met_b["brier_mean"], True)
    s += _metrics_row("LogLoss（↓好）", met_a["logloss_mean"], met_b["logloss_mean"], True)
    s += _metrics_row("ECE（↓好）", met_a["ece"], met_b["ece"], True)
    s += f"| ≥0.8 高预测档 gap | {met_a['bucket_08_gap']} | {met_b['bucket_08_gap']} | — |\n"
    s += _metrics_row("大小球准确率", met_a["over25_acc"], met_b["over25_acc"], False)
    s += _metrics_row("Top1 命中率", met_a["top1_acc"], met_b["top1_acc"], False)
    s += _metrics_row("Top3 覆盖率", met_a["top3_acc"], met_b["top3_acc"], False)
    s += "\n"

    # 3b 共同干净样本子集（剔除补采恢复样本后的最严格口径）
    if common:
        ca_m, cb_m, n_rec, n_unr = common
        s += f"### 3.1 共同干净样本子集（n={ca_m['n']}，剔除 {n_rec} 场补采恢复 / {n_unr} 场不可回放）\n\n"
        s += "| 指标 | A | B | 变化 |\n|---|:---:|:---:|:---:|\n"
        s += _metrics_row("RPS", ca_m["rps_mean"], cb_m["rps_mean"], True)
        s += _metrics_row("Brier", ca_m["brier_mean"], cb_m["brier_mean"], True)
        s += _metrics_row("ECE", ca_m["ece"], cb_m["ece"], True)
        s += _metrics_row("大小球准确率", ca_m["over25_acc"], cb_m["over25_acc"], False)
        s += _metrics_row("Top3 覆盖率", ca_m["top3_acc"], cb_m["top3_acc"], False)
        s += "\n"

    # 4 配对显著性
    s += "## 4. 配对统计检验（B−A）\n\n"
    s += "| 检验 | 均值Δ | t | df | p（双侧） |\n|---|---:|---:|---:|---:|\n"
    test_rows = [("RPS Δ 全集（负=B 好）", gates[0]["deltas"]),
                 ("Brier Δ 全集（负=B 好）", gates[1]["deltas"])]
    if treated_tests:
        test_rows += [("RPS Δ treated 子集", treated_tests[0]),
                      ("Brier Δ treated 子集", treated_tests[1])]
    for name, deltas in test_rows:
        if deltas:
            t, df, p = paired_t_test(deltas)
            s += f"| {name} | {sum(deltas)/len(deltas):+.5f} | {t:+.3f} | {df:.0f} | {p:.4f} |\n"
    s += "\n"

    # 5 分层
    s += "## 5. 分层对比\n\n"
    treated = [pr for pr in pairs if pr["B"]["status"] == "ok" and pr["B"]["factor"] < 0.9999]
    untreated = [pr for pr in pairs if pr["B"]["status"] == "ok" and pr["B"]["factor"] >= 0.9999]
    s += f"### 5.1 按因子激活（treated={len(treated)} / untreated={len(untreated)}）\n\n"
    s += "| 子集 | 臂 | n | RPS | Brier | ECE | 准确率 |\n|---|---|---:|---:|---:|---:|---:|\n"
    for name, sub in [("treated", treated), ("untreated", untreated)]:
        ma = aggregate([p["A"] for p in sub])
        mb = aggregate([p["B"] for p in sub])
        if ma["n"]:
            s += f"| {name} | A | {ma['n']} | {ma['rps_mean']} | {ma['brier_mean']} | {ma['ece']} | {ma['over25_acc']} |\n"
            s += f"| {name} | B | {mb['n']} | {mb['rps_mean']} | {mb['brier_mean']} | {mb['ece']} | {mb['over25_acc']} |\n"

    s += "\n### 5.2 按 λ_total_raw 分桶（B 组）\n\n"
    s += "| λ 区间 | n | λ 均值 | actual 均值 | B over25 pred | actual 大球率 | gap |\n|---|---:|---:|---:|---:|---:|---:|\n"
    okb = [pr["B"] for pr in pairs if pr["B"]["status"] == "ok"]
    for lo, hi in [(1.5, 2.5), (2.5, 3.0), (3.0, 3.5), (3.5, 4.0), (4.0, 6.0)]:
        sel = [m for m in okb if lo <= m["lambda_total_raw"] < hi]
        if len(sel) >= 3:
            ml = sum(m["lambda_total_raw"] for m in sel) / len(sel)
            ma = sum(m["actual_tg"] for m in sel) / len(sel)
            mo = sum(m["over25_prob"] for m in sel) / len(sel)
            oa = sum(1 for m in sel if m["actual_tg"] >= 3) / len(sel)
            s += f"| [{lo},{hi}) | {len(sel)} | {ml:.2f} | {ma:.2f} | {mo:.3f} | {oa:.3f} | {mo-oa:+.3f} |\n"

    s += "\n### 5.3 over25 校准分桶\n\n"
    s += "| 区间 | A n | A pred | A actual | A gap | B n | B pred | B actual | B gap |\n|---|---:|---:|---:|---:|---:|---:|---:|---:|\n"
    bm_a = {b["range"]: b for b in met_a["ece_bins"]}
    bm_b = {b["range"]: b for b in met_b["ece_bins"]}
    for rng in sorted(set(bm_a) | set(bm_b)):
        x, y = bm_a.get(rng, {}), bm_b.get(rng, {})
        s += (f"| {rng} | {x.get('n','—')} | {x.get('avg_pred','—')} "
              f"| {x.get('actual_rate','—')} | {x.get('gap','—')} | {y.get('n','—')} "
              f"| {y.get('avg_pred','—')} | {y.get('actual_rate','—')} | {y.get('gap','—')} |\n")

    # 6 门禁
    s += "\n## 6. 上线门禁判定（计划 §6）\n\n"
    s += "| # | 条件 | 结果 | 判定 |\n|---:|---|---|:---:|\n"
    for g in gates:
        s += f"| {g['id']} | {g['name']} | {g['detail']} | {'✅' if g['pass'] else '❌'} |\n"
    s += "\n## 7. 脏样本根因分解\n\n"
    all_a = [pr["A"] for pr in pairs]
    brier_all = sum(brier_over25(m["over25_prob"], m["actual_tg"]) for m in all_a) / len(all_a)
    # 发布行原始 Brier（完全按发布概率，含 0.0 占位与 2 场不可回放）
    pub_rows = [m for m in all_a if m["published_over25"] is not None]
    brier_pub = sum(brier_over25(m["published_over25"], m["actual_tg"])
                    for m in pub_rows) / len(pub_rows)
    s += f"- 发布行原始 Brier（{len(pub_rows)} 场，含 0.0 占位）：**{brier_pub:.4f}**（对齐实测 0.269）\n"
    s += f"- 重放 control 含当前库脏样本 Brier：**{brier_all:.4f}**\n"
    s += f"- 剔除脏样本后 control Brier：**{met_a['brier_mean']}**\n"
    s += f"- 0.0 占位写入守卫（P0 第 4 项）落地后，新预测不再产生此类污染\n"

    md_path.write_text(s, encoding="utf-8")
    return str(md_path), str(json_path)


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="TG 校准 Shadow A/B 评测")
    parser.add_argument("--league", default=None)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--no-pooled", action="store_true", help="关闭全局池化回退（纯联赛因子消融）")
    parser.add_argument("--factor-multiplier", type=float, default=1.0,
                        help="校准因子再乘系数（参数消融，默认 1.0；如 0.90 表示更强下调）")
    args = parser.parse_args()

    cfg = dict(DEFAULT_CFG)
    if args.no_pooled:
        cfg["pooled_fallback"] = False
    cfg["factor_multiplier"] = args.factor_multiplier

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    samples = load_replay_samples(conn, league=args.league)
    if args.limit:
        samples = samples[:args.limit]
    print(f"[加载] 配对样本 {len(samples)} 场 | unified_engine={USE_UNIFIED_ENGINE}")

    pairs = []
    for i, sample in enumerate(samples):
        pairs.append(replay_one(sample, conn, cfg))
        if (i + 1) % 50 == 0:
            print(f"  ...已双跑 {i + 1}/{len(samples)}")
    conn.close()

    # 口径保真校验（仅 ok 且发布行非 0.0 占位）
    devs = [abs(p["A"]["over25_prob"] - p["A"]["published_over25"])
            for p in pairs if p["A"]["status"] == "ok"
            and p["A"]["published_over25"] is not None and p["A"]["published_over25"] > 0.01]
    n_recovered = sum(1 for p in pairs if p["A"]["status"] == "ok"
                      and (p["A"]["published_over25"] or 0.0) <= 0.01)
    n_unreplayable = sum(1 for p in pairs if p["A"]["status"] == "insufficient"
                         and (p["A"]["published_over25"] or 0.0) > 0.01)
    fidelity = {
        "control vs 发布 over25 平均绝对偏差": {
            "value": f"{sum(devs)/len(devs):.4f}" if devs else "n/a",
            "judge": "✅ <0.03" if devs and sum(devs)/len(devs) < 0.03 else "❌ 口径失真"},
        "最大单场偏差": {"value": f"{max(devs):.4f}" if devs else "n/a",
                      "judge": "✅ <0.10" if devs and max(devs) < 0.10 else "❌"},
        "补采恢复样本（发布 0.0 / 现库有赔率）": {"value": str(n_recovered), "judge": "ℹ️ 数据可得性差异"},
        "不可回放样本（发布有值 / 现库无赔率）": {"value": str(n_unreplayable), "judge": "ℹ️ 数据可得性差异"},
    }

    clim = build_climatology(samples)
    rows_a = [p["A"] for p in pairs]
    rows_b = [p["B"] for p in pairs]
    met_a, met_b = aggregate(rows_a, clim), aggregate(rows_b, clim)

    ok_idx = [i for i, p in enumerate(pairs) if p["A"]["status"] == "ok"]
    rps_d = [met_b["_rps_per_match"][j] - met_a["_rps_per_match"][j]
             for j in range(len(ok_idx))]
    bri_d = [met_b["_brier_per_match"][j] - met_a["_brier_per_match"][j]
             for j in range(len(ok_idx))]
    _, _, p_rps = paired_t_test(rps_d)
    _, _, p_bri = paired_t_test(bri_d)

    treated = [p for p in pairs if p["B"]["status"] == "ok" and p["B"]["factor"] < 0.9999]
    t_a, t_b = aggregate([p["A"] for p in treated]), aggregate([p["B"] for p in treated])

    # 共同干净子集（发布行与重放状态一致且有值）
    common_pairs = [p for p in pairs if p["A"]["status"] == "ok"
                    and (p["A"]["published_over25"] or 0.0) > 0.01]
    recovered_n = sum(1 for p in pairs if p["A"]["status"] == "ok"
                      and (p["A"]["published_over25"] or 0.0) <= 0.01)
    unreplayable_n = sum(1 for p in pairs if p["A"]["status"] == "insufficient"
                         and (p["A"]["published_over25"] or 0.0) > 0.01)
    c_a = aggregate([p["A"] for p in common_pairs], clim)
    c_b = aggregate([p["B"] for p in common_pairs], clim)
    common_metrics = (c_a, c_b, recovered_n, unreplayable_n)

    # treated 子集配对检验（untreated 场 Δ≡0 会人为拉低全集 t 值，单独检验更公允）
    tr_rps_d = [rps_8bucket(p["B"]["dist_8"], p["B"]["actual_tg"])
                - rps_8bucket(p["A"]["dist_8"], p["A"]["actual_tg"])
                for p in treated]
    tr_bri_d = [brier_over25(p["B"]["over25_prob"], p["B"]["actual_tg"])
                - brier_over25(p["A"]["over25_prob"], p["A"]["actual_tg"])
                for p in treated]
    treated_tests = (tr_rps_d, tr_bri_d)

    gates = [
        {"id": 1, "name": "RPS B<A 且 p<0.05，B 的 RPSS≥A",
         "detail": f"RPS {met_a['rps_mean']}→{met_b['rps_mean']}, p={p_rps:.4f}, "
                   f"RPSS {met_a['rpss']}→{met_b['rpss']}",
         "pass": met_b["rps_mean"] < met_a["rps_mean"] and p_rps < 0.05
                 and (met_b["rpss"] or -9) >= (met_a["rpss"] or -9),
         "deltas": rps_d},
        {"id": 2, "name": "Brier B≤0.250 且 ECE 下降、≥0.8 档 gap 收敛",
         "detail": f"Brier={met_b['brier_mean']}, ECE {met_a['ece']}→{met_b['ece']}, "
                   f"hi_gap {met_a['bucket_08_gap']}→{met_b['bucket_08_gap']}",
         "pass": met_b["brier_mean"] <= 0.25 and met_b["ece"] <= met_a["ece"] + 1e-9
                 and (met_b["bucket_08_gap"] is None or met_a["bucket_08_gap"] is None
                      or abs(met_b["bucket_08_gap"]) <= abs(met_a["bucket_08_gap"]) + 1e-9),
         "deltas": bri_d},
        {"id": 3, "name": "treated 子集 RPS/Brier 不恶化、无新增极端失效",
         "detail": (f"treated n={t_a['n']}, RPS {t_a['rps_mean']}→{t_b['rps_mean']}, "
                    f"Brier {t_a['brier_mean']}→{t_b['brier_mean']}") if t_a["n"] else "treated=0",
         "pass": bool(t_a["n"]) and t_b["rps_mean"] <= t_a["rps_mean"] + 1e-9
                 and t_b["brier_mean"] <= t_a["brier_mean"] + 1e-9,
         "deltas": []},
        {"id": 4, "name": "大小球准确率不显著下滑（≥-1pp）",
         "detail": f"acc {met_a['over25_acc']}→{met_b['over25_acc']}",
         "pass": met_b["over25_acc"] >= met_a["over25_acc"] - 0.01,
         "deltas": []},
    ]

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md, js = write_report(met_a, met_b, pairs, fidelity, gates, ts, cfg,
                          common=common_metrics, treated_tests=treated_tests)

    print("\n===== A/B 指标（干净样本 n=%d，脏样本 %d）=====" % (met_a["n"], met_a["n_insufficient"]))
    print(f"RPS:   A={met_a['rps_mean']}  B={met_b['rps_mean']}  p={p_rps:.4f} (RPSS A={met_a['rpss']} B={met_b['rpss']})")
    print(f"Brier: A={met_a['brier_mean']}  B={met_b['brier_mean']}  p={p_bri:.4f} (const=0.250)")
    print(f"ECE:   A={met_a['ece']}  B={met_b['ece']} | hi0.8 gap A={met_a['bucket_08_gap']} B={met_b['bucket_08_gap']}")
    print(f"acc:   A={met_a['over25_acc']}  B={met_b['over25_acc']} | Top3 A={met_a['top3_acc']} B={met_b['top3_acc']}")
    print(f"保真: mean|replay-pub|={fidelity['control vs 发布 over25 平均绝对偏差']['value']}, "
          f"补采恢复={n_recovered}, 不可回放={n_unreplayable}")
    print("门禁: " + " / ".join(f"G{g['id']}{'✅' if g['pass'] else '❌'}" for g in gates))
    print(f"\n报告: {md}\nJSON: {js}")


if __name__ == "__main__":
    main()
