"""
预测核心模块 v1.0
=================
统一预测入口，收敛所有预测逻辑到单一模块。
训练和推理共用同一套代码，消除 Node.js 和 Python 之间的预测逻辑分裂。

模块:
  - CalcEngine: λ/Poisson/Dixon-Coles/A-002/calibrateDraw (纯数学)
  - WDLPredictor: 4模型Stacking (DC+XGB+LGB+Elo) + 英超独立模型参考
  - HandicapPredictor: T-005 v3 两阶段让球预测
  - ScorePredictor: T-006 v4 比分预测
  - TotalGoalsPredictor: 总进球预测
  - PredictionCore: 统一编排入口

设计原则:
  1. 四维度并行调用，互不依赖
  2. 每个模型独立降级（ML失败→Poisson→赔率隐含概率）
  3. 详细日志输出，覆盖所有关键节点
  4. 支持 mock 数据模式，无需真实数据库即可测试

用法:
  from prediction_core import PredictionCore, init_models

  models = init_models()
  core = PredictionCore(models)
  result = core.predict_unified(match, odds_data, is_mock=False)
"""

import json
import os
import sys
import pickle
import warnings
import math
import logging
import numpy as np
from datetime import datetime, timezone, timedelta

warnings.filterwarnings('ignore')

# 路径配置
SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SCRIPT_DIR)
sys.path.insert(0, os.path.join(PROJECT_DIR, 'collection'))

ASSETS_DIR = os.path.join(PROJECT_DIR, "assets")
DATA_DIR = os.path.join(PROJECT_DIR, "data")

# ============================================================
# 日志配置
# ============================================================
logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(name)-12s] %(levelname)-5s %(message)s',
    datefmt='%H:%M:%S'
)
logger = logging.getLogger('prediction_core')


def log_model(name, stage, msg, level='info'):
    """统一日志格式: [模型名称] [阶段] 消息"""
    getattr(logger, level)(f"[{name}] [{stage}] {msg}")


# ============================================================
# 配置常量
# ============================================================
# WDL 模型参数
WDL_TEMPERATURE = 0.800
WDL_DRAW_THRESHOLD_FACTOR = 0.0  # 西甲 argmax

# T-005 v3 模型参数
T005V3_TEMPERATURE = 1.000
T005V3_THRESHOLD = 0.500
T005V3_DRAW_PROB_FLOOR = 0.08  # 走水概率最低 8%
T005V3_DRAW_MODEL_PATH = os.path.join(ASSETS_DIR, 't005v3_draw_detector.pkl')
T005V3_DIR_MODEL_PATH = os.path.join(ASSETS_DIR, 't005v3_direction_predictor.pkl')
T005V3_ELO_PATH = os.path.join(ASSETS_DIR, 't005v2_final_elo_ratings.json')

# T-006 v4 比分预测配置
T006_RHO = -0.30
T006_RHO_HIGH = -0.10
T006_POISSON_WEIGHT = 0.85
T006_MC_WEIGHT = 0.15
T006_SCORE_ODDS_ALPHA = 0.30
T006_MC_SIMULATIONS = 500

# P1-13: λ 主客差值告警阈值（>1.2 表示两队进球期望极度失衡，需赛后拿真实 xG 复核 λ 链路）
LAMBDA_DIFF_ALERT_THRESHOLD = 1.2

# C-20260823-002: 4模型 Stacking 权重 (砍 Poisson+SSM，合并到 DC；与 prediction-engine.js 一致)
# C-20260828-008 (P1-7): 新增贝叶斯层级模型，权重 0.20（纯赛果统计模型，增加方法论多样性）
STACKING_WEIGHTS = {
    'dixonColes': 0.30,
    'xgboost': 0.30,
    'lightgbm': 0.25,
    'elo': 0.15,
    'bayesian': 0.20,
}

