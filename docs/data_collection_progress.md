# 五大联赛数据采集进度汇总

> **创建时间**: 2026-08-25
> **最后更新**: 2026-09-17
> **用途**: 统一标注三大数据源（500.com / SofaScore / Understat）+ Sporttery 竞彩赔率的采集完成情况与完整度，供预测管线与人工巡检参考
> **关联文档**: docs/change_log.md §3.63/§3.70/§3.170~3.172/§3.190, docs/project_memory.md §6.3/§6.3.4, docs/archive/故障排查报告_数据采集_20260912.md, .trae/skills/local-football-scraper/SKILL.md

> **2026-09-17 更新摘要（C-20260917-001~003，实测口径）**：
> 1. **500.com 反爬空壳误报修复**：新增 `anti_bot_blocked` 状态，空壳表格（全 `-` / company_count=0 / 必发成交为空）现判为 EdgeOne 反爬拦截（FAIL），不再误判为「源站无数据」（WARN）；采集端 + 报告端三层闭环：识别 → 三态展示 → 刷新 Cookie 重采恢复。
> 2. **西甲前 6 轮投注/欧指重采恢复真实数据**：用户刷新 Cookie 写入 `data/cookies_500.json`（三层凭证 + `__user_agent`），西甲 26/27 前 6 轮 `--no-skip-existing` 重采，多数场次由空壳恢复为 ok，主胜必发成交量恢复至百万级。
> 3. **赛前报告投注章节三态化**：`generate_unified_report.py` 投注热度章节三分流（✅ OK / ❌ FAIL 反爬 / ⚠️ WARN 源站无数据），09-17 西甲 4 场报告投注热度 100% 完整。
> 4. **运维注意**：PowerShell 下运行报告脚本前须设 `$env:PYTHONIOENCODING='utf-8'`，否则 emoji 输出因 GBK 编码崩溃。

> **2026-09-12 更新摘要（C-20260912-001~003，实测口径）**：
> 1. **500.com 反爬突破**：采集器已升级 **curl_cffi（impersonate="chrome"）+ 手动 EdgeOne Cookie**（`data/cookies_500.json`，三层凭证，每日人工 Edge 导出），4 场 09-12 赛前报告 500 维度全部补齐（各 company_count=44、30 家公司明细）；
> 2. **亚盘刷新通道修复**：`--refresh-odds` 现可补回「先入库后完赛」场次的结算盘口；26/27 五联赛赛程 **1,752 场已全入库**，其中 **113 场有亚盘盘口、74 场完赛结算盘口 74/74 = 100%**；
> 3. **SofaScore 补采 19 场**：巴伦西亚 3 场缺失评级（球队评分 2.80→6.54）+ 6 队 16 场新赛季球员统计（16/16），特征表增至 18,363 行、player_stats 730,128 行；
> 4. 五表行数：odds500_match **19,790** / ouzhi_summary **19,761** / ouzhi_company **542,546**。

> **2026-09-13 补采记录（C-20260913-002，实测口径）**：
> 1. Sporttery 当日采集成功：13 场新增 WDL **29** / HCP **39** / TTG **25** / CRS **560** 行；未开售的比赛保持源站缺失，不人为填充。
> 2. SofaScore 26/27 第 5 轮断点核验：五联赛 **48 场均已有采集记录**；增量特征检查 2026-09-13 起缺失 **0 场**，当日特征有效覆盖 **77/77**。
> 3. 500.com 第 5 轮强刷：**48 场**执行完成；公司明细和投注表有实际写入，但本轮新写入的 `odds500_ouzhi_summary.company_count` **48/48 为 NULL**，因此本次 500.com 补采状态为“明细部分成功、摘要字段待修复”，不能标记为完整成功。
> 4. 重新生成报告后，生成器因排除已开赛场次，汇总由原 **20 场变为 17 场**；原赛前报告保留，不用事后生成结果覆盖历史报告。西甲塞维利亚 vs 巴伦西亚报告仍显示旧的 SofaScore 缺失状态，需单独修复报告回写/匹配链路。

---

## 附 A：模型概率校准与验证产物（EV 期望值引擎所需）

> 本节与 §一~§七 的「数据采集」并列，作为概率校准相关「验证数据产物」的台账，供 EV 引擎回测与优化迭代对齐（change_log §3.106 C-20260903-006 同步）。

