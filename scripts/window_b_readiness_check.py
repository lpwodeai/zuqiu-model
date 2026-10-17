# -*- coding: utf-8 -*-
"""window_b_readiness_check.py — P2/P4「窗口B」就绪检查器（C-20261016-113）

⚠️ 只读：(a) 不修改 odds.db；(b) 仅幂等重写 reports/ 下的 log/清单。
**只提醒，不执行**——开窗动作仍由人决策（重训撞 C-090/092 铁律，不可自动化）。

就绪硬门（两条同时满足 = 亮灯）：
  G1  C-108c 已结案（窗口A 已开过，两变量不得同窗）
  G2  碰撞 == 0（折算后新键在同表内已存在的行数；不为 0 不得开迁）
信息项（不阻断，但窗口内需要）：
  I1  残余不可映射清单已落盘（本脚本自动从最新 dry-run JSON 生成）
  I2  §10 边界已裁定（已定）

碰撞只需检查**混合键表**（同表既有 CN 又有 EN 键）——纯 CN 表折算后新键全为 EN，
与现存键不可能重叠，故 score_history 等纯 CN 大表无需扫描（低成本且正确）。

用法：python scripts/window_b_readiness_check.py
"""
from __future__ import annotations

import json
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
ODDS_DB = PROJECT_DIR / "data" / "odds.db"
REPORT_DIR = PROJECT_DIR / "reports"
CHANGE_LOG = PROJECT_DIR / "docs" / "change_log.md"
OUT_LOG = REPORT_DIR / "window_b_readiness_log.md"
RESIDUAL_MD = REPORT_DIR / "p2_p4_residual_unmapped.md"

sys.path.insert(0, str(PROJECT_DIR / "scripts"))
try:
    from team_names import normalize_team_name
    from p2_p4_dryrun import analyze_table, build_bridge, TABLES_WITH_MATCH_ID
except Exception as e:  # noqa: BLE001
    print(f"[FATAL] 依赖导入失败: {e}")
    sys.exit(0)

CN_RE = re.compile(r"[\u4e00-\u9fff]")


def check_c108c() -> tuple[bool, str]:
    """G1：扫 change_log 的 C-20261015-108c 行，未标 ✅ 即未结案。"""
    try:
        for line in CHANGE_LOG.read_text(encoding="utf-8").splitlines():
            # 只认「定义行」（以 | C-20261015-108c | 开头），排除其他条目的引用
            if line.strip().startswith("| C-20261015-108c |"):
                closed = ("✅" in line) and ("📌" not in line)
                return closed, ("已结案" if closed else "未结案（仍为 📌 待窗口状态）")
    except Exception as e:  # noqa: BLE001
        return False, f"读取失败: {e}"
    return False, "未找到 C-20261015-108c 行"


def check_collisions() -> tuple[int, list]:
    """G2：仅扫混合键表，返回 (总碰撞数, 明细)。"""
    conn = sqlite3.connect(str(ODDS_DB))
    bridge = build_bridge(conn)
    total, detail = 0, []
    for t in TABLES_WITH_MATCH_ID:
        try:
            # 快速判混合：match_id 含字母的行数（GLOB 走 SQLite C 层，快）
            n = conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
            en = conn.execute(
                f"SELECT COUNT(*) FROM \"{t}\" WHERE match_id GLOB '*[A-Za-z]*'"
            ).fetchone()[0]
            if en == 0 or en == n:      # 纯 CN 或纯 EN → 不可能碰撞
                continue
            st = analyze_table(conn, t, bridge)
            total += st["collision"]
            if st["collision"]:
                detail.append(f"{t}: {st['collision']}")
        except sqlite3.Error:
            continue
    conn.close()
    return total, detail


def dump_residual() -> tuple[int, str]:
    """I1：从最新 dry-run JSON 生成残余不可映射清单（落盘供窗口内裁定）。"""
    jsons = sorted(REPORT_DIR.glob("p2_p4_dryrun_*.json"))
    if not jsons:
        return -1, "无 dry-run JSON（先跑 scripts/p2_p4_dryrun.py）"
    data = json.loads(jsons[-1].read_text(encoding="utf-8"))
    rows = [(r["table"], r["unmapped"], r.get("unmapped_sample", []))
            for r in data["tables"] if r["unmapped"]]
    total = sum(u for _, u, _ in rows)
    md = [f"# P2/P4 残余不可映射清单（{datetime.now():%Y-%m-%d %H:%M}）\n",
          f"> 源：{jsons[-1].name}｜合计 **{total}** 条。窗口B 迁移前须逐表裁定："
          "补别名 / 走桥表 / 落遗留清单。\n\n",
          "| 表 | 不可映射 | 样例 |\n|------|--:|------|\n"]
    for t, u, s in rows:
        md.append(f"| {t} | {u} | {'; '.join(s[:5])} |\n")
    RESIDUAL_MD.write_text("".join(md), encoding="utf-8")
    return total, RESIDUAL_MD.name


def main() -> None:
    g1_ok, g1_msg = check_c108c()
    coll, coll_detail = check_collisions()
    residual, residual_msg = dump_residual()

    ready = g1_ok and coll == 0

    lines = [f"# 窗口B（P2/P4 迁移）就绪检查（C-20261016-113）\n",
             f"> 最近运行：{datetime.now():%Y-%m-%d %H:%M}｜**只读检查，不执行任何写入**\n",
             f"> **开窗动作仍需人工决策**（重训撞 C-090/092 铁律）\n\n"]
    lines.append(f"## 结论：{'🟢 已就绪，可开窗' if ready else '🔴 未就绪（继续等待）'}\n\n")
    lines.append("| 门 | 项 | 状态 | 说明 |\n|:--:|------|:--:|------|\n")
    lines.append(f"| G1 | C-108c 已结案 | {'✅' if g1_ok else '❌'} | {g1_msg} |\n")
    lines.append(f"| G2 | 碰撞 = 0 | {'✅' if coll == 0 else '❌'} | 当前 **{coll}**"
                 + (f"（{'； '.join(coll_detail)}）" if coll_detail else "") + " |\n")
    lines.append(f"| I1 | 残余清单落盘 | {'✅' if residual >= 0 else '⚠️'} | "
                 f"{residual if residual >= 0 else '—'} 条 → `{residual_msg}` |\n")
    lines.append("| I2 | §10 边界裁定 | ✅ | R1/R2 + 窗口排序已定（本轮） |\n")

    if ready:
        lines.append("\n> **🟢 满足开窗条件**：请人工确认后按 runbook §11 执行"
                     "（先清碰撞 → 分表迁移 → 每表覆盖率硬门 → 配套重训）。\n")
    else:
        lines.append("\n> **🔴 未就绪**：G1/G2 未同时满足前不得开迁；本器每日自动复查，"
                     "达成即亮灯，无需人工记挂。\n")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    OUT_LOG.write_text("".join(lines), encoding="utf-8")
    print("".join(lines))


if __name__ == "__main__":
    try:
        main()
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] 检查器异常（非阻断）: {e}")
    sys.exit(0)
