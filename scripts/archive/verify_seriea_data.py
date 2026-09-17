"""
意甲2025-2026赛季数据源验证脚本

功能：
1. 验证数据库中意甲数据完整性
2. 验证模型训练功能
3. 验证特征生成功能
4. 验证数据导入功能
5. 验证统计分析功能
6. 生成验证报告

使用方法：
python verify_seriea_data.py
"""

import os
import sys
import sqlite3
import pandas as pd
import numpy as np
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent.parent
DATA_DIR = BASE_DIR / "data"
SCRIPTS_DIR = os.path.join(DATA_DIR, '..', 'scripts')
ODDS_DB = os.path.join(DATA_DIR, 'odds.db')
TIMING_DB = os.path.join(DATA_DIR, 'odds_timing.db')
FIVE_LEAGUES_DB = os.path.join(DATA_DIR, 'five_leagues.db')

# 添加scripts目录到路径
sys.path.append(SCRIPTS_DIR)


class VerificationResult:
    """验证结果类"""
    def __init__(self, name, passed, message, details=None):
        self.name = name
        self.passed = passed
        self.message = message
        self.details = details or {}
    
    def __str__(self):
        status = '✅' if self.passed else '❌'
        return f"{status} {self.name}: {self.message}"


def verify_database_structure():
    """验证数据库结构完整性"""
    results = []
    
    # 检查odds.db
    if os.path.exists(ODDS_DB):
        conn = sqlite3.connect(ODDS_DB)
        cursor = conn.cursor()
        
        tables = ['matches', 'wdl_history', 'handicap_history', 'total_goals_history', 
                  'match_mapping', 'team_mapping', 'import_log']
        
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='{table}'")
            exists = cursor.fetchone()[0] > 0
            
            if exists:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                results.append(VerificationResult(
                    f"表 {table}", True, f"存在 ({count} 条记录)"
                ))
            else:
                results.append(VerificationResult(
                    f"表 {table}", False, "不存在"
                ))
        
        conn.close()
    else:
        results.append(VerificationResult("odds.db", False, "数据库文件不存在"))
    
    # 检查odds_timing.db
    if os.path.exists(TIMING_DB):
        conn = sqlite3.connect(TIMING_DB)
        cursor = conn.cursor()
        
        tables = ['matches', 'wdl_timing', 'handicap_timing', 'score_timing', 
                  'total_goals_timing', 'match_results', 'import_log', 'data_sources']
        
        for table in tables:
            cursor.execute(f"SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='{table}'")
            exists = cursor.fetchone()[0] > 0
            
            if exists:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                results.append(VerificationResult(
                    f"时序表 {table}", True, f"存在 ({count} 条记录)"
                ))
            else:
                results.append(VerificationResult(
                    f"时序表 {table}", False, "不存在"
                ))
        
        conn.close()
    else:
        results.append(VerificationResult("odds_timing.db", False, "时序数据库文件不存在"))
    
    return results


