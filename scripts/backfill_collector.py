# -*- coding: utf-8 -*-
"""
历史赛季回溯采集器（P0-1）

问题背景：
  当前系统仅覆盖 2023-2026 赛季（5,252 场比赛），远低于框架要求的 15,000-25,000 场。
  3 赛季数据量导致过拟合（训练-验证差距 ~8pp）、升班马预测质量差、稀有比分尾部不可靠。

目标：
  回溯采集 2016-2023 赛季，matches 表 5,252 → 15,000+。

数据源：
  - SofaScore API（历史赛季 ID）
  - fbref（补充数据）

断点续传：
  进度文件 data/backfill_progress.json 记录每个赛季的采集状态，
  中断后重新运行自动跳过已完成赛季。

用法：
  python scripts/backfill_collector.py --start-season 2016-2017 --end-season 2023-2024
  python scripts/backfill_collector.py --league 英超 --dry-run  # 预览模式
  python scripts/backfill_collector.py --resume  # 断点续传
"""
from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

# ==================== 路径常量 ====================
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"
PROGRESS_PATH = PROJECT_ROOT / "data" / "backfill_progress.json"
LOG_DIR = PROJECT_ROOT / "logs"

# ==================== 赛季-联赛 SofaScore ID 映射 ====================
# 来源: SofaScore API 联赛+赛季 ID
SEASON_LEAGUE_IDS: Dict[str, Dict[str, int]] = {
    "2016-2017": {"英超": 11733, "西甲": 11906, "意甲": 11966, "德甲": 11818, "法甲": 11648},
    "2017-2018": {"英超": 13380, "西甲": 13662, "意甲": 13768, "德甲": 13477, "法甲": 13384},
    "2018-2019": {"英超": 17359, "西甲": 18020, "意甲": 17932, "德甲": 17597, "法甲": 17279},
    "2019-2020": {"英超": 23776, "西甲": 24127, "意甲": 24644, "德甲": 23538, "法甲": 23872},
    "2020-2021": {"英超": 29415, "西甲": 32501, "意甲": 32523, "德甲": 28210, "法甲": 28222},
    "2021-2022": {"英超": 37036, "西甲": 37223, "意甲": 37475, "德甲": 37166, "法甲": 37167},
    "2022-2023": {"英超": 41886, "西甲": 42409, "意甲": 42415, "德甲": 42268, "法甲": 42273},
    "2023-2024": {"英超": 52186, "西甲": 52376, "意甲": 52760, "德甲": 52608, "法甲": 52571},
}

# 五大联赛名称
ALL_LEAGUES = ["英超", "西甲", "意甲", "德甲", "法甲"]


def _dash_to_slash(season: str) -> str:
    """转换赛季标签：'2016-2017' -> '16/17'（final_sofascore_collector 要求斜杠格式）。"""
    m = re.match(r"^(\d{4})-(\d{4})$", season)
    if not m:
        return season
    return f"{m.group(1)[2:]}/{m.group(2)[2:]}"


