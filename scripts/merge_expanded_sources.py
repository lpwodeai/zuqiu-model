# -*- coding: utf-8 -*-
"""两源反推数据集合并（P0-1/t6 前置，C-20261002-010）。

背景：P0-1 重生成的新 CSV 真标签覆盖扩大（parse 带线结算），但其中
load_all_labeled_samples / load_inference_target 因 INNER JOIN handicap_history，
把「有结算结果但当前无让球赔率快照」的场整体排除，导致：
  - 旧 CSV 独有的 231 场裸三态真标签、1331 场历史反推标签未进入新 CSV；
  - features 基座里无真标签的场，新 CSV 反推覆盖（1551）远小于旧 CSV（3672）。

本脚本把两源按 match_id 合并去重：真标签优先，同源 inferred 冲突取新，
输出「覆盖最大化」的合并数据集，供 merge_expanded_labels 消费。
产物默认写到 reports/_gated_p001/（隔离），不污染 serving glob（reports 根），
待 t6 联合切换时随 pkl 一起启用。

用法：
  python scripts/merge_expanded_sources.py
  python scripts/merge_expanded_sources.py --old <path> --new <path> --out <path>
"""
import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
REPORT_DIR = PROJECT_DIR / "reports"
GATED_DIR = REPORT_DIR / "_gated_p001"


def find_latest(d: Path) -> Path:
    cs = sorted(d.glob("hcp_expanded_dataset_*.csv"))
    if not cs:
        sys.exit(f"未找到 hcp_expanded_dataset_*.csv: {d}")
    return cs[-1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", type=Path, default=None, help="旧源 CSV（默认 reports 根最新）")
    ap.add_argument("--new", type=Path, default=None, help="新源 CSV（默认 _gated_p001 最新）")
    ap.add_argument("--out", type=Path, default=None, help="输出路径（默认 _gated_p001 时间戳）")
    args = ap.parse_args()

    old_path = args.old or find_latest(REPORT_DIR)
    new_path = args.new or find_latest(GATED_DIR)
    out_path = args.out or (
        GATED_DIR / f"hcp_expanded_dataset_merged_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
    )

    old = pd.read_csv(old_path)
    new = pd.read_csv(new_path)
    print(f"旧源: {old_path.name} ({len(old)} 行)")
    print(f"新源: {new_path.name} ({len(new)} 行)")

    # 列对齐检查
    old_cols, new_cols = set(old.columns), set(new.columns)
    miss_old = new_cols - old_cols
    miss_new = old_cols - new_cols
    if miss_old or miss_new:
        print(f"⚠️ 列不一致：旧缺 {miss_old}，新缺 {miss_new}（concat 会自动补 NaN）")

    old["_origin"] = "old"
    new["_origin"] = "new"
    combined = pd.concat([new, old], ignore_index=True)

    # 去重优先级：match_id 升序 → actual 优先 → 同 inferred 时 new 优先
    combined["_src_rank"] = (combined["label_source"] == "actual").astype(int)
    combined["_new_rank"] = (combined["_origin"] == "new").astype(int)
    combined = combined.sort_values(
        ["match_id", "_src_rank", "_new_rank"],
        ascending=[True, False, False],
        kind="stable" if pd is not None else "quicksort",
    )
    out = combined.drop_duplicates("match_id", keep="first").drop(
        columns=["_origin", "_src_rank", "_new_rank"]
    ).reset_index(drop=True)

    GATED_DIR.mkdir(exist_ok=True)
    out.to_csv(out_path, index=False, encoding="utf-8-sig")

    # 统计
    src = out["label_source"].value_counts()
    total = len(out)
    print("\n" + "=" * 60)
    print(f"合并完成 → {out_path}")
    print("=" * 60)
    print(f"总 match_id: {total}（去重后）")
    print(f"  actual  : {int(src.get('actual', 0))}")
    print(f"  inferred: {int(src.get('inferred', 0))}")
    if "actual_handicap_pred" in out.columns:
        names = {0: "上盘赢", 1: "走水", 2: "下盘赢"}
        dist = out["actual_handicap_pred"].value_counts().sort_index()
        for k, v in dist.items():
            print(f"    标签 {names.get(int(k), k)}({int(k)}): {v} ({v/total*100:.1f}%)")
    # 覆盖对比
    old_ids = set(old["match_id"])
    new_ids = set(new["match_id"])
    merged_ids = set(out["match_id"])
    print(f"覆盖对比: 旧 {len(old_ids)} / 新 {len(new_ids)} / 合并 {len(merged_ids)}")
    print(f"  合并覆盖两源并集大小: {len(old_ids | new_ids)}")


if __name__ == "__main__":
    main()