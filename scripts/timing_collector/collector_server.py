"""
赔率时序数据收集器 - HTTP API服务

功能：
1. 接收外部系统打卡的赔率时序数据
2. 数据验证和标准化处理
3. 增量数据存储
4. 提供数据查询接口

API接口：
- POST /api/match - 提交比赛基础信息
- POST /api/wdl - 提交胜平负赔率时序数据
- POST /api/handicap - 提交让球赔率时序数据
- POST /api/total_goals - 提交总进球赔率时序数据
- POST /api/score - 提交比分赔率时序数据
- POST /api/batch - 批量提交所有数据
- GET /api/status - 查询服务状态
- GET /api/statistics - 查询数据统计
"""

import json
import sqlite3
import os
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler

# 配置
DB_PATH = os.path.join(os.path.dirname(__file__), '../../data/odds_timing.db')
HOST = '0.0.0.0'
PORT = 8888

# 数据验证规则
VALID_LEAGUES = ['英超', '西甲', '意甲', '德甲', '法甲', 'EPL', 'La Liga', 'Serie A', 'Bundesliga', 'Ligue 1']
VALID_WDL_RESULTS = ['胜', '平', '负', 'H', 'D', 'A', 'home', 'draw', 'away']
MIN_ODDS = 1.01
MAX_ODDS = 200.0

def get_db_connection():
    """获取数据库连接"""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def generate_match_id(home_team, away_team, match_date):
    """生成标准match_id格式: YYYY-MM-DD_主队_客队"""
    try:
        # 解析日期
        if '/' in match_date:
            parts = match_date.split('/')
            if len(parts) == 3:
                # 尝试dd/mm/yyyy和mm/dd/yyyy两种格式
                if int(parts[0]) > 12:
                    day, month, year = parts
                else:
                    month, day, year = parts
                if len(year) == 2:
                    year = '20' + year
                date_str = f"{year}-{int(month):02d}-{int(day):02d}"
        elif '-' in match_date:
            date_str = match_date
        else:
            date_str = match_date
        
        # 标准化球队名称（去除特殊字符）
        home = ''.join(c for c in home_team if c.isalnum() or c in [' ', '_', '-']).strip()
        away = ''.join(c for c in away_team if c.isalnum() or c in [' ', '_', '-']).strip()
        
        return f"{date_str}_{home}_{away}"
    except:
        return f"UNKNOWN_{home_team}_{away_team}_{match_date}"

def validate_odds(value):
    """验证赔率值"""
    try:
        val = float(value)
        return MIN_ODDS <= val <= MAX_ODDS
    except:
        return False

def validate_match_data(data):
    """验证比赛数据"""
    required = ['home_team', 'away_team', 'match_date']
    for field in required:
        if field not in data or not data[field]:
            return False, f"缺少必填字段: {field}"
    
    if 'league' in data and data['league'] and data['league'] not in VALID_LEAGUES:
        return False, f"无效的联赛: {data['league']}"
    
    return True, ""

def validate_wdl_data(data):
    """验证胜平负赔率数据"""
    required = ['match_id', 'timestamp', 'win_a', 'draw', 'win_b']
    for field in required:
        if field not in data or data[field] is None:
            return False, f"缺少必填字段: {field}"
    
    if not validate_odds(data['win_a']):
        return False, f"主胜赔率无效: {data['win_a']}"
    if not validate_odds(data['draw']):
        return False, f"平局赔率无效: {data['draw']}"
    if not validate_odds(data['win_b']):
        return False, f"客胜赔率无效: {data['win_b']}"
    
    return True, ""

def validate_handicap_data(data):
    """验证让球赔率数据"""
    required = ['match_id', 'timestamp', 'handicap', 'hcp_win', 'hcp_lose']
    for field in required:
        if field not in data or data[field] is None:
            return False, f"缺少必填字段: {field}"
    
    if not validate_odds(data['hcp_win']):
        return False, f"让球胜赔率无效: {data['hcp_win']}"
    if not validate_odds(data['hcp_lose']):
        return False, f"让球负赔率无效: {data['hcp_lose']}"
    
    return True, ""

