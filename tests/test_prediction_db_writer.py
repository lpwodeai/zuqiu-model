# -*- coding: utf-8 -*-
"""prediction_db_writer 单元测试 — match_id 生成、赛季推导、序列化转换。"""
import pytest

from prediction_db_writer import (
    make_match_id,
    derive_season,
    serializable_to_pred,
)


class TestMakeMatchId:
    """match_id 生成"""

    def test_basic(self):
        mid = make_match_id("2026-09-15", "Villarreal", "Real Betis")
        assert mid == "2026-09-15_Villarreal_Real Betis"

    def test_strip_whitespace(self):
        mid = make_match_id("2026-09-15", "  Villarreal  ", "Real  Betis")
        assert mid == "2026-09-15_Villarreal_Real Betis"


class TestDeriveSeason:
    """赛季推导"""

    def test_aug_start(self):
        """8 月起为新赛季"""
        assert derive_season("2026-08-01") == "2026-2027"

    def test_july_end(self):
        """7 月为上赛季末"""
        assert derive_season("2026-07-31") == "2025-2026"

    def test_january(self):
        """1 月跨年"""
        assert derive_season("2026-01-15") == "2025-2026"


class TestSerializableToPred:
    """序列化转换"""

    def test_basic_conversion(self):
        """标准输入应正确转换"""
        m = {
            "match_time": "2026-09-15 20:00",
            "home_en": "Villarreal",
            "away_en": "Real Betis",
            "home": "比利亚雷亚尔",
            "away": "皇家贝蒂斯",
            "league": "西甲",
            "wdl": {"home_prob": 0.49, "draw_prob": 0.26, "away_prob": 0.25},
            "hcp": {"home_win_prob": 0.45, "draw_prob": 0.10, "away_win_prob": 0.45, "line": -0.5},
            "score": {"lambda_home": 2.57, "lambda_away": 1.00, "most_likely": "2-1"},
            "tg": {"over_25_prob": 0.55},
            "ev": {"decision": "AVOID", "best_ev": -0.05},
            "_input_snapshot_json": '{"test": 1}',
            "_feature_version": "208",
            "_config_version": "v2.0",
            "lambda_alert": {"triggered": True, "diff": 1.57, "threshold": 1.2,
                            "lambda_home": 2.57, "lambda_away": 1.00,
                            "message": "test alert"},
        }
        pred = serializable_to_pred(m)

        assert pred["matchDate"] == "2026-09-15"
        assert pred["homeTeam"] == "Villarreal"
        assert pred["awayTeam"] == "Real Betis"
        assert pred["league"] == "西甲"
        assert pred["season"] == "2026-2027"
        assert pred["wdl"]["home"] == 0.49
        assert pred["handicapProb"]["upper"] == 0.45
        assert pred["totalGoalsProbOver"] == 0.55
        assert pred["totalGoalsProbUnder"] == pytest.approx(0.45, abs=1e-6)
        assert pred["lambdaHome"] == 2.57
        assert pred["lambdaAway"] == 1.00
        assert pred["lambda_alert"]["triggered"] is True
        assert pred["input_snapshot_json"] == '{"test": 1}'
        assert pred["feature_version"] == "208"

    def test_missing_fields(self):
        """缺失字段应安全处理"""
        m = {
            "match_time": "2026-09-15 20:00",
            "home_en": "A", "away_en": "B",
            "home": "甲", "away": "乙",
            "league": "西甲",
        }
        pred = serializable_to_pred(m)
        assert pred["wdl"] == {"home": None, "draw": None, "away": None}
        assert pred["totalGoalsProbOver"] is None
        assert pred["lambdaHome"] is None
        assert pred["lambda_alert"] is None
