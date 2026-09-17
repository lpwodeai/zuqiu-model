# -*- coding: utf-8 -*-
"""相似案例检索模块（阶段 3 / T-009）。

基于 understat forecast_w/d/l 三维概率向量，用欧氏距离在历史比赛中
检索相似案例，输出相似案例的实际 WDL 分布。离线回测验证是否有增益。

设计原则（v2.0 方案 §四 第 11 条）：
- 先离线回测，有增益再进 Stacking
- 意甲子集可行性验证
- 不依赖 prediction_core、不修改现有预测链路

文档：docs/模型问题诊断与优化方案_v2.0.md §四 第 11 条
"""
import sqlite3
import json
import math
import logging
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Tuple, Dict

logger = logging.getLogger(__name__)

__all__ = [
    "SimilarCase",
    "SimilarCaseResult",
    "SimilarCaseRetriever",
]

BASE_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = BASE_DIR / "data" / "odds.db"


@dataclass
class SimilarCase:
    """单个相似案例"""
    match_id: str
    datetime: str
    home_team: str
    away_team: str
    home_goals: int
    away_goals: int
    actual_wdl: str            # "H" / "D" / "A"
    forecast: Tuple[float, float, float]  # (forecast_w, forecast_d, forecast_l)
    distance: float            # 欧氏距离


@dataclass
class SimilarCaseResult:
    """相似案例检索结果"""
    target_forecast: Tuple[float, float, float]
    k: int
    cases: List[SimilarCase] = field(default_factory=list)
    wdl_distribution: Dict[str, float] = field(default_factory=dict)  # {"H": 0.45, "D": 0.25, "A": 0.30}
    avg_home_goals: float = 0.0
    avg_away_goals: float = 0.0
    avg_distance: float = 0.0


