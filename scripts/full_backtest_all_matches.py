import sqlite3
import pandas as pd
import numpy as np
from pathlib import Path
from odds_temporal_features import build_odds_temporal_features, load_wdl_history, load_handicap_history, load_total_goals_history, load_score_history, analyze_wdl_trend, analyze_handicap_trend, analyze_total_goals_trend, analyze_score_trend
from score_prediction_module import analyze_match_score

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "odds.db"

def run_full_backtest():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT match_id, home_team, away_team, actual_wdl, actual_handicap, actual_score, actual_total_goals FROM matches ORDER BY match_id')
    matches = cursor.fetchall()
    
    results = []
    
    for match in matches:
        match_id, home_team, away_team, actual_wdl, actual_handicap, actual_score, actual_total_goals = match
        
        wdl_history = load_wdl_history(match_id, conn)
        handicap_history = load_handicap_history(match_id, conn)
        tg_history = load_total_goals_history(match_id, conn)
        score_history = load_score_history(match_id, conn)
        
        wdl_pred = None
        wdl_correct = False
        wdl_confidence = 0
        
        if len(wdl_history) >= 2:
            try:
                wdl_features, _ = analyze_wdl_trend(wdl_history)
                wdl_pred = wdl_features.get('wdl_market_signal')
                wdl_confidence = wdl_features.get('wdl_odds_confidence', 0)
                
                pred_map = {0: '负', 1: '平', 2: '胜'}
                predicted_wdl = pred_map.get(wdl_pred, '未知')
                
                if predicted_wdl == actual_wdl:
                    wdl_correct = True
            except:
                pass
        
        hcp_pred = None
        hcp_correct = False
        
        if len(handicap_history) >= 2:
            try:
                hcp_features, _ = analyze_handicap_trend(handicap_history)
                hcp_pred = hcp_features.get('hcp_prediction')
                
                hcp_map = {-1: '让负', 0: '让平', 1: '让胜'}
                predicted_hcp = hcp_map.get(hcp_pred, '未知')
                
                if actual_handicap:
                    if ('负' in actual_handicap and predicted_hcp == '让负') or \
                       ('平' in actual_handicap and predicted_hcp == '让平') or \
                       ('胜' in actual_handicap and predicted_hcp == '让胜'):
                        hcp_correct = True
            except:
                pass
        
        tg_correct = False
        if len(tg_history) >= 2:
            try:
                tg_features, _ = analyze_total_goals_trend(tg_history)
                target_goals = [tg_features.get(f'tg_target_goals_{i}') for i in range(1, 5) if tg_features.get(f'tg_target_goals_{i}') is not None]
                target_goals = [g for g in target_goals if g is not None]
                if actual_total_goals in target_goals:
                    tg_correct = True
            except:
                pass
        
        score_pred_result = analyze_match_score(match_id)
        score_hit_top1 = False
        score_hit_top3 = False
        score_top1 = None
        score_top1_prob = 0
        score_lambda_home = None
        score_lambda_away = None
        
        if score_pred_result:
            score_hit_top1 = score_pred_result['rank'] == 1
            score_hit_top3 = score_pred_result['hit']
            score_top1 = score_pred_result['top_scores'][0][0] if score_pred_result['top_scores'] else None
            score_top1_prob = score_pred_result['top_scores'][0][1] if score_pred_result['top_scores'] else 0
            score_lambda_home = score_pred_result['lambda_home']
            score_lambda_away = score_pred_result['lambda_away']
        
        results.append({
            'match_id': match_id,
            'home_team': home_team,
            'away_team': away_team,
            'actual_wdl': actual_wdl,
            'wdl_pred': wdl_pred,
            'wdl_correct': wdl_correct,
            'wdl_confidence': wdl_confidence,
            'actual_handicap': actual_handicap,
            'hcp_pred': hcp_pred,
            'hcp_correct': hcp_correct,
            'actual_score': actual_score,
            'actual_total_goals': actual_total_goals,
            'tg_correct': tg_correct,
            'score_hit_top1': score_hit_top1,
            'score_hit_top3': score_hit_top3,
            'score_top1': score_top1,
            'score_top1_prob': score_top1_prob,
            'score_lambda_home': score_lambda_home,
            'score_lambda_away': score_lambda_away,
            'wdl_data_points': len(wdl_history),
            'hcp_data_points': len(handicap_history),
            'tg_data_points': len(tg_history),
            'score_data_points': len(score_history)
        })
    
    conn.close()
    
    return results

