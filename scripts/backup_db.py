# -*- coding: utf-8 -*-
"""数据库备份脚本（C-20260918-030 扩展多 DB 备份）

用法：
    python scripts/backup_db.py [--keep N] [--db NAME] [--list] [--restore FILE]

支持的数据库（C-030 新增 anomaly_samples.db）：
    - five_leagues.db   (生产业务库，默认备份)
    - odds.db            (训练主库，单独大文件备份)
    - anomaly_samples.db (异常样本库，C-030 新增)

设计原则（§9.4 备份策略）：
    - 使用 sqlite3.backup() 在线一致性快照（WAL 模式安全）
    - 单一 DB 单一 .db 输出，配套 .meta.json 元信息
    - 保留近 N 天，旧备份自动清理
"""

import os
import sqlite3
import sys
import time
import json
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"
DEFAULT_DST = PROJECT_ROOT / "backup" / "db_snapshots"
DEFAULT_KEEP = 14  # 保留最近 14 天的备份

# C-20260918-030：默认备份的 DB 列表（five_leagues.db + anomaly_samples.db）
# odds.db 因 1.6GB+ 大文件单独备份，可用 --db odds 显式触发
DEFAULT_BACKUP_DBS = ["five_leagues", "anomaly_samples"]


def backup(src_path, dst_dir, keep_days, db_prefix=None):
    """执行数据库备份。
    返回: (success: bool, message: str, backup_path: str | None)
    """
    src = Path(src_path)
    dst = Path(dst_dir)

    # 1. 检查源文件
    if not src.exists():
        return False, f"源数据库不存在: {src}", None

    src_size = src.stat().st_size
    if src_size == 0:
        return False, "源数据库为空文件", None

    # 2. 创建备份目录
    dst.mkdir(parents=True, exist_ok=True)

    # 3. 生成时间戳文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    prefix = db_prefix or src.stem
    backup_filename = f"{prefix}_{timestamp}.db"
    backup_path = dst / backup_filename

    # 4. 使用 SQLite 在线备份 API 生成一致性快照（WAL 模式下安全，仅产出单个 .db）
    try:
        src_conn = sqlite3.connect(str(src), timeout=30.0)
        try:
            dst_conn = sqlite3.connect(str(backup_path))
            try:
                src_conn.backup(dst_conn)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
    except Exception as e:
        return False, f"备份复制失败: {e}", None

    backup_size = backup_path.stat().st_size

    # 5. 清理旧备份（按 prefix 过滤）
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in dst.iterdir():
        if f.is_file() and f.suffix == ".db" and f.stem.startswith(f"{prefix}_"):
            # 从文件名解析时间戳: {prefix}_YYYYMMDD_HHMMSS
            date_str = f.stem.replace(f"{prefix}_", "")
            try:
                file_time = datetime.strptime(date_str, "%Y%m%d_%H%M%S").timestamp()
                if file_time < cutoff:
                    f.unlink()
                    removed += 1
                    # 同步删除 meta.json
                    meta_sibling = f.with_suffix(".meta.json")
                    if meta_sibling.exists():
                        meta_sibling.unlink()
            except (ValueError, OSError):
                pass

    # 6. 写入备份元信息
    meta = {
        "timestamp": timestamp,
        "source": str(src),
        "db_prefix": prefix,
        "size_bytes": src_size,
        "backup_size_bytes": backup_size,
        "removed_old_backups": removed,
    }
    meta_path = backup_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    msg = f"备份成功: {backup_path} ({backup_size:,} bytes), 清理旧备份 {removed} 个"
    return True, msg, str(backup_path)


def list_backups(dst_dir, prefix=None):
    """列出所有备份（可按 prefix 过滤）"""
    dst = Path(dst_dir)
    if not dst.exists():
        return []
    backups = []
    pattern = f"{prefix}_*.db" if prefix else "*.db"
    for f in sorted(dst.glob(pattern), reverse=True):
        # 从文件名解析 prefix 和时间戳
        stem = f.stem
        # 形如 five_leagues_20260918_120000 或 anomaly_samples_20260918_120000
        try:
            # 找到最后一个 YYYYMMDD_HHMMSS
            parts = stem.rsplit("_", 2)
            if len(parts) == 3:
                db_prefix, date_part, time_part = parts
                date_str = f"{date_part}_{time_part}"
                date_obj = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
            else:
                continue
        except ValueError:
            continue
        backups.append({
            "file": f.name,
            "prefix": db_prefix,
            "size": f.stat().st_size,
            "date": date_obj.strftime("%Y-%m-%d %H:%M:%S"),
        })
    return backups


