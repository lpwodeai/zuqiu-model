"""
西甲第1轮第二批赛前预测脚本 (T-005 v3 让球预测 + 赔率驱动 WDL/比分/总进球)
==============================================================================
比赛: R. Racing Club vs Villarreal, Espanyol vs Levante

预测方法:
    - 让球胜平负: T-005 v3 两阶段模型 (draw_detector + direction_predictor, 71维特征)
    - 胜平负 (WDL): 赔率隐含概率 + 西甲 argmax 阈值 (factor=0.0)
    - 比分: 竞彩网比分赔率隐含概率 + Poisson 分布
    - 总进球: 总进球赔率隐含概率 + Poisson 校准

特征工程:
    - HCP 赔率特征 (15维): 从让球赔率实时计算
    - WDL 平局特征 (3维): 从 WDL 赔率实时计算
    - 球队近期状态/休息/红黄牌/对手Lag (43维): 从历史特征矩阵提取该球队最近一场比赛快照
    - Elo 特征 (10维): 从 t005v2_final_elo_ratings.json 加载并计算
    - 缺失特征: 用历史特征矩阵中位数填充 (如新升级球队无历史数据)
"""
import json, os, sys, re, warnings, pickle
import numpy as np
import pandas as pd
from datetime import datetime, timezone, timedelta
from scipy.stats import poisson

warnings.filterwarnings('ignore')

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
DATA_DIR = os.path.join(PROJECT_DIR, "data")
DB_PATH = os.path.join(DATA_DIR, "odds.db")
os.chdir(PROJECT_DIR)

# 添加 scripts 目录到 path (复用 T-005 v3 特征工程模块)
sys.path.insert(0, SCRIPT_DIR)

CN_TZ = timezone(timedelta(hours=8))

# ============================================================
# 配置
# ============================================================
ODDS_TXT = os.path.join(DATA_DIR, "西甲2026-2027赛季完整时序赔率.txt")
PRE_MATCH_JSON = os.path.join(DATA_DIR, "pre_match_laliga_r1_2.json")
REPORT_DIR = os.path.join(PROJECT_DIR, "docs")

# T-005 v3 模型参数 (与 deploy_t005v3_final.py 一致)
T005V3_TEMPERATURE = 0.800
T005V3_THRESHOLD = 0.500
T005V3_DRAW_MODEL_PATH = os.path.join(ASSETS_DIR, 't005v3_draw_detector.pkl')
T005V3_DIR_MODEL_PATH = os.path.join(ASSETS_DIR, 't005v3_direction_predictor.pkl')
T005V3_ELO_PATH = os.path.join(ASSETS_DIR, 't005v2_final_elo_ratings.json')

# 西甲 WDL 阈值: factor=0.0 (argmax) — 与 prediction-service.js 一致
DRAW_THRESHOLD_FACTOR = 0.0

# 让球结果标签
HCP_LABEL_NAMES = ['上盘赢', '走水', '下盘赢']

# T-005 v3 使用的 71 维特征列 (61 核心特征 + 10 Elo 特征)
T005V3_CORE_FEATURES = [
    # HCP 赔率特征 (15维)
    "hcp_prob_win", "hcp_prob_draw", "hcp_prob_lose",
    "hcp_home_strength", "hcp_draw_risk", "hcp_confidence",
    "hcp_entropy", "hcp_expected_value", "hcp_volatility",
    "hcp_market_sentiment", "hcp_underdog_ratio", "hcp_favorite_margin",
    "hcp_balance", "hcp_upset_risk", "hcp_odds_skew",
    # WDL 平局特征 (3维)
    "wdl_draw_odds", "wdl_draw_prob", "draw_divergence",
    # 球队近期状态 (12维)
    "home_recent_wins", "home_recent_draws", "home_recent_losses",
    "home_recent_goals_for", "home_recent_goals_against", "home_recent_points",
    "away_recent_wins", "away_recent_draws", "away_recent_losses",
    "away_recent_goals_for", "away_recent_goals_against", "away_recent_points",
    # 休息天数 (3维)
    "rest_days_home", "rest_days_away", "rest_days_diff",
    # 红黄牌风险 (4维)
    "home_yellow_cards_l5", "home_red_cards_l5",
    "away_yellow_cards_l5", "away_red_cards_l5",
    # 对手调整 Lag 特征 (24维)
    "home_hcp_draw_vs_stronger_l5", "home_hcp_draw_vs_similar_l5",
    "home_hcp_draw_vs_weaker_l5", "home_hcp_draw_vs_all_l10",
    "away_hcp_draw_vs_stronger_l5", "away_hcp_draw_vs_similar_l5",
    "away_hcp_draw_vs_weaker_l5", "away_hcp_draw_vs_all_l10",
    "home_hcp_draw_at_give1_l10", "home_hcp_draw_at_get1_l10",
    "home_hcp_draw_at_give2_l10",
    "away_hcp_draw_at_give1_l10", "away_hcp_draw_at_get1_l10",
    "away_hcp_draw_at_give2_l10",
    "h2h_hcp_draw_rate", "h2h_hcp_draw_count", "h2h_total_matches",
    "h2h_last5_hcp_draws", "home_h2h_hcp_draw_rate", "away_h2h_hcp_draw_rate",
    "elo_gap_abs", "hcp_draw_prob_rank", "opponent_season_draw_rate",
    "market_draw_std",
]

T005V3_ELO_FEATURES = [
    "home_elo", "away_elo", "elo_diff", "elo_ratio",
    "elo_home_expected", "elo_away_expected", "elo_draw_prob",
    "home_elo_momentum", "away_elo_momentum", "elo_confidence",
]

T005V3_ALL_FEATURES = T005V3_CORE_FEATURES + T005V3_ELO_FEATURES  # 71 维

# 两场比赛定义
MATCHES = [
    {
        'id': 'match_1',
        'event_id': '16421061',
        'home_team': 'R. Racing Club',
        'away_team': 'Villarreal',
        'home_team_cn': '桑坦德竞技',
        'away_team_cn': '比利亚雷亚尔',
        'match_time': '2026-08-16 23:00',
    },
    {
        'id': 'match_2',
        'event_id': '16421053',
        'home_team': 'Espanyol',
        'away_team': 'Levante',
        'home_team_cn': '西班牙人',
        'away_team_cn': '莱万特',
        'match_time': '2026-08-17 01:00',
    },
]

