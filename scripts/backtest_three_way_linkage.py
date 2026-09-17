# -*- coding: utf-8 -*-
"""
三项联动 λ 联合优化 vs 现状 —— 扩样本回测（意甲+法甲+近期）
================================================================
扩样本: 意甲25/26(三项赔率齐全, 盘口线L反推) + 法甲25/26(真L) + 26/27近期6场(真L)

三档对比(其余一致, Poisson比分矩阵):
  A 旧基线    : λ = calc_lambda_from_odds(WDL) -> A-002(WDL+TG两阶段), ρ=-0.30
  B 新三项联动: 用市场 WDL+让球+总进球 联合拟合λ(纯泊松), ρ=-0.30
  C 新+ρ切换  : 同上, 但 |盘口|>=1 时 ρ=-0.10(降低1:1高估)

评估口径(整分布聚合):
  方向命中  = argmax(ΣP主胜, ΣP平, ΣP客胜) == 实际胜负平
  TG大2.5命中 = (ΣP(总进球>=3) >= 0.5) == 实际总进球>=3
  TG区间命中 = argmax(P(0-1), P(2-3), P(4+)) == 实际区间
"""
import sqlite3
import math
import numpy as np
from pathlib import Path
from scipy.stats import poisson
from scipy.optimize import minimize

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
ODDS_DB = DATA / "odds.db"
TIMING_DB = DATA / "odds_timing.db"

MAX_GOALS = 7
AVG_GOALS = 3.6
LAMBDA_MIN, LAMBDA_MAX = 0.3, 4.0
RHO_NORM, RHO_SKEW = -0.30, -0.10


def implied_prob(odds):
    inv = [1.0 / max(o, 1e-10) for o in odds]
    t = sum(inv)
    return [x / t for x in inv]


def dc_tau(h, a, lh, la, rho):
    if h <= 1 and a <= 1:
        if h == 0 and a == 0:
            return 1.0 - lh * la * rho
        if h == 0 and a == 1:
            return 1.0 + lh * rho
        if h == 1 and a == 0:
            return 1.0 + la * rho
        if h == 1 and a == 1:
            return 1.0 - rho
    return 1.0


def score_grid(lh, la, rho=None, dc=True):
    grid = {}
    for h in range(MAX_GOALS + 1):
        for a in range(MAX_GOALS + 1):
            p = poisson.pmf(h, lh) * poisson.pmf(a, la)
            if dc:
                tau = dc_tau(h, a, lh, la, rho if rho is not None else RHO_NORM)
                p = p * max(0.1, min(3.0, tau))
            grid[(h, a)] = p
    t = sum(grid.values())
    return {k: v / t for k, v in grid.items()} if t > 0 else grid


def lambda_baseline(wdl_odds, goals_odds):
    hp, dp, ap = implied_prob(list(wdl_odds))
    lam_h = max(0.3, min(3.5, AVG_GOALS * hp))
    lam_a = max(0.3, min(3.5, AVG_GOALS * ap))
    sh = max(0.7, min(1.8, 0.5 + hp * 1.5))
    sa = max(0.7, min(1.8, 0.5 + ap * 1.5))
    tg_scale = 1.0
    if goals_odds:
        inv = {k: 1.0 / max(o, 1e-10) for k, o in goals_odds.items() if o is not None}
        tt = sum(inv.values())
        if tt > 0:
            exp = 0.0
            for k in inv:
                p = inv[k] / tt
                g = int(k.replace('+', '')) if str(k).replace('+', '').isdigit() else 7
                exp += g * p
            base = lam_h + lam_a
            if base > 0.1:
                tg_scale = max(0.85, min(1.4, exp / base))
    return max(0.3, lam_h * sh * tg_scale), max(0.3, lam_a * sa * tg_scale)


def grid_marginals(grid, L):
    wdl = [0.0, 0.0, 0.0]
    hcp = [0.0, 0.0, 0.0]
    over = under = 0.0
    for (h, a), p in grid.items():
        wdl[0 if h > a else (1 if h == a else 2)] += p
        d = h + L - a
        hcp[0 if d > 0 else (1 if d == 0 else 2)] += p
        if h + a >= 3:
            over += p
        else:
            under += p
    return wdl, hcp, [over, under]


def ce(target, pred):
    eps = 1e-9
    return -sum(t * math.log(max(p, eps)) for t, p in zip(target, pred))