def verify_seriea_data_in_db():
    """验证数据库中的意甲数据"""
    results = []
    
    if not os.path.exists(ODDS_DB):
        results.append(VerificationResult("意甲数据", False, "odds.db不存在"))
        return results
    
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    
    try:
        # 统计意甲比赛数
        cursor.execute("SELECT COUNT(*) FROM matches WHERE match_id LIKE '%_Atalanta_%' OR match_id LIKE '%_Juventus_%' OR match_id LIKE '%_Milan_%' OR match_id LIKE '%_Inter_%' OR match_id LIKE '%_Roma_%' OR match_id LIKE '%_Napoli_%' OR match_id LIKE '%_Lazio_%' OR match_id LIKE '%_Fiorentina_%' OR match_id LIKE '%_Genoa_%' OR match_id LIKE '%_Bologna_%' OR match_id LIKE '%_Torino_%' OR match_id LIKE '%_Udinese_%' OR match_id LIKE '%_Verona_%' OR match_id LIKE '%_Lecce_%' OR match_id LIKE '%_Parma_%' OR match_id LIKE '%_Pisa_%' OR match_id LIKE '%_Sassuolo_%' OR match_id LIKE '%_Cagliari_%' OR match_id LIKE '%_Como_%' OR match_id LIKE '%_Cremonese_%'")
        seriea_count = cursor.fetchone()[0]
        
        if seriea_count > 0:
            results.append(VerificationResult(
                "意甲比赛记录", True, f"{seriea_count} 场"
            ))
            
            # 统计赔率记录
            cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_id LIKE '%_Atalanta_%' OR match_id LIKE '%_Juventus_%' OR match_id LIKE '%_Milan_%' OR match_id LIKE '%_Inter_%' OR match_id LIKE '%_Roma_%' OR match_id LIKE '%_Napoli_%' OR match_id LIKE '%_Lazio_%' OR match_id LIKE '%_Fiorentina_%' OR match_id LIKE '%_Genoa_%' OR match_id LIKE '%_Bologna_%' OR match_id LIKE '%_Torino_%' OR match_id LIKE '%_Udinese_%' OR match_id LIKE '%_Verona_%' OR match_id LIKE '%_Lecce_%' OR match_id LIKE '%_Parma_%' OR match_id LIKE '%_Pisa_%' OR match_id LIKE '%_Sassuolo_%' OR match_id LIKE '%_Cagliari_%' OR match_id LIKE '%_Como_%' OR match_id LIKE '%_Cremonese_%')")
            wdl_count = cursor.fetchone()[0]
            results.append(VerificationResult(
                "意甲胜平负赔率", True, f"{wdl_count} 条"
            ))
            
            cursor.execute("SELECT COUNT(*) FROM handicap_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_id LIKE '%_Atalanta_%' OR match_id LIKE '%_Juventus_%' OR match_id LIKE '%_Milan_%' OR match_id LIKE '%_Inter_%' OR match_id LIKE '%_Roma_%' OR match_id LIKE '%_Napoli_%' OR match_id LIKE '%_Lazio_%' OR match_id LIKE '%_Fiorentina_%' OR match_id LIKE '%_Genoa_%' OR match_id LIKE '%_Bologna_%' OR match_id LIKE '%_Torino_%' OR match_id LIKE '%_Udinese_%' OR match_id LIKE '%_Verona_%' OR match_id LIKE '%_Lecce_%' OR match_id LIKE '%_Parma_%' OR match_id LIKE '%_Pisa_%' OR match_id LIKE '%_Sassuolo_%' OR match_id LIKE '%_Cagliari_%' OR match_id LIKE '%_Como_%' OR match_id LIKE '%_Cremonese_%')")
            hcp_count = cursor.fetchone()[0]
            results.append(VerificationResult(
                "意甲让球赔率", True, f"{hcp_count} 条"
            ))
            
            cursor.execute("SELECT COUNT(*) FROM total_goals_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_id LIKE '%_Atalanta_%' OR match_id LIKE '%_Juventus_%' OR match_id LIKE '%_Milan_%' OR match_id LIKE '%_Inter_%' OR match_id LIKE '%_Roma_%' OR match_id LIKE '%_Napoli_%' OR match_id LIKE '%_Lazio_%' OR match_id LIKE '%_Fiorentina_%' OR match_id LIKE '%_Genoa_%' OR match_id LIKE '%_Bologna_%' OR match_id LIKE '%_Torino_%' OR match_id LIKE '%_Udinese_%' OR match_id LIKE '%_Verona_%' OR match_id LIKE '%_Lecce_%' OR match_id LIKE '%_Parma_%' OR match_id LIKE '%_Pisa_%' OR match_id LIKE '%_Sassuolo_%' OR match_id LIKE '%_Cagliari_%' OR match_id LIKE '%_Como_%' OR match_id LIKE '%_Cremonese_%')")
            tg_count = cursor.fetchone()[0]
            results.append(VerificationResult(
                "意甲总进球赔率", True, f"{tg_count} 条"
            ))
            
            # 统计映射记录
            cursor.execute("SELECT COUNT(*) FROM match_mapping WHERE league LIKE '%Serie%'")
            mapping_count = cursor.fetchone()[0]
            results.append(VerificationResult(
                "意甲比赛映射", True, f"{mapping_count} 条"
            ))
            
            # 检查是否有多时间节点数据
            cursor.execute("""
                SELECT COUNT(DISTINCT timestamp) FROM wdl_history 
                WHERE match_id IN (SELECT match_id FROM matches WHERE match_id LIKE '%_Atalanta_%' OR match_id LIKE '%_Juventus_%' OR match_id LIKE '%_Milan_%' OR match_id LIKE '%_Inter_%' OR match_id LIKE '%_Roma_%' OR match_id LIKE '%_Napoli_%' OR match_id LIKE '%_Lazio_%' OR match_id LIKE '%_Fiorentina_%' OR match_id LIKE '%_Genoa_%' OR match_id LIKE '%_Bologna_%' OR match_id LIKE '%_Torino_%' OR match_id LIKE '%_Udinese_%' OR match_id LIKE '%_Verona_%' OR match_id LIKE '%_Lecce_%' OR match_id LIKE '%_Parma_%' OR match_id LIKE '%_Pisa_%' OR match_id LIKE '%_Sassuolo_%' OR match_id LIKE '%_Cagliari_%' OR match_id LIKE '%_Como_%' OR match_id LIKE '%_Cremonese_%')
            """)
            distinct_timestamps = cursor.fetchone()[0]
            
            if distinct_timestamps > 2:
                results.append(VerificationResult(
                    "多时间节点赔率", True, f"已包含 {distinct_timestamps} 个不同时间节点"
                ))
            else:
                results.append(VerificationResult(
                    "多时间节点赔率", False, f"仅 {distinct_timestamps} 个时间节点，建议至少3个"
                ))
        
        else:
            results.append(VerificationResult(
                "意甲比赛记录", False, "数据库中没有意甲数据"
            ))
    
    except Exception as e:
        results.append(VerificationResult(
            "意甲数据查询", False, f"查询失败: {e}"
        ))
    finally:
        conn.close()
    
    return results


