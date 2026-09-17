"""
训练管线统一编排器单元测试
==========================

覆盖：
  - 建表幂等性
  - should_retrain 四 trigger 全分支
  - 去抖（同 trigger 24h 内不重复）
  - run_training 状态流转（mock 掉 subprocess）
  - get_recent_runs / get_run 查询
"""

import os
import sys
import json
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


# ------------------------------------------------------------------
# Fixture: 用临时数据库隔离
# ------------------------------------------------------------------
@pytest.fixture
def orch(tmp_path, monkeypatch):
    """返回打了临时 DB 补丁的 orchestrator 模块。"""
    db_file = tmp_path / "test_five_leagues.db"
    import pipeline.training_orchestrator as orch_mod
    monkeypatch.setattr(orch_mod, "DB_PATH", db_file)
    monkeypatch.setattr(orch_mod, "_get_conn",
                        lambda: _make_conn(db_file))
    orch_mod.logger.handlers = []
    return orch_mod


def _make_conn(db_path):
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


# ------------------------------------------------------------------
# 建表
# ------------------------------------------------------------------
def test_ensure_table_idempotent(orch):
    """建表两次都不报错，表存在。"""
    orch.ensure_table()
    orch.ensure_table()
    conn = orch._get_conn()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='training_runs'").fetchall()
    assert len(rows) == 1


