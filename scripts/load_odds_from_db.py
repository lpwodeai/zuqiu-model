# -*- coding: utf-8 -*-
"""
从 odds.db 加载一场比赛的完整时序赔率数据（SSOT 替代 TXT 解析）
=============================================================
替代原 parse_odds_block / parse_odds_txt 的 TXT 解析路径，
直接从 odds.db 的 wdl_history / handicap_history / total_goals_history /
score_history 四张时序表 + matches.handicap 字段加载，返回与原 TXT 解析
完全相同的 odds_data 结构，保证 predict_core.predict_unified 等下游接口零改造。

数据流对照:
  旧: TXT 文件 → parse_odds_block() → odds_data dict
  新: odds.db  → load_odds_from_db() → odds_data dict

表结构映射:
  matches.handicap               → odds_data['handicap_odds']['line']
  wdl_history (win_a, draw, win_b) → odds_data['wdl_odds']['records'][i] = {time, win, draw, lose}
  handicap_history (hcp_win, hcp_draw, hcp_lose) → odds_data['handicap_odds']['records'][i]
  total_goals_history (goals_0~goals_7_plus)     → odds_data['tg_odds']['records'][i] = {pub_time, goals:{'0'~'7+'}}
  score_history (score, odds) 按 timestamp 分组 → odds_data['score_odds']['records'][i]
    - score "H:A" 中 H>A 归 win_odds, H==A 归 draw_odds, H<A 归 lose_odds

使用方法:
  from load_odds_from_db import load_odds_from_db
  odds_data = load_odds_from_db('2026-08-22_伊普斯维奇_桑德兰')
  result = core.predict_unified(match, odds_data, is_mock=False)

关联变更: C-20260918-020
"""
import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DB = BASE_DIR / "data" / "odds.db"

# 队名归一化（4级匹配：精确别名→子串→模糊→序列相似度）
import sys as _sys
_sys.path.insert(0, str(BASE_DIR / "scripts"))
from team_name_mapping import normalize_team_name as _normalize_team


def _safe_float(v):
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


def _score_to_class(score_str):
    """
    将比分 "H:A" 分类为 'win' / 'draw' / 'lose'（从主队视角）。
    返回 (class, score_str)；解析失败返回 (None, score_str)。
    """
    if not score_str or ":" not in score_str:
        return None, score_str
    try:
        h, a = score_str.split(":")
        h, a = int(h), int(a)
        if h > a:
            return "win", score_str
        elif h == a:
            return "draw", score_str
        else:
            return "lose", score_str
    except (ValueError, AttributeError):
        return None, score_str


