"""
T-006 集成方案对比实验

目标: 在同一历史数据集上对比四种方案，验证低球先验集成能否提升 0-1球区间命中率

方案对比:
  Baseline (v2): lambda_total 恒=2.5（根因缺陷，0-1球区间1球内命中率仅13.2%）
  PlanA: lambda_total = score_implied_total（直接替换，让 lambda 有方差）
  PlanB: 保持 v2 lambda 不变，但在 fuse 后用 score_implied_total 调整低比分权重
  PlanC: 保持 v2 lambda 不变，但在 fuse 后用 t006 分类器 P(小球) 调整低比分权重（方法A）

评估指标:
  - 精确命中率 / 1球内命中率 / 2球内命中率（整体 + 按总进球数分布）
  - 0-1球区间是重点优化目标
"""
import sqlite3
import pandas as pd
import numpy as np
import os
import logging
import time
import pickle
from datetime import datetime
from scipy.stats import poisson
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
LOG_DIR = BASE_DIR / "logs"
MIN_CONFIDENCE = 0.85
RHO = -0.30
POISSON_WEIGHT = 0.85
MC_WEIGHT = 0.15
HOME_ADVANTAGE = 1.10
LEAGUE_AVG_GOALS = 2.5
TOP_N_EVAL = 8
MAX_GOALS = 7
N_SIM = 10000
LOWGOAL_MODEL_PATH = BASE_DIR / "assets" / "t006_lowgoal_classifier_v1.pkl"

