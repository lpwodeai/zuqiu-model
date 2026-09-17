import sqlite3
import pandas as pd
import numpy as np
import json
import os
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"
OUTPUT_DIR = BASE_DIR / "assets"

REAL_FOOTBALL_STATS = {
    'home_win_rate': (0.42, 0.48),
    'away_win_rate': (0.30, 0.36),
    'draw_rate': (0.18, 0.24),
    'avg_home_goals': (1.4, 1.7),
    'avg_away_goals': (1.1, 1.4),
    'avg_total_goals': (2.5, 3.1),
    'max_goals_per_match': 10,
    'xg_range': (0, 6),
    'possession_range': (30, 70),
    'corners_range': (0, 20)
}

def load_all_data():
    conn = sqlite3.connect(DB_PATH)
    
    matches_query = """
    SELECT m.*, ht.name as home_team_name, at.name as away_team_name, c.name as competition_name
    FROM matches m
    LEFT JOIN teams ht ON m.homeTeamId = ht.id
    LEFT JOIN teams at ON m.awayTeamId = at.id
    LEFT JOIN competitions c ON m.competitionId = c.id
    """
    matches_df = pd.read_sql(matches_query, conn)
    
    teams_df = pd.read_sql("SELECT * FROM teams", conn)
    competitions_df = pd.read_sql("SELECT * FROM competitions", conn)
    
    conn.close()
    
    matches_df['date'] = pd.to_datetime(matches_df['date'])
    matches_df['result'] = np.where(matches_df['homeGoals'] > matches_df['awayGoals'], 2,
                                   np.where(matches_df['homeGoals'] < matches_df['awayGoals'], 0, 1))
    matches_df['goal_diff'] = matches_df['homeGoals'] - matches_df['awayGoals']
    matches_df['total_goals'] = matches_df['homeGoals'] + matches_df['awayGoals']
    
    return matches_df, teams_df, competitions_df

def assess_completeness(df, table_name):
    print(f"\n=== {table_name} 完整性评估 ===")
    
    total_rows = len(df)
    missing_stats = df.isnull().sum()
    completeness_score = 100
    
    for col, count in missing_stats.items():
        if count > 0:
            pct = count / total_rows * 100
            completeness_score -= pct * 0.5
            print(f"  {col}: {count} 缺失 ({pct:.2f}%)")
    
    completeness_score = max(0, min(100, completeness_score))
    print(f"  完整性得分: {completeness_score:.2f}/100")
    
    return {
        'total_rows': total_rows,
        'missing_stats': missing_stats.to_dict(),
        'completeness_score': completeness_score
    }