def validate_total_goals_data(data):
    """验证总进球赔率数据"""
    required = ['match_id', 'timestamp']
    for field in required:
        if field not in data or not data[field]:
            return False, f"缺少必填字段: {field}"
    
    # 至少有一个有效赔率
    goal_fields = ['goals_0', 'goals_1', 'goals_2', 'goals_3', 'goals_4', 
                   'goals_5', 'goals_6', 'goals_7_plus', 'over_25', 'under_25']
    has_valid = False
    for field in goal_fields:
        if field in data and data[field] is not None:
            if validate_odds(data[field]):
                has_valid = True
            else:
                return False, f"{field}赔率无效: {data[field]}"
    
    if not has_valid:
        return False, "至少需要一个有效的总进球赔率"
    
    return True, ""

def validate_score_data(data):
    """验证比分赔率数据"""
    required = ['match_id', 'timestamp', 'score', 'odds']
    for field in required:
        if field not in data or data[field] is None:
            return False, f"缺少必填字段: {field}"
    
    # 验证比分格式
    score = data['score']
    if ':' not in score:
        return False, f"比分格式无效: {score}，应为X:Y格式"
    
    try:
        home_goals, away_goals = score.split(':')
        int(home_goals)
        int(away_goals)
    except:
        return False, f"比分格式无效: {score}"
    
    if not validate_odds(data['odds']):
        return False, f"比分赔率无效: {data['odds']}"
    
    return True, ""

