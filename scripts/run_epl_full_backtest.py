"""
英超比赛全量回测脚本
使用UnifiedBacktestFramework对119场英超比赛执行完整回测
"""
import os
import sys
import json
import sqlite3
from datetime import datetime

# 设置路径
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)

# 设置工作目录
os.chdir(PROJECT_DIR)

from unified_backtest_framework import UnifiedBacktestFramework

DB_PATH = os.path.join(PROJECT_DIR, 'data', 'odds.db')
REPORT_DIR = os.path.join(PROJECT_DIR, 'reports')

def get_epl_match_ids():
    """获取所有英超比赛的match_id"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT match_id, home_team, away_team, match_date 
        FROM matches 
        WHERE match_type = 'Premier League'
        ORDER BY match_date
    """)
    matches = cursor.fetchall()
    conn.close()
    return matches

def run_full_backtest():
    """执行全量回测"""
    print("=" * 80)
    print("英超比赛全量回测")
    print(f"开始时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    # 获取英超比赛列表
    matches = get_epl_match_ids()
    print(f"\n找到 {len(matches)} 场英超比赛")
    
    # 初始化回测框架
    print("\n初始化回测框架...")
    framework = UnifiedBacktestFramework(
        use_ml_model=True,
        use_odds_analysis=True,
        use_score_prediction=True,
        use_multitask=False
    )
    framework.connect()
    
    # 创建错误案例表
    framework._create_error_tables()
    
    # 加载训练模型
    print("加载训练模型...")
    try:
        framework.load_trained_model()
        print("  ✓ 模型加载成功")
    except Exception as e:
        print(f"  ⚠ 模型加载失败: {e}")
        print("  将使用赔率分析和比分预测模块")
    
    # 执行回测
    print(f"\n开始回测 {len(matches)} 场比赛...")
    results = []
    correct_count = 0
    ml_correct = 0
    ml_valid = 0
    odds_correct = 0
    odds_valid = 0
    score_correct = 0
    score_valid = 0
    
    error_details = []
    
    for i, (match_id, home_team, away_team, match_date) in enumerate(matches):
        if (i + 1) % 10 == 0:
            print(f"  进度: {i+1}/{len(matches)} ({(i+1)/len(matches)*100:.1f}%)")
        
        try:
            analysis = framework.run_single_match_analysis(match_id)
            results.append(analysis)
            
            # 获取实际结果
            match_info = analysis.get('match_info', {})
            actual_wdl = match_info.get('actual_wdl')
            
            # 统计最终预测
            final_pred = analysis.get('final_prediction')
            if final_pred and actual_wdl:
                if final_pred == actual_wdl:
                    correct_count += 1
            
            # 统计ML预测
            ml_pred_data = analysis.get('ml_prediction')
            if ml_pred_data and isinstance(ml_pred_data, dict):
                if 'prediction' in ml_pred_data:
                    ml_valid += 1
                    if ml_pred_data['prediction'] == actual_wdl:
                        ml_correct += 1
            
            # 统计赔率预测
            odds_analysis = analysis.get('odds_analysis')
            if odds_analysis and isinstance(odds_analysis, dict):
                wdl_info = odds_analysis.get('wdl')
                if wdl_info and isinstance(wdl_info, dict):
                    odds_valid += 1
                    if wdl_info.get('signal_label') == actual_wdl:
                        odds_correct += 1
            
            # 统计比分预测
            score_pred = analysis.get('score_prediction')
            if score_pred and isinstance(score_pred, dict):
                if 'top1_score' in score_pred:
                    score_valid += 1
                    actual_score = match_info.get('actual_score', '')
                    if score_pred.get('top1_score') == actual_score:
                        score_correct += 1
            
        except Exception as e:
            error_details.append({
                'match_id': match_id,
                'home_team': home_team,
                'away_team': away_team,
                'error': str(e)
            })
    
    total = len(matches)
    
    # 生成报告
    print("\n" + "=" * 80)
    print("回测报告")
    print("=" * 80)
    
    print(f"\n总比赛数: {total}")
    print(f"错误数: {len(error_details)}")
    print(f"有效回测: {total - len(error_details)}")
    
    print(f"\n--- 最终融合预测 ---")
    final_accuracy = correct_count / total * 100 if total > 0 else 0
    print(f"正确: {correct_count}/{total} ({final_accuracy:.2f}%)")
    
    print(f"\n--- ML模型预测 ---")
    if ml_valid > 0:
        ml_accuracy = ml_correct / ml_valid * 100
        ml_coverage = ml_valid / total * 100
        print(f"正确: {ml_correct}/{ml_valid} ({ml_accuracy:.2f}%)")
        print(f"覆盖率: {ml_valid}/{total} ({ml_coverage:.2f}%)")
    else:
        print(f"覆盖率: 0/{total} (0.00%)")
    
    print(f"\n--- 赔率分析预测 ---")
    if odds_valid > 0:
        odds_accuracy = odds_correct / odds_valid * 100
        odds_coverage = odds_valid / total * 100
        print(f"正确: {odds_correct}/{odds_valid} ({odds_accuracy:.2f}%)")
        print(f"覆盖率: {odds_valid}/{total} ({odds_coverage:.2f}%)")
    else:
        print(f"覆盖率: 0/{total} (0.00%)")
    
    print(f"\n--- 比分预测 ---")
    if score_valid > 0:
        score_accuracy = score_correct / score_valid * 100
        score_coverage = score_valid / total * 100
        print(f"正确: {score_correct}/{score_valid} ({score_accuracy:.2f}%)")
        print(f"覆盖率: {score_valid}/{total} ({score_coverage:.2f}%)")
    else:
        print(f"覆盖率: 0/{total} (0.00%)")
    
    if error_details:
        print(f"\n--- 错误详情（前10条）---")
        for err in error_details[:10]:
            print(f"  {err['match_id']}: {err['error'][:100]}")
    
    # 保存报告
    report = {
        'backtest_time': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
        'total_matches': total,
        'error_count': len(error_details),
        'valid_backtests': total - len(error_details),
        'final_prediction': {
            'correct': correct_count,
            'total': total,
            'accuracy': round(final_accuracy, 2)
        },
        'ml_model': {
            'correct': ml_correct,
            'valid': ml_valid,
            'accuracy': round(ml_correct / ml_valid * 100, 2) if ml_valid > 0 else 0,
            'coverage': round(ml_valid / total * 100, 2) if total > 0 else 0
        },
        'odds_analysis': {
            'correct': odds_correct,
            'valid': odds_valid,
            'accuracy': round(odds_correct / odds_valid * 100, 2) if odds_valid > 0 else 0,
            'coverage': round(odds_valid / total * 100, 2) if total > 0 else 0
        },
        'score_prediction': {
            'correct': score_correct,
            'valid': score_valid,
            'accuracy': round(score_correct / score_valid * 100, 2) if score_valid > 0 else 0,
            'coverage': round(score_valid / total * 100, 2) if total > 0 else 0
        },
        'error_details': error_details[:50]
    }
    
    # 确保报告目录存在
    os.makedirs(REPORT_DIR, exist_ok=True)
    
    report_path = os.path.join(REPORT_DIR, f'backtest_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.json')
    with open(report_path, 'w', encoding='utf-8') as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    
    print(f"\n报告已保存: {report_path}")
    
    # 断开数据库连接
    framework.disconnect()
    
    print(f"\n回测完成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 80)
    
    return report

if __name__ == '__main__':
    report = run_full_backtest()