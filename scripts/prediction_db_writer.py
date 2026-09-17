# -*- coding: utf-8 -*-
"""离线预测结果回写 odds.db（matches + model_predictions），打通「预测→落库→回测→重训」闭环。

与 shared/prediction-writer.js 的 savePreMatchPrediction 字段口径一致：
  - match_id = "YYYY-MM-DD_EnglishHome_EnglishAway"
  - prediction_type 细粒度：WDL_home/WDL_draw/WDL_away、HC_upper/HC_draw/HC_lower、
    TG_over_2_5/TG_under_2_5、Lambda_home/Lambda_away
  - 幂等：INSERT OR IGNORE + UNIQUE(match_id, model_name, prediction_type)
  - 连接：busy_timeout=5000、WAL（防 SQLITE_BUSY 高并发写锁）

用法：
  python prediction_db_writer.py <prediction.json>
"""
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = BASE_DIR / "data" / "odds.db"

DEFAULT_MODEL_VERSION = "generate_unified_report_v2.0"


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def make_match_id(match_date: str, home_en: str, away_en: str) -> str:
    clean = lambda s: " ".join(str(s).strip().split())
    return f"{match_date}_{clean(home_en)}_{clean(away_en)}"


def derive_season(match_date: str) -> str:
    """根据比赛日期推导赛季（8 月起为新赛季，横跨两个自然年）。"""
    y = int(match_date[:4])
    m = int(match_date[5:7])
    return f"{y}-{y + 1}" if m >= 8 else f"{y - 1}-{y}"


def save_pre_match_prediction(pred: dict, model_version: str = DEFAULT_MODEL_VERSION) -> str:
    """写入一场比赛的赛前预测（matches + model_predictions，幂等）。

    pred 字段（与 prediction-writer.js 输入一致）:
      matchDate, homeTeam, awayTeam, homeTeamCn, awayTeamCn,
      league, season, handicap,
      wdl={home,draw,away}, handicapProb={upper,draw,lower},
      totalGoalsProbOver, totalGoalsProbUnder, lambdaHome, lambdaAway
    """
    match_id = make_match_id(pred["matchDate"], pred["homeTeam"], pred["awayTeam"])
    now = _now_iso()

    conn = _connect()
    try:
        # 1. matches 表（赛前无 actual 字段，赛后由 updatePostMatchResult 回填）
        conn.execute(
            """INSERT OR IGNORE INTO matches
               (match_id, match_type, league, home_team, away_team, match_date, handicap, handicap_source,
                actual_wdl, actual_handicap, actual_score, actual_total_goals,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, 'sporttery', NULL, NULL, NULL, NULL, ?, ?)""",
            (match_id,
             f"{pred['league']}{pred['season']}赛季",
             pred["league"],
             pred["homeTeam"], pred["awayTeam"], pred["matchDate"], pred.get("handicap"),
             now, now),
        )

        # 2. model_predictions 表（幂等：INSERT OR IGNORE + 唯一约束）
        # P0-06: 追加 input_snapshot_json / feature_version / config_version / model_version 四溯源字段
        insert_pred = (
            "INSERT OR IGNORE INTO model_predictions "
            "(match_id, model_name, prediction_type, prediction, probability, confidence, timestamp, "
            " input_snapshot_json, feature_version, config_version, model_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
        )
        extra = (
            pred.get("input_snapshot_json"),
            pred.get("feature_version"),
            pred.get("config_version"),
            model_version,
        )

        wdl = pred.get("wdl") or {}
        for ptype, label, prob in (
            ("WDL_home", "主胜", wdl.get("home")),
            ("WDL_draw", "平", wdl.get("draw")),
            ("WDL_away", "客胜", wdl.get("away")),
        ):
            if prob is not None:
                conn.execute(insert_pred, (match_id, model_version, ptype, label, prob, prob, now) + extra)

        hcp = pred.get("handicapProb") or {}
        for ptype, label, prob in (
            ("HC_upper", "上盘赢", hcp.get("upper")),
            ("HC_draw", "走水", hcp.get("draw")),
            ("HC_lower", "下盘赢", hcp.get("lower")),
        ):
            if prob is not None:
                conn.execute(insert_pred, (match_id, model_version, ptype, label, prob, prob, now) + extra)

        over = pred.get("totalGoalsProbOver")
        if over is not None:
            conn.execute(insert_pred, (match_id, model_version, "TG_over_2_5", "大球", over, over, now) + extra)
        under = pred.get("totalGoalsProbUnder")
        if under is not None:
            conn.execute(insert_pred, (match_id, model_version, "TG_under_2_5", "小球", under, under, now) + extra)

        lh = pred.get("lambdaHome")
        if lh is not None:
            conn.execute(insert_pred, (match_id, model_version, "Lambda_home", str(lh), lh, lh, now) + extra)
        la = pred.get("lambdaAway")
        if la is not None:
            conn.execute(insert_pred, (match_id, model_version, "Lambda_away", str(la), la, la, now) + extra)

        # P1-13: λ 主客差值告警落库（prediction_type=Lambda_alert）
        la_alert = pred.get("lambda_alert")
        if la_alert and la_alert.get("triggered"):
            alert_val = la_alert.get("diff", 0.0)
            conn.execute(insert_pred, (match_id, model_version, "Lambda_alert",
                        f"λ差值告警: {la_alert.get('message', '')}",
                        float(alert_val), float(alert_val), now) + extra)

        # EV 期望值决策（行式 prediction_type；probability 存原始数值，可能为负表示无价值）
        ev = pred.get("ev") or {}
        if ev.get("decision"):
            best_ev = float(ev.get("best_ev")) if ev.get("best_ev") is not None else 0.0
            conn.execute(insert_pred, (match_id, model_version, "EV_decision", ev["decision"], best_ev, best_ev, now) + extra)
            if ev["decision"] != "AVOID" and ev.get("direction"):
                conn.execute(insert_pred, (match_id, model_version, "EV_direction", ev["direction"], best_ev, best_ev, now) + extra)
        for ptype, val in (
            ("EV_best_ev", ev.get("best_ev")),
            ("EV_best_edge", ev.get("best_edge")),
            ("EV_stake_pct", ev.get("stake_pct")),
            ("EV_vig", ev.get("vig")),
            ("EV_payout_rate", ev.get("payout_rate")),
        ):
            if val is not None:
                conn.execute(insert_pred, (match_id, model_version, ptype, str(val), float(val), float(val), now) + extra)

        # P0-04: 每方向 EV / edge + 决策元数据完整落库（脱离报告即可复现全场 EV 决策）
        for ptype, val in (
            ("EV_home", ev.get("ev_home")),
            ("EV_draw", ev.get("ev_draw")),
            ("EV_away", ev.get("ev_away")),
            ("edge_home", ev.get("edge_home")),
            ("edge_draw", ev.get("edge_draw")),
            ("edge_away", ev.get("edge_away")),
            ("EV_kelly_fraction", ev.get("kelly_fraction")),
            ("EV_threshold_used", ev.get("ev_threshold_used")),
        ):
            if val is not None:
                conn.execute(insert_pred, (match_id, model_version, ptype, str(val), float(val), float(val), now) + extra)
        # 文本型/JSON 型决策元数据（prediction 存文本，probability/confidence 置 0）
        for ptype, val in (
            ("EV_best_direction", ev.get("best_ev_direction")),
            ("EV_kelly_strategy", ev.get("kelly_strategy")),
            ("EV_category", ev.get("match_ev_category")),
            ("EV_odds_snapshot", ev.get("odds_used_for_ev")),
        ):
            if val:
                conn.execute(insert_pred, (match_id, model_version, ptype, str(val), 0.0, 0.0, now) + extra)

        conn.commit()
    finally:
        conn.close()

    return match_id


