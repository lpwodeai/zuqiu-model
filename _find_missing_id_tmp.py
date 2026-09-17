# -*- coding: utf-8 -*-
import sqlite3
con = sqlite3.connect('data/odds.db')
cur = con.cursor()

tm = {r[0]: r[1] for r in cur.execute("select original_name, standard_name from team_mapping")}
OV = {
    'Coventry': '考文垂', 'Deportivo La Coruna': '拉科鲁尼亚', 'Hull': '赫尔城',
    'Ipswich': '伊普斯维奇', 'Le Mans': '勒芒', 'Paris Saint Germain': '巴黎圣日耳曼',
    'Parma Calcio 1913': '帕尔马', '1. FC Köln': '科隆', '1. FSV Mainz 05': '美因茨',
    'Bayer 04 Leverkusen': '勒沃库森', "Borussia M'gladbach": '门兴',
    'Coventry City': '考文垂', 'Deportivo de A Coruña': '拉科鲁尼亚',
    'FC Augsburg': '奥格斯堡', 'FC Bayern München': '拜仁慕尼黑', 'FC Schalke 04': '沙尔克04',
    'Hull City': '赫尔城', 'Ipswich Town': '伊普斯维奇', 'Levante UD': '莱万特',
    'Liverpool FC': '利物浦', 'Málaga CF': '马拉加', 'Real Racing Club': '桑坦德竞技',
    'SC Paderborn 07': '帕德博恩', 'SV 07 Elversberg': '埃尔沃斯堡',
    'SV Werder Bremen': '云达不莱梅',
}
def cn(n):
    return OV.get(n) or tm.get(n) or n

# sofascore 每场: event_id + 队名 + 是否有统计
rows = cur.execute("""select m.league, m.fbref_match_id, m.home_team_cn, m.away_team_cn,
   (select count(*) from match_player_stats p where p.fbref_match_id=m.fbref_match_id) nS
   from fbref_match_mapping m where m.season='26/27'""").fetchall()

# understat 完赛
U = cur.execute("""select league,home_team,away_team from understat_match_team_stats
  where season='26/27' and is_result=1 order by league,datetime""").fetchall()

# 找到缺失场次的 event_id
print("缺失的 10 场 -> event_id:")
for lg, h, a in U:
    ch, ca = cn(h), cn(a)
    cand = [r for r in rows if r[0] == lg and (cn(r[2]), cn(r[3])) in ((ch, ca), (ca, ch))]
    if not cand:
        print("  [MISS映射] %s %s vs %s" % (lg, h, a))
        continue
    # 找无统计的那条
    nostat = [r for r in cand if (r[4] or 0) == 0]
    if nostat:
        for r in nostat:
            print("  %-4s | event_id=%s | %s vs %s (stats=%s)" % (lg, r[1], r[2], r[3], r[4]))
    else:
        print("  %-4s | %s vs %s 已有统计(cand=%d)" % (lg, h, a, len(cand)))