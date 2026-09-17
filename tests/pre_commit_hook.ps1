# ============================================
# Git Pre-commit Hook: CI 检查
# 阶段六 D-020
# ============================================
# 安装方法：
#   1. 将此文件复制到 .git/hooks/pre-commit
#   2. 或在 .github/workflows/ci.yml 中引用
#
# 功能：
#   - 提交前运行快速单元测试
#   - 检查模型资产完整性
#   - 性能回归基线守护

$ErrorActionPreference = "Stop"

Write-Host "=== Pre-commit CI 检查 ===" -ForegroundColor Cyan

# 定位项目根目录
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$projectRoot = Split-Path -Parent (Split-Path -Parent $scriptDir)

# 运行 CI 检查脚本
$ciCheck = Join-Path $scriptDir "ci_check.py"
if (Test-Path $ciCheck) {
    python $ciCheck --quick
    if ($LASTEXITCODE -ne 0) {
        Write-Host "❌ CI 检查失败，提交被阻止" -ForegroundColor Red
        exit 1
    }
    Write-Host "✅ CI 检查通过" -ForegroundColor Green
} else {
    Write-Host "⚠️  ci_check.py 未找到，跳过检查" -ForegroundColor Yellow
}

exit 0