| 产物 | 路径 | 用途 | 样本量 | 生成方式 | 状态 |
|------|------|------|--------|----------|------|
| 严格时序 OOF 概率（XGB/LGB raw + Platt） | `assets/oof_predictions_20260903_012452.csv` | EV 期望值引擎回测基准（无泄漏对照） | 11,965 场 | `scripts/generate_oof_predictions.py`：208 维 selected_features + TimeSeriesSplit(5) + 折内 Platt 校准 | 🟢 生产级，已对齐赔率 96.4% |
| 逐场 6 口径校准后概率（Platt/Temp/Vector/Iso/Temp+DC/Iso+DC） | `assets/calibrated_probs_20260903_163558.csv` | 校准对比基线；下游 EV 择场迭代的可复用输入 | 11,965 场 | `scripts/probability_calibration_benchmark.py`：TimeSeriesSplit(5) 折内 fit/折外 transform 拼回全量 | 🟢 已与 ev_engine 回测链路验证 |
| 校准对比报告（MD+JSON） | `reports/calibration_benchmark_20260903_163558.md/.json` | 校准选型证据链（LogLoss/ECE/平局召回/EV ROI 四维对比） | 6 方案 × 11,965 场 | 同上脚本末节自动生成 | 🟢 结论：TempScaling(on raw) 为当前唯一兼顾平局召回≥0.28 且 ROI 优于基线的方案（平ROI -3.71% vs -5.41%基线，Δ+1.70pp），ROI 仍未转正需叠加择场机制 |
| EV 择场阈值抬升扫描报告（MD+JSON） | `reports/ev_threshold_sweep_20260904_011100.md/.json` | EV 择场「min_ev 阈值抬升 × 置信过滤」组合扫描证据链（72 组合 ROI 矩阵 + 最优组合分赛季/分方向） | 3 方案 × 6 min_ev × 4 置信 × 9,970 场 | `scripts/ev_threshold_sweep.py`：复用 `calibrated_probs_20260903_163558.csv` + ev_engine + odds500_live 赔率桥接，仅对 EV>threshold 的 VALUE 方向下注 | 🟢 结论：72 组合全量无转正（n≥100）；最优 TempScaling(on raw)+min_ev=0.10+置信P50 平ROI -2.93% 仍为负；阈值/置信抬升均使 ROI 恶化（edge 排序失效 + 高置信被高赔率爆冷绑架），EV ROI 转正须进训练端（C-20260904-001 / CALIB-008） |
| EV-loss OOF 概率（B1 λ=1.0 / B2 λ=0.3 / B3 λ=3.0） | `assets/oof_evloss_l1.0_B1_20260904_224059.csv` / `_l0.3_B2_..._224800.csv` / `_l3.0_B3_..._225359.csv` | 训练端 EV/ROI 目标改造验证输入（朴素 EV-policy Loss 三档 λ 扫描） | 3 档 × 11,965 场 | `scripts/generate_oof_evloss.py`：`ev_loss.py` 自定义目标（CE+λ·EVL）+ odds500 收盘均价按 [客胜,平局,主胜] 对齐训练折（96.2%）+ TSS(5) | 🔴 结论：三档全负全面劣于基线（Mono-Pooled(on Temp) 平ROI B2 -8.50% / B1 -8.18% / B3 -10.99% vs 基线 -3.74%）；LGB 数值退化（早停 1~3 轮、平局召回 0）、XGB 平局召回受压；理论根因 `dEVL/dp_y=−o_y` 加剧高赔率方向过度自信 → 朴素「对 EV 直接求导」证伪，需重新设计训练目标（C-20260904-003）。**注：其中 LGB「数值退化」经 C-20260904-004 定位为 LightGBM 4.x 自定义目标 class-major 布局 bug（order='C' reshape 错乱）所致，已修复；XGB 结论不受影响** |
| MOD 蒸馏 OOF 概率（D1 λ=1.0 γ=1.0） | `assets/oof_modloss_l1.0_g1.0_D1_20260904_233636.csv` | 训练端系统性高估根治二连击——市场赔率蒸馏（CE+λ·KL 拉向去抽水市场隐含概率）OOF 验证输入 | 1 档 × 11,965 场 | `scripts/generate_oof_evloss.py --loss mod`：`mod_loss.py` 自定义目标（梯度 p−q_soft、海森恒正）+ 布局修复（order='F'）+ TSS(5) | 🔴 结论：D1 证伪——LGB 训练恢复（5 折 RPS 全达标 0.1935~0.2003）但**平局召回全线崩溃**（5.9%~10.2% vs 基线 32.3%，违反 CALIB-003 ≥0.28 硬约束）；EV 回测 ROI 全部仍负（Mono-Pooled(on raw) -3.55% ≈基线 / Mono-Selected(on Temp) -1.38% 但非单调 n=2521），转正组合 0 个；向市场蒸馏 = 押注市场正确、抹掉模型学「市场定价错误」能力，原理上无法产生正 edge → **MOD 方向证伪（C-20260904-004）** |
| Penalty 惩罚 OOF 概率（E1 λ=1.0 t=3.5） | `assets/oof_penloss_l1.0_t3.5_E1_20260905_010415.csv` + 评估报告 `reports/edge_monotonic_fix_20260905_173810.md` | 训练端系统性高估根治三连击收官——高赔率未命中显式惩罚（CE 锚点 + λ·P，对抗高赔率方向过度自信）OOF 验证输入 | 1 档 × 11,965 场 | `scripts/generate_oof_evloss.py --loss penalty --lambda-pen 1.0 --odds-thresh 3.5`：`penalty_loss.py` 自定义目标（惩罚梯度 p(w−P)、海森可负 floor 保护）+ Fortran-order 布局 + TSS(5) | 🔴 结论：E1 证伪——5 折 RPS 全达标（XGB 0.1995~0.2030 / LGB 0.2016~0.2045）但平局召回仍受压（raw argmax XGB 2.59% / LGB 8.11%，Baseline(Platt) 4.90%）；EV 回测 ROI 全面劣于既有最优校准基线（Mono-Pooled(on Temp) -5.59% vs 基线 -3.74%），>10pp 桶高估幅度未减（12.8pp ≈ 基线 13.0pp），转正组合 0 个 → **训练端 EV 奖励 / MOD 模仿 / Penalty 惩罚 三条路径全部封闭（C-20260905-001）** |
| 回测数据质量审计报告 | `reports/backtest_odds_quality_audit_20260905_175257.md` | EV 回测市场侧数据质量验证（系统性高估是否源于 edge 估值被污染） | 18,082 场有效赔率 / 11,937 场两源对齐 | `scripts/audit_backtest_odds_quality.py`：A1 按赛季隐含概率总和分布 + A2 异常样本面 + A3 竞彩/500.com 桥接对齐水位差 | 🟢 结论：**隐含概率异常已基本消除**（仅 0.2% 样本 impl_sum<1.0，集中 2023/2024），覆盖率按赛季均匀；两源水位差为结构性（竞彩 13% vs 500.com 5.4% 抽水）→ **回测数据干净，系统性高估非数据伪影（C-20260905-002）** |
| 市场水平错位测试报告 | `reports/market_level_mismatch_20260905_175841.md` | 验证「模型概率贴近高抽水竞彩 raw 隐含 → edge 被系统性抬高」假设 | 11,965 场（竞彩对齐 10,199 / 500.com 对齐 11,425） | `scripts/market_level_mismatch_test.py`：模型 blend platt vs 两源去抽水/raw 隐含 + 实际边际 + 按赔率档 max 方向偏差 | 🔴 结论：**水平错位假设证伪**——模型三向水平贴近去抽水真实概率（非竞彩 raw），但 **max 方向选择膨胀 +3~7pp**、>10pp 桶高估 12.8pp → 真问题=选择效应+分歧效应（C-20260905-003） |
| 决策层 edge 收缩测试报告 | `reports/edge_shrink_test_20260905_180719.md` | 决策层均匀收缩 edge（p_used = p_market + s·(p_model−p_market)）能否对冲分歧过度自信 | 5 档 s × 9,970 场 | `scripts/edge_shrink_test.py`：TSS(5) 折内 TempScaling + s∈{1.0,0.75,0.5,0.25,0.0} EV 回测 | 🔴 结论：**证伪**——最优 s=0.75 仅 +0.12pp（-3.71%→-3.59%），ROI 卡 ~-3.6% 无法转正，转正组合 0 个；收缩无法修复选择膨胀（C-20260905-003） |
| 决策层分段诊断报告 | `reports/edge_segment_test_20260905_181046.md` | 方向×赔率档 ROI 剖面 + 赔率上限扫描（低赔率热门方向是否有真实 edge） | 9,446 笔价值投注 | `scripts/edge_segment_test.py`：TSS(5) TempScaling + 逐场 EV 回测分段统计 | ⚠️ 结论：价值投注 **77% 是平局**（n=7,291，-4.47% 全线亏损=主出血点）；cap=2.5 首次整体转正（+1.44%，n=751），正收益全部来自客胜热门（away≤2.5 +1.69%）（C-20260905-003） |
| 热门段稳健性验证报告 | `reports/favorite_edge_robust_20260905_232345.md` | cap∈{2.0,2.25,2.5,2.75} 分折/分赛季/分方向验证客胜热门段是否跨时间稳健 | 9,446 笔价值投注 | `scripts/favorite_edge_robust.py`：TSS 折标签 + 赛季分界 + 命中率 vs 盈亏平衡 z 检验 | 🔴 结论：**统计不显著**——cap=2.0 ROI +1.49% 但 z≈+0.57、分折仅 3/5 正、2018/2019 赛季全线大幅负；多重比较预期内的伪信号，**模型正 edge 无法在当前模型×市场组合下统计确认**（C-20260905-003） |
| 换投注市场重估 ROI 报告 | `reports/market_switch_backtest_20260905_233102.md` | 同一模型概率对四市场（500live/500init/竞彩末条/威廉希尔live）EV 回测 + 真实回收率 = 抽水 + ROI | 9,970 场折外（四市场共用 temp_probs） | `scripts/market_switch_backtest.py`：OOF + TSS 折内 TempScaling + 四市场赔率映射构建 | ⚠️ 结论：模型相对**竞彩末条**真实回收最高（+5.32pp = 抽水 +13.6% + ROI -8.32%），500live/500init/威廉希尔live 为 +1.78/+1.94/+1.20pp；但四市场 ROI 全部为负、转正组合 0 个（C-20260905-004） |
| 竞彩市场回收率稳健性验证报告 | `reports/market_recovery_robust_20260905_233702.md` | 验证模型相对竞彩市场 +5.3pp 回收优势是否统计显著且跨折/跨赛季/跨方向稳健 | 7,436 笔竞彩价值投注 | `scripts/market_recovery_robust.py`：分折（TSS5）/分赛季/分方向 + 命中率 vs 盈亏平衡 z 检验 | 🔴 结论：**统计不显著**——整体真实回收 +5.30pp 但 z=+0.67；分折 4/5 正、分赛季 8/9 正（2019/2020 z=-2.11 显著负）；away 方向 z=+4.74 显著为正但 ROI -6.78% 为负（Jensen 效应）→ **换市场路径证伪，模型正 edge 在所有候选市场均无法统计确认**（C-20260905-004） |
| RL Phase A 策略梯度报告 | `reports/rl_bet_policy_pg_20260905_234603.md` | RL 长期累积 EV 优化目标 Phase A——上下文赌博机策略梯度，策略层自由学习「投/不投/投哪」能否产生统计显著正 ROI | 9,552 笔 bandit 样本（500live 对齐） | `scripts/rl_bet_policy_pg.py`：softmax 期望回报最大化（全枚举无采样）+ λ_ent 熵正则 + Adam + TSS(5) 折内 fit/折外 argmax | 🔴 结论：**证伪**——λ_ent=0.01 最优时策略自然收敛「90.8% 不投 + 仅客胜 875 笔」，ROI +0.79% 但 **z=+0.14 不显著**、分折不稳、2022/2023 赛季 -28.4%；强熵（0.1/1.0）投注量升但 ROI -9.22%/-3.74% 更差 → **RL 路径证伪，系统性高估调查全部闭环**（C-20260905-005） |
| 实盘小注验证（客胜热门段）工具链 | `scripts/live_trial_away_favorite.py` + `data/live_trial_away_favorite_ledger.csv` + `docs/live_trial_away_favorite_protocol.md` | 预注册规则实盘验证（away≤2.5 / EV>0 / ¥20 平注 / 300 注停止）——每日「采竞彩→跑预测→生成投注单→结算」数据驱动收集新样本 | 首日 0 笔（14 场可桥接：3 场赔率>2.5、5 场 EV≤0） | `sporttery_live_collector.py`（竞彩当日 14 场，+WDL 14/HCP 16/TTG 14/CRS 420）+ `generate_unified_report.py --date`（回写 12 场 model_predictions）+ 三源桥接；**每日六步编排已自动化**（C-20260910-007：`scripts/run_daily_p0e.py` + 计划任务 `SoccerModel_P0ELiveTrial` 每日 12:00 --auto-commit，单步失败即中断、日志 logs/run_daily_p0e_*.log） | 🟡 进行中——自动化编排就绪，规则正常运作（宁可少投不可滥投，单日候选 0 属预期）；台账 0/300，预计需数周累积（C-20260906-001 / C-20260910-006/007） |

