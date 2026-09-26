# -*- coding: utf-8 -*-
"""
run_post_match_pipeline.py — 复盘闭环全链路自动化（A2→A3→A4→A6→A5，P0）

===============================================
背景（已归档：原模型改进实施方案 v1.0 §三/A2-A6 + AI复盘闭环落地指南 §1.1）：
  每轮完赛 30 分钟内自动产出结构化复盘，样本持续增长，驱动漂移监控与知识库建设。
  本脚本把 A1-A6 各模块串成单入口，调度建议：Windows 计划任务每小时一次 --catch-up。

全链路（单场）：
  A2 采集   : SofaScore 赛后赛果/阵容/统计/事件时间线（collect_post_match），
              失败时回退 matches 表已有赛果（无赛后明细，A3 降维运行）
  A1 写库   : post_match_review 1 行 + model_predictions 4 行 actual_*（幂等）
  A3 归因   : 8 维度纯规则归因（attribution_engine），写回 attribution_json
  A6 门禁   : compute_data_quality → data_quality_score<0.7 不纳入统计、不写知识库、
              仅反馈采集器（报告自动红标 [缺数据]，禁止反馈模型调参）
  A5 分级   : baseline_level 基线可信度 1-3 级写回 confidence_level；
              仅人工审核 --approve（≥4 级）才写因子库——无人工审核时只出草稿
  A4 报告   : 8 章节复盘 md → docs/post_match/{YYYYMMDD}/ + 当日 _summary.md

运行模式：
  --date YYYY-MM-DD   批量处理指定比赛日
  --match-id <ID>     单场处理
  --catch-up          调度模式：扫「已完赛(≤今日)且已预测但无复盘」的场次
                      （substr(match_id,1,10) 介于 [今日-catchup_days, 今日]）
  --limit N           单次最多处理场次（调度友好，默认 30）
  --dry-run           只扫+采集+计算，不写库不写文件

基础设施约定（§1.3，A1 已固化）：
  - 数据库路径动态定位，禁硬编码盘符
  - odds.db 连接 PRAGMA busy_timeout = 5000
  - 写库统一走 scripts/post_match_schema.py（队名归一 + JSON 序列化 + INSERT OR IGNORE 幂等）
"""

from __future__ import annotations

import argparse
import json
import logging
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# 项目根目录（scripts 的上一级）
PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

# 采集器路径（collection/）
sys.path.insert(0, str(PROJECT_DIR / "collection"))
from final_sofascore_collector import (  # noqa: E402
    SofaScoreClient,
    collect_post_match,
    setup_logging,
)

# 复用 A1 schema 与写库函数
sys.path.insert(0, str(PROJECT_DIR / "scripts"))
from post_match_schema import (  # noqa: E402
    ensure_post_match_schema,
    write_review_and_actuals,
    DEFAULT_MODEL_NAME,
)
from attribution_engine import AttributionEngine, get_event_id  # noqa: E402
sys.path.insert(0, str(PROJECT_DIR / "features"))
from sofascore_pre_match_features import SOFA_TEAM_CN_MAP  # noqa: E402
from confidence_review import baseline_level  # noqa: E402
from generate_post_match_report import (  # noqa: E402
    _fill_review_fields,
    _safe_name,
    build_report,
    build_summary,
    compute_data_quality,
    compute_review_fields,
    load_predictions,
)
# C-20260919-021: 让球盘口线解析（中文串→数值，与赛前报告同口径）
from generate_unified_report import parse_hcp_line  # noqa: E402
from feature_utils import normalize_team_name  # noqa: E402

DEFAULT_LOGGER_NAME = "post_match_pipeline"
DEFAULT_CATCHUP_DAYS = 7
DEFAULT_LIMIT = 30

# 赛果值域归一（matches.actual_wdl 混合 '主胜/胜/平/负' 与 '主胜/平局/客胜'）
_WDL_NORM = {"主胜": "主胜", "平局": "平局", "客胜": "客胜",
             "胜": "主胜", "平": "平局", "负": "客胜"}


def _get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(str(ODDS_DB))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


# ==================== A2：目标场次扫描 ====================
def wdl_from_score(home: int, away: int) -> str:
    """比分 -> 胜平负（对齐 model_predictions WDL_home/draw/away 行格式）。"""
    if home > away:
        return "主胜"
    if home == away:
        return "平局"
    return "客胜"


