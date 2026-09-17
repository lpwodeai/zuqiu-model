#!/usr/bin/env python3
"""
三场比赛赛前数据一体化流程（贝蒂斯vs皇家社会 / 阿森纳vs考文垂 / 马赛vs斯特拉斯）
====================================================================
1. 抓取 SofaScore 赛前数据（detail/lineups/statistics/incidents）→ 原始 JSON + odds.db
2. 解析赔率时序 TXT（支持 "联赛名  主队VS客队" 与 "联赛名  主队  客队" 两种格式）→ odds_timing.db
3. 生成赛前数据 JSON（供 generate_unified_report 使用）
4. 调用 prediction_core 统一预测：胜平负 / 让球 / 比分 / 总进球

用法：python scripts/run_prematch_3matches.py [--skip-crawl]
"""
from __future__ import annotations

import json
import os
import re
import sys
import sqlite3
import logging
import traceback
from pathlib import Path
from datetime import datetime, timezone, timedelta

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
sys.path.insert(0, str(BASE_DIR / "collection"))
sys.path.insert(0, str(BASE_DIR / "scripts"))

from final_sofascore_collector import (  # noqa: E402
    SofaScoreClient,
    init_db_schema_if_needed,
    collect_single_event,
    fetch_event_detail,
)
import data_store  # noqa: E402
from prediction_core import init_models, PredictionCore  # noqa: E402
from prediction_db_writer import batch_save_predictions, serializable_to_pred  # noqa: E402

CN_TZ = timezone(timedelta(hours=8))

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("prematch_3")

# ============================================================
# 三场比赛配置（主客场以 SofaScore 抓取为准，已核实与赔率 TXT 一致）
# ============================================================
MATCHES = [
    {
        "event_id": "16416293",
        "league": "西甲",
        "league_code": "LaLiga",
        "season": "26/27",
        "txt": DATA_DIR / "西甲2026-2027赛季完整时序赔率.txt",
        "home_en": "Real Betis",
        "away_en": "Real Sociedad",
        "home_cn": "贝蒂斯",
        "away_cn": "皇家社会",
    },
    {
        "event_id": "16363633",
        "league": "英超",
        "league_code": "EPL",
        "season": "26/27",
        "txt": DATA_DIR / "英超2026-2027赛季完整时序赔率.txt",
        "home_en": "Arsenal",
        "away_en": "Coventry City",
        "home_cn": "阿森纳",
        "away_cn": "考文垂",
    },
    {
        "event_id": "16310922",
        "league": "法甲",
        "league_code": "Ligue1",
        "season": "26/27",
        "txt": DATA_DIR / "法甲2026-2027赛季完整时序赔率.txt",
        "home_en": "Olympique de Marseille",
        "away_en": "RC Strasbourg",
        "home_cn": "马赛",
        "away_cn": "斯特拉斯",
    },
]

LEAGUE_PREFIX_TOKENS = [
    "西甲", "英超", "意甲", "德甲", "法甲",
    "La Liga", "LaLiga", "Premier League", "Serie A",
    "Bundesliga", "Ligue 1", "Ligue1",
]


def _strip_league_prefix(name: str) -> str:
    name = name.strip()
    for kw in LEAGUE_PREFIX_TOKENS:
        m = re.match(rf"^{re.escape(kw)}\s+", name)
        if m:
            name = name[m.end():].strip()
            break
    return name


