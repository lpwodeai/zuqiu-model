import sqlite3

DB_PATH = r'G:\zuqiu\五大联赛专属模型\五大联赛专属模型\data\odds_timing.db'

# 狼队 vs 西汉姆联丢失的比分赔率快照（第一个2026-01-03 21:53:27）
match_id = '2026-01-03_狼队_西汉姆联'
timestamp = '2026-01-03 21:53:27_2'  # 添加后缀以区分重复时间戳

scores = [
    # 主队胜
    ('1:0', 8.25), ('2:0', 12.00), ('2:1', 7.50), ('3:0', 24.00), ('3:1', 19.00), ('3:2', 25.00),
    ('4:0', 80.00), ('4:1', 50.00), ('4:2', 60.00), ('5:0', 250.0), ('5:1', 150.0), ('5:2', 200.0),
    ('胜其它', 70.00),
    # 平局
    ('0:0', 11.50), ('1:1', 6.00), ('2:2', 11.50), ('3:3', 45.00), ('平其它', 350.0),
    # 客队胜
    ('0:1', 8.75), ('0:2', 13.00), ('1:2', 8.00), ('0:3', 32.00), ('1:3', 22.00), ('2:3', 28.00),
    ('0:4', 100.0), ('1:4', 60.00), ('2:4', 75.00), ('0:5', 400.0), ('1:5', 200.0), ('2:5', 300.0),
    ('负其它', 80.00),
]

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

try:
    count = 0
    for score, odds in scores:
        cursor.execute('''
            INSERT INTO score_timing (match_id, timestamp, score, odds, source,
                                      quality_score, is_valid, created_at)
            VALUES (?, ?, ?, ?, ?, 1.0, 1, datetime('now'))
        ''', (match_id, timestamp, score, odds, 'MANUAL_IMPORT'))
        count += 1
    
    conn.commit()
    print(f"成功插入 {count} 条缺失的比分赔率记录")
    
    # 验证修复结果
    cursor.execute("SELECT COUNT(*) FROM score_timing WHERE match_id=?", (match_id,))
    total = cursor.fetchone()[0]
    print(f"该比赛比分赔率总计: {total} 条 (预期: 155 = 5个时间点 × 31种比分)")
    
except Exception as e:
    conn.rollback()
    print(f"修复失败: {e}")
    raise
finally:
    conn.close()
