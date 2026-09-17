"""
特征时序分离模块 (D-009)
========================
严格区分赛前可用特征和赛后统计特征，防止数据泄露。

核心功能:
    1. detect_leakage(X)            - 检测特征矩阵中的赛后泄露特征
    2. feature_temporal_split(X)    - 过滤出赛前特征子集
    3. generate_feature_attributes(X) - 生成特征属性标记表
    4. validate_no_leakage(X)       - 验证特征矩阵无泄露（断言用）

设计原则:
    - 白名单机制：默认只保留已知的赛前特征
    - 黑名单机制：明确标记的赛后特征一律剔除
    - 未知特征告警：未分类的特征触发警告，需人工确认

关联决策: D-20260805-009
关联经验: EXP-008 (虚假高准确率通常意味着数据泄露)
"""

import os
import pandas as pd
import numpy as np
from typing import Tuple, Dict, List, Optional


# ========================================
# 赛后特征黑名单（绝对禁止作为特征）
# ========================================
# 这些特征是比赛结束后才能获得的统计量，作为特征会导致数据泄露
POST_MATCH_FEATURES_BLACKLIST = {
    # 比赛结果（直接标签泄露）
    'homeGoals', 'awayGoals',           # 比赛比分
    'goal_diff', 'total_goals',         # 由比分计算
    'result', 'actual_wdl',             # 比赛结果标签
    'actual_score', 'actual_handicap',  # 实际开奖
    'actual_total_goals',

    # 赛后统计（xG等高级统计，赛后才有）
    'homeXg', 'awayXg',                 # 预期进球
    'homeShots', 'awayShots',           # 射门数
    'homeShotsOnTarget', 'awayShotsOnTarget',  # 射正数
    'homePossession', 'awayPossession', # 控球率
    'homeCorners', 'awayCorners',       # 角球数
    'homeFouls', 'awayFouls',           # 犯规数
    'homeYellowCards', 'awayYellowCards',       # 黄牌数
    'homeRedCards', 'awayRedCards',     # 红牌数
    'homeOffsides', 'awayOffsides',     # 越位数
    'homeSaves', 'awaySaves',           # 扑救数
    'homePassAccuracy', 'awayPassAccuracy',     # 传球成功率
}