def infer_L(wdl_odds, hcp_odds, lam0):
    """从 WDL+HCP 赔率反推盘口线 L(整数步长, 纯泊松)"""
    lh, la = lam0
    grid = score_grid(lh, la, dc=False)
    t_hcp = implied_prob(list(hcp_odds))
    best_L, best_ce = 0, 1e18
    for L in range(-3, 4):
        _, h, _ = grid_marginals(grid, L)
        e = ce(t_hcp, h)
        if e < best_ce:
            best_ce, best_L = e, L
    return float(best_L)


def joint_loss(x, targets, L):
    lh, la = max(LAMBDA_MIN, x[0]), max(LAMBDA_MIN, x[1])
    grid = score_grid(lh, la, dc=False)  # 纯泊松, 不含 DC
    w, h, t = grid_marginals(grid, L)
    return ce(targets['wdl'], w) + ce(targets['hcp'], h) + ce(targets['tg'], t)


def lambda_joint(wdl_odds, hcp_odds, goals_odds, L, lam0):
    t_wdl = implied_prob(list(wdl_odds))
    t_hcp = implied_prob(list(hcp_odds))
    inv = {k: 1.0 / max(o, 1e-10) for k, o in goals_odds.items() if o is not None}
    tt = sum(inv.values())
    over = under = 0.0
    if tt > 0:
        for k in inv:
            p = inv[k] / tt
            g = int(k.replace('+', '')) if str(k).replace('+', '').isdigit() else 7
            if g >= 3:
                over += p
            else:
                under += p
    targets = {'wdl': t_wdl, 'hcp': t_hcp, 'tg': [max(over, 1e-6), max(under, 1e-6)]}
    res = minimize(joint_loss, x0=list(lam0), args=(targets, L),
                   method='L-BFGS-B', bounds=[(LAMBDA_MIN, LAMBDA_MAX)] * 2)
    lh, la = res.x
    return max(LAMBDA_MIN, min(LAMBDA_MAX, lh)), max(LAMBDA_MIN, min(LAMBDA_MAX, la))


def parse_score(s):
    if not s:
        return None
    s = str(s).strip().replace('：', ':').replace('-', ':')
    if ':' not in s:
        return None
    try:
        h, a = s.split(':')
        return int(h), int(a)
    except Exception:
        return None


def eval_grid(grid, actual):
    wdl, _, tg = grid_marginals(grid, 0)
    pred_dir = int(np.argmax(wdl))
    act_dir = 0 if actual[0] > actual[1] else (1 if actual[0] == actual[1] else 2)
    pred_over = tg[0] >= 0.5
    act_over = (actual[0] + actual[1]) >= 3
    bucket = [0.0, 0.0, 0.0]
    for (h, a), p in grid.items():
        t = h + a
        bucket[0 if t <= 1 else (1 if t <= 3 else 2)] += p
    pred_bk = int(np.argmax(bucket))
    act_bk = 0 if actual[0] + actual[1] <= 1 else (1 if actual[0] + actual[1] <= 3 else 2)
    return pred_dir == act_dir, pred_over == act_over, pred_bk == act_bk, tg[0]


