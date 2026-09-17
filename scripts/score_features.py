"""
比分赔率特征提取模块 (T-003.1)
===============================

从 odds.db 的 score_history 表提取比分赔率数据，计算8维赛前可用特征。

数据来源:
    - odds.db: score_history 表（13,122 个不同 match_id，~31 万条记录）
    - 平均每场 ~25 条比分记录

P1-9 接入（C-20260828-002）:
    - 对齐改用 feature_utils.build_match_alignment 三通道（直接/桥表），
      score_history 无 match_id_en 列，通道2自动跳过
    - 全量一次加载 + groupby 批量计算，替换原逐场候选 match_id 探测循环
      （原实现每场最多 3 次 SQL 查询，14k 场为 O(n) 查询瓶颈）

输出特征（8维）:
    - score_mode_prob:          最可能比分的归一化概率
    - score_entropy:            比分分布熵（越高=越不确定）
    - score_home_win_prob:      所有主胜比分概率之和
    - score_draw_prob:          所有平局比分概率之和
    - score_away_win_prob:      所有客胜比分概率之和
    - score_over_25_prob:       总进球>2.5概率
    - score_expected_goals:     比分加权预期总进球
    - score_top3_concentration: 前3最可能比分概率集中度

特殊处理:
    - "胜其它"/"平其它"/"负其它": 概率比例分配法
    - 单数字比分（"1","0","2","3"）: 跳过
    - 2026-07 时间戳: 按 project_memory 规则过滤

设计原则:
    - 严格使用赛前赔率数据（取每个 match_id 的最新时间戳记录）
    - 缺失值使用全局中位数填充
"""

import os
import sys
import pandas as pd
import numpy as np
from typing import Dict, Optional, Tuple, List

# 自动检测项目根目录，避免硬编码盘符
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_PROJECT_DIR = os.path.dirname(_SCRIPT_DIR)
if _PROJECT_DIR not in sys.path:
    sys.path.insert(0, _PROJECT_DIR)
from db_utils import connect, read_sql  # noqa: E402

TIMING_DB_PATH = os.path.join(_PROJECT_DIR, "data", "odds.db")

# 2026-07 时间戳过滤阈值（project_memory 规则）
MAX_VALID_TIMESTAMP = "2026-07-01 00:00:00"


def load_score_conn():
    """加载 odds.db 连接"""
    return connect(db_path=TIMING_DB_PATH)


def parse_score(score_str: str) -> Optional[Tuple[int, int]]:
    """
    解析比分字符串为 (主队进球, 客队进球)
    
    支持格式: "1:0", "2:1", "0:0", "2-1" 等
    特殊标签: "胜其它", "平其它", "负其它" 返回 None
    
    参数:
        score_str: 比分字符串
    
    返回:
        Optional[Tuple[int, int]]: (主队进球, 客队进球)，无法解析时返回 None
    """
    if not isinstance(score_str, str):
        return None
    
    score_str = score_str.strip()
    
    # 特殊标签
    if score_str in ('胜其它', '平其它', '负其它'):
        return None
    
    # 单数字比分（如 "1", "0", "2", "3"），无效
    if score_str.isdigit():
        return None
    
    # 尝试 ":" 分隔
    if ':' in score_str:
        parts = score_str.split(':')
        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            return (int(parts[0].strip()), int(parts[1].strip()))
    
    # 尝试 "-" 分隔
    if '-' in score_str:
        parts = score_str.split('-')
        if len(parts) == 2 and parts[0].strip().isdigit() and parts[1].strip().isdigit():
            return (int(parts[0].strip()), int(parts[1].strip()))
    
    return None