# T-005 v3 71维特征列
T005V3_CORE_FEATURES = [
    "hcp_prob_win", "hcp_prob_draw", "hcp_prob_lose",
    "hcp_home_strength", "hcp_draw_risk", "hcp_confidence",
    "hcp_entropy", "hcp_expected_value", "hcp_volatility",
    "hcp_market_sentiment", "hcp_underdog_ratio", "hcp_favorite_margin",
    "hcp_balance", "hcp_upset_risk", "hcp_odds_skew",
    "wdl_draw_odds", "wdl_draw_prob", "draw_divergence",
    "home_recent_wins", "home_recent_draws", "home_recent_losses",
    "home_recent_goals_for", "home_recent_goals_against", "home_recent_points",
    "away_recent_wins", "away_recent_draws", "away_recent_losses",
    "away_recent_goals_for", "away_recent_goals_against", "away_recent_points",
    "rest_days_home", "rest_days_away", "rest_days_diff",
    "home_yellow_cards_l5", "home_red_cards_l5",
    "away_yellow_cards_l5", "away_red_cards_l5",
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
T005V3_ALL_FEATURES = T005V3_CORE_FEATURES + T005V3_ELO_FEATURES

# 新赛季首轮 λ 基值
NEW_SEASON_FIRST_ROUND_AVG_GOALS = 3.6


# ============================================================
# C-20260823-P0-4 阶段 C: 统一引擎接入（推理侧最小接入）
# USE_UNIFIED_ENGINE 开关（默认关闭，支持快速回滚）
# 优先级: 环境变量 USE_UNIFIED_ENGINE(1/true/on|0/false/off) > config.yaml unified_engine.enabled
# ============================================================
_UNIFIED_ENGINE_INST = None


def _unified_engine_enabled():
    """读取 USE_UNIFIED_ENGINE 开关（config.yaml unified_engine.enabled 对应 unified_engine.enabled: false）。"""
    env = os.environ.get('USE_UNIFIED_ENGINE', '').strip().lower()
    if env in ('1', 'true', 'yes', 'on'):
        return True
    if env in ('0', 'false', 'no', 'off'):
        return False
    try:
        import yaml
        cfg_path = os.path.join(PROJECT_DIR, 'config.yaml')
        if os.path.exists(cfg_path):
            with open(cfg_path, 'r', encoding='utf-8') as f:
                cfg = yaml.safe_load(f) or {}
            ue = cfg.get('unified_engine') or {}
            return bool(ue.get('enabled', False))
    except Exception:
        pass
    return False


USE_UNIFIED_ENGINE = _unified_engine_enabled()


def get_unified_engine(strong_handicap=False, league=None):
    """阶段 C: DixonColesGenerator 懒加载单例（max_goals=7 与 T-006 对齐）。

    前置项 2/3（unified_engine_integration_plan §8）:
      - max_goals=7 与 T-006 现有 max_goals=7 对齐
      - ρ 策略: strong_handicap → T006_RHO_HIGH(-0.10)，否则 T006_RHO(-0.30)
        （引擎 set_league_rho 内置等价逻辑；league 分支供阶段 A/B 使用）
    """
    global _UNIFIED_ENGINE_INST
    if _UNIFIED_ENGINE_INST is None:
        from unified_prediction_engine import DixonColesGenerator
        _UNIFIED_ENGINE_INST = DixonColesGenerator(max_goals=7)
    if strong_handicap or league is not None:
        _UNIFIED_ENGINE_INST.set_league_rho(league, strong_handicap=strong_handicap)
    else:
        _UNIFIED_ENGINE_INST.set_league_rho(None)
    return _UNIFIED_ENGINE_INST


# ============================================================
# 工具函数
# ============================================================
def odds_to_implied_prob(win, draw, lose):
    """赔率转隐含概率（去水分）"""
    total = 1.0 / win + 1.0 / draw + 1.0 / lose
    return (1.0 / win) / total, (1.0 / draw) / total, (1.0 / lose) / total


# ============================================================
# CalcEngine: 纯数学计算引擎
# ============================================================
class CalcEngine:
    """
    核心数学引擎 — 所有概率计算和 λ 调整的纯数学实现。
    移植自 prediction-engine.js，与 JS 引擎逻辑完全一致。
    """

    @staticmethod
    def poisson_pmf(lmbda, k):
        """Poisson PMF"""
        return math.exp(-lmbda) * (lmbda ** k) / math.factorial(k)

    @staticmethod
    def poisson_random(lmbda, max_val=7):
        """Poisson 随机数生成 (Knuth算法)"""
        L = math.exp(-lmbda)
        k = 0
        p = 1.0
        while True:
            k += 1
            p *= np.random.random()
            if p <= L or k >= max_val + 1:
                break
        return k - 1

    @staticmethod
    def calc_lambda_from_odds(odds_data):
        """从赔率隐含概率计算 λ (进球期望值)"""
        wdl = odds_data['wdl_odds']
        last = wdl['close'] or wdl['records'][-1] if wdl['records'] else {'win': 2.0, 'draw': 3.4, 'lose': 3.0}
        hp, dp, ap = odds_to_implied_prob(last['win'], last['draw'], last['lose'])
        avg_goals = NEW_SEASON_FIRST_ROUND_AVG_GOALS
        lambda_home = avg_goals * hp
        lambda_away = avg_goals * ap
        lambda_home = max(0.3, min(3.5, lambda_home))
        lambda_away = max(0.3, min(3.5, lambda_away))
        return lambda_home, lambda_away

    @staticmethod
    def adjust_lambda_for_mid_score(lambda_home, lambda_away, odds_data=None):
        """
        A-002 中比分 λ 调整 — 移植自 prediction-engine.js L1469-1504。
        两阶段缩放:
          Stage 1: WDL 概率缩放 → λ' = λ × (0.5 + P(win) × 1.5)
          Stage 2: TG 赔率总进球数缩放 → λ'' = λ' × tgExpected / (λH+λA)

        优先使用赔率隐含概率（市场信息，独立于模型），
        仅当无赔率时回退到模型预测。

        返回:
          tuple (lambda_home_adj, lambda_away_adj, trace_dict)
          trace_dict 包含完整链路每一步的中间值，供赛后复盘：
            - base: 原始 λ
            - stage1_wdl: WDL 缩放（win_prob / scale_home / scale_away / source）
            - stage2_tg: TG 缩放（tg_expected / base_total / scale / source）
            - final: 最终 λ + diff
        """
        trace = {
            "base": {"lambda_home": lambda_home, "lambda_away": lambda_away},
            "stage1_wdl": {},
            "stage2_tg": {},
            "final": {},
        }

        # Stage 1: WDL 概率缩放
        wdl_source = "fallback_0.33"
        win_home = 0.33
        win_away = 0.33
        if odds_data:
            wdl = odds_data['wdl_odds']
            last = wdl['close'] or wdl['records'][-1] if wdl['records'] else None
            if last:
                hp, dp, ap = odds_to_implied_prob(last['win'], last['draw'], last['lose'])
                win_home = hp
                win_away = ap
                wdl_source = "odds_implied"

        wdl_scale_home = max(0.7, min(1.8, 0.5 + win_home * 1.5))
        wdl_scale_away = max(0.7, min(1.8, 0.5 + win_away * 1.5))
        trace["stage1_wdl"] = {
            "source": wdl_source,
            "win_home": round(win_home, 4),
            "win_away": round(win_away, 4),
            "scale_home": round(wdl_scale_home, 4),
            "scale_away": round(wdl_scale_away, 4),
            "lambda_after_s1_home": round(lambda_home * wdl_scale_home, 4),
            "lambda_after_s1_away": round(lambda_away * wdl_scale_away, 4),
        }

        # Stage 2: TG 赔率总进球数缩放
        tg_scale = 1.0
        tg_source = "none"
        tg_expected = None
        base_total = lambda_home + lambda_away
        if odds_data and 'tg_odds' in odds_data:
            tg_records = odds_data['tg_odds'].get('records', [])
            if tg_records:
                tg = tg_records[-1]
                goals = tg.get('goals', {})
                if goals:
                    # 从赔率反推隐含期望总进球
                    total_inv = sum(1.0 / max(float(v), 1e-10) for v in goals.values())
                    tg_expected = 0.0
                    for k, v in goals.items():
                        key = int(k.replace('+', '')) if k.replace('+', '').isdigit() else 7
                        p = (1.0 / max(float(v), 1e-10)) / total_inv
                        tg_expected += key * p

                    if base_total > 0.1:
                        tg_scale = tg_expected / base_total
                        tg_scale = max(0.85, min(1.4, tg_scale))
                        tg_source = "odds_implied"

        lambda_home_adj = lambda_home * wdl_scale_home * tg_scale
        lambda_away_adj = lambda_away * wdl_scale_away * tg_scale
        trace["stage2_tg"] = {
            "source": tg_source,
            "base_total": round(base_total, 4),
            "tg_expected": round(tg_expected, 4) if tg_expected is not None else None,
            "scale": round(tg_scale, 4),
        }

        log_model('A-002', 'λ调整',
                  f'λ_home: {lambda_home:.3f}→{lambda_home_adj:.3f} '
                  f'(wdl={wdl_scale_home:.2f}, tg={tg_scale:.2f}), '
                  f'λ_away: {lambda_away:.3f}→{lambda_away_adj:.3f} '
                  f'(wdl={wdl_scale_away:.2f})')

        # P1-13: λ 主客差值告警钩子
        lambda_diff = abs(lambda_home_adj - lambda_away_adj)
        trace["final"] = {
            "lambda_home": round(lambda_home_adj, 4),
            "lambda_away": round(lambda_away_adj, 4),
            "diff": round(lambda_diff, 4),
            "alert_triggered": lambda_diff > LAMBDA_DIFF_ALERT_THRESHOLD,
        }
        if lambda_diff > LAMBDA_DIFF_ALERT_THRESHOLD:
            log_model('A-002', 'λ告警',
                      f'λ主客差={lambda_diff:.3f} > {LAMBDA_DIFF_ALERT_THRESHOLD}（两队进球期望极度失衡，'
                      f'λ主={lambda_home_adj:.3f}/λ客={lambda_away_adj:.3f}，'
                      f'需赛后拿真实 xG 复核 λ 链路）', 'warning')

        return lambda_home_adj, lambda_away_adj, trace

    @staticmethod
    def calibrate_draw_probability(wdl, target_draw_rate=0.26):
        """
        平局概率校准 — 移植自 prediction-engine.js L494-515。
        按比例从 win 和 lose 中调整以匹配目标平局率。
        """
        win, draw, lose = wdl['win'], wdl['draw'], wdl['lose']
        current_draw = draw
        diff = target_draw_rate - current_draw

        if abs(diff) < 0.001:
            return wdl

        win_lose_sum = win + lose
        if win_lose_sum <= 0:
            return wdl

        new_draw = target_draw_rate
        remaining = 1 - new_draw
        new_win = win / win_lose_sum * remaining
        new_lose = lose / win_lose_sum * remaining

        return {'win': new_win, 'draw': new_draw, 'lose': new_lose}

    @staticmethod
    def calc_win_draw_lose_poisson(lambda_home, lambda_away, max_goals=10):
        """Poisson WDL: 枚举所有比分统计胜平负"""
        win_a = draw = win_b = 0.0
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = CalcEngine.poisson_pmf(lambda_home, i) * CalcEngine.poisson_pmf(lambda_away, j)
                if i > j:
                    win_a += p
                elif i == j:
                    draw += p
                else:
                    win_b += p
        total = win_a + draw + win_b
        return {'win': win_a / total, 'draw': draw / total, 'lose': win_b / total}

    @staticmethod
    def calc_win_draw_lose_dixon_coles(lambda_home, lambda_away, rho=-0.30, max_goals=10):
        """Dixon-Coles WDL: 带低比分修正的 Poisson"""
        win_a = draw = win_b = 0.0
        for i in range(max_goals + 1):
            for j in range(max_goals + 1):
                p = CalcEngine.poisson_pmf(lambda_home, i) * CalcEngine.poisson_pmf(lambda_away, j)
                # DC τ 修正
                if i == 0 and j == 0:
                    tau = 1.0 - lambda_home * lambda_away * rho
                elif i == 0 and j == 1:
                    tau = 1.0 + lambda_home * rho
                elif i == 1 and j == 0:
                    tau = 1.0 + lambda_away * rho
                elif i == 1 and j == 1:
                    tau = 1.0 - rho
                else:
                    tau = 1.0
                tau = max(0.1, min(3.0, tau))
                p *= tau
                if i > j:
                    win_a += p
                elif i == j:
                    draw += p
                else:
                    win_b += p
        total = win_a + draw + win_b
        return {'win': win_a / total, 'draw': draw / total, 'lose': win_b / total}

    @staticmethod
    def calc_win_draw_lose_elo(home_team, away_team, elo_ratings, elo_momentum):
        """Elo WDL: 基于 Elo 评分计算胜平负概率"""
        home_elo = elo_ratings.get(home_team, 1500)
        away_elo = elo_ratings.get(away_team, 1500)
        home_mom = elo_momentum.get(home_team, 0)
        away_mom = elo_momentum.get(away_team, 0)
        elo_diff = home_elo - away_elo + home_mom - away_mom
        p_home = 1.0 / (1.0 + 10 ** (-elo_diff / 400.0))
        p_away = 1.0 / (1.0 + 10 ** (elo_diff / 400.0))
        p_draw = max(0.20, 0.30 - abs(elo_diff) / 2000.0)
        total = p_home + p_draw + p_away
        return {'win': p_home / total, 'draw': p_draw / total, 'lose': p_away / total}

    @staticmethod
    def stack_wdl_probabilities(sub_probs, weights):
        """4模型 Stacking 加权融合"""
        stacked = {'win': 0.0, 'draw': 0.0, 'lose': 0.0}
        total_weight = 0.0
        for model_name, probs in sub_probs.items():
            w = weights.get(model_name, 0)
            if w > 0 and probs is not None:
                stacked['win'] += probs['win'] * w
                stacked['draw'] += probs['draw'] * w
                stacked['lose'] += probs['lose'] * w
                total_weight += w
        if total_weight > 0:
            stacked['win'] /= total_weight
            stacked['draw'] /= total_weight
            stacked['lose'] /= total_weight
        return stacked

    @staticmethod
    def dixon_coles_correction(h, a, lambda_home, lambda_away, rho=-0.30):
        """Dixon-Coles τ 修正因子"""
        if h <= 1 and a <= 1:
            if h == 0 and a == 0:
                return 1.0 - lambda_home * lambda_away * rho
            elif h == 0 and a == 1:
                return 1.0 + lambda_home * rho
            elif h == 1 and a == 0:
                return 1.0 + lambda_away * rho
            elif h == 1 and a == 1:
                return 1.0 - rho
        return 1.0

    @staticmethod
    def poisson_score_predict(lambda_home, lambda_away, max_goals=7, rho=-0.30):
        """Poisson + Dixon-Coles 比分概率矩阵"""
        from scipy.stats import poisson
        score_probs = {}
        for h in range(max_goals + 1):
            for a in range(max_goals + 1):
                prob_h = poisson.pmf(h, lambda_home)
                prob_a = poisson.pmf(a, lambda_away)
                tau = CalcEngine.dixon_coles_correction(h, a, lambda_home, lambda_away, rho)
                score_probs[f"{h}:{a}"] = prob_h * prob_a * max(0.1, min(3.0, tau))
        total = sum(score_probs.values())
        if total > 0:
            for k in score_probs:
                score_probs[k] /= total
        return score_probs

    @staticmethod
    def monte_carlo_score_predict(lambda_home, lambda_away, n_sim=500, max_goals=7):
        """Monte Carlo 比分模拟"""
        home_goals = np.random.poisson(lambda_home, n_sim)
        away_goals = np.random.poisson(lambda_away, n_sim)
        home_goals = np.clip(home_goals, 0, max_goals)
        away_goals = np.clip(away_goals, 0, max_goals)
        score_counts = {}
        for h, a in zip(home_goals, away_goals):
            key = f"{h}:{a}"
            score_counts[key] = score_counts.get(key, 0) + 1
        return {k: v / n_sim for k, v in score_counts.items()}

    @staticmethod
    def fuse_score_predictions(poisson_probs, mc_probs, score_odds=None,
                               poisson_weight=0.85, mc_weight=0.15, odds_alpha=0.30):
        """融合 Poisson + MC + 比分赔率"""
        all_keys = set(list(poisson_probs.keys()) + list(mc_probs.keys()))
        fused = {}
        for key in all_keys:
            p = poisson_probs.get(key, 0)
            m = mc_probs.get(key, 0)
            fused[key] = p * poisson_weight + m * mc_weight

        if score_odds and len(score_odds) > 0:
            odds_probs = {}
            total_odds = 0.0
            for score, odds in score_odds.items():
                if odds > 0:
                    odds_probs[score] = 1.0 / odds
                    total_odds += 1.0 / odds
            if total_odds > 0:
                for score in odds_probs:
                    odds_probs[score] /= total_odds
                for score in fused:
                    fused[score] = fused[score] * (1 - odds_alpha) + odds_probs.get(score, 0) * odds_alpha

        total = sum(fused.values())
        if total > 0:
            for k in fused:
                fused[k] /= total
        return fused

    @staticmethod
    def calc_total_goals_from_lambda(lambda_home, lambda_away, max_goals=8):
        """从 λ 计算总进球分布 (0,1,2,...,7+)"""
        dist = {}
        for g in range(max_goals + 1):
            dist[g] = 0.0
            for a in range(g + 1):
                b = g - a
                dist[g] += CalcEngine.poisson_pmf(lambda_home, a) * CalcEngine.poisson_pmf(lambda_away, b)
        over25 = sum(dist.get(g, 0) for g in range(3, max_goals + 1))
        under25 = sum(dist.get(g, 0) for g in range(0, 3))
        total = over25 + under25
        if total > 0:
            over25 /= total
            under25 /= total
        return {'distribution': dist, 'over25': over25, 'under25': under25}


# ============================================================
# 特征构建
# ============================================================
def compute_hcp_features_from_odds(hcp_win, hcp_draw, hcp_lose):
    """从让球赔率实时计算 15 维 HCP 特征"""
    eps = 1e-10
    inv_w = 1.0 / max(hcp_win, eps)
    inv_d = 1.0 / max(hcp_draw, eps)
    inv_l = 1.0 / max(hcp_lose, eps)
    total = inv_w + inv_d + inv_l
    p_win = inv_w / total
    p_draw = inv_d / total
    p_lose = inv_l / total

    probs = np.array([p_win, p_draw, p_lose])

    return {
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
        'hcp_favorite_margin': abs(p_win - p_lose),
        'hcp_balance': (p_win - p_lose) / max(p_draw, eps),
        'hcp_upset_risk': p_lose / max(p_win, eps),
        'hcp_odds_skew': float(np.max(probs) - np.min(probs)),
    }


def compute_wdl_draw_features(wdl_win, wdl_draw, wdl_lose):
    """从 WDL 赔率计算平局相关特征"""
    hp, dp, ap = odds_to_implied_prob(wdl_win, wdl_draw, wdl_lose)
    return {
        'wdl_draw_odds': wdl_draw,
        'wdl_draw_prob': dp,
        'draw_divergence': dp - 0.5 * (hp + ap),
    }


# ============================================================
# 模型加载器
# ============================================================
def load_wdl_model():
    """加载 WDL 胜平负模型 (LightGBM + XGBoost)"""
    log_model('WDL模型', '加载', '开始加载WDL模型...')

    try:
        import glob

        def find_latest(suffix):
            files = glob.glob(os.path.join(ASSETS_DIR, suffix))
            return max(files, key=os.path.getctime) if files else None

        lgb_pkl = find_latest('lgb_model_*.pkl')
        xgb_pkl = find_latest('xgb_model_*.pkl')
        scaler_pkl = find_latest('scaler_*.pkl')
        feat_pkl = find_latest('selected_features_*.pkl')

        if lgb_pkl:
            log_model('WDL模型', '加载', f'全局LGB模型: {lgb_pkl}')
        if xgb_pkl:
            log_model('WDL模型', '加载', f'XGBoost模型: {xgb_pkl}')
        if scaler_pkl:
            log_model('WDL模型', '加载', f'Scaler: {scaler_pkl}')
        if feat_pkl:
            log_model('WDL模型', '加载', f'特征列表: {feat_pkl}')

        lgb_model = None
        xgb_model = None

        if lgb_pkl:
            with open(lgb_pkl, 'rb') as f:
                lgb_model = pickle.load(f)
            n_trees = getattr(lgb_model, 'n_estimators', None) or getattr(lgb_model, 'n_estimators_', None)
            if n_trees is None and hasattr(lgb_model, 'booster_'):
                n_trees = lgb_model.booster_.num_trees()
            if n_trees is None:
                n_trees = '?'
            log_model('WDL模型', '加载', f'LGB: {type(lgb_model).__name__}, {n_trees}棵树')

        if xgb_pkl:
            with open(xgb_pkl, 'rb') as f:
                xgb_model = pickle.load(f)
            log_model('WDL模型', '加载', f'XGB: {type(xgb_model).__name__}')

        if lgb_model is None and xgb_model is None:
            raise FileNotFoundError("未找到任何WDL模型文件")

        # 加载 scaler
        scaler = None
        if scaler_pkl:
            import joblib
            scaler = joblib.load(scaler_pkl)
            log_model('WDL模型', '加载', f'Scaler loaded: {scaler.n_features_in_} features')

        # 加载特征列表
        features = []
        if feat_pkl:
            features = pickle.load(open(feat_pkl, 'rb'))
            log_model('WDL模型', '加载', f'特征列表: {len(features)}维')

        return {
            'lgb_model': lgb_model, 'xgb_model': xgb_model,
            'scaler': scaler, 'features': features, 'status': 'loaded'
        }

    except Exception as e:
        log_model('WDL模型', '加载', f'加载失败: {e}', 'warning')
        log_model('WDL模型', '降级', '将使用Poisson Stacking + 赔率隐含概率')
        return {
            'lgb_model': None, 'xgb_model': None,
            'scaler': None, 'features': [], 'status': 'degraded'
        }


def load_t005v3_model():
    """加载 T-005 v3 让球模型 (71维, 两阶段)"""
    log_model('T-005 v3', '加载', '开始加载T-005 v3模型...')

    try:
        with open(T005V3_DRAW_MODEL_PATH, 'rb') as f:
            draw_model = pickle.load(f)
        log_model('T-005 v3', '加载', f'draw_detector: {type(draw_model).__name__} (n_features={draw_model.n_features_in_})')

        with open(T005V3_DIR_MODEL_PATH, 'rb') as f:
            dir_model = pickle.load(f)
        log_model('T-005 v3', '加载', f'direction_predictor: {type(dir_model).__name__} (n_features={dir_model.n_features_in_})')

        with open(T005V3_ELO_PATH, 'r', encoding='utf-8') as f:
            elo_snapshot = json.load(f)
        log_model('T-005 v3', '加载', f'Elo球队数: {len(elo_snapshot.get("elo_ratings", {}))}')

        return {
            'draw_model': draw_model,
            'dir_model': dir_model,
            'elo_ratings': elo_snapshot.get('elo_ratings', {}),
            'elo_momentum': elo_snapshot.get('elo_momentum', {}),
            'status': 'loaded',
        }
    except Exception as e:
        log_model('T-005 v3', '加载', f'加载失败: {e}', 'warning')
        log_model('T-005 v3', '降级', '将使用赔率隐含概率替代T-005 v3预测')
        return {
            'draw_model': None, 'dir_model': None,
            'elo_ratings': {}, 'elo_momentum': {},
            'status': 'degraded',
        }


def load_epl_model():
    """加载英超独立模型 v3.0 (205维)"""
    log_model('英超模型', '加载', '开始加载英超独立模型 v3.0...')
    try:
        import glob
        epl_dir = os.path.join(ASSETS_DIR, 'epl')
        if not os.path.isdir(epl_dir):
            log_model('英超模型', '加载', 'epl/ 目录不存在', 'warning')
            return None

        def find_latest_epl(suffix):
            files = glob.glob(os.path.join(epl_dir, suffix))
            return max(files, key=os.path.getctime) if files else None

        lgb_pkl = find_latest_epl('lgb_model_epl_v3_*.pkl')
        scaler_pkl = find_latest_epl('scaler_epl_v3_*.pkl')
        feat_pkl = find_latest_epl('selected_features_epl_v3_*.pkl')

        if not lgb_pkl:
            log_model('英超模型', '加载', '未找到英超模型文件', 'warning')
            return None

        with open(lgb_pkl, 'rb') as f:
            lgb_model = pickle.load(f)
        log_model('英超模型', '加载', f'LGB: {type(lgb_model).__name__}, path={os.path.basename(lgb_pkl)}')

        scaler = None
        if scaler_pkl:
            import joblib
            scaler = joblib.load(scaler_pkl)
            log_model('英超模型', '加载', f'Scaler: {scaler.n_features_in_} features')

        features = []
        if feat_pkl:
            features = pickle.load(open(feat_pkl, 'rb'))
            log_model('英超模型', '加载', f'特征列表: {len(features)}维')

        return {
            'lgb_model': lgb_model,
            'scaler': scaler,
            'features': features,
            'status': 'loaded',
        }
    except Exception as e:
        log_model('英超模型', '加载', f'加载失败: {e}', 'warning')
        return None


_BAYESIAN_MODELS_CACHE = {}


def load_bayesian_model(league):
    """加载指定联赛的贝叶斯层级模型（P1-7）。结果缓存，避免重复加载。"""
    global _BAYESIAN_MODELS_CACHE
    if league in _BAYESIAN_MODELS_CACHE:
        return _BAYESIAN_MODELS_CACHE[league]
    try:
        from bayesian_hierarchical_model import BayesianHierarchicalModel
        path = os.path.join(ASSETS_DIR, f"bayesian_model_{league}.json")
        if not os.path.exists(path):
            log_model('贝叶斯', '加载', f'未找到 {os.path.basename(path)}，跳过', 'warning')
            _BAYESIAN_MODELS_CACHE[league] = None
            return None
        model = BayesianHierarchicalModel.load(path)
        _BAYESIAN_MODELS_CACHE[league] = model
        log_model('贝叶斯', '加载', f'{league}: {len(model.teams)} 队, μ={model.mu:.3f} home={model.home_adv:.3f} ρ={model.rho:.3f}')
        return model
    except Exception as e:
        log_model('贝叶斯', '加载', f'加载失败: {e}', 'warning')
        _BAYESIAN_MODELS_CACHE[league] = None
        return None


_STACKING_META_CACHE = {"loaded": False, "data": None}
STACKING_META_MODELS = ["dixonColes", "elo", "xgboost", "lightgbm", "bayesian"]


def _load_stacking_meta_learner():
    """加载 LR meta-learner（P1-7）。结果缓存，失败返回 None。"""
    if _STACKING_META_CACHE["loaded"]:
        return _STACKING_META_CACHE["data"]
    _STACKING_META_CACHE["loaded"] = True
    try:
        path = os.path.join(ASSETS_DIR, "stacking_meta_learner.json")
        if not os.path.exists(path):
            log_model('Stacking', 'Meta', '未找到 stacking_meta_learner.json，回退固定权重', 'warning')
            return None
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        data["coef_"] = np.asarray(data["coef_"], dtype=float)
        data["intercept_"] = np.asarray(data["intercept_"], dtype=float)
        _STACKING_META_CACHE["data"] = data
        log_model('Stacking', 'Meta', f'已加载 meta-learner: coef={data["coef_"].shape}, n_train={data.get("n_train")}')
        return data
    except Exception as e:
        log_model('Stacking', 'Meta', f'加载失败: {e}，回退固定权重', 'warning')
        return None


def apply_stacking_meta_learner(sub_probs):
    """用 LR meta-learner 融合 5 基础模型（P1-7）。

    仅当 meta-learner 已加载且 5 个基础模型全部可用时才返回 WDL 概率字典，
    否则返回 None（由调用方回退固定权重 Stacking）。
    """
    meta = _load_stacking_meta_learner()
    if meta is None:
        return None
    models = meta.get("models", STACKING_META_MODELS)
    feature_names = meta.get("feature_names", [])
    if not feature_names:
        return None
    for m in models:
        if sub_probs.get(m) is None:
            return None

    x = np.zeros(len(feature_names), dtype=float)
    for i, name in enumerate(feature_names):
        model, cls = name.rsplit("__", 1)
        x[i] = float(sub_probs[model][cls])

    logits = meta["intercept_"] + meta["coef_"] @ x
    z = np.exp(logits - logits.max())
    proba = z / z.sum()
    # classes [0,1,2] = [客胜, 平, 主胜]；输出与 stack_wdl_probabilities 对齐
    return {"win": float(proba[2]), "draw": float(proba[1]), "lose": float(proba[0])}


def init_models():
    """初始化所有模型"""
    return {
        'wdl': load_wdl_model(),
        't005': load_t005v3_model(),
        'epl': load_epl_model(),
    }


# ============================================================
# WDLPredictor: WDL 胜平负预测
# ============================================================
class WDLPredictor:
    """WDL 胜平负预测器 (5模型 Stacking: DC+XGB+LGB+Elo+Bayes，英超独立模型作参考)"""

    def __init__(self, wdl_models, epl_model=None):
        self.wdl_models = wdl_models
        self.epl_model = epl_model
        self._lgb_failed = False
        self._xgb_failed = False
        self._bayes_failed = False
        self._cached_df = None
        self._cached_X = None

    def predict(self, match, odds_data, t005_models=None, is_mock=False):
        """
        WDL 胜平负预测 (5模型 Stacking)
        优先级: LR meta-learner → 5模型 Stacking → 赔率隐含概率
        """
        home_team = match['home_team']
        away_team = match['away_team']
        home_cn = match.get('home_team_cn', home_team)
        away_cn = match.get('away_team_cn', away_team)
        # C-20260823-003: LGB/XGB 队名匹配时使用归一化后的中文名（与 load_match_data_odds 一致）
        from feature_utils import normalize_team_name, build_all_features, load_match_data_odds
        home_norm = normalize_team_name(home_cn)
        away_norm = normalize_team_name(away_cn)
        league = match.get('league', match.get('league_cn', ''))

        log_model('WDL模型', '预测', f'开始预测: {home_cn} vs {away_cn}')

        wdl = odds_data['wdl_odds']
        last = wdl['close'] or wdl['records'][-1] if wdl['records'] else {'win': 2.0, 'draw': 3.4, 'lose': 3.0}
        first = wdl['open'] or wdl['records'][0] if wdl['records'] else last

        log_model('WDL模型', '赔率', f'尾盘: win={last["win"]:.2f} draw={last["draw"]:.2f} lose={last["lose"]:.2f}')

        # 赔率趋势
        changes = []
        wc = last['win'] - first['win']
        dc = last['draw'] - first['draw']
        lc = last['lose'] - first['lose']
        if wc < -0.05:
            changes.append(f"主胜赔率↓{abs(wc):.2f}，市场看好主队")
        elif wc > 0.05:
            changes.append(f"主胜赔率↑{wc:.2f}，信心减弱")
        if dc < -0.05:
            changes.append(f"平局赔率↓{abs(dc):.2f}，平局可能性↑")
        elif dc > 0.05:
            changes.append(f"平局赔率↑{dc:.2f}，平局可能性↓")
        if lc < -0.05:
            changes.append(f"客胜赔率↓{abs(lc):.2f}，看好客队")
        elif lc > 0.05:
            changes.append(f"客胜赔率↑{lc:.2f}，客队信心减弱")
        trend = "; ".join(changes) if changes else "赔率整体稳定，市场观点未发生显著变化"

        # 降级: 赔率隐含概率 (always available)
        hp_odds, dp_odds, ap_odds = odds_to_implied_prob(last['win'], last['draw'], last['lose'])
        log_model('WDL模型', '赔率', f'隐含概率: 主胜={hp_odds*100:.1f}% 平局={dp_odds*100:.1f}% 客胜={ap_odds*100:.1f}%')

        model_used = '赔率隐含概率'
        final_hp, final_dp, final_ap = hp_odds, dp_odds, ap_odds
        epl_reference = None  # C-20260829-fix: mock模式下也需初始化，避免UnboundLocalError
        sub_probs = {}  # P1-02: 子模型原始输出（供报告展示 5 子模型概率与分歧度）

        # === 尝试 6模型 Stacking ===
        if not is_mock:
            try:
                # 计算 λ (原始)
                lambda_home_raw, lambda_away_raw = CalcEngine.calc_lambda_from_odds(odds_data)
                log_model('WDL模型', 'Stacking', f'λ_raw: home={lambda_home_raw:.3f}, away={lambda_away_raw:.3f}')

                # A-002 λ 调整
                lambda_home, lambda_away, _ = CalcEngine.adjust_lambda_for_mid_score(
                    lambda_home_raw, lambda_away_raw, odds_data
                )

                # 获取 Elo 数据
                elo_ratings = {}
                elo_momentum = {}
                if t005_models and t005_models.get('elo_ratings'):
                    elo_ratings = t005_models['elo_ratings']
                    elo_momentum = t005_models.get('elo_momentum', {})

                # 子模型预测
                sub_probs = {}

                # 1. Poisson WDL
                poisson_probs = CalcEngine.calc_win_draw_lose_poisson(lambda_home, lambda_away)
                sub_probs['poisson'] = poisson_probs
                log_model('WDL模型', 'Poisson', f'主胜={poisson_probs["win"]*100:.1f}% 平局={poisson_probs["draw"]*100:.1f}% 客胜={poisson_probs["lose"]*100:.1f}%')

                # 2. Dixon-Coles WDL
                dc_probs = CalcEngine.calc_win_draw_lose_dixon_coles(lambda_home, lambda_away)
                sub_probs['dixonColes'] = dc_probs
                log_model('WDL模型', 'DC', f'主胜={dc_probs["win"]*100:.1f}% 平局={dc_probs["draw"]*100:.1f}% 客胜={dc_probs["lose"]*100:.1f}%')

                # 3. SSM (Poisson 均值回归)
                sub_probs['ssm'] = {
                    'win': (poisson_probs['win'] + hp_odds) / 2,
                    'draw': (poisson_probs['draw'] + dp_odds) / 2,
                    'lose': (poisson_probs['lose'] + ap_odds) / 2,
                }
                log_model('WDL模型', 'SSM', f'主胜={sub_probs["ssm"]["win"]*100:.1f}% 平局={sub_probs["ssm"]["draw"]*100:.1f}% 客胜={sub_probs["ssm"]["lose"]*100:.1f}%')

                # 4. Elo WDL
                elo_probs = CalcEngine.calc_win_draw_lose_elo(home_team, away_team, elo_ratings, elo_momentum)
                sub_probs['elo'] = elo_probs
                log_model('WDL模型', 'Elo', f'主胜={elo_probs["win"]*100:.1f}% 平局={elo_probs["draw"]*100:.1f}% 客胜={elo_probs["lose"]*100:.1f}%')

                # 4b. 贝叶斯层级模型 (P1-7) — 分联赛纯赛果统计模型
                if not self._bayes_failed:
                    try:
                        bayes_model = load_bayesian_model(league)
                        if bayes_model is not None:
                            bayes_probs = bayes_model.predict_wdl(home_norm, away_norm)
                            sub_probs['bayesian'] = bayes_probs
                            log_model('WDL模型', '贝叶斯', f'主胜={bayes_probs["win"]*100:.1f}% 平局={bayes_probs["draw"]*100:.1f}% 客胜={bayes_probs["lose"]*100:.1f}%')
                        else:
                            log_model('WDL模型', '贝叶斯', f'{league} 无贝叶斯模型，跳过', 'warning')
                    except Exception as e:
                        self._bayes_failed = True
                        log_model('WDL模型', '贝叶斯', f'推理异常: {e}，后续跳过', 'warning')

                # 5. 英超独立模型 (参考，不参与Stacking)
                epl_reference = None
                if '英超' in league and self.epl_model and self.epl_model.get('status') == 'loaded':
                    try:
                        epl_result = self._predict_epl(match, odds_data)
                        if epl_result:
                            epl_reference = epl_result
                            log_model('WDL模型', '英超独立模型', f'参考: 主胜={epl_result["win"]*100:.1f}% 平局={epl_result["draw"]*100:.1f}% 客胜={epl_result["lose"]*100:.1f}% (不参与Stacking)')
                    except Exception as e:
                        log_model('WDL模型', '英超独立模型', f'推理失败: {e}，跳过', 'warning')

                # 6. LightGBM / XGBoost (基于归一化中文名 + home_team_name/away_team_name 列匹配历史数据)
                # C-20260823-003: 修复两处 bug — (a) 列名错误 home_team→home_team_name, (b) 队名未归一化语言不匹配
                # C-20260823-020: 新增实时 sofascore_team_features 查询 fallback — 当缓存特征矩阵中无匹配时，
                #   从 odds.db 的 sofascore_team_features 表实时查询 52 维球员特征，配合赔率实时计算，
                #   组装完整 165 维特征向量，避免 LGB/XGB 因新赛季比赛无历史数据而退化为纯 Poisson

                # 通用: 尝试从缓存特征矩阵提取特征，失败时触发 fallback
                def _get_feature_vector_for_match(home_norm, away_norm, odds_data, scaler, features):
                    """从缓存特征矩阵或实时构建特征向量 (C-20260823-020)"""
                    if self._cached_df is None:
                        df = load_match_data_odds()
                        self._cached_df = df
                        self._cached_X, _ = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
                    df = self._cached_df
                    X_all = self._cached_X

                    mask = (df['home_team_name'] == home_norm) | (df['away_team_name'] == away_norm)
                    if mask.sum() > 0 and scaler is not None:
                        match_idx = df[mask].index[-1]
                        if match_idx < len(X_all):
                            available = [f for f in features if f in X_all.columns]
                            if len(available) == scaler.n_features_in_:
                                X_vec = X_all.iloc[match_idx:match_idx+1][available].values
                                return X_vec, f'缓存命中 (idx={match_idx}, matches={mask.sum()})'

                    # C-20260823-020: Fallback — 实时查询 sofascore_team_features
                    log_model('WDL模型', 'Fallback', '缓存未命中，尝试实时查询 sofascore_team_features...')
                    try:
                        import sqlite3
                        db_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'data', 'odds.db')
                        conn = sqlite3.connect(db_path)
                        cur = conn.cursor()

                        # 查询 sofascore_team_features 中主客队的球员特征
                        cur.execute('''SELECT * FROM sofascore_team_features
                                       WHERE league=? AND home_team_cn=? AND away_team_cn=?
                                       ORDER BY match_date DESC LIMIT 1''',
                                    (match.get('league', ''), home_norm, away_norm))
                        sofa_row = cur.fetchone()

                        if not sofa_row:
                            conn.close()
                            return None, '无实时数据'

                        # 获取列名
                        sofa_cols = [desc[0] for desc in cur.description]
                        conn.close()

                        # 用 df 的最后一行作为模板特征向量
                        if len(df) == 0:
                            return None, '特征矩阵为空'
                        template_idx = len(df) - 1
                        template_vec = X_all.iloc[template_idx:template_idx+1]

                        # 构建特征向量: 从模板复制，然后覆盖 sofascore 特征
                        available = [f for f in features if f in template_vec.columns]
                        if len(available) != scaler.n_features_in_:
                            return None, f'模板特征维度不匹配 ({len(available)}/{scaler.n_features_in_})'

                        X_vec = template_vec[available].values.copy()
                        feature_list = available

                        # 覆盖 sofascore 球员特征 (T-007, 前缀 sofa_)
                        sofa_feature_map = {}
                        for col_name in sofa_cols:
                            if col_name.startswith('sofa_'):
                                sofa_feature_map[col_name] = sofa_row[sofa_cols.index(col_name)]

                        sofa_count = 0
                        for j, feat in enumerate(feature_list):
                            if feat in sofa_feature_map and sofa_feature_map[feat] is not None:
                                X_vec[0, j] = float(sofa_feature_map[feat])
                                sofa_count += 1

                        log_model('WDL模型', 'Fallback',
                                  f'实时注入: {sofa_count}/{len(sofa_feature_map)} 个 sofascore 特征, '
                                  f'来源: {home_norm} vs {away_norm}')
                        return X_vec, f'实时查询 ({sofa_count} sofa特征)'
                    except Exception as e:
                        log_model('WDL模型', 'Fallback', f'实时查询失败: {e}', 'warning')
                        return None, f'查询异常: {e}'

                if self.wdl_models['lgb_model'] is not None and not self._lgb_failed:
                    try:
                        lgb = self.wdl_models['lgb_model']
                        import pandas as pd
                        X_vec, source = _get_feature_vector_for_match(
                            home_norm, away_norm, odds_data,
                            self.wdl_models['scaler'], self.wdl_models['features']
                        )
                        if X_vec is not None and self.wdl_models['scaler'] is not None:
                            X_scaled = self.wdl_models['scaler'].transform(X_vec)
                            lgb_raw = lgb.predict(X_scaled)[0]
                            sub_probs['lightgbm'] = {
                                'win': lgb_raw[2], 'draw': lgb_raw[1], 'lose': lgb_raw[0]
                            }
                            log_model('WDL模型', 'LGB', f'主胜={lgb_raw[2]*100:.1f}% 平局={lgb_raw[1]*100:.1f}% 客胜={lgb_raw[0]*100:.1f}% [{source}]')
                        else:
                            log_model('WDL模型', 'LGB', f'特征提取失败: {source}，跳过', 'warning')
                    except Exception as e:
                        self._lgb_failed = True
                        import traceback
                        log_model('WDL模型', 'LGB', f'推理异常: {e}，后续跳过', 'warning')
                        traceback.print_exc()

                # XGBoost
                if self.wdl_models['xgb_model'] is not None and not self._xgb_failed:
                    try:
                        xgb = self.wdl_models['xgb_model']
                        if self.wdl_models['scaler'] is not None and self._cached_df is not None:
                            X_vec, source = _get_feature_vector_for_match(
                                home_norm, away_norm, odds_data,
                                self.wdl_models['scaler'], self.wdl_models['features']
                            )
                            if X_vec is not None:
                                X_scaled = self.wdl_models['scaler'].transform(X_vec)
                                import xgboost as _xgb
                                xgb_dmat = _xgb.DMatrix(X_scaled)
                                xgb_raw = xgb.predict(xgb_dmat)[0]
                                sub_probs['xgboost'] = {
                                    'win': xgb_raw[2], 'draw': xgb_raw[1], 'lose': xgb_raw[0]
                                }
                                log_model('WDL模型', 'XGB', f'主胜={xgb_raw[2]*100:.1f}% 平局={xgb_raw[1]*100:.1f}% 客胜={xgb_raw[0]*100:.1f}% [{source}]')
                            else:
                                log_model('WDL模型', 'XGB', f'特征提取失败: {source}，跳过', 'warning')
                        else:
                            log_model('WDL模型', 'XGB', f'scaler/cache未就绪，跳过', 'warning')
                    except Exception as e:
                        self._xgb_failed = True
                        import traceback
                        log_model('WDL模型', 'XGB', f'推理异常: {e}，后续跳过', 'warning')
                        traceback.print_exc()

                # Stacking 融合 (P1-7: 优先 LR meta-learner，回退固定权重)
                available_models = [name for name in sub_probs if sub_probs[name] is not None]
                if len(available_models) >= 2:
                    meta_probs = apply_stacking_meta_learner(sub_probs)
                    if meta_probs is not None:
                        final_hp, final_dp, final_ap = meta_probs['win'], meta_probs['draw'], meta_probs['lose']
                        model_used = f"LR meta-learner ({len(available_models)}/5)"
                        log_model('WDL模型', 'Stacking', f'Meta融合: 主胜={final_hp*100:.1f}% 平局={final_dp*100:.1f}% 客胜={final_ap*100:.1f}%')
                    else:
                        stacked = CalcEngine.stack_wdl_probabilities(sub_probs, STACKING_WEIGHTS)
                        final_hp, final_dp, final_ap = stacked['win'], stacked['draw'], stacked['lose']
                        model_used = f"5模型 Stacking ({len(available_models)}/5: {', '.join(available_models)})"
                        log_model('WDL模型', 'Stacking', f'固定权重融合: 主胜={final_hp*100:.1f}% 平局={final_dp*100:.1f}% 客胜={final_ap*100:.1f}%')
                else:
                    log_model('WDL模型', 'Stacking', f'可用模型不足 ({len(available_models)}个)，降级为赔率', 'warning')

            except Exception as e:
                log_model('WDL模型', 'Stacking', f'5模型Stacking失败: {e}，降级为赔率', 'warning')
                import traceback
                traceback.print_exc()

        # 决策阈值
        wdl_pred = '主胜' if final_hp > max(final_dp, final_ap) else ('平局' if final_dp > final_ap else '客胜')
        if final_dp * WDL_DRAW_THRESHOLD_FACTOR > max(final_hp, final_ap):
            wdl_pred = '平局'
        conf = max(final_hp, final_dp, final_ap)

        log_model('WDL模型', '决策', f'预测={wdl_pred} 置信度={conf*100:.1f}% method={model_used}')

        return {
            'home_prob': float(final_hp),
            'draw_prob': float(final_dp),
            'away_prob': float(final_ap),
            'prediction': wdl_pred,
            'confidence': float(conf),
            'open': first,
            'close': last,
            'records': wdl['records'],
            'trend': trend,
            'method': model_used,
            'model_used': model_used,
            'epl_reference': epl_reference,  # C-20260823-002: 英超独立模型参考(不参与Stacking)
            'sub_models': sub_probs,  # P1-02: 5 子模型原始概率 {name: {win,draw,lose}}，供报告展示
        }

    def _predict_epl(self, match, odds_data):
        """英超独立模型 v3.0 推理 (205维) — 对接 feature_utils.build_all_features()"""
        if not self.epl_model or self.epl_model.get('status') != 'loaded':
            log_model('英超模型', '推理', '模型未加载，跳过', 'warning')
            return None

        home_team = match['home_team']
        away_team = match['away_team']
        home_cn = match.get('home_team_cn', home_team)
        away_cn = match.get('away_team_cn', away_team)

        log_model('英超模型', '推理', f'=== 开始推理: {home_cn} vs {away_cn} ===')

        epl_lgb = self.epl_model.get('lgb_model')
        epl_scaler = self.epl_model.get('scaler')
        epl_features = self.epl_model.get('features', [])

        # === 第1步: 模型完整性校验 ===
        if epl_lgb is None:
            log_model('英超模型', '校验', 'LGB模型为空，跳过', 'error')
            return None
        if epl_scaler is None:
            log_model('英超模型', '校验', 'Scaler为空，跳过', 'error')
            return None
        if not epl_features:
            log_model('英超模型', '校验', '特征列表为空，跳过', 'error')
            return None

        log_model('英超模型', '校验',
                  f'模型就绪: {len(epl_features)}维特征, '
                  f'scaler期望={epl_scaler.n_features_in_}维, '
                  f'LGB类型={type(epl_lgb).__name__}')

        try:
            from feature_utils import build_all_features, load_match_data_odds
            import pandas as pd

            # === 第2步: 加载/复用比赛数据 ===
            df = self._cached_df
            X_all = self._cached_X

            if df is None or X_all is None:
                log_model('英超模型', '数据加载', '缓存未命中，尝试加载odds.db...')
                df = load_match_data_odds()
                if df is None or len(df) == 0:
                    log_model('英超模型', '数据加载', 'odds.db为空或无数据，无法构造特征', 'error')
                    return None
                self._cached_df = df
                log_model('英超模型', '数据加载', f'加载完成: {len(df)}场比赛, shape={df.shape}')

                # 构建全量特征矩阵
                log_model('英超模型', '特征构造', '调用 build_all_features(include_odds=True)...')
                X_all, _ = build_all_features(df, include_odds=True)
                self._cached_X = X_all
                log_model('英超模型', '特征构造', f'特征矩阵: shape={X_all.shape}, 列数={X_all.shape[1]}')
                log_model('英超模型', '特征构造', f'前5列: {list(X_all.columns[:5])}')
                log_model('英超模型', '特征构造', f'后5列: {list(X_all.columns[-5:])}')
            else:
                log_model('英超模型', '数据加载', f'缓存命中: {len(df)}场比赛, 特征shape={X_all.shape}')

            # === 第3步: 匹配当前比赛 ===
            # 尝试多种匹配: 英文名、中文名、主客互换
            mask = (
                (df['home_team_name'].isin([home_team, home_cn])) &
                (df['away_team_name'].isin([away_team, away_cn]))
            )

            match_count = mask.sum()
            if match_count == 0:
                log_model('英超模型', '匹配',
                          f'正向匹配失败: home=[{home_team}, {home_cn}], away=[{away_team}, {away_cn}]，尝试反向...')
                mask = (
                    (df['home_team_name'].isin([away_team, away_cn])) &
                    (df['away_team_name'].isin([home_team, home_cn]))
                )
                match_count = mask.sum()

            if match_count == 0:
                log_model('英超模型', '匹配',
                          f'反向匹配也失败，在{len(df)}场比赛中未找到 {home_cn} vs {away_cn}，'
                          f'尝试构造新行追加到DataFrame...', 'warning')

                # 构造新比赛行
                new_row = pd.DataFrame([{
                    'date': pd.Timestamp.now(),
                    'home_team_name': home_team,
                    'away_team_name': away_team,
                    'homeGoals': np.nan,
                    'awayGoals': np.nan,
                    'result': np.nan,
                    'goal_diff': np.nan,
                    'total_goals': np.nan,
                    'competition_name': '英超',
                }])
                log_model('英超模型', '匹配', f'新行构造: date={new_row["date"].iloc[0]}, competition=英超')

                df = pd.concat([df, new_row], ignore_index=True)
                self._cached_df = df
                log_model('英超模型', '匹配', f'追加后DataFrame: {len(df)}行')

                # 重新构建特征（含新行）
                log_model('英超模型', '特征构造', '重新调用 build_all_features(include_odds=True, ts_odds=True)...')
                X_all, _ = build_all_features(df, include_odds=True, ts_odds=True, consensus_odds=True)
                self._cached_X = X_all
                match_idx = len(df) - 1
                log_model('英超模型', '特征构造', f'重建完成: shape={X_all.shape}, 新行索引={match_idx}')
            else:
                match_idx = df[mask].index[-1]
                matched_home = df.loc[match_idx, 'home_team_name'] if match_idx < len(df) else '?'
                matched_away = df.loc[match_idx, 'away_team_name'] if match_idx < len(df) else '?'
                log_model('英超模型', '匹配',
                          f'找到比赛: 索引={match_idx}, '
                          f'匹配场次={match_count}, '
                          f'队名=[{matched_home} vs {matched_away}]')

            # === 第4步: 提取特征向量 ===
            if match_idx >= len(X_all):
                log_model('英超模型', '特征提取', f'索引{match_idx}越界(len={len(X_all)})', 'error')
                return None

            X_match = X_all.iloc[match_idx:match_idx+1]
            log_model('英超模型', '特征提取', f'X_match shape={X_match.shape}')

            # 检查特征可用性
            available = [f for f in epl_features if f in X_match.columns]
            missing = [f for f in epl_features if f not in X_match.columns]
            available_ratio = len(available) / max(len(epl_features), 1) * 100

            log_model('英超模型', '特征提取',
                      f'特征匹配: {len(available)}/{len(epl_features)} 可用 ({available_ratio:.1f}%), '
                      f'缺失{len(missing)}个')

            if missing:
                missing_sample = missing[:10]
                log_model('英超模型', '特征提取', f'缺失特征(前10): {missing_sample}', 'warning')
                if len(missing) > 10:
                    log_model('英超模型', '特征提取', f'... 还有{len(missing)-10}个缺失特征')

            # 构造对齐 scaler 的特征向量
            scaler_dim = epl_scaler.n_features_in_
            if len(available) < scaler_dim:
                fill_count = scaler_dim - len(available)
                log_model('英超模型', '特征提取',
                          f'特征不足: 需要{scaler_dim}维, 实际{len(available)}维, '
                          f'填充{fill_count}个0值', 'warning')

                # 按 epl_features 顺序构造，缺失填0
                X_vec = np.zeros((1, scaler_dim))
                filled_features = []
                for i, feat in enumerate(epl_features[:scaler_dim]):
                    if feat in X_match.columns:
                        val = X_match[feat].values[0]
                        X_vec[0, i] = val if not pd.isna(val) else 0.0
                    else:
                        filled_features.append(feat)
                if filled_features:
                    log_model('英超模型', '特征提取', f'填充特征(前10): {filled_features[:10]}')
            else:
                # 按 epl_features 顺序提取
                X_vec = X_match[epl_features[:scaler_dim]].values
                # 处理 NaN
                nan_count = np.isnan(X_vec).sum()
                if nan_count > 0:
                    log_model('英超模型', '特征提取', f'NaN值: {nan_count}个, 填充为0', 'warning')
                    X_vec = np.nan_to_num(X_vec, nan=0.0)

            log_model('英超模型', '特征提取',
                      f'特征向量: shape={X_vec.shape}, '
                      f'range=[{X_vec.min():.4f}, {X_vec.max():.4f}], '
                      f'mean={X_vec.mean():.4f}, '
                      f'nonzero={np.count_nonzero(X_vec)}/{scaler_dim}')

            # === 第5步: 缩放 ===
            X_scaled = epl_scaler.transform(X_vec)
            log_model('英超模型', '缩放',
                      f'缩放后: range=[{X_scaled.min():.4f}, {X_scaled.max():.4f}], '
                      f'mean={X_scaled.mean():.4f}')

            # === 第6步: 推理 ===
            probs = epl_lgb.predict_proba(X_scaled)[0]
            log_model('英超模型', '推理', f'raw probs: {probs} (shape={probs.shape})')

            if probs.shape[0] == 3:
                result = {
                    'win': float(probs[2]),
                    'draw': float(probs[1]),
                    'lose': float(probs[0])
                }
            elif probs.shape[0] == 2:
                # 二分类情况（极少见），构造三类
                log_model('英超模型', '推理', f'二分类输出({probs.shape[0]}类)，构造平局概率', 'warning')
                result = {
                    'win': float(probs[1]) * 0.7,
                    'draw': float(probs[1]) * 0.15 + float(probs[0]) * 0.15,
                    'lose': float(probs[0]) * 0.7
                }
            else:
                log_model('英超模型', '推理', f'输出维度异常: {probs.shape}', 'error')
                return None

            log_model('英超模型', '推理',
                      f'=== 推理完成: 主胜={result["win"]*100:.1f}% '
                      f'平局={result["draw"]*100:.1f}% '
                      f'客胜={result["lose"]*100:.1f}% ===')

            return result

        except Exception as e:
            log_model('英超模型', '推理', f'推理异常: {type(e).__name__}: {e}', 'error')
            import traceback
            log_model('英超模型', '推理', traceback.format_exc(), 'error')
            return None


