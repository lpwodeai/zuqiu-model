"""拉取两场西甲第1轮赛前数据 (event_id: 16421061, 16421053)"""
import sys, json, logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "collection"))
from final_sofascore_collector import SofaScoreClient, fetch_event_detail, fetch_event_lineups, parse_lineups

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logger = logging.getLogger("pre_match")

MATCHES = [
    {"event_id": "16421061", "label": "Racing Club vs Villarreal"},
    {"event_id": "16421053", "label": "Espanol vs Levante"},
]

client = SofaScoreClient(logger=logger)

results = {}
for m in MATCHES:
    eid = m["event_id"]
    logger.info(f"=== {m['label']} ({eid}) ===")

    detail = fetch_event_detail(client, eid, logger)
    if not detail:
        logger.error(f"FAILED: detail for {eid}")
        continue

    event = detail.get("event", {})
    home = event.get("homeTeam", {})
    away = event.get("awayTeam", {})
    status = event.get("status", {})

    lineups_data = fetch_event_lineups(client, eid, logger)
    lineups = parse_lineups(lineups_data or {}, eid, logger) if lineups_data else {}

    result = {
        "event_id": eid,
        "label": m["label"],
        "home_name": home.get("name", "?"),
        "home_short": home.get("shortName", "?"),
        "away_name": away.get("name", "?"),
        "away_short": away.get("shortName", "?"),
        "status": status.get("description", "?"),
        "start_time": event.get("startTimestamp", "?"),
        "confirmed": lineups.get("confirmed", False),
        "home_formation": lineups.get("home", {}).get("formation", "?"),
        "away_formation": lineups.get("away", {}).get("formation", "?"),
        "raw_event": event,
    }

    print(f"  主队: {result['home_name']} ({result['home_short']})")
    print(f"  客队: {result['away_name']} ({result['away_short']})")
    print(f"  状态: {result['status']}")
    print(f"  阵容确认: {result['confirmed']}, 主阵型: {result['home_formation']}, 客阵型: {result['away_formation']}")
    print()

    results[eid] = result

# Save for later use
out_path = BASE_DIR / "data" / "pre_match_laliga_r1_2.json"
with open(out_path, "w", encoding="utf-8") as f:
    json.dump({k: {kk: vv for kk, vv in v.items() if kk != "raw_event"} for k, v in results.items()}, f, ensure_ascii=False, indent=2)
print(f"Saved to {out_path}")