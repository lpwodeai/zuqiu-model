"""英超赔率数据模块 (兼容层)

基于 common 通用框架的英超兼容层，保持向后兼容。
所有核心逻辑已迁移到 modules.common，此模块仅提供 EPL 特定的配置和别名。

向后兼容性：
    from modules.epl_odds import load_epl_matches  # ✅ 仍可用
    from modules.epl_odds import TEAM_MAPPINGS     # ✅ 仍可用
"""

__version__ = "2.0.0"
__author__ = "足球预测模型优化团队"
__date__ = "2026-08-14"

# 从 common 导入所有通用接口
from modules.common import (
    # 数据加载
    ODDS_DB_PATH, MatchData, OddsTemporalData, get_db_connection,
    load_league_matches, load_wdl_history, load_handicap_history,
    load_total_goals_history, load_score_history, load_all_match_data,
    load_league_statistics, get_match_ids_by_date_range,
    get_match_ids_with_complete_odds,
    # 数据清洗
    normalize_date, normalize_team_name, validate_odds_range,
    validate_score_format, validate_time_points, detect_missing_data,
    clean_match_data, clean_odds_data, generate_data_quality_report,
    clean_all_data,
    # 时序特征
    detect_v_pattern, calculate_time_weighted_trend,
    calculate_priority_score, calculate_implied_probability,
    extract_wdl_features, extract_handicap_features,
    extract_total_goals_features, extract_score_features,
    build_all_features, build_features_for_matches,
    # 回测引擎
    BacktestConfig, BacktestEngine, run_full_backtest,
    # 联赛配置
    EPL_TEAM_MAPPINGS,
)

# 英超特定球队映射
TEAM_MAPPINGS = EPL_TEAM_MAPPINGS

# 向后兼容别名
def load_epl_matches(league='英超'):
    """向后兼容：加载英超比赛数据"""
    return load_league_matches(league)

__all__ = [
    '__version__', '__author__', '__date__',
    'ODDS_DB_PATH', 'MatchData', 'OddsTemporalData', 'get_db_connection',
    'load_epl_matches', 'load_league_matches', 'load_wdl_history',
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