# ============================================================
# HandicapPredictor: T-005 v3 让球预测
# ============================================================
class HandicapPredictor:
    """T-005 v3 让球预测器 (71维两阶段)"""

    def __init__(self, t005_models):
        self.t005_models = t005_models

    def predict(self, match, odds_data, is_mock=False):
        """T-005 v3 让球预测 (含降级)"""
        home_cn = match.get('home_team_cn', match['home_team'])
        away_cn = match.get('away_team_cn', match['away_team'])
        log_model('T-005 v3', '预测', f'开始预测: {home_cn} vs {away_cn}')

        hcp = odds_data['handicap_odds']
        hcp_line = hcp['line']
        if hcp['close']:
            h = hcp['close']
        elif hcp['records']:
            h = hcp['records'][-1]
        else:
            h = {'win': 0, 'draw': 0, 'lose': 0}

        # 尝试 T-005 v3 模型
        if self.t005_models['draw_model'] is not None:
            try:
                X, feature_quality = self._build_t005v3_feature_vector(match, odds_data, is_mock)
                result = self._predict_t005v3_handicap(X)

                return {
                    'line': hcp_line,
                    'home_win_prob': result['probabilities']['上盘赢'],
                    'draw_prob': result['probabilities']['走水'],
                    'away_win_prob': result['probabilities']['下盘赢'],
                    'prediction': result['prediction'],
                    'confidence': result['confidence'],
                    'open': hcp.get('open', {}),
                    'close': h,
                    'method': f"T-005 v3 两阶段模型 (T={T005V3_TEMPERATURE}, θ={T005V3_THRESHOLD})",
                    'feature_quality': feature_quality,
                    'raw_p_draw': result['raw_p_draw'],
                }
            except Exception as e:
                log_model('T-005 v3', '预测', f'推理失败: {e}', 'warning')
                import traceback
                traceback.print_exc()

        # 降级: 赔率隐含概率
        log_model('T-005 v3', '降级', '使用赔率隐含概率')
        if h['win'] > 0 and h['draw'] > 0 and h['lose'] > 0:
            hcp_hp, hcp_dp, hcp_ap = odds_to_implied_prob(h['win'], h['draw'], h['lose'])
            hcp_pred = '上盘赢' if hcp_hp > max(hcp_dp, hcp_ap) else ('走水' if hcp_dp > hcp_ap else '下盘赢')
            hcp_conf = max(hcp_hp, hcp_dp, hcp_ap)
        else:
            hcp_hp = hcp_dp = hcp_ap = 0
            hcp_pred = '数据不足'
            hcp_conf = 0

        return {
            'line': hcp_line,
            'home_win_prob': hcp_hp, 'draw_prob': hcp_dp, 'away_win_prob': hcp_ap,
            'prediction': hcp_pred, 'confidence': hcp_conf,
            'open': hcp.get('open', {}), 'close': h,
            'method': '赔率隐含概率 (T-005 v3 降级)',
        }

    def _build_t005v3_feature_vector(self, match, odds_data, is_mock=False):
        """构造 T-005 v3 71维特征向量"""
        home_team = match['home_team']
        away_team = match['away_team']
        log_model('T-005 v3', '特征构造', f'开始: {home_team} vs {away_team}')

        features = {}

        # 1. HCP 赔率特征 (15维)
        hcp = odds_data['handicap_odds']
        h = hcp['close'] or hcp['records'][-1] if hcp['records'] else {'win': 2.0, 'draw': 3.3, 'lose': 3.0}
        hcp_line = hcp['line']
        _hcp_disp = f"{hcp_line:+.2f}" if hcp_line is not None else "N/A"
        log_model('T-005 v3', '特征构造', f'让球盘: {_hcp_disp}球, 尾盘: ({h["win"]:.2f}, {h["draw"]:.2f}, {h["lose"]:.2f})')

        hcp_features = compute_hcp_features_from_odds(h['win'], h['draw'], h['lose'])
        features.update(hcp_features)
        log_model('T-005 v3', '特征构造', f'HCP赔率特征: {len(hcp_features)}维 (实时计算)')

        # 2. WDL 平局特征 (3维)
        wdl = odds_data['wdl_odds']
        w = wdl['close'] or wdl['records'][-1] if wdl['records'] else {'win': 2.0, 'draw': 3.4, 'lose': 3.0}
        wdl_features = compute_wdl_draw_features(w['win'], w['draw'], w['lose'])
        features.update(wdl_features)
        log_model('T-005 v3', '特征构造', f'WDL平局特征: {len(wdl_features)}维 (实时计算)')

        # 3. 球队历史特征 (43维) + Elo (10维)
        if is_mock:
            hist_home = {}
            hist_away = {}
            elo_data = {'elo_ratings': {}, 'elo_momentum': {}}
            log_model('T-005 v3', '特征构造', 'Mock模式: 使用空历史特征')
        else:
            hist_home = {}
            hist_away = {}
            elo_data = {'elo_ratings': self.t005_models.get('elo_ratings', {}),
                        'elo_momentum': self.t005_models.get('elo_momentum', {})}

        # 合并历史特征
        for key in T005V3_CORE_FEATURES:
            if key.startswith('home_') and key in hist_home:
                features[key] = hist_home[key]
            elif key.startswith('away_') and key in hist_away:
                features[key] = hist_away[key]
            elif key in hist_home:
                features[key] = hist_home[key]
            elif key in hist_away:
                features[key] = hist_away[key]

        # Elo 特征
        elo_ratings = elo_data.get('elo_ratings', {})
        elo_momentum = elo_data.get('elo_momentum', {})
        home_elo = elo_ratings.get(home_team, 1500)
        away_elo = elo_ratings.get(away_team, 1500)
        home_mom = elo_momentum.get(home_team, 0)
        away_mom = elo_momentum.get(away_team, 0)

        elo_diff = home_elo - away_elo
        elo_feats = {
            'home_elo': home_elo, 'away_elo': away_elo,
            'elo_diff': elo_diff, 'elo_ratio': home_elo / max(away_elo, 1),
            'elo_home_expected': 1.0 / (1.0 + 10 ** ((away_elo - home_elo) / 400.0)),
            'elo_away_expected': 1.0 / (1.0 + 10 ** ((home_elo - away_elo) / 400.0)),
            'elo_draw_prob': 0.25,
            'home_elo_momentum': home_mom, 'away_elo_momentum': away_mom,
            'elo_confidence': (home_elo + away_elo) / 3000.0,
        }
        features.update(elo_feats)
        log_model('T-005 v3', '特征构造', f'Elo: home={home_elo}, away={away_elo}, diff={elo_diff}')

        # 填充缺失特征
        filled_count = 0
        for feat in T005V3_ALL_FEATURES:
            if feat not in features:
                features[feat] = 0.0
                filled_count += 1

        X = np.array([[features[f] for f in T005V3_ALL_FEATURES]])
        completeness = 1.0 - filled_count / len(T005V3_ALL_FEATURES)

        log_model('T-005 v3', '特征构造', f'完成: shape={X.shape}, 完整度={completeness*100:.1f}%, 填充={filled_count}')

        return X, {
            'completeness': completeness,
            'odds_features': 18,
            'history_home_features': sum(1 for k in features if k.startswith('home_')),
            'history_away_features': sum(1 for k in features if k.startswith('away_')),
            'elo_features': 10,
            'median_filled': filled_count,
        }

    def _predict_t005v3_handicap(self, X):
        """T-005 v3 两阶段推理"""
        log_model('T-005 v3', '推理', '=== 两阶段推理开始 ===')

        draw_model = self.t005_models['draw_model']
        dir_model = self.t005_models['dir_model']

        # Stage 1: 走水检测
        p_draw_raw = draw_model.predict_proba(X)[:, 1]
        log_model('T-005 v3', 'Stage1', f'走水检测器原始输出: p_draw={p_draw_raw[0]:.4f} ({p_draw_raw[0]*100:.1f}%)')

        # Stage 2: 方向预测
        p_away_nd = dir_model.predict_proba(X)[:, 1]
        p_home_nd = 1.0 - p_away_nd
        log_model('T-005 v3', 'Stage2', f'方向预测器: p_home_nd={p_home_nd[0]:.4f}, p_away_nd={p_away_nd[0]:.4f}')

        # 融合
        p_home = p_home_nd * (1.0 - p_draw_raw)
        p_away = p_away_nd * (1.0 - p_draw_raw)
        p_draw = p_draw_raw

        total = p_home + p_draw + p_away
        p_home = p_home / total
        p_draw = p_draw / total
        p_away = p_away / total
        log_model('T-005 v3', '融合', f'归一化后: 上盘={p_home[0]:.4f} ({p_home[0]*100:.1f}%), 走水={p_draw[0]:.4f} ({p_draw[0]*100:.1f}%), 下盘={p_away[0]:.4f} ({p_away[0]*100:.1f}%)')

        # 温度缩放
        if T005V3_TEMPERATURE != 1.0:
            p_home = np.exp(np.log(np.clip(p_home, 1e-10, 1.0)) / T005V3_TEMPERATURE)
            p_draw = np.exp(np.log(np.clip(p_draw, 1e-10, 1.0)) / T005V3_TEMPERATURE)
            p_away = np.exp(np.log(np.clip(p_away, 1e-10, 1.0)) / T005V3_TEMPERATURE)
            total = p_home + p_draw + p_away
            p_home = p_home / total
            p_draw = p_draw / total
            p_away = p_away / total
            log_model('T-005 v3', '温度缩放', f'T={T005V3_TEMPERATURE}')

        # 走水概率下限校准
        if T005V3_DRAW_PROB_FLOOR > 0:
            p_draw_before = p_draw[0]
            p_draw = np.maximum(p_draw, T005V3_DRAW_PROB_FLOOR)
            total = p_home + p_draw + p_away
            p_home = p_home / total
            p_draw = p_draw / total
            p_away = p_away / total
            if p_draw[0] > p_draw_before:
                log_model('T-005 v3', '走水下限', f'应用下限 {T005V3_DRAW_PROB_FLOOR*100:.0f}%: 走水 {p_draw_before*100:.1f}%→{p_draw[0]*100:.1f}%')

        # 决策
        p_draw_final = p_draw[0]
        p_home_final = p_home[0]
        p_away_final = p_away[0]

        if p_draw_final >= T005V3_THRESHOLD:
            prediction = '走水'
            confidence = float(p_draw_final)
            log_model('T-005 v3', '决策', f'走水 (p_draw={p_draw_final:.4f} >= θ={T005V3_THRESHOLD})')
        elif p_home_final >= p_away_final:
            prediction = '上盘赢'
            confidence = float(p_home_final)
            log_model('T-005 v3', '决策', f'上盘赢')
        else:
            prediction = '下盘赢'
            confidence = float(p_away_final)
            log_model('T-005 v3', '决策', f'下盘赢')

        return {
            'prediction': prediction,
            'confidence': confidence,
            'raw_p_draw': float(p_draw_raw[0]),
            'probabilities': {
                '上盘赢': float(p_home_final),
                '走水': float(p_draw_final),
                '下盘赢': float(p_away_final),
            }
        }


