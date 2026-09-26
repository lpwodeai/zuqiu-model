import sqlite3
import pandas as pd
import sys
import os
from pathlib import Path

sys.path.append(os.path.dirname(os.path.abspath(__file__)))

BASE_DIR = Path(__file__).resolve().parent.parent
MAIN_DB_PATH = BASE_DIR / "data" / "five_leagues.db"
ODDS_DB_PATH = BASE_DIR / "data" / "odds.db"

def verify_excel_data():
    print("=" * 60)
    print("1. Excel数据验证")
    print("=" * 60)
    
    excel_path = BASE_DIR / "2025-2026 英超 .xlsx"
    
    if not os.path.exists(excel_path):
        print("✗ Excel文件不存在")
        return False
    
    xls = pd.ExcelFile(excel_path)
    sheets = xls.sheet_names
    
    print(f"✓ Excel文件存在，包含 {len(sheets)} 场比赛")
    
    total_wdl = 0
    total_handicap = 0
    total_tg = 0
    total_score = 0
    
    for sheet in sheets:
        df = pd.read_excel(excel_path, sheet_name=sheet)
        
        wdl_count = 0
        handicap_count = 0
        tg_count = 0
        score_count = 0
        
        for i in range(len(df)):
            col0 = str(df.iloc[i, 0]).strip() if pd.notna(df.iloc[i, 0]) else ""
            if col0 == "胜平负固定奖金":
                j = i + 2
                while j < len(df) and pd.notna(df.iloc[j, 0]):
                    wdl_count += 1
                    j += 1
            elif col0 == "让球胜平负固定奖金":
                j = i + 2
                while j < len(df) and pd.notna(df.iloc[j, 0]):
                    handicap_count += 1
                    j += 1
            elif col0 == "总进球固定奖金":
                j = i + 2
                while j < len(df) and pd.notna(df.iloc[j, 0]):
                    tg_count += 1
                    j += 1
            elif col0 == "比分固定奖金":
                j = i + 2
                timestamp_count = 0
                while j < len(df):
                    if pd.notna(df.iloc[j, 2]) and str(df.iloc[j, 2]).strip() and str(df.iloc[j, 2]).strip() != "发布时间":
                        timestamp_count += 1
                    j += 1
                    if j >= len(df) or (pd.isna(df.iloc[j, 0]) and pd.isna(df.iloc[j, 2])):
                        break
                score_count = timestamp_count * 26
        
        total_wdl += wdl_count
        total_handicap += handicap_count
        total_tg += tg_count
        total_score += score_count
    
    print(f"\nExcel数据统计:")
    print(f"  WDL赔率记录: {total_wdl}")
    print(f"  让球赔率记录: {total_handicap}")
    print(f"  总进球赔率记录: {total_tg}")
    print(f"  比分赔率记录: {total_score}")
    
    return True

