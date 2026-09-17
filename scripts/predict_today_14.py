# -*- coding: utf-8 -*-
"""今日五大联赛14场即将开赛比赛预测（胜平负/让球/比分/总进球）"""
import sys, os, re, json, sqlite3
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "collection"))

from prediction_core import init_models, PredictionCore
import logging
logging.basicConfig(level=logging.WARNING)  # 减少日志噪音

# 今日14场即将开赛比赛（排除已完赛的3场）
TODAY_MATCHES = [
    # 英超
    {"league": "英超", "home_cn": "埃弗顿", "away_cn": "水晶宫", "home_en": "Everton", "away_en": "Crystal Palace",
     "txt": "英超2026-2027赛季完整时序赔率.txt"},
    {"league": "英超", "home_cn": "诺丁汉", "away_cn": "利兹联", "home_en": "Nottingham Forest", "away_en": "Leeds United",
     "txt": "英超2026-2027赛季完整时序赔率.txt"},
    {"league": "英超", "home_cn": "伊普斯", "away_cn": "桑德兰", "home_en": "Ipswich Town", "away_en": "Sunderland",
     "txt": "英超2026-2027赛季完整时序赔率.txt"},
    {"league": "英超", "home_cn": "布伦特", "away_cn": "热刺", "home_en": "Brentford", "away_en": "Tottenham Hotspur",
     "txt": "英超2026-2027赛季完整时序赔率.txt"},
    # 西甲
    {"league": "西甲", "home_cn": "毕尔巴鄂", "away_cn": "塞维利亚", "home_en": "Athletic Club", "away_en": "Sevilla",
     "txt": "西甲2026-2027赛季完整时序赔率.txt"},
    {"league": "西甲", "home_cn": "巴伦西亚", "away_cn": "塞尔塔", "home_en": "Valencia", "away_en": "Celta Vigo",
     "txt": "西甲2026-2027赛季完整时序赔率.txt"},
    {"league": "西甲", "home_cn": "西班牙人", "away_cn": "皇马", "home_en": "Espanyol", "away_en": "Real Madrid",
     "txt": "西甲2026-2027赛季完整时序赔率.txt"},
    # 法甲
    {"league": "法甲", "home_cn": "朗斯", "away_cn": "欧塞尔", "home_en": "RC Lens", "away_en": "Auxerre",
     "txt": "法甲2026-2027赛季完整时序赔率.txt"},
    {"league": "法甲", "home_cn": "尼斯", "away_cn": "洛里昂", "home_en": "Nice", "away_en": "Lorient",
     "txt": "法甲2026-2027赛季完整时序赔率.txt"},
    {"league": "法甲", "home_cn": "图卢兹", "away_cn": "里昂", "home_en": "Toulouse", "away_en": "Olympique Lyonnais",
     "txt": "法甲2026-2027赛季完整时序赔率.txt"},
    # 意甲
    {"league": "意甲", "home_cn": "乌迪内斯", "away_cn": "科莫", "home_en": "Udinese", "away_en": "Como",
     "txt": "意甲2026-2027赛季完整时序赔率.txt"},
    {"league": "意甲", "home_cn": "国际米兰", "away_cn": "蒙扎", "home_en": "Inter", "away_en": "Monza",
     "txt": "意甲2026-2027赛季完整时序赔率.txt"},
    {"league": "意甲", "home_cn": "热那亚", "away_cn": "那不勒斯", "home_en": "Genoa", "away_en": "SSC Napoli",
     "txt": "意甲2026-2027赛季完整时序赔率.txt"},
    {"league": "意甲", "home_cn": "帕尔马", "away_cn": "卡利亚里", "home_en": "Parma", "away_en": "Cagliari",
     "txt": "意甲2026-2027赛季完整时序赔率.txt"},
]

LEAGUE_PREFIX_TOKENS = ["西甲", "英超", "意甲", "德甲", "法甲",
    "La Liga", "LaLiga", "Premier League", "Serie A", "Bundesliga", "Ligue 1", "Ligue1"]


def _strip_league_prefix(name):
    name = name.strip()
    for kw in LEAGUE_PREFIX_TOKENS:
        m = re.match(rf"^{re.escape(kw)}\s+", name)
        if m:
            name = name[m.end():].strip()
            break
    return name


def parse_team_line(line):
    line = line.strip()
    if not line:
        return None, None
    body = _strip_league_prefix(line)
    if "VS" in body.upper():
        m = re.search(r"\s*VS\s*", body, flags=re.IGNORECASE)
        if m:
            return body[:m.start()].strip(), body[m.end():].strip()
    parts = [p.strip() for p in re.split(r"\s{2,}", body) if p.strip()]
    if len(parts) >= 2:
        return _strip_league_prefix(parts[-2]), parts[-1]
    sp = body.split()
    if len(sp) >= 2:
        return sp[-2], sp[-1]
    return None, None


def parse_score_section(block_body):
    records = []
    m_start = re.search(r'比分固定奖金', block_body)
    if not m_start:
        return records
    tail = block_body[m_start.end():]
    m_end = re.search(r'\n总进球固定奖金', tail)
    section = tail if not m_end else tail[:m_end.start()]
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


