import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'data', 'five_leagues.db')

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("SELECT id, name, shortName, league FROM teams ORDER BY league, name")
teams = cursor.fetchall()

print(f"Total teams: {len(teams)}")
print("\n--- Teams by league ---")

current_league = ""
for team in teams:
    if team[3] != current_league:
        current_league = team[3]
        print(f"\n{current_league}:")
    print(f"  {team[1]} ({team[2]})")

conn.close()