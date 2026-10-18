# -*- coding: utf-8 -*-
"""C-20261018 复盘统计：子模型 WDL 三指标（方向命中 / RPS / 分桶校准）

只读分析：解析 docs/prematch_reports 下 228+ 份赛前报告 MD 的「子模型原始输出」表
（子模型概率不落库，MD 为唯一均匀来源，lineage 审计已确认），join matches 赛果，
输出 8 系列（5 子模型 + 简单平均 + Stacking 融合 + 市场去水）对照。
市场口径：报告 §二 竞彩赔率三向 1/odds 去水（C-009 同口径）。
"""
import glob
import os
import re
import sqlite3
import sys
from collections import defaultdict

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    from team_names import normalize_team_name as norm
except Exception:
    def norm(x):
        return x

REPORT_GLOB = os.path.join(BASE, "docs", "prematch_reports", "*", "*.md")
DB = os.path.join(BASE, "data", "odds.db")
OUT = os.path.join(BASE, "reports", "submodel_wdl_calibration_20261018.md")

MODELS = ["Dixon-Coles", "XGBoost", "LightGBM", "Elo", "贝叶斯"]
NAME_PAT = {
    "Dixon-Coles": r"Dixon-?Coles",
    "XGBoost": r"XGBoost",
    "LightGBM": r"LightGBM",
    "Elo": r"Elo(?:\s*Rating)?",
    "贝叶斯": r"贝叶斯(?:层级)?",
    "Stacking": r"\**\s*Stacking\s*融合\s*\**",
}
ROW_PAT = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*\*?\*?(?P<h>\d+(?:\.\d+)?)%\*?\*?\s*\|"
    r"\s*\*?\*?(?P<d>\d+(?:\.\d+)?)%\*?\*?\s*\|\s*\*?\*?(?P<a>\d+(?:\.\d+)?)%\*?\*?")
FN_PAT = re.compile(r"^(.+?)_(\d{4}-\d{2}-\d{2})_(.+?)_vs_(.+?)\.md$")
# §二 表：| 主胜 | 60.1% | 1.60 | 55.3% | ... → 取竞彩赔率列
ODDS_PAT = re.compile(
    r"^\|\s*(主胜|平局|客胜)\s*\|\s*[\d.]+%\s*\|\s*(\d+(?:\.\d+)?)\s*\|")


