# -*- coding: utf-8 -*-
"""P2/P3 中长期剩余模块（阶段 3）纯函数单元测试。

覆盖：
  - P2-01 战意量化修正系数（motivation_adjustment）
  - P2-02 低比分系统性低估诊断（low_score_diagnosis）
  - P2-03 概率校准分档监控（probability_calibration_monitor）
  - P2-04 数据源冲突检测（data_source_conflict_detector）
  - P2-06 双轨回测系统（dual_track_backtest）
  - P3-02 风险监控（risk_monitor）
  - P2 球员推算首发准确率复盘（player_lineup_accuracy）

仅测纯函数（不触库），保证诊断口径不随底层改动漂移。
"""
import numpy as np
import pytest

from risk_monitor import compute_risk_metrics, evaluate_risk, RiskConfig
from motivation_adjustment import team_motivation_factors, parse_score as m_parse_score, season_from_date
from low_score_diagnosis import prob_of, parse_score as l_parse_score, diagnose_low_score
from probability_calibration_monitor import reliability_curve, detect_class_bias
from dual_track_backtest import _rps, _ev_bucket, run_track_a
from data_source_conflict_detector import (
    fundamental_home_prob, market_home_prob, detect_conflict, floor_score,
)
from player_lineup_accuracy import hit_rate, aggregate_by
from player_injury_source import (
    InjuryRecord, apply_official_injuries, normalize_injury_records,
    map_sofascore_reason, sofascore_missing_to_injury,
    SOFASCORE_REASON_TO_STATUS, SOFASCORE_SKIP_REASONS,
)
from zip_score_model import (
    zip_inflation, zip_pmf, poisson_pmf, poisson_grid, zip_score_grid,
    cell_prob, log_loss_grid, rps_total_goals, low_score_bias, agg_le_bias,
    evaluate_grids, grid_search_k_scale, MAX_GOALS,
)


# ---------------------------------------------------------------------------
# P3-02 risk_monitor
# ---------------------------------------------------------------------------
class TestRiskMonitor:
    def test_compute_risk_metrics(self):
        bets = [
            {"won": True, "odds": 2.0, "profit": 1.0, "ev": 0.10},
            {"won": False, "odds": 2.0, "profit": -1.0, "ev": 0.10},
            {"won": False, "odds": 2.0, "profit": -1.0, "ev": 0.10},
        ]
        rep = compute_risk_metrics(bets)
        assert rep.n_bets == 3
        assert rep.n_wins == 1
        assert rep.hit_rate == pytest.approx(1 / 3)
        assert rep.flat_profit == pytest.approx(-1.0)
        assert rep.flat_roi == pytest.approx(-1 / 3)
        assert rep.longest_losing_streak == 2
        assert rep.current_losing_streak == 2
        assert rep.profit_factor == pytest.approx(0.5)
        assert rep.max_drawdown == pytest.approx(2 / 3)
        assert rep.avg_ev == pytest.approx(0.10)
        assert rep.ev_bias == pytest.approx(0.10 - (-1 / 3))

    def test_evaluate_risk_streak_alert(self):
        bets = [
            {"won": True, "odds": 2.0, "profit": 1.0, "ev": 0.05},
            {"won": False, "odds": 2.0, "profit": -1.0, "ev": 0.05},
            {"won": False, "odds": 2.0, "profit": -1.0, "ev": 0.05},
        ]
        rep = compute_risk_metrics(bets)
        cfg = RiskConfig(min_bets=1, losing_streak_alert=2)
        rep = evaluate_risk(rep, cfg)
        assert any("最长连亏" in a for a in rep.alerts)

    def test_evaluate_risk_small_sample(self):
        rep = compute_risk_metrics([])
        rep = evaluate_risk(rep, RiskConfig())
        assert any("样本不足" in a for a in rep.alerts)


