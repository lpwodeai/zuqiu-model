import sqlite3
import os

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(BASE_DIR, 'data', 'five_leagues.db')

def delete_player_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    cursor.execute('SELECT COUNT(*) FROM players')
    player_count = cursor.fetchone()[0]
    
    cursor.execute('SELECT COUNT(*) FROM player_stats')
    stats_count = cursor.fetchone()[0]
    
    print(f"=== 删除球员数据 ===")
    print(f"当前players表记录数: {player_count}")
    print(f"当前player_stats表记录数: {stats_count}")
    
    if player_count == 0 and stats_count == 0:
        print("数据已为空，无需删除")
        conn.close()
        return
    
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == '--force':
        confirm = 'y'
    else:
        confirm = input("确定要删除所有球员数据吗？(y/n): ").strip().lower()
    
    if confirm != 'y':
        print("取消删除操作")
        conn.close()
        return
    
    print("\n开始删除数据...")
    
    cursor.execute('DELETE FROM player_stats')
    stats_deleted = cursor.rowcount
    
    cursor.execute('DELETE FROM players')
    players_deleted = cursor.rowcount
    
    conn.commit()
    conn.close()
    
    print(f"\n=== 删除完成 ===")
    print(f"已删除players记录: {players_deleted}")
    print(f"已删除player_stats记录: {stats_deleted}")

if __name__ == "__main__":
    delete_player_data()