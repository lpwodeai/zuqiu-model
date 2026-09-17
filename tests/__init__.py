"""
测试包初始化文件
================

五大联赛专属模型 - 自动化测试框架
阶段六：测试扩展

目录结构:
    tests/
    ├── conftest.py              # pytest 全局 fixtures
    ├── run_all_tests.py         # 主测试运行器
    ├── unit/                    # 单元测试（模块级）
    │   ├── test_elo_rating.py
    │   ├── test_d013_temporal.py
    │   ├── test_feature_temporal.py
    │   ├── test_d017_selection.py
    │   ├── test_d016_calibration.py
    │   └── test_feature_utils.py
    ├── integration/             # 集成测试（跨模块）
    │   ├── test_feature_pipeline.py
    │   └── test_model_pipeline.py
    ├── e2e/                     # 端到端测试（完整流程）
    │   └── test_predict_pipeline.py
    └── fixtures/                # 测试数据 fixture
        └── sample_data.py
"""
