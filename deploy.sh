#!/bin/bash
# ========================================
#   五大联赛足球预测模型 v8.0 - 一键部署脚本
#   适用于 Linux (Ubuntu 22.04+)
# ========================================

set -e

APP_NAME="five-leagues"
APP_DIR="$(cd "$(dirname "$0")" && pwd)"
LOG_DIR="$APP_DIR/logs"
DATA_DIR="$APP_DIR/data"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
CYAN='\033[0;36m'
NC='\033[0m'

print_header() {
    echo -e "${CYAN}========================================${NC}"
    echo -e "${CYAN}  五大联赛足球预测模型 v8.0 部署脚本${NC}"
    echo -e "${CYAN}========================================${NC}"
    echo ""
}

print_step() {
    echo -e "${YELLOW}[$1]${NC} $2"
}

print_success() {
    echo -e "${GREEN}[完成]${NC} $1"
}

print_error() {
    echo -e "${RED}[错误]${NC} $1"
}

# ===== 1. 环境检查 =====
print_step "1/6" "检查环境依赖"

MISSING_DEPS=""

for cmd in node npm; do
    if ! command -v $cmd &> /dev/null; then
        MISSING_DEPS="$MISSING_DEPS $cmd"
    fi
done

if ! command -v pm2 &> /dev/null; then
    echo -e "${YELLOW}[警告]${NC} PM2 未安装，正在安装..."
    npm install -g pm2 || {
        print_error "PM2 安装失败，请手动执行: npm install -g pm2"
        exit 1
    }
fi

if [ -n "$MISSING_DEPS" ]; then
    print_error "缺少依赖:$MISSING_DEPS"
    echo "请先安装后重试:"
    echo "  sudo apt install -y nodejs npm"
    echo "  或访问 https://nodejs.org/ 下载"
    exit 1
fi

print_success "Node.js: $(node --version)"
print_success "PM2: $(pm2 --version)"
echo ""

# ===== 2. 安装依赖 =====
print_step "2/6" "安装项目依赖"
cd "$APP_DIR"

if [ ! -d "node_modules" ]; then
    npm install --production || {
        print_error "依赖安装失败"
        exit 1
    }
    print_success "依赖安装完成"
else
    echo -e "${YELLOW}[跳过]${NC} node_modules 已存在"
fi
echo ""

# ===== 3. 创建目录 =====
print_step "3/6" "创建运行时目录"

mkdir -p "$LOG_DIR" "$DATA_DIR" "$APP_DIR/backups"

print_success "目录准备就绪"
echo ""

# ===== 4. 配置文件 =====
print_step "4/6" "检查配置文件"

if [ ! -f ".env" ]; then
    if [ -f ".env.example" ]; then
        cp .env.example .env
        echo -e "${YELLOW}[提示]${NC} 已从 .env.example 创建 .env"
        echo -e "${YELLOW}[提示]${NC} 请编辑 .env 配置生产环境参数"
        echo -e "${YELLOW}[提示]${NC}   - 修改 JWT_SECRET 为强随机密钥"
        echo -e "${YELLOW}[提示]${NC}   - 修改 CLIENT_URL 为实际域名"
    else
        print_error ".env 文件不存在，且无 .env.example 模板"
        exit 1
    fi
else
    print_success ".env 配置文件已存在"
fi
echo ""

# ===== 5. 设置权限 =====
print_step "5/6" "设置文件权限"

# 敏感文件权限
chmod 600 .env 2>/dev/null || true
chmod 644 data/*.db 2>/dev/null || true
chmod 644 assets/*.js assets/*.json 2>/dev/null || true

# 日志目录
chmod 755 "$LOG_DIR" 2>/dev/null || true

print_success "权限设置完成"
echo ""

# ===== 6. 启动服务 =====
print_step "6/6" "启动生产服务"

echo ""
echo "选择启动模式:"
echo "  [1] 生产环境 (production) - 推荐"
echo "  [2] 开发环境 (development)"
echo "  [3] 测试模式 - 前台运行"
echo ""

read -p "请选择 [1-3] (默认: 1): " choice
choice=${choice:-1}

case $choice in
    2)
        echo ""
        echo "正在以开发环境模式启动..."
        pm2 start ecosystem.config.js --env development
        ;;
    3)
        echo ""
        echo "正在以前台测试模式启动 (Ctrl+C 停止)..."
        node server/index.js
        exit 0
        ;;
    *)
        echo ""
        echo "正在以生产环境模式启动..."
        pm2 start ecosystem.config.js --env production
        ;;
esac

# ===== 完成 =====
echo ""
echo -e "${GREEN}========================================${NC}"
echo -e "${GREEN}  部署完成！${NC}"
echo -e "${GREEN}========================================${NC}"
echo ""
echo -e "  服务地址:  ${CYAN}http://localhost:3000${NC}"
echo -e "  健康检查:  ${CYAN}http://localhost:3000/api/health${NC}"
echo -e "  模型管理:  ${CYAN}http://localhost:3000/api/model/reload/status${NC}"
echo ""
echo -e "  ${YELLOW}常用命令:${NC}"
echo -e "    pm2 status              查看服务状态"
echo -e "    pm2 logs five-leagues   查看实时日志"
echo -e "    pm2 monit               性能监控面板"
echo -e "    pm2 reload five-leagues 零停机重启"
echo -e "    pm2 stop five-leagues   停止服务"
echo ""
echo -e "  ${YELLOW}生产环境注意事项:${NC}"
echo -e "    1. 配置 Nginx 反向代理 (docs/DEPLOYMENT_GUIDE.md 第9节)"
echo -e "    2. 配置 HTTPS 证书 (docs/DEPLOYMENT_GUIDE.md 第10节)"
echo -e "    3. 配置防火墙 (仅开放 80/443 端口)"
echo -e "    4. 设置 PM2 开机自启: pm2 startup systemd && pm2 save"
echo ""

pm2 status