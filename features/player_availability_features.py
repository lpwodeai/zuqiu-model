# -*- coding: utf-8 -*-
"""
球员可用性特征生成器（P0-3）

问题背景：
  sofascore_pre_match_features.py 的 aggregate_team_history() 对所有出场球员
  一视同仁聚合，不区分首发/替补、核心/边缘，升班马返回全 0 特征。

解决方案：
  1. 预计首发 11 人加权评分/xG（替代全队历史平均）
  2. 核心球员依赖度（xG 贡献集中度）
  3. 伤病/停赛扣减因子（基于出场连续性）
  4. 疲劳状态（近 7/14 天出场分钟数）
  5. 阵容稳定性（首发变化率 / 阵型一致性）

生成 12 维 × 主客 = 24 维特征，输出 DataFrame 可直接拼接到现有特征矩阵。

数据流：
  odds.db.match_player_stats + match_lineups
  → 按球队+日期排序 → 预测首发+状态评估 → 球员级加权特征

用法：
  python features/player_availability_features.py                    # 全量生成
  python features/player_availability_features.py --n-recent 5        # 近 N 场窗口
"""
from __future__ import annotations

import argparse
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

# ==================== 路径常量 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
from db_utils import connect  # noqa: E402

DB_PATH = PROJECT_ROOT / "data" / "odds.db"

# ==================== 特征名称常量 ====================
FEATURE_KEYS = [
    # 首发强度 (3维)
    "pa_xi_rating",       # 预计首发平均评分
    "pa_xi_xg",           # 预计首发平均 xG/90
    "pa_xi_xa",           # 预计首发平均 xA/90
    # 核心依赖 (2维)
    "pa_core_xg_share",   # 核心球员（出场率>60%+评分前5）xG 占比
    "pa_core_dependency", # 核心球员依赖度（缺阵时攻击力下降程度）
    # 伤病/缺阵 (2维)
    "pa_availability",    # 球员可用性 (1.0=全勤, <1.0=有缺阵)
    "pa_missing_impact",  # 缺阵球员影响度（缺阵球员 xG 占全队比例）
    # 疲劳状态 (3维)
    "pa_fatigue_7d",      # 近 7 天平均出场分钟数
    "pa_fatigue_14d",     # 近 14 天平均出场分钟数
    "pa_rest_days",       # 距上一场比赛休息天数
    # 阵容稳定性 (2维)
    "pa_squad_stability", # 首发变化率（与上一场首发的 Jaccard 相似度）
    "pa_formation_std",   # 阵型评分标准差（球员间评分差异）
]


