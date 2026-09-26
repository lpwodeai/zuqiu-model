"""
意甲2025-2026赛季数据源平滑过渡脚本

功能：
1. 检测新旧数据源状态
2. 验证新数据源完整性
3. 执行数据迁移（从旧CSV到新格式/数据库）
4. 验证迁移后功能正常
5. 安全删除旧文件（可选）

使用流程：
1. 运行脚本检测当前状态
2. 将新详细数据源放入data目录（SERIEA_2025-26_DETAILED.csv）
3. 运行脚本验证新数据源
4. 运行脚本执行数据导入
5. 运行脚本验证所有功能
6. 确认无误后运行脚本删除旧文件
"""

import os
import sys
import shutil
import sqlite3
import pandas as pd
from datetime import datetime

DATA_DIR = r'g:\zuqiu\五大联赛专属模型\五大联赛专属模型\data'
OLD_FILE = 'SERIEA_2025-26.csv'
NEW_FILE = 'SERIEA_2025-26_DETAILED.csv'
BACKUP_FILE = 'SERIEA_2025-26_BACKUP_{}.csv'
ODDS_DB = os.path.join(DATA_DIR, 'odds.db')
TIMING_DB = os.path.join(DATA_DIR, 'odds_timing.db')

OLD_FILE_PATH = os.path.join(DATA_DIR, OLD_FILE)
NEW_FILE_PATH = os.path.join(DATA_DIR, NEW_FILE)


def check_data_source_status():
    """检查新旧数据源状态"""
    print("=" * 60)
    print("1. 数据源状态检测")
    print("=" * 60)
    
    old_exists = os.path.exists(OLD_FILE_PATH)
    new_exists = os.path.exists(NEW_FILE_PATH)
    
    print(f"旧数据源 ({OLD_FILE}): {'✅ 存在' if old_exists else '❌ 不存在'}")
    print(f"新数据源 ({NEW_FILE}): {'✅ 存在' if new_exists else '❌ 不存在'}")
    
    if old_exists:
        old_size = os.path.getsize(OLD_FILE_PATH) / 1024
        df = pd.read_csv(OLD_FILE_PATH)
        print(f"   - 文件大小: {old_size:.2f} KB")
        print(f"   - 比赛场数: {len(df)} 场")
        print(f"   - 列数: {len(df.columns)}")
    
    if new_exists:
        new_size = os.path.getsize(NEW_FILE_PATH) / 1024
        df = pd.read_csv(NEW_FILE_PATH)
        print(f"   - 文件大小: {new_size:.2f} KB")
        print(f"   - 比赛场数: {len(df)} 场")
        print(f"   - 列数: {len(df.columns)}")
    
    return old_exists, new_exists


def validate_new_data_source():
    """验证新数据源格式和质量"""
    if not os.path.exists(NEW_FILE_PATH):
        print("\n❌ 新数据源不存在，跳过验证")
        return False
    
    print("\n" + "=" * 60)
    print("2. 新数据源格式验证")
    print("=" * 60)
    
    df = pd.read_csv(NEW_FILE_PATH)
    required_columns = ['主队', '客队', '日期', '时间', '主队进球', '客队进球']
    optional_columns = ['bet365_主胜', 'bet365_平局', 'bet365_客胜', 'B365CH', 'B365CD', 'B365CA']
    timing_columns = ['wdl_timing', 'handicap_timing', 'score_timing', 'total_goals_timing']
    
    errors = []
    warnings = []
    
    # 检查必需列
    for col in required_columns:
        if col not in df.columns:
            errors.append(f"缺少必需列: {col}")
    
    # 检查可选列
    for col in optional_columns:
        if col not in df.columns:
            warnings.append(f"缺少可选列: {col}")
    
    # 检查数据质量
    if '日期' in df.columns:
        date_format = df['日期'].str.match(r'\d{4}-\d{2}-\d{2}').mean()
        if date_format < 0.9:
            warnings.append(f"日期格式不一致，标准格式应为 YYYY-MM-DD")
    
    # 检查比赛完整性
    if len(df) < 10:
        warnings.append(f"比赛数据较少，建议至少包含10场比赛")
    
    # 检查赔率数据
    if 'bet365_主胜' in df.columns:
        valid_odds = df['bet365_主胜'].notna().mean()
        print(f"主胜赔率完整率: {valid_odds:.1%}")
    
    # 输出结果
    if errors:
        print("\n❌ 错误:")
        for e in errors:
            print(f"   - {e}")
        return False
    
    if warnings:
        print("\n⚠️ 警告:")
        for w in warnings:
            print(f"   - {w}")
    
    print("\n✅ 新数据源格式验证通过")
    return True


