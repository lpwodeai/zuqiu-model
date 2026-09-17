"""通用赔率分析模块 (Common)

"联赛无关通用框架 + 联赛相关球队映射" 架构的通用层。
所有联赛模块共享的数据加载、清洗、特征提取和回测逻辑。

模块结构：
- league_config:    联赛配置（球队映射、默认参数）
- data_loader:      通用数据加载（参数化联赛名）
- data_cleaner:     通用数据清洗（TEAM_MAPPINGS 作为参数注入）
- temporal_features: 赔率时序特征提取（100% 通用）
- backtest_engine:   回测引擎（100% 通用）

使用方式：
    from modules.common import (
        load_league_matches, load_wdl_history,
        normalize_team_name, clean_match_data,
        build_all_features, build_features_for_matches,
        BacktestEngine, BacktestConfig, run_full_backtest,
        get_team_mappings, get_league_name,
        EPL_TEAM_MAPPINGS, SERIEA_TEAM_MAPPINGS,
    )
"""

from .data_loader import (
    MatchData,
    OddsTemporalData,
    get_db_connection,
    load_league_matches,
    load_wdl_history,
    load_handicap_history,
    load_total_goals_history,
    load_score_history,
    load_all_match_data,
    load_league_statistics,
    get_match_ids_by_date_range,
    get_match_ids_with_complete_odds,
    ODDS_DB_PATH,
    FIVE_LEAGUES_DB_PATH,
)

from .data_cleaner import (
    normalize_date,
    normalize_team_name,
    validate_odds_range,
    validate_score_format,
    validate_time_points,
    detect_missing_data,
    clean_match_data,
    clean_odds_data,
    generate_data_quality_report,
    clean_all_data,
)

from .temporal_features import (
    detect_v_pattern,
    calculate_time_weighted_trend,
    calculate_priority_score,
    calculate_implied_probability,
    extract_wdl_features,
    extract_handicap_features,
    extract_total_goals_features,
    extract_score_features,
    build_all_features,
    build_features_for_matches,
)

from .backtest_engine import (
    BacktestConfig,
    BacktestEngine,
    run_full_backtest,
)

from .league_config import (
    LEAGUE_NAMES,
    EPL_TEAM_MAPPINGS,
    SERIEA_TEAM_MAPPINGS,
    BUNDESLIGA_TEAM_MAPPINGS,
    LALIGA_TEAM_MAPPINGS,
    LIGUE1_TEAM_MAPPINGS,
    LEAGUE_TEAM_MAPPINGS,
    LEAGUE_DEFAULTS,
    get_team_mappings,
    get_league_name,
)

__all__ = [
    # data_loader
    'MatchData', 'OddsTemporalData', 'get_db_connection',
    'load_league_matches', 'load_wdl_history', 'load_handicap_history',
    'load_total_goals_history', 'load_score_history', 'load_all_match_data',
    'load_league_statistics', 'get_match_ids_by_date_range',
    'get_match_ids_with_complete_odds', 'ODDS_DB_PATH', 'FIVE_LEAGUES_DB_PATH',
    # data_cleaner
    'normalize_date', 'normalize_team_name', 'validate_odds_range',
    'validate_score_format', 'validate_time_points', 'detect_missing_data',
    'clean_match_data', 'clean_odds_data', 'generate_data_quality_report',
    'clean_all_data',
    # temporal_features
    'detect_v_pattern', 'calculate_time_weighted_trend',
    'calculate_priority_score', 'calculate_implied_probability',
    'extract_wdl_features', 'extract_handicap_features',
    'extract_total_goals_features', 'extract_score_features',
    'build_all_features', 'build_features_for_matches',
    # backtest_engine
    'BacktestConfig', 'BacktestEngine', 'run_full_backtest',
    # league_config
    'LEAGUE_NAMES',
    'EPL_TEAM_MAPPINGS', 'SERIEA_TEAM_MAPPINGS',
    'BUNDESLIGA_TEAM_MAPPINGS', 'LALIGA_TEAM_MAPPINGS', 'LIGUE1_TEAM_MAPPINGS',
    'LEAGUE_TEAM_MAPPINGS', 'LEAGUE_DEFAULTS',
    'get_team_mappings', 'get_league_name',
]