---

## 一、数据源总览

| 数据源 | 用途 | 入库位置 | 采集器 | 状态 |
|--------|------|----------|--------|------|
| **500.com** | 投注分析 / 百家欧指 / 赛后技术统计 / 亚盘盘口 | odds.db `odds500_*` | collection/final_500_collector.py | 🟢 进行中（curl_cffi+EdgeOne Cookie，C-20260912-001） |
| **SofaScore** | 球队级 46 维特征 + 球员级单场统计 + 阵容/事件 | odds.db `match_player_stats` / `sofascore_team_features` | collection/final_sofascore_collector.py | 🟢 已覆盖多赛季 |
| **Understat** | xG 体系（比赛级/球员级/射门级） | odds.db `understat_*` 三张表 | collection/final_understat_collector.py | 🟢 五大联赛 10 赛季完整 |
| **Sporttery 竞彩** | WDL/让球/总进球/比分时序赔率（WDL 特征核心输入） | odds.db `wdl_history` / `handicap_history` / `total_goals_history` / `score_history` | collection/supplement_sporttery_odds.py + scripts/sporttery_live_collector.py | 🟢 双通道对齐 98.3% |

---

## 二、500.com 采集进度

### 2.1 采集器能力

| 维度 | 说明 |
|------|------|
| 支持联赛 | 五大联赛（英超/西甲/意甲/德甲/法甲），`SEASONS`（16/17~26/27 共 11 季 stid）+ `CN_TO_EN` + `_EXTRA_CN_TO_EN`（31 个历史升降班马） |
| 支持赛季 | 五大联赛 16/17 ~ 26/27（11 季）。轮数：英/西/意 38 轮；德甲 34 轮；法甲 16/17~22/23 为 38 轮（20 队）、23/24 起 34 轮（18 队） |
| 状态过滤 | ✅ 已加 `status==5` 过滤（C-20260825-005）：未赛场次跳过赛后技术统计、比分写 NULL |
| 反爬机制 | ✅ **curl_cffi `Session(impersonate="chrome")` 模拟浏览器 JA3 指纹 + 手动 EdgeOne Cookie**（`--cookies data/cookies_500.json`，三层凭证 `__tst_status`/`EO_Bot_Ssid`/`EO-Bot-Captcha-Token` + `__user_agent`；每日人工 Edge 导出；C-20260912-001，详见 §2.6） |
| 亚盘刷新 | ✅ `--refresh-odds --season 26/27 --rounds 1-8`：完赛场结算盘口全量覆盖、未开赛场仅补空盘（C-20260912-003，详见 §2.6） |
| 三类页面 | 投注分析 (touzhu) / 百家欧指 (ouzhi) / 技术统计 (stat)；赛程接口 getmatch 提供亚盘 handline/pan 与欧赔 win/draw/lost |
| 写入表 | `odds500_match` / `odds500_betting` / `odds500_ouzhi_summary` / `odds500_ouzhi_company` / `odds500_stat` |

