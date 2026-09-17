"""
Dixon-Coles / ZIP 独立验证 (P2-02 附)
对比 Poisson / Dixon-Coles / ZIP 三种比分模型的 WDL 表现和低比分校准。

用法:
  python scripts/dixon_coles_validation.py --league 英超
  python scripts/dixon_coles_validation.py --all-leagues
"""

import argparse
import os
import sys
import sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from prediction_core import CalcEngine

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "odds.db")


def load_historical_matches(conn, league=None, season="26/27", limit=None):
    sql = """
        SELECT fid, league, home_score, away_score, win, draw, lost
        FROM odds500_match
        WHERE status IN (2, 5)
          AND home_score IS NOT NULL AND away_score IS NOT NULL
          AND season = ?
    """
    args = [season]
    if league:
        sql += " AND league = ?"
        args.append(league)
    sql += " ORDER BY match_date, match_time"
    if limit:
        sql += f" LIMIT {int(limit)}"
    return conn.execute(sql, args).fetchall()


def build_odds_data(row):
    if row["win"] and row["draw"] and row["lost"]:
        rec = {"win": row["win"], "draw": row["draw"], "lose": row["lost"]}
    else:
        rec = {"win": 2.0, "draw": 3.4, "lose": 3.0}
    return {
        "wdl_odds": {"open": rec, "close": rec, "records": [rec], "source": "500"},
        "tg_odds": {"records": [], "source": "none"},
    }


def compute_wdl(lambda_h, lambda_a, model="poisson", rho=-0.30, pi0=0.1):
    if model == "poisson":
        r = CalcEngine.calc_win_draw_lose_poisson(lambda_h, lambda_a)
    elif model == "dixon_coles":
        r = CalcEngine.calc_win_draw_lose_dixon_coles(lambda_h, lambda_a, rho=rho)
    elif model == "zip":
        win_a = draw = win_b = 0.0
        for i in range(11):
            for j in range(11):
                ph = pi0 if i == 0 else (1 - pi0) * CalcEngine.poisson_pmf(lambda_h, i)
                pa = pi0 if j == 0 else (1 - pi0) * CalcEngine.poisson_pmf(lambda_a, j)
                p = ph * pa
                if i > j:
                    win_a += p
                elif i == j:
                    draw += p
                else:
                    win_b += p
        total = win_a + draw + win_b
        r = {"win": win_a / total, "draw": draw / total, "lose": win_b / total}
    else:
        raise ValueError(model)
    return r["win"], r["draw"], r["lose"]


def p_total_le1(lambda_h, lambda_a, model="poisson", rho=-0.30, pi0=0.1):
    """P(总进球 <= 1)"""
    p = 0.0
    for i in range(0, 2):
        for j in range(0, 2 - i):
            if model == "poisson":
                p += CalcEngine.poisson_pmf(lambda_h, i) * CalcEngine.poisson_pmf(lambda_a, j)
            elif model == "dixon_coles":
                tau = CalcEngine.dixon_coles_correction(i, j, lambda_h, lambda_a, rho=rho) if i + j <= 1 else 1.0
                p += CalcEngine.poisson_pmf(lambda_h, i) * CalcEngine.poisson_pmf(lambda_a, j) * tau
            elif model == "zip":
                ph = pi0 if i == 0 else (1 - pi0) * CalcEngine.poisson_pmf(lambda_h, i)
                pa = pi0 if j == 0 else (1 - pi0) * CalcEngine.poisson_pmf(lambda_a, j)
                p += ph * pa
    return p


def brier(probs, result_idx):
    y = [0.0, 0.0, 0.0]
    y[result_idx] = 1.0
    return sum((p - yi) ** 2 for p, yi in zip(probs, y))


def ece(probs_list, results_list, n_bins=10):
    bins = [[] for _ in range(n_bins)]
    for probs, r in zip(probs_list, results_list):
        conf = max(probs)
        bin_idx = min(int(conf * n_bins), n_bins - 1)
        correct = (probs.index(conf) == r)
        bins[bin_idx].append((conf, correct))
    ece_val = 0.0
    total = sum(len(b) for b in bins)
    for b in bins:
        if not b:
            continue
        avg_conf = sum(x[0] for x in b) / len(b)
        acc = sum(1 for x in b if x[1]) / len(b)
        ece_val += len(b) / total * abs(acc - avg_conf)
    return ece_val