# ---------------------------------------------------------------------------
# P2-01 motivation_adjustment
# ---------------------------------------------------------------------------
class TestMotivationAdjustment:
    def test_team_motivation_factors_fatigue_relegation(self):
        feat = {"rest_days": 2, "midweek": 0, "density_14d": 0, "incentive": "relegation"}
        attack, defense = team_motivation_factors(feat)
        # rest_low(0.95) × relegation_fight(1.05)
        assert attack == pytest.approx(0.95 * 1.05, abs=1e-4)
        # rest_low(1.04) × relegation_fight(1.02)
        assert defense == pytest.approx(1.04 * 1.02, abs=1e-4)

    def test_team_motivation_factors_neutral(self):
        feat = {"rest_days": 5, "midweek": 0, "density_14d": 2, "incentive": "midtable"}
        attack, defense = team_motivation_factors(feat)
        assert attack == pytest.approx(1.0)
        assert defense == pytest.approx(1.0)

    def test_parse_score(self):
        assert m_parse_score("2:1") == (2, 1)
        assert m_parse_score(None) == (None, None)
        assert m_parse_score("nonsense") == (None, None)

    def test_season_from_date(self):
        assert season_from_date("2026-09-15") == "2026-27"
        assert season_from_date("2026-02-01") == "2025-26"


# ---------------------------------------------------------------------------
# P2-02 low_score_diagnosis
# ---------------------------------------------------------------------------
class TestLowScoreDiagnosis:
    def test_parse_score(self):
        assert l_parse_score("0:0") == (0, 0)
        assert l_parse_score("") == (None, None)

    def test_prob_of(self):
        grid = {(0, 0): 0.5, (3, 2): 0.1}
        assert prob_of(grid, 0, 0) == pytest.approx(0.5)
        assert prob_of(grid, 3, 2) == pytest.approx(0.1)
        assert prob_of(grid, 0, 3) == pytest.approx(0.0)  # 未在格子
        assert prob_of(grid, 6, 0) == pytest.approx(0.0)  # 越界

    def test_diagnose_low_score_bias_sign(self):
        rows = [
            {"actual_h": 0, "actual_a": 0, "total_goals": 0, "league": "TEST", "grid": {(0, 0): 0.10}},
            {"actual_h": 0, "actual_a": 0, "total_goals": 0, "league": "TEST", "grid": {(0, 0): 0.10}},
        ]
        rep = diagnose_low_score(rows)
        cell00 = rep["cell_stats"][0]
        assert cell00["score"] == "0:0"
        assert cell00["avg_pred"] == pytest.approx(0.10)
        assert cell00["actual_freq"] == pytest.approx(1.0)
        assert cell00["bias_pp"] == pytest.approx(90.0)  # 正值=低估
        agg0 = rep["agg_stats"][0]
        assert agg0["label"] == "总进球=0 (0:0)"
        assert agg0["bias_pp"] == pytest.approx(90.0)


# ---------------------------------------------------------------------------
# P2-03 probability_calibration_monitor
# ---------------------------------------------------------------------------
class TestProbabilityCalibration:
    def test_reliability_curve_populated_bin_unbiased(self):
        y = np.array([0, 0, 1, 1, 2, 2])
        p = np.ones((6, 3)) / 3.0  # 均匀预测
        curve = reliability_curve(y, p, class_idx=1, n_bins=20)
        assert len(curve) == 20
        populated = [b for b in curve if b["n"] > 0]
        assert sum(b["n"] for b in populated) == 6
        only = populated[0]
        assert only["avg_pred"] == pytest.approx(1 / 3, abs=1e-2)
        assert only["actual_freq"] == pytest.approx(1 / 3, abs=1e-2)
        assert abs(only["bias"]) < 0.02

    def test_detect_class_bias_conclusion(self):
        y = np.array([0, 0, 1, 1])
        p = np.array([[1.0, 0.0, 0.0]] * 4)  # 全压主胜(away)
        curve = reliability_curve(y, p, class_idx=0, n_bins=10)
        diag = detect_class_bias(curve, direction="under")
        assert diag["n_samples"] == 4
        # 预测1.0 vs 实际频率0.5 → bias = 实际 - 预测 = -0.5（高估）
        assert diag["weighted_bias"] == pytest.approx(-0.5)


