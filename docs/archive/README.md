# docs/archive/ — 文档归档索引

> 维护：系统 / 更新规则：归档/取用文档后同步本索引 + docs/change_log.md
> 定位：历史产物归档（已完成使命但保留追溯价值）；活跃文档一律在 docs/ 根目录
> **C-20260918-049 清理**：原 27 件 B 类历史快照已全部删除（前置条件 C-048 已完成 19 个 .py 文件 28 行注释引用批量更新为"已归档"标注），现仅剩 5 件 A 类必保留 + 本 README 索引。

## 归档条目

### A 类：必保留（5 件，被代码注释或文档间引用，删除会破坏可追溯性）

| 文件 | 归档时间 | 说明 |
|------|---------|------|
| `epl_import_log.md` | 2026-09-14 | 英超 2025-2026 赛季导入日志，被 [scripts/check_odds_history.py L57](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/check_odds_history.py#L57) 代码逻辑读取（C-20260918-039 迁移到此） |
| `SYSTEM_ANALYSIS_REPORT_v2.0.md` | 2026-09-17 | 系统分析报告 v2.0，被五大联赛全栈框架设计 §3.4 L131 + §13.4 L612（PG 迁移验证状态 L1223）文档间引用 |
| `bayesian_shadow_design.md` | 2026-09-14 | P1-B shadow 并行落地设计，被 [run_shadow_incremental.py L5/L79](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/run_shadow_incremental.py#L5) + [setup_shadow_scheduler.py L8](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/setup_shadow_scheduler.py#L8) 代码注释引用 |
| `bayesian_incremental_design.md` | 2026-09-14 | P1-B 贝叶斯增量底座评估方案，被 [bayesian_incremental_ab.py L5](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/bayesian_incremental_ab.py#L5) + [bayesian_incremental.py L5](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/bayesian_incremental.py#L5) 代码注释引用 |
| `unified_engine_integration_plan.md` | 2026-09-14 | 统一预测引擎接入 train_models 实施方案，被 [train_models.py L930/L980/L1161/L1316/L2098/L2257](file:///f:/zuqiu/五大联赛专属模型/五大联赛专属模型/scripts/train_models.py#L930) 代码注释引用 |

### B 类：历史快照（**C-20260918-049 已全部删除**）

原 27 件 B 类历史快照（26 件历史报告快照 + 1 件 match_date_reconciliation）已在 C-20260918-049 全部删除——前置条件 C-20260918-048 已完成 19 个 .py 文件 28 行注释引用批量更新为"已归档"标注，注释不再依赖文件存在。原 27 件清单见 change_log C-20260918-049 记录。

## 取用规则
- 本目录文档仅供追溯参考，不参与当前预测/开发流程
- 任何归档条目如需复活，需在 change_log 记录 D- 编号并移回 docs/ 根目录
- 删除归档条目需用户确认（参照 backups/ 保留策略）
