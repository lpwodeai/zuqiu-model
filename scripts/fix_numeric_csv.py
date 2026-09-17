# -*- coding: utf-8 -*-
"""清洗 pg_export CSV：数值/布尔列中的非法值（空串、非数字文本）→ \\N。

背景：SQLite 类型宽松，INTEGER/REAL/BOOLEAN 列可能存入 '' 或文本，
PostgreSQL COPY 严格拒绝。TEXT 列不做改动（空串语义保留）。
"""
import csv
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
EXPORT_DIR = PROJECT_ROOT / "data" / "pg_export"
SCHEMA_JSON = PROJECT_ROOT / "scripts" / "pg_migration_schema.json"

NUMERIC_TYPES = {"INTEGER", "INT", "REAL", "BOOLEAN"}
BOOL_LEGAL = {"0", "1", "true", "false", "t", "f"}

schema = json.loads(SCHEMA_JSON.read_text(encoding="utf-8"))

for table, info in schema.items():
    ddl = info["ddl"]
    body = ddl[ddl.index("(") + 1: ddl.rindex(")")]
    # 解析数值/布尔列索引
    depth, buf, parts = 0, [], []
    for ch in body:
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(buf).strip()); buf = []
        else:
            buf.append(ch)
    if buf:
        parts.append("".join(buf).strip())

    num_idx = []
    for i, part in enumerate(parts):
        m = re.match(r'^"?(\w+)"?\s+(\w+)\b', part, re.I)
        if m and m.group(2).upper() in NUMERIC_TYPES:
            num_idx.append((i, m.group(2).upper()))
    if not num_idx:
        continue

    path = EXPORT_DIR / f"{table}.csv"
    tmp = path.with_suffix(".csv.fix")
    n_fixed = 0
    with open(path, "r", encoding="utf-8", newline="") as fin, \
         open(tmp, "w", encoding="utf-8", newline="") as fout:
        reader = csv.reader(fin)
        writer = csv.writer(fout, quoting=csv.QUOTE_MINIMAL)
        for row in reader:
            changed = False
            for i, typ in num_idx:
                if i >= len(row):
                    continue
                v = row[i]
                if v == "\\N":
                    continue
                bad = (v == "" or
                       (typ == "BOOLEAN" and v.lower() not in BOOL_LEGAL) or
                       (typ != "BOOLEAN" and not re.match(r'^-?\d+(\.\d+)?([eE][+-]?\d+)?$', v)))
                if bad:
                    row[i] = "\\N"
                    changed = True
            if changed:
                n_fixed += 1
            writer.writerow(row)
    tmp.replace(path)
    print(f"{table:36s} fixed_rows={n_fixed:,} (numeric_cols={len(num_idx)})")

print("done")