def load_score_history(match_id: str, conn) -> pd.DataFrame:
    """
    从 odds.db 加载指定比赛的比分赔率历史
    
    参数:
        match_id: 比赛ID（格式: 'YYYY-MM-DD_中文主队_中文客队'）
        conn: 数据库连接
    
    返回:
        pd.DataFrame: 比分赔率数据，按 timestamp 排序，过滤 2026-07 时间戳
    """
    query = """
        SELECT timestamp, score, odds 
        FROM score_history 
        WHERE match_id = ? 
        ORDER BY timestamp
    """
    df = read_sql(query, conn, params=(match_id,))
    
    if not df.empty:
        try:
            df['timestamp'] = pd.to_datetime(df['timestamp'], format='mixed')
        except Exception:
            pass
        
        # 过滤 2026-07 时间戳（project_memory 规则）
        max_valid = pd.to_datetime(MAX_VALID_TIMESTAMP)
        if 'timestamp' in df.columns and pd.api.types.is_datetime64_any_dtype(df['timestamp']):
            df = df[df['timestamp'] < max_valid]
    
    return df


def compute_score_features_from_df(score_df: pd.DataFrame) -> Dict[str, float]:
    """
    从比分赔率 DataFrame 计算8维特征
    
    处理逻辑:
    1. 解析所有比分字符串
    2. 分别处理正常比分和特殊标签（胜其它/平其它/负其它）
    3. 概率归一化
    4. 计算8维特征
    
    参数:
        score_df: 比分赔率 DataFrame（含 score, odds 列）
    
    返回:
        Dict[str, float]: 8维特征字典
    """
    # 默认值
    defaults = {
        'score_mode_prob': 0.0,
        'score_entropy': 0.0,
        'score_home_win_prob': 0.0,
        'score_draw_prob': 0.0,
        'score_away_win_prob': 0.0,
        'score_over_25_prob': 0.0,
        'score_expected_goals': 0.0,
        'score_top3_concentration': 0.0,
    }
    
    if score_df.empty:
        return defaults
    
    # 取最新时间戳的记录（赛前最新赔率）
    if 'timestamp' in score_df.columns:
        latest_ts = score_df['timestamp'].max()
        latest_df = score_df[score_df['timestamp'] == latest_ts]
    else:
        latest_df = score_df
    
    if latest_df.empty:
        return defaults
    
    # 分类解析比分
    normal_scores = {}      # (home, away) -> odds
    special_other = {'胜其它': 0.0, '平其它': 0.0, '负其它': 0.0}
    
    for _, row in latest_df.iterrows():
        score_str = str(row['score']).strip()
        odds_val = float(row['odds'])
        
        if score_str in special_other:
            special_other[score_str] = odds_val
        else:
            parsed = parse_score(score_str)
            if parsed is not None:
                hg, ag = parsed
                normal_scores[(hg, ag)] = odds_val
    
    # 计算正常比分的总概率和
    total_normal_prob = sum(normal_scores.values())
    
    # 处理"胜其它"：将概率按已知主胜比分概率比例分配
    for label, prob in special_other.items():
        if prob <= 0:
            continue
        
        if label == '胜其它':
            # 找出所有主胜比分
            home_win_scores = {k: v for k, v in normal_scores.items() if k[0] > k[1]}
            if home_win_scores:
                home_win_total = sum(home_win_scores.values())
                if home_win_total > 0:
                    for k in home_win_scores:
                        normal_scores[k] = normal_scores.get(k, 0) + prob * (home_win_scores[k] / home_win_total)
            else:
                # 无已知主胜比分，将概率均分给常见主胜比分
                common_home_wins = [(1, 0), (2, 0), (2, 1), (3, 0), (3, 1)]
                each = prob / len(common_home_wins)
                for k in common_home_wins:
                    normal_scores[k] = normal_scores.get(k, 0) + each
        
        elif label == '平其它':
            draw_scores = {k: v for k, v in normal_scores.items() if k[0] == k[1]}
            if draw_scores:
                draw_total = sum(draw_scores.values())
                if draw_total > 0:
                    for k in draw_scores:
                        normal_scores[k] = normal_scores.get(k, 0) + prob * (draw_scores[k] / draw_total)
            else:
                common_draws = [(0, 0), (1, 1), (2, 2)]
                each = prob / len(common_draws)
                for k in common_draws:
                    normal_scores[k] = normal_scores.get(k, 0) + each
        
        elif label == '负其它':
            away_win_scores = {k: v for k, v in normal_scores.items() if k[0] < k[1]}
            if away_win_scores:
                away_win_total = sum(away_win_scores.values())
                if away_win_total > 0:
                    for k in away_win_scores:
                        normal_scores[k] = normal_scores.get(k, 0) + prob * (away_win_scores[k] / away_win_total)
            else:
                common_away_wins = [(0, 1), (0, 2), (1, 2), (0, 3), (1, 3)]
                each = prob / len(common_away_wins)
                for k in common_away_wins:
                    normal_scores[k] = normal_scores.get(k, 0) + each
    
    # 概率归一化
    total_odds = sum(normal_scores.values())
    if total_odds <= 0:
        return defaults
    
    score_probs = {k: v / total_odds for k, v in normal_scores.items()}
    
    # --- 计算8维特征 ---
    
    # 1. 最可能比分的概率
    score_mode_prob = max(score_probs.values()) if score_probs else 0.0
    
    # 2. 比分分布熵
    entropy = -sum(p * np.log(p + 1e-10) for p in score_probs.values())
    score_entropy = float(np.clip(entropy, 0, 5.0))
    
    # 3-5. 胜平负概率
    home_win_prob = sum(p for (hg, ag), p in score_probs.items() if hg > ag)
    draw_prob = sum(p for (hg, ag), p in score_probs.items() if hg == ag)
    away_win_prob = sum(p for (hg, ag), p in score_probs.items() if hg < ag)
    
    # 6. 总进球>2.5概率
    over_25_prob = sum(p for (hg, ag), p in score_probs.items() if hg + ag > 2)
    
    # 7. 比分加权预期总进球
    expected_goals = sum((hg + ag) * p for (hg, ag), p in score_probs.items())
    
    # 8. 前3最可能比分概率集中度
    sorted_probs = sorted(score_probs.values(), reverse=True)
    top3_concentration = sum(sorted_probs[:3]) if len(sorted_probs) >= 3 else sum(sorted_probs)
    
    return {
        'score_mode_prob': round(float(score_mode_prob), 6),
        'score_entropy': round(float(score_entropy), 6),
        'score_home_win_prob': round(float(home_win_prob), 6),
        'score_draw_prob': round(float(draw_prob), 6),
        'score_away_win_prob': round(float(away_win_prob), 6),
        'score_over_25_prob': round(float(over_25_prob), 6),
        'score_expected_goals': round(float(expected_goals), 6),
        'score_top3_concentration': round(float(top3_concentration), 6),
    }


