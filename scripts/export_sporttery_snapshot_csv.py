"""导出竞彩网当日赛前赔率（含时序）为 CSV，用于人工核对采集准确性。

数据来源：
  - 赛程（场次号/联赛/队名/时间）: getMatchListV1.qry 实时接口
  - 赔率（胜平负/让球/总进球/比分）: data/odds.db 时序表

输出（UTF-8 BOM，Excel 可直接打开）:
  - sporttery_{date}_赔率快照.csv     每场一行：三大玩法最新赔率
  - sporttery_{date}_比分赔率.csv     每场比分 -> 最新赔率（长表）
  - sporttery_{date}_时序_胜平负.csv  胜平负全历史时序
  - sporttery_{date}_时序_让球.csv    让球全历史时序
  - sporttery_{date}_时序_总进球.csv  总进球全历史时序
  - sporttery_{date}_时序_比分.csv    比分全历史时序
"""
from __future__ import annotations

import csv
import sqlite3
import sys
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "odds.db"

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sporttery_sync_to_odds import TEAM_NAME_MAP

BASE = "https://webapi.sporttery.cn/gateway/uniform/football"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "X-Requested-With": "XMLHttpRequest",
    "Referer": "https://www.sporttery.cn/jc/zqszsc/",
}
LEAGUE_IDS = {"英超": "25", "意甲": "40", "西甲": "62", "德甲": "37", "法甲": "32"}
LEAGUE_ID_TO_NAME = {v: k for k, v in LEAGUE_IDS.items()}


def norm(name: str) -> str:
    name = (name or "").strip()
    return TEAM_NAME_MAP.get(name, name)


def fetch_matches(date: str) -> list:
    d = requests.get(f"{BASE}/getMatchListV1.qry?clientCode=3001", headers=HEADERS, timeout=30).json()
    if d.get("errorCode") != "0":
        raise RuntimeError(f"赛程接口失败: {d.get('errorMessage')}")
    out = []
    for mi in d.get("value", {}).get("matchInfoList", []):
        if mi.get("businessDate") != date:
            continue
        for s in mi.get("subMatchList", []):
            lid = str(s.get("leagueId", ""))
            if lid not in LEAGUE_ID_TO_NAME:
                continue
            if s.get("matchStatus") != "Selling":
                continue
            out.append({
                "num": s.get("matchNumStr", ""),
                "league": LEAGUE_ID_TO_NAME[lid],
                "home": s.get("homeTeamAllName", ""),
                "away": s.get("awayTeamAllName", ""),
                "time": s.get("matchTime", ""),
                "mid": f"{date}_{norm(s.get('homeTeamAllName', ''))}_{norm(s.get('awayTeamAllName', ''))}",
            })
    out.sort(key=lambda m: int("".join(ch for ch in m["num"] if ch.isdigit()) or "0"))
    return out


