"""
快速验证调整后的惩罚策略
DRAW_RECALL_MIN=0.20, DRAW_RECALL_PENALTY=-0.03, 分段惩罚
"""

DRAW_RECALL_MIN = 0.20
DRAW_RECALL_PENALTY = -0.03

def compute_draw_recall_penalty(draw_recall):
    if draw_recall >= DRAW_RECALL_MIN:
        return 0.0
    penalty = DRAW_RECALL_PENALTY * (DRAW_RECALL_MIN - draw_recall) / DRAW_RECALL_MIN
    if draw_recall < 0.15:
        extra = 0.02 * (0.15 - draw_recall) / 0.15
        penalty -= extra
    return penalty

print("=" * 70)
print("📊 调整后惩罚策略验证")
print("=" * 70)
print(f"\n  配置: DRAW_RECALL_MIN={DRAW_RECALL_MIN}, DRAW_RECALL_PENALTY={DRAW_RECALL_PENALTY}")
print(f"\n  {'draw_recall':>12} | {'penalty':>10} | {'说明':<30}")
print(f"  {'-'*12}-+-{'-'*10}-+-{'-'*30}")

test_cases = [
    (0.30, "正常，无惩罚"),
    (0.25, "正常，无惩罚"),
    (0.20, "刚好达标，无惩罚"),
    (0.18, "温和惩罚区 (0.15~0.20)"),
    (0.15, "边界点 (无额外惩罚)"),
    (0.12, "严厉惩罚区 (<0.15)"),
    (0.10, "严厉惩罚区 (<0.15)"),
    (0.07, "极端情况"),
    (0.00, "最差情况"),
]

for dr, desc in test_cases:
    p = compute_draw_recall_penalty(dr)
    print(f"  {dr:>12.4f} | {p:>10.6f} | {desc:<30}")

print(f"\n  与旧策略对比 (0.28/-0.05):")
print(f"  {'draw_recall':>12} | {'旧惩罚':>10} | {'新惩罚':>10} | {'变化':>10}")
print(f"  {'-'*12}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}")
old_min, old_pen = 0.28, -0.05
for dr in [0.10, 0.15, 0.18, 0.20, 0.25, 0.28]:
    if dr >= old_min:
        old_p = 0.0
    else:
        old_p = old_pen * (old_min - dr) / old_min
    new_p = compute_draw_recall_penalty(dr)
    diff = new_p - old_p
    print(f"  {dr:>12.4f} | {old_p:>10.6f} | {new_p:>10.6f} | {diff:>+10.6f}")

print(f"\n  ✅ 新策略特点:")
print(f"     1. 门槛从 0.28 降到 0.20，更贴合实际可达水平")
print(f"     2. 惩罚强度从 -0.05 降到 -0.03，避免过度惩罚")
print(f"     3. 分段惩罚: 低于 0.15 时追加惩罚，防止极端失衡")
print(f"     4. 召回率 >= 0.20 时完全无惩罚，鼓励模型探索")