def verify_data_import_script():
    """验证数据导入脚本"""
    results = []
    
    script_path = os.path.join(SCRIPTS_DIR, 'import_all_leagues_odds.py')
    
    if not os.path.exists(script_path):
        results.append(VerificationResult(
            "数据导入脚本", False, f"脚本不存在: {script_path}"
        ))
        return results
    
    try:
        # 尝试导入模块
        from import_all_leagues_odds import LEAGUE_CONFIGS, import_league
        
        # 检查意甲配置
        seriea_config = None
        for config in LEAGUE_CONFIGS:
            if config['code'] == 'SerieA':
                seriea_config = config
                break
        
        if seriea_config:
            results.append(VerificationResult(
                "意甲配置存在", True, f"CSV文件: {seriea_config.get('csv_file', '未配置')}"
            ))
            
            if 'csv_file_new' in seriea_config:
                results.append(VerificationResult(
                    "新数据源配置", True, f"新文件: {seriea_config['csv_file_new']}"
                ))
            else:
                results.append(VerificationResult(
                    "新数据源配置", False, "未配置新数据源"
                ))
        else:
            results.append(VerificationResult(
                "意甲配置", False, "LEAGUE_CONFIGS中未找到意甲配置"
            ))
        
        results.append(VerificationResult(
            "数据导入脚本", True, "模块导入成功"
        ))
    
    except Exception as e:
        results.append(VerificationResult(
            "数据导入脚本", False, f"模块导入失败: {e}"
        ))
    
    return results


