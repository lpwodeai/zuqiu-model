"""
T-006 0-1球区间特征分析脚本（P1方向前置分析）

目标：分析 0-1球区间（2,320场）的特征分布，找出对"小球"预测最显著的特征
为后续构建"小球先验"二分类器提供特征工程依据

分析维度：
1. WDL赔率隐含概率分布（小球 vs 非小球）
2. Lambda分布（小球 vs 非小球）
3. 平局赔率特征
4. 联赛分布
5. 主客队赔率不对称性
6. 比分赔率隐含总进球
7. 单变量AUC排名（识别最强特征）
"""
import sqlite3
import pandas as pd
import numpy as np
import os
import logging
from datetime import datetime
from scipy.stats import mannwhitneyu
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"
LOG_DIR = BASE_DIR / "logs"
MIN_CONFIDENCE = 0.85
LEAGUE_AVG_GOALS = 2.5
HOME_ADVANTAGE = 1.10

os.makedirs(LOG_DIR, exist_ok=True)
ts = datetime.now().strftime('%Y%m%d_%H%M%S')
LOG_PATH = os.path.join(LOG_DIR, f't006_lowgoal_analysis_{ts}.log')
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler(LOG_PATH, encoding='utf-8')
    ]
)
LOG = logging.getLogger('lowgoal_analysis')

def parse_score(score_str):
    if not score_str or ':' not in str(score_str):
        return None, None
    parts = str(score_str).split(':')
    try:
        return int(parts[0]), int(parts[1])
    except (ValueError, IndexError):
        return None, None

