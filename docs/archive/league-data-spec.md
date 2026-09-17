# 五大联赛足球预测模型 - 数据源规范

## 一、数据源概述

本系统需要以下核心数据源，用于支持五大联赛（英超、西甲、德甲、意甲、法甲）的足球比赛预测功能：

| 数据类型 | 用途 | 更新频率 | 优先级 |
|---------|------|---------|--------|
| 球队基础数据 | 球队属性、实力评估 | 每周 | 高 |
| 比赛日程数据 | 赛程安排、比赛结果 | 每日 | 高 |
| 实时赔率数据 | 市场赔率、盘口变化 | 实时 | 高 |
| 球员数据 | 阵容、伤病、状态 | 每日 | 中 |
| 历史交锋数据 | 过往对战记录 | 每月 | 中 |

---

## 二、数据格式规范

### 2.1 球队数据格式

**API端点**: `POST /api/data/import` (type: 'teams')

```json
{
  "league": "PL",
  "teams": [
    {
      "key": "pl_mci",
      "name": "曼城",
      "shortName": "MCI",
      "league": "PL",
      "attack": 3.30,
      "defence": 0.42,
      "tactical": "possession",
      "xGOT": 2.10,
      "xGA": 0.55,
      "marketValue": 14.5,
      "cohesion": 0.96,
      "recentForm": 0.05,
      "xT": 0.32,
      "tempo": 0.70,
      "pressIntensity": 0.85,
      "attackSide": {
        "left": 0.25,
        "center": 0.50,
        "right": 0.25
      }
    }
  ]
}
```

**字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| key | string | 是 | 唯一标识，格式: `${league}_${teamCode}` |
| name | string | 是 | 中文名称 |
| shortName | string | 是 | 球队缩写代码 |
| league | string | 是 | 联赛代码（PL/SA/BL1/SerieA/FL1） |
| attack | number | 是 | 进攻系数 (1.0-4.0) |
| defence | number | 是 | 防守系数 (0.3-1.5) |
| tactical | string | 是 | 战术风格 (possession/pressing/direct/counter/defensive/balanced) |
| xGOT | number | 是 | 预期进球数 (0.5-3.0) |
| xGA | number | 是 | 预期失球数 (0.3-2.0) |
| marketValue | number | 是 | 球队市值（亿欧元） |
| cohesion | number | 是 | 球队凝聚力 (0.5-1.0) |
| recentForm | number | 是 | 近期状态 (-0.2-0.2) |
| xT | number | 否 | 威胁传球预期值 |
| tempo | number | 否 | 比赛节奏 (0.5-1.0) |
| pressIntensity | number | 否 | 逼抢强度 (0.5-1.0) |

### 2.2 比赛数据格式

**API端点**: `POST /api/data/import` (type: 'matches')

```json
{
  "league": "PL",
  "matches": [
    {
      "id": "pl_mci_ars_2026-07-09",
      "homeTeam": "曼城",
      "homeTeamKey": "pl_mci",
      "awayTeam": "阿森纳",
      "awayTeamKey": "pl_ars",
      "league": "PL",
      "round": 1,
      "date": "2026-07-09",
      "time": "20:00",
      "venue": "Etihad Stadium",
      "status": "pending",
      "result": null,
      "odds": {
        "win": 1.45,
        "draw": 4.05,
        "lose": 5.20
      }
    }
  ]
}
```

**字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | 唯一标识，格式: `${league}_${home}_${away}_${date}` |
| homeTeam | string | 是 | 主队中文名 |
| homeTeamKey | string | 是 | 主队唯一标识 |
| awayTeam | string | 是 | 客队中文名 |
| awayTeamKey | string | 是 | 客队唯一标识 |
| league | string | 是 | 联赛代码 |
| round | number | 是 | 轮次 |
| date | string | 是 | 比赛日期 (YYYY-MM-DD) |
| time | string | 是 | 比赛时间 (HH:MM) |
| venue | string | 否 | 球场名称 |
| status | string | 是 | 状态 (pending/live/completed) |
| result | object | 否 | 比赛结果，status=completed时必填 |
| result.homeGoals | number | 否 | 主队进球数 |
| result.awayGoals | number | 否 | 客队进球数 |
| odds | object | 否 | 赔率数据 |
| odds.win | number | 否 | 主胜赔率 |
| odds.draw | number | 否 | 平局赔率 |
| odds.lose | number | 否 | 客胜赔率 |

### 2.3 球员数据格式

**API端点**: `POST /api/data/import` (type: 'players')

```json
{
  "teamKey": "pl_mci",
  "players": [
    {
      "id": "player_001",
      "name": "哈兰德",
      "position": "ST",
      "rating": 9.2,
      "status": "fit",
      "injuryStatus": null,
      "impactFactor": 1.15,
      "recentForm": 0.12
    }
  ]
}
```