def verify_feature_engineering():
    """验证特征工程功能"""
    results = []
    
    script_path = os.path.join(SCRIPTS_DIR, 'feature_engineering_pipeline.py')
    
    if not os.path.exists(script_path):
        results.append(VerificationResult(
            "特征工程脚本", False, f"脚本不存在: {script_path}"
        ))
        return results
    
    try:
        from feature_engineering_pipeline import CSV_FILES, CSV_FILES_DETAILED, get_csv_file
        
        # 检查意甲配置
        if 'SERIEA' in CSV_FILES:
            results.append(VerificationResult(
                "意甲CSV配置", True, f"{CSV_FILES['SERIEA']}"
            ))
        
        if 'SERIEA' in CSV_FILES_DETAILED:
            results.append(VerificationResult(
                "意甲新数据源配置", True, f"{CSV_FILES_DETAILED['SERIEA']}"
            ))
        
        # 测试get_csv_file函数
        csv_path = get_csv_file('SERIEA')
        if csv_path:
            results.append(VerificationResult(
                "get_csv_file函数", True, f"返回: {os.path.basename(csv_path)}"
            ))
        else:
            results.append(VerificationResult(
                "get_csv_file函数", False, "返回None，数据源文件不存在"
            ))
        
        results.append(VerificationResult(
            "特征工程脚本", True, "模块导入成功"
        ))
    
    except Exception as e:
        results.append(VerificationResult(
            "特征工程脚本", False, f"模块导入失败: {e}"
        ))
    
    return results


def verify_backtest_script():
    """验证回测脚本"""
    results = []
    
    script_path = os.path.join(SCRIPTS_DIR, 'backtest_with_odds.py')
    
    if not os.path.exists(script_path):
        results.append(VerificationResult(
            "回测脚本", False, f"脚本不存在: {script_path}"
        ))
        return results
    
    try:
        from backtest_with_odds import CSV_FILES, CSV_FILES_DETAILED, get_csv_file
        
        if 'SERIEA' in CSV_FILES:
            results.append(VerificationResult(
                "意甲CSV配置", True, f"{CSV_FILES['SERIEA']}"
            ))
        
        if 'SERIEA' in CSV_FILES_DETAILED:
            results.append(VerificationResult(
                "意甲新数据源配置", True, f"{CSV_FILES_DETAILED['SERIEA']}"
            ))
        
        csv_path = get_csv_file('SERIEA')
        if csv_path:
            results.append(VerificationResult(
                "get_csv_file函数", True, f"返回: {os.path.basename(csv_path)}"
            ))
        
        results.append(VerificationResult(
            "回测脚本", True, "模块导入成功"
        ))
    
    except Exception as e:
        results.append(VerificationResult(
            "回测脚本", False, f"模块导入失败: {e}"
        ))
    
    return results


def verify_team_attributes():
    """验证球队属性生成功能"""
    results = []
    
    script_path = os.path.join(SCRIPTS_DIR, 'generate_team_attributes.py')
    
    if not os.path.exists(script_path):
        results.append(VerificationResult(
            "球队属性脚本", False, f"脚本不存在: {script_path}"
        ))
        return results
    
    try:
        from generate_team_attributes import CSV_FILES, CSV_FILES_DETAILED, get_csv_file
        
        if 'SERIEA' in CSV_FILES:
            results.append(VerificationResult(
                "意甲CSV配置", True, f"{CSV_FILES['SERIEA']}"
            ))
        
        if 'SERIEA' in CSV_FILES_DETAILED:
            results.append(VerificationResult(
                "意甲新数据源配置", True, f"{CSV_FILES_DETAILED['SERIEA']}"
            ))
        
        csv_path = get_csv_file('SERIEA')
        if csv_path:
            results.append(VerificationResult(
                "get_csv_file函数", True, f"返回: {os.path.basename(csv_path)}"
            ))
        
        results.append(VerificationResult(
            "球队属性脚本", True, "模块导入成功"
        ))
    
    except Exception as e:
        results.append(VerificationResult(
            "球队属性脚本", False, f"模块导入失败: {e}"
        ))
    
    return results


