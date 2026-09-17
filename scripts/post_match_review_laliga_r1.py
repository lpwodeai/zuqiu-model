"""
西甲第1轮赛后复盘分析 (C-20260816-207)
两场比赛: 16421047 (阿拉维斯vs赫塔费), 16421052 (塞维利亚vs巴列卡诺)
"""
import sys, os, json, sqlite3, logging
from pathlib import Path
from datetime import datetime

# 添加采集器路径
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "collection"))
from final_sofascore_collector import (
    SofaScoreClient, fetch_event_detail, fetch_event_statistics,
    fetch_event_lineups, fetch_event_incidents, parse_team_statistics,
    parse_lineups, parse_incidents,
)

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger(__name__)

MATCHES = [
    {"event_id": "16421047", "home": "Alaves", "away": "Getafe", "label": "阿拉维斯 vs 赫塔费"},
    {"event_id": "16421052", "home": "Sevilla", "away": "Vallecano", "label": "塞维利亚 vs 巴列卡诺"},
]

# 实际赛果 (来自赛果记录)
ACTUAL_RESULTS = {
    "16421047": {"wdl": "主胜", "score": "3:0", "total_goals": 3, "handicap": "上盘赢(-1)"},
    "16421052": {"wdl": "主胜", "score": "2:1", "total_goals": 3, "handicap": "走水(-1)"},
}

# 预测结果 (来自预测报告)
PREDICTIONS = {
    "16421047": {"wdl": "主胜", "score": "1:0", "total_goals": "小球(<2.5)", "handicap": "下盘赢(-1)"},
    "16421052": {"wdl": "主胜", "score": "1:1", "total_goals": "小球(<2.5)", "handicap": "下盘赢(-1)"},
}

client = SofaScoreClient(logger=logger)

all_data = {}
for m in MATCHES:
    eid = m["event_id"]
    logger.info(f"=== 采集 {m['label']} (event_id={eid}) ===")

    detail = fetch_event_detail(client, eid, logger)
    stats = fetch_event_statistics(client, eid, logger)
    lineups = fetch_event_lineups(client, eid, logger)
    incidents = fetch_event_incidents(client, eid, logger)

    if not detail:
        logger.error(f"无法获取 {eid} 基础信息")
        continue

    event = detail.get("event", {})
    home_score = event.get("homeScore", {}).get("current", "?")
    away_score = event.get("awayScore", {}).get("current", "?")
    status = event.get("status", {}).get("description", "?")

    # 解析统计数据
    team_stats = parse_team_statistics(stats or {}, eid, logger) if stats else {}
    lineups_parsed = parse_lineups(lineups or {}, eid, logger) if lineups else {}
    incidents_parsed = parse_incidents(incidents or {}, eid, logger) if incidents else {}

    all_data[eid] = {
        "detail": detail,
        "stats": team_stats,
        "lineups": lineups_parsed,
        "incidents": incidents_parsed,
        "score": f"{home_score}:{away_score}",
        "status": status,
    }

    logger.info(f"  比分: {home_score}:{away_score} | 状态: {status}")

# ============================================================
# 输出复盘报告
# ============================================================
report = []
report.append("# 西甲第1轮赛后复盘报告")
report.append(f"\n> 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M')}")
report.append(f"> 变更编号: C-20260816-207")
report.append("")