# ---------------------------------------------------------------------------
# P2-06 dual_track_backtest
# ---------------------------------------------------------------------------
class TestDualTrackBacktest:
    def test_rps_perfect(self):
        assert _rps(1.0, 0.0, 0.0, "home") == pytest.approx(0.0)
        assert _rps(0.0, 0.0, 1.0, "away") == pytest.approx(0.0)

    def test_rps_wrong(self):
        # 实际 home，预测全给 away → RPS 最大 = 1.0
        assert _rps(0.0, 0.0, 1.0, "home") == pytest.approx(1.0)

    def test_ev_bucket(self):
        assert _ev_bucket(0.0) == "≤0"
        assert _ev_bucket(0.01) == "0~2%"
        assert _ev_bucket(0.06) == "5~10%"
        assert _ev_bucket(0.15) == "10~20%"

    def test_run_track_a(self):
        preds = [{"home": 0.6, "draw": 0.2, "away": 0.2, "actual_direction": "home"}]
        track = run_track_a(preds)
        assert track["n"] == 1
        assert track["accuracy"] == pytest.approx(1.0)
        assert track["rps"] == pytest.approx(0.1)
        assert track["log_loss"] == pytest.approx(-np.log(0.6))


# ---------------------------------------------------------------------------
# P2-04 data_source_conflict_detector
# ---------------------------------------------------------------------------
class TestDataSourceConflict:
    def test_fundamental_home_prob_neutral(self):
        p = fundamental_home_prob(7.0, 7.0)
        assert p == pytest.approx(0.439, abs=1e-3)

    def test_fundamental_home_prob_monotonic(self):
        assert fundamental_home_prob(7.5, 7.0) > fundamental_home_prob(7.0, 7.0)

    def test_market_home_prob(self):
        p = market_home_prob(2.0, 3.0, 4.0)
        assert p == pytest.approx(0.5 / (0.5 + 1 / 3 + 0.25), abs=1e-4)

    def test_detect_conflict_direction_mismatch(self):
        res = detect_conflict(0.7, 0.3, prob_gap_threshold=0.15)
        assert res.is_conflict is True
        assert res.direction_mismatch is True
        assert res.conflict_score >= 0.7

    def test_detect_conflict_no_conflict(self):
        res = detect_conflict(0.55, 0.52, prob_gap_threshold=0.15)
        assert res.is_conflict is False
        assert res.tag == ""

    def test_floor_score(self):
        assert floor_score(0.73) == "0.7"
        assert floor_score(0.0) == "0.0"


# ---------------------------------------------------------------------------
# P2 player_lineup_accuracy（球员推算首发准确率复盘）
# ---------------------------------------------------------------------------
class TestLineupAccuracy:
    def test_hit_rate_perfect(self):
        predicted = {f"p{i}" for i in range(11)}
        actual = {f"p{i}" for i in range(11)}
        assert hit_rate(predicted, actual) == pytest.approx(1.0)

    def test_hit_rate_partial(self):
        predicted = {f"p{i}" for i in range(11)}
        actual = {f"p{i}" for i in range(6)} | {f"x{i}" for i in range(5)}
        assert hit_rate(predicted, actual) == pytest.approx(6 / 11)

    def test_hit_rate_empty_actual_nan(self):
        predicted = {f"p{i}" for i in range(11)}
        assert np.isnan(hit_rate(predicted, set()))

    def test_aggregate_by_league(self):
        rows = [
            {"league": "英超", "team": "A", "hit_rate": 1.0},
            {"league": "英超", "team": "B", "hit_rate": 0.5},
            {"league": "西甲", "team": "C", "hit_rate": 0.25},
        ]
        by_league = aggregate_by(rows, "league", min_n=1)
        england = next(x for x in by_league if x["league"] == "英超")
        assert england["n"] == 2
        assert england["mean_hit_rate"] == pytest.approx(0.75)
        assert england["median_hit_rate"] == pytest.approx(0.75)

    def test_aggregate_by_min_n_filter(self):
        rows = [{"league": "英超", "team": "A", "hit_rate": 1.0}]
        assert aggregate_by(rows, "league", min_n=5) == []