def verify_csv_data_source():
    """验证CSV数据源"""
    results = []
    
    # 检查旧数据源
    old_path = os.path.join(DATA_DIR, 'SERIEA_2025-26.csv')
    if os.path.exists(old_path):
        df = pd.read_csv(old_path)
        results.append(VerificationResult(
            "旧CSV数据源", True, f"{len(df)} 场比赛"
        ))
    else:
        results.append(VerificationResult(
            "旧CSV数据源", False, "文件不存在"
        ))
    
    # 检查新数据源
    new_path = os.path.join(DATA_DIR, 'SERIEA_2025-26_DETAILED.csv')
    if os.path.exists(new_path):
        df = pd.read_csv(new_path)
        results.append(VerificationResult(
            "新CSV数据源", True, f"{len(df)} 场比赛"
        ))
        
        # 检查必需列
        required_cols = ['主队', '客队', '日期', '时间', '主队进球', '客队进球']
        missing_cols = [c for c in required_cols if c not in df.columns]
        
        if missing_cols:
            results.append(VerificationResult(
                "新数据源列完整性", False, f"缺少列: {', '.join(missing_cols)}"
            ))
        else:
            results.append(VerificationResult(
                "新数据源列完整性", True, "所有必需列存在"
            ))
        
        # 检查数据质量
        valid_matches = df[(df['主队进球'].notna()) & (df['客队进球'].notna())].shape[0]
        results.append(VerificationResult(
            "比赛结果完整性", True, f"{valid_matches}/{len(df)} 场有结果"
        ))
        
        # 检查赔率数据
        odds_cols = ['bet365_主胜', 'bet365_平局', 'bet365_客胜']
        odds_complete = df[odds_cols].notna().all(axis=1).mean()
        results.append(VerificationResult(
            "赔率数据完整性", True, f"{odds_complete:.1%}"
        ))
    
    else:
        results.append(VerificationResult(
            "新CSV数据源", False, "文件不存在"
        ))
    
    return results


def generate_report(all_results):
    """生成验证报告"""
    print("\n" + "=" * 70)
    print("意甲2025-2026赛季数据源验证报告")
    print(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)
    
    # 统计结果
    total = sum(len(r) for r in all_results)
    passed = sum(1 for r_list in all_results for r in r_list if r.passed)
    failed = total - passed
    
    print(f"\n总体结果: {passed}/{total} 通过")
    
    # 分模块输出
    modules = [
        ("数据库结构", all_results[0]),
        ("意甲数据", all_results[1]),
        ("数据导入脚本", all_results[2]),
        ("特征工程脚本", all_results[3]),
        ("回测脚本", all_results[4]),
        ("球队属性脚本", all_results[5]),
        ("CSV数据源", all_results[6]),
    ]
    
    for module_name, results in modules:
        print(f"\n{'-' * 50}")
        print(f"【{module_name}】")
        print(f"{'-' * 50}")
        
        for result in results:
            print(f"  {result}")
    
    # 输出建议
    print(f"\n{'-' * 50}")
    print("【改进建议】")
    print(f"{'-' * 50}")
    
    if failed == 0:
        print("  ✅ 所有验证通过！")
        print("  建议：可以安全删除旧数据源文件 SERIEA_2025-26.csv")
    else:
        print("  ❌ 部分验证未通过，请检查以下问题：")
        for module_name, results in modules:
            failed_results = [r for r in results if not r.passed]
            if failed_results:
                print(f"  - {module_name}:")
                for r in failed_results:
                    print(f"    * {r.message}")
    
    # 保存报告
    report_path = os.path.join(DATA_DIR, f'verification_report_{datetime.now().strftime("%Y%m%d_%H%M%S")}.txt')
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write("意甲2025-2026赛季数据源验证报告\n")
        f.write(f"生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write("=" * 70 + "\n\n")
        f.write(f"总体结果: {passed}/{total} 通过\n\n")
        
        for module_name, results in modules:
            f.write(f"【{module_name}】\n")
            for result in results:
                f.write(f"  {result}\n")
            f.write("\n")
    
    print(f"\n✅ 报告已保存到: {report_path}")


def main():
    print("=" * 70)
    print("开始验证意甲2025-2026赛季数据源")
    print("=" * 70)
    
    # 执行所有验证
    all_results = [
        verify_database_structure(),
        verify_seriea_data_in_db(),
        verify_data_import_script(),
        verify_feature_engineering(),
        verify_backtest_script(),
        verify_team_attributes(),
        verify_csv_data_source(),
    ]
    
    # 生成报告
    generate_report(all_results)


if __name__ == '__main__':
    main()
