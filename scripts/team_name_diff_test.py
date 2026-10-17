# -*- coding: utf-8 -*-
"""P1 前置：全库队名归一函数行为差分测试（只读，C-096 后续）。

三份实现：
  tm = team_name_mapping.normalize_team_name  -> 返回中文
  fu = feature_utils.normalize_team_name      -> 返回中文
  tn = team_names.normalize_team_name         -> 返回 canonical_en

判据：原始输出空间不同（cn vs canonical_en），直接比会假阳性。
语义比较在 canonical_en 空间进行：旧实现输出再过一遍 tn() 归到 canonical。
原始输出差单列（决定只读消费者切换后展示文本是否变化）。

输入字符串全集 = DB 所有 team 列 distinct + team_names 673 alias + 门报漂移清单。
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
SCRIPTS = BASE / "scripts"
sys.path.insert(0, str(SCRIPTS))

ODDS_DB = BASE / "data" / "odds.db"
CJK_RE = re.compile(r"[㐀-鿿぀-ヿ]")

from team_name_mapping import normalize_team_name as tm_norm  # noqa: E402
from feature_utils import normalize_team_name as fu_norm  # noqa: E402
from team_names import TEAM_ALIASES, normalize_team_name as tn_norm  # noqa: E402


def collect_strings() -> set[str]:
    """DB 全部 team 列 distinct 值 + 全部 alias。"""
    conn = sqlite3.connect(str(ODDS_DB))
    strings: set[str] = set()
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        for t in tables:
            cols = conn.execute(f"PRAGMA table_info('{t}')").fetchall()
            for _, c, ctype, *_ in cols:
                cl = c.lower()
                if "team" not in cl:
                    continue
                if cl.endswith("_id") or cl.endswith("_side"):
                    continue
                try:
                    rows = conn.execute(
                        f"SELECT DISTINCT \"{c}\" FROM '{t}' WHERE \"{c}\" IS NOT NULL").fetchall()
                except Exception:
                    continue
                for (v,) in rows:
                    s = str(v).strip()
                    if s and not s.isdigit() and len(s) >= 2:
                        strings.add(s)
    finally:
        conn.close()
    for ce, aliases in TEAM_ALIASES.items():
        strings.add(ce)
        strings.update(str(a) for a in aliases)
    return strings


def canon_of(out: str | None) -> str | None:
    """把旧实现的中文输出归到 canonical_en（已是 canonical 则原样）。"""
    if out is None:
        return None
    out = str(out).strip()
    if not out:
        return None
    if out in TEAM_ALIASES:
        return out
    return tn_norm(out) or f"__UNRESOLVED__:{out}"


def main() -> None:
    strings = sorted(collect_strings())
    print(f"输入字符串全集: {len(strings)}")

    buckets: dict[str, list[dict]] = {
        "ALL_AGREE": [],          # 三者语义一致
        "OLD_AGREE_NEW_DIFF": [],  # 两份旧一致，但新实现语义不同
        "OLD_DISAGREE": [],        # 两份旧实现语义互不一致
        "NEW_UNRESOLVED": [],      # 新实现解析失败但旧实现可解析
        "RAW_ONLY_DIFF": [],       # 语义一致但原始输出文本不同
    }
    raw_diff_count = 0
    for s in strings:
        try:
            o_tm = tm_norm(s)
        except Exception:
            o_tm = None
        try:
            o_fu = fu_norm(s)
        except Exception:
            o_fu = None
        try:
            o_tn = tn_norm(s)
        except Exception:
            o_tn = None
        c_tm, c_fu, c_tn = canon_of(o_tm), canon_of(o_fu), o_tn
        rec = {"in": s, "tm": o_tm, "fu": o_fu, "tn": o_tn,
               "c_tm": c_tm, "c_fu": c_fu, "c_tn": c_tn}

        if c_tn is None and (c_tm or c_fu):
            buckets["NEW_UNRESOLVED"].append(rec)
            continue
        if c_tm != c_fu:
            buckets["OLD_DISAGREE"].append(rec)
            continue
        if c_tm is not None and c_tn is not None and c_tm != c_tn:
            buckets["OLD_AGREE_NEW_DIFF"].append(rec)
            continue
        buckets["ALL_AGREE"].append(rec)
        if (o_tm or o_fu) and o_tn and (o_tm != o_tn or o_fu != o_tn):
            # 语义一致但文本不同（cn 输出 vs canonical_en）
            if o_tm != o_fu:
                buckets["RAW_ONLY_DIFF"].append(rec)
                raw_diff_count += 1

    print()
    print("=" * 72)
    print("差分结果（语义空间 = canonical_en）")
    print("=" * 72)
    for k, v in buckets.items():
        print(f"  {k}: {len(v)}")

    for k in ("OLD_DISAGREE", "OLD_AGREE_NEW_DIFF", "NEW_UNRESOLVED", "RAW_ONLY_DIFF"):
        if buckets[k]:
            print(f"\n--- {k}（前 15 条）---")
            for r in buckets[k][:15]:
                print(f"  in={r['in']!r}  tm={r['tm']!r}  fu={r['fu']!r}  tn={r['tn']!r}")

    out = SCRIPTS / "team_name_diff_ledger.json"
    out.write_text(json.dumps({
        "total_strings": len(strings),
        "summary": {k: len(v) for k, v in buckets.items()},
        "buckets": buckets,
    }, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n台账: {out}")


if __name__ == "__main__":
    main()