class PlayerAvailabilityFeatures:
    """球员可用性特征生成器。

    从 match_player_stats 和 match_lineups 表提取球员级数据，
    生成 12 维 × 主客 = 24 维球员可用性特征。
    """

    def __init__(self, db_path: Path = DB_PATH, backend: Optional[str] = None):
        self.db_path = db_path
        self.backend = backend  # None=用 DB_BACKEND 环境变量 / "sqlite" / "pg"
        self._conn: Optional[Any] = None
        self._team_stats: Optional[Dict[str, pd.DataFrame]] = None

    def _get_conn(self):
        if self._conn is None:
            self._conn = connect(self.backend, self.db_path)
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
        self._team_stats = None

    # ==================== 核心逻辑 ====================

    def preload_team_stats(self) -> None:
        """一次性载入全量球员统计并按队名分组到内存，替代逐场查库。

        原 compute_team_features 每调用一次对 match_player_stats 做一条带 JOIN 的
        查询（全表重建 18000 场 × 2 ≈ 3.6 万次 DB 查询，是全表重建主要耗时来源）。
        改为单条 SQL 全量载入 + 按 team 分组，之后各队仅在内存 DataFrame 上按
        match_date 过滤，复杂度从 O(场次 × DB查询) 降到内存 pandas 过滤。
        """
        if self._team_stats is not None:
            return
        conn = self._get_conn()
        player_query = """
        SELECT mps.team, mps.player_name, mps.position, mps.rating, mps.expected_goals,
               mps.expected_assists, mps.minutes_played, fmm.match_date,
               mps.is_starter AS is_starting
        FROM match_player_stats mps
        JOIN fbref_match_mapping fmm ON mps.fbref_match_id = fmm.fbref_match_id
        WHERE fmm.fbref_match_id IS NOT NULL
        """
        rows = conn.execute(player_query).fetchall()
        df = pd.DataFrame(
            [dict(r) for r in rows],
            columns=["team", "player_name", "position", "rating", "expected_goals",
                     "expected_assists", "minutes_played", "match_date", "is_starting"],
        )
        df["match_date"] = pd.to_datetime(df["match_date"])
        # 每队按日期降序，便于取 <=200 条近场（仅需该队子表，写入缓存避免重复载入）
        df = df.sort_values(["team", "match_date"], ascending=[True, False])
        self._team_stats = {
            team: g.drop(columns=["team"]).reset_index(drop=True)
            for team, g in df.groupby("team", sort=False)
        }

    def _prepare_recent(
        self,
        team_name: str,
        match_date: str,
        n_recent: int = 5,
        half_life_days: int = 21,
    ) -> Optional[Tuple[pd.DataFrame, pd.Series, pd.DataFrame]]:
        """预载缓存 + 内存过滤 + 计算球员级统计，返回 (df, recent_matches, player_stats)。

        无数据时返回 None（升班马/日期前无可出场记录）。
        供 compute_team_features 与 predict_xi_players 复用，避免推算逻辑漂移。
        """
        if self._team_stats is None:
            self.preload_team_stats()
        df_all = self._team_stats.get(team_name)
        if df_all is None or df_all.empty:
            return None

        match_date_dt = pd.to_datetime(match_date)
        df = df_all[df_all["match_date"] < match_date_dt].head(200).copy()
        if df.empty:
            return None

        # 时间衰减权重
        df["days_ago"] = (match_date_dt - df["match_date"]).dt.days
        df["weight"] = np.exp(-np.log(2) * df["days_ago"] / half_life_days)
        df["weight"] = df["weight"].clip(lower=0.1)

        # 近 N 场按比赛分组的球员集合
        recent_matches = (
            df.groupby("match_date")["player_name"]
            .apply(set)
            .sort_index(ascending=False)
            .head(n_recent)
        )
        if recent_matches.empty:
            return None

        player_stats = self._compute_player_stats(df, recent_matches)
        return df, recent_matches, player_stats

    def compute_team_features(
        self,
        team_name: str,
        match_date: str,
        n_recent: int = 5,
        half_life_days: int = 21,
    ) -> Dict[str, float]:
        """为指定球队在指定日期前计算 12 维球员可用性特征。

        Args:
            team_name: 标准队名（英文 fbref 口径，与 match_player_stats.team 一致）
            match_date: 比赛日期 (YYYY-MM-DD)
            n_recent: 近 N 场窗口
            half_life_days: 时间衰减半衰期

        Returns:
            dict: 12 维特征 (pa_xi_rating, pa_core_xg_share, ...)
        """
        prepared = self._prepare_recent(team_name, match_date, n_recent, half_life_days)
        if prepared is None:
            return self._empty_features()
        df, recent_matches, player_stats = prepared
        return self._build_features(player_stats, df, recent_matches, n_recent, match_date)

    def predict_xi_players(
        self,
        team_name: str,
        match_date: str,
        n_recent: int = 5,
        half_life_days: int = 21,
    ) -> List[str]:
        """推算指定球队在指定日期前的预计首发 11 人名单（player_name 列表）。

        公开版 _predict_starting_xi，供「推算首发准确率复盘」及报告溯源复用。
        规则与 compute_team_features 内部首发推算完全一致（1 门将 + 10 非门将，
        按首发率+评分优先级排序），保证复盘结论可作为特征层的可信度基线。
        """
        prepared = self._prepare_recent(team_name, match_date, n_recent, half_life_days)
        if prepared is None:
            return []
        _, recent_matches, player_stats = prepared
        xi = self._predict_starting_xi(player_stats, recent_matches, n_recent)
        return list(xi["player_name"])

    def _compute_player_stats(
        self, df: pd.DataFrame, recent_matches: pd.Series
    ) -> pd.DataFrame:
        """计算每个球员的加权统计（出场率、评分、xG、分钟数）。

        向量化：原实现逐 player 在 df 上过滤（O(球员²)），在全表重建 3.6 万次调用
        下是主要 CPU 成本。改为先算加权列再用 groupby 一次聚合（O(球员)）。
        """
        if df.empty:
            return pd.DataFrame(
                columns=["player_name", "position", "appearances", "avg_rating",
                         "avg_xg", "avg_xa", "avg_minutes", "is_starting_rate",
                         "total_weight"])
        g = df.copy()
        g["_w"] = g["weight"].fillna(0.0)
        g["_w_rating"] = g["rating"].fillna(6.0) * g["_w"]
        g["_w_xg"] = g["expected_goals"].fillna(0.0) * g["_w"]
        g["_w_xa"] = g["expected_assists"].fillna(0.0) * g["_w"]
        g["_w_min"] = g["minutes_played"].fillna(0.0) * g["_w"]
        # is_starting_rate 沿用原语义：is_starting 未加权均值（首发次数/出场次数）
        g["_start_sum"] = g["is_starting"].fillna(0)

        agg = g.groupby("player_name", sort=False).agg(
            _w=("_w", "sum"),
            _w_rating=("_w_rating", "sum"),
            _w_xg=("_w_xg", "sum"),
            _w_xa=("_w_xa", "sum"),
            _w_min=("_w_min", "sum"),
            _start_sum=("_start_sum", "sum"),
            _app=("_w", "size"),
        ).reset_index()
        # 位置以出场权重最高的那行作为代表（原实现用 mode 近似，此处取最大权重行）
        top_pos = g.loc[g.groupby("player_name")["_w"].idxmax(), ["player_name", "position"]]
        agg = agg.merge(top_pos, on="player_name", how="left")

        total_weight = agg["_w"].replace(0, 1.0)
        out = pd.DataFrame({
            "player_name": agg["player_name"],
            "position": agg["position"].fillna("M"),
            "appearances": agg["_app"],
            "avg_rating": agg["_w_rating"] / total_weight,
            "avg_xg": agg["_w_xg"] / total_weight,
            "avg_xa": agg["_w_xa"] / total_weight,
            "avg_minutes": agg["_w_min"] / total_weight,
            "is_starting_rate": agg["_start_sum"] / agg["_app"].replace(0, 1),
            "total_weight": agg["_w"],
        })
        return out

    def _predict_starting_xi(
        self, player_stats: pd.DataFrame, recent_matches: pd.Series, n_recent: int
    ) -> pd.DataFrame:
        """预测首发 11 人。

        规则：
        1. 近 N 场首发率 > 60% 的球员优先
        2. 按加权评分排序
        3. 至少 1 名门将
        """
        # 门将单独处理
        gk = player_stats[player_stats["position"].str.upper() == "G"].copy()
        gk = gk.sort_values("avg_rating", ascending=False)

        # 非门将
        outfield = player_stats[player_stats["position"].str.upper() != "G"].copy()
        # 首发率高的优先，评分次之
        outfield["priority"] = outfield["is_starting_rate"] * 0.6 + outfield["avg_rating"] / 10.0 * 0.4
        outfield = outfield.sort_values("priority", ascending=False)

        # 组合：1 门将 + 10 非门将
        xi = pd.concat([gk.head(1), outfield.head(10)])
        # 如果不够 11 人，用评分最高的补充
        if len(xi) < 11:
            remaining = player_stats[~player_stats["player_name"].isin(xi["player_name"])]
            remaining = remaining.sort_values("avg_rating", ascending=False)
            xi = pd.concat([xi, remaining.head(11 - len(xi))])

        return xi.head(11)

    def _build_features(
        self,
        player_stats: pd.DataFrame,
        df: pd.DataFrame,
        recent_matches: pd.Series,
        n_recent: int,
        match_date: str,
    ) -> Dict[str, float]:
        """从球员统计中构建 12 维特征。"""
        features = {}

        # --- 首发强度 (3维) ---
        xi = self._predict_starting_xi(player_stats, recent_matches, n_recent)

        features["pa_xi_rating"] = round(float(xi["avg_rating"].mean()), 4)
        features["pa_xi_xg"] = round(float(xi["avg_xg"].sum()), 4)  # 全队 xG 和
        features["pa_xi_xa"] = round(float(xi["avg_xa"].sum()), 4)  # 全队 xA 和

        # --- 核心依赖 (2维) ---
        core_players = player_stats[
            (player_stats["is_starting_rate"] > 0.6)
            & (player_stats["avg_rating"] >= player_stats["avg_rating"].quantile(0.7))
        ]
        total_xg = player_stats["avg_xg"].sum() or 0.01
        core_xg = core_players["avg_xg"].sum()
        features["pa_core_xg_share"] = round(float(core_xg / total_xg), 4)
        # 依赖度 = 核心球员人数 / 全队人数，越高越依赖少数人
        features["pa_core_dependency"] = round(
            float(len(core_players) / max(len(player_stats), 1)), 4
        )

        # --- 伤病/缺阵 (2维) ---
        # 检查最近一场比赛是否有核心球员缺阵
        if len(recent_matches) > 0:
            latest_match_players = recent_matches.iloc[0]  # 最近一场的球员集合
            core_names = set(core_players["player_name"])
            missing_core = core_names - latest_match_players
            missing_core_xg = core_players[
                core_players["player_name"].isin(missing_core)
            ]["avg_xg"].sum()

            availability = 1.0 - min(0.2, len(missing_core) / max(len(core_names), 1))
            missing_impact = missing_core_xg / total_xg
        else:
            availability = 1.0
            missing_impact = 0.0

        features["pa_availability"] = round(float(availability), 4)
        features["pa_missing_impact"] = round(float(missing_impact), 4)

        # --- 疲劳状态 (3维) ---
        recent_7d = df[df["days_ago"] <= 7]
        recent_14d = df[df["days_ago"] <= 14]

        # 近 7/14 天平均出场分钟数（按球员去重后平均）
        for period_days, period_df, key in [
            (7, recent_7d, "pa_fatigue_7d"),
            (14, recent_14d, "pa_fatigue_14d"),
        ]:
            if not period_df.empty:
                avg_min = period_df.groupby("player_name")["minutes_played"].mean().mean()
                # NaN 防护：该时段有出场记录但 minutes_played 全缺失时
                # groupby.mean().mean() 返回 NaN（C-20260907-004：避免 NaN 落库）
                if pd.isna(avg_min):
                    features[key] = 0.0
                else:
                    features[key] = round(float(avg_min), 1)
            else:
                features[key] = 0.0

        # 休息天数（距上一场比赛）
        if not df.empty:
            last_match_date = df["match_date"].max()
            rest_days = (pd.to_datetime(match_date) - last_match_date).days if pd.notna(last_match_date) else 7
            features["pa_rest_days"] = float(max(0, rest_days))
        else:
            features["pa_rest_days"] = 7.0

        # --- 阵容稳定性 (2维) ---
        if len(recent_matches) >= 2:
            # Jaccard 相似度：最近两场首发球员的交集/并集
            prev_xi = recent_matches.iloc[1] if len(recent_matches) > 1 else set()
            curr_xi = recent_matches.iloc[0]
            if prev_xi and curr_xi:
                jaccard = len(prev_xi & curr_xi) / max(len(prev_xi | curr_xi), 1)
                features["pa_squad_stability"] = round(float(jaccard), 4)
            else:
                features["pa_squad_stability"] = 0.5
        else:
            features["pa_squad_stability"] = 0.5

        # 阵型评分标准差
        features["pa_formation_std"] = round(
            float(player_stats["avg_rating"].std() if len(player_stats) > 1 else 0.0), 4
        )

        return features

    # ==================== 批量生成 ====================

    def generate_all(
        self,
        n_recent: int = 5,
        half_life_days: int = 21,
        progress_every: int = 50,
    ) -> pd.DataFrame:
        """批量生成所有比赛的球员可用性特征。

        Returns:
            DataFrame，每行一场比赛，列包含 event_id + 24 维特征（12 维 × 主客）
        """
        conn = self._get_conn()

        matches_query = """
        SELECT fbref_match_id AS event_id,
               home_team_fbref AS home_team,
               away_team_fbref AS away_team,
               match_date, league
        FROM fbref_match_mapping
        WHERE fbref_match_url LIKE '%sofascore%'
        ORDER BY match_date DESC, fbref_match_id
        """
        match_rows = conn.execute(matches_query).fetchall()
        total = len(match_rows)

        rows = []
        for i, row in enumerate(match_rows):
            if (i + 1) % progress_every == 0 or i == 0:
                pct = (i + 1) / total * 100
                print(f"[PA特征] 进度 {i+1}/{total} ({pct:.1f}%) | "
                      f"{row['home_team']} vs {row['away_team']}")

            home_features = self.compute_team_features(
                row["home_team"], row["match_date"], n_recent, half_life_days
            )
            away_features = self.compute_team_features(
                row["away_team"], row["match_date"], n_recent, half_life_days
            )

            result = {
                "event_id": row["event_id"],
                "match_date": row["match_date"],
                "league": row["league"],
                "home_team": row["home_team"],
                "away_team": row["away_team"],
            }
            # 主队特征
            for k in FEATURE_KEYS:
                result[f"{k}_home"] = home_features.get(k, 0.0)
            # 客队特征
            for k in FEATURE_KEYS:
                result[f"{k}_away"] = away_features.get(k, 0.0)

            rows.append(result)

        print(f"[PA特征] 完成: {len(rows)} 场比赛")
        return pd.DataFrame(rows)

    # ==================== 工具方法 ====================

    @staticmethod
    def _empty_features() -> Dict[str, float]:
        """返回全 0 特征（升班马/无数据时）。"""
        return {k: 0.0 for k in FEATURE_KEYS}

    @staticmethod
    def feature_keys() -> List[str]:
        """返回特征键列表（含主客后缀）。"""
        keys = []
        for k in FEATURE_KEYS:
            keys.append(f"{k}_home")
            keys.append(f"{k}_away")
        return keys


