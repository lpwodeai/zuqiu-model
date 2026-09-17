# -*- coding: utf-8 -*-
"""C4 升班马盲区消融验证（降级版：is_promoted 标识 + 联赛自适应升班马前期先验）

背景（模型改进实施方案 v1.0 §五/C4）：
  「_empty_features() 全 0 特征，升班马盲区」。库内无西乙/英冠/意乙次级联赛数据源，故降级
  为「升班马标记 + 先验改善」：不引入外部数据，用现有五大联赛历史里升班马球队的真实首秀
  赛季表现作为联赛自适应先验，为升班马赛季前期（games_played < 5、无本队史）提供更贴合
  的默认值，而非泛化的联赛均线 / 全 0。

严格时序防泄漏：
  - TimeSeriesSplit（n_splits=4，经典前序切分，无 shuffle）
  - 升班马先验只从**该折训练段**的升班马球队统计得出，再应用于该折测试段，杜绝未来回填泄漏
  - 球队滚动状态特征只含该场 date 之前的场次（bisect_left 严格前序）

对比口径：
  - baseline：升班马球队无本队史时用联赛均线默认（0.5/1.3），无 is_promoted 标识
  - augmented：+ home/away_is_promoted + home/away_prom_prior_winrate/goals，并覆写前期状态先验
  - 报告：全量 + 升班马子集的 RPS 与 WDL 准确率（Acc）

用法： python scripts/c4_promoted_ablation.py
产物： reports/c4_promoted_ablation_results.json + console 摘要
"""
import json
import sqlite3
from bisect import bisect_left
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.model_selection import TimeSeriesSplit

try:
    import lightgbm as lgb
    LGB_OK = True
except Exception:
    LGB_OK = False

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DB_PATH = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"
REPORT_DIR.mkdir(exist_ok=True)

EARLY_PHASE_MAX_G = 5   # 赛季前期阈值：该队前置场次 < 5 视为"前期"


def season_year(d):
    return d.year if d.month >= 8 else d.year - 1


def load_matches():
    conn = sqlite3.connect(str(DB_PATH))
    df = pd.read_sql_query(
        "SELECT match_date, league, home_team, away_team, actual_wdl, actual_score "
        "FROM matches", conn)
    conn.close()
    df = df.dropna(subset=['match_date', 'actual_wdl', 'league'])
    df['match_date'] = pd.to_datetime(df['match_date'], errors='coerce')
    df = df.dropna(subset=['match_date'])
    wdl_map = {'主胜': 0, '胜': 0, '平': 1, '平局': 1, '客胜': 2, '负': 2}
    df['y'] = df['actual_wdl'].map(wdl_map)
    df = df.dropna(subset=['y'])
    df['y'] = df['y'].astype(int)
    df = df.sort_values('match_date').reset_index(drop=True)
    return df


