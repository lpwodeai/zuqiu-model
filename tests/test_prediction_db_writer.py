# -*- coding: utf-8 -*-
"""prediction_db_writer 单元测试 — match_id 生成、赛季推导、序列化转换。

C-20260922-053 新增 DB 级测试：
  - replay 落库携带赛前锚点 timestamp + is_replay=1
  - UPSERT 的 timestamp 为 MIN 语义（只早不晚，赛后重跑不得抹掉赛前时间证据）
  - is_replay 为 MIN 语义（0 优先，真赛前预测不被回放污染）
  - 旧库自动迁移 is_replay 列
"""
import sqlite3
from datetime import datetime

import pytest

import prediction_db_writer
from prediction_db_writer import (
    make_match_id,
    derive_season,
    serializable_to_pred,
    save_pre_match_prediction,
)


def _make_minimal_pred(**over):
    pred = {
        "matchDate": "2026-08-29",
        "homeTeam": "Liverpool FC",
        "awayTeam": "Nottingham Forest",
        "homeTeamCn": "利物浦",
        "awayTeamCn": "诺丁汉森林",
        "league": "英超",
        "season": "2026-2027",
        "handicap": -1.0,
        "wdl": {"home": 0.59, "draw": 0.18, "away": 0.23},
    }
    pred.update(over)
    return pred


@pytest.fixture
def temp_db(tmp_path, monkeypatch):
    """把 writer 指向临时库并建最小生产同构 schema。"""
    db_path = tmp_path / "odds.db"
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """CREATE TABLE matches (
             match_id TEXT PRIMARY KEY, match_type TEXT, league TEXT,
             home_team TEXT, away_team TEXT, match_date TEXT, handicap REAL,
             handicap_source TEXT, actual_wdl TEXT, actual_handicap TEXT,
             actual_score TEXT, actual_total_goals INTEGER,
             created_at TEXT, updated_at TEXT)"""
    )
    conn.execute(
        """CREATE TABLE model_predictions (
             id INTEGER PRIMARY KEY AUTOINCREMENT,
             match_id TEXT NOT NULL, model_name TEXT NOT NULL,
             prediction_type TEXT NOT NULL, prediction TEXT,
             probability REAL, confidence REAL, timestamp TEXT,
             input_snapshot_json TEXT, feature_version TEXT,
             config_version TEXT, model_version TEXT,
             UNIQUE(match_id, model_name, prediction_type))"""
    )
    conn.commit()
    conn.close()
    monkeypatch.setattr(prediction_db_writer, "ODDS_DB", db_path)
    return db_path


def _read_wdl_home(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT timestamp, probability, is_replay FROM model_predictions "
        "WHERE prediction_type='WDL_home'"
    ).fetchone()
    conn.close()
    return row


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


