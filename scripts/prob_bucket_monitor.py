# -*- coding: utf-8 -*-
"""生产发布概率档校准监控看板（P0-2 / C-20261002-008）。

只读 odds.db，不回写任何预测/报告数据。复盘口径遵守「只读发布锚点」：
仅统计 model_predictions.is_replay=0（真实赛前发布）的 WDL 概率，
赛后回放补算（is_replay=1）不参与，避免数据穿越。

输出：
  reports/prob_bucket_monitor_<TS>.md     人读看板
  reports/prob_bucket_monitor_<TS>.json   原始分桶
  reports/prob_bucket_monitor_latest.json 稳定快照（供趋势/外部看板消费）

桶规则（固定 5% 一档）：
  n >= 50  「可校准」——样本充足，校准修正允许参考该档
  20<=n<50 「观察」——只监控
  n < 20   不输出（样本过小）
P0-2 阶段所有档位一律只监控、不触发任何自动校准。

用法：
  python scripts/prob_bucket_monitor.py
  python scripts/prob_bucket_monitor.py --db data/odds.db
"""
import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

DEFAULT_DB = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"

CLASSES = [
    ("WDL_home", "主胜"),
    ("WDL_draw", "平局"),
    ("WDL_away", "客胜"),
]
ACTUAL_MAP = {"主胜": "WDL_home", "胜": "WDL_home",
              "平局": "WDL_draw", "平": "WDL_draw",
              "客胜": "WDL_away", "负": "WDL_away"}
N_BINS = 20
MIN_SHOW = 20      # 最小展示样本
CAL_READY = 50     # 可校准样本门槛（P0-2：达到也仅监控）
BIAS_ALERT = 0.02  # |实际率-预测均值| >= 2pp 标偏差
WINDOWS = [("全量", None), ("2026 年至今", "2026-01-01"), ("2025-08 起", "2025-08-01")]


def load_anchor_rows(db_path: Path) -> pd.DataFrame:
    conn = sqlite3.connect(str(db_path))
    try:
        cols = {r[1] for r in conn.execute("PRAGMA table_info(model_predictions)")}
        has_replay = "is_replay" in cols
        replay_filter = "AND mp.is_replay = 0" if has_replay else ""
        sql = f"""
            SELECT mp.match_id, mp.prediction_type, mp.probability,
                   mp.timestamp AS pred_ts,
                   m.actual_wdl, m.league, m.match_date
            FROM model_predictions mp
            JOIN matches m ON mp.match_id = m.match_id
            WHERE mp.prediction_type IN ('WDL_home','WDL_draw','WDL_away')
              AND mp.probability IS NOT NULL
              AND m.actual_wdl IS NOT NULL AND m.actual_wdl != ''
              {replay_filter}
        """
        df = pd.read_sql(sql, conn)
    finally:
        conn.close()
    df["probability"] = pd.to_numeric(df["probability"], errors="coerce")
    df = df[df["probability"].between(0.0, 1.0, inclusive="both")].copy()
    # UNIQUE(match_id, model_name, prediction_type)：同场同类多行（多模型版本）取赛前最早锚点
    df["pred_ts"] = pd.to_datetime(df["pred_ts"], errors="coerce")
    df = df.sort_values("pred_ts").drop_duplicates(["match_id", "prediction_type"], keep="first")
    df["actual_cls"] = df["actual_wdl"].map(ACTUAL_MAP)
    df = df[df["actual_cls"].notna()].copy()
    df["hit"] = (df["actual_cls"] == df["prediction_type"]).astype(int)
    df["match_date"] = pd.to_datetime(df["match_date"], errors="coerce")
    df.attrs["has_replay"] = has_replay
    return df