def parse_team_line(line: str):
    """解析球队行，返回 (home, away)。支持 "西甲  贝蒂斯VS皇家社会" 与 "西甲 La Liga  A  B"。
    先剥离联赛前缀，再按 VS 或 2+ 空格拆分。"""
    line = line.strip()
    if not line:
        return None, None
    body = _strip_league_prefix(line)
    # 中文 "X VS Y" / "XVSY" 格式
    if "VS" in body.upper():
        m = re.search(r"\s*VS\s*", body, flags=re.IGNORECASE)
        if m:
            home = body[:m.start()].strip()
            away = body[m.end():].strip()
            if home and away:
                return home, away
    # 英文 2+ 空格分隔格式
    parts = [p.strip() for p in re.split(r"\s{2,}", body) if p.strip()]
    if len(parts) >= 2:
        return _strip_league_prefix(parts[-2]), parts[-1]
    # 单空格英文 "TeamA TeamB"
    sp = body.split()
    if len(sp) >= 2:
        return sp[-2], sp[-1]
    return None, None


def parse_score_section(block_body: str):
    """解析比分固定奖金区块 → records 列表。

    比分区块含多个"发布时间"子块（开/尾盘），每个子块固定 6 行：
    胜 label / 胜 value / 平 label / 平 value / 负 label / 负 value。
    比分 key 归一化为 "1:0"（去除空格），与 fuse_score_predictions 的 "h:a" 口径对齐。
    """
    records = []
    m_start = re.search(r'比分固定奖金', block_body)
    if not m_start:
        return records
    # 比分 section 范围：从"比分固定奖金"到"总进球固定奖金"（或块结束）
    tail = block_body[m_start.end():]
    m_end = re.search(r'\n总进球固定奖金', tail)
    section = tail if not m_end else tail[:m_end.start()]

    # 按"发布时间"切分：parts[0] 为标题残留，之后每 3 项 = (pub_date, pub_time, body)
    parts = re.split(r'发布时间\s*([\d\-]+)\s+([\d:]+)\s*\n', section)
    for i in range(1, len(parts), 3):
        pub_date = parts[i].strip()
        pub_time = parts[i + 1].strip()
        body = parts[i + 2]
        rec = {"pub_time": f"{pub_date} {pub_time}", "win_odds": {}, "draw_odds": {}, "lose_odds": {}}
        lines = [ln for ln in body.split("\n") if ln.strip() != ""]
        if len(lines) >= 6:
            for label_key, label_line, value_line in (
                ("win_odds", lines[0], lines[1]),
                ("draw_odds", lines[2], lines[3]),
                ("lose_odds", lines[4], lines[5]),
            ):
                labels = [s.strip().replace(" ", "") for s in re.split(r"\t+", label_line.strip()) if s.strip()]
                values = [s.strip() for s in re.split(r"\t+", value_line.strip()) if s.strip()]
                if len(labels) == len(values):
                    for sc, val in zip(labels, values):
                        try:
                            rec[label_key][sc] = float(val)
                        except ValueError:
                            pass
        records.append(rec)
    return records


