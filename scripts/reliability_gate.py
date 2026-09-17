# -*- coding: utf-8 -*-
"""
P0-B: 可靠性图自动化门禁（Reliability Gate）
============================================
对生产预测（model_predictions.Score_grid_* 累加 → WDL 概率）与真实赛果做
全维度可靠性评估，超阈值以非零退出码阻断投产。

1. Reliability Diagram — 主胜/平/客胜 三类概率 10 分桶：预测均值 vs 实际命中率
2. 分桶 ECE — 每桶 |预期-实际| × (n_bin/N) 加权，输出总 ECE（每类均值）/ ECE_draw
3. 全维度分层 — 分联赛（英/西/意/德/法）、赔率区间（竞彩去抽水隐含概率）、
   场景（市场主客强弱）；样本不足维度标记 insufficient-sample，不阻断
4. 门禁 — ECE 超阈值 → 告警 + exit 1（阻断投产）；否则 exit 0

数据口径:
  - 预测概率: Score_grid_{h}_{a} 36 格（和恒为 1）→ P(主胜)=Σ_{h>a}，P(平)=Σ_{h=a}，P(客胜)=Σ_{h<a}
  - 真实赛果: matches.actual_wdl（胜/负/平/主胜/客胜/平局 混合口径归一），
    actual_wdl 缺失时回退解析 actual_score('H:A')
  - 赔率: wdl_history 每场最新快照，去抽水(1/odds 归一) → 隐含概率
  - 列序: 概率矩阵 [客胜(0), 平局(1), 主胜(2)]（与生产一致）

用法:
  python scripts/reliability_gate.py [--model-name t006_score_predictor_v5]
                                     [--db data/odds.db]
                                     [--out-dir docs/reliability_gate]
                                     [--no-chart]
门禁阈值（可覆盖）:
  --ece-overall 0.10   总 ECE（每类均值）上限
  --ece-draw    0.08   平局 ECE 上限（平局高估 z=-2.86 门禁）
  --ece-league  0.15   单联赛 ECE 上限
  --max-bin-gap 0.15   可靠性图分桶偏差上限（|预测-命中|≥此值且 n≥min_samples → FAIL）
  --min-samples 50     维度分层最小样本（低于 → insufficient-sample，不阻断）

输出:
  - docs/reliability_gate/{YYYYMMDD}/reliability_gate_{HHMMSS}.md / .json
  - docs/reliability_gate/{YYYYMMDD}/reliability_diagram_*.png（matplotlib，--no-chart 关闭）
  - 退出码: 0=PASS 可投产；1=FAIL 阻断投产
"""

import argparse
import json
import sqlite3
import sys
import warnings
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import numpy as np

warnings.filterwarnings("ignore")

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DEFAULT_DB = PROJECT_DIR / "data" / "odds.db"
DEFAULT_MODEL = "t006_score_predictor_v5"
DEFAULT_OUT_DIR = PROJECT_DIR / "docs" / "reliability_gate"

N_BINS = 10
CLASS_NAMES = ["客胜", "平局", "主胜"]
_WDL_NORM = {"主胜": 2, "胜": 2, "平局": 1, "平": 1, "客胜": 0, "负": 0}

# 赔率区间分桶（市场最看好方去抽水隐含概率）
_ODDS_BINS = [-np.inf, 0.35, 0.45, 0.55, 0.65, np.inf]
_ODDS_LABELS = ["<0.35", "0.35-0.45", "0.45-0.55", "0.55-0.65", ">=0.65"]


# ============================================================
# 1. 数据加载
# ============================================================
def _parse_score(score):
    """'H:A' → (0=客胜,1=平,2=主胜)；解析失败返回 None"""
    try:
        h, a = score.split(":")
        h, a = int(h), int(a)
    except (ValueError, AttributeError):
        return None
    return 2 if h > a else (0 if h < a else 1)


