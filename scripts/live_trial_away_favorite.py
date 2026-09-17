# -*- coding: utf-8 -*-
"""实盘小注验证 — 客胜热门段（Pre-registered Away-Favorite Trial）

预注册规则（常量固化，禁止中途修改以避免多重比较伪信号）：
  ODDS_CAP    = 2.5    客胜赔率上限（竞彩 win_b）
  EV_MIN      = 0.0    EV > 0（EV = p_away × o_away − 1）
  STAKE_CNY   = 20     平注 ¥20/注
  STOP_AFTER  = 300    累计 300 注强制停止（包括结算后的 W/L 全部计入）
  LOOKAHEAD   = 7 天   只投未来 7 天窗口内的赛事

三源数据桥接（key 构造）：
  1. 赛事清单：odds500_match（season='26/27', status=1, 未来窗口）
  2. 竞彩客胜赔率：wdl_history 末条快照（win_a=主胜 / win_b=客胜）
       cn_key = {match_date}_{normalize(home_team_cn)}_{normalize(away_team_cn)}
  3. 模型客胜概率：model_predictions WDL_away（probability）
       en_key = {match_date}_{home_team_en}_{away_team_en}（保留空格，与库内原样一致）

用法：
  python live_trial_away_favorite.py                      # dry-run：生成投注单报告，不写台账
  python live_trial_away_favorite.py --commit             # 将候选投注写入台账（pending）
  python live_trial_away_favorite.py --settle             # 结算已赛 pending 条目（回填 W/L 与盈亏，
                                                          #   并幂等回写 post_match_review + actual_* 四行）
  python live_trial_away_favorite.py --commit --settle    # 先结算再提交
  python live_trial_away_favorite.py --days 14            # 放宽窗口（默认 7 天）

产物：
  data/live_trial_away_favorite_ledger.csv   台账（去重/状态/盈亏）
  reports/live_trial_slip_<TS>.md            投注单报告（含拒绝原因统计）
  data/odds.db -> post_match_review          结算回写（模块 A1，UNIQUE(match_id) 幂等）
"""
import argparse
import os
import sqlite3
import sys
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
DATA_DIR = PROJECT_DIR / "data"
REPORT_DIR = PROJECT_DIR / "reports"
DB_PATH = DATA_DIR / "odds.db"
LEDGER_PATH = DATA_DIR / "live_trial_away_favorite_ledger.csv"

sys.path.insert(0, str(SCRIPT_DIR))
from feature_utils import normalize_team_name  # noqa: E402
from post_match_schema import (  # noqa: E402
    ensure_post_match_schema,
    write_review_and_actuals,
    DEFAULT_MODEL_NAME,
)

# ── 预注册规则（不可变常量）──────────────────────────────
ODDS_CAP = 2.5          # 客胜赔率上限
EV_MIN = 0.0            # EV 下界（严格 >0）
STAKE_CNY = 20.0        # 平注金额 ¥
STOP_AFTER = 300        # 累计投注停止线
LOOKAHEAD_DAYS = 7      # 未来窗口天数（默认）

LEDGER_COLS = [
    "bet_id", "match_id_en", "match_date", "league",
    "home_en", "away_en", "away_odds", "p_away", "ev",
    "stake_cny", "status", "result", "profit_cny", "slip_ts",
]


# ── 台账读写 ───────────────────────────────────────────
def load_ledger():
    if LEDGER_PATH.exists():
        df = pd.read_csv(LEDGER_PATH, dtype={"bet_id": str})
        for c in LEDGER_COLS:
            if c not in df.columns:
                df[c] = ""
        return df
    return pd.DataFrame(columns=LEDGER_COLS)


def save_ledger(df):
    LEDGER_PATH.parent.mkdir(exist_ok=True)
    df[LEDGER_COLS].to_csv(LEDGER_PATH, index=False, encoding="utf-8-sig")


# ── 数据加载 ───────────────────────────────────────────
def load_odds500_upcoming(conn, today, end_day):
    rows = conn.execute(
        "SELECT match_id, home_team_cn, away_team_cn, home_team_en, away_team_en, "
        "       match_date, league "
        "FROM odds500_match "
        "WHERE season='26/27' AND status=1 AND match_date BETWEEN ? AND ? "
        "ORDER BY match_date, match_time",
        (today, end_day)).fetchall()
    return [
        {"match_id": r[0], "home_cn": r[1], "away_cn": r[2],
         "home_en": r[3], "away_en": r[4], "match_date": r[5], "league": r[6]}
        for r in rows
    ]


