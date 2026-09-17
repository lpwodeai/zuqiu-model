"""快速验证 SofaScore 3 场赛事的连接性与主客场信息。"""
import sys, json, logging
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR / "collection"))
from final_sofascore_collector import SofaScoreClient, fetch_event_detail

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("crawl_test")

MATCHES = [
    ("16416293", "西甲 real-sociedad-real-betis"),
    ("16363633", "英超 arsenal-coventry-city"),
    ("16310922", "法甲 rc-strasbourg-marseille"),
]

client = SofaScoreClient(logger)

for eid, label in MATCHES:
    logger.info(f"=== {label} ({eid}) ===")
    detail = fetch_event_detail(client, eid, logger)
    if not detail:
        logger.error(f"  FAILED: {label}")
        continue
    ev = detail.get("event", {})
    home = ev.get("homeTeam", {}).get("name", "?")
    away = ev.get("awayTeam", {}).get("name", "?")
    home_short = ev.get("homeTeam", {}).get("shortName", "?")
    away_short = ev.get("awayTeam", {}).get("shortName", "?")
    ts = ev.get("startTimestamp")
    status = (ev.get("status") or {}).get("description", "?")
    from datetime import datetime, timezone, timedelta
    cn = datetime.fromtimestamp(ts, tz=timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M") if ts else "?"
    print(f"  HOME={home} ({home_short})  AWAY={away} ({away_short})")
    print(f"  start={cn}  status={status}")

client.close()
print("DONE")