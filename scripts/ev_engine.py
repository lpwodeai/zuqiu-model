# -*- coding: utf-8 -*-
"""EV 期望值引擎（第二层决策引擎）。

独立于预测引擎（prediction_core.py）的纯函数模块：
- 输入：三向模型概率 + 三向市场赔率
- 输出：EV 值、价值空间 edge、凯利仓位、投注决策

设计原则：
- 纯函数、无状态、无副作用
- 不依赖 prediction_core、数据库、网络、pandas
- 全部计算用 float，统一保留 4 位小数输出

文档：docs/EV期望值引擎设计文档_v1.0.md
"""

from dataclasses import dataclass, field
from typing import Optional, Tuple, List
from datetime import datetime
from pathlib import Path
import math
import logging

logger = logging.getLogger(__name__)

__all__ = [
    "OddsData",
    "ModelProbabilities",
    "DirectionEV",
    "MatchEVAnalysis",
    "calc_implied_probabilities",
    "remove_vig",
    "calc_ev",
    "calc_edge",
    "calc_kelly",
    "make_decision",
    "analyze_direction",
    "analyze_match",
    "load_ev_config",
    "format_ev_summary",
    "batch_analyze",
]


# ---------------------------------------------------------------------------
# 核心数据结构
# ---------------------------------------------------------------------------

@dataclass
class OddsData:
    """三向赔率数据"""
    home: float          # 主胜赔率，如 2.10
    draw: float          # 平局赔率，如 3.40
    away: float          # 客胜赔率，如 3.25
    timestamp: Optional[str] = None   # 赔率快照时间，如 "2026-08-30 20:00:00"
    odds_type: Optional[str] = None   # 赔率类型："opening"(初盘) / "live"(即时) / "closing"(终盘)

    def validate(self) -> bool:
        """校验赔率数据合法性"""
        if self.home <= 1.0 or self.draw <= 1.0 or self.away <= 1.0:
            return False
        if not all(math.isfinite(x) for x in [self.home, self.draw, self.away]):
            return False
        return True


@dataclass
class ModelProbabilities:
    """模型输出的三向概率"""
    home: float          # 主胜概率，如 0.475 (47.5%)
    draw: float          # 平局概率，如 0.268 (26.8%)
    away: float          # 客胜概率，如 0.257 (25.7%)
    model_name: Optional[str] = None   # 模型名称，如 "WDL-Stacking-v8.3"
    confidence: Optional[float] = None  # 模型置信度（可选）

    def validate(self) -> bool:
        """校验概率数据合法性"""
        if not all(0.0 <= x <= 1.0 for x in [self.home, self.draw, self.away]):
            return False
        total = self.home + self.draw + self.away
        if abs(total - 1.0) > 0.02:
            return False
        return True

    def normalize(self) -> 'ModelProbabilities':
        """归一化概率，确保和为 1"""
        total = self.home + self.draw + self.away
        if total == 0:
            return ModelProbabilities(home=1 / 3, draw=1 / 3, away=1 / 3,
                                      model_name=self.model_name, confidence=self.confidence)
        return ModelProbabilities(
            home=self.home / total,
            draw=self.draw / total,
            away=self.away / total,
            model_name=self.model_name,
            confidence=self.confidence,
        )


@dataclass
class DirectionEV:
    """单个方向（主胜/平局/客胜）的 EV 分析结果"""
    direction: str           # 方向："home" / "draw" / "away"
    direction_cn: str        # 中文方向："主胜" / "平局" / "客胜"
    odds: float              # 该方向赔率
    p_model: float           # 模型概率
    p_implied: float         # 市场隐含概率（含抽水）
    p_market: float          # 去抽水后的市场真实概率
    edge: float              # 价值空间 = p_model - p_market
    ev: float                # 期望值（每注 1 元）
    kelly_full: float        # 全额凯利比例
    kelly_quarter: float     # 策略调整后的凯利比例（full/2 或 /4）
    kelly_clipped: float     # 裁剪后的凯利比例（上限 cap）
    decision: str            # 决策："VALUE" / "MARGINAL" / "AVOID"