class SimilarCaseRetriever:
    """相似案例检索器 — 基于欧氏距离的 K 近邻检索。

    特征空间：[forecast_w, forecast_d, forecast_l]（三维概率向量，和为 1）
    距离度量：欧氏距离
    数据来源：understat_match_team_stats 表
    """

    def __init__(self, db_path: Optional[Path] = None, league: str = "意甲"):
        self.db_path = db_path or ODDS_DB
        self.league = league
        self._data: List[dict] = []
        self._features: List[Tuple[float, float, float]] = []
        self._loaded = False

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path))
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn

    def load_data(self) -> int:
        """加载历史数据（forecast + 实际比分）。返回加载数据条数。"""
        conn = self._connect()
        try:
            c = conn.cursor()
            c.execute("""SELECT match_id, datetime, home_team, away_team,
                         home_goals, away_goals, forecast_w, forecast_d, forecast_l
                         FROM understat_match_team_stats
                         WHERE league=? AND forecast_w IS NOT NULL
                         AND forecast_d IS NOT NULL AND forecast_l IS NOT NULL
                         AND home_goals IS NOT NULL AND away_goals IS NOT NULL
                         ORDER BY datetime""", (self.league,))
            rows = c.fetchall()
        finally:
            conn.close()

        self._data = []
        self._features = []
        for r in rows:
            fw, fd, fl = float(r[6]), float(r[7]), float(r[8])
            hg, ag = int(r[4]), int(r[5])
            actual_wdl = "H" if hg > ag else ("D" if hg == ag else "A")
            self._data.append({
                "match_id": r[0],
                "datetime": r[1],
                "home_team": r[2],
                "away_team": r[3],
                "home_goals": hg,
                "away_goals": ag,
                "actual_wdl": actual_wdl,
                "forecast": (fw, fd, fl),
            })
            self._features.append((fw, fd, fl))

        self._loaded = True
        logger.info("SimilarCaseRetriever: 加载 %s %d 场历史数据", self.league, len(self._data))
        return len(self._data)

    @staticmethod
    def _euclidean_distance(a: Tuple[float, float, float],
                            b: Tuple[float, float, float]) -> float:
        """计算三维概率向量的欧氏距离。"""
        return math.sqrt(sum((a[i] - b[i]) ** 2 for i in range(3)))

    def retrieve(self, target_forecast: Tuple[float, float, float],
                 k: int = 20, exclude_match_id: Optional[str] = None) -> SimilarCaseResult:
        """检索相似案例。

        Args:
            target_forecast: 目标比赛的 (forecast_w, forecast_d, forecast_l)
            k: 返回的相似案例数
            exclude_match_id: 排除的比赛 ID（留出法回测时排除自身）

        Returns:
            SimilarCaseResult
        """
        if not self._loaded:
            self.load_data()

        # 计算所有比赛的欧氏距离
        distances = []
        for i, d in enumerate(self._data):
            if exclude_match_id and d["match_id"] == exclude_match_id:
                continue
            dist = self._euclidean_distance(target_forecast, self._features[i])
            distances.append((dist, i))

        # 取 K 近邻
        distances.sort(key=lambda x: x[0])
        top_k = distances[:k]

        # 构建 SimilarCase 列表
        cases = []
        for dist, idx in top_k:
            d = self._data[idx]
            cases.append(SimilarCase(
                match_id=d["match_id"],
                datetime=d["datetime"],
                home_team=d["home_team"],
                away_team=d["away_team"],
                home_goals=d["home_goals"],
                away_goals=d["away_goals"],
                actual_wdl=d["actual_wdl"],
                forecast=d["forecast"],
                distance=round(dist, 6),
            ))

        # 统计 WDL 分布
        result = SimilarCaseResult(
            target_forecast=target_forecast,
            k=len(cases),
            cases=cases,
        )

        if cases:
            wdl_count = {"H": 0, "D": 0, "A": 0}
            total_hg = 0
            total_ag = 0
            total_dist = 0.0
            for c in cases:
                wdl_count[c.actual_wdl] += 1
                total_hg += c.home_goals
                total_ag += c.away_goals
                total_dist += c.distance
            n = len(cases)
            result.wdl_distribution = {k_: v / n for k_, v in wdl_count.items()}
            result.avg_home_goals = round(total_hg / n, 2)
            result.avg_away_goals = round(total_ag / n, 2)
            result.avg_distance = round(total_dist / n, 6)

        return result

    def backtest(self, k: int = 20, min_season: str = "16/17") -> dict:
        """离线留出法回测 — 验证相似案例检索是否有增益。

        对每场比赛，用其余比赛的相似案例分布作为预测，与 forecast 预测对比。

        指标：
        - accuracy: argmax 预测 vs 实际结果
        - log_loss: 对数损失
        - brier_score: Brier 分数

        Returns:
            dict with 'similar' and 'forecast' metrics
        """
        if not self._loaded:
            self.load_data()

        # 按 datetime 排序，只用 min_season 之后的数据
        data = [d for d in self._data]

        # 逐场留出法
        sim_correct = 0
        fc_correct = 0
        sim_log_loss = 0.0
        fc_log_loss = 0.0
        sim_brier = 0.0
        fc_brier = 0.0
        total = 0

        eps = 1e-10
        for i, target in enumerate(data):
            # 用其余所有比赛检索相似案例
            result = self.retrieve(
                target_forecast=target["forecast"],
                k=k,
                exclude_match_id=target["match_id"],
            )

            if not result.cases:
                continue

            actual = target["actual_wdl"]
            actual_vec = {"H": 1.0, "D": 0.0, "A": 0.0}
            if actual == "D":
                actual_vec = {"H": 0.0, "D": 1.0, "A": 0.0}
            elif actual == "A":
                actual_vec = {"H": 0.0, "D": 0.0, "A": 1.0}

            # 相似案例预测
            sim_probs = result.wdl_distribution
            sim_pred = max(sim_probs, key=sim_probs.get) if sim_probs else "H"
            if sim_pred == actual:
                sim_correct += 1

            # forecast 预测
            fc_probs = {"H": target["forecast"][0], "D": target["forecast"][1], "A": target["forecast"][2]}
            fc_pred = max(fc_probs, key=fc_probs.get)
            if fc_pred == actual:
                fc_correct += 1

            # 对数损失
            for label in ["H", "D", "A"]:
                p_sim = max(min(sim_probs.get(label, 0), 1 - eps), eps)
                p_fc = max(min(fc_probs.get(label, 0), 1 - eps), eps)
                sim_log_loss += -actual_vec[label] * math.log(p_sim)
                fc_log_loss += -actual_vec[label] * math.log(p_fc)

                # Brier score
                sim_brier += (sim_probs.get(label, 0) - actual_vec[label]) ** 2
                fc_brier += (fc_probs.get(label, 0) - actual_vec[label]) ** 2

            total += 1

        if total == 0:
            return {"error": "no data for backtest"}

        metrics = {
            "league": self.league,
            "total_matches": total,
            "k": k,
            "similar": {
                "accuracy": round(sim_correct / total, 4),
                "log_loss": round(sim_log_loss / total, 4),
                "brier_score": round(sim_brier / total, 4),
            },
            "forecast": {
                "accuracy": round(fc_correct / total, 4),
                "log_loss": round(fc_log_loss / total, 4),
                "brier_score": round(fc_brier / total, 4),
            },
        }

        # 增益判断
        sim_acc = metrics["similar"]["accuracy"]
        fc_acc = metrics["forecast"]["accuracy"]
        metrics["gain"] = {
            "accuracy_delta": round(sim_acc - fc_acc, 4),
            "log_loss_delta": round(metrics["similar"]["log_loss"] - metrics["forecast"]["log_loss"], 4),
            "brier_delta": round(metrics["similar"]["brier_score"] - metrics["forecast"]["brier_score"], 4),
            "has_gain": sim_acc > fc_acc,
        }

        return metrics

    def generate_report(self, k: int = 20) -> str:
        """生成离线回测报告（Markdown 格式）。"""
        metrics = self.backtest(k=k)

        if "error" in metrics:
            return f"# 相似案例检索回测报告\n\n错误: {metrics['error']}\n"

        s = metrics["similar"]
        f = metrics["forecast"]
        g = metrics["gain"]

        lines = [
            "# 相似案例检索离线回测报告",
            "",
            f"> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"> 联赛: {metrics['league']}  |  样本量: {metrics['total_matches']}  |  K 近邻: {metrics['k']}",
            "",
            "## 一、回测方法",
            "",
            "- **特征空间**: understat forecast_w/d/l 三维概率向量",
            "- **距离度量**: 欧氏距离",
            "- **回测方式**: 留出法（每场比赛排除自身，从其余历史比赛中检索 K 近邻）",
            "- **预测方式**: 相似案例的实际 WDL 分布作为预测概率",
            "- **对比基线**: understat forecast 原始预测",
            "",
            "## 二、核心指标对比",
            "",
            "| 指标 | 相似案例检索 | Forecast 基线 | 差值 | 增益 |",
            "|:----:|:---:|:---:|:---:|:---:|",
            f"| 准确率 | {s['accuracy']:.4f} | {f['accuracy']:.4f} | {g['accuracy_delta']:+.4f} | {'✅ 有增益' if g['has_gain'] else '❌ 无增益'} |",
            f"| 对数损失 | {s['log_loss']:.4f} | {f['log_loss']:.4f} | {g['log_loss_delta']:+.4f} | {'✅' if g['log_loss_delta'] < 0 else '❌'} |",
            f"| Brier 分数 | {s['brier_score']:.4f} | {f['brier_score']:.4f} | {g['brier_delta']:+.4f} | {'✅' if g['brier_delta'] < 0 else '❌'} |",
            "",
            "## 三、结论与建议",
            "",
        ]

        if g["has_gain"]:
            lines.append(f"相似案例检索在 {metrics['league']} 子集上 **有增益**"
                         f"（准确率 +{g['accuracy_delta']:.4f}），可考虑进 Stacking 融合。")
        else:
            lines.append(f"相似案例检索在 {metrics['league']} 子集上 **无增益**"
                         f"（准确率 {g['accuracy_delta']:+.4f}），不建议进 Stacking。")
            lines.append("")
            lines.append("可能原因：")
            lines.append("- forecast_w/d/l 已是高质量赛前预测，相似案例检索的信息量不足")
            lines.append("- 三维概率向量维度过低，欧氏距离区分度有限")
            lines.append("- 建议扩展特征空间（如加入 Elo 差、近期战绩、xG 趋势等）")

        lines.append("")
        return "\n".join(lines)


def main():
    """命令行入口：运行意甲子集回测并输出报告。"""
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    retriever = SimilarCaseRetriever(league="意甲")
    count = retriever.load_data()
    print(f"加载意甲历史数据: {count} 场\n")

    # 回测
    report = retriever.generate_report(k=20)
    print(report)

    # 保存报告
    report_dir = BASE_DIR / "reports"
    report_dir.mkdir(exist_ok=True)
    report_path = report_dir / "similar_case_retriever_backtest.md"
    report_path.write_text(report, encoding="utf-8")
    print(f"\n报告已保存: {report_path}")


if __name__ == "__main__":
    main()
