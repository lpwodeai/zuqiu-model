import fetch from 'node-fetch';
import yaml from 'js-yaml';
import fs from 'fs';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

class ApiConfig {
  constructor() {
    this.loadConfig();
    this.cache = new Map();
  }

  loadConfig() {
    const configPath = path.join(__dirname, '../../config.yaml');
    const configContent = fs.readFileSync(configPath, 'utf8');
    const config = yaml.load(configContent);

    this.footballData = this.resolveEnvVars(config.realtime_data?.football_data_api || {});
    this.oddsApi = this.resolveEnvVars(config.realtime_data?.odds_api || {});
    this.understat = config.realtime_data?.understat_api || {};
  }

  resolveEnvVars(obj) {
    const result = {};
    for (const [key, value] of Object.entries(obj)) {
      if (typeof value === 'string' && value.startsWith('${') && value.endsWith('}')) {
        const envVar = value.slice(2, -1);
        result[key] = process.env[envVar] || '';
      } else if (typeof value === 'object') {
        result[key] = this.resolveEnvVars(value);
      } else {
        result[key] = value;
      }
    }
    return result;
  }

  getCache(key) {
    const entry = this.cache.get(key);
    if (entry && Date.now() < entry.expire) {
      return entry.data;
    }
    this.cache.delete(key);
    return null;
  }

  setCache(key, data, ttlSeconds) {
    this.cache.set(key, {
      data,
      expire: Date.now() + ttlSeconds * 1000
    });
  }
}

function createTimeoutController(ms) {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => {
    controller.abort();
  }, ms);
  return { controller, timeoutId };
}

async function fetchWithTimeout(url, options = {}, timeout = 10000) {
  const { controller, timeoutId } = createTimeoutController(timeout);
  
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal
    });
    return response;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error(`Request timeout after ${timeout}ms`);
    }
    throw error;
  } finally {
    clearTimeout(timeoutId);
  }
}

async function fetchWithRetry(fetchFn, maxRetries = 3, baseDelay = 1000) {
  let lastError;
  for (let i = 0; i < maxRetries; i++) {
    try {
      return await fetchFn();
    } catch (error) {
      lastError = error;
      if (i < maxRetries - 1) {
        const delay = baseDelay * Math.pow(2, i) + Math.random() * 1000;
        console.log(`Retry ${i + 1}/${maxRetries} after ${delay.toFixed(0)}ms: ${error.message}`);
        await new Promise(resolve => setTimeout(resolve, delay));
      }
    }
  }
  throw lastError;
}

class FootballDataClient {
  constructor(config) {
    this.config = config.footballData;
    this.baseUrl = this.config.base_url || 'https://api.football-data.org/v4';
    this.apiKey = this.config.api_key;
    this.timeout = this.config.timeout || 10000;
    this.cacheTtl = this.config.cache_ttl_seconds || 3600;
  }

