# 球员级特征工程方案

## 一、概述

本方案旨在构建球员级特征工程体系，整合球员多维度数据（评分、出场记录、伤停状态等核心指标），为足球比赛预测模型提供精细化的球员层面特征支持。

---

## 二、数据维度清单

### 2.1 球员基础信息维度

| 字段名 | 类型 | 必填 | 说明 | 数据格式 | 示例 |
|--------|------|------|------|---------|------|
| playerId | INTEGER | 是 | 球员唯一标识 | 整数 | 1 |
| name | TEXT | 是 | 球员中文名 | 字符串 | 哈兰德 |
| nameEn | TEXT | 否 | 球员英文名 | 字符串 | Haaland |
| teamId | INTEGER | 是 | 所属球队ID | 整数 | 5 |
| position | TEXT | 是 | 具体位置 | 字符串 | ST |
| positionGroup | TEXT | 是 | 位置分组 | 枚举(goalkeeper/defender/midfielder/forward) | forward |
| age | INTEGER | 是 | 年龄 | 整数(18-40) | 23 |
| height | REAL | 否 | 身高(cm) | 浮点数(160-210) | 194.0 |
| weight | REAL | 否 | 体重(kg) | 浮点数(50-110) | 89.0 |
| nationality | TEXT | 否 | 国籍 | 字符串 | 挪威 |
| foot | TEXT | 否 | 惯用脚 | 枚举(左脚/右脚/双脚) | 右脚 |
| jerseyNumber | INTEGER | 否 | 球衣号码 | 整数(1-99) | 9 |
| marketValue | REAL | 是 | 市场价值(亿欧元) | 浮点数(0-30) | 1.8 |
| isKeyPlayer | BOOLEAN | 是 | 是否核心球员 | 布尔值 | 1 |
| lineupRole | TEXT | 是 | 阵容角色 | 枚举(starter/backup/rotation) | starter |
| playerStatus | TEXT | 是 | 当前状态 | 枚举(available/injured/suspended/doubtful) | available |
| injuryStatus | TEXT | 否 | 伤病详情 | 字符串 | 膝盖拉伤，预计缺阵4周 |
| injuryReturnDate | TEXT | 否 | 预计复出日期 | 日期格式(YYYY-MM-DD) | 2026-08-15 |

### 2.2 球员赛季统计维度

| 字段名 | 类型 | 必填 | 说明 | 数据格式 | 示例 |
|--------|------|------|------|---------|------|
| playerId | INTEGER | 是 | 球员唯一标识 | 整数 | 1 |
| season | TEXT | 是 | 赛季 | 字符串(YYYY或YYYY-YY) | 2025 |
| matches | INTEGER | 是 | 出场次数 | 整数(0-50) | 38 |
| starts | INTEGER | 是 | 首发次数 | 整数(0-50) | 35 |
| minutes | INTEGER | 是 | 出场时间(分钟) | 整数(0-5000) | 3100 |
| goals | INTEGER | 是 | 进球数 | 整数(0-50) | 36 |
| assists | INTEGER | 是 | 助攻数 | 整数(0-30) | 8 |
| xg | REAL | 是 | 预期进球 | 浮点数(0-40) | 32.5 |
| xA | REAL | 是 | 预期助攻 | 浮点数(0-20) | 6.8 |
| shots | INTEGER | 是 | 射门次数 | 整数(0-200) | 120 |
| shotsOnTarget | INTEGER | 是 | 射正次数 | 整数(0-100) | 58 |
| bigChances | INTEGER | 是 | 大机会次数 | 整数(0-30) | 18 |
| bigChancesCreated | INTEGER | 是 | 创造大机会次数 | 整数(0-30) | 12 |
| tackles | INTEGER | 是 | 抢断次数 | 整数(0-200) | 45 |
| interceptions | INTEGER | 是 | 拦截次数 | 整数(0-150) | 28 |
| blocks | INTEGER | 是 | 封堵次数 | 整数(0-100) | 15 |
| duelsWon | INTEGER | 是 | 赢得对抗次数 | 整数(0-300) | 120 |
| aerialWon | INTEGER | 是 | 赢得空中对抗次数 | 整数(0-200) | 45 |
| dribblesCompleted | INTEGER | 是 | 成功过人次数 | 整数(0-150) | 42 |
| dribblesAttempted | INTEGER | 是 | 尝试过人次数 | 整数(0-200) | 65 |
| passes | INTEGER | 是 | 传球次数 | 整数(0-5000) | 1200 |
| passesCompleted | INTEGER | 是 | 成功传球次数 | 整数(0-5000) | 1080 |
| keyPasses | INTEGER | 是 | 关键传球次数 | 整数(0-100) | 35 |
| throughBalls | INTEGER | 是 | 直塞球次数 | 整数(0-50) | 12 |
| crosses | INTEGER | 是 | 传中次数 | 整数(0-100) | 28 |
| fouls | INTEGER | 是 | 犯规次数 | 整数(0-50) | 18 |
| yellowCards | INTEGER | 是 | 黄牌数 | 整数(0-15) | 4 |
| redCards | INTEGER | 是 | 红牌数 | 整数(0-3) | 0 |
| penaltyGoals | INTEGER | 是 | 点球进球数 | 整数(0-15) | 8 |
| penaltyMissed | INTEGER | 是 | 点球罚失数 | 整数(0-5) | 1 |
| rating | REAL | 是 | 平均评分 | 浮点数(1-10) | 8.5 |