def bucket_frame(sub: pd.DataFrame) -> pd.DataFrame:
    edges = np.linspace(0.0, 1.0, N_BINS + 1)
    rows = []
    for b in range(N_BINS):
        lo, hi = edges[b], edges[b + 1]
        m = (sub["probability"] >= lo) & (sub["probability"] < hi) if b < N_BINS - 1 \
            else (sub["probability"] >= lo) & (sub["probability"] <= hi)
        n = int(m.sum())
        if n < MIN_SHOW:
            continue
        avg = float(sub.loc[m, "probability"].mean())
        freq = float(sub.loc[m, "hit"].mean())
        if n >= CAL_READY:
            status = "可校准"
        else:
            status = "观察"
        rows.append({
            "bin_low": round(lo, 3), "bin_high": round(hi, 3),
            "n": n, "avg_pred": round(avg, 4), "actual_freq": round(freq, 4),
            "bias": round(freq - avg, 4), "abs_bias": round(abs(freq - avg), 4),
            "status": status,
        })
    return pd.DataFrame(rows)


def brier(sub: pd.DataFrame) -> float:
    y = sub["hit"].astype(float).values
    p = sub["probability"].astype(float).values
    return float(np.mean((p - y) ** 2))


def ece_pivot(df: pd.DataFrame) -> float:
    """按场聚合三向概率后的 top-class ECE（仅三行齐全的场次）。"""
    piv = df.pivot_table(index="match_id", columns="prediction_type",
                         values="probability", aggfunc="first")
    act = df.drop_duplicates("match_id").set_index("match_id")["actual_cls"]
    piv = piv.dropna(subset=[c for c, _ in CLASSES])
    act = act.reindex(piv.index)
    probs = piv[[c for c, _ in CLASSES]].values
    probs = probs / probs.sum(axis=1, keepdims=True)
    cls_idx = probs.argmax(axis=1)
    cls_codes = {c: i for i, (c, _) in enumerate(CLASSES)}
    y = np.array([cls_codes[a] for a in act.values])
    conf = probs[np.arange(len(probs)), cls_idx]
    hit = (cls_idx == y).astype(float)
    edges = np.linspace(0.0, 1.0, N_BINS + 1)
    ece = 0.0
    for b in range(N_BINS):
        lo, hi = edges[b], edges[b + 1]
        m = (conf >= lo) & (conf < hi) if b < N_BINS - 1 else (conf >= lo) & (conf <= hi)
        if m.sum() >= MIN_SHOW:
            ece += (m.sum() / len(conf)) * abs(hit[m].mean() - conf[m].mean())
    return float(ece)


