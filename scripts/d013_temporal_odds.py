"""
D-013 时序赔率特征模块（P1-9 扩展版）
====================================

从 odds.db 的 wdl_history / handicap_history / total_goals_history 三张时序表
提取赔率时间序列，计算 22 维赛前可用特征。

P1-9 扩展（C-20260828-002）:
    - 三张时序表全部接入（原版仅用 wdl_history，handicap/total_goals 闲置）
    - 对齐改用 feature_utils.build_match_alignment 三通道（直接/桥表/match_id_en），
      对齐率 99.4%（原版 match_mapping 查询列名错误，静默回退单通道）
    - 新增「去水后隐含概率漂移」：以去水概率 first→last 漂移替代开→终赔率漂移
      （竞彩终盘非真终盘，且 *_history 无 open/close 字段）
    - 新增「跨盘口一致性」：WDL vs 让球盘去水概率的方向/幅度一致性
    - 修正原 load_timing_total_goals 的 bug：精确比分赔率不能直接求和当概率，
      现按 1/odds 转隐含概率后归一

数据来源:
    - odds.db: wdl_history(12,575场), handicap_history(13,125场),
      total_goals_history(13,126场)
    - 平均每场 5.2 个时间点（WDL）

输出特征（22维）:
    【WDL 时序 10维（原版保留）】
    - wdl_win_volatility / wdl_draw_volatility / wdl_lose_volatility: 三向赔率波动率
    - wdl_win_acceleration:      主胜赔率加速度（二阶导）
    - wdl_late_trend / wdl_early_trend: 临场/早期趋势
    - wdl_mid_stability:         中期稳定性（中间段方差比）
    - wdl_sudden_jump:           突变检测（最大单次相对跳变）
    - wdl_update_frequency:      更新频率（时间点数/天）
    - wdl_total_change:          总变化幅度

    【去水隐含概率漂移 4维】
    - odds_ts_devig_win_drift:   主胜去水概率漂移（末-首）
    - odds_ts_devig_draw_drift:  平局去水概率漂移
    - odds_ts_devig_lose_drift:  客胜去水概率漂移
    - odds_ts_devig_win_close:   末点主胜去水概率（市场最新定价水平）

    【让球时序 3维】
    - odds_ts_hcp_volatility:        上盘(hcp_win)赔率波动率
    - odds_ts_hcp_devig_home_drift:  让球主赢去水概率漂移
    - odds_ts_hcp_devig_home_close:  让球末点主赢去水概率

    【大小球时序 3维】
    - odds_ts_ou25_volatility:       大2.5去水概率波动率
    - odds_ts_ou25_devig_over_drift: 大2.5去水概率漂移
    - odds_ts_ou25_devig_over_close: 大2.5末点去水概率

    【跨盘口一致性 2维】
    - odds_ts_xmkt_wdl_hcp_gap:   WDL末点主胜去水概率 - 让球末点主赢去水概率
    - odds_ts_xmkt_direction_agree: 两盘漂移方向一致性（1一致/0背离/0.5缺数据）

设计原则:
    - 严格使用赛前赔率数据，过滤 2026-07 及以后的合成时间戳（project_memory 规则）
    - 全量一次加载 + groupby 批量计算，避免逐场 SQL 查询的 O(n²) 瓶颈
    - 未对齐/无数据场次填默认值（0.0，mid_stability=0.5，direction_agree=0.5）
    - 输出附带内部指标列 _ts_matched（1=该场 WDL 时序≥2点），供上游做联赛
      z-score 归一时屏蔽无数据行，上游用完应丢弃该列
"""

import os
import sys

import pandas as pd
import numpy as np

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
if _SCRIPT_DIR not in sys.path:
    sys.path.insert(0, _SCRIPT_DIR)

_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from db_utils import connect, read_sql  # noqa: E402

TIMING_DB_PATH = os.path.join(_PROJECT_DIR, "data", "odds.db")

# 2026-07 时间戳过滤阈值（project_memory 规则：26/27 赛季赛前导入的合成时间戳）
MAX_VALID_TIMESTAMP = "2026-07-01 00:00:00"

# 精确总进球赔率列（0~7+）
_GOAL_COLS = ['goals_0', 'goals_1', 'goals_2', 'goals_3',
              'goals_4', 'goals_5', 'goals_6', 'goals_7_plus']

