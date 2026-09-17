# -*- coding: utf-8 -*-
"""EV 期望值引擎历史回测脚本。

对齐三源数据，逐场跑 ev_engine 的决策逻辑，最后评估 ROI / 命中率：
  1. 模型概率   —— assets/all_matches_predictions_*.csv（XGB/LGB OOF + Platt 校准）
  2. 市场赔率   —— odds.db 的 odds500_ouzhi_summary（百家欧指初盘/即盘）
  3. 实际赛果   —— CSV 内 actual_result（0=客胜 / 1=平局 / 2=主胜）

用法：
  python ev_backtest.py
  python ev_backtest.py --odds-source odds500_init --model lgb
  python ev_backtest.py --min-ev 0.05 --kelly-strategy half --kelly-cap 0.30

输出：
  - 控制台汇总 + 分决策/方向/联赛/edge 分桶
  - reports/ev_backtest_<时间戳>.md
  - reports/ev_backtest_<时间戳>.json
"""
import argparse
import csv
import json
import os
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from ev_engine import analyze_match, ModelProbabilities, OddsData  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
CSV_PATH = BASE_DIR / "assets" / "all_matches_predictions_20260821_170550.csv"
DB_PATH = BASE_DIR / "data" / "odds.db"
REPORT_DIR = BASE_DIR / "reports"

# actual_result 编码 → ev_engine 方向
RESULT_TO_DIRECTION = {2: "home", 1: "draw", 0: "away"}
DIRECTION_CN = {"home": "主胜", "draw": "平局", "away": "客胜"}


def season_from_date(date_str):
    """由 YYYY-MM-DD 推导赛季标签（如 2017-11-25 → 2017-18）。

    欧洲五大联赛赛季约 8 月开打、次年 5 月结束：
      - 月 >= 7 → 赛季起始年 = 年（8~12 月 + 7 月）
      - 月 < 7 → 赛季起始年 = 年 - 1（1~5 月为上年赛季后半程）
    """
    if not date_str:
        return "unknown"
    try:
        y, m, _ = date_str[:10].split("-")
        year, month = int(y), int(m)
    except (ValueError, IndexError):
        return "unknown"
    start = year if month >= 7 else year - 1
    return f"{start}-{str(start + 1)[2:]}"


def _f(v, default=None):
    """TEXT 赔率字段 → float，非法返回 None。"""
    if v is None:
        return default
    try:
        f = float(v)
    except (TypeError, ValueError):
        return default
    return f if f > 1.0 else default


