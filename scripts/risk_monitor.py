# -*- coding: utf-8 -*-
"""风险监控模块（P3-02）。

在回测 / 模拟实盘阶段，对第二层 EV 决策引擎的下注结果持续监控关键风险指标：
  - 最大回撤（Max Drawdown）
  - 最长连亏 / 当前连亏（Longest Losing Streak）
  - 盈亏比（Profit Factor）
  - EV 偏差（avg EV vs 实际平注 ROI，检测 EV 信号是否被系统性高估）

触发阈值自动告警，并可联动复盘（输出告警清单）。设计为纯函数库 + CLI 双形态：
  - 库调用：`from risk_monitor import compute_risk_metrics, evaluate_risk`
  - CLI：`python risk_monitor.py --json reports/dual_track_backtest_*.json`
  - 可被 dual_track_backtest.py 直接 import 复用，避免指标口径漂移。

输入 bets 约定（list[dict]），每注 1 单位平注：
  {'won': bool, 'odds': float, 'profit': float, 'ev': float, ...}
  profit = 命中 ? (odds-1) : -1.0
"""
import argparse
import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Iterable, Optional

REPORT_DIR = Path(__file__).resolve().parent.parent / "reports"

__all__ = [
    "RiskConfig", "RiskReport", "compute_risk_metrics", "evaluate_risk",
    "format_risk_report",
]


@dataclass
class RiskConfig:
    """风险告警阈值（可配置）。"""
    max_drawdown_alert: float = 0.20      # 最大回撤超过 20% 告警
    current_drawdown_alert: float = 0.15  # 当前回撤超过 15% 告警
    losing_streak_alert: int = 8          # 最长连亏超过 8 场告警
    profit_factor_min: float = 0.85       # 盈亏比低于 0.85 告警
    ev_bias_alert: float = 0.25           # 平均EV-实际ROI 差距超 25pp 告警（信号系统性高估）
    min_bets: int = 50                    # 少于该下注数不触发统计告警（样本不足）


@dataclass
class RiskReport:
    n_bets: int = 0
    n_wins: int = 0
    hit_rate: float = 0.0
    flat_roi: float = 0.0
    flat_profit: float = 0.0
    max_drawdown: float = 0.0
    current_drawdown: float = 0.0
    longest_losing_streak: int = 0
    current_losing_streak: int = 0
    profit_factor: Optional[float] = None   # 无穷大时 None，表示无亏损
    avg_ev: float = 0.0
    avg_odds: float = 0.0
    ev_bias: float = 0.0                    # avg_ev - flat_roi
    alerts: list = field(default_factory=list)


def compute_risk_metrics(bets: Iterable[dict]) -> RiskReport:
    """由下注结果序列计算全套风险指标。"""
    bets = list(bets)
    rep = RiskReport()
    rep.n_bets = len(bets)
    if rep.n_bets == 0:
        return rep

    profits = []
    wins = 0
    ev_sum = 0.0
    odds_sum = 0.0
    gross_win = 0.0
    gross_loss = 0.0
    longest = cur = 0
    equity = 0.0
    peak = 0.0
    max_dd = 0.0

    for b in bets:
        profit = float(b.get("profit", 0.0))
        odds = float(b.get("odds", 0.0))
        ev = float(b.get("ev", 0.0))
        won = bool(b.get("won", False))
        profits.append(profit)
        wins += 1 if won else 0
        ev_sum += ev
        odds_sum += odds
        if profit > 0:
            gross_win += profit
        else:
            gross_loss += -profit
        # 连亏
        if won:
            cur = 0
        else:
            cur += 1
            longest = max(longest, cur)
        # 回撤（单位平注，权益 = 累计盈亏）
        equity += profit
        peak = max(peak, equity)
        max_dd = max(max_dd, peak - equity)

    rep.n_wins = wins
    rep.hit_rate = wins / rep.n_bets
    rep.flat_profit = sum(profits)
    rep.flat_roi = rep.flat_profit / rep.n_bets
    rep.max_drawdown = max_dd / rep.n_bets if rep.n_bets else 0.0  # 以总下注额归一
    rep.current_drawdown = (peak - equity) / rep.n_bets if rep.n_bets else 0.0
    rep.longest_losing_streak = longest
    # current losing streak：取尾部连续亏损长度
    tail = 0
    for b in reversed(bets):
        if b.get("won"):
            break
        tail += 1
    rep.current_losing_streak = tail
    rep.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else None
    rep.avg_ev = ev_sum / rep.n_bets
    rep.avg_odds = odds_sum / rep.n_bets
    rep.ev_bias = rep.avg_ev - rep.flat_roi
    return rep


