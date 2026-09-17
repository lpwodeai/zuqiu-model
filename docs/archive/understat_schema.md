# Understat 数据表结构说明

> 数据源：https://understat.com （xG 预期进球模型体系）
> 数据库：`data/odds.db`
> 采集器：`collection/final_understat_collector.py`

Understat 是 FBref（传统统计）、SofaScore（评分/传球）之外的第三数据源，核心价值是 **xG 模型体系** 与 **射门级坐标数据**。共 3 张表，字段如下。

---

## 1. understat_match_team_stats（比赛级）

每场一行，主客队比分/xG/赛前胜平负概率。主键 `match_id`。

| 字段 | 类型 | 说明 |
|------|------|------|
| `match_id` | TEXT (PK) | Understat 比赛 ID |
| `season` | TEXT | 赛季，如 `25/26` |
| `league` | TEXT | 联赛（英超/西甲/意甲/德甲/法甲） |
| `datetime` | TEXT | 比赛时间 `YYYY-MM-DD HH:MM:SS` |
| `home_team` | TEXT | 主队名（英文） |
| `home_team_id` | TEXT | 主队 Understat ID |
| `away_team` | TEXT | 客队名（英文） |
| `away_team_id` | TEXT | 客队 Understat ID |
| `home_goals` | INTEGER | 主队进球 |
| `away_goals` | INTEGER | 客队进球 |
| `home_xg` | REAL | 主队预期进球 xG |
| `away_xg` | REAL | 客队预期进球 xG |
| `forecast_w` | REAL | 赛前主胜概率 |
| `forecast_d` | REAL | 赛前平局概率 |
| `forecast_l` | REAL | 赛前主负概率 |
| `is_result` | INTEGER | 是否已完赛（1/0） |
| `collected_at` | TEXT | 采集时间 |

---

## 2. understat_player_xg（球员级）

复合主键 `(match_id, player_id)`。xG 体系核心表。

| 字段 | 类型 | 说明 |
|------|------|------|
| `match_id` | TEXT | 比赛 ID（复合主键之一） |
| `player_id` | TEXT | Understat 球员 ID（复合主键之一） |
| `player_name` | TEXT | 球员名（英文） |
| `team_id` | TEXT | 所属球队 Understat ID |
| `team_name` | TEXT | 所属球队名（英文） |
| `position` | TEXT | 位置（GK/D/DC/M/AM/FW 等） |
| `time` | INTEGER | 出场分钟数 |
| `goals` | INTEGER | 进球 |
| `shots` | INTEGER | 射门数 |
| `xg` | REAL | 预期进球 xG |
| `assists` | INTEGER | 助攻 |
| `xa` | REAL | 预期助攻 xA |
| `key_passes` | INTEGER | 关键传球 |
| `xg_chain` | REAL | xGChain（参与进攻的预期进球贡献） |
| `xg_buildup` | REAL | xGBuildup（组织阶段预期进球贡献，不含射门/助攻） |
| `yellow_card` | INTEGER | 黄牌 |
| `red_card` | INTEGER | 红牌 |
| `season` | TEXT | 赛季，如 `25/26` |
| `league` | TEXT | 联赛（英超/西甲/意甲/德甲/法甲） |

> 说明：`xg_chain` / `xg_buildup` / `xa` 是 Understat 独有字段，FBref 与 SofaScore 均无。

---

## 3. understat_shots（射门级）

每脚射门一行，主键 `shot_id`。用于射门地图可视化、进球质量分析。

| 字段 | 类型 | 说明 |
|------|------|------|
| `shot_id` | TEXT (PK) | 射门 ID |
| `match_id` | TEXT | 比赛 ID |
| `minute` | INTEGER | 射门发生分钟 |
| `result` | TEXT | 结果（Goal/SavedShot/MissedShots/BlockedShot/ShotOnPost/OwnGoal） |
| `x` | REAL | 射门坐标 X（0-1 归一化，纵向） |
| `y` | REAL | 射门坐标 Y（0-1 归一化，横向/离门距离） |
| `xg` | REAL | 该脚射门的预期进球值 |
| `player_id` | TEXT | 射门球员 Understat ID |
| `player_name` | TEXT | 射门球员名（英文） |
| `team_side` | TEXT | 主客队标识（h/a） |
| `team_name` | TEXT | 球队名（英文） |
| `situation` | TEXT | 射门情况（OpenPlay/SetPiece/CounterAttack/Penalty 等） |
| `shot_type` | TEXT | 射门部位（RightFoot/LeftFoot/Head/OtherBodyPart） |
| `player_assisted` | TEXT | 助攻球员名 |
| `last_action` | TEXT | 射门前最后一动作（Cross/Throughball/Pass/None 等） |
| `season` | TEXT | 赛季，如 `25/26` |
| `league` | TEXT | 联赛（英超/西甲/意甲/德甲/法甲） |

---

## 三表关系

```
understat_match_team_stats (1) ──< understat_player_xg (N)  按 match_id 关联
understat_match_team_stats (1) ──< understat_shots (N)      按 match_id 关联
```

## 与现有数据源的对比定位

| 数据 | FBref | SofaScore | Understat |
|------|-------|-----------|-----------|
| 传统事件（射门/犯规/越位） | ✅ | — | — |
| 评分/传球成功率/触球 | — | ✅ | — |
| xG/xA（预期进球/助攻） | — | 部分（30.8%） | ✅ 100% |
| xGChain/xGBuildup | — | — | ✅ 独有 |
| 射门坐标/部位/情况 | — | — | ✅ 独有 |
| 赛前胜平负概率 forecast | — | ✅ | ✅ |