# ---------------------------------------------------------------------------
# P3 player_injury_source（球员官方/推算伤病区分）
# ---------------------------------------------------------------------------
class TestInjurySource:
    def test_injury_record_invalid_source_raises(self):
        with pytest.raises(ValueError):
            InjuryRecord(team="A", player_name="p", source="bogus")

    def test_injury_record_defaults(self):
        rec = InjuryRecord(team="Arsenal", player_name="Saka")
        assert rec.status == "out"
        assert rec.source == "official"
        assert rec.is_out() is True

    def test_injury_record_suspended_is_out(self):
        assert InjuryRecord(team="A", player_name="p", status="suspended").is_out() is True
        assert InjuryRecord(team="A", player_name="p", status="doubtful").is_out() is False

    def test_normalize_injury_records_skips_missing(self):
        recs = normalize_injury_records([
            {"team": "A", "player_name": "p1", "status": "out"},
            {"team": "", "player_name": "p2"},          # 缺队名 → 丢弃
            {"team": "B", "player_name": ""},           # 缺球员 → 丢弃
            {"team": "C", "player_name": "p3", "source": "bogus"},  # 非法 source → 丢弃
        ])
        assert len(recs) == 1
        assert recs[0].team == "A"
        assert recs[0].player_name == "p1"

    def test_apply_official_injuries_full_coverage(self):
        predicted = [f"p{i}" for i in range(11)]
        official = ["p0", "p1"]
        res = apply_official_injuries(predicted, official)
        assert res["n_confirmed_out"] == 2
        assert res["coverage_of_official"] == pytest.approx(1.0)
        assert set(res["confirmed_out"]) == {"p0", "p1"}
        assert len(res["adjusted_xi"]) == 9

    def test_apply_official_injuries_zero_coverage(self):
        predicted = [f"p{i}" for i in range(11)]
        official = ["x0"]
        res = apply_official_injuries(predicted, official)
        assert res["n_confirmed_out"] == 0
        assert res["coverage_of_official"] == pytest.approx(0.0)
        assert len(res["adjusted_xi"]) == 11

    # ---- SofaScore 官方缺阵回填纯函数 ----
    def test_map_sofascore_reason_injury_to_out(self):
        assert map_sofascore_reason("伤病") == "out"
        assert map_sofascore_reason("未知") == "out"
        assert map_sofascore_reason("其他") == "out"

    def test_map_sofascore_reason_suspended(self):
        assert map_sofascore_reason("停赛") == "suspended"

    def test_map_sofascore_reason_transfer_skip(self):
        """转会类 reason → None（跳过，不写入 player_injuries）。"""
        assert map_sofascore_reason("转会") is None
        assert "转会" in SOFASCORE_SKIP_REASONS

    def test_sofascore_missing_to_injury_full_fields(self):
        mp = {
            "team": "Arsenal",
            "player_name": "Saka",
            "reason": "伤病",
            "expected_end_date": "2026-09-20",
            "sofascore_slug": "bukayo-saka",
        }
        rec = sofascore_missing_to_injury(mp)
        assert rec is not None
        assert rec.team == "Arsenal"
        assert rec.player_name == "Saka"
        assert rec.status == "out"
        assert rec.expected_return == "2026-09-20"
        assert rec.source == "official"
        assert rec.source_url == "https://www.sofascore.com/player/bukayo-saka"

    def test_sofascore_missing_to_injury_transfer_skipped(self):
        mp = {"team": "A", "player_name": "p", "reason": "转会"}
        assert sofascore_missing_to_injury(mp) is None

    def test_sofascore_missing_to_injury_empty_skipped(self):
        """球队/球员为空 → 跳过。"""
        assert sofascore_missing_to_injury({"team": "", "player_name": "p", "reason": "伤病"}) is None
        assert sofascore_missing_to_injury({"team": "A", "player_name": "", "reason": "伤病"}) is None