def evaluate(matches, model="poisson", **kwargs):
    correct = total = 0
    brier_sum = 0.0
    probs_list = []
    results_list = []
    low_score_pairs = []

    for row in matches:
        try:
            odds_data = build_odds_data(row)
            lh_raw, la_raw = CalcEngine.calc_lambda_from_odds(odds_data)
            lh, la, _ = CalcEngine.adjust_lambda_for_mid_score(lh_raw, la_raw, odds_data)
            w, d, l = compute_wdl(lh, la, model=model, **kwargs)
        except Exception:
            continue

        hs, as_ = row["home_score"], row["away_score"]
        result = 0 if hs > as_ else (1 if hs == as_ else 2)
        probs = [w, d, l]
        pred = probs.index(max(probs))

        total += 1
        if pred == result:
            correct += 1
        brier_sum += brier(probs, result)
        probs_list.append(probs)
        results_list.append(result)
        low_score_pairs.append((p_total_le1(lh, la, model=model, **kwargs), 1 if hs + as_ <= 1 else 0))

    if total == 0:
        return None

    low_actual = sum(x[1] for x in low_score_pairs) / len(low_score_pairs)
    low_pred_avg = sum(x[0] for x in low_score_pairs) / len(low_score_pairs)

    # 低比分校准 MAE（分桶）
    bins_low = {}
    for p, y in low_score_pairs:
        b_idx = int(p * 10) / 10.0
        bins_low.setdefault(b_idx, []).append((p, y))
    low_mae = 0.0
    cnt = 0
    for b in sorted(bins_low.keys()):
        if len(bins_low[b]) >= 5:
            avg_p = sum(x[0] for x in bins_low[b]) / len(bins_low[b])
            avg_y = sum(x[1] for x in bins_low[b]) / len(bins_low[b])
            low_mae += abs(avg_p - avg_y) * len(bins_low[b])
            cnt += len(bins_low[b])
    low_mae = low_mae / cnt if cnt > 0 else None

    return {
        "total": total,
        "accuracy": correct / total,
        "brier": brier_sum / total,
        "ece": ece(probs_list, results_list),
        "low_actual": low_actual,
        "low_pred_avg": low_pred_avg,
        "low_bias": low_pred_avg - low_actual,
        "low_calib_mae": low_mae,
    }


def print_row(label, m):
    if m is None:
        print(f"  {label:22s}  (无数据)")
        return
    print(f"  {label:22s}  Acc={m['accuracy']*100:5.2f}%  "
          f"Brier={m['brier']:.4f}  ECE={m['ece']:.4f}  "
          f"低比分偏差={m['low_bias']*100:+.2f}pp  "
          f"(预{m['low_pred_avg']*100:.1f}% / 实{m['low_actual']*100:.1f}%)")


def run_league(conn, league):
    matches = load_historical_matches(conn, league=league)
    if not matches:
        print(f"\n[{league}] 无已赛数据")
        return

    print(f"\n{'='*72}")
    print(f"  {league}  已赛: {len(matches)} 场")
    print(f"{'='*72}")

    m_pois = evaluate(matches, model="poisson")
    m_dc = evaluate(matches, model="dixon_coles", rho=-0.30)
    m_zip = evaluate(matches, model="zip", pi0=0.08)

    print_row("Poisson (基准)", m_pois)
    print_row("Dixon-Coles rho=-0.30", m_dc)
    print_row("ZIP pi=0.08", m_zip)

    if m_pois and m_dc:
        bg = m_pois["brier"] - m_dc["brier"]
        ag = m_dc["accuracy"] - m_pois["accuracy"]
        print(f"\n  DC vs Poisson: Brier{'改善' if bg > 0 else '恶化'} {abs(bg)*10000:.1f}e-4, "
              f"准确率{'+' if ag >= 0 else ''}{ag*100:.2f}pp")
    if m_pois and m_zip:
        bg = m_pois["brier"] - m_zip["brier"]
        ag = m_zip["accuracy"] - m_pois["accuracy"]
        print(f"  ZIP vs Poisson: Brier{'改善' if bg > 0 else '恶化'} {abs(bg)*10000:.1f}e-4, "
              f"准确率{'+' if ag >= 0 else ''}{ag*100:.2f}pp")


def main():
    parser = argparse.ArgumentParser(description="Dixon-Coles / ZIP 独立验证 (P2-02)")
    parser.add_argument("--league", type=str, default=None)
    parser.add_argument("--all-leagues", action="store_true")
    parser.add_argument("--db", default=DB_PATH)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    conn.row_factory = sqlite3.Row

    leagues = ["英超", "西甲", "意甲", "德甲", "法甲"] if args.all_leagues else (
        [args.league] if args.league else ["英超"]
    )

    for lg in leagues:
        run_league(conn, lg)

    conn.close()
    print(f"\n结论参考: 若 Brier 改善 < 5e-4 或准确率提升 < 0.5pp，说明低比分修正收益有限，保持 P2 待办。")


if __name__ == "__main__":
    main()