# ============================================================
# ScorePredictor: T-006 v4 比分预测
# ============================================================
class ScorePredictor:
    """T-006 v4 比分预测器 (Poisson+DC+MC融合+比分赔率)"""

    def predict(self, odds_data, lambda_home=None, lambda_away=None, wdl_probs=None):
        """比分预测 (C-20260823-019: 新增 wdl_probs 参数，实现 WDL→比分 重要性重加权)"""
        score_records = odds_data['score_odds']['records']

        if lambda_home is not None and lambda_away is not None:
            try:
                np.random.seed(42)
                if USE_UNIFIED_ENGINE:
                    # C-20260823-P0-4 阶段 C: 统一引擎比分矩阵替代分散 DC
                    # （前置项 1 后引擎 τ 公式与 CalcEngine 已对齐，同 ρ/max_goals 下矩阵一致）
                    _eng = get_unified_engine()
                    _eng_res = _eng.generate(lambda_home, lambda_away, verbose=False)
                    _sm = _eng_res['score_matrix']
                    poisson_probs = {f"{i}:{j}": float(_sm[i, j])
                                     for i in range(_sm.shape[0]) for j in range(_sm.shape[1])}
                else:
                    poisson_probs = CalcEngine.poisson_score_predict(lambda_home, lambda_away, max_goals=7, rho=T006_RHO)
                mc_probs = CalcEngine.monte_carlo_score_predict(lambda_home, lambda_away, n_sim=T006_MC_SIMULATIONS, max_goals=7)

                score_odds_dict = {}
                if score_records:
                    so = score_records[-1]
                    for d in [so.get('win_odds', {}), so.get('draw_odds', {}), so.get('lose_odds', {})]:
                        for sc, odds in d.items():
                            if odds and odds > 0:
                                score_odds_dict[sc.replace(':', ':')] = float(odds)

                fused = CalcEngine.fuse_score_predictions(poisson_probs, mc_probs, score_odds_dict,
                                                          T006_POISSON_WEIGHT, T006_MC_WEIGHT, T006_SCORE_ODDS_ALPHA)

                # C-20260823-019: WDL→比分 重要性重加权
                # 当 WDL Stacking 预测结果与 Poisson 比分分布不一致时（如 WDL=主胜 但 最可能比分=1:1），
                # 用 WDL 概率对 score 分布做重要性重加权，使比分预测与 WDL 方向一致
                if wdl_probs and wdl_probs.get('win') is not None:
                    # 计算 Poisson 比分分布的边际 WDL 概率
                    poisson_marginal = {'win': 0.0, 'draw': 0.0, 'lose': 0.0}
                    for score, prob in fused.items():
                        parts = score.split(':')
                        if len(parts) == 2:
                            h_goals = int(parts[0])
                            a_goals = int(parts[1])
                            if h_goals > a_goals:
                                poisson_marginal['win'] += prob
                            elif h_goals == a_goals:
                                poisson_marginal['draw'] += prob
                            else:
                                poisson_marginal['lose'] += prob

                    # 重要性比率: ratio = WDL_prob / Poisson_marginal_prob
                    eps = 1e-6
                    ratios = {
                        'win': wdl_probs['win'] / max(poisson_marginal['win'], eps),
                        'draw': wdl_probs['draw'] / max(poisson_marginal['draw'], eps),
                        'lose': wdl_probs['lose'] / max(poisson_marginal['lose'], eps),
                    }

                    # 对每个比分重加权: P'(score) = P(score) × ratio[outcome]
                    reweighted = {}
                    for score, prob in fused.items():
                        parts = score.split(':')
                        if len(parts) == 2:
                            h_goals = int(parts[0])
                            a_goals = int(parts[1])
                            if h_goals > a_goals:
                                outcome = 'win'
                            elif h_goals == a_goals:
                                outcome = 'draw'
                            else:
                                outcome = 'lose'
                            reweighted[score] = prob * ratios[outcome]
                        else:
                            reweighted[score] = prob

                    # 归一化
                    total = sum(reweighted.values())
                    if total > 0:
                        for k in reweighted:
                            reweighted[k] /= total

                    log_model('T-006 v4', 'WDL重加权',
                              f'WDL={wdl_probs["win"]*100:.1f}%/{wdl_probs["draw"]*100:.1f}%/{wdl_probs["lose"]*100:.1f}%, '
                              f'Poisson边际={poisson_marginal["win"]*100:.1f}%/{poisson_marginal["draw"]*100:.1f}%/{poisson_marginal["lose"]*100:.1f}%, '
                              f'ratio={ratios["win"]:.2f}/{ratios["draw"]:.2f}/{ratios["lose"]:.2f}')
                    fused = reweighted

                sorted_probs = sorted(fused.items(), key=lambda x: x[1], reverse=True)

                return {
                    'top5': [{'score': sc, 'prob': pr} for sc, pr in sorted_probs[:5]],
                    'most_likely': sorted_probs[0][0] if sorted_probs else 'N/A',
                    'most_likely_prob': sorted_probs[0][1] if sorted_probs else 0.0,
                    'method': f'T-006 v4 (Poisson+DC+MC+赔率融合, λ={lambda_home:.2f}/{lambda_away:.2f})',
                    'lambda_home': lambda_home,
                    'lambda_away': lambda_away,
                }
            except Exception as e:
                log_model('T-006 v4', '预测', f'T-006 v4失败: {e}，降级为赔率', 'warning')

        # 降级: 赔率隐含概率
        if not score_records:
            return {'top5': [], 'most_likely': 'N/A', 'most_likely_prob': 0.0, 'method': '数据不足'}

        so = score_records[-1]
        all_pairs = []
        for sc, odds in so.get('win_odds', {}).items():
            if odds and odds > 0:
                all_pairs.append((sc, 1.0 / odds))
        for sc, odds in so.get('draw_odds', {}).items():
            if odds and odds > 0:
                all_pairs.append((sc, 1.0 / odds))
        for sc, odds in so.get('lose_odds', {}).items():
            if odds and odds > 0:
                all_pairs.append((sc, 1.0 / odds))

        if not all_pairs:
            return {'top5': [], 'most_likely': 'N/A', 'most_likely_prob': 0.0, 'method': '数据不足'}

        total_inv = sum(inv for _, inv in all_pairs)
        probs = [(sc, inv / total_inv) for sc, inv in all_pairs]
        probs.sort(key=lambda x: x[1], reverse=True)

        return {
            'top5': [{'score': sc, 'prob': pr} for sc, pr in probs[:5]],
            'most_likely': probs[0][0] if probs else 'N/A',
            'most_likely_prob': probs[0][1] if probs else 0.0,
            'method': '赔率隐含概率',
        }