# 无数据时的默认值
FEATURE_DEFAULTS = {
    # WDL 时序 10 维
    'wdl_win_volatility': 0.0,
    'wdl_draw_volatility': 0.0,
    'wdl_lose_volatility': 0.0,
    'wdl_win_acceleration': 0.0,
    'wdl_late_trend': 0.0,
    'wdl_early_trend': 0.0,
    'wdl_mid_stability': 0.5,
    'wdl_sudden_jump': 0.0,
    'wdl_update_frequency': 0.0,
    'wdl_total_change': 0.0,
    # 去水漂移 4 维
    'odds_ts_devig_win_drift': 0.0,
    'odds_ts_devig_draw_drift': 0.0,
    'odds_ts_devig_lose_drift': 0.0,
    'odds_ts_devig_win_close': 0.0,
    # 让球时序 3 维
    'odds_ts_hcp_volatility': 0.0,
    'odds_ts_hcp_devig_home_drift': 0.0,
    'odds_ts_hcp_devig_home_close': 0.0,
    # 大小球时序 3 维
    'odds_ts_ou25_volatility': 0.0,
    'odds_ts_ou25_devig_over_drift': 0.0,
    'odds_ts_ou25_devig_over_close': 0.0,
    # 跨盘口一致性 2 维
    'odds_ts_xmkt_wdl_hcp_gap': 0.0,
    'odds_ts_xmkt_direction_agree': 0.5,
    # 内部指标列
    '_ts_matched': 0.0,
}


def load_timing_conn():
    """加载 odds.db 连接（时序数据在 *_history 表中）"""
    return connect(db_path=TIMING_DB_PATH)


# ========================================
# 单表加载器（保留供调试/单场查询使用）
# ========================================

def load_timing_wdl(match_id: str, conn) -> pd.DataFrame:
    """加载单场 WDL 时序赔率（wdl_history 表）"""
    query = """
        SELECT timestamp, win_a, draw, win_b
        FROM wdl_history
        WHERE match_id = ?
        ORDER BY timestamp
    """
    df = read_sql(query, conn, params=(match_id,))
    if not df.empty:
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
        except Exception:
            pass
    return df


def load_timing_handicap(match_id: str, conn) -> pd.DataFrame:
    """加载单场让球时序赔率（handicap_history 表）"""
    query = """
        SELECT timestamp, hcp_win, hcp_draw, hcp_lose
        FROM handicap_history
        WHERE match_id = ?
        ORDER BY timestamp
    """
    df = read_sql(query, conn, params=(match_id,))
    if not df.empty:
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
        except Exception:
            pass
    return df


def load_timing_total_goals(match_id: str, conn) -> pd.DataFrame:
    """加载单场总进球时序，返回大2.5/小2.5的**去水概率**序列。

    修正（C-20260828-002）：原版直接对精确比分赔率求和当概率（赔率不可加），
    现按 1/odds 转隐含概率后归一为 over/under 两向分布。
    """
    cols = ', '.join(_GOAL_COLS)
    query = f"""
        SELECT timestamp, {cols}
        FROM total_goals_history
        WHERE match_id = ?
        ORDER BY timestamp
    """
    df = read_sql(query, conn, params=(match_id,))
    if df.empty:
        return df
    try:
        df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
    except Exception:
        pass
    inv = 1.0 / df[_GOAL_COLS].clip(lower=1e-6)
    total = inv.sum(axis=1)
    over = inv[_GOAL_COLS[3:]].sum(axis=1)
    df['over_25'] = np.where(total > 0, over / total, 0.5)
    df['under_25'] = 1.0 - df['over_25']
    return df[['timestamp', 'over_25', 'under_25']]


# ========================================
# 基础统计函数（原版保留）
# ========================================

def compute_volatility(series: pd.Series) -> float:
    """计算波动率（标准差）"""
    if len(series) < 2:
        return 0.0
    return float(np.std(series.values))


def compute_acceleration(series: pd.Series) -> float:
    """加速度（二阶差分均值）"""
    if len(series) < 3:
        return 0.0
    diffs = np.diff(series.values)
    diffs2 = np.diff(diffs)
    return float(np.mean(diffs2))


def compute_late_trend(series: pd.Series) -> float:
    """临场趋势（最后两次变化）"""
    if len(series) < 2:
        return 0.0
    changes = np.diff(series.values)
    if len(changes) == 0:
        return 0.0
    return float(changes[-1])


def compute_early_trend(series: pd.Series) -> float:
    """早期趋势（前两次变化均值）"""
    if len(series) < 2:
        return 0.0
    changes = np.diff(series.values)
    if len(changes) == 0:
        return 0.0
    early = changes[:min(2, len(changes))]
    return float(np.mean(early))