### 2.3 球员近期状态维度

| 字段名 | 类型 | 必填 | 说明 | 数据格式 | 示例 |
|--------|------|------|------|---------|------|
| playerId | INTEGER | 是 | 球员唯一标识 | 整数 | 1 |
| recentForm | REAL | 是 | 近期状态 | 浮点数(-0.5-0.5) | 0.35 |
| recentRating | REAL | 是 | 近期平均评分 | 浮点数(1-10) | 8.8 |
| goalsLast5 | INTEGER | 是 | 近5场进球数 | 整数(0-10) | 5 |
| assistsLast5 | INTEGER | 是 | 近5场助攻数 | 整数(0-5) | 2 |
| minutesLast5 | INTEGER | 是 | 近5场出场时间 | 整数(0-450) | 420 |
| startsLast5 | INTEGER | 是 | 近5场首发次数 | 整数(0-5) | 5 |
| isInjured | BOOLEAN | 是 | 是否受伤 | 布尔值 | 0 |
| daysSinceLastMatch | INTEGER | 是 | 距上次出场天数 | 整数(0-90) | 3 |

### 2.4 球员历史交锋维度

| 字段名 | 类型 | 必填 | 说明 | 数据格式 | 示例 |
|--------|------|------|------|---------|------|
| playerId | INTEGER | 是 | 球员唯一标识 | 整数 | 1 |
| opponentTeamId | INTEGER | 是 | 对手球队ID | 整数 | 8 |
| h2hMatches | INTEGER | 是 | 对阵该队次数 | 整数(0-20) | 8 |
| h2hGoals | INTEGER | 是 | 对阵该队进球数 | 整数(0-20) | 10 |
| h2hAssists | INTEGER | 是 | 对阵该队助攻数 | 整数(0-10) | 3 |
| h2hAvgRating | REAL | 是 | 对阵该队平均评分 | 浮点数(1-10) | 8.2 |
| h2hMatchesWon | INTEGER | 是 | 对阵该队获胜次数 | 整数(0-20) | 5 |

---

## 三、数据格式要求

### 3.1 数据库表结构

#### players 表

```sql
CREATE TABLE players (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    nameEn TEXT,
    teamId INTEGER NOT NULL,
    position TEXT NOT NULL,
    positionGroup TEXT NOT NULL,
    age INTEGER,
    height REAL,
    weight REAL,
    nationality TEXT,
    jerseyNumber INTEGER,
    marketValue REAL,
    foot TEXT,
    isKeyPlayer BOOLEAN DEFAULT 0,
    lineupRole TEXT DEFAULT 'backup',
    playerStatus TEXT DEFAULT 'available',
    injuryStatus TEXT,
    injuryReturnDate TEXT,
    createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
    updatedAt TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (teamId) REFERENCES teams(id)
);
```

#### player_stats 表

