# -*- coding: utf-8 -*-
"""今日五大联赛即将开赛比赛预测（胜平负/让球/比分/总进球）

数据源（C-20260918-020/022）:
  比赛清单: 从 odds500_match 表动态查询（status=1 未开赛 + 日期范围），
            替代硬编码 TODAY_MATCHES，与 generate_unified_report.py 的 discover_matches 同源
  赔率数据: 从 odds.db 四张时序表加载（load_odds_from_db，SSOT 替代 TXT 解析）

用法:
  python predict_today_14.py                    # 默认查今日 + 未来 2 天
  python predict_today_14.py --days 1           # 仅今日
  python predict_today_14.py --leagues 英超 西甲  # 指定联赛
"""
import sys, argparse, sqlite3
from pathlib import Path
from datetime import datetime, timedelta

BASE = Path(__file__).resolve().parent.parent
DATA = BASE / "data"
sys.path.insert(0, str(BASE / "scripts"))
sys.path.insert(0, str(BASE / "collection"))

from prediction_core import init_models, PredictionCore
from load_odds_from_db import load_odds_from_db, find_match_id_by_cn
import logging
logging.basicConfig(level=logging.WARNING)  # 减少日志噪音

DEFAULT_DB = DATA / "odds.db"


def discover_today_matches(db_path, days_ahead=2, leagues=None):
    """从 odds500_match 表动态查询今日 + 未来 N 天未开赛比赛。

    替代原硬编码 TODAY_MATCHES，与 generate_unified_report.py 的 discover_matches 同源。
    status=1 表示未开赛（2=已赛, 5=取消）。
    """
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        today = datetime.now().strftime("%Y-%m-%d")
        end_date = (datetime.now() + timedelta(days=days_ahead)).strftime("%Y-%m-%d")
        sql = """
            SELECT fid, league, round, match_date, match_time,
                   home_team_cn, away_team_cn, home_team_en, away_team_en
            FROM odds500_match
            WHERE season = '26/27' AND status = 1
              AND match_date >= ? AND match_date <= ?
        """
        args = [today, end_date]
        if leagues:
            placeholders = ",".join("?" for _ in leagues)
            sql += f" AND league IN ({placeholders})"
            args.extend(leagues)
        sql += " ORDER BY match_date, match_time, league"
        rows = conn.execute(sql, args).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            # 队名英文缺失（升班马）时回退中文占位
            d["home_team_en"] = (d.get("home_team_en") or "").strip() or d["home_team_cn"]
            d["away_team_en"] = (d.get("away_team_en") or "").strip() or d["away_team_cn"]
            out.append(d)
        return out
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description="今日五大联赛即将开赛比赛预测")
    parser.add_argument("--days", type=int, default=2, help="查询未来 N 天（默认 2）")
    parser.add_argument("--leagues", nargs="*", default=None, help="限定联赛（如 英超 西甲）")
    args = parser.parse_args()

    print("=" * 70)
    print("今日五大联赛预测 (即将开赛比赛)")
    print(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    scope = f"今日 + 未来 {args.days} 天"
    if args.leagues:
        scope += f"  联赛: {','.join(args.leagues)}"
    else:
        scope += "  全联赛"
    print(scope)
    print("=" * 70)

    print("\n加载模型...")
    models = init_models()
    core = PredictionCore(models)
    print("模型加载完成\n")

    # 动态查询比赛清单（替代硬编码 TODAY_MATCHES）
    matches = discover_today_matches(DEFAULT_DB, days_ahead=args.days, leagues=args.leagues)
    print(f"从 odds500_match 查到 {len(matches)} 场未开赛比赛\n")

    # C-20260919-019: 与 generate_unified_report 对齐——预测前注入待赛虚拟行，
    # 否则未赛场走 fallback 模板会残留他场赔率/Elo 造成方向污染
    core.wdl_predictor.prime_fixtures([
        {
            'home_team': m["home_team_en"], 'away_team': m["away_team_en"],
            'home_team_cn': m["home_team_cn"], 'away_team_cn': m["away_team_cn"],
            'league': m["league"], 'date': m["match_date"],
            'wdl_match_id': find_match_id_by_cn(m["home_team_cn"], m["away_team_cn"]),
        }
        for m in matches
    ])

    results = []
    for m in matches:
        home_cn = m["home_team_cn"]
        away_cn = m["away_team_cn"]
        home_en = m["home_team_en"]
        away_en = m["away_team_en"]
        league = m["league"]

        # SSOT 改造：从 odds.db 反查 match_id 并加载赔率，替代原 TXT 解析
        # 26-27 赛季时序表 match_id 为中文格式，用中文短名模糊匹配
        match_id = find_match_id_by_cn(home_cn, away_cn)
        if not match_id:
            print(f"  [WARN] odds.db 未找到比赛: {home_cn} vs {away_cn}")
            continue
        odds = load_odds_from_db(match_id)
        if not odds:
            print(f"  [WARN] 无赔率数据: {home_cn} vs {away_cn} (match_id={match_id})")
            continue

        if not odds["wdl_odds"]["records"]:
            print(f"  [WARN] 缺WDL赔率: {league} {home_cn} vs {away_cn}")

        match = {
            "home_team": home_en, "away_team": away_en,
            "home_team_cn": home_cn, "away_team_cn": away_cn,
            "league": league,
            "match_time": odds["match_datetime"],
            "round": odds.get("round") or "",
            "match_id": match_id,
        }

        try:
            result = core.predict_unified(match, odds, is_mock=False)
            results.append({"match": match, "result": result, "odds": odds})
        except Exception as e:
            print(f"  [ERROR] {league} {home_cn} vs {away_cn}: {e}")
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

    # C-20260919-019: 方向分布哨兵（与 generate_unified_report 同口径）
    if len(results) >= 8:
        from collections import Counter
        dc = Counter(r["result"]["wdl"]["prediction"] for r in results)
        top_dir, top_n = dc.most_common(1)[0]
        if top_n / len(results) >= 0.85:
            print(f"\n🚨 [方向告警] {len(results)} 场中 {top_dir} 占 {top_n} 场 "
                  f"({top_n/len(results):.0%})，分布极端异常（疑似主客反转），请人工核对！")

    print("\n" + "=" * 70)
    print(f"预测完成! 共 {len(results)} 场")


if __name__ == "__main__":
    main()
