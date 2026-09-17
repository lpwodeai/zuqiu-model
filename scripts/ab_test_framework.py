# -*- coding: utf-8 -*-
"""
ab_test_framework.py — P1-D 线上 A/B 测试框架（流量分流 + 并行在线 + 未来样本统计显著）
====================================================================================
背景：评估报告 P1-D「线上 A/B 测试框架」完全缺失。本模块补齐三项最小能力：

  1. 流量分流：稳定哈希（sha256）把每个「实验单元」确定性地分给某个 variant，
     不随进程重启漂移，也保证同一场比赛永远进同一臂（避免度量被重复污染）。
  2. 并行在线（shadow）：同一 unit 下可同时记录「控制/处理」两臂的概率，
     用于配对显著性检验（McNemar），同时只标记其中一臂为 served（实际服务）。
  3. 未来样本统计显著：累计足够样本后 JOIN matches 赛果，计算各臂
     Accuracy/RPS/LogLoss/DrawRecall + 两比例 z 检验 + McNemar + 95% CI + 门禁。

约定：
  - 概率统一 3 维列序 [客胜, 平局, 主胜]，对齐 train_models.compute_rps。
  - 写入 odds.db 表 ab_test_log（INSERT OR IGNORE 幂等 + busy_timeout）。
  - 分析仅只读连接。

用法：
  python scripts/ab_test_framework.py --init
  python scripts/ab_test_framework.py --assign "<unit_key>" --variants control,treatment
  python scripts/ab_test_framework.py --analyze --experiment <id> [--min-n 30]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
from sklearn.metrics import accuracy_score, log_loss

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "scripts"))

from train_models import compute_rps  # noqa: E402

ODDS_DB = BASE_DIR / "data" / "odds.db"
TABLE = "ab_test_log"

_WDL_NORM = {"主胜": 2, "胜": 2, "平局": 1, "平": 1, "客胜": 0, "负": 0}
CLASS_NAMES = ["客胜", "平局", "主胜"]


# ============================================================
# 1. 稳定流量分流（语言无关：Python hashlib / Node crypto 同算法）
# ============================================================
def hash_bucket(unit_key: str, seed: str = "") -> int:
    """sha256(seed:unit_key) → 0..99 稳定桶号。"""
    raw = f"{seed}:{unit_key}".encode("utf-8")
    digest = hashlib.sha256(raw).hexdigest()
    return int(digest[:8], 16) % 100


def stable_assign(unit_key: str, variants, weights=None, seed: str = "") -> str:
    """确定性把 unit_key 分给某一 variant。

    weights=None 时均匀分流；否则需与 variants 等长（归一化后按累计区间切分）。
    """
    variants = [str(v) for v in variants]
    n = len(variants)
    if n == 0:
        raise ValueError("variants 不能为空")
    if weights is None:
        weights = [1.0] * n
    if len(weights) != n:
        raise ValueError("weights 必须与 variants 等长")
    total = float(sum(weights))
    if total <= 0:
        raise ValueError("weights 之和必须 > 0")
    bucket = hash_bucket(unit_key, seed)
    acc = 0.0
    for i, w in enumerate(weights):
        acc += w
        if bucket < acc / total * 100:
            return variants[i]
    return variants[-1]


# ============================================================
# 2. 落库（幂等 + busy_timeout）
# ============================================================
def connect_db(read_only: bool = False):
    if read_only:
        conn = sqlite3.connect(f"file:{ODDS_DB}?mode=ro", uri=True)
    else:
        conn = sqlite3.connect(str(ODDS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def init_schema(conn):
    conn.execute(f"""
        CREATE TABLE IF NOT EXISTS {TABLE} (
            experiment_id TEXT NOT NULL,
            unit_key      TEXT NOT NULL,
            variant       TEXT NOT NULL,
            probs_json    TEXT NOT NULL,
            served        INTEGER NOT NULL DEFAULT 0,
            match_id      TEXT,
            assigned_at   TEXT NOT NULL,
            PRIMARY KEY (experiment_id, unit_key, variant)
        )""")
    conn.commit()


def record_experiment(conn, experiment_id, unit_key, predictions, served_variant,
                      match_id=None):
    """并行记录所有 variant 的概率（shadow），并标记 served 一臂。

    predictions: {variant: [p_lose, p_draw, p_win]}（3 维，列序客胜/平局/主胜）。
    """
    now = datetime.now(timezone.utc).isoformat()
    for variant, probs in predictions.items():
        arr = [float(x) for x in probs]
        if len(arr) != 3:
            raise ValueError(f"variant {variant} 概率必须 3 维（客胜/平局/主胜），实得 {len(arr)}")
        s = float(sum(arr))
        arr = [x / s if s > 0 else 1.0 / 3.0 for x in arr]
        conn.execute(
            f"INSERT OR IGNORE INTO {TABLE} "
            "(experiment_id, unit_key, variant, probs_json, served, match_id, assigned_at) "
            "VALUES (?,?,?,?,?,?,?)",
            (str(experiment_id), str(unit_key), str(variant),
             json.dumps(arr), 1 if str(variant) == str(served_variant) else 0,
             match_id, now),
        )
    conn.commit()


# ============================================================
# 3. 赛果与指标
# ============================================================
def resolve_actual(home_goals, away_goals, actual_wdl, actual_score):
    if home_goals is not None and away_goals is not None:
        return 2 if home_goals > away_goals else (0 if home_goals < away_goals else 1)
    if actual_wdl:
        y = _WDL_NORM.get(str(actual_wdl).strip())
        if y is not None:
            return y
    if actual_score:
        s = str(actual_score).strip()
        for sep in (":", "-"):
            if sep in s:
                try:
                    h, a = (int(x) for x in s.split(sep))
                    return 2 if h > a else (0 if h < a else 1)
                except (ValueError, AttributeError):
                    return None
    return None


def _load_outcomes(conn):
    out = {}
    for r in conn.execute(
        "SELECT match_id, home_goals, away_goals, actual_wdl, actual_score FROM matches"
    ):
        y = resolve_actual(r["home_goals"], r["away_goals"], r["actual_wdl"], r["actual_score"])
        if y is not None and r["match_id"]:
            out[str(r["match_id"])] = y
    return out


def arm_metrics(Y, P):
    Y = np.asarray(Y, dtype=int)
    P = np.asarray(P, dtype=float)
    n = len(Y)
    if n == 0:
        return {"n": 0}
    rps = float(compute_rps(Y, P, "ab"))
    ll = float(log_loss(Y, P, labels=[0, 1, 2])) if n > 0 else float("nan")
    pred = P.argmax(1)
    acc = float(accuracy_score(Y, pred))
    draw_tp = int(((pred == 1) & (Y == 1)).sum())
    draw_fn = int(((pred != 1) & (Y == 1)).sum())
    dr = draw_tp / (draw_tp + draw_fn) if (draw_tp + draw_fn) else float("nan")
    return {"n": n, "accuracy": acc, "rps": rps, "logloss": ll, "draw_recall": dr}


# ============================================================
# 4. 显著性检验（无 scipy 依赖）
# ============================================================
def _phi(x):
    """标准正态 CDF。"""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def two_prop_z(p1, n1, p2, n2):
    """两比例 z 检验，返回 (z, p)。n≤0 返回 (nan, nan)。"""
    if n1 <= 0 or n2 <= 0:
        return float("nan"), float("nan")
    pooled = (p1 * n1 + p2 * n2) / (n1 + n2)
    if pooled <= 0 or pooled >= 1:
        return float("nan"), float("nan")
    se = math.sqrt(pooled * (1 - pooled) * (1.0 / n1 + 1.0 / n2))
    if se == 0:
        return float("nan"), float("nan")
    z = (p1 - p2) / se
    p = 2.0 * (1.0 - _phi(abs(z)))
    return float(z), float(p)


def mcnemar_p(b, c):
    """配对二分类 McNemar（连续性校正）：b/c 为不一致单元格计数，返回 p。"""
    b, c = int(b), int(c)
    if b + c == 0:
        return 1.0
    chi2 = (abs(b - c) - 1.0) ** 2 / (b + c)
    return math.erfc(math.sqrt(max(chi2, 0.0) / 2.0))


def acc_ci(p, n, z=1.96):
    """正态近似 95% CI（Wilson 修正前移，n 较小时仍可用）。"""
    if n <= 0:
        return [float("nan"), float("nan")]
    se = math.sqrt(p * (1 - p) / n)
    return [max(0.0, p - z * se), min(1.0, p + z * se)]


# ============================================================
# 5. 分析主流程
# ============================================================
def load_records(conn, experiment_id):
    rows = {}
    for r in conn.execute(
        f"SELECT unit_key, variant, probs_json, served, match_id FROM {TABLE} "
        "WHERE experiment_id = ?", (experiment_id,)
    ):
        try:
            probs = json.loads(r["probs_json"])
        except (ValueError, TypeError):
            continue
        rows.setdefault(r["unit_key"], {})[r["variant"]] = {
            "probs": probs, "served": r["served"], "match_id": r["match_id"],
        }
    return rows


def analyze(db_path, experiment_id, min_n=30, paired=False):
    """统计分析 + 门禁。

    paired=False（默认，线上 served 分流口径）：门禁用两比例 z 检验（served 桶）。
    paired=True（shadow 配对口径）：门禁用配对 McNemar，不依赖 served 分流
    （shadow 恒 served=control，treatment 永不真实 serve）。
    """
    conn = connect_db(True)
    try:
        outcomes = _load_outcomes(conn)
        rows = load_records(conn, experiment_id)
    finally:
        conn.close()

    # 聚合：每 unit 每 variant 的 (y, probs, served)
    per_variant = {}
    paired_units = []
    for unit_key, variants in rows.items():
        y = None
        for rec in variants.values():
            mid = rec["match_id"]
            if mid and mid in outcomes:
                y = outcomes[mid]
                break
        if y is None:
            continue
        if len(variants) >= 2:
            paired_units.append(unit_key)
        for variant, rec in variants.items():
            per_variant.setdefault(variant, []).append((y, rec["probs"], rec["served"]))

    variants = sorted(per_variant.keys())
    metrics = {}
    for v in variants:
        Y = [t[0] for t in per_variant[v]]
        P = [t[1] for t in per_variant[v]]
        served = [t[2] for t in per_variant[v]]
        metrics[v] = arm_metrics(Y, P)
        served_Y = [t[0] for t in per_variant[v] if t[2]]
        served_P = [t[1] for t in per_variant[v] if t[2]]
        metrics[v]["served"] = arm_metrics(served_Y, served_P)

    # 显著性：两臂独立占比（served 桶）+ 配对 McNemar（shadow 子集）
    tests = {}
    if len(variants) >= 2:
        c, t = variants[0], variants[1]
        mc, mt = metrics[c]["served"], metrics[t]["served"]
        z, p = two_prop_z(mc.get("accuracy", 0), mc.get("n", 0),
                          mt.get("accuracy", 0), mt.get("n", 0))
        tests["two_prop_z"] = {
            "control": c, "treatment": t,
            "z": z, "p": p,
            "acc_diff": (mt.get("accuracy", 0) - mc.get("accuracy", 0))
            if mc.get("n") and mt.get("n") else float("nan"),
            "control_ci": acc_ci(mc.get("accuracy", 0), mc.get("n", 0)),
            "treatment_ci": acc_ci(mt.get("accuracy", 0), mt.get("n", 0)),
        }
        # 配对 McNemar（仅两臂都记录的 shadow unit）
        b = c_cnt = 0
        for u in paired_units:
            recs = rows[u]
            if c not in recs or t not in recs:
                continue
            y = None
            mid = recs[c].get("match_id") or recs[t].get("match_id")
            if mid and mid in outcomes:
                y = outcomes[mid]
            if y is None:
                continue
            c_correct = int(np.argmax(recs[c]["probs"]) == y)
            t_correct = int(np.argmax(recs[t]["probs"]) == y)
            if c_correct == 1 and t_correct == 0:
                b += 1
            elif c_correct == 0 and t_correct == 1:
                c_cnt += 1
        tests["mcnemar"] = {"n_paired": len(paired_units),
                            "control_only_correct": b, "treatment_only_correct": c_cnt,
                            "p": mcnemar_p(b, c_cnt)}

    # 门禁
    gate = None
    if paired:
        # shadow 配对口径：两臂在同一 unit 上并列记录，用配对 McNemar 判定，
        # 不依赖 served 分流（shadow 恒 served=control，treatment 永不真实 serve）。
        mn = tests.get("mcnemar")
        if not mn or mn["n_paired"] < min_n:
            gate = "insufficient-sample"
        else:
            net = mn["treatment_only_correct"] - mn["control_only_correct"]
            if mn["p"] < 0.05 and net > 0:
                gate = "SIGNIFICANT-WIN"
            elif mn["p"] < 0.05 and net < 0:
                gate = "SIGNIFICANT-LOSS"
            else:
                gate = "NO-SIGNIFICANT-DIFF"
    elif "two_prop_z" in tests:
        # 线上 served 口径：处理臂准确率显著优于控制臂且双臂样本达标
        zt = tests["two_prop_z"]
        if zt["z"] is None or (isinstance(zt["z"], float) and math.isnan(zt["z"])):
            gate = "insufficient-sample"
        elif zt["z"] >= 1.96 and zt["acc_diff"] > 0 and \
                metrics[variants[0]]["served"]["n"] >= min_n and \
                metrics[variants[1]]["served"]["n"] >= min_n:
            gate = "SIGNIFICANT-WIN"
        else:
            gate = "NO-SIGNIFICANT-DIFF"

    return {"experiment_id": experiment_id, "variants": variants,
            "metrics": metrics, "tests": tests, "gate": gate}


# ============================================================
# CLI
# ============================================================
def main():
    ap = argparse.ArgumentParser(description="P1-D 线上 A/B 测试框架")
    ap.add_argument("--init", action="store_true", help="创建 ab_test_log 表")
    ap.add_argument("--assign", help="给定 unit_key 计算稳定分流结果")
    ap.add_argument("--variants", default="control,treatment",
                    help="逗号分隔 variant 列表（--assign/--record 用）")
    ap.add_argument("--weights", help="逗号分隔分流权重，默认均匀")
    ap.add_argument("--seed", default="", help="分流种子（可选）")
    ap.add_argument("--analyze", action="store_true", help="执行统计分析")
    ap.add_argument("--experiment", default="default", help="实验 ID")
    ap.add_argument("--min-n", type=int, default=30, help="门禁最小样本数")
    args = ap.parse_args()

    if args.init:
        conn = connect_db()
        try:
            init_schema(conn)
            print(f"[ab_test] 已初始化表 {TABLE}")
        finally:
            conn.close()
        return

    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    weights = None
    if args.weights:
        weights = [float(w) for w in args.weights.split(",")]

    if args.assign:
        print(f"[ab_test] unit_key={args.assign} → {stable_assign(args.assign, variants, weights, args.seed)}")
        return

    if args.analyze:
        result = analyze(ODDS_DB, args.experiment, args.min_n)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return

    ap.print_help()


if __name__ == "__main__":
    main()