def assess_match_distribution(df):
    print("\n=== 比赛结果分布评估 ===")
    
    results = {}
    
    result_counts = df['result'].value_counts()
    total = len(df)
    
    home_win_rate = result_counts.get(2, 0) / total
    away_win_rate = result_counts.get(0, 0) / total
    draw_rate = result_counts.get(1, 0) / total
    
    print(f"  主胜率: {home_win_rate:.4f} ({result_counts.get(2, 0)}场)")
    print(f"  客胜率: {away_win_rate:.4f} ({result_counts.get(0, 0)}场)")
    print(f"  平局率: {draw_rate:.4f} ({result_counts.get(1, 0)}场)")
    
    home_ok = REAL_FOOTBALL_STATS['home_win_rate'][0] <= home_win_rate <= REAL_FOOTBALL_STATS['home_win_rate'][1]
    away_ok = REAL_FOOTBALL_STATS['away_win_rate'][0] <= away_win_rate <= REAL_FOOTBALL_STATS['away_win_rate'][1]
    draw_ok = REAL_FOOTBALL_STATS['draw_rate'][0] <= draw_rate <= REAL_FOOTBALL_STATS['draw_rate'][1]
    
    distribution_score = 0
    if home_ok: distribution_score += 33
    else:
        print(f"    ⚠️ 主胜率异常 (期望范围: {REAL_FOOTBALL_STATS['home_win_rate']})")
    
    if away_ok: distribution_score += 33
    else:
        print(f"    ⚠️ 客胜率异常 (期望范围: {REAL_FOOTBALL_STATS['away_win_rate']})")
    
    if draw_ok: distribution_score += 34
    else:
        print(f"    ⚠️ 平局率异常 (期望范围: {REAL_FOOTBALL_STATS['draw_rate']})")
    
    print(f"  分布得分: {distribution_score}/100")
    
    results['home_win_rate'] = home_win_rate
    results['away_win_rate'] = away_win_rate
    results['draw_rate'] = draw_rate
    results['distribution_score'] = distribution_score
    results['is_symmetric'] = int(abs(home_win_rate - away_win_rate) < 0.02)
    
    avg_home_goals = df['homeGoals'].mean()
    avg_away_goals = df['awayGoals'].mean()
    avg_total_goals = df['total_goals'].mean()
    
    print(f"\n  场均进球:")
    print(f"    主场: {avg_home_goals:.2f}")
    print(f"    客场: {avg_away_goals:.2f}")
    print(f"    总计: {avg_total_goals:.2f}")
    
    goals_ok = REAL_FOOTBALL_STATS['avg_home_goals'][0] <= avg_home_goals <= REAL_FOOTBALL_STATS['avg_home_goals'][1]
    if not goals_ok:
        print(f"    ⚠️ 主场进球异常 (期望范围: {REAL_FOOTBALL_STATS['avg_home_goals']})")
    
    results['avg_home_goals'] = avg_home_goals
    results['avg_away_goals'] = avg_away_goals
    results['avg_total_goals'] = avg_total_goals
    
    return results

def assess_league_distribution(df):
    print("\n=== 联赛分布评估 ===")
    
    league_counts = df['competition_name'].value_counts()
    results = {}
    
    for league, count in league_counts.items():
        league_df = df[df['competition_name'] == league]
        
        home_win_rate = (league_df['homeGoals'] > league_df['awayGoals']).mean()
        away_win_rate = (league_df['homeGoals'] < league_df['awayGoals']).mean()
        draw_rate = (league_df['homeGoals'] == league_df['awayGoals']).mean()
        
        avg_home_goals = league_df['homeGoals'].mean()
        avg_away_goals = league_df['awayGoals'].mean()
        
        print(f"\n  {league}: {count}场")
        print(f"    主胜率: {home_win_rate:.4f}, 客胜率: {away_win_rate:.4f}, 平局率: {draw_rate:.4f}")
        print(f"    场均进球: 主场{avg_home_goals:.2f}, 客场{avg_away_goals:.2f}")
        
        is_suspicious = abs(home_win_rate - away_win_rate) < 0.02
        if is_suspicious:
            print(f"    ⚠️ 主客场胜率过于接近，可能为合成数据")
        
        results[league] = {
            'matches': count,
            'home_win_rate': home_win_rate,
            'away_win_rate': away_win_rate,
            'draw_rate': draw_rate,
            'avg_home_goals': avg_home_goals,
            'avg_away_goals': avg_away_goals,
            'is_suspicious': int(is_suspicious)
        }
    
    return results