@dataclass
class MatchEVAnalysis:
    """一场比赛的完整 EV 分析结果"""
    # 基础信息
    match_id: Optional[str] = None
    league: Optional[str] = None
    home_team: Optional[str] = None
    away_team: Optional[str] = None

    # 输入数据快照
    odds: Optional[OddsData] = None
    probs: Optional[ModelProbabilities] = None

    # 市场分析
    total_implied: float = 0.0       # 三向隐含概率之和（含抽水）
    vig: float = 0.0                  # 抽水率 = total_implied - 1
    payout_rate: float = 0.0          # 返还率 = 1 / total_implied

    # 三方向分析
    home_analysis: Optional[DirectionEV] = None
    draw_analysis: Optional[DirectionEV] = None
    away_analysis: Optional[DirectionEV] = None

    # 综合决策
    best_direction: Optional[str] = None       # EV 最高的方向
    best_direction_cn: Optional[str] = None    # 中文方向
    best_ev: float = 0.0                        # 最高 EV 值
    best_edge: float = 0.0                      # 最高价值空间
    best_kelly: float = 0.0                     # 建议仓位（策略调整后，已裁剪）
    overall_decision: str = "AVOID"             # 全场决策："VALUE"/"MARGINAL"/"AVOID"
    recommended_stake_pct: float = 0.0          # 建议仓位百分比（0-1）
    match_ev_category: str = "ALL_AVOID"        # 分层标记：ALL_AVOID / HAS_MARGINAL / HAS_VALUE（P1-11）

    # 阈值记录（用于回测和复盘）
    ev_threshold: float = 0.02
    kelly_cap: float = 0.25
    kelly_strategy: str = "quarter"

    # 风险提示
    risk_warnings: List[str] = field(default_factory=list)

    # 元数据
    calculated_at: Optional[str] = None


# ---------------------------------------------------------------------------
# 核心函数
# ---------------------------------------------------------------------------

def calc_implied_probabilities(odds: OddsData) -> Tuple[float, float, float]:
    """计算三向市场隐含概率（含抽水）。公式：p_implied = 1 / odds。"""
    if not odds.validate():
        raise ValueError(f"赔率数据不合法: home={odds.home}, draw={odds.draw}, away={odds.away}")

    p_implied_home = 1.0 / odds.home
    p_implied_draw = 1.0 / odds.draw
    p_implied_away = 1.0 / odds.away

    logger.debug("隐含概率: 主胜=%.4f, 平局=%.4f, 客胜=%.4f",
                 p_implied_home, p_implied_draw, p_implied_away)
    return (p_implied_home, p_implied_draw, p_implied_away)


def remove_vig(p_implied_home: float, p_implied_draw: float, p_implied_away: float
               ) -> Tuple[float, float, float, float, float]:
    """去抽水，将含抽水的隐含概率归一化为真实市场概率。

    返回: (p_market_home, p_market_draw, p_market_away, total_implied, vig)
    """
    total_implied = p_implied_home + p_implied_draw + p_implied_away

    if total_implied <= 0:
        raise ValueError(f"隐含概率总和不合法: {total_implied}")

    p_market_home = p_implied_home / total_implied
    p_market_draw = p_implied_draw / total_implied
    p_market_away = p_implied_away / total_implied

    vig = total_implied - 1.0
    payout_rate = 1.0 / total_implied

    # 正常情况下 total_implied 应在 1.03 ~ 1.20 之间，超出则记录 warning 但不报错
    if total_implied < 1.03 or total_implied > 1.20:
        logger.warning("隐含概率总和异常: %.4f（可能为特殊盘口或数据有误）", total_implied)

    logger.debug("去抽水: 总隐含=%.4f, 抽水=%.4f(%.1f%%), 返还率=%.4f",
                 total_implied, vig, vig * 100, payout_rate)
    logger.debug("市场概率: 主胜=%.4f, 平局=%.4f, 客胜=%.4f",
                 p_market_home, p_market_draw, p_market_away)

    return (p_market_home, p_market_draw, p_market_away, total_implied, vig)


def calc_ev(p_model: float, odds: float) -> float:
    """计算单方向期望值（每注 1 元）。公式：EV = p_model * (odds - 1) - (1 - p_model)。"""
    if not (0.0 <= p_model <= 1.0):
        raise ValueError(f"概率不合法: {p_model}")
    if odds <= 1.0:
        raise ValueError(f"赔率不合法: {odds}")

    return p_model * (odds - 1.0) - (1.0 - p_model)


def calc_edge(p_model: float, p_market: float) -> float:
    """计算价值空间（模型概率 vs 市场真实概率的差值）。公式：edge = p_model - p_market。"""
    if not (0.0 <= p_model <= 1.0):
        raise ValueError(f"模型概率不合法: {p_model}")
    if not (0.0 <= p_market <= 1.0):
        raise ValueError(f"市场概率不合法: {p_market}")
    return p_model - p_market


