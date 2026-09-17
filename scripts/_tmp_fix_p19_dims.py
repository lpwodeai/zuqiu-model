# -*- coding: utf-8 -*-
"""临时：更正 model_gap §5 A/B 实验基线维度（200→165, 233→198）+ change_log 追加记录与统计表。用完即删。"""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# 1) model_gap 两处维度更正
mg = ROOT / "docs/model_gap_analysis_report_v2.0.md"
t = mg.read_text(encoding="utf-8")
mg_subs = [
    ("开启后 B 组 233 维 vs A 组 200 维", "开启后 B 组 198 维 vs A 组 165 维"),
    ("| 指标 | A 基线（200 维） | B 实验（233 维） | 差值 | 结论 |",
     "| 指标 | A 基线（165 维） | B 实验（198 维） | 差值 | 结论 |"),
]
for old, new in mg_subs:
    n = t.count(old)
    assert n == 1, f"model_gap count={n}: {old!r}"
    t = t.replace(old, new)
    print(f"[OK] model_gap: {old[:30]!r}")
mg.write_text(t, encoding="utf-8")

# 2) change_log：追加 C-20260831-007 + 统计表 +1
cl = ROOT / "docs/change_log.md"
t = cl.read_text(encoding="utf-8")

anchor = ("L1021/L1026「233 维 vs 200 维」属 slim_odds=False 实验口径、非生产口径，"
          "暂保留待核） | 低 | 系统 | 已完成 |")
assert t.count(anchor) == 1, f"anchor count={t.count(anchor)}"
new_rec = (
    anchor + "\n"
    "| C-20260831-007 | — | 文档 | docs/model_gap_analysis_report_v2.0.md §5 | "
    "核实并更正 P1-9 A/B 实验基线维度：A 组 200→165 维（slim_odds=True）、B 组 233→198 维（+ts_odds 33） | "
    "L1021/L1026 误把 A 基线写 200（实际 slim_odds=True 基线为 165，见 _tmp_p19_feature_check.py docstring L6 原文），"
    "200 系 233-33 的错误反推，从未被脚本/生产模型证实 | "
    "§5 与脚本/源码/生产模型（165→198→208）口径统一 | 实验基线维度错误记录 | 文档一致性 | "
    "✅ _tmp_p19_feature_check.py docstring 明确 A 基线 165 维、_tmp_p111_shap_ablation.py L234 base(198)、生产 208 维 | "
    "低 | 系统 | 已完成 |"
)
t = t.replace(anchor, new_rec)

# 顺序关键：先 5 字段合计（§5.1）再 4 字段合计（§5.2）
cl_subs = [
    ("| 文档变更 | 65 | 65 | 0 | 0 |", "| 文档变更 | 66 | 66 | 0 | 0 |"),
    ("| **合计** | **555** | **555** | **0** | **0** |", "| **合计** | **556** | **556** | **0** | **0** |"),
    ("| 2026-08-31 | 6 | 6 | 0 |", "| 2026-08-31 | 7 | 7 | 0 |"),
    ("| **合计** | **555** | **555** | **0** |", "| **合计** | **556** | **556** | **0** |"),
]
for old, new in cl_subs:
    n = t.count(old)
    assert n == 1, f"change_log count={n}: {old!r}"
    t = t.replace(old, new)
    print(f"[OK] change_log: {old[:30]!r}")

cl.write_text(t, encoding="utf-8")
print("done")