class DataCollectorHandler(BaseHTTPRequestHandler):
    """数据收集器HTTP处理器"""
    
    def send_json_response(self, status_code, data):
        """发送JSON响应"""
        self.send_response(status_code)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False, indent=2).encode('utf-8'))
    
    def do_OPTIONS(self):
        """处理CORS预检请求"""
        self.send_response(200)
        self.send_header('Access-Control-Allow-Origin', '*')
        self.send_header('Access-Control-Allow-Methods', 'GET, POST, OPTIONS')
        self.send_header('Access-Control-Allow-Headers', 'Content-Type')
        self.end_headers()
    
    def do_GET(self):
        """处理GET请求"""
        try:
            if self.path == '/api/status':
                self.handle_status()
            elif self.path == '/api/statistics':
                self.handle_statistics()
            else:
                self.send_json_response(404, {'error': '未找到接口', 'available': ['/api/status', '/api/statistics']})
        except Exception as e:
            self.send_json_response(500, {'error': str(e)})
    
    def do_POST(self):
        """处理POST请求"""
        try:
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode('utf-8')
            data = json.loads(body) if body else {}
            
            if self.path == '/api/match':
                self.handle_match(data)
            elif self.path == '/api/wdl':
                self.handle_wdl(data)
            elif self.path == '/api/handicap':
                self.handle_handicap(data)
            elif self.path == '/api/total_goals':
                self.handle_total_goals(data)
            elif self.path == '/api/score':
                self.handle_score(data)
            elif self.path == '/api/batch':
                self.handle_batch(data)
            else:
                self.send_json_response(404, {'error': '未找到接口', 'available': [
                    '/api/match', '/api/wdl', '/api/handicap', '/api/total_goals', '/api/score', '/api/batch'
                ]})
        except json.JSONDecodeError:
            self.send_json_response(400, {'error': 'JSON格式错误'})
        except Exception as e:
            self.send_json_response(500, {'error': str(e)})
    
    def handle_status(self):
        """处理状态查询"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM matches")
        match_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM wdl_timing")
        wdl_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM handicap_timing")
        hcp_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM total_goals_timing")
        tg_count = cursor.fetchone()[0]
        
        cursor.execute("SELECT COUNT(*) FROM score_timing")
        score_count = cursor.fetchone()[0]
        
        conn.close()
        
        self.send_json_response(200, {
            'status': 'running',
            'database': DB_PATH,
            'match_count': match_count,
            'wdl_records': wdl_count,
            'handicap_records': hcp_count,
            'total_goals_records': tg_count,
            'score_records': score_count,
            'timestamp': datetime.now().isoformat()
        })
    
    def handle_statistics(self):
        """处理数据统计"""
        conn = get_db_connection()
        cursor = conn.cursor()
        
        # 按联赛统计
        cursor.execute("""
            SELECT league, COUNT(*) as count 
            FROM matches 
            WHERE league IS NOT NULL 
            GROUP BY league 
            ORDER BY count DESC
        """)
        league_stats = [dict(row) for row in cursor.fetchall()]
        
        # 按来源统计
        cursor.execute("""
            SELECT source, COUNT(*) as count 
            FROM matches 
            GROUP BY source 
            ORDER BY count DESC
        """)
        source_stats = [dict(row) for row in cursor.fetchall()]
        
        # 时间点分布
        cursor.execute("""
            SELECT cnt, COUNT(*) as match_count
            FROM (
                SELECT match_id, COUNT(*) as cnt 
                FROM wdl_timing 
                GROUP BY match_id
            )
            GROUP BY cnt
            ORDER BY cnt
            LIMIT 10
        """)
        time_point_stats = [{'time_points': row[0], 'match_count': row[1]} for row in cursor.fetchall()]
        
        conn.close()
        
        self.send_json_response(200, {
            'league_statistics': league_stats,
            'source_statistics': source_stats,
            'time_point_distribution': time_point_stats,
            'timestamp': datetime.now().isoformat()
        })
    
    def handle_match(self, data):
        """处理比赛数据提交"""
        valid, error = validate_match_data(data)
        if not valid:
            self.send_json_response(400, {'error': error})
            return
        
        # 生成match_id
        match_id = generate_match_id(data['home_team'], data['away_team'], data['match_date'])
        
        conn = get_db_connection()
        cursor = conn.cursor()
        
        try:
            cursor.execute("""
                INSERT OR REPLACE INTO matches 
                (match_id, home_team, away_team, match_date, match_time, league, league_code, status, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
            """, (
                match_id,
                data['home_team'],
                data['away_team'],
                data['match_date'],
                data.get('match_time', ''),
                data.get('league', ''),
                data.get('league_code', ''),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(200, {
                'success': True,
                'match_id': match_id,
                'message': '比赛数据保存成功'
            })
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': str(e)})
        finally:
            conn.close()
    
    def handle_wdl(self, data):
        """处理胜平负赔率数据提交"""
        valid, error = validate_wdl_data(data)
        if not valid:
            self.send_json_response(400, {'error': error})
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
                data['timestamp'],
                float(data['win_a']),
                float(data['draw']),
                float(data['win_b']),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(200, {
                'success': True,
                'match_id': data['match_id'],
                'timestamp': data['timestamp'],
                'message': '胜平负赔率数据保存成功'
            })
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': str(e)})
        finally:
            conn.close()
    
    def handle_handicap(self, data):
        """处理让球赔率数据提交"""
        valid, error = validate_handicap_data(data)
        if not valid:
            self.send_json_response(400, {'error': error})
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
                data['timestamp'],
                float(data['handicap']),
                float(data['hcp_win']),
                float(data['hcp_draw']) if data['hcp_draw'] else None,
                float(data['hcp_lose']),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(200, {
                'success': True,
                'match_id': data['match_id'],
                'timestamp': data['timestamp'],
                'message': '让球赔率数据保存成功'
            })
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': str(e)})
        finally:
            conn.close()
    
    def handle_total_goals(self, data):
        """处理总进球赔率数据提交"""
        valid, error = validate_total_goals_data(data)
        if not valid:
            self.send_json_response(400, {'error': error})
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
                data['timestamp'],
                float(data['goals_0']) if 'goals_0' in data and data['goals_0'] else None,
                float(data['goals_1']) if 'goals_1' in data and data['goals_1'] else None,
                float(data['goals_2']) if 'goals_2' in data and data['goals_2'] else None,
                float(data['goals_3']) if 'goals_3' in data and data['goals_3'] else None,
                float(data['goals_4']) if 'goals_4' in data and data['goals_4'] else None,
                float(data['goals_5']) if 'goals_5' in data and data['goals_5'] else None,
                float(data['goals_6']) if 'goals_6' in data and data['goals_6'] else None,
                float(data['goals_7_plus']) if 'goals_7_plus' in data and data['goals_7_plus'] else None,
                float(data['over_25']) if 'over_25' in data and data['over_25'] else None,
                float(data['under_25']) if 'under_25' in data and data['under_25'] else None,
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(200, {
                'success': True,
                'match_id': data['match_id'],
                'timestamp': data['timestamp'],
                'message': '总进球赔率数据保存成功'
            })
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': str(e)})
        finally:
            conn.close()
    
    def handle_score(self, data):
        """处理比分赔率数据提交"""
        valid, error = validate_score_data(data)
        if not valid:
            self.send_json_response(400, {'error': error})
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
                data['timestamp'],
                data['score'],
                float(data['odds']),
                data.get('source', 'EXTERNAL_SYSTEM')
            ))
            conn.commit()
            self.send_json_response(200, {
                'success': True,
                'match_id': data['match_id'],
                'timestamp': data['timestamp'],
                'score': data['score'],
                'message': '比分赔率数据保存成功'
            })
        except Exception as e:
            conn.rollback()
            self.send_json_response(500, {'error': str(e)})
        finally:
            conn.close()
    
    def handle_batch(self, data):
        """处理批量数据提交"""
        results = []
        
        # 处理比赛数据
        if 'matches' in data:
            for match in data['matches']:
                valid, error = validate_match_data(match)
                if not valid:
                    results.append({'type': 'match', 'success': False, 'error': error})
                    continue
                
                match_id = generate_match_id(match['home_team'], match['away_team'], match['match_date'])
                
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO matches 
                        (match_id, home_team, away_team, match_date, match_time, league, league_code, status, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                    """, (
                        match_id, match['home_team'], match['away_team'], match['match_date'],
                        match.get('match_time', ''), match.get('league', ''), 
                        match.get('league_code', ''), match.get('source', 'EXTERNAL_SYSTEM')
                    ))
                    conn.commit()
                    results.append({'type': 'match', 'success': True, 'match_id': match_id})
                except Exception as e:
                    conn.rollback()
                    results.append({'type': 'match', 'success': False, 'error': str(e)})
                finally:
                    conn.close()
        
        # 处理WDL数据
        if 'wdl_records' in data:
            for wdl in data['wdl_records']:
                valid, error = validate_wdl_data(wdl)
                if not valid:
                    results.append({'type': 'wdl', 'success': False, 'error': error})
                    continue
                
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO wdl_timing 
                        (match_id, timestamp, win_a, draw, win_b, source)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (wdl['match_id'], wdl['timestamp'], float(wdl['win_a']), 
                          float(wdl['draw']), float(wdl['win_b']), wdl.get('source', 'EXTERNAL_SYSTEM')))
                    conn.commit()
                    results.append({'type': 'wdl', 'success': True, 'match_id': wdl['match_id']})
                except Exception as e:
                    conn.rollback()
                    results.append({'type': 'wdl', 'success': False, 'error': str(e)})
                finally:
                    conn.close()
        
        # 处理让球数据
        if 'handicap_records' in data:
            for hcp in data['handicap_records']:
                valid, error = validate_handicap_data(hcp)
                if not valid:
                    results.append({'type': 'handicap', 'success': False, 'error': error})
                    continue
                
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO handicap_timing 
                        (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (hcp['match_id'], hcp['timestamp'], float(hcp['handicap']),
                          float(hcp['hcp_win']), float(hcp['hcp_draw']) if hcp['hcp_draw'] else None,
                          float(hcp['hcp_lose']), hcp.get('source', 'EXTERNAL_SYSTEM')))
                    conn.commit()
                    results.append({'type': 'handicap', 'success': True, 'match_id': hcp['match_id']})
                except Exception as e:
                    conn.rollback()
                    results.append({'type': 'handicap', 'success': False, 'error': str(e)})
                finally:
                    conn.close()
        
        # 处理总进球数据
        if 'total_goals_records' in data:
            for tg in data['total_goals_records']:
                valid, error = validate_total_goals_data(tg)
                if not valid:
                    results.append({'type': 'total_goals', 'success': False, 'error': error})
                    continue
                
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO total_goals_timing 
                        (match_id, timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, 
                         goals_5, goals_6, goals_7_plus, over_25, under_25, source)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """, (tg['match_id'], tg['timestamp'],
                          float(tg['goals_0']) if 'goals_0' in tg and tg['goals_0'] else None,
                          float(tg['goals_1']) if 'goals_1' in tg and tg['goals_1'] else None,
                          float(tg['goals_2']) if 'goals_2' in tg and tg['goals_2'] else None,
                          float(tg['goals_3']) if 'goals_3' in tg and tg['goals_3'] else None,
                          float(tg['goals_4']) if 'goals_4' in tg and tg['goals_4'] else None,
                          float(tg['goals_5']) if 'goals_5' in tg and tg['goals_5'] else None,
                          float(tg['goals_6']) if 'goals_6' in tg and tg['goals_6'] else None,
                          float(tg['goals_7_plus']) if 'goals_7_plus' in tg and tg['goals_7_plus'] else None,
                          float(tg['over_25']) if 'over_25' in tg and tg['over_25'] else None,
                          float(tg['under_25']) if 'under_25' in tg and tg['under_25'] else None,
                          tg.get('source', 'EXTERNAL_SYSTEM')))
                    conn.commit()
                    results.append({'type': 'total_goals', 'success': True, 'match_id': tg['match_id']})
                except Exception as e:
                    conn.rollback()
                    results.append({'type': 'total_goals', 'success': False, 'error': str(e)})
                finally:
                    conn.close()
        
        # 处理比分数据
        if 'score_records' in data:
            for score in data['score_records']:
                valid, error = validate_score_data(score)
                if not valid:
                    results.append({'type': 'score', 'success': False, 'error': error})
                    continue
                
                conn = get_db_connection()
                cursor = conn.cursor()
                try:
                    cursor.execute("""
                        INSERT OR REPLACE INTO score_timing 
                        (match_id, timestamp, score, odds, source)
                        VALUES (?, ?, ?, ?, ?)
                    """, (score['match_id'], score['timestamp'], score['score'],
                          float(score['odds']), score.get('source', 'EXTERNAL_SYSTEM')))
                    conn.commit()
                    results.append({'type': 'score', 'success': True, 'match_id': score['match_id']})
                except Exception as e:
                    conn.rollback()
                    results.append({'type': 'score', 'success': False, 'error': str(e)})
                finally:
                    conn.close()
        
        total = len(results)
        success_count = sum(1 for r in results if r['success'])
        
        self.send_json_response(200, {
            'success': success_count == total,
            'total_records': total,
            'success_count': success_count,
            'fail_count': total - success_count,
            'results': results[:10],  # 只返回前10条详细结果
            'message': f'批量处理完成: {success_count}/{total} 成功'
        })
    
    def log_message(self, format, *args):
        """自定义日志格式"""
        print(f"[{datetime.now().isoformat()}] {format % args}")

def start_server():
    """启动HTTP服务"""
    server = HTTPServer((HOST, PORT), DataCollectorHandler)
    print(f'\n{"="*60}')
    print('赔率时序数据收集器服务已启动')
    print(f'服务地址: http://{HOST}:{PORT}')
    print(f'数据库: {DB_PATH}')
    print(f'{"="*60}')
    print('\n可用接口:')
    print('  GET  /api/status          - 查询服务状态')
    print('  GET  /api/statistics      - 查询数据统计')
    print('  POST /api/match           - 提交比赛基础信息')
    print('  POST /api/wdl             - 提交胜平负赔率时序数据')
    print('  POST /api/handicap        - 提交让球赔率时序数据')
    print('  POST /api/total_goals     - 提交总进球赔率时序数据')
    print('  POST /api/score           - 提交比分赔率时序数据')
    print('  POST /api/batch           - 批量提交所有数据')
    print('\n按 Ctrl+C 停止服务')
    print(f'{"="*60}\n')
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print('\n服务已停止')
        server.server_close()

if __name__ == '__main__':
    start_server()