# ============================================================
# TotalGoalsPredictor: 总进球预测
# ============================================================
class TotalGoalsPredictor:
    """总进球预测器 (Poisson λ + 总进球赔率融合)"""

    def predict(self, odds_data, lambda_home=None, lambda_away=None):
        """总进球预测"""
        tg_records = odds_data['tg_odds']['records']

        if lambda_home is not None and lambda_away is not None:
            try:
                if USE_UNIFIED_ENGINE:
                    # C-20260823-P0-4 阶段 C: 引擎 total_goals 分布替代 calc_total_goals_from_lambda
                    # （引擎分布为 0..2*max_goals 归一化，7+ 合并到 7 与融合循环 range(8) 对齐）
                    _eng = get_unified_engine()
                    _eng_res = _eng.generate(lambda_home, lambda_away, verbose=False)
                    _tg = _eng_res['total_goals']
                    _dist = {}
                    for _g in range(7):
                        _dist[_g] = _tg.get(_g, 0)
                    _dist[7] = sum(_tg.get(_gg, 0) for _gg in range(7, 15))
                    tg_lambda = {'distribution': _dist}
                else:
                    tg_lambda = CalcEngine.calc_total_goals_from_lambda(lambda_home, lambda_away)

                if tg_records:
                    tg = tg_records[-1]
                    goals = tg.get('goals', {})
                    if goals:
                        odds_probs = {}
                        total_inv = sum(1.0 / max(float(v), 1e-10) for v in goals.values())
                        for k, v in goals.items():
                            key = int(k.replace('+', '')) if k.replace('+', '').isdigit() else 7
                            odds_probs[key] = (1.0 / max(float(v), 1e-10)) / total_inv

                        fused_dist = {}
                        for g in range(8):
                            p_poisson = tg_lambda['distribution'].get(g, 0)
                            p_odds = odds_probs.get(g, 0)
                            if g == 7:
                                p_poisson = sum(tg_lambda['distribution'].get(gg, 0) for gg in range(7, 9))
                                p_odds = odds_probs.get(7, 0)
                            fused_dist[g] = p_poisson * 0.85 + p_odds * 0.15

                        ft = sum(fused_dist.values())
                        if ft > 0:
                            for g in fused_dist:
                                fused_dist[g] /= ft

                        over25 = sum(fused_dist.get(g, 0) for g in range(3, 8))
                        prediction = '大球(>2.5)' if over25 > 0.5 else '小球(<2.5)'

                        return {
                            'goals': {str(k): v for k, v in fused_dist.items()},
                            'over_25_prob': over25,
                            'prediction': prediction,
                            'method': f'Poisson λ + TG赔率融合 (λ={lambda_home:.2f}/{lambda_away:.2f})',
                            'lambda_home': lambda_home,
                            'lambda_away': lambda_away,
                        }
            except Exception as e:
                log_model('总进球', '预测', f'Poisson λ失败: {e}，降级为赔率', 'warning')

        # 降级: 赔率隐含概率
        if not tg_records:
            return {'goals': {}, 'over_25_prob': 0.0, 'prediction': '数据不足', 'method': '数据不足'}

        tg = tg_records[-1]
        goals = tg.get('goals', {})
        if not goals:
            return {'goals': {}, 'over_25_prob': 0.0, 'prediction': '数据不足', 'method': '数据不足'}

        probs = {}
        total_inv = sum(1.0 / max(float(v), 1e-10) for v in goals.values())
        for k, v in goals.items():
            probs[k] = (1.0 / max(float(v), 1e-10)) / total_inv

        over_25 = sum(probs.get(str(i), 0) for i in range(3, 8))
        prediction = '大球(>2.5)' if over_25 > 0.5 else '小球(<2.5)'

        return {
            'goals': probs,
            'over_25_prob': over_25,
            'prediction': prediction,
            'method': '赔率隐含概率',
        }