# ============================================================
# 赔率 TXT 解析器
# ============================================================
def parse_odds_txt(txt_path):
    """解析完整赔率txt，提取比赛赔率数据"""
    print(f"解析赔率文件: {txt_path}")
    with open(txt_path, 'r', encoding='utf-8') as f:
        content = f.read()

    block_pattern = r'(\d{4}/\d{4} Regular Season 第\d+轮 \d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\n([\s\S]*?)(?=\d{4}/\d{4} Regular Season|$)'
    blocks = re.findall(block_pattern, content)

    matches_odds = {}

    for header, block_body in blocks:
        m_dt = re.search(r'第(\d+)轮 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})', header)
        round_num = m_dt.group(1) if m_dt else '1'
        match_datetime = m_dt.group(2) if m_dt else ''

        # 提取球队（第二行: "西甲 La Liga   Team1    Team2"）
        team_line = re.search(r'西甲 La Liga\s+(.+?)\s{2,}(.+)', block_body)
        if not team_line:
            team_line = re.search(r'西甲 La Liga\s+(\S[\s\S]*?\S)\s{2,}(\S[\s\S]*?\S)', block_body)
        if team_line:
            home_short = team_line.group(1).strip()
            away_short = team_line.group(2).strip()
        else:
            continue

        match_key = f"{home_short}_{away_short}"
        odds_data = {
            'round': round_num,
            'match_datetime': match_datetime,
            'home_short': home_short,
            'away_short': away_short,
            'wdl_odds': {'records': [], 'open': {}, 'close': {}},
            'handicap_odds': {'line': -1, 'records': [], 'open': {}, 'close': {}},
            'score_odds': {'records': []},
            'tg_odds': {'records': []},
        }

        # 1. 胜平负固定奖金
        wdl_pattern = r'胜平负固定奖金\s*\n发布时间\s*胜\s*平\s*负\s*\n([\s\S]*?)\n\n'
        wdl_match = re.search(wdl_pattern, block_body)
        if wdl_match:
            wdl_lines = wdl_match.group(1).strip().split('\n')
            for line in wdl_lines:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 4:
                    time_str = parts[0] + ' ' + parts[1]
                    try:
                        win = float(parts[2])
                        draw = float(parts[3])
                        lose = float(parts[4])
                        record = {'time': time_str, 'win': win, 'draw': draw, 'lose': lose}
                        odds_data['wdl_odds']['records'].append(record)
                    except (ValueError, IndexError):
                        continue
            if odds_data['wdl_odds']['records']:
                odds_data['wdl_odds']['open'] = odds_data['wdl_odds']['records'][0]
                odds_data['wdl_odds']['close'] = odds_data['wdl_odds']['records'][-1]

        # 2. 让球胜平负固定奖金
        hdp_pattern = r'让球胜平负固定奖金\s*\n让球([+\-]?\d+)\s*\n发布时间\s*胜\s*平\s*负\s*\n([\s\S]*?)\n\n'
        hdp_match = re.search(hdp_pattern, block_body)
        if hdp_match:
            odds_data['handicap_odds']['line'] = int(hdp_match.group(1))
            hdp_lines = hdp_match.group(2).strip().split('\n')
            for line in hdp_lines:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 4:
                    time_str = parts[0] + ' ' + parts[1]
                    try:
                        win = float(parts[2])
                        draw = float(parts[3])
                        lose = float(parts[4])
                        record = {'time': time_str, 'win': win, 'draw': draw, 'lose': lose}
                        odds_data['handicap_odds']['records'].append(record)
                    except (ValueError, IndexError):
                        continue
            if odds_data['handicap_odds']['records']:
                odds_data['handicap_odds']['open'] = odds_data['handicap_odds']['records'][0]
                odds_data['handicap_odds']['close'] = odds_data['handicap_odds']['records'][-1]

        # 3. 比分固定奖金
        score_pattern = r'比分固定奖金\s*\n发布时间\s*([\d\-]+)\s+([\d:]+)\s*\n([\s\S]*?)(?=\n发布时间|\n总进球|\Z)'
        for sm in re.finditer(score_pattern, block_body):
            pub_date = sm.group(1)
            pub_time = sm.group(2).strip()
            score_body = sm.group(3)
            rec = {
                'pub_time': f"{pub_date} {pub_time}",
                'win_odds': {},
                'draw_odds': {},
                'lose_odds': {},
            }

            lines = score_body.strip().split('\n')
            if len(lines) >= 6:
                win_scores = [s for s in re.split(r'\t+', lines[0].strip()) if s]
                win_values = [s for s in re.split(r'\t+', lines[1].strip()) if s]
                if len(win_scores) == len(win_values):
                    for sc, val in zip(win_scores, win_values):
                        try:
                            rec['win_odds'][sc.strip()] = float(val)
                        except ValueError:
                            pass

                draw_scores = [s for s in re.split(r'\t+', lines[2].strip()) if s]
                draw_values = [s for s in re.split(r'\t+', lines[3].strip()) if s]
                if len(draw_scores) == len(draw_values):
                    for sc, val in zip(draw_scores, draw_values):
                        try:
                            rec['draw_odds'][sc.strip()] = float(val)
                        except ValueError:
                            pass

                lose_scores = [s for s in re.split(r'\t+', lines[4].strip()) if s]
                lose_values = [s for s in re.split(r'\t+', lines[5].strip()) if s]
                if len(lose_scores) == len(lose_values):
                    for sc, val in zip(lose_scores, lose_values):
                        try:
                            rec['lose_odds'][sc.strip()] = float(val)
                        except ValueError:
                            pass

            odds_data['score_odds']['records'].append(rec)

        # 4. 总进球固定奖金
        tg_pattern = r'总进球固定奖金\s*\n发布时间\s*0\s*1\s*2\s*3\s*4\s*5\s*6\s*7\+\s*\n([\s\S]*?)(?=\n\n|\Z)'
        tg_match = re.search(tg_pattern, block_body)
        if tg_match:
            tg_lines = tg_match.group(1).strip().split('\n')
            for line in tg_lines:
                parts = re.split(r'\s+', line.strip())
                if len(parts) >= 9:
                    try:
                        record = {
                            'time': parts[0] + ' ' + parts[1],
                            'o0': float(parts[2]),
                            'o1': float(parts[3]),
                            'o2': float(parts[4]),
                            'o3': float(parts[5]),
                            'o4': float(parts[6]),
                            'o5': float(parts[7]),
                            'o6': float(parts[8]),
                            'o7': float(parts[9]) if len(parts) > 9 else 0.0,
                        }
                        odds_data['tg_odds']['records'].append(record)
                    except (ValueError, IndexError):
                        continue

        matches_odds[match_key] = odds_data
        print(f"  解析到: {home_short} vs {away_short}")
        print(f"    WDL: {len(odds_data['wdl_odds']['records'])}条, Score: {len(odds_data['score_odds']['records'])}条, TG: {len(odds_data['tg_odds']['records'])}条")

    return matches_odds


# ============================================================
# 赔率匹配
# ============================================================
def match_odds(match, all_odds):
    """匹配比赛到赔率块"""
    home = match['home_team']
    away = match['away_team']

    # 直接匹配
    for key, val in all_odds.items():
        if home in key and away in key:
            return val
    # 模糊匹配
    for key, val in all_odds.items():
        home_parts = home.split()
        away_parts = away.split()
        if any(p in key for p in home_parts) and any(p in key for p in away_parts):
            return val
    # 更模糊
    for key, val in all_odds.items():
        if home[:3].lower() in key.lower() or away[:3].lower() in key.lower():
            return val
    print(f"  ⚠️ 未找到赔率匹配: {home} vs {away}")
    return None


# ============================================================
# T-005 v3 模型加载与特征构造
# ============================================================
def load_t005v3_models():
    """加载 T-005 v3 两阶段模型和 Elo 评级快照"""
    print("\n[T-005 v3] 加载模型...")
    with open(T005V3_DRAW_MODEL_PATH, 'rb') as f:
        draw_model = pickle.load(f)
    with open(T005V3_DIR_MODEL_PATH, 'rb') as f:
        dir_model = pickle.load(f)
    with open(T005V3_ELO_PATH, 'r', encoding='utf-8') as f:
        elo_snapshot = json.load(f)

    print(f"  ✅ draw_detector: {type(draw_model).__name__} (n_features={draw_model.n_features_in_})")
    print(f"  ✅ direction_predictor: {type(dir_model).__name__} (n_features={dir_model.n_features_in_})")
    print(f"  ✅ Elo 球队数: {len(elo_snapshot.get('elo_ratings', {}))}")

    return {
        'draw_model': draw_model,
        'dir_model': dir_model,
        'elo_ratings': elo_snapshot.get('elo_ratings', {}),
        'elo_momentum': elo_snapshot.get('elo_momentum', {}),
    }


def build_historical_feature_matrix():
    """构建历史特征矩阵 (复用 hcp_features_v2.build_all_features_v2)"""
    print("\n[T-005 v3] 构建历史特征矩阵 (可能需要1-2分钟)...")
    from hcp_features_v2 import build_all_features_v2, V2_ALL_FEATURES
    from train_hcp_model import add_elo_features_hcp

    features = build_all_features_v2()
    # features 包含 61 核心特征 + metadata 列

    # 构造 meta 用于 Elo 特征添加
    meta = features[['league', 'date', 'home_team', 'away_team']].copy()
    # 添加 actual_handicap 列 (add_elo_features_hcp 需要 X.index 对应 match_id)
    features_with_elo, elo_cols = add_elo_features_hcp(features.copy(), meta)

    # 合并核心特征 + Elo 特征
    available_features = [c for c in T005V3_ALL_FEATURES if c in features_with_elo.columns]
    missing_features = [c for c in T005V3_ALL_FEATURES if c not in features_with_elo.columns]
    if missing_features:
        print(f"  ⚠️ 缺失特征列 ({len(missing_features)}): {missing_features[:5]}...")
        # 用 0 填充缺失特征列
        for c in missing_features:
            features_with_elo[c] = 0.0

    print(f"  ✅ 历史特征矩阵: {features_with_elo[T005V3_ALL_FEATURES].shape}")
    print(f"  ✅ 球队覆盖: {features_with_elo['home_team'].nunique()} 支主队, {features_with_elo['away_team'].nunique()} 支客队")

    return features_with_elo