def load_odds_from_db(match_id, db_path=None):
    """
    从 odds.db 加载一场比赛的完整时序赔率数据。

    Args:
        match_id: 如 '2026-08-22_伊普斯维奇_桑德兰'（与 matches.match_id 一致）
        db_path: 默认 data/odds.db

    Returns:
        odds_data dict（与原 TXT 解析结构兼容），未找到返回 None
    """
    db_path = Path(db_path) if db_path else DEFAULT_DB
    if not db_path.exists():
        raise FileNotFoundError(f"odds.db not found: {db_path}")

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()

    try:
        # 1. matches.handicap → 让球盘口
        cur.execute(
            "SELECT handicap, match_date FROM matches WHERE match_id = ?",
            (match_id,),
        )
        row = cur.fetchone()
        if row is None:
            return None
        hcp_line = int(row["handicap"]) if row["handicap"] is not None else None
        match_date = row["match_date"] or ""

        # 2. wdl_history → 胜平负时序
        cur.execute(
            "SELECT timestamp, win_a, draw, win_b FROM wdl_history "
            "WHERE match_id = ? ORDER BY timestamp ASC",
            (match_id,),
        )
        wdl_records = []
        for r in cur.fetchall():
            w, d, l = _safe_float(r["win_a"]), _safe_float(r["draw"]), _safe_float(r["win_b"])
            if w is None or d is None or l is None:
                continue
            wdl_records.append({"time": r["timestamp"], "win": w, "draw": d, "lose": l})

        # 3. handicap_history → 让球时序
        cur.execute(
            "SELECT timestamp, hcp_win, hcp_draw, hcp_lose FROM handicap_history "
            "WHERE match_id = ? ORDER BY timestamp ASC",
            (match_id,),
        )
        hcp_records = []
        for r in cur.fetchall():
            w, d, l = _safe_float(r["hcp_win"]), _safe_float(r["hcp_draw"]), _safe_float(r["hcp_lose"])
            if w is None or d is None or l is None:
                continue
            hcp_records.append({"time": r["timestamp"], "win": w, "draw": d, "lose": l})

        # 4. total_goals_history → 总进球时序
        cur.execute(
            "SELECT timestamp, goals_0, goals_1, goals_2, goals_3, goals_4, goals_5, goals_6, goals_7_plus "
            "FROM total_goals_history WHERE match_id = ? ORDER BY timestamp ASC",
            (match_id,),
        )
        tg_records = []
        for r in cur.fetchall():
            goals = {}
            for label, col in [
                ("0", "goals_0"), ("1", "goals_1"), ("2", "goals_2"),
                ("3", "goals_3"), ("4", "goals_4"), ("5", "goals_5"),
                ("6", "goals_6"), ("7+", "goals_7_plus"),
            ]:
                v = _safe_float(r[col])
                if v is not None:
                    goals[label] = v
            if goals:
                tg_records.append({"pub_time": r["timestamp"], "goals": goals})

        # 5. score_history → 比分时序（按 timestamp 分组，按 score 主客分类）
        cur.execute(
            "SELECT timestamp, score, odds FROM score_history "
            "WHERE match_id = ? ORDER BY timestamp ASC",
            (match_id,),
        )
        score_by_ts = {}  # {timestamp: {'win_odds': {}, 'draw_odds': {}, 'lose_odds': {}}}
        for r in cur.fetchall():
            ts = r["timestamp"]
            sc = r["score"]
            od = _safe_float(r["odds"])
            if ts is None or sc is None or od is None:
                continue
            bucket = score_by_ts.setdefault(
                ts, {"win_odds": {}, "draw_odds": {}, "lose_odds": {}}
            )
            cls, sc_key = _score_to_class(sc)
            if cls:
                bucket[f"{cls}_odds"][sc_key] = od
        score_records = [
            {"pub_time": ts, **buckets} for ts, buckets in score_by_ts.items()
        ]

        # 组装 odds_data（与原 TXT 解析结构完全一致）
        odds_data = {
            "round": "",  # round 字段 TXT 才有，DB 无；下游未硬依赖
            "match_datetime": match_date,
            "wdl_odds": {
                "records": wdl_records,
                "open": wdl_records[0] if wdl_records else {},
                "close": wdl_records[-1] if wdl_records else {},
            },
            "handicap_odds": {
                "line": hcp_line if hcp_line is not None else 0,
                "records": hcp_records,
                "open": hcp_records[0] if hcp_records else {},
                "close": hcp_records[-1] if hcp_records else {},
            },
            "score_odds": {"records": score_records},
            "tg_odds": {"records": tg_records},
        }
        return odds_data
    finally:
        conn.close()


def find_match_id_by_teams(home_team, away_team, match_date_prefix="", db_path=None):
    """
    按主客队名（英文，与 matches.home_team/away_team 一致）查询 match_id。
    用于 predict_today_14.py 等场景：从队名反查 match_id 再加载赔率。

    注意：26-27 赛季 wdl_history/handicap_history 等 4 张时序表的 match_id 为
    中文格式（如 '2026-08-22_伊普斯维奇_桑德兰'），match_id_en 字段未填充。
    本函数优先查 wdl_history 用 LIKE 模糊匹配中文队名，确保返回的 match_id
    一定有时序赔率数据；找不到再回退到 matches 表的英文队名精确匹配。

    Args:
        home_team: 英文队名，如 'Ipswich Town'（用于回退匹配）
        away_team: 英文队名，如 'Sunderland'（用于回退匹配）
        match_date_prefix: 可选，限定日期前缀如 '2026-08-22'
        db_path: 默认 data/odds.db

    Returns:
        match_id 字符串；未找到返回 None
    """
    db_path = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        # 优先从 wdl_history 查询（保证返回的 match_id 一定有赔率数据）
        # 时序表 match_id 格式: {date}_{home_cn}_{away_cn}
        if match_date_prefix:
            sql = (
                "SELECT DISTINCT match_id FROM wdl_history "
                "WHERE match_id LIKE ? AND match_id LIKE ? AND match_id LIKE ? "
                "ORDER BY match_id DESC LIMIT 1"
            )
            pattern_home = f"{match_date_prefix}%_{home_team}_%"
            pattern_away = f"{match_date_prefix}%_{away_team}"
        else:
            sql = (
                "SELECT DISTINCT match_id FROM wdl_history "
                "WHERE match_id LIKE ? AND match_id LIKE ? "
                "ORDER BY match_id DESC LIMIT 1"
            )
            pattern_home = f"%_{home_team}_%"
            pattern_away = f"%_{away_team}"
        cur.execute(sql, (pattern_home, pattern_away))
        row = cur.fetchone()
        if row:
            return row[0]
        return None
    finally:
        conn.close()


