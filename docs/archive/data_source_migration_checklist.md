# 数据源迁移清单：load_match_data() → load_match_data_odds()

> 生成时间：2026-08-14
> 迁移目标：将核心脚本从 `five_leagues.db`（875场/1赛季）统一迁移到 `odds.db`（5,252场/3赛季/5大联赛）

---

## 背景

项目存在"双轨制"数据加载：
- `load_match_data()` → 读取 `five_leagues.db`，875 场比赛，仅 2025-2026 赛季
- `load_match_data_odds()` → 读取 `odds.db`，5,252 场比赛，2023-2026 三赛季，五大联赛全覆盖

`train_models.py` 已于 2026-08-14 迁移完成。

---

## 一、可直接迁移（Category A：导入 feature_utils 的 load_match_data）

这些脚本从 `feature_utils` 导入 `load_match_data`，只需增加 `load_match_data_odds` 导入并替换调用。

| # | 文件 | 导入行 | 调用行 | 改动点 |
|---|------|--------|--------|--------|
| 1 | `scripts/_test_predict.py` | L6, L47 | L43 | import 增加 `load_match_data_odds`；调用改为 `load_match_data_odds()` |
| 2 | `scripts/unified_backtest_framework.py` | L18 | L571 | import 增加 `load_match_data_odds`；调用改为 `load_match_data_odds()` |
| 3 | `scripts/hyperparameter_opt.py` | L29 | L44 | import 增加 `load_match_data_odds`；调用改为 `load_match_data_odds()` |
| 4 | `scripts/multitask_trainer.py` | L25 | L280 | import 增加 `load_match_data_odds`；调用改为 `load_match_data_odds()` |
| 5 | `scripts/shap_feature_importance.py` | L26 | L70 | import 增加 `load_match_data_odds`；调用改为 `load_match_data_odds()` |
| 6 | `scripts/shap_analyzer.py` | L33 | L230 | import 增加 `load_match_data_odds`；调用改为 `load_match_data_odds()` |
| 7 | `scripts/run_isolation_backtest.py` | L39 | L91 | import 增加 `load_match_data_odds`；调用改为 `load_match_data_odds()` |

**修改原因**：这些脚本使用 `load_match_data()` 仅获取 875 场数据，缺失德甲/法甲联赛和 2023-2024/2024-2025 赛季数据。

**预期效果**：每个脚本从 875 场扩展至 5,252 场，联赛覆盖从 3 个增至 5 个，赛季覆盖从 1 个增至 3 个。

---

## 二、需手动评估（Category B：自有本地 load_match_data 定义）

这些脚本在文件内部定义了独立的 `load_match_data()` 函数，查询了 `five_leagues.db` 特有的额外列（如 xG、xGOT、Possession、Corners 等），这些列在 `odds.db` 中不存在。**不能简单替换**，需要评估下游代码对这些列的依赖。

| # | 文件 | 本地定义行 | 额外列 | 下游依赖 |
|---|------|-----------|--------|----------|
| 8 | `scripts/feature_engineering_pipeline.py` | L257 | 22 个额外列（xG/xGOT/BigChances/xA/Saves/Shots/Possession/Corners/Fouls/Cards 等） | 需逐列检查 |
| 9 | `scripts/player_feature_engineer.py` | L56 | matchId, home_team_id, away_team_id | 球员特征依赖 ID 关联 |
| 10 | `scripts/model_backtest.py` | L11 | homeXg, awayXg, homePossession, homeCorners, awayCorners | L58-63 直接用于特征构建 |

**处理建议**：
- `model_backtest.py`：已确认 L58-63 使用 xG/Possession/Corners 构建特征，需要先评估这些特征在 `odds.db` 数据源下是否可替代
- `feature_engineering_pipeline.py`：22 个额外列，评估工作量大，建议作为独立任务
- `player_feature_engineer.py`：依赖 `five_leagues.db` 的球员数据表结构，迁移需同步修改球员数据加载逻辑

---

## 三、已完成迁移

| # | 文件 | 状态 |
|---|------|------|
| 0 | `scripts/train_models.py` | ✅ 已迁移（2026-08-14） |

---

## 四、不变更项

| 文件/目录 | 原因 |
|-----------|------|
| `data/five_leagues.db` | **生产运行时数据库**，预测服务依赖此库，不能删除 |
| `scripts/feature_utils.py` 中的 `load_match_data()` | 保留作为 `load_match_data_odds()` 的 fallback 和向后兼容 |
| `scripts/backup_db.py` | 备份 `five_leagues.db`（生产库），不需要改 |
| `scripts/auto_train.py` | 使用 `five_leagues.db` 进行数据触发检测，属于运维逻辑 |

---

## 五、验证计划

1. 迁移完成后运行 `train_models.py` 验证 5,252 场比赛加载
2. 抽查 1-2 个 Category A 脚本的导入是否正常
3. 记录 `five_leagues.db` 保留原因（生产运行时依赖）