def load_wdl_last(conn):
    """wdl_history 每 match_id 末条快照 -> 双索引：
    {归一化中文 key: (win_a, draw, win_b)} 精确匹配；
    {(norm_home, norm_away): {date: odds_tuple}} 日期容差匹配（竞彩官方日 vs 当地日，±1 天）。"""
    rows = conn.execute(
        "SELECT match_id, win_a, draw, win_b, timestamp FROM wdl_history "
        "WHERE win_a > 1 AND win_b > 1 AND draw > 1 "
        "ORDER BY match_id, timestamp").fetchall()
    by_mid = {}
    for mid, wa, dr, wb, ts in rows:
        by_mid[mid] = (wa, dr, wb)
    out, pair = {}, {}
    for mid, (wa, dr, wb) in by_mid.items():
        parts = mid.split("_", 1)
        if len(parts) < 2 or "_" not in parts[1]:
            continue
        date, rest = parts[0], parts[1]
        h, a = rest.rsplit("_", 1)
        nh, na = normalize_team_name(h), normalize_team_name(a)
        out[f"{date}_{nh}_{na}"] = (wa, dr, wb)
        pair.setdefault((nh, na), {})[date] = (wa, dr, wb)
    return out, pair


def load_pred_away(conn):
    """model_predictions WDL_away 最新一条 -> {en match_id: p_away}"""
    rows = conn.execute(
        "SELECT match_id, probability, timestamp FROM model_predictions "
        "WHERE prediction_type='WDL_away' ORDER BY match_id, timestamp").fetchall()
    out = {}
    for mid, p, ts in rows:
        out[mid] = p
    return out


# ── 结算 ───────────────────────────────────────────────
def settle_pending(ledger, conn):
    """对 status='pending' 条目，用 odds500_match 已赛比分回填 result/profit，
    并把实际赛果（actual_score/wdl/tg）幂等回写 post_match_review + 4 行 actual_*。

    post_match_review.match_id 使用本脚本的 match_id_en（{日期}_{主队_en}_{客队_en}），
    与 A2 post-match 采集链路同键，UNIQUE(match_id) 下 INSERT OR IGNORE 幂等。
    """
    ensure_post_match_schema(conn)
    n_done = 0
    n_written = 0
    for i, row in ledger.iterrows():
        if row["status"] != "pending":
            continue
        mid = row["match_id_en"]
        r = conn.execute(
            "SELECT home_score, away_score, status FROM odds500_match "
            "WHERE match_id=? AND status=5", (mid,)).fetchone()
        if r is None:
            continue
        hs, as_, st = r
        if hs is None or as_ is None:
            continue
        won = as_ > hs
        odds = float(row["away_odds"])
        stake = float(row["stake_cny"])
        profit = round((odds - 1.0) * stake, 2) if won else round(-stake, 2)
        ledger.at[i, "status"] = "settled"
        ledger.at[i, "result"] = "W" if won else "L"
        ledger.at[i, "profit_cny"] = profit

        # 幂等回写赛后事实（模块 A1，UNIQUE(match_id)）
        actual_wdl = "客胜" if as_ > hs else ("平局" if as_ == hs else "主胜")
        actual_tg = int(hs) + int(as_)
        try:
            res = write_review_and_actuals(
                conn,
                {
                    "match_id": mid,
                    "league": row.get("league"),
                    "match_date": row.get("match_date"),
                    "home_team": row.get("home_en"),
                    "away_team": row.get("away_en"),
                    "actual_score": f"{hs}:{as_}",
                    "actual_wdl": actual_wdl,
                    "actual_tg": actual_tg,
                },
                model_name=DEFAULT_MODEL_NAME,
            )
            n_written += res["review"] + res["actuals"]
        except Exception as e:  # 回写失败不阻断结算流程
            print(f"[settle] 回写 post_match_review 失败 {mid}: {e}")
        n_done += 1
    if n_written:
        print(f"[settle] 回写 post_match_review（review 计入 + actual_* 行）共 {n_written} 行")
    return n_done


