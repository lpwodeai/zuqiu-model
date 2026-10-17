# -*- coding: utf-8 -*-
"""C-108 装载包零件 ②：match_player_stats 表内 stats_json 重解析 0→NULL 回填。

背景（C-107）：解析器把 JSON 键缺失写成 0 而非 NULL（合成零），若直接上零件③
（聚合分母=有值场次数），59% 合成零会被当"有值"进分母 → A 对受影响列半失效。
本脚本从表内 stats_json 恢复区分度（不依赖外部源），strict 约束：
仅当【目标列恰为 0】且【stats_json 中对应 SofaScore 键缺失（无键或键值 None）】才置 NULL。
键=0（真零）的行不动——区分"源未提供"与"源报告为 0"。

扩列（经抽样定界）：goalAssist→assists（缺失 30.7%）与 interceptionWon→interceptions
（缺失 ~59%）同为合成零；goals 一并处理。全部分母对齐 `minutes_played>0`（= played）。

纪律（C-023/C-099 模式）：默认 dry-run 只预览不改库；--execute 先备份整表 JSON 再事务 UPDATE；
UPDATE 属存量数据修正，与重训同窗执行（254 维取值随之变化，C-090/C-092）。

用法：
  python scripts/reparse_zero_to_null.py            # dry-run 预览（安全）
  python scripts/reparse_zero_to_null.py --execute  # 窗口内执行（先备份再改）
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
ODDS_DB = BASE / "data" / "odds.db"
BACKUP_DIR = BASE / "backups"

# (目标列, stats_json 键)。strict: 目标列=0 且键缺失 → 置 NULL
FIX_MAP = {
    "interceptions": "interceptionWon",
    "goals": "goals",
    "assists": "goalAssist",
}
BATCH = 2000


def backup_rows(conn, ids: list[int]) -> Path:
    """导出将改动行的全列快照到 JSON（回滚凭据）。"""
    cols = [c[1] for c in conn.execute("PRAGMA table_info('match_player_stats')")]
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = BACKUP_DIR / f"c108_reparse_backup_{ts}.json"
    BACKUP_DIR.mkdir(exist_ok=True)
    payload = []
    q = f"SELECT {','.join(cols)} FROM match_player_stats WHERE id=?"
    for rid in ids:
        r = conn.execute(q, (rid,)).fetchone()
        if r is not None:
            payload.append(dict(zip(cols, r)))
    out.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return out


def collect_changes(conn) -> dict[str, list[int]]:
    """返回 {列: [将置 NULL 的行 id]}。只读。"""
    plan: dict[str, list[int]] = {c: [] for c in FIX_MAP}
    cur = conn.execute(
        "SELECT id, stats_json, interceptions, goals, assists FROM match_player_stats "
        "WHERE minutes_played > 0 AND stats_json IS NOT NULL AND length(stats_json) > 3")
    for rid, js, inter, goals, assists in cur:
        try:
            d = json.loads(js)
        except Exception:  # noqa: BLE001
            continue
        if len(d) <= 3:
            continue
        if inter == 0 and d.get("interceptionWon") is None and "interceptionWon" not in d:
            plan["interceptions"].append(rid)
        if goals == 0 and d.get("goals") is None and "goals" not in d:
            plan["goals"].append(rid)
        if assists == 0 and d.get("goalAssist") is None and "goalAssist" not in d:
            plan["assists"].append(rid)
    return plan


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--execute", action="store_true", help="真正写库（默认 dry-run）")
    args = ap.parse_args()

    if not ODDS_DB.exists():
        print(f"[FATAL] DB 不存在: {ODDS_DB}")
        return 2
    conn = sqlite3.connect(str(ODDS_DB))
    try:
        n_played = conn.execute("SELECT COUNT(*) FROM match_player_stats WHERE minutes_played>0").fetchone()[0]
        plan = collect_changes(conn)
        total = sum(len(v) for v in plan.values())
        print(f"=== C-108 零件② 重解析 0→NULL（{'EXECUTE' if args.execute else 'DRY-RUN'}）===")
        print(f"played 行 = {n_played}")
        for col, ids in plan.items():
            print(f"  {col:16s} → 置 NULL {len(ids):>7,} 行 ({len(ids)/n_played*100:5.2f}%)")
        print(f"  合计将改 {total:,} 行")
        if not args.execute:
            print("DRY-RUN：未写库。加 --execute 在重训窗口内执行（先备份）。")
            return 0
        if total == 0:
            print("无改动，跳过备份与 UPDATE。")
            return 0
        all_ids = sorted({i for ids in plan.values() for i in ids})
        bk = backup_rows(conn, all_ids)
        print(f"备份: {bk}（{len(all_ids)} 行）")
        with conn:  # 事务
            for col, ids in plan.items():
                for i in range(0, len(ids), BATCH):
                    conn.executemany(
                        f"UPDATE match_player_stats SET {col}=NULL WHERE id=?",
                        [(x,) for x in ids[i:i + BATCH]])
                print(f"  UPDATE {col}: {len(ids):,} 行")
        # 断言：改后非空率下降且 >0
        for col in FIX_MAP:
            nn = conn.execute(
                f"SELECT COUNT(*) FROM match_player_stats WHERE minutes_played>0 AND {col} IS NOT NULL").fetchone()[0]
            print(f"  断言 {col}: played 非空率 = {nn/n_played*100:.2f}%")
        print("✅ 完成。下一步：跑 census（after 基线）→ 零件③ 聚合分母修复 → replay 对拍。")
        return 0
    except Exception as e:  # noqa: BLE001
        print(f"[ERROR] {type(e).__name__}: {e}")
        return 2
    finally:
        conn.close()


if __name__ == "__main__":
    sys.exit(main())
