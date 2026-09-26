# -*- coding: utf-8 -*-
"""异常样本库管理器（C-20260918-030 改造）

职责：
1. 维护 anomaly_samples 表（UNIQUE(match_id)，中文格式 match_id）
2. 自动扫描 post_match_review + model_predictions + wdl_history，识别异常场次并录入
3. 提供 CRUD/统计接口供训练侧 (train_models.py) 调用

关键设计（C-030）：
- match_id 统一中文格式 f"{match_date}_{home_cn}_{away_cn}"，与 five_leagues.db / matches 表一致
- 自动录入源：post_match_review (odds.db)，对比 model_predictions 的 WDL_* 与 wdl_history 的赔率
- created_at 字段供训练时 6 个月时间窗过滤（防老化）
- 加权去重：与 L3/B4 加权取 max（在 train_models.py 实现）
"""
import sqlite3
import json
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = BASE_DIR / "data" / "anomaly_samples.db"
ODDS_DB_PATH = BASE_DIR / "data" / "odds.db"

# 异常类型枚举（修复 C-030 前 '三重重错误' typo）
ANOMALY_TYPES = ['ML错误', '赔率错误', '融合错误', '爆冷', '双重错误', '三重错误']

# 爆冷阈值：actual_wdl 对应方向的赔率 ≥ 此值视为冷门
# C-20260922-051：5.0 → 4.0（5.0 过保守，近 30 天仅 1 场触发；4.0 扩大召回覆盖更多冷门场次）
UPSET_ODDS_THRESHOLD = 4.0

# WDL 中文标签映射（post_match_review.actual_wdl 与 model_predictions.prediction 的语义对齐）
WDL_HOME_LABEL = '主胜'
WDL_DRAW_LABEL = '平局'
WDL_AWAY_LABEL = '客胜'