def load_predictions(conn: sqlite3.Connection, model_name: str) -> list:
    """Score_grid 36 格 → 每场 WDL 概率 + 真实赛果 + 联赛。"""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT mp.match_id,
               CAST(substr(mp.prediction_type, 12, 1) AS INT),
               CAST(substr(mp.prediction_type, 14, 1) AS INT),
               mp.probability
        FROM model_predictions mp
        WHERE mp.model_name = ?
          AND mp.prediction_type GLOB 'Score_grid_*'
        """,
        (model_name,),
    )
    grids = defaultdict(lambda: np.zeros((6, 6)))
    for mid, h, a, p in cur.fetchall():
        if p is None:
            continue
        grids[mid][h, a] = float(p)

    rows = []
    for mid, g in grids.items():
        ph = float(g[np.tril_indices(6, -1)].sum())  # h > a
        pd = float(np.trace(g))
        pa = float(g[np.triu_indices(6, 1)].sum())   # h < a
        rows.append((mid, ph, pd, pa))
    return rows


def load_actuals(conn: sqlite3.Connection, rows: list) -> list:
    """补 real_y / league；actual_wdl 优先，缺省回退 actual_score。"""
    cur = conn.cursor()
    meta = {}
    cur.execute(
        "SELECT match_id, actual_wdl, actual_score, league FROM matches"
    )
    for mid, wdl, score, league in cur.fetchall():
        y = _WDL_NORM.get(str(wdl).strip()) if wdl else None
        if y is None and score:
            y = _parse_score(str(score))
        if y is not None:
            meta[mid] = (y, league)
    out = []
    for mid, ph, pd, pa in rows:
        rec = meta.get(mid)
        if rec is None:
            continue
        out.append({"match_id": mid, "y": rec[0], "league": rec[1] or "",
                    "proba": np.array([pa, pd, ph])})
    return out


def load_odds_implied(conn: sqlite3.Connection) -> dict:
    """wdl_history 每场最新快照 → 去抽水隐含概率 {match_id: [away,draw,home]}。"""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT match_id, win_a, draw, win_b FROM wdl_history
        WHERE (match_id, timestamp) IN (
            SELECT match_id, MAX(timestamp) FROM wdl_history GROUP BY match_id)
        """
    )
    out = {}
    for mid, w, d, l in cur.fetchall():
        try:
            w, d, l = float(w), float(d), float(l)
            if min(w, d, l) <= 1:
                continue
            inv = [1 / l, 1 / d, 1 / w]  # lose→客胜, draw→平, win→主胜
            s = sum(inv)
            out[mid] = [x / s for x in inv]
        except (TypeError, ValueError):
            continue
    return out


# ============================================================
# 2. 指标：分桶 ECE / Reliability Diagram
# ============================================================
def bin_reliability(prob, hit, n_bins=N_BINS):
    """单类可靠性分桶: 返回每桶 {bin_id, lo, hi, n, conf, acc, gap} + 加权 ECE。"""
    bins = np.linspace(0.0, 1.0, n_bins + 1)
    bucket = np.clip(np.digitize(prob, bins[1:-1]), 0, n_bins - 1)
    N = len(prob)
    out, ece = [], 0.0
    for b in range(n_bins):
        m = bucket == b
        n_b = int(m.sum())
        if n_b == 0:
            continue
        conf = float(prob[m].mean())
        acc = float(hit[m].mean())
        gap = conf - acc
        ece += (n_b / N) * abs(gap)
        out.append({"bin_id": b, "lo": float(bins[b]), "hi": float(bins[b + 1]),
                    "n": n_b, "conf": conf, "acc": acc, "gap": gap})
    return out, float(ece)


def class_ece(y, proba, cls):
    """cls ∈ {0,1,2} 的 ECE。"""
    _, ece = bin_reliability(proba[:, cls], (y == cls).astype(float))
    return ece


def ece_set(y, proba, n_bins=N_BINS):
    """返回 (每类 ECE list[3], 总 ECE=每类均值, 各桶明细 list[3])。"""
    per_cls, bins_by_cls = [], []
    for c in range(3):
        bins, ece = bin_reliability(proba[:, c], (y == c).astype(float), n_bins)
        per_cls.append(ece)
        bins_by_cls.append(bins)
    return per_cls, float(np.mean(per_cls)), bins_by_cls


