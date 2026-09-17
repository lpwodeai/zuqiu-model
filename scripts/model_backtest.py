import sqlite3
import pandas as pd
import numpy as np
import json
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "five_leagues.db"
ASSETS_DIR = BASE_DIR / "assets"

def load_match_data():
    conn = sqlite3.connect(DB_PATH)
    query = """
    SELECT 
        m.date,
        ht.name as home_team_name,
        at.name as away_team_name,
        m.homeGoals,
        m.awayGoals,
        c.name as competition_name,
        m.homeXg,
        m.awayXg,
        m.homePossession,
        m.homeCorners,
        m.awayCorners
    FROM matches m
    LEFT JOIN teams ht ON m.homeTeamId = ht.id
    LEFT JOIN teams at ON m.awayTeamId = at.id
    LEFT JOIN competitions c ON m.competitionId = c.id
    WHERE m.homeGoals IS NOT NULL AND m.awayGoals IS NOT NULL
    ORDER BY m.date
    """
    df = pd.read_sql(query, conn)
    conn.close()
    
    df['date'] = pd.to_datetime(df['date'])
    df['result'] = np.where(df['homeGoals'] > df['awayGoals'], 2,
                           np.where(df['homeGoals'] < df['awayGoals'], 0, 1))
    df['goal_diff'] = df['homeGoals'] - df['awayGoals']
    df['total_goals'] = df['homeGoals'] + df['awayGoals']
    
    return df

def build_basic_features(df):
    features = pd.DataFrame()
    
    league_dummies = pd.get_dummies(df['competition_name'], prefix='league')
    features = pd.concat([features, league_dummies], axis=1)
    
    features['month'] = df['date'].dt.month
    features['day_of_week'] = df['date'].dt.dayofweek
    features['is_weekend'] = (df['date'].dt.dayofweek >= 5).astype(int)
    
    features['is_early_season'] = (df['date'].dt.month.isin([8, 9])).astype(int)
    features['is_mid_season'] = (df['date'].dt.month.isin([10, 11, 12, 1, 2])).astype(int)
    features['is_late_season'] = (df['date'].dt.month.isin([3, 4, 5])).astype(int)
    
    features['home_xg'] = df['homeXg'].fillna(1.0)
    features['away_xg'] = df['awayXg'].fillna(1.0)
    features['xg_diff'] = features['home_xg'] - features['away_xg']
    features['total_xg'] = features['home_xg'] + features['away_xg']
    features['possession_diff'] = df['homePossession'].fillna(50) - 50
    features['corner_diff'] = df['homeCorners'].fillna(5) - df['awayCorners'].fillna(5)
    
    features = features.fillna(0)
    return features

def compute_team_stats(df):
    team_stats = {}
    
    for team in pd.concat([df['home_team_name'], df['away_team_name']]).unique():
        team_matches = df[(df['home_team_name'] == team) | (df['away_team_name'] == team)].sort_values('date').copy()
        
        is_home = team_matches['home_team_name'] == team
        team_goals = np.where(is_home, team_matches['homeGoals'], team_matches['awayGoals'])
        opp_goals = np.where(is_home, team_matches['awayGoals'], team_matches['homeGoals'])
        
        team_matches['team_goals'] = team_goals
        team_matches['opp_goals'] = opp_goals
        team_matches['is_win'] = (team_goals > opp_goals).astype(int)
        
        team_matches['cum_wins'] = team_matches['is_win'].cumsum()
        team_matches['cum_goals'] = team_matches['team_goals'].cumsum()
        team_matches['cum_opp_goals'] = team_matches['opp_goals'].cumsum()
        team_matches['games_played'] = np.arange(1, len(team_matches) + 1)
        
        team_matches['win_rate'] = team_matches['cum_wins'] / team_matches['games_played']
        team_matches['avg_goals'] = team_matches['cum_goals'] / team_matches['games_played']
        team_matches['avg_opp_goals'] = team_matches['cum_opp_goals'] / team_matches['games_played']
        team_matches['recent_form'] = team_matches['is_win'].rolling(window=5, min_periods=1).mean()
        
        team_stats[team] = team_matches
    
    return team_stats