def _parse_scores_vectorized(score_series):
    """向量化解析比分：对每个唯一比分字符串只调用一次 parse_score。

    Returns:
        dict: {原始值 -> ('SP', 特殊标签) | ('N', hg, ag) | ('D',)}
        下游据此一次性映射全表，替代逐行 parse_score 的 O(n) 字符串解析。
    """
    uniq = score_series.unique()
    pmap = {}
    for u in uniq:
        if isinstance(u, str):
            st = u.strip()
            if st in ('胜其它', '平其它', '负其它'):
                pmap[u] = ('SP', st)
                continue
            r = parse_score(st)
            if r is not None:
                pmap[u] = ('N', float(r[0]), float(r[1]))
                continue
        pmap[u] = ('D',)
    return pmap


def _compute_score_features_parsed(hg, ag, sp, odds) -> Dict[str, float]:
    """从已解析的比分数组（最新时间戳快照）计算 8 维特征。

    与原 compute_score_features_from_df 完全等价，仅输入由（已解析的）numpy
    数组替代 iterrows + parse_score，消除逐行字符串解析与 iterrows 开销。
    """
    defaults = {
        'score_mode_prob': 0.0,
        'score_entropy': 0.0,
        'score_home_win_prob': 0.0,
        'score_draw_prob': 0.0,
        'score_away_win_prob': 0.0,
        'score_over_25_prob': 0.0,
        'score_expected_goals': 0.0,
        'score_top3_concentration': 0.0,
    }
    n = hg.shape[0]
    if n == 0:
        return defaults

    normal_scores = {}
    special_other = {'胜其它': 0.0, '平其它': 0.0, '负其它': 0.0}
    for i in range(n):
        label = sp[i]
        if label is not None:
            special_other[label] = float(odds[i])
        elif not np.isnan(hg[i]) and not np.isnan(ag[i]):
            normal_scores[(int(hg[i]), int(ag[i]))] = float(odds[i])

    # 处理特殊标签：概率比例分配法（与原版一致）
    for label, prob in special_other.items():
        if prob <= 0:
            continue
        if label == '胜其它':
            home_win_scores = {k: v for k, v in normal_scores.items() if k[0] > k[1]}
            if home_win_scores:
                home_win_total = sum(home_win_scores.values())
                if home_win_total > 0:
                    for k in home_win_scores:
                        normal_scores[k] = normal_scores.get(k, 0) + prob * (home_win_scores[k] / home_win_total)
            else:
                common_home_wins = [(1, 0), (2, 0), (2, 1), (3, 0), (3, 1)]
                each = prob / len(common_home_wins)
                for k in common_home_wins:
                    normal_scores[k] = normal_scores.get(k, 0) + each
        elif label == '平其它':
            draw_scores = {k: v for k, v in normal_scores.items() if k[0] == k[1]}
            if draw_scores:
                draw_total = sum(draw_scores.values())
                if draw_total > 0:
                    for k in draw_scores:
                        normal_scores[k] = normal_scores.get(k, 0) + prob * (draw_scores[k] / draw_total)
            else:
                common_draws = [(0, 0), (1, 1), (2, 2)]
                each = prob / len(common_draws)
                for k in common_draws:
                    normal_scores[k] = normal_scores.get(k, 0) + each
        elif label == '负其它':
            away_win_scores = {k: v for k, v in normal_scores.items() if k[0] < k[1]}
            if away_win_scores:
                away_win_total = sum(away_win_scores.values())
                if away_win_total > 0:
                    for k in away_win_scores:
                        normal_scores[k] = normal_scores.get(k, 0) + prob * (away_win_scores[k] / away_win_total)
            else:
                common_away_wins = [(0, 1), (0, 2), (1, 2), (0, 3), (1, 3)]
                each = prob / len(common_away_wins)
                for k in common_away_wins:
                    normal_scores[k] = normal_scores.get(k, 0) + each

    total_odds = sum(normal_scores.values())
    if total_odds <= 0:
        return defaults

    score_probs = {k: v / total_odds for k, v in normal_scores.items()}

    score_mode_prob = max(score_probs.values()) if score_probs else 0.0
    entropy = -sum(p * np.log(p + 1e-10) for p in score_probs.values())
    score_entropy = float(np.clip(entropy, 0, 5.0))
    home_win_prob = sum(p for (hg_, ag_), p in score_probs.items() if hg_ > ag_)
    draw_prob = sum(p for (hg_, ag_), p in score_probs.items() if hg_ == ag_)
    away_win_prob = sum(p for (hg_, ag_), p in score_probs.items() if hg_ < ag_)
    over_25_prob = sum(p for (hg_, ag_), p in score_probs.items() if hg_ + ag_ > 2)
    expected_goals = sum((hg_ + ag_) * p for (hg_, ag_), p in score_probs.items())
    sorted_probs = sorted(score_probs.values(), reverse=True)
    top3_concentration = sum(sorted_probs[:3]) if len(sorted_probs) >= 3 else sum(sorted_probs)

    return {
        'score_mode_prob': round(float(score_mode_prob), 6),
        'score_entropy': round(float(score_entropy), 6),
        'score_home_win_prob': round(float(home_win_prob), 6),
        'score_draw_prob': round(float(draw_prob), 6),
        'score_away_win_prob': round(float(away_win_prob), 6),
        'score_over_25_prob': round(float(over_25_prob), 6),
        'score_expected_goals': round(float(expected_goals), 6),
        'score_top3_concentration': round(float(top3_concentration), 6),
    }