def compute_hcp_features_from_odds(hcp_win, hcp_draw, hcp_lose):
    """从让球赔率实时计算 15 维 HCP 特征 (复用 hcp_features.extract_hcp_features 逻辑)"""
    eps = 1e-10
    # 归一化概率
    inv_w = 1.0 / max(hcp_win, eps)
    inv_d = 1.0 / max(hcp_draw, eps)
    inv_l = 1.0 / max(hcp_lose, eps)
    total = inv_w + inv_d + inv_l
    p_win = inv_w / total
    p_draw = inv_d / total
    p_lose = inv_l / total

    probs = np.array([p_win, p_draw, p_lose])
    raw_odds = np.array([hcp_win, hcp_draw, hcp_lose])

    features = {
        'hcp_prob_win': p_win,
        'hcp_prob_draw': p_draw,
        'hcp_prob_lose': p_lose,
        'hcp_home_strength': p_win - p_lose,
        'hcp_draw_risk': p_draw,
        'hcp_confidence': float(np.max(probs)),
        'hcp_entropy': float(-np.sum(probs * np.log(np.clip(probs, eps, 1.0)))),
        'hcp_expected_value': (1.0 / max(hcp_win, eps)) - (1.0 / max(hcp_lose, eps)),
        'hcp_volatility': float(np.std(probs)),
        'hcp_market_sentiment': inv_w / (inv_w + inv_d + inv_l),
        'hcp_underdog_ratio': p_lose / max(p_win, eps),
        'hcp_favorite_margin': 1.0 - float(np.max(probs)),
        'hcp_balance': float(abs(p_win - p_lose)),
        'hcp_upset_risk': p_draw + p_lose,
        'hcp_odds_skew': (hcp_win - hcp_lose) / max(hcp_win + hcp_draw + hcp_lose, eps),
    }
    return features


def compute_wdl_draw_features(wdl_draw_odds, hcp_draw_prob):
    """从 WDL 平局赔率计算 3 维走水专用特征"""
    eps = 1e-10
    wdl_draw_prob = 1.0 / max(wdl_draw_odds, eps)
    # draw_divergence: WDL 平局概率与 HCP 走水概率的差异
    draw_divergence = wdl_draw_prob - hcp_draw_prob
    return {
        'wdl_draw_odds': wdl_draw_odds,
        'wdl_draw_prob': wdl_draw_prob,
        'draw_divergence': draw_divergence,
    }


def compute_elo_features(home_team, away_team, elo_ratings, elo_momentum):
    """从 Elo 评级 JSON 计算 10 维 Elo 特征"""
    HOME_ADVANTAGE = 65.0  # 与 elo_rating.py 一致
    default_elo = 1500.0

    home_elo = elo_ratings.get(home_team, default_elo)
    away_elo = elo_ratings.get(away_team, default_elo)
    home_momentum = elo_momentum.get(home_team, 0.0)
    away_momentum = elo_momentum.get(away_team, 0.0)

    elo_diff = home_elo - away_elo + HOME_ADVANTAGE

    # 预期胜率 (标准 Elo 公式)
    expected_home = 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))
    expected_away = 1.0 - expected_home

    elo_ratio = home_elo / max(away_elo, 1.0)

    # 平局概率 (基于 Elo 差值的高斯衰减)
    diff_normalized = elo_diff / 200.0
    elo_draw_prob = float(np.clip(np.exp(-0.5 * diff_normalized ** 2), 0.0, 1.0))

    elo_confidence = (home_elo + away_elo) / 3000.0

    return {
        'home_elo': round(home_elo, 2),
        'away_elo': round(away_elo, 2),
        'elo_diff': round(elo_diff, 2),
        'elo_ratio': round(elo_ratio, 4),
        'elo_home_expected': round(expected_home, 4),
        'elo_away_expected': round(expected_away, 4),
        'elo_draw_prob': round(elo_draw_prob, 4),
        'home_elo_momentum': round(home_momentum, 2),
        'away_elo_momentum': round(away_momentum, 2),
        'elo_confidence': round(elo_confidence, 4),
    }


def build_match_feature_vector(m, odds_data, hist_features, models):
    """为新比赛构造 71 维特征向量

    策略:
        - HCP/WDL 赔率特征 (18维): 从赔率 TXT 实时计算
        - 球队历史特征 (43维): 从历史特征矩阵提取该球队最近一场比赛快照
        - Elo 特征 (10维): 从 JSON 加载并计算
        - 缺失特征: 用历史特征矩阵中位数填充
    """
    home_team = m['home_team']
    away_team = m['away_team']
    home_cn = m.get('home_team_cn', home_team)
    away_cn = m.get('away_team_cn', away_team)

    print(f"      [build_features] 开始构造特征: {home_team} ({home_cn}) vs {away_team} ({away_cn})")
    feature_vector = {}
    feature_source = {}  # 记录每个特征来源: 'odds' / 'history_home' / 'history_away' / 'elo' / 'median'

    # ===== 1. HCP 赔率特征 (15维) — 从赔率实时计算 =====
    hcp = odds_data.get('handicap_odds', {})
    hcp_close = hcp.get('close', {})
    hcp_win = hcp_close.get('win', 2.0)
    hcp_draw = hcp_close.get('draw', 3.0)
    hcp_lose = hcp_close.get('lose', 3.0)
    hcp_line = hcp.get('line', 'N/A')

    if hcp_win > 0 and hcp_draw > 0 and hcp_lose > 0:
        hcp_feats = compute_hcp_features_from_odds(hcp_win, hcp_draw, hcp_lose)
        print(f"      [build_features] Step 1: HCP赔率特征 15维 ✅ (让{hcp_line:+d}球, 赔率={hcp_win:.2f}/{hcp_draw:.2f}/{hcp_lose:.2f})")
    else:
        # 无让球赔率，用中位数
        hcp_feats = {c: float(hist_features[c].median()) for c in T005V3_CORE_FEATURES[:15]}
        print(f"      [build_features] Step 1: HCP赔率特征 15维 ⚠️ (无让球赔率，使用中位数填充)")

    for k, v in hcp_feats.items():
        feature_vector[k] = float(v)
        feature_source[k] = 'odds'

    # ===== 2. WDL 平局特征 (3维) — 从 WDL 赔率计算 =====
    wdl = odds_data.get('wdl_odds', {})
    wdl_close = wdl.get('close', {})
    wdl_draw_odds = wdl_close.get('draw', 3.0)
    wdl_feats = compute_wdl_draw_features(wdl_draw_odds, hcp_feats.get('hcp_prob_draw', 0.3))
    for k, v in wdl_feats.items():
        feature_vector[k] = float(v)
        feature_source[k] = 'odds'
    print(f"      [build_features] Step 2: WDL平局特征 3维 ✅ (平局赔率={wdl_draw_odds:.2f})")

    # ===== 3. 球队历史特征 (43维) — 从历史特征矩阵提取 =====
    # 球队特征列 (form + rest + cards + opponent_lag)
    team_feature_cols = T005V3_CORE_FEATURES[18:]  # 跳过前18个 HCP+WDL 特征

    # 查找主队最近一场比赛 (作为主队或客队)
    home_matches = hist_features[
        (hist_features['home_team'] == home_team) | (hist_features['away_team'] == home_team)
    ].sort_values('date', ascending=False)

    away_matches = hist_features[
        (hist_features['home_team'] == away_team) | (hist_features['away_team'] == away_team)
    ].sort_values('date', ascending=False)

    home_has_history = len(home_matches) > 0
    away_has_history = len(away_matches) > 0

    if home_has_history:
        home_last = home_matches.iloc[0]
        home_last_date = str(home_last['date'])[:10]
        home_last_opponent = home_last.get('away_team', '?') if home_last['home_team'] == home_team else home_last.get('home_team', '?')
    if away_has_history:
        away_last = away_matches.iloc[0]
        away_last_date = str(away_last['date'])[:10]
        away_last_opponent = away_last.get('away_team', '?') if away_last['home_team'] == away_team else away_last.get('home_team', '?')

    print(f"      [build_features] Step 3: 球队历史查找 — "
          f"主队({home_team}): {'✅' if home_has_history else '❌ 无历史'} "
          f"{f'({len(home_matches)}场, 最近={home_last_date} vs {home_last_opponent})' if home_has_history else ''}"
          f" | 客队({away_team}): {'✅' if away_has_history else '❌ 无历史'} "
          f"{f'({len(away_matches)}场, 最近={away_last_date} vs {away_last_opponent})' if away_has_history else ''}")

    history_home_found = 0
    history_away_found = 0
    median_filled = 0

    # 为每个球队特征赋值
    for col in team_feature_cols:
        if col.startswith('home_'):
            if home_has_history and col in home_last and pd.notna(home_last[col]):
                feature_vector[col] = float(home_last[col])
                feature_source[col] = 'history_home'
                history_home_found += 1
            else:
                feature_vector[col] = float(hist_features[col].median()) if col in hist_features else 0.0
                feature_source[col] = 'median'
                median_filled += 1
        elif col.startswith('away_'):
            if away_has_history and col in away_last and pd.notna(away_last[col]):
                feature_vector[col] = float(away_last[col])
                feature_source[col] = 'history_away'
                history_away_found += 1
            else:
                feature_vector[col] = float(hist_features[col].median()) if col in hist_features else 0.0
                feature_source[col] = 'median'
                median_filled += 1
        else:
            # h2h_*, elo_gap_abs, hcp_draw_prob_rank, opponent_season_draw_rate, market_draw_std
            feature_vector[col] = float(hist_features[col].median()) if col in hist_features else 0.0
            feature_source[col] = 'median'
            median_filled += 1

    print(f"      [build_features] Step 3: 球队历史特征 43维 — 主队命中={history_home_found} 客队命中={history_away_found} 中位数填充={median_filled}")

    # ===== 4. Elo 特征 (10维) — 从 JSON 计算 =====
    elo_feats = compute_elo_features(
        home_team, away_team,
        models['elo_ratings'], models['elo_momentum']
    )
    for k, v in elo_feats.items():
        feature_vector[k] = float(v)
        feature_source[k] = 'elo'

    home_elo_found = home_team in models.get('elo_ratings', {})
    away_elo_found = away_team in models.get('elo_ratings', {})
    print(f"      [build_features] Step 4: Elo特征 10维 — "
          f"主队({home_team}): {'✅' if home_elo_found else '❌ 默认1500'} (Elo={elo_feats['home_elo']:.0f}) | "
          f"客队({away_team}): {'✅' if away_elo_found else '❌ 默认1500'} (Elo={elo_feats['away_elo']:.0f}) | "
          f"diff={elo_feats['elo_diff']:.0f}")

    # ===== 5. 构造特征向量 (按 T005V3_ALL_FEATURES 顺序) =====
    X = np.array([feature_vector.get(c, 0.0) for c in T005V3_ALL_FEATURES], dtype=float).reshape(1, -1)

    # 处理 NaN
    nan_count = np.isnan(X).sum()
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    # 特征完整度统计
    odds_count = sum(1 for s in feature_source.values() if s == 'odds')
    history_home_count = sum(1 for s in feature_source.values() if s == 'history_home')
    history_away_count = sum(1 for s in feature_source.values() if s == 'history_away')
    elo_count = sum(1 for s in feature_source.values() if s == 'elo')
    median_count = sum(1 for s in feature_source.values() if s == 'median')

    feature_quality = {
        'total_features': len(T005V3_ALL_FEATURES),
        'odds_features': odds_count,
        'history_home_features': history_home_count,
        'history_away_features': history_away_count,
        'elo_features': elo_count,
        'median_filled': median_count,
        'home_has_history': home_has_history,
        'away_has_history': away_has_history,
        'home_last_match_date': str(home_last['date']) if home_has_history else None,
        'away_last_match_date': str(away_last['date']) if away_has_history else None,
        'completeness': 1.0 - (median_count / len(T005V3_ALL_FEATURES)),
    }

    print(f"      [build_features] Step 5: 特征向量构造完成 — shape={X.shape}, NaN={nan_count}, "
          f"完整度={feature_quality['completeness']*100:.1f}% "
          f"(来源: 赔率={odds_count} 主队={history_home_count} 客队={history_away_count} Elo={elo_count} 中位数={median_count})")

    return X, feature_quality