  async request(endpoint, options = {}) {
    if (!this.apiKey) {
      throw new Error('Football-Data API key not configured');
    }

    const url = new URL(endpoint, this.baseUrl);
    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        url.searchParams.set(key, value);
      });
    }

    const cacheKey = `football_data_${endpoint}_${JSON.stringify(options.params)}`;
    const cached = this.config.getCache(cacheKey);
    if (cached) return cached;

    const response = await fetchWithTimeout(url, {
      method: options.method || 'GET',
      headers: {
        'X-Auth-Token': this.apiKey,
        'Accept': 'application/json',
        ...options.headers
      }
    }, this.timeout);

    if (!response.ok) {
      throw new Error(`Football-Data API error: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();
    this.config.setCache(cacheKey, data, this.cacheTtl);
    return data;
  }

  async getCompetitions() {
    return this.request('/competitions');
  }

  async getCompetitionMatches(leagueId, options = {}) {
    const params = {
      season: options.season,
      status: options.status,
      ...options
    };
    return this.request(`/competitions/${leagueId}/matches`, { params });
  }

  async getTeamMatches(teamId, options = {}) {
    const params = {
      season: options.season,
      status: options.status,
      ...options
    };
    return this.request(`/teams/${teamId}/matches`, { params });
  }

  async getTeam(teamId) {
    return this.request(`/teams/${teamId}`);
  }

  async getCompetitionStandings(leagueId) {
    return this.request(`/competitions/${leagueId}/standings`);
  }

  async getAllMatches(options = {}) {
    const params = {
      dateFrom: options.dateFrom,
      dateTo: options.dateTo,
      competition: options.competition,
      status: options.status,
      ...options
    };
    return this.request('/matches', { params });
  }
}

class OddsApiClient {
  constructor(config) {
    this.config = config.oddsApi;
    this.baseUrl = this.config.base_url || 'https://api.the-odds-api.com/v4';
    this.apiKey = this.config.api_key;
    this.timeout = this.config.timeout || 10000;
    this.cacheTtl = this.config.cache_ttl_seconds || 300;
    this.regions = this.config.regions || ['uk', 'eu', 'us'];
    this.markets = this.config.markets || ['h2h'];
  }

  async request(endpoint, options = {}) {
    if (!this.apiKey) {
      throw new Error('The Odds API key not configured');
    }

    const url = new URL(endpoint, this.baseUrl);
    const params = {
      apiKey: this.apiKey,
      regions: this.regions.join(','),
      markets: this.markets.join(','),
      ...options.params
    };

    Object.entries(params).forEach(([key, value]) => {
      url.searchParams.set(key, value);
    });

    const cacheKey = `odds_api_${endpoint}_${JSON.stringify(params)}`;
    const cached = this.config.getCache(cacheKey);
    if (cached) return cached;

    const response = await fetchWithTimeout(url, {
      method: options.method || 'GET',
      headers: {
        'Accept': 'application/json',
        ...options.headers
      }
    }, this.timeout);

    if (!response.ok) {
      throw new Error(`The Odds API error: ${response.status} ${response.statusText}`);
    }

    const data = await response.json();
    this.config.setCache(cacheKey, data, this.cacheTtl);
    return data;
  }

  async getOdds(sportKey, options = {}) {
    const params = {
      dateFormat: options.dateFormat || 'iso',
      ...options
    };
    return this.request(`/sports/${sportKey}/odds`, { params });
  }

  async getUpcomingMatches(sportKey) {
    return this.request(`/sports/${sportKey}/events`);
  }

  async getAllSports() {
    return this.request('/sports');
  }

  async getScores(sportKey, options = {}) {
    const params = {
      dateFormat: options.dateFormat || 'iso',
      ...options
    };
    return this.request(`/sports/${sportKey}/scores`, { params });
  }

  async getOddsByLeague(league) {
    const sportKeyMap = {
      'PL': 'soccer_epl',
      'SA': 'soccer_spain_la_liga',
      'BL1': 'soccer_germany_bundesliga',
      'SerieA': 'soccer_italy_serie_a',
      'FL1': 'soccer_france_ligue_one'
    };

    const sportKey = sportKeyMap[league];
    if (!sportKey) {
      throw new Error(`Unsupported league: ${league}`);
    }

    return this.getOdds(sportKey);
  }
}

class UnderstatClient {
  constructor(config) {
    this.config = config.understat;
    this.baseUrl = this.config.base_url || 'https://understat.com';
    this.timeout = this.config.timeout || 15000;
    this.cacheTtl = this.config.cache_ttl_seconds || 7200;
    this.enabled = this.config.enabled || false;
    this.maxRetries = 3;
    this.baseDelay = 2000;
  }

  async request(endpoint, options = {}) {
    if (!this.enabled) {
      throw new Error('Understat API is not enabled');
    }

    const url = new URL(endpoint, this.baseUrl);
    if (options.params) {
      Object.entries(options.params).forEach(([key, value]) => {
        url.searchParams.set(key, value);
      });
    }

    const cacheKey = `understat_${endpoint}_${JSON.stringify(options.params)}`;
    const cached = this.config.getCache(cacheKey);
    if (cached) return cached;

    const fetchFn = async () => {
      const response = await fetchWithTimeout(url, {
        method: options.method || 'GET',
        headers: {
          'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
          'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
          'Accept-Language': 'en-US,en;q=0.5',
          'Referer': 'https://understat.com/',
          ...options.headers
        }
      }, this.timeout);

      if (!response.ok) {
        throw new Error(`Understat API error: ${response.status} ${response.statusText}`);
      }

      return response;
    };

    const response = await fetchWithRetry(fetchFn, this.maxRetries, this.baseDelay);
    const text = await response.text();
    let data = text;

    const jsonMatch = text.match(/var\s+(\w+)\s*=\s*JSON\.parse\('([^']+)'\)/);
    if (jsonMatch) {
      try {
        data = JSON.parse(jsonMatch[2]);
      } catch (e) {
        console.warn('Failed to parse Understat JSON:', e.message);
      }
    }

    this.config.setCache(cacheKey, data, this.cacheTtl);
    return data;
  }

  async getLeagueXg(league, season) {
    const leagueMap = {
      'PL': 'EPL',
      'SA': 'La_liga',
      'BL1': 'Bundesliga',
      'SerieA': 'Serie_A',
      'FL1': 'Ligue_1'
    };

    const leagueCode = leagueMap[league];
    if (!leagueCode) {
      throw new Error(`Unsupported league: ${league}`);
    }

    return this.request(`/league/${leagueCode}/${season}`);
  }

  async getTeamXg(teamId, season) {
    return this.request(`/team/${teamId}/${season}`);
  }

  async getMatchXg(matchId) {
    return this.request(`/match/${matchId}`);
  }
}

const config = new ApiConfig();

const footballDataClient = new FootballDataClient(config);
const oddsApiClient = new OddsApiClient(config);
const understatClient = new UnderstatClient(config);

export {
  config as ApiConfig,
  footballDataClient as FootballDataClient,
  oddsApiClient as OddsApiClient,
  understatClient as UnderstatClient
};