### 2.2 已入库现状

**2026-09-12 实测（最新，共 19,790 行 = 历史 18,038 + 26/27 五联赛 1,752）**：

| 联赛 | 26/27 赛程 | 已完赛(status=5) | 有亚盘盘口 | 完赛已结算盘口 | 说明 |
|------|:----:|:----:|:----:|:----:|------|
| 英超 | 380 | 12 | 19 | 12 | 🟡 进行中，未开盘场次待 --refresh-odds |
| 西甲 | 380 | 22 | 28 | 22 | 🟡 进行中 |
| 意甲 | 380 | 29 | 38 | 29 | 🟡 进行中（1-8 轮已刷新，盘口 27→38） |
| 德甲 | 306 | 1 | 9 | 1 | 🟡 进行中 |
| 法甲 | 306 | 10 | 19 | 10 | 🟡 进行中 |
| **合计** | **1,752** | **74** | **113** | **74（100%）** | 完赛结算盘口零缺口（C-20260912-003） |

> 关联表（2026-09-12）：odds500_ouzhi_summary 19,761 行、odds500_ouzhi_company 542,546 行。历史 16/17~25/26 共 18,038 场维持 100% 完整。

**2026-08-27 历史快照（共 18,418 行，归档留痕）**：

| 联赛 | 赛季 | 场次 | status | 说明 |
|------|------|:----:|:------:|------|
| 五大联赛 | 16/17 | 1,826 | 1,826 已赛 | 🟢 100% |
| 五大联赛 | 17/18 | 1,826 | 1,826 已赛 | 🟢 100% |
| 五大联赛 | 18/19 | 1,826 | 1,826 已赛 | 🟢 100% |
| 五大联赛 | 19/20 | 1,826 | 1,826 已赛 | 🟢 100%（法甲疫情赛季，赛程含 101 场未赛） |
| 五大联赛 | 20/21 | 1,826 | 1,826 已赛 | 🟢 100% |
| 五大联赛 | 21/22 | 1,826 | 1,826 已赛 | 🟢 100% |
| 五大联赛 | 22/23 | 1,826 | 1,826 已赛 | 🟢 100% |
| 五大联赛 | 23/24 | 1,752 | 1,752 已赛 | 🟢 100% |
| 五大联赛 | 24/25 | 1,752 | 1,752 已赛 | 🟢 100% |
| 五大联赛 | 25/26 | 1,752 | 1,752 已赛 | 🟢 100% |
| 英超 | 26/27 | 380 | 10 已赛 / 370 未赛 | 🟡 当前赛季（进行中） |

> 分联赛累计：英超 4,180 / 西甲 3,800 / 意甲 3,800 / 德甲 3,060 / 法甲 3,578，合计 18,418 行。
> 关联表：odds500_betting 18,418 / odds500_ouzhi_summary 18,413（差 5，个别场次欧指缺失）/ odds500_ouzhi_company 539,310 / odds500_stat 17,946（已赛场次全覆盖）。
> **剩余缺口**：无（16/17 法甲 380 场已补齐，16/17~25/26 已结束赛季全部 100% 完整，仅剩 26/27 进行中赛季）。

### 2.3 回采计划（命令3：对标 SofaScore/Understat）

> 对标范围 = SofaScore 球队特征（五大联赛多季）+ Understat（五大联赛 × 10 季 16/17~25/26）。

**阶段一（21/22~25/26，对标 SofaScore 球队特征）— ✅ 已完成（2026-08-26 全量补齐）**

> 21/22（1,826）+ 22/23（1,826）+ 23/24（1,752）+ 24/25（1,752）+ 25/26（1,752）五大联赛已结束赛季全部 100% 完成，四表（match/betting/ouzhi/stat）全覆盖。

**阶段二（16/17~20/21 扩展，对标 Understat 10 季）— ✅ 已完成（16/17 法甲 380 场已补齐）**

| 联赛 | 已采 | 目标 | 缺口 | 说明 |
|------|:----:|:----:|:----:|------|
| 英超 | 1,900 | 1,900 | 0 | 5 季 × 380 ✅ |
| 西甲 | 1,900 | 1,900 | 0 | 5 季 × 380 ✅ |
| 意甲 | 1,900 | 1,900 | 0 | 5 季 × 380 ✅ |
| 德甲 | 1,530 | 1,530 | 0 | 5 季 × 306 ✅ |
| 法甲 | 1,900 | 1,900 | **0** | 16/17 法甲已补齐 ✅ |
| **合计** | **9,130** | **9,130** | **0** | 16/17~20/21 全量完成 ✅ |

> 16/17 法甲 380 场已补齐（stid=9854 接口正常）；此前系后台采集中途漏采。
> 法甲轮数：16/17~22/23 为 20 队 38 轮；23/24 起 18 队 34 轮（见 change_log §3.71/§3.72）。
> 19/20 法甲因新冠提前结束：500.com 赛程枚举 380 场（含 101 场未赛），已赛 279 场有 stat、未赛无 stat（口径正常）。

### 2.4 数据校验（2026-08-27 实测）

| 校验项 | 期望 | 实际 | 结果 |
|--------|------|------|------|
| odds500_match 总行数 | — | 18,418 | ✅ |
| odds500_stat 已赛场次 | = 已赛 match 数（17,946） | 17,946 | ✅ |
| odds500_betting 覆盖 | = match 数（18,418） | 18,418 | ✅ |
| odds500_ouzhi_summary 覆盖 | ≈ match 数 | 18,413（差 5，个别场次欧指缺失） | 🟡 基本全量 |
| 16/17 五大联赛 | 1,826 | 1,826 | ✅ |
| 17/18 五大联赛 | 1,826 已赛 | 1,826 已赛 | ✅ |
| 18/19 五大联赛 | 1,826 已赛 | 1,826 已赛 | ✅ |
| 19/20 五大联赛 | 1,826 已赛 | 1,826 已赛 | ✅（法甲含 101 未赛） |
| 20/21 五大联赛 | 1,826 已赛 | 1,826 已赛 | ✅ |
| 21/22 五大联赛 | 1,826 已赛 | 1,826 已赛 | ✅ |
| 22/23 五大联赛 | 1,826 已赛 | 1,826 已赛 | ✅ |
| 23/24 五大联赛 | 1,752 已赛 | 1,752 已赛 | ✅ |
| 24/25 五大联赛 | 1,752 已赛 | 1,752 已赛 | ✅ |
| 25/26 五大联赛 | 1,752 已赛 | 1,752 已赛 | ✅ |
| 未赛残留 stat | 0 | 0 | ✅ |
| 26/27 英超比分 NULL（370 未赛） | 370 | 370 | ✅ |