def evaluate_risk(rep: RiskReport, cfg: Optional[RiskConfig] = None) -> RiskReport:
    """基于阈值生成告警清单（就地写入 rep.alerts 并返回）。"""
    cfg = cfg or RiskConfig()
    rep.alerts = []
    if rep.n_bets < cfg.min_bets:
        rep.alerts.append(f"下注样本不足（{rep.n_bets}<{cfg.min_bets}），风险指标仅供参考")
        return rep
    if rep.max_drawdown >= cfg.max_drawdown_alert:
        rep.alerts.append(
            f"最大回撤 {rep.max_drawdown*100:.1f}% 超过阈值 {cfg.max_drawdown_alert*100:.0f}%")
    if rep.current_drawdown >= cfg.current_drawdown_alert:
        rep.alerts.append(
            f"当前回撤 {rep.current_drawdown*100:.1f}% 超过阈值 {cfg.current_drawdown_alert*100:.0f}%")
    if rep.longest_losing_streak >= cfg.losing_streak_alert:
        rep.alerts.append(
            f"最长连亏 {rep.longest_losing_streak} 场，达到阈值 {cfg.losing_streak_alert}")
    if rep.profit_factor is not None and rep.profit_factor < cfg.profit_factor_min:
        rep.alerts.append(
            f"盈亏比 {rep.profit_factor:.2f} 低于阈值 {cfg.profit_factor_min}")
    if rep.ev_bias >= cfg.ev_bias_alert:
        rep.alerts.append(
            f"EV 偏差 {rep.ev_bias*100:+.1f}pp（平均EV {rep.avg_ev*100:.1f}% vs 实际ROI "
            f"{rep.flat_roi*100:+.1f}%），EV 信号被系统性高估，需复核校准/择场")
    return rep


def format_risk_report(rep: RiskReport) -> str:
    L = []
    L.append("=" * 60)
    L.append("风险监控报告（P3-02）")
    L.append("=" * 60)
    L.append(f"下注场次: {rep.n_bets}")
    L.append(f"命中率:   {rep.hit_rate*100:.1f}%")
    L.append(f"平注ROI:  {rep.flat_roi*100:+.2f}%")
    L.append(f"平注盈亏: {rep.flat_profit:+.2f} 单位")
    L.append(f"最大回撤: {rep.max_drawdown*100:.2f}%")
    L.append(f"当前回撤: {rep.current_drawdown*100:.2f}%")
    L.append(f"最长连亏: {rep.longest_losing_streak} 场")
    L.append(f"当前连亏: {rep.current_losing_streak} 场")
    L.append(f"盈亏比:   {'∞（无亏损）' if rep.profit_factor is None else f'{rep.profit_factor:.2f}'}")
    L.append(f"平均EV:   {rep.avg_ev*100:+.2f}%")
    L.append(f"平均赔率: {rep.avg_odds:.2f}")
    L.append(f"EV偏差:   {rep.ev_bias*100:+.2f}pp")
    if rep.alerts:
        L.append("-" * 60)
        L.append("告警清单:")
        for a in rep.alerts:
            L.append(f"  ⚠️ {a}")
    else:
        L.append("-" * 60)
        L.append("✅ 无告警")
    L.append("=" * 60)
    return "\n".join(L)


def _load_bets_from_json(path: Path):
    data = json.loads(path.read_text(encoding="utf-8"))
    # 兼容 dual_track_backtest 的 bets，也兼容 ev_backtest 的 bets
    bets = data.get("bets") or data.get("track_b") or []
    out = []
    for r in bets:
        if "profit" not in r and "flat_profit" in r:
            r = dict(r)
            r["profit"] = r["flat_profit"]
        out.append(r)
    return out


def main():
    parser = argparse.ArgumentParser(description="风险监控（P3-02）")
    parser.add_argument("--json", type=Path, help="回测 JSON 路径（dual_track/ev_backtest 产物）")
    parser.add_argument("--max-dd", type=float, default=0.20)
    parser.add_argument("--streak", type=int, default=8)
    parser.add_argument("--ev-bias", type=float, default=0.25)
    args = parser.parse_args()

    if args.json and args.json.exists():
        bets = _load_bets_from_json(args.json)
    else:
        parser.error("需提供 --json（回测产物）")

    cfg = RiskConfig(max_drawdown_alert=args.max_dd, losing_streak_alert=args.streak,
                     ev_bias_alert=args.ev_bias)
    rep = compute_risk_metrics(bets)
    rep = evaluate_risk(rep, cfg)
    print(format_risk_report(rep))


if __name__ == "__main__":
    main()