def predict_t005v3_handicap(models, X):
    """调用 T-005 v3 两阶段模型预测让球胜平负

    使用 deploy_t005v3_final.py 的 predict_two_stage 逻辑:
        1. draw_model 预测走水概率
        2. dir_model 预测方向 (非走水情况下)
        3. 温度缩放 T=0.800
        4. 统一阈值 0.500
    """
    draw_model = models['draw_model']
    dir_model = models['dir_model']

    # Stage 1: 走水概率
    p_draw_raw = draw_model.predict_proba(X)[:, 1]
    p_non_draw = 1.0 - p_draw_raw

    print(f"      [t005v3] Stage 1 (draw_detector): raw_p_draw={p_draw_raw[0]:.4f} ({p_draw_raw[0]*100:.1f}%), "
          f"p_non_draw={p_non_draw[0]:.4f}")

    # Stage 2: 方向概率 (非走水情况下，客胜概率)
    p_away_nd = dir_model.predict_proba(X)[:, 1]
    p_home_nd = 1.0 - p_away_nd

    print(f"      [t005v3] Stage 2 (direction_predictor): p_home_nd={p_home_nd[0]:.4f}, p_away_nd={p_away_nd[0]:.4f}")

    # 融合
    p_home = p_non_draw * p_home_nd
    p_draw_final = p_draw_raw
    p_away = p_non_draw * p_away_nd

    # 归一化
    total = p_home + p_draw_final + p_away
    p_home = p_home / total
    p_draw_final = p_draw_final / total
    p_away = p_away / total

    print(f"      [t005v3] 融合后 (归一化): 上盘={p_home[0]:.4f} ({p_home[0]*100:.1f}%), "
          f"走水={p_draw_final[0]:.4f} ({p_draw_final[0]*100:.1f}%), "
          f"下盘={p_away[0]:.4f} ({p_away[0]*100:.1f}%)")

    # 温度缩放 (T=0.800)
    logits = np.log(np.clip([p_home, p_draw_final, p_away], 1e-12, 1.0)).T / T005V3_TEMPERATURE
    logits = logits - logits.max(axis=1, keepdims=True)
    exp_logits = np.exp(logits)
    probs = exp_logits / exp_logits.sum(axis=1, keepdims=True)

    p_home, p_draw_final, p_away = probs[:, 0], probs[:, 1], probs[:, 2]

    print(f"      [t005v3] 温度缩放后 (T={T005V3_TEMPERATURE}): 上盘={p_home[0]:.4f} ({p_home[0]*100:.1f}%), "
          f"走水={p_draw_final[0]:.4f} ({p_draw_final[0]*100:.1f}%), "
          f"下盘={p_away[0]:.4f} ({p_away[0]*100:.1f}%)")

    # 预测 (统一阈值 0.500)
    if p_draw_final[0] >= T005V3_THRESHOLD:
        prediction = 1  # 走水
        print(f"      [t005v3] 决策: 走水 (p_draw={p_draw_final[0]:.4f} >= θ={T005V3_THRESHOLD})")
    elif p_home[0] >= p_away[0]:
        prediction = 0  # 上盘赢
        print(f"      [t005v3] 决策: 上盘赢 (p_home={p_home[0]:.4f} >= p_away={p_away[0]:.4f})")
    else:
        prediction = 2  # 下盘赢
        print(f"      [t005v3] 决策: 下盘赢 (p_away={p_away[0]:.4f} > p_home={p_home[0]:.4f})")

    probabilities = np.column_stack([p_home, p_draw_final, p_away])

    return {
        'prediction': HCP_LABEL_NAMES[prediction],
        'prediction_code': prediction,
        'probabilities': {
            '上盘赢': float(probabilities[0, 0]),
            '走水': float(probabilities[0, 1]),
            '下盘赢': float(probabilities[0, 2]),
        },
        'confidence': float(probabilities[0, prediction]),
        'raw_p_draw': float(p_draw_raw[0]),
        'temperature': T005V3_TEMPERATURE,
        'threshold': T005V3_THRESHOLD,
    }


# ============================================================
# 预测函数
# ============================================================
def odds_to_implied_prob(win, draw, lose):
    """赔率转隐含概率（归一化去除抽水）"""
    inv_w = 1.0 / win
    inv_d = 1.0 / draw
    inv_l = 1.0 / lose
    total = inv_w + inv_d + inv_l
    return inv_w / total, inv_d / total, inv_l / total