def build_team_features(df):
    team_stats = compute_team_stats(df)
    
    home_data = []
    away_data = []
    
    for idx, row in df.iterrows():
        home_team = row['home_team_name']
        away_team = row['away_team_name']
        match_date = row['date']
        
        home_hist = team_stats.get(home_team, pd.DataFrame())
        away_hist = team_stats.get(away_team, pd.DataFrame())
        
        home_before = home_hist[home_hist['date'] < match_date]
        away_before = away_hist[away_hist['date'] < match_date]
        
        if len(home_before) > 0:
            h_last = home_before.iloc[-1]
            home_stats = {
                'home_win_rate': h_last['win_rate'],
                'home_avg_goals': h_last['avg_goals'],
                'home_avg_opp_goals': h_last['avg_opp_goals'],
                'home_recent_form': h_last['recent_form']
            }
        else:
            home_stats = {
                'home_win_rate': 0.33,
                'home_avg_goals': 1.5,
                'home_avg_opp_goals': 1.5,
                'home_recent_form': 0.5
            }
        
        if len(away_before) > 0:
            a_last = away_before.iloc[-1]
            away_stats = {
                'away_win_rate': a_last['win_rate'],
                'away_avg_goals': a_last['avg_goals'],
                'away_avg_opp_goals': a_last['avg_opp_goals'],
                'away_recent_form': a_last['recent_form']
            }
        else:
            away_stats = {
                'away_win_rate': 0.33,
                'away_avg_goals': 1.5,
                'away_avg_opp_goals': 1.5,
                'away_recent_form': 0.5
            }
        
        home_data.append(home_stats)
        away_data.append(away_stats)
    
    home_df = pd.DataFrame(home_data, index=df.index)
    away_df = pd.DataFrame(away_data, index=df.index)
    
    team_features = pd.concat([home_df, away_df], axis=1)
    team_features['form_diff'] = team_features['home_win_rate'] - team_features['away_win_rate']
    team_features['goals_diff'] = team_features['home_avg_goals'] - team_features['away_avg_goals']
    team_features['recent_form_diff'] = team_features['home_recent_form'] - team_features['away_recent_form']
    
    return team_features

def train_baseline_model(X, y):
    from sklearn.linear_model import LogisticRegression
    from sklearn.ensemble import RandomForestClassifier
    from sklearn.preprocessing import StandardScaler
    
    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)
    
    lr_model = LogisticRegression(solver='lbfgs', max_iter=200, class_weight='balanced')
    lr_model.fit(X_scaled, y)
    
    rf_model = RandomForestClassifier(n_estimators=100, max_depth=8, class_weight='balanced', random_state=42)
    rf_model.fit(X_scaled, y)
    
    return lr_model, rf_model, scaler

def evaluate_model(model, X, y, model_name):
    from sklearn.metrics import accuracy_score, log_loss, brier_score_loss, confusion_matrix
    
    y_pred = model.predict(X)
    y_proba = model.predict_proba(X)
    
    accuracy = accuracy_score(y, y_pred)
    ll = log_loss(y, y_proba)
    brier = brier_score_loss(y, y_proba, pos_label=2)
    
    cm = confusion_matrix(y, y_pred)
    
    return {
        'model': model_name,
        'accuracy': accuracy,
        'log_loss': ll,
        'brier_score': brier,
        'confusion_matrix': cm.tolist(),
        'class_accuracy': [
            cm[0, 0] / cm[0].sum() if cm[0].sum() > 0 else 0,
            cm[1, 1] / cm[1].sum() if cm[1].sum() > 0 else 0,
            cm[2, 2] / cm[2].sum() if cm[2].sum() > 0 else 0
        ]
    }

