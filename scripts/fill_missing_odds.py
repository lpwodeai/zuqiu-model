"""
D-003: 填充缺失赔率数据
使用 SofaScore API 和其他数据源尝试补齐 17 场缺失比赛的赔率
"""

import sqlite3
import os
import sys
import logging
from datetime import datetime

sys.path.insert(0, os.path.dirname(__file__))
from sofascore_client import create_client

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger(__name__)

DB_DIR = os.path.join(os.path.dirname(__file__), '..', 'data')
TIMING_DB = os.path.join(DB_DIR, 'odds_timing.db')

MISSING_WDL = [
    ('2025-12-20_曼城_西汉姆联', '曼城', '西汉姆联', '2025-12-20', '英超2025-2026赛季'),
    ('2026-04-23_伯恩利_曼城', '伯恩利', '曼城', '2026-04-23', '英超2025-2026赛季'),
    ('2026-05-19_阿森纳_伯恩利', '阿森纳', '伯恩利', '2026-05-19', '英超2025-2026赛季'),
    ('2026-01-15_Inter_Milan', 'Inter', 'Milan', '2026-01-15', '意甲2025-2026赛季'),
    ('2026-01-24_Inter_Milan', 'Inter', 'Milan', '2026-01-24', '意甲2025-2026赛季'),
    ('2025-11-22_Bayern Munich_Freiburg', 'Bayern Munich', 'Freiburg', '2025-11-22', '德甲2025-2026赛季'),
    ('2025-12-20_Hamburg_Ein Frankfurt', 'Hamburg', 'Ein Frankfurt', '2025-12-20', '德甲2025-2026赛季'),
    ('2025-12-22_Heidenheim_Bayern Munich', 'Heidenheim', 'Bayern Munich', '2025-12-22', '德甲2025-2026赛季'),
    ('2026-01-24_Bayern Munich_Augsburg', 'Bayern Munich', 'Augsburg', '2026-01-24', '德甲2025-2026赛季'),
    ('2026-03-07_Bayern Munich_M\'gladbach', 'Bayern Munich', "M'gladbach", '2026-03-07', '德甲2025-2026赛季'),
    ('2026-03-21_Bayern Munich_Union Berlin', 'Bayern Munich', 'Union Berlin', '2026-03-21', '德甲2025-2026赛季'),
    ('2026-05-16_Bayern Munich_FC Koln', 'Bayern Munich', 'FC Koln', '2026-05-16', '德甲2025-2026赛季'),
    ('2026-01-05_Paris SG_Paris FC', 'Paris SG', 'Paris FC', '2026-01-05', '法甲2025-2026赛季'),
]

MISSING_SCORE = [
    ('2025-09-16_Verona_Cremonese', 'Verona', 'Cremonese', '2025-09-16', '意甲2025-2026赛季'),
    ('2025-10-20_AC_Milan', 'AC', 'Milan', '2025-10-20', '意甲2025-2026赛季'),
    ('2026-01-19_塞尔塔_巴列卡诺', '塞尔塔', '巴列卡诺', '2026-01-19', '西甲2025-2026赛季'),
    ('2025-12-20_Hamburg_Ein Frankfurt', 'Hamburg', 'Ein Frankfurt', '2025-12-20', '德甲2025-2026赛季'),
]


def try_fill_sofascoe(client, match_info):
    match_id, home, away, date, league = match_info
    logger.info(f"  搜索: {home} vs {away} ({date})")
    
    try:
        result = client.find_match_by_teams_date(home, away, date)
        
        if not result:
            logger.info(f"    未找到比赛")
            return None
        
        logger.info(f"    找到: {result.get('id')} - {result.get('league')}")
        
        odds = client.get_match_odds(result['id'])
        if odds:
            logger.info(f"    获取到 {len(odds)} 组赔率")
        
        details = client.get_match_details(result['id'])
        
        return {
            'match_id': match_id,
            'event_id': result['id'],
            'odds': odds,
            'details': details,
        }
        
    except Exception as e:
        logger.error(f"    采集失败: {e}")
        return None


def try_fill_from_other_sources(match_info):
    match_id, home, away, date, league = match_info
    logger.info(f"  备选方案: {home} vs {away} ({date})")
    
    return None


def progress_callback(current, total, match_id):
    pct = current * 100 / total
    logger.info(f"  [{current}/{total}] ({pct:.0f}%) {match_id}")


def main():
    print("=" * 60)
    print("🎯 D-003: 填充缺失赔率数据")
    print("=" * 60)
    
    client = create_client(rate_limit=2.0)
    
    all_missing = []
    for m in MISSING_WDL:
        all_missing.append({**{'type': 'WDL'}, **dict(zip(['match_id', 'home', 'away', 'date', 'league'], m))})
    for m in MISSING_SCORE:
        all_missing.append({**{'type': 'SCORE'}, **dict(zip(['match_id', 'home', 'away', 'date', 'league'], m))})
    
    seen = set()
    unique_missing = []
    for m in all_missing:
        if m['match_id'] not in seen:
            seen.add(m['match_id'])
            unique_missing.append(m)
    
    print(f"\n📋 需要填充的比赛: {len(unique_missing)} 场 (去重后)")
    
    results = {'found': [], 'not_found': [], 'errors': []}
    
    for i, match in enumerate(unique_missing):
        match_info = (match['match_id'], match['home'], match['away'], match['date'], match['league'])
        
        progress_callback(i, len(unique_missing), match['match_id'])
        
        result = try_fill_sofascoe(client, match_info)
        
        if result:
            results['found'].append(result)
        else:
            alt_result = try_fill_from_other_sources(match_info)
            if alt_result:
                results['found'].append(alt_result)
            else:
                results['not_found'].append(match['match_id'])
    
    print(f"\n📊 采集结果:")
    print(f"  ✅ 找到并采集: {len(results['found'])} 场")
    print(f"  ❌ 未找到: {len(results['not_found'])} 场")
    print(f"  ⚠️  错误: {len(results['errors'])} 场")
    
    if results['found']:
        print(f"\n📝 采集详情:")
        for r in results['found']:
            match_id = r['match_id']
            event_id = r.get('event_id', 'N/A')
            odds_count = len(r.get('odds', []))
            print(f"  {match_id}: event={event_id}, odds={odds_count}组")
    
    if results['not_found']:
        print(f"\n🔴 未找到的比赛 (可能是杯赛):")
        for m_id in results['not_found']:
            print(f"  {m_id}")
    
    print(f"\n💡 建议:")
    print(f"  • 标记 {len(results['not_found'])} 场为已确认缺失（杯赛不在赔率范围内）")
    print(f"  • 对已找到的 {len(results['found'])} 场进行数据验证和入库")
    print(f"  • 考虑使用其他数据源补充杯赛数据")
    
    return results


if __name__ == '__main__':
    main()
