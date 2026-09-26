# -*- coding: utf-8 -*-
"""
SofaScore 球员级数据 → 赛前球队级特征聚合（T-007）

数据流：
  odds.db.match_player_stats + match_lineups + fbref_match_mapping
  → 按球队+日期排序 → 对每场取该队此前 N 场 → 聚合为球队级赛前特征
  → 输出 DataFrame（以 event_id 为 key，含 match_id_cn 用于与 matches 表 JOIN）

生成 34 维 × 主客 = 68 维特征：
  [全队 23 维] 评分/xG/xA/传球成功率/长传成功率/传中成功率/过人成功率/抢断/拦截/
  对抗成功率/空中对抗成功率/抢回球权/丢球/绝佳机会/冲刺距离/高速跑/
  总跑动/门将扑救率/门将扑出球/解围/阵型一致性/出场球员评分标准差
  [位置-specific 11 维, P2-10] 锋线(F)进球/助攻/触球/评分 + 中场(M)传球/触球/评分
  + 后卫(D)拦截/抢断/对抗成功率/评分

用法：
  python sofascore_pre_match_features.py                    # 全量生成并写回 odds.db
  python sofascore_pre_match_features.py --n-recent 5        # 指定近 N 场窗口
  python sofascore_pre_match_features.py --output csv        # 输出 CSV 而非写 DB
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ==================== 路径常量 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent  # 项目根（features 的上一级）
DB_PATH = PROJECT_ROOT / "data" / "odds.db"

# 导入队名归一化工具（确保写入 DB 前队名统一）
# C-20260828-011 (P1-9 遗留): 将项目根加入 sys.path，使 `from features.player_availability_features ...`
# 这类包导入可用（此前仅插入 scripts 目录，`features` 包无法定位 → PA 特征静默失败）
sys.path.insert(0, str(PROJECT_ROOT))
sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
from feature_utils import normalize_team_name
from db_utils import connect, read_sql, write_dataframe  # noqa: E402  (项目根已入 sys.path)

# ==================== SofaScore 英文队名 → 中文映射 ====================
# 覆盖五大联赛 25/26 赛季所有球队（SofaScore 全名格式）
SOFA_TEAM_CN_MAP: Dict[str, str] = {
    # 英超
    "Liverpool FC": "利物浦", "Manchester City": "曼城", "Arsenal FC": "阿森纳",
    "Manchester United": "曼联", "Tottenham Hotspur": "热刺", "Chelsea FC": "切尔西",
    "Newcastle United": "纽卡斯尔", "Aston Villa": "阿斯顿维拉",
    "Brighton & Hove Albion": "布莱顿", "West Ham United": "西汉姆",
    "Brentford FC": "布伦特福德", "Fulham FC": "富勒姆",
    "Crystal Palace": "水晶宫", "Wolverhampton Wanderers": "狼队",
    "Everton FC": "埃弗顿", "Nottingham Forest": "诺丁汉森林",
    "Bournemouth": "伯恩茅斯", "Burnley FC": "伯恩利",
    "Sunderland": "桑德兰", "Leeds United": "利兹联",
    "Sheffield United": "谢菲尔德联", "Luton Town": "卢顿",
    # 西甲
    "Real Madrid": "皇家马德里", "FC Barcelona": "巴塞罗那",
    "Atlético Madrid": "马德里竞技", "Sevilla FC": "塞维利亚",
    "Real Sociedad": "皇家社会", "Villarreal CF": "比利亚雷亚尔",
    "Real Betis": "皇家贝蒂斯", "Athletic Bilbao": "毕尔巴鄂竞技",
    "Valencia CF": "瓦伦西亚", "Getafe CF": "赫塔费",
    "CA Osasuna": "奥萨苏纳", "Girona FC": "赫罗纳",
    "RCD Mallorca": "马略卡", "Rayo Vallecano": "巴列卡诺",
    "RC Celta": "塞尔塔", "Deportivo Alavés": "阿拉维斯",
    "Real Valladolid": "瓦拉多利德", "UD Las Palmas": "拉斯帕尔马斯",
    "CD Leganés": "莱加内斯", "Real Oviedo": "皇家奥维耶多",
    "Girona": "赫罗纳", "Villarreal": "比利亚雷亚尔", "Mallorca": "马略卡",
    # 26/27 升班马
    "Málaga CF": "马拉加", "Levante UD": "莱万特",
    "Deportivo de A Coruña": "拉科鲁尼亚", "Real Racing Club": "桑坦德竞技",
    "Paris FC": "巴黎FC", "FC Schalke 04": "沙尔克04",
    "Hamburger SV": "汉堡", "SC Paderborn 07": "帕德博恩",
    "SV 07 Elversberg": "埃尔弗斯贝格", "Hull City": "赫尔城",
    "Le Mans": "勒芒", "Troyes": "特鲁瓦", "Rayo Vallecano": "巴列卡诺",
    "Espanyol": "西班牙人", "Osasuna": "奥萨苏纳", "Sevilla": "塞维利亚",
    "Bologna": "博洛尼亚", "Sassuolo": "萨索洛", "Juventus": "尤文图斯",
    "Valencia": "巴伦西亚", "Arsenal": "阿森纳", "Chelsea": "切尔西",
    # 意甲
    "Juventus FC": "尤文图斯", "Inter Milan": "国际米兰",
    "Inter": "国际米兰", "AC Milan": "AC米兰", "AC Milan ": "AC米兰",
    "SSC Napoli": "那不勒斯", "AS Roma": "罗马", "Roma": "罗马",
    "SS Lazio": "拉齐奥", "Lazio": "拉齐奥",
    "Atalanta": "亚特兰大", "ACF Fiorentina": "佛罗伦萨",
    "Bologna FC": "博洛尼亚", "Torino FC": "都灵",
    "Udinese Calcio": "乌迪内斯", "Genoa CFC": "热那亚",
    "Hellas Verona": "维罗纳", "US Lecce": "莱切",
    "Cagliari": "卡利亚里", "Empoli FC": "恩波利",
    "Monza": "蒙扎", "US Sassuolo": "萨索洛",
    "Sassuolo": "萨索洛", "Cremonese": "克雷莫纳",
    "Como": "科莫", "Pisa": "比萨",
    "Parma": "帕尔马", "Frosinone": "弗罗西诺内",
    "Venezia": "威尼斯", "Genoa": "热那亚",
    "Udinese": "乌迪内斯", "Empoli": "恩波利",
    "Fiorentina": "佛罗伦萨", "Lecce": "莱切",
    "Torino": "都灵", "Bologna": "博洛尼亚",
    "Salernitana": "萨勒尼塔纳", "Spezia": "斯佩齐亚",
    "US Salernitana": "萨勒尼塔纳",
    # 德甲
    "FC Bayern München": "拜仁慕尼黑", "Bayern Munich": "拜仁慕尼黑",
    "Borussia Dortmund": "多特蒙德", "RB Leipzig": "莱比锡红牛",
    "Bayer 04 Leverkusen": "勒沃库森", "Bayer Leverkusen": "勒沃库森",
    "Eintracht Frankfurt": "法兰克福", "VfB Stuttgart": "斯图加特",
    "Borussia Mönchengladbach": "门兴格拉德巴赫",
    "SV Werder Bremen": "云达不莱梅", "FC Augsburg": "奥格斯堡",
    "VfL Wolfsburg": "沃尔夫斯堡", "1. FSV Mainz 05": "美因茨",
    "SC Freiburg": "弗赖堡", "1. FC Union Berlin": "柏林联合",
    "1. FC Köln": "科隆", "1. FC Heidenheim": "海登海姆",
    "Holstein Kiel": "基尔", "FC St. Pauli": "圣保利",
    "VfL Bochum": "波鸿", "SV Darmstadt 98": "达姆施塔特",
    # 法甲
    "Paris Saint-Germain": "巴黎圣日耳曼", "Paris Saint Germain": "巴黎圣日耳曼",
    "Olympique de Marseille": "马赛", "Olympique Marseille": "马赛",
    "AS Monaco": "摩纳哥", "Monaco": "摩纳哥",
    "Olympique Lyonnais": "里昂", "Olympique Lyon": "里昂", "Lyon": "里昂",
    "LOSC Lille": "里尔", "Lille": "里尔",
    "Stade Rennais": "雷恩", "Stade Rennais FC": "雷恩", "Rennes": "雷恩",
    "OGC Nice": "尼斯", "Nice": "尼斯",
    "RC Lens": "朗斯", "Lens": "朗斯",
    "Stade de Reims": "兰斯", "Stade Reims": "兰斯", "Reims": "兰斯",
    "Stade Brestois 29": "布雷斯特", "Stade Brestois": "布雷斯特", "Brest": "布雷斯特",
    "Angers SCO": "昂热", "Angers": "昂热",
    "RC Strasbourg Alsace": "斯特拉斯堡", "Strasbourg": "斯特拉斯堡",
    "Toulouse FC": "图卢兹", "Toulouse": "图卢兹",
    "AS Saint-Étienne": "圣埃蒂安", "Saint-Étienne": "圣埃蒂安",
    "AJ Auxerre": "欧塞尔", "Auxerre": "欧塞尔",
    "FC Nantes": "南特", "Nantes": "南特",
    "Le Havre AC": "勒阿弗尔", "Le Havre": "勒阿弗尔",
    "Montpellier HSC": "蒙彼利埃", "Montpellier": "蒙彼利埃",
    "Lorient": "洛里昂", "Clermont": "克莱蒙",
    "Metz": "梅斯", "ESTAC Troyes": "特鲁瓦",
}


def sofa_team_to_cn(name: str) -> str:
    """ SofaScore 英文队名 → 中文，未匹配时返回原名 """
    if not name:
        return name
    if name in SOFA_TEAM_CN_MAP:
        return SOFA_TEAM_CN_MAP[name]
    # 尝试去掉 "FC"/"CF" 等后缀再匹配
    for suffix in [" FC", " CF", " AC", " AFC"]:
        if name.endswith(suffix):
            base = name[:-len(suffix)]
            if base in SOFA_TEAM_CN_MAP:
                return SOFA_TEAM_CN_MAP[base]
    return name  # 未匹配，返回原名（后续手动补映射）


def build_match_id_cn(date_str: str, home_cn: str, away_cn: str) -> str:
    """ 构造中文版 match_id（与 matches 表 match_id 格式一致）"""
    return f"{date_str}_{home_cn}_{away_cn}"


# ==================== 特征聚合核心 ====================

def load_sofascore_matches(conn: sqlite3.Connection) -> pd.DataFrame:
    """ 从 fbref_match_mapping 读取所有 SofaScore 比赛基础信息 """
    query = """
        SELECT fbref_match_id AS event_id,
               odds_match_id,
               home_team_cn AS home_team,
               away_team_cn AS away_team,
               league,
               match_date,
               fbref_score
        FROM fbref_match_mapping
        WHERE fbref_match_url LIKE '%sofascore%'
        ORDER BY match_date
    """
    df = read_sql(query, conn)
    if df.empty:
        return df
    df["match_date"] = pd.to_datetime(df["match_date"])
    # 英文队名 → 中文 → 归一化（C-20260823-024: 增加 normalize_team_name 确保队名自动归一化）
    df["home_team_cn"] = df["home_team"].apply(sofa_team_to_cn).apply(normalize_team_name)
    df["away_team_cn"] = df["away_team"].apply(sofa_team_to_cn).apply(normalize_team_name)
    # 构造中文版 match_id（用于与 matches 表 JOIN）
    df["match_id_cn"] = df.apply(
        lambda r: build_match_id_cn(r["match_date"].strftime("%Y-%m-%d"),
                                     r["home_team_cn"], r["away_team_cn"]),
        axis=1,
    )
    return df


def load_sofascore_player_stats(conn: sqlite3.Connection) -> pd.DataFrame:
    """ 从 match_player_stats 读取所有 SofaScore 球员统计 """
    query = """
        SELECT s.fbref_match_id AS event_id,
               s.match_id,
               s.team,
               s.player_name,
               s.position,
               s.is_starter,
               s.minutes_played,
               s.rating,
               s.expected_goals,
               s.expected_assists,
               s.goals,
               s.assists,
               s.accurate_pass_sofa,
               s.total_pass_sofa,
               s.accurate_long_balls,
               s.total_long_balls,
               s.accurate_crosses,
               s.total_crosses,
               s.successful_dribbles,
               s.total_dribbles,
               s.total_tackles,
               s.interceptions,
               s.duels_won,
               s.duels_total,
               s.aerials_won_total,
               s.aerials_total,
               s.ball_recoveries_sofa,
               s.possession_lost,
               s.big_chances_created,
               s.big_chances_missed,
               s.meters_covered_sprinting_km,
               s.meters_covered_high_speed_running_km,
               s.meters_covered_running_km,
               s.meters_covered_jogging_km,
               s.meters_covered_walking_km,
               s.gk_saves_sofa,
               s.goals_prevented,
               s.clearances,
               s.key_passes_sofa,
               s.touches_sofa,
               s.stats_source
        FROM match_player_stats s
        WHERE s.stats_source = 'sofascore'
    """
    df = read_sql(query, conn)
    return df


def load_sofascore_lineups(conn: sqlite3.Connection) -> pd.DataFrame:
    """ 从 match_lineups 读取阵容（阵型/队长/换人信息） """
    query = """
        SELECT l.fbref_match_id AS event_id,
               l.match_id,
               l.team,
               l.formation,
               l.player_name,
               l.is_starter,
               l.captain,
               l.sub_in_time,
               l.sub_out_time,
               l.sub_reason,
               l.minutes_played
        FROM match_lineups l
        WHERE l.fbref_match_id IN (
            SELECT fbref_match_id FROM fbref_match_mapping
            WHERE fbref_match_url LIKE '%sofascore%'
        )
    """
    df = read_sql(query, conn)
    return df


def time_decay_weights(dates: pd.Series, ref_date: pd.Timestamp,
                       half_life_days: int = 21) -> np.ndarray:
    """ 指数时间衰减权重（半衰期可配，默认21天） """
    if len(dates) == 0:
        return np.array([])
    days_diff = (ref_date - dates).dt.days.values.astype(float)
    days_diff = np.maximum(days_diff, 0)
    weights = np.exp(-days_diff * np.log(2) / half_life_days)
    weights = np.maximum(weights, 0.01)
    total = weights.sum()
    if total > 0:
        weights = weights / total
    return weights


# 传球成功率下限门禁：职业队近5场加权传球成功率不可能低于 50%，低于此值视为采集端脏数据
# （如 accurate_pass/total_pass 字段错位产生 0.01 占位），置 0 走下游「缺失」约定，避免污染模型特征。
PASS_SR_MIN = 0.5


def safe_div(num: float, den: float) -> float:
    """ 安全除法，分母为0返回0 """
    if den is None or den == 0 or pd.isna(den):
        return 0.0
    return float(num) / float(den)


def precompute_team_views(
    matches_df: pd.DataFrame,
    stats_df: pd.DataFrame,
    lineups_df: pd.DataFrame,
) -> Tuple[Dict[str, pd.DataFrame], Dict[str, pd.DataFrame], Dict[str, pd.DataFrame]]:
    """按队名预分组，避免 aggregate_team_history 每次调用全表扫描。

    返回:
      matches_by_team: 队名 -> 该队比赛（event_id, match_date）按日期升序
      stats_by_team:   队名 -> 该队全部球员统计（保留 team 列便于后续仅按 event_id 过滤）
      lineups_by_team: 队名 -> 该队全部阵容
    """
    # 比赛长表：每行 = 一支球队的一次出场（主客各展开一行）
    home = matches_df[["event_id", "home_team", "match_date"]].rename(columns={"home_team": "team"})
    away = matches_df[["event_id", "away_team", "match_date"]].rename(columns={"away_team": "team"})
    long_m = pd.concat([home, away], ignore_index=True).sort_values(["team", "match_date"])
    matches_by_team = {
        t: g[["event_id", "match_date"]].reset_index(drop=True)
        for t, g in long_m.groupby("team", sort=False)
    }
    stats_by_team = {t: g for t, g in stats_df.groupby("team", sort=False)}
    lineups_by_team = {t: g for t, g in lineups_df.groupby("team", sort=False)}
    return matches_by_team, stats_by_team, lineups_by_team


def aggregate_team_history(
    team_name: str,
    current_date: pd.Timestamp,
    matches_by_team: Dict[str, pd.DataFrame],
    stats_by_team: Dict[str, pd.DataFrame],
    lineups_by_team: Dict[str, pd.DataFrame],
    n_recent: int = 5,
    half_life_days: int = 21,
) -> Dict[str, float]:
    """
    聚合某支球队在 current_date 之前 N 场的球员统计 → 球队级特征

    参数:
      team_name: SofaScore 英文队名
      current_date: 当前比赛日期
      matches_by_team: 按队名预分组+按日期排序的比赛表（event_id, match_date），
                       每队的行已按 match_date 升序排列，仅需 tail(N) 取近 N 场
      stats_by_team: 按队名预分组的球员统计表（event_id, team, ...）
      lineups_by_team: 按队名预分组的阵容表（event_id, team, formation）
      n_recent: 取近 N 场
      half_life_days: 时间衰减半衰期
    返回:
      34 维特征字典（23 维全队 + 11 维位置-specific D/M/F）

    性能说明（C-20260907-003）:
      原实现每次调用都对全量 matches_df/stats_df（18000+/729000 行）做布尔掩码 +
      isin 过滤，复杂度 O(场次 × 全表)，全表重建 >1h。改为调用前按队名分组一次，
      本函数只在该队子表上 isin（每次 ≤ 数百行），复杂度 O(场次 × 该队行数)。
    """
    features: Dict[str, float] = {}

    # === 1. 找该队此前 N 场比赛 ===
    tm = matches_by_team.get(team_name)
    if tm is None or tm.empty:
        return _empty_features()
    # matches_by_team 每队已按 match_date 升序，tail(N) 即最近 N 场
    team_matches = tm[tm["match_date"] < current_date].tail(n_recent)

    if team_matches.empty:
        # 无历史数据，返回全 0 特征
        return _empty_features()

    # === 2. 取这些比赛的球员统计 ===
    event_ids = team_matches["event_id"].tolist()
    st = stats_by_team.get(team_name)
    if st is None or st.empty:
        return _empty_features()
    team_stats = st[
        st["event_id"].isin(event_ids)
        & (st["minutes_played"] > 0)  # 仅出场球员
    ].copy()

    if team_stats.empty:
        return _empty_features()

    # === 3. 时间衰减权重（按比赛日期） ===
    # 为每场比赛分配权重
    match_weights = {}
    for _, m in team_matches.iterrows():
        match_weights[m["event_id"]] = 0.0  # 先初始化
    w_arr = time_decay_weights(team_matches["match_date"], current_date, half_life_days)
    for eid, w in zip(team_matches["event_id"].tolist(), w_arr):
        match_weights[eid] = w

    # 将权重映射到每行球员统计
    team_stats["weight"] = team_stats["event_id"].map(match_weights)

    # === 4. 聚合 22 维特征 ===
    w = team_stats["weight"]

    # (1) 评分：首发11人加权均值
    starters = team_stats[team_stats["is_starter"] == 1]
    if not starters.empty and starters["rating"].notna().any():
        sw = starters["weight"]
        features["sofa_rat_5g"] = float(
            (starters["rating"].fillna(0) * sw).sum() / sw.sum()
        ) if sw.sum() > 0 else 0.0
    else:
        features["sofa_rat_5g"] = 0.0

    # (2) xG：全队加权总和/场次
    features["sofa_xg_5g"] = float(
        (team_stats["expected_goals"].fillna(0) * w).sum() / len(team_matches)
    )

    # (3) xA：全队加权总和/场次
    features["sofa_xa_5g"] = float(
        (team_stats["expected_assists"].fillna(0) * w).sum() / len(team_matches)
    )

    # (4) 传球成功率：总和比率（脏数据门禁：< PASS_SR_MIN 视为采集异常 → 0 缺失占位）
    _pass_sr = safe_div(
        (team_stats["accurate_pass_sofa"].fillna(0) * w).sum(),
        (team_stats["total_pass_sofa"].fillna(0) * w).sum(),
    )
    features["sofa_pass_sr_5g"] = _pass_sr if _pass_sr >= PASS_SR_MIN else 0.0

    # (5) 长传成功率
    features["sofa_longball_sr_5g"] = safe_div(
        (team_stats["accurate_long_balls"].fillna(0) * w).sum(),
        (team_stats["total_long_balls"].fillna(0) * w).sum(),
    )

    # (6) 传中成功率
    features["sofa_cross_sr_5g"] = safe_div(
        (team_stats["accurate_crosses"].fillna(0) * w).sum(),
        (team_stats["total_crosses"].fillna(0) * w).sum(),
    )

    # (7) 过人成功率
    features["sofa_dribble_sr_5g"] = safe_div(
        (team_stats["successful_dribbles"].fillna(0) * w).sum(),
        (team_stats["total_dribbles"].fillna(0) * w).sum(),
    )

    # (8) 场均抢断
    features["sofa_tackle_5g"] = float(
        (team_stats["total_tackles"].fillna(0) * w).sum() / len(team_matches)
    )

    # (9) 场均拦截
    features["sofa_interception_5g"] = float(
        (team_stats["interceptions"].fillna(0) * w).sum() / len(team_matches)
    )

    # (10) 对抗成功率
    features["sofa_duel_sr_5g"] = safe_div(
        (team_stats["duels_won"].fillna(0) * w).sum(),
        (team_stats["duels_total"].fillna(0) * w).sum(),
    )

    # (11) 空中对抗成功率
    features["sofa_aerial_sr_5g"] = safe_div(
        (team_stats["aerials_won_total"].fillna(0) * w).sum(),
        (team_stats["aerials_total"].fillna(0) * w).sum(),
    )

    # (12) 场均抢回球权
    features["sofa_recovery_5g"] = float(
        (team_stats["ball_recoveries_sofa"].fillna(0) * w).sum() / len(team_matches)
    )

    # (13) 场均丢球
    features["sofa_poss_lost_5g"] = float(
        (team_stats["possession_lost"].fillna(0) * w).sum() / len(team_matches)
    )

    # (14) 场均创造绝佳机会
    features["sofa_big_chance_c_5g"] = float(
        (team_stats["big_chances_created"].fillna(0) * w).sum() / len(team_matches)
    )

    # (15) 场均浪费绝佳机会
    features["sofa_big_chance_m_5g"] = float(
        (team_stats["big_chances_missed"].fillna(0) * w).sum() / len(team_matches)
    )

    # (16) 场均冲刺距离
    features["sofa_sprint_km_5g"] = float(
        (team_stats["meters_covered_sprinting_km"].fillna(0) * w).sum() / len(team_matches)
    )

    # (17) 场均高速跑距离
    features["sofa_hsr_km_5g"] = float(
        (team_stats["meters_covered_high_speed_running_km"].fillna(0) * w).sum() / len(team_matches)
    )

    # (18) 场均总跑动距离（5种跑动类型之和）
    total_dist = (
        team_stats["meters_covered_walking_km"].fillna(0)
        + team_stats["meters_covered_jogging_km"].fillna(0)
        + team_stats["meters_covered_running_km"].fillna(0)
        + team_stats["meters_covered_high_speed_running_km"].fillna(0)
        + team_stats["meters_covered_sprinting_km"].fillna(0)
    )
    features["sofa_total_dist_km_5g"] = float(
        (total_dist * w).sum() / len(team_matches)
    )

    # (19) 门将扑救率（仅统计 position=G 的球员）
    gk_stats = team_stats[team_stats["position"] == "G"]
    if not gk_stats.empty and gk_stats["gk_saves_sofa"].notna().any():
        # 场均扑救数
        features["sofa_gk_saves_5g"] = float(
            (gk_stats["gk_saves_sofa"].fillna(0) * gk_stats["weight"]).sum() / len(team_matches)
        )
        # 场均扑出球（goalsPrevented 越高越好）
        features["sofa_gk_goals_prev_5g"] = float(
            (gk_stats["goals_prevented"].fillna(0) * gk_stats["weight"]).sum() / len(team_matches)
        )
    else:
        features["sofa_gk_saves_5g"] = 0.0
        features["sofa_gk_goals_prev_5g"] = 0.0

    # (20) 场均解围
    features["sofa_clearance_5g"] = float(
        (team_stats["clearances"].fillna(0) * w).sum() / len(team_matches)
    )

    # (21) 阵型一致性：近 N 场使用不同阵型数的倒数（1=完全一致，0.5=两种阵型）
    lu = lineups_by_team.get(team_name)
    if lu is None or lu.empty:
        team_lineups = lineups_df.iloc[0:0]
    else:
        team_lineups = lu[lu["event_id"].isin(event_ids)]
    formations = team_lineups.drop_duplicates(subset=["event_id", "formation"])[
        "formation"
    ].dropna()
    unique_formations = formations.nunique()
    features["sofa_formation_consistency"] = (
        1.0 / unique_formations if unique_formations > 0 else 0.0
    )

    # (22) 首发球员评分标准差（衡量团队稳定性）
    if not starters.empty and starters["rating"].notna().any():
        features["sofa_rat_std_5g"] = float(starters["rating"].std()) if len(starters) > 1 else 0.0
    else:
        features["sofa_rat_std_5g"] = 0.0

    # === P2-10: 位置-specific 聚合（D/M/F 三条线专职信号，G 已在第19维） ===
    # 目的：把「全队简单加权平均」拆分为锋线进攻产出 / 中场组织量 / 后防防守硬度，
    #       让模型看到各条线的强弱分解信号（而非被非本职球员稀释）。
    # 选型依据（2026-08-28 数据探查）：优先用缺失率 <40% 的字段（goals/assists/
    #   interceptions 0%、touches/total_pass 28%、duels 33~38%、rating 29%）；
    #   高缺失字段（xG 81%、key_passes 72%、accurate_pass 83%）在位置级聚合会放大噪声，暂不纳入。
    # 归一方式与全队特征一致：按时间衰减权重 w 加权，除以 len(team_matches) 得场均。

    # (23) 前锋线（F）：进攻终结与参与度
    fw_stats = team_stats[team_stats["position"] == "F"]
    if not fw_stats.empty:
        fw_w = fw_stats["weight"]
        features["sofa_fw_goals_5g"] = float(
            (fw_stats["goals"].fillna(0) * fw_w).sum() / len(team_matches))
        features["sofa_fw_assists_5g"] = float(
            (fw_stats["assists"].fillna(0) * fw_w).sum() / len(team_matches))
        features["sofa_fw_touches_5g"] = float(
            (fw_stats["touches_sofa"].fillna(0) * fw_w).sum() / len(team_matches))
        features["sofa_fw_rat_5g"] = float(
            (fw_stats["rating"].fillna(0) * fw_w).sum() / fw_w.sum()) if fw_w.sum() > 0 else 0.0
    else:
        features["sofa_fw_goals_5g"] = 0.0
        features["sofa_fw_assists_5g"] = 0.0
        features["sofa_fw_touches_5g"] = 0.0
        features["sofa_fw_rat_5g"] = 0.0

    # (24) 中场线（M）：组织量与传导
    mf_stats = team_stats[team_stats["position"] == "M"]
    if not mf_stats.empty:
        mf_w = mf_stats["weight"]
        features["sofa_mf_pass_5g"] = float(
            (mf_stats["total_pass_sofa"].fillna(0) * mf_w).sum() / len(team_matches))
        features["sofa_mf_touches_5g"] = float(
            (mf_stats["touches_sofa"].fillna(0) * mf_w).sum() / len(team_matches))
        features["sofa_mf_rat_5g"] = float(
            (mf_stats["rating"].fillna(0) * mf_w).sum() / mf_w.sum()) if mf_w.sum() > 0 else 0.0
    else:
        features["sofa_mf_pass_5g"] = 0.0
        features["sofa_mf_touches_5g"] = 0.0
        features["sofa_mf_rat_5g"] = 0.0

    # (25) 后卫线（D）：防守硬度
    df_stats = team_stats[team_stats["position"] == "D"]
    if not df_stats.empty:
        df_w = df_stats["weight"]
        features["sofa_df_interception_5g"] = float(
            (df_stats["interceptions"].fillna(0) * df_w).sum() / len(team_matches))
        features["sofa_df_tackle_5g"] = float(
            (df_stats["total_tackles"].fillna(0) * df_w).sum() / len(team_matches))
        features["sofa_df_duel_sr_5g"] = safe_div(
            (df_stats["duels_won"].fillna(0) * df_w).sum(),
            (df_stats["duels_total"].fillna(0) * df_w).sum())
        features["sofa_df_rat_5g"] = float(
            (df_stats["rating"].fillna(0) * df_w).sum() / df_w.sum()) if df_w.sum() > 0 else 0.0
    else:
        features["sofa_df_interception_5g"] = 0.0
        features["sofa_df_tackle_5g"] = 0.0
        features["sofa_df_duel_sr_5g"] = 0.0
        features["sofa_df_rat_5g"] = 0.0

    return features


def _empty_features() -> Dict[str, float]:
    """ 无历史数据时返回全 0 特征 """
    keys = [
        "sofa_rat_5g", "sofa_xg_5g", "sofa_xa_5g",
        "sofa_pass_sr_5g", "sofa_longball_sr_5g", "sofa_cross_sr_5g",
        "sofa_dribble_sr_5g", "sofa_tackle_5g", "sofa_interception_5g",
        "sofa_duel_sr_5g", "sofa_aerial_sr_5g", "sofa_recovery_5g",
        "sofa_poss_lost_5g", "sofa_big_chance_c_5g", "sofa_big_chance_m_5g",
        "sofa_sprint_km_5g", "sofa_hsr_km_5g", "sofa_total_dist_km_5g",
        "sofa_gk_saves_5g", "sofa_gk_goals_prev_5g", "sofa_clearance_5g",
        "sofa_formation_consistency", "sofa_rat_std_5g",
        # P2-10: 位置-specific 特征（D/M/F 三条线）
        "sofa_fw_goals_5g", "sofa_fw_assists_5g", "sofa_fw_touches_5g", "sofa_fw_rat_5g",
        "sofa_mf_pass_5g", "sofa_mf_touches_5g", "sofa_mf_rat_5g",
        "sofa_df_interception_5g", "sofa_df_tackle_5g", "sofa_df_duel_sr_5g", "sofa_df_rat_5g",
    ]
    return {k: 0.0 for k in keys}


def build_sofascore_pre_match_features(
    conn: sqlite3.Connection,
    n_recent: int = 5,
    half_life_days: int = 21,
    progress_every: int = 50,
) -> pd.DataFrame:
    """
    主入口：为每场 SofaScore 比赛生成主客各 22 维赛前特征

    返回 DataFrame 列:
      event_id, match_id_cn, match_date, league,
      home_team_cn, away_team_cn,
      sofa_rat_5g_home, sofa_rat_5g_away, ... (22 × 2 = 44 维特征)
    """
    print(f"[SofaScore特征] 加载数据...")
    matches_df = load_sofascore_matches(conn)
    if matches_df.empty:
        print("[SofaScore特征] ❌ fbref_match_mapping 中无 SofaScore 比赛")
        return pd.DataFrame()

    stats_df = load_sofascore_player_stats(conn)
    lineups_df = load_sofascore_lineups(conn)
    print(f"[SofaScore特征] 比赛数={len(matches_df)} | 球员统计行={len(stats_df)} | 阵容行={len(lineups_df)}")

    # 按队名预分组（C-20260907-003：避免每场对全表扫描，全表重建由 >1h 降至分钟级）
    print("[SofaScore特征] 预分组建表...")
    matches_by_team, stats_by_team, lineups_by_team = precompute_team_views(
        matches_df, stats_df, lineups_df)
    print(f"[SofaScore特征] 预分组完成: {len(matches_by_team)} 队 | {len(stats_by_team)} 队统计 | {len(lineups_by_team)} 队阵容")

    rows: List[Dict[str, Any]] = []
    total = len(matches_df)

    for idx, match in matches_df.iterrows():
        if (idx + 1) % progress_every == 0 or idx == 0:
            pct = (idx + 1) / total * 100
            print(f"[SofaScore特征] 进度 {idx+1}/{total} ({pct:.1f}%) | "
                  f"{match['home_team']} vs {match['away_team']}")

        home_features = aggregate_team_history(
            match["home_team"], match["match_date"],
            matches_by_team, stats_by_team, lineups_by_team,
            n_recent, half_life_days,
        )
        away_features = aggregate_team_history(
            match["away_team"], match["match_date"],
            matches_by_team, stats_by_team, lineups_by_team,
            n_recent, half_life_days,
        )

        row: Dict[str, Any] = {
            "event_id": match["event_id"],
            "match_id_cn": match["match_id_cn"],
            "match_date": match["match_date"].strftime("%Y-%m-%d"),
            "league": match["league"],
            "home_team_cn": normalize_team_name(match["home_team_cn"]),
            "away_team_cn": normalize_team_name(match["away_team_cn"]),
        }
        # 主队特征加 _home 后缀，客队加 _away
        for k, v in home_features.items():
            row[f"{k}_home"] = v
        for k, v in away_features.items():
            row[f"{k}_away"] = v

        rows.append(row)

    result_df = pd.DataFrame(rows)
    print(f"[SofaScore特征] ✅ 生成完成 | {len(result_df)} 场比赛 × {len(result_df.columns)} 列")

    # P0-3: 追加球员可用性特征 (24维)
    try:
        from features.player_availability_features import PlayerAvailabilityFeatures
        pa_gen = PlayerAvailabilityFeatures(db_path=DB_PATH)
        pa_df = pa_gen.generate_all(
            n_recent=n_recent,
            half_life_days=half_life_days,
            progress_every=progress_every * 5,
        )
        pa_gen.close()
        # 仅保留特征列 + event_id，避免重复键
        pa_feature_cols = ["event_id"] + PlayerAvailabilityFeatures.feature_keys()
        pa_df = pa_df[pa_feature_cols]
        result_df = result_df.merge(pa_df, on="event_id", how="left")
        # 填充缺失值
        for col in PlayerAvailabilityFeatures.feature_keys():
            if col in result_df.columns:
                result_df[col] = result_df[col].fillna(0.0)
        print(f"[SofaScore特征] ✅ P0-3 PA特征已追加 | {len(result_df)} 场比赛 × {len(result_df.columns)} 列")
    except Exception as e:
        print(f"[SofaScore特征] ⚠️ P0-3 PA特征追加失败: {e}")

    return result_df


def save_to_db(conn: Any, df: pd.DataFrame) -> None:
    """ 将特征表写入 odds.db 的 sofascore_team_features 表 """
    cursor = conn.cursor()
    cursor.execute("DROP TABLE IF EXISTS sofascore_team_features")
    cols_sql = ", ".join([f'"{c}" {"REAL" if c not in ("event_id","match_id_cn","match_date","league","home_team_cn","away_team_cn") else "TEXT"}' for c in df.columns])
    cursor.execute(f"CREATE TABLE sofascore_team_features ({cols_sql})")
    write_dataframe(conn, df, "sofascore_team_features")
    print(f"[SofaScore特征] ✅ 已写入 odds.db.sofascore_team_features 表 ({len(df)} 行)")


def main() -> int:
    parser = argparse.ArgumentParser(description="SofaScore 球员级 → 赛前球队级特征聚合")
    parser.add_argument("--db", type=str, default=str(DB_PATH), help="odds.db 路径")
    parser.add_argument("--n-recent", type=int, default=5, help="近 N 场窗口（默认5）")
    parser.add_argument("--half-life", type=int, default=21, help="时间衰减半衰期天数（默认21）")
    parser.add_argument("--output", type=str, default="db", choices=["db", "csv"],
                        help="输出方式：db=写回数据库 / csv=输出CSV文件")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"[ERROR] DB not found: {db_path}")
        return 2

    conn = connect(db_path=db_path)
    try:
        df = build_sofascore_pre_match_features(
            conn, n_recent=args.n_recent, half_life_days=args.half_life,
        )
        if df.empty:
            return 1

        if args.output == "db":
            save_to_db(conn, df)
        else:
            out_path = db_path.parent / f"sofascore_team_features_n{args.n_recent}.csv"
            df.to_csv(out_path, index=False, encoding="utf-8-sig")
            print(f"[SofaScore特征] ✅ 已输出 CSV: {out_path}")

        # 打印特征非零率概览
        feature_cols = [c for c in df.columns if c.startswith("sofa_")]
        print(f"\n[SofaScore特征] {len(feature_cols)} 维特征非零率概览:")
        for c in feature_cols[:10]:  # 只打前10个
            nonzero = (df[c] != 0).sum()
            print(f"  {c:35s} 非零率={nonzero/len(df)*100:5.1f}% ({nonzero}/{len(df)})")
        if len(feature_cols) > 10:
            print(f"  ... 共 {len(feature_cols)} 维特征")

    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
