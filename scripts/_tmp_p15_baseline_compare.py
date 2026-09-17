# -*- coding: utf-8 -*-
"""P1-5 基线对比：贝叶斯层级 vs 朴素 Poisson vs 逐队 MLE（无正则）"""
import sys, os, math
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bayesian_hierarchical_model import (
    load_match_data, compute_rps, LEAGUES, BayesianHierarchicalModel, _tau_dixon_coles,
)


def wdl_from_lambda(lam_home, lam_away, rho=0.0, max_goals=10):
    win = draw = lose = 0.0
    for i in range(max_goals + 1):
        pi = math.exp(-lam_home) * lam_home ** i / math.factorial(i)
        for j in range(max_goals + 1):
            pj = math.exp(-lam_away) * lam_away ** j / math.factorial(j)
            p = pi * pj
            if rho != 0.0:
                tau = float(_tau_dixon_coles(
                    np.array([i]), np.array([j]), np.array([lam_home]), np.array([lam_away]), rho)[0])
                p *= tau
            if i > j:
                win += p
            elif i == j:
                draw += p
            else:
                lose += p
    tot = win + draw + lose
    return [lose / tot, draw / tot, win / tot]  # [客胜, 平, 主胜]


def main():
    df = load_match_data()
    all_true, all_bayes, all_const, all_mle = [], [], [], []

    for lg in LEAGUES:
        lg_df = df[df["competition_name"] == lg].sort_values("date").reset_index(drop=True)
        split = int(len(lg_df) * 0.8)
        train_df, test_df = lg_df.iloc[:split], lg_df.iloc[split:]

        # 已保存的贝叶斯模型
        model = BayesianHierarchicalModel.load(
            os.path.join(os.path.dirname(os.path.abspath(__file__)),
                         "..", "assets", f"bayesian_model_{lg}.json"))

        # 常数泊松基线（联赛平均）
        lam_home_const = float(train_df["homeGoals"].mean())
        lam_away_const = float(train_df["awayGoals"].mean())

        # 逐队 MLE（无正则、无 DC）：attack=场均进球-联赛均值, defense=场均失球-联赛均值
        lg_mean_goals = float((train_df["homeGoals"].mean() + train_df["awayGoals"].mean()) / 2)
        teams = sorted(set(train_df["home_team_name"]).union(set(train_df["away_team_name"])))
        scored = {t: 0.0 for t in teams}
        conceded = {t: 0.0 for t in teams}
        games = {t: 0 for t in teams}
        for _, r in train_df.iterrows():
            h, a = r["home_team_name"], r["away_team_name"]
            hg, ag = r["homeGoals"], r["awayGoals"]
            scored[h] += hg; conceded[h] += ag; games[h] += 1
            scored[a] += ag; conceded[a] += hg; games[a] += 1
        att_mle = {t: (scored[t] / max(games[t], 1)) - lg_mean_goals for t in teams}
        def_mle = {t: (conceded[t] / max(games[t], 1)) - lg_mean_goals for t in teams}

        y_true = []
        for _, r in test_df.iterrows():
            h, a = r["home_team_name"], r["away_team_name"]
            y_true.append(int(r["result"]))
            # 贝叶斯
            bw = model.predict_wdl(h, a)
            all_bayes.append([bw["lose"], bw["draw"], bw["win"]])
            # 常数泊松
            all_const.append(wdl_from_lambda(lam_home_const, lam_away_const))
            # 逐队 MLE（无正则泊松）
            att_h = att_mle.get(h, 0.0); def_h = def_mle.get(h, 0.0)
            att_a = att_mle.get(a, 0.0); def_a = def_mle.get(a, 0.0)
            lh = float(np.exp(lg_mean_goals + att_h - def_a))
            la = float(np.exp(lg_mean_goals + att_a - def_h))
            all_mle.append(wdl_from_lambda(lh, la))
        all_true.extend(y_true)

    y = np.array(all_true)
    for name, probs in [("贝叶斯层级", all_bayes), ("朴素常数泊松", all_const), ("逐队MLE泊松(无正则)", all_mle)]:
        p = np.array(probs)
        pred = np.argmax(p, axis=1)
        acc = float(np.mean(pred == y))
        rps = compute_rps(y, p)
        print(f"  {name:<22} 聚合RPS={rps:.4f}  Acc={acc:.3f}  n={len(y)}")


if __name__ == "__main__":
    main()