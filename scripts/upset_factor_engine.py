# -*- coding: utf-8 -*-
"""
upset_factor_engine.py — 冷门因子库（C-20260922-050 新增）

================================================================
背景：
  7 场极端误判根因排查（C-20260922-049）暴露当前模型仅依赖
  anomaly_samples 加权库（事后纠偏），缺乏**赛前**冷门风险量化。
  且原 _classify_anomaly 优先级使爆冷类型恒为 0，叠加失败。
  本模块实现赛前多维冷门风险评分，既训练时加权（与 anomaly
  叠加），又推理时降权/熔断（事前预防）。

冷门因子四维（赛前可观察）：
  1. 实力差距 strength_gap     : 赔率隐含胜率差距反向计分（差距越大越可能爆冷）
  2. 赔率背离 odds_divergence : 模型对市场热门方向的过度自信度（model_fav_p - market_fav_p，C-054 重构，原 KL 散度对集体误判型冷门失效）
  3. 近期状态 recent_form     : 主队近 5 场积分 - 客队近 5 场积分（负差距+大差距=爆冷风险）
  4. 战意/赛季阶段 motivation : 赛季早期(<8轮)/保级区(<倒数4)/争冠区(<前3)等极端段位差

综合 upset_risk_score ∈ [0, 1]，≥0.85 触发熔断标志。

train/serve 同源约束（project_memory 硬约束）：
  - 计算函数纯函数（确定性，依赖输入参数），同一函数被训练侧与推理侧共用
  - 任一维度数据缺失时返回 mid_score=0.5 + 标志特征 upset_data_missing=1，
    禁止静默默认（防 train/serve 分布漂移）

落库：
  anomaly_samples.upset_risk_score 列（C-050 schema 新增），扫描时计算并持久化
  仅供分析查询用；训练侧与推理侧均**实时重算** upset_risk_score
  （确保使用最新数据，避免缓存过期）

用法：
  from upset_factor_engine import compute_upset_risk_score
  score_dict = compute_upset_risk_score(
      home_odds=2.5, draw_odds=3.4, away_odds=3.1,
      ml_probs={'主胜': 0.45, '平局': 0.28, '客胜': 0.27},
      home_recent_pts=7, away_recent_pts=4,
      home_league_pos=8, away_league_pos=14, total_teams=20, match_day=5,
  )
  # score_dict: {strength_gap, odds_divergence, recent_form, motivation, upset_risk_score, upset_data_missing}
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional


# ============================================================
# 维度权重（C-20260923 全历史诊断后修正）
# 诊断结论（1462场/156爆冷，条件子集剔除标签自相关）：
#   rf(Elo差距) AUC=0.703 ✅唯一稳健有效 → 权重1.0
#   mt(战意)    AUC=0.518 ❌纯随机 → 移除
#   sg(实力差距) AUC=0.386 ❌反向(标签自相关) → 移除
#   od(赔率背离) 11965场OOF回测：方向背离时模型命中率30.6%<市场39.5%，
#                幅度背离AUC=0.499，overconfidence条件子集AUC=0.483 → 删除
# rf已在主模型D-012 Elo特征(elo_diff等)中，urs不作为主模型特征，
# 定位为投注过滤层（按urs分箱看爆冷命中率/ROI）。
# ============================================================
DIM_WEIGHTS = {
    "recent_form": 1.0,       # rf = Elo差距，唯一有效维度
}

# 熔断阈值（≥此值触发 upset_alert）
UPSET_CIRCUIT_BREAKER_THRESHOLD = 0.85

# 数据缺失时的中性分数（避免 0 极端化或 1 误报）
MISSING_SCORE = 0.5


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def _implied_probs(home_odds: float, draw_odds: float, away_odds: float) -> Optional[Dict[str, float]]:
    """从赔率计算隐含概率（去水归一）。

    与 feature_utils.py赔率去水口径一致：取倒数后按行归一化。
    """
    try:
        if not (home_odds > 0 and draw_odds > 0 and away_odds > 0):
            return None
        p_h = 1.0 / home_odds
        p_d = 1.0 / draw_odds
        p_a = 1.0 / away_odds
        s = p_h + p_d + p_a
        if s <= 0:
            return None
        return {"主胜": p_h / s, "平局": p_d / s, "客胜": p_a / s}
    except Exception:
        return None


def _strength_gap_score(home_odds: float, draw_odds: float, away_odds: float) -> tuple:
    """维度1：实力差距。

    逻辑：实力差距越大（赔率越悬殊），冷门风险越高。
    - 若 home 是大热门（home_odds << away_odds）→ 客队爆冷风险高
    - 若 away 是大热门（away_odds << home_odds）→ 主队爆冷风险高
    - 双方均势 → 冷门概率低

    计分公式：冷门方向隐含概率 × (1 - 热门方向隐含概率)
    即 underdog_p × (1 - favorite_p)
    """
    impl = _implied_probs(home_odds, draw_odds, away_odds)
    if impl is None:
        return MISSING_SCORE, True
    p_h = impl["主胜"]
    p_a = impl["客胜"]
    # underdog 一方的隐含概率
    underdog_p = min(p_h, p_a)
    # 设计目标：
    # - underdog_p=0.5（均势）→ 无明显冷门方，分数低
    # - underdog_p=0.20（中等被低估方）→ 市场最易过度自信，分数最高
    # - underdog_p=0.05（极端热门）→ 真爆冷概率低，分数低
    # 钟形曲线峰值在 underdog_p=0.20
    score = underdog_p * math.exp(-((underdog_p - 0.20) ** 2) / 0.02) * 5.0
    return _clip(score), False


def _odds_divergence_score(ml_probs: Optional[Dict[str, float]],
                           home_odds: float, draw_odds: float, away_odds: float) -> tuple:
    """维度2：赔率背离（C-20260922-054 重构：从 KL 散度改为"热门过度自信度"）。

    原设计缺陷（C-052 审计）：
      KL(P_model || P_market) 在"集体误判型冷门"中失效——爆冷时模型与市场
      同向（都看好热门），KL→0，od 分低，无法识别冷门风险。

    新设计语义：
      衡量**模型对市场热门方向的过度自信程度**。
      - 找市场隐含概率最高的方向（热门 favorite）
      - od = max(0, model_fav_p - market_fav_p) 归一化
      - 模型比市场更自信热门 → 集体误判风险高 → od 高
      - 模型不看好热门（已捕捉冷门信号）→ od 低（模型已定价冷门）

    这样：集体误判（模型跟着市场一起错，且更自信）→ od 高，正确触发；
          模型独立发现冷门 → od 低，不重复触发（模型已自行降权热门）。
    """
    impl = _implied_probs(home_odds, draw_odds, away_odds)
    if impl is None or not ml_probs:
        return MISSING_SCORE, True

    eps = 1e-10
    keys = ["主胜", "平局", "客胜"]
    p_model = [max(ml_probs.get(k, 0.0), eps) for k in keys]
    s_model = sum(p_model)
    if s_model <= 0:
        return MISSING_SCORE, True
    p_model = [p / s_model for p in p_model]

    p_market = [impl[k] for k in keys]

    # 市场热门方向（隐含概率最高）
    fav_idx = max(range(3), key=lambda i: p_market[i])
    model_fav_p = p_model[fav_idx]
    market_fav_p = p_market[fav_idx]

    # 模型对热门的过度自信：model_fav_p - market_fav_p
    # 仅当模型比市场更自信热门时才有冷门风险（正向偏离）
    # 归一化：以 market_fav_p 为基准，偏离 0.1 → 约 0.5，0.2+ → 1.0
    over_confidence = max(0.0, model_fav_p - market_fav_p)
    # 用 market_fav_p 做分母归一：热门越强（market_fav_p 越大），
    # 相同绝对偏离的过度自信越显著
    denom = max(market_fav_p, 0.1)
    score = 1.0 - math.exp(-(over_confidence / denom) * 4.0)
    return _clip(score), False


def _recent_form_score(home_recent_pts: Optional[float],
                       away_recent_pts: Optional[float]) -> tuple:
    """维度3：近期状态。

    逻辑：主队近 5 场积分 - 客队近 5 场积分。
    差距越大（如主 13 客 3），若市场定价已反映则无冷门；
    若市场未充分反映则爆冷（客队胜）风险高。

    计分公式：|diff| / max_pts × sign_factor
    简化：|diff| / 15（15 分=近 5 场满分差距），capped at 1.0
    """
    if home_recent_pts is None or away_recent_pts is None:
        return MISSING_SCORE, True
    diff = float(home_recent_pts) - float(away_recent_pts)
    # 差距绝对值映射到 [0, 1]，15 分差距封顶
    score = min(abs(diff) / 15.0, 1.0)
    return _clip(score), False


def _motivation_score(home_league_pos: Optional[int],
                      away_league_pos: Optional[int],
                      total_teams: int = 20,
                      match_day: Optional[int] = None) -> tuple:
    """维度4：战意/赛季阶段。

    逻辑：
    - 赛季早期 (<8 轮) → 冷门概率高（积分差距未拉开，强队未上状态）
    - 保级区 (倒数 4 名) → 保级队战意强，爆冷风险高
    - 争冠区 (前 3 名) → 争冠队战意强，但弱队对其爆冷概率显著
    
    计分：
    - 早期阶段 boost: match_day < 8 时 +0.3
    - 保级战意 boost: 任一队在倒数 4 名 +0.3
    - 争冠战意 boost: 任一队在前 3 名 +0.2
    - 综合到 [0, 1]
    """
    if home_league_pos is None or away_league_pos is None or total_teams <= 0:
        return MISSING_SCORE, True

    score = 0.0
    # 早期阶段 boost
    if match_day is not None and match_day < 8:
        score += 0.3
    # 保级区 boost（任一队在倒数 4 名）
    if home_league_pos > total_teams - 4 or away_league_pos > total_teams - 4:
        score += 0.3
    # 争冠区 boost（任一队在前 3 名）
    if home_league_pos <= 3 or away_league_pos <= 3:
        score += 0.2
    # 段位差 boost（双方段位差距大）
    pos_diff = abs(home_league_pos - away_league_pos)
    score += min(pos_diff / 15.0, 0.2)

    return _clip(score), False


def compute_upset_risk_score(
    home_odds: Optional[float] = None,
    draw_odds: Optional[float] = None,
    away_odds: Optional[float] = None,
    ml_probs: Optional[Dict[str, float]] = None,
    home_recent_pts: Optional[float] = None,
    away_recent_pts: Optional[float] = None,
    home_league_pos: Optional[int] = None,
    away_league_pos: Optional[int] = None,
    total_teams: int = 20,
    match_day: Optional[int] = None,
    # C-055: 直接传入队名+比赛日，自动从 Elo/积分榜查询 rf/mt（train/serve 同源）
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
    match_date: Optional[str] = None,
) -> Dict[str, Any]:
    """计算赛前冷门风险综合评分。

    参数：
    - home_odds/draw_odds/away_odds: 赔率（去水前原值）
    - ml_probs: 模型 WDL 概率字典 {'主胜': p, '平局': p, '客胜': p}
    - home_recent_pts/away_recent_pts: 近 5 场积分（可选，若传 home_team/away_team/match_date 则自动用 Elo 差距）
    - home_league_pos/away_league_pos: 当前联赛排名（可选，若传队名+日期则自动用积分榜）
    - total_teams: 联赛总队数（默认 20）
    - match_day: 当前比赛轮次
    - home_team/away_team/match_date: C-055 自动查询 Elo 差距和积分榜排名（推荐用法）
    """
    scores = {}
    missing_flags = {}

    sg, sg_missing = _strength_gap_score(home_odds or 0, draw_odds or 0, away_odds or 0)
    scores["strength_gap"] = sg
    missing_flags["strength_gap"] = sg_missing

    od, od_missing = _odds_divergence_score(ml_probs, home_odds or 0, draw_odds or 0, away_odds or 0)
    scores["odds_divergence"] = od
    missing_flags["odds_divergence"] = od_missing

    # C-055: rf 维 —— 优先用 Elo 差距（自动查询），其次用传入的近期积分差
    if home_team and away_team and match_date:
        try:
            from elo_engine import get_elo_gap
            rf, rf_missing = get_elo_gap(home_team, away_team, match_date), False
        except Exception:
            rf, rf_missing = _recent_form_score(home_recent_pts, away_recent_pts)
    else:
        rf, rf_missing = _recent_form_score(home_recent_pts, away_recent_pts)
    scores["recent_form"] = rf
    missing_flags["recent_form"] = rf_missing

    # C-055: mt 维 —— 优先用积分榜战意信号（自动查询），其次用传入的排名
    if home_team and away_team and match_date:
        try:
            from league_standing import get_motivation_signal
            mt, mt_missing = get_motivation_signal(home_team, away_team, match_date)
        except Exception:
            mt, mt_missing = _motivation_score(home_league_pos, away_league_pos, total_teams, match_day)
    else:
        mt, mt_missing = _motivation_score(home_league_pos, away_league_pos, total_teams, match_day)
    scores["motivation"] = mt
    missing_flags["motivation"] = mt_missing

    # 综合分：加权平均（仅基于可用维度，缺失维度按权重重新归一化）
    available_weight_sum = sum(
        DIM_WEIGHTS[k] for k in DIM_WEIGHTS if not missing_flags[k]
    )
    if available_weight_sum <= 0:
        composite = MISSING_SCORE
        composite_missing = True
    else:
        composite = 0.0
        for k in DIM_WEIGHTS:
            if not missing_flags[k]:
                # 重新归一化权重
                w = DIM_WEIGHTS[k] / available_weight_sum
                composite += w * scores[k]
        composite_missing = False

    # 缺失数 ≥2 时，整体可信度低，置标志
    missing_count = sum(1 for v in missing_flags.values() if v)
    composite_missing_flag = 1 if missing_count >= 2 else 0

    return {
        **scores,
        "upset_risk_score": _clip(composite),
        "upset_data_missing": composite_missing_flag,
        "upset_alert": _clip(composite) >= UPSET_CIRCUIT_BREAKER_THRESHOLD,
    }


def apply_inference_confidence_adjustment(ml_probs: Dict[str, float],
                                          upset_risk_score: float,
                                          upset_data_missing: int = 0) -> Dict[str, float]:
    """推理侧置信度调整：根据冷门风险降低最高概率，提升次概率。

    逻辑：
    - upset_risk_score < 0.3：低风险，不调整
    - 0.3 ≤ score < 0.85：中风险，按比例降低 max_p，分配给其余两方向
    - score ≥ 0.85：高风险熔断，强制压缩 max_p 最多 25%，分配给其余

    返回调整后的概率字典（已归一化）。

    与训练侧 sample_weight 加权**互补不冲突**：
    - 训练侧：sample_weight × (1 + 0.3 × upset_risk_score) — 增加样本权重
    - 推理侧：max_p × (1 - 0.2 × upset_risk_score) — 压缩预测置信度
    """
    if not ml_probs or upset_data_missing:
        return ml_probs or {}

    # 数据缺失时不做调整（避免错误降权）
    if upset_data_missing:
        return ml_probs

    eps = 1e-10
    # 复制一份避免污染输入
    probs = {k: max(v, eps) for k, v in ml_probs.items() if v is not None}
    if not probs:
        return ml_probs

    s = sum(probs.values())
    if s <= 0:
        return ml_probs
    probs = {k: v / s for k, v in probs.items()}

    # 仅在中高风险时调整
    if upset_risk_score < 0.3:
        return probs

    # 找 max 和 rest
    max_key = max(probs, key=probs.get)
    max_p = probs[max_key]
    reduction = 0.2 * min(upset_risk_score, 1.0)  # 最多降低 20%
    if upset_risk_score >= UPSET_CIRCUIT_BREAKER_THRESHOLD:
        reduction = 0.25  # 熔断档固定 25%

    new_max = max_p * (1.0 - reduction)
    delta = max_p - new_max
    probs[max_key] = new_max

    # delta 按其余两方向原比例分配
    rest_keys = [k for k in probs if k != max_key]
    rest_sum = sum(probs[k] for k in rest_keys)
    if rest_sum > 0:
        for k in rest_keys:
            probs[k] += delta * (probs[k] / rest_sum)
    else:
        # 退化：均分
        for k in rest_keys:
            probs[k] += delta / len(rest_keys)

    # 归一化兜底
    s = sum(probs.values())
    if s > 0:
        probs = {k: v / s for k, v in probs.items()}

    return probs


if __name__ == "__main__":
    # 演示：均势场次（低冷门风险）
    print("=== 均势场次 ===")
    r = compute_upset_risk_score(
        home_odds=2.5, draw_odds=3.4, away_odds=2.8,
        ml_probs={"主胜": 0.40, "平局": 0.30, "客胜": 0.30},
        home_recent_pts=7, away_recent_pts=8,
        home_league_pos=8, away_league_pos=7, total_teams=20, match_day=15,
    )
    print(r)

    # 演示：大冷门场次（主队弱客队强 + 模型预测主胜）
    print("\n=== 大冷门场次 ===")
    r = compute_upset_risk_score(
        home_odds=5.0, draw_odds=4.2, away_odds=1.6,
        ml_probs={"主胜": 0.30, "平局": 0.30, "客胜": 0.40},  # 模型和市场分歧大
        home_recent_pts=4, away_recent_pts=12,
        home_league_pos=18, away_league_pos=2, total_teams=20, match_day=6,
    )
    print(r)
    print(f"  熔断标志: {r['upset_alert']}")

    # 演示：调整后概率
    print("\n=== 推理侧概率调整 ===")
    adj = apply_inference_confidence_adjustment(
        {"主胜": 0.65, "平局": 0.25, "客胜": 0.10},
        upset_risk_score=0.7,
    )
    print(adj)