def init_anomaly_database():
    """幂等建表。已存在表会保留数据，仅补齐缺失列。"""
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS anomaly_samples (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            match_id TEXT UNIQUE,
            home_team TEXT,
            away_team TEXT,
            match_date TEXT,
            actual_wdl TEXT,
            actual_score TEXT,
            ml_prediction TEXT,
            ml_confidence REAL,
            ml_probabilities TEXT,
            odds_prediction TEXT,
            odds_confidence REAL,
            fusion_prediction TEXT,
            anomaly_type TEXT,
            confidence_diff REAL,
            notes TEXT,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now','localtime')),
            updated_at TEXT DEFAULT (datetime('now','localtime'))
        )
    """)

    # 兼容旧表：补齐 source 列（C-030 新增）
    try:
        cursor.execute("ALTER TABLE anomaly_samples ADD COLUMN source TEXT DEFAULT 'manual'")
    except sqlite3.OperationalError:
        pass  # 列已存在

    # C-20260922-050 新增：爆冷标志 + 冷门风险评分
    # C-20260922-052 新增：预测时间门禁（防赛后回填预测污染 urs 与标签）
    # C-20260922-053 新增：ml_is_replay（1=赛后回放补算，时间锚定但模型为当前版本）
    for col_def in [
        "is_upset INTEGER DEFAULT 0",
        "upset_odds REAL",
        "upset_risk_score REAL",
        "ml_pred_ts TEXT",
        "pred_ts_valid INTEGER DEFAULT 1",
        "ml_is_replay INTEGER DEFAULT 0",
    ]:
        col_name = col_def.split()[0]
        try:
            cursor.execute(f"ALTER TABLE anomaly_samples ADD COLUMN {col_def}")
        except sqlite3.OperationalError:
            pass  # 列已存在

    # 修正历史 typo：'三重重错误' → '三重错误'
    cursor.execute("UPDATE anomaly_samples SET anomaly_type='三重错误' WHERE anomaly_type='三重重错误'")

    conn.commit()
    conn.close()
    print("异常样本库表结构初始化完成（含 source 列 + typo 修正）")


def _normalize_match_id(match_date, home_cn, away_cn):
    """构造中文格式 match_id：f"{date}_{home}_{away}"。

    与 five_leagues.db matches.match_id / wdl_history.match_id 一致，
    使 train_models.py 的 df['match_id'] 可直接匹配。
    """
    if not match_date or not home_cn or not away_cn:
        return None
    return f"{match_date}_{home_cn}_{away_cn}"


def _argmax_wdl(probs_dict):
    """从 {home: p, draw: p, away: p} 概率字典取 argmax 方向。返回中文标签或 None。"""
    if not probs_dict:
        return None
    items = [(k, v) for k, v in probs_dict.items() if v is not None]
    if not items:
        return None
    best = max(items, key=lambda x: x[1])[0]
    return best


def _argmin_odds_direction(win_a, draw, win_b):
    """从赔率三元组取 argmin 方向（赔率越低 = 越被看好）。返回中文标签或 None。"""
    odds = {WDL_HOME_LABEL: win_a, WDL_DRAW_LABEL: draw, WDL_AWAY_LABEL: win_b}
    items = [(k, v) for k, v in odds.items() if v is not None and v > 0]
    if not items:
        return None
    return min(items, key=lambda x: x[1])[0]


def _check_pred_ts_valid(ml_pred_ts, match_date):
    """C-20260922-052 预测时间门禁。

    判定 model_predictions 的 WDL 预测行是否为赛前/赛中产生。
    审计发现全库 83 个 match_id 的预测为赛后 1~16 天批量回填（如 09-14 14:19
    批量回写 8/29 场次），这类回填预测用于：
      ① 异常标签判定（ML错误/双重错误）——口径不可与真赛前预测等同
      ② urs 的 odds_divergence 维度——回填 ml_probs 污染 KL 散度
    门禁规则：prediction timestamp 日期 ≤ match_date 视为有效（1），否则 0。
    匹配粒度为日期（match_id 只含日期，无开球时刻），当天视为临场。
    """
    if not ml_pred_ts or not match_date:
        return 1  # 无时间信息时保守放行（旧数据兼容），不阻断扫描
    try:
        ts_date = str(ml_pred_ts)[:10]
        return 1 if ts_date <= str(match_date) else 0
    except Exception:
        return 1


def _classify_anomaly(ml_pred, odds_pred, fusion_pred, actual_wdl, odds_values):
    """根据三方预测与 actual_wdl 识别异常类型。

    返回 (anomaly_type, confidence_diff, notes, is_upset, upset_odds)

    C-20260922-050 修复：爆冷与错误类型**可叠加**而非互斥。
    - 主分类（错误优先级）：三重 > 双重 > 单错 > 融合
    - 独立爆冷标志：actual 方向赔率 ≥ UPSET_ODDS_THRESHOLD，无论是否有错误
    - 当主分类为 None 且 is_upset=True → 主分类设为 '爆冷'（避免爆冷类型恒为 0）
    - 当主分类非 None 且 is_upset=True → 保留主分类，notes 追加 "+爆冷"，is_upset=True
    """
    if actual_wdl not in (WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL):
        return None, None, None, False, None

    ml_err = ml_pred in (WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL) and ml_pred != actual_wdl
    odds_err = odds_pred in (WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL) and odds_pred != actual_wdl
    fusion_err = (fusion_pred in (WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL)
                  and fusion_pred != actual_wdl)

    # 爆冷：actual 方向赔率 ≥ 阈值（独立于错误判定）
    is_upset = False
    upset_odds_val = None
    if odds_values:
        upset_odds_val = odds_values.get(actual_wdl)
        if upset_odds_val is not None and upset_odds_val >= UPSET_ODDS_THRESHOLD:
            is_upset = True

    # 主分类（错误优先级）：三重 > 双重 > 单错 > 融合
    if ml_err and odds_err and fusion_err:
        atype = '三重错误'
    elif ml_err and odds_err:
        atype = '双重错误'
    elif ml_err:
        atype = 'ML错误'
    elif odds_err:
        atype = '赔率错误'
    elif fusion_err:
        atype = '融合错误'
    else:
        atype = None

    # 爆冷可独立触发主分类（修复爆冷类型恒为 0 的设计缺陷）
    if atype is None and is_upset:
        atype = '爆冷'
    elif atype is not None and is_upset:
        # 错误与爆冷叠加：保留主分类，notes 追加 +爆冷标记
        pass

    if atype is None and not is_upset:
        return None, None, None, False, None  # 无异常

    notes_parts = []
    if ml_err:
        notes_parts.append(f"ML预测{ml_pred}≠实际{actual_wdl}")
    if odds_err:
        notes_parts.append(f"赔率预测{odds_pred}≠实际{actual_wdl}")
    if fusion_err:
        notes_parts.append(f"融合预测{fusion_pred}≠实际{actual_wdl}")
    if is_upset:
        notes_parts.append(f"爆冷：实际{actual_wdl}方向赔率{upset_odds_val}")
    notes = '; '.join(notes_parts) if notes_parts else None

    # confidence_diff：ML 错时记录 -ml_confidence（如有）
    conf_diff = None
    return atype, conf_diff, notes, is_upset, upset_odds_val


def scan_and_import_anomalies(league=None, days_back=None, verbose=True):
    """从 post_match_review + model_predictions + wdl_history 自动扫描异常场次并录入。

    数据流：
    1. 从 odds.db.post_match_review 读取 actual_wdl 非空的所有行
    2. 对每场：
       a. 构造中文 match_id = f"{match_date}_{home_team}_{away_team}"
       b. 用英文 match_id 查 model_predictions 的 WDL_home/draw/away 三行（ML 预测概率）
       c. 用中文 match_id 查 wdl_history 的 win_a/draw/win_b（赔率，取最新 timestamp）
       d. 比对 actual_wdl，识别异常类型
       e. INSERT OR REPLACE 到 anomaly_samples（中文 match_id 作主键）

    参数：
    - league: 限定联赛（'英超'/'西甲'/'意甲'/'德甲'/'法甲'），None=全部
    - days_back: 只扫描最近 N 天（默认 None=全部）
    - verbose: 打印进度

    返回：dict {total, imported, by_type}
    """
    init_anomaly_database()

    if not ODDS_DB_PATH.exists():
        msg = f"odds.db 不存在: {ODDS_DB_PATH}"
        print(f"[ANOMALY-SCAN-ERR] {msg}")
        return {'total': 0, 'imported': 0, 'by_type': {}, 'error': msg}

    odds_conn = sqlite3.connect(ODDS_DB_PATH)
    odds_conn.row_factory = sqlite3.Row
    ano_conn = sqlite3.connect(DB_PATH)
    ano_cur = ano_conn.cursor()

    # C-053：model_predictions.is_replay 列存在性（旧库无此列时按 0 处理）
    mp_has_replay = any(
        c['name'] == 'is_replay'
        for c in odds_conn.execute("PRAGMA table_info(model_predictions)").fetchall()
    )

    # 1. 构造 post_match_review 查询
    query = "SELECT match_id, league, match_date, home_team, away_team, actual_wdl, actual_score, actual_hcp FROM post_match_review WHERE actual_wdl IS NOT NULL"
    params = []
    if league:
        query += " AND league = ?"
        params.append(league)
    if days_back:
        cutoff = (datetime.now() - timedelta(days=days_back)).strftime('%Y-%m-%d')
        query += " AND match_date >= ?"
        params.append(cutoff)
    query += " ORDER BY match_date DESC"

    rows = odds_conn.execute(query, params).fetchall()
    if verbose:
        print(f"[ANOMALY-SCAN] 待扫描场次: {len(rows)} 条 (league={league or 'all'}, days_back={days_back or 'all'})")

    # 2. 清理旧英文格式样本（C-030 一次性迁移）
    ano_cur.execute("DELETE FROM anomaly_samples WHERE match_id GLOB '*[a-zA-Z][a-zA-Z]*'")
    deleted_old = ano_cur.rowcount
    if deleted_old and verbose:
        print(f"[ANOMALY-SCAN] 清理旧英文格式样本: {deleted_old} 条")

    # 3. 逐场识别异常
    imported = 0
    by_type = {}
    skipped_no_pred = 0
    skipped_no_anomaly = 0

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for r in rows:
        en_match_id = r['match_id']
        match_date = r['match_date']
        home_cn = r['home_team']
        away_cn = r['away_team']
        actual_wdl = r['actual_wdl']
        actual_score = r['actual_score']
        actual_hcp = r['actual_hcp']

        cn_match_id = _normalize_match_id(match_date, home_cn, away_cn)
        if not cn_match_id:
            continue

        # 2a. 从 model_predictions 查 ML 预测（WDL_home/draw/away 三行）
        # C-052：取**最早批次**预测（同场可能既有真赛前预测又有赛后回填，
        # 最早批次才是真实赛前口径），并记录其时间戳做门禁
        # C-053：同批次读取 is_replay（回放补算标记）
        odds_conn2 = odds_conn
        mp_ts_row = odds_conn2.execute(
            f"SELECT MIN(timestamp) AS min_ts FROM model_predictions "
            f"WHERE match_id = ? AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')",
            (en_match_id,)
        ).fetchone()
        ml_pred_ts = mp_ts_row['min_ts'] if mp_ts_row else None

        mp_rows = []
        ml_is_replay = 0
        if ml_pred_ts:
            mp_rows = odds_conn2.execute(
                f"SELECT prediction_type, prediction, probability"
                f"{', is_replay' if mp_has_replay else ''} FROM model_predictions "
                f"WHERE match_id = ? AND prediction_type IN ('WDL_home','WDL_draw','WDL_away') "
                f"AND timestamp = ?",
                (en_match_id, ml_pred_ts)
            ).fetchall()
            if mp_has_replay and mp_rows:
                # 同批三行同标志；MIN 语义（任一行为真赛前即按真赛前）
                ml_is_replay = min(int(r['is_replay'] or 0) for r in mp_rows)

        ml_probs = {}
        for mpr in mp_rows:
            if mpr['prediction_type'] == 'WDL_home':
                ml_probs[WDL_HOME_LABEL] = mpr['probability']
            elif mpr['prediction_type'] == 'WDL_draw':
                ml_probs[WDL_DRAW_LABEL] = mpr['probability']
            elif mpr['prediction_type'] == 'WDL_away':
                ml_probs[WDL_AWAY_LABEL] = mpr['probability']

        ml_pred = _argmax_wdl(ml_probs)
        ml_conf = ml_probs.get(ml_pred) if ml_pred else None

        # C-052 预测时间门禁：赛后回填的预测不用于 urs 的赔率背离维度
        pred_ts_valid = _check_pred_ts_valid(ml_pred_ts, match_date)

        if not ml_pred:
            # 没有 ML 预测数据的场次跳过（无法判定异常）
            skipped_no_pred += 1
            continue

        # 2b. 从 wdl_history 查赔率（中文 match_id 直匹，失败退化到 match_id_en）
        wh_row = odds_conn2.execute(
            "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id = ? "
            "ORDER BY timestamp DESC LIMIT 1",
            (cn_match_id,)
        ).fetchone()

        if not wh_row:
            # 退化用英文 match_id
            wh_row = odds_conn2.execute(
                "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id_en = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (en_match_id,)
            ).fetchone()

        odds_values = None
        odds_pred = None
        odds_conf = None
        if wh_row and wh_row['win_a'] and wh_row['draw'] and wh_row['win_b']:
            odds_values = {
                WDL_HOME_LABEL: wh_row['win_a'],
                WDL_DRAW_LABEL: wh_row['draw'],
                WDL_AWAY_LABEL: wh_row['win_b'],
            }
            odds_pred = _argmin_odds_direction(wh_row['win_a'], wh_row['draw'], wh_row['win_b'])
            # 赔率置信度：用最低赔率的倒数（越高越被看好）
            min_odds = min(wh_row['win_a'], wh_row['draw'], wh_row['win_b'])
            odds_conf = 1.0 / min_odds if min_odds > 0 else None

        # 2c. 融合预测：post_match_review.pred_wdl 字段（当前多数为 NULL，无法识别融合错误）
        pmr_pred = odds_conn2.execute(
            "SELECT pred_wdl, pred_wdl_probs FROM post_match_review WHERE match_id = ?",
            (en_match_id,)
        ).fetchone()
        fusion_pred = None
        if pmr_pred and pmr_pred['pred_wdl']:
            # pred_wdl 可能是 '主胜/平局/客胜' 或 '胜/平/负'，归一
            w = pmr_pred['pred_wdl']
            if '主' in w or w == '胜':
                fusion_pred = WDL_HOME_LABEL
            elif w == '平' or '平' in w:
                fusion_pred = WDL_DRAW_LABEL
            elif '客' in w:
                fusion_pred = WDL_AWAY_LABEL

        # 2d. 识别异常类型（含独立爆冷标志，C-050）
        atype, conf_diff, notes, is_upset, upset_odds_val = _classify_anomaly(
            ml_pred, odds_pred, fusion_pred, actual_wdl, odds_values
        )

        if not atype:
            skipped_no_anomaly += 1
            continue

        # 2e. 计算赛前冷门风险评分（C-050 新增，C-052 时间门禁，C-053 回放门禁）
        # 赛后回填（pred_ts_valid=0）或赛后回放补算（ml_is_replay=1，时间已锚定但
        # 用的是当前版本模型）的 ml_probs 均不传入，urs 仅基于赔率维，避免污染
        # odds_divergence；ML 错误标签判定仍正常使用 ml_probs
        upset_risk_score_val = None
        try:
            from upset_factor_engine import compute_upset_risk_score
            home_odds_v = odds_values.get(WDL_HOME_LABEL) if odds_values else None
            draw_odds_v = odds_values.get(WDL_DRAW_LABEL) if odds_values else None
            away_odds_v = odds_values.get(WDL_AWAY_LABEL) if odds_values else None
            # C-055: 从 en_match_id 提取英文队名，自动查询 Elo 差距与积分榜战意
            _id_parts = en_match_id.split("_")
            _home_en = _id_parts[1] if len(_id_parts) >= 3 else home_cn
            _away_en = _id_parts[2] if len(_id_parts) >= 3 else away_cn
            score_result = compute_upset_risk_score(
                home_odds=home_odds_v, draw_odds=draw_odds_v, away_odds=away_odds_v,
                ml_probs=ml_probs if (ml_probs and pred_ts_valid and not ml_is_replay) else None,
                home_team=_home_en, away_team=_away_en, match_date=match_date,
            )
            upset_risk_score_val = score_result.get('upset_risk_score')
        except Exception as _e:
            if verbose:
                print(f"[ANOMALY-SCAN-WARN] upset_risk_score 计算失败 {cn_match_id}: {_e}")

        if not pred_ts_valid:
            notes = (notes + "; " if notes else "") + \
                f"⚠️赛后回填预测(ts={ml_pred_ts})，urs已排除ml维"
        elif ml_is_replay:
            notes = (notes + "; " if notes else "") + \
                f"♻️回放补算预测(锚点ts={ml_pred_ts},当前模型回放),urs已排除ml维"

        # 2f. INSERT（C-052 改 ON CONFLICT DO UPDATE：重扫保留首扫 created_at，
        # 仅刷新数据列与 updated_at，修复 6 个月老化窗口被重扫重置的语义缺陷）
        try:
            ano_cur.execute("""
                INSERT INTO anomaly_samples (
                    match_id, home_team, away_team, match_date,
                    actual_wdl, actual_score,
                    ml_prediction, ml_confidence, ml_probabilities,
                    odds_prediction, odds_confidence,
                    fusion_prediction,
                    anomaly_type, confidence_diff, notes,
                    is_upset, upset_odds, upset_risk_score,
                    ml_pred_ts, pred_ts_valid, ml_is_replay,
                    source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(match_id) DO UPDATE SET
                    home_team=excluded.home_team,
                    away_team=excluded.away_team,
                    actual_wdl=excluded.actual_wdl,
                    actual_score=excluded.actual_score,
                    ml_prediction=excluded.ml_prediction,
                    ml_confidence=excluded.ml_confidence,
                    ml_probabilities=excluded.ml_probabilities,
                    odds_prediction=excluded.odds_prediction,
                    odds_confidence=excluded.odds_confidence,
                    fusion_prediction=excluded.fusion_prediction,
                    anomaly_type=excluded.anomaly_type,
                    confidence_diff=excluded.confidence_diff,
                    notes=excluded.notes,
                    is_upset=excluded.is_upset,
                    upset_odds=excluded.upset_odds,
                    upset_risk_score=excluded.upset_risk_score,
                    ml_pred_ts=excluded.ml_pred_ts,
                    pred_ts_valid=excluded.pred_ts_valid,
                    ml_is_replay=excluded.ml_is_replay,
                    source=excluded.source,
                    updated_at=excluded.updated_at
            """, (
                cn_match_id, home_cn, away_cn, match_date,
                actual_wdl, actual_score,
                ml_pred, ml_conf,
                json.dumps(ml_probs, ensure_ascii=False) if ml_probs else None,
                odds_pred, odds_conf,
                fusion_pred,
                atype, conf_diff, notes,
                1 if is_upset else 0, upset_odds_val, upset_risk_score_val,
                ml_pred_ts, pred_ts_valid, ml_is_replay,
                'auto_scan', now_str, now_str
            ))
            imported += 1
            by_type[atype] = by_type.get(atype, 0) + 1
            if is_upset:
                by_type['_with_upset'] = by_type.get('_with_upset', 0) + 1
        except Exception as e:
            if verbose:
                print(f"[ANOMALY-SCAN-WARN] 写入失败 {cn_match_id}: {e}")

    # 3. matches 表 fallback 扫描（C-20260922-050 修复扫描滞后）：
    # 找出 matches 表有 actual_wdl 但 post_match_review 未回填的近期场次
    # 解决近 1-3 天比赛因 post_match_review 未回填而漏扫的问题
    try:
        mb_query = (
            "SELECT match_id, league, match_date, home_team, away_team, actual_wdl, actual_score "
            "FROM matches "
            "WHERE actual_wdl IS NOT NULL AND actual_wdl != '' "
            "AND match_id NOT IN (SELECT match_id FROM post_match_review WHERE actual_wdl IS NOT NULL)"
        )
        mb_params = []
        if league:
            mb_query += " AND league = ?"
            mb_params.append(league)
        if days_back:
            mb_query += " AND match_date >= ?"
            mb_params.append(cutoff)
        mb_query += " ORDER BY match_date DESC"

        mb_rows = odds_conn.execute(mb_query, mb_params).fetchall()
        if mb_rows and verbose:
            print(f"[ANOMALY-SCAN] matches 表 fallback 待扫描: {len(mb_rows)} 条（post_match_review 未覆盖）")

        # 加载英文→中文队名映射
        sofa_cn_map = {}
        try:
            import sys as _sys
            _feat_dir = BASE_DIR / "features"
            if str(_feat_dir) not in _sys.path:
                _sys.path.insert(0, str(_feat_dir))
            from sofascore_pre_match_features import SOFA_TEAM_CN_MAP  # type: ignore
            sofa_cn_map = SOFA_TEAM_CN_MAP or {}
        except Exception:
            pass

        for r in mb_rows:
            en_match_id = r['match_id']
            match_date_mb = r['match_date']
            home_en = r['home_team']
            away_en = r['away_team']
            actual_wdl_mb = r['actual_wdl']
            actual_score_mb = r['actual_score']

            # 归一化 actual_wdl（matches 表混合 '胜/平/负' 与 '主胜/平局/客胜'）
            wdl_norm = {'胜': '主胜', '平': '平局', '负': '客胜',
                        '主胜': '主胜', '平局': '平局', '客胜': '客胜'}
            actual_wdl_mb = wdl_norm.get(actual_wdl_mb, actual_wdl_mb)

            # 英文→中文队名转换
            home_cn_mb = sofa_cn_map.get(home_en) or home_en
            away_cn_mb = sofa_cn_map.get(away_en) or away_en

            cn_match_id_mb = _normalize_match_id(match_date_mb, home_cn_mb, away_cn_mb)
            if not cn_match_id_mb:
                continue

            # C-052：移除"已存在即跳过"，改为 ON CONFLICT 幂等刷新，
            # 保证 schema 新增列（pred_ts_valid 等）在重扫时被回填

            # 从 model_predictions 查 ML 预测（C-052：最早批次 + 时间门禁；C-053：回放标记）
            mp_ts_mb = odds_conn.execute(
                "SELECT MIN(timestamp) AS min_ts FROM model_predictions "
                "WHERE match_id = ? AND prediction_type IN ('WDL_home','WDL_draw','WDL_away')",
                (en_match_id,)
            ).fetchone()
            ml_pred_ts_mb = mp_ts_mb['min_ts'] if mp_ts_mb else None
            mp_rows_mb = []
            ml_is_replay_mb = 0
            if ml_pred_ts_mb:
                mp_rows_mb = odds_conn.execute(
                    f"SELECT prediction_type, prediction, probability"
                    f"{', is_replay' if mp_has_replay else ''} FROM model_predictions "
                    f"WHERE match_id = ? AND prediction_type IN ('WDL_home','WDL_draw','WDL_away') "
                    f"AND timestamp = ?",
                    (en_match_id, ml_pred_ts_mb)
                ).fetchall()
                if mp_has_replay and mp_rows_mb:
                    ml_is_replay_mb = min(int(r['is_replay'] or 0) for r in mp_rows_mb)
            ml_probs_mb = {}
            for mpr in mp_rows_mb:
                if mpr['prediction_type'] == 'WDL_home':
                    ml_probs_mb[WDL_HOME_LABEL] = mpr['probability']
                elif mpr['prediction_type'] == 'WDL_draw':
                    ml_probs_mb[WDL_DRAW_LABEL] = mpr['probability']
                elif mpr['prediction_type'] == 'WDL_away':
                    ml_probs_mb[WDL_AWAY_LABEL] = mpr['probability']
            ml_pred_mb = _argmax_wdl(ml_probs_mb)
            ml_conf_mb = ml_probs_mb.get(ml_pred_mb) if ml_pred_mb else None
            pred_ts_valid_mb = _check_pred_ts_valid(ml_pred_ts_mb, match_date_mb)
            if not ml_pred_mb:
                skipped_no_pred += 1
                continue

            # 从 wdl_history 查赔率
            wh_row_mb = odds_conn.execute(
                "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id = ? "
                "ORDER BY timestamp DESC LIMIT 1",
                (cn_match_id_mb,)
            ).fetchone()
            if not wh_row_mb:
                wh_row_mb = odds_conn.execute(
                    "SELECT win_a, draw, win_b FROM wdl_history WHERE match_id_en = ? "
                    "ORDER BY timestamp DESC LIMIT 1",
                    (en_match_id,)
                ).fetchone()
            odds_values_mb = None
            odds_pred_mb = None
            odds_conf_mb = None
            if wh_row_mb and wh_row_mb['win_a'] and wh_row_mb['draw'] and wh_row_mb['win_b']:
                odds_values_mb = {
                    WDL_HOME_LABEL: wh_row_mb['win_a'],
                    WDL_DRAW_LABEL: wh_row_mb['draw'],
                    WDL_AWAY_LABEL: wh_row_mb['win_b'],
                }
                odds_pred_mb = _argmin_odds_direction(wh_row_mb['win_a'], wh_row_mb['draw'], wh_row_mb['win_b'])
                min_odds_mb = min(wh_row_mb['win_a'], wh_row_mb['draw'], wh_row_mb['win_b'])
                odds_conf_mb = 1.0 / min_odds_mb if min_odds_mb > 0 else None

            # matches 表 fallback 无 fusion_pred（post_match_review 缺失）
            atype_mb, conf_diff_mb, notes_mb, is_upset_mb, upset_odds_mb = _classify_anomaly(
                ml_pred_mb, odds_pred_mb, None, actual_wdl_mb, odds_values_mb
            )

            if not atype_mb:
                skipped_no_anomaly += 1
                continue

            upset_risk_score_mb = None
            try:
                from upset_factor_engine import compute_upset_risk_score
                score_mb = compute_upset_risk_score(
                    home_odds=odds_values_mb.get(WDL_HOME_LABEL) if odds_values_mb else None,
                    draw_odds=odds_values_mb.get(WDL_DRAW_LABEL) if odds_values_mb else None,
                    away_odds=odds_values_mb.get(WDL_AWAY_LABEL) if odds_values_mb else None,
                    ml_probs=ml_probs_mb if (ml_probs_mb and pred_ts_valid_mb and not ml_is_replay_mb) else None,
                    home_team=home_en, away_team=away_en, match_date=match_date_mb,
                )
                upset_risk_score_mb = score_mb.get('upset_risk_score')
            except Exception:
                pass

            if not pred_ts_valid_mb:
                notes_mb = (notes_mb + "; " if notes_mb else "") + \
                    f"⚠️赛后回填预测(ts={ml_pred_ts_mb})，urs已排除ml维"
            elif ml_is_replay_mb:
                notes_mb = (notes_mb + "; " if notes_mb else "") + \
                    f"♻️回放补算预测(锚点ts={ml_pred_ts_mb},当前模型回放),urs已排除ml维"

            try:
                ano_cur.execute("""
                    INSERT INTO anomaly_samples (
                        match_id, home_team, away_team, match_date,
                        actual_wdl, actual_score,
                        ml_prediction, ml_confidence, ml_probabilities,
                        odds_prediction, odds_confidence,
                        fusion_prediction,
                        anomaly_type, confidence_diff, notes,
                        is_upset, upset_odds, upset_risk_score,
                        ml_pred_ts, pred_ts_valid, ml_is_replay,
                        source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(match_id) DO UPDATE SET
                        home_team=excluded.home_team,
                        away_team=excluded.away_team,
                        actual_wdl=excluded.actual_wdl,
                        actual_score=excluded.actual_score,
                        ml_prediction=excluded.ml_prediction,
                        ml_confidence=excluded.ml_confidence,
                        ml_probabilities=excluded.ml_probabilities,
                        odds_prediction=excluded.odds_prediction,
                        odds_confidence=excluded.odds_confidence,
                        anomaly_type=excluded.anomaly_type,
                        confidence_diff=excluded.confidence_diff,
                        notes=excluded.notes,
                        is_upset=excluded.is_upset,
                        upset_odds=excluded.upset_odds,
                        upset_risk_score=excluded.upset_risk_score,
                        ml_pred_ts=excluded.ml_pred_ts,
                        pred_ts_valid=excluded.pred_ts_valid,
                        ml_is_replay=excluded.ml_is_replay,
                        source=excluded.source,
                        updated_at=excluded.updated_at
                """, (
                    cn_match_id_mb, home_cn_mb, away_cn_mb, match_date_mb,
                    actual_wdl_mb, actual_score_mb,
                    ml_pred_mb, ml_conf_mb,
                    json.dumps(ml_probs_mb, ensure_ascii=False) if ml_probs_mb else None,
                    odds_pred_mb, odds_conf_mb,
                    None,
                    atype_mb, conf_diff_mb, notes_mb,
                    1 if is_upset_mb else 0, upset_odds_mb, upset_risk_score_mb,
                    ml_pred_ts_mb, pred_ts_valid_mb, ml_is_replay_mb,
                    'auto_scan_matches_fallback', now_str, now_str
                ))
                imported += 1
                by_type[atype_mb] = by_type.get(atype_mb, 0) + 1
                if is_upset_mb:
                    by_type['_with_upset'] = by_type.get('_with_upset', 0) + 1
            except Exception as e:
                if verbose:
                    print(f"[ANOMALY-SCAN-WARN] matches-fallback 写入失败 {cn_match_id_mb}: {e}")
    except Exception as _e:
        if verbose:
            print(f"[ANOMALY-SCAN-WARN] matches 表 fallback 扫描失败: {_e}")

    ano_conn.commit()
    odds_conn.close()
    ano_conn.close()

    if verbose:
        print(f"\n[ANOMALY-SCAN] 扫描完成:")
        print(f"  总扫描场次: {len(rows)}")
        print(f"  成功录入异常: {imported}")
        print(f"  跳过(无 ML 预测): {skipped_no_pred}")
        print(f"  跳过(无异常): {skipped_no_anomaly}")
        print(f"  按类型分布: {by_type}")

    return {
        'total': len(rows),
        'imported': imported,
        'by_type': by_type,
        'skipped_no_pred': skipped_no_pred,
        'skipped_no_anomaly': skipped_no_anomaly,
        'cleaned_old_english': deleted_old,
    }


def add_anomaly_sample(match_id, home_team, away_team, match_date,
                       actual_wdl, actual_score,
                       ml_prediction=None, ml_confidence=None, ml_probabilities=None,
                       odds_prediction=None, odds_confidence=None,
                       fusion_prediction=None,
                       anomaly_type='爆冷', confidence_diff=None, notes=None,
                       source='manual'):
    """手动录入异常样本。

    match_id 必须是中文格式 f"{date}_{home}_{away}"。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    if anomaly_type not in ANOMALY_TYPES:
        anomaly_type = '爆冷'

    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    cursor.execute("""
        INSERT OR REPLACE INTO anomaly_samples (
            match_id, home_team, away_team, match_date,
            actual_wdl, actual_score,
            ml_prediction, ml_confidence, ml_probabilities,
            odds_prediction, odds_confidence,
            fusion_prediction,
            anomaly_type, confidence_diff, notes,
            source, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        match_id, home_team, away_team, match_date,
        actual_wdl, actual_score,
        ml_prediction, ml_confidence,
        json.dumps(ml_probabilities, ensure_ascii=False) if isinstance(ml_probabilities, dict) else (
            pd.Series(ml_probabilities).to_json() if ml_probabilities else None
        ),
        odds_prediction, odds_confidence,
        fusion_prediction,
        anomaly_type, confidence_diff, notes,
        source, now_str, now_str
    ))

    conn.commit()
    conn.close()
    print(f"异常样本已添加: {home_team} vs {away_team} (类型={anomaly_type})")


def get_anomaly_match_ids(months_back=6):
    """读取异常样本 match_id 列表（用于训练时加权匹配）。

    参数：
    - months_back: 只取最近 N 个月内创建的样本（防老化，C-030）

    返回：list[str] 中文格式 match_id
    """
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        if months_back:
            cutoff = (datetime.now() - timedelta(days=months_back * 30)).strftime('%Y-%m-%d %H:%M:%S')
            cursor.execute(
                "SELECT match_id FROM anomaly_samples WHERE created_at >= ?",
                (cutoff,)
            )
        else:
            cursor.execute("SELECT match_id FROM anomaly_samples")
        ids = [row[0] for row in cursor.fetchall()]
        conn.close()
        return ids
    except Exception as e:
        print(f"[ANOMALY-WARN] 读取异常样本库失败: {e}")
        return []


def get_anomaly_samples(anomaly_type=None, months_back=None):
    """读取异常样本 DataFrame（可选按类型/时间窗过滤）。"""
    conn = sqlite3.connect(DB_PATH)

    query = "SELECT * FROM anomaly_samples WHERE 1=1"
    params = []

    if anomaly_type:
        query += " AND anomaly_type = ?"
        params.append(anomaly_type)
    if months_back:
        cutoff = (datetime.now() - timedelta(days=months_back * 30)).strftime('%Y-%m-%d %H:%M:%S')
        query += " AND created_at >= ?"
        params.append(cutoff)

    query += " ORDER BY match_date DESC"
    df = pd.read_sql(query, conn, params=params)
    conn.close()

    return df


def get_anomaly_stats():
    """异常样本统计。"""
    conn = sqlite3.connect(DB_PATH)
    stats = {}

    cursor = conn.cursor()
    cursor.execute("SELECT COUNT(*) FROM anomaly_samples")
    stats['total'] = cursor.fetchone()[0]

    cursor.execute("SELECT anomaly_type, COUNT(*) FROM anomaly_samples GROUP BY anomaly_type")
    stats['by_type'] = dict(cursor.fetchall())

    cursor.execute("SELECT source, COUNT(*) FROM anomaly_samples GROUP BY source")
    stats['by_source'] = dict(cursor.fetchall())

    cursor.execute("""
        SELECT
            CASE
                WHEN ml_prediction IS NOT NULL AND ml_prediction != actual_wdl AND odds_prediction IS NOT NULL AND odds_prediction != actual_wdl THEN 'ML+赔率都错'
                WHEN ml_prediction IS NOT NULL AND ml_prediction != actual_wdl THEN '仅ML错'
                WHEN odds_prediction IS NOT NULL AND odds_prediction != actual_wdl THEN '仅赔率错'
                ELSE '其他'
            END as error_type,
            COUNT(*)
        FROM anomaly_samples
        WHERE ml_prediction IS NOT NULL OR odds_prediction IS NOT NULL
        GROUP BY error_type
    """)
    stats['error_distribution'] = dict(cursor.fetchall())

    # 联赛分布（从 home_team 推断，简化）
    cursor.execute("SELECT match_date, COUNT(*) FROM anomaly_samples GROUP BY substr(match_date,1,7)")
    stats['by_month'] = dict(cursor.fetchall())

    conn.close()
    return stats


def batch_add_from_backtest(results):
    """从回测结果批量录入异常样本（保留接口，供 backtest 链路调用）。

    与 scan_and_import_anomalies 的区别：
    - 本函数从内存中的回测 results 直接读取 ml/odds/fusion 三方预测
    - scan_and_import_anomalies 从 DB 表扫描
    两者均用 INSERT OR IGNORE 幂等写入。
    """
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    added_count = 0
    now_str = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    for r in results:
        match_info = r.get('match_info', {})
        match_id = r.get('match_id')
        if not match_id:
            continue

        home_team = match_info.get('home_team')
        away_team = match_info.get('away_team')
        match_date = match_info.get('match_date')
        actual_wdl = match_info.get('actual_wdl')
        actual_score = match_info.get('actual_score')

        ml_pred = r.get('ml_prediction', {})
        ml_prediction = ml_pred.get('prediction')
        ml_confidence = ml_pred.get('confidence')
        ml_probabilities = ml_pred.get('probabilities')

        odds_analysis = r.get('odds_analysis', {}).get('wdl', {})
        odds_prediction = odds_analysis.get('signal_label')
        odds_confidence = odds_analysis.get('confidence')

        fusion_prediction = r.get('final_prediction')

        if actual_wdl in [WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL]:
            ml_error = ml_prediction in [WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL] and ml_prediction != actual_wdl
            odds_error = odds_prediction in [WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL] and odds_prediction != actual_wdl
            fusion_error = (fusion_prediction in [WDL_HOME_LABEL, WDL_DRAW_LABEL, WDL_AWAY_LABEL]
                            and fusion_prediction != actual_wdl)

            if ml_error or odds_error or fusion_error:
                if ml_error and odds_error and fusion_error:
                    anomaly_type = '三重错误'
                elif ml_error and odds_error:
                    anomaly_type = '双重错误'
                elif ml_error:
                    anomaly_type = 'ML错误'
                elif odds_error:
                    anomaly_type = '赔率错误'
                else:
                    anomaly_type = '融合错误'

                confidence_diff = -ml_confidence if (ml_confidence and ml_prediction != actual_wdl) else None

                notes = []
                if ml_error:
                    notes.append(f"ML预测{ml_prediction}，实际{actual_wdl}")
                if odds_error:
                    notes.append(f"赔率预测{odds_prediction}，实际{actual_wdl}")
                if fusion_error:
                    notes.append(f"融合预测{fusion_prediction}，实际{actual_wdl}")
                notes = '; '.join(notes)

                cursor.execute("""
                    INSERT OR IGNORE INTO anomaly_samples (
                        match_id, home_team, away_team, match_date,
                        actual_wdl, actual_score,
                        ml_prediction, ml_confidence, ml_probabilities,
                        odds_prediction, odds_confidence,
                        fusion_prediction,
                        anomaly_type, confidence_diff, notes,
                        source, created_at, updated_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    match_id, home_team, away_team, match_date,
                    actual_wdl, actual_score,
                    ml_prediction, ml_confidence,
                    json.dumps(ml_probabilities, ensure_ascii=False) if isinstance(ml_probabilities, dict) else (
                        pd.Series(ml_probabilities).to_json() if ml_probabilities else None
                    ),
                    odds_prediction, odds_confidence,
                    fusion_prediction,
                    anomaly_type, confidence_diff, notes,
                    'backtest', now_str, now_str
                ))
                added_count += 1

    conn.commit()
    conn.close()
    print(f"批量添加完成，共添加 {added_count} 条异常样本")
    return added_count


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="异常样本库管理")
    parser.add_argument('--init', action='store_true', help='初始化表结构')
    parser.add_argument('--scan', action='store_true', help='自动扫描录入异常')
    parser.add_argument('--league', default=None, help='限定联赛（英超/西甲/意甲/德甲/法甲）')
    parser.add_argument('--days-back', type=int, default=None, help='只扫描最近 N 天')
    parser.add_argument('--stats', action='store_true', help='显示统计')
    parser.add_argument('--list', action='store_true', help='列出最近样本')
    args = parser.parse_args()

    if args.init or not (args.scan or args.stats or args.list):
        init_anomaly_database()

    if args.scan:
        result = scan_and_import_anomalies(league=args.league, days_back=args.days_back)
        print(f"\n扫描结果: {result}")

    if args.stats:
        stats = get_anomaly_stats()
        print("\n异常样本库统计:")
        print(f"  总样本数: {stats['total']}")
        print(f"  按类型分布: {stats.get('by_type', {})}")
        print(f"  按来源分布: {stats.get('by_source', {})}")
        print(f"  错误分布: {stats.get('error_distribution', {})}")
        print(f"  按月分布: {stats.get('by_month', {})}")

    if args.list:
        df = get_anomaly_samples(months_back=6)
        if not df.empty:
            print(f"\n最近异常样本 ({len(df)} 条，最近6个月):")
            print(df[['match_date', 'home_team', 'away_team', 'actual_wdl',
                      'ml_prediction', 'odds_prediction', 'anomaly_type', 'source']].to_string())
        else:
            print("无异常样本")
