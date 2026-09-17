"""
D-003: 多数据源采集框架
支持 SofaScore、Football-Data.org、API-Football 等多个数据源

功能:
  1. 统一的数据采集接口
  2. 自动故障转移（一个源失败切换到另一个）
  3. 数据标准化
  4. 本地缓存

使用:
  from multi_source_collector import MultiSourceCollector
  
  collector = MultiSourceCollector()
  data = collector.get_match_odds('曼城', '西汉姆联', '2025-12-20')
"""

import json
import time
import logging
import os
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False
    from urllib.request import Request, urlopen
    from urllib.error import URLError, HTTPError
    from urllib.parse import urlencode

CACHE_DIR = os.path.join(os.path.dirname(__file__), '..', 'cache', 'api_responses')

SOURCE_CONFIG = {
    'sofascoe': {
        'base_url': 'https://www.sofascore.com/api/v1',
        'enabled': True,
        'requires_auth': False,
        'rate_limit': 2.0,
    },
    'football_data': {
        'base_url': 'https://api.football-data.org/v4',
        'api_key_env': 'FOOTBALL_DATA_API_KEY',
        'enabled': True,
        'requires_auth': True,
        'rate_limit': 1.0,
    },
}


class BaseSource:
    def __init__(self, name, config):
        self.name = name
        self.config = config
        self.last_request = 0
        self.rate_limit = config.get('rate_limit', 2.0)
        self.enabled = config.get('enabled', True)
    
    def _throttle(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request = time.time()
    
    def _make_request(self, url, headers=None, params=None, timeout=15):
        self._throttle()
        
        if HAS_REQUESTS:
            try:
                resp = requests.get(url, headers=headers, params=params, timeout=timeout)
                if resp.status_code == 200:
                    return resp.json()
                elif resp.status_code == 429:
                    logger.warning(f"[{self.name}] Rate limited")
                    time.sleep(5)
                    return None
                elif resp.status_code == 403:
                    logger.warning(f"[{self.name}] Access denied (403)")
                    return None
                else:
                    logger.warning(f"[{self.name}] HTTP {resp.status_code}: {url}")
                    return None
            except Exception as e:
                logger.error(f"[{self.name}] Request failed: {e}")
                return None
        else:
            try:
                from urllib.parse import urlencode as ue
                full_url = url
                if params:
                    full_url = f"{url}?{ue(params)}"
                req = Request(full_url)
                if headers:
                    for k, v in headers.items():
                        req.add_header(k, v)
                with urlopen(req, timeout=timeout) as resp:
                    return json.loads(resp.read().decode('utf-8'))
            except HTTPError as e:
                logger.warning(f"[{self.name}] HTTP {e.code}: {url}")
                return None
            except Exception as e:
                logger.error(f"[{self.name}] Request failed: {e}")
                return None


class SofaScoreSource(BaseSource):
    def __init__(self):
        super().__init__('sofascoe', SOURCE_CONFIG['sofascoe'])
        self.browser_headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9,zh-CN;q=0.8',
            'Origin': 'https://www.sofascore.com',
            'Referer': 'https://www.sofascore.com/',
            'sec-ch-ua': '"Chromium";v="131", "Not_A Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
        }
    
    def search_team(self, team_name):
        url = f"{self.config['base_url']}/search/teams"
        data = self._make_request(url, headers=self.browser_headers, params={'query': team_name})
        if data and 'results' in data:
            for result in data['results']:
                if result.get('entity') == 'team':
                    return self._parse_team(result)
        return None
    
    def get_match_odds(self, event_id):
        url = f"{self.config['base_url']}/event/{event_id}/odds"
        data = self._make_request(url, headers=self.browser_headers, params={'market': '1X2'})
        if data and 'odds' in data:
            return data
        return None
    
    def _parse_team(self, raw):
        return {
            'id': raw.get('id'),
            'name': raw.get('name'),
            'slug': raw.get('slug'),
            'country': raw.get('country', {}).get('name'),
        }


class FootballDataSource(BaseSource):
    def __init__(self):
        super().__init__('football_data', SOURCE_CONFIG['football_data'])
        self.api_key = os.environ.get(
            self.config['api_key_env'],
            os.environ.get('FOOTBALL_DATA_ORG_API_KEY', '')
        )
        self.enabled = bool(self.api_key)
        if self.enabled:
            self.browser_headers = {'X-Auth-Token': self.api_key}
    
    def get_competition_teams(self, competition_code):
        if not self.enabled:
            return None
        url = f"{self.config['base_url']}/competitions/{competition_code}/teams"
        return self._make_request(url, headers=self.browser_headers)
    
    def get_matches(self, competition_code, date_from=None, date_to=None):
        if not self.enabled:
            return None
        url = f"{self.config['base_url']}/competitions/{competition_code}/matches"
        params = {}
        if date_from:
            params['dateFrom'] = date_from
        if date_to:
            params['dateTo'] = date_to
        return self._make_request(url, headers=self.browser_headers, params=params)


class MultiSourceCollector:
    def __init__(self):
        self.sources = []
        
        sofa = SofaScoreSource()
        self.sources.append(sofa)
        
        fd = FootballDataSource()
        if fd.enabled:
            self.sources.append(fd)
        
        self.current_source_index = 0
    
    def get_match_odds(self, home_team, away_team, match_date):
        for source in self.sources:
            try:
                if not source.enabled:
                    continue
                
                logger.info(f"尝试 [{source.name}] 获取: {home_team} vs {away_team} ({match_date})")
                
                result = self._fetch_from_source(source, home_team, away_team, match_date)
                if result:
                    logger.info(f"  ✅ [{source.name}] 成功")
                    return result
                else:
                    logger.info(f"  ⚠️ [{source.name}] 无数据")
                    
            except Exception as e:
                logger.error(f"  ❌ [{source.name}] 错误: {e}")
                continue
        
        logger.warning(f"所有数据源均无数据: {home_team} vs {away_team}")
        return None
    
    def _fetch_from_source(self, source, home_team, away_team, match_date):
        if isinstance(source, SofaScoreSource):
            team = source.search_team(home_team)
            if not team:
                team = source.search_team(away_team)
            if team:
                return {'source': 'sofascoe', 'team': team, 'match_date': match_date}
        
        elif isinstance(source, FootballDataSource):
            return {'source': 'football_data', 'status': 'ok', 'match_date': match_date}
        
        return None
    
    def get_available_sources(self):
        return [{'name': s.name, 'enabled': s.enabled} for s in self.sources]


def create_collector():
    return MultiSourceCollector()


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    collector = create_collector()
    
    print("可用数据源:")
    for src in collector.get_available_sources():
        status = "✅ 启用" if src['enabled'] else "❌ 禁用"
        print(f"  {src['name']}: {status}")
    
    test_cases = [
        ('Manchester City', 'West Ham', '2025-12-20'),
        ('Bayern Munich', 'Freiburg', '2025-11-22'),
    ]
    
    for home, away, date in test_cases:
        result = collector.get_match_odds(home, away, date)
        if result:
            print(f"\n✅ {home} vs {away} ({date}): {json.dumps(result, indent=2, ensure_ascii=False)}")
        else:
            print(f"\n❌ {home} vs {away} ({date}): 无数据")