```sql
CREATE TABLE player_stats (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playerId INTEGER NOT NULL,
    season TEXT NOT NULL,
    matches INTEGER DEFAULT 0,
    starts INTEGER DEFAULT 0,
    minutes INTEGER DEFAULT 0,
    goals INTEGER DEFAULT 0,
    assists INTEGER DEFAULT 0,
    xg REAL DEFAULT 0,
    xA REAL DEFAULT 0,
    shots INTEGER DEFAULT 0,
    shotsOnTarget INTEGER DEFAULT 0,
    bigChances INTEGER DEFAULT 0,
    bigChancesCreated INTEGER DEFAULT 0,
    tackles INTEGER DEFAULT 0,
    interceptions INTEGER DEFAULT 0,
    blocks INTEGER DEFAULT 0,
    duelsWon INTEGER DEFAULT 0,
    aerialWon INTEGER DEFAULT 0,
    dribblesCompleted INTEGER DEFAULT 0,
    dribblesAttempted INTEGER DEFAULT 0,
    passes INTEGER DEFAULT 0,
    passesCompleted INTEGER DEFAULT 0,
    keyPasses INTEGER DEFAULT 0,
    throughBalls INTEGER DEFAULT 0,
    crosses INTEGER DEFAULT 0,
    fouls INTEGER DEFAULT 0,
    yellowCards INTEGER DEFAULT 0,
    redCards INTEGER DEFAULT 0,
    penaltyGoals INTEGER DEFAULT 0,
    penaltyMissed INTEGER DEFAULT 0,
    rating REAL DEFAULT 0,
    createdAt TEXT DEFAULT CURRENT_TIMESTAMP,
    updatedAt TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (playerId, season)
);
```

#### player_recent_form 表（可选）

```sql
CREATE TABLE player_recent_form (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    playerId INTEGER NOT NULL,
    recentForm REAL,
    recentRating REAL,
    goalsLast5 INTEGER,
    assistsLast5 INTEGER,
    minutesLast5 INTEGER,
    startsLast5 INTEGER,
    isInjured BOOLEAN,
    daysSinceLastMatch INTEGER,
    updatedAt TEXT DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (playerId)
);
```

### 3.2 API数据格式

#### 球员数据导入接口

```json
{
  "type": "players",
  "data": [
    {
      "id": "player_001",
      "name": "哈兰德",
      "nameEn": "Erling Haaland",
      "teamKey": "pl_mci",
      "position": "ST",
      "positionGroup": "forward",
      "age": 23,
      "height": 194.0,
      "weight": 89.0,
      "nationality": "挪威",
      "foot": "右脚",
      "jerseyNumber": 9,
      "marketValue": 1.8,
      "isKeyPlayer": true,
      "lineupRole": "starter",
      "playerStatus": "available",
      "injuryStatus": null,
      "stats": {
        "season": "2025",
        "matches": 38,
        "starts": 35,
        "minutes": 3100,
        "goals": 36,
        "assists": 8,
        "xg": 32.5,
        "xA": 6.8,
        "rating": 8.5
      }
    }
  ]
}
```

---

## 四、数据来源及获取方式

### 4.1 主要数据源

| 数据源 | 提供商 | 费用 | 覆盖范围 | 推荐程度 |
|--------|--------|------|---------|---------|
| Opta | Stats Perform | 付费 | 完整球员数据 | ⭐⭐⭐⭐⭐ |
| Sportradar | Sportradar | 付费 | 完整球员数据 | ⭐⭐⭐⭐ |
| WhoScored | WhoScored.com | 付费 | 详细球员统计 | ⭐⭐⭐⭐ |
| Transfermarkt | Transfermarkt | 免费 | 转会和市场价值 | ⭐⭐⭐⭐ |
| FBref | FBref.com | 免费 | 高级统计数据 | ⭐⭐⭐⭐ |
| Football-Data.org | API-Football | 免费(有限) | 基础数据 | ⭐⭐⭐ |

### 4.2 数据采集方案

#### 方案A：API接口采集

```python
# 示例：使用Football-Data.org API
import requests

API_KEY = "your_api_key"
headers = {"X-Auth-Token": API_KEY}

# 获取球队球员
response = requests.get(
    "https://api.football-data.org/v4/teams/66/players",
    headers=headers
)
players = response.json()
```

