"""
赔率时序数据收集器 - HTTP服务
用于接收外部系统打卡的赔率时序数据

API端点：
- POST /api/v1/match - 添加比赛基础信息
- POST /api/v1/wdl - 添加胜平负赔率时序
- POST /api/v1/handicap - 添加让球赔率时序
- POST /api/v1/total_goals - 添加总进球赔率时序
- POST /api/v1/score - 添加比分赔率时序
- POST /api/v1/match_result - 添加比赛结果
- GET /api/v1/health - 健康检查
- GET /api/v1/stats - 统计信息

运行方式：python timing_server.py
默认端口：8081
"""

import json
import sqlite3
import time
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

DB_PATH = 'data/odds_timing.db'
HOST = '0.0.0.0'
PORT = 8081

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def validate_odds(odds):
    """验证赔率值是否在有效范围内(1.01-200.0)"""
    if odds is None:
        return True  # 允许NULL
    try:
        val = float(odds)
        return 1.01 <= val <= 200.0
    except:
        return False

def validate_match_id(match_id):
    """验证match_id格式"""
    if not match_id or not isinstance(match_id, str):
        return False
    # 格式要求: YYYY-MM-DD_主队_客队
    parts = match_id.split('_')
    if len(parts) != 3:
        return False
    date_part = parts[0]
    if len(date_part) != 10 or date_part[4] != '-' or date_part[7] != '-':
        return False
    return True

def parse_timestamp(ts):
    """解析时间戳为标准格式"""
    if ts is None:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        # 尝试解析多种格式
        for fmt in ['%Y-%m-%d %H:%M:%S', '%Y/%m/%d %H:%M:%S', '%Y-%m-%dT%H:%M:%S', '%Y-%m-%d']:
            try:
                return datetime.strptime(str(ts), fmt).strftime('%Y-%m-%d %H:%M:%S')
            except ValueError:
                continue
        # 如果都不行，返回当前时间
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    except:
        return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