def build_score_features_legacy(df: pd.DataFrame, odds_conn=None) -> pd.DataFrame:
    """构建比分赔率特征（8维）【旧版，保留用于回退验证】

    对齐策略（P1-9）：score_history 的 match_id 主要为中文格式，matches 表
    16/17~22/23 为中文格式、23/24 起为英文格式。统一走
    feature_utils.build_match_alignment 三通道对齐后按 matches_match_id
    分组取最新快照，再映射回 df['match_id']。

    参数:
        df: 比赛数据 DataFrame（需含 match_id 列）
        odds_conn: odds.db 连接（可选）

    返回:
        pd.DataFrame: 8维比分赔率特征矩阵，索引与 df 对齐
    """
    if odds_conn is None:
        odds_conn = load_score_conn()
        own_conn = True
    else:
        own_conn = False

    try:
        # 延迟导入避免模块加载顺序问题
        from feature_utils import build_match_alignment

        # 1. 全量加载 score_history（过滤 2026-07 合成时间戳）
        raw = read_sql(
            "SELECT match_id, timestamp, score, odds FROM score_history "
            "WHERE timestamp < ?",
            odds_conn, params=(MAX_VALID_TIMESTAMP,))

        # 2. 三通道对齐：history.match_id → matches.match_id
        alignment = build_match_alignment(odds_conn, "score_history")
        raw['mmid'] = raw['match_id'].map(alignment)
        n_history = raw['match_id'].nunique()
        raw = raw.dropna(subset=['mmid'])
        n_aligned = raw['mmid'].nunique()

        # 3. 按 matches_match_id 分组，取每组最新时间戳快照计算特征
        raw['timestamp'] = pd.to_datetime(raw['timestamp'], format='mixed',
                                          errors='coerce')
        raw = raw.dropna(subset=['timestamp']).sort_values('timestamp')

        records = {}
        for mmid, g in raw.groupby('mmid', sort=False):
            records[mmid] = compute_score_features_from_df(g)
    finally:
        if own_conn:
            odds_conn.close()

    feat_df = pd.DataFrame.from_dict(records, orient='index')
    matched_count = int(df['match_id'].isin(feat_df.index).sum()) if len(feat_df) else 0

    # 4. 映射回 df（未命中行填 0.0 = 无数据，与原版行为一致）
    feat_df = feat_df.reindex(pd.Index(df['match_id'].values))
    feat_df.index = df.index

    # 保证 8 列完整且顺序一致（feat_df 可能为空）
    score_cols = get_score_feature_names()
    for c in score_cols:
        if c not in feat_df.columns:
            feat_df[c] = 0.0
    feat_df = feat_df[score_cols].fillna(0.0)

    # 5. 异常值处理
    from feature_utils import winsorize_series as _ws
    for col in feat_df.columns:
        feat_df[col] = _ws(feat_df[col], lower_percentile=1, upper_percentile=99)

    print(f"   [T-003.1] score_history 对齐: {n_aligned}/{n_history} 场，"
          f"特征覆盖: {matched_count}/{len(df)} ({matched_count / max(len(df), 1) * 100:.1f}%)")

    return feat_df