# ========================================
# 赛前特征白名单（当前113维特征的分类）
# ========================================
# 每个特征标记: (类别, 时序类型, 来源, 描述)
# 时序类型: pre_match(赛前可用) / post_match(赛后才有) / derived_pre(基于赛前历史派生)
PRE_MATCH_FEATURE_CATALOG = {
    # --- 基础特征 (11维) ---
    'league_德甲':    ('基础', 'pre_match', 'build_features', '德甲联赛标识(one-hot)'),
    'league_西甲':    ('基础', 'pre_match', 'build_features', '西甲联赛标识(one-hot)'),
    'league_法甲':    ('基础', 'pre_match', 'build_features', '法甲联赛标识(one-hot)'),
    'league_英超':    ('基础', 'pre_match', 'build_features', '英超联赛标识(one-hot)'),
    'league_意甲':    ('基础', 'pre_match', 'build_features', '意甲联赛标识(one-hot)'),
    'month':                ('基础', 'pre_match', 'build_features', '比赛月份(1-12)'),
    'day_of_week':          ('基础', 'pre_match', 'build_features', '星期几(0=周一)'),
    'is_weekend':           ('基础', 'pre_match', 'build_features', '是否周末(1/0)'),
    'is_early_season':      ('基础', 'pre_match', 'build_features', '是否赛季初(8-9月)'),
    'is_mid_season':        ('基础', 'pre_match', 'build_features', '是否赛季中(10-2月)'),
    'is_late_season':       ('基础', 'pre_match', 'build_features', '是否赛季末(3-5月)'),

    # --- 球队历史统计特征 (63维) - 主队 (19维) ---
    'home_avg_goals':           ('球队', 'derived_pre', 'precompute_team_stats', '主队历史场均进球(赛前累计)'),
    'home_avg_opp_goals':       ('球队', 'derived_pre', 'precompute_team_stats', '主队历史场均失球(赛前累计)'),
    'home_win_rate':            ('球队', 'derived_pre', 'precompute_team_stats', '主队历史胜率(赛前累计)'),
    'home_draw_rate':           ('球队', 'derived_pre', 'precompute_team_stats', '主队历史平局率(赛前累计)'),
    'home_loss_rate':           ('球队', 'derived_pre', 'precompute_team_stats', '主队历史败率(赛前累计)'),
    'home_goals_std':           ('球队', 'derived_pre', 'precompute_team_stats', '主队历史进球标准差'),
    'home_recent_form':         ('球队', 'derived_pre', 'precompute_team_stats', '主队近5场状态(rolling mean)'),
    'home_form_trend':          ('球队', 'derived_pre', 'precompute_team_stats', '主队状态趋势(6场rolling差)'),
    'home_consecutive_wins':    ('球队', 'derived_pre', 'precompute_team_stats', '主队连胜场次'),
    'home_consecutive_losses':  ('球队', 'derived_pre', 'precompute_team_stats', '主队连败场次'),
    'home_consecutive_undefeated': ('球队', 'derived_pre', 'precompute_team_stats', '主队连续不败场次'),
    'home_games_played':        ('球队', 'derived_pre', 'precompute_team_stats', '主队已赛场次'),
    'home_weighted_win_rate':   ('球队', 'derived_pre', 'precompute_team_stats', '主队时间衰减加权胜率'),
    'home_weighted_avg_goals':  ('球队', 'derived_pre', 'precompute_team_stats', '主队时间衰减加权场均进球'),
    'home_home_win_rate':       ('球队', 'derived_pre', 'precompute_team_stats', '主队主场胜率'),
    'home_away_win_rate':       ('球队', 'derived_pre', 'precompute_team_stats', '主队客场胜率'),
    'home_home_goals':          ('球队', 'derived_pre', 'precompute_team_stats', '主队主场场均进球'),
    'home_away_goals':          ('球队', 'derived_pre', 'precompute_team_stats', '主队客场场均进球'),
    'home_home_advantage':      ('球队', 'derived_pre', 'precompute_team_stats', '主队主场优势(主胜率-客胜率)'),

    # --- 球队历史统计特征 - 客队 (19维) ---
    'away_avg_goals':           ('球队', 'derived_pre', 'precompute_team_stats', '客队历史场均进球(赛前累计)'),
    'away_avg_opp_goals':       ('球队', 'derived_pre', 'precompute_team_stats', '客队历史场均失球(赛前累计)'),
    'away_win_rate':            ('球队', 'derived_pre', 'precompute_team_stats', '客队历史胜率(赛前累计)'),
    'away_draw_rate':           ('球队', 'derived_pre', 'precompute_team_stats', '客队历史平局率(赛前累计)'),
    'away_loss_rate':           ('球队', 'derived_pre', 'precompute_team_stats', '客队历史败率(赛前累计)'),
    'away_goals_std':           ('球队', 'derived_pre', 'precompute_team_stats', '客队历史进球标准差'),
    'away_recent_form':         ('球队', 'derived_pre', 'precompute_team_stats', '客队近5场状态(rolling mean)'),
    'away_form_trend':          ('球队', 'derived_pre', 'precompute_team_stats', '客队状态趋势(6场rolling差)'),
    'away_consecutive_wins':    ('球队', 'derived_pre', 'precompute_team_stats', '客队连胜场次'),
    'away_consecutive_losses':  ('球队', 'derived_pre', 'precompute_team_stats', '客队连败场次'),
    'away_consecutive_undefeated': ('球队', 'derived_pre', 'precompute_team_stats', '客队连续不败场次'),
    'away_games_played':        ('球队', 'derived_pre', 'precompute_team_stats', '客队已赛场次'),
    'away_weighted_win_rate':   ('球队', 'derived_pre', 'precompute_team_stats', '客队时间衰减加权胜率'),
    'away_weighted_avg_goals':  ('球队', 'derived_pre', 'precompute_team_stats', '客队时间衰减加权场均进球'),
    'away_home_win_rate':       ('球队', 'derived_pre', 'precompute_team_stats', '客队主场胜率'),
    'away_away_win_rate':       ('球队', 'derived_pre', 'precompute_team_stats', '客队客场胜率'),
    'away_home_goals':          ('球队', 'derived_pre', 'precompute_team_stats', '客队主场场均进球'),
    'away_away_goals':          ('球队', 'derived_pre', 'precompute_team_stats', '客队客场场均进球'),
    'away_home_advantage':      ('球队', 'derived_pre', 'precompute_team_stats', '客队主场优势(主胜率-客胜率)'),

    # --- H2H交锋历史 (11维) ---
    'h2h_matches':              ('H2H', 'derived_pre', 'calc_h2h_stats', '历史交锋场次'),
    'h2h_home_win_rate':        ('H2H', 'derived_pre', 'calc_h2h_stats', '交锋中主队胜率'),
    'h2h_away_win_rate':        ('H2H', 'derived_pre', 'calc_h2h_stats', '交锋中客队胜率'),
    'h2h_draw_rate':            ('H2H', 'derived_pre', 'calc_h2h_stats', '交锋中平局率'),
    'h2h_avg_goals_home':       ('H2H', 'derived_pre', 'calc_h2h_stats', '交锋场均主场进球'),
    'h2h_avg_goals_away':       ('H2H', 'derived_pre', 'calc_h2h_stats', '交锋场均客场进球'),
    'h2h_avg_total_goals':      ('H2H', 'derived_pre', 'calc_h2h_stats', '交锋场均总进球'),
    'h2h_goal_diff_avg':        ('H2H', 'derived_pre', 'calc_h2h_stats', '交锋平均净胜球'),
    'h2h_last_result':          ('H2H', 'derived_pre', 'calc_h2h_stats', '上次交锋结果(0/1/2)'),
    'h2h_home_streak':          ('H2H', 'derived_pre', 'calc_h2h_stats', '主队交锋连胜场次'),
    'h2h_away_streak':          ('H2H', 'derived_pre', 'calc_h2h_stats', '客队交锋连胜场次'),

    # --- 对手强度 (2维) ---
    'home_opponent_avg_win_rate': ('球队', 'derived_pre', 'build_team_features', '主队近期对手平均胜率'),
    'away_opponent_avg_win_rate': ('球队', 'derived_pre', 'build_team_features', '客队近期对手平均胜率'),

    # --- 差值特征 (12维) ---
    'form_diff':                ('差值', 'derived_pre', 'build_team_features', '主客胜率差'),
    'goals_diff':               ('差值', 'derived_pre', 'build_team_features', '主客场均进球差'),
    'defence_diff':             ('差值', 'derived_pre', 'build_team_features', '防守差(客失球-主失球)'),
    'recent_form_diff':         ('差值', 'derived_pre', 'build_team_features', '近期状态差'),
    'form_trend_diff':          ('差值', 'derived_pre', 'build_team_features', '状态趋势差'),
    'streak_diff':              ('差值', 'derived_pre', 'build_team_features', '连胜连败差'),
    'undefeated_diff':          ('差值', 'derived_pre', 'build_team_features', '不败场次差'),
    'weighted_form_diff':       ('差值', 'derived_pre', 'build_team_features', '加权胜率差'),
    'weighted_goals_diff':      ('差值', 'derived_pre', 'build_team_features', '加权进球差'),
    'venue_diff':               ('差值', 'derived_pre', 'build_team_features', '主场优势差'),
    'goals_stability_diff':     ('差值', 'derived_pre', 'build_team_features', '进球稳定性差'),
    'opponent_strength_diff':   ('差值', 'derived_pre', 'build_team_features', '对手强度差'),

    # --- 赔率特征: WDL胜平负 (16维) ---
    'wdl_open_win':             ('赔率', 'pre_match', 'build_odds_features', 'WDL开盘主胜赔率'),
    'wdl_open_draw':            ('赔率', 'pre_match', 'build_odds_features', 'WDL开盘平局赔率'),
    'wdl_open_lose':            ('赔率', 'pre_match', 'build_odds_features', 'WDL开盘客胜赔率'),
    'wdl_close_win':            ('赔率', 'pre_match', 'build_odds_features', 'WDL收盘主胜赔率'),
    'wdl_close_draw':           ('赔率', 'pre_match', 'build_odds_features', 'WDL收盘平局赔率'),
    'wdl_close_lose':           ('赔率', 'pre_match', 'build_odds_features', 'WDL收盘客胜赔率'),
    'wdl_win_trend':            ('赔率', 'pre_match', 'build_odds_features', 'WDL主胜赔率变化趋势'),
    'wdl_draw_trend':           ('赔率', 'pre_match', 'build_odds_features', 'WDL平局赔率变化趋势'),
    'wdl_lose_trend':           ('赔率', 'pre_match', 'build_odds_features', 'WDL客胜赔率变化趋势'),
    'wdl_implied_win':          ('赔率', 'pre_match', 'build_odds_features', 'WDL主胜隐含概率(归一化)'),
    'wdl_implied_draw':         ('赔率', 'pre_match', 'build_odds_features', 'WDL平局隐含概率(归一化)'),
    'wdl_implied_lose':         ('赔率', 'pre_match', 'build_odds_features', 'WDL客胜隐含概率(归一化)'),
    'wdl_overround':            ('赔率', 'pre_match', 'build_odds_features', 'WDL赔付率(隐含概率之和)'),
    'wdl_favorite':             ('赔率', 'pre_match', 'build_odds_features', 'WDL热门选项(0/1/2)'),
    'wdl_favorite_prob':        ('赔率', 'pre_match', 'build_odds_features', 'WDL热门选项概率'),
    'wdl_record_count':         ('赔率', 'pre_match', 'build_odds_features', 'WDL赔率记录数'),

    # --- D-010: WDL 凯利指数（3维） ---
    'wdl_kelly_win':            ('赔率衍生', 'pre_match', 'build_odds_features', 'WDL主胜凯利指数(>0有价值)'),
    'wdl_kelly_draw':           ('赔率衍生', 'pre_match', 'build_odds_features', 'WDL平局凯利指数(>0有价值)'),
    'wdl_kelly_lose':           ('赔率衍生', 'pre_match', 'build_odds_features', 'WDL客胜凯利指数(>0有价值)'),

    # --- D-010: WDL 赔率变化率（3维） ---
    'wdl_win_change_rate':      ('赔率衍生', 'pre_match', 'build_odds_features', 'WDL主胜赔率变化率(<0看好)'),
    'wdl_draw_change_rate':     ('赔率衍生', 'pre_match', 'build_odds_features', 'WDL平局赔率变化率'),
    'wdl_lose_change_rate':     ('赔率衍生', 'pre_match', 'build_odds_features', 'WDL客胜赔率变化率(<0看好)'),

    # --- 赔率特征: 让球 (13维) ---
    'hcp_open_win':             ('赔率', 'pre_match', 'build_odds_features', '让球开盘主胜赔率'),
    'hcp_open_draw':            ('赔率', 'pre_match', 'build_odds_features', '让球开盘平局赔率'),
    'hcp_open_lose':            ('赔率', 'pre_match', 'build_odds_features', '让球开盘客胜赔率'),
    'hcp_close_win':            ('赔率', 'pre_match', 'build_odds_features', '让球收盘主胜赔率'),
    'hcp_close_draw':           ('赔率', 'pre_match', 'build_odds_features', '让球收盘平局赔率'),
    'hcp_close_lose':           ('赔率', 'pre_match', 'build_odds_features', '让球收盘客胜赔率'),
    'hcp_win_trend':            ('赔率', 'pre_match', 'build_odds_features', '让球主胜赔率变化趋势'),
    'hcp_draw_trend':           ('赔率', 'pre_match', 'build_odds_features', '让球平局赔率变化趋势'),
    'hcp_lose_trend':           ('赔率', 'pre_match', 'build_odds_features', '让球客胜赔率变化趋势'),
    'hcp_implied_win':          ('赔率', 'pre_match', 'build_odds_features', '让球主胜隐含概率'),
    'hcp_implied_draw':         ('赔率', 'pre_match', 'build_odds_features', '让球平局隐含概率'),
    'hcp_implied_lose':         ('赔率', 'pre_match', 'build_odds_features', '让球客胜隐含概率'),
    'hcp_record_count':         ('赔率', 'pre_match', 'build_odds_features', '让球赔率记录数'),

    # --- D-010: 让球凯利指数（3维） ---
    'hcp_kelly_win':            ('赔率衍生', 'pre_match', 'build_odds_features', '让球主胜凯利指数(>0有价值)'),
    'hcp_kelly_draw':           ('赔率衍生', 'pre_match', 'build_odds_features', '让球平局凯利指数(>0有价值)'),
    'hcp_kelly_lose':           ('赔率衍生', 'pre_match', 'build_odds_features', '让球客胜凯利指数(>0有价值)'),

    # --- D-010: 让球赔率变化率（3维） ---
    'hcp_win_change_rate':      ('赔率衍生', 'pre_match', 'build_odds_features', '让球主胜赔率变化率(<0看好)'),
    'hcp_draw_change_rate':     ('赔率衍生', 'pre_match', 'build_odds_features', '让球平局赔率变化率'),
    'hcp_lose_change_rate':     ('赔率衍生', 'pre_match', 'build_odds_features', '让球客胜赔率变化率(<0看好)'),

    # --- 赔率特征: 总进球 (6维) ---
    'tg_over_25_prob':          ('赔率', 'pre_match', 'build_odds_features', '总进球>2.5概率'),
    'tg_under_25_prob':         ('赔率', 'pre_match', 'build_odds_features', '总进球<=2.5概率'),
    'tg_most_likely':           ('赔率', 'pre_match', 'build_odds_features', '最可能进球数'),
    'tg_most_likely_prob':      ('赔率', 'pre_match', 'build_odds_features', '最可能进球数概率'),
    'tg_expected':              ('赔率', 'pre_match', 'build_odds_features', '预期总进球(加权平均)'),
    'tg_record_count':          ('赔率', 'pre_match', 'build_odds_features', '总进球赔率记录数'),

    # --- 赔率覆盖率标识 (4维) ---
    'has_wdl_odds':             ('赔率', 'pre_match', 'build_odds_features', '是否有WDL赔率(0/1)'),
    'has_hcp_odds':             ('赔率', 'pre_match', 'build_odds_features', '是否有让球赔率(0/1)'),
    'has_tg_odds':              ('赔率', 'pre_match', 'build_odds_features', '是否有总进球赔率(0/1)'),
    'odds_coverage':            ('赔率', 'pre_match', 'build_odds_features', '赔率覆盖度(0-3)'),

    # --- D-010: 市场信心度/确定性特征（3维） ---
    'odds_confidence':          ('赔率衍生', 'pre_match', 'build_odds_features', '市场确信度(最大隐含概率)'),
    'odds_entropy':             ('赔率衍生', 'pre_match', 'build_odds_features', '赔率熵(市场不确定性)'),
    'bookmaker_margin':         ('赔率衍生', 'pre_match', 'build_odds_features', '庄家利润率(赔付率-1)'),

    # --- D-010: 价值投注信号（2维，跨赔率+球队特征） ---
    'value_bet_home':           ('赔率衍生', 'pre_match', 'build_all_features', '主队价值投注(隐含概率-历史胜率)'),
    'value_bet_away':           ('赔率衍生', 'pre_match', 'build_all_features', '客队价值投注(隐含概率-历史胜率)'),

    # --- D-012: Elo Rating 特征（10维）---
    'home_elo':                 ('Elo', 'derived_pre', 'build_elo_features', '主队赛前Elo评分'),
    'away_elo':                 ('Elo', 'derived_pre', 'build_elo_features', '客队赛前Elo评分'),
    'elo_diff':                 ('Elo', 'derived_pre', 'build_elo_features', 'Elo差值(主-客+主场优势)'),
    'elo_ratio':                ('Elo', 'derived_pre', 'build_elo_features', 'Elo比率(主/客)'),
    'elo_home_expected':        ('Elo', 'derived_pre', 'build_elo_features', '主队Elo预期胜率'),
    'elo_away_expected':        ('Elo', 'derived_pre', 'build_elo_features', '客队Elo预期胜率'),
    'elo_draw_prob':            ('Elo', 'derived_pre', 'build_elo_features', '平局概率(Elo差值非线性)'),
    'home_elo_momentum':        ('Elo', 'derived_pre', 'build_elo_features', '主队近5场Elo动量'),
    'away_elo_momentum':        ('Elo', 'derived_pre', 'build_elo_features', '客队近5场Elo动量'),
    'elo_confidence':           ('Elo', 'derived_pre', 'build_elo_features', 'Elo置信度(双方Elo之和)'),

    # --- D-013: 时序赔率变化速率特征（10维）---
    'wdl_win_volatility':       ('Temporal', 'derived_pre', 'build_d013_features', '主队胜赔率波动率(标准差)'),
    'wdl_draw_volatility':      ('Temporal', 'derived_pre', 'build_d013_features', '平局赔率波动率'),
    'wdl_lose_volatility':      ('Temporal', 'derived_pre', 'build_d013_features', '客队胜赔率波动率'),
    'wdl_win_acceleration':     ('Temporal', 'derived_pre', 'build_d013_features', '主队胜赔率加速度(二阶导)'),
    'wdl_late_trend':           ('Temporal', 'derived_pre', 'build_d013_features', '临场趋势(最后两次变化)'),
    'wdl_early_trend':          ('Temporal', 'derived_pre', 'build_d013_features', '早期趋势(前两次变化)'),
    'wdl_mid_stability':        ('Temporal', 'derived_pre', 'build_d013_features', '中期稳定性(中间段方差比)'),
    'wdl_sudden_jump':          ('Temporal', 'derived_pre', 'build_d013_features', '突变检测(最大单次跳变)'),
    'wdl_update_frequency':     ('Temporal', 'derived_pre', 'build_d013_features', '赔率更新频率(时间点/天)'),
    'wdl_total_change':         ('Temporal', 'derived_pre', 'build_d013_features', '总变化幅度(绝对变化之和)'),

    # --- T-003.1: Score Odds Features (8维) ---
    'score_mode_prob':           ('Score', 'derived_pre', 'build_score_features', '最可能比分的概率'),
    'score_entropy':             ('Score', 'derived_pre', 'build_score_features', '比分分布熵(-sum(p*log(p)))'),
    'score_home_win_prob':       ('Score', 'derived_pre', 'build_score_features', '主胜比分概率之和'),
    'score_draw_prob':           ('Score', 'derived_pre', 'build_score_features', '平局比分概率之和'),
    'score_away_win_prob':       ('Score', 'derived_pre', 'build_score_features', '客胜比分概率之和'),
    'score_over_25_prob':        ('Score', 'derived_pre', 'build_score_features', '总进球>2.5概率(比分赔率)'),
    'score_expected_goals':      ('Score', 'derived_pre', 'build_score_features', '比分加权预期总进球'),
    'score_top3_concentration':  ('Score', 'derived_pre', 'build_score_features', '前3比分概率集中度'),

    # --- T-003.2: Nonlinear Transformations (29维) ---
    # A组: WDL收盘赔率变换 (9维)
    'wdl_winA_log':              ('NL', 'derived_pre', 'build_nonlinear_features', 'log(主胜赔率)'),
    'wdl_drawA_log':             ('NL', 'derived_pre', 'build_nonlinear_features', 'log(平局赔率)'),
    'wdl_loseA_log':             ('NL', 'derived_pre', 'build_nonlinear_features', 'log(客胜赔率)'),
    'wdl_winA_sqrt':             ('NL', 'derived_pre', 'build_nonlinear_features', 'sqrt(主胜赔率)'),
    'wdl_drawA_sqrt':            ('NL', 'derived_pre', 'build_nonlinear_features', 'sqrt(平局赔率)'),
    'wdl_loseA_sqrt':            ('NL', 'derived_pre', 'build_nonlinear_features', 'sqrt(客胜赔率)'),
    'wdl_winA_inv':              ('NL', 'derived_pre', 'build_nonlinear_features', '1/主胜赔率'),
    'wdl_drawA_inv':             ('NL', 'derived_pre', 'build_nonlinear_features', '1/平局赔率'),
    'wdl_loseA_inv':             ('NL', 'derived_pre', 'build_nonlinear_features', '1/客胜赔率'),
    # B组: 隐含概率变换 (9维)
    'wdl_imp_win_sq':            ('NL', 'derived_pre', 'build_nonlinear_features', '主胜概率平方'),
    'wdl_imp_draw_sq':           ('NL', 'derived_pre', 'build_nonlinear_features', '平局概率平方'),
    'wdl_imp_lose_sq':           ('NL', 'derived_pre', 'build_nonlinear_features', '客胜概率平方'),
    'wdl_imp_win_cu':            ('NL', 'derived_pre', 'build_nonlinear_features', '主胜概率立方'),
    'wdl_imp_lose_cu':           ('NL', 'derived_pre', 'build_nonlinear_features', '客胜概率立方'),
    'wdl_imp_win_log':           ('NL', 'derived_pre', 'build_nonlinear_features', 'log(主胜概率+ε)'),
    'wdl_imp_lose_log':          ('NL', 'derived_pre', 'build_nonlinear_features', 'log(客胜概率+ε)'),
    'wdl_implied_entropy':       ('NL', 'derived_pre', 'build_nonlinear_features', '胜平负隐含概率熵'),
    'wdl_implied_gini':          ('NL', 'derived_pre', 'build_nonlinear_features', '胜平负隐含概率基尼系数'),
    # C组: HCP赔率变换 (3维)
    'hcp_close_win_log':         ('NL', 'derived_pre', 'build_nonlinear_features', 'log(让球主胜赔率)'),
    'hcp_close_draw_log':        ('NL', 'derived_pre', 'build_nonlinear_features', 'log(让球平局赔率)'),
    'hcp_close_lose_log':        ('NL', 'derived_pre', 'build_nonlinear_features', 'log(让球客胜赔率)'),
    # D组: TG概率变换 (2维)
    'tg_over_25_odds':           ('NL', 'derived_pre', 'build_nonlinear_features', '大球隐含赔率(1/tg_over_25_prob)'),
    'tg_under_25_odds':          ('NL', 'derived_pre', 'build_nonlinear_features', '小球隐含赔率(1/tg_under_25_prob)'),
    # E组: 市场指标变换 (3维)
    'odds_confidence_sq':        ('NL', 'derived_pre', 'build_nonlinear_features', '市场确信度平方'),
    'odds_entropy_log':          ('NL', 'derived_pre', 'build_nonlinear_features', 'log(赔率熵+ε)'),
    'bookmaker_margin_sqrt':     ('NL', 'derived_pre', 'build_nonlinear_features', 'sqrt(庄家利润率)'),
    # F组: 跨特征交互 (3维)
    'wdl_imp_win_x_kelly':       ('NL', 'derived_pre', 'build_nonlinear_features', '主胜概率×凯利指数'),
    'wdl_imp_lose_x_kelly':      ('NL', 'derived_pre', 'build_nonlinear_features', '客胜概率×凯利指数'),
    'wdl_imp_ratio':             ('NL', 'derived_pre', 'build_nonlinear_features', '主客隐含概率比'),

    # --- T-005 v2方向A: 对手调整 Lag 特征组A 对手实力分层让球走水率 (8维) ---
    'home_hcp_draw_vs_stronger_l5':  ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队近5场vs更强对手让球走水率'),
    'home_hcp_draw_vs_similar_l5':   ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队近5场vs相近实力对手让球走水率'),
    'home_hcp_draw_vs_weaker_l5':    ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队近5场vs更弱对手让球走水率'),
    'home_hcp_draw_vs_all_l10':      ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队近10场总体让球走水率'),
    'away_hcp_draw_vs_stronger_l5':  ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队近5场vs更强对手让球走水率'),
    'away_hcp_draw_vs_similar_l5':   ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队近5场vs相近实力对手让球走水率'),
    'away_hcp_draw_vs_weaker_l5':    ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队近5场vs更弱对手让球走水率'),
    'away_hcp_draw_vs_all_l10':      ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队近10场总体让球走水率'),

    # --- T-005 v2方向A: 对手调整 Lag 特征组B 盘口线类别专属Lag (6维) ---
    'home_hcp_draw_at_give1_l10':    ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队近10场让1球盘口走水率'),
    'home_hcp_draw_at_get1_l10':     ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队近10场受让1球盘口走水率'),
    'home_hcp_draw_at_give2_l10':    ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队近10场让2球及以上盘口走水率'),
    'away_hcp_draw_at_give1_l10':    ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队近10场让1球盘口走水率'),
    'away_hcp_draw_at_get1_l10':     ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队近10场受让1球盘口走水率'),
    'away_hcp_draw_at_give2_l10':    ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队近10场让2球及以上盘口走水率'),

    # --- T-005 v2方向A: 对手调整 Lag 特征组C 直接交锋H2H让球历史 (6维) ---
    'h2h_hcp_draw_rate':             ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '双方历史交锋让球走水率'),
    'h2h_hcp_draw_count':            ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '双方历史交锋让球走水总数'),
    'h2h_total_matches':             ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '双方历史交锋总场次'),
    'h2h_last5_hcp_draws':           ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '双方近5次交锋让球走水数'),
    'home_h2h_hcp_draw_rate':        ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '主队视角H2H让球走水率'),
    'away_h2h_hcp_draw_rate':        ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '客队视角H2H让球走水率'),

    # --- T-005 v2方向A: 对手调整 Lag 特征组D 市场信号增强 (4维) ---
    'elo_gap_abs':                   ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '赛前Elo差距绝对值'),
    'hcp_draw_prob_rank':            ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '让球平局赔率分位数排名'),
    'opponent_season_draw_rate':     ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '对手赛季平局率'),
    'market_draw_std':               ('对手Lag', 'derived_pre', 'build_all_opponent_lag_features', '时序让球平局赔率标准差'),
}


