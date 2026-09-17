"""
P1-13 λ 链路复盘脚本
赛后拿真实比分/xG 反推赛前 λ 计算链路哪一步错了，辅助翻车定位。

用法：
  python scripts/lambda_trace_review.py --match_id MATCH_ID --home_xg 1.2 --away_xg 0.8
"""

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import sqlite3
from prediction_core import CalcEngine, odds_to_implied_prob


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds.db")


def load_match(match_id):
    """从 odds.db 拼装 odds_data + 真实 xG。"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    # WDL 时序赔率
    wdl_rows = cur.execute(
        "SELECT timestamp, win_a, draw, win_b FROM wdl_history WHERE match_id = ? ORDER BY timestamp",
        (match_id,),
    ).fetchall()
    if not wdl_rows:
        conn.close()
        raise ValueError(f"未找到 match_id={match_id} 的 WDL 赔率数据")

    records = []
    for r in wdl_rows:
        records.append({
            "timestamp": r["timestamp"],
            "win": r["win_a"],
            "draw": r["draw"],
            "lose": r["win_b"],
        })
    wdl_odds = {
        "open": records[0],
        "close": records[-1],
        "records": records,
        "source": "sporttery_wdl",
    }

    # TG 大小球赔率（可选）
    tg_records = []
    try:
        tg_rows = cur.execute(
            "SELECT timestamp, over_2_5, under_2_5 FROM tg_history WHERE match_id = ? ORDER BY timestamp",
            (match_id,),
        ).fetchall()
        for r in tg_rows:
            tg_records.append({
                "timestamp": r["timestamp"],
                "goals": {"2.5": r["over_2_5"]},
            })
    except Exception:
        pass
    tg_odds = {"records": tg_records, "source": "sporttery_tg"} if tg_records else {"records": [], "source": "none"}

    # 聚合球员 xG → 比赛级真实 xG
    real_xg = {}
    try:
        rows = cur.execute(
            "SELECT team, SUM(xg) as total_xg FROM match_player_stats WHERE match_id = ? AND xg IS NOT NULL GROUP BY team",
            (match_id,),
        ).fetchall()
        for r in rows:
            real_xg[r["team"]] = r["total_xg"]
    except Exception:
        pass

    conn.close()
    odds_data = {"wdl_odds": wdl_odds, "tg_odds": tg_odds, "match_id": match_id}
    return odds_data, real_xg



def run_trace(match_id, home_xg=None, away_xg=None, home_score=None, away_score=None):
    odds_data, real_xg = load_match(match_id)

    # 原始 λ
    lambda_raw_h, lambda_raw_a = CalcEngine.calc_lambda_from_odds(odds_data)
    # A-002 调整（含完整 trace）
    lambda_adj_h, lambda_adj_a, trace = CalcEngine.adjust_lambda_for_mid_score(
        lambda_raw_h, lambda_raw_a, odds_data
    )
    trace["raw"] = {"lambda_home": round(lambda_raw_h, 4), "lambda_away": round(lambda_raw_a, 4)}

    # 泊松 + DC 对比
    wdl_poisson = CalcEngine.calc_win_draw_lose_poisson(lambda_adj_h, lambda_adj_a)
    wdl_dc = CalcEngine.calc_win_draw_lose_dixon_coles(lambda_adj_h, lambda_adj_a)

    # 真实值
    xg_vals = list(real_xg.values())
    real_home_xg = home_xg if home_xg is not None else (xg_vals[0] if len(xg_vals) >= 1 else None)
    real_away_xg = away_xg if away_xg is not None else (xg_vals[1] if len(xg_vals) >= 2 else None)

    sep = "=" * 72
    print(sep)
    print(f"  λ 链路复盘  |  match_id = {match_id}")
    print(sep)

    print("\n【输入】赔率隐含概率（收盘 WDL）")
    last = odds_data["wdl_odds"]["records"][-1]
    hp, dp, ap = odds_to_implied_prob(last["win"], last["draw"], last["lose"])
    print(f"  主胜: {last['win']:.3f}  平: {last['draw']:.3f}  客胜: {last['lose']:.3f}")
    print(f"  隐含: 主={hp:.3f}  平={dp:.3f}  客={ap:.3f}  合计={hp+dp+ap:.3f}")

    print(f"\n【Step 0】原始 λ（avg_goals × 隐含胜概率）")
    print(f"  λ_home_raw = {lambda_raw_h:.4f}")
    print(f"  λ_away_raw = {lambda_raw_a:.4f}")
    print(f"  合计 = {lambda_raw_h + lambda_raw_a:.4f}")

    s1 = trace["stage1_wdl"]
    print(f"\n【Stage 1】WDL 缩放  (source: {s1['source']})")
    print(f"  P(主胜)={s1['win_home']:.4f} → scale_home = {s1['scale_home']:.4f}  [0.7~1.8]")
    print(f"  P(客胜)={s1['win_away']:.4f} → scale_away = {s1['scale_away']:.4f}  [0.7~1.8]")
    print(f"  λ_home_s1 = {s1['lambda_after_s1_home']:.4f}")
    print(f"  λ_away_s1 = {s1['lambda_after_s1_away']:.4f}")

    s2 = trace["stage2_tg"]
    print(f"\n【Stage 2】TG 总进球缩放  (source: {s2['source']})")
    print(f"  base_total (S1后) = {s2['base_total']:.4f}")
    if s2["tg_expected"] is not None:
        print(f"  tg_expected (赔率隐含) = {s2['tg_expected']:.4f}")
        print(f"  tg_scale = {s2['scale']:.4f}  [0.85~1.4]")
    else:
        print(f"  无 TG 赔率，tg_scale = 1.0")

    final = trace["final"]
    print(f"\n【最终 λ】")
    print(f"  λ_home = {final['lambda_home']:.4f}")
    print(f"  λ_away = {final['lambda_away']:.4f}")
    print(f"  主客差 = {final['diff']:.4f}  {'[ALERT] 告警' if final['alert_triggered'] else '（正常）'}")

    print(f"\n【衍生概率】Poisson vs Dixon-Coles (rho=-0.30)")
    print(f"               主胜     平       客胜")
    print(f"  Poisson:     {wdl_poisson['win']*100:5.2f}%  {wdl_poisson['draw']*100:5.2f}%  {wdl_poisson['lose']*100:5.2f}%")
    print(f"  DixonColes:  {wdl_dc['win']*100:5.2f}%  {wdl_dc['draw']*100:5.2f}%  {wdl_dc['lose']*100:5.2f}%")
    dc_gap = (wdl_dc["draw"] - wdl_poisson["draw"]) * 100
    print(f"  DC平局修正: {dc_gap:+.2f}pp")

    # 真实值对比 + 归因
    has_real = (real_home_xg is not None and real_away_xg is not None) or (home_score is not None)
    if has_real:
        print(f"\n【真实结果 vs 预测】")
        if home_score is not None and away_score is not None:
            print(f"  真实比分: {home_score} - {away_score}")
        if real_home_xg is not None and real_away_xg is not None:
            err_h = final["lambda_home"] - real_home_xg
            err_a = final["lambda_away"] - real_away_xg
            total_err = abs(err_h) + abs(err_a)
            print(f"  真实 xG:    {real_home_xg:.3f} - {real_away_xg:.3f}")
            print(f"  预测 λ:     {final['lambda_home']:.3f} - {final['lambda_away']:.3f}")
            print(f"  偏差:       {err_h:+.3f}   {err_a:+.3f}   (MAE合计: {total_err:.3f})")

            print(f"\n【偏差归因】每一步对 MAE 的影响")
            mae_raw = abs(lambda_raw_h - real_home_xg) + abs(lambda_raw_a - real_away_xg)
            mae_s1 = abs(s1["lambda_after_s1_home"] - real_home_xg) + abs(s1["lambda_after_s1_away"] - real_away_xg)
            mae_final = total_err
            print(f"  Step 0 (raw):  MAE = {mae_raw:.3f}")
            print(f"  Stage 1 (WDL): MAE = {mae_s1:.3f}   {'改善' if mae_s1 < mae_raw else '恶化'}")
            print(f"  Stage 2 (TG):  MAE = {mae_final:.3f}   {'改善' if mae_final < mae_s1 else '恶化'}")

    print(f"\n【trace JSON】")
    print(json.dumps(trace, ensure_ascii=False, indent=2))
    print(sep)
    return trace


def main():
    parser = argparse.ArgumentParser(description="λ 链路复盘")
    parser.add_argument("--match_id", required=True)
    parser.add_argument("--home_xg", type=float, default=None)
    parser.add_argument("--away_xg", type=float, default=None)
    parser.add_argument("--home_score", type=int, default=None)
    parser.add_argument("--away_score", type=int, default=None)
    args = parser.parse_args()

    try:
        run_trace(args.match_id, args.home_xg, args.away_xg, args.home_score, args.away_score)
    except Exception as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