# ---------------- 数据加载 ----------------
def load_from_odds_db():
    leagues = ['意甲2025-2026赛季', '法甲2025-2026赛季']
    out = []
    c = sqlite3.connect(str(ODDS_DB))
    c.row_factory = sqlite3.Row
    for lg in leagues:
        rows = c.execute("""
            SELECT match_id, home_team, away_team, handicap, actual_score, match_type
            FROM matches
            WHERE actual_score IS NOT NULL AND actual_score != ''
              AND match_type = ?
        """, (lg,)).fetchall()
        for r in rows:
            sc = parse_score(r['actual_score'])
            if sc is None:
                continue
            mid = r['match_id']
            w = c.execute("SELECT win_a, draw, win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
            h = c.execute("SELECT hcp_win, hcp_draw, hcp_lose FROM handicap_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
            g = c.execute("SELECT goals_0,goals_1,goals_2,goals_3,goals_4,goals_5,goals_6,goals_7_plus FROM total_goals_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
            if not (w and h and g):
                continue
            goals_odds = {f"{i}": g[i] for i in range(8)}
            goals_odds["7+"] = g[7]
            wdl = (w[0], w[1], w[2])
            hcp = (h[0], h[1], h[2])
            if r['handicap'] is not None:
                L = float(r['handicap'])
                lsrc = '真'
            else:
                lam0 = lambda_baseline(wdl, goals_odds)
                L = infer_L(wdl, hcp, lam0)
                lsrc = '推'
            out.append({'label': f"{r['home_team']} vs {r['away_team']}", 'league': lg[:2],
                        'L': L, 'lsrc': lsrc, 'score': sc, 'wdl': wdl, 'hcp': hcp, 'goals': goals_odds})
    c.close()
    return out


def load_recent():
    mapping = {
        '2026-08-16_Alaves_Getafe': '2026-08-16_Deportivo Alavés_Getafe',
        '2026-08-16_Sevilla_Vallecano': '2026-08-16_Sevilla_Rayo Vallecano',
        '2026-08-16_R. Racing Club_Villarreal': '2026-08-16_Real Racing Club_Villarreal',
        '2026-08-17_Espanol_Levante': '2026-08-17_Espanyol_Levante UD',
        '2026-08-22_贝蒂斯_皇家社会': '2026-08-22_Real Betis_Real Sociedad',
        '2026-08-22_阿森纳_考文垂': '2026-08-22_Arsenal_Coventry City',
        '2026-08-22_马赛_斯特拉斯': '2026-08-22_Olympique de Marseille_RC Strasbourg',
    }
    oc = sqlite3.connect(str(ODDS_DB))
    tc = sqlite3.connect(str(TIMING_DB))
    tc.row_factory = sqlite3.Row
    out = []
    for tid, mid in mapping.items():
        m = oc.execute("SELECT home_team, away_team, handicap, actual_score, match_type FROM matches WHERE match_id=?", (mid,)).fetchone()
        if not m:
            continue
        sc = parse_score(m[3])
        if sc is None:
            continue
        w = tc.execute("SELECT win_a, draw, win_b FROM wdl_timing WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (tid,)).fetchone()
        h = tc.execute("SELECT handicap, hcp_win, hcp_draw, hcp_lose FROM handicap_timing WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (tid,)).fetchone()
        g = tc.execute("SELECT goals_0,goals_1,goals_2,goals_3,goals_4,goals_5,goals_6,goals_7_plus FROM total_goals_timing WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (tid,)).fetchone()
        if not (w and h and g):
            continue
        goals_odds = {f"{i}": g[i] for i in range(8)}
        goals_odds["7+"] = g[7]
        out.append({'label': f"{m[0]} vs {m[1]}", 'league': '近',
                    'L': float(h['handicap']), 'lsrc': '真', 'score': sc,
                    'wdl': (w['win_a'], w['draw'], w['win_b']), 'hcp': (h['hcp_win'], h['hcp_draw'], h['hcp_lose']), 'goals': goals_odds})
    oc.close()
    tc.close()
    return out


def main():
    matches = load_from_odds_db() + load_recent()
    n = len(matches)
    print(f"回测样本: {n} 场\n")

    A = {'dir': 0, 'tg': 0, 'bk': 0}
    B = {'dir': 0, 'tg': 0, 'bk': 0}
    C = {'dir': 0, 'tg': 0, 'bk': 0}
    grp = {}  # league -> {A:{...},B:{...},C:{...},n}

    for m in matches:
        lam0 = lambda_baseline(m['wdl'], m['goals'])
        lamJ = lambda_joint(m['wdl'], m['hcp'], m['goals'], m['L'], lam0)
        rho_c = RHO_SKEW if abs(m['L']) >= 1 else RHO_NORM

        gridA = score_grid(lam0[0], lam0[1], rho=RHO_NORM)
        gridB = score_grid(lamJ[0], lamJ[1], rho=RHO_NORM)
        gridC = score_grid(lamJ[0], lamJ[1], rho=rho_c)

        dA, tA, bA, _ = eval_grid(gridA, m['score'])
        dB, tB, bB, _ = eval_grid(gridB, m['score'])
        dC, tC, bC, _ = eval_grid(gridC, m['score'])
        A['dir'] += dA; A['tg'] += tA; A['bk'] += bA
        B['dir'] += dB; B['tg'] += tB; B['bk'] += bB
        C['dir'] += dC; C['tg'] += tC; C['bk'] += bC

        lg = m['league']
        if lg not in grp:
            grp[lg] = {'A': {'dir': 0, 'tg': 0, 'bk': 0}, 'B': {'dir': 0, 'tg': 0, 'bk': 0},
                       'C': {'dir': 0, 'tg': 0, 'bk': 0}, 'n': 0}
        grp[lg]['A']['dir'] += dA; grp[lg]['A']['tg'] += tA; grp[lg]['A']['bk'] += bA
        grp[lg]['B']['dir'] += dB; grp[lg]['B']['tg'] += tB; grp[lg]['B']['bk'] += bB
        grp[lg]['C']['dir'] += dC; grp[lg]['C']['tg'] += tC; grp[lg]['C']['bk'] += bC
        grp[lg]['n'] += 1

    print("========== 分联赛汇总 ==========")
    print(f"{'联赛':<6}{'场数':>5} | {'方向A/B/C':>16} | {'大2.5 A/B/C':>16} | {'区间 A/B/C':>16}")
    print("-" * 72)
    for lg, d in grp.items():
        na = d['n']
        da, db, dc = d['A']['dir'], d['B']['dir'], d['C']['dir']
        ta, tb, tc = d['A']['tg'], d['B']['tg'], d['C']['tg']
        ka, kb, kc = d['A']['bk'], d['B']['bk'], d['C']['bk']
        print(f"{lg:<6}{na:>5} | {da:>4}/{db:>4}/{dc:>4} ({da/na*100:.0f}/{db/na*100:.0f}/{dc/na*100:.0f}%) | "
              f"{ta:>4}/{tb:>4}/{tc:>4} ({ta/na*100:.0f}/{tb/na*100:.0f}/{tc/na*100:.0f}%) | "
              f"{ka:>4}/{kb:>4}/{kc:>4} ({ka/na*100:.0f}/{kb/na*100:.0f}/{kc/na*100:.0f}%)")

    print("-" * 72)
    print("\n========== 全量汇总 ==========")
    print(f"方向命中率:      A旧={A['dir']}/{n}={A['dir']/n*100:.1f}%  "
          f"B新={B['dir']}/{n}={B['dir']/n*100:.1f}%  "
          f"C新+ρ切={C['dir']}/{n}={C['dir']/n*100:.1f}%")
    print(f"TG(大2.5)命中率: A旧={A['tg']}/{n}={A['tg']/n*100:.1f}%  "
          f"B新={B['tg']}/{n}={B['tg']/n*100:.1f}%  "
          f"C新+ρ切={C['tg']}/{n}={C['tg']/n*100:.1f}%")
    print(f"TG区间(0-1/2-3/4+)命中率: A旧={A['bk']}/{n}={A['bk']/n*100:.1f}%  "
          f"B新={B['bk']}/{n}={B['bk']/n*100:.1f}%  "
          f"C新+ρ切={C['bk']}/{n}={C['bk']/n*100:.1f}%")

    # 真 L / 反推 L 分组
    print("\n========== 按L来源分组(真L vs 反推L) ==========")
    for tag in ['真', '推']:
        sub = [m for m in matches if m['lsrc'] == tag]
        if not sub:
            continue
        na = len(sub)
        sa = sb = sc_ = {'dir': 0, 'tg': 0, 'bk': 0}
        sa = {'dir': 0, 'tg': 0, 'bk': 0}; sb = {'dir': 0, 'tg': 0, 'bk': 0}; sc_ = {'dir': 0, 'tg': 0, 'bk': 0}
        for m in sub:
            lam0 = lambda_baseline(m['wdl'], m['goals'])
            lamJ = lambda_joint(m['wdl'], m['hcp'], m['goals'], m['L'], lam0)
            rho_c = RHO_SKEW if abs(m['L']) >= 1 else RHO_NORM
            dA, tA, bA, _ = eval_grid(score_grid(lam0[0], lam0[1], rho=RHO_NORM), m['score'])
            dB, tB, bB, _ = eval_grid(score_grid(lamJ[0], lamJ[1], rho=RHO_NORM), m['score'])
            dC, tC, bC, _ = eval_grid(score_grid(lamJ[0], lamJ[1], rho=rho_c), m['score'])
            sa['dir'] += dA; sa['tg'] += tA; sa['bk'] += bA
            sb['dir'] += dB; sb['tg'] += tB; sb['bk'] += bB
            sc_['dir'] += dC; sc_['tg'] += tC; sc_['bk'] += bC
        nm = '真盘口L' if tag == '真' else '反推L'
        print(f"{nm}({na}场): 方向 {sa['dir']}/{na}={sa['dir']/na*100:.1f}% / {sb['dir']}/{na}={sb['dir']/na*100:.1f}% / {sc_['dir']}/{na}={sc_['dir']/na*100:.1f}%  |  "
              f"区间 {sa['bk']}/{na}={sa['bk']/na*100:.1f}% / {sb['bk']}/{na}={sb['bk']/na*100:.1f}% / {sc_['bk']}/{na}={sc_['bk']/na*100:.1f}%")


if __name__ == "__main__":
    main()