def hcp_from_handicap(home: int, away: int, handicap: Optional[float]) -> Optional[str]:
    """让球数与比分 -> 上盘/走水/下盘（对齐 model_predictions HC_upper/draw/lower 行格式）。

    handicap 以主队为基准（负=主队让球，正=主队受让），调整后主队得分 = home + handicap：
      > 客队得分 -> 上盘赢；== -> 走水；< -> 下盘赢
    """
    if handicap is None:
        return None
    adjusted = home + float(handicap)
    if adjusted > away:
        return "上盘赢"
    if adjusted == away:
        return "走水"
    return "下盘赢"


def resolve_handicap_line(
    conn: sqlite3.Connection, match_id: str, match_hcp: Any = None
) -> tuple:
    """解析让球盘口线（主队视角：负=让球/正=受让，与赛前报告 parse_hcp_line 同口径）。

    取数优先级：① matches.handicap 数值（SSOT）；
                ② odds500_match.handicap 中文串按 match_id 直接对齐；
                ③ 按 比赛日+归一化主客中文队名 模糊对齐（match_id 口径失配兜底）。
    返回 (line 或 None, source 或 None)。
    """
    if match_hcp is not None:
        try:
            return float(match_hcp), "matches"
        except (TypeError, ValueError):
            pass

    row = conn.execute(
        "SELECT handicap FROM odds500_match WHERE match_id=? "
        "AND handicap IS NOT NULL AND handicap<>'' LIMIT 1",
        (match_id,),
    ).fetchone()
    if row:
        line = parse_hcp_line(row["handicap"])
        if line is not None:
            return line, "odds500_match"

    m = conn.execute(
        "SELECT match_date, home_team, away_team FROM matches WHERE match_id=?",
        (match_id,),
    ).fetchone()
    if m:
        row = conn.execute(
            "SELECT handicap FROM odds500_match "
            "WHERE match_date=? AND home_team_cn=? AND away_team_cn=? "
            "AND handicap IS NOT NULL AND handicap<>'' LIMIT 1",
            (m["match_date"],
             normalize_team_name(m["home_team"]),
             normalize_team_name(m["away_team"])),
        ).fetchone()
        if row:
            line = parse_hcp_line(row["handicap"])
            if line is not None:
                return line, "odds500_match_fuzzy"
    return None, None


def collect_target_matches(
    conn: sqlite3.Connection,
    match_date: Optional[str],
    match_id_filter: Optional[str],
) -> List[Dict[str, Any]]:
    """扫 model_predictions 去重取目标场次，JOIN fbref_match_mapping 得 event_id/league，JOIN matches 得 handicap。"""
    cur = conn.cursor()

    if match_id_filter:
        where, params = "match_id = ?", [match_id_filter]
    else:
        where, params = "match_id LIKE ?", [f"{match_date}%"]

    cur.execute(
        f"""
        SELECT mp.match_id,
               fm.fbref_match_id,
               fm.league,
               m.handicap
        FROM (SELECT DISTINCT match_id FROM model_predictions WHERE {where}) mp
        LEFT JOIN fbref_match_mapping fm ON mp.match_id = fm.odds_match_id
        LEFT JOIN matches m ON mp.match_id = m.match_id
        ORDER BY mp.match_id
        """,
        params,
    )
    targets = []
    for row in cur.fetchall():
        match_id = row["match_id"]
        event_id = str(row["fbref_match_id"]) if row["fbref_match_id"] else None
        league = row["league"]
        if event_id is None:
            # 预测口径（UTC 日期+预测侧队名）与 mapping 口径（北京日期+SofaScore 队名）失配时回退
            event_id = get_event_id(conn, match_id)
        if event_id and not league:
            # 回退命中后按 event_id 补取 league（否则报告文件名前缀变「未知」）
            lr = conn.execute(
                "SELECT league FROM fbref_match_mapping "
                "WHERE fbref_match_id=? LIMIT 1", (event_id,)).fetchone()
            league = lr[0] if lr else None
        targets.append({
            "match_id": match_id,
            "event_id": event_id,
            "league": league,
            "handicap": row["handicap"],
        })
    return targets


