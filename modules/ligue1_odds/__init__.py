"""法甲赔率数据模块 (兼容层)

基于 common 通用框架的法甲兼容层，提供法甲专属配置和别名。
所有核心逻辑位于 modules.common。

向后兼容性：
    from modules.ligue1_odds import load_ligue1_matches  # ✅ 可用
    from modules.ligue1_odds import TEAM_MAPPINGS         # ✅ 可用
"""

__version__ = "1.0.0"
__author__ = "足球预测模型优化团队"
__date__ = "2026-08-14"

from modules.common import (
    ODDS_DB_PATH, MatchData, OddsTemporalData, get_db_connection,
    load_league_matches, load_wdl_history, load_handicap_history,
    load_total_goals_history, load_score_history, load_all_match_data,
    load_league_statistics, get_match_ids_by_date_range,
    get_match_ids_with_complete_odds,
    normalize_date, normalize_team_name, validate_odds_range,
    validate_score_format, validate_time_points, detect_missing_data,
    clean_match_data, clean_odds_data, generate_data_quality_report,
    clean_all_data,
    detect_v_pattern, calculate_time_weighted_trend,
    calculate_priority_score, calculate_implied_probability,
    extract_wdl_features, extract_handicap_features,
    extract_total_goals_features, extract_score_features,
    build_all_features, build_features_for_matches,
    BacktestConfig, BacktestEngine, run_full_backtest,
    LIGUE1_TEAM_MAPPINGS,
)

TEAM_MAPPINGS = LIGUE1_TEAM_MAPPINGS

def load_ligue1_matches(league='法甲'):
    """向后兼容：加载法甲比赛数据"""
    return load_league_matches(league)

__all__ = [
    '__version__', '__author__', '__date__',
    'ODDS_DB_PATH', 'MatchData', 'OddsTemporalData', 'get_db_connection',
    'load_ligue1_matches', 'load_league_matches', 'load_wdl_history',
    'load_handicap_history', 'load_total_goals_history', 'load_score_history',
    'load_all_match_data', 'load_league_statistics',
    'get_match_ids_by_date_range', 'get_match_ids_with_complete_odds',
    'TEAM_MAPPINGS',
    'normalize_date', 'normalize_team_name', 'validate_odds_range',
    'validate_score_format', 'validate_time_points', 'detect_missing_data',
    'clean_match_data', 'clean_odds_data', 'generate_data_quality_report',
    'clean_all_data',
    'detect_v_pattern', 'calculate_time_weighted_trend',
    'calculate_priority_score', 'calculate_implied_probability',
    'extract_wdl_features', 'extract_handicap_features',
    'extract_total_goals_features', 'extract_score_features',
    'build_all_features', 'build_features_for_matches',
    'BacktestConfig', 'BacktestEngine', 'run_full_backtest',
]