# -*- coding: utf-8 -*-
"""
matches 赛果传播回填（C-20261018-137）
=====================================
背景：matches 表存在同一场物理比赛的多个键行——canonical 中文行（如
2026-10-17_威尼斯_那不勒斯）与英文/别名/日期漂移行（如 2026-10-19_Venezia_SSC Napoli）。
赛果接口回填只命中中文 canonical 行，英文漂移行 actual_score 永空，
报告链兜底枚举（actual_score 空=未赛）据此把已完赛场次误判未赛、重复生成错目录件。

本脚本做**赛果向传播**：对每个空赛果行，按「队名双重归一 + 日期 ±3 天内」
找同对阵已填赛果行，把 actual_score / actual_wdl / actual_total_goals 复制过来，
actual_handicap 用本行自己的 handicap 重算（不照抄，防盘口口径差异）。

安全性：
  - 默认 dry-run（只打印不写库）；--apply 才落库。
  - 只更新空赛果行，绝不覆盖已有赛果（坏数据永不覆盖好数据同族守卫）。
  - 只接受赛果行 match_date <= 今天 作为源（未来场无赛果可抄）。
  - 同对阵归一后 ±3 天窗口：五大联赛同主客对阵 3 天内双赛现实中不存在。

用法：
  python scripts/backfill_match_results_propagate.py            # dry-run
  python scripts/backfill_match_results_propagate.py --apply    # 落库
"""
import argparse
import sqlite3
import sys
from datetime import date, datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"

sys.path.insert(0, str(PROJECT_ROOT / "scripts"))
try:
    from feature_utils import normalize_team_name as _norm
except Exception:
    _norm = lambda s: (s or "").strip()

WINDOW_DAYS = 3


def _days(d1: str, d2: str) -> int:
    return abs((datetime.strptime(d1, "%Y-%m-%d") - datetime.strptime(d2, "%Y-%m-%d")).days)


def _derive_from_score(full_score: str):
    """从比分推导 (actual_wdl, actual_total_goals)；无效场次/不可解析返回 (None, None)。"""
    if not full_score or ":" not in full_score:
        return None, None
    try:
        h, a = map(int, full_score.split(":"))
        wdl = "胜" if h > a else ("平" if h == a else "负")
        return wdl, h + a
    except Exception:
        return None, None


def _actual_handicap(handicap, full_score: str) -> str:
    """与 sporttery_collector.compute_actual_handicap 同逻辑。"""
    if not handicap or not full_score or ":" not in full_score:
        return ""
    try:
        h, a = map(int, full_score.split(":"))
        h_adj = h + handicap
        sign = "+" if handicap > 0 else ""
        hstr = f"({sign}{int(handicap)})"
        if h_adj > a:
            return f"{hstr}胜"
        if h_adj == a:
            return f"{hstr}平"
        return f"{hstr}负"
    except Exception:
        return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际落库（默认 dry-run）")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    today = date.today().strftime("%Y-%m-%d")

    scored = conn.execute(
        "SELECT match_id, match_date, home_team, away_team, actual_score, actual_wdl,"
        " actual_total_goals FROM matches"
        " WHERE actual_score IS NOT NULL AND actual_score != '' AND match_date <= ?",
        (today,),
    ).fetchall()
    empty = conn.execute(
        "SELECT match_id, match_date, home_team, away_team, handicap FROM matches"
        " WHERE actual_score IS NULL OR actual_score = ''"
    ).fetchall()

    # 源索引：归一 (home, away) -> 赛果行列表
    src = {}
    for r in scored:
        key = (_norm(r["home_team"]), _norm(r["away_team"]))
        src.setdefault(key, []).append(r)

    plans = []
    for r in empty:
        key = (_norm(r["home_team"]), _norm(r["away_team"]))
        cands = [s for s in src.get(key, []) if _days(s["match_date"], r["match_date"]) <= WINDOW_DAYS]
        if not cands:
            continue
        best = min(cands, key=lambda s: _days(s["match_date"], r["match_date"]))
        plans.append((r, best))

    print(f"源赛果行 {len(scored)}，空赛果行 {len(empty)}，可传播 {len(plans)} 行")
    for r, s in plans:
        print(f"  {r['match_id']}  <=  {s['match_id']}  score={s['actual_score']} wdl={s['actual_wdl']}")

    if not args.apply:
        print("dry-run，未落库（--apply 生效）")
        conn.close()
        return

    n = 0
    cur = conn.cursor()
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for r, s in plans:
        # wdl/总进球优先从比分现推（源行 wdl 可能为空，如 2026-09-19 拜仁 7:0 行）；
        # 比分不可解析（如「无效场次」）才回退源行原值
        d_wdl, d_total = _derive_from_score(s["actual_score"])
        wdl = d_wdl if d_wdl else s["actual_wdl"]
        total = d_total if d_total is not None else s["actual_total_goals"]
        ahcp = _actual_handicap(r["handicap"], s["actual_score"])
        cur.execute(
            "UPDATE matches SET actual_score=?, actual_wdl=?, actual_total_goals=?,"
            " actual_handicap=?, updated_at=? WHERE match_id=?"
            " AND (actual_score IS NULL OR actual_score = '')",  # 双保险：不覆盖已填行
            (s["actual_score"], wdl, total, ahcp, now, r["match_id"]),
        )
        n += cur.rowcount
    conn.commit()
    conn.close()
    print(f"已回填 {n} 行")


if __name__ == "__main__":
    main()