def collect_catchup_targets(
    conn: sqlite3.Connection,
    since: str,
    until: str,
    limit: int,
) -> List[Dict[str, Any]]:
    """调度模式：已预测且已完赛（match_date<=今日）但尚无复盘记录的场次，按日期倒序取 limit 场。"""
    cur = conn.cursor()
    cur.execute(
        """
        SELECT mp.match_id, fm.fbref_match_id, fm.league, m.handicap
        FROM (SELECT DISTINCT match_id FROM model_predictions) mp
        LEFT JOIN fbref_match_mapping fm ON mp.match_id = fm.odds_match_id
        LEFT JOIN matches m ON mp.match_id = m.match_id
        LEFT JOIN post_match_review pmr ON mp.match_id = pmr.match_id
        WHERE pmr.match_id IS NULL
          AND substr(mp.match_id, 1, 10) BETWEEN ? AND ?
        ORDER BY mp.match_id DESC
        LIMIT ?
        """,
        (since, until, limit),
    )
    targets = []
    for row in cur.fetchall():
        match_id = row["match_id"]
        event_id = str(row["fbref_match_id"]) if row["fbref_match_id"] else None
        league = row["league"]
        if event_id is None:
            # 预测口径（UTC 日期+预测侧队名）与 mapping 口径（北京日期+SofaScore 队名）失配时回退
            event_id = get_event_id(conn, match_id)
        if event_id and not league:
            # 回退命中后按 event_id 补取 league（否则报告文件名前缀变「未知」）
            lr = conn.execute(
                "SELECT league FROM fbref_match_mapping "
                "WHERE fbref_match_id=? LIMIT 1", (event_id,)).fetchone()
            league = lr[0] if lr else None
        targets.append({
            "match_id": match_id,
            "event_id": event_id,
            "league": league,
            "handicap": row["handicap"],
        })
    return targets


