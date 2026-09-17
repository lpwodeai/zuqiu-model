# -*- coding: utf-8 -*-
"""EV 期望值引擎单元测试（pytest 风格，对标 docs/EV期望值引擎设计文档_v1.0.md §9）。

覆盖：隐含概率、去抽水、EV/edge/kelly 计算、决策规则、完整比赛分析。
"""
import pytest

from ev_engine import (
    calc_implied_probabilities, remove_vig, calc_ev, calc_edge,
    calc_kelly, make_decision, analyze_match,
    ModelProbabilities, OddsData,
)


class TestImpliedProbabilities:
    """隐含概率计算"""

    def test_basic(self):
        odds = OddsData(home=2.0, draw=3.0, away=4.0)
        h, d, a = calc_implied_probabilities(odds)
        assert h == pytest.approx(0.5, abs=1e-4)
        assert d == pytest.approx(1 / 3, abs=1e-4)
        assert a == pytest.approx(0.25, abs=1e-4)

    def test_invalid_odds(self):
        odds = OddsData(home=0.5, draw=3.0, away=4.0)
        with pytest.raises(ValueError):
            calc_implied_probabilities(odds)


class TestRemoveVig:
    """去抽水"""

    def test_normalized(self):
        h, d, a, total, vig = remove_vig(0.5, 1 / 3, 0.25)
        assert h + d + a == pytest.approx(1.0, abs=1e-4)
        assert vig == pytest.approx(0.0833, abs=1e-3)


class TestCalcEV:
    """EV 计算"""

    def test_positive(self):
        assert calc_ev(0.5, 2.20) == pytest.approx(0.1, abs=1e-4)

    def test_negative(self):
        assert calc_ev(0.4, 2.00) == pytest.approx(-0.2, abs=1e-4)

    def test_break_even(self):
        assert calc_ev(0.5, 2.00) == pytest.approx(0.0, abs=1e-4)

    def test_invalid_prob(self):
        with pytest.raises(ValueError):
            calc_ev(p_model=1.5, odds=2.0)


class TestCalcEdge:
    """价值空间"""

    def test_positive_edge(self):
        assert calc_edge(0.5, 0.45) == pytest.approx(0.05, abs=1e-4)


class TestCalcKelly:
    """凯利计算"""

    def test_quarter_strategy(self):
        full, quarter, clipped = calc_kelly(0.55, 2.10, strategy="quarter", cap=0.25)
        assert full == pytest.approx(0.1409, abs=1e-3)
        assert quarter == pytest.approx(0.0352, abs=1e-3)
        assert clipped == pytest.approx(0.0352, abs=1e-3)

    def test_cap_enforced(self):
        _, _, clipped = calc_kelly(0.9, 1.50, strategy="full", cap=0.25)
        assert clipped <= 0.25


class TestMakeDecision:
    """决策规则"""

    def test_value(self):
        assert make_decision(ev=0.05, edge=0.03, ev_threshold=0.02) == "VALUE"

    def test_marginal(self):
        assert make_decision(ev=0.01, edge=0.02, ev_threshold=0.02) == "MARGINAL"

    def test_avoid_negative_ev(self):
        assert make_decision(ev=-0.05, edge=0.03, ev_threshold=0.02) == "AVOID"

    def test_avoid_negative_edge(self):
        assert make_decision(ev=0.05, edge=-0.01, ev_threshold=0.02) == "AVOID"


class TestAnalyzeMatch:
    """完整比赛分析"""

    def test_value_match(self):
        probs = ModelProbabilities(home=0.55, draw=0.25, away=0.20)
        odds = OddsData(home=2.00, draw=3.50, away=4.00)
        result = analyze_match(probs, odds, ev_threshold=0.02)
        assert result.overall_decision == "VALUE"
        assert result.best_direction == "home"
        assert result.best_ev > 0.02
        assert result.recommended_stake_pct > 0
        total_p = (result.home_analysis.p_model +
                   result.draw_analysis.p_model +
                   result.away_analysis.p_model)
        assert total_p == pytest.approx(1.0, abs=1e-4)

    def test_all_avoid(self):
        probs = ModelProbabilities(home=0.45, draw=0.30, away=0.25)
        odds = OddsData(home=2.22, draw=3.33, away=4.00)
        result = analyze_match(probs, odds, ev_threshold=0.02)
        assert result.overall_decision == "AVOID"
        assert result.best_direction is None
        assert result.recommended_stake_pct == 0.0


class TestModelProbabilities:
    """概率数据结构"""

    def test_normalize(self):
        probs = ModelProbabilities(home=0.50, draw=0.30, away=0.21)
        assert probs.validate()
        norm = probs.normalize()
        assert norm.home + norm.draw + norm.away == pytest.approx(1.0, abs=1e-4)
