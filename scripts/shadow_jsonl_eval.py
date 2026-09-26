"""Shadow jsonl 配对评测脚本（C-20260926-102）

消费 reports/ 下三条 Shadow 流水（赛前快照、按 match_id 去重覆盖），
JOIN odds.db 的实际赛果做配对评测：

  1. t006_shadow_v5.jsonl   —— v4 vs v5 比分（top1/top5/within1/within2）
  2. tg_calib_shadow.jsonl  —— TG λ 校准 control vs shadow（RPS/Brier/logloss/ECE）
  3. tg_wodds_shadow.jsonl  —— TG 融合权重 control vs shadow（同上）

口径约定：
  - 指标函数直接 import 自 tg_calibration_shadow_eval，保证单一事实源；
  - 评测对象是 jsonl 中赛前真实记录，不现场重算（区别于 replay 型脚本）；
  - 门禁沿用 C-20260921-029/041 三闸门，本脚本只出判定，不自动切生产；
  - 剔除场次（v5 缺赔率 error / 无赛果）在报告头部明确计数，不静默丢弃。

局限：jsonl 只存比分 top5 而非全分布，故比分段无法算 RPS，只能算
top1 命中/top5 覆盖/within1/within2（与 t006_v5_offline_retest 同口径子集）。

CLI:
    python scripts/shadow_jsonl_eval.py [--league 英超]
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_DIR / "scripts"))

from tg_calibration_shadow_eval import (  # noqa: E402
    brier_over25,
    ece_over25,
    logloss_8bucket,
    paired_t_test,
    rps_8bucket,
)

DB_PATH = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"

JSONL_FILES = {
    "t006": "t006_shadow_v5.jsonl",
    "tg_calib": "tg_calib_shadow.jsonl",
    "tg_wodds": "tg_wodds_shadow.jsonl",
}


# ============================================================
# 数据加载
# ============================================================
def load_jsonl(name: str) -> Dict[str, dict]:
    path = REPORT_DIR / name
    out: Dict[str, dict] = {}
    for ln in path.read_text(encoding="utf-8").splitlines():
        if not ln.strip():
            continue
        d = json.loads(ln)
        out[d["match_id"]] = d
    return out


def load_outcomes(conn: sqlite3.Connection) -> Dict[str, dict]:
    """match_id -> {actual_score, actual_tg}；post_match_review 为主，matches 补 TG。"""
    out: Dict[str, dict] = {}
    for r in conn.execute(
        "SELECT match_id, actual_score, actual_tg FROM post_match_review "
        "WHERE actual_score IS NOT NULL OR actual_tg IS NOT NULL"
    ):
        out[r[0]] = {"actual_score": r[1], "actual_tg": r[2]}
    # 补 actual_tg（matches.actual_total_goals），不覆盖已有值
    for r in conn.execute(
        "SELECT match_id, actual_score, actual_total_goals FROM matches "
        "WHERE actual_total_goals IS NOT NULL"
    ):
        cur = out.setdefault(r[0], {"actual_score": None, "actual_tg": None})
        if cur.get("actual_tg") is None:
            cur["actual_tg"] = r[2]
        if cur.get("actual_score") is None and r[1]:
            cur["actual_score"] = r[1]
    return out


def parse_score(s: str) -> Optional[Tuple[int, int]]:
    try:
        h, a = str(s).split(":")[:2]
        return int(h), int(a)
    except (ValueError, AttributeError):
        return None


# ============================================================
# ① 比分评测（v4 vs v5）
# ============================================================
def score_metrics(top5: List[dict], most_likely: str,
                  ah: int, aa: int) -> Optional[dict]:
    """top5=[{score,prob}]；返回 top1/top5/within1/within2 + top1 概率。"""
    if not top5:
        return None
    actual_key = f"{ah}:{aa}"
    scores = [x["score"] for x in top5]
    top1 = (most_likely == actual_key)
    top5_hit = actual_key in set(scores)
    w1 = w2 = False
    for sc in scores:
        p = parse_score(sc)
        if p is None:
            continue
        d = abs(p[0] - ah) + abs(p[1] - aa)
        w1 |= d <= 1
        w2 |= d <= 2
    return {
        "top1": bool(top1),
        "top5": bool(top5_hit),
        "within1": bool(w1),
        "within2": bool(w2),
        "top1_prob": float(top5[0].get("prob", 0.0)),
    }


def eval_scores(rows_t006: Dict[str, dict],
                outcomes: Dict[str, dict],
                league: Optional[str]) -> dict:
    pairs: List[dict] = []
    excluded = {"v5_error": 0, "no_outcome": 0, "bad_score": 0}

    for mid, rec in rows_t006.items():
        if league and rec.get("league") != league:
            continue
        oc = outcomes.get(mid)
        actual = parse_score(oc["actual_score"]) if oc else None
        v5 = rec.get("v5")
        v5_valid = isinstance(v5, dict) and "top5" in v5
        if not v5_valid:
            excluded["v5_error"] += 1
            continue
        if actual is None:
            excluded["no_outcome"] += 1
            continue

        m4 = score_metrics(rec.get("v4_top5", []), rec.get("v4_most_likely", ""),
                           actual[0], actual[1])
        m5 = score_metrics(v5.get("top5", []), v5.get("most_likely", ""),
                           actual[0], actual[1])
        if m4 is None or m5 is None:
            excluded["bad_score"] += 1
            continue
        pairs.append({"match_id": mid, "league": rec.get("league"),
                      "actual": f"{actual[0]}:{actual[1]}", "v4": m4, "v5": m5})

    def agg(key: str) -> dict:
        n = len(pairs)
        return {
            "n": n,
            "top1_acc": round(100 * sum(p[key]["top1"] for p in pairs) / n, 2) if n else None,
            "top5_cover": round(100 * sum(p[key]["top5"] for p in pairs) / n, 2) if n else None,
            "within1": round(100 * sum(p[key]["within1"] for p in pairs) / n, 2) if n else None,
            "within2": round(100 * sum(p[key]["within2"] for p in pairs) / n, 2) if n else None,
            "avg_top1_prob": round(sum(p[key]["top1_prob"] for p in pairs) / n, 4) if n else None,
        }

    return {"pairs": pairs, "excluded": excluded,
            "summary_v4": agg("v4"), "summary_v5": agg("v5")}


# ============================================================
# ②③ TG 八档评测
# ============================================================
def _norm_goals(goals: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """取 0..6、7+ 八档；键统一为字符串；做归一化（防快照含微小残差）。"""
    if not isinstance(goals, dict):
        return None
    keys = [str(i) for i in range(7)] + ["7+"]
    dist: Dict[str, float] = {}
    for k in keys:
        v = goals.get(k, goals.get(int(k) if k != "7+" else k))
        if v is None:
            return None
        dist[k] = float(v)
    total = sum(dist.values())
    if total <= 0:
        return None
    return {k: v / total for k, v in dist.items()}


def eval_tg(rows: Dict[str, dict], outcomes: Dict[str, dict],
            payload_key: str, league: Optional[str]) -> dict:
    pairs: List[dict] = []
    excluded = {"no_goals_payload": 0, "no_outcome_tg": 0}

    for mid, rec in rows.items():
        if league and rec.get("league") != league:
            continue
        payload = rec.get(payload_key)
        if not isinstance(payload, dict):
            excluded["no_goals_payload"] += 1
            continue
        dist_a = _norm_goals(payload.get("control_goals"))
        dist_b = _norm_goals(payload.get("shadow_goals"))
        oc = outcomes.get(mid)
        actual_tg = oc.get("actual_tg") if oc else None
        if dist_a is None or dist_b is None:
            excluded["no_goals_payload"] += 1
            continue
        if actual_tg is None:
            excluded["no_outcome_tg"] += 1
            continue
        actual_tg = int(actual_tg)

        a = {
            "dist_8": dist_a,
            "over25_prob": float(payload.get("control_over25", 0.0)),
            "actual_tg": actual_tg,
        }
        b = {
            "dist_8": dist_b,
            "over25_prob": float(payload.get("shadow_over25", 0.0)),
            "actual_tg": actual_tg,
        }
        pairs.append({
            "match_id": mid, "league": rec.get("league"),
            "A": a, "B": b,
            "rps_a": rps_8bucket(dist_a, actual_tg),
            "rps_b": rps_8bucket(dist_b, actual_tg),
            "bri_a": brier_over25(a["over25_prob"], actual_tg),
            "bri_b": brier_over25(b["over25_prob"], actual_tg),
        })

    n = len(pairs)
    if n == 0:
        return {"pairs": [], "excluded": excluded, "summary": None, "gates": []}

    def summarize(arm: str) -> dict:
        rps_pref = f"rps_{arm}"
        rows_arm = [p[arm.upper()] for p in pairs]
        return {
            "n": n,
            "rps_mean": round(sum(p[rps_pref] for p in pairs) / n, 4),
            "brier_mean": round(sum(p[f"bri_{arm}"] for p in pairs) / n, 4),
            "logloss_mean": round(
                sum(logloss_8bucket(p[arm.upper()]["dist_8"],
                                    p[arm.upper()]["actual_tg"]) for p in pairs) / n, 4),
            "ece": round(ece_over25(rows_arm)[0], 4),
        }

    summary_a = summarize("a")
    summary_b = summarize("b")

    # 配对检验（返回 (t, df, p_two_sided)）
    rps_deltas = [p["rps_b"] - p["rps_a"] for p in pairs]
    bri_deltas = [p["bri_b"] - p["bri_a"] for p in pairs]
    _, _, p_rps = paired_t_test(rps_deltas)
    _, _, p_bri = paired_t_test(bri_deltas)

    # 极端失效：RPS≥0.5 的场次
    ext_a = sum(1 for p in pairs if p["rps_a"] >= 0.5)
    ext_b = sum(1 for p in pairs if p["rps_b"] >= 0.5)

    # 三闸门（C-029/041 口径）
    gates = [
        {
            "id": 1,
            "name": "RPS：B<A 且配对 p<0.05",
            "passed": bool(summary_b["rps_mean"] < summary_a["rps_mean"] and p_rps < 0.05),
            "detail": f"RPS {summary_a['rps_mean']}→{summary_b['rps_mean']}, p={p_rps:.4f}",
        },
        {
            "id": 2,
            "name": "Brier B≤0.250 且 ECE 不上升",
            "passed": bool(summary_b["brier_mean"] <= 0.250
                           and summary_b["ece"] <= summary_a["ece"] + 1e-9),
            "detail": f"Brier {summary_a['brier_mean']}→{summary_b['brier_mean']}, "
                      f"ECE {summary_a['ece']}→{summary_b['ece']}",
        },
        {
            "id": 3,
            "name": "无新增极端失效（RPS≥0.5 场次不增）",
            "passed": bool(ext_b <= ext_a),
            "detail": f"极端失效 {ext_a}→{ext_b}；Brier Δ p={p_bri:.4f}",
        },
    ]

    return {
        "pairs": pairs,
        "excluded": excluded,
        "summary_A": summary_a,
        "summary_B": summary_b,
        "p_rps": round(p_rps, 4),
        "p_brier": round(p_bri, 4),
        "gates": gates,
        "all_gates_passed": all(g["passed"] for g in gates),
    }


# ============================================================
# 报告渲染
# ============================================================
def _row(label: str, a: float, b: float) -> str:
    better = " ✅" if b < a else (" ❌" if b > a else " ≈")
    return f"| {label} | {a} | {b} | {b-a:+.4f}{better} |\n"


def _row_up(label: str, a: float, b: float, neutral: bool = False) -> str:
    """越高越好的指标行（比分命中率类）；neutral 时只显示差值不打标。"""
    if neutral:
        mark = " ·"
    else:
        mark = " ✅" if b > a else (" ❌" if b < a else " ≈")
    return f"| {label} | {a} | {b} | {b-a:+.4f}{mark} |\n"


def render_md(result: dict, league: Optional[str]) -> str:
    ts = result["generated_at"]
    scope = f"（筛选联赛：{league}）" if league else "（全联赛）"
    s = f"# Shadow jsonl 配对评测报告 {ts}{scope}\n\n"
    s += "评测对象：reports/ 三条 Shadow 流水中的**赛前真实快照**，"
    s += f"JOIN odds.db 实际赛果。指标口径与 tg_calibration_shadow_eval 同源。\n\n"

    # ① 比分
    sc = result["score"]
    v4, v5 = sc["summary_v4"], sc["summary_v5"]
    s += "## 一、比分 v4 vs v5（T-006）\n\n"
    ex = sc["excluded"]
    s += (f"- 配对成功：**{v4['n']}** 场；剔除：v5 缺 TG 八档赔率 {ex['v5_error']} 场、"
          f"无赛果 {ex['no_outcome']} 场、数据异常 {ex['bad_score']} 场\n")
    s += "- 局限：jsonl 只存 top5 比分，无法计算全分布 RPS\n\n"
    s += "| 指标（%） | v4（现生产） | v5（Shadow） | 差值 |\n|---|---:|---:|---:|\n"
    for key, label in [("top1_acc", "Top1 命中"), ("top5_cover", "Top5 覆盖"),
                       ("within1", "Top5 内 within1"), ("within2", "Top5 内 within2")]:
        s += _row_up(label, v4[key], v5[key])
    s += _row_up("平均 Top1 概率", v4["avg_top1_prob"], v5["avg_top1_prob"], neutral=True)
    s += "\n"

    # ②③ TG
    for tag, title in [("tg_calib", "二、TG λ 校准（control factor=1.0 vs shadow 滚动因子）"),
                       ("tg_wodds", "三、TG 融合权重（control w=0.15 vs shadow w=0.30）")]:
        r = result[tag]
        s += f"## {title}\n\n"
        if not r["summary_A"]:
            s += "无可用配对样本。\n\n"
            continue
        a, b = r["summary_A"], r["summary_B"]
        e = r["excluded"]
        s += (f"- 配对成功：**{a['n']}** 场；剔除：八档缺失 {e['no_goals_payload']} 场、"
              f"无 actual_tg {e['no_outcome_tg']} 场\n\n")
        s += "| 指标 | A（control） | B（shadow） | 差值 |\n|---|---:|---:|---:|\n"
        s += _row("RPS（↓好）", a["rps_mean"], b["rps_mean"])
        s += _row("Brier（↓好）", a["brier_mean"], b["brier_mean"])
        s += _row("Logloss（↓好）", a["logloss_mean"], b["logloss_mean"])
        s += _row("ECE（↓好）", a["ece"], b["ece"])
        s += f"\n配对显著性：RPS Δ p={r['p_rps']}，Brier Δ p={r['p_brier']}\n\n"
        s += "### 门禁判定（C-029/041 三闸门，全过才具备切生产候选资格）\n\n"
        for g in r["gates"]:
            mark = "✅" if g["passed"] else "❌"
            s += f"- {mark} 闸门{g['id']} {g['name']} —— {g['detail']}\n"
        verdict = "**三闸门全过，可进入切换评审**" if r["all_gates_passed"] \
            else "**未全过，继续 Shadow 累积，不切生产**"
        s += f"\n结论：{verdict}\n\n"

    s += "---\n本脚本只出判定，不自动修改生产配置；切换由人工拍板。\n"
    return s


# ============================================================
# main
# ============================================================
def main() -> None:
    parser = argparse.ArgumentParser(description="Shadow jsonl 配对评测")
    parser.add_argument("--league", default=None, help="只评测指定联赛，如 英超")
    args = parser.parse_args()

    rows = {k: load_jsonl(v) for k, v in JSONL_FILES.items()}
    conn = sqlite3.connect(str(DB_PATH))
    outcomes = load_outcomes(conn)
    conn.close()

    result = {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "league_filter": args.league,
        "score": eval_scores(rows["t006"], outcomes, args.league),
        "tg_calib": eval_tg(rows["tg_calib"], outcomes, "tg_calib", args.league),
        "tg_wodds": eval_tg(rows["tg_wodds"], outcomes, "tg_wodds", args.league),
    }

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    json_path = REPORT_DIR / f"shadow_jsonl_eval_{stamp}.json"
    md_path = REPORT_DIR / f"shadow_jsonl_eval_{stamp}.md"
    json_path.write_text(json.dumps(result, ensure_ascii=False, indent=1),
                         encoding="utf-8")
    md_path.write_text(render_md(result, args.league), encoding="utf-8")

    print(f"JSON: {json_path}")
    print(f"MD:   {md_path}")
    sv4, sv5 = result["score"]["summary_v4"], result["score"]["summary_v5"]
    print(f"比分 n={sv4['n']}: v4 top1={sv4['top1_acc']}% -> v5 top1={sv5['top1_acc']}%; "
          f"top5 {sv4['top5_cover']}% -> {sv5['top5_cover']}%")
    for tag in ["tg_calib", "tg_wodds"]:
        r = result[tag]
        if r["summary_A"]:
            print(f"{tag} n={r['summary_A']['n']}: RPS {r['summary_A']['rps_mean']} -> "
                  f"{r['summary_B']['rps_mean']} (p={r['p_rps']}); "
                  f"全闸门={r['all_gates_passed']}")


if __name__ == "__main__":
    main()
