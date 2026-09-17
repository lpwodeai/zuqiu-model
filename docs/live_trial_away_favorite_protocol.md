# 实盘小注验证协议（Pre-registered Protocol）— 客胜热门段

**创建时间**: 2026-09-06
**状态**: 🟢 生效中（规则冻结，禁止中途修改）
**目的**: 在系统性高估调查全部闭环（概率/训练/数据/决策/换市场/RL 全路径证伪）后，用唯一剩余的数据驱动路径——**实盘小注**——收集新样本，统计验证「客胜热门段（away ≤ 2.5）」是否存在可确认的正 edge。

---

## 一、背景与依据

- 回测发现：价值投注 77% 是平局（n=7,291，-4.47% 全线亏损=主出血点）；`cap=2.5` 时整体首次转正（+1.44%，n=751），正收益全部来自**客胜热门**（away≤2.5，+1.69%）（C-20260905-003 `edge_segment_test`）。
- 但该信号统计不显著：cap=2.0 ROI +1.49% 但 z≈+0.57、分折仅 3/5 正、2018/2019 赛季全线 -10%~-23%；经 50+ 组合搜索后单段 +1.5% 属多重比较预期内伪信号（C-20260905-003 `favorite_edge_robust`）。
- 结论：**回测无法区分「真 edge」与「伪信号」，唯一出路是实盘小注采集新样本**。预注册固化规则，避免继续事后搜索造成多重比较污染。

## 二、预注册规则（冻结常量，见 scripts/live_trial_away_favorite.py）

| 项 | 值 | 说明 |
|----|----|------|
| 投注方向 | 客胜（away） | 单方向，不做主胜/平局对照 |
| 客胜赔率上限 | ≤ 2.5 | 竞彩 wdl_history 末条快照 win_b |
| EV 门槛 | > 0 | EV = p_away × o_away − 1（模型原始 WDL_away 概率） |
| 单注金额 | ¥20 平注 | 固定，不随 EV/信心调整 |
| 停止准则 | 累计 300 注 | 达到即强制停止，不再新增 |
| 投注窗口 | 未来 7 天 | odds500_match status=1 未开赛场次 |
| 去重 | match_id_en | 同一赛事绝不重复投注 |

**概率口径声明**：EV 使用 `model_predictions` 中 `WDL_away` 原始概率（generate_unified_report 写入），**未叠加 TemperatureScaling 校准**。已知原始概率存在系统性高估 edge 倾向（C-20260905 系列），此偏差属预注册接受项——若校准后仍为正 edge，原始概率口径只会给出**更保守**的投注数量，不会系统性偏向命中。

## 三、数据流与工具链

```
sporttery_live_collector.py         竞彩赔率时序 → odds.db wdl_history
        ↓（每日）
generate_unified_report.py --date    四维预测 → odds.db model_predictions (WDL_away)
        ↓（每日）
live_trial_away_favorite.py         三源桥接 + 规则过滤 → 投注单 + 台账
```

- 三源桥接 key：
  - 赛事清单：`odds500_match`（season='26/27', status=1, 未来窗口）
  - 竞彩赔率：`{date}_{normalize(home_cn)}_{normalize(away_cn)}`，日期容差 ±1 天（竞彩官方日 vs 当地日）
  - 模型概率：`{date}_{home_en}_{away_en}`（保留空格，与库内 match_id 原样一致）
- 台账：`data/live_trial_away_favorite_ledger.csv`
  - 列：bet_id, match_id_en, match_date, league, home_en, away_en, away_odds, p_away, ev, stake_cny, status(pending/settled), result(W/L), profit_cny, slip_ts
- 结算：`--settle` 用 odds500_match（status=5）实际比分判定客胜，回填 result/profit。

## 四、评估准则（300 注满后判定）

| 指标 | 判定 |
|------|------|
| 平注 ROI | > 0 且 95% 置信区间不包含 0（z ≥ 1.96，n ≥ 100）→ 确认正 edge |
| 命中率 | vs 1/平均赔率 盈亏平衡点，z 检验 |
| 分段时间 | 按季度/赛季分段，正收益段占比 |
| 判定规则 | 三段（ROI 显著 / ROI 为正但不显著 / ROI 为负） |
| 中途熔断 | 不设（规则冻结）；累计 300 注后停止 |

**注意**：z 检验在赔率长尾下会高估显著性（Jensen 效应，C-20260905-004 教训），最终以**净盈亏金额**为准绳，显著性仅作参考。

## 五、日志与审计

- 每笔投注记录：赔率/概率/EV/时间戳，台账可追溯至当日投注单报告（`reports/live_trial_slip_<TS>.md`）。
- 每日运行节奏：采集竞彩 → 生成预测 → dry-run 查看 → `--commit` 提交 → 次日 `--settle` 结算。
- 投注单报告含拒绝原因统计（no_wdl/no_pred/odds>cap/ev<=0/dup/stop），保证「为什么不投」也可审计。

## 六、风险声明

- 本验证为**真实资金**投入，预期总投入 ≤ ¥6,000（300 注 × ¥20）。
- 预注册规则基于历史回测的弱信号（统计不显著），**大概率真实 ROI 为负**；验证目的是收集样本，不是追求盈利。
- 若 300 注未满但样本已足以判定（如 100 注 ROI 显著为负），需人工决策是否提前终止——此决策须记录于 change_log，不改变规则本身。