> **历史清理记录**: 2026-08-25 16:43 执行 `DELETE FROM odds500_stat WHERE fid IN (SELECT fid FROM odds500_match WHERE status != 5)`，删除未赛场次残留脏技术统计（C-20260825-006）。

### 2.5 待扩展项

- ~~**26/27 五大联赛（当前赛季）**：`SEASONS` 字典补 stid~~ ✅ 已完成（英超 27953 / 西甲 28016 / 意甲 27864 / 德甲 28025 / 法甲 27893），1,752 场赛程全入库；亚盘/欧赔随赛季推进用 `--refresh-odds` 滚动补
- **26/27 亚盘盘口滚动补齐**：113/1,752 已有盘口；第 5 轮后多数场次尚未开盘，需在临近开赛前重复执行 `--refresh-odds`（前置条件：当日有效的 EdgeOne Cookie）
- **法甲 19/20（疫情缩水赛季）**: Understat 该赛季仅 279 场，500.com 采集时需确认实际可采场次与轮数
- 扩展方式：访问 `https://liansai.500.com/` 找到目标联赛+赛季的 stid，加入 `SEASONS` 字典

### 2.6 2026-09-12 EdgeOne 反爬应对与亚盘刷新（C-20260912-001/003）

> 完整排查过程见 `docs/archive/故障排查报告_数据采集_20260912.md`，运维细则见 project_memory.md §6.3.4。

**反爬（已解决）**：500.com 部署腾讯云 EdgeOne 两层人机防护，`requests` 因 JA3 指纹被拦（采集结果为空壳行）。采集器已改用 `curl_cffi`（`Session(impersonate="chrome")`），配合人工导出的 Cookie 通过：
- Cookie 文件默认 `data/cookies_500.json`（`--cookies` 可覆盖），需含 `__tst_status`、`EO_Bot_Ssid`、`EO-Bot-Captcha-Token`（真人勾选后写入，与 UA 绑定，JSON 内用 `__user_agent` 记录导出浏览器 UA）；
- **Cookie 需每日人工从自己的 Edge 浏览器导出**；高频请求会触发 EdgeOne 升级防护，保持 delay、避免连续探测；
- 正常全量采集命令不变：`python collection/final_500_collector.py --season 26/27 --league all --rounds all --delay 0.5`。

**亚盘刷新（已修复）**：原 `refresh_upcoming_odds` 对 `status==5` 完赛场直接跳过，导致「先入库、后完赛」比赛的结算亚盘永远缺失。现分两情形：完赛场用结算 handline/pan/欧赔全量覆盖并补比分、纠正 stale status；未开赛/进行中场仅当库内盘口为空时补齐。

```powershell
# 26/27 亚盘/欧赔滚动刷新（幂等，只走 getmatch 赛程接口；临近开赛每日跑）
cd f:\zuqiu\五大联赛专属模型\五大联赛专属模型
C:\Python314\python.exe collection\final_500_collector.py --refresh-odds --season 26/27 --rounds 1-8
```

**2026-09-12 补采验证**：4 场赛前报告（柏林联合vs沙尔克04 / 威尼斯vs佛罗伦萨 / 雷恩vs马赛 / 塞维利亚vs巴伦西亚）500 维度均写库（summary company_count=44、公司明细各 30 家、betting 各 1 行），报告完整度 100%。

**完整度口径**：报告 500 维度完整度 = 有行且 company_count>0（或有公司明细）；空壳占位行计缺失（generate_unified_report.py L566-571）。

**空壳 vs 反爬判定（2026-09-17 修正，C-20260917-001）**：
- 投注表格全 `-` / 必发成交量全 0 / company_count=0 / 页面含「正在验证」「安全验证」「EdgeOne」等字样 → **`anti_bot_blocked`（反爬拦截，FAIL）**，不是源站无数据，必须刷新 Cookie 重采。
- 仅当源站真实业务无数据（未开盘 / 无投注数据 / 无欧指公司）才判 `source_no_data`（WARN）。
- 二者不可混用，旧空壳数据统一按 FAIL 降级处理。

---

## 三、SofaScore 采集进度

### 3.1 采集器能力

| 维度 | 说明 |
|------|------|
| 支持联赛 | 五大联赛（英超/西甲/意甲/德甲/法甲），`LEAGUES_CONFIG` 全配置 |
| 支持赛季 | 11 个赛季（16/17 ~ 26/27），每联赛均有 season ID |
| 反爬机制 | curl_cffi impersonate="chrome" 绕过 Akamai TLS 指纹 |
| 四接口 | event / statistics / lineups / incidents |
| 写入表 | `match_player_stats` / `match_lineups` / `fbref_match_mapping` / `fbref_players` / `sofascore_team_features`（98 字段 = 68 sofa_* + 24 pa_*） |

### 3.2 球员级统计（match_player_stats，stats_source='sofascore'）

| 指标 | 值 | 说明 |
|------|:--:|------|
| 总球员行 | 730,128 | 平均约 40 球员/场（2026-09-12 实测；09-12 补采 19 场 +1,605 行） |
| 时间范围 | 2016-08 ~ 2026-09 | 月份分布见 §3.4 |
| 26/27 新赛季 | 08-22~09-07 缺口已补 19 场 | 巴伦西亚 3 场评级 + 6 队 16 场统计（C-20260912-001），后续轮次依赖赛后采集链路 |

### 3.3 球队级聚合特征（sofascore_team_features，98 字段 = 68 维 sofa_* + 24 维 pa_*）

> P1-9 前置重建（C-20260828-002）：由 23/24 起三季扩至 16/17~26/27 全季，含 26/27 已排赛程的赛前特征预生成。24 维 pa_* 球员可用性特征已于 2026-09-12 接入统一报告第十二章（C-20260912-002）。

| 联赛 | 行数 | 覆盖范围 |
|------|:----:|------|
| 英超 | 3,940 | 2016-08 ~ 2026-09 |
| 意甲 | 3,876 | 2016-08 ~ 2026-09 |
| 西甲 | 3,877 | 2016-08 ~ 2026-09 |
| 法甲 | 3,564 | 2016-08 ~ 2026-09 |
| 德甲 | 3,106 | 2016-08 ~ 2026-09 |
| **合计** | **18,363** | 98 字段（68 sofa_* + 24 pa_*），2026-09-12 实测 |