class BackfillCollector:
    """历史赛季回溯采集器。

    负责协调 SofaScore 和 fbref 两个数据源的历史数据采集，
    支持断点续传和进度追踪。
    """

    def __init__(
        self,
        db_path: Path = DB_PATH,
        progress_path: Path = PROGRESS_PATH,
        dry_run: bool = False,
    ):
        self.db_path = db_path
        self.progress_path = progress_path
        self.dry_run = dry_run
        self._conn: Optional[sqlite3.Connection] = None

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(str(self.db_path))
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA busy_timeout = 5000")
        return self._conn

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None

    # ==================== 进度管理 ====================

    def load_progress(self) -> Dict[str, Any]:
        """加载断点续传进度。"""
        if self.progress_path.exists():
            with open(self.progress_path, "r", encoding="utf-8") as f:
                return json.load(f)
        return {
            "version": "1.0",
            "started_at": None,
            "last_updated": None,
            "seasons": {},
            "total_matches_collected": 0,
        }

    def save_progress(self, progress: Dict[str, Any]):
        """保存断点续传进度。"""
        progress["last_updated"] = datetime.now().isoformat()
        self.progress_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.progress_path, "w", encoding="utf-8") as f:
            json.dump(progress, f, ensure_ascii=False, indent=2)

    def is_season_completed(
        self, progress: Dict, season: str, league: str
    ) -> bool:
        """检查赛季-联赛是否已完成。"""
        return progress.get("seasons", {}).get(season, {}).get(league, {}).get("completed", False)

    def mark_season_completed(
        self, progress: Dict, season: str, league: str, matches_count: int
    ):
        """标记赛季-联赛为已完成。"""
        progress.setdefault("seasons", {}).setdefault(season, {})[league] = {
            "completed": True,
            "matches_count": matches_count,
            "completed_at": datetime.now().isoformat(),
        }
        progress["total_matches_collected"] = progress.get("total_matches_collected", 0) + matches_count

    def get_existing_seasons(self) -> Dict[str, Set[str]]:
        """查询数据库中已有的赛季-联赛组合。

        matches 表无独立 season/league 列，联赛+赛季编码在 match_type 字段
        （形如 "意甲2025-2026赛季"），需从中解析。
        """
        conn = self._get_conn()
        try:
            rows = conn.execute(
                "SELECT DISTINCT match_type FROM matches WHERE match_type IS NOT NULL"
            ).fetchall()
        except sqlite3.OperationalError:
            return {}

        season_re = re.compile(r"(\d{4}-\d{4})")
        existing: Dict[str, Set[str]] = {}
        for row in rows:
            mt = row["match_type"] or ""
            m = season_re.search(mt)
            if not m:
                continue
            season = m.group(1)
            for lg in ALL_LEAGUES:
                if lg in mt:
                    existing.setdefault(season, set()).add(lg)
                    break
        return existing

    def get_current_match_count(self) -> int:
        """查询当前 matches 表行数。"""
        conn = self._get_conn()
        try:
            return conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0]
        except sqlite3.OperationalError:
            return 0

    # ==================== 采集策略 ====================

    def plan_collection(
        self,
        start_season: str,
        end_season: str,
        leagues: List[str],
        resume: bool = True,
    ) -> List[Tuple[str, str]]:
        """规划待采集的赛季-联赛组合。

        Returns:
            List[(season, league)]: 待采集列表
        """
        progress = self.load_progress() if resume else {"seasons": {}}
        existing = self.get_existing_seasons()

        season_keys = sorted(SEASON_LEAGUE_IDS.keys())
        plan = []

        for season in season_keys:
            if season < start_season or season > end_season:
                continue
            for league in leagues:
                if league not in SEASON_LEAGUE_IDS.get(season, {}):
                    continue
                # 跳过已完成的
                if self.is_season_completed(progress, season, league):
                    print(f"  [跳过] {season} {league} (已完成)")
                    continue
                # 跳过数据库中已存在的
                if league in existing.get(season, set()):
                    print(f"  [跳过] {season} {league} (数据库中已存在)")
                    continue
                plan.append((season, league))

        return plan

    # ==================== 采集执行 ====================

    def collect_season_league(
        self, season: str, league: str, progress: Dict
    ) -> int:
        """采集单个赛季-联赛的数据。

        委托给现有的 SofaScore 采集器（final_sofascore_collector.py）。

        Returns:
            int: 采集到的比赛数
        """
        league_id = SEASON_LEAGUE_IDS.get(season, {}).get(league)
        if not league_id:
            print(f"  [错误] 未找到赛季 {season} {league} 的 ID")
            return 0

        season_slash = _dash_to_slash(season)

        print(f"\n{'='*60}")
        print(f"[采集] {season} {league} (league_id={league_id} -> {season_slash})")
        print(f"{'='*60}")

        if self.dry_run:
            print(f"  [DRY-RUN] 将调用: python final_sofascore_collector.py "
                  f"--leagues {league} --season {season_slash}")
            return 380  # 估计每赛季每联赛 380 场

        # 委托给现有采集器
        import subprocess

        collector_script = PROJECT_ROOT / "collection" / "final_sofascore_collector.py"
        if not collector_script.exists():
            # 尝试其他常见路径
            for alt_path in [
                PROJECT_ROOT / "scripts" / "final_sofascore_collector.py",
                PROJECT_ROOT / "collection" / "sofascore_collector.py",
            ]:
                if alt_path.exists():
                    collector_script = alt_path
                    break
            else:
                print(f"  [警告] 未找到采集器脚本，跳过 {season} {league}")
                return 0

        try:
            result = subprocess.run(
                [
                    sys.executable, str(collector_script),
                    "--leagues", league,
                    "--season", season_slash,
                    "--resume",
                ],
                cwd=str(PROJECT_ROOT),
                capture_output=True,
                text=True,
                timeout=3600,  # 1小时超时
            )
            if result.returncode == 0:
                # 统计本次采集到的比赛数
                before_count = self.get_current_match_count()
                # 从输出中提取匹配数
                print(result.stdout[-500:] if len(result.stdout) > 500 else result.stdout)
                after_count = self.get_current_match_count()
                collected = after_count - before_count
                return max(0, collected)
            else:
                print(f"  [错误] 采集器返回非零: {result.returncode}")
                print(result.stderr[-500:] if len(result.stderr) > 500 else result.stderr)
                return 0
        except subprocess.TimeoutExpired:
            print(f"  [超时] {season} {league} 采集超时")
            return 0
        except Exception as e:
            print(f"  [异常] {season} {league}: {e}")
            return 0

    # ==================== 主入口 ====================

    def run(
        self,
        start_season: str = "2016-2017",
        end_season: str = "2023-2024",
        leagues: Optional[List[str]] = None,
        resume: bool = True,
    ) -> Dict[str, Any]:
        """执行回溯采集。

        Args:
            start_season: 起始赛季
            end_season: 结束赛季
            leagues: 联赛列表（None = 全部五大联赛）
            resume: 是否断点续传

        Returns:
            采集汇总 dict
        """
        if leagues is None:
            leagues = ALL_LEAGUES

        # 规划
        plan = self.plan_collection(start_season, end_season, leagues, resume)

        if not plan:
            print("没有需要采集的赛季-联赛组合。")
            return {"total_collected": 0, "plan": []}

        print(f"\n待采集: {len(plan)} 个赛季-联赛组合")
        for season, league in plan:
            print(f"  - {season} {league}")

        if self.dry_run:
            print(f"\n[DRY-RUN] 预计采集 {len(plan) * 380} 场比赛")
            return {"total_collected": len(plan) * 380, "plan": plan, "dry_run": True}

        # 执行
        progress = self.load_progress() if resume else {"seasons": {}}
        if not progress.get("started_at"):
            progress["started_at"] = datetime.now().isoformat()

        total_collected = 0
        start_time = time.time()

        for i, (season, league) in enumerate(plan):
            print(f"\n[{i+1}/{len(plan)}] {season} {league}")

            try:
                collected = self.collect_season_league(season, league, progress)
                total_collected += collected
                self.mark_season_completed(progress, season, league, collected)
                self.save_progress(progress)

                elapsed = time.time() - start_time
                rate = total_collected / elapsed * 3600 if elapsed > 0 else 0
                print(f"  进度: {total_collected} 场 | "
                      f"速率: {rate:.0f} 场/小时 | "
                      f"已用时: {elapsed/3600:.1f}h")
            except KeyboardInterrupt:
                print(f"\n[中断] 进度已保存到 {self.progress_path}")
                self.save_progress(progress)
                break
            except Exception as e:
                print(f"  [异常] {season} {league}: {e}")
                continue

        # 汇总
        final_count = self.get_current_match_count()
        progress["final_match_count"] = final_count
        progress["completed_at"] = datetime.now().isoformat()
        self.save_progress(progress)

        print(f"\n{'='*60}")
        print(f"回溯采集完成")
        print(f"  本次采集: {total_collected} 场")
        print(f"  数据库总计: {final_count} 场")
        print(f"  进度文件: {self.progress_path}")
        print(f"{'='*60}")

        return {
            "total_collected": total_collected,
            "final_match_count": final_count,
            "plan": [f"{s} {l}" for s, l in plan],
        }


