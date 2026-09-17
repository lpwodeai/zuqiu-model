import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
conn = sqlite3.connect(BASE_DIR / "data" / "odds.db")

print("=" * 70)
print("fbref_match_mapping 全量验证")
print("=" * 70)

# 按 season 和 league 统计
rows = conn.execute("""
    SELECT season, league, COUNT(*) as cnt
    FROM fbref_match_mapping
    GROUP BY season, league
    ORDER BY season, league
""").fetchall()

seasons = {}
for r in rows:
    s = r[0]
    if s not in seasons:
        seasons[s] = {}
    seasons[s][r[1]] = r[2]

total = 0
for s in sorted(seasons.keys()):
    league_total = sum(seasons[s].values())
    total += league_total
    print(f"\n  {s}:")
    for l, c in sorted(seasons[s].items()):
        expected = 380 if l in ["英超", "西甲", "意甲"] else 306
        ok = "✅" if abs(c - expected) <= 5 else "⚠️"
        print(f"    {l}: {c:>4d} 场 (预期 {expected}) {ok}")
    print(f"    小计: {league_total} 场")

print(f"\n  全库总计: {total} 场")
print(f"  3赛季 × 理论值 1,752 = 5,256 场")
print(f"  差异: {total - 5256} 场 ({'✅ 正常' if abs(total-5256) < 50 else '⚠️ 需排查'})")

conn.close()