**字段说明**:

| 字段 | 类型 | 必填 | 说明 |
|------|------|------|------|
| id | string | 是 | 球员唯一标识 |
| name | string | 是 | 球员中文名 |
| position | string | 是 | 位置 (GK/DF/MF/ST) |
| rating | number | 是 | 球员评分 (1-10) |
| status | string | 是 | 状态 (fit/injured/suspended) |
| injuryStatus | string | 否 | 伤病详情 |
| impactFactor | number | 是 | 影响力因子 (0.8-1.3) |
| recentForm | number | 是 | 近期状态 (-0.3-0.3) |

---

## 三、联赛代码映射

| 代码 | 联赛名称 | 国家 | 球队数量 |
|------|---------|------|---------|
| PL | 英超 | England | 20 |
| SA | 西甲 | Spain | 20 |
| BL1 | 德甲 | Germany | 18 |
| SerieA | 意甲 | Italy | 20 |
| FL1 | 法甲 | France | 20 |

---

## 四、数据更新频率要求

| 数据类型 | 最小更新频率 | 更新时机 | 说明 |
|---------|------------|---------|------|
| 球队基础数据 | 每周 | 周一凌晨 | 基于上周比赛表现更新 |
| 比赛日程数据 | 每日 | 凌晨 | 更新当日赛程和前一日结果 |
| 实时赔率数据 | 实时 | 比赛前24小时 | 至少每5分钟更新一次 |
| 球员数据 | 每日 | 上午10点 | 更新伤病和阵容信息 |
| 历史交锋数据 | 每月 | 月末 | 更新当月完成的比赛记录 |

---

## 五、数据获取方式建议

### 5.1 推荐API接口

| 数据源 | 提供商 | 费用 | 推荐程度 |
|--------|--------|------|---------|
| Football-Data.org | API-Football | 免费(有限) | ⭐⭐⭐ |
| Sportradar | Sportradar | 付费 | ⭐⭐⭐⭐ |
| Opta | Stats Perform | 付费 | ⭐⭐⭐⭐⭐ |
| RapidAPI | 聚合 | 混合 | ⭐⭐⭐ |

### 5.2 数据采集方案

#### 方案A：API接口采集（推荐）

```bash
# 使用Python脚本定时采集
# 示例: 使用Football-Data.org API

# 英超数据端点
GET https://api.football-data.org/v4/competitions/PL/matches
GET https://api.football-data.org/v4/competitions/PL/teams

# 西甲数据端点
GET https://api.football-data.org/v4/competitions/PD/matches
GET https://api.football-data.org/v4/competitions/PD/teams
```

#### 方案B：数据库同步

如果已有数据库存储五大联赛数据，可通过以下方式同步：

```python
# 示例: 从PostgreSQL同步
import psycopg2

conn = psycopg2.connect(
    host="localhost",
    database="football",
    user="user",
    password="password"
)
```

#### 方案C：文件导入

支持JSON文件批量导入：

```bash
# 通过API导入
curl -X POST http://localhost:3000/api/data/import \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d @teams_data.json
```

---

## 六、数据验证规则

### 6.1 球队数据校验

- `attack` 必须在 [1.0, 4.0] 范围内
- `defence` 必须在 [0.3, 1.5] 范围内
- `cohesion` 必须在 [0.5, 1.0] 范围内
- `key` 格式必须为 `${league}_${code}`

### 6.2 比赛数据校验

- `date` 必须为有效日期格式
- `status` 必须为 pending/live/completed 之一
- `result` 在 status=completed 时必须存在且包含 homeGoals/awayGoals

### 6.3 赔率数据校验

- 赔率值必须大于1
- 隐含概率之和必须在 [0.8, 1.3] 范围内

---

## 七、数据存储结构

```
data/
├── teams/
│   ├── pl_teams.json
│   ├── sa_teams.json
│   ├── bl1_teams.json
│   ├── seriea_teams.json
│   └── fl1_teams.json
├── matches/
│   ├── pl_mci_ars_2026-07-09.json
│   └── ...
└── odds/
    ├── pl_mci_ars_2026-07-09.json
    └── ...
```

---

## 八、注意事项

1. **数据一致性**: 确保球队key在所有数据类型中保持一致
2. **时区处理**: 所有时间使用UTC时间存储，前端转换为本地时区
3. **错误处理**: 导入失败时保留原始数据，记录错误日志
4. **缓存策略**: 球队数据缓存1小时，比赛数据缓存5分钟，赔率数据不缓存
5. **数据备份**: 每日凌晨自动备份数据文件夹