def build_score_features(df: pd.DataFrame, odds_conn=None) -> pd.DataFrame:
    """构建比分赔率特征（8维）【向量化版，C-20260830-009】

    与原 build_score_features_legacy 完全等价，优化点：
    1. 比分字符串按唯一值只解析一次（_parse_scores_vectorized），
       替换逐行 parse_score 的 O(n) 字符串解析；
    2. 每场「最新时间戳快照」用 groupby.transform('max') 向量化筛选，
       替换逐组 max + 布尔过滤；
    3. 分组特征计算用 numpy 数组切片，替换 iterrows 循环。
    """
    if odds_conn is None:
        odds_conn = load_score_conn()
        own_conn = True
    else:
        own_conn = False

    try:
        from feature_utils import build_match_alignment

        # 1. 全量加载 score_history（过滤 2026-07 合成时间戳）
        raw = read_sql(
            "SELECT match_id, timestamp, score, odds FROM score_history "
            "WHERE timestamp < ?",
            odds_conn, params=(MAX_VALID_TIMESTAMP,))

        # 2. 三通道对齐：history.match_id → matches.match_id
        alignment = build_match_alignment(odds_conn, "score_history")
        raw['mmid'] = raw['match_id'].map(alignment)
        n_history = raw['match_id'].nunique()
        raw = raw.dropna(subset=['mmid'])
        n_aligned = raw['mmid'].nunique()

        # 3. 时间戳正规化 + 每场最新时间戳快照（向量化筛选）
        raw['timestamp'] = pd.to_datetime(raw['timestamp'], format='mixed',
                                          errors='coerce')
        raw = raw.dropna(subset=['timestamp']).sort_values('timestamp')
        latest_ts = raw.groupby('mmid')['timestamp'].transform('max')
        snap = raw[raw['timestamp'] == latest_ts]

        # 4. 比分向量化解析（唯一值只解析一次）→ hg/ag/sp/odds 数组
        pmap = _parse_scores_vectorized(snap['score'])
        parsed = snap['score'].map(pmap)
        vals = parsed.to_numpy()
        n = len(vals)
        hg = np.empty(n, dtype=np.float64)
        ag = np.empty(n, dtype=np.float64)
        sp = np.empty(n, dtype=object)
        for i, v in enumerate(vals):
            if isinstance(v, tuple) and v[0] == 'N':
                hg[i] = v[1]; ag[i] = v[2]; sp[i] = None
            elif isinstance(v, tuple) and v[0] == 'SP':
                hg[i] = np.nan; ag[i] = np.nan; sp[i] = v[1]
            else:
                hg[i] = np.nan; ag[i] = np.nan; sp[i] = None
        odds_arr = snap['odds'].to_numpy(dtype=np.float64)

        # 5. 分组计算（numpy 切片，无 iterrows）
        records = {}
        for mmid, idx in snap.groupby('mmid', sort=False).indices.items():
            records[mmid] = _compute_score_features_parsed(
                hg[idx], ag[idx], sp[idx], odds_arr[idx])
    finally:
        if own_conn:
            odds_conn.close()

    feat_df = pd.DataFrame.from_dict(records, orient='index')
    matched_count = int(df['match_id'].isin(feat_df.index).sum()) if len(feat_df) else 0

    # 6. 映射回 df（未命中行填 0.0 = 无数据，与原版行为一致）
    feat_df = feat_df.reindex(pd.Index(df['match_id'].values))
    feat_df.index = df.index

    # 保证 8 列完整且顺序一致（feat_df 可能为空）
    score_cols = get_score_feature_names()
    for c in score_cols:
        if c not in feat_df.columns:
            feat_df[c] = 0.0
    feat_df = feat_df[score_cols].fillna(0.0)

    # 7. 异常值处理
    from feature_utils import winsorize_series as _ws
    for col in feat_df.columns:
        feat_df[col] = _ws(feat_df[col], lower_percentile=1, upper_percentile=99)

    print(f"   [T-003.1] score_history 对齐: {n_aligned}/{n_history} 场，"
          f"特征覆盖: {matched_count}/{len(df)} ({matched_count / max(len(df), 1) * 100:.1f}%)")

    return feat_df