def load_data(conn):
    """加载所有高置信比赛 + WDL赔率 + Score赔率"""
    LOG.info('[load_data] 加载高置信比赛 + WDL + Score赔率...')
    cursor = conn.cursor()
    cursor.execute('''
        SELECT m.match_id, m.home_team, m.away_team, m.actual_score, m.match_type,
               mm.sh_match_id, mm.confidence
        FROM matches m
        INNER JOIN match_id_mapping mm ON mm.matches_match_id = m.match_id
            AND mm.confidence >= ?
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
            'actual_score': r[3], 'league': r[4] or 'Unknown',
            'sh_match_id': r[5], 'confidence': r[6],
            'home_goals': h, 'away_goals': a, 'total_goals': h + a
        })
    LOG.info(f'[load_data] 加载 {len(matches)} 场')
    # 批量预加载WDL（避免逐场查询）
    sh_ids = [m['sh_match_id'] for m in matches if m['sh_match_id']]
    LOG.info(f'[load_data] 批量加载WDL赔率 {len(sh_ids)} 条...')
    wdl_dict = {}
    if sh_ids:
        placeholders = ','.join('?' * len(sh_ids))
        cursor.execute(f'''
            SELECT match_id, win_a, draw, win_b, timestamp
            FROM wdl_history WHERE match_id IN ({placeholders})
        ''', sh_ids)
        for row in cursor.fetchall():
            mid, wa, d, wb, ts = row
            if mid not in wdl_dict or ts > wdl_dict[mid]['timestamp']:
                wdl_dict[mid] = {'win_a': wa, 'draw': d, 'win_b': wb, 'timestamp': ts}
    # 批量预加载Score赔率（取均值）
    LOG.info(f'[load_data] 批量加载Score赔率...')
    score_dict = {}
    if sh_ids:
        cursor.execute(f'''
            SELECT match_id, score, odds FROM score_history WHERE match_id IN ({placeholders})
        ''', sh_ids)
        tmp = {}
        for mid, score, odds in cursor.fetchall():
            if mid not in tmp:
                tmp[mid] = {}
            if score not in tmp[mid]:
                tmp[mid][score] = []
            tmp[mid][score].append(odds)
        for mid, sd in tmp.items():
            score_dict[mid] = {s: np.mean(o) for s, o in sd.items()}
    # 合并到matches
    for m in matches:
        wdl = wdl_dict.get(m['sh_match_id'])
        m['has_wdl'] = wdl is not None
        if wdl:
            m['win_a'] = wdl['win_a']
            m['draw'] = wdl['draw']
            m['win_b'] = wdl['win_b']
            # 隐含概率
            total_implied = 1.0 / wdl['win_a'] + 1.0 / wdl['draw'] + 1.0 / wdl['win_b']
            m['prob_home'] = (1.0 / wdl['win_a']) / total_implied
            m['prob_draw'] = (1.0 / wdl['draw']) / total_implied
            m['prob_away'] = (1.0 / wdl['win_b']) / total_implied
            m['margin'] = (total_implied - 1.0) * 100  # 博彩公司margin%
            # Lambda估计
            lh = LEAGUE_AVG_GOALS * m['prob_home'] * HOME_ADVANTAGE
            la = LEAGUE_AVG_GOALS * m['prob_away']
            tl = lh + la
            if tl > 0:
                lh = lh / tl * LEAGUE_AVG_GOALS
                la = la / tl * LEAGUE_AVG_GOALS
            m['lambda_home'] = lh
            m['lambda_away'] = la
            m['lambda_total'] = lh + la
            # 不对称性
            m['lambda_asymmetry'] = abs(lh - la) / (lh + la) if (lh + la) > 0 else 0
        else:
            m['win_a'] = m['draw'] = m['win_b'] = np.nan
            m['prob_home'] = m['prob_draw'] = m['prob_away'] = np.nan
            m['margin'] = np.nan
            m['lambda_home'] = m['lambda_away'] = m['lambda_total'] = np.nan
            m['lambda_asymmetry'] = np.nan
        # Score赔率隐含总进球
        sd = score_dict.get(m['sh_match_id'], {})
        m['has_score_odds'] = len(sd) > 0
        if sd:
            implied_total = 0.0
            total_prob = 0.0
            for score, odds_val in sd.items():
                h, a = parse_score(score)
                if h is not None:
                    p = 1.0 / odds_val
                    implied_total += (h + a) * p
                    total_prob += p
            m['score_implied_total'] = implied_total / total_prob if total_prob > 0 else np.nan
            # 平局赔率均值（比分0:0/1:1/2:2）
            draw_odds_list = [sd[s] for s in ['0:0', '1:1', '2:2', '3:3'] if s in sd]
            m['avg_draw_score_odds'] = np.mean(draw_odds_list) if draw_odds_list else np.nan
            # 0:0赔率
            m['odds_00'] = sd.get('0:0', np.nan)
            m['odds_11'] = sd.get('1:1', np.nan)
            m['odds_10'] = sd.get('1:0', np.nan)
            m['odds_01'] = sd.get('0:1', np.nan)
        else:
            m['score_implied_total'] = np.nan
            m['avg_draw_score_odds'] = np.nan
            m['odds_00'] = m['odds_11'] = m['odds_10'] = m['odds_01'] = np.nan
    return pd.DataFrame(matches)

def analyze_lowgoal_distribution(df):
    """分析0-1球区间特征分布"""
    LOG.info('\n' + '=' * 80)
    LOG.info('0-1球区间特征分布分析 (小球=total_goals<=1, 大球=total_goals>=2)')
    LOG.info('=' * 80)
    df_valid = df[df['has_wdl']].copy()
    df_valid['is_lowgoal'] = (df_valid['total_goals'] <= 1).astype(int)
    n_low = df_valid['is_lowgoal'].sum()
    n_high = len(df_valid) - n_low
    LOG.info(f'\n有效样本（有WDL赔率）: {len(df_valid)}')
    LOG.info(f'  小球(0-1球): {n_low} ({n_low/len(df_valid)*100:.1f}%)')
    LOG.info(f'  大球(2+球):  {n_high} ({n_high/len(df_valid)*100:.1f}%)')
    LOG.info(f'  基线准确率(全猜小球): {n_low/len(df_valid)*100:.1f}%')
    LOG.info(f'  基线准确率(全猜大球): {n_high/len(df_valid)*100:.1f}%')
    # === 1. 数值特征分布对比（Mann-Whitney U检验）===
    LOG.info('\n' + '-' * 80)
    LOG.info('【1】数值特征分布对比 (小球 vs 大球) + Mann-Whitney U 检验')
    LOG.info('-' * 80)
    LOG.info(f'{"特征":<24} | {"小球均值":<10} | {"大球均值":<10} | {"方向":<6} | {"U统计量":<12} | {"p值":<12} | {"显著性":<8}')
    LOG.info('-' * 100)
    numeric_features = [
        'lambda_total', 'lambda_home', 'lambda_away', 'lambda_asymmetry',
        'prob_home', 'prob_draw', 'prob_away', 'margin',
        'draw', 'win_a', 'win_b',
        'score_implied_total', 'avg_draw_score_odds',
        'odds_00', 'odds_11', 'odds_10', 'odds_01'
    ]
    feature_stats = []
    for feat in numeric_features:
        sub = df_valid[[feat, 'is_lowgoal']].dropna()
        if len(sub) < 50:
            continue
        low_vals = sub[sub['is_lowgoal'] == 1][feat]
        high_vals = sub[sub['is_lowgoal'] == 0][feat]
        if len(low_vals) < 10 or len(high_vals) < 10:
            continue
        mean_low = low_vals.mean()
        mean_high = high_vals.mean()
        direction = '小球↑' if mean_low > mean_high else '小球↓'
        try:
            u_stat, p_val = mannwhitneyu(low_vals, high_vals, alternative='two-sided')
        except Exception:
            u_stat, p_val = 0, 1.0
        if p_val < 0.001:
            sig = '***极显著'
        elif p_val < 0.01:
            sig = '**显著'
        elif p_val < 0.05:
            sig = '*弱显著'
        else:
            sig = '不显著'
        LOG.info(f'{feat:<24} | {mean_low:<10.4f} | {mean_high:<10.4f} | {direction:<6} | {u_stat:<12.1f} | {p_val:<12.6f} | {sig:<8}')
        feature_stats.append({'feature': feat, 'mean_low': mean_low, 'mean_high': mean_high, 'p_val': p_val, 'abs_diff': abs(mean_low - mean_high)})
    # === 2. 单变量AUC排名（最强预测特征）===
    LOG.info('\n' + '-' * 80)
    LOG.info('【2】单变量 AUC 排名（识别最强小球预测特征）')
    LOG.info('-' * 80)
    from sklearn.metrics import roc_auc_score
    auc_results = []
    for feat in numeric_features:
        sub = df_valid[[feat, 'is_lowgoal']].dropna()
        if len(sub) < 50:
            continue
        try:
            auc = roc_auc_score(sub['is_lowgoal'], sub[feat])
            # AUC<0.5 表示反向预测，取max(auc, 1-auc)作为区分能力
            auc_disc = max(auc, 1 - auc)
            direction = '正向(高→小球)' if auc > 0.5 else '反向(低→小球)'
            auc_results.append({'feature': feat, 'auc': auc, 'auc_discrimination': auc_disc, 'direction': direction})
        except Exception:
            continue
    auc_results = pd.DataFrame(auc_results).sort_values('auc_discrimination', ascending=False).to_dict('records')
    LOG.info(f'{"排名":<4} | {"特征":<24} | {"AUC":<8} | {"区分力":<10} | {"方向":<20}')
    LOG.info('-' * 80)
    for i, r in enumerate(auc_results, 1):
        LOG.info(f'{i:<4} | {r["feature"]:<24} | {r["auc"]:.4f}   | {r["auc_discrimination"]:.4f}     | {r["direction"]:<20}')
    # === 3. 联赛分布 ===
    LOG.info('\n' + '-' * 80)
    LOG.info('【3】联赛小球率分布')
    LOG.info('-' * 80)
    league_stats = df_valid.groupby('league').agg(
        n=('is_lowgoal', 'count'),
        lowgoal_rate=('is_lowgoal', 'mean'),
        avg_lambda_total=('lambda_total', 'mean')
    ).sort_values('lowgoal_rate', ascending=False)
    LOG.info(f'{"联赛":<30} | {"场数":<6} | {"小球率":<8} | {"平均λ总":<8}')
    LOG.info('-' * 60)
    for league, row in league_stats.iterrows():
        if row['n'] >= 20:
            LOG.info(f'{league[:30]:<30} | {int(row["n"]):<6} | {row["lowgoal_rate"]*100:5.1f}%   | {row["avg_lambda_total"]:.3f}')
    # === 4. 分箱分析：lambda_total 分段小球率 ===
    LOG.info('\n' + '-' * 80)
    LOG.info('【4】Lambda总进球分箱 → 小球率')
    LOG.info('-' * 80)
    df_valid['lambda_bin'] = pd.cut(df_valid['lambda_total'], bins=[0, 1.5, 2.0, 2.5, 3.0, 3.5, 5.0], labels=['<1.5', '1.5-2.0', '2.0-2.5', '2.5-3.0', '3.0-3.5', '>=3.5'])
    bin_stats = df_valid.groupby('lambda_bin').agg(
        n=('is_lowgoal', 'count'),
        lowgoal_rate=('is_lowgoal', 'mean')
    )
    LOG.info(f'{"λ总区间":<12} | {"场数":<6} | {"小球率":<8} | 可视化')
    LOG.info('-' * 60)
    for b, row in bin_stats.iterrows():
        bar = '█' * int(row['lowgoal_rate'] * 50)
        LOG.info(f'{str(b):<12} | {int(row["n"]):<6} | {row["lowgoal_rate"]*100:5.1f}%   | {bar}')
    # === 5. 平局赔率分箱 ===
    LOG.info('\n' + '-' * 80)
    LOG.info('【5】平局赔率分箱 → 小球率')
    LOG.info('-' * 80)
    df_valid['draw_bin'] = pd.cut(df_valid['draw'], bins=[0, 3.0, 3.3, 3.5, 4.0, 5.0, 10.0], labels=['<3.0', '3.0-3.3', '3.3-3.5', '3.5-4.0', '4.0-5.0', '>=5.0'])
    draw_stats = df_valid.groupby('draw_bin').agg(
        n=('is_lowgoal', 'count'),
        lowgoal_rate=('is_lowgoal', 'mean')
    )
    LOG.info(f'{"平局赔率":<12} | {"场数":<6} | {"小球率":<8} | 可视化')
    LOG.info('-' * 60)
    for b, row in draw_stats.iterrows():
        bar = '█' * int(row['lowgoal_rate'] * 50)
        LOG.info(f'{str(b):<12} | {int(row["n"]):<6} | {row["lowgoal_rate"]*100:5.1f}%   | {bar}')
    # === 6. 总结：推荐特征 ===
    LOG.info('\n' + '=' * 80)
    LOG.info('【总结】推荐用于"小球先验"分类器的特征（按 AUC 区分力排序）')
    LOG.info('=' * 80)
    top_n = 10
    LOG.info(f'Top-{top_n} 特征:')
    for i, r in enumerate(auc_results[:top_n], 1):
        LOG.info(f'  {i}. {r["feature"]:<24} AUC={r["auc"]:.4f} 区分力={r["auc_discrimination"]:.4f} {r["direction"]}')
    LOG.info(f'\n特征工程建议:')
    LOG.info('  1. 一阶特征: lambda_total, draw, score_implied_total, lambda_asymmetry')
    LOG.info('  2. 二阶特征: draw/lambda_total 比值, prob_draw*lambda_total, (1-prob_draw)*lambda_asymmetry')
    LOG.info('  3. 比分赔率特征: odds_00, odds_11, avg_draw_score_odds')
    LOG.info('  4. 联赛one-hot: 法甲/意甲小球率显著高, 德甲/英超显著低')
    return feature_stats, auc_results, league_stats

def main():
    LOG.info(f'日志路径: {LOG_PATH}')
    conn = sqlite3.connect(DB_PATH)
    df = load_data(conn)
    conn.close()
    feature_stats, auc_results, league_stats = analyze_lowgoal_distribution(df)
    LOG.info(f'\n分析完成。完整日志: {LOG_PATH}')

if __name__ == '__main__':
    main()