def assess_numerical_features(df):
    print("\n=== 数值特征评估 ===")
    
    features = {
        'homeXg': REAL_FOOTBALL_STATS['xg_range'],
        'awayXg': REAL_FOOTBALL_STATS['xg_range'],
        'homePossession': REAL_FOOTBALL_STATS['possession_range'],
        'homeCorners': REAL_FOOTBALL_STATS['corners_range'],
        'awayCorners': REAL_FOOTBALL_STATS['corners_range'],
        'homeGoals': (0, REAL_FOOTBALL_STATS['max_goals_per_match']),
        'awayGoals': (0, REAL_FOOTBALL_STATS['max_goals_per_match'])
    }
    
    results = {}
    anomalies_found = 0
    
    for feature, expected_range in features.items():
        if feature not in df.columns:
            continue
        
        series = df[feature].dropna()
        if len(series) == 0:
            continue
        
        min_val = series.min()
        max_val = series.max()
        mean_val = series.mean()
        std_val = series.std()
        
        out_of_range = ((series < expected_range[0]) | (series > expected_range[1])).sum()
        
        print(f"\n  {feature}:")
        print(f"    范围: [{min_val:.2f}, {max_val:.2f}] (期望: {expected_range})")
        print(f"    均值: {mean_val:.2f}, 标准差: {std_val:.2f}")
        print(f"    超出范围: {out_of_range}个 ({out_of_range/len(series)*100:.2f}%)")
        
        if out_of_range > 0:
            anomalies_found += 1
            print(f"    ⚠️ 存在异常值")
        
        results[feature] = {
            'min': min_val,
            'max': max_val,
            'mean': mean_val,
            'std': std_val,
            'out_of_range': int(out_of_range),
            'expected_range': expected_range
        }
    
    quality_score = max(0, 100 - anomalies_found * 10)
    print(f"\n  数值特征质量得分: {quality_score}/100")
    results['quality_score'] = quality_score
    
    return results

def assess_temporal_distribution(df):
    print("\n=== 时间分布评估 ===")
    
    df['month'] = df['date'].dt.month
    df['year'] = df['date'].dt.year
    df['weekday'] = df['date'].dt.dayofweek
    
    monthly_counts = df.groupby('month')['result'].count()
    weekday_counts = df.groupby('weekday')['result'].count()
    
    print(f"\n  月度分布:")
    for month, count in monthly_counts.sort_index().items():
        print(f"    {month}月: {count}场")
    
    print(f"\n  星期分布:")
    weekday_names = ['周一', '周二', '周三', '周四', '周五', '周六', '周日']
    for day, count in weekday_counts.sort_index().items():
        print(f"    {weekday_names[day]}: {count}场")
    
    weekend_matches = df[df['weekday'].isin([5, 6])].shape[0]
    weekday_matches = df[~df['weekday'].isin([5, 6])].shape[0]
    
    weekend_ratio = weekend_matches / len(df)
    print(f"\n  周末比赛比例: {weekend_ratio:.4f}")
    
    if weekend_ratio < 0.3:
        print(f"    ⚠️ 周末比赛比例异常低")
    
    return {
        'monthly_distribution': monthly_counts.to_dict(),
        'weekday_distribution': weekday_counts.to_dict(),
        'weekend_ratio': weekend_ratio,
        'date_range': f"{df['date'].min().strftime('%Y-%m-%d')} to {df['date'].max().strftime('%Y-%m-%d')}"
    }

def assess_team_data(teams_df, matches_df):
    print("\n=== 球队数据评估 ===")
    
    teams_with_matches = pd.concat([matches_df['home_team_name'], matches_df['away_team_name']]).unique()
    teams_in_db = teams_df['name'].unique()
    
    missing_in_matches = set(teams_in_db) - set(teams_with_matches)
    missing_in_db = set(teams_with_matches) - set(teams_in_db)
    
    print(f"  数据库球队数: {len(teams_df)}")
    print(f"  比赛中出现的球队数: {len(teams_with_matches)}")
    print(f"  数据库中有但无比赛的球队: {len(missing_in_matches)}")
    print(f"  比赛中有但数据库中缺失的球队: {len(missing_in_db)}")
    
    league_distribution = teams_df['league'].value_counts()
    print(f"\n  球队联赛分布:")
    for league, count in league_distribution.items():
        print(f"    {league}: {count}支")
    
    return {
        'total_teams': len(teams_df),
        'teams_with_matches': len(teams_with_matches),
        'missing_in_matches': list(missing_in_matches),
        'missing_in_db': list(missing_in_db),
        'league_distribution': league_distribution.to_dict()
    }