def find_match_id_by_cn(home_cn, away_cn, match_date_prefix="", db_path=None):
    """
    按中文队名（短名或全名）模糊查询 wdl_history 中的 match_id。
    26-27 赛季时序表的 match_id 用中文全名（如 '伊普斯维奇'），
    而 predict_today_14.py 的 TODAY_MATCHES 用中文短名（如 '伊普斯'），
    所以用 LIKE 模糊匹配。

    Args:
        home_cn: 中文短名，如 '伊普斯'
        away_cn: 中文短名，如 '桑德兰'
        match_date_prefix: 可选，限定日期前缀如 '2026-08-22'
        db_path: 默认 data/odds.db

    Returns:
        match_id 字符串；未找到返回 None
    """
    db_path = Path(db_path) if db_path else DEFAULT_DB
    conn = sqlite3.connect(str(db_path))
    cur = conn.cursor()
    try:
        # 归一化队名（4级匹配），确保和 wdl_history 的标准名一致
        # 解决 odds500_match 用「托特纳姆热刺」但 wdl_history 归一化为「热刺」的失配
        home_norm = _normalize_team(home_cn) or home_cn
        away_norm = _normalize_team(away_cn) or away_cn
        if match_date_prefix:
            sql = (
                "SELECT DISTINCT match_id FROM wdl_history "
                "WHERE match_id LIKE ? AND match_id LIKE ? AND match_id LIKE ? "
                "ORDER BY match_id DESC LIMIT 1"
            )
            params = (f"{match_date_prefix}%_{home_norm}%", f"%_{away_norm}", f"{match_date_prefix}%")
        else:
            sql = (
                "SELECT DISTINCT match_id FROM wdl_history "
                "WHERE match_id LIKE ? AND match_id LIKE ? "
                "ORDER BY match_id DESC LIMIT 1"
            )
            params = (f"%_{home_norm}%", f"%_{away_norm}")
        cur.execute(sql, params)
        row = cur.fetchone()
        if row:
            return row[0]
        # fallback：用原始队名再查一次（归一化未命中时兜底）
        if home_norm != home_cn or away_norm != away_cn:
            if match_date_prefix:
                params = (f"{match_date_prefix}%_{home_cn}%", f"%_{away_cn}", f"{match_date_prefix}%")
            else:
                params = (f"%_{home_cn}%", f"%_{away_cn}")
            cur.execute(sql, params)
            row = cur.fetchone()
            if row:
                return row[0]
        return None
    finally:
        conn.close()


if __name__ == "__main__":
    # 自测：加载一场 26-27 比赛，验证结构完整性
    import sys
    test_match_id = "2026-08-22_伊普斯维奇_桑德兰"
    if len(sys.argv) > 1:
        test_match_id = sys.argv[1]
    print(f"加载 match_id: {test_match_id}")
    odds = load_odds_from_db(test_match_id)
    if odds is None:
        print(f"❌ 未找到 match_id: {test_match_id}")
        sys.exit(1)
    print(f"  match_datetime: {odds['match_datetime']}")
    print(f"  wdl_odds: {len(odds['wdl_odds']['records'])} 条时序, open={odds['wdl_odds']['open']}")
    print(f"  handicap_odds: line={odds['handicap_odds']['line']}, {len(odds['handicap_odds']['records'])} 条时序")
    print(f"  score_odds: {len(odds['score_odds']['records'])} 个时间点")
    if odds['score_odds']['records']:
        last = odds['score_odds']['records'][-1]
        print(f"    最新: pub_time={last['pub_time']}, win_odds={len(last['win_odds'])} 个, draw_odds={len(last['draw_odds'])} 个, lose_odds={len(last['lose_odds'])} 个")
    print(f"  tg_odds: {len(odds['tg_odds']['records'])} 条时序")
    if odds['tg_odds']['records']:
        print(f"    最新: {odds['tg_odds']['records'][-1]}")
    print("✅ 结构验证通过")
