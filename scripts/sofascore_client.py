"""
SofaScore 数据采集客户端

功能:
  1. 通过SofaScore API获取比赛数据、球员统计、赔率信息
  2. 支持实时比分、比赛状态更新
  3. 数据缓存和限流机制
  4. 可插拔的数据源架构

使用:
  client = SofaScoreClient()
  matches = client.get_matches(league='EPL', date='2026-08-05')
  odds = client.get_odds(match_id='xxx')
"""

import json
import time
import logging
from datetime import datetime, timedelta
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError
from urllib.parse import urlencode
from typing import Optional

logger = logging.getLogger(__name__)

SOFASCORE_BASE = 'https://www.sofascore.com/api/v1'

LEAGUE_MAP = {
    '英超': 'EPL',
    '西甲': 'LaLiga',
    '意甲': 'SerieA',
    '德甲': 'Bundesliga',
    '法甲': 'Ligue1',
    'EPL': 'EPL',
    'La Liga': 'LaLiga',
    'Serie A': 'SerieA',
    'Bundesliga': 'Bundesliga',
    'Ligue 1': 'Ligue1',
}

REQUEST_DELAY = 1.5
MAX_RETRIES = 3
REQUEST_TIMEOUT = 15


class SofaScoreClient:
    """SofaScore API 数据采集客户端"""
    
    def __init__(self, rate_limit=None, timeout=None):
        self.rate_limit = rate_limit or REQUEST_DELAY
        self.timeout = timeout or REQUEST_TIMEOUT
        self.last_request = 0
        self._cache = {}
        self._cache_ttl = 3600
        
    def _throttle(self):
        elapsed = time.time() - self.last_request
        if elapsed < self.rate_limit:
            time.sleep(self.rate_limit - elapsed)
        self.last_request = time.time()
    
    def _request(self, endpoint, params=None):
        url = f"{SOFASCORE_BASE}/{endpoint}"
        if params:
            url += f"?{urlencode(params)}"
        
        if url in self._cache:
            cached = self._cache[url]
            if time.time() - cached['time'] < self._cache_ttl:
                return cached['data']
        
        for attempt in range(MAX_RETRIES):
            try:
                self._throttle()
                
                req = Request(url)
                req.add_header('User-Agent', 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36')
                req.add_header('Accept', 'application/json')
                
                with urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode('utf-8'))
                
                self._cache[url] = {'data': data, 'time': time.time()}
                return data
                
            except HTTPError as e:
                if e.code == 429:
                    wait = (attempt + 1) * 5
                    logger.warning(f"Rate limited, waiting {wait}s...")
                    time.sleep(wait)
                elif e.code == 404:
                    logger.info(f"Resource not found: {url}")
                    return None
                else:
                    logger.error(f"HTTP {e.code}: {url}")
                    
            except (URLError, TimeoutError) as e:
                logger.warning(f"Request failed (attempt {attempt + 1}): {e}")
                if attempt < MAX_RETRIES - 1:
                    time.sleep(2 ** attempt)
                    
        logger.error(f"All retries exhausted: {url}")
        return None
    
    def search_team(self, team_name):
        data = self._request('search/teams', {'query': team_name})
        if data and 'results' in data:
            for result in data['results']:
                if result.get('entity') == 'team':
                    return {
                        'id': result.get('id'),
                        'name': result.get('name'),
                        'slug': result.get('slug'),
                        'country': result.get('country', {}).get('name'),
                    }
        return None
    
    def search_match(self, home_team, away_team, date=None):
        params = {'query': f"{home_team} vs {away_team}"}
        if date:
            params['date'] = date
        data = self._request('search/events', params)
        if data and 'results' in data:
            for result in data['results']:
                if result.get('entity') == 'event':
                    return self._parse_event(result)
        return None
    
    def get_match_details(self, event_id):
        data = self._request(f'event/{event_id}')
        if data:
            return self._parse_full_event(data)
        return None
    
    def get_match_odds(self, event_id, market_type='1X2'):
        data = self._request(f'event/{event_id}/odds', {'market': market_type})
        if data and 'odds' in data:
            return self._parse_odds(data)
        return None
    
    def get_team_form(self, team_id, last_n=5):
        data = self._request(f'team/{team_id}/form', {'last': last_n})
        return data
    
    def get_player_stats(self, event_id):
        data = self._request(f'event/{event_id}/player-stats')
        return data
    
    def get_lineups(self, event_id):
        data = self._request(f'event/{event_id}/lineups')
        return data
    
    def _parse_event(self, raw):
        return {
            'id': raw.get('id'),
            'slug': raw.get('slug'),
            'sport': raw.get('sport', {}).get('name'),
            'league': raw.get('tournament', {}).get('name'),
            'home_team': raw.get('homeTeam', {}).get('name'),
            'away_team': raw.get('awayTeam', {}).get('name'),
            'start_time': raw.get('startTime'),
            'status': raw.get('status', {}).get('code'),
            'venue': raw.get('venue', {}).get('name'),
        }
    
    def _parse_full_event(self, data):
        result = self._parse_event(data)
        result.update({
            'score': data.get('score', {}),
            'players': data.get('players', []),
            'goal_events': data.get('goalEvents', []),
            'substitutions': data.get('substitutions', []),
            'key_events': data.get('keyEvents', []),
        })
        return result
    
    def _parse_odds(self, data):
        odds = []
        for item in data.get('odds', []):
            odds.append({
                'market': item.get('market'),
                'selections': [
                    {
                        'name': sel.get('name'),
                        'price': sel.get('price'),
                        'label': sel.get('label'),
                    }
                    for sel in item.get('selections', [])
                ],
                'timestamp': item.get('timestamp'),
            })
        return odds
    
    def find_match_by_teams_date(self, home_team, away_team, match_date):
        home = self.search_team(home_team)
        away = self.search_team(away_team)
        
        if not home or not away:
            logger.warning(f"Team search failed: {home_team} vs {away_team}")
            return None
        
        date_str = match_date.strftime('%Y-%m-%d') if isinstance(match_date, datetime) else match_date
        
        data = self._request(f'tournament/{self._get_tournament_id(home["id"])}/events', {
            'date': date_str,
        })
        
        if not data or 'events' not in data:
            match = self.search_match(home_team, away_team, date_str)
            return match
        
        for event in data['events']:
            if (event.get('homeTeam', {}).get('id') == home['id'] and
                event.get('awayTeam', {}).get('id') == away['id']):
                return self._parse_event(event)
        
        return None
    
    def _get_tournament_id(self, team_id):
        data = self._request(f'team/{team_id}/statistics')
        if data and 'statistics' in data:
            stats = data['statistics']
            if stats and len(stats) > 0:
                return stats[0].get('tournament', {}).get('id')
        return None
    
    def batch_fetch_missing(self, missing_matches, progress_callback=None):
        results = {'found': [], 'not_found': [], 'errors': []}
        
        total = len(missing_matches)
        for i, match in enumerate(missing_matches):
            match_id, home, away, date, league = match
            
            try:
                result = self.find_match_by_teams_date(home, away, date)
                
                if result:
                    odds = self.get_match_odds(result['id'])
                    results['found'].append({
                        'match_id': match_id,
                        'sofascoe_event': result,
                        'odds': odds,
                    })
                else:
                    results['not_found'].append(match_id)
                    
            except Exception as e:
                results['errors'].append({
                    'match_id': match_id,
                    'error': str(e),
                })
            
            if progress_callback:
                progress_callback(i + 1, total, match_id)
        
        return results


def create_client(**kwargs):
    return SofaScoreClient(**kwargs)


if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    client = create_client()
    
    team = client.search_team('Manchester City')
    print(f"Team search: {team}")
    
    match = client.search_match('Manchester City', 'West Ham', '2025-12-20')
    print(f"Match search: {match}")
