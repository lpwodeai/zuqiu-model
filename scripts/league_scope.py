"""联赛范围 SSOT（Single Source of Truth）。

本项目只预测五大联赛。**任何采集器都不得自写一份联赛列表**——
散落硬编码的历史教训：normalize_team_name 双份并存，一份改了另一份没改 = 口径漂移。

新增采集器时：`from league_scope import TOP5_LEAGUES, is_top5`，禁止复制字面量。
"""
from __future__ import annotations

# 唯一权威名单（中文，与竞彩/500 展示口径一致）
TOP5_LEAGUES: tuple[str, ...] = ("英超", "西甲", "意甲", "德甲", "法甲")

# 常见别名 → canonical（用于跨源对齐，来源不同写法）
ALIASES: dict[str, str] = {
    "英格兰超级联赛": "英超", "EPL": "英超", "Premier League": "英超", "PL": "英超",
    "西班牙甲级联赛": "西甲", "La Liga": "西甲", "LaLiga": "西甲",
    "意大利甲级联赛": "意甲", "Serie A": "意甲",
    "德国甲级联赛": "德甲", "Bundesliga": "德甲",
    "法国甲级联赛": "法甲", "Ligue 1": "法甲",
}


def normalize_league(name: str | None) -> str:
    """把源侧写法归一成 canonical 中文名；无法识别时原样返回（去空白）。"""
    raw = (name or "").strip()
    if not raw:
        return ""
    return ALIASES.get(raw, raw)


def is_top5(name: str | None) -> bool:
    """是否为五大联赛（含别名）。写入门与抓取过滤共用同一判定。"""
    return normalize_league(name) in TOP5_LEAGUES
