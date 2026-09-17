"""
⚠️ 已弃用 (DEPRECATED) —— 请勿运行本脚本 ⚠️
======================================================================

match_id_mapping 表当前已由 score_match_enhancer.py 建表
（列：sh_match_id / matches_match_id / match_method / confidence），
并由 fill_missing_matches.py 补充映射。本脚本的建表 schema 已过时，
且内含 `DELETE FROM match_id_mapping`，运行会清空正确映射并重建为错误结构。

历史背景（保留备查）
--------------------
为 wdl_history / handicap_history / total_goals_history 建立 match_id 映射
======================================================================

问题：history 表使用中文球队名 match_id（如 "2023-08-12_伯恩利_曼彻斯特城"），
      matches 表使用英文球队名 match_id（如 "2025-08-24_Genoa_Lecce"），
      两者无法直接 LEFT JOIN。

解决：复用 TEAM_NAME_MAP（193 条）+ normalize_team_name，构建中→英候选 match_id，
      在 matches 表中匹配，建立映射表。

策略（与 score_features.py 相同思路，方向相反）：
    1. 从 history 表收集所有唯一 match_id（中文格式）
    2. 解析 match_id → date + home_cn + away_cn
    3. 构建反向映射（中文→英文候选列表）
    4. 生成英文候选 match_id → 在 matches 表中查找
    5. 写入 match_id_mapping 表

运行: python scripts/create_odds_match_mapping.py
"""

import sqlite3
import sys
import os
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feature_utils import TEAM_NAME_MAP, normalize_team_name

DB_PATH = BASE_DIR / "data" / "odds.db"

# ── 弃用告警：阻止误运行，防止清空正确映射表 ──
print(
    "[弃用] create_odds_match_mapping.py 已废弃，请勿运行！\n"
    "  match_id_mapping 表已由 score_match_enhancer.py 建表、fill_missing_matches.py 补充；\n"
    "  本脚本建表 schema 已过时，且含 `DELETE FROM match_id_mapping`，运行会清空正确映射。"
)
sys.exit(1)


def build_reverse_map():
    """
    构建中文→英文候选列表的反向映射
    
    因为多个英文名可能映射到同一个中文名（如 'Inter'→'国际米兰', 'Inter Milan'→'国际米兰'），
    所以反向映射是一个 中文名→[英文名列表] 的字典。
    """
    cn_to_en = {}
    for eng, chn in TEAM_NAME_MAP.items():
        if chn not in cn_to_en:
            cn_to_en[chn] = []
        if eng not in cn_to_en[chn]:
            cn_to_en[chn].append(eng)
    return cn_to_en


def parse_chinese_match_id(match_id: str):
    """
    解析中文格式 match_id: 'YYYY-MM-DD_中文主队_中文客队'
    
    返回: (date_str, home_cn, away_cn) 或 None
    """
    parts = match_id.split('_')
    if len(parts) < 3:
        return None
    date_str = parts[0]
    home_cn = parts[1]
    away_cn = parts[2]
    return date_str, home_cn, away_cn


def build_english_candidates(date_str: str, home_cn: str, away_cn: str, cn_to_en: dict):
    """
    为中文 match_id 构建英文候选 match_id 列表
    
    策略：
    1. 先通过 normalize_team_name 标准化中文名（处理旧标准名→变体名链）
    2. 从反向映射中获取所有可能的英文名
    3. 生成笛卡尔积候选 match_id
    4. 支持 ±1 天日期容差
    """
    candidates = []
    
    # 步骤 1: 先标准化中文名（处理 "巴利亚多利德"→"瓦拉多利德" 这样的链）
    home_cn_normalized = normalize_team_name(home_cn)
    away_cn_normalized = normalize_team_name(away_cn)
    
    # 步骤 2: 获取主队和客队的英文候选（优先用标准化后的名）
    home_en_candidates = list(cn_to_en.get(home_cn_normalized, []))
    away_en_candidates = list(cn_to_en.get(away_cn_normalized, []))
    
    # 如果标准化后也查不到，尝试用原始名
    if not home_en_candidates and home_cn_normalized != home_cn:
        home_en_candidates = list(cn_to_en.get(home_cn, []))
    if not away_en_candidates and away_cn_normalized != away_cn:
        away_en_candidates = list(cn_to_en.get(away_cn, []))
    
    # 如果反查不到，也尝试直接用中文名本身（可能是英文缩写已被存入）
    if not home_en_candidates:
        home_en_candidates = [home_cn, home_cn_normalized]
    if not away_en_candidates:
        away_en_candidates = [away_cn, away_cn_normalized]
    
    # 去重
    home_en_candidates = list(dict.fromkeys(home_en_candidates))
    away_en_candidates = list(dict.fromkeys(away_en_candidates))
    
    # 步骤 3: 生成所有候选 match_id（笛卡尔积），支持 ±1 天日期容差
    from datetime import datetime, timedelta
    date_variants = [date_str]
    try:
        dt = datetime.strptime(date_str, '%Y-%m-%d')
        date_variants.append((dt - timedelta(days=1)).strftime('%Y-%m-%d'))
        date_variants.append((dt + timedelta(days=1)).strftime('%Y-%m-%d'))
    except ValueError:
        pass
    
    for d in date_variants:
        for home_en in home_en_candidates:
            for away_en in away_en_candidates:
                en_match_id = f"{d}_{home_en}_{away_en}"
                if en_match_id not in candidates:
                    candidates.append(en_match_id)
    
    return candidates


