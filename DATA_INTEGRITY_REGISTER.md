# 数据完整性台账（DATA INTEGRITY REGISTER）

> 本台账记录经过审计确认、**有意保留或有意处置**的数据库状态。
> 未来审计/清理遇到同态时，直接引用本条，禁止重复立项、禁止无证据重挖。

---

## 条目 1：history 表 19% "孤儿" = 合法 scope 外赛事

**审计日期**：2026-10-22
**审计方法**：全表 DISTINCT + 精细匹配 2,000 条 × 4 表（零抽样）
**结论**：有意保留、不处置、不算脏数据

### 证据链

matches 表只覆盖 5 大联赛（意甲/法甲/英超/西甲/德甲），共 18,344 场。
四张 history 表（score/wdl/hcp/tg）上游数据源覆盖范围更广（杯赛/次级联赛/资格赛等）。

硬证据：

| 指标 | 精确值 |
|---|---|
| score_history DISTINCT match_id | 13,512 |
| 不在 matches + archive 的 match_id | **2,566 (19.0%)** |
| 孤儿年月在 matches 里存在的 | **2,566 = 100%**（排除"历史赛季没存"假设）|
| 孤儿年月 matches 里完全没有 | 0 |
| 精细匹配：同 date+home+away 但 match_id 不同（格式差异） | **0 (0.0%)** |
| 精细匹配：真 scope 外 | **2,000 (100.0%)** |

### 处置决策

- **不给四张 history 表加 FK 约束**（加了会把 19% 合法赔率数据变成孤儿）
- history 表在 scan_orphans.py 里走**软模式**（exit 0），只查 X_X 格式 bug
- FK 硬约束只上四张保护表：model_predictions / post_match_review / ab_test_log / features（这四张已清零 0 孤儿）

---

## 条目 2：A1 self-vs-self 死 bug 批次 = 已清理

**审计日期**：2026-10-22
**处置日期**：2026-10-22（同批次审计+清理）
**处置动作**：幂等 DELETE 4 张 history 表中的 self-vs-self match_id

### 证据链

- 涉及 match_id 数：**15 个**（AC Milan 主场写成自己打自己的格式 bug）
- 涉及总行数：**1,499 行**（四表合计：score=1,271 / wdl=77 / hcp=83 / tg=68）
- 跨表一致性：14/15 出现在全部四张 history 表（同一采集器 bug 批次）
- 最新残留日期：2026-05-11（之后停产出，死 bug）

### 清理细节

| 动作 | 值 |
|---|---|
| 备份文件 | `data/odds.db.pre-a1-cleanup-backup` (1750.9 MB) |
| 清理后四表 self-vs-self 残留 | **0 个 match_id** |
| FK 保护表零影响 | ✅ model_predictions/post_match_review/ab_test_log/features 均无 self-vs-self |
| 消费方影响 | 零（self-vs-self 格式在任何 5 大联赛特征 join 里不会被命中）|

---

## 条目 3：model_predictions FK DDL = ON DELETE CASCADE，暂不改

**DDL 现状**：FOREIGN KEY(match_id) REFERENCES matches(match_id) ON DELETE CASCADE
**审计日期**：2026-10-22
**当前状态**：安全（CASCADE 是哑弹，从未被执行过）

### 处置决策

- **当前安全**：7 条 DELETE FROM matches 路径已全部加 FK_CHILD_TABLES pre-delete（先删 FK 子表 → 最后删父行）
- **维护窗口改**：未来某个维护窗口重建 model_predictions 63 万行表，把 CASCADE → NO ACTION
- **理由**：CASCADE 意味着"DELETE FROM matches WHERE match_id=X 会静默级联删掉 model_predictions 里所有引用 X 的预测行"——虽然当前没触发入口，但前瞻风险存在

### 维护窗口五步手术纪律

1. 备份：`sqlite3 .backup 'odds.db.pre-cascade-backup'` + `VACUUM INTO`
2. 导出 schema：`SELECT sql FROM sqlite_master` 存 .sql 文件（含索引/约束）
3. 重建：`PRAGMA foreign_keys = OFF`（重建期间必须关）→ `CREATE new` 改 FK → `INSERT INTO new SELECT * FROM old` → 行数比对 → `DROP old` → `RENAME`
4. 验证：前后 row count 一致 + 三探针（正向写/孤儿写被拒/DELETE 有子行被拒）
5. 登记：更新本台账

---

## 条目 4：fk_soft_match_id（含空格的 canonical 键）= 已知设计缺陷

**审计日期**：2026-10-22（本轮根因排查顺带发现）
**性质**：已知、接受、暂不改

### 事实

`make_match_id` 不把队名里的空格替换为下划线（`Real Betis` 保留空格），导致 match_id 含空格。
历史上 `g2_collision_clear.py` 旧版用 `.split(" ")[0]` 反解 match_id 时，含空格的键被截断成第一个词（`2026-10-18_Real Betis_FC Barcelona` → `2026-10-18_Real`）。

### 已做防护

- g2_collision_clear.py 旧版字符串反解 → 改由 action tuple 直接传递 canonical match_id
- scan_orphans.py 等基础设施用 `=` 精确比较，不做 `.split(" ")`

### 不处理理由

全量修改 match_id 格式涉及 4298 行含空格的合法预测数据、跨 7+ 张表的 JOIN 一致性、所有写入脚本的 match_id 生成逻辑。改动面太大、风险太高，且已有 g2 防御断言兜底。

---

## 条目 5：FK 保护表 = 4 张

**审计日期**：2026-10-22
**当前状态**：已开 PRAGMA foreign_keys = ON

