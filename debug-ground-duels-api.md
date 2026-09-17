# Debug Session: ground-duels-api

Status: [OPEN]
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
- [ ] Step 1: Dump API response keys
- [ ] Step 2: Compare endpoints
- [ ] Step 3: Identify correct field path
- [ ] Step 4: Fix mapping
- [ ] Step 5: Verify fix