# ============================================================
# 3. 全维度分层
# ============================================================
def _stratum(y, proba):
    per_cls, overall, _ = ece_set(y, proba)
    return {"n": int(len(y)), "ece": overall, "ece_away": per_cls[0],
            "ece_draw": per_cls[1], "ece_home": per_cls[2],
            "draw_hit_rate": float((y == 1).mean()),
            "draw_pred_mean": float(proba[:, 1].mean())}


def stratify(data: list, odds_implied: dict, min_samples: int) -> dict:
    """返回 {league: {...}, odds_interval: {...}, scenario: {...}, overall: {...}}。"""
    y = np.array([r["y"] for r in data])
    proba = np.stack([r["proba"] for r in data])
    out = {"overall": _stratum(y, proba)}

    # 3.1 分联赛
    leagues = sorted({r["league"] for r in data if r["league"]})
    dim_league = {}
    for lg in leagues:
        idx = [i for i, r in enumerate(data) if r["league"] == lg]
        dim_league[lg] = _stratum(y[idx], proba[idx])
    out["by_league"] = dim_league

    # 3.2/3.3 依赖赔率
    imp = np.zeros((len(data), 3))
    has_odds = np.zeros(len(data), dtype=bool)
    for i, r in enumerate(data):
        o = odds_implied.get(r["match_id"])
        if o:
            imp[i] = o
            has_odds[i] = True

    dim_odds = {}
    if has_odds.any():
        fav = imp.max(axis=1)
        for i, label in enumerate(_ODDS_LABELS):
            mask = has_odds & (fav > _ODDS_BINS[i]) & (fav <= _ODDS_BINS[i + 1])
            if mask.any():
                dim_odds[label] = _stratum(y[mask], proba[mask])
    out["by_odds_interval"] = dim_odds

    # 3.3 场景：市场主客强弱（主强/客强/均势）
    dim_scene = {}
    if has_odds.any():
        gap = imp[:, 2] - imp[:, 0]
        for label, cond in [("主强", gap >= 0.15), ("客强", gap <= -0.15),
                            ("均势", np.abs(gap) < 0.15)]:
            mask = has_odds & cond
            if mask.any():
                dim_scene[label] = _stratum(y[mask], proba[mask])
    out["by_scenario"] = dim_scene

    # 样本不足 → insufficient-sample 标记（不阻断）
    for dim in ("by_league", "by_odds_interval", "by_scenario"):
        for k, v in out[dim].items():
            if v["n"] < min_samples:
                v["insufficient_sample"] = True
    return out


# ============================================================
# 4. 门禁判定
# ============================================================
def gate_verdict(strata: dict, diagram: dict, ece_overall: float, ece_draw: float,
                 ece_league: float, max_bin_gap: float, min_samples: int) -> dict:
    """返回 {status: PASS/FAIL, violations: [str]}。"""
    violations = []
    ov = strata["overall"]
    if ov["ece"] >= ece_overall:
        violations.append(
            f"总 ECE {ov['ece']:.3f} ≥ 阈值 {ece_overall:.3f}（平均每类校准偏差超限）")
    if ov["ece_draw"] >= ece_draw:
        violations.append(
            f"平局 ECE {ov['ece_draw']:.3f} ≥ 阈值 {ece_draw:.3f}（平局概率高估，z=-2.86 已知缺陷门禁）")

    # 可靠性图分桶偏差门禁：某桶 |预测-命中| ≥ max_bin_gap 且样本足够 → 阻断
    for cls, bins in diagram.items():
        for b in bins:
            if b["n"] >= min_samples and abs(b["gap"]) >= max_bin_gap:
                violations.append(
                    f"可靠性图[{cls}] 桶[{b['lo']:.1f}-{b['hi']:.1f}] "
                    f"预测 {b['conf']*100:.1f}% vs 命中 {b['acc']*100:.1f}% "
                    f"偏差 {b['gap']*100:+.1f}pp ≥ {max_bin_gap*100:.0f}pp（n={b['n']}）")

    for lg, s in strata["by_league"].items():
        if s["n"] >= min_samples and s["ece"] >= ece_league:
            violations.append(
                f"联赛[{lg}] ECE {s['ece']:.3f} ≥ 阈值 {ece_league:.3f}（n={s['n']}）")

    for dim in ("by_odds_interval", "by_scenario"):
        for k, s in strata[dim].items():
            if s["n"] >= min_samples and s["ece"] >= ece_overall:
                violations.append(
                    f"维度[{dim}/{k}] ECE {s['ece']:.3f} ≥ 阈值 {ece_overall:.3f}（n={s['n']}）")

    return {"status": "FAIL" if violations else "PASS", "violations": violations}