class TestReplayTimestampSemantics:
    """C-053 写入侧时间语义保护（DB 级）。"""

    def test_replay_uses_anchor_and_flag(self, temp_db):
        """replay 落库：timestamp=赛前锚点，is_replay=1。"""
        save_pre_match_prediction(
            _make_minimal_pred(), is_replay=True,
            pred_timestamp="2026-08-29 00:00:00")
        row = _read_wdl_home(temp_db)
        assert row["timestamp"] == "2026-08-29 00:00:00"
        assert row["is_replay"] == 1

    def test_normal_write_is_not_replay(self, temp_db):
        """常规赛前落库：is_replay=0。"""
        save_pre_match_prediction(_make_minimal_pred())
        assert _read_wdl_home(temp_db)["is_replay"] == 0

    def test_upsert_keeps_earliest_timestamp(self, temp_db):
        """赛后重跑（更晚 ts）不得覆盖更早的赛前时间戳，但概率值照常更新。"""
        save_pre_match_prediction(
            _make_minimal_pred(wdl={"home": 0.50, "draw": 0.20, "away": 0.30}),
            pred_timestamp="2026-08-29 12:00:00")
        save_pre_match_prediction(
            _make_minimal_pred(wdl={"home": 0.59, "draw": 0.18, "away": 0.23}),
            pred_timestamp="2026-09-14 14:19:44")
        row = _read_wdl_home(temp_db)
        assert row["timestamp"] == "2026-08-29 12:00:00"
        assert row["probability"] == pytest.approx(0.59)

    def test_upsert_takes_earlier_timestamp_when_rewriting(self, temp_db):
        """先写晚 ts 再补写早 ts（锚点修复场景）：时间戳取更早者。"""
        save_pre_match_prediction(
            _make_minimal_pred(), pred_timestamp="2026-09-14 14:19:44")
        save_pre_match_prediction(
            _make_minimal_pred(), is_replay=True,
            pred_timestamp="2026-08-29 00:00:00")
        row = _read_wdl_home(temp_db)
        assert row["timestamp"] == "2026-08-29 00:00:00"

    def test_premark_flag_not_polluted_by_replay(self, temp_db):
        """真赛前 is_replay=0 的行，赛后 replay 重写后仍为 0（0 优先）。"""
        save_pre_match_prediction(
            _make_minimal_pred(), pred_timestamp="2026-08-29 12:00:00")
        save_pre_match_prediction(
            _make_minimal_pred(), is_replay=True,
            pred_timestamp="2026-08-29 00:00:00")
        assert _read_wdl_home(temp_db)["is_replay"] == 0

    def test_legacy_db_auto_migrates_is_replay_column(self, temp_db):
        """无 is_replay 列的旧库：首次写入自动迁移且历史默认 0。"""
        save_pre_match_prediction(_make_minimal_pred())
        row = _read_wdl_home(temp_db)
        assert row["is_replay"] == 0

    def test_pred_dict_carries_replay_fields(self):
        """serializable_to_pred 透传 _is_replay/_pred_timestamp。"""
        m = {
            "match_time": "2026-08-29 20:00",
            "home_en": "Liverpool FC", "away_en": "Nottingham Forest",
            "home": "利物浦", "away": "诺丁汉森林", "league": "英超",
            "_is_replay": True, "_pred_timestamp": "2026-08-29 00:00:00",
        }
        pred = serializable_to_pred(m)
        assert pred["isReplay"] is True
        assert pred["predTimestamp"] == "2026-08-29 00:00:00"


class TestReplayAnchorResolution:
    """C-053 --replay 赛前时间锚点解析（generate_unified_report）。"""

    def test_buildlog_generated_at_when_premark(self):
        from generate_unified_report import pick_replay_anchor
        ts, evidence = pick_replay_anchor(
            "2026-08-29", "2026-08-28 12:00:00",
            datetime(2026, 9, 21, 0, 14))
        assert ts == "2026-08-28 12:00:00"
        assert evidence == "buildlog"

    def test_file_ctime_when_buildlog_postmatch(self):
        """build_log 已被赛后重渲，但 md 创建时间是赛前 → 取 ctime。"""
        from generate_unified_report import pick_replay_anchor
        ts, evidence = pick_replay_anchor(
            "2026-09-19", "2026-09-21 08:18:43",
            datetime(2026, 9, 16, 12, 4))
        assert ts == "2026-09-16 12:04:00"
        assert evidence == "file_ctime"

    def test_match_date_fallback_without_evidence(self):
        """无任何赛前证据（赛后首次补算）→ 保守锚比赛日 00:00。"""
        from generate_unified_report import pick_replay_anchor
        ts, evidence = pick_replay_anchor(
            "2026-08-29", "2026-09-14 12:01:27",
            datetime(2026, 9, 14, 12, 1))
        assert ts == "2026-08-29 00:00:00"
        assert evidence == "match_date"

    def test_match_date_fallback_when_nothing(self):
        from generate_unified_report import pick_replay_anchor
        ts, evidence = pick_replay_anchor("2026-08-29", None, None)
        assert ts == "2026-08-29 00:00:00"
        assert evidence == "match_date"

    def test_resolve_reads_preexisting_buildlog(self, tmp_path):
        """集成：渲染前读旧 build_log 的 generated_at。"""
        import json as _json
        from generate_unified_report import resolve_replay_anchor
        report = tmp_path / "英超_2026-08-29_利物浦_vs_诺丁汉森林.md"
        report.write_text("old", encoding="utf-8")
        log = report.parent / (report.stem + "_build_log.json")
        log.write_text(_json.dumps({"generated_at": "2026-08-28 12:00:00"}),
                       encoding="utf-8")
        ts, evidence = resolve_replay_anchor("2026-08-29", report)
        assert ts == "2026-08-28 12:00:00"
        assert evidence == "buildlog"