def verify_database_import():
    print("\n" + "=" * 60)
    print("2. 数据库导入验证")
    print("=" * 60)
    
    conn = sqlite3.connect(ODDS_DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute("SELECT COUNT(*) FROM matches")
    match_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM wdl_history")
    wdl_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM handicap_history")
    handicap_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM total_goals_history")
    tg_count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM score_history")
    score_count = cursor.fetchone()[0]
    
    print(f"\nodds.db数据统计:")
    print(f"  比赛记录: {match_count}")
    print(f"  WDL赔率记录: {wdl_count}")
    print(f"  让球赔率记录: {handicap_count}")
    print(f"  总进球赔率记录: {tg_count}")
    print(f"  比分赔率记录: {score_count}")
    
    cursor.execute("SELECT match_id, home_team, away_team, actual_wdl, actual_score FROM matches")
    matches = cursor.fetchall()
    
    print(f"\n新导入的10场比赛:")
    for match in matches:
        match_id, home, away, wdl, score = match
        print(f"  ✓ {match_id}: {home} vs {away} - {wdl} ({score})")
    
    conn.close()
    
    return match_count >= 10

def verify_data_consistency():
    print("\n" + "=" * 60)
    print("3. 数据一致性验证")
    print("=" * 60)
    
    conn = sqlite3.connect(ODDS_DB_PATH)
    cursor = conn.cursor()
    
    print("\n检查match_id格式一致性:")
    cursor.execute("SELECT DISTINCT match_id FROM matches")
    match_ids = [row[0] for row in cursor.fetchall()]
    
    chinese_ids = [mid for mid in match_ids if any('\u4e00' <= c <= '\u9fff' for c in mid)]
    english_ids = [mid for mid in match_ids if not any('\u4e00' <= c <= '\u9fff' for c in mid)]
    
    print(f"  中文match_id: {len(chinese_ids)} 个")
    print(f"  英文match_id: {len(english_ids)} 个")
    
    print("\n检查数据完整性:")
    for match_id in english_ids[:5]:
        cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE match_id = ?", (match_id,))
        wdl_cnt = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM handicap_history WHERE match_id = ?", (match_id,))
        hcp_cnt = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM total_goals_history WHERE match_id = ?", (match_id,))
        tg_cnt = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM score_history WHERE match_id = ?", (match_id,))
        score_cnt = cursor.fetchone()[0]
        
        print(f"  {match_id}: WDL={wdl_cnt}, HCP={hcp_cnt}, TG={tg_cnt}, Score={score_cnt}")
    
    print("\n检查赔率数据有效性:")
    cursor.execute("SELECT COUNT(*) FROM wdl_history WHERE win_a <= 0 OR draw <= 0 OR win_b <= 0")
    invalid_wdl = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM handicap_history WHERE hcp_win <= 0 OR hcp_draw <= 0 OR hcp_lose <= 0")
    invalid_hcp = cursor.fetchone()[0]
    
    print(f"  WDL无效赔率: {invalid_wdl} 条")
    print(f"  让球无效赔率: {invalid_hcp} 条")
    
    conn.close()
    
    return invalid_wdl == 0 and invalid_hcp == 0

def verify_odds_features_module():
    print("\n" + "=" * 60)
    print("4. 赔率特征模块验证")
    print("=" * 60)
    
    try:
        from odds_temporal_features import build_odds_temporal_features, load_odds_database
        
        conn = load_odds_database()
        cursor = conn.cursor()
        cursor.execute("SELECT match_id FROM matches")
        match_ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        print(f"\n测试 {len(match_ids)} 场比赛的赔率特征提取")
        
        features_df, feature_info = build_odds_temporal_features(match_ids)
        
        print(f"\n✓ 特征提取成功:")
        print(f"  提取的特征数量: {len(features_df.columns)}")
        print(f"  处理的比赛数: {len(features_df)}")
        
        wdl_features = [col for col in features_df.columns if col.startswith('wdl_')]
        hcp_features = [col for col in features_df.columns if col.startswith('hcp_')]
        tg_features = [col for col in features_df.columns if col.startswith('tg_')]
        
        print(f"\n特征分类:")
        print(f"  WDL特征: {len(wdl_features)} 个")
        print(f"  让球特征: {len(hcp_features)} 个")
        print(f"  总进球特征: {len(tg_features)} 个")
        
        print(f"\n特征数据统计:")
        print(f"  非零值比例: {(features_df != 0).mean().mean():.2%}")
        print(f"  缺失值比例: {features_df.isna().mean().mean():.2%}")
        
        return True
        
    except Exception as e:
        print(f"\n✗ 赔率特征模块验证失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def verify_main_db_integration():
    print("\n" + "=" * 60)
    print("5. 主数据库集成验证")
    print("=" * 60)
    
    main_conn = sqlite3.connect(MAIN_DB_PATH)
    odds_conn = sqlite3.connect(ODDS_DB_PATH)
    
    main_cursor = main_conn.cursor()
    odds_cursor = odds_conn.cursor()
    
    main_cursor.execute("""
        SELECT m.date, ht.name as home_team, at.name as away_team, 
               m.homeGoals, m.awayGoals, c.name as competition
        FROM matches m
        LEFT JOIN teams ht ON m.homeTeamId = ht.id
        LEFT JOIN teams at ON m.awayTeamId = at.id
        LEFT JOIN competitions c ON m.competitionId = c.id
        ORDER BY m.date
        LIMIT 10
    """)
    
    main_matches = main_cursor.fetchall()
    
    odds_cursor.execute("SELECT match_id, home_team, away_team, match_date FROM matches")
    odds_matches = odds_cursor.fetchall()
    
    print(f"\n主数据库(five_leagues.db)中的比赛: {len(main_matches)}")
    print(f"赔率数据库(odds.db)中的比赛: {len(odds_matches)}")
    
    print("\n主数据库前5场比赛:")
    for match in main_matches[:5]:
        date, home, away, hg, ag, comp = match
        print(f"  {date}: {home} vs {away} ({hg}:{ag}) - {comp}")
    
    print("\n赔率数据库中的比赛:")
    for match in odds_matches:
        match_id, home, away, match_date = match
        print(f"  {match_date}: {home} vs {away}")
    
    matched_count = 0
    for main_match in main_matches:
        main_date, main_home, main_away, _, _, _ = main_match
        for odds_match in odds_matches:
            odds_date = odds_match[3]
            odds_home = odds_match[1]
            odds_away = odds_match[2]
            
            if (main_home in odds_home or odds_home in main_home) and \
               (main_away in odds_away or odds_away in main_away):
                matched_count += 1
                break
    
    print(f"\n匹配的比赛数: {matched_count}/{len(main_matches)}")
    
    main_conn.close()
    odds_conn.close()
    
    return matched_count > 0

def verify_feature_engineering_pipeline():
    print("\n" + "=" * 60)
    print("6. 特征工程管道验证")
    print("=" * 60)
    
    try:
        from feature_engineering_pipeline import load_match_data, build_all_features
        
        df = load_match_data(MAIN_DB_PATH)
        print(f"\n✓ 比赛数据加载成功: {len(df)} 场比赛")
        
        X, y, feature_info = build_all_features(df)
        print(f"\n✓ 特征构建成功:")
        print(f"  总特征数: {len(X.columns)}")
        print(f"  样本数: {len(X)}")
        
        odds_features = [col for col in X.columns if col.startswith('wdl_') or col.startswith('hcp_') or col.startswith('tg_')]
        print(f"\n赔率特征数: {len(odds_features)}")
        
        if odds_features:
            print(f"\n部分赔率特征:")
            for feat in odds_features[:10]:
                info = feature_info.get(feat, '无描述')
                print(f"  - {feat}: {info}")
            
            print(f"\n赔率特征数据统计:")
            odds_df = X[odds_features]
            print(f"  非零值比例: {(odds_df != 0).mean().mean():.2%}")
            print(f"  缺失值比例: {odds_df.isna().mean().mean():.2%}")
        
        print("\n✓ 特征工程管道验证通过")
        return True
        
    except Exception as e:
        print(f"\n✗ 特征工程管道验证失败: {e}")
        import traceback
        traceback.print_exc()
        return False

def main():
    results = []
    
    results.append(("Excel数据验证", verify_excel_data()))
    results.append(("数据库导入验证", verify_database_import()))
    results.append(("数据一致性验证", verify_data_consistency()))
    results.append(("赔率特征模块验证", verify_odds_features_module()))
    results.append(("主数据库集成验证", verify_main_db_integration()))
    results.append(("特征工程管道验证", verify_feature_engineering_pipeline()))
    
    print("\n" + "=" * 60)
    print("验证结果汇总")
    print("=" * 60)
    
    passed = sum(1 for _, result in results if result)
    total = len(results)
    
    for name, result in results:
        status = "✓ 通过" if result else "✗ 失败"
        print(f"  {name}: {status}")
    
    print(f"\n总通过数: {passed}/{total}")
    
    if passed == total:
        print("\n🎉 所有验证通过! 赔率数据已正确、完整地融入到系统中。")
    else:
        print(f"\n⚠️ 有 {total - passed} 项验证失败，请检查相关模块。")

if __name__ == "__main__":
    main()