def generate_report(results):
    total = len(results)
    
    wdl_correct = sum(1 for r in results if r['wdl_correct'])
    hcp_correct = sum(1 for r in results if r['hcp_correct'])
    tg_correct = sum(1 for r in results if r['tg_correct'])
    score_hit_top1 = sum(1 for r in results if r['score_hit_top1'])
    score_hit_top3 = sum(1 for r in results if r['score_hit_top3'])
    
    wdl_valid = sum(1 for r in results if r['wdl_data_points'] >= 2)
    hcp_valid = sum(1 for r in results if r['hcp_data_points'] >= 2)
    tg_valid = sum(1 for r in results if r['tg_data_points'] >= 2)
    
    avg_wdl_confidence = sum(r['wdl_confidence'] for r in results if r['wdl_confidence'] > 0) / sum(1 for r in results if r['wdl_confidence'] > 0) if sum(1 for r in results if r['wdl_confidence'] > 0) > 0 else 0
    avg_score_top1_prob = sum(r['score_top1_prob'] for r in results) / total if total > 0 else 0
    
    avg_lambda_home = sum(r['score_lambda_home'] for r in results if r['score_lambda_home'] is not None) / total if total > 0 else 0
    avg_lambda_away = sum(r['score_lambda_away'] for r in results if r['score_lambda_away'] is not None) / total if total > 0 else 0
    
    print('=' * 90)
    print('完整回测报告 - 全部比赛 (融合比分预测)')
    print('=' * 90)
    print(f'\n总比赛数: {total}')
    print(f'WDL数据充足(>=2): {wdl_valid}/{total}')
    print(f'让球数据充足(>=2): {hcp_valid}/{total}')
    print(f'总进球数据充足(>=2): {tg_valid}/{total}')
    print()
    print('=' * 90)
    print('统计结果:')
    print('=' * 90)
    print(f'{"玩法":<12} | {"正确":<6} | {"有效":<6} | {"准确率":<10}')
    print('-' * 90)
    print(f'{"胜平负":<12} | {wdl_correct:<6} | {wdl_valid:<6} | {wdl_correct/wdl_valid*100:.1f}%' if wdl_valid > 0 else f'{"胜平负":<12} | {wdl_correct:<6} | {wdl_valid:<6} | N/A')
    print(f'{"让球":<12} | {hcp_correct:<6} | {hcp_valid:<6} | {hcp_correct/hcp_valid*100:.1f}%' if hcp_valid > 0 else f'{"让球":<12} | {hcp_correct:<6} | {hcp_valid:<6} | N/A')
    print(f'{"总进球":<12} | {tg_correct:<6} | {tg_valid:<6} | {tg_correct/tg_valid*100:.1f}%' if tg_valid > 0 else f'{"总进球":<12} | {tg_correct:<6} | {tg_valid:<6} | N/A')
    print(f'{"比分Top-1":<12} | {score_hit_top1:<6} | {total:<6} | {score_hit_top1/total*100:.1f}%')
    print(f'{"比分Top-3":<12} | {score_hit_top3:<6} | {total:<6} | {score_hit_top3/total*100:.1f}%')
    
    print(f'\n平均置信度:')
    print(f'  WDL: {avg_wdl_confidence:.1f}%')
    print(f'  比分Top-1概率: {avg_score_top1_prob:.4f}')
    
    print(f'\n比分预测模型参数:')
    print(f'  平均主队Lambda: {avg_lambda_home:.2f}')
    print(f'  平均客队Lambda: {avg_lambda_away:.2f}')
    print(f'  平均总预期进球: {avg_lambda_home + avg_lambda_away:.2f}')
    
    print()
    print('=' * 90)
    print('详细结果:')
    print('=' * 90)
    print(f'{"序号":<4} | {"主客队":<30} | {"比分":<8} | {"胜平负":<6} | {"预测":<6} | {"正确":<6} | {"让球":<12} | {"预测":<8} | {"正确":<6} | {"进球":<6} | {"预测":<6} | {"比分Top1":<10}')
    print('-' * 90)
    
    for i, r in enumerate(results, 1):
        wdl_pred_str = {0:'负', 1:'平', 2:'胜'}.get(r['wdl_pred'], 'N/A')
        hcp_pred_str = {-1:'让负', 0:'让平', 1:'让胜'}.get(r['hcp_pred'], 'N/A')
        
        team_display = f'{r["home_team"]} vs {r["away_team"]}'
        if len(team_display) > 30:
            team_display = team_display[:27] + '...'
        
        score_top1_display = f'{r["score_top1"]}({"✓" if r["score_hit_top1"] else "✗"})' if r["score_top1"] else 'N/A'
        
        print(f'{i:<4} | {team_display:<30} | {r["actual_score"]:<8} | {r["actual_wdl"]:<6} | {wdl_pred_str:<6} | {"✓" if r["wdl_correct"] else "✗":<6} | {r["actual_handicap"][:11] if r["actual_handicap"] else "N/A":<12} | {hcp_pred_str:<8} | {"✓" if r["hcp_correct"] else "✗":<6} | {r["actual_total_goals"]:<6} | {"✓" if r["tg_correct"] else "✗":<6} | {score_top1_display:<10}')
    
    summary = {
        'total_matches': total,
        'wdl_accuracy': wdl_correct/wdl_valid*100 if wdl_valid > 0 else 0,
        'hcp_accuracy': hcp_correct/hcp_valid*100 if hcp_valid > 0 else 0,
        'tg_accuracy': tg_correct/tg_valid*100 if tg_valid > 0 else 0,
        'score_top1_accuracy': score_hit_top1/total*100 if total > 0 else 0,
        'score_top3_accuracy': score_hit_top3/total*100 if total > 0 else 0,
        'avg_wdl_confidence': avg_wdl_confidence,
        'avg_score_top1_prob': avg_score_top1_prob,
        'avg_lambda_home': avg_lambda_home,
        'avg_lambda_away': avg_lambda_away
    }
    
    return summary

if __name__ == "__main__":
    results = run_full_backtest()
    summary = generate_report(results)