#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
D2: 模型版本管理清理 — assets/*.pkl 版本归档工具

功能:
  --list     只读列出 assets/*.pkl,按模型族分组展示 版本/大小/修改时间/白名单状态
  --archive  对每个模型族保留最近 N 版(--keep,默认 5),其余移动归档到
             backups/models/(默认 dry-run,--apply 才真正执行)
  --restore  把归档文件从 backups/models/ 移回 assets/
  --status   查询 data/odds.db 的 model_versions 表最近记录

白名单保护(绝不归档,一律不动):
  1. deployment/latest_model.json 的 model_paths 指向的模型文件
  2. t005v3_* / t006_* 当前生产系列
  3. 运行中的 advanced_model 最近 N 版

归档规则: 对每个模型族,按修改时间新→旧保留最近 N 版,其余移入
  backups/models/;白名单文件即使超出最近 N 版也绝不移动。

每次 --archive --apply 执行后,按模型族向 data/odds.db 的 model_versions
表写入一条记录(INSERT OR IGNORE + UNIQUE(model_name, version) 保证幂等):
  model_name=族名, version=该族最新被归档版本,
  file_path/archive_path=相对项目根路径, status='archived',
  trigger_experiment=--experiment 传入的触发实验名。

用法:
  python scripts/manage_model_versions.py --list
  python scripts/manage_model_versions.py --archive                      # dry-run 预览
  python scripts/manage_model_versions.py --archive --apply --keep 5 --experiment "D2"
  python scripts/manage_model_versions.py --restore advanced_model_20260805_104054.pkl
  python scripts/manage_model_versions.py --status
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

# 动态定位项目根目录(脚本位于 scripts/ 下,其父目录即项目根,不写死盘符)
SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_DIR = SCRIPT_DIR.parent
ASSETS_DIR = PROJECT_DIR / "assets"
BACKUP_MODELS_DIR = PROJECT_DIR / "backups" / "models"
DB_PATH = PROJECT_DIR / "data" / "odds.db"
LATEST_MODEL_JSON = PROJECT_DIR / "deployment" / "latest_model.json"

# 带版本时间戳的模型文件名: 前缀_YYYYMMDD_HHMMSS.pkl
TS_RE = re.compile(r"^(.+)_(\d{8}_\d{6})\.pkl$")

# 生产系列白名单前缀
PROD_PREFIXES = ("t005v3_", "t006_")

# model_versions 表结构(幂等建表;UNIQUE(model_name, version) + INSERT OR IGNORE 保证重复执行不冲突)
CREATE_MODEL_VERSIONS_SQL = """
CREATE TABLE IF NOT EXISTS model_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT,
    version TEXT,
    file_path TEXT,
    archive_path TEXT,
    status TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    trigger_experiment TEXT,
    UNIQUE(model_name, version)
)
"""
REQUIRED_COLS = {"model_name", "version", "file_path", "archive_path",
                 "status", "created_at", "trigger_experiment"}


def human_size(num_bytes: int) -> str:
    """字节数 → 人类可读大小"""
    return f"{num_bytes / 1024 / 1024:.2f} MB"


def family_of(fname: str) -> str:
    """按文件名前缀归族: 带时间戳取时间戳前的前缀(如 advanced_model),
    无时间戳取下划线前的首段(如 t005v3 / t006)"""
    m = TS_RE.match(fname)
    if m:
        return m.group(1)
    stem = fname[:-4]
    return stem.split("_")[0] if "_" in stem else stem


def version_of(fname: str) -> str:
    """提取版本号: 时间戳版本;无时间戳返回 fixed"""
    m = TS_RE.match(fname)
    return m.group(2) if m else "fixed"


def rel_path(p: Path) -> str:
    """相对项目根的路径(可移植,不包含盘符)"""
    return str(p.relative_to(PROJECT_DIR))


def collect_pkl_files() -> list[Path]:
    """assets/ 下所有 .pkl 文件(按修改时间新→旧排序)"""
    if not ASSETS_DIR.is_dir():
        print(f"❌ assets 目录不存在: {ASSETS_DIR}")
        sys.exit(1)
    files = [p for p in ASSETS_DIR.iterdir() if p.is_file() and p.suffix == ".pkl"]
    files.sort(key=lambda p: (p.stat().st_mtime, p.name), reverse=True)
    return files


def collect_whitelist(files: list[Path], keep: int) -> set[str]:
    """收集白名单保护的文件名集合(绝不归档)"""
    wl: set[str] = set()

    # 1. latest_model.json 指向的模型路径(仅 .pkl 模型文件)
    if LATEST_MODEL_JSON.is_file():
        try:
            data = json.loads(LATEST_MODEL_JSON.read_text(encoding="utf-8"))
            for p in data.get("model_paths", {}).values():
                if p and p.endswith(".pkl"):
                    wl.add(Path(p).name)
        except (OSError, json.JSONDecodeError) as e:
            print(f"⚠️ 读取 {LATEST_MODEL_JSON} 失败: {e}")

    # 2. t005v3_* / t006_* 生产系列
    for p in files:
        if p.name.startswith(PROD_PREFIXES):
            wl.add(p.name)

    # 3. 运行中的 advanced_model 最近 N 版
    advanced = [p for p in files if family_of(p.name) == "advanced_model"]
    for p in advanced[:keep]:
        wl.add(p.name)

    return wl


def plan_archive(files: list[Path], keep: int, whitelist: set[str]) -> list[Path]:
    """计算将归档的文件: 每族保留最近 N 版,其余归档;白名单一律不动"""
    to_archive: list[Path] = []
    by_family: dict[str, list[Path]] = {}
    for p in files:
        by_family.setdefault(family_of(p.name), []).append(p)

    for members in by_family.values():
        for p in members[keep:]:          # 超出最近 N 版的部分
            if p.name not in whitelist:   # 白名单保护,绝不移动
                to_archive.append(p)
    return to_archive


# ------------------------- 数据库 -------------------------

def connect_db() -> sqlite3.Connection:
    """连接 data/odds.db,启用 busy_timeout=5000 避免写锁冲突"""
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def ensure_model_versions_table(conn: sqlite3.Connection) -> None:
    """幂等建表: 不存在则创建;存在但结构不匹配且为空则重建为约定结构"""
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='model_versions'"
    ).fetchone()
    if not row:
        conn.execute(CREATE_MODEL_VERSIONS_SQL)
        conn.commit()
        return
    cols = {r[1] for r in conn.execute("PRAGMA table_info(model_versions)")}
    if REQUIRED_COLS.issubset(cols):
        return
    n = conn.execute("SELECT COUNT(*) FROM model_versions").fetchone()[0]
    if n == 0:
        # 旧结构且空表 → 直接重建,不损失数据
        conn.execute("DROP TABLE model_versions")
        conn.execute(CREATE_MODEL_VERSIONS_SQL)
        conn.commit()
        print("⚠️ 已重建空的 model_versions 表(旧表结构与约定不一致)")
    else:
        # 非空旧表 → 只补缺失列,不破坏数据(无法补 UNIQUE 约束,仅尽力兼容)
        for c in sorted(REQUIRED_COLS - cols):
            conn.execute(f"ALTER TABLE model_versions ADD COLUMN {c} TEXT")
        conn.commit()
        print("⚠️ 已为 model_versions 表补齐缺失列")


# ------------------------- 子命令 -------------------------

def cmd_list(args: argparse.Namespace) -> None:
    """--list: 只读列出 assets/*.pkl,按模型族分组"""
    files = collect_pkl_files()
    whitelist = collect_whitelist(files, args.keep)
    to_archive_set = {p.name for p in plan_archive(files, args.keep, whitelist)}

    by_family: dict[str, list[Path]] = {}
    for p in files:
        by_family.setdefault(family_of(p.name), []).append(p)

    print("=" * 78)
    print(f"assets/*.pkl 模型清单 — 共 {len(files)} 个文件,{len(by_family)} 个模型族 "
          f"(每族保留最近 {args.keep} 版)")
    print("=" * 78)
    for fam in sorted(by_family):
        members = by_family[fam]
        wl_n = sum(1 for p in members if p.name in whitelist)
        print(f"\n[族: {fam}] 共 {len(members)} 个,白名单 {wl_n} 个")
        for p in members:
            if p.name in whitelist:
                tag = "白名单"
            elif p.name in to_archive_set:
                tag = "将归档"
            else:
                tag = "保留"
            mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M:%S")
            print(f"  [{tag:4s}] {p.name:<52s} {human_size(p.stat().st_size):>9s}  {mtime}")


def cmd_archive(args: argparse.Namespace) -> None:
    """--archive: 每族保留最近 N 版,其余归档到 backups/models/"""
    files = collect_pkl_files()
    whitelist = collect_whitelist(files, args.keep)
    to_archive = plan_archive(files, args.keep, whitelist)

    print("=" * 78)
    print(f"模型版本归档 {'[APPLY 执行]' if args.apply else '[DRY-RUN 预览]'}")
    print(f"  assets 模型文件总数 : {len(files)}")
    print(f"  白名单保护          : {len(whitelist)} 个文件")
    print(f"  每族保留最近版本数  : {args.keep}")
    print("=" * 78)

    if not to_archive:
        print("\n✅ 没有需要归档的文件。")
        return

    total_mb = sum(p.stat().st_size for p in to_archive) / 1024 / 1024
    print(f"\n将归档 {len(to_archive)} 个文件(共 {total_mb:.1f} MB)到 backups/models/:\n")
    for p in to_archive:
        mtime = datetime.fromtimestamp(p.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
        print(f"  [{family_of(p.name):24s}] {p.name}  "
              f"({version_of(p.name)}, {human_size(p.stat().st_size)}, {mtime})")

    if not args.apply:
        print("\n💡 DRY-RUN:确认无误后加 --apply 真正执行归档。")
        return

    # --apply: 真正执行移动 + 按族写入 model_versions
    BACKUP_MODELS_DIR.mkdir(parents=True, exist_ok=True)
    moved: list[Path] = []
    skipped = 0
    for p in to_archive:
        dest = BACKUP_MODELS_DIR / p.name
        if dest.exists():
            print(f"  ⚠️ 目标已存在,跳过: {p.name}")
            skipped += 1
            continue
        try:
            shutil.move(str(p), str(dest))
            print(f"  🗂️ 已归档 {p.name} → backups/models/")
            moved.append(p)
        except OSError as e:
            print(f"  ❌ 归档失败 {p.name}: {e}")

    if not moved:
        print("\n⚠️ 本次没有实际归档的文件。")
        return

    # 按族插入 model_versions 记录(幂等)
    by_family: dict[str, list[Path]] = {}
    for p in moved:
        by_family.setdefault(family_of(p.name), []).append(p)

    conn = connect_db()
    try:
        ensure_model_versions_table(conn)
        inserted = 0
        for fam, members in by_family.items():
            # members 保持新→旧顺序,首项即该族最新被归档版本
            newest = members[0]
            cur = conn.execute(
                "INSERT OR IGNORE INTO model_versions "
                "(model_name, version, file_path, archive_path, status, trigger_experiment) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (fam, version_of(newest.name), rel_path(newest),
                 rel_path(BACKUP_MODELS_DIR / newest.name), "archived", args.experiment),
            )
            inserted += cur.rowcount
        conn.commit()
        print(f"\n✅ 归档完成:移动 {len(moved)} 个文件(跳过 {skipped}),"
              f"model_versions 新增记录 {inserted} 条。")
    finally:
        conn.close()


def cmd_restore(args: argparse.Namespace) -> None:
    """--restore: 把归档文件从 backups/models/ 移回 assets/"""
    fname = args.restore
    if not fname.endswith(".pkl"):
        fname += ".pkl"  # 允许省略后缀
    src = BACKUP_MODELS_DIR / fname
    if not src.is_file():
        print(f"❌ backups/models/ 中不存在 {fname}")
        sys.exit(1)
    dest = ASSETS_DIR / fname
    if dest.exists():
        print(f"❌ assets/ 已存在同名文件 {fname},不覆盖")
        sys.exit(1)

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    shutil.move(str(src), str(dest))
    print(f"✅ 已恢复 {fname} → assets/")

    # 同步更新 model_versions 对应记录状态
    try:
        conn = connect_db()
        try:
            ensure_model_versions_table(conn)
            cur = conn.execute(
                "UPDATE model_versions SET status='restored' "
                "WHERE archive_path=? AND status='archived'",
                (rel_path(dest),),
            )
            conn.commit()
            if cur.rowcount:
                print(f"  ℹ️ 已更新 model_versions 记录 {cur.rowcount} 条 → status='restored'")
        finally:
            conn.close()
    except sqlite3.Error as e:
        print(f"  ⚠️ 更新 model_versions 失败(不影响文件恢复): {e}")


def cmd_status(args: argparse.Namespace) -> None:
    """--status: 查询 model_versions 表最近记录"""
    conn = connect_db()
    try:
        ensure_model_versions_table(conn)
        rows = conn.execute(
            "SELECT id, model_name, version, status, created_at, trigger_experiment, archive_path "
            "FROM model_versions ORDER BY id DESC LIMIT 20"
        ).fetchall()
        print("=" * 78)
        print(f"model_versions 最近记录(显示 {len(rows)} 条)")
        print("=" * 78)
        if not rows:
            print("(空表,尚无记录)")
        for r in rows:
            print(f"  #{r['id']:<4d} [{r['model_name']}] v{r['version']}  "
                  f"{r['status']}  {r['created_at']}  {r['trigger_experiment'] or ''}")
            if r["archive_path"]:
                print(f"        → {r['archive_path']}")
    finally:
        conn.close()


def main() -> None:
    # Windows 控制台输出中文/emoji 兼容
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

    ap = argparse.ArgumentParser(
        description="D2 模型版本管理清理: assets/*.pkl 版本归档工具")
    ap.add_argument("--list", action="store_true", help="只读列出 assets/*.pkl 模型清单")
    ap.add_argument("--archive", action="store_true",
                    help="按模型族归档过期版本(默认 dry-run)")
    ap.add_argument("--apply", action="store_true",
                    help="与 --archive 配合,真正执行归档(默认仅预览)")
    ap.add_argument("--keep", type=int, default=5,
                    help="每个模型族保留最近 N 版(默认 5)")
    ap.add_argument("--restore", metavar="文件名",
                    help="把 backups/models/ 中的归档文件移回 assets/")
    ap.add_argument("--status", action="store_true",
                    help="查询 data/odds.db 的 model_versions 表最近记录")
    ap.add_argument("--experiment", default="CLI 手动归档",
                    help="触发实验名称,写入 model_versions.trigger_experiment")
    args = ap.parse_args()

    if args.keep < 1:
        ap.error("--keep 必须 >= 1")
    if args.apply and not args.archive:
        ap.error("--apply 只能与 --archive 一起使用")

    ops = sum(bool(x) for x in (args.list, args.archive, args.restore, args.status))
    if ops != 1:
        ap.error("请指定且仅指定一个操作: --list / --archive / --restore / --status")

    if args.list:
        cmd_list(args)
    elif args.archive:
        cmd_archive(args)
    elif args.restore:
        cmd_restore(args)
    else:
        cmd_status(args)


if __name__ == "__main__":
    main()