def parse_odds_block(block_body, round_num, match_datetime):
    odds_data = {
        "round": round_num, "match_datetime": match_datetime,
        "wdl_odds": {"records": [], "open": {}, "close": {}},
        "handicap_odds": {"line": None, "records": [], "open": {}, "close": {}},
        "score_odds": {"records": []},
        "tg_odds": {"records": []},
    }
    # 胜平负
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
    # 让球
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
    # 比分
    odds_data["score_odds"]["records"] = parse_score_section(block_body)
    # 总进球
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
                for idx, label in enumerate(["0", "1", "2", "3", "4", "5", "6", "7+"]):
                    if idx + 2 < len(parts):
                        try:
                            goals[label] = float(parts[idx + 2])
                        except ValueError:
                            pass
                if goals:
                    odds_data["tg_odds"]["records"].append({"pub_time": f"{parts[0]} {parts[1]}", "goals": goals})
    return odds_data


def find_and_parse_odds(txt_path, home_cn, away_cn):
    content = txt_path.read_text(encoding="utf-8")
    block_pattern = r'(\d{4}/\d{4} Regular Season 第\d+轮 \d{4}-\d{2}-\d{2} \d{2}:\d{2})\s*\n([\s\S]*?)(?=\d{4}/\d{4} Regular Season|$)'
    for header, block_body in re.findall(block_pattern, content):
        m_dt = re.search(r"第(\d+)轮 (\d{4}-\d{2}-\d{2} \d{2}:\d{2})", header)
        round_num = m_dt.group(1) if m_dt else "1"
        match_datetime = m_dt.group(2) if m_dt else ""
        lines = [ln for ln in block_body.split("\n") if ln.strip() != ""]
        if not lines:
            continue
        home, away = parse_team_line(lines[0])
        if not home or not away:
            continue
        if (home_cn in home and away_cn in away) or (home_cn in away and away_cn in home):
            odds_data = parse_odds_block(block_body, round_num, match_datetime)
            meta = {"round_num": round_num, "match_datetime": match_datetime, "home": home, "away": away}
            return meta, odds_data
    return None, None


def main():
    print("=" * 70)
    print("今日五大联赛预测 (14场即将开赛比赛)")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("=" * 70)

    print("\n加载模型...")
    models = init_models()
    core = PredictionCore(models)
    print("模型加载完成\n")

    results = []
    for m in TODAY_MATCHES:
        txt_path = DATA / m["txt"]
        meta, odds = find_and_parse_odds(txt_path, m["home_cn"], m["away_cn"])
        if not meta:
            print(f"  [WARN] 未找到赔率: {m['home_cn']} vs {m['away_cn']}")
            continue

        if not odds["wdl_odds"]["records"]:
            print(f"  [WARN] 缺WDL赔率: {m['league']} {m['home_cn']} vs {m['away_cn']}")

        match = {
            "home_team": m["home_en"], "away_team": m["away_en"],
            "home_team_cn": m["home_cn"], "away_team_cn": m["away_cn"],
            "league": m["league"],
            "match_time": meta["match_datetime"],
            "round": f"第{meta['round_num']}轮",
        }

        try:
            result = core.predict_unified(match, odds, is_mock=False)
            results.append({"match": match, "result": result, "odds": odds})
        except Exception as e:
            print(f"  [ERROR] {m['league']} {m['home_cn']} vs {m['away_cn']}: {e}")
            import traceback
            traceback.print_exc()

    # 打印摘要
    print("\n" + "=" * 70)
    print("预测摘要")
    print("=" * 70)
    for r in results:
        m = r["match"]
        res = r["result"]
        od = r["odds"]
        hcp_line = od["handicap_odds"].get("line")
        hcp_str = f"让球{hcp_line}" if hcp_line is not None else "让球N/A"

        print(f"\n【{m['league']}】{m['home_team_cn']} vs {m['away_team_cn']} ({m['round']} {m['match_time']})")
        wdl = res["wdl"]
        print(f"  胜平负: {wdl['prediction']}  置信度 {wdl['confidence']*100:.1f}%  [{wdl['method']}]")
        print(f"          主胜 {wdl['home_prob']*100:.1f}% | 平局 {wdl['draw_prob']*100:.1f}% | 客胜 {wdl['away_prob']*100:.1f}%")
        hcp = res["hcp"]
        print(f"  让球({hcp_str}): {hcp['prediction']}  置信度 {hcp['confidence']*100:.1f}%  [{hcp.get('method','N/A')}]")
        try:
            print(f"          上盘赢 {hcp['home_win_prob']*100:.1f}% | 走水 {hcp['draw_prob']*100:.1f}% | 下盘赢 {hcp['away_win_prob']*100:.1f}%")
        except KeyError:
            pass
        sc = res["score"]
        print(f"  比分: {sc['most_likely']} ({sc['most_likely_prob']*100:.1f}%)  [{sc['method']}]")
        tg = res["tg"]
        print(f"  总进球: {tg['prediction']}  大2.5={tg['over_25_prob']*100:.1f}%  [{tg['method']}]")

    print("\n" + "=" * 70)
    print("预测完成!")


if __name__ == "__main__":
    main()