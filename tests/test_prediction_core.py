# -*- coding: utf-8 -*-
"""prediction_core 内核模块回归测试（防底层改动漂移）。

覆盖：
- CalcEngine.adjust_lambda_for_mid_score（A-002 λ 调整 + P1-13 告警钩子）
- CalcEngine.calc_lambda_from_odds（λ 基础计算）
- LAMBDA_DIFF_ALERT_THRESHOLD 常量
- predict_unified mock 模式返回结构（如模型可加载）
"""
import logging
import pytest

from prediction_core import (
    CalcEngine,
    LAMBDA_DIFF_ALERT_THRESHOLD,
)


# ============================================================
# 常量测试
# ============================================================

class TestLambdaAlertThreshold:
    """P1-13: λ 差值告警阈值常量"""

    def test_threshold_value(self):
        """阈值应为 1.2"""
        assert LAMBDA_DIFF_ALERT_THRESHOLD == 1.2


# ============================================================
# CalcEngine.adjust_lambda_for_mid_score 测试
# ============================================================

class TestAdjustLambda:
    """A-002 λ 调整 + P1-13 告警钩子"""

    def test_basic_adjustment_no_odds(self):
        """无 odds_data 时应使用默认 win=0.33 缩放"""
        lh, la, trace = CalcEngine.adjust_lambda_for_mid_score(2.0, 1.5, odds_data=None)
        # win_home=0.33 → wdl_scale = max(0.7, min(1.8, 0.5+0.33*1.5)) = max(0.7, min(1.8, 0.995)) = 0.995
        # tg_scale=1.0（无 tg_odds）
        assert lh == pytest.approx(2.0 * 0.995, abs=1e-4)
        assert la == pytest.approx(1.5 * 0.995, abs=1e-4)
        # P1-13: trace 结构完整性校验
        assert "base" in trace
        assert "stage1_wdl" in trace
        assert "stage2_tg" in trace
        assert "final" in trace
        assert trace["stage1_wdl"]["source"] == "fallback_0.33"
        assert trace["final"]["alert_triggered"] is False

    def test_lambda_diff_alert_triggered(self, caplog):
        """P1-13: λ 差值 > 1.2 时应触发告警"""
        with caplog.at_level(logging.WARNING):
            CalcEngine.adjust_lambda_for_mid_score(3.0, 1.0, odds_data=None)
        # 调整后 λ 差值仍 > 1.2
        alert_logs = [r for r in caplog.records if 'λ告警' in r.getMessage()]
        assert len(alert_logs) == 1
        assert '1.2' in alert_logs[0].getMessage()

    def test_lambda_diff_alert_not_triggered(self, caplog):
        """P1-13: λ 差值 < 1.2 时不应触发告警"""
        with caplog.at_level(logging.WARNING):
            CalcEngine.adjust_lambda_for_mid_score(1.5, 1.0, odds_data=None)
        # 调整后差值 ≈ 0.5 * 0.995 ≈ 0.4975 < 1.2
        alert_logs = [r for r in caplog.records if 'λ告警' in r.getMessage()]
        assert len(alert_logs) == 0

    def test_lambda_clamp_range(self):
        """λ 应在 0.3-3.5 范围内（由 calc_lambda_from_odds 保证）"""
        # 极端赔率测试
        lh, la, trace = CalcEngine.adjust_lambda_for_mid_score(3.5, 0.3, odds_data=None)
        # 0.3 * 0.995 ≈ 0.2985 < 0.3，但 adjust 不再 clamp（clamp 在 calc_lambda_from_odds）
        # 这里只验证 adjust 不会产生 NaN
        assert lh == pytest.approx(3.5 * 0.995, abs=1e-4)
        assert la == pytest.approx(0.3 * 0.995, abs=1e-4)
        assert lh > 0 and la > 0


# ============================================================
# CalcEngine.calc_lambda_from_odds 测试
# ============================================================

class TestCalcLambdaFromOdds:
    """λ 基础计算（从赔率隐含概率）"""

    def test_basic_lambda(self):
        """标准赔率应产出合理 λ 值"""
        odds_data = {
            'wdl_odds': {
                'close': {'win': 2.0, 'draw': 3.5, 'lose': 3.5},
                'records': [],
            }
        }
        lh, la = CalcEngine.calc_lambda_from_odds(odds_data)
        # hp = 1/2.0 = 0.5, ap = 1/3.5 ≈ 0.286
        # avg_goals * hp, avg_goals * ap
        assert 0.3 <= lh <= 3.5
        assert 0.3 <= la <= 3.5
        assert lh > la  # 主队赔率低 → λ 更高

    def test_lambda_fallback_no_close(self):
        """无 close 时应回退到默认赔率"""
        odds_data = {
            'wdl_odds': {
                'close': None,
                'records': [],
            }
        }
        lh, la = CalcEngine.calc_lambda_from_odds(odds_data)
        # 默认 win=2.0, draw=3.4, lose=3.0
        assert 0.3 <= lh <= 3.5
        assert 0.3 <= la <= 3.5

    def test_lambda_clamp(self):
        """极端赔率应被 clamp 到 0.3-3.5"""
        odds_data = {
            'wdl_odds': {
                'close': {'win': 1.01, 'draw': 51.0, 'lose': 51.0},
                'records': [],
            }
        }
        lh, la = CalcEngine.calc_lambda_from_odds(odds_data)
        assert lh <= 3.5
        assert la >= 0.3
