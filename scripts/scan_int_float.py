# -*- coding: utf-8 -*-
"""扫描导出 CSV: 找出 INTEGER 列实际含小数/非整数值的列, 输出 PG 类型覆盖清单"""
import csv
import json
import re
from pathlib import Path

sys_dir = Path(__file__).resolve().parent
import sys
sys.path.insert(0, str(sys_dir))
from pg_migrate import EXPORT_DIR, PROJECT_ROOT, load_schema, split_top_level, table_columns

INT_RE = re.compile(r"^-?\d+$")
NUM_RE = re.compile(r"^-?\d+(\.\d+)?([eE][+-]?\d+)?$")
BOOL_LEGAL = {"true", "false", "t", "f", "1", "0", ""}

schema = load_schema()
bad_float = {}   # table -> set(cols): INTEGER 列含小数
bad_bool = {}    # table -> set(cols): BOOLEAN 列含非法值

for table, info in schema.items():
    cols = table_columns(info["ddl"])
    types = {}
    body = info["ddl"][info["ddl"].index("(") + 1: info["ddl"].rindex(")")]
    for p in split_top_level(body):
        m = re.match(r'^\s*"?(\w+)"?\s+(INTEGER|INT|BOOLEAN|REAL)\b', p.strip(), re.I)
        if m:
            types[m.group(1)] = m.group(2).upper()
    int_cols = [c for c in cols if types.get(c) in ("INTEGER", "INT")]
    bool_cols = [c for c in cols if types.get(c) == "BOOLEAN"]
    if not int_cols and not bool_cols:
        continue
    idx_int = {cols.index(c): c for c in int_cols}
    idx_bool = {cols.index(c): c for c in bool_cols}
    path = EXPORT_DIR / f"{table}.csv"
    if not path.exists():
        continue
    with open(path, encoding="utf-8", newline="") as f:
        reader = csv.reader(f)
        for row in reader:
            for i, c in idx_int.items():
                if c in bad_float.get(table, set()):
                    continue
                v = row[i] if i < len(row) else ""
                if v and v != "\\N" and NUM_RE.match(v) and not INT_RE.match(v):
                    bad_float.setdefault(table, set()).add(c)
            for i, c in idx_bool.items():
                if c in bad_bool.get(table, set()):
                    continue
                v = row[i] if i < len(row) else ""
                if v and v != "\\N" and v.lower() not in BOOL_LEGAL:
                    bad_bool.setdefault(table, set()).add(c)
    if table in bad_float or table in bad_bool:
        print(f"[scan] {table}: float->{sorted(bad_float.get(table, []))} bool->{sorted(bad_bool.get(table, []))}", flush=True)

out = {"float_in_int": {t: sorted(c) for t, c in bad_float.items()},
       "bad_bool": {t: sorted(c) for t, c in bad_bool.items()}}
(PROJECT_ROOT / "scripts" / "pg_type_overrides.json").write_text(
    json.dumps(out, indent=1), encoding="utf-8")
print(json.dumps(out, indent=1))
