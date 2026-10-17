# -*- coding: utf-8 -*-
"""离线预测结果回写 odds.db（matches + model_predictions），打通「预测→落库→回测→重训」闭环。

与 shared/prediction-writer.js 的 savePreMatchPrediction 字段口径一致：
  - match_id = "YYYY-MM-DD_EnglishHome_EnglishAway"
  - prediction_type 细粒度：WDL_home/WDL_draw/WDL_away、HC_upper/HC_draw/HC_lower、
    TG_over_2_5/TG_under_2_5、TG_top1/TG_top3（C-20260921-035）、
    Lambda_home/Lambda_away、Score_top1/Score_top5（C-20260921-034）
  - 幂等：INSERT OR IGNORE + UNIQUE(match_id, model_name, prediction_type)
  - 连接：busy_timeout=5000、WAL（防 SQLITE_BUSY 高并发写锁）

用法：
  python prediction_db_writer.py <prediction.json>
"""
import json
import logging
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = BASE_DIR / "data" / "odds.db"

DEFAULT_MODEL_VERSION = "generate_unified_report_v2.0"

logger = logging.getLogger("prediction_db_writer")


def _is_valid_prob(p) -> bool:
    """C-20260921-036: TG 写入守卫——概率必须严格落在 (0,1)。

    拦截无有效赔率时 TotalGoalsPredictor 降级产出的 0.0 占位行
    （历史 0.0 脏样本已实测把 Brier 从 0.240 抬到 0.269+）。
    """
    try:
        v = float(p)
    except (TypeError, ValueError):
        return False
    return 0.0 < v < 1.0


def _is_goalline_integer(hp) -> bool:
    """C-20261002-014: 竞彩 goalLine 必为整数；亚盘 0.25/0.5/0.75 档（x.25/x.5/x.75）
    不得写入 matches.handicap（否则又把竞彩口径污染成亚盘，见 §15.2 避坑）。"""
    if hp is None:
        return False
    try:
        return float(hp).is_integer()
    except (TypeError, ValueError):
        return False


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def _ensure_is_replay_column(conn: sqlite3.Connection) -> None:
    """C-20260922-053: 旧库幂等迁移 is_replay 列（0=真实赛前发布，1=赛后回放补算）。"""
    cols = {r[1] for r in conn.execute("PRAGMA table_info(model_predictions)")}
    if "is_replay" not in cols:
        conn.execute(
            "ALTER TABLE model_predictions "
            "ADD COLUMN is_replay INTEGER NOT NULL DEFAULT 0"
        )


def _now_iso() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def make_match_id(match_date: str, home_en: str, away_en: str) -> str:
    clean = lambda s: " ".join(str(s).strip().split())
    return f"{match_date}_{clean(home_en)}_{clean(away_en)}"


def derive_season(match_date: str) -> str:
    """根据比赛日期推导赛季（8 月起为新赛季，横跨两个自然年）。"""
    y = int(match_date[:4])
    m = int(match_date[5:7])
    return f"{y}-{y + 1}" if m >= 8 else f"{y - 1}-{y}"