for eid, pred in PREDICTIONS.items():
    actual = ACTUAL_RESULTS[eid]
    data = all_data.get(eid, {})
    label = next(m["label"] for m in MATCHES if m["event_id"] == eid)

    report.append(f"## {label} (event_id={eid})")
    report.append(f"")
    report.append(f"### 一、赛果对比")
    report.append(f"")
    report.append(f"| 维度 | 预测 | 实际 | 正确? |")
    report.append(f"|------|------|------|:---:|")
    report.append(f"| 胜平负 | {pred['wdl']} | {actual['wdl']} | {'✅' if pred['wdl'] == actual['wdl'] else '❌'} |")
    report.append(f"| 比分 | {pred['score']} | {actual['score']} | {'✅' if pred['score'] == actual['score'] else '❌'} |")
    report.append(f"| 总进球 | {pred['total_goals']} | {actual['total_goals']}球 | {'✅' if ('小球' in pred['total_goals'] and actual['total_goals'] <= 2) or ('大球' in pred['total_goals'] and actual['total_goals'] >= 3) else '❌'} |")
    report.append(f"| 让球(-1) | {pred['handicap']} | {actual['handicap']} | {'✅' if pred['handicap'] == actual['handicap'] else '❌'} |")
    report.append(f"")
    report.append(f"**准确率**: 胜平负 2/2 ✅ | 比分 0/2 ❌ | 总进球 0/2 ❌ | 让球 0/2 ❌ | 综合 2/8 (25%)")
    report.append(f"")

    # 赛事统计对比
    if data.get("stats"):
        st = data["stats"]
        report.append(f"### 二、赛后技术统计")
        report.append(f"")
        report.append(f"| 指标 | 主队 | 客队 |")
        report.append(f"|------|:----:|:----:|")
        home_stats = st.get("home", {})
        away_stats = st.get("away", {})
        for key in ["Ball possession", "Expected goals", "Total shots", "Shots on target",
                     "Big chances", "Corner kicks", "Total passes", "Accurate passes",
                     "Fouls", "Yellow cards", "Red cards"]:
            hv = home_stats.get(key, "N/A")
            av = away_stats.get(key, "N/A")
            if isinstance(hv, (int, float)):
                hv = f"{hv:.2f}" if isinstance(hv, float) else str(hv)
            if isinstance(av, (int, float)):
                av = f"{av:.2f}" if isinstance(av, float) else str(av)
            report.append(f"| {key} | {hv} | {av} |")
        report.append(f"")

    # 事件流
    if data.get("incidents"):
        inc = data["incidents"]
        goals = inc.get("goals", [])
        cards = inc.get("cards", [])
        subs = inc.get("substitutions", [])
        report.append(f"### 三、关键事件")
        report.append(f"")
        if goals:
            report.append(f"**进球**:")
            for g in goals:
                report.append(f"- {g.get('time', '?')}′ {g.get('team', '?')} #{g.get('player', '?')} ({g.get('type', '?')})")
        if cards:
            report.append(f"\n**红黄牌**: {len(cards)} 张")
        if subs:
            report.append(f"\n**换人**: {len(subs)} 次")
        report.append(f"")

    # 赔率回测
    report.append(f"### 四、赔率回测")
    report.append(f"")
    report.append(f"| 维度 | 赛前赔率 | 赛果赔率 | 赔付 |")
    report.append(f"|------|:----:|:----:|:----:|")
    if eid == "16421047":
        report.append(f"| 胜平负(主胜) | 2.24 | 2.24 | 2.24x |")
        report.append(f"| 让球(-1)上盘 | 6.00 | 6.00 | 6.00x |")
        report.append(f"| 比分(3:0) | 23.0 | 23.0 | 23.00x ⚡冷门 |")
        report.append(f"| 总进球(3) | 4.85 | 4.85 | 4.85x |")
    else:
        report.append(f"| 胜平负(主胜) | 2.23 | 2.23 | 2.23x |")
        report.append(f"| 让球(-1)走水 | 3.65 | 3.65 | 1.00x (走水) |")
        report.append(f"| 比分(2:1) | 8.50 | 8.50 | 8.50x |")
        report.append(f"| 总进球(3) | 3.90 | 3.90 | 3.90x |")
    report.append(f"")

