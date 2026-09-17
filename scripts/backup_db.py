# -*- coding: utf-8 -*-
"""
five_leagues.db 数据库备份脚本
用于备份服务器运行时数据库，防止误删
用法：python scripts/backup_db.py [--keep N] [--src PATH] [--dst PATH]
"""

import os
import shutil
import sys
import time
import json
from datetime import datetime
from pathlib import Path

# 配置
PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SRC = PROJECT_ROOT / "data" / "five_leagues.db"
DEFAULT_DST = PROJECT_ROOT / "backup" / "db_snapshots"
DEFAULT_KEEP = 14  # 保留最近 14 天的备份


def backup(src_path, dst_dir, keep_days):
    """
    执行数据库备份
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
    backup_filename = f"five_leagues_{timestamp}.db"
    backup_path = dst / backup_filename

    # 4. 复制文件（含 WAL/SHM）
    try:
        shutil.copy2(src, backup_path)
        # 尝试复制 WAL 和 SHM
        for ext in ["-wal", "-shm"]:
            sidecar = src.parent / (src.name + ext)
            if sidecar.exists():
                shutil.copy2(sidecar, dst / (backup_filename + ext))
    except Exception as e:
        return False, f"备份复制失败: {e}", None

    backup_size = backup_path.stat().st_size

    # 5. 清理旧备份
    cutoff = time.time() - keep_days * 86400
    removed = 0
    for f in dst.iterdir():
        if f.is_file() and f.suffix == ".db" and "_" in f.stem:
            # 从文件名解析时间戳: five_leagues_YYYYMMDD_HHMMSS.db
            try:
                date_str = f.stem.replace("five_leagues_", "")
                file_time = datetime.strptime(date_str, "%Y%m%d_%H%M%S").timestamp()
                if file_time < cutoff:
                    f.unlink()
                    removed += 1
            except (ValueError, OSError):
                pass

    # 6. 写入备份元信息
    meta = {
        "timestamp": timestamp,
        "source": str(src),
        "size_bytes": src_size,
        "backup_size_bytes": backup_size,
        "removed_old_backups": removed,
    }
    meta_path = backup_path.with_suffix(".meta.json")
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    msg = f"备份成功: {backup_path} ({backup_size:,} bytes), 清理旧备份 {removed} 个"
    return True, msg, str(backup_path)


def list_backups(dst_dir):
    """列出所有备份"""
    dst = Path(dst_dir)
    if not dst.exists():
        return []
    backups = []
    for f in sorted(dst.glob("five_leagues_*.db"), reverse=True):
        backups.append({
            "file": f.name,
            "size": f.stat().st_size,
            "date": datetime.strptime(
                f.stem.replace("five_leagues_", ""), "%Y%m%d_%H%M%S"
            ).strftime("%Y-%m-%d %H:%M:%S"),
        })
    return backups


def main():
    import argparse

    parser = argparse.ArgumentParser(description="备份 five_leagues.db")
    parser.add_argument("--list", action="store_true", help="列出已有备份")
    parser.add_argument("--restore", metavar="BACKUP_FILE", help="从备份文件恢复")
    parser.add_argument("--keep", type=int, default=DEFAULT_KEEP, help=f"保留天数 (默认 {DEFAULT_KEEP})")
    parser.add_argument("--src", default=str(DEFAULT_SRC), help="源数据库路径")
    parser.add_argument("--dst", default=str(DEFAULT_DST), help="备份目录")
    args = parser.parse_args()

    if args.list:
        backups = list_backups(args.dst)
        if not backups:
            print("暂无备份")
        else:
            print(f"备份目录: {args.dst}")
            print(f"备份数量: {len(backups)}")
            print("-" * 70)
            for b in backups:
                size_mb = b["size"] / 1024 / 1024
                print(f"  {b['date']}  {b['size']:>10,} bytes ({size_mb:.1f} MB)  {b['file']}")
        return

    if args.restore:
        src = Path(args.restore)
        if not src.exists():
            print(f"备份文件不存在: {src}")
            sys.exit(1)
        dst = Path(args.src)
        print(f"恢复 {src} -> {dst}")
        print("⚠️  这将覆盖当前数据库，继续吗? (y/N)")
        if input().lower() != "y":
            print("已取消")
            return
        shutil.copy2(src, dst)
        # 恢复 WAL/SHM
        for ext in ["-wal", "-shm"]:
            sidecar = src.parent / (src.name + ext)
            if sidecar.exists():
                shutil.copy2(sidecar, dst.parent / (dst.name + ext))
        print("✅ 恢复完成，请重启服务")
        return

    # 默认: 执行备份
    success, msg, _ = backup(args.src, args.dst, args.keep)
    if success:
        print(f"✅ {msg}")
    else:
        print(f"❌ {msg}")
        sys.exit(1)


if __name__ == "__main__":
    main()