#### 方案B：数据库同步

```python
import sqlite3

conn = sqlite3.connect("data/five_leagues.db")
cursor = conn.cursor()

# 查询球员数据
cursor.execute('SELECT * FROM players WHERE teamId = ?', (team_id,))
players = cursor.fetchall()
```

---

## 五、数据质量要求

### 5.1 完整性要求

| 字段类别 | 完整率要求 | 处理方式 |
|---------|-----------|---------|
| 基础信息（姓名、位置、年龄） | ≥99% | 缺失则标记为未知 |
| 核心统计（进球、助攻、出场） | ≥95% | 缺失填充0或联赛平均值 |
| 高级指标（xg、xA、评分） | ≥80% | 缺失填充联赛平均值或估算 |
| 伤停状态 | ≥90% | 缺失默认为available |

### 5.2 准确性要求

- **评分数据**：必须为1-10范围内的浮点数，精确到小数点后1位
- **年龄计算**：基于出生日期自动计算，避免硬编码
- **市场价值**：单位统一为亿欧元，精确到小数点后2位
- **位置分类**：必须映射到标准位置分组(goalkeeper/defender/midfielder/forward)

### 5.3 时效性要求

| 数据类型 | 更新频率 | 更新时机 |
|---------|---------|---------|
| 伤停状态 | 每日 | 上午10点 |
| 球员统计 | 每周 | 比赛结束后24小时内 |
| 市场价值 | 每月 | 月初 |
| 基础信息 | 每季 | 赛季开始前 |

### 5.4 一致性要求

- 球员姓名在所有数据类型中保持一致
- 球队ID必须与teams表中的ID对应
- 位置分组必须统一（如GK统一为goalkeeper）
- 日期格式统一为YYYY-MM-DD

---

## 六、特征工程方案

### 6.1 特征提取策略

#### 6.1.1 基础特征

| 特征名 | 计算方式 | 说明 |
|--------|---------|------|
| player_age | 直接使用 | 球员年龄 |
| player_market_value | 直接使用 | 市场价值 |
| player_is_key | one-hot编码 | 是否核心球员 |
| player_status_available | one-hot编码 | 状态是否可用 |
| player_status_injured | one-hot编码 | 状态是否受伤 |
| player_status_suspended | one-hot编码 | 状态是否停赛 |

#### 6.1.2 效率特征

| 特征名 | 计算方式 | 说明 |
|--------|---------|------|
| goals_per_90 | goals / (minutes / 90) | 每90分钟进球 |
| assists_per_90 | assists / (minutes / 90) | 每90分钟助攻 |
| xg_per_90 | xg / (minutes / 90) | 每90分钟预期进球 |
| xa_per_90 | xA / (minutes / 90) | 每90分钟预期助攻 |
| shots_per_90 | shots / (minutes / 90) | 每90分钟射门 |
| shots_on_target_rate | shotsOnTarget / (shots + 0.01) | 射正率 |
| pass_completion_rate | passesCompleted / (passes + 0.01) | 传球成功率 |
| dribble_success_rate | dribblesCompleted / (dribblesAttempted + 0.01) | 过人成功率 |
| aerial_win_rate | aerialWon / (duelsWon + 0.01) | 空中对抗成功率 |

#### 6.1.3 稳定性特征

| 特征名 | 计算方式 | 说明 |
|--------|---------|------|
| rating_std | 评分标准差 | 表现稳定性 |
| goals_std | 进球数标准差 | 进攻稳定性 |
| minutes_std | 出场时间标准差 | 出场稳定性 |
| starts_ratio | starts / (matches + 0.01) | 首发率 |

#### 6.1.4 对比特征

| 特征名 | 计算方式 | 说明 |
|--------|---------|------|
| home_top_player_rating | 主队核心球员评分 | 主队实力 |
| away_top_player_rating | 客队核心球员评分 | 客队实力 |
| home_avg_player_rating | 主队首发平均评分 | 主队整体实力 |
| away_avg_player_rating | 客队首发平均评分 | 客队整体实力 |
| rating_diff | home_avg_player_rating - away_avg_player_rating | 评分差值 |
| market_value_diff | home_total_value - away_total_value | 市值差值 |
| injured_count_diff | home_injured_count - away_injured_count | 伤病数量差值 |
| suspended_count_diff | home_suspended_count - away_suspended_count | 停赛数量差值 |