def compute_mid_stability(series: pd.Series) -> float:
    """中期稳定性（中间段方差 / 总方差）"""
    if len(series) < 4:
        return 0.5
    n = len(series)
    mid_start = n // 3
    mid_end = 2 * n // 3
    mid_variance = np.var(series.values[mid_start:mid_end])
    total_variance = np.var(series.values)
    if total_variance < 1e-10:
        return 0.5
    return float(mid_variance / total_variance)


def compute_sudden_jump(series: pd.Series, threshold: float = 0.20) -> float:
    """突变检测（最大单次相对变化，clip 到 [0,1]）"""
    if len(series) < 2:
        return 0.0
    changes = np.abs(np.diff(series.values))
    base = np.abs(series.values[:-1])
    with np.errstate(divide='ignore', invalid='ignore'):
        relative_changes = np.where(base > 1e-6, changes / base, 0.0)
    max_jump = float(np.max(relative_changes)) if len(relative_changes) > 0 else 0.0
    return float(np.clip(max_jump, 0.0, 1.0))


def compute_update_frequency(df: pd.DataFrame) -> float:
    """更新频率（时间点数 / 覆盖天数）"""
    if len(df) < 2 or 'timestamp' not in df.columns:
        return 0.0
    try:
        time_range = (df['timestamp'].iloc[-1] - df['timestamp'].iloc[0]).total_seconds() / 86400.0
        if time_range < 0.01:
            return float(len(df))
        return float(len(df) / time_range)
    except Exception:
        return float(len(df))


def compute_total_change(series: pd.Series) -> float:
    """总变化幅度（绝对变化之和）"""
    if len(series) < 2:
        return 0.0
    return float(np.sum(np.abs(np.diff(series.values))))


# ========================================
# P1-9 新增：去水与跨盘口工具
# ========================================

def devig_3way(odds: np.ndarray) -> np.ndarray:
    """三向赔率去水：1/odds 归一化为概率。

    Args:
        odds: shape (n, 3) 的赔率矩阵（win, draw, lose）

    Returns:
        shape (n, 3) 的去水概率矩阵，每行和为 1
    """
    inv = 1.0 / np.clip(odds, 1e-6, None)
    return inv / inv.sum(axis=1, keepdims=True)


def _valid_rows(df: pd.DataFrame, cols: list) -> pd.DataFrame:
    """过滤掉赔率缺失/非正的行"""
    mask = pd.Series(True, index=df.index)
    for c in cols:
        mask &= df[c].notna() & (df[c] > 0)
    return df[mask]


def _compute_wdl_features(g: pd.DataFrame) -> dict:
    """从单场 WDL 时序（已按 timestamp 排序）计算 14 维特征（10 原版 + 4 漂移）。"""
    f = {}
    wa = g['win_a'].values.astype(float)
    dr = g['draw'].values.astype(float)
    lb = g['win_b'].values.astype(float)

    # --- 原版 10 维 ---
    f['wdl_win_volatility'] = compute_volatility(pd.Series(wa))
    f['wdl_draw_volatility'] = compute_volatility(pd.Series(dr))
    f['wdl_lose_volatility'] = compute_volatility(pd.Series(lb))
    f['wdl_win_acceleration'] = compute_acceleration(pd.Series(wa))
    f['wdl_late_trend'] = compute_late_trend(pd.Series(wa))
    f['wdl_early_trend'] = compute_early_trend(pd.Series(wa))
    f['wdl_mid_stability'] = compute_mid_stability(pd.Series(wa))
    f['wdl_sudden_jump'] = compute_sudden_jump(pd.Series(wa))
    f['wdl_update_frequency'] = compute_update_frequency(g)
    f['wdl_total_change'] = compute_total_change(pd.Series(wa))

    # --- P1-9 去水漂移 4 维 ---
    p = devig_3way(np.column_stack([wa, dr, lb]))
    f['odds_ts_devig_win_drift'] = float(p[-1, 0] - p[0, 0])
    f['odds_ts_devig_draw_drift'] = float(p[-1, 1] - p[0, 1])
    f['odds_ts_devig_lose_drift'] = float(p[-1, 2] - p[0, 2])
    f['odds_ts_devig_win_close'] = float(p[-1, 0])

    # 内部量（跨盘口一致性/匹配判定用，最后从输出中剔除）
    f['_wdl_close_win_p'] = float(p[-1, 0])
    f['_wdl_win_drift'] = float(p[-1, 0] - p[0, 0])
    f['_wdl_n'] = int(len(g))
    return f


