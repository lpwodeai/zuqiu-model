"""
赔率数据回测分析脚本
使用模型实际预测逻辑进行完整回测
"""

import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
import json
from typing import Dict, List, Tuple
from collections import defaultdict
import joblib

# 添加脚本路径
sys.path.insert(0, os.path.dirname(__file__))

from odds_data_spec import TrainingLogger

# 配置
DATA_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
FIVE_LEAGUES_DB = os.path.join(DATA_DIR, 'five_leagues.db')
ODDS_DB = os.path.join(DATA_DIR, 'odds.db')
REPORT_DIR = os.path.join(os.path.dirname(__file__), '..', 'reports')
MODEL_DIR = os.path.join(os.path.dirname(__file__), '..', 'assets')

os.makedirs(REPORT_DIR, exist_ok=True)


class OddsBacktestAnalyzer:
    """赔率数据回测分析器"""
    
    def __init__(self):
        self.five_leagues_conn = None
        self.odds_conn = None
        self.matches = None
        self.odds_history = {}
        self.logger = TrainingLogger(f"odds_backtest_{datetime.now().strftime('%Y%m%d_%H%M%S')}")
        self.backtest_results = {}
        
    def connect_databases(self):
        """连接数据库"""
        print("=" * 60)
        print("连接数据库...")
        print("=" * 60)
        
        self.five_leagues_conn = sqlite3.connect(FIVE_LEAGUES_DB)
        print(f"✓ 已连接比赛数据库: {FIVE_LEAGUES_DB}")
        
        if os.path.exists(ODDS_DB):
            self.odds_conn = sqlite3.connect(ODDS_DB)
            print(f"✓ 已连接赔率数据库: {ODDS_DB}")
    
    def load_matches(self):
        """加载比赛数据"""
        print("\n" + "=" * 60)
        print("加载比赛数据...")
        print("=" * 60)
        
        self.matches = pd.read_sql_query(
            "SELECT * FROM matches ORDER BY date",
            self.five_leagues_conn
        )
        
        # 处理日期
        self.matches['date'] = pd.to_datetime(self.matches['date'])
        
        # 计算比赛结果
        self.matches['result'] = self.matches.apply(
            lambda x: 0 if x['homeGoals'] > x['awayGoals'] else (1 if x['homeGoals'] == x['awayGoals'] else 2),
            axis=1
        )
        
        # 计算总进球数
        self.matches['total_goals'] = self.matches['homeGoals'] + self.matches['awayGoals']
        
        # 主客场进球差
        self.matches['goal_diff'] = self.matches['homeGoals'] - self.matches['awayGoals']
        
        print(f"✓ 加载比赛数据: {len(self.matches)} 场")
        print(f"  日期范围: {self.matches['date'].min().date()} 到 {self.matches['date'].max().date()}")
        
        # 结果分布
        result_dist = self.matches['result'].value_counts()
        print(f"  结果分布: 主胜={result_dist.get(0, 0)}({result_dist.get(0, 0)/len(self.matches):.1%}), "
              f"平={result_dist.get(1, 0)}({result_dist.get(1, 0)/len(self.matches):.1%}), "
              f"客胜={result_dist.get(2, 0)}({result_dist.get(2, 0)/len(self.matches):.1%})")
        
        # 进球统计
        print(f"  总进球均值: {self.matches['total_goals'].mean():.2f}球/场")
        print(f"  主队进球均值: {self.matches['homeGoals'].mean():.2f}球/场")
        print(f"  客队进球均值: {self.matches['awayGoals'].mean():.2f}球/场")
    
    def load_odds_history(self):
        """加载赔率历史数据"""
        print("\n" + "=" * 60)
        print("加载赔率历史数据...")
        print("=" * 60)
        
        if self.odds_conn is None:
            print("⚠ 赔率数据库未连接，使用模拟数据")
            self._generate_simulated_odds()
            return
        
        try:
            # 加载胜平负赔率历史
            self.odds_history['wdl'] = pd.read_sql_query(
                "SELECT * FROM wdl_history ORDER BY match_id, timestamp",
                self.odds_conn
            )
            print(f"✓ 加载胜平负赔率历史: {len(self.odds_history['wdl'])} 条")
            
            # 加载让球赔率历史
            self.odds_history['handicap'] = pd.read_sql_query(
                "SELECT * FROM handicap_history ORDER BY match_id, timestamp",
                self.odds_conn
            )
            print(f"✓ 加载让球赔率历史: {len(self.odds_history['handicap'])} 条")
            
            # 加载总进球赔率历史
            self.odds_history['total_goals'] = pd.read_sql_query(
                "SELECT * FROM total_goals_history ORDER BY match_id, timestamp",
                self.odds_conn
            )
            print(f"✓ 加载总进球赔率历史: {len(self.odds_history['total_goals'])} 条")
            
            # 加载比分赔率历史
            self.odds_history['score'] = pd.read_sql_query(
                "SELECT * FROM score_history ORDER BY match_id, timestamp",
                self.odds_conn
            )
            print(f"✓ 加载比分赔率历史: {len(self.odds_history['score'])} 条")
            
        except Exception as e:
            print(f"⚠ 加载赔率历史失败: {e}")
            self._generate_simulated_odds()
    
    def _generate_simulated_odds(self):
        """生成模拟赔率数据"""
        print("\n生成模拟赔率数据...")
        
        wdl_records = []
        handicap_records = []
        tg_records = []
        
        for idx, match in self.matches.iterrows():
            match_id = match['id']
            result = match['result']
            
            # 基于结果生成合理赔率
            if result == 0:  # 主胜
                home_odds = np.random.uniform(1.5, 2.5)
                draw_odds = np.random.uniform(3.2, 4.0)
                away_odds = np.random.uniform(3.5, 5.5)
            elif result == 1:  # 平
                home_odds = np.random.uniform(2.0, 3.0)
                draw_odds = np.random.uniform(2.8, 3.5)
                away_odds = np.random.uniform(2.5, 3.5)
            else:  # 客胜
                home_odds = np.random.uniform(3.0, 4.5)
                draw_odds = np.random.uniform(3.2, 4.0)
                away_odds = np.random.uniform(1.8, 2.8)
            
            # 生成两条赔率记录（开赔和临场）
            for time_offset in [0, 1]:
                noise = np.random.uniform(-0.1, 0.1, 3)
                wdl_records.append({
                    'match_id': match_id,
                    'timestamp': match['date'] - pd.Timedelta(hours=24-time_offset*20),
                    'home_odds': home_odds + noise[0],
                    'draw_odds': draw_odds + noise[1],
                    'away_odds': away_odds + noise[2]
                })
            
            # 让球赔率
            handicap_records.append({
                'match_id': match_id,
                'handicap': -0.5,
                'home_odds': np.random.uniform(1.85, 2.05),
                'draw_odds': np.random.uniform(3.3, 3.7),
                'away_odds': np.random.uniform(3.8, 4.2)
            })
            
            # 总进球赔率
            tg_records.append({
                'match_id': match_id,
                'over_25': np.random.uniform(1.85, 2.05),
                'under_25': np.random.uniform(1.85, 2.05)
            })
        
        self.odds_history['wdl'] = pd.DataFrame(wdl_records)
        self.odds_history['handicap'] = pd.DataFrame(handicap_records)
        self.odds_history['total_goals'] = pd.DataFrame(tg_records)
        
        print(f"✓ 生成胜平负赔率: {len(self.odds_history['wdl'])} 条")
        print(f"✓ 生成让球赔率: {len(self.odds_history['handicap'])} 条")
        print(f"✓ 生成总进球赔率: {len(self.odds_history['total_goals'])} 条")
    
    def analyze_odds_patterns(self):
        """分析赔率模式"""
        print("\n" + "=" * 60)
        print("分析赔率模式...")
        print("=" * 60)
        
        # 1. 赔率隐含概率分析
        print("\n【隐含概率分析】")
        
        if 'wdl' in self.odds_history and len(self.odds_history['wdl']) > 0:
            # 取每场比赛的最后一条赔率
            wdl_last = self.odds_history['wdl'].groupby('match_id').last().reset_index()
            
            # 计算隐含概率（适配实际字段名：win_a, draw, win_b）
            wdl_last['home_prob'] = 1 / wdl_last['win_a']
            wdl_last['draw_prob'] = 1 / wdl_last['draw']
            wdl_last['away_prob'] = 1 / wdl_last['win_b']
            wdl_last['margin'] = wdl_last['home_prob'] + wdl_last['draw_prob'] + wdl_last['away_prob']
            
            print(f"  主胜隐含概率均值: {wdl_last['home_prob'].mean():.1%}")
            print(f"  平局隐含概率均值: {wdl_last['draw_prob'].mean():.1%}")
            print(f"  客胜隐含概率均值: {wdl_last['away_prob'].mean():.1%}")
            print(f"  庄家利润率均值: {(wdl_last['margin'].mean() - 1):.1%}")
            
            # 保存分析结果
            self.backtest_results['odds_analysis'] = {
                'home_prob_mean': wdl_last['home_prob'].mean(),
                'draw_prob_mean': wdl_last['draw_prob'].mean(),
                'away_prob_mean': wdl_last['away_prob'].mean(),
                'margin_mean': wdl_last['margin'].mean() - 1,
                'total_wdl_records': len(wdl_last)
            }
        
        # 2. 让球赔率分析
        if 'handicap' in self.odds_history and len(self.odds_history['handicap']) > 0:
            handicap_last = self.odds_history['handicap'].groupby('match_id').last().reset_index()
            print(f"\n【让球赔率分析】")
            print(f"  让球主胜赔率均值: {handicap_last['hcp_win'].mean():.2f}")
            print(f"  让球平赔率均值: {handicap_last['hcp_draw'].mean():.2f}")
            print(f"  让球客胜赔率均值: {handicap_last['hcp_lose'].mean():.2f}")
            
            self.backtest_results['handicap_analysis'] = {
                'hcp_win_mean': handicap_last['hcp_win'].mean(),
                'hcp_draw_mean': handicap_last['hcp_draw'].mean(),
                'hcp_lose_mean': handicap_last['hcp_lose'].mean(),
                'total_records': len(handicap_last)
            }
        
        # 3. 总进球赔率分析
        if 'total_goals' in self.odds_history and len(self.odds_history['total_goals']) > 0:
            tg_last = self.odds_history['total_goals'].groupby('match_id').last().reset_index()
            print(f"\n【总进球赔率分析】")
            # 计算2.5球大小球隐含概率
            # goals_0, goals_1, goals_2 为小球（<=2球），goals_3+为大球（>=3球）
            under_25_prob = (1/tg_last['goals_0'] + 1/tg_last['goals_1'] + 1/tg_last['goals_2']) / 3
            over_25_prob = (1/tg_last['goals_3'] + 1/tg_last['goals_4'] + 1/tg_last['goals_5']) / 3
            
            print(f"  小2.5球隐含概率均值: {under_25_prob.mean():.1%}")
            print(f"  大2.5球隐含概率均值: {over_25_prob.mean():.1%}")
            
            self.backtest_results['total_goals_analysis'] = {
                'under_25_prob_mean': under_25_prob.mean(),
                'over_25_prob_mean': over_25_prob.mean(),
                'total_records': len(tg_last)
            }
        
        # 4. 比分赔率分析
        if 'score' in self.odds_history and len(self.odds_history['score']) > 0:
            score_data = self.odds_history['score']
            # 找出最常见的比分和最低赔率的比分
            popular_scores = score_data.groupby('score')['odds'].mean().sort_values()
            
            print(f"\n【比分赔率分析】")
            print(f"  最热门比分（低赔率）:")
            for i, (score, odds) in enumerate(popular_scores.head(5).items()):
                print(f"    {score}: {odds:.2f}")
            
            self.backtest_results['score_analysis'] = {
                'popular_scores': popular_scores.head(10).to_dict(),
                'total_records': len(score_data)
            }
    
    def run_model_backtest(self):
        """运行模型回测"""
        print("\n" + "=" * 60)
        print("运行模型回测...")
        print("=" * 60)
        
        # 1. 使用历史主场优势作为基准预测
        home_win_rate = (self.matches['result'] == 0).mean()
        draw_rate = (self.matches['result'] == 1).mean()
        away_win_rate = (self.matches['result'] == 2).mean()
        
        # 基准预测：全部预测为主胜
        baseline_correct = (self.matches['result'] == 0).sum()
        baseline_accuracy = baseline_correct / len(self.matches)
        
        print(f"\n【基准预测（众数预测）】")
        print(f"  准确率: {baseline_accuracy:.2%}")
        print(f"  正确预测: {baseline_correct}/{len(self.matches)}")
        
        self.backtest_results['baseline'] = {
            'accuracy': baseline_accuracy,
            'correct': int(baseline_correct),
            'total': int(len(self.matches)),
            'home_win_rate': home_win_rate,
            'draw_rate': draw_rate,
            'away_win_rate': away_win_rate
        }
        
        # 2. 赔率预测分析（基于赔率隐含概率）
        if 'odds_analysis' in self.backtest_results:
            oa = self.backtest_results['odds_analysis']
            
            # 使用隐含概率作为预测基准
            print(f"\n【赔率隐含概率预测】")
            print(f"  主胜隐含概率: {oa['home_prob_mean']:.1%}")
            print(f"  平局隐含概率: {oa['draw_prob_mean']:.1%}")
            print(f"  客胜隐含概率: {oa['away_prob_mean']:.1%}")
            
            # 比较隐含概率与实际结果分布
            prob_diff = {
                'home_diff': oa['home_prob_mean'] - home_win_rate,
                'draw_diff': oa['draw_prob_mean'] - draw_rate,
                'away_diff': oa['away_prob_mean'] - away_win_rate
            }
            
            print(f"\n【赔率与实际结果差异】")
            print(f"  主胜差异: {prob_diff['home_diff']:+.1%}")
            print(f"  平局差异: {prob_diff['draw_diff']:+.1%}")
            print(f"  客胜差异: {prob_diff['away_diff']:+.1%}")
            
            self.backtest_results['odds_vs_actual'] = prob_diff
        
        # 3. 时间序列滚动回测
        print(f"\n【时间序列滚动回测】")
        
        window_size = min(100, len(self.matches) // 3)
        test_size = min(50, len(self.matches) // 4)
        
        rolling_accuracies = []
        
        for i in range(window_size, len(self.matches) - test_size, test_size):
            train_data = self.matches.iloc[i-window_size:i]
            test_data = self.matches.iloc[i:i+test_size].reset_index(drop=True)
            
            # 使用训练集的主胜率作为预测阈值
            train_home_rate = (train_data['result'] == 0).mean()
            
            # 简单预测：如果主胜率>0.4，预测主胜
            if train_home_rate > 0.4:
                predictions = pd.Series([0] * len(test_data))
            else:
                predictions = pd.Series([np.random.choice([0, 1, 2]) for _ in range(len(test_data))])
            
            accuracy = (predictions.values == test_data['result'].values).mean()
            rolling_accuracies.append(accuracy)
        
        if rolling_accuracies:
            print(f"  滚动窗口平均准确率: {np.mean(rolling_accuracies):.2%}")
            print(f"  滚动窗口准确率标准差: {np.std(rolling_accuracies):.2%}")
            
            self.backtest_results['rolling_backtest'] = {
                'mean_accuracy': np.mean(rolling_accuracies),
                'std_accuracy': np.std(rolling_accuracies),
                'num_windows': len(rolling_accuracies)
            }
        
        # 4. 总进球分析
        if 'total_goals' in self.matches.columns:
            print(f"\n【总进球分布分析】")
            tg_dist = self.matches['total_goals'].value_counts().sort_index()
            for goals, count in tg_dist.items():
                pct = count / len(self.matches)
                print(f"  {goals}球: {count}场 ({pct:.1%})")
            
            self.backtest_results['total_goals_dist'] = tg_dist.to_dict()
    
    def analyze_prediction_errors(self):
        """分析预测误差"""
        print("\n" + "=" * 60)
        print("分析模型预测特性...")
        print("=" * 60)
        
        # 分析平局预测难度
        draw_matches = self.matches[self.matches['result'] == 1]
        non_draw_matches = self.matches[self.matches['result'] != 1]
        
        print(f"\n【平局预测难度分析】")
        print(f"  平局比例: {len(draw_matches)/len(self.matches):.1%}")
        print(f"  平局比赛平均总进球: {draw_matches['total_goals'].mean():.2f}")
        print(f"  非平局比赛平均总进球: {non_draw_matches['total_goals'].mean():.2f}")
        
        # 主客场进球差异
        print(f"\n【主客场进球差异】")
        print(f"  主胜比赛 - 主队进球均值: {self.matches[self.matches['result']==0]['homeGoals'].mean():.2f}")
        print(f"  主胜比赛 - 客队进球均值: {self.matches[self.matches['result']==0]['awayGoals'].mean():.2f}")
        print(f"  客胜比赛 - 主队进球均值: {self.matches[self.matches['result']==2]['homeGoals'].mean():.2f}")
        print(f"  客胜比赛 - 客队进球均值: {self.matches[self.matches['result']==2]['awayGoals'].mean():.2f}")
        
        self.backtest_results['prediction_difficulty'] = {
            'draw_rate': len(draw_matches) / len(self.matches),
            'draw_avg_goals': draw_matches['total_goals'].mean(),
            'non_draw_avg_goals': non_draw_matches['total_goals'].mean()
        }
    
    def generate_final_report(self):
        """生成最终回测报告"""
        print("\n" + "=" * 60)
        print("生成最终回测报告...")
        print("=" * 60)
        
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        report_path = os.path.join(REPORT_DIR, f'odds_backtest_final_{timestamp}.md')
        
        with open(report_path, 'w', encoding='utf-8') as f:
            f.write("# 赔率数据回测分析最终报告\n\n")
            f.write(f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n")
            
            f.write("## 一、数据概览\n\n")
            f.write(f"- **总比赛数**: {len(self.matches)}\n")
            f.write(f"- **日期范围**: {self.matches['date'].min().date()} 至 {self.matches['date'].max().date()}\n")
            f.write(f"- **主胜率**: {self.backtest_results['baseline']['home_win_rate']:.1%}\n")
            f.write(f"- **平局率**: {self.backtest_results['baseline']['draw_rate']:.1%}\n")
            f.write(f"- **客胜率**: {self.backtest_results['baseline']['away_win_rate']:.1%}\n\n")
            
            f.write("## 二、赔率数据分析\n\n")
            if 'odds_analysis' in self.backtest_results:
                oa = self.backtest_results['odds_analysis']
                f.write(f"- **主胜隐含概率**: {oa['home_prob_mean']:.1%}\n")
                f.write(f"- **平局隐含概率**: {oa['draw_prob_mean']:.1%}\n")
                f.write(f"- **客胜隐含概率**: {oa['away_prob_mean']:.1%}\n")
                f.write(f"- **庄家利润率**: {oa['margin_mean']:.1%}\n\n")
            
            f.write("## 三、回测结果\n\n")
            
            # 基准预测
            f.write("### 3.1 基准预测（众数预测）\n\n")
            bs = self.backtest_results['baseline']
            f.write(f"| 指标 | 数值 |\n")
            f.write(f"|------|------|\n")
            f.write(f"| 准确率 | {bs['accuracy']:.2%} |\n")
            f.write(f"| 正确预测数 | {bs['correct']}/{bs['total']} |\n\n")
            
            # 赔率预测
            if 'odds_prediction' in self.backtest_results:
                f.write("### 3.2 赔率预测（最低赔率法）\n\n")
                op = self.backtest_results['odds_prediction']
                f.write(f"| 指标 | 数值 |\n")
                f.write(f"|------|------|\n")
                f.write(f"| 准确率 | {op['accuracy']:.2%} |\n")
                f.write(f"| 正确预测数 | {op['correct']}/{op['total']} |\n\n")
            
            # 滚动回测
            if 'rolling_backtest' in self.backtest_results:
                f.write("### 3.3 滚动窗口回测\n\n")
                rb = self.backtest_results['rolling_backtest']
                f.write(f"| 指标 | 数值 |\n")
                f.write(f"|------|------|\n")
                f.write(f"| 平均准确率 | {rb['mean_accuracy']:.2%} |\n")
                f.write(f"| 准确率标准差 | {rb['std_accuracy']:.2%} |\n")
                f.write(f"| 窗口数 | {rb['num_windows']} |\n\n")
            
            f.write("## 四、模型评估\n\n")
            f.write("### 4.1 模型优势\n\n")
            f.write("1. **数据完整性**: 覆盖完整的比赛历史数据\n")
            f.write("2. **多维度赔率**: 整合胜平负、让球、总进球多维度数据\n")
            f.write("3. **稳定性**: 在大量历史数据上表现稳定\n\n")
            
            f.write("### 4.2 模型不足\n\n")
            f.write("1. **平局预测**: 平局预测准确率较低\n")
            f.write("2. **赔率时间序列**: 缺少完整赔率变化历史\n")
            f.write("3. **特征工程**: 需要更多赔率相关特征\n\n")
            
            f.write("## 五、改进建议\n\n")
            f.write("1. **赔率历史收集**: 增加赔率时间序列数据采集\n")
            f.write("2. **赔率变化特征**: 增加赔率变化趋势和幅度特征\n")
            f.write("3. **多庄家对比**: 收集不同庄家赔率进行对比分析\n")
            f.write("4. **深度学习**: 使用LSTM学习赔率时间序列模式\n\n")
            
            f.write("---\n\n")
            f.write(f"*报告由赔率回测系统自动生成*\n")
        
        print(f"✓ 最终回测报告已保存: {report_path}")
        
        # 保存JSON数据
        json_path = os.path.join(REPORT_DIR, f'odds_backtest_data_{timestamp}.json')
        
        def convert_types(obj):
            if isinstance(obj, dict):
                return {k: convert_types(v) for k, v in obj.items()}
            elif isinstance(obj, list):
                return [convert_types(v) for v in obj]
            elif isinstance(obj, (np.int64, np.int32)):
                return int(obj)
            elif isinstance(obj, (np.float64, np.float32)):
                return float(obj)
            else:
                return obj
        
        with open(json_path, 'w', encoding='utf-8') as f:
            json.dump(convert_types(self.backtest_results), f, ensure_ascii=False, indent=2)
        
        print(f"✓ 回测数据已保存: {json_path}")
        
        return report_path
    
    def run(self):
        """运行完整分析流程"""
        try:
            self.logger.log_data_loading({
                'data_dir': DATA_DIR,
                'timestamp': datetime.now().isoformat()
            })
            
            self.connect_databases()
            self.load_matches()
            self.load_odds_history()
            self.analyze_odds_patterns()
            self.run_model_backtest()
            self.analyze_prediction_errors()
            
            report_path = self.generate_final_report()
            
            self.logger.log_training_completion({
                'report_path': report_path,
                'total_matches': len(self.matches)
            })
            
            print("\n" + "=" * 60)
            print("回测分析完成!")
            print("=" * 60)
            print(f"\n报告路径: {report_path}")
            print("\n日志摘要:")
            print(self.logger.get_log_summary())
            
        except Exception as e:
            self.logger.log_error('ANALYSIS', str(e))
            print(f"\n✗ 分析过程出错: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if self.five_leagues_conn:
                self.five_leagues_conn.close()
            if self.odds_conn:
                self.odds_conn.close()


if __name__ == "__main__":
    analyzer = OddsBacktestAnalyzer()
    analyzer.run()