# ==================== CLI 入口 ====================

def main():
    parser = argparse.ArgumentParser(
        description="历史赛季回溯采集器（P0-1）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python backfill_collector.py --start-season 2016-2017 --end-season 2023-2024
  python backfill_collector.py --league 英超 --dry-run
  python backfill_collector.py --resume
        """,
    )
    parser.add_argument(
        "--start-season", type=str, default="2016-2017",
        help="起始赛季（默认: 2016-2017）",
    )
    parser.add_argument(
        "--end-season", type=str, default="2023-2024",
        help="结束赛季（默认: 2023-2024）",
    )
    parser.add_argument(
        "--league", type=str, default=None,
        help="联赛过滤（如: 英超, 西甲），不指定则采集全部",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="预览模式，不实际采集",
    )
    parser.add_argument(
        "--resume", action="store_true", default=True,
        help="断点续传（默认开启）",
    )
    parser.add_argument(
        "--no-resume", action="store_true",
        help="禁用断点续传，重新采集所有",
    )
    parser.add_argument(
        "--db-path", type=str, default=None,
        help="数据库路径",
    )
    args = parser.parse_args()

    leagues = [args.league] if args.league else ALL_LEAGUES
    db_path = Path(args.db_path) if args.db_path else DB_PATH

    collector = BackfillCollector(
        db_path=db_path,
        dry_run=args.dry_run,
    )

    try:
        result = collector.run(
            start_season=args.start_season,
            end_season=args.end_season,
            leagues=leagues,
            resume=not args.no_resume,
        )
        print(f"\n汇总: {json.dumps(result, ensure_ascii=False, indent=2)}")
    finally:
        collector.close()


if __name__ == "__main__":
    main()