# ── 投注单生成 ─────────────────────────────────────────
def _candidate_dates(mdate):
    """竞彩官方日 vs 500.com 当地日允许 ±1 天错位。"""
    cands = {mdate}
    try:
        d = datetime.strptime(mdate, "%Y-%m-%d")
        cands.add((d - timedelta(days=1)).strftime("%Y-%m-%d"))
        cands.add((d + timedelta(days=1)).strftime("%Y-%m-%d"))
    except ValueError:
        pass
    return cands


def _match_away_odds(m, wdl, wdl_pair):
    """返回 (win_a, draw, win_b)；先精确 key，再按队名 + 日期容差。"""
    cn_key = f"{m['match_date']}_{normalize_team_name(m['home_cn'])}_{normalize_team_name(m['away_cn'])}"
    if cn_key in wdl:
        return wdl[cn_key]
    pair_key = (normalize_team_name(m["home_cn"]), normalize_team_name(m["away_cn"]))
    by_date = wdl_pair.get(pair_key)
    if not by_date:
        return None
    for cd in _candidate_dates(m["match_date"]):
        if cd in by_date:
            return by_date[cd]
    return None


def build_slip(upcoming, wdl, pred, ledger, today):
    wdl, wdl_pair = wdl
    existing = set(ledger["match_id_en"].astype(str)) if len(ledger) else set()
    n_committed = int((ledger["status"].isin(["pending", "settled"])).sum()) if len(ledger) else 0

    bets, rejected = [], {"no_wdl": 0, "no_pred": 0, "odds>cap": 0,
                          "ev<=0": 0, "dup": 0, "stop": 0}
    for m in upcoming:
        if n_committed >= STOP_AFTER:
            rejected["stop"] += 1
            continue
        en_key = f"{m['match_date']}_{m['home_en']}_{m['away_en']}"
        odds3 = _match_away_odds(m, wdl, wdl_pair)
        if odds3 is None:
            rejected["no_wdl"] += 1
            continue
        o_away = float(odds3[2])
        if en_key not in pred:
            rejected["no_pred"] += 1
            continue
        p_away = float(pred[en_key])
        if not (0.0 < p_away < 1.0):
            rejected["no_pred"] += 1
            continue
        if o_away > ODDS_CAP:
            rejected["odds>cap"] += 1
            continue
        ev = p_away * o_away - 1.0
        if ev <= EV_MIN:
            rejected["ev<=0"] += 1
            continue
        if m["match_id"] in existing:
            rejected["dup"] += 1
            continue
        bets.append({
            "match_id_en": m["match_id"],
            "match_date": m["match_date"],
            "league": m["league"],
            "home_en": m["home_en"],
            "away_en": m["away_en"],
            "away_odds": round(o_away, 3),
            "p_away": round(p_away, 4),
            "ev": round(ev, 5),
            "stake_cny": STAKE_CNY,
        })
        n_committed += 1
    return bets, rejected, n_committed


# ── 报告 ───────────────────────────────────────────────
def write_report(bets, rejected, ledger, run_ts, mode, n_committed):
    REPORT_DIR.mkdir(exist_ok=True)
    path = REPORT_DIR / f"live_trial_slip_{run_ts}.md"
    lines = [
        "# 实盘小注验证 — 客胜热门段投注单（Pre-registered）",
        "",
        f"**生成时间**: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
        f"**模式**: {mode}",
        f"**规则**: 客胜赔率≤{ODDS_CAP} / EV>{EV_MIN} / 平注¥{STAKE_CNY:.0f} / 累计{STOP_AFTER}注停止",
        "",
        "## 一、本期投注单",
        "",
        "| # | 日期 | 联赛 | 主队 | 客队 | 客胜赔率 | p_away | EV |",
        "|---|------|------|------|------|---------|--------|-----|",
    ]
    for i, b in enumerate(bets, 1):
        lines.append(
            f"| {i} | {b['match_date']} | {b['league']} | {b['home_en']} | {b['away_en']} "
            f"| {b['away_odds']} | {b['p_away']:.4f} | {b['ev']:+.3f} |")
    lines += ["", "## 二、拒绝原因统计", "", "| 原因 | 场次 |", "|------|------|"]
    for k, v in rejected.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## 三、台账进度", "",
              f"- 累计已投注（pending+settled）: **{n_committed} / {STOP_AFTER}** 注"]
    if len(ledger):
        settled = ledger[ledger["status"] == "settled"]
        if len(settled):
            total_stake = len(settled) * STAKE_CNY
            total_profit = settled["profit_cny"].astype(float).sum()
            wins = int((settled["result"] == "W").sum())
            lines += [
                f"- 已结算: **{len(settled)}** 注（胜 {wins} / 负 {len(settled)-wins}）",
                f"- 累计投入: ¥{total_stake:.2f} / 累计盈亏: **¥{total_profit:+.2f}**",
                f"- 平注 ROI: **{total_profit/total_stake*100:+.2f}%**",
            ]
    lines.append("")
    if mode.startswith("dry"):
        lines.append("> ⚠️ dry-run：本期候选未写入台账。确认后运行 `--commit` 提交。")
    text = "\n".join(lines)
    path.write_text(text, encoding="utf-8")
    return path