def save_pre_match_prediction(pred: dict, model_version: str = DEFAULT_MODEL_VERSION,
                              is_replay: bool = False,
                              pred_timestamp: str = None) -> str:
    """写入一场比赛的赛前预测（matches + model_predictions，幂等）。

    pred 字段（与 prediction-writer.js 输入一致）:
      matchDate, homeTeam, awayTeam, homeTeamCn, awayTeamCn,
      league, season, handicap,
      wdl={home,draw,away}, handicapProb={upper,draw,lower},
      totalGoalsProbOver, totalGoalsProbUnder, lambdaHome, lambdaAway,
      scoreTop5=[{score,prob}]（C-20260921-034，T-006 真实发布比分）
      另支持 isReplay/predTimestamp（C-20260922-053，与显式参数等价）

    C-20260922-053 时间语义保护：
      - is_replay=True 表示赛后回放补算（--replay），须同时给 pred_timestamp
        赛前锚点（build_log/md 证据时间或比赛日）；timestamp 不再无脑取 now。
      - UPSERT 对 timestamp/is_replay 取 MIN：赛后重跑不得抹掉更早的赛前
        时间证据；真赛前行(is_replay=0)不被回放污染。概率等数据列照常覆盖
        （保留 C-20260919-018 的修正语义）。
    """
    # dict 内嵌字段与显式参数等价（显式参数优先）
    is_replay = bool(is_replay or pred.get("isReplay"))
    pred_timestamp = pred_timestamp or pred.get("predTimestamp")
    match_id = make_match_id(pred["matchDate"], pred["homeTeam"], pred["awayTeam"])
    row_ts = pred_timestamp or _now_iso()
    replay_flag = 1 if is_replay else 0

    # C-20261002-014 / C-20261013-067: pred["handicap"] 现来自竞彩整数 goalLine（SSOT），
    # 亚盘兜底已禁用；守卫保留作防御层，万一亚盘线再次混入则非整数一律不写入（置 NULL）。
    raw_hcp = pred.get("handicap")
    hcp_line = raw_hcp if _is_goalline_integer(raw_hcp) else None
    if raw_hcp is not None and hcp_line is None:
        logger.warning("handicap 写入守卫: match_id=%s 亚盘线 %r 非整数盘口，matches.handicap 置 NULL",
                       match_id, raw_hcp)

    conn = _connect()
    try:
        _ensure_is_replay_column(conn)
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
             pred["homeTeam"], pred["awayTeam"], pred["matchDate"], hcp_line,
             _now_iso(), _now_iso()),
        )

        # 2. model_predictions 表
        # C-20260919-018: 由 INSERT OR IGNORE 改为 UPSERT —— 修正后重跑需覆盖旧预测
        # （旧实现导致重生成只更新报告文件，DB 内残留首次预测）。
        # C-20260922-053: timestamp/is_replay 改 MIN 语义——赛后重跑只覆盖数据列，
        # 不抹掉更早的赛前时间证据，真赛前行(is_replay=0)不被回放补算污染。
        # P0-06: 追加 input_snapshot_json / feature_version / config_version / model_version 四溯源字段
        insert_pred = (
            "INSERT INTO model_predictions "
            "(match_id, model_name, prediction_type, prediction, probability, confidence, timestamp, "
            " is_replay, input_snapshot_json, feature_version, config_version, model_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(match_id, model_name, prediction_type) DO UPDATE SET "
            " prediction=excluded.prediction, probability=excluded.probability, "
            " confidence=excluded.confidence, "
            " timestamp=MIN(model_predictions.timestamp, excluded.timestamp), "
            " is_replay=MIN(model_predictions.is_replay, excluded.is_replay), "
            " input_snapshot_json=excluded.input_snapshot_json, "
            " feature_version=excluded.feature_version, "
            " config_version=excluded.config_version, "
            " model_version=excluded.model_version"
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
                conn.execute(insert_pred, (match_id, model_version, ptype, label, prob, prob, row_ts, replay_flag) + extra)

        # C-20261013-068: 竞彩未开让球盘（hcp_line=None）不写 HC 行——
        # prediction_core 已短路返回 None 概率，此为防御层防其他调用路径漏短路。
        hcp = pred.get("handicapProb") or {}
        if hcp_line is None:
            logger.info("HC 写入守卫: match_id=%s 竞彩未开让球盘，跳过 HC_upper/draw/lower 落库",
                       match_id)
        else:
            for ptype, label, prob in (
                ("HC_upper", "上盘赢", hcp.get("upper")),
                ("HC_draw", "走水", hcp.get("draw")),
                ("HC_lower", "下盘赢", hcp.get("lower")),
            ):
                if prob is not None:
                    conn.execute(insert_pred, (match_id, model_version, ptype, label, prob, prob, row_ts, replay_flag) + extra)

        # C-20260921-036: TG 写入守卫——0.0/1.0/None/越界概率不落库（无有效赔率降级场）
        over = pred.get("totalGoalsProbOver")
        if _is_valid_prob(over):
            under = pred.get("totalGoalsProbUnder")
            if not _is_valid_prob(under):
                under = round(1.0 - float(over), 6)
            conn.execute(insert_pred, (match_id, model_version, "TG_over_2_5", "大球", over, over, row_ts, replay_flag) + extra)
            conn.execute(insert_pred, (match_id, model_version, "TG_under_2_5", "小球", under, under, row_ts, replay_flag) + extra)
        else:
            logger.warning("TG 写入守卫: match_id=%s over25=%r 非有效概率，跳过 TG_over/under 落库",
                           match_id, over)

        # C-20260921-035: 真实发布精确进球数（复盘只读，大小球不再作结论）
        # C-20260921-036: top3 同样走概率守卫，脏数据/越界整组不落库
        tg_top3 = pred.get("totalGoalsTop3") or []
        tg_top3_valid = [x for x in tg_top3 if _is_valid_prob(x.get("prob"))]
        if tg_top3_valid:
            t1 = tg_top3_valid[0]
            t1_label = t1.get("label") or f"{t1.get('goals')}球"
            t1_prob = float(t1.get("prob"))
            conn.execute(insert_pred, (match_id, model_version, "TG_top1",
                        t1_label, t1_prob, t1_prob, row_ts, replay_flag) + extra)
            t3_items = [
                {"goals": x.get("goals"),
                 "label": x.get("label") or f"{x.get('goals')}球",
                 "prob": float(x.get("prob"))}
                for x in list(tg_top3_valid)[:3]
            ]
            conn.execute(insert_pred, (match_id, model_version, "TG_top3",
                        json.dumps(t3_items, ensure_ascii=False), None, None, row_ts, replay_flag) + extra)
        elif tg_top3:
            logger.warning("TG 写入守卫: match_id=%s top3 概率全部无效，跳过 TG_top1/top3 落库",
                           match_id)

        lh = pred.get("lambdaHome")
        if lh is not None:
            conn.execute(insert_pred, (match_id, model_version, "Lambda_home", str(lh), lh, lh, row_ts, replay_flag) + extra)
        la = pred.get("lambdaAway")
        if la is not None:
            conn.execute(insert_pred, (match_id, model_version, "Lambda_away", str(la), la, la, row_ts, replay_flag) + extra)

        # P1-13: λ 主客差值告警落库（prediction_type=Lambda_alert）
        la_alert = pred.get("lambda_alert")
        if la_alert and la_alert.get("triggered"):
            alert_val = la_alert.get("diff", 0.0)
            conn.execute(insert_pred, (match_id, model_version, "Lambda_alert",
                        f"λ差值告警: {la_alert.get('message', '')}",
                        float(alert_val), float(alert_val), row_ts, replay_flag) + extra)

        # C-20260921-034: T-006 真实发布比分（复盘只读此两行，禁止现场重推——保证复盘=发布口径）
        # Score_top1: prediction=比分, probability=该比分概率；Score_top5: prediction=JSON 数组
        score_top5 = pred.get("scoreTop5") or []
        if score_top5:
            s1 = score_top5[0]
            p1 = float(s1.get("prob")) if s1.get("prob") is not None else None
            conn.execute(insert_pred, (match_id, model_version, "Score_top1",
                        str(s1.get("score")), p1, p1, row_ts, replay_flag) + extra)
            top5_items = [
                {"score": str(x.get("score")),
                 "prob": float(x.get("prob")) if x.get("prob") is not None else None}
                for x in list(score_top5)[:5]
            ]
            conn.execute(insert_pred, (match_id, model_version, "Score_top5",
                        json.dumps(top5_items, ensure_ascii=False), None, None, row_ts, replay_flag) + extra)

        # EV 期望值决策（行式 prediction_type；probability 存原始数值，可能为负表示无价值）
        ev = pred.get("ev") or {}
        if ev.get("decision"):
            best_ev = float(ev.get("best_ev")) if ev.get("best_ev") is not None else 0.0
            conn.execute(insert_pred, (match_id, model_version, "EV_decision", ev["decision"], best_ev, best_ev, row_ts, replay_flag) + extra)
            if ev["decision"] != "AVOID" and ev.get("direction"):
                conn.execute(insert_pred, (match_id, model_version, "EV_direction", ev["direction"], best_ev, best_ev, row_ts, replay_flag) + extra)
        for ptype, val in (
            ("EV_best_ev", ev.get("best_ev")),
            ("EV_best_edge", ev.get("best_edge")),
            ("EV_stake_pct", ev.get("stake_pct")),
            ("EV_vig", ev.get("vig")),
            ("EV_payout_rate", ev.get("payout_rate")),
        ):
            if val is not None:
                conn.execute(insert_pred, (match_id, model_version, ptype, str(val), float(val), float(val), row_ts, replay_flag) + extra)

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
                conn.execute(insert_pred, (match_id, model_version, ptype, str(val), float(val), float(val), row_ts, replay_flag) + extra)
        # 文本型/JSON 型决策元数据（prediction 存文本，probability/confidence 置 0）
        for ptype, val in (
            ("EV_best_direction", ev.get("best_ev_direction")),
            ("EV_kelly_strategy", ev.get("kelly_strategy")),
            ("EV_category", ev.get("match_ev_category")),
            ("EV_odds_snapshot", ev.get("odds_used_for_ev")),
        ):
            if val:
                conn.execute(insert_pred, (match_id, model_version, ptype, str(val), 0.0, 0.0, row_ts, replay_flag) + extra)

        conn.commit()
    finally:
        conn.close()

    return match_id


def batch_save_predictions(preds: list, model_version: str = DEFAULT_MODEL_VERSION) -> list:
    """批量写回多场预测，返回写入的 match_id 列表。

    C-20260922-053: 各场可在 dict 内带 isReplay/predTimestamp（回放锚点）。
    """
    ids = []
    for p in preds:
        ids.append(save_pre_match_prediction(p, model_version))
    return ids


# C-20261011-049: 5 子模型 WDL 概率落库（影子线数据地基）
# 报告子模型表键（generate_unified_report.SUB_MODEL_LABELS）→ 落库标签
SUBMODEL_TAGS = (
    ("dixonColes", "DC"),
    ("xgboost", "XGB"),
    ("lightgbm", "LGB"),
    ("elo", "Elo"),
    ("bayesian", "Bayes"),
)
_SUBMODEL_DIRS = (("win", "home", "主胜"), ("draw", "draw", "平"), ("lose", "away", "客胜"))


def save_submodel_probs(match_date: str, home_team: str, away_team: str,
                        sub_models: dict, model_version: str = DEFAULT_MODEL_VERSION,
                        is_replay: bool = False, pred_timestamp: str = None) -> int:
    """写入一场比赛 5 个基础子模型的 WDL 三向概率（最多 15 行，UPSERT 幂等）。

    C-20261011-049：子模型概率此前只活在赛前报告 MD 中（model_predictions 仅
    融合值 WDL_home/draw/away），C-046 §15.5⑧ argmax 一致性维度与 C-048 族
    结构影子线评估需要查库取数。本函数仅铺数据地基，不参与任何生产口径。

    prediction_type 命名：Submodel_<DC|XGB|LGB|Elo|Bayes>_<home|draw|away>
    写入/时间语义与 save_pre_match_prediction 完全一致（timestamp、is_replay
    取 MIN；概率走 _is_valid_prob 守卫，脏值跳过）。返回写入行数。
    """
    match_id = make_match_id(match_date, home_team, away_team)
    row_ts = pred_timestamp or _now_iso()
    replay_flag = 1 if is_replay else 0
    written = 0

    conn = _connect()
    try:
        _ensure_is_replay_column(conn)
        insert_pred = (
            "INSERT INTO model_predictions "
            "(match_id, model_name, prediction_type, prediction, probability, confidence, timestamp, "
            " is_replay, input_snapshot_json, feature_version, config_version, model_version) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, NULL, ?) "
            "ON CONFLICT(match_id, model_name, prediction_type) DO UPDATE SET "
            " prediction=excluded.prediction, probability=excluded.probability, "
            " confidence=excluded.confidence, "
            " timestamp=MIN(model_predictions.timestamp, excluded.timestamp), "
            " is_replay=MIN(model_predictions.is_replay, excluded.is_replay), "
            " model_version=excluded.model_version"
        )
        for model_key, tag in SUBMODEL_TAGS:
            p = (sub_models or {}).get(model_key)
            if not p:
                continue
            for src_key, dir_key, label in _SUBMODEL_DIRS:
                val = p.get(src_key)
                if _is_valid_prob(val):
                    val = float(val)
                    conn.execute(
                        insert_pred,
                        (match_id, model_version, f"Submodel_{tag}_{dir_key}",
                         label, val, val, row_ts, replay_flag, model_version),
                    )
                    written += 1
        conn.commit()
    finally:
        conn.close()
    return written


def batch_save_submodel_probs(rows: list, model_version: str = DEFAULT_MODEL_VERSION) -> int:
    """批量落库行式结果（generate_unified_report rows 结构：含 home_en/away_en/
    match_date/sub_models/_is_replay/_pred_timestamp）。返回总写入行数。"""
    total = 0
    for r in rows:
        total += save_submodel_probs(
            r["match_date"], r["home_en"], r["away_en"], r.get("sub_models"),
            model_version=model_version,
            is_replay=bool(r.get("_is_replay")),
            pred_timestamp=r.get("_pred_timestamp"),
        )
    return total


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
        # C-20260921-035: 真实发布精确进球 Top3，落 TG_top1/TG_top3
        "totalGoalsTop3": tg.get("top3"),
        "lambdaHome": score.get("lambda_home"),
        "lambdaAway": score.get("lambda_away"),
        # C-20260921-034: 真实发布 Top5（list[{score,prob}]），落 Score_top1/Score_top5
        "scoreTop5": score.get("top5"),
        "lambda_alert": m.get("lambda_alert"),  # P1-13: λ 主客差值告警
        "ev": m.get("ev"),
        # P0-06: 预测溯源字段（input_snapshot_json 已序列化为 JSON 字符串，版本字段随行携带）
        "input_snapshot_json": m.get("_input_snapshot_json"),
        "feature_version": m.get("_feature_version"),
        "config_version": m.get("_config_version"),
        # C-20260922-053: 赛后回放补算标记与赛前锚点时间戳
        "isReplay": bool(m.get("_is_replay")),
        "predTimestamp": m.get("_pred_timestamp"),
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