def calc_kelly(p_model: float, odds: float, strategy: str = "quarter", cap: float = 0.25
               ) -> Tuple[float, float, float]:
    """计算凯利下注比例。

    返回: (kelly_full, kelly_strategy, kelly_clipped)
    """
    if not (0.0 <= p_model <= 1.0):
        raise ValueError(f"概率不合法: {p_model}")
    if odds <= 1.0:
        raise ValueError(f"赔率不合法: {odds}")
    if strategy not in ("full", "half", "quarter"):
        raise ValueError(f"凯利策略不合法: {strategy}，必须是 full/half/quarter")
    if not (0.0 < cap <= 1.0):
        raise ValueError(f"仓位上限不合法: {cap}")

    b = odds - 1.0  # 净赔率
    q = 1.0 - p_model  # 失败概率

    # 全额凯利
    if abs(b) < 1e-12:
        kelly_full = 0.0
    else:
        kelly_full = (b * p_model - q) / b

    # 策略调整
    strategy_factor = {"full": 1.0, "half": 2.0, "quarter": 4.0}[strategy]
    kelly_strategy = kelly_full / strategy_factor

    # 裁剪：不小于 0，不大于 cap
    kelly_clipped = min(max(kelly_strategy, 0.0), cap)

    logger.debug("凯利: p=%.4f, odds=%.2f, b=%.2f, full=%.4f, %s=%.4f, clipped=%.4f(cap=%.2f)",
                 p_model, odds, b, kelly_full, strategy, kelly_strategy, kelly_clipped, cap)

    return (kelly_full, kelly_strategy, kelly_clipped)


def make_decision(ev: float, edge: float, ev_threshold: float = 0.02) -> str:
    """生成单方向投注决策。

    决策规则:
        EV > ev_threshold 且 edge > 0  → VALUE
        EV > 0 且 edge > 0              → MARGINAL
        EV <= 0 或 edge <= 0            → AVOID
    """
    if ev_threshold < 0:
        raise ValueError(f"EV阈值不能为负: {ev_threshold}")

    if ev > ev_threshold and edge > 0:
        return "VALUE"
    elif ev > 0 and edge > 0:
        return "MARGINAL"
    else:
        return "AVOID"


def analyze_direction(
    direction: str,
    p_model: float,
    odds: float,
    p_implied: float,
    p_market: float,
    ev_threshold: float = 0.02,
    kelly_strategy: str = "quarter",
    kelly_cap: float = 0.25,
) -> DirectionEV:
    """分析单个方向（主胜/平局/客胜）的完整 EV 指标。"""
    direction_cn_map = {"home": "主胜", "draw": "平局", "away": "客胜"}
    direction_cn = direction_cn_map.get(direction, direction)

    edge = calc_edge(p_model, p_market)
    ev = calc_ev(p_model, odds)
    kelly_full, kelly_strat, kelly_clipped = calc_kelly(p_model, odds, kelly_strategy, kelly_cap)
    decision = make_decision(ev, edge, ev_threshold)

    return DirectionEV(
        direction=direction,
        direction_cn=direction_cn,
        odds=odds,
        p_model=p_model,
        p_implied=p_implied,
        p_market=p_market,
        edge=edge,
        ev=ev,
        kelly_full=kelly_full,
        kelly_quarter=kelly_strat,
        kelly_clipped=kelly_clipped,
        decision=decision,
    )


