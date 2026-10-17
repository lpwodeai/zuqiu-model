# -*- coding: utf-8 -*-
"""C-20261011-040: 从历史赛前 md 回填 TG_top1/TG_top3 发布行。

背景：TG_top1/TG_top3 发布行自 2026-09-19 起稳定，8/30~9/18 硬空档（v2.0 重构期字段丢失），
8 月零星缺失；复盘脚本 C-20260921-035 起只读真实发布行、禁止由 λ/大小球重推，
导致空档场次复盘「进球数 Top1/Top3」标「无发布记录」。

本脚本：以复盘 md 文件名为锚（中文竞彩队名），经 difflib 模糊定位赛前 md 与模型库
match_id，解析赛前 md 中真实展示过的「进球数 Top3 预测」表 + 摘要卡 Top1，
按 model_name='prematch_md_backfill' 写入 model_predictions（不碰 actual_* 键，
INSERT OR IGNORE 防重）。发布值=当年报告真实展示值，非编造。

用法:
    python backfill_tg_from_prematch_md.py            # dry-run，只打印
    python backfill_tg_from_prematch_md.py --apply    # 实际写库
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import unicodedata
from difflib import SequenceMatcher

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB = os.path.join(ROOT, "data", "odds.db")
PM_DIR = os.path.join(ROOT, "docs", "post_match")
PRE_DIR = os.path.join(ROOT, "docs", "prematch_reports")


def norm(s):
    s = unicodedata.normalize("NFD", s or "")
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\b(CF|FC|AC|AS|SC|SV|01|1|FSV|07)\b", " ", s)
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def sim(a, b):
    return SequenceMatcher(None, norm(a), norm(b)).ratio()


def tg_label_to_goals(label):
    m = re.search(r"(\d+)", label or "")
    return int(m.group(1)) if m else None


def parse_tg_from_md(txt):
    """从赛前 md 解析进球数 Top1/Top3（兼容摘要卡与 §五 两种格式、7+球 标签）。"""
    top1 = None  # (label, prob)
    m = re.search(r"\|\s*\*{0,2}总进球 Top1\*{0,2}\s*\|\s*\*{0,2}(\d+\+?球)\*{0,2}\s*\|\s*([\d.]+)%", txt)
    if m:
        top1 = (m.group(1), float(m.group(2)) / 100.0)
    top3 = []
    block = re.search(r"\*\*进球数 Top3 预测\*\*\s*\n(.*?)(?:\n\n|\n>)", txt, re.S)
    src = block.group(1) if block else ""
    for mm in re.finditer(
            r"\|\s*(\d)\s*\|\s*\*{0,2}(\d+\+?球)\*{0,2}\s*\|\s*([\d.]+)%\s*\|\s*([\d.]+)%\s*\|", src):
        rank, label, prob, _cum = int(mm.group(1)), mm.group(2), float(mm.group(3)) / 100.0, mm.group(4)
        top3.append((rank, label, prob))
    top3.sort()
    top3 = [(label, prob) for _, label, prob in top3[:3]]
    if top1 is None and top3:
        top1 = top3[0]
    gen = re.search(r"\*\*生成时间\*\*[:：]\s*(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})", txt)
    gen_ts = gen.group(1) if gen else None
    return top1, top3, gen_ts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际写库（默认 dry-run）")
    args = ap.parse_args()

    conn = sqlite3.connect(DB)
    cur = conn.cursor()
    have = {(mid, pt) for mid, pt in cur.execute(
        "SELECT match_id,prediction_type FROM model_predictions "
        "WHERE prediction_type IN ('TG_top1','TG_top3')")}

    # 模型库 match_id 索引: (date, norm_home, norm_away) -> match_id
    model_ids = {}
    for (mid,) in cur.execute("SELECT DISTINCT match_id FROM model_predictions"):
        d, p = mid[:10], mid[11:].split("_")
        if len(p) >= 2:
            model_ids[(d, norm(p[0]), norm(p[-1]))] = mid
    by_date = {}
    for (d, h, a), mid in model_ids.items():
        by_date.setdefault(d, []).append((h, a, mid))

    # 500 中文映射层（复盘=中文竞彩名 → odds500 行 → 模型英文名）:
    # (date, home_cn, away_cn) -> 500 match_id；另存按日期列表做中文模糊
    cn_exact = {}
    cn_by_date = {}
    for mid5, hcn, acn, md in cur.execute(
            "SELECT match_id,home_team_cn,away_team_cn,match_date FROM odds500_match "
            "WHERE match_id IS NOT NULL"):
        cn_exact[(md, hcn, acn)] = mid5
        cn_by_date.setdefault(md, []).append((hcn, acn, mid5))

    def resolve(iso, home, away):
        mid5 = cn_exact.get((iso, home, away))
        if mid5 is None:
            best, bs = None, 0.0
            for hcn, acn, m5 in cn_by_date.get(iso, []):
                s = min(SequenceMatcher(None, home, hcn).ratio(),
                        SequenceMatcher(None, away, acn).ratio())
                if s > bs:
                    bs, best = s, m5
            if bs < 0.62:
                return None
            mid5 = best
        p5 = mid5[11:].split("_")
        if len(p5) < 2:
            return None
        mid = model_ids.get((iso, norm(p5[0]), norm(p5[-1])))
        if mid:
            return mid
        for h, a, mm in by_date.get(iso, []):
            if min(sim(p5[0], h), sim(p5[-1], a)) >= 0.8:
                return mm
        return None

    # 赛前 md 索引: date -> [(home_cn, away_cn, path)]
    pre_files = {}
    for f in glob.glob(os.path.join(PRE_DIR, "*", "*_vs_*.md")):
        bn = os.path.basename(f)[:-3]
        if "_build_log" in bn:
            continue
        d = re.search(r"(\d{4}-\d{2}-\d{2})", bn)
        if not d or "_vs_" not in bn:
            continue
        try:
            l2, away = bn.rsplit("_vs_", 1)
            home = l2.rsplit("_", 1)[-1]
        except ValueError:
            continue
        pre_files.setdefault(d.group(1), []).append((home, away, f))

    stats = {"scan": 0, "resolved": 0, "parsed": 0, "inserted": 0, "skip_exist": 0,
             "no_md": 0, "no_parse": 0, "no_gen": 0}
    problems = []
    for d in sorted(glob.glob(os.path.join(PM_DIR, "*"))):
        iso = "{}-{}-{}".format(os.path.basename(d)[:4], os.path.basename(d)[4:6], os.path.basename(d)[6:])
        for f in glob.glob(os.path.join(d, "*复盘.md")):
            bn = os.path.basename(f)[: -len("_复盘.md")]
            if "_vs_" not in bn:
                continue
            stats["scan"] += 1
            left, away = bn.rsplit("_vs_", 1)
            home = left.rsplit("_", 1)[-1]
            # 1) 定位模型 match_id（中文竞彩名 → 500 → 模型英文名）
            mid = resolve(iso, home, away)
            if not mid:
                problems.append(("no_model_id", iso, home, away))
                continue
            stats["resolved"] += 1
            if (mid, "TG_top1") in have and (mid, "TG_top3") in have:
                stats["skip_exist"] += 1
                continue
            # 2) 定位赛前 md（中文原串比对；norm() 会把中文归零导致全匹配，禁用）
            best, bs = None, 0.0
            for h, a, pf in pre_files.get(iso, []):
                s = min(SequenceMatcher(None, home, h).ratio(),
                        SequenceMatcher(None, away, a).ratio())
                if s > bs:
                    bs, best = s, pf
            if best is None or bs < 0.72:
                stats["no_md"] += 1
                problems.append(("no_prematch_md", iso, home, away))
                continue
            with open(best, encoding="utf-8") as fh:
                txt = fh.read()
            top1, top3, gen_ts = parse_tg_from_md(txt)
            if not top1 or not top3:
                stats["no_parse"] += 1
                problems.append(("no_parse", iso, home, away, os.path.basename(best)))
                continue
            if not gen_ts:
                stats["no_gen"] += 1
                problems.append(("no_gen_time", iso, home, away))
                continue
            stats["parsed"] += 1
            snap = json.dumps({"match_date": iso, "backfill_from": os.path.relpath(best, ROOT),
                               "note": "C-20261011-040 回填自赛前 md 展示值"}, ensure_ascii=False)
            ins = []
            if (mid, "TG_top1") not in have:
                ins.append((mid, "prematch_md_backfill", "TG_top1", top1[0], top1[1], top1[1],
                            gen_ts, snap, "md_backfill", "—", "prematch_md_backfill", 0))
            if (mid, "TG_top3") not in have:
                payload = json.dumps([{"goals": tg_label_to_goals(lb), "label": lb, "prob": p}
                                      for lb, p in top3], ensure_ascii=False)
                ins.append((mid, "prematch_md_backfill", "TG_top3", payload, None, None,
                            gen_ts, snap, "md_backfill", "—", "prematch_md_backfill", 0))
            print("[{}] {} top1={} top3={} gen={}".format(
                "APPLY" if args.apply else "DRY", mid, top1[0],
                "、".join(lb for lb, _ in top3), gen_ts))
            if args.apply and ins:
                cur.executemany(
                    "INSERT OR IGNORE INTO model_predictions"
                    "(match_id,model_name,prediction_type,prediction,probability,confidence,"
                    " timestamp,input_snapshot_json,feature_version,config_version,"
                    " model_version,is_replay) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", ins)
                stats["inserted"] += len(ins)

    if args.apply:
        conn.commit()
    conn.close()
    print("\n统计:", stats)
    if problems:
        print("问题清单 ({}):".format(len(problems)))
        for p in problems[:20]:
            print("  ", p)


if __name__ == "__main__":
    main()