def table_md(bf: pd.DataFrame) -> list:
    lines = ["| 概率档 | 场次 | 预测均值 | 实际率 | 偏差 | 状态 |",
             "|---|---|---|---|---|---|"]
    for _, r in bf.iterrows():
        flag = "⚠️" if r["abs_bias"] >= BIAS_ALERT else ""
        lines.append(
            f"| {r['bin_low']*100:.0f}~{r['bin_high']*100:.0f}% | {r['n']} | "
            f"{r['avg_pred']*100:.1f}% | {r['actual_freq']*100:.1f}% | "
            f"{r['bias']*100:+.1f}pp{flag} | {r['status']} |"
        )
    return lines


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()

    df = load_anchor_rows(args.db)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    REPORT_DIR.mkdir(exist_ok=True)

    n_matches = df["match_id"].nunique()
    date_min = df["match_date"].min()
    date_max = df["match_date"].max()
    top_ece = ece_pivot(df)

    payload = {
        "meta": {
            "generated_at": datetime.now().isoformat(timespec="seconds"),
            "db": str(args.db),
            "anchor_rule": "model_predictions.is_replay=0（真实赛前发布；回放补算剔除）",
            "has_is_replay_column": bool(df.attrs.get("has_replay")),
            "n_rows": int(len(df)),
            "n_matches": int(n_matches),
            "date_range": [str(date_min.date()) if date_min is not None else None,
                           str(date_max.date()) if date_max is not None else None],
            "bin_width": 1.0 / N_BINS,
            "min_show": MIN_SHOW,
            "calibration_ready_n": CAL_READY,
            "policy": "P0-2：全部档位仅监控，不触发自动校准",
            "top_class_ece": round(top_ece, 4),
        },
        "windows": {},
    }

    md = ["# 生产概率档校准监控看板（P0-2）\n",
          f"**生成时间**：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
          f"**锚点口径**：{payload['meta']['anchor_rule']}",
          f"**样本**：{n_matches} 场 / {len(df)} 行 WDL 锚点概率"
          f"（{payload['meta']['date_range'][0]} ~ {payload['meta']['date_range'][1]}）",
          f"**分档**：固定 5% 档；n≥50=可校准，20≤n<50=观察，n<20 不展示",
          f"**Top-class ECE（n≥{MIN_SHOW} 档参与）**：{top_ece*100:.2f}%",
          "> P0-2 阶段：所有档位一律只监控，不触发任何自动校准。\n"]

    for wname, cut in WINDOWS:
        sub_all = df if cut is None else df[df["match_date"] >= cut]
        wmeta = {"n_matches": int(sub_all["match_id"].nunique())}
        md.append(f"\n## 窗口：{wname}（{wmeta['n_matches']} 场）\n")
        payload["windows"][wname] = {"n_matches": wmeta["n_matches"], "classes": {}}
        for ptype, cn in CLASSES:
            sub = sub_all[sub_all["prediction_type"] == ptype]
            bf = bucket_frame(sub)
            br = brier(sub) if len(sub) else None
            ready = int((bf["n"] >= CAL_READY).sum()) if len(bf) else 0
            shown = int(len(bf))
            actual_rate = float(sub["hit"].mean()) if len(sub) else None
            avg_pred = float(sub["probability"].mean()) if len(sub) else None
            md.append(f"### {cn}（{ptype}）")
            md.append(f"- 样本 {len(sub)} 行；展示 {shown} 档，其中 n≥50 的 **{ready}** 档；"
                      f"Brier={br:.4f}；整体预测均值={avg_pred*100:.1f}% / 实际率={actual_rate*100:.1f}%"
                      f"（偏差 {(actual_rate-avg_pred)*100:+.1f}pp）" if len(sub) else "- 无样本")
            if len(bf):
                md.extend(table_md(bf))
            md.append("")
            payload["windows"][wname]["classes"][ptype] = {
                "cn": cn, "n_rows": int(len(sub)),
                "brier": round(br, 4) if br is not None else None,
                "avg_pred": round(avg_pred, 4) if avg_pred is not None else None,
                "actual_rate": round(actual_rate, 4) if actual_rate is not None else None,
                "bins_ready": ready, "bins_shown": shown,
                "buckets": bf.to_dict("records"),
            }

    # 联赛覆盖速览
    lg = (df.drop_duplicates("match_id")
            .groupby("league")
            .agg(n=("match_id", "nunique"))
            .reset_index()
            .sort_values("n", ascending=False))
    md.append("\n## 联赛样本覆盖\n")
    md.append("| 联赛 | 已结算锚点场数 |")
    md.append("|---|---|")
    for _, r in lg.iterrows():
        md.append(f"| {r['league']} | {int(r['n'])} |")
    payload["league_coverage"] = {r["league"]: int(r["n"]) for _, r in lg.iterrows()}

    md.append("\n---\n*由 scripts/prob_bucket_monitor.py 生成（只读，不回写）*\n")

    md_path = REPORT_DIR / f"prob_bucket_monitor_{ts}.md"
    json_path = REPORT_DIR / f"prob_bucket_monitor_{ts}.json"
    latest_path = REPORT_DIR / "prob_bucket_monitor_latest.json"
    md_path.write_text("\n".join(md), encoding="utf-8")
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    latest_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    print(f"✅ {md_path}")
    print(f"✅ {json_path}")
    print(f"✅ {latest_path}")
    print(f"锚点场次={n_matches} ECE={top_ece:.4f}")
    for wname, _ in WINDOWS:
        w = payload["windows"][wname]
        ready = {c: w["classes"][c]["bins_ready"] for c, _ in CLASSES if c in w["classes"]}
        print(f"  [{wname}] n={w['n_matches']} n≥50档位数={ready}")


if __name__ == "__main__":
    main()