def write_csv(path: Path, header: list, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def main() -> None:
    date = "2026-08-29"
    stamp = date.replace("-", "")
    matches = fetch_matches(date)
    conn = sqlite3.connect(str(DB))
    cur = conn.cursor()

    snap_rows, score_snap_rows = [], []
    meta = [["num", "league", "home", "away", "time", "mid"]]
    wdl_rows, hcp_rows, ttg_rows, score_rows = [], [], [], []

    PRE = lambda m: [m["num"], m["league"], m["home"], m["away"], m["time"]]

    for m in matches:
        mid = m["mid"]
        # 快照：最新一行
        w = cur.execute("SELECT win_a,draw,win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
        h = cur.execute("SELECT hcp_win,hcp_draw,hcp_lose FROM handicap_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
        t = cur.execute("SELECT goals_0,goals_1,goals_2,goals_3,goals_4,goals_5,goals_6,goals_7_plus FROM total_goals_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
        snap_rows.append(PRE(m) + [
            *([f"{x:.2f}" for x in w] if w else ["", "", ""]),
            *([f"{x:.2f}" for x in h] if h else ["", "", ""]),
            *([f"{x:.2f}" for x in t] if t else [""] * 8),
        ])

        # 时序：全历史
        for row in cur.execute("SELECT timestamp,win_a,draw,win_b FROM wdl_history WHERE match_id=? ORDER BY timestamp", (mid,)):
            wdl_rows.append(PRE(m) + [row[0], f"{row[1]:.2f}", f"{row[2]:.2f}", f"{row[3]:.2f}"])
        for row in cur.execute("SELECT timestamp,hcp_win,hcp_draw,hcp_lose FROM handicap_history WHERE match_id=? ORDER BY timestamp", (mid,)):
            hcp_rows.append(PRE(m) + [row[0], f"{row[1]:.2f}", f"{row[2]:.2f}", f"{row[3]:.2f}"])
        for row in cur.execute("SELECT timestamp,goals_0,goals_1,goals_2,goals_3,goals_4,goals_5,goals_6,goals_7_plus FROM total_goals_history WHERE match_id=? ORDER BY timestamp", (mid,)):
            ttg_rows.append(PRE(m) + [row[0]] + [f"{x:.2f}" for x in row[1:]])
        for row in cur.execute("SELECT timestamp,score,odds FROM score_history WHERE match_id=? ORDER BY timestamp", (mid,)):
            score_rows.append(PRE(m) + [row[0], row[1], f"{row[2]:.2f}"])
        # 比分快照（最新 timestamp 的全部比分）
        ts = cur.execute("SELECT timestamp FROM score_history WHERE match_id=? ORDER BY timestamp DESC LIMIT 1", (mid,)).fetchone()
        if ts:
            for sc, od in cur.execute("SELECT score,odds FROM score_history WHERE match_id=? AND timestamp=? ORDER BY odds", (mid, ts[0])):
                score_snap_rows.append(PRE(m) + [sc, f"{od:.2f}"])

    conn.close()

    write_csv(ROOT / f"sporttery_{stamp}_赔率快照.csv",
              ["场次号", "联赛", "主队", "客队", "开赛时间",
               "胜平负_胜", "胜平负_平", "胜平负_负",
               "让球_胜", "让球_平", "让球_负",
               "总进球_0球", "总进球_1球", "总进球_2球", "总进球_3球",
               "总进球_4球", "总进球_5球", "总进球_6球", "总进球_7球及以上"],
              snap_rows)
    write_csv(ROOT / f"sporttery_{stamp}_比分赔率.csv",
              ["场次号", "联赛", "主队", "客队", "比分", "赔率"], score_snap_rows)
    write_csv(ROOT / f"sporttery_{stamp}_时序_胜平负.csv",
              ["场次号", "联赛", "主队", "客队", "更新时间", "胜", "平", "负"], wdl_rows)
    write_csv(ROOT / f"sporttery_{stamp}_时序_让球.csv",
              ["场次号", "联赛", "主队", "客队", "更新时间", "让球胜", "让球平", "让球负"], hcp_rows)
    write_csv(ROOT / f"sporttery_{stamp}_时序_总进球.csv",
              ["场次号", "联赛", "主队", "客队", "更新时间",
               "0球", "1球", "2球", "3球", "4球", "5球", "6球", "7球及以上"], ttg_rows)
    write_csv(ROOT / f"sporttery_{stamp}_时序_比分.csv",
              ["场次号", "联赛", "主队", "客队", "更新时间", "比分", "赔率"], score_rows)

    print(f"✅ 快照 {len(snap_rows)} 场")
    print(f"✅ 比分快照 {len(score_snap_rows)} 行")
    print(f"✅ 时序: 胜平负 {len(wdl_rows)} / 让球 {len(hcp_rows)} / 总进球 {len(ttg_rows)} / 比分 {len(score_rows)} 行")


if __name__ == "__main__":
    main()