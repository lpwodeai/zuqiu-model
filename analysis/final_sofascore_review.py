# -*- coding: utf-8 -*-
"""最终核查：球队级统计分组项数 + 换人事件时间/换入换出 + 替补球员名单"""
import json

with open("sofascore_stats_14025013.json", "r", encoding="utf-8") as f:
    stats = json.load(f)
with open("sofascore_incidents_14025013.json", "r", encoding="utf-8") as f:
    incidents = json.load(f)
with open("sofascore_lineups_14025013.json", "r", encoding="utf-8") as f:
    lineups = json.load(f)

print("=" * 70)
print("【1】球队级统计分组 ALL period - 明细（英超比赛样例）")
print("=" * 70)

for period in stats.get("statistics", []):
    pname = period["period"]
    if pname != "ALL":
        continue
    total_items = 0
    all_items = []
    for grp in period.get("groups", []):
        gname = grp["groupName"]
        items = grp["statisticsItems"]
        total_items += len(items)
        print(f"\n  ▶ {gname}（{len(items)}项）：")
        for it in items:
            key = it.get("key")
            name = it.get("name")
            h = it.get("homeValue") if it.get("homeValue") is not None else it.get("home")
            a = it.get("awayValue") if it.get("awayValue") is not None else it.get("away")
            print(f"      - {key:<35} | {name:<28} | 主: {h}  客: {a}")
            all_items.append(key)
    print(f"\n  ✅ ALL period球队级统计合计: {total_items}项")
    print(f"  去重字段: {len(set(all_items))}")

print("\n" + "=" * 70)
print("【2】阵容：首发11人 + 替补名单（含位置/号码）")
print("=" * 70)

for side, sname in [("home", "Liverpool"), ("away", "Bournemouth")]:
    team = lineups[side]
    print(f"\n  ▶ {sname} 阵型: {team.get('formation')}")
    players = team.get("players", [])
    starters = [p for p in players if not p.get("substitute")]
    subs = [p for p in players if p.get("substitute")]
    
    print(f"    首发11人（{len(starters)}）:")
    for p in starters:
        pl = p["player"]
        pos_code = p.get("position") or pl.get("position")
        shirt = p.get("shirtNumber") or p.get("jerseyNumber") or pl.get("jerseyNumber")
        rating = p.get("statistics", {}).get("rating", "N/A")
        mins = p.get("statistics", {}).get("minutesPlayed", "?")
        print(f"      # {shirt:<3} {pos_code:<4} {pl['name']:<22} rating={rating} mins={mins}")
    
    print(f"    替补名单（{len(subs)}）:")
    for p in subs:
        pl = p["player"]
        pos_code = p.get("position") or pl.get("position")
        shirt = p.get("shirtNumber") or p.get("jerseyNumber") or pl.get("jerseyNumber")
        mins = p.get("statistics", {}).get("minutesPlayed", 0)
        came_on = "✓(出场)" if mins and int(mins) > 0 else f" (未上场, mins={mins})"
        print(f"      # {shirt:<3} {pos_code:<4} {pl['name']:<22} {came_on}")

print("\n" + "=" * 70)
print("【3】完整换人记录（换入/换出球员 + 时间 + 原因）")
print("=" * 70)

# incidents里有很多事件，找substitution类型的
sub_events = [e for e in incidents.get("incidents", []) if e.get("type") == "substitution"]
goal_events = [e for e in incidents.get("incidents", []) if e.get("type") in ("goal", "penaltyShootoutMiss", "penalty")]
card_events = [e for e in incidents.get("incidents", []) if e.get("type") == "card"]

print(f"\n  🔄 换人事件（{len(sub_events)}次）:")
for e in sub_events:
    minute = e.get("time")
    add = e.get("addedTime")
    t_str = f"{minute}'" if not add else f"{minute}'+{add}"
    side = "主(LIV)" if e.get("homeAway") == "home" else "客(BOU)"
    player_out = e.get("playerOutName", "?")
    player_in = e.get("playerInName", "?")
    reason = e.get("reason") or e.get("subType") or "-"
    print(f"    [{t_str:<6}] {side:<8} ↓OUT: {player_out:<20} ↑IN: {player_in:<20} 原因/子类型: {reason}")

print(f"\n  ⚽ 进球/点球事件（{len(goal_events)}）:")
for e in goal_events:
    minute = e.get("time")
    add = e.get("addedTime")
    t_str = f"{minute}'" if not add else f"{minute}'+{add}"
    side = "主(LIV)" if e.get("homeAway") == "home" else "客(BOU)"
    who = e.get("playerName", "?")
    sub = e.get("subType") or e.get("type")
    assist = e.get("assist1Name")
    xg = e.get("xg")
    details = [f"进球者:{who}", f"方式:{sub}"]
    if assist: details.append(f"助攻:{assist}")
    if xg: details.append(f"xG={xg}")
    print(f"    [{t_str:<6}] {side:<8} {' | '.join(details)}")

print(f"\n  🟨🟥 红黄牌事件（{len(card_events)}）:")
for e in card_events:
    minute = e.get("time")
    t_str = f"{minute}'"
    side = "主(LIV)" if e.get("homeAway") == "home" else "客(BOU)"
    who = e.get("playerName", "?")
    card_type = e.get("cardType") or e.get("subType")
    print(f"    [{t_str:<6}] {side:<8} {card_type} : {who}")

print("\n" + "=" * 70)
print("【4】每个位置球员的统计字段全集（按位置去重）")
print("=" * 70)

per_pos_fields = {}
for side in ("home", "away"):
    for p in lineups[side]["players"]:
        pos = p.get("position") or p["player"].get("position", "?")
        st = p.get("statistics") or {}
        per_pos_fields.setdefault(pos, set())
        for k in st.keys():
            per_pos_fields[pos].add(k)

all_player_fields_ever = set()
for pos, fields in per_pos_fields.items():
    all_player_fields_ever |= fields
    print(f"\n  位置 {pos}: {len(fields)} 字段")
    for fld in sorted(fields):
        print(f"      - {fld}")

print(f"\n  ✅ 全场单场球员统计字段并集（去重后）: {len(all_player_fields_ever)} 项")
print(f"  字段明细: {sorted(all_player_fields_ever)}")
print("\n  ✅ 球队级统计字段数(ALL period去重) + 球员级字段数: ",
      len(set(all_items)), "+", len(all_player_fields_ever), "=",
      len(set(all_items)) + len(all_player_fields_ever))
