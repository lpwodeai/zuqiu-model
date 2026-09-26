"""TG λ_total 滚动缩放校准器（C-20260921-036）

从赛前历史配对样本（model_predictions Lambda 行 + post_match_review actual_tg）
计算 λ_total 系统性高估比例，输出缩放因子用于修正 Poisson/DC 输入。

校准因子公式：
    factor = clamp(mean(actual_tg) / mean(λ_home + λ_away), 0.7, 1.0)
    样本不足 → factor=1.0（不校准）

严格时间切片：sample.match_date < target.match_date（禁止未来泄露）
赛季隔离：跨赛季样本不参与（derive_season 严格匹配）
"""
from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"

DEFAULT_CFG: Dict[str, Any] = {
    "window_days": 90,
    "min_samples": 30,
    "factor_min": 0.7,
    "factor_max": 1.0,      # P0 保守：只允许下调
    "same_season_only": True,
    "fallback_factor": 1.0,
    # C-20260921-036: 联赛样本不足（赛季初冷启动）时回退到五联赛全局池化因子，
    # 仍不足再返回 fallback_factor。
    "pooled_fallback": True,
}

logger = logging.getLogger("tg_calib")


def _derive_season(match_date: str) -> str:
    """根据比赛日期推导赛季（8 月起为新赛季，横跨两个自然年）。
    对齐 prediction_db_writer.derive_season。
    """
    y = int(match_date[:4])
    m = int(match_date[5:7])
    return f"{y}-{y + 1}" if m >= 8 else f"{y - 1}-{y}"


def is_valid_tg_sample(row: sqlite3.Row) -> bool:
    """数据守卫：过滤 0.0 占位行 / 缺失行。

    判定：actual_tg IS NOT NULL AND actual_tg >= 0
          AND Lambda_home > 0 AND Lambda_away > 0
          AND match_date IS NOT NULL
    """
    try:
        lh = float(row["lambda_home"]) if row["lambda_home"] else 0.0
        la = float(row["lambda_away"]) if row["lambda_away"] else 0.0
        atg = int(row["actual_tg"]) if row["actual_tg"] is not None else -1
        md = row["match_date"] or ""
        return lh > 0 and la > 0 and atg >= 0 and len(md) >= 10
    except (TypeError, ValueError):
        return False


def load_calib_samples(
    conn: sqlite3.Connection,
    target_match_date: str,
    league: Optional[str] = None,
    cfg: Dict[str, Any] = DEFAULT_CFG,
) -> Tuple[list, int]:
    """从 model_predictions JOIN post_match_review 取同期同赛季样本。

    严格时间切片：< target_match_date, derive_season 相等。
    返回 (valid_samples, n_filtered_out)。
    """
    window_days = cfg.get("window_days", 90)
    same_season = cfg.get("same_season_only", True)

    target_dt = datetime.strptime(target_match_date[:10], "%Y-%m-%d")
    cutoff_start = (target_dt - timedelta(days=window_days)).strftime("%Y-%m-%d")

    season_cond = ""
    params: list = [cutoff_start, target_match_date[:10]]
    if same_season:
        target_season = _derive_season(target_match_date)
        # derive_season 在 SQL 中无法直接调用，取两个半年范围
        y = int(target_match_date[:4])
        m = int(target_match_date[5:7])
        if m >= 8:
            season_start = f"{y}-08-01"
            season_end = f"{y + 1}-07-31"
        else:
            season_start = f"{y - 1}-08-01"
            season_end = f"{y}-07-31"
        season_cond = "AND date(pmr.match_date) >= date(?) AND date(pmr.match_date) <= date(?)"
        params = [season_start, season_end, cutoff_start, target_match_date[:10]]

    league_cond = ""
    if league:
        league_cond = "AND pmr.league = ?"
        params.append(league)

    # 取 Lambda_home + Lambda_away + actual_tg 配对
    sql = f"""
        SELECT lh.match_id, pmr.match_date, pmr.league,
               CAST(lh.prediction AS REAL) AS lambda_home,
               CAST(la.prediction AS REAL) AS lambda_away,
               pmr.actual_tg
        FROM model_predictions lh
        JOIN model_predictions la
          ON lh.match_id = la.match_id
         AND lh.model_name = la.model_name
         AND la.prediction_type = 'Lambda_away'
        JOIN post_match_review pmr ON pmr.match_id = lh.match_id
        WHERE lh.prediction_type = 'Lambda_home'
          AND lh.model_name = 'generate_unified_report_v2.0'
          AND pmr.actual_tg IS NOT NULL
          {season_cond}
          {league_cond}
          AND date(pmr.match_date) >= date(?)
          AND date(pmr.match_date) < date(?)
        ORDER BY pmr.match_date
    """
    # 重新组织 params（season_cond 已插入了 season_start/season_end 和 cutoff_start/target）
    # SQL 中参数顺序：season_start, season_end (if season_cond), cutoff_start, target, league (if league_cond)
    # 但由于 SQL 模板顺序问题，需要重新对齐
    # 简化：直接构建完整 params
    params_final: list = []
    sql_parts = []
    if same_season:
        sql_parts.append("AND date(pmr.match_date) >= date(?) AND date(pmr.match_date) <= date(?)")
        params_final.extend([season_start, season_end])
    sql_parts.append("AND date(pmr.match_date) >= date(?)")
    params_final.append(cutoff_start)
    sql_parts.append("AND date(pmr.match_date) < date(?)")
    params_final.append(target_match_date[:10])
    if league:
        sql_parts.append("AND pmr.league = ?")
        params_final.append(league)

    extra_cond = " ".join(sql_parts)
    sql = f"""
        SELECT lh.match_id, pmr.match_date, pmr.league,
               CAST(lh.prediction AS REAL) AS lambda_home,
               CAST(la.prediction AS REAL) AS lambda_away,
               pmr.actual_tg
        FROM model_predictions lh
        JOIN model_predictions la
          ON lh.match_id = la.match_id
         AND lh.model_name = la.model_name
         AND la.prediction_type = 'Lambda_away'
        JOIN post_match_review pmr ON pmr.match_id = lh.match_id
        WHERE lh.prediction_type = 'Lambda_home'
          AND lh.model_name = 'generate_unified_report_v2.0'
          AND pmr.actual_tg IS NOT NULL
          {extra_cond}
        ORDER BY pmr.match_date
    """

    rows = conn.execute(sql, params_final).fetchall()
    valid = [r for r in rows if is_valid_tg_sample(r)]
    n_filtered = len(rows) - len(valid)
    return valid, n_filtered


