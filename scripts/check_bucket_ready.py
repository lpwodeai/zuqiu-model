# -*- coding: utf-8 -*-
"""概率档校准监控桶就绪检查（t6 启动门禁 / P0-2）。

只读 odds.db，检查「平局 WDL_draw」任一概率档是否积累到 n>=50。
- 达标：写 reports/t6_ready.flag，退出码 0
- 未达标：打印当前进度与差距，退出码 1

退出码语义面向 Windows 任务计划程序 / CI：0 == 可启动 t6。

用法：
  python scripts/check_bucket_ready.py
  python scripts/check_bucket_ready.py --min 50 --db data/odds.db
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
REPORT_DIR = PROJECT_DIR / "reports"
sys.path.insert(0, str(SCRIPT_DIR))

import prob_bucket_monitor as pbm  # noqa: E402

TARGET_TYPE = "WDL_draw"
TARGET_CN = "平局"
N_BINS = 20
READY_FLAG = REPORT_DIR / "t6_ready.flag"


def bucket_counts(sub, n_bins=N_BINS):
    """返回各档 [(lo, hi, n)]，含 n=0 的空档。"""
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    out = []
    for b in range(n_bins):
        lo, hi = edges[b], edges[b + 1]
        m = (sub["probability"] >= lo) & (sub["probability"] < hi) if b < n_bins - 1 \
            else (sub["probability"] >= lo) & (sub["probability"] <= hi)
        out.append((round(lo, 3), round(hi, 3), int(m.sum())))
    return out


def desktop_notify(msg_body):
    """非阻塞弹 Windows 桌面提醒框；无交互会话时静默降级，返回是否已尝试。"""
    code = (
        "import ctypes,sys;"
        "ctypes.windll.user32.MessageBoxW(0, sys.argv[1], u't6 门禁就绪', 0x40)"
    )
    try:
        subprocess.Popen([sys.executable, "-c", code, msg_body])
        return True
    except Exception:
        return False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=pbm.DEFAULT_DB)
    ap.add_argument("--min", type=int, default=50, help="就绪样本门槛（默认 50）")
    args = ap.parse_args()

    df = pbm.load_anchor_rows(args.db)
    sub = df[df["prediction_type"] == TARGET_TYPE]
    counts = bucket_counts(sub)
    mx_lo, mx_hi, mx_n = max(counts, key=lambda x: x[2])

    print("=" * 60)
    print(f"就绪检查：{TARGET_CN}（{TARGET_TYPE}）概率档达 n>={args.min}？")
    print("=" * 60)
    print(f"平局锚点行数：{len(sub)}（is_replay=0 已结算）")
    print(f"最大档：{mx_lo:.2f}~{mx_hi:.2f}，n={mx_n}")

    nonzero = sorted([(lo, hi, n) for lo, hi, n in counts if n > 0], key=lambda x: -x[2])
    print("非空档分布（按样本降序）：")
    for lo, hi, n in nonzero:
        bar = "█" * min(int(n / args.min * 20), 20)
        print(f"  [{lo:.2f},{hi:.2f}): {n:>3d}  {bar}")

    if mx_n >= args.min:
        payload = {
            "ready": True,
            "threshold": args.min,
            "target": TARGET_TYPE,
            "target_cn": TARGET_CN,
            "max_bucket": {"lo": mx_lo, "hi": mx_hi, "n": mx_n},
            "checked_at": datetime.now().isoformat(timespec="seconds"),
            "note": "t6 可启动：λ bisect + 走水样本(含 inferred 两源合并) + "
                    "阈值/温度/平局factor 重校准 + 严格时序 A/B",
        }
        REPORT_DIR.mkdir(exist_ok=True)
        READY_FLAG.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                              encoding="utf-8")
        # 醒目提醒文件（人读）
        notice = REPORT_DIR / "T6_READY.txt"
        notice.write_text(
            "\n".join([
                "=" * 62,
                "  T6 门禁就绪 —— 需要人工动作",
                "=" * 62,
                f"  平局概率档已达 n>={args.min}（最大档 {mx_lo:.2f}~{mx_hi:.2f}，n={mx_n}）",
                f"  检查时间：{payload['checked_at']}",
                "",
                "  下一步（严格时序 A/B，通过 deploy 硬门禁后才落库切生产）：",
                "    1. λ bisect 切换（config lambda_split.mode: legacy -> bisect）",
                "    2. 走水样本入训练（P0-1 已修管道，合并两源 inferred）",
                "    3. 阈值/温度/平局 factor 重校准",
                "    4. 通过 deploy_t005v3_final 硬门禁才替换 pkl 与切 config",
                "=" * 62,
            ]), encoding="utf-8")
        print("\n✅ 已就绪，写标记文件：", READY_FLAG)
        print("📄 醒目提醒文件       ：", notice)
        if desktop_notify(
            f"平局概率档已积累到 n>={args.min}，可启动 t6 联合重训。\n"
            f"最大档 {mx_lo:.2f}~{mx_hi:.2f}（n={mx_n}）。"
        ):
            print("🔔 桌面通知已弹")
        else:
            print("⚠️ 桌面通知不可用（非交互会话），请依 flag/txt 文件判断")
        sys.exit(0)
    else:
        print(f"\n⏳ 未就绪（最大档 {mx_n}/{args.min}，差 {args.min - mx_n} 场）")
        sys.exit(1)


if __name__ == "__main__":
    main()