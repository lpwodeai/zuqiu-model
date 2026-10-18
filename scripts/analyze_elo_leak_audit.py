# -*- coding: utf-8 -*-
"""C-20261018 Elo 泄漏排查（只读）——C-142 §五-3 挂账项

排查规格（裁决书）：as-of 口径核对 + 快照保真比对 + 月衰减归因；期间 C-013 γ 不动。

方法：
  1) 保真校验：按 deploy_t005v2_final.build_team_elo_snapshot 同款口径重放全量 matches
     （实际赛果、match_date 排序、(日期,主,客) 去重、K=32/HA=65、动量=近5场 Elo 值差），
     与 assets/t005v2_final_elo_ratings.json 对拍，验证重放引擎忠实。
  2) 反演：生产 Elo→WDL（prediction_core.calc_win_draw_lose_elo）满足 w/l = 10^(d/400)，
     由报告 Elo 行精确反演 d_md = 400*log10(w/l)；平局列做内部一致性残差校验。
  3) lag 指纹：对每个截止 k∈[-3,+3] 天（相对比赛日），重放 as-of d_k = (h_elo-a_elo)+(mom_h-mom_a)
     （严格 date < 比赛日+k），取 k* = argmin|d_md - d_k|。k*=0/-1 = 赛前合法状态；
     k*≥+1 = 含比赛日及之后结果（可能含本场自身）→ 泄漏指纹。
  4) 泄漏指向：Δd = d_md - d_asof(date<比赛日) 与赛果符号的相关；MD-Elo vs as-of-Elo vs
     市场去水 的方向命中率对照（总体 + 分月衰减）。

裁决门槛（预承诺）：
  - k*≥+1 占比 > 20% → 污染系统性存在
  - corr(Δd, 赛果符号) > 0.15 → 方向性泄漏
  - MD 命中 - asof 命中 > 3pp → 优势部分为假象
"""
import glob
import json
import math
import os
import re
import sqlite3
import sys
from bisect import bisect_left
from collections import defaultdict
from datetime import date, timedelta

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from team_names import normalize_team_name as norm  # noqa: E402
from elo_rating import DEFAULT_ELO, K_FACTOR, HOME_ADVANTAGE, update_elo  # noqa: E402

REPORT_GLOB = os.path.join(BASE, "docs", "prematch_reports", "*", "*.md")
DB = os.path.join(BASE, "data", "odds.db")
JSON_PATH = os.path.join(BASE, "assets", "t005v2_final_elo_ratings.json")
OUT = os.path.join(BASE, "reports", "elo_leak_audit_20261018.md")

FN_PAT = re.compile(r"^(.+?)_(\d{4}-\d{2}-\d{2})_(.+?)_vs_(.+?)\.md$")
GEN_PAT = re.compile(r"生成时间\*\*:\s*(\d{4}-\d{2}-\d{2})\s+(\d{2}):(\d{2})")
ROW_PAT = re.compile(
    r"^\|\s*(?P<name>[^|]+?)\s*\|\s*\*?\*?(?P<h>\d+(?:\.\d+)?)%\*?\*?\s*\|"
    r"\s*\*?\*?(?P<d>\d+(?:\.\d+)?)%\*?\*?\s*\|\s*\*?\*?(?P<a>\d+(?:\.\d+)?)%\*?\*?")
ELO_NAME = re.compile(r"Elo(?:\s*Rating)?")
ODDS_PAT = re.compile(r"^\|\s*(主胜|平局|客胜)\s*\|\s*[\d.]+%\s*\|\s*(\d+(?:\.\d+)?)\s*\|")


def parse_score(s):
    s = str(s).strip()
    if "其它" in s or s == "":
        return None
    for sep in [":", "-"]:
        if sep in s:
            p = s.split(sep)
            try:
                return int(p[0]), int(p[1])
            except ValueError:
                return None
    return None


def replay_elo():
    """重放生产口径 Elo；返回 team_hist{canon:[(date_str, elo_after)]} 与赛果库。"""
    conn = sqlite3.connect(DB)
    raw = conn.execute(
        "SELECT match_date, home_team, away_team, actual_score FROM matches "
        "WHERE actual_score IS NOT NULL AND actual_score != '' ORDER BY match_date").fetchall()
    conn.close()
    seen, rows, results = set(), [], {}
    for d, h, a, sc in raw:
        hn, an = norm(h), norm(a)
        key = (d, hn, an)
        if key in seen:
            continue
        seen.add(key)
        g = parse_score(sc)
        if g is None:
            continue
        rows.append((d, hn, an, g))
        results.setdefault(key, g)
    ratings, hist = {}, defaultdict(list)
    for d, hn, an, (hg, ag) in rows:
        eh = ratings.get(hn, DEFAULT_ELO)
        ea = ratings.get(an, DEFAULT_ELO)
        s = 1.0 if hg > ag else (0.5 if hg == ag else 0.0)
        nh, na = update_elo(eh, ea, s, K_FACTOR, HOME_ADVANTAGE)
        ratings[hn], ratings[an] = nh, na
        hist[hn].append((d, nh))
        hist[an].append((d, na))
    return hist, results