def backup_old_file():
    """备份旧数据源"""
    if not os.path.exists(OLD_FILE_PATH):
        print("\n❌ 旧数据源不存在，无需备份")
        return None
    
    print("\n" + "=" * 60)
    print("3. 备份旧数据源")
    print("=" * 60)
    
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = os.path.join(DATA_DIR, BACKUP_FILE.format(timestamp))
    
    try:
        shutil.copy2(OLD_FILE_PATH, backup_path)
        print(f"✅ 已备份到: {backup_path}")
        return backup_path
    except Exception as e:
        print(f"❌ 备份失败: {e}")
        return None


def import_new_data_to_db():
    """将新数据源导入数据库"""
    if not os.path.exists(NEW_FILE_PATH):
        print("\n❌ 新数据源不存在，无法导入")
        return False
    
    print("\n" + "=" * 60)
    print("4. 导入新数据源到数据库")
    print("=" * 60)
    
    try:
        # 运行现有的导入脚本
        sys.path.append(os.path.join(DATA_DIR, '..', 'scripts'))
        from import_all_leagues_odds import import_league
        
        config = {
            'name': 'Serie A',
            'code': 'SerieA',
            'competition_id': 4,
            'csv_file': NEW_FILE,
            'lang': 'zh',
            'team_col_home': '主队',
            'team_col_away': '客队',
            'date_col': '日期',
            'time_col': '时间',
            'home_goals_col': '主队进球',
            'away_goals_col': '客队进球',
            'result_col': '赛果',
            'wdl_home': 'bet365_主胜',
            'wdl_draw': 'bet365_平局',
            'wdl_away': 'bet365_客胜',
            'wdl_close_home': 'B365CH',
            'wdl_close_draw': 'B365CD',
            'wdl_close_away': 'B365CA',
            'tg_over': 'bet365_大2.5',
            'tg_under': 'bet365_小2.5',
            'tg_close_over': 'B365C>2.5',
            'tg_close_under': 'B365C<2.5',
            'hcp_line': '亚盘盘口',
            'hcp_home': 'bet365_亚盘主',
            'hcp_away': 'bet365_亚盘客',
            'hcp_close_home': 'B365CAHH',
            'hcp_close_away': 'B365CAHA',
        }
        
        m, w, h, t, mp = import_league(config)
        print(f"\n导入结果: {m}场比赛, {w}条WDL, {h}条让球, {t}条总进球, {mp}条映射")
        
        if m > 0:
            print("✅ 新数据导入成功")
            return True
        else:
            print("❌ 新数据导入失败")
            return False
    
    except Exception as e:
        print(f"❌ 导入过程出错: {e}")
        return False


def verify_database_integrity():
    """验证数据库完整性"""
    print("\n" + "=" * 60)
    print("5. 验证数据库完整性")
    print("=" * 60)
    
    if not os.path.exists(ODDS_DB):
        print("❌ odds.db 不存在")
        return False
    
    conn = sqlite3.connect(ODDS_DB)
    cursor = conn.cursor()
    
    try:
        # 检查matches表
        cursor.execute("SELECT COUNT(*) FROM matches WHERE match_id LIKE '%_Atalanta_%' OR match_id LIKE '%_Juventus_%' OR match_id LIKE '%_Milan_%' OR match_id LIKE '%_Inter_%'")
        seriea_count = cursor.fetchone()[0]
        print(f"意甲比赛记录数: {seriea_count}")
        
        # 检查赔率数据
        cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id IN (SELECT match_id FROM matches WHERE match_id LIKE '%_Atalanta_%' OR match_id LIKE '%_Juventus_%')")
        wdl_count = cursor.fetchone()[0]
        print(f"意甲胜平负赔率记录: {wdl_count}")
        
        # 检查映射表
        cursor.execute("SELECT COUNT(*) FROM match_mapping WHERE league LIKE '%Serie%'")
        mapping_count = cursor.fetchone()[0]
        print(f"意甲比赛映射记录: {mapping_count}")
        
        if seriea_count > 0:
            print("\n✅ 数据库验证通过")
            return True
        else:
            print("\n❌ 数据库中没有意甲数据")
            return False
    
    except Exception as e:
        print(f"❌ 数据库验证失败: {e}")
        return False
    finally:
        conn.close()


