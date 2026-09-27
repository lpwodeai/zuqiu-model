# 足球预测模型 - 项目记忆

> 创建时间：2026-07-23
> 用途：存储项目核心规则、工程约定、经验教训，确保跨会话一致性
> 关联文档：docs/optimization_log.md, docs/key_decisions.md, docs/change_log.md

---

## 目录

1. [硬性规则](#一硬性规则)
2. [工程约定](#二工程约定)
3. [经验教训](#三经验教训)
4. [记忆系统规则](#四记忆系统规则)
5. [每日对话检索清单](#五每日对话检索清单)

---

## 一、硬性规则

### 1.1 数据规则 (DATA-001 ~ DATA-020)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| DATA-001 | 必须使用真实比赛数据，禁止硬编码或模拟数据 | 2026-07-23 |
| DATA-002 | 赔率数据保持原始精度，不四舍五入 | 2026-07-23 |
| DATA-003 | 日期格式统一为 YYYY-MM-DD HH:MM:SS | 2026-07-23 |
| DATA-004 | 比分格式统一为 X:Y | 2026-07-23 |
| DATA-005 | 球队名称使用中文标准名称 | 2026-07-23 |
| DATA-006 | match_id 格式为 YYYY-MM-DD_主队_客队 | 2026-07-23 |
| DATA-007 | 所有数据导入必须经过验证 | 2026-07-24 |
| DATA-008 | 数据库变更必须记录到 change_log.md | 2026-07-24 |
| DATA-009 | 数据泄露检查：特征不得使用赛后信息 | 2026-07-24 |
| DATA-010 | 「当前缺阵」查询**禁止使用 player_injuries 累积表**（合并 10 赛季历史伤病行，expected_return 为空的旧行被永久误判缺阵，实测马拉加误判 32/比利亚雷亚尔 74 人）；必须查 match_missing_players 精确到比赛日（`substr(match_id,1,10)=目标日`）且 `reason!='转会'`；赛前采集走 `collection/prematch_sofascore_lineups.py`（SofaScore /event/{id}/lineups 单接口=预测首发+官方伤停，/injuries 端点不存在） | 2026-09-17 §3.191 |
| DATA-011 | **赔率数据唯一 SSOT 为 `odds.db`**——禁止保留或新建手动 TXT 旁路（`data/{联赛}{赛季}赛季完整时序赔率.txt` 已全量删除，C-20260918-019/021）；预测脚本（`predict_today_14.py`/`generate_complete_report.py` 等）必须用 `scripts/load_odds_from_db.py` 的 `load_odds_from_db(match_id)` 从 `odds.db` 加载时序赔率，禁止读 TXT；26-27 赛季时序表 match_id 为中文格式（`{date}_{主队中文}_{客队中文}`），`match_id_en` 字段未填充，反查须用 `find_match_id_by_cn()` 中文短名模糊匹配 | 2026-09-18 C-020/021 |
| DATA-012 | **队名归一化口径分治**——`team_name_mapping.normalize_team_name`（4 级模糊匹配：精确别名→子串→模糊→序列相似度）写入口径（与 sporttery_live_collector.normalize_team / wdl_history / score_history 一致，使用 TEAM_ALIASES 标准短名如「纽卡斯尔」「热刺」「毕尔巴鄂」「云达不莱梅」）；`feature_utils.normalize_team_name`（纯精确映射）适合对齐 score_history 内部变体名（如「云达不来梅」「布赖顿」），**不可用于跨表 LIKE 模糊匹配**（无法处理 500.com 全名 ↔ wdl_history 短名场景，实测 5 场被跳过）；`generate_unified_report.find_sporttery_match_id` / `find_any_sporttery_match_id` 已改用 `_normalize_team_for_wdl`（team_name_mapping 优先 + feature_utils fallback + 原名 fallback）桥接函数（C-20260918-052）；其他位置（feature_utils 8 处调用）保留原状（用于 score_history 内部口径对齐，不涉及跨表）；**禁止** 在 wdl_history LIKE 查询场景直接用 `feature_utils.normalize_team_name` | 2026-09-18 C-20260918-052 |
| DATA-013 | **SofaScore 网络：本地 DNS 污染绕过方案**——`api.sofascore.com`/`www.sofascore.com` 本地 DNS 解析失败属网络层阻断（非 Akamai 反爬，curl_cffi 指纹模拟仍有效）；绕过：①DoH 取真实 IP（Fastly CDN，如 146.75.115.52）；②curl_cffi `impersonate="chrome"` + **URL 用 IP + `Host` 头 + `verify=False`** 直连（TLS SNI 为 IP 但 Fastly 按 Host 路由）；**已自动化为 `collection/doh_http_client.py`**（`DohSession`：直连优先 + DNS/连接异常自动降级 DoH，多 DoH 服务商容错 + 多 A 记录重试；CLI：`check`/`resolve`/`fetch [--force-doh]`），未开赛场次只需写 `fbref_match_mapping` 映射行（event_id + CN_TZ 日期 + 英文队名），再跑 `features/incremental_sofascore_features.py --since {date}` 聚合两队近 5 场历史数据生成 `sofascore_team_features` 行（无需赛后 lineups/statistics） | 2026-09-18 C-20260918-053 / 2026-09-19 C-20260919-001 |
| DATA-014 | **match_id 双轨口径硬约束（跨表 JOIN 必须 fuzzy 桥接，禁止假设等号）**——①**预测轨**：`model_predictions.match_id` / `matches.match_id` 用 **UTC 日期 + 预测侧英文简名**（如 `2026-09-16_FC Barcelona_Real Racing Club`、`2026-09-17_La Coruna_Sevilla`、`2026-09-18_Malaga_Villarreal`）；②**采集轨**：`fbref_match_mapping.odds_match_id` / `match_player_stats.match_id` / `match_lineups.match_id` 用 **北京日期 + SofaScore 英文全名**（如 `2026-09-17_FC Barcelona_Real Racing Club`、`2026-09-17_Deportivo de A Coruña_Sevilla`、`2026-09-18_Málaga CF_Villarreal`）；跨日凌晨场两轨日期差 1 天、队名简/全（含重音符、FC/CF 后缀、Deportivo de A... 前缀）均可能不同；③**禁止 UPDATE `fbref_match_mapping.odds_match_id` 对齐预测轨**——会破坏与 stats/lineups 的关联；也禁止补同 event 别名行（多处 COUNT/枚举消费者会重复计数）；④跨轨关联唯一正确方式：按 event_id fuzzy 桥接（`attribution_engine.find_event_id_fuzzy`：match_id 解日期+主客名 → NFKD 去重音/小写/剔停用词与俱乐部后缀得 token → mapping 日期±1 窗口主客双 token 匹配，包含或 Jaccard≥0.3，**命中须唯一**）；`get_event_id` 精确失配已自动回退；复盘流水线目标扫描已接入且按 event_id 补取 league；⑤**同一 SofaScore event 只允许一份复盘**——`_find_reviewed_alias` 经 SOFA_TEAM_CN_MAP 转中文按日期+双队名查重，重复预测残留口径（如 9-16_La Coruna_Sevilla）跳过；⑥`_summary.md` 必须按目录日期全量重建（`_rebuild_dir_summary`），禁止用「本次处理场次」覆盖；⑦复盘报告的正式 match_id 口径以赛前报告 build_log 为准 | 2026-09-19 C-20260919-014 |
| DATA-015 | **matches 表 match_id 已统一 SofaScore 全名口径（C-090 对 DATA-014 双轨的修正）**——①DATA-014 的「预测轨=matches 用英文简名」系对 26/27 初污染状态的描述而非设计：诊断证实 matches 25/26 全量及 26/27 主流（1940/1983）与采集轨 ps mid 完全一致（SofaScore 全名），简名行（odds500 风格 `La Coruna`/`Malaga`/`Hull`）与中文行（populate C-086 归一中文）均为污染源产生的重复行；②C-20260926-090 迁移后 matches 26/27 单一口径=**SofaScore 全名**（与 `match_player_stats`/`match_lineups`/`fbref_match_mapping.odds_match_id` 同号），CN 残留 0、同场重复 0、ps 孤儿 65→15；③**populate_matches_from_fbref.py 生成 match_id 必须用 `fbref_match_mapping.odds_match_id` 原值**（禁止归一化中文名——C-086 方向错误已纠正）；④DATA-014 的 event_id fuzzy 桥接**仍然有效**，用于关联存量赛前报告（旧简名/中文 mid）、odds500 生态（`odds500_match`/`odds500_stat` 仍含 144+5 个 CN mid，风险 S 待修）；⑤matches.handicap CHECK 约束仅允许 `handicap_source='sporttery'`（竞彩 goalLine 口径），任何补插/迁移禁止写入 odds500 亚盘 handicap | 2026-09-26 C-20260926-090 |
| DATA-016 | **Akamai 指纹级 403 处置：curl_cffi → Playwright 页面导航自动后备（C-20260926-092）**——①现象：api.sofascore.com 对 curl_cffi（edge **与** chrome 两指纹）及用户 Edge 普通窗口均 403 challenge，Edge 无痕窗口与 Playwright headless `page.goto` 200；②根因：本轮风控纯凭客户端全套指纹（TLS JA3/HTTP2 SETTINGS/导航请求头/JS 传感器）判定，且 **Akamai 全程未下发任何 cookie**——手动导 cookie 路线无凭证可取（`document.cookie` 里只有分析类 cookie，HttpOnly 的 `_abck` 根本不存在）；Playwright 裸 `APIRequestContext` 同样 403（缺页面导航自动补全的 sec-ch-ua/sec-fetch 等头），**仅完整页面导航通过**；③处置：`final_sofascore_collector.py` 请求统一收口 `_send()`，curl_cffi 首次 403 challenge 时惰性启动 headless Chromium（en-US、先 goto www.sofascore.com 热身）以页面导航重发，`_pw_active=True` 后全部请求走浏览器；`close()` 自动清理全部资源；playwright 未装/启动失败时行为与旧版一致（仅告警）；④通道自然恢复后 curl_cffi 自动直连、零开销，无需回退开关；⑤教训：遇到 403 先做「指纹通道矩阵」实测（curl_cffi×edge/chrome、page.goto、APIRequestContext、浏览器无痕）再决定路线，不要直接让用户翻 cookie——无 cookie 下发时该路线是死胡同 | 2026-09-26 C-20260926-092 |
| DATA-017 | **odds500 生态 match_id 已统一 SofaScore 全名口径（C-20260926-093，风险 S 关闭）**——①五表（`odds500_match`/`odds500_betting`/`odds500_ouzhi_summary`/`odds500_ouzhi_company`/`odds500_stat`）原 26/27 CN mid 全部清零（144 场升班马 + 2 个历史 ad-hoc 残留），行数零损失；②**写入侧硬约束**：`final_500_collector._EXTRA_CN_TO_EN` 新队/旧队一律映射 SofaScore 全名（勒芒`Le Mans`、考文垂`Coventry City`、桑坦德竞技`Real Racing Club`、埃沃斯堡`SV 07 Elversberg`、赫尔城`Hull City`、马拉加`Málaga CF`、拉科鲁尼亚`Deportivo de A Coruña`、帕德博恩`SC Paderborn 07`、沙尔克04`FC Schalke 04`）；**禁止**再引入 Understat 简名目标；③`collect_match`/`build_match_id` 拼 mid 前必须经 `resolve_sofa_mid()` 按日期 ±2 天 + 双队名归一反查 `fbref_match_mapping.odds_match_id`，唯一命中则日期/全名整体采用（消除 500 赛程 40 场日期漂移），零/多候选才退回映射拼接（适用于 SofaScore 事件尚未注册的德甲第 7 轮起 29 场）；④`_norm_team` 结果仍含中文时必须告警（每队一次），禁止静默产出 CN mid；⑤迁移审计物：`backup/migrate_risk_s.py`、`backup/risk_s_rename_plan.json`、迁移前备份 `backup/odds_backup_riskS_20260926.db` | 2026-09-26 C-20260926-093 |
| DATA-018 | **平局决策阈值因子单一来源 = config.yaml（C-20260926-094，风险2关闭）+ MLflow 已停用（方案B）**——①`prediction_core.py` 删除硬编码 `WDL_DRAW_THRESHOLD_FACTOR=0.0`，新增 `get_draw_threshold_factor(league_cn)` 读 `config.yaml`（`draw_threshold_mode`/`draw_threshold_factor`/`draw_threshold_factor_league`），predict 决策两处（主决策+冷门调整后重决策）均调该函数，与 Node `prediction-service.js` 同源；中文联赛名→config 代码映射 `LEAGUE_CN_TO_CODE`（法甲FL1/英超PL/德甲BL1/意甲IT/西甲LaLiga）；**禁止**再在 Python 硬编码任何平局阈值因子；②**MLflow 方案B 已执行**：`train_models.py`/`advanced_model_trainer.py` 移除全部 `import mlflow`/`MLFLOW_AVAILABLE`/`mlflow.*` 调用；`scripts/mlflow_repro.py` 去 MLflow 依赖改 `save_repro_snapshot()`（纯标准库，仅落盘 `assets/repro_snapshot_<ts>.json`）；`mlruns/` 影子目录已删除（畸形 run：仅 artifacts/ 无 meta.yaml，MLflow API 实测 run not found，新版文件后端已维护模式）；`.gitignore` 补 `mlruns/`+`assets/`；**禁止**再引入 MLflow 追踪（如需实验追踪请重议方案 A 并显式 `set_tracking_uri`/`set_experiment`）；③**argmax 为生产既定决策（C-20260926-095 实测依据）**：15,135 场 OOF 搜索证实 Stacking 天然强反平局（LR meta 平局召回 0.6%/固定权重 0.9%），提召回≥0.28 需 LR F=1.42（损失 2.38pp）或固定 F=1.22（损失 2.10pp），故保持 argmax（acc 0.5236/RPS 0.2005）；单模型报告 F=1.45/1.50 禁止直接套 Stacking；若业务强制提召回，优先固定权重 F=1.22；联赛启用仅限西甲 1.37/意甲 1.43，法甲 1.44 禁启用 | 2026-09-26 C-20260926-094 / C-20260926-095 |

| DATA-019 | **Node 侧死依赖治理规则（C-20260926-096）**——①node_modules 非权威资产、不入库（.gitignore 第1行）、可 `npm ci` 重建；package.json 变更必须经 install/uninstall 同步 lock（lockfileVersion 3），禁止只改一侧；②已移除 4 个零引用死依赖：`sqlite3`（绑定缺失、服务全走 better-sqlite3）、`sql.js`（WASM，已被 better-sqlite3 替代）、`jsdom`（采集用 cheerio）、`bull`（仅 redis 直连）；最终 287.1MB/370 包/16 dependencies；③**`node-schedule` 为保留的功能依赖**——train-scheduler.js L164 `await import('node-schedule')` 动态加载，提供 cron `0 2 * * 1`（周一02:00）定时训练，禁止当作死依赖移除；④**审计方法论（教训）**：依赖审计不得只做静态 import 扫描，必须覆盖 `import()`/createRequire 等动态形态，并以真实服务启动日志复验（能力回退告警），确认无功能受损后方可定稿 | 2026-09-26 C-20260926-096 |

| DATA-020 | **离线特征链路数据保真规则（C-20260926-097）**——①球员特征链路必须先跑 player_feature_engineer 再跑 integrate_player_features；球队无球员数据（如 id 98/99）映射为**结构性零=未知**，禁止伪造：winsorize/clip 不得把未知零抬为 p1 真实值（已用未知行掩码在 clip 后恢复未知侧及 diff 为零）；②多矩阵按位置 concat 前必须校验行数一致，不一致硬报错（防静默错位）；③pandas 读 CSV 浮点列必须加 `float_precision='round_trip'`（默认 C 解析器单次往返实测 478 个 1 ULP 漂移；禁止用 %.17g 规避——长串反而触发更多误差）；④离线脚本缺必需输入必须非零退出，禁止静默 return；精选清单按列交集取列防过期 KeyError；⑤§9.13 写 output/ 的脚本实测 6 个（data_quality/model_validation/generate_team_attributes 写 assets/） | 2026-09-26 C-20260926-097 |

### 1.2 特征规则 (FEAT-001 ~ FEAT-014)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| FEAT-001 | 必须区分赛前/赛后特征，预测只用赛前特征 | 2026-07-23 |
| FEAT-002 | 81个赔率时序特征必须保留 | 2026-07-23 |
| FEAT-003 | 训练和预测必须使用相同特征集合 | 2026-07-23 |
| FEAT-004 | StandardScaler 只在训练集 fit，不得在测试集 fit | 2026-07-24 |
| FEAT-005 | 时间序列交叉验证，按时间划分训练/测试集 | 2026-07-24 |
| FEAT-006 | h2h_last_result 不得泄露标签 (L-012) | 2026-07-24 |
| FEAT-007 | 运算符优先级检查：A&B\|C&D 必须加括号 | 2026-07-24 |
| FEAT-008 | 特征重要性排序后保留Top-K特征 | 2026-07-23 |
| FEAT-009 | 时序/比分赔率特征由 ts_odds 开关控制（build_all_features 默认 False），开启后接入 D-013 22维 + T-003.1 8维并做联赛 z-score 归一 | 2026-08-28 |
| FEAT-010 | 时序赔率表（wdl/handicap/total_goals/score_history）对齐 matches 必须使用 build_match_alignment 四通道（直连+match_id_en+桥表+中→英 canon/token，通道3 由 C-20260920-025 新增），禁止自定义单通道 JOIN | 2026-08-28 |
| FEAT-011 | 多博彩公司赔率一致性特征由 consensus_odds 开关控制（build_all_features 默认 False，生产 True），接入 10 维（mkt_imp_{win,draw,lose} 3 + mkt_dev_{win,draw,lose} 3 + mkt_dev_abs + mkt_dispersion + mkt_company_count + mkt_return），生产启用后模型 198→208 维 | 2026-08-28 |
| FEAT-012 | 情境化特征由 ctx_features 开关控制（build_all_features 默认 False），接入 14 维（休息天数3 + 赛程密度4 + 连续作战2 + 积分压力3 + 德比/新军经验2）；A/B 及降维复查均无 RPS 增益，生产维持 False 不启用 | 2026-08-28 |
| FEAT-013 | PA 特征官方伤停校正仅对**未赛场次**（match_date >= 今天）生效，历史场次必须保持历史出场连续性推算（防时间穿越回灌）；官方缺阵名单查 match_missing_players（DATA-010 口径），XI 推算先剔除官方缺阵球员再从剩余池重排，availability/missing_impact 切换官方口径；未采集时静默降级纯推算 | 2026-09-17 §3.191 |
| FEAT-014 | **赔率转概率必须先取倒数**：口径为 `1/odds → 按行归一化去水`，**禁止对原始赔率直接线性归一化**（强队赔率低会被赋低概率，方向恰反；C-20260920-025 已修正 hcp_features.normalize_probabilities 并重训 T-005 v3）。所有衍生特征只基于去水概率，**train/serve 必须同源同公式**（favorite_margin=1−max(p)、balance=\|p0−p2\|、upset_risk=p1+p2、odds_skew=max−min、draw_divergence=\|1/wdl平赔 − hcp去水走水概率\|）；serving 历史 43 维由 `hcp_features_v2.get_serving_feature_matrix()` 单例供给（中位数兜底顺序须与训练一致），训练截止常量构建期临时提升、finally 还原。中文历史键对齐走 `build_match_alignment` 四通道（通道3=中→英 canon/token 唯一候选）。**C-20260920-026 已关闭 T-004 遗留**：tg_features.normalize_probabilities 同模式修正，`train_tg_model --deploy` 首次产出 assets/t004_tg_lgb_model.pkl（bundle 含 feature_cols/medians，10,528 场×374 维）；多路对齐多对一必须去重（build 入口按 matches_match_id，sofa/lag 防御性去重）。注意：OLD/NEW/ZERO 三口径对照证实 T-004 基础 20 维被 312 维 Lag"架空"（指标三口径几乎相同），数学修复价值在口径正确性；若后续要让市场信号真正生效，须从特征结构层面另立任务 | 2026-09-20 C-20260920-025 / 026 |

### 1.3 模型规则 (MODEL-001 ~ MODEL-007)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| MODEL-001 | 使用 class_weight 处理类别不平衡 | 2026-07-23 |
| MODEL-002 | XGBoost/LightGBM 必须使用 sklearn API | 2026-07-23 |
| MODEL-003 | 集成权重必须动态调整 | 2026-07-23 |
| MODEL-004 | 参数从 config.yaml 读取，禁止硬编码 | 2026-07-23 |
| MODEL-005 | 训练准确率与测试准确率差距不得超过20% | 2026-07-24 |
| MODEL-006 | 必须与基线模型(全押主胜47.22%)比较 | 2026-07-24 |
| MODEL-007 | 模型保存时记录版本号和性能指标 | 2026-07-23 |

### 1.5 概率校准与 EV 决策规则 (CALIB-001 ~ CALIB-009)

> C-20260903-006 概率校准对比实验（严格时序 OOF 11965 场，TimeSeriesSplit(5) 折内 fit→折外 transform，无校准泄漏）后固化的校准选型硬约束。关键基线：当前生产 Platt 校准 LogLoss 0.9907 / 平局召回 13.94% / EV 平注 ROI -5.41%。

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| CALIB-001 | 概率校准**必须**基于 raw 概率反推 logits 后再做温度/向量缩放；**禁止**对 Platt 后概率再叠加同属 parametric 类的缩放（T=1.469 时平局类 T_draw 顶到边界 8.0，导致平局召回坍缩至 1% 以下） | 2026-09-03 §3.106 |
| CALIB-002 | 校准对比实验的「训练/应用」必须与时序 CV 同源（折内训练集拟合校准器，**仅在折外验证集应用**），禁止用全量标签拟合校准器再作用于同一样本（会造成正向偏差 +1~3pp ROI 不可信） | 2026-09-03 §3.106 |
| CALIB-003 | 平局召回率≥0.28 为第一优先级约束，LogLoss/ROI 优化必须在满足平局召回达标前提下进行；平局召回不达标而 ROI「更好」的方案，统一视为不可投产 | 2026-09-03 §3.106（VectorScaling/Iso 平局召回 0.3~2.3%，ROI 再优也无意义） |
| CALIB-004 | **当前首选校准方案 = TempScaling(on raw, NLL 最优搜索 T)**。11965 场 OOF 实测：T≈0.896~0.975（逐折递减，越近赛季越需锐化）、平局召回 27.16%（✅达标，Δ vs Platt +13.22pp）、平注 ROI -3.71%（Δ vs Platt +1.70pp，当前最优）、Top-class ECE 7.70%（略高于 Platt 的 3.73%，属合理 trade-off） | 2026-09-03 §3.106 |
| CALIB-005 | Isotonic-OVR（一对其余保序回归）**禁止单独用于概率校准**。对 Platt 概率再做保序会系统性压低平局识别（平召从 13.94%→2.32%），ROI 也大幅劣化（-5.41%→-8.28%）。保序回归仅可与 DrawCalibrator 叠加做「平局专项恢复」，但仍劣于 Temp(on raw) 单级 | 2026-09-03 §3.106 |
| CALIB-006 | DrawCalibrator factor 在 0.95 以下可显著抬升平局召回，但必须以「温度缩放先压过度自信」为前置条件的 Temp+DrawCal(0.30) 组合为上限；单独 DrawCal 或 factor<0.90 会牺牲 EV 排序质量（平召 39.81% 但 ROI 反而从 -3.71% 恶化到 -5.44%） | 2026-09-03 §3.106 |
| CALIB-007 | 概率校准**本身不能使 EV ROI 转正**（当前 6 方案最佳 Temp ROI 仍 -3.71%，平均 EV 仍 +17~21%，系统性高估仅被部分压缩）。EV ROI 转正必须叠加：①EV 阈值抬升/择场过滤（min_ev 0.05→0.10 + 置信过滤）；②训练端直接以 EV/ROI 为目标（替代 WDL 交叉熵）；③edge 分桶单调回归（>10pp 桶仍 -6.53% 需专项修复） | 2026-09-03 §3.106 结论 |
| CALIB-008 | **EV 择场「阈值抬升 + 置信过滤」双条件经 72 组合全量扫描（C-20260904-001）证实无法使 ROI 转正**（9970 场 OOF，n≥100 全部为负）。实测反直觉规律：①min_ev 抬升 0.02→0.15 时平均 EV 升至 +35~57% 但 ROI 反而恶化（Baseline -5.15%→-7.28%），高 EV 方向实际胜率系统性低于 EV 隐含胜率（**edge 排序失效**）；②置信 P90 过滤后命中率暴跌至 9.8~12.4%（高置信被高赔率爆冷方向绑架）；③最优组合 Temp+min_ev=0.10+P50 仍 -2.93%，分赛季仅 3/9 季转正无一致性。→ **后续 ROI 转正**必须**直接进训练端**（loss 对 EV/ROI 求导或 edge 分桶单调回归修复），纯后处理择场已证伪 | 2026-09-04 §3.107 |
| CALIB-009 | **edge 分桶单调回归修复（C-20260904-002）结论：保序回归只能恢复分桶「单调性（排序修复）」，不能凭空产生正 edge（水平修复）**。实测 6 口径：①唯一单调方案 = Mono-Pooled(on Temp) 单一单调可靠性回归（pool 三类 (p,y) 拟合 p→P(y|p)，argmax 排序不变故平局召回结构性保持 32.3%），分桶 0~3pp -13.4% → 3~6pp -4.9% → 6~10pp -3.1% → >10pp -1.5% **单调递增但全桶仍负**；②整体最优仍 Mono-Pooled(on raw) -3.65%（n=9464）但分桶非单调；③**选择条件化 winner's curse 修正（Mono-Selected）证伪**：对 EV 引擎选中的 max-EV 方向拟合 p→P(win|选中) 再重算 edge/EV，整体平ROI -6.28% 反而更差、分桶非单调、>10pp 桶高估幅度升至 +17.5pp；④Mono-OVR 平召仅 0.92% 再次证实逐类保序坍缩，pooled 单映射是保平局召回的必要设计。→ 所有方案 ROI 均负、转正组合 0 个，**系统性概率高估的根治必须进训练端 EV/ROI 目标改造（loss 直接对 EV/ROI 求导）** | 2026-09-04 §3.108 |

### 1.6 安全规则 (SECURITY-001 ~ SECURITY-005)

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| SECURITY-001 | 禁止执行危险SQL：DELETE/DROP/UPDATE/INSERT/ALTER | 2026-07-23 |
| SECURITY-002 | 所有输入必须验证 | 2026-07-23 |
| SECURITY-003 | 禁止硬编码敏感信息 | 2026-07-23 |
| SECURITY-004 | 数据库操作必须使用参数化查询 | 2026-07-23 |
| SECURITY-005 | 配置文件不得包含密码或密钥 | 2026-07-23 |

### 1.7 知识库规则 (KB-001 ~ KB-004)

> C-20260907-011（模块 B1）固化。知识库 = 赛后复盘闭环（模块 A）沉淀的经验知识库（L1 统计先验 / L2 特征工程 / L3 样本权重），不是规则引擎权重库；A5 审核确认（≥4 级）后才可写入。

| 规则ID | 规则内容 | 来源 |
|--------|----------|------|
| KB-001 | 知识库统一 schema：`data/knowledge_base/{英超,西甲,意甲,德甲,法甲,global}/{feature_insights,sample_weights,rules}.json` + 根目录 `human_corrections.json`，外层 wrapper `{version,league,layer,updated_at,entries[]}`（对齐指南 §3.1）；**所有读写一律走 `scripts/knowledge_base_schema.py`**（load_entries/save_entries/load_human_corrections/add_insight/add_correction），禁止其他脚本直接读改 JSON 造成双写路径 | 2026-09-07 §3.126 |
| KB-002 | 写入门禁：`add_insight` 强制 confidence>=4 才写入 feature_insights（<4 级拒绝）；按 match_id 去重幂等；无 AI 归因主因（primary_cause）不写（仅赛果确认不入库） | 2026-09-07 §3.126 |
| KB-003 | `human_corrections.json` 位于 knowledge_base **根目录**（全局文件，非 global/ 子目录），条目对齐 §B4（11 字段）；每月清理失效条目：`--cleanup` 将 last_verified 超期（默认 90 天）且 status=active 的条目置为 expired（保留可追溯，不删除） | 2026-09-07 §3.126 |
| KB-004 | B2 赛前预测接入用 `get_insights(league)` 读取（同联赛优先 + global 兜底合并，默认过滤 active 且 ≥4 级）；A3 attribution_json 双形态解析统一用 `parse_attribution`/`primary_cause`（A5 基线分级与 B1 写入复用同一实现） | 2026-09-07 §3.126 |
| KB-005 | B2 赛前报告「知识库参考」章节：generate_unified_report.py 读同联赛 L2 feature_insights（get_insights），仅展示指导特征开发**不自动改参**；知识库读取异常降级标注「读取失败，本场结论不受影响」，不阻断报告生成（对齐 ev_engine 降级惯例） | 2026-09-07 §3.127 |
| KB-006 | SQL 子句拼接教训（--league 过滤长期失效根因，C-20260907-012）：`AND 条件` 一律拼接在 WHERE 子句内、**禁止放 ORDER BY 之后**——SQLite 会把 `ORDER BY ... AND league IN (?)` 解析为 ORDER BY 布尔表达式（`league AND league IN (?)`），条件静默失效且不报错 | 2026-09-07 §3.127 |

---

## 二、工程约定

### 2.1 文件结构

```
五大联赛专属模型/
├── 五大联赛专属模型/
│   ├── assets/          # 模型文件、配置
│   ├── client/          # 前端代码
│   ├── data/            # 数据文件、数据库
│   ├── docs/            # 文档
│   ├── logs/            # 日志
│   ├── modules/         # 功能模块（epl_odds, seriea_odds）
│   ├── output/          # 输出
│   ├── reports/         # 报告
│   └── scripts/         # 脚本
├── import_bundesliga.py  # 德甲导入脚本
├── import_ligue1.py      # 法甲导入脚本
└── project_memory.md    # 本文件
```

### 2.2 数据库约定

| 数据库 | 用途 | 路径 |
|--------|------|------|
| odds.db | 主数据库，存储比赛和赔率历史数据（含四张时序表 wdl_history/handicap_history/total_goals_history/score_history） | data/odds.db |
| ~~odds_timing.db~~ | ~~时序赔率中转库~~（已删除 C-20260918-021） | 时序数据已全量并入 odds.db 四张 `*_history` 表 |
| five_leagues.db | 比赛数据库，存储基本比赛信息 | data/five_leagues.db |
| PostgreSQL | odds.db 的 PG 后端（P2-11 迁移，2026-08-29），批处理负载提速 | localhost:5432 odds（DB_BACKEND=pg 切换） |

**数据库访问统一走 db_utils（双后端 SQLite/PG，2026-08-29 固化）：**
- 读：`db_utils.read_sql(sql, conn, params)` 替代 `pd.read_sql`（pandas 不支持非 SQLAlchemy 的 pg8000 连接）。
- 写：`db_utils.write_dataframe(conn, df, table)` 替代 `df.to_sql`（`df.to_sql` 仅支持 SQLAlchemy 引擎 / sqlite3 连接，pg8000 裸连接不兼容）；内部用 `executemany` 批量 INSERT，`NaN/NaT→NULL`、`datetime→ISO 字符串`。
- 建表数值列：`db_utils.numeric_sql_type(conn)` 返回 `REAL`（SQLite）≈ `DOUBLE PRECISION`（PG）。
- 连接：`db_utils.connect(backend=None, db_path=None)`，`DB_BACKEND=pg/sqlite` 环境变量切换，默认 SQLite 零风险。
- 规则：**禁止**在新增脚本里直接 `df.to_sql` / `import sqlite3` 硬连（`five_leagues.db` 旧库兜底读除外）；占位符用 `?`（PG 端自动转 `%s`）。
- **pg8000 唯一来源=vendor 目录 `pylibs/`（C-20260926-098）**：全局 site-packages 未安装 pg8000；pylibs/ 随仓库分发（.gitignore 未排除），三包依赖链 pg8000 1.31.5 → scramp 1.4.10（SCRAM 认证）→ asn1crypto 1.5.1；切 PG 前不得删除该目录。当前 DB_BACKEND 未设、5432 无监听，属备而不用；版本冻结无 requirements 记录，升级需先补来源。

**~~odds_timing.db 表结构~~（已删除 C-20260918-021，时序数据已并入 odds.db）：**
- 原表 matches/wdl_timing/handicap_timing/total_goals_timing/score_timing/match_results/import_log 的数据已全量沉淀于 odds.db 的 matches/wdl_history/handicap_history/total_goals_history/score_history 四张时序表，中转库使命结束不再需要。
- 预测脚本加载时序赔率须用 `scripts/load_odds_from_db.py`（DATA-011），禁止重建 TXT 旁路或中转库。
- 队名归一化全链路统一调 `scripts/team_name_mapping.py` 的 `normalize_team_name()`（4 级匹配：精确别名→子串→模糊→序列相似度）（DATA-012，C-20260918-023）：①写入侧 `sporttery_live_collector.normalize_team` 先调 `normalize_team_name` 再 fallback `TEAM_NAME_MAP.get` 再 fallback 原名，确保 wdl_history 等 4 张时序表 match_id 用中文标准名；②查询侧 `load_odds_from_db.find_match_id_by_cn` 查询前先归一化 `home_cn/away_cn` + fallback 原始队名，解决 odds500_match 用「托特纳姆热刺」但 wdl_history 归一化为「热刺」的跨表失配；③历史数据修正用 `scripts/fix_match_id_normalize.py`（扫描 4 张时序表 DISTINCT match_id 拆分 `{date}_{home}_{away}` 调 `normalize_team_name` 生成新 match_id，先 DELETE 冲突行再 UPDATE，事务内执行）；禁止在采集器里用简单 `dict.get(name, name)` 无 fallback 的归一化。

**model_predictions 行口径（C-20260921-034 固化）：**
- 生产落库 prediction_type 含 WDL_*/HC_*/TG_*/Lambda_home/Lambda_away/Lambda_alert/**Score_top1/Score_top5**；唯一索引 UNIQUE(match_id, model_name, prediction_type)。
- Score_top1.prediction=发布比分、probability=其概率；Score_top5.prediction=JSON 数组（Top10 报告的前 5 行）。
- **复盘只读 Score_top1/Score_top5 发布行，禁止由 λ 现场重推**；读不到标「无发布记录」，命中统计按 None 剔除。
- timestamp 一律本地（Asia/Shanghai）naive `%Y-%m-%d %H:%M:%S`，含 actual_* 行。

**TG 校准 Shadow 口径（C-20260921-036 固化）：**
- **生产 model_predictions 的 TG 行永远 factor=1.0**；λ 滚动校准（`tg_lambda_calibrator.get_calib_factor_hierarchical`）当前只做 shadow 双算，写 `reports/tg_calib_shadow.jsonl`（control/shadow 双份 8 档分布+factor+trace），不展示、不落库；开关 `TRAE_TG_CALIB_SHADOW`（env > config.yaml `tg_calibration.shadow_enabled`，默认开），config `tg_calibration.enabled=false` 在门禁通过前不得开启。
- TG 落库守卫：TG_over/under/top1/top3 概率必须严格 ∈(0,1)，0.0/1.0/None/越界一律跳过（无有效赔率的「数据不足」降级场）；**存量 49 场 98 行 0.0/1.0 占位脏行已于 C-20260921-037 清洗删除**（审计清单 backup/tg_dirty_rows_deleted_C037.json，备份 backup/db_snapshots/odds_20260921_150658.db），历史查询不再需要 probability>0 过滤。
- A/B 评测只读 model_predictions 发布 λ 与发布 TG 概率，**禁止现场重推**；`tg_calibration_shadow_eval.py` 已逐行对齐生产口径（unified engine + fuzzy 桥接 + 7+合并 + 0.85/0.15 融合），改动 predict() 后必须先验证 control 重放≈发布（保真偏差应 <0.01）。
- 上线门禁（计划 §6）：全集 RPS 配对检验 p<0.05 且 RPSS>0、Brier≤0.250、ECE 与高桶 gap 收敛、分层无新极端失效、准确率无显著下滑；2026-09-21 首测 154 场方向正确但 p≈0.19~0.43 不显著，结论=保持 Shadow 累积样本。`factor_multiplier` 探索值（×0.95）属同集调参，锁定 1.0 不得进生产。
- **C-038 Shin 去水拆解实验结论**：Shin (1993) 法 z≈0.05 方向正确（冷门↓热门↑→over25↓），但受 15% 融合权重限制，RPS/Brier Δ<0.0001 不显著、高桶 gap 0.164 零变化。**高桶 gap 未收敛的根因不在去水方法，而在 Poisson λ 系统性偏高（85% 权重主导）**。后续方向：要么提升赔率权重（0.15→0.25~0.30），要么加强 λ 校准幅度（isotonic/temperature scaling），需独立实验。
- **C-039 融合权重 0.25 实验——首个统计显著结果**：154 场 A/B（去水固定 simple，唯一变量 W_ODDS 0.15→0.25），RPS 0.1400→0.1386（**p=0.000 显著**）、Brier 0.2400→0.2379（**p=0.005 显著**）、高桶 gap 0.164→0.134（收敛 30%）、Top1/Top3 +1.3%。**判定 WEIGHT_EFFECTIVE**——首个通过 RPS+Brier 双显著门禁的实验，可作为 Shadow 上线候选。高桶 gap 0.134 未归零仍有空间，可继续探索 0.30。
- **C-040 融合权重 0.30 实验 + Shadow 接入**：0.30 全面优于 0.25——RPS 0.1380（p=0.000）、Brier 0.2369（p=0.006）、**高桶 gap 0.085（收敛 48%）**、Top1 22.1%。Shadow 接入完成：config `w_odds=0.15`(生产)/`shadow_w_odds=0.30`(影子)，predict() 新增 w_odds 参数替换硬编码 0.85/0.15，predict_unified 双算块写 `result['_shadow_tg_wodds']`，generate_unified_report 写 `reports/tg_wodds_shadow.jsonl`（match_id 去重）。生产值不变、enabled=false 不上线。
- **C-041 联合实验结论（8 组对比）**：B7(factor+Shin+w=0.30) RPS=0.1367 Brier=0.2347 **均值最优**，但高桶 gap 0.201 **退化（过度校正）**且 Brier p=0.113 不显著。**B3(w=0.30 alone) 仍是最优上线候选**：双重显著（p=0.000/0.006）+高桶 gap 0.085 最优。factor+Shin 在 w=0.30 上有边际 RPS -0.0013 但引入方差致显著性下降。结论：Shadow 继续用 B3，后续可探索 isotonic/temperature scaling 替代 mean-ratio factor 避免方差引入。
- **【强制执行】TG 优化路线定案（C-041 后固化，必须按此执行）**：
  - **核心结论**：权重是核心杠杆（每 +0.05 权重 RPS 降 ~0.002），校准方法是边际微调（边际贡献仅 ~0.001 RPS）。B3（w=0.30）已捕获 90% 增益，剩余 10% 不值得冒方差引入 + 高桶退化风险。
  - **P0（当前阶段）**：B3 Shadow 累积样本。C-040 已接入生产 Shadow 双算（`reports/tg_wodds_shadow.jsonl`），每日自动累积，数百场后重跑 `tg_weight_shadow_eval.py` 重判门禁。**在此期间不得改动 Shadow 配置**。
  - **⚠️ C-20260926-099/100 订正（适用全部三条 shadow jsonl：t006_shadow_v5/tg_calib_shadow/tg_wodds_shadow，各 247 行）**：①**每日累积曾停滞，根因非模型而是计划任务**——`TraeCode_PrematchReport_2030` 被注册成裸 TimeTrigger（8/30 一次性，无每日重复），8/30 后永不触发；9/1–9/21 报告均为 9/23 晚手工批量补写（jsonl 停在 9/24 04:26）；**C-100 已重注册为每日 20:30 + 电池策略放宽 + StartWhenAvailable，ps1 加步骤追踪/失败传播，手工完整复验通过（7/8 步 OK）**；②**9/22–10/9 国际比赛日空档**（odds500_match 与 SofaScore 均 0 场），期间无 shadow 正常，10/10 联赛恢复自动续算；③**jsonl 只写不读**（eval 均现场重算）——配对评测闸门仍悬空，接线评测是首要待办；④遗留：无比赛日邮件步骤 FAIL 待优化、9/1 三场 status 漏回补。详见框架 §9.15 / §5.3。
  - **C-20260926-101 追加**：用户确认不需要邮件推送，邮件步骤已从 run_prematch_2030.ps1 移除（每日只跑采集/特征/生成报告/闭环校验）；**send_report_email.py 与 email_config.json 保留闲置**，恢复只需加回一步。注意此前列为遗留的「无比赛日邮件 FAIL」随之自动消解。
  - **C-20260926-102 追加**：Shadow jsonl 配对评测已接线——新增 `scripts/shadow_jsonl_eval.py`，消费三条 jsonl **赛前快照**（区别于 replay 型现场重算）JOIN odds.db（post_match_review 主、matches 补 TG），指标 import 同源、三闸门只出判定不自动切。首份基线 n=182（剔除：v5 缺 TG 八档 33、无赛果 32）：比分 v5 top1 10.99 vs v4 9.89、top5 48.9 vs 43.41（within 覆盖反降）；**λ 校准 p=0.263 不显著继续累积；w_odds 0.15→0.30：RPS p=0.002、Brier 0.2391、ECE 改善，三闸门全过 → 待人工评审是否切生产**。脚本暂手工跑、未挂计划任务。教训已记：paired_t_test 返回 (t,df,p)；命中率类指标箭头方向。
  - **P1（可选，需用户发令）**：探索 0.35~0.40 权重。权重收益比校准方法大一个量级，是更值得探索的方向。但须在 B3 Shadow 门禁通过后、或与 B3 并行做独立离线实验（单一变量原则）。
  - **P2（低优先，条件触发）**：isotonic/temperature scaling 替代 mean-ratio factor。**触发条件**：Shadow 累积数百场后 B3 高桶 gap 仍 >0.10 且有继续收敛需求时才做。无此条件不得启动。
  - **【禁止】联合上线 factor+Shin**：C-041 已证伪——高桶 gap 0.201 退化 + Brier p=0.113 不显著。
  - **【禁止】在 B3 Shadow 门禁通过前改动生产 `w_odds` 或 `tg_calibration.enabled`**：生产恒 w_odds=0.15、enabled=false。

**odds.db 内 Understat 表结构（2026-08-22 新增，第三数据源）：**
- understat_match_team_stats: 比赛级 xG（match_id, 主客队, 比分, xG, forecast 胜平负概率）
- understat_player_xg: 球员级 xG 体系（xG/xA/xGChain/xGBuildup/key_passes）
- understat_shots: 射门级（坐标 X/Y、xG、射门部位/情况/结果）

**联赛命名规范：**
- league 字段：`英超2025-2026赛季`, `西甲2025-2026赛季`, `意甲2025-2026赛季`, `德甲2025-2026赛季`, `法甲2025-2026赛季`
- match_type 字段（odds.db）：与 league 字段保持一致

### 2.3 代码风格

- 缩进：4空格
- 命名：snake_case（函数和变量）、PascalCase（类）
- 注释：docstring 风格
- 类型提示：所有函数必须有类型提示
- 错误处理：try-except 必须记录日志

### 2.4 导入脚本规范

1. 解析 txt/csv 文件，提取比赛和赔率数据
2. 检查是否已存在（避免重复）
3. 先清除旧数据，再插入新数据
4. 直接写入 odds.db 四张时序表（~~odds_timing.db~~ 中转库已删除 C-20260918-021）
5. 写入 import_log 日志
6. 验证数据完整性

### 2.5 特征工程规范（D-009）

1. 所有新特征必须在 `feature_temporal.py` 的 `PRE_MATCH_FEATURE_CATALOG` 中注册
2. 赛后特征（homeGoals/awayGoals/xG/shots/possession等）严禁进入特征矩阵X
3. 训练前必须调用 `validate_no_leakage(X)` 进行断言式验证
4. 特征属性标记表 `feature_attributes.csv` 随每次训练自动生成
5. 泄露检测基于真实特征矩阵X动态检测，禁止使用硬编码静态列表

---

## 三、经验教训

### 3.1 数据相关 (EXP-001 ~ EXP-006)

| 经验ID | 经验描述 | 发现日期 | 影响 |
|--------|----------|----------|------|
| EXP-001 | 德甲/法甲球队行格式不同（D1 vs 法甲 Ligue 1），解析时需适配 | 2026-08-04 | 导入脚本需按联赛定制 |
| EXP-002 | 比分表头用tab分割，而非空格分割（如 '1 : 0' 被空格拆成3部分） | 2026-08-05 | 比分解析必须用 `.split('\t')` |
| EXP-003 | 赔率时间戳有两种：真实发布时间 vs 导入时间(2026-07)，需过滤 | 2026-08-04 | 同步时过滤 2026-07 前缀的时间戳 |
| EXP-004 | 不同联赛的时序赔率记录数差异大（法甲法甲81条/场 vs 德甲15条/场） | 2026-08-05 | 需检查数据完整性 |
| EXP-005 | NOT NULL 约束问题（如 handicap_timing.hcp_draw 可能为 NULL） | 2026-07-24 | 插入前检查表结构约束 |
| EXP-006 | match_id 格式必须统一，否则无法关联两个数据库 | 2026-07-23 | 映射脚本自动生成标准 match_id |

### 3.2 模型相关 (EXP-007 ~ EXP-010)

| 经验ID | 经验描述 | 发现日期 | 影响 |
|--------|----------|----------|------|
| EXP-007 | h2h_last_result 因运算符优先级 bug 导致标签泄露（1:1映射） | 2026-07-24 | 必须添加括号确保 date 过滤 |
| EXP-008 | 虚假高准确率(98.32%)通常意味着数据泄露 | 2026-07-24 | 回测必须用时间序列交叉验证 |
| EXP-009 | 模型真实准确率(41.67%)低于基线(47.22%)说明预测能力不足 | 2026-07-24 | 需增加赔率特征优化 |
| EXP-010 | 平局预测(11.11%)极差，需特殊处理 | 2026-07-24 | 使用 class_weight 或欠采样 |
| EXP-016 | five_leagues.db仅含3联赛875场，odds.db含5联赛1265场，数据源选择直接决定模型上限 | 2026-08-05 | 训练前必须确认数据源完整性 |
| EXP-017 | 赔率特征为0维的根因：球队名中英混杂+时间戳格式不统一，单一match_id格式无法匹配 | 2026-08-05 | build_odds_features必须支持多格式match_id匹配 |
| EXP-018 | pd.to_datetime()对混合格式时间戳报错，必须使用format='mixed' | 2026-08-05 | 所有时间戳解析统一使用format='mixed' |
| EXP-019 | 集成39维赔率特征后，CV准确率从44.7%提升至48.6%，过拟合差距从0.377降至0.292 | 2026-08-05 | 赔率特征是最强预测信号，必须集成 |
| EXP-020 | XGBoost早停轮数25过小导致欠拟合，best_iteration仅19；增大n_estimators至300后best_iteration达93 | 2026-08-05 | n_estimators和early_stopping_rounds需配合调整 |
| EXP-021 | D-009特征泄露检测发现：当前113维特征实际无泄露（homeGoals/awayGoals等只在df中用于计算标签y，未进入X），启动脚本的硬编码静态检查列表是误报 | 2026-08-05 | 泄露检测必须基于真实特征矩阵X动态检测，而非硬编码静态列表 |
| EXP-022 | feature_temporal.py采用黑名单+白名单双重机制：黑名单明确禁止赛后特征，白名单标记每个特征的时序属性（pre_match/derived_pre/post_match），未分类特征触发告警 | 2026-08-05 | 未来新增特征必须在PRE_MATCH_FEATURE_CATALOG中注册，否则触发unknown告警 |
| EXP-023 | 特征属性标记表(feature_attributes.csv)应包含：序号/特征名/类别/时序类型/是否赛前可用/是否泄露风险/来源函数/描述，便于审计和团队协作 | 2026-08-05 | 特征工程必须有完整文档，不可只靠代码注释 |
| EXP-024 | D-010新增17维赔率衍生特征（凯利指数6+变化率6+市场信心度3+价值投注2），CV标准差从4.3%降至1.6%（模型更稳定），但准确率未提升（48.0%持平），过拟合差距略增0.026，说明存在冗余特征 | 2026-08-05 | 衍生特征与原特征高度相关（如bookmaker_margin=wdl_overround-1线性变换），需D-011特征选择剔除冗余 |
| EXP-025 | 凯利指数公式 Kelly=(p×odds-1)/(odds-1)，其中p为归一化隐含概率，odds为收盘赔率，需clip到[-1,1]防止极端值；>0表示赔率被高估有投注价值 | 2026-08-05 | 赔率衍生特征计算必须做异常值处理 |
| EXP-026 | 赔率变化率=(close-open)/open，需clip到[-1,1]；负值表示该结果可能性上升（市场看好），正值表示看淡 | 2026-08-05 | 动量指标方向解读需统一，避免符号混淆 |
| EXP-027 | D-011三方法融合特征选择：相关性分析(剔除|corr|>0.9)+XGBoost重要性(gain+weight双指标avg_rank)+RFE验证，130→65维降维50%，CV准确率48.0%→49.3%，过拟合差距0.318→0.283，验证集CV标准差1.85%→0.85%稳定性提升54% | 2026-08-05 | 三方法融合比单一方法更稳健，相关性分析先剔除冗余，XGB重要性排序，RFE交叉验证 |
| EXP-028 | XGBClassifier的booster.get_score()在sklearn API下返回的键是原始列名而非f0/f1格式，直接用f'f{i}'映射会全部返回0；必须用model.feature_importances_(sklearn标准)作为gain主通道，booster.get_score()作为补充 | 2026-08-05 | XGBoost特征重要性提取必须双通道：sklearn API + booster API，避免特征名映射bug |
| EXP-029 | 强制保留所有赔率特征(56维)的策略有效：赔率特征是最强预测信号，即使相关性高(如wdl_close_win↔wdl_open_win corr=0.95)也保留两者；剩余配额(9维)从基础/球队特征中按XGB avg_rank选取 | 2026-08-05 | 特征选择需结合业务知识，赔率信号不可丢弃，仅在非赔率特征中做选择 |
| EXP-030 | D-011验证脚本(独立5折TimeSeriesSplit)显示降维后CV标准差从1.85%→0.85%(降54%)，但集成到train_models_v2.py(EnhancedTimeSeriesCV)后CV标准差2.27%，说明两种CV实现差异较大；应以train_models_v2.py的CV为准 | 2026-08-05 | 验证脚本和正式训练的CV实现不同，结果会有差异，对比时需注明CV类型 |
| EXP-031 | SofaScore API的反爬机制是Akamai Bot Manager(TLS JA3指纹)，不是IP限流或HTTP headers，标准requests.get(headers={"User-Agent":...})必定403；必须用curl_cffi(impersonate="chrome")模拟Chrome的JA3/JA3S指纹才能正常返回200；无需注册账号、无需API Key | 2026-08-09 | 采集SofaScore必须用curl_cffi impersonate="chrome"，不能用标准requests |
| EXP-032 | SofaScore incidents接口的事件类型字段是`incidentType`（值为"goal"/"substitution"/"card"）而非`type`（type为None会覆盖成phaseStart/matchEnd）；换人原因是`injury`布尔字段而非reason字符串；不注意会导致进球/换人/红黄牌事件解析为0条。球员进球/yellow/red必须用incidents交叉索引覆盖lineups中的statistics.goals值（lineups的statistics.goals偶尔与实际事件流不一致） | 2026-08-09 | ① 事件类型用incidentType判断；② 换人/进球/红黄牌数据必须以incidents接口为准并构建字典索引反查，不可信任lineups统计字段 |
| EXP-033 | fbref_schema.py的4张fbref表结构可直接复用于SofaScore：fbref_match_id存sofascore event_id、fbref_player_id存sofascore player_id、stats_source='sofascore'区分；但match_player_stats的92个fbref列名与SofaScore的statistics key不直接对应（fbref:passes_completed vs sofascore:accuratePass），必须新增34列SofaScore专有扩展列并用SOFA_TO_FBREF_FIELD_MAP动态映射；stats_json列存储全量原始statistics JSON，作为字段映射失败时的兜底；ALTER TABLE ADD COLUMN在列已存在时会报错，需捕获"duplicate column name"异常 | 2026-08-09 | 跨数据源复用DB schema时：① 用stats_source列区分来源；② 用字段映射表+新增扩展列处理异构字段；③ 扩展列ALTER必须有异常忽略重复列；④ stats_json必存，便于未来从历史数据解析新字段 |
| EXP-034 | 比分赔率特征覆盖率仅23.0%（1209/5252场），根因：score_history表使用中文球队名（如"利物浦"），matches表使用英文球队名（如"Liverpool FC"），match_id格式不兼容；通过双向映射（TEAM_NAME_MAP + cn_to_en反向映射）生成多个候选match_id可提升覆盖率，但TEAM_NAME_MAP覆盖不全（当前约80队）导致仍有大量比赛无法匹配 | 2026-08-10 | 比分赔率特征覆盖率提升需要：①扩充TEAM_NAME_MAP至覆盖所有历史球队名称变体；②考虑模糊匹配（编辑距离/拼音相似度）；③记录未匹配案例用于针对性补充映射 |
| EXP-035 | 非线性变换特征（29维）中包含大量与原始赔率特征高度相关的衍生特征（如log(赔率)、sqrt(赔率)与原始赔率线性相关），D-011特征选择会自然剔除这类冗余特征；实际保留的非线性特征维度取决于特征选择阈值，不强制保留可避免引入噪声 | 2026-08-10 | 非线性变换特征设计原则：①优先选择与原始特征非线性关系强的变换（如熵、基尼系数、交互项）；②简单数学变换（log/sqrt/平方）可能被D-011剔除，不应强制保留；③跨特征交互（如概率×凯利）比单特征变换更有价值 |
| EXP-036 | 新特征模块集成到现有管线需遵循固定模式：①创建独立模块（如score_features.py）；②在feature_utils.py的build_all_features()添加include_xxx参数；③在feature_temporal.py的PRE_MATCH_FEATURE_CATALOG注册所有新特征（类别+时序类型+来源函数）；④在feature_selection_d011.py中决定是否强制保留（赔率衍生类强制保留，变换类不强制）；⑤在train_models_v2.py中启用开关并更新特征统计打印 | 2026-08-10 | 五步集成模式确保新特征：①通过泄露检测；②在D-011中正确分类；③在训练日志中可见；④可独立开关控制；⑤不破坏现有特征管线 |
| EXP-037 | Understat 数据端点 getMatchData/{id} 与 getLeagueData/{slug}/{season} 必须带 X-Requested-With: XMLHttpRequest 头，否则 404；Referer 头可省（C-20260921-045 实测确认，采集器仍保留 Referer 作兜底）；未开赛比赛(isResult=false)无 rosters/shots 数据需自动跳过；xG/xA/xGChain/xGBuildup 与射门坐标(X/Y)是 Understat 独有字段，弥补 FBref/SofaScore 缺口 | 2026-08-22（C-045 修正） | 采集 Understat 必须带 X-Requested-With 头、用 isResult 过滤未开赛、404 不重试；三张独立表存原始 xG 体系（比赛级/球员级/射门级） |
| EXP-038 | build_team_features 主循环对每场比赛调用 `home_hist[home_hist['date'] < match_date]` 全量布尔过滤（主/客各1次 + 对手至多20次），造成 O(n·m) 的 DataFrame 反复分配，全量 14362 场耗时 480.7s；修复：复用 precompute_team_stats 已产出的「按日期升序 + RangeIndex」每队统计表，预提取每队日期 int64 数组构建 lookup，用 `np.searchsorted(side='left')` O(log n) 定位「date<md 最近一场」，并以 `range(len(df))` 替代 `iterrows`；因缓存经 merge 后为 RangeIndex 且严格升序，布尔前缀过滤与 `.iloc[:k]` 完全等价、语义无损 | 2026-08-30 | 特征构建性能优化：全量 build_team_features 480.7s→55.9s（88%提速）；正确性三重校验 0/166 + 0/500 + 0/500 零差异；时序「最近一场」查找优先 searchsorted 而非布尔过滤 |
| EXP-039 | build_all_features 其余模块（Elo/D-013 时序赔率/比分赔率/赔率特征 build_odds_features/三通道对齐 build_match_alignment）的共性瓶颈是「逐行/逐组/逐场」循环与重复 DB 查询/大表 JOIN；统一替换为 numpy 向量化 + `np.searchsorted` O(log n) + 一次性批量加载 + 内存 set/dict 对齐。收益：Elo 29.34s→1.05s、比分赔率 59.65s→28.47s、赔率特征 63.69s→4.82s、D-013 4.58s，端到端 build_all_features 瓶颈消除 | 2026-08-30 | 特征构建二次优化：逐模块 profiling 定位 O(n·m)/iterrows，numpy 向量化 + searchsorted + 整表加载替换；逐字段三重校验零差异 |
| EXP-040 | 总进球赔率特征 legacy bug：`_build_odds_features_legacy` 在末条 total_goals 快照 `goals_3..goals_7_plus` 含 NULL（未开赛/部分数据场次，仅存 under 市场 goals_0/1/2）时，`total_prob=sum(...)` 变 NaN 使归一化跳过，但 `under_25/over_25/tg_expected` 仍在 `if total_prob>0` 块外计算，产出未归一化垃圾值（如 tg_under_25_prob=8.98）；向量化版用 `valid=total>0` 守卫，`total=NaN→valid=False→返回 NaN` 后中位数填充，语义正确。影响范围仅 67 场 partial-NaN 行 + 无数据行中位数微移 | 2026-08-30 | 计算派生特征时，归一化守卫（分母>0）与派生值赋值必须在同一 if 块内，否则部分缺失数据会产出未归一化值；向量化时用 `np.where(matched & valid, ..., np.nan)` 统一守卫 |
| EXP-041 | matches.league 列被活跃写入口（prediction_db_writer.py 等）漏写——联赛信息完整编码在 match_type（如「英超2026-2027赛季」）但 league 列为 NULL，导致 `load_completed_matches` 等用 `league IS NOT NULL` 硬过滤的下游消费端断粮（08-25 后完赛 83 场中 58 场被丢弃）。修复：一次性 `_backfill_league.py` 按 match_type 前缀回填 94 行 + 读取端新增 `_derive_league()` 从 match_type 兜底派生（C-20260911-023） | 2026-09-11 | 写入口分散时，读取端从冗余编码字段兜底派生是单点根治；新写入口应同时写 league 与 match_type；下游过滤慎用 `IS NOT NULL` 硬过滤，优先按业务主键（actual_score）过滤 + 派生兜底 |
| EXP-042 | shadow 配对双跑（同一 match_id 记录 control/treatment 两臂、treatment 永不真实 serve）场景下，门禁必须用配对 McNemar（`ab_test_framework.analyze(paired=True)`）；复用线上 served 分流 z 检验会因 treatment served_n=0 恒报 insufficient-sample。线上真实分流场景沿用 paired=False 原口径不变（C-20260911-022） | 2026-09-11 | 离线 shadow/回放对照 = 配对样本 → 配对检验；线上分流 = 独立样本 → 两比例 z/Mann-Whitney；统计口径必须与实验设计匹配 |
| EXP-043 | 500.com 强刷可能出现公司明细/投注表有真实数据，但 `odds500_ouzhi_summary.company_count` 和摘要赔率字段为 NULL；“采集命令完成”不能等同于摘要有效，必须同时校验 summary 核心字段、company 明细和 betting 数据。报告生成器还会排除已开赛场次，补采后不可直接用全量生成覆盖历史赛前报告 | 2026-09-13 | 500.com 48场强刷：company 明细均有30行、betting各1行，但 summary company_count 48/48 NULL；SofaScore 当日特征77/77有效；需修复摘要写入/解析和历史报告回写策略 |
| EXP-044 | SofaScore 采集器字段映射误把 `accuratePass`（准确传球**数**，整数）映射到 `pass_completion_pct`（**百分比**，0-100）列，导致 `accurate_pass_sofa` 长期未赋值、`sofa_pass_sr_5g` 分子缺失触发 `PASS_SR_MIN` 门禁，报告误报「未采集」；修复：映射改 `accuratePass`→`accurate_pass_sofa` + 迁移脚本 `repair_pass_sr_mapping.py` 把误存值迁回并清空污染（C-20260915-005） | 2026-09-15 | 跨源字段映射必须核对「计数 vs 比例」语义；同一列被两个源以不同单位复用时（FBref 存百分比 / SofaScore 存计数）应拆分为独立列，避免单位污染 |
| EXP-045 | P0-B 门禁平局高估 +81.3pp 根因**非模型校准而是历史退化数据伪影**：444 场 Score_grid（`t006_score_predictor_v5`，2016-2023 历史批量回填、`wdl_history` 赔率为空）退化为单一高分比分格（如 `5:5=0.874`），系更早版本算法（λ 无上限/无哨兵）IPF 乘性迭代坍缩；当前 `predict_score_distribution_v5`（λ≤2.775、无赔率走泊松兜底）已无法复现。修复：`predict_score_distribution_v5` 加健康哨兵（对角线>0.6 或单格>0.6 → 回退干净 DC 网格，阈值 0.6 远高于真实值上限零误伤）+ `clean_degenerate_score_grids.py` 备份删除 15984 行；门禁复跑 PASS（总 ECE 0.49%/ECE_draw 0.27%/分桶偏差 11.3pp，平局 25.42% vs 命中 25.18%）（C-20260924-072） | 2026-09-24 | 门禁类指标异常先区分「模型坏」vs「数据伪影」：444 条(3%)退化数据即可拖出 +81pp 假性高估；比分网格产出链路必须有退化哨兵（单格/对角线过载检测），历史回填数据需定期健康扫描 |

### 3.3 流程相关 (EXP-011 ~ EXP-015)

| 经验ID | 经验描述 | 发现日期 | 影响 |
|--------|----------|----------|------|
| EXP-011 | 每日对话前需检索所有日志文件，避免遗漏上下文 | 2026-07-23 | 建立日志检索流程 |
| EXP-012 | 任务完成后必须更新所有相关日志文档 | 2026-07-23 | optimization_log + change_log + prompt_template |
| EXP-013 | 阶段切换时必须更新 prompt_template.md | 2026-07-23 | 保持上下文模板与当前阶段同步 |
| EXP-014 | 数据变更必须先备份再操作 | 2026-07-24 | 防止数据丢失 |
| EXP-015 | 多步骤任务必须用 TodoWrite 规划 | 2026-08-04 | 确保任务完整性 |
| EXP-046 | **编辑工具会给带 BOM 的文件重复写 BOM，须落盘后复核字节头**（C-20260927-004）：change_log.md 文件头实测累积 **4 个** UTF-8 BOM（`EF BB BF × 4`，历史编辑工具重复写入）；用 Edit 工具修改该文件后 BOM 数又从 1 变 2——保存时工具无条件再写一个 BOM，多次编辑即堆叠。教训：①统计前导 BOM 必须用循环逐 3 字节计数，不能只抽查前 9 字节（本次误判为「三重」实为四重）；②对带 BOM 文件做任何编辑后，必须用字节头复核（`EF BB BF 23` 为单 BOM 正常态），发现堆叠用「剥除全部前导 BOM → 重写单个」归一；③各文档 BOM 惯例不同（change_log.md 有单 BOM、五大联赛全栈框架设计.md/project_memory.md 无 BOM），按各自原状维持，禁止顺手互相改造；④PS1 含中文必须 BOM 见 §10.3 第 5 条 | 2026-09-27 | 带 BOM 文档编辑后强制字节头复核；BOM 计数用循环不用抽样；单文件 BOM 惯例保持一致 |
| EXP-047 | **本机新版 Python 全绿不等于 CI 旧版本全绿：PEP 649 会掩盖注解 NameError**（C-20260927-005）：CI 红灯 run#3~#10 排查发现，`team_name_mapping.py:405` 用 `Optional[str]` 注解却未 `from typing import Optional`——**Python 3.14 因 PEP 649 默认对函数注解延迟求值，定义函数时不解析注解故不报 NameError；3.11/3.12 立即求值，import 模块即炸**。本机仅装 Py3.14、293 用例全绿，该问题长期不可见，直到 CI 关闭 fail-fast 跑全矩阵（3.11/3.12 四 job 全挂、3.14 两 job 全绿，跨 OS 一致）才暴露。教训：①凡用 typing 注解名（Optional/List/Dict/Union…）必须确认已 import，可用 AST 脚本扫「注解引用 typing 名但未绑定、且无 `from __future__ import annotations`」的文件（本次 602 个 .py 仅 1 处隐患）；②CI 矩阵必须保留与生产一致的最低 Python 版本且 `fail-fast: false`，否则单 job 失败会级联取消其他版本、掩盖真实分布；③Actions job 日志 API 302 重定向到 Azure blob 时必须剥离 Authorization 头，否则 Bearer 泄漏给 blob 端返回 401（urllib 需自定义 redirect handler） | 2026-09-27 | typing 注解必查 import；CI 多版本矩阵+fail-fast:false 不可只信本机新版；拉 Actions 日志重定向时剥离认证头 |

---

## 四、记忆系统规则

### 4.1 对话策略 (MEM-001 ~ MEM-006)

| 规则ID | 规则内容 |
|--------|----------|
| MEM-001 | 每次新会话开始时，必须读取 prompt_template.md 获取当前上下文 |
| MEM-002 | 每次任务完成后，必须更新 optimization_log.md 和 change_log.md |
| MEM-003 | 关键决策必须记录到 key_decisions.md |
| MEM-004 | 硬性规则变更必须更新本文件 (project_memory.md) |
| MEM-005 | 每日对话前必须执行日志检索流程（见第五章） |
| MEM-006 | 每一项优化必须同步完成实现、验证和文档更新；验证失败或中止也必须记录实际状态、证据和遗留风险，文档未同步不得标记为已完成 |

### 4.2 日志更新规则 (LOG-001 ~ LOG-006)

| 规则ID | 规则内容 |
|--------|----------|
| LOG-001 | optimization_log.md：记录每日完成的任务和状态变化 |
| LOG-002 | change_log.md：记录每次代码/参数/配置变更 |
| LOG-003 | key_decisions.md：记录重要决策及其状态 |
| LOG-004 | prompt_template.md：更新阶段标识、已完成项、待办事项 |
| LOG-005 | project_memory.md：更新经验教训和规则 |
| LOG-006 | import_log 表：数据导入必须写入日志 |

### 4.3 快照机制

每次重要会话结束时，生成会话快照：
- 会话ID和时间戳
- 完成的任务列表
- 关键决策和代码变更
- 下次会话的待办事项

---

## 五、每日对话检索清单

### 5.1 检索流程

每次新对话开始时，按以下顺序检索：

```
1. 读取 project_memory.md（本文件）
   - 获取硬性规则
   - 获取工程约定
   - 获取经验教训

2. 读取 prompt_template.md
   - 获取当前阶段标识
   - 获取已完成优化项
   - 获取性能基准值
   - 获取待办事项

3. 读取 optimization_log.md
   - 查看最新任务完成情况
   - 查看问题修复进度

4. 读取 change_log.md
   - 查看最近的代码/配置变更
   - 了解变更原因和关联决策

5. 读取 key_decisions.md
   - 查看关键决策状态
   - 待实施决策列表
```

### 5.2 快速检查清单

- [ ] 当前阶段和进度已了解
- [ ] 已完成的优化项已记录
- [ ] 关键参数配置已确认
- [ ] 性能基准值已更新
- [ ] 待办事项已明确
- [ ] 硬性规则已检查
- [ ] 变更日志已同步
- [ ] 决策日志已更新

### 5.3 关键文件索引

| 文件 | 路径 | 用途 | 更新频率 |
|------|------|------|----------|
| project_memory.md | 项目根目录 | 规则/约定/经验 | 规则变更时 |
| prompt_template.md | docs/ | 上下文模板 | 每次阶段切换 |

---

## 七、当前模型最新状态（2026-09-12）

> **最后更新**: 2026-09-15
> **最新训练**: ✅ 20260908_004604（全量特征集 **254 维** selected_features，含 slim_odds+ts_odds+consensus+T-007 球员 lag 等）
> **最新模型资产**: assets/lgb_model_20260908_004604.pkl, assets/xgb_model_20260908_004604.pkl, assets/scaler_20260908_004604.pkl, assets/selected_features_20260908_004604.pkl, assets/draw_calibrator_params_20260908_004604.json, assets/feature_bridge.json（训练↔服务端 254 维特征桥接，C-20260910-011）
> **状态唯一权威**: P0~P2 优化项状态以 docs/模型优化评估报告_v2.0.md 为准（**已删除 C-20260918-049**，替代位置见 五大联赛全栈框架设计.md §十二，C-20260911-020 六文档职责收敛）
> **09-12 数据侧要点**: ①500.com 采集器已升级 curl_cffi + EdgeOne 手动 Cookie（C-20260912-001）；②亚盘刷新通道修复，26/27 完赛场结算盘口 74/74 = 100%（C-20260912-003）；③统一报告新增第十二章球员/阵容（pa_*）与第十四章积分/战意两通道（C-20260912-002）。详见 §6.3.4。

### 6.1 模型架构

| 维度 | 模型 | 特征维度 | 说明 |
|------|------|:------:|------|
| WDL 胜平负 | 5基础模型 (DixonColes + Elo + XGB + LGB + 贝叶斯) + LR meta-learner | **254维** | slim_odds=True（精简赔率30维）+ ts_odds=True（+33：时序22+比分8+扩展3）+ consensus_odds=True（+10）+ 球员 lag/其他全量特征集至 254 维；Stacking 优先 LR meta-learner（OOF RPS 0.1984）、回退固定权重；训练↔服务端 254 维 CI 强制对齐（C-20260910-004）+ feature_bridge 桥接 110 维真实值（C-20260910-011） |
| 统一引擎（比分/总进球） | DixonColes 引擎全量投产 | - | C-20260909-008 全量切换（ScorePredictor/TotalGoalsPredictor 走 DC 引擎，四维统一导出）；P1-C' 双基线补对照四指标完全一致（C-20260910-008） |
| T-005 v3 让球 | 两阶段 LGBMClassifier (draw_detector + direction_predictor) | 71维 | 温度 T=1.0（argmax），走水召回率 42.2% |
| T-006 v5 比分 | Poisson(Dixon-Coles) + 去水 WDL 数值求解 λ + IPF 重加权 + **生产 WDL 锚定** | - | v5 替代 v4（C-20260908-022）：Top-1 13.14%（v4 10.70%）、Top-3 30.44%、Top-5 45.47%，WDL 不劣化；生产锚定读 model_predictions WDL_home/draw/away（缺失回退去水）；⚠️ v5 仅接入 C2 回测模块 `score_prediction_module.py`，实时赛前报告路径 `generate_unified_report→prediction_core.ScorePredictor` 仍为 **T-006 v4**，报告 MODEL_HEADER/比分方法 应标 v4（2026-09-14 端到端验收修正） |
| P1-B 贝叶斯增量（shadow） | EKF 增量更新 attack/defense 后验 | - | 离线滚动 shadow 与在线 Node 预测解耦（`run_shadow_incremental.py`）；τ 逐联赛标定（全 0.01）；生产仍 serve control，全量重滚后 5/5 后验 STABLE、意甲配对门禁 SIGNIFICANT-WIN（p=0.0484/净胜+31）、其余 NO-SIGNIFICANT-DIFF（C-20260910-015 ~ C-20260911-025） |
| 总进球 | Poisson λ + 总进球赔率融合 | - | λ 动态调整 |

### 6.2 关键参数

| 参数 | 值 | 说明 |
|------|:--:|------|
| slim_odds | True | 精简赔率 30维（基础） |
| ts_odds | True | 时序/比分赔率 33维（D-013 22 + T-003.1 8 + 扩展 3）+ 联赛 z-score 归一 |
| consensus_odds | True | 多博彩公司赔率一致性 10维（全量特征集合计 254维） |
| Train-Serving 对齐 | 254 维 | selected_features_20260908_004604.pkl = feature_scaler_params.js feature_names；CI 强制对齐测试 tests/verify-feature-alignment.js；feature_bridge.json 桥接 110 维真实值（C-20260910-004/011） |
| unified_engine | enabled | DixonColes 引擎全量投产（C-20260909-008），legacy 可回退（USE_UNIFIED_ENGINE=0） |
| P1-B shadow τ | 0.01（逐联赛统一） | 英超按 Acc/RPS 权衡取 0.01（C-20260911-018/019）；生产切换前需连续 K=3 轮稳定 + min-n≥60（C-20260911-024） |
| T005V3_TEMPERATURE | 1.0 | 温度缩放取消（argmax） |
| T006_MC_SIMULATIONS | 500 | 蒙特卡洛模拟次数 |
| WDL_TEMPERATURE | 0.8 | WDL 温度缩放 |
| 英超独立模型 | epl_reference | 从 Stacking 移除，独立输出参考 |

### 6.3 数据源

| 数据源 | 路径 | 大小 | 覆盖 |
|--------|------|:--:|------|
| odds.db | data/odds.db | 1,669MB | 14,521 场比赛（league 空值 0，C-20260911-023 已回填；26/27 新写入行仍有 32 行 NULL 待下轮回填），赔率覆盖率 98.3%（双通道） |
| sofascore_team_features | data/odds.db (表) | 18,363行 | 98 字段（68 维 sofa_* + 24 维 pa_* 球员可用性），5 大联赛（英超3,940/西甲3,877/意甲3,876/法甲3,564/德甲3,106；2026-09-12 实测） |
| match_player_stats | data/odds.db (表) | 730,128行 | 2016-08~2026-09 跨 11 赛季 sofa+fbref；09-12 补采巴伦西亚 3 场评级 + 6 队 16 场新赛季统计（C-20260912-001） |
| Understat 三表 | data/odds.db (表) | 529369+451488行 | 五大联赛×10赛季（16/17~25/26）全量完整 |
| 500.com 五表 | data/odds.db (表) | match 19,790 / summary 19,761 / company 542,546 行 | 16/17~25/26 十季 18,038 场 100% + 26/27 五联赛 1,752 场赛程全入库；采集器已升级 curl_cffi + EdgeOne Cookie（C-20260912-001） |
| Sporttery 时序赔率 | data/odds.db (表) | wdl_history / handicap_history / total_goals_history / score_history | 覆盖 16/17~25/26 全部 10 季（已结束赛季 100% 完成），26/27 随赛程推进（sporttery_live_collector 定时采集） |
| 26/27 赛季覆盖 | - | 184场(matches表，54 场完赛有比分) | 赛果采集滞后：完赛仅英超15/西甲19/法甲14/意甲4/德甲2；积分榜/战意通道带不完整保护（C-20260912-002） |
| 赔率 TXT（已删除） | ~~data/{联赛}2026-2027赛季完整时序赔率.txt~~ | - | 已于 C-20260918-021 全量删除，数据已入库 odds.db 四张时序表；预测脚本改走 `load_odds_from_db.py`（DATA-011） |
| SofaScore 26/27 覆盖 | sofascore_team_features / match_player_stats 表 | 赛前特征已预生成 | 09-12 补采 19 场（巴伦西亚3+6队16），4 场赛前报告完整度 100%；后续轮次仍依赖赛后采集链路 |
| SofaScore 赛前首发/伤停 | match_predicted_lineups / match_missing_players 表 | 374+134 行（09-18 实测） | 赛前采集链 `collection/prematch_sofascore_lineups.py`（管线步骤 0a-1 每日 20:30），官方伤停接入 PA 特征校正（FEAT-013），未来 122 场 PA 已重算回写 |
| Understat 26/27 覆盖 | understat_match_team_stats 表 | 22场 | 法甲1/西甲20/英超1，待补采 |
| 500.com 26/27 亚盘 | odds500_match 表 | 113/1,752 场有盘口 | 74 场完赛结算盘口 100% 补齐（意甲38/西甲28/英超19/法甲19/德甲9）；未开赛场次临近开赛前跑 `--refresh-odds` 补盘（C-20260912-003） |

#### 6.3.1 赔率导出 Excel 文件（派生数据，非数据源）

`data/` 下两个 Excel 文件均由 `scripts/export_odds_excel.py` 从 `odds.db` 导出的**人工审阅用报表**，不是模型训练的数据源。代码中仅引用 v2 版本作为输出路径。

| 文件 | 大小 | 引用 | 说明 |
|------|:--:|:--:|------|
| `odds_data_export.xlsx` | 1.08MB | **无**（旧版，已废弃） | 缺少「比分赔率时序_样例」sheet，其余内容与 v2 完全一致 |
| `odds_data_export_v2.xlsx` | 1.25MB | `export_odds_excel.py#L15`（OUTPUT） | 当前有效版本，含 7 个 sheet（比赛总览/比赛明细/WDL/让球/总进球/比分赔率时序样例/WDL时间点分布） |

**两个文件内容对比（2026-08-24 查验）**：
- 比赛总览 / 比赛明细 / WDL时序赔率_样例 / 让球时序赔率_样例 / 总进球时序赔率_样例 / WDL时间点分布 — 行数、内容**完全相同**
- 唯一区别：v2 多了「比分赔率时序_样例」sheet（5001 行，对应 `score_history` 表）
- 结论：`odds_data_export.xlsx` 是 v2 之前的旧输出，已无代码引用，可安全删除

#### 6.3.2 文档自动更新机制（2026-08-26 固化）

> 用户要求「以后每次优化都自动更新」。以下为**硬性规则**，每次完成代码/参数/配置/数据/文档优化后必须执行：

1. **变更日志**：在 `docs/change_log.md` 新增 `C-YYYYMMDD-NNN` 变更记录（含变更前/后/原因/验证结果），并同步更新 §5 变更统计表。
2. **数据采集进度**：涉及数据源时，同步更新 `docs/data_collection_progress.md`（完成情况 + 完整度标注）。
3. **项目记忆**：涉及规则/约束/数据源/经验时，同步更新 `docs/project_memory.md` 对应章节。
4. **技能文档**：涉及采集器/脚本时，同步更新 `.trae/skills/local-football-scraper/SKILL.md`。
5. **顺序**：按 P0（阻塞性过时）→ P1（重要差距）→ P2（补充完善）优先级执行，不遗漏关键更新。

#### 6.3.3 自动化调度与生产运维状态（2026-09-11 实测）

Windows 计划任务（5 项 Ready + 1 项脚本就绪待注册）：

| 任务 | 触发 | 内容 | 关键状态 |
|------|------|------|----------|
| T005v3_AutoRetrain | 每日 08:00 | 自动重训触发器守护（三重触发 + 性能门禁） | Ready |
| SoccerModel_ConceptDrift | 每日 08:30 | P0-C 概念漂移检测（`concept_drift_gate.py`，仅告警禁止自动重训） | Ready，Last Result 0 |
| SoccerModel_ReliabilityGate | 每日 09:00 | P0-B 可靠性图门禁（`reliability_gate.py`，ECE/Reliability Diagram/全维度分层，超阈值 exit 1；`setup_reliability_scheduler.py` 管理） | 脚本就绪，⏳ 待管理员注册（非提权报 Access denied） |
| SoccerModel_PostMatchReview | 每小时 | P0-A 复盘闭环（`run_post_match_pipeline.py` A2→A6，--catch-up --limit 30） | Ready；post_match_review 77 条 |
| SoccerModel_P0ELiveTrial | 每日 12:00 | P0-E 实盘验证六步编排（`run_daily_p0e.py`，--auto-commit） | Ready；台账 0/300（单日候选 0 属预期） |
| SoccerModel_P1BShadow | 每日 13:00 | P1-B shadow 滚动吸收（`run_shadow_incremental.py --roll`；`setup_shadow_scheduler.py` 管理，`/ru SYSTEM` 非交互登录） | Ready；shadow ab_test_log 28,834 行（配对双跑样本） |

> 运维注意：① P1BShadow 为 `/ru SYSTEM`，若 Python 依赖装在用户 site-packages 可能读不到（需系统级安装或改 `--ru`）；② 任务默认 No Start On Batteries，电池供电会跳过；③ 重新注册用 `scripts/setup_shadow_scheduler.py`（支持 --query/--ru/--password/--st/--python，C-20260911-025）。

#### 6.3.4 500.com EdgeOne 反爬与亚盘刷新运维（2026-09-12，C-20260912-001/003）

1. **反爬本质是 TLS 指纹，不是 JS**：500.com 部署腾讯云 EdgeOne（JS 挑战 + Security Verification 两层），`requests` 的 JA3 指纹被识别。解法为 `curl_cffi`（`Session(impersonate="chrome")`）+ 手动 Cookie，**不是**上 headless 浏览器。
2. **Cookie 三层凭证缺一不可**：`__tst_status`（固定常量）、`EO_Bot_Ssid`、`EO-Bot-Captcha-Token`（真人勾选后写入，唯一放行凭证）；Token 与 UA 绑定，JSON 中需用 `__user_agent` 键记录导出浏览器（Edge 152）UA。文件 `data/cookies_500.json`，采集器 `--cookies` 指定。**Cookie 需每日人工从自己的 Edge 导出刷新**（用户熟悉的手动 cookie 流程，不引入持久化后台方案）。
3. **高频请求会触发 EdgeOne 升级防护**（直接跳过人机层、加重 IP 风控），采集需保持 `--delay`、避免连续探测。
4. **亚盘刷新命令**（只走 getmatch 赛程接口，不逐场抓页面）：
   `python collection/final_500_collector.py --refresh-odds --season 26/27 --rounds 1-8`
   完赛场（status=5）结算盘口全量覆盖并补比分/纠正状态；未开赛场仅在库内盘口为空时补齐。临近开赛每日/赛前数小时重复跑即可，幂等。
5. **完整度口径防复发**：500.com 维度完整度 = 有行 **且** company_count>0/有公司明细；空壳占位行（company_count=0）计缺失（generate_unified_report.py L566-571）。
6. **空壳 = 反爬拦截，不是源站无数据（2026-09-17 修正，C-20260917-001）**：投注页全 `-` / 必发成交为空 / company_count=0 → `anti_bot_blocked`（FAIL），必须刷新 Cookie 重采；**仅当源站真实业务无数据才判 `source_no_data`（WARN）**。二者不可混用。
7. **Cookie 新鲜度先核验再采集（2026-09-20，C-031）**：用户提供的 cookie 可能是旧会话——先看 `sdc_session` 毫秒时间戳判断导出时间；旧 cookie 返回 2122B Security Verification 页。新 token 约前 20~23 次请求真实成功。
8. **EdgeOne 按 TLS 会话"抽签"，重建会话即重抽（2026-09-20，C-031）**：超过首批真实请求后，同 cookie、同请求头（XHR 头有无已对照排除）下，不同 `curl_cffi` 会话有的返回 987B JS 挑战页、有的放行；冷却/加大 delay 均无效。**解法：丢弃当前 Client、新建会话重新 TLS 握手重抽，直到放行**（19 场实际仅 1 次重建即全部通过）。该机制目前在临时驱动脚本中使用，未固化进采集器。
9. **SofaScore 评级覆盖率教训**：球员 `rating` 缺失被 `fillna(0)` 参与加权会把球队评分拉成 2.x 畸变（巴伦西亚 2.80 事件）。补采用 `scripts/backfill_valencia_ratings.py` / `backfill_recent_player_stats.py`（INSERT OR REPLACE 幂等），补后须删旧特征行重跑 `features/incremental_sofascore_features.py`；长期需在特征聚合前加 rating 覆盖率校验（<80% 告警）。

### 6.4 已知限制

- 赫尔城 vs 曼联（周六007, 2026-08-22 19:30）：英超 TXT 中无此场赔率数据，无法预测
- TXT 源数据球队名为缩写，已通过 TEAM_NAME_MAP 补全映射（C-20260823-016）
- 西甲 TXT 中 R. Racing Club vs Villarreal 无法解析（队名含空格+点号）
- TXT 文件仅支持单赛季格式（2026/2027 Regular Season 第X轮），跨赛季数据需另行处理
- 英超/西甲 26/27 赛季首轮：sofascore_team_features 无历史数据，LGB/XGB fallback 到 Poisson 家族 Stacking（C-20260823-021）
- 法甲/意甲 26/27 赛季首轮：有历史 SofaScore 数据（3-4行），ML 模型可正常推理
- 报告第十二章「伤病/缺阵」为代理指标：pa_availability/pa_missing_impact 基于历史出场连续性推断，**不是官方伤病/停赛名单**；真实名单需新增 SofaScore/Transfermarkt 伤病爬虫（C-20260912-002）
- 第十四章积分榜依赖 matches 表赛果：26/27 赛果采集滞后（09-12 仅 54 场完赛）时章节自动显示不完整警告、战意显「—」；赛果补齐后自动恢复，无需改码
- 26/27 亚盘：未到开盘窗口的场次（约第 5 轮以后）handicap 为空属正常，需临近开赛重复执行 `--refresh-odds`

### 6.5 阶段3 P2/P3 中长期剩余项落地（2026-09-15，C-20260915-007~013）

按《基于预测报告发现的问题.txt》v1.0 尚未落地的 P2/P3 项，新增六个诊断/监控/回测模块（`scripts/`）并固化 pytest 口径（`tests/test_p2p3_modules.py`，22 passed）：

| 模块 | 问题项 | 脚本 | 关键结论 |
|------|--------|------|----------|
| 概率校准分档监控 | P2-03 | `probability_calibration_monitor.py` | 5% 分桶；平局档位加权偏差 +1.06pp、超阈值 5 档（系统性低估） |
| 双轨回测系统 | P2-06 | `dual_track_backtest.py` | 轨道A 内核 RPS=0.1978/LogLoss=0.9907/Acc=52.87%/ECE=3.73%；轨道B EV决策 ROI=-5.42%、最大回撤 5.47%、最长连亏 16 场 |
| 风险监控 | P3-02 | `risk_monitor.py` | 触发「最长连亏 16 场」「EV偏差 +26.5pp（EV 被系统性高估）」告警 |
| 低比分低估诊断 | P2-02 | `low_score_diagnosis.py` | 11144 场：0-0/1-0/0-1 低估 +1.79/+4.36/+3.02pp，为 Dixon-Coles/ZIP 迭代提供基线 |
| 数据源冲突检测 | P2-04 | `data_source_conflict_detector.py` | SofaScore 基本面 vs 500 市场信号；校准后冲突率 52.7%→33.7% |
| 战意量化修正 | P2-01 | `motivation_adjustment.py` | 7 条攻/防系数规则 + 消融验证 |

- 重要现实：轨道B（EV>0 择场）平注 ROI 仍为负，与历史「系统性高估」结论一致 —— EV 决策引擎在当前模型×市场组合下无统计显著正 edge，需校准/训练端根治（参见 3.107~3.116 历史闭环）。
- 六个模块均为独立诊断/回测脚本，不接入实时预测链路，不影响线上 WDL/比分推理。

### 6.6 阶段3 P2/P3 深水项落地（2026-09-15，C-20260915-014~016）

承 6.5 六模块后，继续落地三项「真·深水」中长期项（pytest 22→33）：

| 深水项 | 问题项 | 脚本 | 关键结论 |
|--------|--------|------|----------|
| ZIP 零膨胀泊松比分模型 | P3 | `zip_score_model.py` | 11144 场网格 0.00~0.40：低比分校准最优 k_scale=0.3（≤1 偏差 +8.56→+1.02pp）但 LogLoss 劣化 +0.1056，LogLoss 最优 k_scale=0；**ZIP 无增益，不进入生产**。根因=两队进球负相关（Dixon-Coles ρ），单队独立零膨胀无法建模跨队相关 → 建议调 ρ 或升级双变量泊松 |
| 球员推算首发准确率复盘 | P2 | `player_lineup_accuracy.py` + `player_availability_features.predict_xi_players()` | 小样本 200 场英超平均命中率 65.83%/中位数 63.64%；Crystal Palace 83.73% 最稳、Wolverhampton 51.67% 最易翻车；全量 18471 场×5 联赛落库 `player_xi_accuracy_review`（source=inferred，为官方源基线） |
| 官方/推算伤病区分 + 数据源升级 | P3 | `player_injury_source.py` | `InjuryRecord`(source∈official/inferred) + `player_injuries` 表 + `apply_official_injuries()`（官方缺阵覆盖推算，输出 coverage_of_official）；官方采集探测：transfermarkt 直连 405(Cloudflare)/premierleague 404，需 Playwright 或用户 Edge cookies |

- **低比分低估根因（重要升级结论）**：不是单队零膨胀不足，而是**两队进球负相关**未建模。生产 v5 网格（含 Dixon-Coles ρ=-0.15）仍是 T-006 比分输出锚点，下一步优先网格搜索 ρ∈[-0.30,-0.10] 或升级 bivariate Poisson，而非 ZIP。
- **伤病/首发特征可信度基线**：推算首发整体命中率 ~66%，翻车球队（Wolverhampton/Sunderland/Burnley/Leeds 等 ~55-59%）赛前应下调 pa_*/首发特征置信度权重；官方伤病源一旦接入，`apply_official_injuries` 可校正推算并量化 coverage_of_official。

---

## 六、v3 时代新增经验教训（2026-08-12 补充）

### 6.1 T-005 让球胜平负预测经验

1. **数据扩充是关键**: 赔率反推盘口线将样本从242场扩充至3915场（16倍），CV从48.33%提升至55.37%。逻辑回归反推盘口线标签准确率92.3%，是数据扩充的有效方法。
2. **走水过预测根因**: class_weight='balanced' 从源头导致走水概率偏高，需 ratio=1.5 调整。组合优化（ratio=1.5 + T=2.150 + 动态阈值）使走水预测率从66.9%降至19.6%。
3. **规则引擎副作用**: apply_rule_adjustments() 启发式规则在数据量扩大时累计放大走水信号，导致走水率从16.7%→53.3%。方案A：USE_RULE_ENGINE = False，仅保留核心模型。
4. **对手调整Lag特征价值**: 24维对手Lag特征贡献57.1%决策权重，是走水召回率提升的关键。4组特征设计（对手实力分层+盘口线类别+H2H交锋+市场信号）覆盖走水预测的多维度。
5. **性能优化模式**: O(N²) apply → merge_asof 批量对齐 + groupby+rolling 向量化，是大数据量Lag特征计算的标准优化路径。

### 6.2 自动重训触发器经验

1. **静态配置需执行引擎**: deploy_trigger.flag 仅定义配置，必须配合 retrain_trigger_runner.py 执行引擎才能真正触发重训。
2. **数据触发需组合检测**: 单一表记录数检测不可靠，必须组合 matches 表计数 + handicap_history/wdl_history 最新日期哈希，才能准确检测新数据入库。
3. **性能门禁必须强制**: 仅当 passed=True 或 force=True 时才更新线上模型，防止性能退化的模型上线。
4. **Windows计划任务是定时触发关键**: 通过 schtasks 注册 T005v3_AutoRetrain 任务，每日8:00执行守护进程，确保定时触发在Windows环境生效。
5. **状态持久化必备**: trigger_state.json 记录上次检查时间、重训次数、成功率、最近指标，是触发器可靠运行的基础。

### 6.3 文档同步经验

1. **三时代层级识别**: 10份核心文档可分为 pre-ML（54场JS专家系统）/v2（1265场60维LGB）/v3（5252场114维多任务）三个时代，需按时代层级系统性同步。
2. **P0/P1/P2 优先级排序**: 阻塞性过时（P0）→ 重要差距（P1）→ 补充完善（P2），按优先级顺序执行避免遗漏关键更新。
3. **文档版本号管理**: 每次重大更新需提升文档版本号（如 v1.0→v2.0），并在文末记录更新说明，保持文档可追溯性。
4. **跨文档一致性**: 性能指标、阶段状态、模型配置需在 prompt_template.md、CONVERSATION_WORKFLOW_GUIDE.md、PROJECT_DELIVERY_REPORT.md、model_optimization_plan.md 四份文档间保持一致。

### 6.4 v3 时代新增硬性规则

1. T-005 v3 模型必须使用 ratio=1.5 + T=2.150 + 动态阈值 + USE_RULE_ENGINE=False 配置
2. 自动重训触发器必须配置三重机制（定时/数据/周期）+ 性能门禁（走水召回率≥0.30，预测率偏差≤0.02）
3. 对手调整Lag特征必须通过 shift(1) + rolling(window) 防泄露机制验证
4. trigger_state.json 必须持久化记录触发器运行状态
5. 文档同步必须按 P0→P1→P2 优先级顺序执行
6. 跨文档性能指标必须在 prompt_template.md/CONVERSATION_WORKFLOW_GUIDE.md/PROJECT_DELIVERY_REPORT.md/model_optimization_plan.md 间保持一致

---

---

## 七、v4 时代新增经验教训（2026-08-23 补充）

### 7.1 过度优化修复经验

1. **联赛分档阈值过拟合（多重比较问题）**: 5联赛×6网格点=30次实验选最优，本质是"噪声的最大值"而非信号。全局最优 0.90 仅 +0.09pp（噪声区间），联赛分档的 +1pp 增益下赛季很可能消失。**正确做法**：嵌套CV（外层时间分割+内层网格搜索），或加开关搁置、用新赛季前瞻性验证。

2. **赔率特征精简方法论**: 135维赔率特征（64.6%）中大量 lag/rolling/标准差衍生特征在拟合噪声。A/B测试（5折时间序列CV）显示：精简到30维核心特征后，准确率仅 -0.48pp，但平局召回率 +1.55pp，特征构建速度 1.6x，训练速度 1.8x。**精简收益 > 成本**，核心保留：WDL/HCP 隐含概率 + 凯利指数 + 赔率变化率 + 市场置信度。

3. **pandas frame.insert 碎片化是特征构建性能瓶颈**: 逐个 `frame.insert` 每次触发 DataFrame 内存重分配，52列球员特征产生大量 PerformanceWarning，是特征构建耗时的核心瓶颈。**修复**：改为 `pd.concat` 批量拼接，精简特征构建 318s→210s（省 108s）。

4. **Monte Carlo 3000→500 零风险**: 统计精度损失 <0.3%，比分预测提速 6 倍。**对8×8 Dixon-Coles 修正后的 Poisson 矩阵，Monte Carlo 仅用于验证，解析矩阵已是精确概率分布**。

5. **三重校准叠加（Platt + T=0.8 + 决策阈值）扭曲概率**: 只保留 Platt Scaling 一层，温度 T 回退 1.0，ECE 校准改善。

6. **伪集成（固定权重 Stacking）**: 6模型中 3个 Poisson 变体共享同一 λ，输出高度相关。砍到 4模型（DC + XGB + LGB + Elo），减少冗余推理。

7. **英超独立模型过度拟合**: 1141 样本训 205 维（样本/特征比 5.6:1），CV 47.25% 低于全局 53.99%。从 Stacking 移除，改为独立参考输出。

### 7.2 v4 时代新增硬性规则

1. 特征构建必须使用 `pd.concat` 批量拼接，禁止逐个 `frame.insert`（避免 DataFrame 碎片化）
2. 赔率特征默认使用精简版（30维）而非全量（135维）；全量版仅用于 A/B 对比
3. 联赛分档阈值默认关闭（argmax 模式），保留开关供前瞻性验证
4. 温度缩放默认 T=1.0（等价 argmax），不使用手工 T 值
5. Monte Carlo 模拟次数默认 500，不使用 3000+
6. Stacking 基础模型为 5 个（DC + Elo + XGB + LGB + 贝叶斯），由 LR meta-learner 融合（`apply_stacking_meta_learner`，缺任一模型回退固定权重），英超独立模型不参与集成
7. SofaScore 数据写入 sofascore_team_features 表前必须调用 normalize_team_name 归一化队名（C-20260823-024）

---

## 八、报告完整性门禁时代经验（2026-08-31 补充）

### 8.1 玩法缺位 vs 数据缺失的区分（核心规则）

1. **竞彩「未开胜平负正盘」是玩法缺位，不是数据缺失**: 深盘强队（如皇马让两球半、巴萨）竞彩常只开让球/大小球/比分。判定标准：`wdl_history` 无记录但 `handicap_history`/`total_goals_history`/`score_history` 有记录 → `wdl_not_offered=True`。判定必须用 `find_any_sporttery_match_id()` 四表探针（含 ±3 天日期容差，防误命中历史赛季同名对阵）。
2. **三层文案口径必须一致**: ①单场时序章节「ℹ️ 竞彩未开售胜平负盘（仅让球/大小球/比分），上表为 500.com 初盘参考（非时序缺失）」；②单场诊断表「未开售WDL(500兜底)」；③汇总告警说明列「竞彩未开售胜平负盘（非缺失）」。真缺漂移则分别是「⚠️ 仅 1 条竞彩 WDL 快照需回踩」「仅1快照(缺漂移)」「仅1条快照缺漂移」。
3. **邮件 [缺数据] 红标与调度后置校验只对「真缺数据」触发**: 判定模式为 `仅1条快照缺漂移|无竞彩WDL时序`，不得使用「数据完整性告警」标题判定（否则纯 not_offered 场的当天也会误红标，误导运营做无效回踩）。

### 8.2 「仅1条快照」的两种成因与处置

1. **采集循环未回踩**: live_collector 2 小时循环会在赛前自动补第 2 条快照（实测 08-30 21:19~22:16 三场自愈）；跨天后仍只有 1 条才需手动 `--no-skip-existing` 回踩。
2. **源站赔率零变动**: 竞彩 oddsHistory 自开盘后从未更新（如奥萨苏纳vs赫塔费 08-29 09:36 后无第 2 条），任何回踩都无效，报告如实标注「仅1条快照缺漂移」即可，不是漏采。处置前先用 API 直连探针（getMatchListV1 + getFixedBonusV1 比对 hadList 条数与库内快照数）确认是否与源站同步，避免盲目回踩。

### 8.3 竞彩销售日与实际比赛日错位

竞彩 `businessDate`（销售日归属）常比 matches 表实际开球日早一天（凌晨场归前一日销售日）。报告定位竞彩 match_id 依赖 ±3 天日期容差可正常对齐；统计「当日竞彩场次」时须先确认口径（销售日 vs 开球日），避免误判漏采。

### 8.4 变更日志统计表防脱节

change_log.md §5 统计表曾长期停留在 136 而实际记录已达 554（增量会话只追加记录区、不更新统计区）。追加 C-记录时同步核对 §5.1/§5.2，或定期用脚本按记录行重建（解析 `^\| C-\d{8}-\d{3} \| 时间 \| 类型 \|` 行按列统计）。

## 九、实盘小注验证时代（2026-09-06 补充）

### 9.1 预注册协议（TRIAL-001 ~ TRIAL-005，详见 docs/live_trial_away_favorite_protocol.md）

1. **规则冻结不可改**（TRIAL-001）: 客胜赔率≤2.5 / EV>0（EV=p_away×o_away−1，model_predictions WDL_away 原始概率）/ 平注 ¥20 / 累计 300 注停止 / 未来 7 天窗口 / match_id_en 去重。禁止事后修改规则（防多重比较污染）。
2. **每日节奏**（TRIAL-002）: ①`sporttery_live_collector.py`（采集当日竞彩，无 cookie 可直连 webapi.sporttery.cn）→ ②`generate_unified_report.py --date <当日>`（回写 model_predictions）→ ③`live_trial_away_favorite.py --commit`（dry-run 先看，候选符合才提交）→ 次日 `--settle` 结算（odds500_match status=5 实际比分回填 W/L）。
3. **三源桥接 key**（TRIAL-003）: 竞彩 `{date}_{normalize(home_cn)}_{normalize(away_cn)}`（±1 天日期容差，竞彩官方日 vs 当地日错位）+ 模型 `{date}_{home_en}_{away_en}`（保留空格原样）。竞彩凌晨场归前一销售日、500.com 记当地日，精确 key 仅匹配 8/14 场，±1 天容差后 14/14。
4. **概率口径**（TRIAL-004）: EV 用模型原始 WDL_away 概率，**不叠加 TempScaling**（已知系统性高估 edge → 投注量系统性偏少，保守方向，属预注册接受项）。
5. **评估准则**（TRIAL-005）: 300 注满后 ROI + z 检验 + 分段时间；**以净盈亏为准绳**（Jensen 效应下 z 检验高估显著性，C-20260905-004 教训）。

### 9.2 实盘验证经验（EXP-016）

1. **竞彩采集器无需 cookies**（EXP-016）: sporttery_live_collector.py 直连 webapi.sporttery.cn 公开接口（getMatchListV1 + getFixedBonusV1），HEADERS 无 cookie 可正常采集当日五大联赛开售场次，无需用户提供 Edge cookies（区别于历史 collectors）。
2. **首日 0 笔是规则正常运作**（EXP-017）: 14 场可桥接中 8 场有预测，3 场赔率>2.5、5 场 EV≤0 → 0 笔通过。「宁可少投不可滥投」；300 注需数周累积，不是每日都有。
3. **预测管线只覆盖 odds500_match 清单**（EXP-018）: generate_unified_report 按 odds500_match（status=1）跑，竞彩有开售但 500.com 清单缺失的凌晨场（日期错位）需跑对应日期才生成预测；当日 12/17 场有预测（特征数据不全的跳过）。

---

## 十、主客反转排查时代（2026-09-19 补充，C-20260919-018）

### 10.1 教训（EXP-019 主客系统性反转三根因）

1. **Fallback 模板向量是方向污染高危点**: 未赛场走 fallback 时若以「df 最后一行」为模板仅覆盖部分特征，残留的赔率/Elo/概率会整体携带模板场方向；模板恰好是强客场即全批次反转。判别法——对照实验三向量：全零（模型先验）/仅已知特征/纯模板，三者中模板组方向与异常一致即坐实。正确做法：批量预测前显式注入待赛场虚拟行（`prime_fixtures`），让未赛场与已赛场走同一套特征构建，命中缓存必须 AND 精确主客对（OR 逻辑会误中无关场次）。
2. **Elo 计算输入必须先去重再算**: matches 表同场比赛英文行+中文行（34 对）归一化收敛同键，直接遍历会双重计数（失利方被双罚）。快照构建固定流程：队名归一化 → (日期,主,客) 去重 → 计算 → 经 TEAM_ALIASES 展开为英文键+别名键（prediction_core 用英文 match 字段查询，快照主键必须英文）。
3. **INSERT OR IGNORE 与「重跑修正」语义冲突**: 报告文件被覆盖但 DB 残留首次预测，造成报告与库不一致。需要覆盖的预测表一律用 ON CONFLICT … DO UPDATE 全字段 UPSERT。

### 10.2 口径备忘

- `feature_utils.load_match_data_odds(dedup=True)`：归一化后按 日期/主/客 去重，优先保留有 actual_score 的行；预测链默认走 dedup。
- WDL 与让球头允许在平手均势场（市场 H/A 接近）存在方向分歧，属头间正常差异；硬矛盾判定只针对非平手盘（如 -0.5 强主场 WDL 却客胜）。
- 修复后基线（2026-09-19 窗口 20 场）：WDL 14 主/6 客/0 平；子模型对明确热门与市场一致。

### 10.3 防复发加固（C-20260919-019）

1. **三个入口全覆盖 prime**：generate_unified_report / predict_today_14 / backfill_missing_prematch_reports；新增批量入口必须同样先 prime，否则等同把 C-018 隐患请回来。
2. **Fallback 固定中性口径**：零向量 + 本场 sofa 覆盖；禁止任何「拿他场行作模板」写法。
3. **方向哨兵口径**：n≥8 且单侧 ≥85% → 告警（两处入口已覆盖）。
4. **Elo 快照每日自动重建**：`deploy_t005v2_final.py --elo-only`，已挂 run_prematch_2030.ps1 步骤 0c；快照为嵌套结构（顶层 5 键，内部 415 球队键），LGB Elo 特征不走此文件。
5. **PS1 含中文必须存 UTF-8 BOM**：无 BOM 时 Windows PowerShell 按 GBK 误读会报解析错（run_prematch_2030 已修）。
6. **同场多键归零**：无英文客名时管线用混合键（考文垂/桑坦德/埃沃斯堡），旧英文键污染行已删；日后遇重复键先归一化归并再删孤儿。
7. **子模型概率入 sub_probs 必须是 Python float**（C-20260919-020）：XGBoost Booster.predict 给 numpy.float32，渲染端 isinstance(v,(int,float)) 会误判缺失（显示 —/4/5）；LGB 为 float64 不受影响。教训：模型输出接入展示字典前统一 float()，且"显示缺失"≠"没参与融合"。
8. **复盘让球结算盘口线口径**（C-20260919-021）：matches.handicap 缺失率 39%，赛后管线须走 resolve_handicap_line（matches 数值 → odds500_match.handicap 同 match_id → 日期+归一化中文队名），兜底线回写 matches；写已存在复盘行只补 NULL 事实列。深盘串（三球/三球半=3.25、三球半=3.5、三球半/四球=3.75）已补入 HCP_STR_MAP。
9. **XGB 显示「—」的可观测性与三套口径纪律**（C-20260919-022）：「—」有假性缺失（XGB Booster 输出 numpy.float32 非 float 子类→渲染失败，但实际已入融合；float() 根治，C-020）与真性缺失（pkl/scaler/特征两级取数/推理异常）之分；XGB 缺失三层哨兵（推理 error 日志/报告内联🚨/批次汇总）只告警不改概率。**EV 推荐方向可与 WDL 概率方向相反**（P冷门×高赔率>1 的小仓位价值提示，非改判），报告两处自动 ℹ️ 注释。铁律：模型输出/EV 规则/展示标签三套口径严格分离，禁止为"看起来一致"而覆盖任何一方。
10. **【工作模式·2026-09-19 暂停→2026-09-21 恢复盘核】**：2026-09-19 用户曾要求暂停复盘，09-21 用户重新指示恢复复盘并修复让球/Understat 遗留问题（C-042~044）。复盘待办现状：①✅98 场缺 actual_hcp 已批量修复 89 场（C-044，248/267 有结果，19 场 matches.handicap=NULL 不在竞彩开售范围无法补）；②✅ Understat xG 匹配率 10/30→30/30（C-044 ±1 天时区偏移兼容）；③✅ sporttery_collector 赛前盘口存储修复（C-043）+ 2026-2027 赛季补采 228 场（C-044）；④⏳ Top5 比分改读 T-006（仍待办）；⑤✅ 历史赛季（2022-2026）NULL handicap 已批量补采 4145 行（C-20260922-046：Phase A 23/24 本地 raw 合并 1220 行 + Phase B 赛程-only 采集 24/25+25/26 共 100 月度分页 + Phase C 合并 2925 行），2022-2026 赛季 NULL 5885→1269，剩余为竞彩未开售场次（数据源天花板）；backfill_handicap_from_schedule.py 工具可复用于未来补采；⑥✅ Understat API 可用性已实测确认（C-20260921-045）：getLeagueData/getMatchData 在带 X-Requested-With 头时返回 200，原「404 网站改版」结论证伪，采集器无需重构。
11. **子模型计数必须以 STACKING_META_MODELS 五键为准**（C-20260919-023）：sub_probs 运行时含 7 个概率源（五基模 + poisson/ssm 辅助参考源，后两者不参与融合、不进报告子模型表），任何"n/5"计数（model_used 标题、XGB 哨兵）直接 `len(sub_probs)` 会显示 7/5。教训：计数口径以架构常量为准，不要数整个工作字典。
12. **特征管线修复协议**（C-20260920-025）：①发现 serving 异常先量化（同 holdout、同 pkl 的 Full/Zero 对照）再动手；②指标"越修越差"往往是跨口径污染（本次：serving 喂正确概率、模型训练在错误表示上），修复顺序固定为 修转换 → 重建矩阵 → 重训 → 再验证，禁止只修单边；③排查用逐层追踪（原始表→raw→latest→normalize→extract）定位错误首次出现环节；④树模型对非线性尺度鲁棒（旧错误表示仍 RPS 0.197），但走水检测依赖特征语义，数学错误直接致 AUC 0.52；⑤批量重生成必须覆盖 status 1/2/5（merged_discover + 强制 has_prediction=False + 全量重写 _summary）。
13. **多路对齐与特征架空识别**（C-20260920-026）：①多路对齐（桥表+match_id_en+中→英）可能多对一，所有下游 merge/reindex 入口必须按目标键去重（本次丢 24 条），否则 pandas 索引赋值会 shape mismatch broadcast 报错；修复长期静默失效的增强器时，先核对 SQL 引用列真实存在（本次 sofascore 查询引用了不存在的 s.match_id_en）；②判断某组特征是否真正生效，用同一其余特征做 OLD/NEW/ZERO 三口径严格对照——若 ZERO 与另两者同水平，说明该组被其他特征"架空"，数学/语义修复仍必须做（口径正确性），但不得宣称指标增益，要让信号真正生效须从特征结构层面另立任务；③独立管线长期失修时，优先收敛到训练脚本提供的 serving 入口（predict_for_matches），禁止在管线内复制特征逻辑。
14. **跨模块接口契约：取数前必须核实返回键真实存在**（C-20260920-027）：消费端 `x.get('某键')` 静默返回 None、下游再以 .get 兜底时，功能会"成功地空跑"且零报错——本次 WDL→比分重加权（C-20260823-019 引入）因 WDLPredictor 返回字典从未提供 `probabilities` 键，自上线起完全失效近一个月。规则：①新增跨模块数据传递，消费端对必需键做断言/哨兵（缺失即告警，禁止静默 None 兜底）；②判断"某功能是否生效"必须实跑核对产物（比分表成分、数值列），不能只凭代码里存在调用；③产出端与展示端键名口径必须按同一契约对齐（本次 TG 产出 `"7"`、展示查 `"7+"` 恒 0.0%；标题 Top10 实际只给 5 行），行数/标题以实际列表为准。
15. **T-006 v5 接入冻结口径 + Top5 定档**（C-20260920-028）：报告比分榜**定档 Top5**（C-027 的 Top10 一天后按用户指示缩减，A/D 两修复保留）。v5 接入方案四项决策已冻结，编码不得擅自走样：①节奏=Shadow 并行后切换（离线复测→双算只对比不落库→切默认，v4 留一个版本周期，降级链 v5→v4→赔率隐含→数据不足）；②WDL 锚定**只认 §一 Stacking 生产概率**，比分轨禁止自行去水另造一套 WDL；③比分赔率信号保留（融合后**嵌套一轮 IPF** 修回边缘），MC 弃用；④recency 首期不做、不建快照，total 先验只取 TG 赔率隐含 E[g]。v5 路径绕过 A-002、λ_alert 继续挂 v5 的 λ；联赛 ρ 映射（E 项）接入时补写（原型未实现）。
16. **v5 离线复测与 Shadow 工程模式**（C-20260920-029）：①**单份算法原则**：serving 层 `predict_score_v5` 落在 v5 模块内，离线复测脚本只能做薄封装复用它——复测结果与生产 shadow 才天然一致（本次重构后 72 场零偏差）；禁止复测脚本另写一份算法。②**Shadow 双算铁律**：新模型 shadow 必须在生产结果产出后调用、全程 try/except 隔离，只写独立产物（`reports/t006_shadow_v5.jsonl`），不展示、不落库、不改任何既有字段；开关默认开且可环境变量关闭（`TRAE_T006_SHADOW=0`）。③**失败也要记录**：shadow 的 v5 error 行（TG 缺失等）是真实覆盖率监控数据，不丢弃，但赛后评测脚本必须按 error/success 区分。④**复测解读纪律**：n=72 的小样本多数分层差异仅 1–3 场，必须结合分层方向（v5 赢高频低球、输冷门高球）与根因诊断（市场先验保守 vs 管线 bug）综合判断，禁止凭总体单点差异下"更优/更差"结论。
17. **Train/Serve 同源的完整含义：同代码、同参数、同分布**（C-20260920-030）：①"公式同源"不止是思路一致——共享常量（`LEAGUE_RHO` 单一定义、训练脚本导入）、共享代码路径（OOF 与 serving 走同一条 λ→DC 链路），只共享思路过几个版本必漂移。②元模型特征必须覆盖其推理时会遇到的全部输入分布：OOF 的 DC 若只在联赛均值 λ（输出 35~55%）上训练，serving 的 80%+ 极端 DC 就是 OOD 外推；修复后 OOF 含 4500 场赔率 DC（含极端值），LR 学会在极端 DC 上自动降权（DC 80.4% 时融合主胜 49.4%→44.5%）。③无法消除的口径缺口用**标志特征**而非掩盖：2024-25 赛季无赔率采集 → 回退联赛均值 λ + `dc_odds_fallback=1` 作为第 16 维 meta 特征（serving 恒 0），让模型自己区分两分布；禁止静默兜底。④验收口径纪律：holdout（近期有赔率场）RPS 改善 0.0019、OOF 全量微劣 0.0005 并存时，以贴近生产分布的 holdout 为准；且先验证"极端输入行为受控"再谈指标。⑤红线：禁止改写基模输出（收缩/截断/硬阈值），要条件化处理就把条件量（分歧度、兜底标志）作为特征送入。

---

**文档版本**: v1.53
**创建时间**: 2026-07-23
**最后更新**: 2026-09-27（CI 红灯根因闭环 run#10 六矩阵全绿，C-20260927-005；新增 EXP-047）
**更新频率**: 规则或经验变更时更新
**维护人**: 模型优化团队
**更新说明**:
- **v1.53 (2026-09-27)**: CI 红灯根因排查闭环（C-20260927-005）：GitHub Actions 自 8/12 起 #3~#8 连续 failure 的两层根因——①workflow pip 清单漏装 requests/beautifulsoup4（final_500_collector 顶层 import，dd9f43b 只补 requests 漏掉 bs4）；②team_name_mapping.py 的 `Optional[str]` 注解未 import，Py3.14 PEP 649 延迟求值掩盖、3.11/3.12 NameError（8 用例）。修复：pip 补 beautifulsoup4、strategy 改 fail-fast:false、补 typing import（84277f7+3c7d67f），**run#10 六矩阵（ubuntu/windows×3.11/3.12/3.14）全 success**。新增 EXP-047：本机新版 Python 全绿 ≠ CI 旧版本全绿，typing 注解必须查 import（AST 扫描 602 文件仅 1 处），CI 保留最低版本矩阵+fail-fast:false，Actions 日志重定向须剥离认证头。
- **v1.52 (2026-09-27)**: ①tests/ 目录审计（第 9 次同型）结论归档《五大联赛全栈框架设计.md》新增 §9.18「测试与 CI 体系」、§13.4 风险表新增 2 行（C-20260927-003）：293 用例本机 281 过/12 跳/0 失败（365.69s），但 GitHub Actions 可取 4 次运行全红（run#6 唯一真实失败 ubuntu/Py3.12，日志 403 根因未取证）、pre-commit hook 未装、Node 6 脚本脱管（writer 测试 4 失败 schema 漂移）、npm test 空壳——自动门禁名存实亡登记为中-高风险。②change_log.md 文件头四重 BOM 归一为单 BOM（C-20260927-004），新增 EXP-046：编辑工具会给带 BOM 文件重复写 BOM，带 BOM 文档每次编辑后必须复核字节头，BOM 计数用循环逐 3 字节而非抽样。
- **v1.51 (2026-09-26)**: 风险 M 修复（C-20260926-091）：logs/ 日志轮转与自动清理。①`final_sofascore_collector.setup_logging` 改固定文件 `sofascore_collector.log` + `RotatingFileHandler(maxBytes=5MB, backupCount=3)`（磁盘上限约 20MB，import logging.handlers）；②新增 `purge_old_collector_artifacts()`——每次启动自动清理：旧时间戳 log 保留最近 3 个、summary JSON 保留最近 10 个，try/except 全隔离；③`odds_data_spec.purge_old_training_logs()`——`TrainingLogger.__init__` 自动执行，training JSON mtime>30 天删除但最近 20 个保底（retrain_trigger_runner 只取最新，与风险O 兼容）；④清理模式不匹配 `sofascore_progress_*.json`（resume 依赖）与轮转 `.log.N` 文件，实测零误伤。结果：首次触发删除旧 log 524/旧 summary 144/超期 training 12；logs/ 由 1028 文件/77.81MB 降至 **349 文件/14.09MB（-82%）**，此后自动维护、无需计划任务。经验教训——日志清理优先「启动时挂钩 + 保留最近 N」而非依赖计划任务；glob 模式设计须用真实文件清单验证排除项（progress/轮转文件）；删除前做模式安全预览是防止误删 resume 状态的必要步骤。遗留：understat/xgscore/sporttery/pipeline 时间戳 log（约 10MB）待推广同一模式。
- **v1.50 (2026-09-26)**: 风险 R 修复（C-20260926-090）：matches 26/27 match_id 统一为 SofaScore 全名口径。深度诊断推翻原登记方向（统一中文）与 DATA-014 的「预测轨简名」描述：matches 实际主流（25/26 全量、26/27 1940/1983）与采集轨 ps mid 完全一致（SofaScore 全名）；232 个 CN 行全部来自 populate（C-086 误用归一化中文名生成 match_id），另有 23 组 odds500 简名行 vs SofaScore 全名行双行。迁移（backup/migrate_risk_r.py，两轮，迁移前备份 backup/odds_backup_riskR_20260926.db）：233 组重复组保留 SofaScore 口径行（有 ps 优先），字段 merge 351 项、删 238 行；21 场 CN 独有改名；model_predictions 迁移 83 mid（UNIQUE 冲突保 timestamp=MIN/is_replay=MIN，符 C-053）、post_match_review 迁移 33 mid；补插 52 场 ps 孤儿（数据源 odds500_match，handicap 不写——matches CHECK 仅允许 sporttery 竞彩口径）。结果：matches 26/27 共 2012 场、CN 残留 0、重复 0、ps 孤儿 65→15（余 15 场 odds500 无记录：6 场历史漏场+9 场未来场）。根源修复：populate 的 match_id 改用 fbref_match_mapping.odds_match_id 原值，重跑 inserted=0 幂等。新增 DATA-015 规则；新登记风险 S（odds500_match 144 + odds500_stat 5 个 CN mid）。经验教训——跨源一致性应以采集器/ps 的实际写入口径为准，不能凭「归一化」直觉选方向；修复前必须先诊断清「谁是多数派、谁与健康历史一致」；matches.handicap 有 CHECK 约束仅允许 sporttery 源。
- **v1.49 (2026-09-26)**: 风险 N 修复（C-20260926-089）：采集器 `mark_done` 仅对已结束比赛（status=finished/ended）标记，未开赛比赛不写库不标记，下次自动重试。删除 26/27 进度文件重采后，player_stats 覆盖率从 26.1% 提升至 40.6%（法甲因 Akamai 403 未采完）。新发现风险 R：26/27 赛季 matches.match_id 语言不一致（中文 232/英文 231），与采集器 SofaScore 英文名 match_id 不匹配，致 match_player_stats 孤儿率 23.9%（25/26 及之前仅 0~0.3%）。需统一 match_id 为归一化中文名。
- **v1.48 (2026-09-26)**: logs/ 目录专项审计（C-20260926-088），登记风险 M-Q。M（高，待修复）：logs/ 1156 文件/80.33MB，.log 占 85%，全项目无 RotatingFileHandler/maxBytes/backupCount，sofascore_collector 每次运行生成 ~1MB .log 无清理；N（中，待评估）：collector 对 partial（部分接口失败）仍 mark_done，该场永不重试可能致永久数据缺口；O/P/Q（低，接受）。日志无轮转是运维债务，长期会挤压同盘 DB 空间。
- **v1.47 (2026-09-26)**: import_data 风险 K 续修复（C-20260925-087）：`populate_matches_from_fbref.py` 幂等性与一致性增强。①改用 `team_name_mapping.normalize_team_name` 归一化队名生成 match_id 与 home/away_team（fbref home_team_cn 实际存英文名，原直接用会与 matches 现有中文 match_id 跨源不一致）；②双键去重——match_id 相同 OR (date, 归一主队, 归一客队) 相同均跳过，杜绝同场重复插入；③fallback 对 `YY/YY` 格式补全为 `20YY-20YY+1`（原生成 `西甲22-23赛季` 非标准）；④批量修复历史 3012 条非标准 match_type。验证：重跑 inserted=0（幂等）、matches 无重复比赛、match_type 全标准。经验教训——数据导入脚本的去重键不能仅依赖生成的 ID（ID 可能因来源不同格式不一致），必须用业务主键（date+队名）做去重；fallback 逻辑要覆盖所有输入格式变体，不能假设输入格式统一。
- **v1.46 (2026-09-26)**: import_data 审计风险 J/K/L 登记到框架文档 §4.1。风险 K 修复（C-20260925-086）：`populate_matches_from_fbref.py` 的 `LEAGUE_SEASON_MAP` 补充 26/27 赛季五联赛映射（原仅到 25/26，重跑会走 fallback 生成非标准 match_type）。风险 L（sofascore_backfill 无条件覆盖）评估为低风险接受——字段均从 stats_json 派生，stats_json 是唯一数据源，脚本已做 `if sofa_key in stats` 防御。至此 import_data 审计 4 项风险（J 已修复 C-085、K 已修复 C-086、L 接受）全部闭环。
- **v1.45 (2026-09-25)**: import_data/ 目录审计 + 修复（C-20260925-085）。①`cleanup_matches.py` 匹配键修复：原用 `match_id NOT IN (odds_match_id)`，因 `matches.match_id`（中文队名）与 `fbref_match_mapping.odds_match_id`（英文队名）仅 35.3% 匹配，会误归档 64.7%。改用 `team_name_mapping.normalize_team_name` 归一双方队名后以 `(match_date, 归一主队, 归一客队)` 匹配（含主客对调兜底），匹配率 98.4%，仅 1.6% 待归档；默认 `dry-run` 仅预览，`--apply` 才执行。②僵尸脚本 `import_bundesliga.py`/`import_ligue1.py`（25-26 一次性 TXT 导入，odds_timing.db 空、源 TXT 已删）移入 `scripts/archive/`。框架文档 §3.8 原结论④「match_id 无漂移」修正为已修复。经验教训——文档「已排除」类结论必须用真实数据交集验证，不能仅凭命名同构推断；跨表 JOIN 键若命名相同但来源不同（中文 vs 英文），交集率才是判据；一次性数据脚本默认 dry-run 是防止误删的最后一道防线。
- **v1.44 (2026-09-25)**: 风险 H 修复（C-20260925-083）：三个编排器 D3 步骤从全量 `sofascore_pre_match_features.py`（~88min，超时 15/25min 必杀）改为增量 `incremental_sofascore_features.py`（幂等 INSERT OR REPLACE，仅补缺场次，秒~分钟级），全量重建改为手动触发。风险 I 修复（C-20260925-084）：①新增 `features/__init__.py` 使 features 为正规包；②incremental 脚本裸导入统一为 `from features.x import`；③`_cn_fbref_map` 由实例级提升为类级单例缓存（`predict_unified` 每场新建实例，原每场全表扫描 fbref_match_mapping）。features 目录专项审计 5 项风险（E/F/G/H/I）全部修复完成。经验教训——编排器的「默认路径」必须与超时预算匹配：全量脚本若超时报错，应默认走增量而不是靠人工规避；只读映射表（赛季内不变）的缓存应提为类级/模块级单例，避免每请求重建。
- **v1.43 (2026-09-25)**: 风险 F 修复（C-20260925-082）：天气气候估算不乘 λ。关键事实：`calc_weather_factor` 的 `is_estimate` 恒 True、`CONFIG_PATH` 从未使用，输出为「联赛×月份」气候均值——同月同联赛所有场乘数相同，是赛季性常量非逐场信号，且赔率已隐含市场天气预期。方案：新增 `_load_weather_config` 读 `match_conditions.weather.apply_climate_to_lambda`（默认 false），is_estimate 时 attack/defence_impact 强制 1.0，气候数据仅用于报告展示；JS 参考轨 `calcWeatherImpact` 兼容 Python schema 与扁平 temperature 演示 schema。经验教训——「定义了配置常量却从未读取」是死代码的明确信号；当一个因子对同组所有样本取相同值（季节常量）时，它不是特征而是偏置，且该偏置已被赔率吸收，应默认不参与 λ 调整，仅在拿到逐场实时数据时才启用。
- **v1.42 (2026-09-25)**: 风险 G 修复（C-20260925-081）：伤病信号双通道去重，λ 双路分流——关键架构事实：T-006 v4 比分网格用 wdl_probs 做重要性重加权后**边际强制等于 WDL 概率**，故 Score λ 乘伤病因子与 pa_*（含官方缺阵名单，已入 WDL）构成重复计数；而 TG（85% Poisson(λ)+15% 大小球赔率）无 WDL/pa_* 通道，λ 伤病乘数是其唯一伤病信号。方案：Score λ 默认仅乘天气（天气不在 WDL/pa_*），TG λ 保留全量（伤病×核心×天气）；config 门禁 `match_conditions.score_injury_adjust`（env TRAE_MC_SCORE_INJURY 覆盖）+ shadow 双算写 `_shadow_score_injury`。经验教训：判断「重复特征」不能只看输入列表，必须沿输出通道核实融合算子——边际重加权会覆盖 λ 的边际效应，同一信号对不同输出（Score vs TG）的冗余性结论可以相反。
- **v1.41 (2026-09-25)**: 风险 E 修复（C-20260925-080）：`calc_injury_factor` 核心球员池由全历史聚合改为赛前近 `n_recent` 场窗口（出场率=窗口出场/窗口场次，阈值 0.6），消除离队名宿（Messi/Piqué/Busquets 等）被识别为核心并误扣 λ 的问题；经验教训——「核心球员」类滚动身份特征必须带时间窗口与活跃判定，分母用窗口场次而非队史最大出场数。
- **v1.40 (2026-09-25)**: features/ 目录专项审计（C-20260925-079）新增风险 E-I：E 伤病核心池全历史（高，已由 v1.41 修复）、F 天气恒气候均值且 JS 轨 schema 不匹配、G 伤病信号 pa_* 特征与 λ 乘数双通道重复计数、H D3 全量重建 88 分钟 vs 编排超时 15/25 分钟、I 包结构/导入风格/映射缓存。
- **v1.39 (2026-09-24)**: 框架设计风险 B/C 治理（C-20260924-078）：①风险 B——`sofascore_pre_match_features.save_to_db` 由 `DROP TABLE`+`CREATE` 改为 `CREATE TABLE IF NOT EXISTS`+`ALTER TABLE ADD COLUMN` 对齐列+`INSERT OR REPLACE`，不再清空增量脚本写入的行；②风险 C——PA 特征缺失哨兵统一为 -1.0（生成端 fillna、增量 `_clean`/默认值、服务 fallback 初始化四处对齐），sofa_ 特征 0.0 保留为真实值；存量表迁移 74 主/94 客行全-0 PA→-1.0，合法 0 值保留。
- **v1.38 (2026-09-24)**: match_condition 死特征接入预测链路（C-20260924-077）：`calc_injury_factor` SQL 修复（`team_name`→`team` + JOIN `fbref_match_mapping` 取 `match_date`，最近一场改 `MAX(match_date)` 取全场球员）+ `_cn_to_fbref_team` 队名映射（直查 fbref 英文 → 中文归一查缓存映射）；`prediction_core.predict_unified` 在 λ 计算后注入 `injury×keyPlayer×weather_atk×weather_def` 因子，对齐 JS `calcLambdaMatch` 语义，`try/except` 全隔离降级为 1.0；框架文档 §4.1 风险 A/D 已修复、B（全量/增量写冲突）/C（缺失值 0.0 vs -1.0 双口径）待治理。
- **v1.37 (2026-09-20)**: §6.3.4 新增第 7/8 条——cookie 新鲜度先按 sdc_session 时间戳核验；EdgeOne 首批约 20~23 次真实请求后按 TLS 会话抽签发 987B 挑战页，重建会话重抽即可放行（C-031）。每日采集 19 场，预测版赛前数据口径。
- **v1.36 (2026-09-20)**: §10.3 新增第 17 条——train/serve 同源完整含义（同代码/同参数/同分布、标志特征替代静默兜底；C-20260920-030）；meta-learner 重训（16 维），holdout RPS 0.2043→0.2024。
- **v1.35 (2026-09-20)**: §10.3 新增第 16 条——v5 离线复测与 Shadow 工程模式（C-20260920-029）；reports/t006_v5_offline_retest.json + t006_shadow_v5.jsonl 建立。
- **v1.34 (2026-09-20)**: §10.3 新增第 15 条——Top5 定档 + v5 接入冻结口径（C-20260920-028）；0919 全天 18 场重生成+_summary+UPSERT。
- **v1.33 (2026-09-20)**: §10.3 新增第 14 条跨模块接口契约（取数前核实返回键、必需键哨兵、产出/展示键名对齐；C-20260920-027）；0919 全天 18 场报告重生成+_summary+UPSERT。
- **v1.32 (2026-09-20)**: FEAT-014 更新——T-004 同模式修复完成（C-20260920-026）；§10.3 新增第 13 条（多路对齐多对一去重 + 特征架空识别）；assets/t004_tg_lgb_model.pkl 首次创建。
- **v1.31 (2026-09-20)**: 新增 FEAT-014（赔率转概率先取倒数、train/serve 特征契约、四通道对齐），FEAT-010 升四通道；§10.3 新增第 12 条特征管线修复协议；T-005 v3 重训上线（C-20260920-025），T-004 同模式问题挂起待决策。
- **v1.30 (2026-09-20)**: §10.3 新增第 11 条——n/5 计数以 STACKING_META_MODELS 五键为准（C-20260919-023）。
- **v1.29 (2026-09-19)**: §10.3 新增第 10 条——用户指示暂停一切赛后复盘动作，主线转为预测模型优化与预测问题治理。
- **v1.28 (2026-09-19)**: §10.3 新增第 9 条——XGB「—」真假性缺失判别、三层哨兵、三套口径分离铁律（C-20260919-022）。
- **v1.27 (2026-09-19)**: §10.3 新增第 8 条——复盘让球结算盘口线三级解析与幂等补洞（C-20260919-021）。
- **v1.26 (2026-09-19)**: §10.3 新增第 7 条——模型输出接入展示字典前统一 float()，显示缺失≠未参与融合（C-20260919-020）。
- **v1.25 (2026-09-19)**: 新增 §10.3 防复发加固六条（C-20260919-019）；全栈框架设计 §6/§10.3/§13.4 同步。
- **v1.24 (2026-09-19)**: 新增第十章「主客反转排查时代」：①EXP-019 三根因——fallback 模板向量污染（对照实验判别法→prime_fixtures 虚拟行+AND 精确命中）、Elo 双轨重复双重计数（归一化去重→TEAM_ALIASES 英文键展开重建，169 队/415 键，热刺 1448.92→1478.31）、INSERT OR IGNORE 致重跑不更新（改 ON CONFLICT DO UPDATE UPSERT）；②口径备忘——预测链 dedup 默认、平手均势场头间分歧不算硬矛盾；③窗口 20 场重生成后 WDL 14 主/6 客/0 平，子模型对明确热门与市场一致，报告+DB 均已更新（C-20260919-018）。
- **v1.23 (2026-09-12)**: ①§7 当前模型最新状态更新至 2026-09-12：500.com 采集器 requests→curl_cffi（impersonate="chrome"）+ EdgeOne 三层 Cookie（含与 UA 绑定的 EO-Bot-Captcha-Token，每日人工 Edge 导出）恢复 500 数据可用，4 场赛前报告完整度 100%（C-20260912-001，详见《故障排查报告_数据采集_20260912》（已删除 C-20260918-049））；②新增 §6.3.4 EdgeOne 反爬与亚盘刷新运维 6 条（TLS 指纹本质、Cookie 凭证、风控节奏、--refresh-odds 用法、完整度口径、rating 覆盖率教训）；③亚盘 refresh_upcoming_odds 修复「先入库后完赛」结算盘丢失 bug，26/27 五联赛 113/1752 有盘口、74 场完赛 100% 结算（C-20260912-003）；④统一报告第十二章接入 24 维 pa_* 代理指标、第十四章新增积分榜/战意（compute_league_standings/classify_zhan_yi）+ 数据不完整保护（C-20260912-002）；⑤数据源表实测刷新（odds.db 1,669MB、500 match 19,790 行、sofascore 特征 18,363 行/98 字段、player_stats 730,128 行、matches 26/27 共 184 行/54 完赛）；⑥§6.4 新增 3 条已知限制（伤病代理指标、积分榜依赖赛果时效、未开盘亚盘）。
- **v1.22 (2026-09-11)**: ①§7 当前模型最新状态同步至 2026-09-11：最新训练 20260908_004604（254 维，slim+ts+consensus+球员 lag 全量特征集）、训练↔服务端 CI 强制对齐 + feature_bridge 桥接（C-20260910-004/011）、统一引擎 DixonColes 全量投产（C-20260909-008）、P1-B 贝叶斯增量 shadow（τ=0.01 逐联赛、意甲 SIGNIFICANT-WIN p=0.0484、生产仍 serve control）；②新增 §6.3.3 自动化调度与生产运维状态（5 项计划任务表 + SYSTEM 账户/电池供电运维注意）；③数据源表更新（odds.db 1,666MB/14,521 场 league 空值 0、26/27 已 152 场、Sporttery 10 季全完成）；④新增 EXP-041（league 列漏写/match_type 兜底派生根因，C-20260911-023）与 EXP-042（shadow 配对 McNemar 口径，C-20260911-022）。P0~P2 状态以《模型优化评估报告_v2.0》为唯一权威（**已删除 C-20260918-049**，替代位置见 五大联赛全栈框架设计.md §十二，C-20260911-020）。
- **v1.21 (2026-09-06)**: 新增第九章「实盘小注验证时代」：①预注册协议五条规则（TRIAL-001~005）——客胜热门段（away≤2.5）+ EV>0 + ¥20 平注 + 300 注停止，规则冻结防多重比较污染；②每日节奏三步骤（采竞彩→跑预测→生成投注单/结算）；③三源桥接 key 与 ±1 天日期容差（竞彩官方日 vs 当地日错位，14/14 场全匹配）；④概率口径声明（原始 WDL_away 不叠加 TempScaling，系统性高估属预注册接受项）；⑤评估准则以净盈亏为准绳（Jensen 效应）；⑥经验 EXP-016~018（竞彩采集器无 cookie 直连、首日 0 笔是规则正常运作、预测管线只覆盖 odds500 清单）。配套 C-20260906-001 + docs/live_trial_away_favorite_protocol.md。
- **v1.20 (2026-09-04)**: 新增 §1.5 CALIB-009（C-20260904-002 edge 分桶单调回归修复）：①Mono-Pooled(on Temp) 单一单调保序使分桶恢复单调递增（0~3pp -13.4%→3~6pp -4.9%→6~10pp -3.1%→>10pp -1.5%，唯一单调方案）但**全桶仍负、ROI 未转正**，保序只能做「排序修复」不能做「水平修复」；②选择条件化 winner's curse 修正（Mono-Selected）证伪——整体 -6.28% 反而更差、分桶非单调、>10pp 桶高估升至 +17.5pp；③整体最优仍 Mono-Pooled(on raw) -3.65%、Mono-OVR 平召 0.92% 证实逐类保序坍缩（pooled 保平局召回是必要设计）。→ 概率层（校准/保序/择场/选择修正）四连证伪，**系统性高估根治唯一剩路 = 训练端 EV/ROI 目标改造**。
- **v1.18 (2026-09-03)**: 新增 §1.5「概率校准与 EV 决策规则 CALIB-001~007」：①严格时序 OOF 11965 场 × TimeSeriesSplit(5) 折内 fit/折外 transform 的校准对比流程（C-20260903-006）；②Vector/Isotonic 对 Platt 后概率叠加会坍缩平局召回（<2.5%），禁止单用于 WDL 校准；③当前首选 TempScaling(on raw, NLL 最优)：T≈0.896~0.975 逐折递减，平局召回 27.16%（≥0.28 达标）、平注 ROI -3.71%（较基线 +1.70pp 最优）；④校准本身不能使 EV ROI 转正（仍 -3.71%），后续必须叠加 EV 择场 + 训练端 EV 目标 + edge 分桶修复三方面（CALIB-007）。EV ROI 负根因闭环：§3.105 分赛季拆解排除「老赛季赔率质量」、§3.106 校准选型确认「校准仅能压缩高估幅度、无法完全消除」。
- **v1.17 (2026-09-01)**: EV 期望值引擎（决策层）落地：新建 scripts/ev_engine.py（4 dataclass + 8 核心函数 + 17 单测全通过），对接 generate_unified_report.py 第六章、prediction_db_writer.py 行式落库 7 类 EV_* prediction_type（INSERT OR IGNORE 幂等），实现「预测→决策→落库→回测」闭环，预测引擎与投注决策引擎分离（C-20260901-003~006）；特征维度口径复核：生产链 165(slim_odds)→198(+ts_odds)→208(+consensus) 核实无误，当前 feature_utils 实际 211→244→254（T-007 球员特征 52→98 维、+46 死特征，预测管线子集对齐至 208 无错配，24 个非 sofa 列 = pa_* 球员可用性特征）；T-007 球员扩展死特征消融（208 vs 208+8）：三折指标改善≈0.75% 噪声范围内无统计显著，维持 208 维不纳入（C-20260901-002）。
- **v1.16 (2026-08-31)**: 新增第八章「报告完整性门禁时代经验」：not_offered 玩法缺位四表探针判定（C-20260831-001/002）、邮件红标与调度后置校验判定收窄至真缺数据（C-20260831-003/004）、「仅1条快照」两种成因（自愈/源站零变动，C-20260831-005）、竞彩销售日与实际开球日错位提醒、change_log §5 统计表重建（136→554 条防脱节）。
- **v1.15 (2026-08-28)**: P1-10 情境化特征降维复查闭环（逐维 LoO + 分组 ablation + 2-fold A/B，`scripts/_tmp_p110_shap_ablation.py`）：14 维无有价值子集，逐维 RPS 边际 ≤|0.0003|（噪声内）、分组边际 ≤|0.0002|、无子集优于基线（full RPS 0.2018≈基线 0.2017），维持 `ctx_features=False` 不启用（C-20260828-022）；新增 FEAT-012。
- **v1.14 (2026-08-28)**: P1-11 多博彩公司赔率一致性特征生产启用（`consensus_odds=True`，10 维，模型 198→208 维，修复 RPS 列序 bug 后 A/B 四指标全优）；P1-11 降维复查闭环（逐维 LoO + 分组 ablation：无子集优于全量，维持全量 10 维，C-20260828-021）；P1-10 情境化特征 14 维 A/B RPS/Acc 无增益，暂不启用（`ctx_features=False`）；新增 FEAT-011。
- **v1.13 (2026-08-28)**: P1-8/P1-9 收尾落地并同步文档：P1-9 ts_odds 完整校准复验四指标全优（Blend RPS -0.0155），生产 `ts_odds=True`（WDL 特征 165→198 维）+ PA 导入路径修复（C-011）；P1-8 xg_deep 覆盖率 88.5% 达标但 RPS 轻微劣化，保持 `xg_deep=False` 暂不采用；`docs/SYSTEM_ANALYSIS_REPORT_v2.0.md` 同步至 v3.0（C-20260828-015，WDL 架构更新为 198 维 + 5 基础模型 Stacking）。
- **v1.12 (2026-08-28)**: P1-7 Stacking 完成 LR meta-learner（§6.1 WDL 架构更新为 5基础模型+meta-learner；§7.2 硬性规则第6条更新）：`train_stacking_meta.py` 生成 `stacking_meta_learner.json`（多分类 LR），`prediction_core.apply_stacking_meta_learner` 融合，OOF RPS 0.1984 < 固定 0.2038。
- **v1.11 (2026-08-27 23:31)**: Sporttery 时序赔率 16/17~25/26 已结束赛季全部完成（20/21 1,208、21/22 930、22/23 1,001 场入库，合计 handicap 13,125 / wdl 12,575 / total 13,126 / score 13,122）。
- **v1.10 (2026-08-27 21:10)**: Sporttery 时序赔率赛季覆盖更新至 16/17~19/20 + 23/24~25/26 共 7 季（19/20 835 场入库），剩余 20/21~22/23 共 3 季。
- **v1.9 (2026-08-27 20:50)**: Sporttery 时序赔率赛季覆盖更新至 16/17~18/19 + 23/24~25/26 共 6 季（17/18 1,800 场、18/19 1,551 场入库），剩余 19/20~22/23 共 4 季。
- **v1.8 (2026-08-27 16:30)**: 数据源表新增 Sporttery 时序赔率赛季覆盖；采集器 sporttery_collector.py `SEASON_RANGES` 扩展至 16/17~25/26 共 10 季（对标 Understat/SofaScore）。
- **v1.7 (2026-08-26 20:30)**: 数据源表 500.com 更新为 18,038 场（16/17~20/21 回采完成，仅剩 16/17 法甲 380 缺口）。
- **v1.6 (2026-08-26 20:00)**: 数据源表 500.com 更新为 9,288 场（23/24 补齐，阶段一 21/22~25/26 全量完成）；采集器新增 `--skip-existing` 断点续采 + 请求重试。
- **v1.5 (2026-08-26 19:30)**: 数据源表 500.com 更新为 7,536 场（21/22、22/23 回采完成，各 1,826）。
- **v1.4 (2026-08-26 17:30)**: 数据源表 500.com 更新为 4,644 场（SEASONS 16/17~25/26 10 季）；新增 §6.3.2 文档自动更新机制硬性规则。
- **v1.3 (2026-08-23 19:00)**: T-006 v4 模型架构新增 WDL 概率重加权说明；v4 硬性规则新增第7条（SofaScore 队名归一化）；模型状态更新至最新。
- **v1.2 (2026-08-23)**: 新增第七章 v4 时代经验教训，包含过度优化修复 7 条经验 + 6 条新增硬性规则。记录依据架构诊断报告完成的 6 项优化改动。
- **v1.1 (2026-08-12)**: 新增第六章 v3 时代经验教训，包含 T-005 让球预测、自动重训触发器、文档同步三方面经验，以及6条 v3 时代新增硬性规则。
- **v1.0 (2026-07-23)**: 初始版本，包含5大类硬性规则、工程约定、经验教训。