# ── 主流程 ─────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="实盘小注验证 — 客胜热门段投注单与台账")
    parser.add_argument("--days", type=int, default=LOOKAHEAD_DAYS,
                        help=f"未来窗口天数（默认 {LOOKAHEAD_DAYS}）")
    parser.add_argument("--commit", action="store_true", help="将候选投注写入台账")
    parser.add_argument("--settle", action="store_true", help="结算已赛 pending 条目")
    parser.add_argument("--no-report", action="store_true", help="不生成 markdown 报告")
    args = parser.parse_args()

    RUN_TS = datetime.now().strftime("%Y%m%d_%H%M%S")
    today = datetime.now().strftime("%Y-%m-%d")
    end_day = (datetime.now() + timedelta(days=args.days)).strftime("%Y-%m-%d")

    ledger = load_ledger()
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("PRAGMA busy_timeout=5000")

    # 1) 先结算（可选）
    if args.settle:
        n_settled = settle_pending(ledger, conn)
        if n_settled:
            save_ledger(ledger)
        print(f"[settle] 结算 {n_settled} 笔 → 台账已更新")
        if not args.commit and args.no_report:
            conn.close()
            return

    # 2) 生成投注单
    upcoming = load_odds500_upcoming(conn, today, end_day)
    wdl_pair = load_wdl_last(conn)
    pred = load_pred_away(conn)
    print(f"[1/3] 未来窗口赛事: {len(upcoming)} 场（{today} ~ {end_day}）")
    print(f"[2/3] 竞彩末条: {len(wdl_pair[0])} 场 / 模型预测 WDL_away: {len(pred)} 场")

    bets, rejected, n_committed = build_slip(upcoming, wdl_pair, pred, ledger, today)
    mode = "commit" if args.commit else "dry-run"
    print(f"[3/3] 候选投注: {len(bets)} 笔 | 拒绝: {rejected}")
    print(f"      台账累计: {n_committed}/{STOP_AFTER} 注")
    for i, b in enumerate(bets, 1):
        print(f"  {i:>2}. {b['match_date']} {b['league']:>4} {b['home_en']:<22} vs {b['away_en']:<22} "
              f"客胜{b['away_odds']:.2f} p={b['p_away']:.4f} EV={b['ev']:+.3f}")

    # 3) 提交台账（可选）
    if args.commit and bets:
        slip_ts = datetime.now().isoformat(timespec="seconds")
        rows = []
        for i, b in enumerate(bets):
            rows.append({
                "bet_id": f"LT-{RUN_TS}-{i+1:03d}",
                **b,
                "status": "pending",
                "result": "",
                "profit_cny": "",
                "slip_ts": slip_ts,
            })
        new_df = pd.DataFrame(rows, columns=LEDGER_COLS)
        out = pd.concat([ledger, new_df], ignore_index=True) if len(ledger) else new_df
        save_ledger(out)
        ledger = out
        print(f"[commit] 已写入台账 {len(rows)} 笔 → {LEDGER_PATH}")

    # 4) 报告
    if not args.no_report:
        path = write_report(bets, rejected, ledger, RUN_TS, mode, n_committed)
        print(f"[report] {path}")

    conn.close()


if __name__ == "__main__":
    main()
