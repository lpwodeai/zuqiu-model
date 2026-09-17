"""测试数据收集器API"""
import requests
import json

BASE_URL = 'http://localhost:8888/api'

def test_status():
    """测试状态接口"""
    response = requests.get(f'{BASE_URL}/status')
    print('Status:', response.status_code)
    print('Response:', response.json())
    return response.json()

def test_match():
    """测试比赛数据提交"""
    data = {
        'home_team': '利物浦',
        'away_team': '阿森纳',
        'match_date': '2025-08-23',
        'league': '英超',
        'source': 'EXTERNAL_SYSTEM'
    }
    response = requests.post(f'{BASE_URL}/match', json=data)
    print('\nMatch:', response.status_code)
    print('Response:', response.json())
    return response.json()

def test_wdl():
    """测试胜平负赔率提交"""
    data = {
        'match_id': '2025-08-23_利物浦_阿森纳',
        'timestamp': '2025-08-23 18:00:00',
        'win_a': 1.85,
        'draw': 3.40,
        'win_b': 4.20,
        'source': 'EXTERNAL_SYSTEM'
    }
    response = requests.post(f'{BASE_URL}/wdl', json=data)
    print('\nWDL:', response.status_code)
    print('Response:', response.json())
    
    # 添加更多时间点
    timestamps = [
        '2025-08-22 18:00:00',
        '2025-08-23 10:00:00',
        '2025-08-23 14:00:00',
        '2025-08-23 19:00:00',  # 比赛前1小时
    ]
    odds = [
        (1.80, 3.50, 4.30),
        (1.82, 3.45, 4.25),
        (1.84, 3.42, 4.22),
        (1.88, 3.38, 4.15),
    ]
    
    for ts, (wa, dr, wb) in zip(timestamps, odds):
        data = {
            'match_id': '2025-08-23_利物浦_阿森纳',
            'timestamp': ts,
            'win_a': wa,
            'draw': dr,
            'win_b': wb,
            'source': 'EXTERNAL_SYSTEM'
        }
        response = requests.post(f'{BASE_URL}/wdl', json=data)
        print(f'  Timestamp {ts}: {response.status_code}')

def test_handicap():
    """测试让球赔率提交"""
    data = {
        'match_id': '2025-08-23_利物浦_阿森纳',
        'timestamp': '2025-08-23 18:00:00',
        'handicap': -0.5,
        'hcp_win': 2.05,
        'hcp_draw': None,
        'hcp_lose': 1.85,
        'source': 'EXTERNAL_SYSTEM'
    }
    response = requests.post(f'{BASE_URL}/handicap', json=data)
    print('\nHandicap:', response.status_code)
    print('Response:', response.json())

def test_total_goals():
    """测试总进球赔率提交"""
    data = {
        'match_id': '2025-08-23_利物浦_阿森纳',
        'timestamp': '2025-08-23 18:00:00',
        'goals_0': 12.0,
        'goals_1': 6.5,
        'goals_2': 4.2,
        'goals_3': 3.5,
        'goals_4': 4.0,
        'goals_5': 6.5,
        'goals_6': 10.0,
        'goals_7_plus': 15.0,
        'over_25': 1.90,
        'under_25': 1.95,
        'source': 'EXTERNAL_SYSTEM'
    }
    response = requests.post(f'{BASE_URL}/total_goals', json=data)
    print('\nTotal Goals:', response.status_code)
    print('Response:', response.json())

def test_score():
    """测试比分赔率提交"""
    scores = [
        ('1:0', 5.5),
        ('2:0', 8.0),
        ('2:1', 10.0),
        ('1:1', 6.0),
        ('0:1', 7.0),
        ('0:2', 12.0),
    ]
    for score, odds in scores:
        data = {
            'match_id': '2025-08-23_利物浦_阿森纳',
            'timestamp': '2025-08-23 18:00:00',
            'score': score,
            'odds': odds,
            'source': 'EXTERNAL_SYSTEM'
        }
        response = requests.post(f'{BASE_URL}/score', json=data)
        print(f'\nScore {score}: {response.status_code}')

def test_batch():
    """测试批量提交"""
    data = {
        'matches': [
            {
                'home_team': '曼联',
                'away_team': '曼城',
                'match_date': '2025-08-24',
                'league': '英超',
                'source': 'EXTERNAL_SYSTEM'
            }
        ],
        'wdl_records': [
            {
                'match_id': '2025-08-24_曼联_曼城',
                'timestamp': '2025-08-24 18:00:00',
                'win_a': 3.20,
                'draw': 3.40,
                'win_b': 2.25,
                'source': 'EXTERNAL_SYSTEM'
            },
            {
                'match_id': '2025-08-24_曼联_曼城',
                'timestamp': '2025-08-24 19:00:00',
                'win_a': 3.30,
                'draw': 3.45,
                'win_b': 2.20,
                'source': 'EXTERNAL_SYSTEM'
            }
        ]
    }
    response = requests.post(f'{BASE_URL}/batch', json=data)
    print('\nBatch:', response.status_code)
    print('Response:', response.json())

def test_statistics():
    """测试统计接口"""
    response = requests.get(f'{BASE_URL}/statistics')
    print('\nStatistics:', response.status_code)
    print('Response:', response.json())

if __name__ == '__main__':
    print('='*60)
    print('数据收集器API测试')
    print('='*60)
    
    test_status()
    test_match()
    test_wdl()
    test_handicap()
    test_total_goals()
    test_score()
    test_batch()
    test_statistics()
    
    print('\n' + '='*60)
    print('测试完成!')
    print('='*60)
