# -*- coding: utf-8 -*-
"""英超第一轮数据核对 - 生成完整报告"""
import sqlite3

DB_PATH = r"F:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds.db"
conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
cur = conn.cursor()

PREMIER_LEAGUE_R1_IDS = [
    '16363633', '16363634', '16363635', '16363636', '16363238',
    '16363242', '16363236', '16363243', '16363246', '16363244'
]

# 用 fbref_match_mapping 获取完整比赛信息
placeholders = ','.join('?' * len(PREMIER_LEAGUE_R1_IDS))
matches = cur.execute(
    f"""SELECT m.*, 
        (SELECT COUNT(*) FROM match_lineups l WHERE l.fbref_match_id = m.fbref_match_id) as total_lineups,
        (SELECT COUNT(*) FROM match_player_stats s WHERE s.fbref_match_id = m.fbref_match_id) as total_stats,
        (SELECT COUNT(*) FROM match_player_stats s WHERE s.fbref_match_id = m.fbref_match_id AND (s.goals > 0 OR s.assists > 0 OR s.shots > 0 OR s.xg > 0)) as actual_stats
        FROM fbref_match_mapping m 
        WHERE m.fbref_match_id IN ({placeholders})
        ORDER BY m.match_date""",
    PREMIER_LEAGUE_R1_IDS
).fetchall()

print("=" * 90)
print("英超第一轮 (2026/27 赛季) 赛前数据抓取核对报告")
print("=" * 90)

if not matches:
    print("⚠️ 未找到比赛数据！")
else:
    for idx, m in enumerate(matches, 1):
        print(f"\n{'='*90}")
        print(f"【比赛 {idx}/10】Event ID: {m['fbref_match_id']}")
        print(f"{'='*90}")
        print(f"📅 比赛日期: {m['match_date']}")
        print(f"🏆 联赛: {m['league']} ({m['season']} 赛季) | 第 {m['fbref_week']} 轮")
        print(f"⚔️ 对阵: {m['home_team_fbref']} vs {m['away_team_fbref']}")
        print(f"🇨🇳 中文: {m['home_team_cn']} vs {m['away_team_cn']}")
        print(f"📋 阵容记录: {m['total_lineups']} 条 | 球员统计: {m['total_stats']} 条 ({m['actual_stats']} 条有实际数据)")
        
        # 获取阵容详情
        lineups = cur.execute(
            "SELECT team, player_name, jersey_number, position, is_starter, captain, formation FROM match_lineups WHERE fbref_match_id = ? ORDER BY team, is_starter DESC, jersey_number",
            (m['fbref_match_id'],)
        ).fetchall()
        
        # 按球队分组
        teams = {}
        for p in lineups:
            team = p['team']
            if team not in teams:
                teams[team] = {'formation': p['formation'], 'starters': [], 'subs': []}
            if p['is_starter'] == 1:
                teams[team]['starters'].append(p)
            else:
                teams[team]['subs'].append(p)
        
        for team_name, team_data in teams.items():
            print(f"\n  ▶ {team_name} | 阵型: {team_data['formation']}")
            print(f"    ┌─ 首发 ({len(team_data['starters'])} 人)")
            for p in team_data['starters']:
                cap = " (队长)" if p['captain'] == 1 else ""
                print(f"    │  ⭐ #{p['jersey_number']:>2} {p['player_name']} [{p['position']}]{cap}")
            print(f"    └─ 替补 ({len(team_data['subs'])} 人)")
            for p in team_data['subs']:
                print(f"      🔄 #{p['jersey_number']:>2} {p['player_name']} [{p['position']}]")
        
        # 球员统计
        stats = cur.execute(
            "SELECT player_name, team, goals, assists, shots, shots_on_target, xg, rating FROM match_player_stats WHERE fbref_match_id = ? AND (goals > 0 OR assists > 0 OR shots > 0 OR xg > 0)",
            (m['fbref_match_id'],)
        ).fetchall()
        
        if stats:
            print(f"\n  📊 有数据的球员:")
            for s in stats:
                parts = []
                if s['goals']: parts.append(f"进球⚽{s['goals']}")
                if s['assists']: parts.append(f"助攻🅰️{s['assists']}")
                if s['shots']: parts.append(f"射门{s['shots_on_target']}/{s['shots']}")
                if s['xg']: parts.append(f"xG={s['xg']:.2f}")
                stat_str = ", ".join(parts)
                print(f"    {s['player_name']} ({s['team']}): {stat_str}")
        else:
            print(f"\n  📊 球员统计: 赛前无数据 (待比赛结束后更新)")

# 总结
print(f"\n{'='*90}")
print("📋 数据核对总结")
print(f"{'='*90}")
print(f"\n✅ 比赛总数: {len(matches)} 场")
print(f"✅ 阵容总记录: {sum(m['total_lineups'] for m in matches)} 条")
print(f"✅ 球员统计总记录: {sum(m['total_stats'] for m in matches)} 条")
print(f"📊 有实际数据的球员: {sum(m['actual_stats'] for m in matches)} 人 (仅 Arsenal 场有历史数据)")

# 检查各队首发人数是否完整
print(f"\n🔍 阵容完整性检查:")
for m in matches:
    lineup_count = m['total_lineups']
    expected_min = 22  # 至少 2 队各 11 人首发
    if lineup_count >= expected_min:
        status = "✅ 完整"
    else:
        status = f"⚠️ 不完整 (仅 {lineup_count} 人)"
    print(f"    {m['fbref_match_id']}: {m['home_team_fbref']} vs {m['away_team_fbref']} - {status} ({lineup_count} 人)")

conn.close()
print(f"\n{'='*90}")
print("核对完成！请对照 SofaScore 网站验证上述信息。")
print(f"{'='*90}")