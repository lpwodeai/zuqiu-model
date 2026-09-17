import os
import sys
import time
import logging
import argparse
from datetime import datetime, timedelta
import sqlite3
import yaml
import psutil
import requests
from bs4 import BeautifulSoup
import json

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'five_leagues.db')
CONFIG_PATH = os.path.join(PROJECT_ROOT, 'config.yaml')
LAST_FETCH_FILE = os.path.join(PROJECT_ROOT, 'data', 'last_realtime_fetch.txt')
INJURY_DATA_FILE = os.path.join(PROJECT_ROOT, 'data', 'injury_data.json')
ODDS_HISTORY_FILE = os.path.join(PROJECT_ROOT, 'data', 'odds_history.json')

def load_config():
    if os.path.exists(CONFIG_PATH):
        with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f)
    return {}

CONFIG = load_config()

logging.basicConfig(
    level=getattr(logging, CONFIG.get('logging', {}).get('level', 'INFO')),
    format=CONFIG.get('logging', {}).get('format', '%(asctime)s - %(levelname)s - %(message)s'),
    handlers=[
        logging.FileHandler(os.path.join(PROJECT_ROOT, 'logs/realtime_data.log'), encoding='utf-8'),
        logging.StreamHandler()
    ]
)

logger = logging.getLogger(__name__)

def check_resource_usage():
    cpu_percent = psutil.cpu_percent(interval=1)
    memory_percent = psutil.virtual_memory().percent
    
    max_cpu = CONFIG.get('realtime_data', {}).get('resource_limits', {}).get('max_cpu', 80)
    max_memory = CONFIG.get('realtime_data', {}).get('resource_limits', {}).get('max_memory', 85)
    
    if cpu_percent > max_cpu:
        logger.warning(f'CPU使用率过高: {cpu_percent}% > {max_cpu}%')
        return False
    if memory_percent > max_memory:
        logger.warning(f'内存使用率过高: {memory_percent}% > {max_memory}%')
        return False
    
    logger.info(f'资源使用正常 - CPU: {cpu_percent}%, 内存: {memory_percent}%')
    return True

def get_last_fetch_time():
    if not os.path.exists(LAST_FETCH_FILE):
        return None
    try:
        with open(LAST_FETCH_FILE, 'r') as f:
            last_fetch_str = f.read().strip()
            return datetime.fromisoformat(last_fetch_str)
    except Exception as e:
        logger.error(f'读取上次抓取时间失败: {e}')
        return None

def update_last_fetch_time():
    try:
        with open(LAST_FETCH_FILE, 'w') as f:
            f.write(datetime.now().isoformat())
        logger.info(f'已更新上次抓取时间')
    except Exception as e:
        logger.error(f'更新抓取时间失败: {e}')

def scrape_injury_data():
    logger.info('开始抓取伤停信息...')
    
    injury_sources = CONFIG.get('realtime_data', {}).get('injury_sources', [])
    if not injury_sources:
        injury_sources = [
            {'name': 'transfermarkt', 'url': 'https://www.transfermarkt.com/'}
        ]
    
    injury_data = {}
    
    for source in injury_sources:
        try:
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            }
            response = requests.get(source['url'], headers=headers, timeout=10)
            if response.status_code == 200:
                logger.info(f'成功访问 {source["name"]}')
                injury_data[source['name']] = {'fetched': True, 'timestamp': datetime.now().isoformat()}
            else:
                logger.warning(f'访问 {source["name"]} 失败: {response.status_code}')
                injury_data[source['name']] = {'fetched': False, 'error': f'HTTP {response.status_code}'}
        except Exception as e:
            logger.error(f'抓取 {source["name"]} 伤停信息失败: {e}')
            injury_data[source['name']] = {'fetched': False, 'error': str(e)}
    
    return injury_data