# ============================================================
# PredictionCore: 统一预测编排入口
# ============================================================
class PredictionCore:
    """
    统一预测核心 — 所有预测逻辑的唯一入口。
    四维度: WDL + T-005 v3 + T-006 v4 + 总进球
    """

    def __init__(self, models):
        """
        Args:
            models: dict with keys 'wdl', 't005', 'epl'
        """
        self.models = models
        self.calc = CalcEngine()
        self.wdl_predictor = WDLPredictor(models['wdl'], epl_model=models.get('epl'))
        self.hcp_predictor = HandicapPredictor(models['t005'])
        self.score_predictor = ScorePredictor()
        self.tg_predictor = TotalGoalsPredictor()

    def predict_unified(self, match, odds_data, is_mock=False):
        """
        统一预测: 四维度并行预测

        Args:
            match: dict with 'home_team', 'away_team', 'home_team_cn', 'away_team_cn', 'league'
            odds_data: dict with 'wdl_odds', 'handicap_odds', 'score_odds', 'tg_odds'
            is_mock: bool, 是否使用 mock 数据

        Returns:
            dict with 'wdl', 'hcp', 'score', 'tg'
        """
        home_cn = match.get('home_team_cn', match['home_team'])
        away_cn = match.get('away_team_cn', match['away_team'])

        print(f"\n{'='*60}")
        print(f"统一预测: {home_cn} vs {away_cn}")
        print(f"WDL模型状态: {self.models['wdl']['status']}, T-005 v3状态: {self.models['t005']['status']}, Mock: {is_mock}")
        print(f"{'='*60}")

        result = {}

        # 预先计算 λ (原始 + A-002 调整)
        lambda_home, lambda_away = None, None
        lambda_alert = None  # P1-13: λ 主客差值告警标志
        if not is_mock:
            try:
                lambda_home_raw, lambda_away_raw = CalcEngine.calc_lambda_from_odds(odds_data)
                lambda_home, lambda_away, lambda_trace = CalcEngine.adjust_lambda_for_mid_score(
                    lambda_home_raw, lambda_away_raw, odds_data
                )
                # P1-13: 把完整 λ 链路追踪塞进结果，供赛后复盘脚本使用
                lambda_trace["raw"] = {
                    "lambda_home": round(lambda_home_raw, 4),
                    "lambda_away": round(lambda_away_raw, 4),
                }
                result['lambda_trace'] = lambda_trace
                log_model('统一预测', 'λ', f'原始: {lambda_home_raw:.3f}/{lambda_away_raw:.3f} → A-002调整: {lambda_home:.3f}/{lambda_away:.3f}')
                # P1-13: λ 主客差值告警
                lambda_diff = abs(lambda_home - lambda_away)
                if lambda_diff > LAMBDA_DIFF_ALERT_THRESHOLD:
                    lambda_alert = {
                        'triggered': True,
                        'threshold': LAMBDA_DIFF_ALERT_THRESHOLD,
                        'lambda_home': round(lambda_home, 4),
                        'lambda_away': round(lambda_away, 4),
                        'diff': round(lambda_diff, 4),
                        'message': f'λ主客差={lambda_diff:.3f} > {LAMBDA_DIFF_ALERT_THRESHOLD}（两队进球期望极度失衡，需赛后拿真实 xG 复核 λ 链路）',
                    }
                    result['lambda_alert'] = lambda_alert
            except Exception as e:
                log_model('统一预测', 'λ', f'λ计算失败: {e}', 'warning')

        # 1. WDL 胜平负预测 (6模型 Stacking)
        print(f"\n--- WDL 胜平负预测 ('谁赢') ---")
        result['wdl'] = self.wdl_predictor.predict(match, odds_data, self.models['t005'], is_mock)

        # 2. T-005 v3 让球预测
        print(f"\n--- T-005 v3 让球预测 ('赢多少') ---")
        result['hcp'] = self.hcp_predictor.predict(match, odds_data, is_mock)

        # 3. 比分预测 (T-006 v4) — C-20260823-019: 传入 WDL 概率做重要性重加权
        print(f"\n--- 比分预测 (T-006 v4) ---")
        wdl_result = result['wdl']
        wdl_probs = None
        if wdl_result and wdl_result.get('probabilities'):
            wdl_probs = wdl_result['probabilities']
        result['score'] = self.score_predictor.predict(odds_data, lambda_home, lambda_away, wdl_probs=wdl_probs)

        # 4. 总进球预测
        print(f"\n--- 总进球预测 ---")
        result['tg'] = self.tg_predictor.predict(odds_data, lambda_home, lambda_away)

        # 汇总
        print(f"\n{'='*60}")
        print(f"预测汇总: {home_cn} vs {away_cn}")
        print(f"  WDL: {result['wdl']['prediction']} ({result['wdl']['method']})")
        print(f"  让球: {result['hcp']['prediction']} ({result['hcp'].get('method', 'N/A')})")
        print(f"  比分: {result['score']['most_likely']} ({result['score']['most_likely_prob']*100:.1f}%) [{result['score']['method']}]")
        print(f"  总进球: {result['tg']['prediction']} [{result['tg']['method']}]")
        print(f"{'='*60}\n")

        return result


# ============================================================
# 快速入口：直接调用预测
# ============================================================
def quick_predict(match, odds_data, models=None, is_mock=False):
    """
    快速预测入口 — 单场比赛四维度预测。

    Args:
        match: dict with 'home_team', 'away_team', 'league' (optional: 'home_team_cn', 'away_team_cn')
        odds_data: dict with 'wdl_odds', 'handicap_odds', 'score_odds', 'tg_odds'
        models: 预加载的模型 dict (如果为 None 则自动加载)
        is_mock: bool

    Returns:
        dict with 'wdl', 'hcp', 'score', 'tg'
    """
    if models is None:
        models = init_models()

    core = PredictionCore(models)
    return core.predict_unified(match, odds_data, is_mock=is_mock)