def predict_match(m, odds_data, models=None, hist_features=None):
    """四维度预测: WDL (赔率驱动), 让球 (T-005 v3 模型), 比分 (赔率+Poisson), 总进球 (赔率+Poisson)"""
    result = {}

    home_team = m['home_team']
    away_team = m['away_team']
    home_cn = m.get('home_team_cn', home_team)
    away_cn = m.get('away_team_cn', away_team)

    print(f"    [predict_match] 开始预测: {home_cn} vs {away_cn}")
    print(f"    [predict_match] 模型可用: {'✅' if models is not None else '❌'}, 历史特征矩阵: {'✅' if hist_features is not None else '❌'} "
          f"(shape={hist_features.shape if hist_features is not None else 'N/A'})")

    # ---------- 1. 胜平负 (WDL) — 赔率隐含概率 + 西甲 argmax 阈值 ----------
    wdl = odds_data['wdl_odds']
    last = wdl['close'] or wdl['records'][-1] if wdl['records'] else {'win': 2.0, 'draw': 3.4, 'lose': 3.0}
    first = wdl['open'] or wdl['records'][0] if wdl['records'] else last

    print(f"    [WDL] 赔率记录: {len(wdl.get('records', []))}条, 初盘=({first['win']:.2f}, {first['draw']:.2f}, {first['lose']:.2f}), "
          f"尾盘=({last['win']:.2f}, {last['draw']:.2f}, {last['lose']:.2f})")

    hp, dp, ap = odds_to_implied_prob(last['win'], last['draw'], last['lose'])
    print(f"    [WDL] 隐含概率: 主胜={hp*100:.1f}% 平局={dp*100:.1f}% 客胜={ap*100:.1f}%")

    # 决策阈值调整 (西甲 factor=0.0 = argmax)
    wdl_pred = '主胜' if hp > max(dp, ap) else ('平局' if dp > ap else '客胜')
    if dp * DRAW_THRESHOLD_FACTOR > max(hp, ap):
        wdl_pred = '平局'
    print(f"    [WDL] 决策: factor={DRAW_THRESHOLD_FACTOR}, 预测={wdl_pred}, 置信度={max(hp,dp,ap)*100:.1f}%")

    result['wdl'] = {
        'home_prob': hp, 'draw_prob': dp, 'away_prob': ap,
        'raw_home': hp, 'raw_draw': dp, 'raw_away': ap,
        'prediction': wdl_pred,
        'confidence': max(hp, dp, ap),
        'open': first,
        'close': last,
        'records': wdl['records'],
        'method': '赔率隐含概率 + argmax (factor=0.0)',
    }

    # 赔率趋势
    changes = []
    wc = last['win'] - first['win']
    dc = last['draw'] - first['draw']
    lc = last['lose'] - first['lose']
    if wc < -0.05: changes.append(f"主胜赔率↓{abs(wc):.2f}，市场看好主队")
    elif wc > 0.05: changes.append(f"主胜赔率↑{wc:.2f}，信心减弱")
    if dc < -0.05: changes.append(f"平局赔率↓{abs(dc):.2f}，平局可能性↑")
    elif dc > 0.05: changes.append(f"平局赔率↑{dc:.2f}，平局可能性↓")
    if lc < -0.05: changes.append(f"客胜赔率↓{abs(lc):.2f}，看好客队")
    elif lc > 0.05: changes.append(f"客胜赔率↑{lc:.2f}，客队信心减弱")
    result['wdl']['trend'] = "; ".join(changes) if changes else "赔率整体稳定，市场观点未发生显著变化"
    print(f"    [WDL] 赔率趋势: {result['wdl']['trend']}")

    # ---------- 2. 让球胜平负 — T-005 v3 两阶段模型 ----------
    hcp = odds_data['handicap_odds']
    hcp_line = hcp['line']
    if hcp['close']:
        h = hcp['close']
    elif hcp['records']:
        h = hcp['records'][-1]
    else:
        h = {'win': 0, 'draw': 0, 'lose': 0}

    print(f"    [HCP] 让球盘: {hcp_line:+d}球, 赔率记录: {len(hcp.get('records', []))}条, "
          f"尾盘=({h.get('win', 'N/A')}, {h.get('draw', 'N/A')}, {h.get('lose', 'N/A')})")

    # 初始化 T-005 v3 结果占位
    t005v3_result = None
    hcp_feature_quality = None

    # 调用 T-005 v3 模型预测
    if models is not None and hist_features is not None:
        print(f"    [HCP] 模型可用，开始构造特征向量...")
        try:
            X, feature_quality = build_match_feature_vector(m, odds_data, hist_features, models)
            print(f"    [HCP] 特征向量构造完成: shape={X.shape}, 特征完整度={feature_quality['completeness']*100:.1f}%")
            print(f"    [HCP] 调用 T-005 v3 两阶段模型...")
            t005v3_result = predict_t005v3_handicap(models, X)

            hcp_pred = t005v3_result['prediction']
            hcp_conf = t005v3_result['confidence']
            hcp_probs = t005v3_result['probabilities']
            hcp_hp = hcp_probs['上盘赢']
            hcp_dp = hcp_probs['走水']
            hcp_ap = hcp_probs['下盘赢']
            hcp_method = f"T-005 v3 两阶段模型 (T={t005v3_result['temperature']}, θ={t005v3_result['threshold']})"
            hcp_feature_quality = feature_quality

            print(f"    [HCP] T-005 v3 预测完成: {hcp_pred} (置信度: {hcp_conf*100:.1f}%)")
            print(f"    [HCP] 概率分布: 上盘赢={hcp_hp*100:.1f}% 走水={hcp_dp*100:.1f}% 下盘赢={hcp_ap*100:.1f}%")
            print(f"    [HCP] 特征来源: 赔率={feature_quality['odds_features']} 主队历史={feature_quality['history_home_features']} "
                  f"客队历史={feature_quality['history_away_features']} Elo={feature_quality['elo_features']} 中位数填充={feature_quality['median_filled']}")
        except Exception as e:
            print(f"    [HCP] ⚠️ T-005 v3 预测失败，降级为赔率隐含概率: {e}")
            import traceback; traceback.print_exc()
            # 降级: 使用赔率隐含概率
            if h['win'] > 0 and h['draw'] > 0 and h['lose'] > 0:
                hcp_hp, hcp_dp, hcp_ap = odds_to_implied_prob(h['win'], h['draw'], h['lose'])
                hcp_pred = '上盘赢' if hcp_hp > max(hcp_dp, hcp_ap) else ('走水' if hcp_dp > hcp_ap else '下盘赢')
                hcp_conf = max(hcp_hp, hcp_dp, hcp_ap)
            else:
                hcp_hp = hcp_dp = hcp_ap = 0
                hcp_pred = '数据不足'
                hcp_conf = 0
            hcp_method = '赔率隐含概率 (T-005 v3 降级)'
            print(f"    [HCP] 降级预测: {hcp_pred} (置信度: {hcp_conf*100:.1f}%)")
    else:
        # 无模型时降级为赔率隐含概率
        print(f"    [HCP] 模型不可用 (models={'✅' if models is not None else '❌'}, hist_features={'✅' if hist_features is not None else '❌'})，降级为赔率隐含概率")
        if h['win'] > 0 and h['draw'] > 0 and h['lose'] > 0:
            hcp_hp, hcp_dp, hcp_ap = odds_to_implied_prob(h['win'], h['draw'], h['lose'])
            hcp_pred = '上盘赢' if hcp_hp > max(hcp_dp, hcp_ap) else ('走水' if hcp_dp > hcp_ap else '下盘赢')
            hcp_conf = max(hcp_hp, hcp_dp, hcp_ap)
        else:
            hcp_hp = hcp_dp = hcp_ap = 0
            hcp_pred = '数据不足'
            hcp_conf = 0
        hcp_method = '赔率隐含概率 (未加载 T-005 v3 模型)'
        print(f"    [HCP] 降级预测: {hcp_pred} (置信度: {hcp_conf*100:.1f}%)")

    result['hcp'] = {
        'line': hcp_line,
        'home_win_prob': hcp_hp, 'draw_prob': hcp_dp, 'away_win_prob': hcp_ap,
        'prediction': hcp_pred, 'confidence': hcp_conf,
        'open': hcp.get('open', {}),
        'close': h,
        'method': hcp_method,
        'feature_quality': hcp_feature_quality,
        'raw_p_draw': t005v3_result.get('raw_p_draw') if t005v3_result is not None else None,
    }

    # ---------- 3. 比分预测 ----------
    score_odds_records = odds_data['score_odds']['records']
    score_from_odds = False
    score_top5 = []
    most_likely_score = '1:0'
    most_likely_prob = 0.0

    print(f"    [SCORE] 比分赔率记录: {len(score_odds_records)}条")

    if score_odds_records:
        so = score_odds_records[-1]
        win_odds = so.get('win_odds', {})
        draw_odds = so.get('draw_odds', {})
        lose_odds = so.get('lose_odds', {})

        print(f"    [SCORE] 赔率条目: 主胜={len(win_odds)}个 平局={len(draw_odds)}个 客胜={len(lose_odds)}个, "
              f"发布时间={so.get('pub_time', 'N/A')}")

        score_inv_pairs = []
        for sc, odds_val in list(win_odds.items()) + list(draw_odds.items()) + list(lose_odds.items()):
            if odds_val and isinstance(odds_val, (int, float)) and odds_val > 0:
                score_inv_pairs.append((sc, 1.0 / float(odds_val)))

        all_inv = sum(inv for sc, inv in score_inv_pairs)
        if all_inv > 0 and len(score_inv_pairs) >= 5:
            score_probs_norm = [(sc, inv / all_inv) for sc, inv in score_inv_pairs]
            score_probs_norm.sort(key=lambda x: x[1], reverse=True)
            score_from_odds = True
            score_top5 = [{"score": sc, "prob": pr} for sc, pr in score_probs_norm[:5]]
            most_likely_score = score_top5[0]['score']
            most_likely_prob = score_top5[0]['prob']
            print(f"    [SCORE] ✅ 赔率隐含概率可用: {len(score_inv_pairs)}个比分, Top-1={most_likely_score} ({most_likely_prob*100:.1f}%)")
        else:
            print(f"    [SCORE] ⚠️ 比分赔率不足 (有效条目={len(score_inv_pairs)}), 降级为Poisson")

    # Poisson 分布
    tg = odds_data['tg_odds']['records'][-1] if odds_data['tg_odds']['records'] else {}
    if tg:
        tg_total = sum(1.0 / tg.get(f'o{i}', 999) for i in range(8) if tg.get(f'o{i}', 999) > 0)
        tg_lambda = sum(i * (1.0 / tg.get(f'o{i}', 999)) / tg_total for i in range(8) if tg.get(f'o{i}', 999) > 0)
        h_ratio = hp / (hp + ap) if (hp + ap) > 0 else 0.5
        lh = tg_lambda * h_ratio
        la = tg_lambda * (1 - h_ratio)
        print(f"    [SCORE] Poisson λ: 总={tg_lambda:.3f}, 主队={lh:.3f}, 客队={la:.3f} (h_ratio={h_ratio:.3f})")
    else:
        tg_lambda = 2.0
        lh = 1.1
        la = 0.9
        print(f"    [SCORE] Poisson λ: 默认值 总={tg_lambda:.1f}, 主={lh:.1f}, 客={la:.1f}")

    poisson_scores = []
    for hh in range(6):
        for aa in range(6):
            prob = poisson.pmf(hh, lh) * poisson.pmf(aa, la)
            poisson_scores.append({"score": f"{hh}:{aa}", "prob": prob})
    total_p = sum(s["prob"] for s in poisson_scores)
    for s in poisson_scores:
        s["prob"] /= total_p
    poisson_scores.sort(key=lambda x: x["prob"], reverse=True)

    result['score'] = {
        'from_odds': score_from_odds,
        'top5': score_top5 if score_from_odds else poisson_scores[:5],
        'most_likely': most_likely_score if score_from_odds else (poisson_scores[0]['score'] if poisson_scores else '1:0'),
        'most_likely_prob': most_likely_prob if score_from_odds else (poisson_scores[0]['prob'] if poisson_scores else 0),
        'poisson_top5': poisson_scores[:5],
        'lambda_home': lh,
        'lambda_away': la,
        'expected_total_goals': tg_lambda,
        'latest_pub_time': score_odds_records[-1]['pub_time'] if score_odds_records else '',
    }

    print(f"    [SCORE] 最可能比分: {result['score']['most_likely']} ({result['score']['most_likely_prob']*100:.1f}%), "
          f"方法={'赔率隐含概率' if score_from_odds else 'Poisson'}, 期望总进球={tg_lambda:.2f}")

    # ---------- 4. 总进球数 ----------
    tg_records = odds_data['tg_odds']['records']
    print(f"    [TG] 总进球赔率记录: {len(tg_records)}条")
    if tg:
        tg_dist = {}
        tg_inv_sum = sum(1.0 / tg.get(f'o{i}', 999) for i in range(8) if tg.get(f'o{i}', 999) > 0)
        for i in range(8):
            key = f'{i}+' if i == 7 else str(i)
            odds_val = tg.get(f'o{i}', 0)
            if odds_val and odds_val > 0:
                tg_dist[key] = (1.0 / odds_val) / tg_inv_sum
            else:
                tg_dist[key] = poisson.pmf(i, tg_lambda) if i < 7 else (1 - poisson.cdf(6, tg_lambda))
        over25 = 1 - sum(tg_dist[str(k)] for k in range(3) if str(k) in tg_dist)
        tg_pred = "大球(>2.5)" if over25 > 0.5 else "小球(<2.5)"
    else:
        tg_dist = {}
        over25 = 1 - poisson.cdf(2, tg_lambda)
        tg_pred = "大球(>2.5)" if over25 > 0.5 else "小球(<2.5)"

    result['tg'] = {
        'expected': tg_lambda,
        'distribution': tg_dist,
        'over_2_5': over25,
        'prediction': tg_pred,
        'records': odds_data['tg_odds']['records'],
    }

    print(f"    [TG] 预测: {tg_pred} (期望={tg_lambda:.2f}球, 大球概率={over25*100:.1f}%)")
    print(f"    [predict_match] 预测完成: WDL={result['wdl']['prediction']}, HCP={result['hcp']['prediction']}, "
          f"SCORE={result['score']['most_likely']}, TG={tg_pred}")

    return result