def build_team_bridge(db_path: Path):
    """构建 (日期, 标准化主队, 标准化客队) -> 英文 match_id 的桥接映射。

    背景：matches.match_id 在 2016~2023 赛季为中文格式（如 2016-08-28_曼彻斯特城_西汉姆联），
    而 odds500_ouzhi_summary / odds500_match 的 match_id 为英文格式（2016-08-14_Arsenal_Liverpool FC），
    直接 match_id join 只能覆盖英文赛季（~36%）。本映射通过 odds500_match 自带的
    home_team_cn/away_team_cn 中文队名 + 比赛日期，为中文 match_id 找回其英文赔率键，将覆盖率提升至 ~96%。
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    bridge = {}
    cur.execute("SELECT match_id, match_date, home_team_cn, away_team_cn FROM odds500_match")
    for mid, md, h, a in cur.fetchall():
        if not mid:
            continue
        d = (md or "")[:10]
        nh = normalize_team_name(h)
        na = normalize_team_name(a)
        bridge[(d, nh, na)] = mid
    conn.close()
    return bridge


def load_model_predictions(csv_path: Path, model: str):
    """读取模型概率 CSV，返回 list[dict]。

    每条: {match_id, league, actual_direction, home, draw, away}
    model: mean | xgb | lgb
    """
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            match_id = r.get("match_id")
            if not match_id:
                continue
            actual = r.get("actual_result")
            if actual is None or actual == "":
                continue
            direction = RESULT_TO_DIRECTION.get(int(actual))
            if direction is None:
                continue

            def prob(prefix, d):
                col = f"{prefix}_platt_{d}"
                try:
                    return float(r[col])
                except (KeyError, ValueError):
                    return None

            if model in ("xgb", "mean"):
                xgb_h = prob("xgb", "home")
                xgb_d = prob("xgb", "draw")
                xgb_a = prob("xgb", "away")
            if model in ("lgb", "mean"):
                lgb_h = prob("lgb", "home")
                lgb_d = prob("lgb", "draw")
                lgb_a = prob("lgb", "away")

            if model == "xgb":
                h, d, a = xgb_h, xgb_d, xgb_a
            elif model == "lgb":
                h, d, a = lgb_h, lgb_d, lgb_a
            else:  # mean
                h = (xgb_h + lgb_h) / 2 if xgb_h is not None and lgb_h is not None else None
                d = (xgb_d + lgb_d) / 2 if xgb_d is not None and lgb_d is not None else None
                a = (xgb_a + lgb_a) / 2 if xgb_a is not None and lgb_a is not None else None

            if None in (h, d, a):
                continue
            # 归一化，保证和为 1
            total = h + d + a
            if total <= 0:
                continue
            rows.append({
                "match_id": match_id,
                "league": r.get("competition_name", ""),
                "date": (r.get("date") or "")[:10],
                "team_home": r.get("home_team_name") or "",
                "team_away": r.get("away_team_name") or "",
                "actual_direction": direction,
                "home": h / total,
                "draw": d / total,
                "away": a / total,
            })
    return rows


def load_market_odds(db_path: Path, odds_source: str):
    """读取市场赔率，返回 {match_id: (home, draw, away)}。

    odds_source: odds500_live(即盘均赔) | odds500_init(初盘均赔)
                | odds500_match(500.com 单场参考赔率) | wdl_history(竞彩终盘)
    """
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    odds = {}

    if odds_source == "odds500_match":
        cur.execute("SELECT match_id, win, draw, lost FROM odds500_match")
        for mid, w, d, l in cur.fetchall():
            h, dd, a = _f(w), _f(d), _f(l)
            if mid and None not in (h, dd, a):
                odds[mid] = (h, dd, a)
    elif odds_source in ("odds500_live", "odds500_init"):
        prefix = "avg_live" if odds_source == "odds500_live" else "avg_init"
        cols = f"{prefix}_win, {prefix}_draw, {prefix}_lose"
        cur.execute(f"SELECT match_id, {cols} FROM odds500_ouzhi_summary")
        for mid, w, d, l in cur.fetchall():
            h, dd, a = _f(w), _f(d), _f(l)
            if mid and None not in (h, dd, a):
                odds[mid] = (h, dd, a)
    elif odds_source == "wdl_history":
        # 取每场最后一个时间戳快照（近似终盘）
        cur.execute(
            "SELECT match_id_en, win_a, draw, win_b FROM wdl_history "
            "WHERE match_id_en IS NOT NULL AND match_id_en != '' ORDER BY timestamp"
        )
        for mid, w, d, b in cur.fetchall():
            h, dd, a = _f(w), _f(d), _f(b)
            if mid and None not in (h, dd, a):
                odds[mid] = (h, dd, a)  # 覆盖式取最后快照
    else:
        conn.close()
        raise ValueError(f"未知赔率源: {odds_source}")

    conn.close()
    return odds


def run_backtest(preds, odds_map, min_ev, kelly_strategy, kelly_cap):
    """逐场跑 EV 决策，返回结果列表（仅含已下注场次）+ 统计信息。"""
    results = []
    stats = {"total": len(preds), "with_odds": 0, "bet": 0, "no_odds": 0}

    for p in preds:
        o = odds_map.get(p.get("_odds_key") or p["match_id"])
        if o is None:
            stats["no_odds"] += 1
            continue
        stats["with_odds"] += 1

        try:
            analysis = analyze_match(
                probs=ModelProbabilities(home=p["home"], draw=p["draw"], away=p["away"]),
                odds=OddsData(home=o[0], draw=o[1], away=o[2]),
                ev_threshold=min_ev,
                kelly_strategy=kelly_strategy,
                kelly_cap=kelly_cap,
            )
        except Exception as e:
            continue

        # 选可投注方向：非 AVOID 中 EV 最高者（规避 best_direction 与 overall_decision 可能不一致的问题）
        candidates = [d for d in (analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis)
                      if d.decision != "AVOID"]
        if not candidates:
            continue
        bet_dir = max(candidates, key=lambda d: d.ev)

        won = (p["actual_direction"] == bet_dir.direction)
        odds_val = bet_dir.odds

        # 平注（1 单位）与凯利注
        flat_profit = (odds_val - 1.0) if won else -1.0
        stake_kelly = bet_dir.kelly_clipped
        kelly_profit = stake_kelly * (odds_val - 1.0) if won else -stake_kelly

        results.append({
            "match_id": p["match_id"],
            "league": p["league"],
            "date": p.get("date", ""),
            "season": season_from_date(p.get("date", "")),
            "direction": bet_dir.direction,
            "direction_cn": bet_dir.direction_cn,
            "odds": odds_val,
            "p_model": bet_dir.p_model,
            "p_market": bet_dir.p_market,
            "edge": bet_dir.edge,
            "ev": bet_dir.ev,
            "kelly": bet_dir.kelly_clipped,
            "decision": bet_dir.decision,
            "won": won,
            "flat_profit": flat_profit,
            "kelly_profit": kelly_profit,
        })
        stats["bet"] += 1

    return results, stats


def calc_baseline(preds, odds_map):
    """赔率基准：对所有有赔率场次「呆买」某方向 / 最低赔率方向的平注 ROI。

    用于判断 EV 信号是真 edge 还是赔率本身有偏（如即盘事后补采导致的水位失真）。
    返回 {home/draw/away/favorite: {'n','flat_roi','hit_rate'}}
    """
    dirs = {"home": [], "draw": [], "away": []}
    fav = []
    for p in preds:
        o = odds_map.get(p.get("_odds_key") or p["match_id"])
        if o is None:
            continue
        home_o, draw_o, away_o = o
        for d, odds_val in (("home", home_o), ("draw", draw_o), ("away", away_o)):
            won = (p["actual_direction"] == d)
            dirs[d].append((odds_val - 1.0) if won else -1.0)
        # 最低赔率方向（市场最看好）
        prob_map = {"home": 1 / home_o, "draw": 1 / draw_o, "away": 1 / away_o}
        fav_d = max(prob_map, key=prob_map.get)
        fav_odds = {"home": home_o, "draw": draw_o, "away": away_o}[fav_d]
        fav.append((fav_odds - 1.0) if p["actual_direction"] == fav_d else -1.0)

    out = {}
    for d, profits in dirs.items():
        n = len(profits)
        out[d] = {"n": n, "flat_roi": sum(profits) / n if n else 0.0,
                  "hit_rate": sum(1 for x in profits if x > 0) / n if n else 0.0}
    n = len(fav)
    out["favorite"] = {"n": n, "flat_roi": sum(fav) / n if n else 0.0,
                       "hit_rate": sum(1 for x in fav if x > 0) / n if n else 0.0}
    return out


def _roi_summary(results, key=None):
    """按 key 分组计算平注 ROI 与命中率。key=None 表示整体。"""
    groups = defaultdict(list)
    for r in results:
        groups[key(r) if key else "all"].append(r)

    out = {}
    for k, rs in groups.items():
        n = len(rs)
        if n == 0:
            continue
        wins = sum(1 for r in rs if r["won"])
        total_profit = sum(r["flat_profit"] for r in rs)
        total_stake = n  # 平注每注 1 单位
        kelly_profit = sum(r["kelly_profit"] for r in rs)
        kelly_stake = sum(r["kelly"] for r in rs)
        out[k] = {
            "n": n,
            "hits": wins,
            "hit_rate": wins / n,
            "flat_roi": total_profit / total_stake,
            "flat_profit": total_profit,
            "kelly_roi": kelly_profit / kelly_stake if kelly_stake > 0 else 0.0,
            "avg_ev": sum(r["ev"] for r in rs) / n,
            "avg_odds": sum(r["odds"] for r in rs) / n,
        }
    return out


def _edge_bucket(edge):
    for lo, hi, label in [(0.0, 0.03, "0~3pp"), (0.03, 0.06, "3~6pp"),
                          (0.06, 0.10, "6~10pp"), (0.10, 1.0, ">10pp")]:
        if lo <= edge < hi:
            return label
    return "unknown"


def generate_report(cfg, preds, odds_map, results, stats, summary_all, baseline):
    """生成 markdown + json 报告。"""
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    md_path = REPORT_DIR / f"ev_backtest_{ts}.md"
    json_path = REPORT_DIR / f"ev_backtest_{ts}.json"

    by_decision = _roi_summary(results, key=lambda r: r["decision"])
    by_direction = _roi_summary(results, key=lambda r: r["direction_cn"])
    by_league = _roi_summary(results, key=lambda r: r["league"])
    by_edge = _roi_summary(results, key=lambda r: _edge_bucket(r["edge"]))
    by_season = _roi_summary(results, key=lambda r: r["season"])
    by_season = {k: by_season[k] for k in sorted(by_season)}

    def table_rows(d):
        lines = []
        for k, v in d.items():
            lines.append(f"| {k} | {v['n']} | {v['hits']} | {v['hit_rate']*100:.1f}% | "
                         f"{v['flat_roi']*100:+.2f}% | {v['flat_profit']:+.2f} | "
                         f"{v['kelly_roi']*100:+.2f}% | {v['avg_ev']*100:+.2f}% |")
        return "\n".join(lines)

    s = summary_all.get("all", {})
    lines = []
    lines.append("# EV 期望值引擎历史回测报告\n")
    lines.append(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
    lines.append("## 一、配置\n")
    lines.append(f"- 模型概率: {cfg.model}")
    lines.append(f"- 赔率源: {cfg.odds_source}")
    lines.append(f"- EV 阈值: {cfg.min_ev}   凯利策略: {cfg.kelly_strategy}   仓位上限: {cfg.kelly_cap}\n")
    lines.append("## 二、数据概览\n")
    lines.append(f"- 全量预测场次: {stats['total']}")
    lines.append(f"- 有赔率场次: {stats['with_odds']}（无赔率: {stats['no_odds']}）")
    lines.append(f"- 触发投注场次: {stats['bet']}（VALUE+MARGINAL）\n")
    lines.append("## 三、整体结果（平注每注 1 单位）\n")
    lines.append("| 指标 | 值 |\n|---|---|")
    lines.append(f"| 投注场次 | {s.get('n', 0)} |")
    lines.append(f"| 命中数 | {s.get('hits', 0)} |")
    lines.append(f"| 命中率 | {s.get('hit_rate', 0)*100:.1f}% |")
    lines.append(f"| 平注盈亏 | {s.get('flat_profit', 0):+.2f} 单位 |")
    lines.append(f"| 平注 ROI | {s.get('flat_roi', 0)*100:+.2f}% |")
    lines.append(f"| 凯利 ROI | {s.get('kelly_roi', 0)*100:+.2f}% |")
    lines.append(f"| 平均 EV | {s.get('avg_ev', 0)*100:+.2f}% |")
    lines.append(f"| 平均赔率 | {s.get('avg_odds', 0):.2f} |\n")
    lines.append("## 四、赔率基准（呆买某方向，判断赔率是否有偏）\n")
    lines.append("| 基准 | 场次 | 命中率 | 平注ROI |\n|---|---|---|---|")
    for bname, bv in baseline.items():
        lines.append(f"| {bname} | {bv['n']} | {bv['hit_rate']*100:.1f}% | {bv['flat_roi']*100:+.2f}% |")
    lines.append("\n## 五、按决策分层\n")
    lines.append("| 决策 | 场次 | 命中 | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV |\n|---|---|---|---|---|---|---|---|")
    lines.append(table_rows(by_decision) + "\n")
    lines.append("## 六、按投注方向\n")
    lines.append("| 方向 | 场次 | 命中 | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV |\n|---|---|---|---|---|---|---|---|")
    lines.append(table_rows(by_direction) + "\n")
    lines.append("## 七、按联赛\n")
    lines.append("| 联赛 | 场次 | 命中 | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV |\n|---|---|---|---|---|---|---|---|")
    lines.append(table_rows(by_league) + "\n")
    lines.append("## 八、按 edge 分桶\n")
    lines.append("| edge | 场次 | 命中 | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV |\n|---|---|---|---|---|---|---|---|")
    lines.append(table_rows(by_edge) + "\n")
    lines.append("## 九、按赛季（分赛季 ROI 拆解）\n")
    lines.append("| 赛季 | 场次 | 命中 | 命中率 | 平注ROI | 平注盈亏 | 凯利ROI | 平均EV |\n|---|---|---|---|---|---|---|---|")
    lines.append(table_rows(by_season) + "\n")
    lines.append("---\n*本报告由 ev_backtest.py 自动生成*\n")

    REPORT_DIR.mkdir(exist_ok=True)
    md_path.write_text("\n".join(lines), encoding="utf-8")

    def conv(o):
        if isinstance(o, dict):
            return {k: conv(v) for k, v in o.items()}
        if isinstance(o, list):
            return [conv(v) for v in o]
        return o

    payload = {
        "config": vars(cfg),
        "stats": stats,
        "summary": summary_all,
        "baseline": baseline,
        "by_decision": by_decision,
        "by_direction": by_direction,
        "by_league": by_league,
        "by_edge": by_edge,
        "by_season": by_season,
        "bets": results,
    }
    json_path.write_text(json.dumps(conv(payload), ensure_ascii=False, indent=2), encoding="utf-8")

    return md_path, json_path


def main():
    parser = argparse.ArgumentParser(description="EV 期望值引擎历史回测")
    parser.add_argument("--csv", default=str(CSV_PATH))
    parser.add_argument("--db", default=str(DB_PATH))
    parser.add_argument("--model", choices=["mean", "xgb", "lgb"], default="mean")
    parser.add_argument("--odds-source", choices=["odds500_live", "odds500_init", "odds500_match", "wdl_history"],
                        default="odds500_live")
    parser.add_argument("--min-ev", type=float, default=0.02)
    parser.add_argument("--kelly-strategy", choices=["full", "half", "quarter"], default="quarter")
    parser.add_argument("--kelly-cap", type=float, default=0.25)
    args = parser.parse_args()

    print("加载模型概率...")
    preds = load_model_predictions(Path(args.csv), args.model)
    print(f"  模型预测场次: {len(preds)} (model={args.model})")

    print("加载市场赔率...")
    odds_map = load_market_odds(Path(args.db), args.odds_source)
    print(f"  赔率记录数: {len(odds_map)} (source={args.odds_source})")

    # 桥接中文 match_id → 英文赔率键（覆盖 2016~2023 中文赛季）
    bridge = build_team_bridge(Path(args.db))
    bridged = 0
    for p in preds:
        key = p["match_id"]
        if key not in odds_map:
            alt = bridge.get((p.get("date"), normalize_team_name(p.get("team_home", "")),
                              normalize_team_name(p.get("team_away", ""))))
            if alt and alt in odds_map:
                key = alt
                bridged += 1
        p["_odds_key"] = key
    print(f"  经队名桥接解析出赔率的中文场次: {bridged}")

    print("运行 EV 回测...")
    results, stats = run_backtest(preds, odds_map, args.min_ev, args.kelly_strategy, args.kelly_cap)
    summary_all = _roi_summary(results)
    baseline = calc_baseline(preds, odds_map)

    print("\n" + "=" * 60)
    print(f"有赔率场次: {stats['with_odds']}  触发投注: {stats['bet']}")
    s = summary_all.get("all", {})
    if s:
        print(f"命中率: {s['hit_rate']*100:.1f}%  平注ROI: {s['flat_roi']*100:+.2f}%  "
              f"凯利ROI: {s['kelly_roi']*100:+.2f}%  平均EV: {s['avg_ev']*100:+.2f}%")
    print("赔率基准(呆买): " + "  ".join(
        f"{k}={v['flat_roi']*100:+.1f}%" for k, v in baseline.items()))
    print("=" * 60)

    by_season = _roi_summary(results, key=lambda r: r["season"])
    print("\n分赛季 ROI 拆解（平注）:")
    for s in sorted(by_season):
        v = by_season[s]
        print(f"  {s:>7} | n={v['n']:>5} | 命中率 {v['hit_rate']*100:5.1f}% | "
              f"平注ROI {v['flat_roi']*100:+6.2f}% | 平均EV {v['avg_ev']*100:+6.2f}% | 盈亏 {v['flat_profit']:+8.2f}")

    md_path, json_path = generate_report(args, preds, odds_map, results, stats, summary_all, baseline)
    print(f"\n报告: {md_path}")
    print(f"数据: {json_path}")


if __name__ == "__main__":
    main()