def run_time_series_backtest(X, y, df, n_splits=5):
    from sklearn.model_selection import TimeSeriesSplit
    
    tscv = TimeSeriesSplit(n_splits=n_splits)
    
    results = []
    
    print(f"\n{'='*60}")
    print(f"时间序列交叉验证 ({n_splits}折)")
    print(f"{'='*60}")
    
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        print(f"\n--- 第 {fold+1}/{n_splits} 折 ---")
        
        X_train, X_val = X.iloc[train_idx], X.iloc[val_idx]
        y_train, y_val = y.iloc[train_idx], y.iloc[val_idx]
        
        train_date_range = f"{df['date'].iloc[train_idx].min().strftime('%Y-%m')} ~ {df['date'].iloc[train_idx].max().strftime('%Y-%m')}"
        val_date_range = f"{df['date'].iloc[val_idx].min().strftime('%Y-%m')} ~ {df['date'].iloc[val_idx].max().strftime('%Y-%m')}"
        
        print(f"  训练期: {train_date_range} ({len(X_train)}场)")
        print(f"  验证期: {val_date_range} ({len(X_val)}场)")
        
        lr_model, rf_model, scaler = train_baseline_model(X_train, y_train)
        
        X_val_scaled = scaler.transform(X_val)
        
        lr_result = evaluate_model(lr_model, X_val_scaled, y_val, 'LogisticRegression')
        rf_result = evaluate_model(rf_model, X_val_scaled, y_val, 'RandomForest')
        
        lr_result['fold'] = fold
        rf_result['fold'] = fold
        lr_result['train_date'] = train_date_range
        rf_result['train_date'] = val_date_range
        
        results.append(lr_result)
        results.append(rf_result)
        
        print(f"  LogisticRegression - Accuracy: {lr_result['accuracy']:.4f}")
        print(f"  RandomForest - Accuracy: {rf_result['accuracy']:.4f}")
    
    return results

def analyze_league_performance(X, y, df):
    from sklearn.metrics import accuracy_score
    
    results = {}
    
    for league in df['competition_name'].unique():
        league_mask = df['competition_name'] == league
        X_league = X[league_mask]
        y_league = y[league_mask]
        
        if len(X_league) < 100:
            continue
        
        from sklearn.linear_model import LogisticRegression
        from sklearn.preprocessing import StandardScaler
        
        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X_league)
        
        lr_model = LogisticRegression(solver='lbfgs', max_iter=200)
        lr_model.fit(X_scaled, y_league)
        
        y_pred = lr_model.predict(X_scaled)
        accuracy = accuracy_score(y_league, y_pred)
        
        results[league] = {
            'matches': len(X_league),
            'accuracy': accuracy,
            'result_distribution': y_league.value_counts().to_dict()
        }
    
    return results

def poisson_baseline(df):
    from scipy.stats import poisson
    
    league_stats = {}
    for league in df['competition_name'].unique():
        league_df = df[df['competition_name'] == league]
        league_stats[league] = {
            'home_avg': league_df['homeGoals'].mean(),
            'away_avg': league_df['awayGoals'].mean()
        }
    
    predictions = []
    for _, row in df.iterrows():
        league = row['competition_name']
        stats = league_stats.get(league, {'home_avg': 1.5, 'away_avg': 1.2})
        
        lambda_home = stats['home_avg']
        lambda_away = stats['away_avg']
        
        win_prob = 0
        draw_prob = 0
        lose_prob = 0
        
        for h in range(0, 8):
            for a in range(0, 8):
                prob = poisson.pmf(h, lambda_home) * poisson.pmf(a, lambda_away)
                if h > a:
                    win_prob += prob
                elif h == a:
                    draw_prob += prob
                else:
                    lose_prob += prob
        
        total = win_prob + draw_prob + lose_prob
        if total > 0:
            win_prob /= total
            draw_prob /= total
            lose_prob /= total
        
        pred_class = np.argmax([lose_prob, draw_prob, win_prob])
        predictions.append(pred_class)
    
    from sklearn.metrics import accuracy_score
    accuracy = accuracy_score(df['result'], predictions)
    
    return {
        'model': 'Poisson Baseline',
        'accuracy': accuracy,
        'league_stats': league_stats
    }