def compute_factor(
    samples: list,
    cfg: Dict[str, Any] = DEFAULT_CFG,
) -> Tuple[float, Dict[str, Any]]:
    """计算校准因子。

    返回 (factor, trace)。
    factor = clamp(mean(actual_tg) / mean(λ_total), factor_min, factor_max)
    样本不足 → fallback_factor
    """
    min_samples = cfg.get("min_samples", 30)
    factor_min = cfg.get("factor_min", 0.7)
    factor_max = cfg.get("factor_max", 1.0)
    fallback = cfg.get("fallback_factor", 1.0)

    n = len(samples)
    if n < min_samples:
        return fallback, {
            "n": n,
            "n_filtered_out": 0,
            "mean_lambda_total": None,
            "mean_actual_tg": None,
            "raw_factor": None,
            "clamped_factor": fallback,
            "fallback_reason": f"insufficient_samples ({n} < {min_samples})",
        }

    lt_values = [float(r["lambda_home"]) + float(r["lambda_away"]) for r in samples]
    tg_values = [int(r["actual_tg"]) for r in samples]

    mean_lt = sum(lt_values) / len(lt_values)
    mean_tg = sum(tg_values) / len(tg_values)

    if mean_lt <= 0:
        return fallback, {
            "n": n,
            "n_filtered_out": 0,
            "mean_lambda_total": mean_lt,
            "mean_actual_tg": mean_tg,
            "raw_factor": None,
            "clamped_factor": fallback,
            "fallback_reason": "mean_lambda_total <= 0",
        }

    raw_factor = mean_tg / mean_lt
    clamped = max(factor_min, min(factor_max, raw_factor))

    return clamped, {
        "n": n,
        "n_filtered_out": 0,
        "mean_lambda_total": round(mean_lt, 4),
        "mean_actual_tg": round(mean_tg, 4),
        "raw_factor": round(raw_factor, 4),
        "clamped_factor": round(clamped, 4),
        "fallback_reason": None,
    }


