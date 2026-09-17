"""
快速抓取两场西甲赛前数据
使用现有 final_sofascore_collector.py 的 SofaScoreClient
"""
import json, logging, sys, os

# 设置路径
COLLECTION_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "collection")
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, COLLECTION_DIR)

os.chdir(PROJECT_DIR)

from final_sofascore_collector import SofaScoreClient

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s %(message)s')
logger = logging.getLogger("pre_match")

MATCHES = [
    ("16421047", "Alaves", "Getafe", "2026-08-16 01:30"),
    ("16421052", "Sevilla", "Vallecano", "2026-08-16 03:30"),
]

client = SofaScoreClient(logger)

def fetch_event(event_id):
    result = {}
    logger.info(f"=== 抓取 event_id={event_id} ===")
    
    # 1. 基础信息
    event = client.get(f"/event/{event_id}", tag="event")
    if event:
        result['event'] = event
        ht = event.get('homeTeam',{})
        at = event.get('awayTeam',{})
        st = event.get('status',{})
        logger.info(f"  OK: {ht.get('name','')} vs {at.get('name','')}")
        logger.info(f"    status: code={st.get('code','')} started={st.get('startedAt','')} finished={st.get('finishedAt','')}")
        logger.info(f"    startTimestamp: {event.get('startTimestamp','')}")
        logger.info(f"    venue: {(event.get('venue') or {}).get('name','')}")
        logger.info(f"    round: {event.get('roundInfo',{}).get('round','')}")
    else:
        logger.warning("  基础信息获取失败")
    
    # 2. 统计数据
    stats = client.get(f"/event/{event_id}/statistics", tag="statistics")
    if stats:
        result['statistics'] = stats
        groups = list(stats.get('statistics',{}).keys()) if isinstance(stats.get('statistics'), dict) else []
        logger.info(f"  统计数据 OK: groups={groups}")
    else:
        logger.warning("  统计数据获取失败")
    
    # 3. 阵容
    lineups = client.get(f"/event/{event_id}/lineups", tag="lineups")
    if lineups:
        result['lineups'] = lineups
        confirmed = lineups.get('confirmed', False)
        logger.info(f"  阵容数据 OK: confirmed={confirmed}")
    else:
        logger.warning("  阵容数据获取失败")
    
    # 4. 事件流
    incidents = client.get(f"/event/{event_id}/incidents", tag="incidents")
    if incidents:
        result['incidents'] = incidents
        cnt = len(incidents.get('incidents', []))
        logger.info(f"  事件流 OK: {cnt} 条")
    else:
        logger.warning("  事件流获取失败")
    
    return result

# 抓取
all_data = {}
for eid, home, away, time_str in MATCHES:
    logger.info(f"\n{'='*60}")
    logger.info(f"抓取: {home} vs {away} ({time_str})")
    logger.info(f"{'='*60}")
    
    data = fetch_event(eid)
    all_data[eid] = data
    
    out_dir = os.path.join(PROJECT_DIR, "五大联赛专属模型", "五大联赛专属模型", "data", "sofascore_raw", "pre_match", eid)
    os.makedirs(out_dir, exist_ok=True)
    
    for key, value in data.items():
        fpath = os.path.join(out_dir, f"{key}.json")
        with open(fpath, 'w', encoding='utf-8') as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
        logger.info(f"  已保存: {fpath}")

# 汇总
print(f"\n{'='*60}")
print("抓取完成汇总:")
print(f"{'='*60}")
for eid, data in all_data.items():
    keys = list(data.keys())
    event_info = data.get('event', {})
    ht = event_info.get('homeTeam', {}).get('name', '?')
    at = event_info.get('awayTeam', {}).get('name', '?')
    status = event_info.get('status', {})
    print(f"\n  {ht} vs {at} (event_id={eid})")
    print(f"    状态: code={status.get('code','?')} started={status.get('startedAt','?')} finished={status.get('finishedAt','?')}")
    for k in keys:
        v = data[k]
        if k == 'event':
            print(f"    {k}: ✓ ({len(json.dumps(v))} bytes)")
        elif k == 'statistics':
            groups = list(v.get('statistics',{}).keys()) if isinstance(v.get('statistics'), dict) else 'N/A'
            print(f"    {k}: ✓ groups={groups}")
        elif k == 'lineups':
            confirmed = v.get('confirmed', False)
            print(f"    {k}: ✓ confirmed={confirmed}")
        elif k == 'incidents':
            cnt = len(v.get('incidents', []))
            print(f"    {k}: ✓ {cnt} 条")