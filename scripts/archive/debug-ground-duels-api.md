# Debug Session: ground-duels-api

Status: [CLOSED] — 2026-08-22 当日闭环（结论补记于 2026-09-27，依据 C-20260927-015）
Date: 2026-08-22
Issue: ground_duels_won 字段覆盖率极低（仅17行），但 SofaScore 网站 UI 明确展示了 "Ground duels (won)" 列

## 用户截图证据
SofaScore 球员统计表有以下列：
- Duels (won) | **Ground duels (won)** | Aerial duels (won) | Possession lost | Fouls | ...
- 例：Aurelé Amenda: Duels=4, Ground=1, Aerial=3

## 假设列表
A. API 返回了地面对抗但 key 名不同（如 groundWin, groundDuelsWon）
B. 地面对抗在另一个 API 端点（player-stats 而非 lineups）
C. 数据在嵌套结构中（如 statistics.duels.groundWon）
D. 需要特定参数获取分项数据

## 排查计划
1. 抓取实际 SofaScore API 响应，dump 所有 key
2. 对比 lineups 端点和 player-stats 端点
3. 确定正确的字段路径
4. 更新映射逻辑

## 进展
- [x] Step 1: Dump API response keys
- [x] Step 2: Compare endpoints
- [x] Step 3: Identify correct field path — **结论：假设 A-D 均不成立，API 不直接提供 ground duels 字段**
- [x] Step 4: Fix mapping — **改为公式推导，非字段映射**
- [x] Step 5: Verify fix

## 闭环结论（补记于 2026-09-27，依据 C-20260927-015）

1. **真实答案**：SofaScore API **无 ground duels 独立字段**（假设 A 字段名不同 / B 另端点 / C 嵌套 / D 特定参数，全部证伪）。UI 展示的 "Ground duels (won)" 由既有字段推导：
   `ground_duels = max(0, duels − aerials)`（won 与 total 两口径同公式）。
2. **存量回填**：2026-08-22 当日 11:28-11:31 编写一次性脚本 `backfill_ground_duels.py`（直连 sqlite3，单事务 3 条 UPDATE），回填完成度 100%——`ground_duels_won` 与 `duels_won` 覆盖精确持平、公式 0 行偏差；脚本已于 2026-09-27 归档 `scripts/archive/`（C-015）。
3. **前向接线**：采集器 `collection/final_sofascore_collector.py` 新数据入库时按同公式 `max(0, (dw or 0) − (aw or 0))` 计算。
4. **2026-09-27 只读实测**：`ground_duels_won` 非空 455,504 / 表总 738,111（非空比例 61.71%，与 duels_won 覆盖一致；缺失行为该场无对抗统计，非字段缺陷）。
5. **特征状态**：数据已备、特征管道**尚未接线**（features/、modules/ 零消费）；未来接入 duels 类特征时须注意 aerials 缺失时 ground 为上界估计的口径。