def analyze_match(
    probs: ModelProbabilities,
    odds: OddsData,
    ev_threshold: float = 0.02,
    kelly_strategy: str = "quarter",
    kelly_cap: float = 0.25,
    match_id: Optional[str] = None,
    league: Optional[str] = None,
    home_team: Optional[str] = None,
    away_team: Optional[str] = None,
) -> MatchEVAnalysis:
    """主函数：对一场比赛进行完整的 EV 分析。"""
    # 1. 校验输入
    if not probs.validate():
        raise ValueError(f"模型概率不合法: home={probs.home}, draw={probs.draw}, away={probs.away}")
    if not odds.validate():
        raise ValueError(f"赔率数据不合法: home={odds.home}, draw={odds.draw}, away={odds.away}")

    # 2. 归一化模型概率
    probs_norm = probs.normalize()

    # 3. 计算市场隐含概率（含抽水）
    p_implied_home, p_implied_draw, p_implied_away = calc_implied_probabilities(odds)

    # 4. 去抽水
    p_market_home, p_market_draw, p_market_away, total_implied, vig = remove_vig(
        p_implied_home, p_implied_draw, p_implied_away
    )
    payout_rate = 1.0 / total_implied

    # 5. 分别分析三个方向
    home_analysis = analyze_direction(
        "home", probs_norm.home, odds.home, p_implied_home, p_market_home,
        ev_threshold, kelly_strategy, kelly_cap
    )
    draw_analysis = analyze_direction(
        "draw", probs_norm.draw, odds.draw, p_implied_draw, p_market_draw,
        ev_threshold, kelly_strategy, kelly_cap
    )
    away_analysis = analyze_direction(
        "away", probs_norm.away, odds.away, p_implied_away, p_market_away,
        ev_threshold, kelly_strategy, kelly_cap
    )

    # 6. 选择投注方向。
    #    注：make_decision 的优先级与 EV 严格单调递增——
    #       EV <= 0  => AVOID（且 EV>0 恒有 edge>0，见 analyze_direction 的推导）
    #       0 < EV <= ev_threshold => MARGINAL
    #       EV > ev_threshold       => VALUE
    #    因此"EV 最高"者与"决策优先级最高"者必然一致。这里显式按
    #    "非 AVOID 且 EV 最高"选取，避免今后调整决策阈值时两个 max 逻辑漂移。
    directions = [home_analysis, draw_analysis, away_analysis]
    candidates = [d for d in directions if d.decision != "AVOID"]

    if not candidates:
        # 全方向 AVOID：回报"最不难看"方向（最大 EV，可能为负）仅供风险提示参考，实际不下注
        best = max(directions, key=lambda d: d.ev)
        overall_decision = "AVOID"
        best_direction = None
        best_direction_cn = None
        best_ev = best.ev
        best_edge = best.edge
        best_kelly = 0.0
        recommended_stake_pct = 0.0
    else:
        best = max(candidates, key=lambda d: d.ev)
        overall_decision = best.decision
        best_direction = best.direction
        best_direction_cn = best.direction_cn
        best_ev = best.ev
        best_edge = best.edge
        best_kelly = best.kelly_clipped
        recommended_stake_pct = best.kelly_clipped if best.decision == "VALUE" else best.kelly_clipped * 0.5

    # P1-11: EV 分层统计标记（供双轨回测 EV 切片，HAS_VALUE 优先于 HAS_MARGINAL）
    _decisions = {d.decision for d in directions}
    if "VALUE" in _decisions:
        match_ev_category = "HAS_VALUE"
    elif "MARGINAL" in _decisions:
        match_ev_category = "HAS_MARGINAL"
    else:
        match_ev_category = "ALL_AVOID"

    # 8. 风险提示
    risk_warnings = []
    if 0 < best_ev < ev_threshold * 1.5:
        risk_warnings.append(f"EV接近阈值({best_ev*100:.1f}%)，概率误差可能导致EV转负")
    if vig > 0.12:
        risk_warnings.append(f"抽水率偏高({vig*100:.1f}%)，市场利润空间被压缩")
    if total_implied < 1.03:
        risk_warnings.append("隐含概率总和异常偏低，可能赔率数据有误")
    if probs_norm.home == probs_norm.draw == probs_norm.away:
        risk_warnings.append("模型三向概率完全相等，可能模型未正常工作")
    if best_kelly >= kelly_cap * 0.9:
        risk_warnings.append(f"建议仓位接近上限({best_kelly*100:.1f}%)，注意风险控制")

    # 组装结果
    result = MatchEVAnalysis(
        match_id=match_id,
        league=league,
        home_team=home_team,
        away_team=away_team,
        odds=odds,
        probs=probs_norm,
        total_implied=total_implied,
        vig=vig,
        payout_rate=payout_rate,
        home_analysis=home_analysis,
        draw_analysis=draw_analysis,
        away_analysis=away_analysis,
        best_direction=best_direction,
        best_direction_cn=best_direction_cn,
        best_ev=best_ev,
        best_edge=best_edge,
        best_kelly=best_kelly,
        overall_decision=overall_decision,
        recommended_stake_pct=recommended_stake_pct,
        match_ev_category=match_ev_category,
        ev_threshold=ev_threshold,
        kelly_cap=kelly_cap,
        kelly_strategy=kelly_strategy,
        risk_warnings=risk_warnings,
        calculated_at=datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )

    logger.info("EV分析完成: %s vs %s | 决策=%s | 最佳方向=%s | EV=%.2f%% | 仓位=%.1f%%",
                home_team, away_team, overall_decision, best_direction_cn,
                best_ev * 100, recommended_stake_pct * 100)

    return result


# ---------------------------------------------------------------------------
# 辅助函数
# ---------------------------------------------------------------------------