def batch_save_predictions(preds: list, model_version: str = DEFAULT_MODEL_VERSION) -> list:
    """批量写回多场预测，返回写入的 match_id 列表。"""
    ids = []
    for p in preds:
        ids.append(save_pre_match_prediction(p, model_version))
    return ids


def serializable_to_pred(m: dict) -> dict:
    """把 run_prematch_3matches.py / generate_unified_report.py 输出的结果项，
    转换为 save_pre_match_prediction 需要的输入。"""
    match_date = m["match_time"].split(" ")[0]
    wdl = m.get("wdl") or {}
    hcp = m.get("hcp") or {}
    score = m.get("score") or {}
    tg = m.get("tg") or {}

    over = tg.get("over_25_prob")
    return {
        "matchDate": match_date,
        "homeTeam": m["home_en"],
        "awayTeam": m["away_en"],
        "homeTeamCn": m["home"],
        "awayTeamCn": m["away"],
        "league": m["league"],
        "season": derive_season(match_date),
        "handicap": hcp.get("line"),
        "wdl": {"home": wdl.get("home_prob"), "draw": wdl.get("draw_prob"), "away": wdl.get("away_prob")},
        "handicapProb": {"upper": hcp.get("home_win_prob"), "draw": hcp.get("draw_prob"), "lower": hcp.get("away_win_prob")},
        "totalGoalsProbOver": over,
        "totalGoalsProbUnder": round(1 - float(over), 6) if over is not None else None,
        "lambdaHome": score.get("lambda_home"),
        "lambdaAway": score.get("lambda_away"),
        "lambda_alert": m.get("lambda_alert"),  # P1-13: λ 主客差值告警
        "ev": m.get("ev"),
        # P0-06: 预测溯源字段（input_snapshot_json 已序列化为 JSON 字符串，版本字段随行携带）
        "input_snapshot_json": m.get("_input_snapshot_json"),
        "feature_version": m.get("_feature_version"),
        "config_version": m.get("_config_version"),
    }


def write_json_file(json_path: str, model_version: str = DEFAULT_MODEL_VERSION) -> list:
    """读取预测 JSON（list 结构），逐场回写数据库，返回 match_id 列表。"""
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    return batch_save_predictions([serializable_to_pred(m) for m in data], model_version)


def main():
    if len(sys.argv) < 2:
        print("用法: python prediction_db_writer.py <prediction.json>")
        sys.exit(1)
    ids = write_json_file(sys.argv[1])
    print(f"✅ 已回写 {len(ids)} 场比赛到 model_predictions:")
    for i in ids:
        print(f"  - {i}")


if __name__ == "__main__":
    main()