def parse_odds_block(block_body: str, round_num: str, match_datetime: str, home_short: str, away_short: str):
    """解析单个比赛块的赔率数据，返回 generate_unified_report 兼容的 odds_data。"""
    odds_data = {
        "round": round_num,
        "match_datetime": match_datetime,
        "home_short": home_short,
        "away_short": away_short,
        "wdl_odds": {"records": [], "open": {}, "close": {}},
        "handicap_odds": {"line": None, "records": [], "open": {}, "close": {}},
        "score_odds": {"records": []},
        "tg_odds": {"records": []},
    }

    # 1. 胜平负
    wdl_pattern = r'胜平负固定奖金\s*\n发布时间\s*胜\s*平\s*负\s*\n([\s\S]*?)\n\n'
    wdl_match = re.search(wdl_pattern, block_body)
    if wdl_match:
        for line in wdl_match.group(1).strip().split("\n"):
            parts = re.split(r"\s+", line.strip())
            if len(parts) >= 5:
                try:
                    odds_data["wdl_odds"]["records"].append({
                        "time": parts[0] + " " + parts[1],
                        "win": float(parts[2]), "draw": float(parts[3]), "lose": float(parts[4]),
                    })
                except ValueError:
                    continue
        if odds_data["wdl_odds"]["records"]:
            odds_data["wdl_odds"]["open"] = odds_data["wdl_odds"]["records"][0]
            odds_data["wdl_odds"]["close"] = odds_data["wdl_odds"]["records"][-1]

    # 2. 让球
    hdp_pattern = r'让球胜平负固定奖金\s*\n让球([+\-]?\d+)\s*\n发布时间\s*胜\s*平\s*负\s*\n([\s\S]*?)\n\n'
    hdp_match = re.search(hdp_pattern, block_body)
    if hdp_match:
        odds_data["handicap_odds"]["line"] = int(hdp_match.group(1))
        for line in hdp_match.group(2).strip().split("\n"):
            parts = re.split(r"\s+", line.strip())
            if len(parts) >= 5:
                try:
                    odds_data["handicap_odds"]["records"].append({
                        "time": parts[0] + " " + parts[1],
                        "win": float(parts[2]), "draw": float(parts[3]), "lose": float(parts[4]),
                    })
                except ValueError:
                    continue
        if odds_data["handicap_odds"]["records"]:
            odds_data["handicap_odds"]["open"] = odds_data["handicap_odds"]["records"][0]
            odds_data["handicap_odds"]["close"] = odds_data["handicap_odds"]["records"][-1]

    # 3. 比分
    odds_data["score_odds"]["records"] = parse_score_section(block_body)

    # 4. 总进球
    tg_pattern = r'总进球固定奖金\s*\n发布时间\s+[\d]+\+?\s+[\d]+\+?\s+[\d]+\+?\s+[\d]+\+?\s+[\d]+\+?\s+[\d]+\+?\s+[\d]+\+?\s+[\d]+\+?\s*\n([\s\S]*?)(?=\n\n\d{2}-\d{2}|\n\n\d{4}/\d{4}|\Z)'
    tg_match = re.search(tg_pattern, block_body)
    if tg_match:
        for line in tg_match.group(1).strip().split("\n"):
            line = line.strip()
            if not line or not re.match(r"\d{4}-\d{2}-\d{2}", line):
                continue
            parts = re.split(r"\s+", line)
            if len(parts) >= 10:
                goals = {}
                tg_labels = ["0", "1", "2", "3", "4", "5", "6", "7+"]
                for idx, label in enumerate(tg_labels):
                    if idx + 2 < len(parts):
                        try:
                            goals[label] = float(parts[idx + 2])
                        except ValueError:
                            pass
                if goals:
                    odds_data["tg_odds"]["records"].append({"pub_time": f"{parts[0]} {parts[1]}", "goals": goals})

    return odds_data