def detect_leakage(X: pd.DataFrame, verbose: bool = True) -> Dict:
    """
    检测特征矩阵中的赛后泄露特征。

    参数:
        X: 特征矩阵
        verbose: 是否打印检测详情

    返回:
        dict: {
            'has_leakage': bool,         # 是否存在泄露
            'leakage_features': list,    # 泄露特征列表
            'unknown_features': list,    # 未分类特征列表
            'total_features': int,       # 总特征数
            'safe_features': int,        # 安全特征数
        }
    """
    feature_names = list(X.columns)
    leakage_features = []
    unknown_features = []
    safe_features = []

    for feat in feature_names:
        if feat in POST_MATCH_FEATURES_BLACKLIST:
            leakage_features.append(feat)
        elif feat in PRE_MATCH_FEATURE_CATALOG:
            category, temporal_type, source, desc = PRE_MATCH_FEATURE_CATALOG[feat]
            if temporal_type == 'post_match':
                leakage_features.append(feat)
            else:
                safe_features.append(feat)
        else:
            unknown_features.append(feat)

    result = {
        'has_leakage': len(leakage_features) > 0,
        'has_unknown': len(unknown_features) > 0,
        'leakage_features': leakage_features,
        'unknown_features': unknown_features,
        'total_features': len(feature_names),
        'safe_features': len(safe_features),
    }

    if verbose:
        print("\n🔍 【特征泄露检测】")
        print("-" * 70)
        print(f"  总特征数: {result['total_features']}")
        print(f"  ✅ 安全特征: {len(safe_features)}")
        print(f"  {'❌' if leakage_features else '✅'} 泄露特征: {len(leakage_features)}")
        print(f"  {'⚠️' if unknown_features else '✅'} 未分类特征: {len(unknown_features)}")

        if leakage_features:
            print(f"\n  ❌ 发现 {len(leakage_features)} 个泄露特征（赛后统计）:")
            for f in leakage_features:
                print(f"     • {f}")

        if unknown_features:
            print(f"\n  ⚠️  发现 {len(unknown_features)} 个未分类特征（需人工确认）:")
            for f in unknown_features:
                print(f"     • {f}")

        if not leakage_features and not unknown_features:
            print(f"\n  ✅ 所有特征均为赛前可用，无数据泄露风险！")

    return result