### 3.4 月份分布（match_player_stats，节选近 3 赛季）

| 月份 | 场次 | 月份 | 场次 | 月份 | 场次 |
|------|:----:|------|:----:|------|:----:|
| 2023-08 | 124 | 2024-08 | 113 | 2025-08 | 116 |
| 2023-09 | 183 | 2024-09 | 183 | 2025-09 | 162 |
| 2023-10 | 174 | 2024-10 | 153 | 2025-10 | 164 |
| 2023-11 | 144 | 2024-11 | 161 | 2025-11 | 179 |
| 2023-12 | 230 | 2024-12 | 207 | 2025-12 | 182 |
| 2024-01 | 135 | 2025-01 | 181 | 2026-01 | 216 |
| 2024-02 | 204 | 2025-02 | 196 | 2026-02 | 195 |
| 2024-03 | 176 | 2025-03 | 166 | 2026-03 | 176 |
| 2024-04 | 206 | 2025-04 | 206 | 2026-04 | 181 |
| 2024-05 | 178 | 2025-05 | 185 | 2026-05 | 187 |
| — | — | — | — | 2026-08 | 52 |

> 2023-08 ~ 2026-05 覆盖完整（每月 130~230 场，符合五大联赛节奏）；2026-08 仅 52 场（26/27 新赛季首轮部分，待补全）。

### 3.5 完整度评估

| 赛季区间 | 联赛覆盖 | 完整度 | 备注 |
|----------|----------|:------:|------|
| 16/17 ~ 25/26 | 五大联赛全 | 🟢 ~95%+ | 10 赛季历史数据基本完整（18037 场） |
| 26/27（进行中） | 五大联赛前几轮 | 🟡 赛前特征已预生成，赛后球员统计滚动补采 | 09-12 已补 19 场（16/16 成功）；后续轮次需赛后管线（`run_post_match_pipeline.py --catch-up`）按时触发，防 08-22~09-07 式大面积缺行重演 |

---

## 四、Understat 采集进度

### 4.1 采集器能力

| 维度 | 说明 |
|------|------|
| 支持联赛 | 五大联赛（slug: EPL/La_liga/Serie_A/Bundesliga/Ligue_1） |
| 支持赛季 | 任意赛季（`--season YY/YY`，内部转起始年份如 26/27→2026） |
| 数据端点 | getLeagueData/{slug}/{season}（比赛列表）+ getMatchData/{id}（rosters+shots） |
| 写入表 | `understat_match_team_stats` / `understat_player_xg` / `understat_shots`（均含 league+season 字段） |
| 过滤机制 | 默认只抓已完赛（isResult=true），`--include-fixtures` 可含未赛 |

### 4.2 比赛级（understat_match_team_stats）联赛×赛季分布

| 联赛 | 16/17 | 17/18 | 18/19 | 19/20 | 20/21 | 21/22 | 22/23 | 23/24 | 24/25 | 25/26 | 26/27 |
|------|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|:-----:|
| 英超 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 1 |
| 西甲 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 20 |
| 意甲 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | 380 | — |
| 德甲 | 306 | 306 | 306 | 306 | 306 | 306 | 306 | 306 | 306 | 306 | — |
| 法甲 | 380 | 380 | 380 | 279* | 380 | 380 | 380 | 306 | 306 | 306 | 1 |

> *法甲 19/20 赛季因新冠疫情提前结束，仅 279 场（非数据缺失）。
> 法甲 23/24 起缩编为 18 队（306 场/季），22/23 及之前为 20 队（380 场/季）。

### 4.3 球员级 + 射门级

| 联赛 | 球员级 distinct_match | 球员级总行 | 射门级 distinct_match | 射门级总行 |
|------|:--------------------:|:----------:|:--------------------:|:----------:|
| 英超 | 3,801 | 108,710 | 3,801 | 96,739 |
| 西甲 | 3,808 | 113,900 | 3,808 | 90,767 |
| 意甲 | 3,800 | 113,687 | 3,800 | 98,892 |
| 德甲 | 3,059 | 90,981 | 3,059 | 79,723 |
| 法甲 | 3,477 | 102,091 | 3,477 | 85,367 |
| **合计** | **17,945** | **529,369** | **17,945** | **451,488** |

### 4.4 完整度评估

| 赛季区间 | 联赛覆盖 | 完整度 | 备注 |
|----------|----------|:------:|------|
| 16/17 ~ 25/26 | 五大联赛全 | 🟢 100% | 10 赛季全量完整（含 19/20 法甲疫情缩水） |
| 26/27（进行中） | 法甲1/西甲20/英超1 | 🔴 22/~1750 已采 | 新赛季仅起步，需补采 |

### 4.5 与 matches 表对齐率（merge_understat_to_matches.py）

| 赛季 | 对齐场次 / 总场次 | 覆盖率 |
|------|:-----------------:|:------:|
| 23/24 | 1,641 / 1,752 | 93.7% |
| 24/25 | 1,714 / 1,752 | 97.8% |
| 25/26 | 1,751 / 1,752 | 99.9% |
| 26/27 | 16 / 22 | 72.7%（赛季进行中） |
| 16/17 ~ 22/23 | — | 未对齐（matches 表未收录该时段降级/历史球队） |

---

## 五、Sporttery 竞彩赔率进度

### 5.1 赔率历史表（odds.db）

| 表 | 总场次 | 对齐率（双通道） | 说明 |
|----|:------:|:---------------:|------|
| wdl_history | 12,575 | 98.3% | 胜平负时序赔率 |
| handicap_history | 13,125 | 98.3% | 让球时序赔率 |
| total_goals_history | 13,126 | 98.3% | 总进球时序赔率 |
| score_history | 13,122 场（816,026 行） | 63.8% | 比分赔率（覆盖率受队名变体限制） |