def get_score_feature_names() -> List[str]:
    """返回所有比分赔率特征名列表"""
    return [
        'score_mode_prob',
        'score_entropy',
        'score_home_win_prob',
        'score_draw_prob',
        'score_away_win_prob',
        'score_over_25_prob',
        'score_expected_goals',
        'score_top3_concentration',
    ]


# ========================================
# 独立运行验证
# ========================================

if __name__ == '__main__':
    import sys
    import os
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from feature_utils import load_match_data_odds
    
    print("=" * 70)
    print("🧪 比分赔率特征模块验证")
    print("=" * 70)
    
    df = load_match_data_odds()
    print(f"  加载比赛数据: {len(df)} 场")
    
    print("\n  📊 构建比分赔率特征...")
    score_df = build_score_features(df)
    print(f"  比分赔率特征维度: {score_df.shape[1]}")
    print(f"  特征列表: {list(score_df.columns)}")
    
    print("\n  📊 比分赔率特征统计:")
    print(score_df.describe().round(4))
    
    # 检查非零率
    print("\n  📊 非零值比例:")
    for col in score_df.columns:
        non_zero = (score_df[col] != 0).sum()
        pct = non_zero / len(score_df) * 100
        print(f"    {col}: {non_zero}/{len(score_df)} ({pct:.1f}%)")
    
    print("\n✅ 比分赔率特征模块验证完成!")