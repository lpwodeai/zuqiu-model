# -*- coding: utf-8 -*-
"""pytest 全局配置 — 把 scripts/ 目录加入 sys.path，使测试能直接 import 预测模块。"""
import os
import sys

SCRIPT_DIR = os.path.join(os.path.dirname(__file__), '..', 'scripts')
SCRIPT_DIR = os.path.abspath(SCRIPT_DIR)
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)