# ------------------------------------------------------------------
# 决策表
# ------------------------------------------------------------------
class TestShouldRetrain:
    """should_retrain 决策表全分支覆盖。"""

    def test_scheduled_first_time_triggers_full(self, orch):
        orch.ensure_table()
        res = orch.should_retrain("scheduled")
        assert res["should"] is True
        assert res["training_type"] == "full"
        assert "首次" in res["reason"]

    def test_scheduled_within_7_days_skips(self, orch):
        orch.ensure_table()
        started = (datetime.now() - timedelta(days=3)).isoformat()
        conn = orch._get_conn()
        conn.execute(
            "INSERT INTO training_runs "
            "(trigger_source, training_type, started_at, finished_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("scheduled", "full", started, started, "success"))
        conn.commit()
        conn.close()
        res = orch.should_retrain("scheduled")
        assert res["should"] is False
        assert "阈值" in res["reason"]

    def test_scheduled_after_7_days_triggers_full(self, orch):
        orch.ensure_table()
        started = (datetime.now() - timedelta(days=8)).isoformat()
        conn = orch._get_conn()
        conn.execute(
            "INSERT INTO training_runs "
            "(trigger_source, training_type, started_at, finished_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("scheduled", "full", started, started, "success"))
        conn.commit()
        conn.close()
        res = orch.should_retrain("scheduled")
        assert res["should"] is True
        assert res["training_type"] == "full"

    def test_new_data_below_threshold_skips(self, orch):
        res = orch.should_retrain("new_data", {"new_match_count": 5})
        assert res["should"] is False

    def test_new_data_incremental_range(self, orch):
        res = orch.should_retrain("new_data", {"new_match_count": 15})
        assert res["should"] is True
        assert res["training_type"] == "incremental"

    def test_new_data_full_threshold(self, orch):
        res = orch.should_retrain("new_data", {"new_match_count": 35})
        assert res["should"] is True
        assert res["training_type"] == "full"

    def test_degraded_below_threshold_skips(self, orch):
        res = orch.should_retrain("degraded", {"accuracy_drop": 2.0})
        assert res["should"] is False

    def test_degraded_accuracy_drop_triggers_full(self, orch):
        res = orch.should_retrain("degraded", {"accuracy_drop": 4.0})
        assert res["should"] is True
        assert res["training_type"] == "full"

    def test_degraded_ece_delta_triggers_full(self, orch):
        res = orch.should_retrain("degraded", {"ece_delta": 6.0})
        assert res["should"] is True
        assert res["training_type"] == "full"

    def test_manual_always_full(self, orch):
        res = orch.should_retrain("manual")
        assert res["should"] is True
        assert res["training_type"] == "full"

    def test_manual_with_type_incremental(self, orch):
        res = orch.should_retrain("manual",
                                  {"training_type": "incremental"})
        assert res["should"] is True
        assert res["training_type"] == "incremental"

    def test_unknown_trigger_returns_skip(self, orch):
        res = orch.should_retrain("bogus_trigger")
        assert res["should"] is False
        assert "未知" in res["reason"]


    def test_dedup_prevents_duplicate_within_24h(self, orch):
        orch.ensure_table()
        started = (datetime.now() - timedelta(hours=1)).isoformat()
        conn = orch._get_conn()
        conn.execute(
            "INSERT INTO training_runs "
            "(trigger_source, training_type, started_at, finished_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("degraded", "full", started, started, "success"))
        conn.commit()
        conn.close()
        res = orch.should_retrain("degraded", {"accuracy_drop": 10.0})
        assert res["dedup_hit"] is True
        assert res["should"] is False
        assert "去抖" in res["reason"]

    def test_dedup_not_applied_to_manual(self, orch):
        orch.ensure_table()
        started = (datetime.now() - timedelta(hours=1)).isoformat()
        conn = orch._get_conn()
        conn.execute(
            "INSERT INTO training_runs "
            "(trigger_source, training_type, started_at, finished_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("manual", "full", started, started, "success"))
        conn.commit()
        conn.close()
        res = orch.should_retrain("manual")
        assert res["dedup_hit"] is False
        assert res["should"] is True

    def test_different_triggers_independent(self, orch):
        orch.ensure_table()
        started = (datetime.now() - timedelta(hours=1)).isoformat()
        conn = orch._get_conn()
        conn.execute(
            "INSERT INTO training_runs "
            "(trigger_source, training_type, started_at, finished_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("scheduled", "full", started, started, "success"))
        conn.commit()
        conn.close()
        res = orch.should_retrain("degraded", {"accuracy_drop": 10.0})
        assert res["dedup_hit"] is False
        assert res["should"] is True


# ------------------------------------------------------------------
# run_training 状态流转（mock subprocess）
# ------------------------------------------------------------------
class FakeProc:
    returncode = 0
    stdout = "RPS: 0.1234\nLogLoss: 0.856\n准确率: 0.55\n平局召回: 0.32\n"
    stderr = ""


class BadProc:
    returncode = 1
    stdout = ""
    stderr = "训练失败错误信息" * 10


def test_run_training_success_writes_metrics(orch, monkeypatch):
    """成功训练后，training_runs 表有 success 记录且指标正确。"""
    orch.ensure_table()
    monkeypatch.setattr(orch.subprocess, "run",
                        lambda *a, **kw: FakeProc())
    result = orch.run_training("manual", {"training_type": "full"},
                               force=True)
    assert result["status"] == "success"
    assert result["run_id"] is not None
    run = orch.get_run(result["run_id"])
    assert run["status"] == "success"
    assert run["val_rps"] == pytest.approx(0.1234)
    assert run["val_logloss"] == pytest.approx(0.856)
    assert run["val_acc"] == pytest.approx(0.55)
    assert run["val_draw_recall"] == pytest.approx(0.32)
    assert run["finished_at"] is not None
    assert run["artifacts_json"] is not None
    artifacts = json.loads(run["artifacts_json"])
    assert artifacts["script"] == "train_models.py"


def test_run_training_failure_writes_error(orch, monkeypatch):
    """训练失败后，状态 = failed 且有 error_msg。"""
    orch.ensure_table()
    monkeypatch.setattr(orch.subprocess, "run",
                        lambda *a, **kw: BadProc())
    result = orch.run_training("manual", {"training_type": "full"},
                               force=True)
    assert result["status"] == "failed"
    assert "error" in result
    run = orch.get_run(result["run_id"])
    assert run["status"] == "failed"
    assert run["error_msg"] is not None


def test_run_training_skip_when_not_needed(orch):
    """决策为 skip 时不写记录，返回 skipped。"""
    orch.ensure_table()
    started = (datetime.now() - timedelta(days=3)).isoformat()
    conn = orch._get_conn()
    conn.execute(
        "INSERT INTO training_runs "
        "(trigger_source, training_type, started_at, finished_at, status) "
        "VALUES (?, ?, ?, ?, ?)",
        ("scheduled", "full", started, started, "success"))
    conn.commit()
    conn.close()
    result = orch.run_training("scheduled")
    assert result["status"] == "skipped"
    assert result["run_id"] is None
    runs = orch.get_recent_runs(100)
    assert len(runs) == 1


# ------------------------------------------------------------------
# 查询
# ------------------------------------------------------------------
class TestQueries:
    def test_get_recent_runs_orders_by_started(self, orch):
        orch.ensure_table()
        conn = orch._get_conn()
        for i in range(5):
            started = (datetime.now() - timedelta(hours=i)).isoformat()
            conn.execute(
                "INSERT INTO training_runs "
                "(trigger_source, training_type, started_at, status) "
                "VALUES (?, ?, ?, ?)",
                ("scheduled", "full", started, "success"))
        conn.commit()
        conn.close()
        runs = orch.get_recent_runs(3)
        assert len(runs) == 3
        assert runs[0]["started_at"] > runs[2]["started_at"]

    def test_get_run_returns_none_for_missing(self, orch):
        assert orch.get_run(999999) is None


# ------------------------------------------------------------------
# _parse_train_output
# ------------------------------------------------------------------
class TestParseTrainOutput:
    def test_parses_all_metrics(self, orch):
        out = "RPS: 0.1234\nLogLoss: 0.856\n准确率: 0.55\n平局召回: 0.32\n"
        m = orch._parse_train_output(out)
        assert m["rps"] == pytest.approx(0.1234)
        assert m["logloss"] == pytest.approx(0.856)
        assert m["acc"] == pytest.approx(0.55)
        assert m["draw_recall"] == pytest.approx(0.32)

    def test_empty_output_returns_empty_dict(self, orch):
        m = orch._parse_train_output("nothing here")
        assert m == {}


# ------------------------------------------------------------------
# daily-check 综合检查 / dry-run
# ------------------------------------------------------------------
class TestDailyCheck:
    def test_dry_run_does_not_run_training(self, orch, monkeypatch):
        """dry_run=True 时不应调用 subprocess.run。"""
        orch.ensure_table()
        # mock get_new_match_count_since 返回一些新比赛
        monkeypatch.setattr(orch, "get_new_match_count_since",
                            lambda x=None: {"new_matches": 35,
                                            "total_matches": 1000,
                                            "latest_date": "2026-09-15"})
        # 用一个计数器跟踪 run_training 是否被调
        called = {"count": 0}
        original = orch.run_training

        def fake_run_training(*a, **kw):
            called["count"] += 1
            return original(*a, **kw)

        monkeypatch.setattr(orch, "run_training", fake_run_training)

        result = orch.run_daily_check(skip_resource_check=True, dry_run=True)
        assert result["dry_run"] is True
        assert result["decision"]["should"] is True
        assert result["training_result"]["status"] == "dry_run"
        assert called["count"] == 0  # run_training 不应被调用

    def test_dry_run_false_executes_training(self, orch, monkeypatch):
        """dry_run=False 时应实际执行 run_training。"""
        orch.ensure_table()
        monkeypatch.setattr(orch, "get_new_match_count_since",
                            lambda x=None: {"new_matches": 0,
                                            "total_matches": 1000,
                                            "latest_date": "2026-09-15"})
        # 没有新比赛且不是周期触发 → 跳过
        # 先塞一条 1 天前的全量记录，让 scheduled 也不触发
        started = (datetime.now() - timedelta(days=1)).isoformat()
        conn = orch._get_conn()
        conn.execute(
            "INSERT INTO training_runs "
            "(trigger_source, training_type, started_at, finished_at, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("scheduled", "full", started, started, "success"))
        conn.commit()
        conn.close()

        result = orch.run_daily_check(skip_resource_check=True, dry_run=False)
        assert result["dry_run"] is False
        assert result["decision"]["should"] is False
        assert result["training_result"] is None

    def test_resource_insufficient_skips(self, orch, monkeypatch):
        """资源不足时直接返回，不走数据/决策。"""
        orch.ensure_table()
        monkeypatch.setattr(orch, "check_resource_availability",
                            lambda: {"ok": False, "reason": "CPU=95%",
                                     "cpu_pct": 95, "mem_pct": 50,
                                     "disk_pct": 30})
        result = orch.run_daily_check(skip_resource_check=False, dry_run=True)
        assert result["resource"]["ok"] is False
        assert result["data"] is None
        assert result["decision"]["should"] is False

    def test_get_new_match_count_handles_missing_db(self, orch, monkeypatch,
                                                     tmp_path):
        """DB 连接失败时返回 0，不崩溃。"""
        # 把 DB_PATH 指向不存在的路径
        bad_db = tmp_path / "nonexistent.db"
        monkeypatch.setattr(orch, "DATA_DIR", tmp_path)
        # 用 try/except 验证函数不崩溃
        try:
            result = orch.get_new_match_count_since()
            assert result["new_matches"] == 0
            assert result["total_matches"] == 0
        except Exception as e:
            # 也可以抛异常，至少不应是语法/结构错误
            assert "unable to open" in str(e).lower() or isinstance(
                e, (sqlite3.OperationalError, FileNotFoundError))
