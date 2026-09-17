#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
查询 SofaScore 五大联赛 2026-2027 赛季 ID

C-001 数据管线 — 步骤1: 获取新赛季 season_id
通过 SofaScore API 查询各联赛 uniqueTournament 的 26/27 赛季 ID

用法:
    python scripts/query_season_ids.py

输出:
    - 控制台打印各联赛 26/27 赛季 ID
    - 更新 collection/final_sofascore_collector.py 中的 LEAGUE_CONFIG
"""

import json
import sys
import os
import urllib.request
import urllib.error
import ssl

# SofaScore API 配置
BASE_URL = "https://api.sofascore.com/api/v1"

# 五大联赛 uniqueTournament ID (已验证)
LEAGUE_IDS = {
    '英超': 17,
    '西甲': 8,
    '意甲': 23,
    '德甲': 35,
    '法甲': 34,
}

# 已知的 25/26 赛季 ID (参考)
KNOWN_SEASON_IDS = {
    '英超': 76986,
    '西甲': 77559,
    '意甲': 76457,
    '德甲': 77333,
    '法甲': 77356,
}

# 25/26 赛季名称 (用于对比)
SEASON_25_26 = "2025/2026"


def fetch_json(url, timeout=15):
    """通过 HTTPS 请求获取 JSON 数据 (绕过 SSL 验证)"""
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE

    req = urllib.request.Request(url, headers={
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
        'Accept': 'application/json',
    })

    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode('utf-8'))
    except urllib.error.HTTPError as e:
        print(f"  HTTP {e.code}: {e.reason}")
        return None
    except urllib.error.URLError as e:
        print(f"  网络错误: {e.reason}")
        return None
    except Exception as e:
        print(f"  请求失败: {e}")
        return None


def query_league_seasons(league_name, tournament_id):
    """查询指定联赛的所有赛季"""
    url = f"{BASE_URL}/unique-tournament/{tournament_id}/seasons"
    print(f"\n{'='*60}")
    print(f"查询 {league_name} (uniqueTournament={tournament_id})")
    print(f"URL: {url}")

    data = fetch_json(url)
    if not data:
        return None

    seasons = data.get('seasons', [])
    if not seasons:
        print(f"  ⚠️ 未找到赛季数据")
        return None

    # 查找 26/27 赛季
    target_season = None
    for s in seasons:
        season_name = s.get('name', '')
        season_id = s.get('id')
        year = s.get('year', '')
        print(f"  - id={season_id}, name='{season_name}', year='{year}'")

        if '26/27' in season_name or '2026/2027' in season_name:
            target_season = s
            print(f"    ✅ 找到 26/27 赛季!")

    if not target_season:
        # 尝试从年份推断
        print(f"  ⚠️ 未找到明确的 26/27 赛季，尝试从 year 字段推断...")
        for s in seasons:
            year = s.get('year', '')
            if '26' in year or '2026' in year:
                target_season = s
                print(f"    ✅ 从 year='{year}' 推断为 26/27 赛季 (id={s.get('id')})")
                break

    return target_season


def main():
    print("=" * 60)
    print("SofaScore 2026-2027 赛季 ID 查询工具")
    print("=" * 60)

    results = {}
    for league_name, tournament_id in LEAGUE_IDS.items():
        season = query_league_seasons(league_name, tournament_id)
        if season:
            results[league_name] = {
                'season_id': season.get('id'),
                'season_name': season.get('name'),
                'year': season.get('year'),
            }
        else:
            results[league_name] = {'season_id': None, 'error': '未找到'}

    # 打印汇总
    print(f"\n\n{'='*60}")
    print("查询结果汇总")
    print(f"{'='*60}")
    print(f"{'联赛':<8} {'25/26 ID':<10} {'26/27 ID':<10} {'状态'}")
    print(f"{'-'*50}")
    for league_name, info in results.items():
        old_id = KNOWN_SEASON_IDS.get(league_name, 'N/A')
        new_id = info.get('season_id') or 'N/A'
        status = '✅ 已找到' if info.get('season_id') else '❌ 未找到'
        print(f"{league_name:<8} {str(old_id):<10} {str(new_id):<10} {status}")

    # 生成更新代码
    print(f"\n\n{'='*60}")
    print("如需更新 final_sofascore_collector.py，请在 LEAGUE_CONFIG 中添加:")
    print(f"{'='*60}")
    print("'26/27': {")
    for league_name, info in results.items():
        sid = info.get('season_id')
        if sid:
            print(f"    '{league_name}': {sid},")
        else:
            print(f"    '{league_name}': None,  # ⚠️ 待确认")
    print("}")

    # 保存结果到 JSON
    output_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'data', 'season_ids_26_27.json'
    )
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, 'w', encoding='utf-8') as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print(f"\n📁 结果已保存到: {output_path}")

    return results


if __name__ == '__main__':
    main()