def parse_report(path):
    """返回 (submodels{model:(h,d,a)}, market_odds(h,d,a) or None)"""
    sub, odds = {}, {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = ROW_PAT.match(line.strip())
            if m:
                nm = m.group("name").strip()
                for key, pat in NAME_PAT.items():
                    if re.fullmatch(pat, nm):
                        sub[key] = tuple(float(m.group(g)) / 100.0 for g in ("h", "d", "a"))
                        break
            o = ODDS_PAT.match(line.strip())
            if o:
                odds[o.group(1)] = float(o.group(2))
    mkt = None
    if len(odds) == 3 and all(v > 1 for v in odds.values()):
        inv = {k: 1.0 / v for k, v in odds.items()}
        s = sum(inv.values())
        mkt = (inv["主胜"] / s, inv["平局"] / s, inv["客胜"] / s)
    return sub, mkt


def rps(p, actual_idx):
    o = [0.0, 0.0, 0.0]
    o[actual_idx] = 1.0
    return (((p[0] - o[0]) ** 2) + ((p[0] + p[1] - o[0] - o[1]) ** 2)) / 2.0


def main():
    # 1) 收集报告（去重：正本=目录日期==文件名日期 优先）
    recs = {}
    for f in glob.glob(REPORT_GLOB):
        if f.endswith("_summary.md"):
            continue
        m = FN_PAT.match(os.path.basename(f))
        if not m:
            continue
        lg, dt, h, a = m.groups()
        key = (dt, h, a)
        dird = os.path.basename(os.path.dirname(f))
        if key not in recs or dird == dt.replace("-", ""):
            recs[key] = (lg, f)

    # 2) join 赛果
    conn = sqlite3.connect(DB)
    db = {}
    for d, h, a, sc in conn.execute(
            "SELECT match_date, home_team, away_team, actual_score FROM matches "
            "WHERE actual_score IS NOT NULL AND actual_score!=''"):
        db.setdefault((d, norm(h), norm(a)), sc)
    conn.close()

    # 3) 逐场解析 + 建样本
    series = MODELS + ["简单平均", "Stacking", "市场去水"]
    stat = {s: {"n": 0, "hit": 0, "rps": [], "league": defaultdict(lambda: [0, 0]),
                "bucket": defaultdict(lambda: [0, 0.0, 0]),
                "pred_dir": [0, 0, 0]} for s in series}
    actual_dir = [0, 0, 0]
    elo_dir_stat = [[0, 0], [0, 0], [0, 0]]
    mkt_dir_stat = [[0, 0], [0, 0], [0, 0]]
    elo_month = defaultdict(lambda: [0, 0])
    disagree = [0, 0, 0]  # elo对 / 市场对 / 皆错
    n_join, n_parse_any = 0, 0
    for (dt, h, a), (lg, f) in sorted(recs.items()):
        sc = db.get((dt, norm(h), norm(a)))
        if not sc:
            continue
        sm = re.match(r"^\s*(\d+)\s*[:：-]\s*(\d+)", sc)
        if not sm:
            continue
        hg, ag = int(sm.group(1)), int(sm.group(2))
        actual = 0 if hg > ag else (1 if hg == ag else 2)
        actual_dir[actual] += 1
        n_join += 1
        sub, mkt = parse_report(f)
        probs = dict(sub)
        if len(sub) >= 2:
            probs["简单平均"] = tuple(sum(x[i] for x in sub.values()) / len(sub) for i in range(3))
        if mkt:
            probs["市场去水"] = mkt
        if probs:
            n_parse_any += 1
        for sname, p in probs.items():
            if sname not in stat or any(v <= 0 for v in p):
                continue
            st = stat[sname]
            st["n"] += 1
            pred = max(range(3), key=lambda i: p[i])
            hit = int(pred == actual)
            st["hit"] += hit
            st["rps"].append(rps(p, actual))
            st["league"][lg][0] += hit
            st["league"][lg][1] += 1
            st["pred_dir"][pred] += 1
            pc = p[pred]
            b = "0.4-0.5" if 0.4 <= pc < 0.5 else ("0.5-0.6" if 0.5 <= pc < 0.6 else ("0.6+" if pc >= 0.6 else "<0.4"))
            bk = st["bucket"][b]
            bk[0] += 1
            bk[1] += pc
            bk[2] += hit
        # Elo × 市场 证伪检查专项
        if "Elo" in probs and "市场去水" in probs:
            ep = max(range(3), key=lambda i: probs["Elo"][i])
            mp = max(range(3), key=lambda i: probs["市场去水"][i])
            elo_dir_stat[ep][1] += 1
            elo_dir_stat[ep][0] += int(ep == actual)
            mkt_dir_stat[mp][1] += 1
            mkt_dir_stat[mp][0] += int(mp == actual)
            em = elo_month[dt[:7]]
            em[1] += 1
            em[0] += int(ep == actual)
            if ep != mp:
                disagree[0] += int(ep == actual)
                disagree[1] += int(mp == actual)
                disagree[2] += int(ep != actual and mp != actual)

    # 4) 输出
    L = []
    L.append("# 子模型 WDL 复盘统计（方向命中 / RPS / 分桶校准）\n")
    L.append("> 生成: analyze_submodel_wdl_calibration.py | 样本: 报告 {} 份去重 → join 赛果 {} 场（解析到子模型表 {} 场）".format(len(recs), n_join, n_parse_any))
    L.append("> 数据源: docs/prematch_reports MD（子模型概率不落库，MD 唯一均匀来源）× matches.actual_score；市场=报告内竞彩赔率 1/odds 去水（C-009 口径）\n")

    L.append("## 一、总览（方向命中率 / 平均 RPS）\n")
    L.append("| 系列 | n | 方向命中率 | 平均 RPS |")
    L.append("|------|--:|:---------:|:-------:|")
    for s in series:
        st = stat[s]
        if st["n"]:
            L.append("| {} | {} | {:.1%} | {:.4f} |".format(s, st["n"], st["hit"] / st["n"], sum(st["rps"]) / len(st["rps"])))

    L.append("\n## 二、分联赛方向命中率\n")
    leagues = sorted({lg for s in series for lg in stat[s]["league"]})
    L.append("| 系列 | " + " | ".join(leagues) + " |")
    L.append("|------" + "|------:" * len(leagues) + "|")
    for s in series:
        row = []
        for lg in leagues:
            hh, nn = stat[s]["league"].get(lg, [0, 0])
            row.append("{:.1%} ({})".format(hh / nn, nn) if nn else "—")
        L.append("| {} | ".format(s) + " | ".join(row) + " |")

    L.append("\n## 二点五、方向分布对照（argmax 主/平/客 占比 vs 实际赛果分布）\n")
    L.append("> 用途：区分「真方向感」与「主场偏置」——若某系列 argmax 主胜占比畸高且样本主胜率高，命中率高可能是偏置红利\n")
    tot = sum(actual_dir)
    L.append("| 系列 | argmax 主胜 | argmax 平 | argmax 客胜 |")
    L.append("|------|:----------:|:--------:|:----------:|")
    L.append("| **实际赛果** | {:.1%} | {:.1%} | {:.1%} |".format(actual_dir[0] / tot, actual_dir[1] / tot, actual_dir[2] / tot))
    for s in series:
        st = stat[s]
        if st["n"]:
            pd_ = st["pred_dir"]
            L.append("| {} | {:.1%} | {:.1%} | {:.1%} |".format(s, pd_[0] / st["n"], pd_[1] / st["n"], pd_[2] / st["n"]))

    L.append("\n## 三、分桶校准（argmax 置信桶：预测均值 vs 实际命中）\n")
    for s in series:
        st = stat[s]
        if not st["n"]:
            continue
        L.append("### {}\n".format(s))
        L.append("| 桶 | n | 预测均值 | 实际命中 | 差距(实-预) |")
        L.append("|----|--:|:-------:|:-------:|:----------:|")
        for b in ["<0.4", "0.4-0.5", "0.5-0.6", "0.6+"]:
            nn, sp, hh = st["bucket"].get(b, [0, 0.0, 0])
            if nn:
                L.append("| {} | {} | {:.1%} | {:.1%} | {:+.1%} |".format(b, nn, sp / nn, hh / nn, hh / nn - sp / nn))
        L.append("")

    L.append("\n## 四、Elo × 市场 证伪检查专项\n")
    n_d = sum(disagree)
    L.append("- 分方向命中：Elo 主胜 picks {}/{}（{:.1%}）、客胜 picks {}/{}（{:.1%}）；"
             "市场 主胜 picks {}/{}（{:.1%}）、客胜 picks {}/{}（{:.1%}）".format(
                 elo_dir_stat[0][0], elo_dir_stat[0][1],
                 elo_dir_stat[0][0] / max(1, elo_dir_stat[0][1]),
                 elo_dir_stat[2][0], elo_dir_stat[2][1],
                 elo_dir_stat[2][0] / max(1, elo_dir_stat[2][1]),
                 mkt_dir_stat[0][0], mkt_dir_stat[0][1],
                 mkt_dir_stat[0][0] / max(1, mkt_dir_stat[0][1]),
                 mkt_dir_stat[2][0], mkt_dir_stat[2][1],
                 mkt_dir_stat[2][0] / max(1, mkt_dir_stat[2][1])))
    L.append("- 分月命中（Elo）：" + "、".join(
        "{} {:.0%}({})".format(k, v[0] / v[1], v[1]) for k, v in sorted(elo_month.items())))
    L.append("- Elo 与市场方向不一致 {} 场：Elo 对 {}、市场对 {}、皆错 {}".format(n_d, disagree[0], disagree[1], disagree[2]))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fo:
        fo.write("\n".join(L))
    print("n_join=", n_join, "n_parse=", n_parse_any)
    for s in series:
        st = stat[s]
        if st["n"]:
            print("{}: n={} hit={:.1%} rps={:.4f}".format(s, st["n"], st["hit"] / st["n"], sum(st["rps"]) / len(st["rps"])))
    print("OUT:", OUT)


if __name__ == "__main__":
    main()