> **赛季覆盖（2026-08-27 实测，完整度终检通过）**：时序赔率已覆盖 16/17~25/26 全部 10 季主线，已结束赛季全部完成；仅 26/27 进行中（竞彩未开售）。
>
> 16/17 已采 1,787 场（英370/西372/意369/德300/法376，成功 1,787 / 失败 0）。其中 wdl 1,710 场（77 场 WDL 玩法竞彩未开售），让球/总进球/比分各 1,787 场。
>
> 17/18 已采 1,800 场（英380/西367/意379/德299/法375，成功 1,800 / 失败 0），覆盖对标 98.6%。wdl 1,707 / 让球 1,799 / 总进球 1,799 / 比分 1,799 场。
>
> 18/19 已采 1,551 场（英326/西319/意316/德259/法331，成功 1,551 / 失败 0），覆盖对标 84.9%（2018 世界杯后体彩对五大联赛非热门场开售收缩，竞彩网数据源本身的覆盖缺口，非采集失败）。wdl 1,477 / 让球 1,551 / 总进球 1,551 / 比分 1,551 场。
>
> 19/20 已采 835 场（英192/西160/意161/德163/法159，成功 835 / 失败 0）。wdl 797 / 让球 835 / 总进球 835 / 比分 835 场（38 场 WDL 竞彩未开售；19/20 为新冠缩水赛季，法甲提前终止、竞彩 2020 年 2~5 月阶段性停售，835 为体彩完整可售口径）。
>
> 20/21 已采 1,208 场（英246/西262/意261/德196/法243）。wdl 1,166 / 让球 1,208 / 总进球 1,208 / 比分 1,208 场（法甲 10 月漏采已补 8 场，1200→1208）。
>
> 21/22 已采 930 场（英222/西199/意176/德157/法176）。wdl 859 / 让球 930 / 总进球 930 / 比分 930 场（英超 11 月时序赔率已补采 12 场）。
>
> 22/23 已采 1,001 场（英308/西177/意201/德152/法163）。wdl 952 / 让球 1,001 / 总进球 1,001 / 比分 1,001 场（2022 世界杯 12 月停摆正常，德甲/意甲 12 月=0 非漏采，德甲 1 月=2 为真实开售量）。

### 5.2 对齐策略

- **双通道**: `match_id_en` 直接列（77.3%）+ `match_id_mapping` 桥表（95.0%）= 合并 98.3%
- **桥表 6 策略**: cn_to_en 83.0% / substring 8.3% / direct 6.3% / sofascore 1.3% / fuzzy_date 1.1% / date_tolerance 0.1%
- **Understat 对齐**: 队名归一化 + 14 别名 + 日期±1天容差，23/24-25/26 覆盖率 93.7%~99.9%

### 5.3 26/27 赛前回踩核验（2026-08-31 实测）

> 背景：8-30 报告完整性门禁标出 4 场「仅1条快照缺漂移」（皇马/曼联/拉科/卡利亚里），8-31 回踩核验结论如下。

| 核验项 | 结果 |
|--------|------|
| 8-30 三场自愈 | 曼联(2条)/拉科(2条)/卡利亚里(2条) 已由 live_collector 2小时循环自动补全第 2 条快照（08-30 21:19~22:16），无需手动回踩 |
| 皇马 vs 马拉加 | 竞彩未开胜平负正盘（handicap 9条/ttg 7条完整）→ not_offered，非漏采；报告已用「竞彩未开售胜平负盘」文案区分 |
| 8-31 销售日 5 场 | `sporttery_live_collector.py --date 2026-08-31` 新增 0，经 API 直连探针（getMatchListV1+getFixedBonusV1）比对确认与源站完全同步：莱切(4)/维拉(4)/亚特兰大(2) 完整；奥萨苏纳vs赫塔费源站 hadList 仅 1 条（08-29 09:36 开盘后赔率零变动，无第 2 条可采）；巴塞罗那vs巴列卡诺 had=0/hhad=5 为 not_offered 新例 |
| 销售日错位提醒 | 竞彩 businessDate=2026-08-31 的 5 场对应 matches 表实际开球日 2026-09-01 凌晨场；报告定位竞彩 match_id 依赖 ±3 天日期容差，跨销售日可正常对齐 |
| 20260901 报告验证 | 5 场全部生成，汇总告警表正确分列「仅1条快照缺漂移」（奥萨苏纳，真实缺漂移，触发邮件红标）与「竞彩未开售胜平负盘（非缺失）」（巴萨，不触发红标） |

> **结论**: 8-31 无可补数据（源站零变动）；「仅1条快照」有两种成因——①采集循环未回踩（会自愈/可回踩）②源站赔率零变动（无法回踩，报告如实标注）。

---

## 六、剩余采集任务

### 6.1 26/27 新赛季（进行中，最高优先级）

| 数据源 | 当前覆盖 | 目标 | 采集命令 |
|--------|----------|------|----------|
| 500.com 五大联赛 | 1,752 场赛程全入库；74 已完赛（结算盘口 74/74）；113 场有亚盘 | 赛前赔率/亚盘随赛程滚动刷新 | `--refresh-odds --season 26/27 --rounds 1-8`（需当日 EdgeOne Cookie，见 §2.6） |
| SofaScore 五大联赛 | 赛前特征已预生成；09-12 补采 19 场球员统计/评级 | ~1970 场（全赛季） | 见 §7.1 + 赛后管线 catch-up |
| Understat 五大联赛 | 22 场（法甲1+西甲20+英超1） | ~1750 场（已赛部分） | 见 §7.2 |
| Sporttery 时序 | 26/27 随赛程推进中（8-30/8-31 销售日 17 场已入时序表，详见 §5.3） | 赛前赔率随赛程推进 | `sporttery_live_collector.py` 定时 |

### 6.2 500.com 历史回采任务（中优先级）

> `SEASONS` 字典已扩展至 16/17~25/26（10 季），采集执行见 §7.3 命令3-A~3-D。

| 任务 | 说明 | 采集命令 |
|------|------|----------|
| 阶段一剩余：21/22~23/24 待补（4,644 场） | 意/德/法补 21/22~23/24；英/西补 22/23~23/24 | §7.3 命令3-B |
| 阶段二扩展：16/17~20/21（≈9,029 场） | 五大联赛全新回采，对标 Understat 10 季 | §7.3 命令3-C |
| 法甲 19/20 疫情缩水季 | 需确认 500.com 实际可采场次 | §7.3 命令3-D |

### 6.3 Sporttery 竞彩时序赔率历史回采（对标 10 季）

> `SEASON_RANGES` 已扩展至 16/17~25/26（10 季）。**16/17~25/26 已结束赛季竞彩时序赔率已全部完成**，剩余仅 26/27 随赛程推进（竞彩开售后用 `sporttery_live_collector.py` §7.5 定时采集）。

| 任务 | 说明 | 采集命令 |
|------|------|----------|
| ~~20/21~22/23 待补采（3 季）~~ ✅ 已完成 | 20/21（1,208）、21/22（930）、22/23（1,001）已补采完毕 | 已执行 |

---

## 七、PowerShell 后台采集命令

### 7.1 SofaScore 26/27 五大联赛补采

```powershell
$root = "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "sofascore_2627_$stamp.log"
$errLog = Join-Path $logDir "sofascore_2627_$stamp.err.log"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$proc = Start-Process -FilePath "python" `
  -ArgumentList "`"$root\collection\final_sofascore_collector.py`" --leagues all --season 26/27 --resume" `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -PassThru

