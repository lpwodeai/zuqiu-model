# -*- coding: utf-8 -*-
"""P2-11: 数据库适配层 — SQLite / PostgreSQL 双后端

用法:
    from db_utils import connect, read_sql

    conn = connect()                # 默认 SQLite (data/odds.db), 行为同 sqlite3.connect
    conn = connect(backend="pg")    # 强制 PostgreSQL
    conn = connect(backend="sqlite")

后端选择优先级: connect(backend=...) > 环境变量 DB_BACKEND(pg/sqlite, 默认 sqlite)

PG 连接参数(环境变量): PG_HOST / PG_PORT / PG_USER / PG_PASSWORD / PG_DB
    默认 localhost:5432 postgres/postgres odds

兼容性:
  - "?" 占位符自动转 "%s"(仅 PG 后端)
  - conn.execute / cursor / fetchone / fetchall / fetchmany / commit / rollback / close
  - sqlite3.Row 风格键访问: row["col"] / row[0] / row.keys()
  - pandas: 用 read_sql(sql, conn, params) 替代 pd.read_sql
    (pandas 不支持非 SQLAlchemy 的 PG 连接, 本函数对两后端行为一致)
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

PROJECT_ROOT = Path(__file__).resolve().parent
DB_PATH = PROJECT_ROOT / "data" / "odds.db"


class Row:
    """sqlite3.Row 风格行对象: 支持整数索引 / 列名(大小写不敏感) / keys()。"""

    __slots__ = ("_values", "_map")

    def __init__(self, columns: Sequence[str], values: Sequence[Any]):
        self._values = tuple(values)
        self._map = {c.lower(): i for i, c in enumerate(columns)}

    def keys(self) -> list[str]:
        return list(self._map.keys())

    def __getitem__(self, key):
        if isinstance(key, int):
            return self._values[key]
        return self._values[self._map[str(key).lower()]]

    def __len__(self) -> int:
        return len(self._values)

    def __iter__(self):
        return iter(self._values)

    def __repr__(self) -> str:
        return f"Row({dict(zip(self._map, self._values))!r})"


class PgCursor:
    """pg8000 cursor 的 sqlite3 风格包装。"""

    def __init__(self, cur):
        self._cur = cur

    @property
    def description(self):
        return self._cur.description

    def execute(self, sql: str, params: Sequence[Any] = ()) -> "PgCursor":
        self._cur.execute(_to_pg_sql(sql), tuple(params))
        return self

    def executemany(self, sql: str, seq: Iterable[Sequence[Any]]) -> "PgCursor":
        self._cur.executemany(_to_pg_sql(sql), [tuple(p) for p in seq])
        return self

    def fetchone(self) -> Optional[Row]:
        r = self._cur.fetchone()
        return None if r is None else Row([d[0] for d in self._cur.description], r)

    def fetchall(self) -> list[Row]:
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return [Row(cols, r) for r in self._cur.fetchall()]

    def fetchmany(self, size: int = 1) -> list[Row]:
        cols = [d[0] for d in self._cur.description] if self._cur.description else []
        return [Row(cols, r) for r in self._cur.fetchmany(size)]

    def close(self) -> None:
        try:
            self._cur.close()
        except Exception:  # noqa: BLE001
            pass


class PgConnection:
    """pg8000 connection 的 sqlite3 兼容包装。"""

    def __init__(self, conn):
        self._conn = conn

    def cursor(self) -> PgCursor:
        return PgCursor(self._conn.cursor())

    def execute(self, sql: str, params: Sequence[Any] = ()) -> PgCursor:
        return self.cursor().execute(sql, params)

    def commit(self) -> None:
        self._conn.commit()

    def rollback(self) -> None:
        try:
            self._conn.rollback()
        except Exception:  # noqa: BLE001
            pass

    def close(self) -> None:
        try:
            self._conn.close()
        except Exception:  # noqa: BLE001
            pass

    @staticmethod
    def row_factory():  # 兼容检查代码; PG 后端恒为 Row
        return Row


def _to_pg_sql(sql: str) -> str:
    """SQLite 风格 ? 占位符 → PG %s。跳过引号内的问号。"""
    out, in_str = [], False
    i = 0
    while i < len(sql):
        ch = sql[i]
        if ch == "'":
            in_str = not in_str
            out.append(ch)
            i += 1
        elif ch == "?" and not in_str:
            out.append("%s")
            i += 1
        else:
            out.append(ch)
            i += 1
    return "".join(out)


def _pg_kwargs() -> dict:
    return dict(
        host=os.environ.get("PG_HOST", "localhost"),
        port=int(os.environ.get("PG_PORT", "5432")),
        user=os.environ.get("PG_USER", "postgres"),
        password=os.environ.get("PG_PASSWORD", "postgres"),
        database=os.environ.get("PG_DB", "odds"),
        timeout=int(os.environ.get("PG_TIMEOUT", "3600")),
    )


def connect(backend: Optional[str] = None, db_path: Optional[Path] = None):
    """返回 sqlite3.Connection 或 PgConnection。

    backend: "sqlite" / "pg" / None(用环境变量 DB_BACKEND, 默认 sqlite)
    """
    chosen = (backend or os.environ.get("DB_BACKEND", "sqlite")).lower()
    if chosen == "sqlite":
        conn = sqlite3.connect(str(db_path or DB_PATH))
        conn.row_factory = sqlite3.Row  # 与 PG 端一致, 返回支持键访问的行
        conn.execute("PRAGMA busy_timeout = 5000")
        return conn
    if chosen == "pg":
        sys_dir = PROJECT_ROOT / "pylibs"
        if str(sys_dir) not in sys.path:
            sys.path.insert(0, str(sys_dir))
        import pg8000.dbapi
        return PgConnection(pg8000.dbapi.connect(**_pg_kwargs()))
    raise ValueError(f"未知后端: {chosen!r} (可选 sqlite / pg)")


def read_sql(sql: str, conn, params: Optional[Sequence[Any]] = None):
    """统一的 pandas 读入(两后端行为一致), 替代 pd.read_sql。"""
    import pandas as pd

    cur = conn.execute(sql, params or ())
    rows = cur.fetchall()
    cols = [d[0] for d in cur.description]
    cur.close()
    return pd.DataFrame([tuple(r) for r in rows], columns=cols)


def write_dataframe(conn, df, table_name: str) -> int:
    """将 DataFrame 批量 INSERT 进已存在的表(两后端行为一致), 替代 df.to_sql。

    df.to_sql 仅在 SQLAlchemy 引擎 / sqlite3 连接下可用, PG 后端(pg8000 裸连接)
    不支持; 本函数用 executemany 实现, NaN/NaT → NULL、datetime → ISO 字符串。
    """
    import math
    import pandas as pd
    from datetime import date, datetime

    if df.empty:
        return 0

    cols = [str(c) for c in df.columns]
    quoted = ", ".join(f'"{c}"' for c in cols)
    ph = ", ".join("?" for _ in cols)
    sql = f"INSERT INTO {table_name} ({quoted}) VALUES ({ph})"

    def _cell(v):
        if v is None or v is pd.NaT:
            return None
        if isinstance(v, (datetime, date)):
            return v.isoformat()
        if isinstance(v, float) and math.isnan(v):
            return None
        return v

    rows = [tuple(_cell(v) for v in rec.values()) for rec in df.to_dict("records")]

    cursor = conn.cursor()
    cursor.executemany(sql, rows)
    conn.commit()
    return len(rows)


def numeric_sql_type(conn) -> str:
    """数值列在两后端等价的建表类型: SQLite REAL(8 字节) ≈ PG DOUBLE PRECISION。"""
    return "DOUBLE PRECISION" if isinstance(conn, PgConnection) else "REAL"


def table_exists(conn, table_name: str) -> bool:
    """表存在性检查(两后端行为一致), 替代 sqlite_master 查询。"""
    if isinstance(conn, PgConnection):
        cur = conn.execute(
            "SELECT 1 FROM information_schema.tables "
            "WHERE table_schema = 'public' AND table_name = ?",
            (table_name,),
        )
    else:
        cur = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
            (table_name,),
        )
    return cur.fetchone() is not None
