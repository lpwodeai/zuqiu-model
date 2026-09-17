# -*- coding: utf-8 -*-
"""
P1-E: MLflow 可复现快照绑定工具

统一采集三类可复现快照，供 train_models.py 与 advanced_model_trainer.py 两个训练入口复用，
避免双入口各自实现导致记录口径不一致（对齐 D3「配置单一来源」/ A5「消除双写路径」的项目惯例）：

  1. git commit     — 训练时代码版本
  2. 数据源版本     — odds.db 路径 / mtime / size / 内容 sha1（可关闭）
  3. 特征集版本     — 特征名顺序 + 维度 + sha1

用法：
    from mlflow_repro import log_repro_snapshot
    log_repro_snapshot(BASE_DIR, feature_names, artifact_dir=OUTPUT_DIR, artifact_suffix=timestamp)

注意：
  - 只依赖标准库 + mlflow（mlflow 在调用方已确认 MLFLOW_AVAILABLE 时才调用）。
  - 数据源 sha1 为 odds.db 全文件流式哈希（约 1.7GB），训练任务内一次性成本，可接受；
    如需关闭（如频繁重训），将 HASH_DATA_FILE 置为 False。
"""

import os
import json
import hashlib
import subprocess

HASH_DATA_FILE = True
DATA_DB_REL = os.path.join('data', 'odds.db')


def get_git_commit(base_dir, timeout=10):
    """返回当前 git HEAD 提交号；不在 git 仓库或失败时返回 'N/A'。"""
    try:
        r = subprocess.run(['git', 'rev-parse', 'HEAD'], capture_output=True,
                           text=True, cwd=str(base_dir), timeout=timeout)
        return r.stdout.strip() if r.returncode == 0 else 'N/A'
    except Exception:
        return 'N/A'


def sha1_file(path, chunk_size=1 << 20):
    """流式计算文件 sha1，避免大文件一次性载入内存。"""
    h = hashlib.sha1()
    with open(path, 'rb') as f:
        while True:
            b = f.read(chunk_size)
            if not b:
                break
            h.update(b)
    return h.hexdigest()


def data_source_snapshot(base_dir, db_rel=DATA_DB_REL):
    """返回数据源版本快照：相对路径 / mtime / size / 内容 sha1（HASH_DATA_FILE 控制）。"""
    p = os.path.join(str(base_dir), db_rel)
    if not os.path.exists(p):
        return {}
    st = os.stat(p)
    snap = {
        'data_source': os.path.relpath(p, str(base_dir)),
        'data_source_mtime': st.st_mtime,
        'data_source_size': int(st.st_size),
    }
    if HASH_DATA_FILE:
        try:
            snap['data_source_sha1'] = sha1_file(p)
        except Exception:
            snap['data_source_sha1'] = 'N/A'
    return snap


def feature_set_snapshot(feature_names):
    """返回特征集版本快照：维度 + 特征名顺序 sha1。"""
    names = [str(n) for n in feature_names]
    h = hashlib.sha1(json.dumps(names, ensure_ascii=False).encode('utf-8')).hexdigest()
    return {
        'feature_dim': len(names),
        'feature_set_hash': h,
    }


def build_repro_snapshot(base_dir, feature_names):
    """汇总三类快照为大 dict（含 feature_names 列表，供 artifact 落盘）。"""
    snap = {'git_commit': get_git_commit(base_dir)}
    snap.update(data_source_snapshot(base_dir))
    snap.update(feature_set_snapshot(feature_names))
    snap['feature_names'] = [str(n) for n in feature_names]
    snap['data_source_sha1'] = snap.get('data_source_sha1', 'N/A')
    return snap


def log_repro_snapshot(base_dir, feature_names, artifact_dir=None, artifact_suffix='repro'):
    """向当前 mlflow run 写入可复现快照 params，并落盘/挂载 repro_snapshot JSON artifact。

    返回快照 dict。artifact_dir 为 None 时仅记录 params 不落 artifact。
    """
    import mlflow

    snap = build_repro_snapshot(base_dir, feature_names)

    for key, val in snap.items():
        if isinstance(val, (int, float, str, bool)):
            mlflow.log_param(key, val)

    if artifact_dir:
        path = os.path.join(str(artifact_dir), f'repro_snapshot_{artifact_suffix}.json')
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(snap, f, ensure_ascii=False, indent=2)
        mlflow.log_artifact(path)

    return snap