def fetch_odds_data():
    logger.info('开始获取赔率数据...')
    
    odds_api_config = CONFIG.get('realtime_data', {}).get('odds_api', {})
    api_key = odds_api_config.get('api_key', '')
    regions = odds_api_config.get('regions', ['uk', 'eu'])
    markets = odds_api_config.get('markets', ['h2h'])
    
    odds_history = {}
    
    if api_key:
        try:
            url = 'https://api.the-odds-api.com/v4/sports/soccer_epl/odds'
            params = {
                'apiKey': api_key,
                'regions': ','.join(regions),
                'markets': ','.join(markets),
                'oddsFormat': 'decimal'
            }
            response = requests.get(url, params=params, timeout=15)
            if response.status_code == 200:
                data = response.json()
                for match in data[:10]:
                    key = f"{match['home_team']}_{match['away_team']}"
                    odds_history[key] = {
                        'home_team': match['home_team'],
                        'away_team': match['away_team'],
                        'commence_time': match['commence_time'],
                        'bookmakers': []
                    }
                    for bookmaker in match.get('bookmakers', []):
                        for market in bookmaker.get('markets', []):
                            outcomes = market.get('outcomes', [])
                            if len(outcomes) >= 3:
                                odds_history[key]['bookmakers'].append({
                                    'name': bookmaker['title'],
                                    'winA': outcomes[0]['price'],
                                    'draw': outcomes[1]['price'],
                                    'winB': outcomes[2]['price']
                                })
                logger.info(f'成功获取 {len(odds_history)} 场比赛赔率数据')
            else:
                logger.warning(f'赔率API访问失败: {response.status_code}')
        except Exception as e:
            logger.error(f'获取赔率数据失败: {e}')
    
    return odds_history

def save_injury_data(injury_data):
    try:
        existing_data = {}
        if os.path.exists(INJURY_DATA_FILE):
            with open(INJURY_DATA_FILE, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        
        existing_data.update({
            'timestamp': datetime.now().isoformat(),
            'sources': injury_data
        })
        
        with open(INJURY_DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f'伤停数据已保存到 {INJURY_DATA_FILE}')
    except Exception as e:
        logger.error(f'保存伤停数据失败: {e}')

def save_odds_history(odds_history):
    try:
        existing_data = {}
        if os.path.exists(ODDS_HISTORY_FILE):
            with open(ODDS_HISTORY_FILE, 'r', encoding='utf-8') as f:
                existing_data = json.load(f)
        
        current_snapshot = {
            'timestamp': datetime.now().isoformat(),
            'matches': odds_history
        }
        
        if 'history' not in existing_data:
            existing_data['history'] = []
        
        existing_data['history'].append(current_snapshot)
        
        if len(existing_data['history']) > 100:
            existing_data['history'] = existing_data['history'][-100:]
        
        with open(ODDS_HISTORY_FILE, 'w', encoding='utf-8') as f:
            json.dump(existing_data, f, ensure_ascii=False, indent=2)
        
        logger.info(f'赔率历史数据已保存到 {ODDS_HISTORY_FILE}')
    except Exception as e:
        logger.error(f'保存赔率历史数据失败: {e}')

def should_fetch():
    fetch_config = CONFIG.get('realtime_data', {}).get('fetch', {})
    interval_minutes = fetch_config.get('interval_minutes', 60)
    
    last_fetch_time = get_last_fetch_time()
    
    if last_fetch_time is None:
        logger.info('首次抓取，开始执行')
        return True
    
    time_diff = datetime.now() - last_fetch_time
    return time_diff.total_seconds() >= interval_minutes * 60

def run_data_collection(force=False):
    logger.info('========== 开始实时数据采集 ==========')
    
    if not check_resource_usage() and not force:
        logger.warning('资源使用过高，跳过数据采集')
        return False
    
    injury_data = scrape_injury_data()
    odds_history = fetch_odds_data()
    
    save_injury_data(injury_data)
    save_odds_history(odds_history)
    
    update_last_fetch_time()
    
    logger.info('实时数据采集完成')
    return True

def main():
    parser = argparse.ArgumentParser(description='实时数据采集脚本')
    parser.add_argument('--force', action='store_true', help='强制执行数据采集，忽略资源检查')
    parser.add_argument('--continuous', action='store_true', help='持续运行，按间隔自动采集')
    args = parser.parse_args()
    
    logger.info('实时数据采集服务启动')
    
    if args.continuous:
        while True:
            if should_fetch() or args.force:
                run_data_collection(force=args.force)
            else:
                logger.info('距离上次采集不足周期，等待中...')
            
            fetch_interval = CONFIG.get('realtime_data', {}).get('fetch', {}).get('interval_minutes', 60)
            time.sleep(fetch_interval * 60)
    else:
        success = run_data_collection(force=args.force)
        if success:
            logger.info('数据采集成功完成')
        else:
            logger.error('数据采集失败')
    
    logger.info('实时数据采集服务结束')

if __name__ == '__main__':
    main()