# ============================================================
# 5. 输出：md / json / png
# ============================================================
def _fmt_ece(s):
    return (f"{s['ece']*100:.2f}% (客{s['ece_away']*100:.2f}/"
            f"平{s['ece_draw']*100:.2f}/主{s['ece_home']*100:.2f})")


def render_charts(proba, y, out_dir: Path, model_name: str):
    """Reliability Diagram PNG（三类合并一张 + 各类单张）。"""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    def draw_axis(ax, bins, cname, color):
        conf = [b["conf"] for b in bins]
        acc = [b["acc"] for b in bins]
        ax.plot(conf, acc, "o-", color=color, label=cname)
        ax.plot([0, 1], [0, 1], "k--", lw=0.8, alpha=0.6)
        ax.set_xlim(0, 1)
        ax.set_ylim(0, 1)
        ax.set_xlabel("预测概率")
        ax.set_ylabel("实际命中率")
        ax.legend()

    _, _, bins_by_cls = ece_set(y, proba)
    colors = ["#c0392b", "#2980b9", "#27ae60"]
    fig, axes = plt.subplots(1, 3, figsize=(15, 4.5), sharey=True)
    for c, ax in enumerate(axes):
        draw_axis(ax, bins_by_cls[c], CLASS_NAMES[c], colors[c])
        ax.set_title(f"{CLASS_NAMES[c]} Reliability")
    fig.suptitle(f"Reliability Diagram — {model_name}", y=1.02)
    fig.tight_layout()
    fig.savefig(out_dir / "reliability_diagram.png", dpi=120,
                bbox_inches="tight")
    plt.close(fig)
    for c in range(3):
        fig, ax = plt.subplots(figsize=(5.5, 4.5))
        draw_axis(ax, bins_by_cls[c], CLASS_NAMES[c], colors[c])
        ax.set_title(f"{CLASS_NAMES[c]} Reliability — {model_name}")
        fig.tight_layout()
        fig.savefig(out_dir / f"reliability_diagram_{CLASS_NAMES[c]}.png",
                    dpi=120, bbox_inches="tight")
        plt.close(fig)