def verify_scripts_compatibility():
    """验证脚本兼容性"""
    print("\n" + "=" * 60)
    print("6. 验证脚本兼容性")
    print("=" * 60)
    
    scripts = [
        ('import_all_leagues_odds.py', '数据导入脚本'),
        ('feature_engineering_pipeline.py', '特征工程脚本'),
        ('backtest_with_odds.py', '回测脚本'),
        ('generate_team_attributes.py', '球队属性生成脚本'),
    ]
    
    scripts_dir = os.path.join(DATA_DIR, '..', 'scripts')
    all_pass = True
    
    for script, desc in scripts:
        script_path = os.path.join(scripts_dir, script)
        if os.path.exists(script_path):
            # 检查脚本中是否包含get_csv_file函数或新数据源配置
            with open(script_path, 'r', encoding='utf-8') as f:
                content = f.read()
            
            if 'CSV_FILES_DETAILED' in content or 'get_csv_file' in content:
                print(f"✅ {script} - {desc} (已更新)")
            else:
                print(f"⚠️ {script} - {desc} (未更新，可能不支持新数据源)")
                all_pass = False
        else:
            print(f"❌ {script} - {desc} (脚本不存在)")
            all_pass = False
    
    if all_pass:
        print("\n✅ 所有脚本兼容性验证通过")
    else:
        print("\n⚠️ 部分脚本需要更新")
    
    return all_pass


def delete_old_file():
    """安全删除旧数据源"""
    if not os.path.exists(OLD_FILE_PATH):
        print("\n❌ 旧数据源不存在，无需删除")
        return True
    
    print("\n" + "=" * 60)
    print("7. 删除旧数据源")
    print("=" * 60)
    print("⚠️ 警告：此操作将永久删除旧数据源文件！")
    print("请确保：")
    print("   1. 新数据源已成功导入")
    print("   2. 数据库验证通过")
    print("   3. 所有脚本兼容性验证通过")
    
    # 交互式确认
    confirm = input("\n确认删除旧文件吗？(y/N): ").strip().lower()
    if confirm != 'y':
        print("❌ 用户取消删除")
        return False
    
    try:
        os.remove(OLD_FILE_PATH)
        print(f"✅ 已删除旧文件: {OLD_FILE_PATH}")
        return True
    except Exception as e:
        print(f"❌ 删除失败: {e}")
        return False


def main():
    print("=" * 60)
    print("意甲2025-2026赛季数据源平滑过渡工具")
    print(f"运行时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 60)
    
    # 解析命令行参数
    action = 'all'
    if len(sys.argv) > 1:
        action = sys.argv[1].lower()
    
    # 根据参数执行不同操作
    if action == 'status':
        check_data_source_status()
    
    elif action == 'validate':
        check_data_source_status()
        validate_new_data_source()
    
    elif action == 'backup':
        backup_old_file()
    
    elif action == 'import':
        check_data_source_status()
        validate_new_data_source()
        import_new_data_to_db()
    
    elif action == 'verify':
        verify_database_integrity()
        verify_scripts_compatibility()
    
    elif action == 'delete':
        verify_database_integrity()
        verify_scripts_compatibility()
        delete_old_file()
    
    elif action == 'all':
        # 完整流程
        old_exists, new_exists = check_data_source_status()
        
        if not new_exists:
            print("\n" + "=" * 60)
            print("❌ 新数据源不存在，请先提供新详细数据源")
            print(f"请将新文件命名为: {NEW_FILE}")
            print(f"放置到目录: {DATA_DIR}")
            print("=" * 60)
            sys.exit(1)
        
        # 验证新数据源
        if not validate_new_data_source():
            print("\n" + "=" * 60)
            print("❌ 新数据源验证失败，请检查数据格式")
            print("=" * 60)
            sys.exit(1)
        
        # 备份旧文件
        backup_path = backup_old_file()
        
        # 导入新数据
        if not import_new_data_to_db():
            print("\n" + "=" * 60)
            print("❌ 新数据导入失败")
            if backup_path:
                print(f"提示：可从备份恢复: {backup_path}")
            print("=" * 60)
            sys.exit(1)
        
        # 验证数据库和脚本
        db_ok = verify_database_integrity()
        script_ok = verify_scripts_compatibility()
        
        if not db_ok or not script_ok:
            print("\n" + "=" * 60)
            print("❌ 验证未通过，请检查问题")
            if backup_path:
                print(f"提示：可从备份恢复: {backup_path}")
            print("=" * 60)
            sys.exit(1)
        
        # 删除旧文件（交互式）
        print("\n" + "=" * 60)
        print("✅ 所有验证通过！")
        print("=" * 60)
        
        delete_old_file()
        
        print("\n" + "=" * 60)
        print("过渡流程完成！")
        print("=" * 60)
    
    else:
        print("用法:")
        print("  python transition_seriea_data.py [action]")
        print("  action:")
        print("    status      - 检查数据源状态")
        print("    validate    - 验证新数据源格式")
        print("    backup      - 备份旧数据源")
        print("    import      - 导入新数据源")
        print("    verify      - 验证数据库和脚本")
        print("    delete      - 删除旧数据源")
        print("    all         - 执行完整过渡流程（默认）")


if __name__ == '__main__':
    main()