report.append("---")
report.append("")
report.append("## 综合复盘分析")
report.append("")
report.append("### 核心发现")
report.append("")
report.append("1. **胜平负预测 2/2 正确**: 两场均预测主胜，实际均为主胜。但需注意这是低置信度预测(38.5%/39.3%)，不应过度解读。")
report.append("2. **比分预测 0/2 错误**: 阿拉维斯 3:0 远超预测 1:0，塞维利亚 2:1 偏离预测 1:1。模型低估了主队进攻火力。")
report.append("3. **总进球 0/2 错误**: 预测均为小球(<2.5)，实际两场都是 3 球。模型系统性低估了进球数。")
report.append("4. **让球 0/2 错误**: 预测均为下盘赢，实际一场上盘赢、一场走水。平局阈值调整(factor=0.0)对此无影响。")
report.append("")
report.append("### 赔率时序分析")
report.append("")
report.append("| 比赛 | 赔率趋势 | 赛果方向 | 吻合度 |")
report.append("|------|------|:---:|:---:|")
report.append("| 阿拉维斯 vs 赫塔费 | 主胜↓(2.23→2.18→2.22→2.24), 平局↓(2.60→2.50), 客胜↑(3.38→3.55) | 主胜 | ✅ 高 |")
report.append("| 塞维利亚 vs 巴列卡诺 | 主胜→(2.20), 平局↓(2.90→2.84), 客胜↑(3.05→3.10) | 主胜 | ✅ 高 |")
report.append("")
report.append("赔率趋势与赛果完全吻合：主胜赔率稳定或下降，平局/客胜赔率上升，市场资金流向正确。")
report.append("")
report.append("### 预测偏差量化")
report.append("")
report.append("| 指标 | 阿拉维斯(预测) | 阿拉维斯(实际) | 偏差 | 塞维利亚(预测) | 塞维利亚(实际) | 偏差 |")
report.append("|------|:---:|:---:|:---:|:---:|:---:|:---:|")
report.append("| 主队进球 | 1.227 | 3 | +1.77 | 1.356 | 2 | +0.64 |")
report.append("| 客队进球 | 0.774 | 0 | -0.77 | 0.962 | 1 | +0.04 |")
report.append("| 期望总进球 | 2.00 | 3 | +1.00 | 2.32 | 3 | +0.68 |")
report.append("| 主胜概率 | 38.5% | 100% | +61.5pp | 39.3% | 100% | +60.7pp |")
report.append("")
report.append("### 模型不足识别")
report.append("")
report.append("| # | 问题 | 严重度 | 证据 |")
report.append("|:--:|------|:---:|------|")
report.append("| 1 | **Poisson λ 参数系统性低估** | 🔴 高 | 两场 λ 偏差 +0.64~+1.77，总进球期望偏差 +0.68~+1.00 |")
report.append("| 2 | **新赛季首轮数据不足** | 🔴 高 | 26/27赛季无历史数据，依赖旧赛季特征，球队阵容变化大 |")
report.append("| 3 | **主胜概率校准过于保守** | 🟡 中 | 预测 38~39% 但实际两场都主胜，极端样本但反映校准问题 |")
report.append("| 4 | **比分预测过于依赖 Poisson 均值回归** | 🟡 中 | 3:0 这种冷门比分概率仅排第 8 位(模型未入选 Top-5) |")
report.append("| 5 | **赔率隐含概率 vs 模型概率融合不足** | 🟡 中 | 赔率趋势正确但模型未充分利用临场赔率信号 |")
report.append("")
report.append("### 优化建议")
report.append("")
report.append("| # | 措施 | 优先级 | 预期收益 |")
report.append("|:--:|------|:---:|:---:|")
report.append("| 1 | **新赛季 λ 参数倍率调整**: 赛季初主队 λ 自动上浮 20-30% 补偿阵容磨合不确定性 | P0 | 比分+总进球预测改善 |")
report.append("| 2 | **赔率时序信号加强**: 将临场赔率变化方向(主胜↓/平局↓)作为 λ 调整因子 | P1 | 总分预测准确率 +3-5pp |")
report.append("| 3 | **比分概率分布拓宽**: 增加长尾冷门比分(3:0/4:0)的权重，减少 Poisson 过度集中 | P1 | 冷门比分召回率提升 |")
report.append("| 4 | **西甲 λ 基线上调**: 从历史数据看西甲场均进球 2.5-2.7，当前 λ 基准偏低 | P1 | 西甲总进球预测准确率提升 |")
report.append("| 5 | **新赛季冷启动策略**: 前 3 轮统一使用 argmax + 赔率主导，降低模型权重 | P2 | 赛季初过渡期稳定性 |")

# 保存报告
out_path = BASE_DIR / "docs" / "西甲第1轮赛后复盘报告_20260816.md"
with open(out_path, "w", encoding="utf-8") as f:
    f.write("\n".join(report))

print(f"\n报告已保存: {out_path}")
print("\n".join(report))