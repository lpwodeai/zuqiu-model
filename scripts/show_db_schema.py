import sqlite3

def show_db_schema():
    """显示odds.db的完整表结构"""
    conn = sqlite3.connect('data/odds.db')
    cursor = conn.cursor()
    
    tables = ['matches', 'wdl_history', 'handicap_history', 'total_goals_history', 'score_history']
    
    for table in tables:
        print(f'\n{"="*60}')
        print(f'【{table}表结构】')
        print(f'{"="*60}')
        
        cursor.execute(f'PRAGMA table_info({table})')
        columns = cursor.fetchall()
        
        print(f'\n字段列表:')
        for col in columns:
            print(f'  [{col[0]:2d}] {col[1]:20} {col[2]:10} NOT NULL={col[3]} DEFAULT={col[4]}')
        
        # 查看索引
        print(f'\n索引:')
        cursor.execute(f'PRAGMA index_list({table})')
        indexes = cursor.fetchall()
        if indexes:
            for idx in indexes:
                print(f'  {idx[1]}')
        else:
            print('  无索引')
        
        # 查看样例数据
        print(f'\n样例数据（前2行）:')
        cursor.execute(f'SELECT * FROM {table} LIMIT 2')
        rows = cursor.fetchall()
        if rows:
            col_names = [col[1] for col in columns]
            for row_num, row in enumerate(rows):
                print(f'\n  第{row_num+1}行:')
                for name, value in zip(col_names, row):
                    print(f'    {name}: {value}')
    
    conn.close()

if __name__ == '__main__':
    show_db_schema()