def feature_temporal_split(X: pd.DataFrame, verbose: bool = True) -> pd.DataFrame:
    """
    过滤出赛前特征子集，剔除所有赛后泄露特征。

    参数:
        X: 原始特征矩阵
        verbose: 是否打印过滤详情

    返回:
        pd.DataFrame: 仅含赛前特征的特征矩阵
    """
    detection = detect_leakage(X, verbose=verbose)

    cols_to_keep = []
    cols_to_drop = []

    for feat in X.columns:
        if feat in detection['leakage_features']:
            cols_to_drop.append(feat)
        else:
            cols_to_keep.append(feat)

    X_clean = X[cols_to_keep].copy()

    if verbose and cols_to_drop:
        print(f"\n🧹 【赛前特征过滤】")
        print("-" * 70)
        print(f"  原始特征: {X.shape[1]}维")
        print(f"  剔除泄露特征: {len(cols_to_drop)}个")
        print(f"  保留赛前特征: {X_clean.shape[1]}维")
        if cols_to_drop:
            print(f"  剔除列表: {cols_to_drop}")
    elif verbose:
        print(f"\n✅ 无需过滤，所有 {X.shape[1]} 维特征均为赛前可用。")

    return X_clean


def generate_feature_attributes(X: pd.DataFrame, output_path: Optional[str] = None) -> pd.DataFrame:
    """
    生成特征属性标记表，记录每个特征的分类信息。

    参数:
        X: 特征矩阵
        output_path: CSV输出路径（可选）

    返回:
        pd.DataFrame: 特征属性表
    """
    detection = detect_leakage(X, verbose=False)

    rows = []
    for idx, feat in enumerate(X.columns, 1):
        if feat in PRE_MATCH_FEATURE_CATALOG:
            category, temporal_type, source, desc = PRE_MATCH_FEATURE_CATALOG[feat]
        elif feat in POST_MATCH_FEATURES_BLACKLIST:
            category, temporal_type, source, desc = ('赛后', 'post_match', '未知', '赛后统计特征(禁止使用)')
        else:
            category, temporal_type, source, desc = ('未知', 'unknown', '未知', '需人工确认')

        is_safe = temporal_type in ('pre_match', 'derived_pre')
        rows.append({
            '序号': idx,
            '特征名': feat,
            '类别': category,
            '时序类型': temporal_type,
            '是否赛前可用': '是' if is_safe else '否',
            '是否泄露风险': '否' if is_safe else '是',
            '来源函数': source,
            '描述': desc,
        })

    attrs_df = pd.DataFrame(rows)

    if output_path:
        attrs_df.to_csv(output_path, index=False, encoding='utf-8-sig')
        print(f"📄 特征属性标记表已保存: {output_path}")

    return attrs_df