# ============================================================
# 报告渲染
# ============================================================
def render_match_section(m, pre_data, o, r):
    """渲染单场比赛报告"""
    wdl, hcp, sc, tg = r["wdl"], r["hcp"], r["score"], r["tg"]
    hcp_line = hcp['line']

    # 球队信息
    home_name = pre_data.get('home_name', m['home_team'])
    away_name = pre_data.get('away_name', m['away_team'])
    home_form = pre_data.get('home_formation', 'N/A')
    away_form = pre_data.get('away_formation', 'N/A')
    status = pre_data.get('status', 'Not started')
    confirmed = pre_data.get('confirmed', False)

    sec = f"""
## {m['home_team_cn']} ({home_name}) vs {m['away_team_cn']} ({away_name})

> **赛事信息**: 西甲 2026/2027 Regular Season 第1轮  
> **比赛时间**: {m['match_time']} (北京时间)  
> **比赛状态**: {status}  
> **阵容状态**: {'✅ 已确认' if confirmed else '⏳ 预测版'}  
> **阵型**: {home_name} {home_form} | {away_name} {away_form}  
> **SofaScore**: [点击查看](https://www.sofascore.ro/football/match/{m['event_id']})

---

### 一、胜平负预测（西甲专属阈值 factor={DRAW_THRESHOLD_FACTOR}）

| 结果 | 市场隐含概率 |
|------|:----------:|
| 主胜 {m['home_team_cn']} | **{wdl['home_prob']*100:.1f}%** |
| 平局 | {wdl['draw_prob']*100:.1f}% |
| 客胜 {m['away_team_cn']} | {wdl['away_prob']*100:.1f}% |

> **预测结果**: **{wdl['prediction']}** (置信度: {wdl['confidence']*100:.1f}%)

#### 赔率时序变化

| 时间 | 主胜 | 平局 | 客胜 |
|------|:----:|:----:|:----:|
"""
    for rec in wdl['records']:
        t_short = rec['time'][5:16]
        sec += f"| {t_short} | {rec['win']:.2f} | {rec['draw']:.2f} | {rec['lose']:.2f} |\n"

    sec += f"\n> **赔率趋势分析**: {wdl['trend']}\n"

    # 让球胜平负 (T-005 v3 模型预测)
    hcp_arrow = f"{hcp_line:+d}"
    hcp_method = hcp.get('method', '赔率隐含概率')
    hcp_quality = hcp.get('feature_quality')

    sec += f"""
### 二、让球胜平负预测 ({m['home_team_cn']} {hcp_arrow}球)

> **预测方法**: {hcp_method}

| 结果 | 赔率 | 模型概率 |
|------|:----:|:--------:|
| 上盘赢 | {hcp['close'].get('win','N/A')} | {hcp['home_win_prob']*100:.1f}% |
| 走水 | {hcp['close'].get('draw','N/A')} | {hcp['draw_prob']*100:.1f}% |
| 下盘赢 | {hcp['close'].get('lose','N/A')} | **{hcp['away_win_prob']*100:.1f}%** |

> **预测**: **{hcp['prediction']}** (置信度: {hcp['confidence']*100:.1f}%)
"""

    # 特征完整度报告 (仅 T-005 v3 模型预测时显示)
    if hcp_quality:
        sec += f"""
<details>
<summary>📊 T-005 v3 特征完整度</summary>

| 指标 | 值 |
|------|:--:|
| 总特征维度 | {hcp_quality['total_features']} |
| 赔率实时特征 | {hcp_quality['odds_features']} |
| 主队历史特征 | {hcp_quality['history_home_features']} ({'✅ 有历史' if hcp_quality['home_has_history'] else '❌ 无历史'}) |
| 客队历史特征 | {hcp_quality['history_away_features']} ({'✅ 有历史' if hcp_quality['away_has_history'] else '❌ 无历史'}) |
| Elo 特征 | {hcp_quality['elo_features']} |
| 中位数填充 | {hcp_quality['median_filled']} |
| **特征完整度** | **{hcp_quality['completeness']*100:.1f}%** |
"""
        if hcp_quality.get('home_last_match_date'):
            sec += f"| 主队最近比赛 | {hcp_quality['home_last_match_date'][:10]} |\n"
        if hcp_quality.get('away_last_match_date'):
            sec += f"| 客队最近比赛 | {hcp_quality['away_last_match_date'][:10]} |\n"
        sec += """
</details>

"""

    sec += "\n"

    # 比分预测
    sec += f"""
### 三、比分预测

**方法**: {'真实赔率隐含概率' if sc['from_odds'] else 'Poisson分布模型'}  
**数据来源**: 竞彩网比分赔率 @ {sc.get('latest_pub_time','N/A')}  
**Poisson λ参数**: 主队={sc['lambda_home']:.3f}, 客队={sc['lambda_away']:.3f}  
**期望总进球**: {sc['expected_total_goals']:.2f}

#### Top-5 最可能比分

| 排名 | 比分 | 概率 | 累计概率 |
|:----:|:----:|:----:|:--------:|
"""
    cum_prob = 0
    for i, s_item in enumerate(sc['top5']):
        cum_prob += s_item['prob']
        sec += f"| Top-{i+1} | **{s_item['score']}** | {s_item['prob']*100:.1f}% | {cum_prob*100:.1f}% |\n"

    sec += f"\n> **最可能比分**: **{sc['most_likely']}** (概率: {sc['most_likely_prob']*100:.1f}%)\n"

    # Poisson对比
    if sc['poisson_top5']:
        sec += "\n#### Poisson模型参考\n\n"
        sec += "| 排名 | 比分 | 概率 |\n|:----:|:----:|:----:|\n"
        for i, s_item in enumerate(sc['poisson_top5'][:5]):
            sec += f"| Top-{i+1} | {s_item['score']} | {s_item['prob']*100:.1f}% |\n"
        sec += "\n"

    # 比分赔率全表
    if o.get('score_odds') and o['score_odds']['records']:
        so = o['score_odds']['records'][-1]
        sec += f"\n#### 完整比分赔率表 (发布时间: {so['pub_time']})\n\n"
        sec += "**主胜比分**:\n\n| 比分 | 赔率 | 比分 | 赔率 | 比分 | 赔率 |\n|:----:|:----:|:----:|:----:|:----:|:----:|\n"
        wo = list(so.get('win_odds', {}).items())
        for i in range(0, min(len(wo), 13), 3):
            row = ""
            for j in range(3):
                if i + j < len(wo):
                    row += f"| {wo[i+j][0]} | {wo[i+j][1]:.2f} "
                else:
                    row += "|  |  "
            sec += row + "|\n"

        sec += "\n**平局比分**:\n\n| 比分 | 赔率 | 比分 | 赔率 | 比分 | 赔率 |\n|:----:|:----:|:----:|:----:|:----:|:----:|\n"
        do = list(so.get('draw_odds', {}).items())
        for i in range(0, len(do), 3):
            row = ""
            for j in range(3):
                if i + j < len(do):
                    row += f"| {do[i+j][0]} | {do[i+j][1]:.2f} "
                else:
                    row += "|  |  "
            sec += row + "|\n"

        sec += "\n**客胜比分**:\n\n| 比分 | 赔率 | 比分 | 赔率 | 比分 | 赔率 |\n|:----:|:----:|:----:|:----:|:----:|:----:|\n"
        lo = list(so.get('lose_odds', {}).items())
        for i in range(0, min(len(lo), 13), 3):
            row = ""
            for j in range(3):
                if i + j < len(lo):
                    row += f"| {lo[i+j][0]} | {lo[i+j][1]:.2f} "
                else:
                    row += "|  |  "
            sec += row + "|\n"
        sec += "\n"

    # 总进球数
    sec += f"""
### 四、总进球数预测

| 进球数 | 赔率 | 隐含概率 |
|:------:|:----:|:--------:|
"""
    if tg.get('records'):
        tg_last = tg['records'][-1]
        keys = ['o0', 'o1', 'o2', 'o3', 'o4', 'o5', 'o6', 'o7']
        labels = ['0球', '1球', '2球', '3球', '4球', '5球', '6球', '7+球']
        for k, label in zip(keys, labels):
            odds_val = tg_last.get(k, 0)
            key_match = '7+' if '7+' in label else label.replace('球', '')
            prob = tg['distribution'].get(key_match, 0)
            sec += f"| {label} | {odds_val:.2f} | {prob*100:.1f}% |\n"

    sec += f"""
> **预测**: **{tg['prediction']}**  
> **大球概率(>2.5)**: {tg['over_2_5']*100:.1f}% | **期望进球数**: {tg['expected']:.2f}

#### 总进球赔率时序

| 时间 | 0 | 1 | 2 | 3 | 4 | 5 | 6 | 7+ |
|------|:-:|:-:|:-:|:-:|:-:|:-:|:-:|:-:|
"""
    for rec in tg.get('records', []):
        t_short = rec['time'][5:16]
        sec += f"| {t_short} | {rec.get('o0',0):.2f} | {rec.get('o1',0):.2f} | {rec.get('o2',0):.2f} | {rec.get('o3',0):.2f} | {rec.get('o4',0):.2f} | {rec.get('o5',0):.2f} | {rec.get('o6',0):.2f} | {rec.get('o7',0):.2f} |\n"

    # 风险提示
    sec += f"""
### 五、风险提示

1. **新赛季首轮不确定性**: 夏季转会窗口引援尚未完全磨合，战术体系待验证
2. **阵容预测版风险**: 赛前阵容为 SofaScore 预测版，实际首发可能调整
3. **赔率临场波动**: 当前赔率截止至发布时间，临场1小时内可能因首发确认、资金流向等因素显著变化
4. **西甲阈值**: 使用 factor={DRAW_THRESHOLD_FACTOR} (argmax模式)，不做平局阈值调整
5. **比分预测局限**: 比分预测 Top-1 命中率通常 12%-15%，建议关注 Top-3/Top-5 累计概率
6. **本报告仅供研究参考，不构成投注建议**

---

"""
    return sec