def get_calib_factor(
    match_date: str,
    league: Optional[str] = None,
    cfg: Dict[str, Any] = DEFAULT_CFG,
    db_path: Path = ODDS_DB,
) -> Tuple[float, Dict[str, Any]]:
    """主入口：返回 (factor, trace)。

    生产路径与 shadow 路径都通过此函数取因子；上层决定是否使用。
    异常 → fallback_factor=1.0，trace.fallback_reason='exception: ...'
    """
    try:
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        try:
            samples, n_filtered = load_calib_samples(conn, match_date, league, cfg)
            # 把 n_filtered 加进 trace
            factor, trace = compute_factor(samples, cfg)
            trace["n_filtered_out"] = n_filtered
            return factor, trace
        finally:
            conn.close()
    except Exception as e:
        logger.warning(f"get_calib_factor 异常: {e}", exc_info=True)
        return cfg.get("fallback_factor", 1.0), {
            "n": 0,
            "n_filtered_out": 0,
            "mean_lambda_total": None,
            "mean_actual_tg": None,
            "raw_factor": None,
            "clamped_factor": cfg.get("fallback_factor", 1.0),
            "fallback_reason": f"exception: {type(e).__name__}: {e}",
        }


def apply_factor(
    lambda_home: float,
    lambda_away: float,
    factor: float,
) -> Tuple[float, float]:
    """对 λ 同比例缩放，保持主客相对强度不变。"""
    return lambda_home * factor, lambda_away * factor


def get_calib_factor_hierarchical(
    match_date: str,
    league: Optional[str] = None,
    cfg: Dict[str, Any] = DEFAULT_CFG,
    db_path: Path = ODDS_DB,
    conn: Optional[sqlite3.Connection] = None,
) -> Tuple[float, Dict[str, Any]]:
    """分层校准因子主入口（C-20260921-036）。

    优先级：
      1. 同联赛样本数 ≥ min_samples → 使用联赛因子（source='league'）
      2. cfg.pooled_fallback=True 且全局样本数 ≥ min_samples → 全局池化因子（source='pooled'）
      3. 均不足 / 异常 → fallback_factor（source='fallback'）

    解决赛季初冷启动：单联赛每周约 5~10 场，90 天窗口内攒满 30 场需要
    6~8 周；五联赛池化后第 2~3 周即可激活。市场大球系统性偏差主要来自
    TG 玩法高抽水，跨联赛同方向，池化因子在 P0 阶段可接受。

    Args:
        conn: 可选复用连接（离线批量回放每场均调用，避免反复开关 SQLite）。
    """
    own_conn = conn is None
    try:
        if own_conn:
            conn = sqlite3.connect(str(db_path))
            conn.row_factory = sqlite3.Row

        # 1) 联赛层
        lg_samples, lg_filtered = load_calib_samples(conn, match_date, league, cfg)
        lg_factor, lg_trace = compute_factor(lg_samples, cfg)
        lg_trace["n_filtered_out"] = lg_filtered
        if league and len(lg_samples) >= cfg.get("min_samples", 30):
            lg_trace["source"] = "league"
            lg_trace["league"] = league
            lg_trace["pooled"] = None
            return lg_factor, lg_trace

        # 2) 全局池化层
        pooled_trace = None
        if cfg.get("pooled_fallback", True):
            pl_samples, pl_filtered = load_calib_samples(conn, match_date, None, cfg)
            pl_factor, pl_trace = compute_factor(pl_samples, cfg)
            pl_trace["n_filtered_out"] = pl_filtered
            if len(pl_samples) >= cfg.get("min_samples", 30):
                pl_trace["source"] = "pooled"
                pl_trace["league"] = league
                pl_trace["league_layer"] = {
                    "n": lg_trace.get("n"),
                    "raw_factor": lg_trace.get("raw_factor"),
                    "clamped_factor": lg_trace.get("clamped_factor"),
                }
                return pl_factor, pl_trace
            pooled_trace = pl_trace

        # 3) 均不足 → fallback
        fb = cfg.get("fallback_factor", 1.0)
        lg_trace["source"] = "fallback"
        lg_trace["league"] = league
        lg_trace["pooled"] = pooled_trace
        return fb, lg_trace
    except Exception as e:
        logger.warning(f"get_calib_factor_hierarchical 异常: {e}", exc_info=True)
        return cfg.get("fallback_factor", 1.0), {
            "n": 0,
            "n_filtered_out": 0,
            "mean_lambda_total": None,
            "mean_actual_tg": None,
            "raw_factor": None,
            "clamped_factor": cfg.get("fallback_factor", 1.0),
            "fallback_reason": f"exception: {type(e).__name__}: {e}",
            "source": "fallback",
            "league": league,
        }
    finally:
        if own_conn and conn is not None:
            conn.close()