def build_features(df):
    """每场特征：球队滚动状态（严格 date 之前）+ 升班马标识 + league 类别 + 升班马匹配标记。"""
    # 各队全库首次登场日期（标识用途，与标签时序无关、非泄漏）
    home_min = df.groupby('home_team')['match_date'].min()
    away_min = df.groupby('away_team')['match_date'].min()
    teams = set(df['home_team']) | set(df['away_team'])
    first_date = {}
    for t in teams:
        vals = [v for v in (home_min.get(t, None), away_min.get(t, None)) if not pd.isna(v)]
        first_date[t] = min(vals) if vals else None

    # 每队事件序列
    evmap = {}
    for _, r in df.iterrows():
        parts = None
        try:
            parts = str(r['actual_score']).split(':') if isinstance(r['actual_score'], str) else None
            hs, as_ = int(parts[0]), int(parts[1])
        except Exception:
            hs = as_ = None
        for team, gf, ga in (
                (r['home_team'], hs, as_),
                (r['away_team'], as_, hs)):
            evmap.setdefault(team, {'ns': [], 'gf': [], 'ga': []})
            evmap[team]['ns'].append(r['match_date'].value)  # ns int
            evmap[team]['gf'].append(gf)
            evmap[team]['ga'].append(ga)

    rows = []
    for i, r in df.iterrows():
        md_ns = r['match_date'].value
        sy = season_year(r['match_date'])
        rec = {'league': r['league'], 'y': int(r['y'])}
        h_prom = int(bool(first_date[r['home_team']]) and season_year(pd.Timestamp(first_date[r['home_team']])) == sy)
        a_prom = int(bool(first_date[r['away_team']]) and season_year(pd.Timestamp(first_date[r['away_team']])) == sy)
        rec['home_is_promoted'] = h_prom
        rec['away_is_promoted'] = a_prom
        rec['is_promoted_match'] = int(h_prom or a_prom)
        for side, team in (('home', r['home_team']), ('away', r['away_team'])):
            ev = evmap[team]
            ns = ev['ns']; k = bisect_left(ns, md_ns)
            if k == 0:
                rec[f'{side}_win_rate'] = 0.5
                rec[f'{side}_avg_goals'] = 1.3
                rec[f'{side}_avg_opp_goals'] = 1.2
                rec[f'{side}_games'] = 0
            else:
                gf = np.array([x for x in ev['gf'][:k]], dtype=float)
                ga = np.array([x for x in ev['ga'][:k]], dtype=float)
                valid = ~np.isnan(gf)
                gf, ga = gf[valid], ga[valid]
                if len(gf) == 0:
                    rec[f'{side}_win_rate'] = 0.5
                    rec[f'{side}_avg_goals'] = 1.3
                    rec[f'{side}_avg_opp_goals'] = 1.2
                    rec[f'{side}_games'] = 0
                else:
                    rec[f'{side}_win_rate'] = float((gf > ga).mean())
                    rec[f'{side}_avg_goals'] = float(gf.mean())
                    rec[f'{side}_avg_opp_goals'] = float(ga.mean())
                    rec[f'{side}_games'] = int(len(gf))
        rec['winrate_diff'] = rec['home_win_rate'] - rec['away_win_rate']
        rec['goals_diff'] = rec['home_avg_goals'] - rec['away_avg_goals']
        rec['games_diff'] = rec['home_games'] - rec['away_games']
        rows.append(rec)
    F = pd.DataFrame(rows)
    F['league_cat'] = F['league'].astype('category').cat.codes
    # 赛季轮次近似（组内序号比）
    F['round_frac'] = 0.5
    df_key = F.copy(); df_key['season_key'] = df['match_date'].map(season_year).values
    grp = df_key.groupby(['league', 'season_key'])['league'].cumcount() + 1
    sizes = df_key.groupby(['league', 'season_key'])['league'].transform('size')
    F['round_frac'] = (grp / sizes).values
    # 升班马先验占位列（augmented 覆写填充）
    for c in ['home_prom_prior_winrate', 'home_prom_prior_goals', 'away_prom_prior_winrate', 'away_prom_prior_goals']:
        F[c] = 0.0
    return F


def promoted_prior(train):
    """从训练段升班马球队统计联赛自适应先验（防泄漏：只用本折训练数据）。"""
    out = {}
    for lg in train['league'].unique():
        sub = train[(train['is_promoted_match'] == 1) & (train['league'] == lg)]
        if len(sub) < 20:
            continue
        hp = sub[sub['home_is_promoted'] == 1]
        ap = sub[sub['away_is_promoted'] == 1]
        hw = hp['home_win_rate'].mean() if len(hp) else np.nan
        aw = ap['away_win_rate'].mean() if len(ap) else np.nan
        hg = hp['home_avg_goals'].mean() if len(hp) else np.nan
        ag = ap['away_avg_goals'].mean() if len(ap) else np.nan
        if not all(np.isnan(v) for v in (hw, aw, hg, ag)):
            out[lg] = {'home_wr': hw, 'away_wr': aw, 'home_g': hg, 'away_g': ag}
    return out


def apply_aug(X, pri):
    """对升班马前期样本：以联赛自适应先验覆写状态默认值，并填充先验特征列。"""
    X = X.copy()
    for lg, p in pri.items():
        mask = X['league'] == lg
        for side in ('home', 'away'):
            sel = mask & (X[f'{side}_is_promoted'] == 1) & (X[f'{side}_games'] < EARLY_PHASE_MAX_G)
            wr = p[f'{side}_wr']; g = p[f'{side}_g']
            if not np.isnan(wr):
                X.loc[sel, f'{side}_win_rate'] = X.loc[sel, f'{side}_win_rate'].where(X.loc[sel, f'{side}_games'] == 0, X.loc[sel, f'{side}_win_rate'])
                X.loc[sel & (X[f'{side}_games'] == 0), f'{side}_win_rate'] = wr
                X.loc[sel, f'{side}_prom_prior_winrate'] = wr
            if not np.isnan(g):
                X.loc[sel & (X[f'{side}_games'] == 0), f'{side}_avg_goals'] = g
                X.loc[sel, f'{side}_prom_prior_goals'] = g
    # 覆写后重算 diff
    X['winrate_diff'] = X['home_win_rate'] - X['away_win_rate']
    X['goals_diff'] = X['home_avg_goals'] - X['away_avg_goals']
    return X