# ============================================================
# 主函数
# ============================================================
def main():
    print("=" * 70)
    print("西甲第1轮预测 - 第二批次 (Racing vs Villarreal, Espanyol vs Levante)")
    print("=" * 70)

    # 1. 加载赛前数据
    print("\n[1/5] 加载赛前数据...")
    with open(PRE_MATCH_JSON, 'r', encoding='utf-8') as f:
        pre_data = json.load(f)
    for m in MATCHES:
        eid = m['event_id']
        if eid in pre_data:
            print(f"  ✅ {m['home_team_cn']} vs {m['away_team_cn']} — 赛前数据已加载 "
                  f"(状态: {pre_data[eid].get('status','?')}, 阵容: {'已确认' if pre_data[eid].get('confirmed') else '预测版'})")
        else:
            print(f"  ⚠️ {m['home_team_cn']} vs {m['away_team_cn']} — 未找到赛前数据")

    # 2. 加载 T-005 v3 模型和历史特征矩阵
    print("\n[2/5] 加载 T-005 v3 模型...")
    models = None
    hist_features = None
    try:
        models = load_t005v3_models()
        print("  ✅ T-005 v3 模型加载成功")
    except Exception as e:
        print(f"  ⚠️ T-005 v3 模型加载失败: {e}")
        print(f"  ⚠️ 让球预测将降级为赔率隐含概率")

    print("\n[3/5] 构建历史特征矩阵...")
    if models is not None:
        try:
            hist_features = build_historical_feature_matrix()
            print("  ✅ 历史特征矩阵构建成功")
        except Exception as e:
            print(f"  ⚠️ 历史特征矩阵构建失败: {e}")
            import traceback; traceback.print_exc()
            print(f"  ⚠️ 让球预测将降级为赔率隐含概率")
            models = None  # 无历史特征矩阵时模型不可用

    # 4. 解析赔率
    print("\n[4/5] 解析赔率数据...")
    all_odds = parse_odds_txt(ODDS_TXT)
    print(f"  共解析到 {len(all_odds)} 场比赛赔率")

    # 5. 匹配 + 预测
    print("\n[5/5] 运行预测...")
    predictions = {}
    match_odds_data = {}

    for m in MATCHES:
        print(f"\n  {'='*50}")
        print(f"  比赛: {m['home_team_cn']} vs {m['away_team_cn']}")
        print(f"  时间: {m['match_time']}")

        od = match_odds(m, all_odds)
        if od:
            match_odds_data[m['id']] = od
            print(f"  ✅ 赔率匹配成功")
            print(f"    WDL: {len(od['wdl_odds']['records'])}条, 让球: {len(od['handicap_odds']['records'])}条, 比分: {len(od['score_odds']['records'])}条, TG: {len(od['tg_odds']['records'])}条")
        else:
            print(f"  ⚠️ 未匹配到赔率，使用默认值")
            match_odds_data[m['id']] = {
                'wdl_odds': {'records': [{'time': '2026-08-15 00:00:00', 'win': 2.2, 'draw': 3.0, 'lose': 3.2}],
                             'open': {'win': 2.2, 'draw': 3.0, 'lose': 3.2}, 'close': {'win': 2.2, 'draw': 3.0, 'lose': 3.2}},
                'handicap_odds': {'line': -1, 'records': [], 'open': {}, 'close': {'win': 5.5, 'draw': 3.5, 'lose': 1.5}},
                'score_odds': {'records': []},
                'tg_odds': {'records': [{'time': '2026-08-15 00:00:00', 'o0':5.0,'o1':3.0,'o2':3.2,'o3':4.5,'o4':8.0,'o5':18.0,'o6':35.0,'o7':50.0}]},
            }

        predictions[m['id']] = predict_match(m, match_odds_data[m['id']], models=models, hist_features=hist_features)
        pr = predictions[m['id']]
        print(f"    WDL: {pr['wdl']['prediction']} (置信度: {pr['wdl']['confidence']*100:.1f}%)")
        print(f"    胜平负概率: 主{pr['wdl']['home_prob']*100:.1f}% / 平{pr['wdl']['draw_prob']*100:.1f}% / 客{pr['wdl']['away_prob']*100:.1f}%")
        print(f"    让球: {pr['hcp']['prediction']} (让{pr['hcp']['line']:+d}球) — 方法: {pr['hcp']['method']}")
        print(f"    比分: {pr['score']['most_likely']} (概率: {pr['score']['most_likely_prob']*100:.1f}%)")
        print(f"    总进球: {pr['tg']['prediction']} (期望: {pr['tg']['expected']:.2f}, 大球概率: {pr['tg']['over_2_5']*100:.1f}%)")

    # 6. 生成报告
    print("\n[6/6] 生成预测报告...")
    now = datetime.now().strftime("%Y-%m-%d %H:%M")

    report = f"""# 西甲2026-2027赛季 第1轮 赛前预测分析报告 (第二批次)

> **报告生成时间**: {now}  
> **模型版本**: T-005 v3 | 西甲专属阈值 factor={DRAW_THRESHOLD_FACTOR} (argmax)  
> **数据来源**: SofaScore API (赛前数据) + 中国体彩竞彩网赔率  
> **赔率文件**: 西甲2026-2027赛季完整时序赔率.txt

---

## 预测摘要

| 比赛 | 胜平负 | 置信度 | 让球 | 最可能比分 | 概率 | 总进球 |
|------|:------:|:------:|:----:|:--------:|:----:|:------:|
"""
    for m in MATCHES:
        pr = predictions[m['id']]
        hcp_line = match_odds_data[m['id']]['handicap_odds']['line']
        report += (f"| {m['home_team_cn']} vs {m['away_team_cn']} "
                   f"| **{pr['wdl']['prediction']}** | {pr['wdl']['confidence']*100:.1f}% "
                   f"| {pr['hcp']['prediction']} ({hcp_line:+d}) "
                   f"| {pr['score']['most_likely']} | {pr['score']['most_likely_prob']*100:.1f}% "
                   f"| {pr['tg']['prediction']} |\n")

    report += """
---

## 详细预测分析

"""

    for m in MATCHES:
        pd_item = pre_data.get(m['event_id'], {})
        od = match_odds_data[m['id']]
        pr = predictions[m['id']]
        report += render_match_section(m, pd_item, od, pr)

    # 附录
    report += f"""
## 附录

### A. 预测方法说明

#### A.1 胜平负预测
- **核心算法**: 市场赔率隐含概率 → 归一化去抽水 → 决策阈值 factor={DRAW_THRESHOLD_FACTOR}
- **西甲专属**: factor=0.0 (argmax模式)，不做平局阈值调整
- **校准依据**: 西甲历史平局预测率48.6%远超实际26.1%，argmax模式准确率+2.02pp

#### A.2 让球胜平负
- **核心算法**: T-005 v3 两阶段模型 (draw_detector + direction_predictor)
- **特征维度**: 71维 (15 HCP赔率 + 3 WDL平局 + 43 球队历史 + 10 Elo)
- **温度缩放**: T=0.800 (校准 Platt Scaling)
- **统一阈值**: θ=0.500 (走水概率≥0.500判定为走水)
- **特征填充**: 赔率实时计算 + 历史特征矩阵快照 + JSON加载Elo + 中位数填充缺失
- **降级策略**: 模型加载失败或特征构造异常时，降级为让球赔率隐含概率

#### A.3 比分预测
- **主方法**: 竞彩网比分赔率隐含概率（归一化，去除彩金抽水）
- **辅助方法**: Poisson分布模型，λ由赔率隐含概率联合估算
- **局限**: Top-1命中率通常12-15%，建议参考Top-3（累计~30%）或Top-5（累计~40%）

#### A.4 总进球数预测
- 总进球赔率隐含概率推导 + Poisson校准
- 大/小判断以2.5球为界，概率>50%即判为大/小球

---

*本报告由足球预测模型系统自动生成，所有数据仅供研究参考，不构成任何投注建议。*
"""

    report_filename = f"西甲第1轮赛前预测报告_第二批_{datetime.now().strftime('%Y%m%d_%H%M%S')}.md"
    report_path = os.path.join(REPORT_DIR, report_filename)
    with open(report_path, 'w', encoding='utf-8') as f:
        f.write(report)

    print(f"  报告保存成功!")
    print(f"  文件: {report_path}")
    print(f"  大小: {os.path.getsize(report_path)/1024:.1f} KB")

    print("\n" + "=" * 70)
    print("✅ 预测完成！")
    print("=" * 70)


if __name__ == '__main__':
    main()