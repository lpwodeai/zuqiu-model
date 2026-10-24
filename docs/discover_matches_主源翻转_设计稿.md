# discover_matches 主源翻转设计稿（C-199 冻结期落稿）

**时间锚**：2026-10-23
**裁决来源**：C-013 裁决落地后执行（约 11 月中下旬）
**冻结期约束**：零代码，本稿仅定义架构，执行排 C-013 后与 L1/L2 同批
**前置依赖**：A 存废裁决（500 退役，C-199 granularity 实测）

---

## 一、问题根因

### 当前 discover_matches 架构（generate_unified_report.py L309-481）

```python
# L322-332: 主源 = odds500_match (status=1)
SELECT * FROM odds500_match WHERE status=1 AND season=? 
  AND match_date >= date(?, '-1 day')
  AND match_date <= date(?, '+1 day')

# L439-461: 兜底补全 = 竞彩 matches 表（反向反查 500）
# 500 命中时取 500 的 round/home_team_en
# 500 未命中时 round=None, home_team_en 走 team_names.py 补
```

### 三层反噬

| 层 | 问题 | C-199 证据 |
|---|---|---|
| L1 采集器 | `run_prematch_2030.ps1` C-100 重写时移除 final_500_collector | grep False |
| L2 数据 | odds500_match 1752 行停在 2026-08/09 开局 fixture | DB 实测 |
| L3 discover 语义 | 主源读历史残行 → 看似有数据、实际是 snapshot → 看门狗口径差（C-197 O4）→ 3 场德甲被 skip | L322-332 主源位置 |

### 设计原意

选 odds500_match 为主源的原始原因（2026-09 前）：**竞彩 matches 表不给英文队名 + round**。discover 下游需要这两个字段 feed 报告生成。500 断供后"兜底补全"语义本该翻转，但代码地位没调。

---

## 二、翻转后架构

### 核心原则

**权威源 = 竞彩 matches 表**（在售清单，采集器健康在链），**富化层 = 外部补充接口**（EN 名解析 + round + 开盘信息）。竞彩未开售的比赛 → 富化层也缺失 → skip（设计内行为，宁缺毋滥）。

### 骨架定义

```
discover_matches(v2):
  Step 1: 权威清单 = sporttery matches 表（actual_score IS NULL）
  Step 2: 队名归一（C-096 scripts/team_names.py canonical 口径）
  Step 3: 富化层 = 三源反查（优先级递减）
    a. TOA /v4/odds/ h2h spread totals（Starter 免费，20-24 庄/场）
       → 取 TOA home_team/away_team（原生 EN）+ commence_time + h2h/spreads/totals
    b. odds500_match 反查（队名 + 日期 ±2 天）
       → 仅取 EN 名 + round（盘口字段 A 退役后不再读）
    c. SofaScore fixture 反查（队名 + 日期 ±2 天）
       → 补 round（if TOA/500 都没）
  Step 4: EN 名解析走 team_names.py canonical（C-176 验证这条路）
  Step 5: round 缺省语义 = None（竞彩不给 round 时取 NULL，登记不阻断）
  Step 6: skip 判定 = 竞彩未售 + 富化层全缺失 → skip（设计内行为）
```

### 3 场德甲 skip 验收样例（C-197 ⑤）

| 场次 | 竞彩是否开售 | 富化层是否命中 | 旧 discover 行为 | v2 discover 行为 |
|---|---|---|---|---|
| 美因茨vs不莱梅 | ❌ 未售 | 500 过期 snapshot / TOA 21:30 场？ | 主源读到 500 残行 → 主循环判"无 WDL 赔率"→ skip | 竞彩清单无 → 富化层反查 TOA → 若 TOA 有则纳入、若 TOA 也无则 skip |
| 弗赖堡vs拜仁 | ❌ 未售 | 同上 | 同上 | 同上 |
| 霍芬海姆vs勒沃库森 | ❌ 未售 | 同上 | 同上 | 同上 |

**验收标准**：v2 discover 对这 3 场的行为 = 竞彩清单无 → TOA 反查命中（TOA `/v4/odds/soccer_germany_bundesliga/` 返回 18 场含 21:30 场）→ 纳入批处理 → 主循环用 TOA h2h 做 WDL 欧指。

### 看门狗口径修正（C-197 O4）

**旧口径**：注册数 = odds500_match status=1 行数（历史残行，可能虚报）
**新口径**：注册数 = matches 表 actual_score IS NULL 且 match_date 近 7 天 行数（竞彩权威清单）
**O4 同批解决**：discover 翻源后看门狗注册数口径自然对齐，不再出现"注册 8/报告 0"口径差。

---

## 三、EN 名解析路径

竞彩 matches 表只给中文队名（如"美因茨"、"拜仁慕尼黑"）。canonical EN 名解析路径：

```
Step 1: 先查 team_names.py canonical 表（166 canonical / 673 alias，C-096 验证覆盖）
  → 命中 → 直接取 canonical_en
Step 2: 若未命中（新队/拼写变体）
  → TOA 反查 TOA home_team/away_team（原生 EN）
  → 用 TOA EN 名作为临时 canonical，写入 team_names.py 注册表
Step 3: 都未命中
  → EN 名暂用中文拼音转换（fallback）
  → 登记到 "EN 名待核验" 观察列表
```

**纪律**：EN 名只增不改，历史 canonical 永不覆盖（C-096 身份分裂教训）。

---

## 四、round 缺省语义

竞彩 matches 表不给 round 字段。round 解析优先级：

```
1. SofaScore fixture round（如果 0a-1 首发采集已落 sofascore_fixture 表）
2. TOA 无 round 字段（Starter 层限制）
3. odds500_match round（仅 500 断供前 2026-08/09 行可用，新赛季不更新）
4. match_date 推断（weekday/ISO week → 非精确）
5. None（登记到 "round 缺省" 观察列表，不阻断流程）
```

---

## 五、执行序约束

1. **discover 翻源先于 L1/L2**：L1 写归一 + L2 读合并 = 在 discover v2 新语义下执行，不能先 L1/L2 再翻源（翻源会改 discover 输出结构 → L1/L2 白做）
2. **discover 翻源与 TOA 接入同批**：富化层接口天然就是反查，TOA 只是往反查列表里加一个来源，零结构重写
3. **A（500 退役）随 discover 翻源落地**：富化层不再读 500 盘口字段（盘口已禁用 L1643/199），仅 EN 名兜底

---

## 六、验收 checklist

- [ ] discover v2 产出 matches 数 = matches 表 actual_score IS NULL 数（竞彩权威清单对齐）
- [ ] 3 场德甲（美因茨/弗赖堡vs拜仁/霍芬海姆vs勒沃库森）在 v2 discover 下的行为有明确日志（纳入/skip 理由）
- [ ] 至少 3 场竞彩已售 + TOA 富化命中的比赛：discover 产出 TOA h2h/spreads/totals 字段
- [ ] 看门狗注册数口径 = matches actual_score IS NULL 数，O4 口径差消失
- [ ] 报告生成零阻塞（round=None 的比赛表头显示"轮次未知"，降级处理）
- [ ] 冻结期承诺兑现：零 prediction_core.py 改动、零 pipeline 层新依赖

---

**归档**：本设计稿冻结期撰写，C-013 裁决后执行。执行后登记新 change_log 条目。