def main():
    import argparse

    parser = argparse.ArgumentParser(description="数据库备份（支持 five_leagues/anomaly_samples/odds 多 DB）")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    parser.add_argument("--restore", metavar="BACKUP_FILE", help="从备份文件恢复")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"保留天数 (默认 {DEFAULT_KEEP})")
    parser.add_argument("--db", default=None,
                        help="指定备份的 DB 名称（five_leagues/anomaly_samples/odds）；默认备份所有 DEFAULT_BACKUP_DBS")
    parser.add_argument("--src", default=None, help="自定义源数据库路径（覆盖 --db）")
    parser.add_argument("--dst", default=str(DEFAULT_DST), help="备份目录")
    args = parser.parse_args()

    if args.list:
        backups = list_backups(args.dst)
        if not backups:
            print("暂无备份")
        else:
            print(f"备份目录: {args.dst}")
            print(f"备份数量: {len(backups)}")
            print("-" * 80)
            # 按 prefix 分组
            from collections import defaultdict
            groups = defaultdict(list)
            for b in backups:
                groups[b["prefix"]].append(b)
            for prefix, items in groups.items():
                print(f"\n[{prefix}] ({len(items)} 个)")
                for b in items:
                    size_mb = b["size"] / 1024 / 1024
                    print(f"  {b['date']}  {b['size']:>10,} bytes ({size_mb:.2f} MB)  {b['file']}")
        return

    if args.restore:
        src = Path(args.restore)
        if not src.exists():
            print(f"备份文件不存在: {src}")
            sys.exit(1)
        # 推断 prefix → 目标恢复路径
        stem = src.stem
        parts = stem.rsplit("_", 2)
        if len(parts) == 3:
            db_prefix = parts[0]
        else:
            db_prefix = "unknown"
        dst_path = DATA_DIR / f"{db_prefix}.db"
        print(f"恢复 {src} -> {dst_path}")
        print("⚠️  这将覆盖当前数据库，继续吗? (y/N)")
        if input().lower() != "y":
            print("已取消")
            return
        # WAL 一致性防护：先删除目标库旧 -wal/-shm 残留，避免脏 WAL 污染恢复结果
        for stale in [dst_path.parent / (dst_path.name + "-wal"),
                      dst_path.parent / (dst_path.name + "-shm")]:
            if stale.exists():
                stale.unlink()
        try:
            src_conn = sqlite3.connect(str(src), timeout=30.0)
            try:
                dst_conn = sqlite3.connect(str(dst_path), timeout=30.0)
                try:
                    src_conn.backup(dst_conn)
                finally:
                    dst_conn.close()
            finally:
                src_conn.close()
        except Exception as e:
            print(f"❌ 恢复失败: {e}")
            sys.exit(1)
        print("✅ 恢复完成，请重启服务")
        return

    # 默认：执行备份
    if args.src:
        # 自定义源
        src_path = Path(args.src)
        prefix = src_path.stem
        success, msg, _ = backup(src_path, args.dst, args.keep, db_prefix=prefix)
        if success:
            print(f"✅ {msg}")
        else:
            print(f"❌ {msg}")
            sys.exit(1)
        return

    # 按名称备份（默认所有 DEFAULT_BACKUP_DBS）
    if args.db:
        db_names = [args.db]
    else:
        db_names = DEFAULT_BACKUP_DBS

    all_success = True
    for db_name in db_names:
        src_path = DATA_DIR / f"{db_name}.db"
        if not src_path.exists():
            print(f"⚠️  跳过（源不存在）: {src_path}")
            continue
        success, msg, _ = backup(src_path, args.dst, args.keep, db_prefix=db_name)
        if success:
            print(f"✅ [{db_name}] {msg}")
        else:
            print(f"❌ [{db_name}] {msg}")
            all_success = False

    if not all_success:
        sys.exit(1)


if __name__ == "__main__":
    main()