#### 6.1.5 时间衰减特征

| 特征名 | 计算方式 | 说明 |
|--------|---------|------|
| weighted_goals_per_90 | 指数衰减加权 | 近期进球效率 |
| weighted_assists_per_90 | 指数衰减加权 | 近期助攻效率 |
| weighted_rating | 指数衰减加权 | 近期平均评分 |

---

## 七、与现有回测数据的整合方案

### 7.1 可行性分析

**结论：完全可行**

现有特征工程流程（`feature_engineering_pipeline.py`）可以通过以下方式整合球员特征：

1. **数据源兼容性**：球员数据存储在SQLite数据库中，与现有比赛数据共享同一数据库
2. **特征拼接**：球员特征可以与球队特征、比赛特征、H2H特征并行拼接
3. **时间序列一致性**：球员特征按比赛日期提取，符合时间序列交叉验证要求

### 7.2 整合策略

#### 方案A：直接嵌入现有特征工程（推荐）

```python
# 在build_all_features中添加球员特征
def build_all_features(df):
    temporal_features = build_temporal_features(df)
    league_features = build_league_features(df)
    match_features = build_match_features(df)
    team_features = build_team_features(df)
    player_features = build_player_features(df)  # 新增
    
    all_features = pd.concat([
        temporal_features, 
        league_features, 
        match_features, 
        team_features,
        player_features  # 新增
    ], axis=1)
    
    return all_features, df['result']
```

#### 方案B：独立特征提取后合并

```python
# 独立运行球员特征提取
player_features = extract_player_features(db_path)

# 与现有特征合并
X = pd.merge(existing_features, player_features, on='match_id', how='left')
```

### 7.3 整合注意事项

1. **数据泄漏风险**：球员特征必须基于赛前数据，避免使用赛后统计
2. **时间对齐**：球员特征应取比赛前的最新数据
3. **缺失值处理**：无球员数据的比赛使用联赛平均值填充
4. **维度控制**：球员特征数量应控制在合理范围（建议50-80维）

---

## 八、赛后阵容数据及球员表现分析的补充方案

### 8.1 可行性分析

**结论：可以添加，但需注意数据泄漏问题**

| 数据类型 | 是否可用于训练 | 是否可用于回测 | 说明 |
|---------|--------------|--------------|------|
| 赛前首发阵容 | ✅ | ✅ | 可作为特征 |
| 赛前伤停名单 | ✅ | ✅ | 可作为特征 |
| 赛后出场时间 | ❌ | ✅ | 仅用于分析，不可训练 |
| 赛后球员评分 | ❌ | ✅ | 仅用于分析，不可训练 |
| 赛后球员统计 | ❌ | ✅ | 仅用于分析，不可训练 |

### 8.2 补充数据维度

#### 8.2.1 赛前阵容数据

| 字段名 | 类型 | 说明 |
|--------|------|------|
| matchId | INTEGER | 比赛ID |
| teamId | INTEGER | 球队ID |
| playerId | INTEGER | 球员ID |
| isStarter | BOOLEAN | 是否首发 |
| position | TEXT | 场上位置 |
| lineupOrder | INTEGER | 出场顺序 |

#### 8.2.2 赛后球员表现数据

| 字段名 | 类型 | 说明 |
|--------|------|------|
| matchId | INTEGER | 比赛ID |
| playerId | INTEGER | 球员ID |
| minutesPlayed | INTEGER | 出场时间 |
| goals | INTEGER | 进球数 |
| assists | INTEGER | 助攻数 |
| rating | REAL | 比赛评分 |
| xg | REAL | 预期进球 |
| xA | REAL | 预期助攻 |
| manOfTheMatch | BOOLEAN | 是否当选最佳 |

### 8.3 应用场景

#### 场景1：赛前阵容分析（可用于训练）

