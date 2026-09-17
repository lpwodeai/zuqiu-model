# -*- coding: utf-8 -*-
"""
3 场已完赛 · 赛后结果补齐 + 预测命中率计算
==========================================
1. 补齐 odds.db.matches.actual_handicap（让球胜平负，此前为 NULL）
   根据 matches.handicap 与 actual_score 计算：
     adjusted = home_goals + handicap
     胜(上盘赢) / 平(走水) / 负(下盘赢)
2. 计算预测命中率：model_predictions（预测概率） JOIN matches（赛果）
   三个维度：胜平负(WDL) / 让球(HC) / 总进球(TG, 2.5 盘)

说明：model_predictions 仅存预测概率（无 actual 列），赛后结果落库在
      matches.actual_* 字段；命中率通过两表 join 计算。
数据范围：match_date = '2026-08-22'
"""
import sys
from datetime import datetime
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
ODDS_DB = BASE / "data" / "odds.db"
sys.path.insert(0, str(BASE))
from db_utils import connect  # noqa: E402


def actual_handicap_label(handicap, home_goals, away_goals):
    """让球胜平负（home 视角）：胜/平/负。handicap 为 home 让球盘口。"""
    adjusted = home_goals + handicap
    if adjusted > away_goals:
        return "胜"      # 上盘赢
    if adjusted < away_goals:
        return "负"      # 下盘赢
    return "平"          # 走水


HC_LABEL = {"胜": "上盘赢", "平": "走水", "负": "下盘赢"}


def argmax(items):
    """items: [(label, prob), ...] 返回概率最大的 label；平局优先取前面的。"""
    return max(items, key=lambda x: x[1])[0]


def load_predictions(cur, match_id, model_name):
    rows = cur.execute(
        "SELECT prediction_type, probability FROM model_predictions "
        "WHERE match_id=? AND model_name=?",
        (match_id, model_name),
    ).fetchall()
    return {pt: prob for pt, prob in rows}


def main():
    now = datetime.now().isoformat(sep=" ")
    conn = connect(db_path=ODDS_DB)
    cur = conn.cursor()

    # 只处理 08-22 这 3 场
    matches = cur.execute(
        "SELECT match_id, home_team, away_team, handicap, actual_wdl, "
        "actual_score, actual_total_goals "
        "FROM matches WHERE match_date='2026-08-22' ORDER BY match_id"
    ).fetchall()

    model_name = "generate_unified_report_v2.0"

    print("=" * 78)
    print("赛后结果补齐 + 预测命中率")
    print("=" * 78)

    for m in matches:
        match_id, home, away, handicap, actual_wdl, actual_score, actual_tg = m
        hg, ag = (int(x) for x in actual_score.split("-"))
        ah = actual_handicap_label(handicap, hg, ag)
        # 补齐 actual_handicap
        cur.execute(
            "UPDATE matches SET actual_handicap=?, updated_at=? WHERE match_id=?",
            (ah, now, match_id),
        )

        preds = load_predictions(cur, match_id, model_name)

        # 胜平负
        wdl_pred = argmax([
            ("主胜", preds.get("WDL_home") or 0),
            ("平", preds.get("WDL_draw") or 0),
            ("客胜", preds.get("WDL_away") or 0),
        ])
        wdl_hit = wdl_pred == actual_wdl

        # 让球
        hc_pred = argmax([
            ("上盘赢", preds.get("HC_upper") or 0),
            ("走水", preds.get("HC_draw") or 0),
            ("下盘赢", preds.get("HC_lower") or 0),
        ])
        hc_actual = HC_LABEL[ah]
        hc_hit = hc_pred == hc_actual

        # 总进球 (2.5 盘)
        tg_pred = argmax([
            ("大球", preds.get("TG_over_2_5") or 0),
            ("小球", preds.get("TG_under_2_5") or 0),
        ])
        tg_actual = "大球" if actual_tg >= 3 else "小球"
        tg_hit = tg_pred == tg_actual

        print(f"\n[{match_id}]")
        print(f"  {home} vs {away}  盘口 {handicap:+.1f}  比分 {actual_score}")
        print(f"  胜平负  预测={wdl_pred:<3} 实际={actual_wdl:<3} {'✓命中' if wdl_hit else '✗未中'}")
        print(f"  让球    预测={hc_pred:<4} 实际={hc_actual:<4}('{ah}') {'✓命中' if hc_hit else '✗未中'}")
        print(f"  总进球  预测={tg_pred:<3} 实际={tg_actual:<3}({actual_tg}球) {'✓命中' if tg_hit else '✗未中'}")

    conn.commit()

    # 汇总
    dims = defaultdict(lambda: [0, 0])  # dim -> [hit, total]
    for m in matches:
        match_id, home, away, handicap, actual_wdl, actual_score, actual_tg = m
        hg, ag = (int(x) for x in actual_score.split("-"))
        ah = actual_handicap_label(handicap, hg, ag)
        preds = load_predictions(cur, match_id, model_name)

        wdl_pred = argmax([
            ("主胜", preds.get("WDL_home") or 0),
            ("平", preds.get("WDL_draw") or 0),
            ("客胜", preds.get("WDL_away") or 0)])
        dims["胜平负"][1] += 1
        dims["胜平负"][0] += 1 if wdl_pred == actual_wdl else 0

        hc_pred = argmax([
            ("上盘赢", preds.get("HC_upper") or 0),
            ("走水", preds.get("HC_draw") or 0),
            ("下盘赢", preds.get("HC_lower") or 0)])
        dims["让球"][1] += 1
        dims["让球"][0] += 1 if hc_pred == HC_LABEL[ah] else 0

        tg_pred = argmax([
            ("大球", preds.get("TG_over_2_5") or 0),
            ("小球", preds.get("TG_under_2_5") or 0)])
        tg_actual = "大球" if actual_tg >= 3 else "小球"
        dims["总进球"][1] += 1
        dims["总进球"][0] += 1 if tg_pred == tg_actual else 0

    print("\n" + "=" * 78)
    print("命中率汇总（3 场）")
    print("=" * 78)
    total_hit = total_n = 0
    for dim in ("胜平负", "让球", "总进球"):
        hit, n = dims[dim]
        total_hit += hit
        total_n += n
        print(f"  {dim:<6} : {hit}/{n} = {hit/n*100:.1f}%")
    print(f"  {'合计':<6} : {total_hit}/{total_n} = {total_hit/total_n*100:.1f}%")

    conn.close()


if __name__ == "__main__":
    main()