os.makedirs(LOG_DIR, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_PATH = os.path.join(LOG_DIR, f't006_integration_compare_{ts}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[logging.StreamHandler(), logging.FileHandler(LOG_PATH, encoding='utf-8')]
)
LOG = logging.getLogger('integration_compare')


def parse_score(s):
    if not s or ':' not in str(s):
        return None, None
    parts = str(s).split(':')
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None, None


def batch_load_data(conn):
    """批量加载所有比赛 + WDL + Score 赔率到内存（避免逐场DB查询）"""
    LOG.info('[batch_load] 加载比赛 + WDL + Score 赔率...')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.match_id, m.home_team, m.away_team, m.actual_score, m.actual_total_goals,
               mm.sh_match_id, mm.confidence
        FROM matches m
        INNER JOIN match_id_mapping mm ON mm.matches_match_id = m.match_id AND mm.confidence >= ?
        WHERE m.actual_score IS NOT NULL AND m.actual_score != ''
        GROUP BY m.match_id
    ''', (MIN_CONFIDENCE,))
    matches = []
    for r in cursor.fetchall():
        h, a = parse_score(r[3])
        if h is None:
            continue
        matches.append({
            'match_id': r[0], 'home_team': r[1], 'away_team': r[2],
            'actual_score': r[3], 'actual_total_goals': r[4],
            'sh_match_id': r[5], 'confidence': r[6],
            'home_goals': h, 'away_goals': a
        })
    LOG.info(f'[batch_load] 比赛: {len(matches)}')

    # 批量加载 WDL（取最新 timestamp）
    sh_ids = [m['sh_match_id'] for m in matches if m['sh_match_id']]
    wdl_dict = {}
    if sh_ids:
        placeholders = ','.join('?' * len(sh_ids))
        cursor.execute(f'''
            SELECT match_id, win_a, draw, win_b, timestamp
            FROM wdl_history WHERE match_id IN ({placeholders})
        ''', sh_ids)
        for row in cursor.fetchall():
            mid, wa, d, wb, t = row
            if mid not in wdl_dict or t > wdl_dict[mid]['timestamp']:
                wdl_dict[mid] = {'win_a': wa, 'draw': d, 'win_b': wb, 'timestamp': t}
    LOG.info(f'[batch_load] WDL赔率: {len(wdl_dict)}')

    # 批量加载 Score 赔率
    score_dict = {}
    if sh_ids:
        cursor.execute(f'''
            SELECT match_id, score, odds FROM score_history WHERE match_id IN ({placeholders})
        ''', sh_ids)
        tmp = {}
        for mid, score, odds in cursor.fetchall():
            tmp.setdefault(mid, {}).setdefault(score, []).append(odds)
        for mid, sd in tmp.items():
            score_dict[mid] = {s: np.mean(o) for s, o in sd.items()}
    LOG.info(f'[batch_load] Score赔率: {len(score_dict)}')

    # 合并到 matches
    for m in matches:
        m['wdl'] = wdl_dict.get(m['sh_match_id'])
        m['score_odds'] = score_dict.get(m['sh_match_id'], {})
    return matches


def calc_score_implied_total(score_odds):
    """从比分赔率计算隐含总进球"""
    if not score_odds:
        return None
    implied_total = 0.0
    total_prob = 0.0
    for score, odds_val in score_odds.items():
        h, a = parse_score(score)
        if h is not None:
            p = 1.0 / odds_val
            implied_total += (h + a) * p
            total_prob += p
    if total_prob > 0 and implied_total > 0:
        return implied_total / total_prob
    return None


def calculate_lambda_v2(odds, score_odds):
    """v2 原始 lambda 计算（lambda_total 恒=LEAGUE_AVG_GOALS）"""
    if odds:
        wa, d, wb = odds['win_a'], odds['draw'], odds['win_b']
        ti = 1.0 / wa + 1.0 / d + 1.0 / wb
        prob_home = (1.0 / wa) / ti
        prob_away = (1.0 / wb) / ti
        lh = LEAGUE_AVG_GOALS * prob_home * HOME_ADVANTAGE
        la = LEAGUE_AVG_GOALS * prob_away
        tl = lh + la
        if tl > 0:
            lh = lh / tl * LEAGUE_AVG_GOALS
            la = la / tl * LEAGUE_AVG_GOALS
        # v2 比分赔率缩放（但归一化后仍=LEAGUE_AVG_GOALS）
        if score_odds and len(score_odds) > 0:
            implied_total = 0.0
            total_prob = 0.0
            for score, ov in score_odds.items():
                h, a = parse_score(score)
                if h is not None:
                    p = 1.0 / ov
                    implied_total += (h + a) * p
                    total_prob += p
            if total_prob > 0 and implied_total > 0:
                implied_total /= total_prob
                scale = implied_total / LEAGUE_AVG_GOALS if LEAGUE_AVG_GOALS > 0 else 1.0
                lh_s = lh * scale
                la_s = la * scale
                lh = 0.6 * lh + 0.4 * lh_s
                la = 0.6 * la + 0.4 * la_s
    else:
        lh = LEAGUE_AVG_GOALS * 0.55
        la = LEAGUE_AVG_GOALS * 0.45
    return max(0.1, min(6.0, lh)), max(0.1, min(6.0, la))


def calculate_lambda_planA(odds, score_odds):
    """方案A: lambda_total = score_implied_total，按 prob_home/prob_away 分配"""
    sit = calc_score_implied_total(score_odds)
    if odds:
        wa, d, wb = odds['win_a'], odds['draw'], odds['win_b']
        ti = 1.0 / wa + 1.0 / d + 1.0 / wb
        prob_home = (1.0 / wa) / ti
        prob_away = (1.0 / wb) / ti
        # 主场优势：prob_home 乘以 HOME_ADVANTAGE 后重新归一化
        home_weight = prob_home * HOME_ADVANTAGE
        away_weight = prob_away
        total_weight = home_weight + away_weight
        if total_weight > 0:
            home_ratio = home_weight / total_weight
            away_ratio = away_weight / total_weight
        else:
            home_ratio, away_ratio = 0.55, 0.45
        # lambda_total: 优先用 score_implied_total，否则回退联赛均值
        lambda_total = sit if sit is not None and sit > 0 else LEAGUE_AVG_GOALS
        lh = lambda_total * home_ratio
        la = lambda_total * away_ratio
    else:
        # 无 WDL 赔率回退
        lambda_total = sit if sit is not None and sit > 0 else LEAGUE_AVG_GOALS
        lh = lambda_total * 0.55
        la = lambda_total * 0.45
    return max(0.1, min(6.0, lh)), max(0.1, min(6.0, la))


def poisson_predict_dc(lambda_home, lambda_away, max_goals=MAX_GOALS):
    """Poisson + Dixon-Coles 修正（向量化）"""
    goals_grid = np.arange(max_goals + 1)
    pmf_home = poisson.pmf(goals_grid, lambda_home)
    pmf_away = poisson.pmf(goals_grid, lambda_away)
    raw_matrix = np.outer(pmf_home, pmf_away)
    tau = np.ones((max_goals + 1, max_goals + 1))
    tau[0, 0] = 1.0 - lambda_home * lambda_away * RHO
    tau[0, 1] = 1.0 + lambda_home * RHO
    tau[1, 0] = 1.0 + lambda_away * RHO
    tau[1, 1] = 1.0 - RHO
    tau = np.clip(tau, 0.1, 3.0)
    corrected = raw_matrix * tau
    total = corrected.sum()
    if total > 0:
        corrected = corrected / total
    probs = {}
    for h in range(max_goals + 1):
        for a in range(max_goals + 1):
            probs[f'{h}:{a}'] = float(corrected[h, a])
    return probs


def monte_carlo_predict(lambda_home, lambda_away, n_sim=N_SIM, max_goals=MAX_GOALS):
    """蒙特卡洛模拟（向量化）"""
    hg = np.clip(np.random.poisson(lambda_home, n_sim), 0, max_goals)
    ag = np.clip(np.random.poisson(lambda_away, n_sim), 0, max_goals)
    codes = hg.astype(np.int32) * 10 + ag.astype(np.int32)
    uniq, counts = np.unique(codes, return_counts=True)
    probs = {}
    for code, cnt in zip(uniq, counts):
        h = int(code // 10)
        a = int(code % 10)
        probs[f'{h}:{a}'] = float(cnt) / n_sim
    return probs


def fuse(poisson_probs, mc_probs):
    all_scores = set(list(poisson_probs.keys()) + list(mc_probs.keys()))
    fused = {}
    for s in all_scores:
        fused[s] = poisson_probs.get(s, 0) * POISSON_WEIGHT + mc_probs.get(s, 0) * MC_WEIGHT
    total = sum(fused.values())
    if total > 0:
        for k in fused:
            fused[k] /= total
    return fused


def adjust_lowgoal_weights(fused, score_implied_total, alpha=0.5):
    """方案B: 用 score_implied_total 调整低比分权重
    score_implied_total < LEAGUE_AVG_GOALS → 提升低比分权重
    score_implied_total > LEAGUE_AVG_GOALS → 降低低比分权重
    """
    if score_implied_total is None:
        return fused
    low_scores = ['0:0', '0:1', '1:0', '1:1']
    # 调整因子: score_implied_total 越低，因子越大
    # 基准: LEAGUE_AVG_GOALS=2.5
    # 因子 = 1 + alpha * (LEAGUE_AVG_GOALS - score_implied_total) / LEAGUE_AVG_GOALS
    factor = 1.0 + alpha * (LEAGUE_AVG_GOALS - score_implied_total) / LEAGUE_AVG_GOALS
    # 限制因子范围 [0.5, 2.0]
    factor = max(0.5, min(2.0, factor))
    adjusted = dict(fused)
    for s in low_scores:
        if s in adjusted:
            adjusted[s] *= factor
    # 重新归一化
    total = sum(adjusted.values())
    if total > 0:
        for k in adjusted:
            adjusted[k] /= total
    return adjusted


def adjust_lowgoal_weights_by_prior(fused, p_low, base_rate=0.22, alpha=0.5):
    """方案C(方法A): 用 t006 分类器 P(小球) 调整低比分权重

    p_low: 分类器预测的 P(total_goals <= 1)
    base_rate: 小球率基准（训练集实际小球率）
    factor = 1 + alpha * (p_low - base_rate) / base_rate，clip 到 [0.5, 2.0]
    """
    if p_low is None or base_rate <= 0:
        return fused
    low_scores = ['0:0', '0:1', '1:0', '1:1']
    factor = 1.0 + alpha * (p_low - base_rate) / base_rate
    factor = max(0.5, min(2.0, factor))
    adjusted = dict(fused)
    for s in low_scores:
        if s in adjusted:
            adjusted[s] *= factor
    total = sum(adjusted.values())
    if total > 0:
        for k in adjusted:
            adjusted[k] /= total
    return adjusted


def build_lowgoal_prior_map(conn):
    """加载 t006 低进球分类器，构建 match_id -> P(小球) 映射。

    复用 t006_lowgoal_classifier.load_data + build_features 保证特征口径一致。
    返回 (p_low_map, base_rate)。
    """
    from t006_lowgoal_classifier import load_data, build_features
    with open(LOWGOAL_MODEL_PATH, 'rb') as f:
        bundle = pickle.load(f)
    model = bundle['model']
    feature_cols = bundle['feature_cols']

    df = load_data(conn)
    X, y, _dates, _feats, df_feat = build_features(df)
    p_low = model.predict_proba(X[feature_cols])[:, 1]
    p_low_map = dict(zip(df_feat['match_id'], p_low))
    base_rate = float(y.mean()) if len(y) > 0 else 0.22
    LOG.info(f'[build_lowgoal_prior_map] 分类器覆盖 {len(p_low_map)} 场，base_rate={base_rate:.4f}')
    return p_low_map, base_rate


def evaluate(actual_score, fused_probs, top_n=TOP_N_EVAL):
    sorted_scores = sorted(fused_probs.items(), key=lambda x: x[1], reverse=True)
    actual = str(actual_score)
    actual_h, actual_a = parse_score(actual_score)
    exact_hit = 0
    within1_hit = 0
    within2_hit = 0
    for score, _ in sorted_scores[:top_n]:
        h, a = parse_score(score)
        if h is not None and actual_h is not None:
            diff = abs(h - actual_h) + abs(a - actual_a)
            if score == actual:
                exact_hit = 1
            if diff <= 2:
                within2_hit = 1
            if diff <= 1:
                within1_hit = 1
    return exact_hit, within1_hit, within2_hit


def run_plan(matches, plan_name, alpha=0.5, p_low_map=None, base_rate=0.22):
    """运行一个方案的回测"""
    LOG.info(f'\n[{plan_name}] 开始回测 {len(matches)} 场...')
    t_start = time.perf_counter()
    results = []
    for i, m in enumerate(matches):
        if (i + 1) % 1000 == 0:
            LOG.info(f'  [{plan_name}] 进度: {i+1}/{len(matches)}')
        odds = m['wdl']
        score_odds = m['score_odds']
        sit = calc_score_implied_total(score_odds)
        if plan_name == 'Baseline':
            lh, la = calculate_lambda_v2(odds, score_odds)
        elif plan_name == 'PlanA':
            lh, la = calculate_lambda_planA(odds, score_odds)
        elif plan_name in ('PlanB', 'PlanC'):
            lh, la = calculate_lambda_v2(odds, score_odds)
        pp = poisson_predict_dc(lh, la)
        mc = monte_carlo_predict(lh, la)
        fused = fuse(pp, mc)
        if plan_name == 'PlanB' and sit is not None:
            fused = adjust_lowgoal_weights(fused, sit, alpha=alpha)
        elif plan_name == 'PlanC':
            p_low = p_low_map.get(m['match_id']) if p_low_map else None
            fused = adjust_lowgoal_weights_by_prior(fused, p_low, base_rate=base_rate, alpha=alpha)
        ex, w1, w2 = evaluate(m['actual_score'], fused)
        results.append({
            'match_id': m['match_id'],
            'actual_score': m['actual_score'],
            'total_goals': m['actual_total_goals'] if m['actual_total_goals'] is not None else (m['home_goals'] + m['away_goals']),
            'lambda_home': lh, 'lambda_away': la, 'lambda_total': lh + la,
            'score_implied_total': sit,
            'exact_hit': ex, 'within1_hit': w1, 'within2_hit': w2,
            'has_odds': odds is not None, 'has_score_odds': len(score_odds) > 0
        })
    elapsed = time.perf_counter() - t_start
    LOG.info(f'  [{plan_name}] 完成，耗时 {elapsed:.2f}s')
    return results


def summarize(results, plan_name):
    df = pd.DataFrame(results)
    total = len(df)
    ex = df['exact_hit'].sum()
    w1 = df['within1_hit'].sum()
    w2 = df['within2_hit'].sum()
    LOG.info(f'\n{"="*80}')
    LOG.info(f'【{plan_name}】回测结果 (n={total})')
    LOG.info(f'{"="*80}')
    LOG.info(f'  精确命中率: {ex}/{total} ({ex/total*100:.2f}%)')
    LOG.info(f'  1球内命中率: {w1}/{total} ({w1/total*100:.2f}%)')
    LOG.info(f'  2球内命中率: {w2}/{total} ({w2/total*100:.2f}%)')
    LOG.info(f'  平均 lambda_total: {df["lambda_total"].mean():.3f}')
    LOG.info(f'  lambda_total 标准差: {df["lambda_total"].std():.3f}')
    has_sit = df[df['score_implied_total'].notna()]
    if len(has_sit) > 0:
        LOG.info(f'  有 score_implied_total: {len(has_sit)} ({len(has_sit)/total*100:.1f}%)')
        LOG.info(f'  score_implied_total 均值: {has_sit["score_implied_total"].mean():.3f}')
        LOG.info(f'  score_implied_total 标准差: {has_sit["score_implied_total"].std():.3f}')

    # 按总进球数分布
    LOG.info(f'\n  按总进球数分布:')
    bins = [0, 2, 4, 6, 8, 15]
    labels = ['0-1球', '2-3球', '4-5球', '6-7球', '8+球']
    df['goal_bin'] = pd.cut(df['total_goals'], bins=bins, labels=labels, right=False)
    for label in labels:
        sub = df[df['goal_bin'] == label]
        if len(sub) > 0:
            s_ex = sub['exact_hit'].sum()
            s_w1 = sub['within1_hit'].sum()
            s_w2 = sub['within2_hit'].sum()
            LOG.info(f'    {label:>8}: n={len(sub):>4} | 精确={s_ex/len(sub)*100:5.1f}% | 1球内={s_w1/len(sub)*100:5.1f}% | 2球内={s_w2/len(sub)*100:5.1f}%')
    return {
        'plan': plan_name, 'total': total,
        'exact_rate': ex/total*100, 'within1_rate': w1/total*100, 'within2_rate': w2/total*100,
        'avg_lambda_total': df['lambda_total'].mean(),
        'std_lambda_total': df['lambda_total'].std(),
        'goal_0_1_within1': df[df['goal_bin']=='0-1球']['within1_hit'].mean()*100 if len(df[df['goal_bin']=='0-1球']) > 0 else 0,
        'goal_0_1_exact': df[df['goal_bin']=='0-1球']['exact_hit'].mean()*100 if len(df[df['goal_bin']=='0-1球']) > 0 else 0,
        'goal_2_3_within1': df[df['goal_bin']=='2-3球']['within1_hit'].mean()*100 if len(df[df['goal_bin']=='2-3球']) > 0 else 0,
        'goal_4_5_within1': df[df['goal_bin']=='4-5球']['within1_hit'].mean()*100 if len(df[df['goal_bin']=='4-5球']) > 0 else 0,
    }


def main():
    LOG.info('=' * 80)
    LOG.info('T-006 集成方案对比实验')
    LOG.info('  Baseline: v2 原始 (lambda_total 恒=2.5)')
    LOG.info('  PlanA: lambda_total = score_implied_total')
    LOG.info('  PlanB: v2 lambda + 低比分权重后调整 (alpha=0.5)')
    LOG.info('  PlanC: v2 lambda + t006分类器 P(小球) 低比分重加权 (alpha=0.5)')
    LOG.info('=' * 80)
    LOG.info(f'日志路径: {LOG_PATH}')

    np.random.seed(42)
    conn = sqlite3.connect(DB_PATH)
    matches = batch_load_data(conn)
    p_low_map, base_rate = build_lowgoal_prior_map(conn)
    conn.close()

    # 运行四个方案（每个方案前重置随机种子，确保 MC 一致性）
    np.random.seed(42)
    baseline_results = run_plan(matches, 'Baseline')
    np.random.seed(42)
    planA_results = run_plan(matches, 'PlanA')
    np.random.seed(42)
    planB_results = run_plan(matches, 'PlanB')
    np.random.seed(42)
    planC_results = run_plan(matches, 'PlanC', p_low_map=p_low_map, base_rate=base_rate)

    # 汇总对比
    s_base = summarize(baseline_results, 'Baseline (v2)')
    s_A = summarize(planA_results, 'PlanA (lambda=score_implied_total)')
    s_B = summarize(planB_results, 'PlanB (低比分权重调整 α=0.5)')
    s_C = summarize(planC_results, 'PlanC (t006分类器 P小球 α=0.5)')

    LOG.info('\n' + '=' * 80)
    LOG.info('【四方案汇总对比】')
    LOG.info('=' * 80)
    LOG.info(f'{"指标":<20} | {"Baseline":>9} | {"PlanA":>9} | {"PlanB":>9} | {"PlanC":>9}')
    LOG.info('-' * 80)
    LOG.info(f'{"精确命中率":<20} | {s_base["exact_rate"]:>8.2f}% | {s_A["exact_rate"]:>8.2f}% | {s_B["exact_rate"]:>8.2f}% | {s_C["exact_rate"]:>8.2f}%')
    LOG.info(f'{"1球内命中率":<20} | {s_base["within1_rate"]:>8.2f}% | {s_A["within1_rate"]:>8.2f}% | {s_B["within1_rate"]:>8.2f}% | {s_C["within1_rate"]:>8.2f}%')
    LOG.info(f'{"2球内命中率":<20} | {s_base["within2_rate"]:>8.2f}% | {s_A["within2_rate"]:>8.2f}% | {s_B["within2_rate"]:>8.2f}% | {s_C["within2_rate"]:>8.2f}%')
    LOG.info(f'{"lambda_total 均值":<20} | {s_base["avg_lambda_total"]:>8.3f}  | {s_A["avg_lambda_total"]:>8.3f}  | {s_B["avg_lambda_total"]:>8.3f}  | {s_C["avg_lambda_total"]:>8.3f}')
    LOG.info(f'{"lambda_total 标准差":<20} | {s_base["std_lambda_total"]:>8.3f}  | {s_A["std_lambda_total"]:>8.3f}  | {s_B["std_lambda_total"]:>8.3f}  | {s_C["std_lambda_total"]:>8.3f}')
    LOG.info('-' * 80)
    LOG.info(f'{"0-1球区间 1球内":<20} | {s_base["goal_0_1_within1"]:>8.1f}% | {s_A["goal_0_1_within1"]:>8.1f}% | {s_B["goal_0_1_within1"]:>8.1f}% | {s_C["goal_0_1_within1"]:>8.1f}%')
    LOG.info(f'{"0-1球区间 精确":<20} | {s_base["goal_0_1_exact"]:>8.1f}% | {s_A["goal_0_1_exact"]:>8.1f}% | {s_B["goal_0_1_exact"]:>8.1f}% | {s_C["goal_0_1_exact"]:>8.1f}%')
    LOG.info(f'{"2-3球区间 1球内":<20} | {s_base["goal_2_3_within1"]:>8.1f}% | {s_A["goal_2_3_within1"]:>8.1f}% | {s_B["goal_2_3_within1"]:>8.1f}% | {s_C["goal_2_3_within1"]:>8.1f}%')
    LOG.info(f'{"4-5球区间 1球内":<20} | {s_base["goal_4_5_within1"]:>8.1f}% | {s_A["goal_4_5_within1"]:>8.1f}% | {s_B["goal_4_5_within1"]:>8.1f}% | {s_C["goal_4_5_within1"]:>8.1f}%')

    # 决策
    LOG.info('\n' + '=' * 80)
    LOG.info('【决策分析】')
    LOG.info('=' * 80)
    plans = {'Baseline': s_base, 'PlanA': s_A, 'PlanB': s_B, 'PlanC': s_C}

    best_w1 = max(plans.items(), key=lambda kv: kv[1]['within1_rate'])
    LOG.info(f'  整体1球内命中率最优: {best_w1[0]} ({best_w1[1]["within1_rate"]:.2f}%)')

    best_01 = max(plans.items(), key=lambda kv: kv[1]['goal_0_1_within1'])
    LOG.info(f'  0-1球区间1球内命中率最优: {best_01[0]} ({best_01[1]["goal_0_1_within1"]:.1f}%)')

    # 检查是否损害 2-3球区间
    base_23 = s_base['goal_2_3_within1']
    LOG.info(f'\n  2-3球区间1球内命中率影响:')
    for name, s in plans.items():
        LOG.info(f'    {name:<10} = {s["goal_2_3_within1"]:.1f}% (Δ={s["goal_2_3_within1"]-base_23:+.1f}pp)')

    # 关键对比：PlanC vs PlanB（分类器是否跑赢标量 score_implied_total）
    LOG.info(f'\n  PlanC vs PlanB 关键对比（目标：分类器是否跑赢标量 score_implied_total）:')
    LOG.info(f'    0-1球区间1球内: PlanB={s_B["goal_0_1_within1"]:.1f}% → PlanC={s_C["goal_0_1_within1"]:.1f}% (Δ={s_C["goal_0_1_within1"]-s_B["goal_0_1_within1"]:+.1f}pp)')
    LOG.info(f'    0-1球区间精确: PlanB={s_B["goal_0_1_exact"]:.1f}% → PlanC={s_C["goal_0_1_exact"]:.1f}% (Δ={s_C["goal_0_1_exact"]-s_B["goal_0_1_exact"]:+.1f}pp)')

    LOG.info(f'\n完成! 完整日志: {LOG_PATH}')


if __name__ == '__main__':
    main()