def detect_synthetic_patterns(df):
    print("\n=== 合成数据模式检测 ===")
    
    patterns = []
    
    home_away_symmetry = abs(df['homeGoals'].mean() - df['awayGoals'].mean()) < 0.1
    if home_away_symmetry:
        patterns.append("主客场进球数过于对称")
        print(f"  ✅ 检测到: 主客场进球数过于对称 (主场{df['homeGoals'].mean():.2f}, 客场{df['awayGoals'].mean():.2f})")
    
    draw_rate_extreme = df['result'].value_counts().get(1, 0) / len(df) < 0.15
    if draw_rate_extreme:
        patterns.append("平局率异常低")
        print(f"  ✅ 检测到: 平局率异常低")
    
    score_distribution = df.groupby(['homeGoals', 'awayGoals']).size().sort_values(ascending=False).head(5)
    if score_distribution.iloc[0] > len(df) * 0.05:
        patterns.append("特定比分出现频率过高")
        print(f"  ✅ 检测到: 特定比分出现频率过高")
    
    date_regularity = df['date'].diff().dt.days.value_counts().head(1)
    if date_regularity.iloc[0] > len(df) * 0.3:
        patterns.append("比赛日期规律性过强")
        print(f"  ✅ 检测到: 比赛日期规律性过强")
    
    is_synthetic = len(patterns) >= 2
    print(f"\n  合成数据判定: {'是' if is_synthetic else '否'}")
    
    return {
        'detected_patterns': patterns,
        'is_synthetic': int(is_synthetic),
        'pattern_count': len(patterns)
    }

def convert_numpy_types(obj):
    if isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, dict):
        return {k: convert_numpy_types(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_numpy_types(item) for item in obj]
    else:
        return obj

def generate_report(assessments):
    print("\n" + "=" * 70)
    print("数据质量评估报告")
    print("=" * 70)
    
    total_score = sum(a.get('completeness_score', a.get('distribution_score', a.get('quality_score', 50))) for a in assessments.values()) / len(assessments)
    
    print(f"\n综合评分: {total_score:.2f}/100")
    
    report = {
        'timestamp': datetime.now().isoformat(),
        'overall_score': total_score,
        'assessments': assessments
    }
    
    report = convert_numpy_types(report)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    output_path = os.path.join(OUTPUT_DIR, f"data_quality_report_{timestamp}.json")
    
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    
    print(f"\n报告已保存到: {output_path}")
    
    return report, total_score

def main():
    print("=" * 70)
    print("数据质量评估工具")
    print("=" * 70)
    
    print("\n1. 加载数据...")
    matches_df, teams_df, competitions_df = load_all_data()
    print(f"   比赛数据: {len(matches_df)}场")
    print(f"   球队数据: {len(teams_df)}支")
    print(f"   联赛数据: {len(competitions_df)}个")
    
    assessments = {}
    
    print("\n2. 完整性评估...")
    assessments['matches_completeness'] = assess_completeness(matches_df, 'matches')
    assessments['teams_completeness'] = assess_completeness(teams_df, 'teams')
    
    print("\n3. 比赛结果分布评估...")
    assessments['match_distribution'] = assess_match_distribution(matches_df)
    
    print("\n4. 联赛分布评估...")
    assessments['league_distribution'] = assess_league_distribution(matches_df)
    
    print("\n5. 数值特征评估...")
    assessments['numerical_features'] = assess_numerical_features(matches_df)
    
    print("\n6. 时间分布评估...")
    assessments['temporal_distribution'] = assess_temporal_distribution(matches_df)
    
    print("\n7. 球队数据评估...")
    assessments['team_data'] = assess_team_data(teams_df, matches_df)
    
    print("\n8. 合成数据模式检测...")
    assessments['synthetic_detection'] = detect_synthetic_patterns(matches_df)
    
    report, score = generate_report(assessments)
    
    print("\n" + "=" * 70)
    if score >= 80:
        print("数据质量评估: 优秀 ✅")
    elif score >= 60:
        print("数据质量评估: 良好 ⚠️")
    elif score >= 40:
        print("数据质量评估: 较差 ⚠️")
    else:
        print("数据质量评估: 严重不合格 ❌")
    print("=" * 70)
    
    return report, score

if __name__ == "__main__":
    main()