def asof_lookup(hist, team, cutoff):
    """严格 date < cutoff 的 (elo, momentum)；动量=近5场 elo 值差（≥5 场否则 0）。"""
    h = hist.get(team)
    if not h:
        return DEFAULT_ELO, 0.0
    dates = [x[0] for x in h]
    i = bisect_left(dates, cutoff)
    if i == 0:
        return DEFAULT_ELO, 0.0
    elo = h[i - 1][1]
    if i >= 5:
        mom = h[i - 1][1] - h[i - 5][1]
    else:
        mom = 0.0
    return elo, mom


def d_from_probs(w, l):
    return 400.0 * math.log10(w / l) if w > 0 and l > 0 else None


def dir_from_d(d):
    ph = 1.0 / (1.0 + 10 ** (-d / 400.0))
    pd_ = max(0.20, 0.30 - abs(d) / 2000.0)
    pa = 1.0 - ph
    return max(range(3), key=lambda i: (ph, pd_, pa)[i])


def main():
    hist, results = replay_elo()

    # 1) 保真校验 vs 当前 JSON
    json_ok = "未找到 JSON"
    if os.path.exists(JSON_PATH):
        with open(JSON_PATH, encoding="utf-8") as f:
            snap = json.load(f)
        je, jm = snap.get("elo_ratings", {}), snap.get("elo_momentum", {})
        canon_json = defaultdict(list)
        for k, v in je.items():
            canon_json[norm(k)].append(v)
        canon_jm = defaultdict(list)
        for k, v in jm.items():
            canon_jm[norm(k)].append(v)
        de, dm, ncmp = [], [], 0
        today = date.today().isoformat()
        for team in set(list(canon_json.keys())[:]):
            elo_r, mom_r = asof_lookup(hist, team, (date.today() + timedelta(days=1)).isoformat())
            if team in canon_json:
                de.append(abs(sum(canon_json[team]) / len(canon_json[team]) - elo_r))
                ncmp += 1
            if team in canon_jm:
                dm.append(abs(sum(canon_jm[team]) / len(canon_jm[team]) - mom_r))
        json_ok = ("对拍 {} 队：|Δelo| 均值 {:.2f} / 最大 {:.2f}；|Δ动量| 均值 {:.2f} / 最大 {:.2f}"
                   .format(ncmp, sum(de) / max(1, len(de)), max(de) if de else -1,
                           sum(dm) / max(1, len(dm)), max(dm) if dm else -1))

    # 2) 扫描 MD
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

    samples = []
    for (dt, h, a), (lg, f) in sorted(recs.items()):
        g = results.get((dt, norm(h), norm(a)))
        if g is None:
            continue
        hg, ag = g
        sign = 1 if hg > ag else (0 if hg == ag else -1)
        actual = 0 if hg > ag else (1 if hg == ag else 2)
        elo_p, odds, gen = None, {}, None
        with open(f, encoding="utf-8") as fo:
            for line in fo:
                ls = line.strip()
                m = ROW_PAT.match(ls)
                if m and ELO_NAME.fullmatch(m.group("name").strip()):
                    elo_p = tuple(float(m.group(x)) / 100.0 for x in ("h", "d", "a"))
                o = ODDS_PAT.match(ls)
                if o:
                    odds[o.group(1)] = float(o.group(2))
                if gen is None:
                    gm = GEN_PAT.search(ls)
                    if gm:
                        gen = (gm.group(1), int(gm.group(2)))
        if elo_p is None or elo_p[0] <= 0 or elo_p[2] <= 0:
            continue
        d_md = d_from_probs(elo_p[0], elo_p[2])
        # 内部一致性：由 d_md 推平局概率 vs MD 平局列
        ph = 1.0 / (1.0 + 10 ** (-d_md / 400.0))
        pdr = max(0.20, 0.30 - abs(d_md) / 2000.0)
        draw_expect = pdr / (1.0 + pdr)
        draw_resid = abs(draw_expect - elo_p[1])
        # lag 指纹
        md_date = date.fromisoformat(dt)
        d_k = {}
        for k in range(-3, 4):
            cut = (md_date + timedelta(days=k)).isoformat()
            eh, mh = asof_lookup(hist, norm(h), cut)
            ea, ma = asof_lookup(hist, norm(a), cut)
            d_k[k] = (eh - ea) + (mh - ma)
        k_star = min(d_k, key=lambda k: abs(d_md - d_k[k]))
        d_asof = d_k[0]  # date < 比赛日：赛前合法口径
        mkt = None
        if len(odds) == 3 and all(v > 1 for v in odds.values()):
            inv = {k2: 1.0 / v for k2, v in odds.items()}
            s = sum(inv.values())
            mkt = (inv["主胜"] / s, inv["平局"] / s, inv["客胜"] / s)
        gen_date, gen_hour = gen if gen else (None, None)
        if gen_date is None:
            bucket = "未知"
        elif gen_date > dt:
            bucket = "跨日重生成"
        elif gen_hour < 16:
            bucket = "午间生成"
        else:
            bucket = "晚间生成"
        samples.append({"dt": dt, "lg": lg, "h": h, "a": a, "sign": sign, "actual": actual,
                        "d_md": d_md, "d_asof": d_asof, "k_star": k_star, "bucket": bucket,
                        "draw_resid": draw_resid, "elo_dir": max(range(3), key=lambda i: elo_p[i]),
                        "asof_dir": dir_from_d(d_asof),
                        "mkt_dir": max(range(3), key=lambda i: mkt[i]) if mkt else None})

    n = len(samples)
    # 3) 聚合
    lag_hist = defaultdict(int)
    lag_by_bucket = defaultdict(lambda: defaultdict(int))
    for s in samples:
        lag_hist[s["k_star"]] += 1
        lag_by_bucket[s["bucket"]][s["k_star"]] += 1
    leak_share = sum(v for k, v in lag_hist.items() if k >= 1) / max(1, n)

    dd = [s["d_md"] - s["d_asof"] for s in samples]
    sg = [s["sign"] for s in samples]
    mean_dd = sum(dd) / max(1, n)
    if n > 2:
        m1, m2 = mean_dd, sum(sg) / n
        cov = sum((dd[i] - m1) * (sg[i] - m2) for i in range(n)) / n
        sd1 = math.sqrt(sum((x - m1) ** 2 for x in dd) / n)
        sd2 = math.sqrt(sum((x - m2) ** 2 for x in sg) / n)
        corr = cov / (sd1 * sd2) if sd1 > 0 and sd2 > 0 else 0.0
    else:
        corr = 0.0
    dd_by_sign = defaultdict(list)
    for s in samples:
        dd_by_sign[s["sign"]].append(s["d_md"] - s["d_asof"])

    hit = {"md": 0, "asof": 0, "mkt": 0}
    n_mkt = 0
    month_stat = defaultdict(lambda: [0, 0, 0, 0])  # md对, asof对, mkt对, n
    disagree = [0, 0, 0]
    for s in samples:
        hit["md"] += int(s["elo_dir"] == s["actual"])
        hit["asof"] += int(s["asof_dir"] == s["actual"])
        ms = month_stat[s["dt"][:7]]
        ms[0] += int(s["elo_dir"] == s["actual"])
        ms[1] += int(s["asof_dir"] == s["actual"])
        ms[3] += 1
        if s["mkt_dir"] is not None:
            n_mkt += 1
            hit["mkt"] += int(s["mkt_dir"] == s["actual"])
            ms[2] += int(s["mkt_dir"] == s["actual"])
            if s["elo_dir"] != s["asof_dir"]:
                disagree[0] += int(s["elo_dir"] == s["actual"])
                disagree[1] += int(s["asof_dir"] == s["actual"])
                disagree[2] += int(s["elo_dir"] != s["actual"] and s["asof_dir"] != s["actual"])
    gap = (hit["md"] - hit["asof"]) / max(1, n)

    # 4) 裁决
    v1 = leak_share > 0.20
    v2 = corr > 0.15
    v3 = gap > 0.03
    verdict = "泄漏成立" if (v1 or v2) else ("优势部分为假象" if v3 else "未发现泄漏证据")

    # 5) 输出
    L = []
    L.append("# Elo 泄漏排查（C-142 §五-3 挂账项，只读）\n")
    L.append("> 生成: analyze_elo_leak_audit.py | 样本: MD 去重 join 赛果 {} 场".format(n))
    L.append("> 方法: 反演 d_md=400·log10(w/l) × 重放 as-of Elo（生产同款口径）× lag 指纹\n")
    L.append("## 一、保真校验（重放引擎 vs 当前 JSON 快照）\n")
    L.append("- " + json_ok)
    L.append("\n## 二、lag 指纹（k* = d_md 最贴合的截止日，相对比赛日）\n")
    L.append("| k* | -3 | -2 | -1 | 0 | +1 | +2 | +3 |")
    L.append("|----|" + "---:|" * 7)
    L.append("| 场次数 | " + " | ".join(str(lag_hist.get(k, 0)) for k in range(-3, 4)) + " |")
    L.append("\n分生成时段：\n")
    L.append("| 时段 | n | k*≤0 占比 | k*≥+1 占比 |")
    L.append("|------|--:|----------:|-----------:|")
    for b in ["午间生成", "晚间生成", "跨日重生成", "未知"]:
        bb = lag_by_bucket.get(b)
        if not bb:
            continue
        nb = sum(bb.values())
        lo = sum(v for k, v in bb.items() if k <= 0)
        hi = sum(v for k, v in bb.items() if k >= 1)
        L.append("| {} | {} | {:.1%} | {:.1%} |".format(b, nb, lo / nb, hi / nb))
    L.append("\n- **k*≥+1 总占比: {:.1%}**（含比赛日及之后结果 → 污染指纹；门槛 20%）".format(leak_share))
    L.append("\n## 三、Δd × 赛果（方向性泄漏检验）\n")
    L.append("- Δd = d_md − d_asof：均值 {:+.1f} elo 分".format(mean_dd))
    L.append("- corr(Δd, 赛果符号): **{:+.3f}**（门槛 0.15；>0 = 报告 Elo 差被系统性拉向实际胜方）".format(corr))
    L.append("- 分赛果 Δd 均值：" + "、".join(
        "{} {:+.1f}({})".format({1: "主胜", 0: "平局", -1: "客胜"}[k],
                                sum(v) / len(v), len(v)) for k, v in sorted(dd_by_sign.items(), reverse=True)))
    L.append("\n## 四、方向命中率对照（MD-Elo vs as-of Elo vs 市场去水）\n")
    L.append("| 系列 | n | 命中率 |")
    L.append("|------|--:|:------:|")
    L.append("| MD 报告 Elo | {} | {:.1%} |".format(n, hit["md"] / max(1, n)))
    L.append("| as-of 重放 Elo | {} | {:.1%} |".format(n, hit["asof"] / max(1, n)))
    L.append("| 市场去水 | {} | {:.1%} |".format(n_mkt, hit["mkt"] / max(1, n_mkt)))
    L.append("\n- **MD − as-of 命中差: {:+.1%}**（门槛 3pp）".format(gap))
    n_dis = sum(disagree)
    L.append("- MD 与 as-of 方向不一致 {} 场：MD 对 {}、as-of 对 {}、皆错 {}".format(n_dis, disagree[0], disagree[1], disagree[2]))
    L.append("\n分月（MD / as-of / 市场）：\n")
    L.append("| 月份 | n | MD-Elo | as-of Elo | 市场 |")
    L.append("|------|--:|:------:|:--------:|:----:|")
    for mth in sorted(month_stat):
        a1, a2, a3, nn = month_stat[mth]
        L.append("| {} | {} | {:.1%} | {:.1%} | {:.1%} |".format(mth, nn, a1 / nn, a2 / nn, a3 / nn))
    L.append("\n## 五、裁决\n")
    L.append("- 门槛① k*≥+1 占比 {:.1%} > 20% → {}".format(leak_share, "触发" if v1 else "未触发"))
    L.append("- 门槛② corr(Δd, 赛果) {:+.3f} > 0.15 → {}".format(corr, "触发" if v2 else "未触发"))
    L.append("- 门槛③ 命中差 {:+.1%} > 3pp → {}".format(gap, "触发" if v3 else "未触发"))
    L.append("\n**结论：{}**".format(verdict))

    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as fo:
        fo.write("\n".join(L))

    print("n =", n, "|", json_ok)
    print("lag_hist:", dict(sorted(lag_hist.items())), "| k*>=+1: {:.1%}".format(leak_share))
    print("corr(dD, sign) = {:+.3f} | mean dD = {:+.1f}".format(corr, mean_dd))
    print("hit: md={:.1%} asof={:.1%} mkt={:.1%} | gap={:+.1%}".format(
        hit["md"] / max(1, n), hit["asof"] / max(1, n), hit["mkt"] / max(1, n_mkt), gap))
    print("disagree(md vs asof): n={} md对={} asof对={} 皆错={}".format(n_dis, *disagree))
    print("VERDICT:", verdict)
    print("OUT:", OUT)


if __name__ == "__main__":
    main()