def evaluate(y_true, proba):
    acc = float((proba.argmax(1) == y_true).mean())
    P_cum = proba.cumsum(1)
    O_cum = np.zeros_like(proba)
    for c in range(3):
        O_cum[:, c] = (y_true <= c).astype(float)
    rps = float((np.square(P_cum - O_cum).sum(1) / 2).mean())
    return acc, rps


def main():
    if not LGB_OK:
        print("lightgbm 不可用，退出")
        return
    F = build_features(load_matches())

    BASE = ['round_frac', 'home_win_rate', 'home_avg_goals', 'home_avg_opp_goals', 'home_games',
            'away_win_rate', 'away_avg_goals', 'away_avg_opp_goals', 'away_games',
            'winrate_diff', 'goals_diff', 'games_diff', 'league_cat']
    EXT = BASE + ['home_is_promoted', 'away_is_promoted',
                  'home_prom_prior_winrate', 'home_prom_prior_goals',
                  'away_prom_prior_winrate', 'away_prom_prior_goals']

    seen = {'base': {'y': [], 'proba': [], 'pm': []},
            'aug': {'y': [], 'proba': [], 'pm': []}}
    tscv = TimeSeriesSplit(n_splits=4)
    for tr_idx, te_idx in tscv.split(F):
        train, test = F.iloc[tr_idx], F.iloc[te_idx]
        pri = promoted_prior(train)
        test_a = apply_aug(test, pri)

        def fit(Xtr, Xte, feats):
            clf = lgb.LGBMClassifier(n_estimators=200, learning_rate=0.04, num_leaves=15,
                                     max_depth=4, subsample=0.8, colsample_bytree=0.8,
                                     random_state=42, verbose=-1, force_col_wise=True)
            clf.fit(Xtr[feats], Xtr['y'])
            proba = clf.predict_proba(Xte[feats])
            out = np.zeros((len(Xte), 3))
            for j, c in enumerate(clf.classes_):
                out[:, int(c)] = proba[:, j]
            return out

        proba_b = fit(train, test, BASE)
        proba_a = fit(apply_aug(train, pri), test_a, EXT)

        seen['base']['y'].extend(list(test['y'].values))
        seen['base']['proba'].append(proba_b)
        seen['base']['pm'].extend(list((test['is_promoted_match'] == 1).values))
        seen['aug']['y'].extend(list(test['y'].values))
        seen['aug']['proba'].append(proba_a)
        seen['aug']['pm'].extend(list((test['is_promoted_match'] == 1).values))

    def agg(s):
        y = np.array(s['y']); proba = np.vstack(s['proba']); pm = np.array(s['pm'])
        acc_all, rps_all = evaluate(y, proba)
        acc_p, rps_p = evaluate(y[pm], proba[pm])
        return {'all': {'rps': round(rps_all, 6), 'acc': round(acc_all, 6)},
                'promoted': {'rps': round(rps_p, 6), 'acc': round(acc_p, 6), 'n': int(pm.sum())}}

    res = {'baseline': agg(seen['base']), 'augmented': agg(seen['aug'])}
    out = {
        'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'early_phase_max_g': EARLY_PHASE_MAX_G,
        'baseline': res['baseline'],
        'augmented': res['augmented'],
        'delta_promoted_rps': round(res['baseline']['promoted']['rps'] - res['augmented']['promoted']['rps'], 6),
        'delta_promoted_acc': round(res['augmented']['promoted']['acc'] - res['baseline']['promoted']['acc'], 6),
    }
    path = REPORT_DIR / 'c4_promoted_ablation_results.json'
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding='utf-8')
    print("=== C4 升班马消融结果 ===")
    print("基线 baseline:", json.dumps(res['baseline'], ensure_ascii=False))
    print("增强 augmented:", json.dumps(res['augmented'], ensure_ascii=False))
    print(f"升班马子集 ΔRPS={out['delta_promoted_rps']:+.5f}  ΔAcc={out['delta_promoted_acc']:+.5f}")
    print(f"保存: {path}")


if __name__ == '__main__':
    main()