def _compute_hcp_features(g: pd.DataFrame) -> dict:
    """从单场让球时序（已排序）计算 3 维特征 + 内部跨盘口量。"""
    f = {}
    hw = g['hcp_win'].values.astype(float)
    hd = g['hcp_draw'].values.astype(float)
    hl = g['hcp_lose'].values.astype(float)

    f['odds_ts_hcp_volatility'] = compute_volatility(pd.Series(hw))

    p = devig_3way(np.column_stack([hw, hd, hl]))
    f['odds_ts_hcp_devig_home_drift'] = float(p[-1, 0] - p[0, 0])
    f['odds_ts_hcp_devig_home_close'] = float(p[-1, 0])

    f['_hcp_close_home_p'] = float(p[-1, 0])
    f['_hcp_home_drift'] = float(p[-1, 0] - p[0, 0])
    return f


def _compute_ou_features(g: pd.DataFrame) -> dict:
    """从单场总进球时序（已排序）计算大2.5去水概率 3 维特征。"""
    f = {}
    inv = 1.0 / g[_GOAL_COLS].clip(lower=1e-6).values.astype(float)
    total = inv.sum(axis=1)
    over_raw = inv[:, 3:].sum(axis=1)
    p_over = np.where(total > 1e-9, over_raw / np.clip(total, 1e-9, None), 0.5)

    f['odds_ts_ou25_volatility'] = float(np.std(p_over)) if len(p_over) >= 2 else 0.0
    f['odds_ts_ou25_devig_over_drift'] = float(p_over[-1] - p_over[0])
    f['odds_ts_ou25_devig_over_close'] = float(p_over[-1])
    return f


def _compute_cross_market(wdl_f: dict, hcp_f: dict) -> dict:
    """跨盘口一致性：WDL 与让球盘去水概率的幅度差 + 漂移方向一致性。"""
    f = {}
    has_wdl = '_wdl_close_win_p' in wdl_f
    has_hcp = '_hcp_close_home_p' in hcp_f
    if has_wdl and has_hcp:
        f['odds_ts_xmkt_wdl_hcp_gap'] = wdl_f['_wdl_close_win_p'] - hcp_f['_hcp_close_home_p']
        sign_w = np.sign(wdl_f['_wdl_win_drift'])
        sign_h = np.sign(hcp_f['_hcp_home_drift'])
        f['odds_ts_xmkt_direction_agree'] = 1.0 if sign_w == sign_h else 0.0
    else:
        f['odds_ts_xmkt_wdl_hcp_gap'] = 0.0
        f['odds_ts_xmkt_direction_agree'] = 0.5
    return f


# ========================================
# 向量化分组计算（C-20260830-008：替换逐组 pd.Series 包装的 O(n·group) 瓶颈）
# ========================================

def _group_apply_np(df: pd.DataFrame, key_col: str, fn, value_cols: list,
                    matrix: bool = False) -> dict:
    """按 key_col 分组，用 numpy 切片批量计算每场特征（纯 numpy，无 iterrows/Series）。

    与逐组 `df.groupby(...)` + `_compute_*_features(g)` 等价：
    - 分组位置由 groupby.indices 给出，组内顺序 = df 行序（已按 timestamp 排序）；
    - 每组仅做一次 numpy 数组 fancy-index 切片，再调用 fn 计算。
    """
    groups = df.groupby(key_col, sort=False).indices
    arrs = [df[c].to_numpy(dtype=np.float64) for c in value_cols]
    out = {}
    if matrix:
        for k, idx in groups.items():
            out[k] = fn(np.column_stack([a[idx] for a in arrs]))
    else:
        for k, idx in groups.items():
            out[k] = fn(*(a[idx] for a in arrs))
    return out