# ---------------------------------------------------------------------------
# P3 zip_score_model（ZIP 零膨胀泊松比分模型）
# ---------------------------------------------------------------------------
class TestZipScoreModel:
    # ---- zip_inflation ----
    def test_zip_inflation_low_lambda_high_pi(self):
        """弱队（λ→0）零膨胀最显著。"""
        assert zip_inflation(0.0, k_scale=0.3, pi_max=0.40) == pytest.approx(0.3, abs=1e-6)

    def test_zip_inflation_high_lambda_low_pi(self):
        """强队（λ大）零膨胀趋近 0。"""
        pi = zip_inflation(10.0, k_scale=0.3, pi_max=0.40)
        assert pi < 0.05
        assert pi > 0.0

    def test_zip_inflation_pi_max_cap(self):
        """k_scale 很大时被 pi_max 钳制。"""
        pi = zip_inflation(0.0, k_scale=5.0, pi_max=0.40)
        assert pi == pytest.approx(0.40)

    def test_zip_inflation_non_negative(self):
        """λ 负数兜底为 0，π 恒非负。"""
        pi = zip_inflation(-1.0, k_scale=0.3, pi_max=0.40)
        assert pi >= 0.0

    # ---- poisson_pmf / zip_pmf ----
    def test_poisson_pmf_known_values(self):
        import math
        assert poisson_pmf(0, 1.0) == pytest.approx(math.exp(-1.0), abs=1e-9)
        assert poisson_pmf(1, 1.0) == pytest.approx(math.exp(-1.0), abs=1e-9)

    def test_poisson_pmf_negative_k(self):
        assert poisson_pmf(-1, 1.0) == 0.0

    def test_zip_pmf_k0_formula(self):
        """ZIP P(0) = π + (1−π)·e^{−λ_z}，λ_z=λ/(1−π)。"""
        import math
        lam, pi = 1.0, 0.3
        lam_z = lam / (1.0 - pi)
        expected = pi + (1.0 - pi) * math.exp(-lam_z)
        assert zip_pmf(0, lam, pi) == pytest.approx(expected, abs=1e-9)

    def test_zip_pmf_k1_formula(self):
        """ZIP P(1) = (1−π)·e^{−λ_z}·λ_z。"""
        import math
        lam, pi = 1.0, 0.3
        lam_z = lam / (1.0 - pi)
        expected = (1.0 - pi) * math.exp(-lam_z) * lam_z
        assert zip_pmf(1, lam, pi) == pytest.approx(expected, abs=1e-9)

    def test_zip_pmf_negative_k(self):
        assert zip_pmf(-1, 1.0, 0.3) == 0.0

    def test_zip_reduces_to_poisson_when_pi_zero(self):
        """π=0 时 ZIP 退化为普通泊松。"""
        lam = 1.5
        for k in range(6):
            assert zip_pmf(k, lam, 0.0) == pytest.approx(poisson_pmf(k, lam), abs=1e-9)

    # ---- 网格 ----
    def test_poisson_grid_sum_to_one(self):
        g = poisson_grid(1.5, 1.2)
        assert sum(g.values()) == pytest.approx(1.0, abs=1e-9)

    def test_poisson_grid_shape(self):
        g = poisson_grid(1.5, 1.2)
        assert len(g) == (MAX_GOALS + 1) ** 2

    def test_zip_grid_sum_to_one(self):
        g = zip_score_grid(1.5, 1.2, k_scale=0.3)
        assert sum(g.values()) == pytest.approx(1.0, abs=1e-9)

    def test_zip_mean_matching_invariant(self):
        """均值匹配：ZIP 单队期望进球应≈λ（一阶矩不变）。"""
        lam_h, lam_a = 1.5, 1.0
        g = zip_score_grid(lam_h, lam_a, k_scale=0.3)
        eh = sum(h * p for (h, _a), p in g.items())
        ea = sum(a * p for (_h, a), p in g.items())
        # 截断到 MAX_GOALS 有小幅偏差
        assert eh == pytest.approx(lam_h, abs=0.15)
        assert ea == pytest.approx(lam_a, abs=0.15)