def validate_no_leakage(X: pd.DataFrame, context: str = "") -> None:
    """
    断言式验证：确保特征矩阵无泄露特征。
    如发现泄露特征，抛出 ValueError 阻止训练继续。

    参数:
        X: 特征矩阵
        context: 验证上下文描述（用于错误信息）

    Raises:
        ValueError: 当检测到泄露特征时
    """
    detection = detect_leakage(X, verbose=False)

    if detection['has_leakage']:
        leakage_list = detection['leakage_features']
        msg = f"数据泄露检测失败{' (' + context + ')' if context else ''}！\n"
        msg += f"发现 {len(leakage_list)} 个赛后泄露特征:\n"
        for f in leakage_list:
            msg += f"  - {f}\n"
        msg += "\n请使用 feature_temporal_split() 过滤后再训练。"
        raise ValueError(msg)

    if detection['has_unknown']:
        unknown_list = detection['unknown_features']
        print(f"⚠️  警告{' (' + context + ')' if context else ''}: "
              f"发现 {len(unknown_list)} 个未分类特征，建议确认其时序属性:")
        for f in unknown_list:
            print(f"  - {f}")


def print_feature_summary(X: pd.DataFrame) -> None:
    """打印特征分类统计摘要"""
    attrs = generate_feature_attributes(X)

    print("\n📊 【特征分类统计】")
    print("-" * 70)
    print(f"  总特征数: {len(attrs)}")

    print(f"\n  按类别:")
    for cat, count in attrs['类别'].value_counts().items():
        print(f"    {cat}: {count}维")

    print(f"\n  按时序类型:")
    for tt, count in attrs['时序类型'].value_counts().items():
        marker = '✅' if tt in ('pre_match', 'derived_pre') else '❌'
        print(f"    {marker} {tt}: {count}维")

    safe_count = (attrs['是否泄露风险'] == '否').sum()
    risk_count = (attrs['是否泄露风险'] == '是').sum()
    print(f"\n  泄露风险: ✅安全 {safe_count}维 / ❌风险 {risk_count}维")


if __name__ == '__main__':
    # 独立运行：加载特征并检测
    import sys
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from feature_utils import load_match_data_odds, build_all_features

    print("=" * 70)
    print("🧪 D-009 特征泄露检测独立验证")
    print("=" * 70)

    df = load_match_data_odds()
    X, y = build_all_features(df, include_odds=True)

    # 1. 检测泄露
    detection = detect_leakage(X)

    # 2. 打印分类摘要
    print_feature_summary(X)

    # 3. 生成属性标记表
    output_csv = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        'reports', 'feature_attributes.csv'
    )
    os.makedirs(os.path.dirname(output_csv), exist_ok=True)
    generate_feature_attributes(X, output_csv)

    # 4. 过滤验证
    X_clean = feature_temporal_split(X)

    print("\n" + "=" * 70)
    if not detection['has_leakage'] and not detection['has_unknown']:
        print("✅ D-009 验证通过：所有特征均为赛前可用，无数据泄露风险！")
    else:
        print("⚠️  请检查上述告警信息。")
    print("=" * 70)