class TimingDataHandler(BaseHTTPRequestHandler):
    """HTTP请求处理器"""
    
    def send_json_response(self, status_code, data):
        """发送JSON响应"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    
    def do_GET(self):
        """处理GET请求"""
        if self.path == '/api/v1/health':
            self.handle_health()
        elif self.path == '/api/v1/stats':
            self.handle_stats()
        else:
            self.send_json_response(404, {'error': 'Not found', 'message': 'Invalid endpoint'})
    
    def do_POST(self):
        """处理POST请求"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body)
        except:
            self.send_json_response(400, {'error': 'Bad request', 'message': 'Invalid JSON'})
            return
        
        if self.path == '/api/v1/match':
            self.handle_add_match(data)
        elif self.path == '/api/v1/wdl':
            self.handle_add_wdl(data)
        elif self.path == '/api/v1/handicap':
            self.handle_add_handicap(data)
        elif self.path == '/api/v1/total_goals':
            self.handle_add_total_goals(data)
        elif self.path == '/api/v1/score':
            self.handle_add_score(data)
        elif self.path == '/api/v1/match_result':
            self.handle_add_result(data)
        elif self.path == '/api/v1/batch':
            self.handle_batch(data)
        else:
            self.send_json_response(404, {'error': 'Not found', 'message': 'Invalid endpoint'})
    
    def handle_health(self):
        """健康检查"""
        try:
            conn = get_db_connection()
            conn.execute('SELECT 1')
            conn.close()
            self.send_json_response(200, {'status': 'healthy', 'database': 'ok'})
        except Exception as e:
            self.send_json_response(500, {'status': 'unhealthy', 'error': str(e)})
    
    def handle_stats(self):
        """统计信息"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        stats = {}
        
        # 比赛数
        cursor.execute('SELECT COUNT(*) FROM matches')
        stats['total_matches'] = cursor.fetchone()[0]
        
        # 状态分布
        cursor.execute('SELECT status, COUNT(*) FROM matches GROUP BY status')
        stats['match_status'] = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 数据源统计
        cursor.execute('SELECT source, COUNT(*) FROM matches GROUP BY source')
        stats['source_distribution'] = {row[0]: row[1] for row in cursor.fetchall()}
        
        # 赔率记录数
        for table, name in [('wdl_timing', 'wdl_records'), ('handicap_timing', 'hcp_records'),
                           ('total_goals_timing', 'tg_records'), ('score_timing', 'score_records')]:
            cursor.execute(f'SELECT COUNT(*) FROM {table}')
            stats[name] = cursor.fetchone()[0]
        
        # 平均时间点数
        cursor.execute('SELECT AVG(cnt) FROM (SELECT match_id, COUNT(*) as cnt FROM wdl_timing GROUP BY match_id)')
        avg = cursor.fetchone()[0]
        stats['avg_time_points'] = round(avg, 1) if avg else 0
        
        conn.close()
        
        self.send_json_response(200, stats)
    
    def handle_add_match(self, data):
        """添加比赛基础信息"""
        required_fields = ['match_id', 'home_team', 'away_team', 'match_date']
        
        # 验证必填字段
        missing = [f for f in required_fields if f not in data]
        if missing:
            self.send_json_response(400, {'error': 'Missing fields', 'fields': missing})
            return
        
        # 验证match_id格式
        if not validate_match_id(data['match_id']):
            self.send_json_response(400, {'error': 'Invalid match_id format', 
                                         'expected': 'YYYY-MM-DD_主队_客队',
                                         'received': data['match_id']})
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO matches 
                (match_id, home_team, away_team, match_date, match_time, league, league_code, status, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                data['match_id'],
                data['home_team'],
                data['away_team'],
                data['match_date'],
                data.get('match_time'),
                data.get('league'),
                data.get('league_code'),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(201, {'success': True, 'match_id': data['match_id']})
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': 'Database error', 'message': str(e)})
        finally:
            conn.close()
    
    def handle_add_wdl(self, data):
        """添加胜平负赔率时序"""
        required_fields = ['match_id', 'timestamp', 'win_a', 'draw', 'win_b']
        
        missing = [f for f in required_fields if f not in data]
        if missing:
            self.send_json_response(400, {'error': 'Missing fields', 'fields': missing})
            return
        
        # 验证赔率
        if not all(validate_odds(data[f]) for f in ['win_a', 'draw', 'win_b']):
            self.send_json_response(400, {'error': 'Invalid odds value', 'range': '1.01-200.0'})
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO wdl_timing
                (match_id, timestamp, win_a, draw, win_b, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                data['match_id'],
                parse_timestamp(data['timestamp']),
                float(data['win_a']),
                float(data['draw']),
                float(data['win_b']),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(201, {'success': True, 'match_id': data['match_id']})
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': 'Database error', 'message': str(e)})
        finally:
            conn.close()
    
    def handle_add_handicap(self, data):
        """添加让球赔率时序"""
        required_fields = ['match_id', 'timestamp', 'handicap', 'hcp_win', 'hcp_lose']
        
        missing = [f for f in required_fields if f not in data]
        if missing:
            self.send_json_response(400, {'error': 'Missing fields', 'fields': missing})
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO handicap_timing
                (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose, source)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                data['match_id'],
                parse_timestamp(data['timestamp']),
                float(data['handicap']),
                float(data['hcp_win']),
                float(data['hcp_draw']) if data.get('hcp_draw') else None,
                float(data['hcp_lose']),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(201, {'success': True, 'match_id': data['match_id']})
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': 'Database error', 'message': str(e)})
        finally:
            conn.close()
    
    def handle_add_total_goals(self, data):
        """添加总进球赔率时序"""
        required_fields = ['match_id', 'timestamp']
        
        missing = [f for f in required_fields if f not in data]
        if missing:
            self.send_json_response(400, {'error': 'Missing fields', 'fields': missing})
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO total_goals_timing
                (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4,
                 goals_5, goals_6, goals_7_plus, over_25, under_25, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                data['match_id'],
                parse_timestamp(data['timestamp']),
                float(data['goals_0']) if data.get('goals_0') else None,
                float(data['goals_1']) if data.get('goals_1') else None,
                float(data['goals_2']) if data.get('goals_2') else None,
                float(data['goals_3']) if data.get('goals_3') else None,
                float(data['goals_4']) if data.get('goals_4') else None,
                float(data['goals_5']) if data.get('goals_5') else None,
                float(data['goals_6']) if data.get('goals_6') else None,
                float(data['goals_7_plus']) if data.get('goals_7_plus') else None,
                float(data['over_25']) if data.get('over_25') else None,
                float(data['under_25']) if data.get('under_25') else None,
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(201, {'success': True, 'match_id': data['match_id']})
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': 'Database error', 'message': str(e)})
        finally:
            conn.close()
    
    def handle_add_score(self, data):
        """添加比分赔率时序"""
        required_fields = ['match_id', 'timestamp', 'score', 'odds']
        
        missing = [f for f in required_fields if f not in data]
        if missing:
            self.send_json_response(400, {'error': 'Missing fields', 'fields': missing})
            return
        
        if not validate_odds(data['odds']):
            self.send_json_response(400, {'error': 'Invalid odds value', 'range': '1.01-200.0'})
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO score_timing
                (match_id, timestamp, score, odds, source)
                VALUES (?, ?, ?, ?, ?)
            """, (
                data['match_id'],
                parse_timestamp(data['timestamp']),
                data['score'],
                float(data['odds']),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(201, {'success': True, 'match_id': data['match_id']})
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': 'Database error', 'message': str(e)})
        finally:
            conn.close()
    
    def handle_add_result(self, data):
        """添加比赛结果"""
        required_fields = ['match_id']
        
        missing = [f for f in required_fields if f not in data]
        if missing:
            self.send_json_response(400, {'error': 'Missing fields', 'fields': missing})
            return
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO match_results
                (match_id, actual_score, actual_wdl, actual_handicap, actual_total_goals, source)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                data['match_id'],
                data.get('actual_score'),
                data.get('actual_wdl'),
                data.get('actual_handicap'),
                int(data['actual_total_goals']) if data.get('actual_total_goals') else None,
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            
            # 更新比赛状态
            cursor.execute("UPDATE matches SET status = 'finished' WHERE match_id = ?", (data['match_id'],))
            conn.commit()
            
            self.send_json_response(201, {'success': True, 'match_id': data['match_id']})
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': 'Database error', 'message': str(e)})
        finally:
            conn.close()
    
    def handle_batch(self, data):
        """批量添加数据"""
        if 'items' not in data or not isinstance(data['items'], list):
            self.send_json_response(400, {'error': 'Invalid batch request', 'message': 'items must be an array'})
            return
        
        results = []
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            for item in data['items']:
                item_type = item.get('type')
                if item_type == 'match':
                    self._batch_add_match(cursor, item['data'])
                elif item_type == 'wdl':
                    self._batch_add_wdl(cursor, item['data'])
                elif item_type == 'handicap':
                    self._batch_add_handicap(cursor, item['data'])
                elif item_type == 'total_goals':
                    self._batch_add_tg(cursor, item['data'])
                elif item_type == 'score':
                    self._batch_add_score(cursor, item['data'])
                elif item_type == 'match_result':
                    self._batch_add_result(cursor, item['data'])
                results.append({'type': item_type, 'success': True})
            
            conn.commit()
            self.send_json_response(201, {'success': True, 'count': len(results), 'results': results})
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': 'Batch operation failed', 'message': str(e)})
        finally:
            conn.close()
    
    def _batch_add_match(self, cursor, data):
        cursor.execute("""
            INSERT OR REPLACE INTO matches 
            (match_id, home_team, away_team, match_date, match_time, league, league_code, status, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
        """, (
            data['match_id'], data['home_team'], data['away_team'],
            data['match_date'], data.get('match_time'), data.get('league'),
            data.get('league_code'), data.get('source', 'EXTERNAL_SYSTEM')
        ))
    
    def _batch_add_wdl(self, cursor, data):
        cursor.execute("""
            INSERT OR REPLACE INTO wdl_timing
            (match_id, timestamp, win_a, draw, win_b, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data['match_id'], parse_timestamp(data['timestamp']),
            float(data['win_a']), float(data['draw']), float(data['win_b']),
            data.get('source', 'EXTERNAL_SYSTEM')
        ))
    
    def _batch_add_handicap(self, cursor, data):
        cursor.execute("""
            INSERT OR REPLACE INTO handicap_timing
            (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose, source)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (
            data['match_id'], parse_timestamp(data['timestamp']),
            float(data['handicap']), float(data['hcp_win']),
            float(data['hcp_draw']) if data.get('hcp_draw') else None,
            float(data['hcp_lose']), data.get('source', 'EXTERNAL_SYSTEM')
        ))
    
    def _batch_add_tg(self, cursor, data):
        cursor.execute("""
            INSERT OR REPLACE INTO total_goals_timing
            (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4,
             goals_5, goals_6, goals_7_plus, over_25, under_25, source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data['match_id'], parse_timestamp(data['timestamp']),
            float(data['goals_0']) if data.get('goals_0') else None,
            float(data['goals_1']) if data.get('goals_1') else None,
            float(data['goals_2']) if data.get('goals_2') else None,
            float(data['goals_3']) if data.get('goals_3') else None,
            float(data['goals_4']) if data.get('goals_4') else None,
            float(data['goals_5']) if data.get('goals_5') else None,
            float(data['goals_6']) if data.get('goals_6') else None,
            float(data['goals_7_plus']) if data.get('goals_7_plus') else None,
            float(data['over_25']) if data.get('over_25') else None,
            float(data['under_25']) if data.get('under_25') else None,
            data.get('source', 'EXTERNAL_SYSTEM')
        ))
    
    def _batch_add_score(self, cursor, data):
        cursor.execute("""
            INSERT OR REPLACE INTO score_timing
            (match_id, timestamp, score, odds, source)
            VALUES (?, ?, ?, ?, ?)
        """, (
            data['match_id'], parse_timestamp(data['timestamp']),
            data['score'], float(data['odds']),
            data.get('source', 'EXTERNAL_SYSTEM')
        ))
    
    def _batch_add_result(self, cursor, data):
        cursor.execute("""
            INSERT OR REPLACE INTO match_results
            (match_id, actual_score, actual_wdl, actual_handicap, actual_total_goals, source)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (
            data['match_id'], data.get('actual_score'), data.get('actual_wdl'),
            data.get('actual_handicap'),
            int(data['actual_total_goals']) if data.get('actual_total_goals') else None,
            data.get('source', 'EXTERNAL_SYSTEM')
        ))
        cursor.execute("UPDATE matches SET status = 'finished' WHERE match_id = ?", (data['match_id'],))
    
    def log_message(self, format, *args):
        """自定义日志格式"""
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        print(f'[{timestamp}] {format % args}')

def run_server():
    """启动HTTP服务"""
    server_address = (HOST, PORT)
    httpd = HTTPServer(server_address, TimingDataHandler)
    
    print(f'\n{"="*60}')
    print('赔率时序数据收集器启动!')
    print(f'服务地址: http://{HOST}:{PORT}')
    print(f'数据库: {DB_PATH}')
    print(f'{"="*60}')
    print('可用端点:')
    print('  POST  /api/v1/match        - 添加比赛基础信息')
    print('  POST  /api/v1/wdl          - 添加胜平负赔率时序')
    print('  POST  /api/v1/handicap     - 添加让球赔率时序')
    print('  POST  /api/v1/total_goals  - 添加总进球赔率时序')
    print('  POST  /api/v1/score        - 添加比分赔率时序')
    print('  POST  /api/v1/match_result - 添加比赛结果')
    print('  POST  /api/v1/batch        - 批量添加数据')
    print('  GET   /api/v1/health       - 健康检查')
    print('  GET   /api/v1/stats        - 统计信息')
    print(f'{"="*60}')
    
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    
    httpd.server_close()
    print(f'\n服务器已停止')

if __name__ == '__main__':
    # 确保数据库已初始化
    if not os.path.exists(DB_PATH):
        from init_timing_db import init_database
        init_database()
    
    run_server()