# ---------------------------------------------------------------------------
# P1-01: compute_confidence_breakdown 扩展（extra_penalty 冲突扣分）
# ---------------------------------------------------------------------------
class TestConfidenceBreakdownExtraPenalty:
    def test_no_penalty_unchanged(self):
        """无 extra_penalty 时结果与旧版本一致。"""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from generate_unified_report import compute_confidence_breakdown
        r1 = compute_confidence_breakdown(100, 0.7, 0.02, 5, 2)
        r2 = compute_confidence_breakdown(100, 0.7, 0.02, 5, 2, extra_penalty=0.0)
        assert r1["score"] == r2["score"]
        assert r1["base"] == r2["base"]

    def test_conflict_penalty_applied(self):
        """跨源冲突时 extra_penalty 会从总分中扣除。"""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from generate_unified_report import compute_confidence_breakdown
        base = compute_confidence_breakdown(100, 0.7, 0.02, 5, 2, extra_penalty=0)
        penalized = compute_confidence_breakdown(100, 0.7, 0.02, 5, 2, extra_penalty=5.0)
        assert penalized["score"] == base["score"] - 5
        # 拆解 components 中应有「跨数据源冲突」项
        names = [c["name"] for c in penalized["components"]]
        assert "跨数据源冲突" in names

    def test_penalty_capped_at_10(self):
        """extra_penalty 上限 10 分。"""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'scripts'))
        from generate_unified_report import compute_confidence_breakdown
        r = compute_confidence_breakdown(100, 0.9, 0.02, 5, 2, extra_penalty=999.0)
        penalty_item = [c for c in r["components"] if c["name"] == "跨数据源冲突"][0]
        assert abs(penalty_item["value"]) == pytest.approx(10.0)


