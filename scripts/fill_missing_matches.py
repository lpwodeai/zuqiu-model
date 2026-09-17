# -*- coding: utf-8 -*-
"""补全 odds.db 中缺失的 matches 和 match_id_mapping 记录"""
import sqlite3, json, glob, os
from pathlib import Path
from collections import defaultdict

BASE = Path(__file__).resolve().parent.parent
DB = str(BASE / "data" / "odds.db")
LOGS = str(BASE / "logs")

# 联赛 → match_type 映射
LEAGUE_TO_MATCH_TYPE = {
    "英超": "英超2026-2027赛季",
    "西甲": "西甲2026-2027赛季",
    "法甲": "法甲2026-2027赛季",
    "意甲": "意甲2026-2027赛季",
}

def main():
    conn = sqlite3.connect(DB)
    c = conn.cursor()

    # 1. 收集所有 summary JSON 中的 event_id → match 信息
    event_map = {}  # event_id → {league, date, home_team, away_team}
    seen = set()

    for fpath in sorted(glob.glob(os.path.join(LOGS, "sofascore_collector_summary_20260822_*.json"))):
        with open(fpath, "r", encoding="utf-8") as f:
            data = json.load(f)
        if data.get("season") != "26/27":
            continue

        for league, events in data.get("league_results", {}).items():
            for evt in events:
                eid = str(evt["event_id"])
                if eid in seen:
                    continue
                seen.add(eid)

                match_name = evt["match"]  # e.g. "Inter vs Monza"
                parts = match_name.split(" vs ", 1)
                if len(parts) != 2:
                    print(f"  [WARN] 无法解析比赛名: {match_name}")
                    continue
                home_team, away_team = parts[0].strip(), parts[1].strip()

                event_map[eid] = {
                    "league": league,
                    "date": evt["date"],
                    "home_team": home_team,
                    "away_team": away_team,
                }

    print(f"从 summary JSON 解析到 {len(event_map)} 个 event")

    # 2. 查询 matches 表中已有的 match_id
    c.execute("SELECT match_id FROM matches")
    existing_matches = set(r[0] for r in c.fetchall())

    # 3. 查询 match_id_mapping 中已有的映射
    c.execute("SELECT sh_match_id, matches_match_id FROM match_id_mapping")
    existing_mapping = {r[0]: r[1] for r in c.fetchall()}

    new_matches = 0
    new_mappings = 0

    for eid, info in sorted(event_map.items()):
        match_id = f"{info['date']}_{info['home_team']}_{info['away_team']}"
        match_type = LEAGUE_TO_MATCH_TYPE.get(info["league"], f"{info['league']}2026-2027赛季")

        # 插入 matches 表（如果不存在）
        if match_id not in existing_matches:
            try:
                c.execute("""
                    INSERT INTO matches (match_id, home_team, away_team, match_date, match_type)
                    VALUES (?, ?, ?, ?, ?)
                """, (match_id, info["home_team"], info["away_team"], info["date"], match_type))
                new_matches += 1
                existing_matches.add(match_id)
                print(f"  [matches] + {match_id} | {info['league']}")
            except Exception as e:
                print(f"  [ERROR matches] {match_id}: {e}")

        # 插入 match_id_mapping 表（如果不存在）
        if eid not in existing_mapping:
            try:
                c.execute("""
                    INSERT OR IGNORE INTO match_id_mapping (sh_match_id, matches_match_id, match_method)
                    VALUES (?, ?, 'sofascore')
                """, (eid, match_id))
                new_mappings += 1
                existing_mapping[eid] = match_id
                print(f"  [mapping] + event_id={eid} -> {match_id}")
            except Exception as e:
                print(f"  [ERROR mapping] event_id={eid}: {e}")

    conn.commit()

    print(f"\n=== 完成 ===")
    print(f"新增 matches 记录: {new_matches}")
    print(f"新增 match_id_mapping 记录: {new_mappings}")

    # 验证
    c.execute("SELECT COUNT(*) FROM matches WHERE match_date >= '2026-08-22' AND match_date <= '2026-08-24'")
    total_matches = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM match_id_mapping WHERE sh_match_id IN ({})".format(
        ",".join(f"'{eid}'" for eid in event_map.keys())
    ))
    total_mapping = c.fetchone()[0]
    print(f"验证: matches 表 8/22-8/24 共 {total_matches} 条, mapping 共 {total_mapping} 条")

    conn.close()

if __name__ == "__main__":
    main()