def find_and_parse_odds(txt_path: Path, home_cn: str, away_cn: str):
    """在 TXT 中定位目标比赛块并解析赔率。返回 (meta, odds_data) 或 (None, None)。"""
    content = txt_path.read_text(encoding="utf-8")
    block_pattern = r'(\d{4}/\d{4} Regular Season 第\d+轮 \d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\n([\s\S]*?)(?=\d{4}/\d{4} Regular Season|$)'
    for header, block_body in re.findall(block_pattern, content):
        m_dt = re.search(r"第(\d+)轮 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", header)
        round_num = m_dt.group(1) if m_dt else "1"
        match_datetime = m_dt.group(2) if m_dt else ""
        lines = [ln for ln in block_body.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        team_line = lines[0]
        home, away = parse_team_line(team_line)
        if not home or not away:
            continue
        # 匹配目标比赛（中文名包含关系）
        if (home_cn in home and away_cn in away) or (home_cn in away and away_cn in home):
            odds_data = parse_odds_block(block_body, round_num, match_datetime, home, away)
            meta = {
                "round_num": round_num,
                "match_datetime": match_datetime,
                "home": home,
                "away": away,
            }
            return meta, odds_data
    return None, None


# ============================================================
# 1. SofaScore 抓取 + 写库
# ============================================================
def crawl_sofascore():
    client = SofaScoreClient(logger)
    conn = sqlite3.connect(str(DATA_DIR / "odds.db"), check_same_thread=False)
    init_db_schema_if_needed(conn, logger)

    results = []
    for m in MATCHES:
        eid = m["event_id"]
        logger.info("=" * 60)
        logger.info(f"抓取 SofaScore: {m['home_en']} vs {m['away_en']} (event={eid})")
        detail = fetch_event_detail(client, eid, logger)
        if not detail:
            logger.error(f"  抓取失败: {eid}")
            results.append({"event_id": eid, "error": "detail fetch failed"})
            continue
        event = detail.get("event", detail)
        try:
            r = collect_single_event(client, event, m["league"], m["season"], conn, logger, dry_run=False)
            results.append(r)
            logger.info(f"  写库完成: status={r['status']} counts={r.get('counts')}")
        except Exception as e:
            logger.error(f"  写库异常: {type(e).__name__}: {e}")
            results.append({"event_id": eid, "error": str(e)})

    client.close()
    conn.close()
    return results


# ============================================================
# 2. 赔率时序落库 odds_timing.db
# ============================================================
def store_odds_to_timing(parsed_odds):
    conn = data_store._connect(data_store.ODDS_TIMING_DB)
    data_store.ensure_odds_timing_schema(conn)
    cur = conn.cursor()
    stats = {"matches": 0, "wdl": 0, "hcp": 0, "tg": 0, "score": 0}

    for m in MATCHES:
        meta, odds = parsed_odds[m["event_id"]]
        if not meta:
            continue
        match_date = meta["match_datetime"].split(" ")[0]
        match_time = meta["match_datetime"].split(" ")[1] if " " in meta["match_datetime"] else ""
        mid = data_store.build_match_id(match_date, meta["home"], meta["away"])
        cur.execute(
            """INSERT OR REPLACE INTO matches
               (match_id, home_team, away_team, match_date, match_time, league, league_code, source)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (mid, meta["home"], meta["away"], match_date, match_time, m["league"], m["league_code"], "TXT_IMPORT"),
        )
        stats["matches"] += 1

        for rec in odds["wdl_odds"]["records"]:
            cur.execute(
                """INSERT OR REPLACE INTO wdl_timing (match_id, timestamp, win_a, draw, win_b, source)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (mid, rec["time"], rec["win"], rec["draw"], rec["lose"], "TXT_IMPORT"),
            )
            stats["wdl"] += 1

        hline = odds["handicap_odds"].get("line") or 0
        for rec in odds["handicap_odds"]["records"]:
            cur.execute(
                """INSERT OR REPLACE INTO handicap_timing
                   (match_id, timestamp, handicap, hcp_win, hcp_draw, hcp_lose, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (mid, rec["time"], hline, rec["win"], rec["draw"], rec["lose"], "TXT_IMPORT"),
            )
            stats["hcp"] += 1

        for rec in odds["tg_odds"]["records"]:
            g = rec["goals"]
            goals = [
                g.get("0"), g.get("1"), g.get("2"), g.get("3"),
                g.get("4"), g.get("5"), g.get("6"), g.get("7+"),
            ]
            cur.execute(
                """INSERT OR REPLACE INTO total_goals_timing
                   (match_id, timestamp, goals_0, goals_1, goals_2, goals_3,
                    goals_4, goals_5, goals_6, goals_7_plus, source)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (mid, rec["pub_time"], *goals, "TXT_IMPORT"),
            )
            stats["tg"] += 1

        for rec in odds["score_odds"]["records"]:
            for score, val in rec["win_odds"].items():
                cur.execute(
                    """INSERT OR REPLACE INTO score_timing (match_id, timestamp, score, odds, source)
                       VALUES (?, ?, ?, ?, ?)""",
                    (mid, rec["pub_time"], score.replace(" ", ""), val, "TXT_IMPORT"),
                )
                stats["score"] += 1
            for score, val in rec["draw_odds"].items():
                cur.execute(
                    """INSERT OR REPLACE INTO score_timing (match_id, timestamp, score, odds, source)
                       VALUES (?, ?, ?, ?, ?)""",
                    (mid, rec["pub_time"], score.replace(" ", ""), val, "TXT_IMPORT"),
                )
                stats["score"] += 1
            for score, val in rec["lose_odds"].items():
                cur.execute(
                    """INSERT OR REPLACE INTO score_timing (match_id, timestamp, score, odds, source)
                       VALUES (?, ?, ?, ?, ?)""",
                    (mid, rec["pub_time"], score.replace(" ", ""), val, "TXT_IMPORT"),
                )
                stats["score"] += 1

    conn.commit()
    conn.close()
    logger.info(f"赔率时序落库: matches={stats['matches']} wdl={stats['wdl']} hcp={stats['hcp']} tg={stats['tg']} score={stats['score']}")
    return stats


# ============================================================
# 3. 生成赛前 JSON（generate_unified_report 兼容格式）
# ============================================================
def build_pre_match_json(crawl_results):
    pre = {}
    for m in MATCHES:
        eid = m["event_id"]
        entry = {
            "event_id": eid,
            "home_name": m["home_en"],
            "home_short": m["home_en"],
            "away_name": m["away_en"],
            "away_short": m["away_en"],
            "status": "Not started",
            "confirmed": False,
            "home_formation": "",
            "away_formation": "",
            "start_time": 0,
            "label": f"{m['home_cn']} vs {m['away_cn']}",
        }
        # 尝试从抓取结果补充阵型
        r = next((r for r in crawl_results if str(r.get("event_id")) == eid), None)
        if r and not r.get("error"):
            entry["start_time"] = 0  # 由 detail 提供，此处省略
        pre[eid] = entry
    out = DATA_DIR / "pre_match_3matches.json"
    out.write_text(json.dumps(pre, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info(f"赛前 JSON 已生成: {out}")
    return pre


# ============================================================
# 4. 统一预测
# ============================================================
def predict(parsed_odds):
    logger.info("加载预测模型 (prediction_core)...")
    models = init_models()
    core = PredictionCore(models)
    results = []
    for m in MATCHES:
        meta, odds = parsed_odds[m["event_id"]]
        if not meta:
            logger.warning(f"无赔率数据，跳过预测: {m['home_cn']} vs {m['away_cn']}")
            continue
        # 若 WDL 赔率缺失，记录警告但仍尝试预测（其余维度可用）
        if not odds["wdl_odds"]["records"]:
            logger.warning(f"⚠️  {m['league']} {m['home_cn']} vs {m['away_cn']}: 胜平负赔率缺失，WDL 将回退到占位赔率")

        match = {
            "id": f"match_{m['event_id']}",
            "event_id": m["event_id"],
            "home_team": m["home_en"],
            "away_team": m["away_en"],
            "home_team_cn": m["home_cn"],
            "away_team_cn": m["away_cn"],
            "home_short": m["home_cn"],
            "away_short": m["away_cn"],
            "match_time": meta["match_datetime"],
            "league": m["league"],
            "round": f"第{meta['round_num']}轮",
            "sofascore_url": f"https://www.sofascore.ro/football/match/{m['event_id']}",
            "odds_data": odds,
            "pre_data": None,
        }
        result = core.predict_unified(match, odds, is_mock=False)
        results.append({"match": match, "result": result})
    return results


def print_summary(results):
    print("\n" + "=" * 70)
    print("三场比赛预测摘要")
    print("=" * 70)
    for r in results:
        m = r["match"]
        res = r["result"]
        print(f"\n【{m['league']} 】{m['home_team_cn']} vs {m['away_team_cn']} ({m['round']} {m['match_time']})")
        wdl = res["wdl"]
        print(f"  胜平负: {wdl['prediction']}  置信度 {wdl['confidence']*100:.1f}%  [{wdl['method']}]")
        print(f"          主胜 {wdl['home_prob']*100:.1f}% | 平局 {wdl['draw_prob']*100:.1f}% | 客胜 {wdl['away_prob']*100:.1f}%")
        hcp = res["hcp"]
        print(f"  让球:   {hcp['prediction']}  置信度 {hcp['confidence']*100:.1f}%  [{hcp.get('method','N/A')}]")
        try:
            print(f"          上盘 {hcp['home_win_prob']*100:.1f}% | 走水 {hcp['draw_prob']*100:.1f}% | 下盘 {hcp['away_win_prob']*100:.1f}%")
        except KeyError:
            pass
        sc = res["score"]
        print(f"  比分:   {sc['most_likely']}  ({sc['most_likely_prob']*100:.1f}%)  [{sc['method']}]")
        tg = res["tg"]
        print(f"  总进球: {tg['prediction']}  大球概率 {tg['over_25_prob']*100:.1f}%  [{tg['method']}]")
    print("\n" + "=" * 70)


def main():
    skip_crawl = "--skip-crawl" in sys.argv

    # 步骤 1：爬虫
    if skip_crawl:
        logger.info("跳过 SofaScore 抓取（--skip-crawl）")
        crawl_results = []
    else:
        crawl_results = crawl_sofascore()

    # 步骤 2：解析赔率 TXT 并落库
    parsed_odds = {}
    for m in MATCHES:
        meta, odds = find_and_parse_odds(m["txt"], m["home_cn"], m["away_cn"])
        if meta:
            parsed_odds[m["event_id"]] = (meta, odds)
            n_wdl = len(odds["wdl_odds"]["records"])
            n_hcp = len(odds["handicap_odds"]["records"])
            n_tg = len(odds["tg_odds"]["records"])
            n_score = len(odds["score_odds"]["records"])
            logger.info(f"赔率解析: {m['league']} {meta['home']} vs {meta['away']} | "
                        f"wdl={n_wdl} hcp={n_hcp} tg={n_tg} score={n_score}")
        else:
            parsed_odds[m["event_id"]] = (None, None)
            logger.error(f"未能定位赔率: {m['league']} {m['home_cn']} vs {m['away_cn']}")

    try:
        store_odds_to_timing(parsed_odds)
    except Exception as e:
        logger.error(f"赔率时序落库异常（不影响预测）: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())

    # 步骤 3：生成赛前 JSON
    pre = build_pre_match_json(crawl_results)

    # 步骤 4：预测
    results = predict(parsed_odds)
    print_summary(results)

    # 保存预测结果
    out = DATA_DIR / f"prediction_3matches_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
    serializable = []
    for r in results:
        serializable.append({
            "league": r["match"]["league"],
            "home": r["match"]["home_team_cn"],
            "away": r["match"]["away_team_cn"],
            "home_en": r["match"]["home_team"],
            "away_en": r["match"]["away_team"],
            "round": r["match"]["round"],
            "match_time": r["match"]["match_time"],
            "wdl": r["result"]["wdl"],
            "hcp": r["result"]["hcp"],
            "score": r["result"]["score"],
            "tg": r["result"]["tg"],
        })
    out.write_text(json.dumps(serializable, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    logger.info(f"预测结果已保存: {out}")

    # 步骤 5：回写离线预测结果到 odds.db，打通「预测→落库→回测→重训」闭环
    try:
        ids = batch_save_predictions([serializable_to_pred(s) for s in serializable])
        logger.info(f"预测结果已回写 model_predictions: {len(ids)} 场 -> {ids}")
    except Exception as e:
        logger.error(f"回写 model_predictions 失败（不影响预测）: {type(e).__name__}: {e}")
        logger.error(traceback.format_exc())


if __name__ == "__main__":
    main()