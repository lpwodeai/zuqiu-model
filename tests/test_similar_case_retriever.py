# -*- coding: utf-8 -*-
"""similar_case_retriever 单元测试 — 欧氏距离、K 近邻检索、WDL 分布。"""
import pytest

from similar_case_retriever import (
    SimilarCaseRetriever,
    SimilarCase,
    SimilarCaseResult,
)


class TestEuclideanDistance:
    """欧氏距离计算"""

    def test_identical_vectors(self):
        """相同向量距离为 0"""
        dist = SimilarCaseRetriever._euclidean_distance((0.5, 0.3, 0.2), (0.5, 0.3, 0.2))
        assert dist == pytest.approx(0.0, abs=1e-10)

    def test_orthogonal_vectors(self):
        """正交向量距离最大"""
        dist = SimilarCaseRetriever._euclidean_distance((1.0, 0.0, 0.0), (0.0, 0.0, 1.0))
        assert dist == pytest.approx(2.0 ** 0.5, abs=1e-4)

    def test_symmetric(self):
        """距离应对称"""
        a, b = (0.4, 0.3, 0.3), (0.5, 0.2, 0.3)
        d1 = SimilarCaseRetriever._euclidean_distance(a, b)
        d2 = SimilarCaseRetriever._euclidean_distance(b, a)
        assert d1 == pytest.approx(d2, abs=1e-10)


class TestRetrieve:
    """相似案例检索（内存 mock 数据）"""

    @pytest.fixture
    def retriever_with_mock_data(self):
        """构造一个加载了 mock 数据的检索器"""
        r = SimilarCaseRetriever(league="意甲")
        # 手工注入 mock 数据
        r._data = [
            {"match_id": "m1", "datetime": "2025-01-01", "home_team": "A", "away_team": "B",
             "home_goals": 2, "away_goals": 1, "actual_wdl": "H", "forecast": (0.5, 0.3, 0.2)},
            {"match_id": "m2", "datetime": "2025-02-01", "home_team": "C", "away_team": "D",
             "home_goals": 1, "away_goals": 1, "actual_wdl": "D", "forecast": (0.5, 0.3, 0.2)},
            {"match_id": "m3", "datetime": "2025-03-01", "home_team": "E", "away_team": "F",
             "home_goals": 0, "away_goals": 2, "actual_wdl": "A", "forecast": (0.1, 0.2, 0.7)},
            {"match_id": "m4", "datetime": "2025-04-01", "home_team": "G", "away_team": "H",
             "home_goals": 3, "away_goals": 0, "actual_wdl": "H", "forecast": (0.9, 0.05, 0.05)},
        ]
        r._features = [d["forecast"] for d in r._data]
        r._loaded = True
        return r

    def test_retrieve_k2(self, retriever_with_mock_data):
        """检索 K=2 应返回距离最近的 2 场"""
        r = retriever_with_mock_data
        result = r.retrieve((0.5, 0.3, 0.2), k=2)
        assert len(result.cases) == 2
        assert result.cases[0].distance <= result.cases[1].distance
        # m1 和 m2 的 forecast 与目标完全一致
        match_ids = {c.match_id for c in result.cases}
        assert "m1" in match_ids
        assert "m2" in match_ids

    def test_wdl_distribution(self, retriever_with_mock_data):
        """WDL 分布应正确计算"""
        r = retriever_with_mock_data
        result = r.retrieve((0.5, 0.3, 0.2), k=2)
        # m1=H, m2=D → H=0.5, D=0.5, A=0.0
        assert result.wdl_distribution["H"] == pytest.approx(0.5, abs=1e-4)
        assert result.wdl_distribution["D"] == pytest.approx(0.5, abs=1e-4)
        assert result.wdl_distribution["A"] == pytest.approx(0.0, abs=1e-4)

    def test_exclude_self(self, retriever_with_mock_data):
        """排除自身比赛"""
        r = retriever_with_mock_data
        result = r.retrieve((0.5, 0.3, 0.2), k=10, exclude_match_id="m1")
        match_ids = {c.match_id for c in result.cases}
        assert "m1" not in match_ids

    def test_avg_goals(self, retriever_with_mock_data):
        """平均进球数计算"""
        r = retriever_with_mock_data
        result = r.retrieve((0.5, 0.3, 0.2), k=2)
        # m1: 2-1, m2: 1-1 → avg_hg=1.5, avg_ag=1.0
        assert result.avg_home_goals == pytest.approx(1.5, abs=1e-4)
        assert result.avg_away_goals == pytest.approx(1.0, abs=1e-4)

    def test_distant_forecast(self, retriever_with_mock_data):
        """远距离 forecast 应排在后面"""
        r = retriever_with_mock_data
        result = r.retrieve((0.1, 0.2, 0.7), k=2)
        # m3 的 forecast (0.1, 0.2, 0.7) 距离最近
        assert result.cases[0].match_id == "m3"
        assert result.cases[0].distance == pytest.approx(0.0, abs=1e-10)
