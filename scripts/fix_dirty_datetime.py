# -*- coding: utf-8 -*-
"""odds.db 日期/时间戳脏数据规范化工具（对应框架文档 §13.4 数据质量债治理）。

只做「格式规范化 + 语义保留」，不新增/不删除数据行：
  match_date:
    2025/8/16            -> 2025-08-16          (斜杠→连字符、月/日补零)
  timestamp:
    2025-08-14   13:50:32  -> 2025-08-14 13:50:32 (压缩多空格为单空格)
    2026-08-19T19:07:33.384566[+00:00] -> 2026-08-19 19:07:33  (ISO→空格秒, 截断微秒/时区)
    2025-09-21           -> 2025-09-21 00:00:00  (纯日期→补零时)
    2025-09-21_close     -> 2025-09-21 23:59:59  (收盘标记→当日末秒, 保留与开赛快照的时序区分)
    2026-01-03 21:53:27_2  -> 保留不动 (_N 去重后缀, 有 UNIQUE 语义, 去后缀可能撞键)

安全设计:
  - 默认 dry-run（只读）; 加 --apply 才落库
  - UPDATE OR IGNORE: 撞 UNIQUE(match_id, timestamp[, score]) 的行跳过并计数
  - 幂等: 再次运行改动数为 0
  - 排除下划线前缀的备份/临时表
  - --backup: 落库前用 SQLite online backup 生成快照

用法:
  python scripts/fix_dirty_datetime.py                 # dry-run 查看计划
  python scripts/fix_dirty_datetime.py --apply         # 实际落库(不备份)
  python scripts/fix_dirty_datetime.py --apply --backup
"""
import argparse
import re
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", line_buffering=True)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DB = PROJECT_ROOT / "data" / "odds.db"

ISO_TS = re.compile(r"^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2}:\d{2})(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})?$")
DATE_CLOSE = re.compile(r"^(\d{4}-\d{2}-\d{2})_close$")
PURE_DATE = re.compile(r"^(\d{4}-\d{2}-\d{2})$")
SLASH_DATE = re.compile(r"^(\d{4})/(\d{1,2})/(\d{1,2})$")


def norm_timestamp(v):
    if v is None:
        return None, False
    s = str(v).strip()
    orig = s
    s = re.sub(r"\s+", " ", s)            # 1) 多空格 → 单空格
    m = DATE_CLOSE.match(s)               # 2) _close 收盘标记 → 23:59:59
    if m:
        s = m.group(1) + " 23:59:59"
    else:
        m = PURE_DATE.match(s)            # 3) 纯日期 → 00:00:00
        if m:
            s = m.group(1) + " 00:00:00"
        else:
            m = ISO_TS.match(s)           # 4) ISO T 微秒(+时区) → 空格秒
            if m:
                s = m.group(1) + " " + m.group(2)
    return s, (s != orig)


def norm_date(v):
    if v is None:
        return None, False
    s = str(v).strip()
    m = SLASH_DATE.match(s)
    if m:
        return "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))), True
    return s, False


def backup(conn: sqlite3.Connection) -> Path:
    dst = PROJECT_ROOT / "data" / "odds_backup_{}.db".format(
        datetime.now().strftime("%Y%m%d_%H%M%S"))
    # VACUUM INTO: 把当前库内容复制成快照文件（SQLite 3.27+）
    conn.execute("VACUUM INTO '{}'".format(str(dst).replace("'", "''")))
    return dst


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="实际落库（默认 dry-run）")
    ap.add_argument("--backup", action="store_true", help="落库前 VACUUM INTO 生成快照")
    args = ap.parse_args()

    conn = sqlite3.connect(str(DB))
    conn.row_factory = sqlite3.Row

    backup_dst = None
    if args.apply and args.backup:
        backup_dst = backup(conn)
        print("\n[backup] 清洗前快照 -> {}".format(backup_dst))

    tables = [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name NOT LIKE '\\_%' ESCAPE '\\' ORDER BY name")]

    rows = []
    total_changed = 0
    total_conflict = 0

    for t in tables:
        cols = [r[1] for r in conn.execute('PRAGMA table_info("{}")'.format(t))]
        targets = []
        if "match_date" in cols:
            targets.append(("match_date", norm_date))
        if "timestamp" in cols:
            targets.append(("timestamp", norm_timestamp))
        if not targets:
            continue
        for col, norm in targets:
            qcol = '"{}"'.format(col)
            changed = 0
            conflict = 0
            cur = conn.execute('SELECT rowid, {} FROM "{}"'.format(qcol, t))
            while True:
                batch = cur.fetchmany(20000)
                if not batch:
                    break
                updates = []
                for rid, val in batch:
                    newval, diffs = norm(val)
                    if diffs:
                        updates.append((newval, rid))
                if args.apply and updates:
                    for newval, rid in updates:
                        c2 = conn.execute(
                            'UPDATE OR IGNORE "{}" SET {}=? WHERE rowid=?'.format(t, qcol),
                            (newval, rid))
                        if c2.rowcount == 0:
                            conflict += 1
                        else:
                            changed += 1
                elif updates:
                    changed += len(updates)
            if changed or conflict:
                rows.append((t, col, changed, conflict))
                total_changed += changed
                total_conflict += conflict

    if args.apply:
        conn.commit()
    conn.close()

    print("\n===== 清洗报告 {} =====".format("(已落库)" if args.apply else "(DRY-RUN，未修改)"))
    for t, col, c, cf in rows:
        line = "  {:<24} {:<12} 改 {:,} 行".format(t, col, c)
        if cf:
            line += "，撞键跳过 {:,}".format(cf)
        print(line)
    print("----- 合计: 改 {:,} 行, 撞键跳过 {:,} -----".format(total_changed, total_conflict))
    if backup_dst:
        print("[backup] 回滚点: {}（确认无误后可删除）".format(backup_dst))
    if not args.apply:
        print("提示: 确认无误后加 --apply 实际落库（建议 --apply --backup）。")


if __name__ == "__main__":
    main()