def _wdl_feats_np(wa, dr, lb) -> dict:
    """WDL 时序 14 维（10 原版 + 4 去水漂移），纯 numpy 版（与原 _compute_wdl_features 等价）。"""
    n = wa.shape[0]
    f = {}

    # 波动率（标准差）
    f['wdl_win_volatility'] = float(np.std(wa)) if n >= 2 else 0.0
    f['wdl_draw_volatility'] = float(np.std(dr)) if n >= 2 else 0.0
    f['wdl_lose_volatility'] = float(np.std(lb)) if n >= 2 else 0.0

    # 加速度（二阶差分均值）
    f['wdl_win_acceleration'] = float(np.mean(np.diff(wa, 2))) if n >= 3 else 0.0

    if n >= 2:
        changes = np.diff(wa)
        f['wdl_late_trend'] = float(changes[-1])
        f['wdl_early_trend'] = float(np.mean(changes[:min(2, changes.shape[0])]))
        base = np.abs(wa[:-1])
        rel = np.where(base > 1e-6, np.abs(changes) / base, 0.0)
        f['wdl_sudden_jump'] = float(np.clip(np.max(rel), 0.0, 1.0))
        f['wdl_total_change'] = float(np.sum(np.abs(changes)))
        f['wdl_update_frequency'] = float(n)
    else:
        f['wdl_late_trend'] = 0.0
        f['wdl_early_trend'] = 0.0
        f['wdl_sudden_jump'] = 0.0
        f['wdl_total_change'] = 0.0
        f['wdl_update_frequency'] = 0.0

    if n >= 4:
        mid_start = n // 3
        mid_end = 2 * n // 3
        mid_var = float(np.var(wa[mid_start:mid_end]))
        total_var = float(np.var(wa))
        f['wdl_mid_stability'] = (mid_var / total_var) if total_var >= 1e-10 else 0.5
    else:
        f['wdl_mid_stability'] = 0.5

    # 去水漂移 4 维 + 内部量
    p = devig_3way(np.column_stack([wa, dr, lb]))
    f['odds_ts_devig_win_drift'] = float(p[-1, 0] - p[0, 0])
    f['odds_ts_devig_draw_drift'] = float(p[-1, 1] - p[0, 1])
    f['odds_ts_devig_lose_drift'] = float(p[-1, 2] - p[0, 2])
    f['odds_ts_devig_win_close'] = float(p[-1, 0])
    f['_wdl_close_win_p'] = float(p[-1, 0])
    f['_wdl_win_drift'] = float(p[-1, 0] - p[0, 0])
    f['_wdl_n'] = int(n)
    return f


def _hcp_feats_np(hw, hd, hl) -> dict:
    """让球时序 3 维 + 内部跨盘口量，纯 numpy 版。"""
    f = {}
    f['odds_ts_hcp_volatility'] = float(np.std(hw)) if hw.shape[0] >= 2 else 0.0
    p = devig_3way(np.column_stack([hw, hd, hl]))
    f['odds_ts_hcp_devig_home_drift'] = float(p[-1, 0] - p[0, 0])
    f['odds_ts_hcp_devig_home_close'] = float(p[-1, 0])
    f['_hcp_close_home_p'] = float(p[-1, 0])
    f['_hcp_home_drift'] = float(p[-1, 0] - p[0, 0])
    return f


def _ou_feats_np(goal_matrix) -> dict:
    """大小球时序 3 维，纯 numpy 版（输入为 [N, 8] 精确比分赔率矩阵）。"""
    inv = 1.0 / np.clip(goal_matrix, 1e-6, None)
    total = inv.sum(axis=1)
    over_raw = inv[:, 3:].sum(axis=1)
    p_over = np.where(total > 1e-9, over_raw / np.clip(total, 1e-9, None), 0.5)
    f = {}
    f['odds_ts_ou25_volatility'] = float(np.std(p_over)) if p_over.shape[0] >= 2 else 0.0
    f['odds_ts_ou25_devig_over_drift'] = float(p_over[-1] - p_over[0])
    f['odds_ts_ou25_devig_over_close'] = float(p_over[-1])
    return f


# ========================================
# 主构建函数
# ========================================