```python
def build_lineup_features(df):
    features = pd.DataFrame(index=df.index)
    
    # 主队首发平均评分
    features['home_starter_avg_rating'] = df.apply(
        lambda x: get_team_avg_rating(x['home_team_name'], x['date'], is_starter=True),
        axis=1
    )
    
    # 客队首发平均评分
    features['away_starter_avg_rating'] = df.apply(
        lambda x: get_team_avg_rating(x['away_team_name'], x['date'], is_starter=True),
        axis=1
    )
    
    # 核心球员缺阵情况
    features['home_key_player_missing'] = df.apply(
        lambda x: count_missing_key_players(x['home_team_name'], x['date']),
        axis=1
    )
    
    return features
```

#### 场景2：赛后分析（仅用于模型评估）

```python
def analyze_player_contribution(match_id):
    """分析球员表现对比赛结果的贡献"""
    player_stats = get_post_match_player_stats(match_id)
    
    # 计算球员影响力
    for player in player_stats:
        influence = calculate_player_influence(
            player['rating'],
            player['goals'],
            player['assists'],
            player['minutesPlayed']
        )
        player['influence'] = influence
    
    return sorted(player_stats, key=lambda x: x['influence'], reverse=True)
```

---

## 九、特征工程实施步骤

### 步骤1：数据采集与清洗

1. 从数据源获取球员基础信息和统计数据
2. 统一数据格式和命名规范
3. 处理缺失值和异常值
4. 验证数据一致性

### 步骤2：特征提取

1. 提取基础特征（年龄、市值、位置等）
2. 计算效率特征（进球效率、传球成功率等）
3. 计算稳定性特征（评分标准差、出场稳定性等）
4. 计算对比特征（主客队球员实力对比）
5. 计算时间衰减特征（近期表现加权）

### 步骤3：特征选择

1. 使用互信息方法评估特征重要性
2. 筛选与目标变量相关性最高的特征
3. 去除高度相关的冗余特征
4. 验证特征子集的有效性

### 步骤4：特征标准化

1. 使用StandardScaler进行Z-score标准化
2. 保存标准化参数用于预测阶段
3. 确保训练和预测使用相同的标准化参数

### 步骤5：特征验证

1. 时间序列交叉验证验证特征有效性
2. 分析特征重要性排序
3. 评估特征对模型性能的贡献
4. 迭代优化特征方案

---

## 十、预期特征数量

| 特征类别 | 预期维度数 |
|---------|-----------|
| 球员基础特征 | 15-20 |
| 球员效率特征 | 10-15 |
| 球员稳定性特征 | 5-8 |
| 主客队对比特征 | 10-15 |
| 时间衰减特征 | 5-10 |
| **总计** | **45-68** |

与现有特征工程（~170维）整合后，总特征维度预计达到 **215-238维**，通过特征选择后保留约 **80-120维**。

---

## 十一、风险与应对措施

### 11.1 数据质量风险

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| 球员数据缺失 | 特征不完整 | 使用联赛平均值填充 |
| 伤停信息不准确 | 特征偏差 | 多源交叉验证 |
| 评分标准不一致 | 特征不可比 | 统一标准化处理 |

### 11.2 数据泄漏风险

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| 使用赛后数据训练 | 模型过拟合 | 严格使用赛前数据 |
| 未来信息混入 | 预测偏差 | 时间序列验证 |
| 特征前瞻引用 | 虚假准确率 | 按比赛日期严格过滤 |

### 11.3 计算复杂度风险

| 风险 | 影响 | 应对措施 |
|------|------|---------|
| 特征维度爆炸 | 训练效率低下 | 特征选择和降维 |
| 球员数据量大 | 内存占用高 | 分批处理和缓存 |
| 计算时间过长 | 迭代效率低 | 并行计算和优化 |

---

## 十二、实施建议

1. **优先完成数据采集**：确保球员数据质量是特征工程的基础
2. **逐步验证特征有效性**：每类特征提取后进行单独验证
3. **与现有特征整合测试**：确保新特征与现有特征互补而非冗余
4. **保留特征提取日志**：记录每类特征的计算过程和来源
5. **定期更新特征方案**：根据模型表现和数据变化迭代优化