| 表 | FK 模式 | 孤儿数 |
|---|---|---|
| model_predictions | 硬（PRAGMA ON） | 0 |
| post_match_review | 硬（PRAGMA ON） | 0 |
| ab_test_log | 硬（DDL 已重建带 REFERENCES） | 0 |
| features | 硬（PRAGMA ON） | 0 |
| score_history | **不保护**（scope 外 19% 合法） | 软模式只查 X_X |
| wdl_history | **不保护**（同上） | 同上 |
| handicap_history | **不保护**（同上） | 同上 |
| total_goals_history | **不保护**（同上） | 同上 |

---

## 条目 6：matches 表字段回填缺失（league 空值 + 赛果空/缺行）= 确认待窗口 C 处置

**审计日期**：2026-10-22
**触发**：`docs/post_match/20261020/英超_热刺_vs_考文垂_复盘.md` WDL 通道输出 `insufficient-history`
**性质**：数据缺失（字段未回填），**不是预测丢失**；暂不处置，回填排窗口 C

### 证据链（全量精确计数，零抽样）

| 指标 | 精确值 |
|---|---|
| matches.league 为空/空串（全库） | 137 行 |
| 其中已有赛果 | 128 行 |
| odds500_match 26/27 英超已完赛 | 60 场 |
| matches 表覆盖 | 59 场（缺 `2026-09-12_Chelsea_Hull`） |
| 59 场中 league='英超' | 31 场（有效赛果 27） |
| 59 场中 league 为空 | 28 场（有效赛果 27） |
| 59 场中缺有效赛果 | 5 场（4 场 actual_wdl=NULL + 1 场空串） |
| 当前 `get_league_hit_rate('英超')` 可用样本 | **29（<50 → 返回 None）** |
| 仅回填 league 后可用样本 | **54（≥50 → 门槛通过）** |
| 回填 league + 补齐 5 场赛果后 | **59（均已有 WDL 预测；Chelsea_Hull 无预测不计）** |

`get_league_hit_rate()` 门槛 = 同联赛 ≥50 条 WDL 预测（`scripts/attribution_engine.py` L1092），SQL 为 `WHERE m.league=?`——league 空的比赛被整体漏计，导致分母系统性低估。

### 缺失明细

| 类型 | match_id | 现状 |
|---|---|---|
| 整行缺失 | `2026-09-12_Chelsea_Hull` | matches 无该行（odds500 记录 2:2 已完赛） |
| 赛果空串 | `2026-09-05_Manchester City_Coventry City` | actual_wdl=''（odds500 记录 1:0） |
| 赛果 NULL | `2026-08-23_Brighton & Hove Albion_Aston Villa` | actual_wdl=NULL（odds500 记录 4:0） |
| 赛果 NULL | `2026-09-05_Brighton & Hove Albion_Leeds United` | actual_wdl=NULL（odds500 记录 1:1） |
| 赛果 NULL | `2026-09-13_Coventry City_Brighton & Hove Albion` | actual_wdl=NULL（odds500 记录 0:5） |
| 赛果 NULL | `2026-09-19_Brighton & Hove Albion_Arsenal` | actual_wdl=NULL（odds500 记录 3:0） |

### 处置决策（排窗口 C，本次零写入）

1. 以 `odds500_match` 为唯一源，幂等回填 league（`league IS NULL OR league=''`，全库 137 行）
2. 补齐 5 场赛果（actual_wdl + actual_score）与 `2026-09-12_Chelsea_Hull` 缺行
3. 回填前后计数比对；重跑 `get_league_hit_rate('英超')` 断言 ≠ None 且 ≥50
4. 完成后更新本条目状态 + 关联 change_log C-20261022-189

### 监控状态（C-20261022-194 更新）

- **此前**：本缺口无任何哨兵，只能靠复盘报告"结果源"标注被动发现（盲区 C）。
- **现状**：`scripts/scan_backfill_gap.py` 已挂 chain_watchdog 每日巡检（非阻断软模式）——双信号（post_match_review 已出 ∨ 开赛 ≥48h）+ 滞后天数分桶 [0-1d/2-3d/4-7d/>7d] + league 空值计数，哑扫描不做队名 join（C-191 未修前）。
- **首跑基线（2026-10-22）**：26/27 赛果回填缺口 34 场 [2-3d:5 | 4-7d:10 | >7d:19]；league 空值 137 场（与本条目 128→137 口径一致：128 为"已完赛"过滤，137 为全量）。
- **窗口 C 验收**：L3a/b 回填完成后本扫描 >7d 桶须归零（扫描即回填的测量仪器）。

### 关联

- C-20261022-188（同一复盘 WDL 通道缺命中门禁，显示层缺陷）
- C-20261022-189（本条的数据侧登记）
- 条目 1 / 条目 5（不冲突：本条是字段空值，不是孤儿，不改变 FK 保护表清单）

---

## 历史重挖记录（供未来审计参考）

| 轮次 | 审计内容 | 修正点 |
|---|---|---|
| N1（根因排查）| 抽样 5 条 score_history 孤儿 → 误判 100% bug | 全量 DISTINCT 后修正：A1 self-vs-self 仅 0.55%（14 match_id） |
| N2（CASCADE 风险）| 以为 ON DELETE CASCADE 会静默删预测行 | 发现 PRAGMA foreign_keys 默认 OFF，CASCADE 从未执行过 |
| N3（scope 外孤儿）| 以为"matches 表没存历史赛季" | 精细匹配证明：孤儿日期全在 matches 覆盖范围内，是同月其他比赛 |