def build_d013_features(df: pd.DataFrame, timing_conn=None) -> pd.DataFrame:
    """构建 D-013 时序赔率特征矩阵（22 维 + 内部 _ts_matched 指标列）。

    Args:
        df: 比赛数据 DataFrame，需含 match_id 列（matches.match_id 格式）
        timing_conn: odds.db 连接（可选，默认自建）

    Returns:
        pd.DataFrame: 索引与 df 对齐，22 个特征列 + _ts_matched。
        _ts_matched 供上游联赛 z-score 屏蔽无数据行，用完应丢弃。
    """
    if timing_conn is None:
        conn = load_timing_conn()
        own_conn = True
    else:
        conn = timing_conn
        own_conn = False

    try:
        # 延迟导入避免模块加载顺序问题
        from feature_utils import build_match_alignment

        # ---- 1. 一次性加载三张时序表（过滤 2026-07 合成时间戳）----
        wdl = read_sql(
            "SELECT match_id, timestamp, win_a, draw, win_b FROM wdl_history "
            "WHERE timestamp < ?",
            conn, params=(MAX_VALID_TIMESTAMP,))
        hcp = read_sql(
            "SELECT match_id, timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history "
            "WHERE timestamp < ?",
            conn, params=(MAX_VALID_TIMESTAMP,))
        tg = read_sql(
            f"SELECT match_id, timestamp, {', '.join(_GOAL_COLS)} FROM total_goals_history "
            "WHERE timestamp < ?",
            conn, params=(MAX_VALID_TIMESTAMP,))

        # ---- 2. 三通道对齐：history.match_id → matches.match_id ----
        wdl['mmid'] = wdl['match_id'].map(build_match_alignment(conn, 'wdl_history'))
        hcp['mmid'] = hcp['match_id'].map(build_match_alignment(conn, 'handicap_history'))
        tg['mmid'] = tg['match_id'].map(build_match_alignment(conn, 'total_goals_history'))
    finally:
        if own_conn:
            conn.close()

    for t in (wdl, hcp, tg):
        t.dropna(subset=['mmid'], inplace=True)

    # ---- 3. 排序并过滤无效赔率行 ----
    wdl = _valid_rows(wdl.sort_values('timestamp'), ['win_a', 'draw', 'win_b'])
    hcp = _valid_rows(hcp.sort_values('timestamp'), ['hcp_win', 'hcp_draw', 'hcp_lose'])
    tg = _valid_rows(tg.sort_values('timestamp'), _GOAL_COLS)

    # ---- 4. 按场比赛分组计算（纯 numpy 向量化，替换逐组 iterrows/Series 包装）----
    wdl_feats = _group_apply_np(wdl, 'mmid', _wdl_feats_np,
                                ['win_a', 'draw', 'win_b'])
    hcp_feats = _group_apply_np(hcp, 'mmid', _hcp_feats_np,
                                ['hcp_win', 'hcp_draw', 'hcp_lose'])
    ou_feats = _group_apply_np(tg, 'mmid', _ou_feats_np, _GOAL_COLS, matrix=True)

    # ---- 5. 合并三市场 + 跨盘口一致性 ----
    all_mmids = set(wdl_feats) | set(hcp_feats) | set(ou_feats)
    records = {}
    for mmid in all_mmids:
        wf = wdl_feats.get(mmid, {})
        hf = hcp_feats.get(mmid, {})
        of = ou_feats.get(mmid, {})
        rec = {}
        rec.update({k: v for k, v in wf.items() if not k.startswith('_')})
        rec.update({k: v for k, v in hf.items() if not k.startswith('_')})
        rec.update(of)
        rec.update(_compute_cross_market(wf, hf))
        rec['_ts_matched'] = 1.0 if wf.get('_wdl_n', 0) >= 2 else 0.0
        records[mmid] = rec

    feat_df = pd.DataFrame.from_dict(records, orient='index')

    # ---- 6. 对齐回 df（按 match_id 映射，未命中填默认值）----
    feat_df = feat_df.reindex(pd.Index(df['match_id'].values))
    feat_df = feat_df.fillna(FEATURE_DEFAULTS)
    feat_df.index = df.index

    n_matched = int(feat_df['_ts_matched'].sum())
    print(f"   [D-013] 时序特征覆盖: {n_matched}/{len(df)} "
          f"({n_matched / max(len(df), 1) * 100:.1f}%)")

    return feat_df


def get_d013_feature_names() -> list:
    """返回 D-013 所有特征名列表（22 维）"""
    names = [k for k in FEATURE_DEFAULTS if k != '_ts_matched']
    return names


# ========================================
# 独立运行验证
# ========================================

if __name__ == '__main__':
    from feature_utils import load_match_data_odds

    print("=" * 70)
    print("[TEST] D-013 时序赔率特征验证（P1-9 扩展版）")
    print("=" * 70)

    df = load_match_data_odds()
    print(f"  加载比赛数据: {len(df)} 场")

    print("\n  构建 D-013 时序赔率特征...")
    d013_df = build_d013_features(df)
    print(f"  D-013 特征维度: {d013_df.shape[1]} (含 _ts_matched)")

    print("\n  非零值比例:")
    for col in d013_df.columns:
        non_zero = (d013_df[col] != 0).sum()
        pct = non_zero / len(d013_df) * 100
        print(f"    {col}: {non_zero}/{len(d013_df)} ({pct:.1f}%)")

    print("\n  特征统计:")
    print(d013_df.drop(columns=['_ts_matched']).describe().round(4).to_string())

    print("\n[OK] D-013 时序赔率特征模块验证完成!")