def format_ev_summary(analysis: MatchEVAnalysis) -> str:
    """生成 EV 分析的文本摘要（用于日志和报告）。"""
    lines = []
    lines.append("=" * 60)
    lines.append(f"EV分析: {analysis.home_team} vs {analysis.away_team}")
    lines.append(f"联赛: {analysis.league} | 比赛ID: {analysis.match_id}")
    lines.append("=" * 60)
    lines.append("")
    lines.append("市场分析:")
    lines.append(f"  隐含概率总和: {analysis.total_implied*100:.1f}%")
    lines.append(f"  抽水率: {analysis.vig*100:.1f}%")
    lines.append(f"  返还率: {analysis.payout_rate*100:.1f}%")
    lines.append("")
    lines.append(f"{'方向':<6} {'赔率':>6} {'模型概率':>8} {'市场概率':>8} {'edge':>8} {'EV':>8} {'凯利(1/4)':>10} {'决策':>10}")
    lines.append("-" * 70)
    for d in [analysis.home_analysis, analysis.draw_analysis, analysis.away_analysis]:
        lines.append(f"{d.direction_cn:<6} {d.odds:>6.2f} {d.p_model*100:>7.1f}% {d.p_market*100:>7.1f}% "
                     f"{d.edge*100:>+7.1f}% {d.ev*100:>+7.2f}% {d.kelly_clipped*100:>9.1f}% {d.decision:>10}")
    lines.append("")
    lines.append(f"综合决策: {analysis.overall_decision}")
    if analysis.best_direction:
        lines.append(f"推荐方向: {analysis.best_direction_cn}")
        lines.append(f"最佳EV: {analysis.best_ev*100:.2f}%")
        lines.append(f"建议仓位: {analysis.recommended_stake_pct*100:.1f}%")
    if analysis.risk_warnings:
        lines.append("")
        lines.append("风险提示:")
        for w in analysis.risk_warnings:
            lines.append(f"  ⚠ {w}")
    lines.append("")
    return "\n".join(lines)


def batch_analyze(matches_data: list) -> list:
    """批量分析多场比赛的 EV。

    参数:
        matches_data: 比赛数据列表，每个元素是一个字典:
            {
                'match_id': '...',
                'league': '英超',
                'home_team': '...',
                'away_team': '...',
                'probs': {'home': 0.45, 'draw': 0.30, 'away': 0.25},
                'odds': {'home': 2.10, 'draw': 3.40, 'away': 3.25}
            }

    返回:
        MatchEVAnalysis 对象列表（失败的比赛对应 None）
    """
    results = []
    for match in matches_data:
        try:
            probs = ModelProbabilities(**match['probs'])
            odds = OddsData(**match['odds'])
            ev = analyze_match(
                probs=probs,
                odds=odds,
                match_id=match.get('match_id'),
                league=match.get('league'),
                home_team=match.get('home_team'),
                away_team=match.get('away_team'),
            )
            results.append(ev)
        except Exception as e:
            logger.error("比赛 %s EV分析失败: %s", match.get('match_id'), e)
            results.append(None)
    return results


def load_ev_config(config_path: Optional[str] = None) -> dict:
    """P1-10: 从全局配置文件读取 EV 决策参数（阈值 / 凯利策略 / 仓位上限）。

    默认读取项目根目录 config.yaml 的 ``ev:`` 段；缺失或读取失败时回退硬编码默认值，
    保证 ev_engine 仍可作为纯函数独立使用（不破坏无状态设计）。

    返回 dict: {ev_threshold_default, kelly_default_strategy, kelly_upper_cap}
    """
    defaults = {
        "ev_threshold_default": 0.02,
        "kelly_default_strategy": "quarter",
        "kelly_upper_cap": 0.25,
    }
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / "config.yaml"
    try:
        import yaml
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
    except Exception as e:  # 文件缺失 / yaml 不可用 / 字段异常 → 回退默认，不阻断预测
        logger.warning("读取 EV 配置失败(%s)，回退默认阈值/仓位: %s", e, defaults)
        return defaults

    ev_cfg = cfg.get("ev") or {}
    try:
        out = {
            "ev_threshold_default": float(ev_cfg.get("ev_threshold_default", defaults["ev_threshold_default"])),
            "kelly_default_strategy": str(ev_cfg.get("kelly_default_strategy", defaults["kelly_default_strategy"])),
            "kelly_upper_cap": float(ev_cfg.get("kelly_upper_cap", defaults["kelly_upper_cap"])),
        }
    except (TypeError, ValueError) as e:
        logger.warning("EV 配置字段非法(%s)，回退默认阈值/仓位: %s", e, defaults)
        return defaults
    return out