def get_model_name(conn: sqlite3.Connection, match_id: str) -> str:
    """取该 match_id 在 model_predictions 中已有的 model_name（用于 actual_* 行归属）。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT model_name FROM model_predictions WHERE match_id=? "
        "ORDER BY id DESC LIMIT 1",
        (match_id,),
    )
    row = cur.fetchone()
    return row["model_name"] if row else DEFAULT_MODEL_NAME


def build_review(
    match_id: str,
    league: Optional[str],
    data: Dict[str, Any],
    handicap: Optional[float],
) -> Dict[str, Any]:
    """由 SofaScore 赛后数据组装 post_match_review 行（赛果部分；归因/质量分由 A3/A6 回填）。"""
    home, away = int(data["home_score"]), int(data["away_score"])
    return {
        "match_id": match_id,
        "league": league,
        "match_date": data.get("match_date") or match_id[:10],
        "home_team": data["home_team"],
        "away_team": data["away_team"],
        "actual_score": data["score"],
        "actual_half_score": data.get("half_score"),
        "actual_wdl": wdl_from_score(home, away),
        "actual_hcp": hcp_from_handicap(home, away, handicap),
        "actual_tg": home + away,
    }


def build_review_from_matches(
    conn: sqlite3.Connection, match_id: str, hcp_line: Optional[float] = None
) -> Optional[Dict[str, Any]]:
    """SofaScore 不可用时回退 matches 表已有赛果组装 review（无赛后明细，A3 降维运行）。"""
    cur = conn.cursor()
    cur.execute(
        "SELECT match_date, league, home_team, away_team, actual_wdl, actual_score, "
        "actual_handicap, actual_total_goals, handicap FROM matches WHERE match_id=?",
        (match_id,),
    )
    m = cur.fetchone()
    if m is None or not m["actual_score"] or not m["actual_wdl"]:
        return None
    try:
        hs, aws = str(m["actual_score"]).split(":")
        home, away = int(hs), int(aws)
    except (ValueError, TypeError):
        return None
    wdl = _WDL_NORM.get(str(m["actual_wdl"]))
    if not wdl:
        return None
    # 让球结果统一按 handicap+比分推导（matches.actual_handicap 是 '(-1)负' 等让球胜平负口径，
    # 与复盘表/报告要求的上盘赢/走水/下盘赢不一致）
    # C-20260919-021: matches.handicap 缺失时用外部解析的盘口线（odds500 兜底）
    hcp = hcp_from_handicap(
        home, away, hcp_line if hcp_line is not None else m["handicap"]
    )
    tg = m["actual_total_goals"] if m["actual_total_goals"] is not None else home + away
    return {
        "match_id": match_id,
        "league": m["league"],
        "match_date": m["match_date"] or match_id[:10],
        "home_team": m["home_team"],
        "away_team": m["away_team"],
        "actual_score": m["actual_score"],
        "actual_half_score": None,
        "actual_wdl": wdl,
        "actual_hcp": hcp,
        "actual_tg": int(tg),
    }


# ==================== A3/A5/A6 单模块封装 ====================
def _load_review_row(conn: sqlite3.Connection, match_id: str) -> Optional[sqlite3.Row]:
    cur = conn.cursor()
    cur.execute("SELECT * FROM post_match_review WHERE match_id=?", (match_id,))
    return cur.fetchone()


def _run_a3(conn: sqlite3.Connection, match_id: str,
            post_data: Optional[Dict[str, Any]],
            logger: logging.Logger, write: bool = True) -> Optional[Dict[str, Any]]:
    """A3 八维归因；write=True 时写回 attribution_json（幂等 UPDATE）。失败返回 None 不阻断流水线。"""
    engine = AttributionEngine(conn_odds=conn)
    try:
        attr = engine.run(match_id, post_data=post_data)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[{match_id}] A3 归因失败: {e}")
        return None
    finally:
        engine.close()
    if attr and write:
        cur = conn.cursor()
        cur.execute(
            "UPDATE post_match_review SET attribution_json=? WHERE match_id=?",
            (json.dumps(attr, ensure_ascii=False), match_id),
        )
        conn.commit()
    return attr


def _write_quality_level(conn: sqlite3.Connection, match_id: str,
                         dq_score: float, attr: Optional[Dict[str, Any]]) -> Optional[int]:
    """A6 质量分 + A5 基线可信度写回（无人工审核时最高 3 级，仅作待审基线）。"""
    level = None
    if attr is not None:
        base = baseline_level({"attribution_json": attr})
        level = base["level"]
    cur = conn.cursor()
    cur.execute(
        "UPDATE post_match_review SET data_quality_score=?, confidence_level=? WHERE match_id=?",
        (float(dq_score), level, match_id),
    )
    conn.commit()
    return level


# ==================== 单场全链路 ====================
def _find_reviewed_alias(
    conn: sqlite3.Connection, event_id: str, current_mid: str
) -> Optional[str]:
    """该 SofaScore event 是否已有其他 match_id 口径的复盘行。

    mapping 的 home/away_team_cn 存英文，经 SOFA_TEAM_CN_MAP 转中文后，
    按 match_date + 中文双队名在 post_match_review 查重。
    """
    row = conn.execute(
        "SELECT match_date, home_team_cn, away_team_cn FROM fbref_match_mapping "
        "WHERE fbref_match_id=? LIMIT 1", (event_id,)).fetchone()
    if not row:
        return None
    home_cn = SOFA_TEAM_CN_MAP.get(row["home_team_cn"])
    away_cn = SOFA_TEAM_CN_MAP.get(row["away_team_cn"])
    if not home_cn or not away_cn:
        return None
    r = conn.execute(
        "SELECT match_id FROM post_match_review "
        "WHERE match_date=? AND home_team=? AND away_team=? AND match_id<>? LIMIT 1",
        (row["match_date"], home_cn, away_cn, current_mid)).fetchone()
    return r["match_id"] if r else None


def process_one(
    conn: sqlite3.Connection,
    client: SofaScoreClient,
    target: Dict[str, Any],
    dry_run: bool,
    logger: logging.Logger,
) -> Dict[str, Any]:
    """单场 A2→A1→A3→A6→A5→A4 全链路。返回统计与汇总 dict。"""
    mid = target["match_id"]
    res: Dict[str, Any] = {"mid": mid, "status": "skip"}

    # ---- 同 event 已被其他预测口径复盘 → 跳过（防止重复预测残留产生第二份报告）----
    if target.get("event_id"):
        alias = _find_reviewed_alias(conn, target["event_id"], mid)
        if alias:
            logger.info(f"[{mid}] 同场比赛已有复盘（{alias}），跳过重复口径")
            res["status"] = "existing"
            return res

    # ---- A2 采集赛果/阵容/事件 ----
    post_data = None
    if target.get("event_id"):
        try:
            post_data = collect_post_match(client, target["event_id"], logger)
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{mid}] 赛后采集异常: {e}")

    # ---- C-20260919-021: 统一解析让球盘口线（matches 缺失时 odds500_match 兜底）----
    hcp_line, hcp_src = resolve_handicap_line(conn, mid, target.get("handicap"))
    if hcp_line is not None:
        target["handicap"] = hcp_line
        if hcp_src != "matches":
            logger.info(f"[{mid}] 盘口线兜底: {hcp_line:+.2f}（来源 {hcp_src}）")

    review = None
    if post_data is not None:
        review = build_review(mid, target.get("league"), post_data, hcp_line)
    else:
        post_data = None  # 无赛后明细 → A3 降维运行
        review = build_review_from_matches(conn, mid, hcp_line=hcp_line)
        if review is not None:
            logger.info(f"[{mid}] SofaScore 未采集到赛果，回退 matches 表赛果")

    if review is None:
        # 比赛未结束或任一赛果来源不可用
        res["status"] = "not_finished"
        return res

    if dry_run:
        # 完整计算 A3/A6/A5/A4 但零写入（验证全链路产出）
        attr = _run_a3(conn, mid, post_data, logger, write=False)
        pred = load_predictions(conn, mid)
        fields = compute_review_fields(conn, review, pred)
        dq = compute_data_quality(conn, review, pred, post_data=post_data)
        level = baseline_level({"attribution_json": attr})["level"] if attr else None
        rev = dict(review)
        rev["human_reviewed"] = 0
        _ = build_report(rev, pred, fields, attr, dq, post_data=post_data)  # 仅验证可渲染
        res.update(status="candidate", review=review, fields=fields,
                   attribution=attr, dq=dq, confidence_level=level)
        gate = "🚨<0.7" if dq["alert"] else "✅"
        logger.info(f"[{mid}] [dry-run] 比分 {review['actual_score']} {review['actual_wdl']} | "
                    f"归因={attr.get('primary_cause') if attr else '—'} | "
                    f"基线{level}级 | 质量分{dq['score']} {gate}")
        return res

    # ---- A1 写复盘 + actual_* 四行（幂等） ----
    # C-20260919-021: 盘口线由 500 兜底得到时回写 matches.handicap，避免下次重复兜底
    if hcp_line is not None and hcp_src and hcp_src != "matches":
        cur = conn.cursor()
        cur.execute(
            "UPDATE matches SET handicap=? WHERE match_id=? AND handicap IS NULL",
            (hcp_line, mid),
        )
        if cur.rowcount > 0:
            conn.commit()
            logger.info(f"[{mid}] 盘口线已回写 matches.handicap={hcp_line:+.2f}（{cur.rowcount} 行）")
    model_name = get_model_name(conn, mid)
    result = write_review_and_actuals(conn, review, model_name=model_name)
    res["status"] = "new" if result["review"] else "existing"

    row = _load_review_row(conn, mid)
    if row is None:
        logger.warning(f"[{mid}] 复盘行写入后读取失败")
        return res

    # ---- A3 八维归因 ----
    attr = _run_a3(conn, mid, post_data, logger)

    # ---- A6 数据质量门禁 + A4 预测字段回填 ----
    pred = load_predictions(conn, mid)
    fields = compute_review_fields(conn, row, pred)
    _fill_review_fields(conn, mid, fields)
    dq = compute_data_quality(conn, row, pred, post_data=post_data)

    # ---- A6 门禁分写回 + A5 基线可信度 ----
    level = _write_quality_level(conn, mid, dq["score"], attr)

    # ---- A4 生成 8 章节复盘报告 ----
    row = _load_review_row(conn, mid)
    report = build_report(row, pred, fields, attr, dq, post_data=post_data)
    mdate = str(row["match_date"] or mid[:10]).replace("-", "")
    out_dir = PROJECT_DIR / "docs" / "post_match" / mdate
    out_dir.mkdir(parents=True, exist_ok=True)
    fname = f"{row['league'] or '未知'}_{_safe_name(row['home_team'])}_vs_{_safe_name(row['away_team'])}_复盘.md"
    (out_dir / fname).write_text(report, encoding="utf-8")

    res.update({
        "report_file": fname,
        "out_dir": out_dir,
        "review": row,
        "fields": fields,
        "attribution": attr,
        "dq": dq,
        "confidence_level": level,
    })
    gate = "🚨<0.7" if dq["alert"] else "✅"
    logger.info(f"[{mid}] 复盘完成 {review['actual_score']} {review['actual_wdl']} | "
                f"归因={attr.get('primary_cause') if attr else '—'} | "
                f"基线{level}级 | 质量分{dq['score']} {gate}")
    return res


def _rebuild_dir_summary(conn: sqlite3.Connection, out_dir: Path) -> None:
    """按目录日期从 post_match_review 全量重建 _summary.md。

    修复单场模式（--match-id）的既有缺陷：原逻辑用「本次处理场次」覆盖整个
    _summary.md，会挤掉同日期目录内其他已存在的复盘。
    """
    iso_date = f"{out_dir.name[:4]}-{out_dir.name[4:6]}-{out_dir.name[6:]}"
    rows = conn.execute(
        "SELECT * FROM post_match_review WHERE match_date=? ORDER BY match_id",
        (iso_date,)).fetchall()
    summaries: List[Dict[str, Any]] = []
    for row in rows:
        mid = row["match_id"]
        pred = load_predictions(conn, mid)
        fields = compute_review_fields(conn, row, pred)
        attr = None
        if row["attribution_json"]:
            try:
                attr = json.loads(row["attribution_json"])
            except json.JSONDecodeError:
                attr = None
        score = row["data_quality_score"] if row["data_quality_score"] is not None else 1.0
        dq = {"score": score, "alert": float(score) < 0.7, "issues": []}
        fname = (f"{row['league'] or '未知'}_{_safe_name(row['home_team'])}_vs_"
                 f"{_safe_name(row['away_team'])}_复盘.md")
        summaries.append({
            "review": row, "fields": fields, "attribution": attr,
            "dq": dq, "report_file": fname,
        })
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "_summary.md").write_text(
        build_summary(summaries, out_dir), encoding="utf-8")


# ==================== 主流程 ====================
def run_pipeline(
    mode: str,
    match_date: Optional[str],
    match_id_filter: Optional[str],
    dry_run: bool,
    limit: int,
    catchup_days: int,
    logger: logging.Logger,
) -> None:
    conn = _get_conn()
    try:
        ensure_post_match_schema(conn)

        if mode == "catch-up":
            today = date.today().isoformat()
            since = (date.today() - timedelta(days=catchup_days)).isoformat()
            targets = collect_catchup_targets(conn, since, today, limit)
        else:
            targets = collect_target_matches(conn, match_date, match_id_filter)
        if not targets:
            logger.info("未找到目标场次（无匹配的 model_predictions）")
            return

        stats: Dict[str, int] = {"total": 0, "new": 0, "existing": 0,
                                 "candidate": 0, "not_finished": 0, "errors": 0}
        summaries_by_dir: Dict[Path, List[Dict[str, Any]]] = {}
        client = SofaScoreClient(logger=logger)
        try:
            for t in targets:
                stats["total"] += 1
                try:
                    res = process_one(conn, client, t, dry_run, logger)
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{t['match_id']}] 处理异常: {e}")
                    stats["errors"] += 1
                    continue
                key = res.get("status")
                if key in stats:
                    stats[key] += 1
                if not dry_run and res.get("out_dir") is not None:
                    summaries_by_dir.setdefault(res["out_dir"], []).append(res)
        finally:
            client.close()

        if not dry_run:
            for out_dir in summaries_by_dir:
                _rebuild_dir_summary(conn, out_dir)
    finally:
        conn.close()

    # C-20260922-050 修复扫描滞后：复盘链路结束后自动触发 anomaly_samples 扫描
    # 确保近 7 天的赛果立即入库 anomaly_samples.db（包含 matches 表 fallback 扫描）
    if not dry_run:
        try:
            sys.path.insert(0, str(PROJECT_DIR / "scripts"))
            from anomaly_sample_manager import scan_and_import_anomalies  # noqa: E402
            scan_result = scan_and_import_anomalies(days_back=7, verbose=False)
            logger.info(
                f"[ANOMALY-AUTO-SCAN] 触发完成: 录入 {scan_result.get('imported', 0)} 条, "
                f"跳过无预测 {scan_result.get('skipped_no_pred', 0)} 条, "
                f"跳过无异常 {scan_result.get('skipped_no_anomaly', 0)} 条"
            )
        except Exception as _e:
            logger.warning(f"[ANOMALY-AUTO-SCAN] 触发失败（不影响复盘主流程）: {_e}")

    # C-20260924-068 修复 B3 知识库自动迭代无调度：复盘链路结束后自动触发 knowledge_iteration 扫描
    # 生成 docs/knowledge_iteration 报告 + 写 L3 sample_weights pending 建议（幂等，需人工 --approve）
    # 复用上方 anomaly 段已执行的 sys.path.insert(scripts)，同目录懒 import，异常全隔离不影响主流程。
    if not dry_run:
        try:
            from knowledge_iteration import scan as kb_scan  # noqa: E402
            kb_result = kb_scan()
            logger.info(
                f"[B3-AUTO-SCAN] 触发完成: 复盘 {kb_result.get('n_total', 0)} 场, "
                f"触发式 {kb_result.get('triggers', 0)} 组, 报告 {kb_result.get('report', '')}"
            )
        except Exception as _e:
            logger.warning(f"[B3-AUTO-SCAN] 触发失败（不影响复盘主流程）: {_e}")

    print("=" * 70)
    print(f"复盘闭环汇总（{'dry-run 不写库' if dry_run else '已写库'}）")
    print(f"  目标场次      : {stats['total']}")
    if dry_run:
        print(f"  待处理候选    : {stats['candidate']}")
    else:
        print(f"  新写复盘      : {stats['new']}")
        print(f"  已存在回填    : {stats['existing']}")
    print(f"  未结束/无赛果 : {stats['not_finished']}")
    if stats["errors"]:
        print(f"  处理异常      : {stats['errors']}")
    print("=" * 70)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="复盘闭环全链路自动化（A2 采集 → A3 归因 → A6 门禁 → A5 分级 → A4 报告）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""示例:
  # 调度模式：扫近期已完赛未复盘场次（建议 Windows 计划任务每小时一次）
  python scripts/run_post_match_pipeline.py --catch-up --limit 30

  # 指定日期批量
  python scripts/run_post_match_pipeline.py --date 2026-09-06

  # 单场指定
  python scripts/run_post_match_pipeline.py --match-id "2026-08-22_Arsenal_Coventry City"

  # 只扫不写（验证目标场次与全链路输出）
  python scripts/run_post_match_pipeline.py --catch-up --limit 3 --dry-run
        """,
    )
    parser.add_argument("--date", type=str, default=None, metavar="YYYY-MM-DD",
                        help="批量处理日期（扫当日 model_predictions match_id 前缀）")
    parser.add_argument("--match-id", type=str, default=None, metavar="MATCH_ID",
                        help="单场处理（优先级最高）")
    parser.add_argument("--catch-up", action="store_true",
                        help="调度模式：扫近期已完赛且已预测但无复盘的场次")
    parser.add_argument("--catchup-days", type=int, default=DEFAULT_CATCHUP_DAYS,
                        help="--catch-up 回溯天数（默认 7）")
    parser.add_argument("--limit", type=int, default=DEFAULT_LIMIT,
                        help="--catch-up 单次最多处理场次（默认 30）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只扫+采集+计算，不写库不写文件")
    args = parser.parse_args()

    if args.match_id:
        mode, match_date, match_id_filter = "single", None, args.match_id
    elif args.date:
        try:
            datetime.strptime(args.date, "%Y-%m-%d")
        except ValueError:
            parser.error(f"--date 格式应为 YYYY-MM-DD，收到: {args.date}")
        mode, match_date, match_id_filter = "date", args.date, None
    elif args.catch_up:
        mode, match_date, match_id_filter = "catch-up", None, None
    else:
        parser.error("必须提供 --date / --match-id / --catch-up 之一")

    logger = setup_logging("post-match-pipeline")
    run_pipeline(mode, match_date, match_id_filter, args.dry_run,
                 args.limit, args.catchup_days, logger)


if __name__ == "__main__":
    main()