def write_md(report: dict, out_dir: Path, model_name: str, verdict: dict):
    ts = report["generated_at"]
    lines = [
        "# 可靠性图门禁报告（P0-B Reliability Gate）",
        "",
        f"**生成时间**: {ts}",
        f"**模型**: `{model_name}`",
        f"**样本量**: {report['n']} 场（已完赛 + 有 Score_grid 预测）",
        f"**门禁状态**: {'✅ PASS 可投产' if verdict['status'] == 'PASS' else '❌ FAIL 阻断投产'}",
        "",
        "## 一、总览",
        "",
        "| 指标 | 值 | 阈值 | 判定 |",
        "|---|---|---|---|",
    ]
    ov = report["strata"]["overall"]
    lines += [
        f"| 总 ECE（每类均值） | {ov['ece']*100:.2f}% | {report['thresholds']['ece_overall']*100:.2f}% | "
        f"{'PASS' if ov['ece'] < report['thresholds']['ece_overall'] else 'FAIL'} |",
        f"| ECE_draw（平局） | {ov['ece_draw']*100:.2f}% | {report['thresholds']['ece_draw']*100:.2f}% | "
        f"{'PASS' if ov['ece_draw'] < report['thresholds']['ece_draw'] else 'FAIL'} |",
        f"| 分桶最大偏差（|预测-命中|） | {report['diagram_max_gap']*100:.1f}pp | "
        f"{report['thresholds']['max_bin_gap']*100:.0f}pp | "
        f"{'PASS' if report['diagram_max_gap'] < report['thresholds']['max_bin_gap'] else 'FAIL'} |",
        f"| 平局命中率 | {ov['draw_hit_rate']*100:.2f}% | — | — |",
        f"| 平局预测均值 | {ov['draw_pred_mean']*100:.2f}% | — | 预测均值 vs 命中率反映高估/低估 |",
    ]
    if verdict["violations"]:
        lines += ["", "## 二、违规项（阻断投产）", ""]
        lines += [f"1. {v}" for v in verdict["violations"]]

    for dim, title in [("by_league", "三、分联赛"), ("by_odds_interval", "四、赔率区间（竞彩去抽水隐含概率）"),
                       ("by_scenario", "五、场景（市场主客强弱）")]:
        lines += ["", f"## {title}", "", "| 分层 | n | 总ECE | ECE客 | ECE平 | ECE主 | 状态 |",
                  "|---|---|---|---|---|---|---|"]
        for k, s in report["strata"][dim].items():
            if s.get("insufficient_sample"):
                status = f"样本不足(<{report['thresholds']['min_samples']}) 不阻断"
            else:
                status = "PASS" if s["ece"] < report["thresholds"]["ece_overall"] else "FAIL"
            lines.append(
                f"| {k} | {s['n']} | {s['ece']*100:.2f}% | {s['ece_away']*100:.2f}% | "
                f"{s['ece_draw']*100:.2f}% | {s['ece_home']*100:.2f}% | {status} |")

    lines += ["", "## 六、Reliability Diagram（10 分桶，预测均值 vs 命中率）", ""]
    if (out_dir / "reliability_diagram.png").exists():
        lines += ["![Reliability Diagram](reliability_diagram.png)", ""]
    lines += ["| 类别 | 桶 | n | 预测均值 | 命中率 | 偏差 |", "|---|---|---|---|---|---|"]
    for c in range(3):
        for b in report["diagram"][CLASS_NAMES[c]]:
            lines.append(
                f"| {CLASS_NAMES[c]} | {b['lo']:.1f}-{b['hi']:.1f} | {b['n']} | "
                f"{b['conf']*100:.2f}% | {b['acc']*100:.2f}% | {b['gap']*100:+.2f}pp |")
    lines += ["", "## 七、结论", ""]
    if verdict["status"] == "PASS":
        lines.append("**结论**: 概率可靠性达标，可通过门禁投产。")
    else:
        lines.append("**结论**: 概率可靠性未达标（存在平局高估/校准偏差），**阻断投产**；"
                     "需校准修正或回退模型后重跑本门禁，PASS 方可晋升。")
    (out_dir / f"reliability_gate.md").write_text("\n".join(lines), encoding="utf-8")


