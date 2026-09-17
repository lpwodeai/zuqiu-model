#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
导出 TEAM_NAME_MAP 为共享 JSON 文件，供 Node.js 端使用

解决 C-004: TEAM_NAME_MAP 双源映射统一
Python 端 193 条 ↔ Node.js 端 65 条 → 统一为共享 JSON 文件

用法:
    python scripts/export_team_name_map.py
输出:
    assets/team_name_map.json  (中→英映射)
    assets/team_name_map_reverse.json  (英→中映射)
"""

import json
import os
import sys

# 添加 scripts 目录到 path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from feature_utils import TEAM_NAME_MAP

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, 'assets')
os.makedirs(ASSETS_DIR, exist_ok=True)

def build_export_maps():
    """
    从 TEAM_NAME_MAP 构建导出映射

    TEAM_NAME_MAP 结构: {english_name: chinese_name}
    包含两种映射:
    1. English → Chinese (主映射, 如 'Manchester City': '曼彻斯特城')
    2. Chinese → Chinese (别名映射, 如 '曼城': '曼彻斯特城')

    需要导出:
    1. chinese_name → english_name (供 Node.js 查找用)
    2. english_name → chinese_name (反向映射)
    """
    # 中→英映射 (主映射, 供预测服务用)
    cn_to_en = {}

    # 英→中映射 (反向映射)
    en_to_cn = {}

    # 中文别名链: {别名: 标准中文名}
    cn_aliases = {}

    # 第一遍: 处理 English → Chinese 映射
    for eng_name, cn_name in TEAM_NAME_MAP.items():
        is_chinese_key = any('\u4e00' <= c <= '\u9fff' for c in eng_name)

        if is_chinese_key:
            # 中文→中文别名，暂存，第二遍处理
            cn_aliases[eng_name] = cn_name
            continue

        # 标准化英文名 (小写)
        eng_key = eng_name.lower().strip()

        # 中→英
        if cn_name not in cn_to_en:
            cn_to_en[cn_name] = eng_key
        elif len(eng_key) < len(cn_to_en[cn_name]):
            # 优先使用较短的英文名 (更通用)
            cn_to_en[cn_name] = eng_key

        # 英→中
        if eng_key not in en_to_cn:
            en_to_cn[eng_key] = cn_name

    # 第二遍: 处理中文别名链
    # 例如: '曼城' → '曼彻斯特城' → 'manchester city'
    # 结果: cn_to_en['曼城'] = 'manchester city'
    alias_count = 0
    for alias, standard_name in cn_aliases.items():
        if standard_name in cn_to_en:
            if alias not in cn_to_en:
                cn_to_en[alias] = cn_to_en[standard_name]
                alias_count += 1
        elif alias in cn_to_en:
            # 反向: 标准名是别名，别名已有英文映射
            if standard_name not in cn_to_en:
                cn_to_en[standard_name] = cn_to_en[alias]
                alias_count += 1

    if alias_count > 0:
        print(f"   🔗 已解析 {alias_count} 条中文别名链")

    return cn_to_en, en_to_cn


def main():
    cn_to_en, en_to_cn = build_export_maps()

    # 写入中→英映射
    output_path = os.path.join(ASSETS_DIR, 'team_name_map.json')
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(cn_to_en, f, ensure_ascii=False, indent=2)
    print(f"✅ 中→英映射已导出: {output_path} ({len(cn_to_en)} 条)")

    # 写入英→中映射
    reverse_path = os.path.join(ASSETS_DIR, 'team_name_map_reverse.json')
    with open(reverse_path, 'w', encoding='utf-8') as f:
        json.dump(en_to_cn, f, ensure_ascii=False, indent=2)
    print(f"✅ 英→中映射已导出: {reverse_path} ({len(en_to_cn)} 条)")

    # 打印统计
    print(f"\n📊 导出统计:")
    print(f"   原始 TEAM_NAME_MAP: {len(TEAM_NAME_MAP)} 条")
    print(f"   中→英映射: {len(cn_to_en)} 条")
    print(f"   英→中映射: {len(en_to_cn)} 条")

    # 打印各联赛覆盖
    leagues = {'英超': 0, '西甲': 0, '意甲': 0, '德甲': 0, '法甲': 0, '其他': 0}
    league_keywords = {
        '英超': ['arsenal', 'chelsea', 'liverpool', 'manchester', 'tottenham', 'newcastle',
                 'everton', 'fulham', 'brighton', 'west ham', 'brentford', 'crystal palace',
                 'wolves', 'nottingham', 'bournemouth', 'burnley', 'leeds', 'leicester',
                 'southampton', 'sheffield', 'luton', 'ipswich', 'sunderland'],
        '西甲': ['barcelona', 'real madrid', 'atletico', 'sevilla', 'valencia', 'villarreal',
                 'real sociedad', 'real betis', 'athletic', 'getafe', 'osasuna', 'girona',
                 'mallorca', 'rayo', 'cadiz', 'alaves', 'valladolid', 'las palmas',
                 'leganes', 'almeria', 'celta', 'granada', 'elche', 'espanyol', 'levante', 'oviedo'],
        '意甲': ['juventus', 'inter', 'milan', 'napoli', 'roma', 'lazio', 'atalanta',
                 'fiorentina', 'bologna', 'torino', 'udinese', 'genoa', 'verona',
                 'lecce', 'cagliari', 'empoli', 'monza', 'como', 'cremonese',
                 'parma', 'pisa', 'sassuolo', 'frosinone', 'salernitana', 'venezia'],
        '德甲': ['bayern', 'dortmund', 'leipzig', 'leverkusen', 'frankfurt', 'stuttgart',
                 'gladbach', 'werder', 'augsburg', 'wolfsburg', 'mainz', 'freiburg',
                 'union berlin', 'koln', 'heidenheim', 'holstein', 'st. pauli',
                 'hoffenheim', 'hamburg', 'darmstadt', 'bochum'],
        '法甲': ['paris', 'marseille', 'monaco', 'lyon', 'lille', 'rennes', 'nice',
                 'lens', 'reims', 'brest', 'angers', 'strasbourg', 'toulouse',
                 'saint-etienne', 'auxerre', 'nantes', 'le havre', 'lorient',
                 'clermont', 'montpellier', 'metz', 'paris fc'],
    }
    for eng_name in cn_to_en.values():
        found = False
        for league, keywords in league_keywords.items():
            for kw in keywords:
                if kw in eng_name.lower():
                    leagues[league] += 1
                    found = True
                    break
            if found:
                break
        if not found:
            leagues['其他'] += 1

    for league, count in leagues.items():
        print(f"   {league}: {count} 条")


if __name__ == '__main__':
    main()