# ---------------------------------------------------------------------------
# P1-17: final_500_collector parse_touzhu crawl_status 测试
# ---------------------------------------------------------------------------
class TestParseTouzhuCrawlStatus:
    def _soup(self, html):
        from bs4 import BeautifulSoup
        return BeautifulSoup(html, "html.parser")

    def test_parse_ok(self):
        """正常表格数据 → crawl_status=ok。"""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'collection'))
        from final_500_collector import parse_touzhu
        html = """
        <table class="bif-yab">
          <tr><td>主队</td><td>2.00</td><td>50%</td><td>-</td><td>40%</td><td>2.10</td><td>10万</td><td>盈</td><td>0.8</td><td>85</td><td>0.9</td></tr>
          <tr><td>平局</td><td>3.20</td><td>31%</td><td>-</td><td>30%</td><td>3.30</td><td>8万</td><td>亏</td><td>0.5</td><td>60</td><td>0.7</td></tr>
          <tr><td>客队</td><td>3.50</td><td>28%</td><td>-</td><td>30%</td><td>3.60</td><td>7万</td><td>亏</td><td>0.6</td><td>55</td><td>0.6</td></tr>
        </table>
        """
        out = parse_touzhu(self._soup(html))
        assert out["_crawl_status"] == "ok"
        assert out["home_name"] == "主队"

    def test_no_table_source_no_data(self):
        """页面有「投注分析/必发」字样但无表格 → source_no_data。"""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'collection'))
        from final_500_collector import parse_touzhu
        html = "<html><body>投注分析 必发 暂无数据</body></html>"
        out = parse_touzhu(self._soup(html))
        assert out["_crawl_status"] == "source_no_data"

    def test_empty_page_crawl_failed(self):
        """完全空/乱页 → crawl_parse_failed。"""
        import sys, os
        sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'collection'))
        from final_500_collector import parse_touzhu
        html = "<html><body>hello world</body></html>"
        out = parse_touzhu(self._soup(html))
        assert out["_crawl_status"] == "crawl_parse_failed"

    def test_zip_00_higher_than_poisson(self):
        """零膨胀应抬高 (0,0) 格概率。"""
        pp = poisson_grid(0.8, 0.8)
        zp = zip_score_grid(0.8, 0.8, k_scale=0.3)
        assert zp[(0, 0)] > pp[(0, 0)]

    # ---- cell_prob / log_loss_grid ----
    def test_cell_prob_in_bounds(self):
        g = poisson_grid(1.0, 1.0)
        assert cell_prob(g, 0, 0) > 0.0

    def test_cell_prob_out_of_bounds(self):
        g = poisson_grid(1.0, 1.0)
        assert cell_prob(g, 99, 0) == 0.0
        assert cell_prob(g, -1, 0) == 0.0

    def test_log_loss_grid_out_of_range_is_none(self):
        g = poisson_grid(1.0, 1.0)
        assert log_loss_grid(g, 99, 0) is None

    def test_log_loss_grid_high_confidence_low(self):
        g = {(1, 0): 0.99, (0, 1): 0.01}
        ll = log_loss_grid(g, 1, 0)
        assert ll is not None
        assert ll < 0.1

    # ---- rps_total_goals ----
    def test_rps_perfect_prediction(self):
        g = {(2, 0): 1.0}
        assert rps_total_goals(g, 2) == pytest.approx(0.0, abs=1e-9)

    def test_rps_between_0_and_1(self):
        g = poisson_grid(1.5, 1.2)
        v = rps_total_goals(g, 3)
        assert 0.0 <= v <= 1.0

    # ---- low_score_bias / agg_le_bias ----
    def test_low_score_bias_sign(self):
        """模型 0-0 概率 0，实际 0-0 → 低估 +100pp。"""
        grid = {(0, 0): 0.0, (1, 0): 1.0}
        rows = [{"actual_h": 0, "actual_a": 0, "total_goals": 0}]
        bias = low_score_bias([grid], rows, cells=[(0, 0)])
        assert bias[0]["score"] == "0:0"
        assert bias[0]["bias_pp"] == pytest.approx(100.0, abs=1e-6)

    def test_agg_le_bias_underestimation(self):
        grid = {(0, 0): 0.2, (1, 0): 0.6, (2, 0): 0.2}
        rows = [{"actual_h": 0, "actual_a": 0, "total_goals": 0}]
        le = agg_le_bias([grid], rows, max_goals_total=1)
        assert le["bias_pp"] == pytest.approx(20.0, abs=1e-6)

    # ---- evaluate_grids ----
    def test_evaluate_grids_keys(self):
        rows = [{
            "lambda_home": 1.5, "lambda_away": 1.0,
            "actual_h": 1, "actual_a": 0, "total_goals": 1,
        }]
        ev = evaluate_grids(rows, poisson_grid)
        for k in ("log_loss", "rps", "low_bias", "le1", "le2", "in_range"):
            assert k in ev

    def test_evaluate_grids_missing_lambda(self):
        rows = [{
            "lambda_home": None, "lambda_away": None,
            "actual_h": 1, "actual_a": 0, "total_goals": 1,
        }]
        ev = evaluate_grids(rows, poisson_grid)
        import math
        assert math.isnan(ev["log_loss"])
        assert ev["in_range"] == 0

    # ---- grid_search_k_scale ----
    def test_grid_search_covers_all_candidates(self):
        rows = [{
            "lambda_home": 1.2, "lambda_away": 0.9,
            "actual_h": 1, "actual_a": 0, "total_goals": 1,
        }]
        candidates = [0.0, 0.1, 0.3]
        gs = grid_search_k_scale(rows, candidates=candidates)
        assert len(gs) == 3
        # 按 LogLoss 升序
        for i in range(len(gs) - 1):
            assert gs[i]["log_loss"] <= gs[i + 1]["log_loss"]
        for s in gs:
            assert "k_scale" in s
            assert "log_loss" in s
            assert "bias_00_pp" in s
            assert "bias_le1_pp" in s