def main():
    print("=" * 60)
    print("足球预测模型回测评估报告")
    print("=" * 60)
    
    print("\n1. 加载比赛数据...")
    df = load_match_data()
    print(f"   共加载 {len(df)} 场比赛")
    print(f"   日期范围: {df['date'].min().strftime('%Y-%m-%d')} 至 {df['date'].max().strftime('%Y-%m-%d')}")
    
    print("\n2. 数据分布分析...")
    result_dist = df['result'].value_counts()
    print(f"   主胜: {result_dist.get(2, 0)} ({result_dist.get(2, 0)/len(df)*100:.2f}%)")
    print(f"   平局: {result_dist.get(1, 0)} ({result_dist.get(1, 0)/len(df)*100:.2f}%)")
    print(f"   客胜: {result_dist.get(0, 0)} ({result_dist.get(0, 0)/len(df)*100:.2f}%)")
    
    print("\n3. 构建特征...")
    basic_features = build_basic_features(df)
    print(f"   基础特征维度: {basic_features.shape[1]}")
    
    team_features = build_team_features(df)
    print(f"   球队特征维度: {team_features.shape[1]}")
    
    X = pd.concat([basic_features, team_features], axis=1)
    y = df['result']
    print(f"   总特征维度: {X.shape[1]}")
    
    print("\n4. 运行时间序列回测...")
    backtest_results = run_time_series_backtest(X, y, df, n_splits=5)
    
    print("\n5. 汇总回测结果...")
    lr_results = [r for r in backtest_results if r['model'] == 'LogisticRegression']
    rf_results = [r for r in backtest_results if r['model'] == 'RandomForest']
    
    print("\nLogisticRegression 汇总:")
    print(f"  平均准确率: {np.mean([r['accuracy'] for r in lr_results]):.4f} ± {np.std([r['accuracy'] for r in lr_results]):.4f}")
    print(f"  平均LogLoss: {np.mean([r['log_loss'] for r in lr_results]):.4f}")
    print(f"  平均Brier: {np.mean([r['brier_score'] for r in lr_results]):.4f}")
    
    print("\nRandomForest 汇总:")
    print(f"  平均准确率: {np.mean([r['accuracy'] for r in rf_results]):.4f} ± {np.std([r['accuracy'] for r in rf_results]):.4f}")
    print(f"  平均LogLoss: {np.mean([r['log_loss'] for r in rf_results]):.4f}")
    print(f"  平均Brier: {np.mean([r['brier_score'] for r in rf_results]):.4f}")
    
    print("\n6. 各联赛性能分析...")
    league_results = analyze_league_performance(X, y, df)
    for league, stats in league_results.items():
        print(f"  {league}: {stats['matches']}场 - 准确率: {stats['accuracy']:.4f}")
    
    print("\n7. Poisson基准模型评估...")
    poisson_result = poisson_baseline(df)
    print(f"  Poisson基准准确率: {poisson_result['accuracy']:.4f}")
    
    print("\n" + "=" * 60)
    print("回测评估报告生成完成")
    print("=" * 60)
    
    report = {
        'date': pd.Timestamp.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_matches': len(df),
        'date_range': f"{df['date'].min().strftime('%Y-%m-%d')} to {df['date'].max().strftime('%Y-%m-%d')}",
        'result_distribution': {
            'home_win': int(result_dist.get(2, 0)),
            'draw': int(result_dist.get(1, 0)),
            'away_win': int(result_dist.get(0, 0))
        },
        'feature_dimensions': X.shape[1],
        'backtest_results': backtest_results,
        'league_performance': league_results,
        'poisson_baseline': poisson_result,
        'summary': {
            'lr_accuracy_mean': float(np.mean([r['accuracy'] for r in lr_results])),
            'lr_accuracy_std': float(np.std([r['accuracy'] for r in lr_results])),
            'rf_accuracy_mean': float(np.mean([r['accuracy'] for r in rf_results])),
            'rf_accuracy_std': float(np.std([r['accuracy'] for r in rf_results])),
            'poisson_accuracy': float(poisson_result['accuracy'])
        }
    }
    
    report_path = os.path.join(ASSETS_DIR, 'backtest_report.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=2, ensure_ascii=False)
    print(f"\n报告已保存到: {report_path}")
    
    return report

if __name__ == "__main__":
    main()