"已启动 PID=$($proc.Id)"
"stdout: $outLog"
"stderr: $errLog"
```

### 7.2 Understat 26/27 五大联赛采集

```powershell
$root = "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "understat_2627_$stamp.log"
$errLog = Join-Path $logDir "understat_2627_$stamp.err.log"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$proc = Start-Process -FilePath "python" `
  -ArgumentList "`"$root\collection\final_understat_collector.py`" --leagues all --season 26/27 --resume" `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -PassThru

"已启动 PID=$($proc.Id)"
"stdout: $outLog"
"stderr: $errLog"
```

### 7.3 500.com 五大联赛历史回采（命令3）

> 对标 SofaScore / Understat 覆盖：**五大联赛 × 16/17 ~ 25/26**（见 §2.3 阶段一 + 阶段二）。
> 写法幂等（`INSERT OR REPLACE`），重复采集安全。

**命令3-A：一条命令全量回采（10 季，--season all 涵盖 16/17~25/26）**

```powershell
$root = "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
$stamp = Get-Date -Format "yyyyMMdd_HHmmss"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$outLog = Join-Path $logDir "500_backfill_all_$stamp.log"
$errLog = Join-Path $logDir "500_backfill_all_$stamp.err.log"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$proc = Start-Process -FilePath "python" `
  -ArgumentList "`"$root\collection\final_500_collector.py`" --season all --league all --rounds all --delay 0.5" `
  -WorkingDirectory $root `
  -WindowStyle Hidden `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -PassThru

"已启动 PID=$($proc.Id)"
"stdout: $outLog"
"stderr: $errLog"
```

**命令3-B：阶段一剩余回采（✅ 已完成，21/22~25/26 全量入库）**

> 阶段一五大联赛已结束赛季（21/22~25/26）已全部 100% 完成，无需再跑。命令保留备用：

```powershell
$root = "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 重跑自动跳过已采集场次（--skip-existing 默认开启）
python "$root\collection\final_500_collector.py" --season 23/24 --league all --rounds all --delay 0.5
```

**命令3-C：阶段二扩展回采（16/17~20/21，对标 Understat 10 季）— ✅ 已完成**

```powershell
$root = "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

foreach ($s in @("16/17","17/18","18/19","19/20","20/21")) {
  python "$root\collection\final_500_collector.py" --season $s --league all --rounds all --delay 0.5
}
```

**命令3-D：补齐 16/17 法甲 — ✅ 已完成**

```powershell
$root = "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# ✅ 已完成：16/17 法甲 380 场已入库（--skip-existing 默认开启，重跑不会重复）
python "$root\collection\final_500_collector.py" --season 16/17 --league 法甲 --rounds all --delay 0.5
```

### 7.4 500.com 26/27 当前赛季补采（赛季推进后）

> `SEASONS` 字典已含 26/27 五大联赛 stid（英超 27953 / 西甲 28016 / 意甲 27864 / 德甲 28025 / 法甲 27893，2026-08-29 探明已验证）；英超 26/27 已在库（`--skip-existing` 自动跳过），其余四联赛可直采：

```powershell
python "$root\collection\final_500_collector.py" --season 26/27 --league all --rounds all --delay 0.5
```

### 7.5 Sporttery 赛前时序赔率（定时，每 2 小时）

```powershell
# Windows 计划任务（推荐）
schtasks /Create /TN "SportteryLiveCollector" `
  /TR "C:\Python314\python.exe F:\zuqiu\五大联赛专属模型\五大联赛专属模型\scripts\sporttery_live_collector.py" `
  /SC HOURLY /MO 2 /F

# 或 PowerShell 后台作业
Start-Job -ScriptBlock {
    Set-Location "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
    while ($true) { python scripts\sporttery_live_collector.py; Start-Sleep -Seconds 7200 }
}
```

### 7.6 Sporttery 竞彩时序赔率历史回采（命令6，✅ 已完成）

> 对标 Understat/SofaScore 10 季；16/17~25/26 已结束赛季竞彩时序赔率已 **100% 完成**（10 季主线全部入库，让球 handicap 合计 13,125 场），剩余仅 26/27 随赛程推进（竞彩开售后见 §7.5）。时序表 `INSERT OR IGNORE` 幂等，重复采集安全。以下命令仅作断点续采/复跑存档。

```powershell
$root = "f:\zuqiu\五大联赛专属模型\五大联赛专属模型"
$logDir = Join-Path $root "logs"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null
$env:PYTHONIOENCODING = "utf-8"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

# 已完成复跑的 3 个赛季（竞彩网用完整年份格式；幂等，复跑仅跳过）
$seasons = @("2020-2021","2021-2022","2022-2023")

foreach ($s in $seasons) {
    $stamp = Get-Date -Format "yyyyMMdd_HHmmss"
    $outLog = Join-Path $logDir "sporttery_${s}_$stamp.log"
    $errLog = Join-Path $logDir "sporttery_${s}_$stamp.err.log"
    Write-Host "===== 开始采集 $s ====="
    $proc = Start-Process -FilePath "python" `
      -ArgumentList "`"$root\scripts\sporttery_collector.py`" --season $s --league all" `
      -WorkingDirectory $root -WindowStyle Hidden `
      -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
    $proc.WaitForExit()
    Write-Host "===== 完成 $s (exit=$($proc.ExitCode)) 日志: $outLog ====="
}
Write-Host "全部 3 个赛季采集完成"
```

> 试跑验证场次：`python scripts\sporttery_collector.py --season 2016-2017 --league all --dry-run`

---

## 八、监控命令

```powershell
# 实时查看 SofaScore 采集日志
Get-Content -LiteralPath "f:\zuqiu\五大联赛专属模型\五大联赛专属模型\logs\sofascore_2627_*.log" -Encoding UTF8 -Wait -Tail 30

# 查看 Understat 采集日志
Get-Content -LiteralPath "f:\zuqiu\五大联赛专属模型\五大联赛专属模型\logs\understat_2627_*.log" -Encoding UTF8 -Wait -Tail 30

# 查看 500.com 采集日志
Get-Content -LiteralPath "f:\zuqiu\五大联赛专属模型\五大联赛专属模型\logs\500_collect_2627_*.log" -Encoding UTF8 -Wait -Tail 30

# 仅看汇总行
Get-Content -LiteralPath "f:\zuqiu\五大联赛专属模型\五大联赛专属模型\logs\sofascore_2627_*.log" -Encoding UTF8 |
  Select-String "本季采集|完成|未赛|进度" | Select-Object -Last 20

# 竞彩时序赔率采集进度（查 DB 最准；日志为重定向块缓冲会延迟）
python "f:\zuqiu\五大联赛专属模型\五大联赛专属模型\logs\_check_sporttery_progress.py" --season 2016-2017
```

---

**文档版本**: v1.0
**创建时间**: 2026-08-25
**维护人**: 模型优化团队
**更新频率**: 每次采集任务完成后更新
