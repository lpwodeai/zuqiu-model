"""
SofaScore 赛前数据实时抓取器
使用 SofaScoreClient 抓取指定 event_id 的赛前完整数据
"""
import json
import os
import sys
import logging
from datetime import datetime, timezone, timedelta

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
PROJECT_DIR = os.path.dirname(SCRIPT_DIR)  # 五大联赛专属模型/五大联赛专属模型
COLLECTION_DIR = os.path.join(PROJECT_DIR, 'collection')
sys.path.insert(0, COLLECTION_DIR)

RAW_DIR = os.path.join(PROJECT_DIR, "data", "sofascore_raw", "pre_match")
os.makedirs(RAW_DIR, exist_ok=True)

CN_TZ = timezone(timedelta(hours=8))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-7s | %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("pre_match")

try:
    from final_sofascore_collector import SofaScoreClient
    HAS_CLIENT = True
except ImportError:
    HAS_CLIENT = False
    logger.error("无法导入 SofaScoreClient，请确认 final_sofascore_collector.py 在 collection/ 目录下")

MATCHES = [
    {
        "event_id": "16421047",
        "home": "Deportivo Alavés",
        "away": "Getafe",
        "label": "阿拉维斯 vs 赫塔费",
    },
    {
        "event_id": "16421052",
        "home": "Sevilla",
        "away": "Rayo Vallecano",
        "label": "塞维利亚 vs 巴列卡诺",
    },
]


def fetch_event_data(client, event_id, label):
    """抓取单场比赛的完整数据"""
    logger.info(f"{'='*60}")
    logger.info(f"抓取比赛: {label} (event_id={event_id})")
    logger.info(f"{'='*60}")

    # 1. Event 基础信息
    logger.info("1. 获取 event 基础信息...")
    event_data = client.get(f"/event/{event_id}", tag="event")
    if not event_data:
        logger.error("❌ event 数据获取失败")
        return None

    e = event_data.get("event", event_data)
    status = e.get("status", {}).get("type", "")
    status_desc = e.get("status", {}).get("description", "")
    logger.info(f"   状态: {status} ({status_desc})")

    # 检查是否为赛前
    is_pre_match = status in ("notstarted", "scheduled")
    if is_pre_match:
        logger.info(f"   ✅ 确认为赛前数据 (status={status})")
    else:
        logger.warning(f"   ⚠️  比赛已非赛前状态: {status}")
        if status in ("finished", "live", "halftime"):
            logger.warning(f"   ❌ 这是赛后/进行中数据！")

    # 2. Lineups 阵容
    logger.info("2. 获取 lineups 阵容数据...")
    lineups_data = client.get(f"/event/{event_id}/lineups", tag="lineups")
    if lineups_data:
        confirmed = lineups_data.get("confirmed", False)
        home_players = len(lineups_data.get("home", {}).get("players", []))
        away_players = len(lineups_data.get("away", {}).get("players", []))
        missing = len(lineups_data.get("home", {}).get("missingPlayers", [])) + \
                  len(lineups_data.get("away", {}).get("missingPlayers", []))
        logger.info(f"   阵容确认: {'✅ 已确认' if confirmed else '⏳ 预测版'}")
        logger.info(f"   主队球员: {home_players}人, 客队球员: {away_players}人")
        logger.info(f"   伤病球员: {missing}人")
    else:
        logger.warning("   ⚠️  lineups 数据获取失败")

    # 3. Statistics 统计
    logger.info("3. 获取 statistics 统计数据...")
    stats_data = client.get(f"/event/{event_id}/statistics", tag="statistics")
    if stats_data:
        periods = [p.get("period", "") for p in stats_data.get("statistics", [])]
        logger.info(f"   可用时段: {periods}")
        if not periods:
            logger.warning("   ⚠️  无统计数据（赛前正常）")
    else:
        logger.warning("   ⚠️  statistics 数据获取失败（赛前可能无数据）")

    # 4. Incidents 事件流
    logger.info("4. 获取 incidents 事件流...")
    incidents_data = client.get(f"/event/{event_id}/incidents", tag="incidents")
    if incidents_data:
        incidents = incidents_data.get("incidents", [])
        logger.info(f"   事件数: {len(incidents)}")
        if len(incidents) == 0 and is_pre_match:
            logger.info(f"   ✅ 赛前无事件流，符合预期")
    else:
        logger.warning("   ⚠️  incidents 数据获取失败")

    # 5. 额外尝试: pre-match 特有 API
    logger.info("5. 尝试获取赛前预览数据...")
    pre_match_data = client.get(f"/event/{event_id}/pre-match", tag="pre-match")
    if pre_match_data:
        logger.info(f"   ✅ 获取到 pre-match 数据: {list(pre_match_data.keys())[:10]}")
    else:
        logger.info(f"   ℹ️  无 pre-match 端点（可能不支持）")

    # 6. 保存数据
    save_dir = os.path.join(RAW_DIR, event_id)
    os.makedirs(save_dir, exist_ok=True)

    files_saved = []
    for name, data in [
        ("event.json", event_data),
        ("lineups.json", lineups_data),
        ("statistics.json", stats_data),
        ("incidents.json", incidents_data),
        ("pre_match.json", pre_match_data),
    ]:
        if data is not None:
            filepath = os.path.join(save_dir, name)
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            files_saved.append(name)
            logger.info(f"   💾 保存: {name}")

    logger.info(f"\n📊 抓取完成: {label}")
    logger.info(f"   保存目录: {save_dir}")
    logger.info(f"   保存文件: {files_saved}")
    logger.info(f"   比赛状态: {status}")
    logger.info(f"   是否赛前: {'✅ 是' if is_pre_match else '❌ 否'}")

    return {
        "event_id": event_id,
        "label": label,
        "status": status,
        "is_pre_match": is_pre_match,
        "confirmed": lineups_data.get("confirmed", False) if lineups_data else False,
        "files": files_saved,
        "save_dir": save_dir,
    }


def main():
    if not HAS_CLIENT:
        logger.error("SofaScoreClient 不可用，无法运行")
        sys.exit(1)

    logger.info("=" * 70)
    logger.info("SofaScore 赛前数据实时抓取器启动")
    logger.info("=" * 70)

    client = SofaScoreClient(logger)

    results = []
    for m in MATCHES:
        result = fetch_event_data(client, m["event_id"], m["label"])
        if result:
            results.append(result)
        print()

    client.close()

    # 汇总
    logger.info("=" * 70)
    logger.info("抓取汇总")
    logger.info("=" * 70)
    for r in results:
        logger.info(f"  {r['label']}:")
        logger.info(f"    状态: {r['status']} | 赛前: {'✅' if r['is_pre_match'] else '❌'}")
        logger.info(f"    阵容: {'已确认' if r['confirmed'] else '预测版'}")
        logger.info(f"    文件: {r['files']}")

    logger.info("\n✅ 抓取完成！")


if __name__ == "__main__":
    main()