def main():
    print("=" * 70)
    print("🔗 建立 odds history 表 → matches 表 match_id 映射")
    print("=" * 70)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # ── 1. 构建反向映射 ──
    print("\n📋 步骤 1: 构建中文→英文反向映射...")
    cn_to_en = build_reverse_map()
    print(f"   中文名数量: {len(cn_to_en)}")
    print(f"   英文名总数: {len(TEAM_NAME_MAP)}")
    print(f"   示例: 国际米兰 → {cn_to_en.get('国际米兰', ['N/A'])}")
    print(f"   示例: 曼彻斯特城 → {cn_to_en.get('曼彻斯特城', ['N/A'])}")
    print(f"   示例: 布赖顿 → {cn_to_en.get('布赖顿', ['N/A'])}")
    
    # ── 2. 收集所有需要映射的 match_id ──
    print("\n📋 步骤 2: 收集所有 history 表中的唯一 match_id...")
    
    history_tables = ['wdl_history', 'handicap_history', 'total_goals_history']
    all_history_ids = set()
    
    for table in history_tables:
        cursor.execute(f"SELECT DISTINCT match_id FROM {table}")
        ids = {row[0] for row in cursor.fetchall()}
        all_history_ids.update(ids)
        print(f"   {table}: {len(ids)} 个唯一 match_id")
    
    print(f"   合并去重后: {len(all_history_ids)} 个唯一 match_id")
    
    # ── 3. 加载 matches 表的所有 match_id ──
    print("\n📋 步骤 3: 加载 matches 表 match_id 索引...")
    cursor.execute("SELECT match_id, match_type, home_team, away_team FROM matches")
    matches_rows = cursor.fetchall()
    matches_ids = {row[0] for row in matches_rows}
    print(f"   matches 表: {len(matches_ids)} 个 match_id")
    
    # 也构建一个 (date, home, away) → match_id 的备用索引（用于日期格式差异）
    date_team_index = {}
    for row in matches_rows:
        mid, mtype, home, away = row
        parts = mid.split('_')
        if len(parts) >= 3:
            key = (parts[0], home.lower(), away.lower())
            date_team_index[key] = mid
    
    # ── 4. 执行匹配 ──
    print("\n📋 步骤 4: 执行中→英 match_id 匹配...")
    
    mappings = []  # (sh_match_id, matches_match_id)
    failed = []    # 匹配失败的 sh_match_id
    matched_count = 0
    total = len(all_history_ids)
    
    start_time = time.time()
    
    for h_mid in all_history_ids:
        parsed = parse_chinese_match_id(h_mid)
        if parsed is None:
            failed.append((h_mid, "无法解析"))
            continue
        
        date_str, home_cn, away_cn = parsed
        
        # 策略 1: 构建英文候选 match_id，直接在 matches_ids 中查找
        en_candidates = build_english_candidates(date_str, home_cn, away_cn, cn_to_en)
        
        found = False
        for en_mid in en_candidates:
            if en_mid in matches_ids:
                mappings.append((h_mid, en_mid))
                matched_count += 1
                found = True
                break
        
        if found:
            continue
        
        # 策略 2: 用 (date, home_team, away_team) 模糊匹配（忽略大小写）
        home_cn_lower = home_cn.lower()
        away_cn_lower = away_cn.lower()
        
        # 尝试所有可能的英文名
        home_en_list = cn_to_en.get(home_cn, [home_cn])
        away_en_list = cn_to_en.get(away_cn, [away_cn])
        
        for home_en in home_en_list:
            for away_en in away_en_list:
                key = (date_str, home_en.lower(), away_en.lower())
                if key in date_team_index:
                    mappings.append((h_mid, date_team_index[key]))
                    matched_count += 1
                    found = True
                    break
            if found:
                break
        
        if not found:
            failed.append((h_mid, f"无匹配: {date_str}_{home_cn}_{away_cn}"))
        
        # 进度报告
        if (matched_count + len(failed)) % 500 == 0:
            elapsed = time.time() - start_time
            progress = (matched_count + len(failed)) / total * 100
            print(f"   进度: {matched_count + len(failed)}/{total} ({progress:.1f}%), "
                  f"匹配: {matched_count}, 失败: {len(failed)}, 耗时: {elapsed:.1f}s")
    
    elapsed = time.time() - start_time
    print(f"\n   完成! 匹配: {matched_count}/{total} ({matched_count/total*100:.1f}%), "
          f"失败: {len(failed)}, 耗时: {elapsed:.1f}s")
    
    # ── 5. 写入映射表 ──
    print("\n📋 步骤 5: 写入 match_id_mapping 表...")
    
    # 创建映射表（如果不存在）
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS match_id_mapping (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sh_match_id TEXT NOT NULL,
            matches_match_id TEXT NOT NULL,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(sh_match_id)
        )
    """)
    
    # 清空旧映射
    cursor.execute("DELETE FROM match_id_mapping")
    
    # 批量插入
    cursor.executemany(
        "INSERT INTO match_id_mapping (sh_match_id, matches_match_id) VALUES (?, ?)",
        mappings
    )
    
    conn.commit()
    print(f"   已写入 {len(mappings)} 条映射记录")
    
    # ── 6. 验证覆盖率 ──
    print("\n📋 步骤 6: 验证各表覆盖率...")
    
    for table in history_tables:
        cursor.execute(f"""
            SELECT 
                COUNT(DISTINCT h.match_id) as total_history,
                COUNT(DISTINCT m.matches_match_id) as mapped,
                COUNT(DISTINCT CASE WHEN m.matches_match_id IS NOT NULL THEN h.match_id END) as matched_history
            FROM {table} h
            LEFT JOIN match_id_mapping m ON h.match_id = m.sh_match_id
        """)
        row = cursor.fetchone()
        total_h, mapped_matches, matched_h = row
        print(f"   {table}: {matched_h}/{total_h} history match_id 已映射 "
              f"({matched_h/total_h*100:.1f}%), 对应 {mapped_matches} 个 matches match_id")
    
    # 按联赛统计覆盖率
    print("\n📋 步骤 7: 按联赛统计覆盖率...")
    for table in history_tables:
        cursor.execute(f"""
            SELECT 
                mt.match_type,
                COUNT(DISTINCT h.match_id) as total,
                COUNT(DISTINCT CASE WHEN m.matches_match_id IS NOT NULL THEN h.match_id END) as matched
            FROM {table} h
            LEFT JOIN match_id_mapping m ON h.match_id = m.sh_match_id
            LEFT JOIN matches mt ON m.matches_match_id = mt.match_id
            GROUP BY mt.match_type
            ORDER BY mt.match_type
        """)
        rows = cursor.fetchall()
        if rows:
            print(f"\n   --- {table} ---")
            for row in rows:
                league, total_cnt, matched_cnt = row
                pct = matched_cnt / total_cnt * 100 if total_cnt > 0 else 0
                print(f"   {league or '未知'}: {matched_cnt}/{total_cnt} ({pct:.1f}%)")
    
    # ── 7. 失败样例 ──
    if failed:
        print(f"\n📋 匹配失败样例 (共 {len(failed)} 个):")
        for h_mid, reason in failed[:10]:
            print(f"   {h_mid} → {reason}")
        if len(failed) > 10:
            print(f"   ... 还有 {len(failed) - 10} 个")
    
    conn.close()
    
    print("\n" + "=" * 70)
    print(f"✅ match_id 映射完成!")
    print(f"   总映射数: {matched_count}/{total} ({matched_count/total*100:.1f}%)")
    print(f"   映射表: odds.db → match_id_mapping")
    print("=" * 70)


if __name__ == '__main__':
    main()