# ==================== CLI 入口 ====================

def main():
    parser = argparse.ArgumentParser(description="球员可用性特征生成器（P0-3）")
    parser.add_argument("--n-recent", type=int, default=5, help="近 N 场窗口")
    parser.add_argument("--half-life", type=int, default=21, help="时间衰减半衰期（天）")
    parser.add_argument("--output", type=str, default=None, help="输出 CSV 路径")
    parser.add_argument("--db-path", type=str, default=None, help="数据库路径")
    parser.add_argument("--backend", type=str, default=None,
                        help="数据库后端: sqlite / pg (默认 DB_BACKEND 环境变量)")
    args = parser.parse_args()

    db_path = Path(args.db_path) if args.db_path else DB_PATH
    generator = PlayerAvailabilityFeatures(db_path=db_path, backend=args.backend)

    try:
        df = generator.generate_all(
            n_recent=args.n_recent,
            half_life_days=args.half_life,
        )

        if args.output:
            output_path = Path(args.output)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(output_path, index=False, encoding="utf-8-sig")
            print(f"已保存到: {output_path}")
        else:
            print(f"\n生成 {len(df)} 行 × {len(df.columns)} 列特征")
            print(f"特征维度: {len(PlayerAvailabilityFeatures.feature_keys())} 维 (12 × 主客)")
            print(f"非零率: {(df[PlayerAvailabilityFeatures.feature_keys()] != 0).mean().mean():.1%}")
            print("\n样本:")
            print(df.head(3).to_string())
    finally:
        generator.close()


if __name__ == "__main__":
    main()