def run_reliability_gate(db_path, model_name=DEFAULT_MODEL, out_dir=DEFAULT_OUT_DIR,
                         ece_overall=0.10, ece_draw=0.08, ece_league=0.15,
                         max_bin_gap=0.15, min_samples=50, render=True):
    conn = sqlite3.connect(str(db_path))

    rows = load_predictions(conn, model_name)
    data = load_actuals(conn, rows)
    odds_implied = load_odds_implied(conn)
    conn.close()

    if len(data) == 0:
        raise SystemExit(f"[FAIL] 模型 {model_name} 无「Score_grid + 真实赛果」可用样本，门禁无法运行")

    y = np.array([r["y"] for r in data])
    proba = np.stack([r["proba"] for r in data])
    _, _, bins_by_cls = ece_set(y, proba)
    diagram = {CLASS_NAMES[c]: bins_by_cls[c] for c in range(3)}

    strata = stratify(data, odds_implied, min_samples)
    verdict = gate_verdict(strata, diagram, ece_overall, ece_draw,
                           ece_league, max_bin_gap, min_samples)

    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    out_dir = out_dir / datetime.now().strftime("%Y%m%d")  # 按日归档
    report = {
        "generated_at": ts,
        "model_name": model_name,
        "n": len(data),
        "with_odds": int(sum(1 for r in data if r["match_id"] in odds_implied)),
        "thresholds": {"ece_overall": ece_overall, "ece_draw": ece_draw,
                       "ece_league": ece_league, "max_bin_gap": max_bin_gap,
                       "min_samples": min_samples},
        "strata": strata,
        "diagram": diagram,
        "diagram_max_gap": float(max(
            (abs(b["gap"]) for bins in diagram.values() for b in bins), default=0.0)),
        "verdict": verdict,
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    if render:
        try:
            render_charts(proba, y, out_dir, model_name)
        except Exception as e:
            print(f"[WARN] 图表渲染失败（跳过）: {e}")
    write_md(report, out_dir, model_name, verdict)
    (out_dir / "reliability_gate.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=float),
        encoding="utf-8")
    return report, verdict


def main():
    ap = argparse.ArgumentParser(description="P0-B 可靠性图自动化门禁")
    ap.add_argument("--model-name", default=DEFAULT_MODEL)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out-dir", type=Path, default=DEFAULT_OUT_DIR)
    ap.add_argument("--ece-overall", type=float, default=0.10)
    ap.add_argument("--ece-draw", type=float, default=0.08)
    ap.add_argument("--ece-league", type=float, default=0.15)
    ap.add_argument("--max-bin-gap", type=float, default=0.15)
    ap.add_argument("--min-samples", type=int, default=50)
    ap.add_argument("--no-chart", action="store_true")
    args = ap.parse_args()

    report, verdict = run_reliability_gate(
        args.db, args.model_name, args.out_dir,
        args.ece_overall, args.ece_draw, args.ece_league,
        args.max_bin_gap, args.min_samples, render=not args.no_chart)

    ov = report["strata"]["overall"]
    print("=" * 62)
    print("[P0-B] 可靠性图自动化门禁")
    print(f"  模型: {report['model_name']}  样本: {report['n']} 场  "
          f"含赔率: {report['with_odds']} 场")
    print(f"  总 ECE: {ov['ece']*100:.2f}%  ECE_draw: {ov['ece_draw']*100:.2f}%  "
          f"分桶最大偏差: {report['diagram_max_gap']*100:.1f}pp")
    print(f"  (平局预测均值 {ov['draw_pred_mean']*100:.2f}% vs 命中率 {ov['draw_hit_rate']*100:.2f}%)")
    for lg, s in report["strata"]["by_league"].items():
        print(f"    [{lg}] n={s['n']:5d}  ECE={s['ece']*100:5.2f}%  "
              f"ECE_draw={s['ece_draw']*100:5.2f}%"
              + ("  [样本不足]" if s.get("insufficient_sample") else ""))
    print("-" * 62)
    print(f"[{'FAIL' if verdict['status']=='FAIL' else 'PASS'}] 可靠性门禁: {verdict['status']}"
          + ("" if verdict["status"] == "PASS" else " — 阻断投产"))
    for v in verdict["violations"]:
        print(f"    - {v}")
    print(f"  报告: {args.out_dir / datetime.now().strftime('%Y%m%d') / 'reliability_gate.md'}")
    print(f"  数据: {args.out_dir / datetime.now().strftime('%Y%m%d') / 'reliability_gate.json'}")
    print("=" * 62)
    return 1 if verdict["status"] == "FAIL" else 0


if __name__ == "__main__":
    sys.exit(main())
