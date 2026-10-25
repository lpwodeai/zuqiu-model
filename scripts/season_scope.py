"""赛季口径 SSOT（Single Source of Truth）——C-20261024-207。

赛季字符串**禁止手传/硬编码**（如 `--season 26/27`、`season='26/27'`）——
散落硬编码的历史教训：26/27 赛季 2026-08-15 开打，understat 采集器
2026-09-22 才首次落库（无人"翻页"，赛季前 5 周零采集）。

采集器接入：`from season_scope import current_season`，禁止自写推导逻辑。
报告链（discover_matches 内 season 硬编码）接入排 C-013 后。

口径约定：五大联赛赛季 8 月开打、次年 5-6 月收官——
8 月 1 日起记为新赛季 `yy/yy+1`，此前记为 `yy-1/yy`（7 月休赛期无数据可采，天然静默）。
赛季串格式：两位年份斜杠（"26/27"），与 matches/odds500_match/understat 三表现存口径一致。
"""
from __future__ import annotations

from datetime import date

# 新赛季起始月（8 月）。改赛季规则只改这里，全项目生效。
SEASON_START_MONTH: int = 8


def season_for_date(d: date) -> str:
    """任意日期所属赛季（"26/27" 格式）。回填/补采场景用。"""
    y = d.year % 100
    if d.month >= SEASON_START_MONTH:
        return f"{y:02d}/{y + 1:02d}"
    return f"{y - 1:02d}/{y:02d}"


def current_season(today: date | None = None) -> str:
    """当日所属赛季。today 可注入便于测试。"""
    return season_for_date(today or date.today())


if __name__ == "__main__":
    # 自检三边界（C-207 登记验证规格）：7 月→上一季 / 8 月→新一季 / 跨年 1 月
    assert season_for_date(date(2026, 7, 31)) == "25/26"
    assert season_for_date(date(2026, 8, 1)) == "26/27"
    assert season_for_date(date(2027, 1, 15)) == "26/27"
    assert season_for_date(date(2027, 8, 1)) == "27/28"
    print(f"[season_scope] OK 自检 4 例通过；当日赛季 = {current_season()}")
